"""One door for every file a person can drop in: the app works out what it is.

  .yaml/.yml/.json   a project file (greencomputer.yaml), a custom-hardware description, or a
                     full device profile
  .rpt/.txt/.log     a vendor tool report (Vivado, ST Edge AI, Vela)
  .csv               a power-meter capture (Joulescope, Otii, PPK2...)
  .pdf               a datasheet: text is extracted here, specs are read by the Green Agent and
                     must be confirmed by a person (same gate as web datasheet import)

ONNX models go to /models/upload (they need options such as the dynamic-dimension default).
Nothing is executed; text is parsed as data, and uploads are size limited.
"""

from __future__ import annotations

import io
import json
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.client import LLMError
from app.agent.datasheet import DatasheetError, extract_hardware_spec
from app.api.v1.agent import _llm, _models
from app.auth.deps import Ctx, get_db, require_writer
from app.data_loader import load_devices, load_models
from app.db import repository as repo
from app.engine.hwbuild import CustomHardwareSpec, build_custom
from app.engine.vendor_reports import detect, parse_power_csv, parse_report
from app.integrations.tavily import SearchHit
from app.schemas.hardware import HardwareProfile
from app.sdk import ProjectFileError, load_project
from app.security import limit_datasheet, max_custom_items
from app.services import hardware_service as hw

router = APIRouter()

MAX_FILE_BYTES = 20 * 1024 * 1024
PDF_MAX_PAGES = 40
PDF_MAX_CHARS = 60_000


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - pypdf is a declared dependency
        raise HTTPException(503, "PDF reading is not installed (pip install pypdf).") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:PDF_MAX_PAGES]:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > PDF_MAX_CHARS:
                break
    except Exception as e:  # noqa: BLE001 - any parse failure is a user-facing 422
        raise HTTPException(422, f"could not read this PDF: {e}") from e
    text = "\n".join(parts).strip()
    if len(text) < 200:
        raise HTTPException(
            422, "this PDF has almost no extractable text (is it a scan?). Paste the specs by hand."
        )
    return text[:PDF_MAX_CHARS]


def _structured(text: str, name: str) -> dict[str, Any]:
    try:
        doc = json.loads(text) if name.endswith(".json") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise HTTPException(422, f"{name}: not valid {'JSON' if name.endswith('.json') else 'YAML'} ({e})") from e
    if not isinstance(doc, dict):
        raise HTTPException(422, f"{name}: expected an object at the top level")
    return doc


def _import_project(db: Session, doc: dict[str, Any], ws: int) -> dict[str, Any]:
    """Turn a greencomputer.yaml into a project spec the UI can load (and create hardware)."""
    warnings: list[str] = []
    created = None
    doc = dict(doc)
    if "custom" in doc:
        cap = max_custom_items()
        if cap and len(hw.list_drafts(db, ws)) >= cap:
            raise HTTPException(503, "This workspace has reached its limit of custom devices.")
        try:
            created = hw.register_custom(db, CustomHardwareSpec.model_validate(doc.pop("custom")), ws)
        except ValidationError as e:
            raise HTTPException(422, f"custom hardware: {e.errors()[0]['msg']}") from e
        doc["device"] = created.id
        warnings.append(f"Created your hardware '{created.name}'.")
    model_ref = doc.get("model")
    custom_ids = {m.id for m in repo.list_custom_models(db, ws)}
    real_model = model_ref if isinstance(model_ref, str) and (
        model_ref in load_models() or model_ref in custom_ids
    ) else None
    if real_model is None:
        raise HTTPException(
            422,
            f"model '{model_ref}' is not in the library or among your uploaded models. Upload the "
            "ONNX file first, then import the project file with its model name.",
        )
    probe = {**doc, "model": "mobilenet-v2"} if real_model in custom_ids else doc
    device_ref = doc.get("device")
    if isinstance(device_ref, str) and device_ref.startswith("custom-") and device_ref not in load_devices(ws):
        raise HTTPException(422, f"device '{device_ref}' is not in this workspace")
    try:
        p = load_project(probe, ws)
    except ProjectFileError as e:
        raise HTTPException(422, str(e)) from e
    spec = p.spec.model_copy(update={"model_id": real_model})
    return {"kind": "project", "spec": spec.model_dump(mode="json"), "budgets": p.budgets,
            "hardware_created": created.id if created else None, "warnings": warnings}  # fmt: skip


@router.post("/files/inspect")
async def inspect_file(
    request: Request,
    filename: str = Query(min_length=1, max_length=200),
    supply_v: float | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> dict[str, Any]:
    ws = ctx.workspace_id
    data = await request.body()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"file too large (limit {MAX_FILE_BYTES // 1024 // 1024} MB)")
    if not data:
        raise HTTPException(422, "the file is empty")
    name = filename.lower()

    if name.endswith(".pdf"):
        limit_datasheet(request)
        text = _pdf_text(data)
        try:
            llm = _llm(request)
            model_id = _models(request, llm).for_task("fast")
            hit = SearchHit(filename, f"uploaded:{filename}", text[:400], 1.0, text)
            ex = extract_hardware_spec(llm, model_id, filename.rsplit(".", 1)[0], [hit])
        except LLMError as e:
            raise HTTPException(
                503,
                f"{e}. Reading datasheets needs the Green Agent (TOKEN_FACTORY_API_KEY). "
                "You can describe the hardware by hand instead.",
            ) from e
        except DatasheetError as e:
            raise HTTPException(422, str(e)) from e
        draft = hw.store_draft(db, filename.rsplit(".", 1)[0], ex, [f"uploaded:{filename}"], ws)
        return {"kind": "datasheet", "draft": draft}

    text = data.decode("utf-8", errors="replace")

    if name.endswith((".yaml", ".yml", ".json")):
        doc = _structured(text, name)
        if "style" in doc and "name" in doc:  # a custom-hardware description
            try:
                spec = CustomHardwareSpec.model_validate(doc)
                preview = build_custom(spec, "preview")
            except ValidationError as e:
                msg = "; ".join(x["msg"] for x in e.errors()[:3])
                raise HTTPException(422, f"hardware description: {msg}") from e
            return {"kind": "hardware_spec", "spec": spec.model_dump(mode="json", exclude_none=True),
                    "preview": preview.model_dump(mode="json")}  # fmt: skip
        if "model" in doc and ("device" in doc or "custom" in doc):
            return _import_project(db, doc, ws)
        if {"peak_tflops", "power_modes", "cooling_options"} <= doc.keys():
            try:
                prof = HardwareProfile.model_validate(doc)
            except ValidationError as e:
                raise HTTPException(422, f"device profile: {e.errors()[0]['msg']}") from e
            return {"kind": "hardware_profile", "profile": prof.model_dump(mode="json")}
        raise HTTPException(
            422,
            "could not tell what this file is. Expected a project file (model + device), a "
            "hardware description (name + style) or a full device profile.",
        )

    if name.endswith(".csv"):
        try:
            return {"kind": "power", **parse_power_csv(text, supply_v)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    if name.endswith((".rpt", ".txt", ".log", ".md")):
        try:
            tool = detect(text)
            if tool is None:
                raise ValueError("could not tell which tool wrote this report")
            return {"kind": "report", **parse_report(text, tool)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    if name.endswith((".onnx", ".tflite", ".pt", ".pth", ".pb", ".h5", ".safetensors")):
        raise HTTPException(
            422,
            "models are uploaded with the model uploader (ONNX only). Export PyTorch/TensorFlow "
            "models to ONNX first; .pt and .pkl files are refused on purpose.",
        )
    raise HTTPException(422, f"unsupported file type: {filename}")


@router.post("/hardware/profile", status_code=201, response_model=HardwareProfile)
def register_profile(
    body: HardwareProfile, db: Session = Depends(get_db), ctx: Ctx = Depends(require_writer)
) -> HardwareProfile:
    """Register a full device profile the user uploaded (after seeing what it contains)."""
    if body.id in load_devices() and not body.id.startswith("custom-"):
        raise HTTPException(409, f"'{body.id}' is a built-in device; choose another id")
    prof = body if body.id.startswith("custom-") else body.model_copy(update={"id": f"custom-{body.id}"})
    return hw.register_profile(db, prof, ctx.workspace_id)
