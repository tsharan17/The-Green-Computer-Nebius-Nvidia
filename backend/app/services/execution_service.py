"""Run a project's model for real on an execution backend and keep what was measured.

A job runs in a background thread that drives the isolated worker process (time-limited). On
success it stores, separately and labelled:
  * a measurement (latency on "this server's CPU", device id `host-cpu`) that feeds the workspace's
    calibration and the predicted-vs-measured view;
  * a measured execution trace (operator timings from the ONNX Runtime profiler);
  * a comparison with the predicted trace for the same model and CPU.
Power, energy and temperature are not measured on this path and are never filled in.
"""

from __future__ import annotations

import os
import platform
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.data_loader import get_device
from app.db import repository as repo
from app.db.models import ArtifactRow, ExecutionJobRow
from app.execution.runner import run_worker
from app.providers.execution import get_backend
from app.providers.storage import BlobStore
from app.schemas.measurement import MeasurementIn
from app.schemas.project import ProjectOut
from app.schemas.results import Config
from app.services import audit
from app.services import measurement_service as ms
from app.services import twin_service as twin
from app.services.model_lookup import resolve_model
from app.twin.measured import build_measured_trace, compare_by_op

HOST_DEVICE = "host-cpu"
REFERENCE = {"resnet-50": "resnet50", "mobilenet-v2": "mobilenet_v2"}
MAX_SECONDS = float(os.environ.get("GC_EXEC_MAX_SECONDS", "120"))


class ExecutionError(ValueError):
    pass


def _cpu_label() -> str:
    return (platform.processor() or platform.machine() or "CPU").strip()[:120]


def model_source(db: Session, spec_model_id: str, ws: int, blobs: BlobStore) -> tuple[str, str]:
    """('upload', blob path) or ('reference', builder name); raises when nothing runnable exists."""
    row = repo.custom_model_row(db, spec_model_id, ws)
    if row is not None:
        art = db.get(ArtifactRow, row.artifact_id) if row.artifact_id else None
        p = blobs.path(art.blob_key) if art and art.blob_key else None
        if p is None:
            raise ExecutionError("This model was entered by hand or its file was deleted, so there is nothing to "
                                 "run. Upload its ONNX file under Model and hardware.")  # fmt: skip
        return "upload", str(p)
    if spec_model_id in REFERENCE:
        return "reference", REFERENCE[spec_model_id]
    raise ExecutionError("This library model has no runnable file here (only ResNet-50 and MobileNetV2 can be "
                         "rebuilt from their reference architecture). Upload its ONNX file to run it.")  # fmt: skip


def _materialize(kind: str, value: str, folder: str) -> str:
    if kind == "upload":
        return value
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "bench" / "reference_models.py"
    if not path.is_file():
        raise ExecutionError("The reference model builder (bench/reference_models.py) is not installed here.")
    spec = importlib.util.spec_from_file_location("gc_reference_models_exec", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    out = os.path.join(folder, f"{value}.onnx")
    with open(out, "wb") as f:
        f.write(getattr(mod, value)(materialize=True))
    return out


def start(sf: sessionmaker[Session], db: Session, project: ProjectOut, ws: int, user_id: int | None,
          backend_id: str, batch: int, runs: int, warmup: int, blobs: BlobStore) -> ExecutionJobRow:  # fmt: skip
    backend = get_backend(backend_id)
    if backend is None or backend_id in ("nebius-jobs", "remote-device"):
        raise ExecutionError("Choose a local backend (remote GPU jobs are started from the Nebius panel).")
    info = backend.info()
    if info.status != "available":
        raise ExecutionError(f"{info.name} is {info.status.replace('_', ' ')} here: {info.detail} {info.enable}".strip())
    kind, value = model_source(db, project.spec.model_id, ws, blobs)
    running = db.scalars(select(ExecutionJobRow).where(ExecutionJobRow.workspace_id == ws,
                                                       ExecutionJobRow.status.in_(("queued", "running")))).first()  # fmt: skip
    if running is not None:
        raise ExecutionError("A run is already in progress in this workspace; wait for it to finish.")
    row = ExecutionJobRow(workspace_id=ws, project_id=project.id, backend=backend_id, status="queued",
                          params={"batch": batch, "runs": runs, "warmup": warmup, "source": kind,
                                  "providers": info.providers, "max_seconds": MAX_SECONDS},
                          created_by=user_id)  # fmt: skip
    db.add(row)
    db.commit()
    job_id = row.id
    threading.Thread(target=_run, args=(sf, job_id, project, ws, user_id, kind, value), daemon=True).start()
    return row


def _run(sf: sessionmaker[Session], job_id: int, project: ProjectOut, ws: int, user_id: int | None,
         kind: str, value: str) -> None:  # fmt: skip
    with sf() as db:
        row = db.get(ExecutionJobRow, job_id)
        assert row is not None
        row.status = "running"
        db.commit()
        p = row.params
        try:
            model = resolve_model(db, project.spec.model_id, ws)
            with tempfile.TemporaryDirectory(prefix="gc_exec_") as d:
                path = _materialize(kind, value, d)
                local = os.path.join(d, "model.onnx")
                if path != local:
                    with open(path, "rb") as src, open(local, "wb") as dst:
                        dst.write(src.read())
                res, wall = run_worker(
                    ["bench", "--src", local, "--providers", ",".join(p["providers"]), "--batch", str(p["batch"]),
                     "--runs", str(p["runs"]), "--warmup", str(p["warmup"]), "--shape", model.input_desc,
                     "--max-seconds", str(p["max_seconds"])], p["max_seconds"] + 120,
                )  # fmt: skip
            row.seconds = wall
            audit.usage(db, "execution_seconds", wall, workspace_id=ws, user_id=user_id, project_id=project.id,
                        detail={"job_id": job_id, "backend": row.backend}, commit=False)  # fmt: skip
            if not res.get("ok"):
                raise ExecutionError(str(res.get("error", "the run failed")))
            _store(db, row, res, project, ws, user_id, model)
            row.status = "succeeded"
        except Exception as e:  # noqa: BLE001 - every failure is recorded on the job, honestly
            row.status = "failed"
            row.error = str(e)[:990]
        row.finished_at = datetime.now(UTC)
        db.commit()
        audit.audit(db, "execution.finished", workspace_id=ws, user_id=user_id, target_type="execution",
                    target_id=job_id, detail={"project_id": project.id, "status": row.status,
                                              "backend": row.backend})  # fmt: skip


def _store(db: Session, row: ExecutionJobRow, res: dict[str, Any], project: ProjectOut, ws: int,
           user_id: int | None, model) -> None:  # fmt: skip
    hw = get_device(HOST_DEVICE, ws)
    precision = "int8" if any(o["op"] in ("MatMulInteger", "ConvInteger", "QLinearConv", "DynamicQuantizeLinear")
                              for o in res.get("operators", [])) else "fp32"  # fmt: skip
    lat = res["latency_ms"]
    m = MeasurementIn(
        device_id=HOST_DEVICE, model_id=model.id, precision=precision, batch=res["batch"], source="local_bench",
        hardware_label=f"{_cpu_label()} (this server, {', '.join(res['providers'])})",
        latency_ms_mean=lat["mean"], latency_ms_p50=lat["p50"], latency_ms_p95=lat["p95"],
        latency_ms_p99=lat["p99"], throughput_fps=res.get("throughput_fps"), n_runs=res["runs"],
        measured_at=datetime.now(UTC), external_id=f"exec-{row.id}",
        notes=f"Run by the platform (job {row.id}); synthetic inputs; latency only.",
    )  # fmt: skip
    measured = build_measured_trace(res, model.id, model.name, HOST_DEVICE, f"{_cpu_label()} (this server)",
                                    precision, row.backend)  # fmt: skip
    row.trace_id = twin.store(db, ws, project.id, user_id, measured, f"measured run (job {row.id})")
    comparison: dict[str, Any] = {}
    try:
        spec = project.spec.model_copy(update={"device_id": HOST_DEVICE, "cooling_id": None, "baseline": None})
        cfg = Config(precision=precision, batch=res["batch"], power_mode_id=hw.power_modes[0].id,
                     cooling_id=hw.default_cooling)  # fmt: skip
        if hw.peak_tflops.get(precision, 0) > 0:
            pred = twin.compute_trace(db, spec, cfg, ws)
            comparison = {
                "predicted_batch_ms": pred.totals.batch_ms, "measured_batch_ms": lat["mean"],
                "measured_p95_ms": lat["p95"],
                "error_percent": (pred.totals.batch_ms / lat["mean"] - 1) * 100 if lat["mean"] else None,
                "predicted_provenance": pred.provenance, "by_op": compare_by_op(pred, measured)[:20],
                "note": "Prediction for the host-cpu profile at the same batch and precision, after any "
                        "calibration this workspace already had (the new measurement refits it afterwards).",
            }  # fmt: skip
        else:
            comparison = {"note": f"The host-cpu profile has no {precision} throughput, so no prediction to compare."}
    except (twin.TraceError, KeyError, ValueError) as e:
        comparison = {"note": f"No prediction to compare: {e}"}
    # Store the measurement only now, so the comparison above used the calibration as it was before.
    if hw.peak_tflops.get(precision, 0) > 0:
        stored, _, _ = ms.ingest(db, [m], ws)
        row.measurement_id = stored[0].id
    else:
        comparison["measurement_note"] = (f"Not stored as a calibration measurement: the host-cpu profile does not "
                                          f"model {precision}.")
    row.result = {"latency_ms": lat, "throughput_fps": res.get("throughput_fps"), "runs": res["runs"],
                  "providers": res["providers"], "onnxruntime": res.get("onnxruntime"),
                  "peak_rss_bytes": res.get("peak_rss_bytes"), "inputs": res.get("inputs"),
                  "precision": precision, "operators": len(res.get("operators", [])),
                  "notes": res.get("notes", []), "comparison": comparison,
                  "hardware_label": m.hardware_label}  # fmt: skip


def view(r: ExecutionJobRow) -> dict[str, Any]:
    return {"id": r.id, "backend": r.backend, "status": r.status, "params": r.params, "result": r.result,
            "error": r.error, "measurement_id": r.measurement_id, "trace_id": r.trace_id, "seconds": r.seconds,
            "created_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}  # fmt: skip


def list_jobs(db: Session, ws: int, project_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(ExecutionJobRow).where(ExecutionJobRow.workspace_id == ws,
                                                    ExecutionJobRow.project_id == project_id)
                      .order_by(ExecutionJobRow.id.desc()).limit(50))  # fmt: skip
    return [view(r) for r in rows]


def get_job(db: Session, ws: int, project_id: int, job_id: int) -> ExecutionJobRow | None:
    r = db.get(ExecutionJobRow, job_id)
    return r if r is not None and r.workspace_id == ws and r.project_id == project_id else None


def recover_stale(db: Session) -> int:
    """Jobs left queued/running by a restart can never finish: mark them failed, say why."""
    n = 0
    for r in db.scalars(select(ExecutionJobRow).where(ExecutionJobRow.status.in_(("queued", "running")))):
        r.status, r.error, n = "failed", "The server restarted while this run was in progress.", n + 1
    db.commit()
    return n
