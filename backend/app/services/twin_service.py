"""Build, store and export execution traces for a project."""

from __future__ import annotations

import csv
import io
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_loader import get_device
from app.db import repository as repo
from app.db.models import ExecutionTraceRow
from app.engine.evaluator import evaluate, matches
from app.engine.layers import _reference_graph
from app.engine.optimise import baseline_config
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectOut, ProjectSpec
from app.schemas.results import Config, OptimisationResult
from app.schemas.twin import ExecutionTrace
from app.services import measurement_service as ms
from app.services.model_lookup import resolve_model
from app.twin.trace import build_trace

Which = Literal["baseline", "best", "custom"]


class TraceError(ValueError):
    pass


def graph_model(model: ModelProfile) -> tuple[ModelProfile, str]:
    """The model with an operator graph where one exists: the uploaded ONNX graph, else the
    published reference architecture (ResNet-50, MobileNetV2), else the aggregate profile."""
    if model.layers:
        return model, "your ONNX graph"
    ref = _reference_graph(model.id)
    if ref is not None:
        return ref.model_copy(update={"id": model.id, "name": model.name,
                                      "baseline_accuracy": model.baseline_accuracy}), \
            "the published reference architecture of this model"  # fmt: skip
    return model, ""


def config_for(db: Session, project: ProjectOut, which: Which, custom: Config | None, ws: int | None) -> tuple[Config, str]:
    spec = project.spec
    hw = get_device(spec.device_id, ws)
    if which == "custom":
        if custom is None:
            raise TraceError("give a configuration for which=custom")
        return custom, "custom configuration"
    if which == "best":
        raw = repo.latest_run(db, project.id, "optimise")
        if raw is None:
            raise TraceError("Run the analysis first: the recommended setup comes from it.")
        res = OptimisationResult.model_validate(raw)
        if res.best is None:
            raise TraceError("The last analysis found no setup that meets every requirement.")
        return res.best.config, "recommended setup"
    return spec.baseline or baseline_config(hw, spec.cooling_id), "current setup"


def compute_trace(db: Session, spec: ProjectSpec, cfg: Config, ws: int | None) -> ExecutionTrace:
    model = resolve_model(db, spec.model_id, ws)
    hw = get_device(spec.device_id, ws)
    cal = ms.active_calibrations(db, hw.id, ws).get(cfg.precision)
    meas = next((m for m in ms.measurements_for(db, hw.id, model.id, ws) if matches(m, hw, model.id, cfg)), None)
    try:
        result = evaluate(model, hw, cfg, spec.workload, spec.constraints,
                          ms.measured_retention(db, model.id, ws).get(cfg.precision),
                          calibration=cal, measured=meas)  # fmt: skip
        g, source = graph_model(model)
        return build_trace(g, hw, cfg, spec.workload, result, cal, source)
    except ValueError as e:
        raise TraceError(str(e)) from e


def store(db: Session, ws: int, project_id: int, user_id: int | None, trace: ExecutionTrace, label: str) -> int:
    v = repo.current_version_row(db, project_id)
    t = trace.totals
    row = ExecutionTraceRow(
        workspace_id=ws, project_id=project_id, version_id=v.id if v else None, kind=trace.kind,
        provenance=trace.provenance, source=trace.source, schema_id=trace.schema_id, label=label[:200],
        config=trace.config, created_by=user_id,
        summary={"fidelity": trace.fidelity, "events": len(trace.events), "batch_ms": t.batch_ms,
                 "latency_ms": t.latency_ms, "energy_per_inference_mj": t.energy_per_inference_mj,
                 "peak_working_memory_bytes": t.peak_working_memory_bytes,
                 "average_power_w": t.average_power_w, "version": v.version if v else None},
        trace=trace.model_dump(mode="json"),
    )  # fmt: skip
    db.add(row)
    db.commit()
    return row.id


def list_traces(db: Session, ws: int, project_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(ExecutionTraceRow).where(
        ExecutionTraceRow.workspace_id == ws, ExecutionTraceRow.project_id == project_id
    ).order_by(ExecutionTraceRow.id.desc()).limit(100))  # fmt: skip
    return [{"id": r.id, "kind": r.kind, "provenance": r.provenance, "source": r.source, "label": r.label,
             "config": r.config, "summary": r.summary, "created_at": r.created_at.isoformat()} for r in rows]  # fmt: skip


def get_trace(db: Session, ws: int, project_id: int, trace_id: int) -> ExecutionTraceRow | None:
    row = db.get(ExecutionTraceRow, trace_id)
    if row is None or row.workspace_id != ws or row.project_id != project_id:
        return None
    return row


def _csv(rows: list[dict[str, Any]], header: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def trace_csv(trace: dict[str, Any], table: Literal["events", "memory", "tensors"]) -> str:
    """Raw data behind each chart, with the provenance in a comment-free column so spreadsheets
    keep it."""
    prov = f"{trace['kind']}:{trace['provenance']}"
    if table == "events":
        rows = []
        for e in trace["events"]:
            en = e["energy_mj"]
            rows.append({**e, **{f"energy_{k}_mj": v for k, v in en.items()},
                         "energy_total_mj": sum(en.values()), "provenance": prov})  # fmt: skip
        return _csv(rows, ["index", "name", "op", "kind", "stage", "start_ms", "duration_ms", "compute_ms",
                           "memory_ms", "overhead_ms", "bound", "flops", "weight_bytes", "activation_bytes",
                           "bandwidth_gbs", "bandwidth_utilisation", "working_set_bytes", "live_after_bytes",
                           "power_w", "energy_compute_mj", "energy_memory_mj", "energy_idle_mj",
                           "energy_leakage_mj", "energy_cooling_mj", "energy_total_mj", "provenance"])  # fmt: skip
    if table == "memory":
        return _csv([{**p, "provenance": prov} for p in trace["memory_timeline"]],
                    ["event", "t_ms", "weights_bytes", "runtime_bytes", "activation_bytes", "total_bytes",
                     "capacity_bytes", "over_capacity", "provenance"])  # fmt: skip
    return _csv([{**t, "provenance": prov} for t in trace["tensors"]],
                ["id", "kind", "bytes", "producer", "last_use", "provenance"])
