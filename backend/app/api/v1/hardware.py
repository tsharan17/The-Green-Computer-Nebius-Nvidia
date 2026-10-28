"""Hardware import from datasheets (Tavily + Nemotron) with mandatory human confirmation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.client import LLMError
from app.agent.datasheet import DatasheetError, clean_extraction
from app.api.v1.agent import _llm, _models
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.engine.hwbuild import CustomHardwareSpec, build_custom
from app.engine.vendor_reports import parse_power_csv, parse_report
from app.integrations import tavily as tv
from app.schemas.hardware import HardwareProfile
from app.security import limit_datasheet, max_custom_items
from app.services import audit
from app.services import hardware_service as hw

router = APIRouter()

TAVILY_OFF = (
    "Datasheet import needs a Tavily key (TAVILY_API_KEY in .env, free via the Nebius Builder "
    "Program) and the Green Agent (TOKEN_FACTORY_API_KEY). You can also enter a device by hand."
)


def default_tavily_factory() -> tv.TavilyClient:
    return tv.TavilyClient()


class ImportIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


@router.post("/hardware/import", status_code=201)
def import_hardware(
    body: ImportIn, request: Request, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> dict[str, Any]:  # fmt: skip
    limit_datasheet(request)
    factory = getattr(request.app.state, "tavily_factory", default_tavily_factory)
    try:
        tavily = factory()
        llm = _llm(request)
        model_id = _models(request, llm).for_task("fast")
        return hw.create_draft(db, body.name, tavily, llm, model_id, ctx.workspace_id)
    except (tv.TavilyError, LLMError) as e:
        raise HTTPException(503, f"{e}. {TAVILY_OFF}") from e
    except DatasheetError as e:
        raise HTTPException(422, str(e)) from e


class ManualIn(BaseModel):
    """Enter a device by hand (no LLM or search): goes through the same draft -> confirm gate."""

    name: str = Field(min_length=2, max_length=120)
    vendor: str | None = None
    kind: str | None = None
    peak_tflops: dict[str, float]
    mem_bw_gbs: float = Field(gt=0)
    mem_gb: float = Field(gt=0)
    power_modes: list[dict[str, Any]] = []
    tdp_w: float | None = None
    t_throttle_c: float | None = None


@router.post("/hardware/manual", status_code=201)
def manual_draft(
    body: ManualIn, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> dict[str, Any]:
    cap = max_custom_items()
    if cap and len(hw.list_drafts(db, ctx.workspace_id)) >= cap:
        raise HTTPException(503, "This demo has reached its limit of imported devices.")
    raw = body.model_dump(exclude_none=True)
    ex = clean_extraction({**raw, "sources": {}}, set())
    ex.evidence = [{"field": k, "value": v, "source_url": None} for k, v in ex.candidate.items()]
    return hw.store_draft(db, body.name, ex, [], ctx.workspace_id)


@router.post("/hardware/custom/preview", response_model=HardwareProfile)
def custom_preview(body: CustomHardwareSpec, _ctx: Ctx = Depends(require_ctx)) -> HardwareProfile:
    """What the wizard would create, without saving it (live preview of derived values)."""
    return build_custom(body, "preview")


@router.post("/hardware/custom", status_code=201, response_model=HardwareProfile)
def custom_create(
    body: CustomHardwareSpec, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> HardwareProfile:
    cap = max_custom_items()
    if cap and len(hw.list_drafts(db, ctx.workspace_id)) >= cap:
        raise HTTPException(503, "This workspace has reached its limit of custom devices.")
    out = hw.register_custom(db, body, ctx.workspace_id)
    audit.audit(db, "hardware.created", workspace_id=ctx.workspace_id, user_id=ctx.user_id,
                target_type="device", target_id=out.id, detail={"name": out.name}, ip=ctx.ip)  # fmt: skip
    return out


@router.delete("/hardware/custom/{device_id}")
def custom_delete(
    device_id: str, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> dict[str, bool]:
    if not hw.remove_custom(db, device_id, ctx.workspace_id):
        raise HTTPException(404, "custom device not found")
    return {"deleted": True}


class ReportIn(BaseModel):
    text: str = Field(min_length=10, max_length=400_000)
    tool: str | None = None
    supply_v: float | None = Field(None, gt=0)


@router.post("/hardware/parse-report")
def parse_vendor_report(body: ReportIn, _ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    """Pull numbers out of a Vivado / ST Edge AI / Vela report or a power-meter CSV."""
    try:
        if body.tool == "power_csv":
            return parse_power_csv(body.text, body.supply_v)
        return parse_report(body.text, body.tool)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/hardware/drafts")
def drafts(db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    return hw.list_drafts(db, ctx.workspace_id)


@router.get("/hardware/drafts/{draft_id}")
def draft(
    draft_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_ctx)
) -> dict[str, Any]:
    d = hw.get_draft(db, draft_id, ctx.workspace_id)
    if d is None:
        raise HTTPException(404, "draft not found")
    return d


class ConfirmIn(BaseModel):
    overrides: dict[str, Any] = {}


@router.post("/hardware/drafts/{draft_id}/confirm", response_model=HardwareProfile)
def confirm_draft(
    draft_id: int, body: ConfirmIn, db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> HardwareProfile:  # fmt: skip
    """Only a person can confirm: the agent has no tool for this."""
    try:
        return hw.confirm(db, draft_id, body.overrides, ctx.workspace_id)
    except DatasheetError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/hardware/drafts/{draft_id}/reject")
def reject_draft(
    draft_id: int, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> dict[str, Any]:
    try:
        return hw.reject(db, draft_id, ctx.workspace_id)
    except DatasheetError as e:
        raise HTTPException(404, str(e)) from e
