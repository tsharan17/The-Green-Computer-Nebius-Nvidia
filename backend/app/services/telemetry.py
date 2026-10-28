"""Privacy-safe usage telemetry: off unless an operator turns it on, and inspectable first.

  GC_TELEMETRY=1 and GC_TELEMETRY_URL=<your endpoint>   both needed before anything is sent
  scripts/telemetry.py                                   prints the payload; sends only when enabled

The payload is aggregate counts and versions for this installation, under a random installation id
kept in the data directory. It never contains names, emails, project names, file names, IP addresses,
numbers from analyses, or anything a user typed. There is no default destination.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.db.models import ExportRow, ProjectRow, RemoteDeviceRow, RunRow, UserRow, WorkspaceRow
from app.engine import assumptions as A

SCHEMA = "green-computer.telemetry/1"


def enabled() -> bool:
    return os.environ.get("GC_TELEMETRY", "").lower() in ("1", "true", "yes", "on") and bool(os.environ.get("GC_TELEMETRY_URL"))


def installation_id(data_dir: Path) -> str:
    f = Path(data_dir) / "installation_id"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(16))
    return f.read_text().strip()


def snapshot(db: Session, data_dir: Path, dialect: str) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=30)

    def count(model: Any, *where: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    return {
        "schema": SCHEMA,
        "installation": installation_id(data_dir),
        "version": __version__,
        "engine_version": A.ENGINE_VERSION,
        "database": dialect,
        "counts": {
            "workspaces": count(WorkspaceRow), "users": count(UserRow),
            "users_active_30d": count(UserRow, UserRow.last_login_at >= since),
            "projects": count(ProjectRow), "analyses_30d": count(RunRow, RunRow.created_at >= since),
            "exports_30d": count(ExportRow, ExportRow.created_at >= since), "remote_devices": count(RemoteDeviceRow),
        },
        "never_included": "names, emails, project or file names, IP addresses, analysis numbers, anything typed by users",
    }  # fmt: skip


def send(payload: dict[str, Any]) -> bool:
    if not enabled():
        return False
    req = urllib.request.Request(os.environ["GC_TELEMETRY_URL"], data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})  # fmt: skip
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - operator-configured URL
        return 200 <= r.status < 300
