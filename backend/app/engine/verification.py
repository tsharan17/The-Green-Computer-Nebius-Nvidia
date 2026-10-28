"""Verification and Requirements Manager: is this project ready to analyse, and what is missing?

Deterministic checks over the project's inputs (model, hardware, cooling, workload, requirements)
and the files uploaded to it. A file that uploaded is not a file that is correct: the report only
repeats what each file's validator found, and never says a design is electrically or functionally
right. Pure: no I/O; the caller resolves the model, device and file rows.
"""

from __future__ import annotations

from typing import Any, Literal

from app.schemas.hardware import HardwareProfile
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectSpec

SCHEMA = "green-computer.verification/1"
Status = Literal["pass", "warn", "fail", "info"]
BYTES_PER = {"fp32": 4, "fp16": 2, "int8": 1}
# What a file validator's status means for the workflow (ArtifactRow.status values).
FILE_STATUS = {
    "valid": ("pass", "read and checked"),
    "needs_review": ("warn", "read, but some values need your review"),
    "invalid": ("fail", "could not be read"),
    "unsupported": ("info", "stored but not analysed: this format is not parsed yet"),
}


def _c(cid: str, area: str, status: Status, message: str, fix: str = "") -> dict[str, Any]:
    return {"id": cid, "area": area, "status": status, "message": message, "fix": fix}


def verify(
    spec: ProjectSpec,
    model: ModelProfile | None,
    hw: HardwareProfile | None,
    files: list[dict[str, Any]] | None = None,
    measured_accuracy: bool = False,
    version: int | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    add = checks.append

    # 1. AI model
    if model is None:
        add(_c("model.present", "model", "fail", f"The model “{spec.model_id}” is not in the library or this workspace.",
               "Pick a library model or upload an ONNX file on the Input step."))  # fmt: skip
    else:
        add(_c("model.present", "model", "pass", f"{model.name}: {model.params_m:g} M parameters, {model.gflops:g} GFLOPs per input."))
        if model.fidelity == "manual":
            add(_c("model.fidelity", "model", "warn", "The model was described by hand, so layer-level analysis is not available.",
                   "Upload the ONNX file for a per-layer memory and time breakdown."))  # fmt: skip
        elif model.layers:
            add(_c("model.fidelity", "model", "pass", f"Layer graph available ({len(model.layers)} layers)."))
        if measured_accuracy:
            add(_c("model.accuracy", "model", "pass", "Measured accuracy is on file for at least one precision."))
        else:
            add(_c("model.accuracy", "model", "info",
                   f"No measured accuracy: the effect of lower precision on {model.accuracy_metric} is an estimate.",
                   "Run an INT8 comparison on your own data (Validate step) to replace the estimate."))  # fmt: skip

    # 2. Hardware and cooling
    if hw is None:
        add(_c("hardware.present", "hardware", "fail", f"The device “{spec.device_id}” is not in the library or this workspace.",
               "Pick a library board or import a device profile."))  # fmt: skip
    else:
        add(_c("hardware.present", "hardware", "pass", f"{hw.name}: {hw.mem_gb:g} GB memory, {len(hw.power_modes)} power modes."))
        if not hw.verified:
            add(_c("hardware.verified", "hardware", "info",
                   "This device profile has not been checked against real measurements; results are predictions.",
                   "Import a power or timing log from the board to calibrate it."))  # fmt: skip
        cid = spec.cooling_id or hw.default_cooling
        if cid not in {c.id for c in hw.cooling_options}:
            add(_c("hardware.cooling", "hardware", "fail", f"Cooling “{cid}” is not an option on {hw.name}.",
                   "Choose one of the device's cooling options."))  # fmt: skip
        else:
            add(_c("hardware.cooling", "hardware", "pass", f"Cooling: {hw.cooling(cid).name}."))

    # 3. Workload and requirements
    w, k = spec.workload, spec.constraints
    if not -40 <= w.ambient_c <= 85:
        add(_c("workload.ambient", "workload", "warn", f"Ambient {w.ambient_c:g} °C is outside the usual −40…85 °C range.",
               "Check the unit (°C) and the operating environment."))  # fmt: skip
    else:
        add(_c("workload.ambient", "workload", "pass", f"{w.fps:g} inputs/s at {w.ambient_c:g} °C, {w.hours_per_day:g} h/day."))
    unset = [n for n, v in (("latency", k.max_latency_ms), ("memory", k.max_memory_gb)) if v is None]
    if unset:
        add(_c("requirements.optional", "requirements", "info",
               f"No limit set for {' or '.join(unset)}; the analysis reports it but does not check it.",
               "Set it on the Requirements step if it matters for this application."))  # fmt: skip

    # 4. Consistency between model, hardware and requirements
    if model is not None and hw is not None:
        smallest = min(BYTES_PER.get(p, 4) for p in hw.peak_tflops) if hw.peak_tflops else 4
        weights_gb = model.params_m * 1e6 * smallest / 1e9
        if weights_gb > hw.mem_gb:
            add(_c("fit.memory", "consistency", "fail",
                   f"The weights alone need {weights_gb:.2f} GB at the lowest supported precision; {hw.name} has {hw.mem_gb:g} GB.",
                   "Choose a smaller model or a device with more memory."))  # fmt: skip
        else:
            add(_c("fit.memory", "consistency", "pass", f"Weights fit in memory ({weights_gb:.2f} GB of {hw.mem_gb:g} GB at the lowest precision)."))
        lowest_cap = min(m.cap_w for m in hw.power_modes)
        if k.max_power_w < hw.idle_w:
            add(_c("fit.power", "consistency", "fail",
                   f"The power limit {k.max_power_w:g} W is below the device's idle power ({hw.idle_w:g} W).",
                   "Raise the power limit or choose a lower-power device."))  # fmt: skip
        elif k.max_power_w < lowest_cap:
            add(_c("fit.power", "consistency", "warn",
                   f"The power limit {k.max_power_w:g} W is below the lowest power mode ({lowest_cap:g} W); results depend on the workload staying light.",
                   ""))  # fmt: skip
        if spec.baseline is not None and spec.baseline.precision not in hw.peak_tflops:
            add(_c("fit.precision", "consistency", "fail",
                   f"Your current setup uses {spec.baseline.precision.upper()}, which {hw.name} does not support.",
                   "Change the current setup's precision on the Input step."))  # fmt: skip

    # 5. Uploaded files: repeat what each validator found; never more.
    for f in files or []:
        st, meaning = FILE_STATUS.get(f.get("status", ""), ("warn", "status unknown"))
        msg = f"{f.get('filename', 'file')} ({f.get('kind', 'file')}): {meaning}."
        fix = ""
        if st == "info":
            fix = f.get("needed") or "Upload a supported representation (see the list of formats)."
        elif st == "fail":
            fix = f.get("error") or "Upload the file again or export it in a supported format."
        add(_c(f"file.{f.get('id')}", "files", st, msg, fix))  # type: ignore[arg-type]

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    return {
        "schema": SCHEMA,
        "status": "fail" if fails else "warn" if warns else "pass",
        "ready_for_analysis": not fails,
        "inputs": {"model_id": spec.model_id, "device_id": spec.device_id,
                   "cooling_id": spec.cooling_id or (hw.default_cooling if hw else None), "project_version": version},
        "checks": checks,
        "missing": [c["fix"] or c["message"] for c in fails],
        "counts": {s: sum(1 for c in checks if c["status"] == s) for s in ("pass", "warn", "fail", "info")},
        "note": "These checks cover presence, format and consistency. They do not establish that a design is "
                "electrically, thermally or functionally correct.",
    }  # fmt: skip
