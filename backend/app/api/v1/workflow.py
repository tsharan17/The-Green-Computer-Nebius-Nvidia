"""Workflow stages: verification report, pipeline status, structured recommendations (engine and
optional AI candidates) and the cooling sweep with its figures."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.client import LLMError
from app.api.v1.agent import DISABLED, _llm, _models
from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.config import get_llm_settings
from app.render import figures
from app.security import limit_agent
from app.services import audit
from app.services import plan_service as plans
from app.services import workflow_service as wf

router = APIRouter(tags=["workflow"])
_SWEEP_CACHE: dict[tuple[int, int, str, str], bytes] = {}
_SWEEP_CACHE_MAX = 64


@router.get("/projects/{project_id}/verification")
def project_verification(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Verification and Requirements Manager report for the project's current inputs."""
    return wf.verification(db, project_for(db, ctx, project_id), ctx.workspace_id)


@router.get("/projects/{project_id}/pipeline")
def project_pipeline(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Each workflow stage with its stored record and whether it is done, ready, blocked or stale."""
    return wf.pipeline(db, project_for(db, ctx, project_id), ctx.workspace_id)


@router.get("/projects/{project_id}/recommendations")
def project_recommendations(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Software and hardware recommendations from the latest analysis, ranked by the project's
    requirements, with each one's validation state."""
    return wf.recommendations(db, project_for(db, ctx, project_id), ctx.workspace_id)


class AiIn(BaseModel):
    refresh: bool = False


@router.post("/projects/{project_id}/recommendations/ai")
def project_ai_candidates(
    project_id: int, request: Request, body: AiIn | None = None, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer), _rl: None = Depends(limit_agent),
) -> dict[str, Any]:  # fmt: skip
    """Candidate changes from the optional language model. Sends only the engine's numbers and the
    ids of changes it can evaluate; never file contents. Cached per analysis."""
    p = project_for(db, ctx, project_id)
    if not get_llm_settings().configured:
        raise HTTPException(503, DISABLED)
    try:
        llm = _llm(request)
        model = _models(request, llm).for_task("recommend")
        out = wf.ai_candidates(db, p, ctx.workspace_id, ctx.user_id, llm, model, refresh=bool(body and body.refresh))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, f"The AI provider could not produce candidates: {e}") from e
    if not out.get("cached"):
        audit.usage(db, "ai_tokens", float((out.get("usage") or {}).get("total_tokens") or 0), workspace_id=ctx.workspace_id, user_id=ctx.user_id, project_id=p.id,
                    detail={"kind": "recommendations", "model": out.get("model"),
                            "tokens": (out.get("usage") or {}).get("total_tokens")})  # fmt: skip
    return out


@router.get("/projects/{project_id}/cooling-sweep")
def project_cooling_sweep(project_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Temperature over ambient x input rate for every cooling option, and power mode x cooling."""
    try:
        return plans.cooling_sweep(db, project_for(db, ctx, project_id), ctx.workspace_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e).strip("'")) from e


@router.get("/projects/{project_id}/figures/{name}")
def project_figure(
    project_id: int, name: str, theme: Literal["light", "dark"] = "light", db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_ctx),
) -> Response:  # fmt: skip
    """Seaborn/matplotlib cooling figures (PNG) drawn from the engine's cooling sweep."""
    p = project_for(db, ctx, project_id)
    if name not in figures.SWEEP_FIGURES:
        raise HTTPException(404, f"unknown figure; choose one of {', '.join(sorted(figures.SWEEP_FIGURES))}")
    key = (p.id, p.version, name, theme)
    png = _SWEEP_CACHE.get(key)
    if png is None:
        try:
            png = figures.render_sweep(name, plans.cooling_sweep(db, p, ctx.workspace_id), theme)
        except (figures.FigureError, KeyError, ValueError) as e:
            raise HTTPException(422, str(e).strip("'")) from e
        if len(_SWEEP_CACHE) >= _SWEEP_CACHE_MAX:
            _SWEEP_CACHE.pop(next(iter(_SWEEP_CACHE)))
        _SWEEP_CACHE[key] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
