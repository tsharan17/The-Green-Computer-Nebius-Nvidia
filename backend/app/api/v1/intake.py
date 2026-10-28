"""Guided intake: supported formats per hardware category, validation, and one upload door.

Every upload is identified, validated, recorded as an artifact (with its validation report) and
answered with an honest status:
  valid          parsed and usable
  needs_review   readable, but values must be confirmed by a person (datasheets, scans)
  invalid        recognised format, but the content is wrong; the report says what to fix
  unsupported    a format with no parser here (schematic, PCB, RTL, netlist, assembly, raw firmware,
                 non-ONNX models); the answer names the exact format that works today
"""

from __future__ import annotations

import json
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.routes import project_for
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.data_loader import load_devices
from app.engine.datasheet_scan import scan_datasheet
from app.engine.design_parsers import (
    DesignParseError,
    detect_design_report,
    parse_hls,
    parse_kicad_pcb,
    parse_kicad_sch,
    parse_verilog,
    parse_vivado_timing,
)
from app.engine.elf_report import ElfError, check_against, elf_footprint
from app.engine.hw_validate import template, validate_profile
from app.engine.hwbuild import CustomHardwareSpec, build_custom
from app.engine.intake_formats import MAX_BYTES, catalogue, unsupported_for
from app.engine.measure_parsers import (
    MeasurementParseError,
    parse_bench_json,
    parse_nvidia_smi_csv,
    parse_tegrastats,
)
from app.engine.vendor_reports import detect, parse_power_csv, parse_report
from app.engine.workload.onnx_parser import ModelParseError
from app.services import intake
from app.services.capabilities import capabilities

router = APIRouter(tags=["intake"])


@router.get("/intake/formats")
def formats(_ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    return catalogue()


@router.get("/intake/templates/device-profile.yaml", response_class=PlainTextResponse)
def profile_template(_ctx: Ctx = Depends(require_ctx)) -> PlainTextResponse:
    body = ("# Device profile template for The Green Computer. Replace every value with your\n"
            "# hardware's figures; units may be written with the value (e.g. '68 GB/s').\n"
            + yaml.safe_dump(template(), sort_keys=False, allow_unicode=True))  # fmt: skip
    return PlainTextResponse(body, media_type="application/yaml", headers={
        "Content-Disposition": 'attachment; filename="device-profile-template.yaml"'})  # fmt: skip


@router.post("/intake/validate/device-profile")
def validate_device_profile(doc: dict[str, Any], _ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    return validate_profile(doc)


@router.get("/capabilities")
def capability_report(request: Request, _ctx: Ctx = Depends(require_ctx)) -> list[dict[str, Any]]:
    return capabilities(request.app.state.engine, request.app.state.email_sender.name,
                        request.app.state.blobs.name)  # fmt: skip


def _answer(art, kind: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"artifact_id": art.id if art is not None else None, "kind": kind, "status": status,
            "message": message, **extra}  # fmt: skip


def _design_message(rep: dict[str, Any]) -> str:
    t = rep["tool"]
    if t == "kicad_sch":
        return (f"KiCad schematic: {len(rep['parts'])} parts, {len(rep['sheets'])} sub-sheet(s). Metadata only; "
                "add it to the system graph to place the parts.")  # fmt: skip
    if t == "kicad_pcb":
        o = rep.get("outline")
        size = f"{o['width_mm']:g} × {o['height_mm']:g} mm, " if o else ""
        return (f"KiCad board: {size}{len(rep['copper_layers'])} copper layers, {len(rep['parts'])} footprints, "
                f"{rep['net_count']} nets. Metadata only: nothing was simulated.")  # fmt: skip
    if t == "verilog":
        return (f"Verilog: {len(rep['modules'])} module(s), top: {', '.join(rep['top_modules'][:4]) or 'none'}. "
                "Structure only: not simulated or synthesised.")  # fmt: skip
    if t == "vivado_timing":
        s = rep["summary"]
        return (f"Vivado timing: WNS {s.get('wns_ns'):g} ns, TNS {s.get('tns_ns', 0):g} ns; constraints "
                f"{'met' if rep['constraints_met'] else 'NOT met'} according to Vivado.")  # fmt: skip
    lat = rep.get("latency_cycles") or {}
    g = lambda v: "?" if v is None else f"{v:g}"  # noqa: E731
    return (f"HLS report for {rep.get('top')}: latency {g(lat.get('best'))}–{g(lat.get('worst'))} cycles at an "
            f"estimated {g(rep.get('estimated_clock_ns'))} ns clock (HLS estimate, before implementation).")  # fmt: skip


def _design_summary(rep: dict[str, Any]) -> dict[str, Any]:
    """The upload answer carries a short summary; the full parse stays in the artifact's report."""
    out = {k: v for k, v in rep.items() if k not in ("parts", "connections", "modules")}
    if "parts" in rep:
        out["parts_sample"] = rep["parts"][:25]
        out["parts_total"] = len(rep["parts"])
    if "modules" in rep:
        out["modules_sample"] = [{k: m[k] for k in ("name", "port_count", "clocks", "line")} | {"instances": len(m["instances"])}
                                 for m in rep["modules"][:25]]  # fmt: skip
        out["modules_total"] = len(rep["modules"])
    return out


@router.post("/intake/upload")
async def upload(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    category: str | None = Query(None, pattern="^(model|board_soc|mcu|fpga|accelerator|custom_system)$"),
    project_id: int | None = None,
    supply_v: float | None = Query(None, gt=0),
    dynamic_dim_default: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
) -> dict[str, Any]:
    blobs = request.app.state.blobs
    name = filename.lower()
    is_model = name.endswith(".onnx") or category == "model"
    limit = MAX_BYTES["model" if is_model else "hardware"]
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file too large (limit {limit // (1024 * 1024)} MB)")
    data = await request.body()
    if len(data) > limit:
        raise HTTPException(413, f"file too large (limit {limit // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(422, "the file is empty")
    spec = project_for(db, ctx, project_id).spec if project_id is not None else None

    def store(kind: str, status: str, report: dict[str, Any], keep: bool = True):
        return intake.store_artifact(blobs, db, ctx, data, filename, kind, status, report, project_id,
                                     keep_bytes=keep)  # fmt: skip

    fmt = unsupported_for(filename)
    if fmt is not None:
        art = store("unsupported", "unsupported", {"format": fmt["id"], "required": fmt["required"]}, keep=False)
        verb = "is planned" if fmt["status"] == "planned" else "is not accepted"
        return _answer(art, "unsupported", "unsupported",
                       f"{fmt['name']} {verb}: {fmt['gives']}", format=fmt, required=fmt["required"])  # fmt: skip

    if name.endswith(".onnx"):
        try:
            stored, parsed, art = intake.store_onnx_model(
                blobs, db, ctx, data, filename, None, dynamic_dim_default, 1.0,
                "not provided (relative accuracy only)", project_id,
            )  # fmt: skip
        except ModelParseError as e:
            dynamic = "dynamic dim" in str(e).lower()
            return _answer(None, "onnx_model", "invalid", str(e), needs_dynamic_dim=dynamic,
                           hint="Give a value for the dynamic dimensions (e.g. the input size) and upload again."
                           if dynamic else "Fix the export (see the formats table) and upload again.")  # fmt: skip
        return _answer(art, "onnx_model", "valid",
                       f"{stored.n_layers} compute layers, {stored.params_m:.2f} M parameters, "
                       f"{stored.gflops:.3f} GFLOPs per input.", model=stored.model_dump(mode="json", exclude={"layers", "tensors"}),
                       report=art.validation, warnings=parsed.warnings)  # fmt: skip

    if name.endswith((".elf", ".axf", ".out")) or data[:4] == b"\x7fELF":
        try:
            fp = elf_footprint(data)
        except ElfError as e:
            art = store("firmware_elf", "invalid", {"errors": [str(e)]}, keep=False)
            return _answer(art, "firmware_elf", "invalid", str(e))
        checks: list[dict[str, Any]] = []
        if spec is not None:
            hw = load_devices(ctx.workspace_id).get(spec.device_id)
            if hw is not None:
                flash_kb = hw.weights_mem_gb * 1e9 / 1024 if hw.weights_mem_gb else None
                checks = check_against(fp, flash_kb, hw.mem_gb * 1e9 / 1024)
                fp["device"] = hw.name
        fp["checks"] = checks
        art = store("firmware_elf", "valid", fp)
        return _answer(art, "firmware_elf", "valid",
                       f"{fp['machine']} {fp['type']}: {fp['flash_bytes'] / 1024:.1f} KB flash, "
                       f"{fp['ram_bytes'] / 1024:.1f} KB RAM (static).", report=fp)  # fmt: skip

    design = {".kicad_sch": ("kicad_schematic", parse_kicad_sch), ".kicad_pcb": ("kicad_pcb", parse_kicad_pcb),
              ".v": ("verilog", parse_verilog), ".sv": ("verilog", parse_verilog)}  # fmt: skip
    ext = next((e for e in design if name.endswith(e)), None)
    if ext is not None or (name.endswith(".xml") and detect_design_report(data[:200_000].decode("utf-8", "replace")) == "hls_report"):
        kind, parser = design[ext] if ext is not None else ("hls_report", parse_hls)
        try:
            rep = parser(data.decode("utf-8", errors="replace"))
        except DesignParseError as e:
            art = store(kind, "invalid", {"errors": [str(e)]}, keep=False)
            return _answer(art, kind, "invalid", str(e))
        art = store(kind, "valid", rep)
        return _answer(art, kind, "valid", _design_message(rep), report=_design_summary(rep), warnings=rep.get("warnings", []))

    if name.endswith(".pdf"):
        rep = scan_datasheet(data)
        art = store("datasheet_pdf", rep["status"], rep, keep=rep["status"] != "invalid")
        return _answer(art, "datasheet_pdf", rep["status"], rep["message"], report=rep)

    text = data.decode("utf-8", errors="replace")
    if name.endswith((".yaml", ".yml", ".json")):
        try:
            doc = json.loads(text) if name.endswith(".json") else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            art = store("structured", "invalid", {"errors": [f"not valid {'JSON' if name.endswith('.json') else 'YAML'}: {e}"]}, keep=False)
            return _answer(art, "structured", "invalid", f"The file is not valid {'JSON' if name.endswith('.json') else 'YAML'}: {e}")
        if isinstance(doc, dict) and doc.get("schema", "").startswith("green-computer.bench"):
            try:
                recs = parse_bench_json(text)
            except MeasurementParseError as e:
                art = store("measurement_file", "invalid", {"errors": [str(e)]}, keep=False)
                return _answer(art, "measurement_file", "invalid", str(e))
            art = store("measurement_file", "valid", {"records": len(recs)})
            return _answer(art, "measurement_file", "valid",
                           f"{len(recs)} measurement record(s). Import them under Verify to compare with predictions.",
                           records=[r.model_dump(mode="json") for r in recs])  # fmt: skip
        if isinstance(doc, dict) and "style" in doc and "name" in doc:
            try:
                hspec = CustomHardwareSpec.model_validate(doc)
                preview = build_custom(hspec, "preview")
            except ValidationError as e:
                errs = [f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors()[:6]]
                art = store("hardware_description", "invalid", {"errors": errs}, keep=False)
                return _answer(art, "hardware_description", "invalid", "The hardware description has problems.",
                               report={"errors": errs})  # fmt: skip
            art = store("hardware_description", "valid", {"name": hspec.name, "style": hspec.style})
            return _answer(art, "hardware_description", "valid", f"Hardware description for {hspec.name}.",
                           spec=hspec.model_dump(mode="json", exclude_none=True), preview=preview.model_dump(mode="json"))  # fmt: skip
        if isinstance(doc, dict) and "model" in doc and ("device" in doc or "custom" in doc):
            from app.api.v1.files import _import_project

            try:
                out = _import_project(db, doc, ctx.workspace_id)
            except HTTPException as e:
                art = store("project_file", "invalid", {"errors": [str(e.detail)]}, keep=False)
                return _answer(art, "project_file", "invalid", str(e.detail))
            art = store("project_file", "valid", {"model": doc.get("model")})
            return _answer(art, "project_file", "valid", "Project file read.", project=out)
        if isinstance(doc, dict) and ({"peak_tflops", "power_modes", "cooling_options"} & doc.keys()):
            rep = validate_profile(doc)
            art = store("device_profile", "valid" if rep["ok"] else "invalid", rep, keep=rep["ok"])
            return _answer(art, "device_profile", "valid" if rep["ok"] else "invalid",
                           "Device profile is valid. Review it, then add it to your hardware."
                           if rep["ok"] else f"{len(rep['errors'])} problem(s) to fix in the profile.",
                           report=rep)  # fmt: skip
        art = store("structured", "invalid", {"errors": ["unrecognised content"]}, keep=False)
        return _answer(art, "structured", "invalid",
                       "Could not tell what this file is. Expected a device profile (peak_tflops, "
                       "power_modes, cooling_options...), a hardware description (name + style), a "
                       "project file (model + device) or a benchmark result (schema: green-computer.bench/1).")  # fmt: skip

    if name.endswith(".csv"):
        head = text[:2000].lower()
        if "power.draw" in head or "temperature.gpu" in head:
            try:
                tr = parse_nvidia_smi_csv(text)
            except MeasurementParseError as e:
                art = store("telemetry_log", "invalid", {"errors": [str(e)]}, keep=False)
                return _answer(art, "telemetry_log", "invalid", str(e))
            rep = {"source": "nvidia-smi", **tr.__dict__}
            art = store("telemetry_log", "valid", rep)
            return _answer(art, "telemetry_log", "valid", f"nvidia-smi log: {tr.samples} samples.", report=rep)
        try:
            rep = parse_power_csv(text, supply_v)
        except ValueError as e:
            art = store("power_log", "invalid", {"errors": [str(e)]}, keep=False)
            return _answer(art, "power_log", "invalid", str(e))
        art = store("power_log", "valid", rep)
        return _answer(art, "power_log", "valid", "Power log read (measured values).", report=rep)

    if name.endswith((".rpt", ".txt", ".log", ".md")):
        if "RAM " in text[:4000] and ("GR3D_FREQ" in text or "VDD_IN" in text or "POM_5V_IN" in text):
            try:
                tr = parse_tegrastats(text)
            except MeasurementParseError as e:
                art = store("telemetry_log", "invalid", {"errors": [str(e)]}, keep=False)
                return _answer(art, "telemetry_log", "invalid", str(e))
            rep = {"source": "tegrastats", **tr.__dict__}
            art = store("telemetry_log", "valid", rep)
            return _answer(art, "telemetry_log", "valid", f"tegrastats log: {tr.samples} samples.", report=rep)
        dtool = detect_design_report(text)
        if dtool is not None:
            kind = "hls_report" if dtool == "hls_report" else "vendor_report"
            try:
                rep = parse_hls(text) if dtool == "hls_report" else parse_vivado_timing(text)
            except DesignParseError as e:
                art = store(kind, "invalid", {"errors": [str(e)]}, keep=False)
                return _answer(art, kind, "invalid", str(e))
            art = store(kind, "valid", rep)
            return _answer(art, kind, "valid", _design_message(rep), report=rep)
        tool = detect(text)
        if tool is None:
            art = store("vendor_report", "invalid", {"errors": ["unrecognised report"]}, keep=False)
            return _answer(art, "vendor_report", "invalid",
                           "Could not tell which tool wrote this report. Supported: Vivado utilisation/power/timing, HLS csynth, "
                           "ST Edge AI analyze, Arm Vela summary, tegrastats.")  # fmt: skip
        try:
            rep = parse_report(text, tool)
        except ValueError as e:
            art = store("vendor_report", "invalid", {"errors": [str(e)]}, keep=False)
            return _answer(art, "vendor_report", "invalid", str(e))
        art = store("vendor_report", "valid", rep)
        return _answer(art, "vendor_report", "valid", f"{tool} report read.", report=rep)

    art = store("unknown", "unsupported", {"filename": filename}, keep=False)
    return _answer(art, "unknown", "unsupported",
                   "This file type is not recognised. See the supported formats for your hardware category.")
