"""FastAPI dependencies: who is calling, in which workspace, and may they do this.

Authentication: the ``gc_session`` cookie (browser) or ``Authorization: Bearer <token>`` (CLI/API).
CSRF: a cookie-authenticated request that changes state must carry ``X-GC-CSRF: 1``. Browsers do not
let another site set custom headers without a CORS preflight, which this API never grants, and the
cookie is SameSite=Lax. Workspace: ``X-GC-Workspace: <id>`` or the user's own workspace by default.
Every lookup of an owned object goes through here and answers 404 for other tenants' objects, so a
caller cannot even learn that they exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import service as auth
from app.db.models import SessionRow, UserRow
from app.security import client_ip

COOKIE = "gc_session"
CSRF_HEADER = "x-gc-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


@dataclass
class Ctx:
    user: UserRow
    session: SessionRow
    workspace_id: int
    role: str
    ip: str
    via: str  # cookie | bearer

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "editor")


def _token(request: Request) -> tuple[str, str] | None:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip(), "bearer"
    cookie = request.cookies.get(COOKIE)
    return (cookie, "cookie") if cookie else None


def optional_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx | None:
    got = _token(request)
    if got is None:
        return None
    token, via = got
    found = auth.resolve_token(db, token)
    if found is None:
        return None
    session, user = found
    ws_header = request.headers.get("x-gc-workspace")
    if ws_header:
        try:
            ws_id = int(ws_header)
        except ValueError:
            raise HTTPException(400, "X-GC-Workspace must be a workspace id") from None
    else:
        ws_id = auth.default_workspace_id(db, user.id) or 0
    role = auth.role_in(db, user.id, ws_id) if ws_id else None
    if role is None:
        raise HTTPException(403, "You are not a member of this workspace.")
    return Ctx(user=user, session=session, workspace_id=ws_id, role=role,
               ip=client_ip(request), via=via)  # fmt: skip


def require_ctx(request: Request, ctx: Ctx | None = Depends(optional_ctx)) -> Ctx:
    if ctx is None:
        raise HTTPException(401, "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    if ctx.via == "cookie" and request.method not in SAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, "Missing X-GC-CSRF header (cross-site request refused).")
    return ctx


def require_writer(ctx: Ctx = Depends(require_ctx)) -> Ctx:
    if not ctx.can_write:
        raise HTTPException(403, "Your role in this workspace is read-only.")
    from app.auth.account_security import verification_required

    if verification_required() and ctx.user.email_verified_at is None:
        raise HTTPException(403, "Confirm your email address first: open the link we sent you, or send a new one from Account.")
    return ctx
