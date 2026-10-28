"""Accounts: sign-up, sign-in sessions, password reset, account deletion, workspaces.

Self-hosted on purpose (no identity SaaS). Tokens are random 256-bit values; only their SHA-256 is
stored, so a database leak does not leak usable sessions or reset links.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, needs_rehash, password_problem, verify_password
from app.db.models import (
    AgentRunRow,
    ArtifactRow,
    AuditLogRow,
    BenchmarkJobRow,
    CalibrationRow,
    CustomModelRow,
    DesignPlanRow,
    EmailVerificationRow,
    ExecutionJobRow,
    ExecutionTraceRow,
    ExportRow,
    HardwareDraftRow,
    MeasurementRow,
    MembershipRow,
    PasswordResetRow,
    ProjectRow,
    ProjectVersionRow,
    RemoteDeviceRow,
    RemoteJobRow,
    RunRow,
    SessionRow,
    SystemGraphRow,
    TermsAcceptanceRow,
    UsageEventRow,
    UserRow,
    WorkspaceRow,
)
from app.providers.email import EmailSender
from app.providers.storage import BlobStore

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")
RESET_TTL = timedelta(hours=1)
ROLES = ("owner", "editor", "viewer")


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise AuthError(422, "Enter a valid email address.")
    return e


# --- workspaces ---------------------------------------------------------------------------


def create_workspace(db: Session, user: UserRow, name: str, personal: bool) -> WorkspaceRow:
    ws = WorkspaceRow(name=name[:120] or "Workspace", personal=personal, legacy=False)
    db.add(ws)
    db.flush()
    db.add(MembershipRow(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.flush()
    return ws


def memberships(db: Session, user_id: int) -> list[tuple[WorkspaceRow, str]]:
    rows = db.execute(
        select(WorkspaceRow, MembershipRow.role)
        .join(MembershipRow, MembershipRow.workspace_id == WorkspaceRow.id)
        .where(MembershipRow.user_id == user_id)
        .order_by(WorkspaceRow.personal.desc(), WorkspaceRow.id)
    ).all()
    return [(w, r) for w, r in rows]


def role_in(db: Session, user_id: int, workspace_id: int) -> str | None:
    return db.scalars(
        select(MembershipRow.role).where(
            MembershipRow.user_id == user_id, MembershipRow.workspace_id == workspace_id
        )
    ).first()


def default_workspace_id(db: Session, user_id: int) -> int | None:
    ms = memberships(db, user_id)
    return ms[0][0].id if ms else None


def _claim_legacy(db: Session, user: UserRow) -> WorkspaceRow | None:
    """The first account on a database inherits data created before accounts existed."""
    legacy = db.scalars(select(WorkspaceRow).where(WorkspaceRow.legacy.is_(True))).first()
    if legacy is None:
        return None
    if db.scalars(select(MembershipRow).where(MembershipRow.workspace_id == legacy.id)).first():
        return None
    db.add(MembershipRow(workspace_id=legacy.id, user_id=user.id, role="owner"))
    db.flush()
    return legacy


# --- sign-up / sign-in --------------------------------------------------------------------


def signup(db: Session, email: str, password: str, display_name: str = "") -> tuple[UserRow, dict]:
    e = normalise_email(email)
    problem = password_problem(password, e)
    if problem:
        raise AuthError(422, problem)
    if db.scalars(select(UserRow).where(UserRow.email == e)).first():
        raise AuthError(409, "An account with this email already exists. Sign in instead.")
    user = UserRow(email=e, display_name=(display_name or "").strip()[:120],
                   password_hash=hash_password(password), is_active=True)  # fmt: skip
    db.add(user)
    db.flush()
    ws = create_workspace(db, user, f"{user.display_name or e.split('@')[0]}'s workspace", True)
    legacy = _claim_legacy(db, user)
    db.commit()
    return user, {"workspace_id": ws.id, "claimed_legacy_workspace": legacy.id if legacy else None}


def authenticate(db: Session, email: str, password: str) -> UserRow:
    """Same error and similar time whether the email exists or not (no account enumeration)."""
    try:
        e = normalise_email(email)
    except AuthError:
        e = ""
    user = db.scalars(select(UserRow).where(UserRow.email == e)).first() if e else None
    if user is None:
        verify_password(password, hash_password("timing-equaliser-x"))
        raise AuthError(401, "Email or password is incorrect.")
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise AuthError(401, "Email or password is incorrect.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _now()
    db.commit()
    return user


def create_session(
    db: Session, user: UserRow, *, days: int, kind: str = "browser", label: str = "",
    user_agent: str = "", ip: str = "",
) -> tuple[str, SessionRow]:  # fmt: skip
    token = secrets.token_urlsafe(32)
    row = SessionRow(
        user_id=user.id, token_hash=token_hash(token), kind=kind, label=label[:120],
        user_agent=user_agent[:300], ip=ip[:64], expires_at=_now() + timedelta(days=days),
    )  # fmt: skip
    db.add(row)
    db.commit()
    return token, row


def resolve_token(db: Session, token: str) -> tuple[SessionRow, UserRow] | None:
    if not token or len(token) > 200:
        return None
    row = db.scalars(select(SessionRow).where(SessionRow.token_hash == token_hash(token))).first()
    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= _now():
        return None
    user = db.get(UserRow, row.user_id)
    if user is None or not user.is_active:
        return None
    if _now() - _aware(row.last_seen_at) > timedelta(minutes=5):
        row.last_seen_at = _now()
        db.commit()
    return row, user


def revoke_session(db: Session, session_id: int, user_id: int) -> bool:
    row = db.get(SessionRow, session_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    db.commit()
    return True


def revoke_all(db: Session, user_id: int, except_id: int | None = None) -> int:
    q = update(SessionRow).where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
    if except_id is not None:
        q = q.where(SessionRow.id != except_id)
    n = db.execute(q.values(revoked_at=_now())).rowcount  # type: ignore[attr-defined]
    db.commit()
    return int(n or 0)


def list_sessions(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(SessionRow)
        .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
        .order_by(SessionRow.id.desc())
    )
    now = _now()
    return [
        {"id": r.id, "kind": r.kind, "label": r.label, "user_agent": r.user_agent, "ip": r.ip,
         "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat(),
         "expires_at": r.expires_at.isoformat()}
        for r in rows if _aware(r.expires_at) > now
    ]  # fmt: skip


# --- passwords ----------------------------------------------------------------------------


def change_password(db: Session, user: UserRow, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AuthError(403, "Your current password is incorrect.")
    problem = password_problem(new, user.email)
    if problem:
        raise AuthError(422, problem)
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    db.commit()


def request_reset(
    db: Session, email: str, sender: EmailSender, public_url: str
) -> str | None:
    """Create and send a reset link. Returns the token (for development display) or None when no
    such account exists. The HTTP response is identical either way."""
    try:
        e = normalise_email(email)
    except AuthError:
        return None
    user = db.scalars(select(UserRow).where(UserRow.email == e)).first()
    if user is None or not user.is_active:
        return None
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetRow(user_id=user.id, token_hash=token_hash(token),
                            expires_at=_now() + RESET_TTL))  # fmt: skip
    db.commit()
    link = f"{public_url}/#/reset?token={token}"
    sender.send(
        user.email,
        "Reset your Green Computer password",
        "Someone (hopefully you) asked to reset the password for this account.\n\n"
        f"Open this link within one hour to choose a new password:\n{link}\n\n"
        "If you did not ask for this, ignore this email; your password stays the same.",
    )
    return token


def reset_password(db: Session, token: str, new: str) -> UserRow:
    row = db.scalars(
        select(PasswordResetRow).where(PasswordResetRow.token_hash == token_hash(token or ""))
    ).first()
    if row is None or row.used_at is not None or _aware(row.expires_at) <= _now():
        raise AuthError(400, "This reset link is invalid or has expired. Ask for a new one.")
    user = db.get(UserRow, row.user_id)
    if user is None:
        raise AuthError(400, "This reset link is invalid or has expired. Ask for a new one.")
    problem = password_problem(new, user.email)
    if problem:
        raise AuthError(422, problem)
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    row.used_at = _now()
    db.commit()
    revoke_all(db, user.id)  # every existing sign-in must re-authenticate
    return user


# --- deletion -----------------------------------------------------------------------------

# Every table that holds workspace-owned rows, in an order that respects foreign keys. Later
# phases append their tables here (see app.auth.service.register_owned_table).
# Deleted first-to-last in reverse, so list parents before children: exports reference plans.
_EXTRA_WORKSPACE_TABLES: list[Any] = [ExecutionTraceRow, DesignPlanRow, ExportRow, ExecutionJobRow, SystemGraphRow, RemoteDeviceRow, RemoteJobRow]
_EXTRA_PROJECT_TABLES: list[Any] = []


def register_owned_table(model: Any, by: str = "workspace") -> None:
    (_EXTRA_WORKSPACE_TABLES if by == "workspace" else _EXTRA_PROJECT_TABLES).append(model)


@dataclass
class DeletionSummary:
    workspaces_deleted: int = 0
    memberships_removed: int = 0
    projects_deleted: int = 0
    files_deleted: int = 0
    workspace_ids: list[int] = field(default_factory=list)
    project_ids: list[int] = field(default_factory=list)


def delete_workspace_data(db: Session, workspace_id: int, blobs: BlobStore | None) -> DeletionSummary:
    """Remove a workspace and everything in it (rows and stored files)."""
    s = DeletionSummary(workspaces_deleted=1)
    project_ids = list(db.scalars(select(ProjectRow.id).where(ProjectRow.workspace_id == workspace_id)))
    s.projects_deleted = len(project_ids)
    s.files_deleted = len(list(db.scalars(
        select(ArtifactRow.id).where(ArtifactRow.workspace_id == workspace_id)
    )))  # fmt: skip
    for model in reversed(_EXTRA_PROJECT_TABLES):
        db.execute(delete(model).where(model.project_id.in_(project_ids)))
    for model in reversed(_EXTRA_WORKSPACE_TABLES):
        db.execute(delete(model).where(model.workspace_id == workspace_id))
    if project_ids:
        db.execute(delete(RunRow).where(RunRow.project_id.in_(project_ids)))
        db.execute(delete(AgentRunRow).where(AgentRunRow.project_id.in_(project_ids)))
        db.execute(delete(UsageEventRow).where(UsageEventRow.project_id.in_(project_ids)))
    for model in (UsageEventRow, AuditLogRow, CustomModelRow, MeasurementRow, CalibrationRow,
                  BenchmarkJobRow, HardwareDraftRow):  # fmt: skip
        db.execute(delete(model).where(model.workspace_id == workspace_id))
    db.execute(delete(ArtifactRow).where(ArtifactRow.workspace_id == workspace_id))
    if project_ids:
        db.execute(delete(ProjectVersionRow).where(ProjectVersionRow.project_id.in_(project_ids)))
    db.execute(delete(ProjectRow).where(ProjectRow.workspace_id == workspace_id))
    db.execute(delete(MembershipRow).where(MembershipRow.workspace_id == workspace_id))
    db.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id))
    if blobs is not None:
        blobs.delete_namespace(f"ws{workspace_id}")
    return s


def delete_account(
    db: Session, user: UserRow, password: str, confirm: str, blobs: BlobStore | None
) -> DeletionSummary:
    """Delete the account. Workspaces the user owns alone are deleted with all their data; in
    shared workspaces only the membership goes and the user's name is removed from records."""
    if confirm != "DELETE":
        raise AuthError(422, 'Type DELETE to confirm.')
    if not verify_password(password, user.password_hash):
        raise AuthError(403, "Your password is incorrect.")
    total = DeletionSummary()
    for ws, role in memberships(db, user.id):
        owners = db.scalars(select(MembershipRow.user_id).where(
            MembershipRow.workspace_id == ws.id, MembershipRow.role == "owner"
        )).all()  # fmt: skip
        others = db.scalars(select(MembershipRow.user_id).where(
            MembershipRow.workspace_id == ws.id, MembershipRow.user_id != user.id
        )).all()  # fmt: skip
        if not others:
            total.project_ids += list(db.scalars(select(ProjectRow.id).where(ProjectRow.workspace_id == ws.id)))
            r = delete_workspace_data(db, ws.id, blobs)
            total.workspaces_deleted += 1
            total.workspace_ids.append(ws.id)
            total.projects_deleted += r.projects_deleted
            total.files_deleted += r.files_deleted
            continue
        if role == "owner" and owners == [user.id]:
            # Hand the shared workspace to the longest-standing remaining member.
            heir = db.scalars(select(MembershipRow).where(
                MembershipRow.workspace_id == ws.id, MembershipRow.user_id != user.id
            ).order_by(MembershipRow.id)).first()  # fmt: skip
            if heir is not None:
                heir.role = "owner"
        db.execute(delete(MembershipRow).where(
            MembershipRow.workspace_id == ws.id, MembershipRow.user_id == user.id
        ))  # fmt: skip
        total.memberships_removed += 1
    for model, col in ((ProjectRow, "created_by"), (ProjectVersionRow, "created_by"),
                       (RunRow, "user_id"), (ArtifactRow, "uploaded_by"),
                       (AuditLogRow, "user_id"), (UsageEventRow, "user_id")):  # fmt: skip
        db.execute(update(model).where(getattr(model, col) == user.id).values({col: None}))
    for model, col in _USER_REFERENCES:
        db.execute(update(model).where(getattr(model, col) == user.id).values({col: None}))
    db.execute(delete(SessionRow).where(SessionRow.user_id == user.id))
    db.execute(delete(PasswordResetRow).where(PasswordResetRow.user_id == user.id))
    db.execute(delete(EmailVerificationRow).where(EmailVerificationRow.user_id == user.id))
    db.execute(delete(TermsAcceptanceRow).where(TermsAcceptanceRow.user_id == user.id))
    db.execute(delete(UserRow).where(UserRow.id == user.id))
    # Keep a trace that an account was deleted, without personal data.
    db.add(AuditLogRow(workspace_id=None, user_id=None, action="account.deleted",
                       target_type="user", target_id="", detail={
                           "workspaces_deleted": total.workspaces_deleted,
                           "memberships_removed": total.memberships_removed}))  # fmt: skip
    db.commit()
    return total


# Later phases register (model, column) pairs that reference users and must be anonymised.
_USER_REFERENCES: list[tuple[Any, str]] = [
    (ExecutionTraceRow, "created_by"), (DesignPlanRow, "created_by"), (ExportRow, "created_by"),
    (ExecutionJobRow, "created_by"), (SystemGraphRow, "created_by"),
    (RemoteDeviceRow, "created_by"), (RemoteJobRow, "approved_by"),
]


def register_user_reference(model: Any, column: str) -> None:
    _USER_REFERENCES.append((model, column))
