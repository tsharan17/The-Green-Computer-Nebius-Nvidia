"""Launch Nebius GPU benchmark jobs, follow them, and ingest their measurements."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_loader import get_device, load_devices
from app.db.models import BenchmarkJobRow
from app.engine.measure_parsers import MeasurementParseError, parse_bench_json
from app.integrations.jobs import (
    JobsError,
    JobSpec,
    NebiusJobs,
    extract_result_json,
    get_jobs,
)
from app.services import measurement_service as ms

TERMINAL = {"succeeded", "failed", "cancelled"}


def _view(row: BenchmarkJobRow) -> dict:
    return {
        "id": row.id,
        "job_name": row.job_name,
        "job_id": row.job_id,
        "device_id": row.device_id,
        "model_id": row.model_id,
        "precision": row.precision,
        "batches": row.batches,
        "state": row.state,
        "ingested": row.ingested,
        "measurement_ids": row.measurement_ids,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
    }


def _row(db: Session, row_id: int, ws: int | None) -> BenchmarkJobRow:
    row = db.get(BenchmarkJobRow, row_id)
    if row is None or row.workspace_id != ws:
        raise JobsError("benchmark job not found")
    return row


def submit(
    db: Session, spec: JobSpec, jobs: NebiusJobs | None = None, ws: int | None = None
) -> dict:
    jobs = jobs or get_jobs()
    if spec.device_id not in load_devices(ws):
        raise JobsError(f"unknown device '{spec.device_id}'")
    st = jobs.status()
    if not st.configured:
        raise JobsError(f"Nebius Serverless Jobs unavailable: {st.reason}")
    name, job_id = jobs.submit(spec, get_device(spec.device_id, ws))
    row = BenchmarkJobRow(
        job_name=name, job_id=job_id, device_id=spec.device_id, model_id=spec.model_id,
        precision=spec.precision, batches=list(spec.batches), state="running",
        measurement_ids=[], workspace_id=ws,
    )  # fmt: skip
    db.add(row)
    db.commit()
    return _view(row)


def list_jobs(db: Session, ws: int | None = None) -> list[dict]:
    col = BenchmarkJobRow.workspace_id
    q = select(BenchmarkJobRow).where(col.is_(None) if ws is None else col == ws)
    rows = db.scalars(q.order_by(BenchmarkJobRow.id.desc()))
    return [_view(r) for r in rows]


def refresh(db: Session, row_id: int, jobs: NebiusJobs | None = None, ws: int | None = None) -> dict:
    """Poll the job; when it has succeeded, read its logs and ingest the measurements once."""
    jobs = jobs or get_jobs()
    row = _row(db, row_id, ws)
    if row.state in TERMINAL and (row.ingested or row.state != "succeeded"):
        return _view(row)
    try:
        row.state = jobs.get(row.job_id).state
        if row.state == "succeeded" and not row.ingested:
            records = parse_bench_json(extract_result_json(jobs.logs(row.job_id)))
            stored, _created, _reports = ms.ingest(db, records, ws)
            row.ingested = True
            row.measurement_ids = [m.id for m in stored]
            row.error = None
    except (JobsError, MeasurementParseError, ms.MeasurementError) as e:
        row.error = str(e)[:590]
    db.commit()
    return _view(row)


def cancel(db: Session, row_id: int, jobs: NebiusJobs | None = None, ws: int | None = None) -> dict:
    jobs = jobs or get_jobs()
    row = _row(db, row_id, ws)
    jobs.cancel(row.job_id)
    row.state = "cancelled"
    db.commit()
    return _view(row)
