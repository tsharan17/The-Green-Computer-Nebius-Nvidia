"""Uploaded files: validate, store the bytes, record the artifact and its audit trail.

Every upload, valid or not, gets an artifact row (so the history shows what was tried and why it was
refused). Only files that pass validation keep their bytes in the blob store.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Ctx
from app.db import repository as repo
from app.db.models import ArtifactRow
from app.engine.workload.onnx_parser import ModelParseError, ParseResult, parse_onnx
from app.engine.workload.onnx_report import onnx_report
from app.providers.storage import BlobStore
from app.schemas.model import ModelProfile
from app.services import audit

CONTENT_TYPES = {
    ".onnx": "application/octet-stream", ".json": "application/json", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".csv": "text/csv", ".pdf": "application/pdf", ".txt": "text/plain",
    ".rpt": "text/plain", ".log": "text/plain", ".md": "text/markdown", ".kicad_sch": "text/plain",
    ".kicad_pcb": "text/plain", ".v": "text/plain", ".sv": "text/plain", ".xml": "application/xml",
}  # fmt: skip


def content_type_for(filename: str) -> str:
    ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return CONTENT_TYPES.get(ext, "application/octet-stream")


def safe_filename(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "upload"
    return "".join(c for c in base if c.isprintable() and c not in '<>:"|?*')[:255] or "upload"


def store_artifact(
    blobs: BlobStore,
    db: Session,
    ctx: Ctx,
    data: bytes,
    filename: str,
    kind: str,
    status: str,
    validation: dict[str, Any],
    project_id: int | None = None,
    keep_bytes: bool = True,
) -> ArtifactRow:
    import hashlib

    fname = safe_filename(filename)
    key, digest = (blobs.put(f"ws{ctx.workspace_id}", data) if keep_bytes
                   else (None, hashlib.sha256(data).hexdigest()))  # fmt: skip
    row = ArtifactRow(
        workspace_id=ctx.workspace_id, project_id=project_id, kind=kind, filename=fname,
        content_type=content_type_for(fname), size_bytes=len(data), sha256=digest, blob_key=key,
        status=status, validation=validation, uploaded_by=ctx.user_id,
    )  # fmt: skip
    db.add(row)
    db.flush()
    audit.audit(db, "file.uploaded", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="artifact", target_id=row.id,
                detail={"filename": fname, "kind": kind, "status": status, "size_bytes": len(data),
                        "sha256": digest, "project_id": project_id}, ip=ctx.ip, commit=False)  # fmt: skip
    if keep_bytes:
        audit.usage(db, "artifact_upload_bytes", len(data), workspace_id=ctx.workspace_id,
                    user_id=ctx.user_id, project_id=project_id, detail={"artifact_id": row.id},
                    commit=False)  # fmt: skip
    db.commit()
    return row


def store_onnx_model(
    blobs: BlobStore,
    db: Session,
    ctx: Ctx,
    data: bytes,
    filename: str,
    name: str | None,
    dynamic_dim_default: int | None,
    baseline_accuracy: float,
    accuracy_metric: str,
    project_id: int | None,
) -> tuple[ModelProfile, ParseResult, ArtifactRow]:
    report = onnx_report(data)
    try:
        parsed = parse_onnx(data, name or safe_filename(filename).rsplit(".", 1)[0], dynamic_dim_default)
    except ModelParseError as e:
        report["errors"] = [*report.get("errors", []), str(e)]
        store_artifact(blobs, db, ctx, data, filename, "onnx_model", "invalid", report,
                       project_id, keep_bytes=False)  # fmt: skip
        raise
    report["warnings"] = sorted(set(report.get("warnings", [])) | set(parsed.warnings))
    art = store_artifact(blobs, db, ctx, data, filename, "onnx_model", "valid", report, project_id)
    profile = parsed.profile.model_copy(
        update={"baseline_accuracy": baseline_accuracy, "accuracy_metric": accuracy_metric}
    )
    stored = repo.create_custom_model(db, profile, ctx.workspace_id, art.id)
    art.validation = {**art.validation, "model_id": stored.id}
    db.commit()
    return stored, parsed, art


def artifact_view(r: ArtifactRow) -> dict[str, Any]:
    return {
        "id": r.id, "kind": r.kind, "filename": r.filename, "content_type": r.content_type,
        "size_bytes": r.size_bytes, "sha256": r.sha256, "status": r.status,
        "validation": r.validation, "project_id": r.project_id, "stored": r.blob_key is not None,
        "uploaded_by": r.uploaded_by, "created_at": r.created_at.isoformat(),
    }  # fmt: skip


def list_artifacts(
    db: Session, workspace_id: int, project_id: int | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    q = select(ArtifactRow).where(ArtifactRow.workspace_id == workspace_id,
                                  ArtifactRow.deleted_at.is_(None))  # fmt: skip
    if project_id is not None:
        q = q.where(ArtifactRow.project_id == project_id)
    if kind:
        q = q.where(ArtifactRow.kind == kind)
    return [artifact_view(r) for r in db.scalars(q.order_by(ArtifactRow.id.desc()))]


def get_artifact(db: Session, workspace_id: int, artifact_id: int) -> ArtifactRow | None:
    row = db.get(ArtifactRow, artifact_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        return None
    return row
