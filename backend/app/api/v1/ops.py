"""Operations: readiness for load balancers and orchestrators, and the telemetry preview."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.deps import Ctx, get_db, require_ctx
from app.db.migrate import current_revision, head_revision
from app.services import telemetry

router = APIRouter(tags=["operations"])


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """200 when this instance can serve: database reachable, schema at the latest migration, file
    storage writable. 503 with the failing check otherwise. Reveals no configuration."""
    checks: list[dict[str, Any]] = []
    engine = request.app.state.engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks.append({"check": "database", "ok": True})
    except Exception:  # noqa: BLE001 - any failure means not ready
        checks.append({"check": "database", "ok": False})
    try:
        ok = current_revision(engine) == head_revision()
        checks.append({"check": "schema", "ok": ok})
    except Exception:  # noqa: BLE001
        checks.append({"check": "schema", "ok": False})
    try:
        root = request.app.state.blobs.root
        probe = root / f".ready-{secrets.token_hex(4)}"
        probe.write_bytes(b"ok")
        probe.unlink()
        checks.append({"check": "file_storage", "ok": True})
    except Exception:  # noqa: BLE001
        checks.append({"check": "file_storage", "ok": False})
    ok = all(c["ok"] for c in checks)
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@router.get("/telemetry/preview")
def telemetry_preview(request: Request, _ctx: Ctx = Depends(require_ctx), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Exactly what would be sent, and whether sending is on (off unless the operator enabled it)."""
    payload = telemetry.snapshot(db, request.app.state.data_dir, request.app.state.engine.dialect.name)
    return {"enabled": telemetry.enabled(), "payload": payload,
            "note": "Sent only when GC_TELEMETRY=1 and GC_TELEMETRY_URL are both set, by scripts/telemetry.py."}  # fmt: skip
