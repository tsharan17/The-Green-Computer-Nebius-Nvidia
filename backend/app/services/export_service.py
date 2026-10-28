"""Exports: every file the platform hands over, generated from stored analyses and recorded.

See providers/artifacts.py for the labels. Each export is stored (blob + `exports` row) so the
history shows exactly what was handed over, when, from which plan, with which label.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_loader import get_device
from app.db import repository as repo
from app.db.models import DesignPlanRow, ExportRow
from app.engine import assumptions as A
from app.engine.pdf_writer import markdown_to_pdf
from app.engine.recipe import build_recipe
from app.engine.report import build_report_md, md_to_html
from app.execution.runner import run_worker
from app.providers.artifacts import LABEL_TEXT, GeneratedArtifact
from app.providers.storage import BlobStore
from app.schemas.project import ProjectOut
from app.schemas.results import OptimisationResult
from app.services.model_lookup import resolve_model

INT8_MIN_COSINE = 0.99

CATALOG: list[dict[str, Any]] = [
    {"kind": "report_md", "name": "Engineering report (Markdown)", "label": "generated", "needs_plan": False},
    {"kind": "report_html", "name": "Engineering report (web page)", "label": "generated", "needs_plan": False},
    {"kind": "report_pdf", "name": "Engineering report (PDF)", "label": "generated", "needs_plan": False},
    {"kind": "recommendations_json", "name": "Recommendations (JSON)", "label": "generated", "needs_plan": False},
    {"kind": "recommendations_csv", "name": "Recommendations (CSV)", "label": "generated", "needs_plan": False},
    {"kind": "plan_json", "name": "Design plan (JSON)", "label": "generated", "needs_plan": True},
    {"kind": "comparison_csv", "name": "Old vs new parameters and metrics (CSV)", "label": "generated", "needs_plan": True},
    {"kind": "comparison_json", "name": "Old vs new parameters and metrics (JSON)", "label": "generated", "needs_plan": True},
    {"kind": "hardware_changes_yaml", "name": "Hardware change specification (YAML)", "label": "generated", "needs_plan": True},
    {"kind": "deployment_yaml", "name": "Runtime and deployment configuration (YAML)", "label": "template", "needs_plan": False},
    {"kind": "recipe_zip", "name": "Recipe package (scripts)", "label": "template", "needs_plan": False},
    {"kind": "plan_package_zip", "name": "Complete plan package (zip)", "label": "generated", "needs_plan": True},
    {"kind": "int8_model", "name": "INT8 ONNX model", "label": "generated_validated", "needs_plan": False,
     "needs_upload": True},
    # Release 2
    {"kind": "founder_md", "name": "Summary for founders and customers (Markdown)", "label": "generated", "needs_plan": False, "group": "report"},
    {"kind": "founder_pdf", "name": "Summary for founders and customers (PDF)", "label": "generated", "needs_plan": False, "group": "report"},
    {"kind": "engineering_full_md", "name": "Full engineering report with appendices (Markdown)", "label": "generated", "needs_plan": False, "group": "report"},
    {"kind": "engineering_full_pdf", "name": "Full engineering report with appendices (PDF)", "label": "generated", "needs_plan": False, "group": "report"},
    {"kind": "system_graph_json", "name": "Hardware system graph (JSON)", "label": "generated", "needs_plan": False, "group": "data"},
    {"kind": "evidence_zip", "name": "Evidence package (zip)", "label": "generated", "needs_plan": False, "group": "package"},
    {"kind": "manifest_json", "name": "Reproducibility manifest (JSON)", "label": "generated", "needs_plan": False, "group": "data"},
    {"kind": "handoff_zip", "name": "Engineering handoff package (zip)", "label": "generated", "needs_plan": True, "group": "package"},
]  # fmt: skip
KINDS = {c["kind"] for c in CATALOG}


class ExportError(ValueError):
    pass


def catalog(db: Session, project: ProjectOut, ws: int) -> list[dict[str, Any]]:
    has_run = repo.latest_run(db, project.id, "optimise") is not None
    row = repo.custom_model_row(db, project.spec.model_id, ws)
    has_upload = bool(row and row.artifact_id)
    out = []
    for c in CATALOG:
        reason = None
        if not has_run:
            reason = "Run the analysis first."
        if c.get("needs_upload") and not has_upload:
            reason = ("Needs the model's ONNX file: upload it under Model and hardware (library models have "
                      "no file to transform).")  # fmt: skip
        out.append({**c, "label_text": LABEL_TEXT[c["label"]], "available": reason is None, "reason": reason})
    return out


def optimised_catalogue() -> list[dict[str, Any]]:
    """Optimised model files: only real, checked transformations are offered."""
    from app.services.handoff_service import OPTIMISED

    return OPTIMISED


# --- context --------------------------------------------------------------------------------


class _Ctx:
    def __init__(self, db: Session, project: ProjectOut, ws: int, plan: DesignPlanRow | None) -> None:
        self.db, self.project, self.ws, self.plan = db, project, ws, plan
        raw = repo.latest_run(db, project.id, "optimise")
        if raw is None:
            raise ExportError("Run the analysis first, then export.")
        try:
            self.res = OptimisationResult.model_validate(raw)
        except ValueError as e:
            raise ExportError("The stored analysis is from an older version; run it again.") from e
        self.spec = project.spec
        self.model = resolve_model(db, self.spec.model_id, ws)
        self.hw = get_device(self.spec.device_id, ws)
        self.stamp = datetime.now(UTC)
        self.base = f"{_slug(self.spec.name)}" + (f"-plan-{_slug(plan.name)}-v{plan.version}" if plan else "")


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "project"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def plan_md(plan: DesignPlanRow) -> str:
    a = plan.analysis
    lines = [f"## Design plan: {plan.name} (version {plan.version})", ""]
    if plan.note:
        lines += [plan.note, ""]
    lines += [f"Built from analysis run {a.get('source_run_id')} of input version {a.get('project_version')}.", "",
              "### Selected changes", ""]  # fmt: skip
    for c in a["changes"]:
        flag = " (directional what-if)" if c.get("directional") else ""
        lines.append(f"- **{c['title']}**{flag}. Confidence: {c.get('confidence', 'medium')}. {c.get('risk', '')}")
    for n in a.get("applied", {}).get("notes", []):
        lines.append(f"- Note: {n}")
    lines += ["", f"Result: baseline **{a['baseline']['feasibility'].replace('_', ' ')}**, plan "
              f"**{a['plan']['feasibility'].replace('_', ' ')}**.", "", a.get("accuracy", ""), "",
              "### Parameters that change", "", "| Parameter | Baseline | Plan | Unit |", "|---|---|---|---|"]  # fmt: skip
    for p in a["diff"]["parameters"]:
        if p["changed"]:
            lines.append(f"| {p['name'].replace('_', ' ')} | {_fmt(p['baseline'])} | {_fmt(p['plan'])} | {p['unit']} |")
    lines += ["", "### Outputs, baseline vs plan", "",
              "| Metric | Baseline (range) | Plan (range) | Change | Source |", "|---|---|---|---|---|"]  # fmt: skip
    for m in a["diff"]["metrics"]:
        b, p = m["baseline"], m["plan"]
        ch = f"{m['change_percent']:+.1f}%" if m["change_percent"] is not None else "n/a"
        lines.append(f"| {m['name'].replace('_', ' ')} ({m['unit']}) | {_fmt(b['value'])} ({_fmt(b['low'])} to "
                     f"{_fmt(b['high'])}) | {_fmt(p['value'])} ({_fmt(p['low'])} to {_fmt(p['high'])}) | {ch} | "
                     f"{b['provenance']} -> {p['provenance']} |")  # fmt: skip
    lines += ["", "### Each change on its own", ""]
    for s in a["singles"]:
        e = next((m for m in s["diff"]["metrics"] if m["name"] == "energy_per_inference"), None)
        ch = f"{e['change_percent']:+.1f}% energy per input" if e and e["change_percent"] is not None else ""
        lines.append(f"- {s['change']['title']}: {s['view']['feasibility'].replace('_', ' ')}; {ch}")
    if a.get("directional"):
        lines += ["", "Directional items are analytical what-ifs on chip or board parameters. They are not "
                  "validated designs and no PCB, RTL or chip file was produced."]  # fmt: skip
    return "\n".join(lines) + "\n"


def report_md(c: _Ctx) -> str:
    md = build_report_md(c.spec, c.model, c.hw, c.res, c.stamp)
    if c.plan:
        md += "\n" + plan_md(c.plan)
    return md


def _comparison_rows(plan: DesignPlanRow) -> list[dict[str, Any]]:
    a = plan.analysis
    rows: list[dict[str, Any]] = []
    for p in a["diff"]["parameters"]:
        rows.append({"section": "parameter", "name": p["name"], "unit": p["unit"], "baseline": p["baseline"],
                     "plan": p["plan"], "changed": p["changed"]})  # fmt: skip
    for m in a["diff"]["metrics"]:
        b, p = m["baseline"], m["plan"]
        rows.append({"section": "metric", "name": m["name"], "unit": m["unit"], "baseline": b["value"],
                     "baseline_low": b["low"], "baseline_high": b["high"], "baseline_source": b["provenance"],
                     "plan": p["value"], "plan_low": p["low"], "plan_high": p["high"], "plan_source": p["provenance"],
                     "change_percent": m["change_percent"], "changed": m["change_percent"] not in (None, 0)})  # fmt: skip
    return rows


def _csv(rows: list[dict[str, Any]], header: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8")


def deployment(c: _Ctx) -> dict[str, Any]:
    cfg = c.res.best.config if c.res.best else c.res.baseline.config
    model, hw = c.model, c.hw
    if c.plan:
        cfg_d = c.plan.analysis["applied"]["config"]
        from app.schemas.results import Config

        cfg = Config.model_validate(cfg_d)
        model = resolve_model(c.db, c.plan.analysis["applied"]["model_id"], c.ws)
        try:
            hw = get_device(c.plan.analysis["applied"]["device_id"], c.ws)
        except KeyError:
            pass
        r = c.plan.analysis["plan"]["metrics"]
    else:
        r = (c.res.best or c.res.baseline).metrics
        r = {k: v.model_dump(mode="json") for k, v in r.items()}
    mode = hw.mode(cfg.power_mode_id) if cfg.power_mode_id in {m.id for m in hw.power_modes} else hw.power_modes[0]
    jetson = hw.id.startswith("jetson")
    eps = {"datacenter_gpu": ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
           "edge_soc": ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"] if jetson
           else ["CPUExecutionProvider"]}.get(hw.kind, ["CPUExecutionProvider"])  # fmt: skip

    def m(k: str) -> dict[str, Any] | None:
        x = r.get(k)
        return {"value": x["value"], "low": x["low"], "high": x["high"], "unit": x["unit"], "source": x["provenance"]} if x else None

    return {
        "schema": "green-computer.deployment/1",
        "status": "TEMPLATE: values come from the analysis; the commands must be adapted to your device "
                  "and nothing here has been run.",
        "generated_at": c.stamp.isoformat(),
        "model": {"id": model.id, "name": model.name, "precision": cfg.precision,
                  "input_resolution_scale": cfg.resolution_scale, "pruned_fraction": cfg.sparsity},
        "runtime": {"engine": "onnxruntime", "execution_providers": eps, "batch": cfg.batch,
                    "session_options": {"graph_optimization_level": "ORT_ENABLE_ALL", "intra_op_num_threads": 0}},
        "device": {"id": hw.id, "name": hw.name,
                   "power_mode": {"id": mode.id, "name": mode.name, "cap_w": mode.cap_w,
                                  "command": f"sudo nvpmodel -m <index of '{mode.name}'>" if jetson else None},
                   "clock_trim": cfg.clock_trim,
                   "clock_command": "sudo jetson_clocks --show  # then cap GPU max freq accordingly" if jetson and cfg.clock_trim < 1 else None,
                   "cooling": hw.cooling(cfg.cooling_id).model_dump() if cfg.cooling_id in {x.id for x in hw.cooling_options} else cfg.cooling_id},  # fmt: skip
        "expected": {k: m(k) for k in ("latency", "capacity_fps", "power_avg", "energy_per_inference", "temperature", "memory")},
        "validate": ["Run the model on the device with these settings for 10 minutes of sustained load.",
                     "Log latency, power and temperature (tegrastats / nvidia-smi / a power meter).",
                     "Import the logs under Verify and compare with the expected ranges above.",
                     "Measure task accuracy on your validation set at this precision and input size."],
    }  # fmt: skip


def hardware_changes(c: _Ctx) -> dict[str, Any]:
    assert c.plan is not None
    a = c.plan.analysis
    changed = [p for p in a["diff"]["parameters"] if p["changed"]]
    return {
        "schema": "green-computer.hardware-changes/1",
        "status": "Specification of parameter changes. Directional items are analytical what-ifs: no PCB, "
                  "RTL, netlist or chip-design file was generated or validated.",
        "plan": {"name": c.plan.name, "version": c.plan.version},
        "device": {"baseline": a["baseline"]["parameters"]["device"], "plan": a["plan"]["parameters"]["device"]},
        "parameter_changes": [{"parameter": p["name"], "from": p["baseline"], "to": p["plan"], "unit": p["unit"]}
                              for p in changed],
        "hardware_what_ifs": a["applied"].get("hardware_changes", {}),
        "directional_items": a.get("directional", []),
        "how_to_validate": ["Thermal: fit the cooling and log junction temperature under sustained load.",
                            "Chip/board parameters: model in an architecture tool (e.g. Timeloop/Accelergy), "
                            "then prototype; only silicon proves a chip change."],
    }  # fmt: skip


def _int8(c: _Ctx, blobs: BlobStore) -> GeneratedArtifact:
    from app.db.models import ArtifactRow

    row = repo.custom_model_row(c.db, c.spec.model_id, c.ws)
    art = c.db.get(ArtifactRow, row.artifact_id) if row and row.artifact_id else None
    src_path = blobs.path(art.blob_key) if art and art.blob_key else None
    if src_path is None:
        raise ExportError("The project's model has no stored ONNX file (library models cannot be transformed). "
                          "Upload the model's ONNX under Model and hardware.")  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="gc_int8_") as d:
        src = os.path.join(d, "model_fp32.onnx")
        dst = os.path.join(d, "model_int8.onnx")
        with open(src, "wb") as f:
            f.write(src_path.read_bytes())
        q, t1 = run_worker(["quantize", "--src", src, "--dst", dst], 600)
        if not q.get("ok"):
            raise ExportError(f"INT8 quantisation did not run: {q.get('error')}. Nothing was exported.")
        cmp, t2 = run_worker(["compare", "--a", src, "--b", dst, "--shape", c.model.input_desc], 600)
        if not cmp.get("ok"):
            raise ExportError(f"The quantised model could not be validated: {cmp.get('error')}. Nothing was exported.")
        validation = {
            "method": q["method"], "src_bytes": q["src_bytes"], "dst_bytes": q["dst_bytes"],
            "check": f"Both models run in ONNX Runtime on {cmp['samples']} identical synthetic inputs; outputs compared.",
            "worst_cosine": cmp["worst_cosine"], "worst_relative_l2": cmp["worst_relative_l2"],
            "threshold_cosine": INT8_MIN_COSINE, "passed": cmp["worst_cosine"] >= INT8_MIN_COSINE,
            "not_measured": "Task accuracy on real data was NOT measured; synthetic-input agreement does not "
                            "guarantee accuracy. Validate on your own data.",
            "notes": sorted(set(q.get("notes", []) + cmp.get("notes", []))), "execution_seconds": t1 + t2,
        }  # fmt: skip
        if not validation["passed"]:
            raise ExportError(f"The INT8 model's outputs differ too much from the original (worst cosine "
                              f"{cmp['worst_cosine']:.4f} < {INT8_MIN_COSINE}). It was not exported; keep the "
                              "sensitive layers in higher precision (see the recipe's sensitivity script).")  # fmt: skip
        with open(dst, "rb") as f:
            data = f.read()
    return GeneratedArtifact(f"{_slug(c.model.name)}-int8.onnx", data, "application/octet-stream",
                             "generated_validated",
                             "INT8 weight-quantised ONNX, checked by running both models on synthetic inputs.",
                             validation)  # fmt: skip


def generate(db: Session, project: ProjectOut, ws: int, kind: str, plan: DesignPlanRow | None,
             blobs: BlobStore) -> GeneratedArtifact:  # fmt: skip
    if kind not in KINDS:
        raise ExportError(f"unknown export '{kind}'")
    spec_row = next(x for x in CATALOG if x["kind"] == kind)
    if spec_row["needs_plan"] and plan is None:
        raise ExportError("Choose a saved design plan for this export.")
    c = _Ctx(db, project, ws, plan)
    title = f"{c.spec.name}: {c.model.name} on {c.hw.name}"
    if kind == "report_md":
        return GeneratedArtifact(f"{c.base}-report.md", report_md(c).encode(), "text/markdown; charset=utf-8",
                                 "generated", "Engineering report with every number's range and source.")  # fmt: skip
    if kind == "report_html":
        return GeneratedArtifact(f"{c.base}-report.html", md_to_html(report_md(c), title).encode(),
                                 "text/html; charset=utf-8", "generated", "Engineering report as a web page.")  # fmt: skip
    if kind == "report_pdf":
        return GeneratedArtifact(f"{c.base}-report.pdf", markdown_to_pdf(report_md(c), title), "application/pdf",
                                 "generated", "Engineering report as a printable PDF.")  # fmt: skip
    if kind in ("recommendations_json", "recommendations_csv"):
        rows = [a.model_dump(mode="json") for a in c.res.advice]
        if kind.endswith("json"):
            recs = json.dumps({"schema": "green-computer.recommendations/1", "engine": A.ENGINE_VERSION,
                               "generated_at": c.stamp.isoformat(), "recommendations": rows}, indent=1).encode()  # fmt: skip
            return GeneratedArtifact(f"{c.base}-recommendations.json", recs, "application/json", "generated",
                                     "Every recommendation with its effect, confidence, trade-offs and checks.")  # fmt: skip
        for r in rows:
            r.update({f"effect_{k}": v for k, v in r.pop("effect", {}).items()})
        header = ["id", "tier", "title", "action", "gain", "why", "confidence", "confidence_reason", "effort",
                  "in_recommended", "fixes", "tradeoffs", "validate_steps", "effect_energy_saving_percent",
                  "effect_power_saving_percent", "effect_temperature_change_c", "effect_alone_energy_saving_percent"]  # fmt: skip
        return GeneratedArtifact(f"{c.base}-recommendations.csv", _csv(rows, header), "text/csv", "generated",
                                 "Recommendations as a spreadsheet.")  # fmt: skip
    if kind == "plan_json":
        assert plan is not None
        doc = {"schema": "green-computer.plan/1", "generated_at": c.stamp.isoformat(),
                "plan": {"id": plan.id, "name": plan.name, "version": plan.version, "note": plan.note,
                         "created_at": plan.created_at.isoformat()},
                "project": {"id": project.id, "name": c.spec.name, "inputs_version": project.version},
                "analysis": plan.analysis}  # fmt: skip
        return GeneratedArtifact(f"{c.base}.json", json.dumps(doc, indent=1, default=str).encode(),
                                 "application/json", "generated", "The plan: selected changes, how they were "
                                 "applied, and the full comparison.")  # fmt: skip
    if kind in ("comparison_csv", "comparison_json"):
        assert plan is not None
        rows = _comparison_rows(plan)
        if kind.endswith("json"):
            return GeneratedArtifact(f"{c.base}-comparison.json", json.dumps(rows, indent=1, default=str).encode(),
                                     "application/json", "generated", "Old vs new parameters and metrics.")  # fmt: skip
        header = ["section", "name", "unit", "baseline", "baseline_low", "baseline_high", "baseline_source", "plan",
                  "plan_low", "plan_high", "plan_source", "change_percent", "changed"]  # fmt: skip
        return GeneratedArtifact(f"{c.base}-comparison.csv", _csv(rows, header), "text/csv", "generated",
                                 "Old vs new parameters and metrics as a spreadsheet.")  # fmt: skip
    if kind == "hardware_changes_yaml":
        hw_yaml = yaml.safe_dump(hardware_changes(c), sort_keys=False, allow_unicode=True)
        return GeneratedArtifact(f"{c.base}-hardware-changes.yaml", hw_yaml.encode(), "application/yaml", "generated",
                                 "Specification of hardware parameter changes (directional items flagged).")  # fmt: skip
    if kind == "deployment_yaml":
        body = "# TEMPLATE - adapt the commands to your device; nothing here has been run.\n" + yaml.safe_dump(
            deployment(c), sort_keys=False, allow_unicode=True)
        return GeneratedArtifact(f"{c.base}-deployment.yaml", body.encode(), "application/yaml", "template",
                                 "Runtime and deployment settings with the predicted ranges to check against.")  # fmt: skip
    if kind == "recipe_zip":
        files, _ = build_recipe(c.spec, c.model, c.hw, c.res)
        return GeneratedArtifact(f"{c.base}-recipe.zip", _zip({f"recipe/{n}": f.content.encode() for n, f in files.items()}),
                                 "application/zip", "template", "Scripts to convert, build, configure and measure.")  # fmt: skip
    if kind == "plan_package_zip":
        assert plan is not None
        parts = {k: generate(db, project, ws, k, plan, blobs) for k in
                 ("plan_json", "comparison_csv", "report_md", "report_html", "report_pdf", "deployment_yaml",
                  "hardware_changes_yaml")}  # fmt: skip
        files, _ = build_recipe(c.spec, c.model, c.hw, c.res)
        manifest = {"schema": "green-computer.package/1", "generated_at": c.stamp.isoformat(),
                    "files": [{"path": p.filename, "label": p.label, "label_text": LABEL_TEXT[p.label],
                               "description": p.description} for p in parts.values()]
                    + [{"path": f"recipe/{n}", "label": "template", "label_text": LABEL_TEXT["template"],
                        "description": f.description} for n, f in files.items()]}  # fmt: skip
        content = {p.filename: p.content for p in parts.values()}
        content |= {f"recipe/{n}": f.content.encode() for n, f in files.items()}
        content["MANIFEST.json"] = json.dumps(manifest, indent=1).encode()
        return GeneratedArtifact(f"{c.base}-package.zip", _zip(content), "application/zip", "generated",
                                 "Everything for this plan; MANIFEST.json labels each file.")  # fmt: skip
    if kind == "int8_model":
        return _int8(c, blobs)
    return _release2(db, project, ws, kind, plan, blobs, c, title)


def _release2(db: Session, project: ProjectOut, ws: int, kind: str, plan: DesignPlanRow | None, blobs: BlobStore,
              c: _Ctx, title: str) -> GeneratedArtifact:  # fmt: skip
    from app.services import handoff_service as H
    from app.services import system_graph_service

    if kind in ("founder_md", "founder_pdf"):
        md = H.founder_md(c)
        if kind == "founder_md":
            return GeneratedArtifact(f"{c.base}-summary.md", md.encode(), "text/markdown; charset=utf-8", "generated",
                                     "One page in plain language: the answer, what to change, how sure we are.")  # fmt: skip
        return GeneratedArtifact(f"{c.base}-summary.pdf", markdown_to_pdf(md, f"{c.spec.name}: summary"), "application/pdf",
                                 "generated", "The plain-language summary as a PDF.")  # fmt: skip
    if kind in ("engineering_full_md", "engineering_full_pdf"):
        md = report_md(c) + H.engineering_appendix_md(c)
        if kind == "engineering_full_md":
            return GeneratedArtifact(f"{c.base}-engineering-report.md", md.encode(), "text/markdown; charset=utf-8", "generated",
                                     "The engineering report plus appendices: system graph, thermal attribution, runtime "
                                     "effects, evidence per number, calibration quality and device runs.")  # fmt: skip
        return GeneratedArtifact(f"{c.base}-engineering-report.pdf", markdown_to_pdf(md, title), "application/pdf",
                                 "generated", "The full engineering report as a PDF.")  # fmt: skip
    if kind == "system_graph_json":
        g = system_graph_service.current(db, ws, project)
        return GeneratedArtifact(f"{c.base}-system-graph.json", json.dumps(g, indent=1, default=str).encode(), "application/json",
                                 "generated", "The hardware system graph (current version) with the source of every part and fact.")  # fmt: skip
    if kind == "evidence_zip":
        return GeneratedArtifact(f"{c.base}-evidence.zip", _zip(H.evidence_files(c)), "application/zip", "generated",
                                 "What supports each number: evidence, measurements, calibration fits, device runs, graph.")  # fmt: skip
    if kind == "manifest_json":
        m = H.manifest(c)
        return GeneratedArtifact(f"{c.base}-reproducibility.json", json.dumps(m, indent=1, default=str).encode(), "application/json",
                                 "generated", "Inputs, data hashes, versions and the expected numbers; scripts/reproduce.py "
                                 "recomputes and checks them.", {"expected": m["expected"], "hashes": m["hashes"],
                                                                  "stored_analysis_differs": m["stored_analysis_differs"]})  # fmt: skip
    if kind == "handoff_zip":
        assert plan is not None
        parts = {k: generate(db, project, ws, k, plan, blobs) for k in
                 ("plan_json", "comparison_csv", "hardware_changes_yaml", "deployment_yaml", "engineering_full_md",
                  "engineering_full_pdf", "founder_md", "system_graph_json", "manifest_json")}  # fmt: skip
        content = {f"{_folder(k)}/{p.filename}": p.content for k, p in parts.items()}
        content |= {f"evidence/{n}": b for n, b in H.evidence_files(c).items()}
        files, _ = build_recipe(c.spec, c.model, c.hw, c.res)
        content |= {f"recipe/{n}": f.content.encode() for n, f in files.items()}
        int8 = H.latest_int8(db, ws, project.id)
        if int8 is not None and int8.blob_key:
            content[f"models/{int8.filename}"] = blobs.get(int8.blob_key)
        entries: list[dict[str, Any]] = [{"path": f"{_folder(k)}/{p.filename}", "label": p.label, "label_text": LABEL_TEXT[p.label],
                    "description": p.description} for k, p in parts.items()]
        entries += [{"path": f"evidence/{n}", "label": "generated", "label_text": LABEL_TEXT["generated"],
                     "description": "evidence data"} for n in H.evidence_files(c)]
        entries += [{"path": f"recipe/{n}", "label": "template", "label_text": LABEL_TEXT["template"], "description": f.description}
                    for n, f in files.items()]
        if int8 is not None and int8.blob_key:
            entries.append({"path": f"models/{int8.filename}", "label": int8.label, "label_text": LABEL_TEXT[int8.label],
                            "description": "INT8 model exported earlier, with its validation in the exports history",
                            "validation": int8.validation})  # fmt: skip
        not_included = [o for o in H.OPTIMISED if o["kind"] != "int8_model" or int8 is None]
        for e in entries:
            e["sha256"] = hashlib.sha256(content[e["path"]]).hexdigest()
        manifest = {"schema": "green-computer.handoff/1", "generated_at": c.stamp.isoformat(),
                    "project": {"id": project.id, "name": c.spec.name, "inputs_version": project.version},
                    "plan": {"id": plan.id, "name": plan.name, "version": plan.version}, "files": entries,
                    "optimised_models_not_included": not_included,
                    "start_here": "reports/ (summary, then the engineering report), then plan/ and hardware/; "
                                  "check numbers with scripts/reproduce.py and data/*-reproducibility.json."}  # fmt: skip
        content["MANIFEST.json"] = json.dumps(manifest, indent=1, default=str).encode()
        content["README.md"] = _handoff_readme(c, plan, entries, not_included).encode()
        return GeneratedArtifact(f"{c.base}-handoff.zip", _zip(content), "application/zip", "generated",
                                 "Everything an engineering team needs to take over: reports, plan, hardware changes, graph, "
                                 "evidence, recipe, reproducibility manifest; MANIFEST.json labels and hashes every file.")  # fmt: skip
    raise ExportError(f"unknown export '{kind}'")


def _folder(kind: str) -> str:
    if kind in ("engineering_full_md", "engineering_full_pdf", "founder_md"):
        return "reports"
    if kind in ("plan_json", "comparison_csv"):
        return "plan"
    if kind in ("hardware_changes_yaml", "deployment_yaml"):
        return "hardware"
    return "data"


def _handoff_readme(c: _Ctx, plan: DesignPlanRow, entries: list[dict[str, Any]], not_included: list[dict[str, Any]]) -> str:
    lines = [f"# Handoff: {c.spec.name}", "", f"Plan \"{plan.name}\" v{plan.version}. Generated {c.stamp:%Y-%m-%d %H:%M} UTC.", "",
             "Labels: *Generated and validated here* (produced and checked by running it), *Generated from your stored "
             "analysis* (data), *Template* (adapt and run yourself; not executed here).", "",
             "| File | Label | What it is |", "|---|---|---|"]  # fmt: skip
    lines += [f"| {e['path']} | {e['label_text']} | {e['description']} |" for e in entries]
    lines += ["", "## Not included", ""]
    lines += [f"- {o['name']}: {o['how']}" for o in not_included]
    lines += ["", "Directional hardware items are analytical what-ifs: no PCB, RTL or chip file was produced or simulated."]
    return "\n".join(lines) + "\n"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def store(db: Session, blobs: BlobStore, ws: int, project_id: int, plan_id: int | None, kind: str,
          art: GeneratedArtifact, user_id: int | None) -> ExportRow:  # fmt: skip
    key, digest = blobs.put(f"ws{ws}", art.content)
    row = ExportRow(workspace_id=ws, project_id=project_id, plan_id=plan_id, kind=kind, filename=art.filename,
                    media_type=art.media_type, label=art.label, size_bytes=len(art.content), sha256=digest,
                    blob_key=key, validation=art.validation, created_by=user_id)  # fmt: skip
    db.add(row)
    db.commit()
    return row


def view(r: ExportRow) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "filename": r.filename, "label": r.label, "label_text": LABEL_TEXT.get(r.label, r.label),
            "size_bytes": r.size_bytes, "sha256": r.sha256, "plan_id": r.plan_id, "validation": r.validation,
            "created_at": r.created_at.isoformat()}  # fmt: skip


def list_exports(db: Session, ws: int, project_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(ExportRow).where(ExportRow.workspace_id == ws, ExportRow.project_id == project_id)
                      .order_by(ExportRow.id.desc()).limit(200))  # fmt: skip
    return [view(r) for r in rows]


def get_export(db: Session, ws: int, project_id: int, export_id: int) -> ExportRow | None:
    r = db.get(ExportRow, export_id)
    return r if r is not None and r.workspace_id == ws and r.project_id == project_id else None

