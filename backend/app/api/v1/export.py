"""Report and recipe export, rendered from the stored analysis."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx
from app.data_loader import get_device
from app.db import repository as repo
from app.engine.recipe import build_recipe
from app.engine.report import build_report_md, md_to_html
from app.schemas.results import OptimisationResult
from app.services import audit
from app.services.model_lookup import resolve_model

router = APIRouter()


def _load(db: Session, ctx: Ctx, project_id: int):
    p = project_for(db, ctx, project_id)
    raw = repo.latest_run(db, project_id, "optimise")
    if raw is None:
        raise HTTPException(404, "Run the analysis first (Analyse), then export.")
    try:
        res = OptimisationResult.model_validate(raw)
    except ValidationError as e:
        raise HTTPException(409, "The stored analysis is from an older version; run it again.") from e
    ws = ctx.workspace_id
    return p.spec, resolve_model(db, p.spec.model_id, ws), get_device(p.spec.device_id, ws), res


def _exported(db: Session, ctx: Ctx, project_id: int, what: str) -> None:
    audit.usage(db, "export", 1, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                project_id=project_id, detail={"what": what}, commit=False)  # fmt: skip
    audit.audit(db, "export.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="project", target_id=project_id,
                detail={"what": what, "project_id": project_id}, ip=ctx.ip)  # fmt: skip


@router.get("/projects/{project_id}/report")
def report(
    project_id: int,
    format: str = Query("md", pattern="^(md|html)$"),
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> Response:
    spec, model, hw, res = _load(db, ctx, project_id)
    md = build_report_md(spec, model, hw, res, datetime.now(UTC))
    _exported(db, ctx, project_id, f"report.{format}")
    if format == "html":
        return HTMLResponse(md_to_html(md, f"Report: {model.name} on {hw.name}"))
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.get("/projects/{project_id}/recipe")
def recipe(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> dict[str, Any]:
    spec, model, hw, res = _load(db, ctx, project_id)
    files, warnings = build_recipe(spec, model, hw, res)
    return {
        "files": {n: {"description": f.description, "content": f.content} for n, f in files.items()},
        "warnings": warnings,
    }


@router.get("/projects/{project_id}/recipe.zip")
def recipe_zip(
    project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> Response:
    spec, model, hw, res = _load(db, ctx, project_id)
    files, _ = build_recipe(spec, model, hw, res)
    _exported(db, ctx, project_id, "recipe.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, f in files.items():
            z.writestr(name, f.content)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="green-computer-recipe-{project_id}.zip"'},
    )
