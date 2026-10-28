"""A workspace's data: retention rules, a complete export, and proof that deletion happened.

Retention is off until an owner sets it. Each rule names a category and an age in days; applying
the rules first reports what would go (dry run), then deletes it and writes an audit entry. The audit
log keeps at least 90 days so security events stay reviewable.

The export is every row the workspace owns (secrets such as token and code hashes left out) plus every
stored file, in one zip with a manifest of counts and SHA-256 hashes.

Deletion verification counts what is left of a deleted workspace or account in every table that
can hold its data, and checks that its file folder is gone.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    ArtifactRow,
    AuditLogRow,
    Base,
    CustomModelRow,
    EmailVerificationRow,
    ExecutionJobRow,
    ExecutionTraceRow,
    ExportRow,
    MembershipRow,
    PasswordResetRow,
    ProjectRow,
    RemoteJobRow,
    SessionRow,
    SystemGraphRow,
    UserRow,
    WorkspaceRow,
)
from app.providers.storage import BlobStore
from app.services import audit

RULES = {
    "exports_days": "Exported files and their records",
    "traces_days": "Stored execution traces",
    "device_runs_days": "Finished runs on devices (measurements they created stay)",
    "unused_files_days": "Uploaded files nothing refers to any more",
    "audit_days": "Audit log entries (at least 90 days are always kept)",
}
AUDIT_MIN_DAYS = 90
SECRET_COLUMNS = {"token_hash", "pairing_hash", "password_hash", "mfa_secret", "mfa_recovery", "signature"}


def _now() -> datetime:
    return datetime.now(UTC)


def get_retention(db: Session, ws: int) -> dict[str, int | None]:
    row = db.get(WorkspaceRow, ws)
    saved = ((row.settings or {}) if row else {}).get("retention", {})
    return {k: saved.get(k) for k in RULES}


def set_retention(db: Session, ws: int, user: int | None, rules: dict[str, int | None]) -> dict[str, int | None]:
    clean: dict[str, int | None] = {}
    for k, v in rules.items():
        if k not in RULES:
            raise ValueError(f"unknown rule '{k}'")
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1 or v > 36500):
            raise ValueError(f"{k} must be a number of days from 1 to 36500, or empty for 'keep'")
        if k == "audit_days" and v is not None and v < AUDIT_MIN_DAYS:
            raise ValueError(f"the audit log keeps at least {AUDIT_MIN_DAYS} days")
        clean[k] = v
    row = db.get(WorkspaceRow, ws)
    assert row is not None
    row.settings = {**(row.settings or {}), "retention": {**get_retention(db, ws), **clean}}
    audit.audit(db, "retention.changed", workspace_id=ws, user_id=user, detail=clean, commit=False)
    db.commit()
    return get_retention(db, ws)


def _referenced_artifacts(db: Session, ws: int) -> set[int]:
    refs = {a for a in db.scalars(select(CustomModelRow.artifact_id).where(CustomModelRow.workspace_id == ws)) if a}
    refs |= {a for a in db.scalars(select(RemoteJobRow.artifact_id).where(RemoteJobRow.workspace_id == ws)) if a}
    for src in db.scalars(select(SystemGraphRow.source_artifacts).where(SystemGraphRow.workspace_id == ws)):
        refs |= {int(x) for x in src or []}
    return refs


def apply_retention(db: Session, blobs: BlobStore, ws: int, user: int | None, dry_run: bool = True) -> dict[str, Any]:
    rules = get_retention(db, ws)
    now = _now()
    out: dict[str, Any] = {"dry_run": dry_run, "rules": rules, "removed": {}}

    def cutoff(k: str) -> datetime | None:
        d = rules.get(k)
        return now - timedelta(days=d) if d else None

    if (c := cutoff("exports_days")) is not None:
        rows = list(db.scalars(select(ExportRow).where(ExportRow.workspace_id == ws, ExportRow.created_at < c)))
        out["removed"]["exports"] = len(rows)
        if not dry_run:
            for r in rows:
                if r.blob_key:
                    blobs.delete(r.blob_key)
                db.delete(r)
    if (c := cutoff("traces_days")) is not None:
        ids = list(db.scalars(select(ExecutionTraceRow.id).where(ExecutionTraceRow.workspace_id == ws, ExecutionTraceRow.created_at < c)))
        out["removed"]["traces"] = len(ids)
        if not dry_run and ids:
            db.execute(update(ExecutionJobRow).where(ExecutionJobRow.trace_id.in_(ids)).values(trace_id=None))
            db.execute(delete(ExecutionTraceRow).where(ExecutionTraceRow.id.in_(ids)))
    if (c := cutoff("device_runs_days")) is not None:
        q = select(RemoteJobRow.id).where(RemoteJobRow.workspace_id == ws, RemoteJobRow.status.not_in(("queued", "claimed")),
                                          RemoteJobRow.created_at < c)  # fmt: skip
        ids = list(db.scalars(q))
        out["removed"]["device_runs"] = len(ids)
        if not dry_run and ids:
            db.execute(delete(RemoteJobRow).where(RemoteJobRow.id.in_(ids)))
    if (c := cutoff("unused_files_days")) is not None:
        refs = _referenced_artifacts(db, ws)
        stale = [a for a in db.scalars(select(ArtifactRow).where(ArtifactRow.workspace_id == ws, ArtifactRow.deleted_at.is_(None),
                                                                ArtifactRow.created_at < c)) if a.id not in refs]  # fmt: skip
        out["removed"]["unused_files"] = len(stale)
        if not dry_run:
            for a in stale:
                key = a.blob_key
                a.deleted_at, a.blob_key = now, None
                db.flush()
                if key and not db.scalars(select(ArtifactRow.id).where(ArtifactRow.blob_key == key, ArtifactRow.deleted_at.is_(None))).first():
                    blobs.delete(key)
    if (c := cutoff("audit_days")) is not None:
        n = db.scalar(select(func.count()).select_from(AuditLogRow).where(AuditLogRow.workspace_id == ws, AuditLogRow.created_at < c)) or 0
        out["removed"]["audit_entries"] = int(n)
        if not dry_run and n:
            db.execute(delete(AuditLogRow).where(AuditLogRow.workspace_id == ws, AuditLogRow.created_at < c))
    if not dry_run:
        audit.audit(db, "retention.applied", workspace_id=ws, user_id=user, detail=out["removed"], commit=False)
        db.commit()
    return out


# --- export ------------------------------------------------------------------------------------------


def _owned_tables() -> list[Any]:
    """Every table with a workspace_id column, plus project-linked tables (found through projects)."""
    return [t for t in Base.metadata.sorted_tables if "workspace_id" in t.c and t.name not in ("rate_limits",)]


def _rows(db: Session, table: Any, where: Any) -> list[dict[str, Any]]:
    cols = [c for c in table.c if c.name not in SECRET_COLUMNS]
    return [dict(r._mapping) for r in db.execute(select(*cols).where(where))]


def export_workspace(db: Session, blobs: BlobStore, ws: int, user: int | None) -> tuple[bytes, dict[str, Any]]:
    w = db.get(WorkspaceRow, ws)
    assert w is not None
    files: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    project_ids = list(db.scalars(select(ProjectRow.id).where(ProjectRow.workspace_id == ws)))
    for t in _owned_tables():
        rows = _rows(db, t, t.c.workspace_id == ws)
        counts[t.name] = len(rows)
        files[f"data/{t.name}.json"] = json.dumps(rows, indent=1, default=str).encode()
    for name in ("project_versions", "runs"):
        t = Base.metadata.tables[name]
        rows = _rows(db, t, t.c.project_id.in_(project_ids)) if project_ids else []
        counts[name] = len(rows)
        files[f"data/{name}.json"] = json.dumps(rows, indent=1, default=str).encode()
    members = [{"user_id": m.user_id, "role": m.role} for m in db.scalars(select(MembershipRow).where(MembershipRow.workspace_id == ws))]
    files["data/members.json"] = json.dumps(members, indent=1).encode()
    stored = 0
    for a in db.scalars(select(ArtifactRow).where(ArtifactRow.workspace_id == ws, ArtifactRow.blob_key.is_not(None))):
        if a.blob_key and blobs.path(a.blob_key) is not None:  # type: ignore[attr-defined]
            files[f"files/uploads/{a.id}-{a.filename}"] = blobs.get(a.blob_key)
            stored += 1
    for e in db.scalars(select(ExportRow).where(ExportRow.workspace_id == ws, ExportRow.blob_key.is_not(None))):
        if e.blob_key and blobs.path(e.blob_key) is not None:  # type: ignore[attr-defined]
            files[f"files/exports/{e.id}-{e.filename}"] = blobs.get(e.blob_key)
            stored += 1
    manifest = {
        "schema": "green-computer.workspace-export/1", "generated_at": _now().isoformat(),
        "workspace": {"id": w.id, "name": w.name, "created_at": str(w.created_at)},
        "row_counts": counts, "stored_files": stored,
        "left_out": "Secrets (password, token, pairing-code and authenticator hashes, request signatures) and other "
                    "workspaces' data. Member emails are not included; user ids and roles are.",
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
    }  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=1))
    audit.audit(db, "workspace.exported", workspace_id=ws, user_id=user, detail={"rows": sum(counts.values()), "files": stored})
    return buf.getvalue(), manifest


# --- deletion verification ---------------------------------------------------------------------------


def verify_deleted(db: Session, blobs: BlobStore, workspace_ids: list[int], user_id: int | None = None,
                   project_ids: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
    """Counts of anything left; ok only when every count is zero and the file folders are gone."""
    left: dict[str, int] = {}
    for t in _owned_tables():
        if workspace_ids:
            n = db.scalar(select(func.count()).select_from(t).where(t.c.workspace_id.in_(workspace_ids))) or 0
            if n:
                left[t.name] = int(n)
    if workspace_ids:
        n = db.scalar(select(func.count()).select_from(WorkspaceRow).where(WorkspaceRow.id.in_(workspace_ids))) or 0
        if n:
            left["workspaces"] = int(n)
        for name in ("project_versions", "runs"):
            t = Base.metadata.tables[name]
            if project_ids:
                n = db.scalar(select(func.count()).select_from(t).where(t.c.project_id.in_(project_ids))) or 0
                if n:
                    left[name] = int(n)
    if user_id is not None:
        for model, col in ((UserRow, UserRow.id), (SessionRow, SessionRow.user_id), (PasswordResetRow, PasswordResetRow.user_id),
                           (EmailVerificationRow, EmailVerificationRow.user_id), (MembershipRow, MembershipRow.user_id)):  # fmt: skip
            n = db.scalar(select(func.count()).select_from(model).where(col == user_id)) or 0
            if n:
                left[model.__tablename__] = int(n)
    folders = [f"ws{w}" for w in workspace_ids if getattr(blobs, "has_namespace", lambda _ns: False)(f"ws{w}")]
    return {"ok": not left and not folders, "rows_left": left, "file_folders_left": folders,
            "checked_tables": sorted({t.name for t in _owned_tables()} | {"workspaces", "project_versions", "runs", "users",
                                     "auth_sessions", "password_resets", "email_verifications", "memberships"}),
            "checked_at": _now().isoformat()}  # fmt: skip
