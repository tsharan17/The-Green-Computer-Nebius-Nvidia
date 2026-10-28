"""Data access. Every function touching owned data takes the caller's workspace and only ever
returns that workspace's rows (plus shared library rows where the table has them).

`workspace_id=None` means "no tenant": the CLI, the SDK, library maintenance at start-up and the
older single-user tests. It sees only unowned rows, never a tenant's.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    CalibrationRow,
    CustomModelRow,
    MeasurementRow,
    ProjectRow,
    ProjectVersionRow,
    RunRow,
)
from app.schemas.measurement import (
    Calibration,
    CalibrationReport,
    MeasurementIn,
    MeasurementOut,
)
from app.schemas.model import ModelProfile
from app.schemas.project import ProjectOut, ProjectSpec


def _owned(col, workspace_id: int | None):
    return col.is_(None) if workspace_id is None else col == workspace_id


def _visible(col, workspace_id: int | None):
    """Owned rows plus shared library rows (NULL owner)."""
    return col.is_(None) if workspace_id is None else or_(col == workspace_id, col.is_(None))


def _out(row: ProjectRow) -> ProjectOut:
    return ProjectOut(
        id=row.id, spec=ProjectSpec.model_validate(row.spec), version=row.current_version,
        workspace_id=row.workspace_id, archived=bool(row.archived),
        created_at=row.created_at, updated_at=row.updated_at,
    )  # fmt: skip


def spec_hash(spec: ProjectSpec) -> str:
    return hashlib.sha256(
        json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def create_project(
    db: Session, spec: ProjectSpec, workspace_id: int | None = None, user_id: int | None = None
) -> ProjectOut:
    row = ProjectRow(name=spec.name, spec=spec.model_dump(mode="json"), workspace_id=workspace_id,
                     created_by=user_id, current_version=1)  # fmt: skip
    db.add(row)
    db.flush()
    db.add(ProjectVersionRow(project_id=row.id, version=1, spec=row.spec, spec_hash=spec_hash(spec),
                             note="created", created_by=user_id))  # fmt: skip
    db.commit()
    return _out(row)


def list_projects(
    db: Session, workspace_id: int | None = None, include_archived: bool = False
) -> list[ProjectOut]:
    q = select(ProjectRow).where(_owned(ProjectRow.workspace_id, workspace_id))
    if not include_archived:
        q = q.where(ProjectRow.archived.is_(False))
    return [_out(r) for r in db.scalars(q.order_by(ProjectRow.id))]


def _row(db: Session, project_id: int, workspace_id: int | None) -> ProjectRow | None:
    row = db.get(ProjectRow, project_id)
    if row is None or row.workspace_id != workspace_id:
        return None
    return row


def get_project(db: Session, project_id: int, workspace_id: int | None = None) -> ProjectOut | None:
    row = _row(db, project_id, workspace_id)
    return _out(row) if row else None


def update_project(
    db: Session, project_id: int, spec: ProjectSpec, workspace_id: int | None = None,
    user_id: int | None = None, note: str = "",
) -> ProjectOut | None:  # fmt: skip
    """Save inputs. A changed spec becomes a new immutable version; an unchanged one does not."""
    row = _row(db, project_id, workspace_id)
    if row is None:
        return None
    digest = spec_hash(spec)
    latest = current_version_row(db, project_id)
    if latest is None or latest.spec_hash != digest:
        nxt = (latest.version if latest else 0) + 1
        db.add(ProjectVersionRow(project_id=row.id, version=nxt, spec=spec.model_dump(mode="json"),
                                 spec_hash=digest, note=note[:500], created_by=user_id))  # fmt: skip
        row.current_version = nxt
    row.name = spec.name
    row.spec = spec.model_dump(mode="json")
    db.commit()
    return _out(row)


def set_archived(db: Session, project_id: int, workspace_id: int, archived: bool) -> ProjectOut | None:
    row = _row(db, project_id, workspace_id)
    if row is None:
        return None
    row.archived = archived
    db.commit()
    return _out(row)


def current_version_row(db: Session, project_id: int) -> ProjectVersionRow | None:
    return db.scalars(
        select(ProjectVersionRow)
        .where(ProjectVersionRow.project_id == project_id)
        .order_by(ProjectVersionRow.version.desc())
        .limit(1)
    ).first()


def list_versions(db: Session, project_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ProjectVersionRow)
        .where(ProjectVersionRow.project_id == project_id)
        .order_by(ProjectVersionRow.version.desc())
    )
    return [
        {"id": r.id, "version": r.version, "spec": r.spec, "note": r.note,
         "created_by": r.created_by, "created_at": r.created_at.isoformat()}
        for r in rows
    ]  # fmt: skip


def get_version(db: Session, project_id: int, version: int) -> ProjectVersionRow | None:
    return db.scalars(
        select(ProjectVersionRow).where(
            ProjectVersionRow.project_id == project_id, ProjectVersionRow.version == version
        )
    ).first()


def save_run(
    db: Session, project_id: int, kind: str, engine_version: str, result: dict,
    user_id: int | None = None,
) -> int:  # fmt: skip
    v = current_version_row(db, project_id)
    row = RunRow(project_id=project_id, kind=kind, engine_version=engine_version, result=result,
                 version_id=v.id if v else None, user_id=user_id)  # fmt: skip
    db.add(row)
    db.commit()
    return row.id


def latest_run(db: Session, project_id: int, kind: str) -> dict | None:
    row = latest_run_row(db, project_id, kind)
    return row.result if row else None


def latest_run_row(db: Session, project_id: int, kind: str) -> RunRow | None:
    return db.scalars(
        select(RunRow)
        .where(RunRow.project_id == project_id, RunRow.kind == kind)
        .order_by(RunRow.id.desc())
        .limit(1)
    ).first()


def get_run(db: Session, project_id: int, run_id: int) -> RunRow | None:
    row = db.get(RunRow, run_id)
    return row if row is not None and row.project_id == project_id else None


def list_runs(db: Session, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
    versions = {
        v.id: v.version
        for v in db.scalars(select(ProjectVersionRow).where(ProjectVersionRow.project_id == project_id))
    }
    rows = db.scalars(
        select(RunRow).where(RunRow.project_id == project_id).order_by(RunRow.id.desc()).limit(limit)
    )
    out = []
    for r in rows:
        res = r.result or {}
        summary: dict[str, Any] = {}
        base = res.get("baseline") if r.kind == "optimise" else res if r.kind == "evaluate" else None
        if isinstance(base, dict) and "feasibility" in base:
            summary["feasibility"] = base["feasibility"]
            m = base.get("metrics", {})
            for k in ("power_avg", "latency", "energy_per_inference", "temperature"):
                if k in m:
                    summary[k] = {"value": m[k]["value"], "unit": m[k]["unit"]}
        if r.kind == "optimise" and isinstance(res.get("best"), dict):
            summary["best_feasibility"] = res["best"].get("feasibility")
        out.append({
            "id": r.id, "kind": r.kind, "engine_version": r.engine_version,
            "version": versions.get(r.version_id) if r.version_id else None,
            "created_at": r.created_at.isoformat(), "user_id": r.user_id, "summary": summary,
        })  # fmt: skip
    return out


CUSTOM_PREFIX = "custom-"


def create_custom_model(
    db: Session, profile: ModelProfile, workspace_id: int | None = None,
    artifact_id: int | None = None,
) -> ModelProfile:  # fmt: skip
    row = CustomModelRow(name=profile.name, profile={}, workspace_id=workspace_id,
                         artifact_id=artifact_id)  # fmt: skip
    db.add(row)
    db.flush()  # assigns the integer id
    stored = profile.model_copy(update={"id": f"{CUSTOM_PREFIX}{row.id}"})
    row.profile = stored.model_dump(mode="json")
    db.commit()
    return stored


def list_custom_models(db: Session, workspace_id: int | None = None) -> list[ModelProfile]:
    rows = db.scalars(
        select(CustomModelRow)
        .where(_owned(CustomModelRow.workspace_id, workspace_id))
        .order_by(CustomModelRow.id)
    )
    return [ModelProfile.model_validate(r.profile) for r in rows]


def custom_model_row(db: Session, model_id: str, workspace_id: int | None = None) -> CustomModelRow | None:
    if not model_id.startswith(CUSTOM_PREFIX):
        return None
    try:
        row = db.get(CustomModelRow, int(model_id[len(CUSTOM_PREFIX) :]))
    except ValueError:
        return None
    if row is None or row.workspace_id != workspace_id:
        return None
    return row


def get_custom_model(db: Session, model_id: str, workspace_id: int | None = None) -> ModelProfile | None:
    row = custom_model_row(db, model_id, workspace_id)
    return ModelProfile.model_validate(row.profile) if row else None


def _m_out(row: MeasurementRow) -> MeasurementOut:
    return MeasurementOut(id=row.id, created_at=row.created_at, library=row.workspace_id is None,
                          **row.record)  # fmt: skip


def add_measurement(
    db: Session, m: MeasurementIn, workspace_id: int | None = None
) -> tuple[MeasurementOut, bool]:
    """Insert a measurement; an already-imported external_id is returned, not duplicated.

    external_id is globally unique, so a tenant's id is namespaced to keep tenants from colliding
    with (or detecting) each other's imports.
    """
    if m.external_id and workspace_id is not None and not m.external_id.startswith("w"):
        m = m.model_copy(update={"external_id": f"w{workspace_id}:{m.external_id}"[:80]})
    if m.external_id:
        existing = db.scalars(
            select(MeasurementRow).where(MeasurementRow.external_id == m.external_id)
        ).first()
        if existing is not None and existing.workspace_id == workspace_id:
            return _m_out(existing), False
        if existing is not None:
            m = m.model_copy(update={"external_id": None})
    row = MeasurementRow(
        device_id=m.device_id,
        model_id=m.model_id,
        precision=m.precision,
        batch=m.batch,
        external_id=m.external_id,
        record=m.model_dump(mode="json"),
        workspace_id=workspace_id,
    )
    db.add(row)
    db.commit()
    return _m_out(row), True


def list_measurements(
    db: Session,
    device_id: str | None = None,
    model_id: str | None = None,
    precision: str | None = None,
    workspace_id: int | None = None,
) -> list[MeasurementOut]:
    q = select(MeasurementRow).where(_visible(MeasurementRow.workspace_id, workspace_id))
    q = q.order_by(MeasurementRow.id)
    if device_id:
        q = q.where(MeasurementRow.device_id == device_id)
    if model_id:
        q = q.where(MeasurementRow.model_id == model_id)
    if precision:
        q = q.where(MeasurementRow.precision == precision)
    return [_m_out(r) for r in db.scalars(q)]


def delete_measurement(
    db: Session, measurement_id: int, workspace_id: int | None = None
) -> MeasurementOut | None:
    """Only a workspace's own measurements can be deleted (library rows are read-only)."""
    row = db.get(MeasurementRow, measurement_id)
    if row is None or row.workspace_id != workspace_id:
        return None
    out = _m_out(row)
    from app.db.models import ExecutionJobRow

    db.execute(update(ExecutionJobRow).where(ExecutionJobRow.measurement_id == row.id).values(measurement_id=None))
    db.delete(row)
    db.commit()
    return out


def next_calibration_version(
    db: Session, device_id: str, precision: str, workspace_id: int | None = None
) -> int:
    rows = db.scalars(
        select(CalibrationRow.version)
        .where(CalibrationRow.device_id == device_id, CalibrationRow.precision == precision,
               _owned(CalibrationRow.workspace_id, workspace_id))  # fmt: skip
        .order_by(CalibrationRow.version.desc())
        .limit(1)
    ).first()
    return (rows or 0) + 1


def save_calibration(db: Session, report: CalibrationReport, workspace_id: int | None = None) -> None:
    cal = report.calibration
    db.add(
        CalibrationRow(
            device_id=report.device_id,
            precision=report.precision,
            version=report.version,
            accepted=cal.latency_calibrated or cal.power_calibrated,
            calibration=cal.model_dump(mode="json"),
            report=report.model_dump(mode="json"),
            workspace_id=workspace_id,
        )
    )
    db.commit()


def has_calibration(db: Session, device_id: str, precision: str, workspace_id: int | None) -> bool:
    return db.scalars(
        select(CalibrationRow.id).where(
            CalibrationRow.device_id == device_id, CalibrationRow.precision == precision,
            _owned(CalibrationRow.workspace_id, workspace_id),
        ).limit(1)
    ).first() is not None  # fmt: skip


def active_calibration(
    db: Session, device_id: str, precision: str, workspace_id: int | None = None
) -> Calibration | None:
    """The workspace's own latest fit if it has one for this (device, precision), else the library
    fit. A workspace fit includes the library measurements, so it supersedes the library one."""

    def latest(ws: int | None) -> CalibrationRow | None:
        return db.scalars(
            select(CalibrationRow)
            .where(
                CalibrationRow.device_id == device_id,
                CalibrationRow.precision == precision,
                CalibrationRow.accepted.is_(True),
                _owned(CalibrationRow.workspace_id, ws),
            )
            .order_by(CalibrationRow.version.desc())
            .limit(1)
        ).first()

    row = latest(workspace_id) if workspace_id is not None else None
    if row is None:
        row = latest(None)
    return Calibration.model_validate(row.calibration) if row else None


def calibration_history(
    db: Session, device_id: str | None = None, workspace_id: int | None = None
) -> list[dict]:
    q = select(CalibrationRow).where(_visible(CalibrationRow.workspace_id, workspace_id))
    q = q.order_by(CalibrationRow.id.desc())
    if device_id:
        q = q.where(CalibrationRow.device_id == device_id)
    return [
        {
            "device_id": r.device_id,
            "precision": r.precision,
            "version": r.version,
            "accepted": r.accepted,
            "scope": "library" if r.workspace_id is None else "workspace",
            "created_at": r.created_at.isoformat(),
            "calibration": r.calibration,
            "latency": r.report["latency"],
            "power": r.report["power"],
        }
        for r in db.scalars(q)
    ]
