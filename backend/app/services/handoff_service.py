"""Release 2 hand-over files: a plain report for founders and customers, a full engineering report,
the system graph, an evidence package, a reproducibility manifest (with an offline verifier) and a
complete handoff package. Every file is generated from stored analyses and data here and labelled.

Optimised model files are real or absent: only transformations that actually run and are checked
here are offered (INT8 today); the others are listed with what they would need.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repository as repo
from app.db.models import ArtifactRow, ExportRow, RemoteJobRow
from app.engine import assumptions as A
from app.engine.optimise import optimise
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration, MeasurementIn
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectSpec
from app.schemas.results import OptimisationResult
from app.services import measurement_service as ms

MANIFEST_SCHEMA = "green-computer.reproducibility/1"
HEADLINE = ("latency", "capacity_fps", "power_avg", "energy_per_inference", "memory", "temperature",
            "temperature_unthrottled", "cost_per_day", "carbon_per_day")  # fmt: skip
REL_TOL = 1e-9

# Optimised model files the platform could hand over, and what each needs. Only INT8 runs here.
OPTIMISED = [
    {"kind": "int8_model", "name": "INT8 ONNX model", "status": "available when the ONNX file is uploaded",
     "how": "ONNX Runtime dynamic quantisation, then both models are run on identical inputs and compared."},
    {"kind": "fp16_model", "name": "FP16 ONNX model", "status": "not offered",
     "how": "Needs the onnxconverter-common float16 converter and an FP16-capable runtime to validate; "
            "use the recipe's convert script on your device."},
    {"kind": "pruned_model", "name": "Pruned model", "status": "not offered",
     "how": "Pruning changes accuracy and needs your training data and a fine-tuning run; the recipe "
            "names the steps."},
    {"kind": "tensorrt_engine", "name": "TensorRT engine", "status": "not offered",
     "how": "Engines are built for one GPU and TensorRT version, on that device: run trtexec there "
            "(recipe script)."},
]  # fmt: skip


def _norm(obj: Any) -> Any:
    """JSON-equal values hash equally across languages: a whole-number float (Python writes 40.0,
    JavaScript writes 40) is hashed as an integer."""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _norm(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(v) for v in obj]
    return obj


def _sha(obj: Any) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _pkg(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_commit() -> str | None:
    import os

    env = os.environ.get("GC_GIT_SHA")
    if env:
        return env
    root = Path(__file__).resolve().parents[3]
    head = root / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (root / ".git" / ref[5:]).read_text().strip()
        return ref
    except OSError:
        return None


def _headline(res: OptimisationResult) -> dict[str, Any]:
    def pick(ev: Any) -> dict[str, float] | None:
        return {k: ev.metrics[k].value for k in HEADLINE if k in ev.metrics} if ev is not None else None

    return {"baseline": pick(res.baseline), "best": pick(res.best),
            "best_config": res.best.config.model_dump() if res.best else None, "evaluated": res.evaluated}  # fmt: skip


def _inputs(db: Session, spec: ProjectSpec, ws: int, model: ModelProfile, hw: HardwareProfile) -> dict[str, Any]:
    return {
        "spec": spec.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "hardware": hw.model_dump(mode="json"),
        "measured_retention": ms.measured_retention(db, model.id, ws),
        "calibrations": {k: v.model_dump(mode="json") for k, v in ms.active_calibrations(db, hw.id, ws).items()},
        "measurements": [m.model_dump(mode="json") for m in ms.measurements_for(db, hw.id, model.id, ws)],
    }


def recompute(inputs: dict[str, Any]) -> OptimisationResult:
    """Run the engine on exactly the captured inputs (no database, no network)."""
    return optimise(
        ProjectSpec.model_validate(inputs["spec"]), ModelProfile.model_validate(inputs["model"]),
        HardwareProfile.model_validate(inputs["hardware"]), measured=inputs.get("measured_retention") or {},
        calibrations={k: Calibration.model_validate(v) for k, v in (inputs.get("calibrations") or {}).items()},
        measurements=[MeasurementIn.model_validate(m) for m in inputs.get("measurements") or []],
    )  # fmt: skip


def manifest(c: Any) -> dict[str, Any]:
    """Everything needed to recompute this project's numbers, with hashes, versions and the result."""
    db, ws = c.db, c.ws
    inputs = _inputs(db, c.spec, ws, c.model, c.hw)
    fresh = recompute(inputs)
    stored_stale = _headline(c.res) != _headline(fresh)
    arts = db.scalars(select(ArtifactRow).where(ArtifactRow.workspace_id == ws, ArtifactRow.project_id == c.project.id,
                                                ArtifactRow.deleted_at.is_(None)))  # fmt: skip
    data_dir = Path(__file__).resolve().parents[1] / "data"
    cat = data_dir / "cooling_catalogue.yaml"
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": c.stamp.isoformat(),
        "project": {"id": c.project.id, "name": c.spec.name, "inputs_version": c.project.version},
        "engine": {"version": A.ENGINE_VERSION,
                   "constants": {k: v for k, v in vars(A).items() if k.isupper() and isinstance(v, (int, float, str))}},
        "software": {"python": platform.python_version(), "platform": platform.platform(),
                     "packages": {p: _pkg(p) for p in ("numpy", "pydantic", "fastapi", "sqlalchemy", "onnx", "onnxruntime")},
                     "git_commit": _git_commit()},
        "hashes": {"spec": _sha(inputs["spec"]), "model_profile": _sha(inputs["model"]), "hardware_profile": _sha(inputs["hardware"]),
                   "calibrations": _sha(inputs["calibrations"]), "measurements": _sha(inputs["measurements"]),
                   "cooling_catalogue": _sha(cat.read_bytes()) if cat.exists() else None},
        "artifacts": [{"id": a.id, "filename": a.filename, "kind": a.kind, "sha256": a.sha256, "status": a.status} for a in arts],
        "inputs": inputs,
        "expected": _headline(fresh),
        "stored_analysis_differs": stored_stale,
        "determinism": "The engine is deterministic: the same inputs, data and engine version give the same numbers. "
                       "Search time is not part of the result.",
        "how_to_reproduce": "cd backend && .venv/Scripts/python ../scripts/reproduce.py <this file>  "
                            "(bin/python on Linux/macOS; no database or network needed)",
    }  # fmt: skip


def verify_manifest(doc: dict[str, Any]) -> dict[str, Any]:
    """Recompute from a manifest and compare every headline number. Pure apart from the engine."""
    if doc.get("schema") != MANIFEST_SCHEMA:
        return {"ok": False, "problems": [f"not a {MANIFEST_SCHEMA} file"], "compared": 0}
    problems: list[str] = []
    if doc.get("engine", {}).get("version") != A.ENGINE_VERSION:
        problems.append(f"engine version differs: manifest {doc.get('engine', {}).get('version')}, here {A.ENGINE_VERSION}")
    inputs = doc["inputs"]
    for k in ("spec", "model", "hardware", "calibrations", "measurements"):
        key = {"model": "model_profile", "hardware": "hardware_profile"}.get(k, k)
        if _sha(inputs[k]) != doc["hashes"].get(key):
            problems.append(f"the captured {k} does not match its hash (edited after export?)")
    got = _headline(recompute(inputs))
    exp = doc["expected"]
    compared = 0
    for side in ("baseline", "best"):
        a, b = exp.get(side), got.get(side)
        if (a is None) != (b is None):
            problems.append(f"{side}: present in one result only")
            continue
        for k, v in (a or {}).items():
            compared += 1
            w = (b or {}).get(k)
            if w is None or abs(w - v) > REL_TOL * max(abs(v), 1e-12):
                problems.append(f"{side}.{k}: expected {v}, got {w}")
    if exp.get("best_config") != got.get("best_config"):
        problems.append("the recommended configuration differs")
    return {"ok": not problems, "problems": problems, "compared": compared}


# --- reports --------------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0f}%"


def founder_md(c: Any) -> str:
    """One page in plain language for a founder, customer or manager."""
    res, spec = c.res, c.spec
    v = res.verdict
    b, best = res.baseline, res.best
    rec = [a for a in res.advice if a.in_recommended]
    conf = "low" if any(a.confidence == "low" for a in rec) else "medium" if any(a.confidence == "medium" for a in rec) else "high"
    measured = any(m.provenance.value == "measured" for m in b.metrics.values())
    lines = [f"# {spec.name}", "",
             f"*{c.model.name} on {c.hw.name}. Prepared {c.stamp:%d %B %Y} by The Green Computer.*", "",
             "## The answer", "", v.summary if v else "Analysis complete.", ""]  # fmt: skip
    state = {"predicted_feasible": "meets every requirement", "verified_feasible": "meets every requirement (measured)",
             "uncertain": "probably works, not yet certain", "infeasible": "misses a requirement",
             "verified_infeasible": "misses a requirement (measured)"}  # fmt: skip
    lines += [f"- **Today:** {state.get(b.feasibility.value, b.feasibility.value)}."]
    if best:
        lines += [f"- **With the recommended changes:** {state.get(best.feasibility.value, best.feasibility.value)}."]
    if v and v.savings:
        s = v.savings
        lines += [f"- **Expected improvement:** {_pct(s.energy_percent)} less energy per input, {_pct(s.power_percent)} less power, "
                  f"about {s.cost_per_year:,.0f} less electricity cost and {s.carbon_kg_per_year:,.0f} kg less CO₂e a year at your hours "
                  "and prices."]  # fmt: skip
    lines += ["", "## What we recommend", ""]
    lines += [f"{i}. {a.action}" for i, a in enumerate(rec[:5], 1)] or ["No change is needed."]
    if res.limiting_factor:
        lines += ["", f"**What limits it today:** {res.limiting_factor}."]
    lines += ["", "## How sure we are", "",
              f"Confidence: **{conf}**. " + ("Some numbers are measured on the device." if measured else
              "These are predictions from a physics model with stated ranges; nothing has been measured on your device yet."),
              "", "Before committing: run the recommended setup on the real device and compare (the platform does this "
              "with its device agent or an imported log).", "",
              "## Next steps", "", "1. Approve the plan and run it on one device.", "2. Compare the measured numbers with "
              "this prediction (Verify).", "3. Roll out once the measurement agrees.", "",
              "---", f"Engine {A.ENGINE_VERSION}. Every number has a range and a source in the full engineering report."]  # fmt: skip
    return "\n".join(lines) + "\n"


def engineering_appendix_md(c: Any) -> str:
    """Appendices for the full engineering report: graph, thermal attribution, runtime, evidence, runs."""
    from app.services import physics_service, system_graph_service

    db, ws, p = c.db, c.ws, c.project
    out = ["", "# Appendices", ""]
    g = system_graph_service.current(db, ws, p)
    gr = g["graph"]
    out += ["## A. Hardware system graph", "",
            f"Version {g['version']}{'' if g.get('saved') else ' (from the device profile, not saved)'}: "
            f"{len(gr['components'])} parts, {len(gr['connections'])} connections.", "",
            "| Part | Kind | Source | Confidence |", "|---|---|---|---|"]  # fmt: skip
    out += [f"| {x['name']} | {x['kind']} | {x['origin']} | {x['confidence']} |" for x in gr["components"][:80]]
    out += ["", *[f"- {x}" for x in gr.get("limitations", [])], ""]
    try:
        ph = physics_service.physics(db, ws, p, "baseline")
    except Exception as err:  # noqa: BLE001 - an appendix must not sink the report
        ph = None
        out += [f"(Physics views unavailable: {err})", ""]
    if ph and ph.get("thermal"):
        th = ph["thermal"]
        out += ["## B. Thermal attribution (today)", "", f"Lumped (decision model): {th['lumped_c']:.1f} °C. Status: {th['status']}.", "",
                "| Part | °C | Heat W | Label |", "|---|---|---|---|"]  # fmt: skip
        out += [f"| {n['name']} | {n['temperature_c']:.1f} | {n['heat_w']:.3g} | {n['label']} |" for n in th["nodes"]]
        out += ["", "| From | To | K/W | Source |", "|---|---|---|---|"]
        out += [f"| {r['a']} | {r['b']} | {r['k_per_w']:.3g} | {r['source']} |" for r in th["resistors"]]
        out += [""]
    if ph:
        out += ["## C. Runtime effects and memory (today)", "", "| Effect | ms | Per | In headline | Source |", "|---|---|---|---|---|"]
        for e in ph["runtime"]["effects"]:
            ms_txt = "unknown" if e["ms"] is None else f"{e['ms']:.4g}"
            out.append(f"| {e['title']} | {ms_txt} | {e['per']} | {'yes' if e['in_headline'] else 'no'} | {e['provenance']} |")
        out += ["", "| Level | Holds | Fits | Source |", "|---|---|---|---|"]
        out += [f"| {m['name']} | {m['holds']} | {'–' if m['fits'] is None else 'yes' if m['fits'] else 'no'} | {m['source']} |" for m in ph["memory"]]
        out += [""]
    ev = physics_service.evidence(db, ws, p)
    out += ["## D. Evidence for each headline number", ""]
    for s in ev["setups"]:
        out += [f"### {s['label']}", "", "| Metric | Value | Range | Provenance | Based on |", "|---|---|---|---|---|"]
        out += [f"| {m['key']} | {m['value']:.4g} {m['unit']} | {m['low']:.4g}–{m['high']:.4g} | {m['provenance']}"
                f"{' (calibrated)' if m['calibrated'] else ''} | {'; '.join(f['fact'] + ': ' + f['source'] for f in m['facts'])} |"
                for m in s["metrics"]]  # fmt: skip
        out += [""]
    out += ["### Calibration quality", "", "| Precision | Applied | Latency fit | Power fit | Runs |", "|---|---|---|---|---|"]
    out += [f"| {r['precision']} | {'v' + str(r['version']) if r['applied'] else 'no'} | {r['latency']['grade']} | {r['power']['grade']} | {r['measurements']} |"
            for r in ev["calibration"]["precisions"]]  # fmt: skip
    jobs = list(db.scalars(select(RemoteJobRow).where(RemoteJobRow.workspace_id == ws, RemoteJobRow.project_id == p.id)
                           .order_by(RemoteJobRow.id.desc()).limit(20)))  # fmt: skip
    if jobs:
        out += ["", "## E. Runs on devices", "", "| Run | Status | Mode | Quality | Measurement |", "|---|---|---|---|---|"]
        out += [f"| {j.id} | {j.status} | {(j.result or {}).get('mode', '–')} | {(j.quality or {}).get('verdict', '–')} | {j.measurement_id or '–'} |" for j in jobs]
    return "\n".join(out) + "\n"


def evidence_files(c: Any) -> dict[str, bytes]:
    """The evidence package: what supports the numbers, as data."""
    from app.services import physics_service, system_graph_service

    db, ws, p = c.db, c.ws, c.project
    ev = physics_service.evidence(db, ws, p)
    files: dict[str, bytes] = {
        "evidence.json": json.dumps(ev, indent=1, default=str).encode(),
        "calibration_history.json": json.dumps(repo.calibration_history(db, c.hw.id, ws), indent=1, default=str).encode(),
        "measurements.json": json.dumps([m.model_dump(mode="json") for m in ms.measurements_for(db, c.hw.id, c.model.id, ws)], indent=1).encode(),
        "system_graph.json": json.dumps(system_graph_service.current(db, ws, p), indent=1, default=str).encode(),
        "analysis.json": json.dumps(c.res.model_dump(mode="json"), indent=1).encode(),
    }  # fmt: skip
    try:
        files["physics_today.json"] = json.dumps(physics_service.physics(db, ws, p, "baseline"), indent=1, default=str).encode()
    except Exception:  # noqa: BLE001 - a measured-only trace may not support every view
        pass
    jobs = [{"id": j.id, "status": j.status, "mode": (j.result or {}).get("mode"), "quality": j.quality,
             "environment": (j.result or {}).get("environment"), "artifact": j.payload.get("artifact"),
             "measurement_id": j.measurement_id} for j in
            db.scalars(select(RemoteJobRow).where(RemoteJobRow.workspace_id == ws, RemoteJobRow.project_id == p.id))]  # fmt: skip
    files["device_runs.json"] = json.dumps(jobs, indent=1, default=str).encode()
    files["README.md"] = (
        b"# Evidence package\n\n"
        b"What supports each number in the analysis, as data. Provenance labels: measured (a real run), calibrated "
        b"(predicted, rescaled to real runs), analytical (physics model with stated assumptions), assumed, vendor_spec.\n\n"
        b"- evidence.json: every headline metric for today and the plan, its range, provenance and the facts behind it\n"
        b"- measurements.json: the real runs of this model on this device that the engine used\n"
        b"- calibration_history.json: every calibration fit, accepted or not, with its errors\n"
        b"- device_runs.json: runs through the device agent, with quality checks (simulated runs are labelled)\n"
        b"- system_graph.json: the hardware graph with the source of every part and fact\n"
        b"- physics_today.json: thermal attribution, memory hierarchy and runtime effects (labelled)\n"
        b"- analysis.json: the full stored analysis\n"
    )  # fmt: skip
    return files


def latest_int8(db: Session, ws: int, project_id: int) -> ExportRow | None:
    """The most recent stored INT8 export of this project (only real, validated ones are ever stored)."""
    return db.scalars(select(ExportRow).where(ExportRow.workspace_id == ws, ExportRow.project_id == project_id,
                                              ExportRow.kind == "int8_model", ExportRow.blob_key.is_not(None))
                      .order_by(ExportRow.id.desc())).first()  # fmt: skip


def now() -> datetime:
    return datetime.now(UTC)
