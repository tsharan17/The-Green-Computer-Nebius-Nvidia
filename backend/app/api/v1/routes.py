from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import __version__
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.config import get_llm_settings
from app.data_loader import load_devices
from app.db import repository as repo
from app.engine.workload.onnx_parser import ModelParseError, manual_profile
from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectOut, ProjectSpec
from app.schemas.results import Config, EvaluationResult, OptimisationResult
from app.security import (
    demo_mode,
    limit_analyse,
    max_custom_items,
    max_projects,
    max_upload_bytes,
)
from app.services import audit
from app.services import evaluation_service as svc
from app.services.model_lookup import visible_models
from app.services.project_service import SpecError, validate_refs

__all__ = ["router", "get_db", "project_for"]

router = APIRouter()

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
REFUSED_EXTENSIONS = (".pt", ".pth", ".pkl", ".pickle", ".bin", ".ckpt", ".joblib")


def _validate_refs(db: Session, spec: ProjectSpec, ws: int) -> None:
    try:
        validate_refs(db, spec, ws)
    except SpecError as e:
        raise HTTPException(422, str(e)) from e


def _check_custom_model_cap(db: Session, ws: int) -> None:
    cap = max_custom_items()
    if cap and len(repo.list_custom_models(db, ws)) >= cap:
        raise HTTPException(503, "This workspace has reached its limit of uploaded models.")


def project_for(db: Session, ctx: Ctx, project_id: int) -> ProjectOut:
    """The project if it is in the caller's workspace; 404 otherwise (even if it exists)."""
    p = repo.get_project(db, project_id, ctx.workspace_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


@router.get("/health")
def health() -> dict:
    from app.integrations.jobs import status as jobs_status

    jobs = jobs_status()
    return {
        "status": "ok",
        "version": __version__,
        "demo_mode": demo_mode(),
        "agent": {"configured": get_llm_settings().configured},
        "jobs": {"configured": jobs.configured, "detail": jobs.reason},
    }


@router.get(
    "/models/library",
    response_model=list[ModelProfile],
    response_model_exclude={"__all__": {"layers", "tensors"}},
)
def model_library(db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[ModelProfile]:
    return visible_models(db, ctx.workspace_id)


class UploadOut(BaseModel):
    profile: ModelProfile
    warnings: list[str]
    artifact_id: int | None = None
    validation: dict[str, Any] = {}


@router.post(
    "/models/upload",
    response_model=UploadOut,
    response_model_exclude={"profile": {"layers", "tensors"}},
    status_code=201,
)
async def upload_model(
    request: Request,
    filename: str = "model.onnx",
    name: str | None = None,
    dynamic_dim_default: int | None = Query(None, ge=1),
    baseline_accuracy: float = 1.0,
    accuracy_metric: str = "not provided (relative accuracy only)",
    project_id: int | None = None,
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> UploadOut:
    """Raw ONNX bytes in the request body (Content-Type: application/octet-stream).

    The file is validated, summarised, stored (so it can be executed and transformed later) and
    recorded in the workspace's artifact history."""
    from app.services import intake

    if filename.lower().endswith(REFUSED_EXTENSIONS):
        raise HTTPException(415, "pickle-based and torch checkpoint files are refused; use ONNX")
    declared = request.headers.get("content-length")
    limit = max_upload_bytes(MAX_UPLOAD_BYTES)
    _check_custom_model_cap(db, ctx.workspace_id)
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file too large (limit {limit // (1024 * 1024)} MB)")
    data = await request.body()
    if len(data) > limit:
        raise HTTPException(413, f"file too large (limit {limit // (1024 * 1024)} MB)")
    if not 0 < baseline_accuracy <= 1:
        raise HTTPException(422, "baseline_accuracy must be in (0, 1]")
    if project_id is not None:
        project_for(db, ctx, project_id)
    try:
        stored, parsed, art = intake.store_onnx_model(
            request.app.state.blobs, db, ctx, data, filename, name, dynamic_dim_default,
            baseline_accuracy, accuracy_metric, project_id,
        )  # fmt: skip
    except ModelParseError as e:
        raise HTTPException(422, str(e)) from e
    return UploadOut(profile=stored, warnings=parsed.warnings, artifact_id=art.id,
                     validation=art.validation)  # fmt: skip


class ManualModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    params_m: float = Field(gt=0)
    gflops: float = Field(gt=0)
    n_layers: int = Field(50, ge=1)
    act_mb_fp32: float | None = Field(None, gt=0)
    baseline_accuracy: float = Field(1.0, gt=0, le=1.0)
    accuracy_metric: str = "not provided (relative accuracy only)"


@router.post(
    "/models/manual",
    response_model=ModelProfile,
    response_model_exclude={"layers", "tensors"},
    status_code=201,
)
def add_manual_model(
    body: ManualModelIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> ModelProfile:
    _check_custom_model_cap(db, ctx.workspace_id)
    profile = manual_profile(
        body.name, body.params_m, body.gflops, body.n_layers, body.act_mb_fp32,
        body.baseline_accuracy, body.accuracy_metric,
    )  # fmt: skip
    out = repo.create_custom_model(db, profile, ctx.workspace_id)
    audit.audit(db, "model.manual_created", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="model", target_id=out.id, detail={"name": out.name}, ip=ctx.ip)  # fmt: skip
    return out


@router.get("/hardware/library", response_model=list[HardwareProfile])
def hardware_library(ctx: Ctx = Depends(require_ctx)) -> list[HardwareProfile]:
    return list(load_devices(ctx.workspace_id).values())


# --- projects -----------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    include_archived: bool = False, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> list[ProjectOut]:
    return repo.list_projects(db, ctx.workspace_id, include_archived)


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    spec: ProjectSpec, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> ProjectOut:
    cap = max_projects()
    if cap and len(repo.list_projects(db, ctx.workspace_id, True)) >= cap:
        raise HTTPException(503, "This workspace has reached its project limit.")
    _validate_refs(db, spec, ctx.workspace_id)
    p = repo.create_project(db, spec, ctx.workspace_id, ctx.user_id)
    audit.audit(db, "project.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="project", target_id=p.id, detail={"name": spec.name}, ip=ctx.ip)  # fmt: skip
    return p


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> ProjectOut:
    return project_for(db, ctx, project_id)


@router.put("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, spec: ProjectSpec, note: str = "", db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> ProjectOut:  # fmt: skip
    before = project_for(db, ctx, project_id)
    _validate_refs(db, spec, ctx.workspace_id)
    out = repo.update_project(db, project_id, spec, ctx.workspace_id, ctx.user_id, note)
    if out is None:
        raise HTTPException(404, "project not found")
    if out.version != before.version:
        audit.audit(db, "project.version_created", workspace_id=ctx.workspace_id,
                    user_id=ctx.user_id, target_type="project", target_id=project_id,
                    detail={"version": out.version, "project_id": project_id}, ip=ctx.ip)  # fmt: skip
    return out


class ArchiveIn(BaseModel):
    archived: bool = True


@router.post("/projects/{project_id}/archive", response_model=ProjectOut)
def archive_project(
    project_id: int, body: ArchiveIn, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> ProjectOut:  # fmt: skip
    out = repo.set_archived(db, project_id, ctx.workspace_id, body.archived)
    if out is None:
        raise HTTPException(404, "project not found")
    audit.audit(db, "project.archived" if body.archived else "project.restored",
                workspace_id=ctx.workspace_id, user_id=ctx.user_id, target_type="project",
                target_id=project_id, ip=ctx.ip)  # fmt: skip
    return out


@router.get("/projects/{project_id}/versions")
def project_versions(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> list[dict]:
    project_for(db, ctx, project_id)
    return repo.list_versions(db, project_id)


@router.post("/projects/{project_id}/versions/{version}/restore", response_model=ProjectOut)
def restore_version(
    project_id: int, version: int, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> ProjectOut:  # fmt: skip
    """Make an old version's inputs current again (as a new version: history is never rewritten)."""
    project_for(db, ctx, project_id)
    v = repo.get_version(db, project_id, version)
    if v is None:
        raise HTTPException(404, "version not found")
    spec = ProjectSpec.model_validate(v.spec)
    _validate_refs(db, spec, ctx.workspace_id)
    out = repo.update_project(db, project_id, spec, ctx.workspace_id, ctx.user_id,
                              note=f"restored from version {version}")  # fmt: skip
    assert out is not None
    audit.audit(db, "project.version_restored", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="project", target_id=project_id,
                detail={"from_version": version, "new_version": out.version,
                        "project_id": project_id}, ip=ctx.ip)  # fmt: skip
    return out


@router.get("/projects/{project_id}/runs")
def project_runs(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> list[dict]:
    project_for(db, ctx, project_id)
    return repo.list_runs(db, project_id)


@router.get("/projects/{project_id}/runs/{run_id}")
def project_run(
    project_id: int, run_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> dict:
    project_for(db, ctx, project_id)
    row = repo.get_run(db, project_id, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return {"id": row.id, "kind": row.kind, "engine_version": row.engine_version,
            "created_at": row.created_at.isoformat(), "result": row.result}  # fmt: skip


@router.post("/projects/{project_id}/evaluate", response_model=EvaluationResult)
def evaluate_project(
    project_id: int, config: Config | None = None, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> EvaluationResult:  # fmt: skip
    p = project_for(db, ctx, project_id)
    try:
        out = svc.evaluate_project(db, p.id, p.spec, config, ctx.workspace_id, ctx.user_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit.usage(db, "analysis", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                project_id=p.id, detail={"kind": "evaluate"})  # fmt: skip
    return out


@router.post("/projects/{project_id}/optimise", response_model=OptimisationResult)
def optimise_project(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer),
    _rl: None = Depends(limit_analyse),
) -> OptimisationResult:  # fmt: skip
    p = project_for(db, ctx, project_id)
    out = svc.optimise_project(db, p.id, p.spec, ctx.workspace_id, ctx.user_id)
    audit.usage(db, "analysis", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                project_id=p.id, detail={"kind": "optimise", "evaluated": out.evaluated},
                commit=False)  # fmt: skip
    audit.audit(db, "analysis.run", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="project", target_id=p.id,
                detail={"project_id": p.id, "version": p.version,
                        "feasibility": out.baseline.feasibility.value}, ip=ctx.ip)  # fmt: skip
    return out


@router.post("/projects/{project_id}/layers")
def project_layers(
    project_id: int, config: Config | None = None, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> dict:  # fmt: skip
    """Where the time goes, layer by layer, for a configuration (default: the current setup)."""
    from app.engine.layers import layer_profile
    from app.engine.optimise import baseline_config
    from app.services.model_lookup import resolve_model

    p = project_for(db, ctx, project_id)
    model = resolve_model(db, p.spec.model_id, ctx.workspace_id)
    hw = load_devices(ctx.workspace_id)[p.spec.device_id]
    cfg = config or p.spec.baseline or baseline_config(hw, p.spec.cooling_id)
    try:
        return layer_profile(model, hw, cfg)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
