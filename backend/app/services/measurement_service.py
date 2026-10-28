"""Measurements in, calibration out, and the predicted-vs-measured view."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_loader import DATA_DIR, get_device, load_devices
from app.db import repository as repo
from app.engine.calibration import CalibItem, fit_calibration
from app.engine.measure_parsers import MeasurementParseError, parse_bench_json
from app.schemas.measurement import (
    Calibration,
    CalibrationReport,
    MeasurementIn,
    MeasurementOut,
    VerifyOut,
    VerifyRow,
    VerifySummary,
)
from app.services.model_lookup import resolve_model

MEASUREMENTS_DIR = DATA_DIR / "measurements"


class MeasurementError(ValueError):
    """A measurement refers to something the system does not know."""


def _check_refs(db: Session, m: MeasurementIn, ws: int | None = None) -> None:
    if m.device_id not in load_devices(ws):
        raise MeasurementError(f"unknown device '{m.device_id}'")
    try:
        resolve_model(db, m.model_id, ws)
    except KeyError as e:
        raise MeasurementError(str(e)) from e
    hw = get_device(m.device_id, ws)
    if m.power_mode_id and m.power_mode_id not in {p.id for p in hw.power_modes}:
        raise MeasurementError(f"unknown power mode '{m.power_mode_id}' for {hw.id}")
    if hw.peak_tflops.get(m.precision, 0) <= 0:
        raise MeasurementError(f"{hw.id} has no {m.precision} support in its profile")


def build_items(
    db: Session, device_id: str, precision: str, ws: int | None = None
) -> list[CalibItem]:
    items = []
    for m in repo.list_measurements(db, device_id=device_id, precision=precision, workspace_id=ws):
        if m.batch_latency_ms is None or m.synthetic:
            continue
        try:
            model = resolve_model(db, m.model_id, ws)
        except KeyError:
            continue
        items.append(CalibItem(m.id, model, MeasurementIn.model_validate(m.model_dump())))
    return items


def refit(db: Session, device_id: str, precision: str, ws: int | None = None) -> CalibrationReport:
    """Fit from every measurement the scope can see for (device, precision): library data, plus the
    workspace's own when `ws` is set. Stores the version (accepted or not) in that scope."""
    items = build_items(db, device_id, precision, ws)
    version = repo.next_calibration_version(db, device_id, precision, ws)
    report = fit_calibration(items, get_device(device_id, ws), device_id, precision, version)
    repo.save_calibration(db, report, ws)
    return report


def ingest(
    db: Session, records: list[MeasurementIn], ws: int | None = None
) -> tuple[list[MeasurementOut], int, list]:
    """Store measurements (idempotent on external_id) and refit each affected (device, precision)."""
    for m in records:
        _check_refs(db, m, ws)
    stored: list[MeasurementOut] = []
    created = 0
    touched: set[tuple[str, str]] = set()
    for m in records:
        out, was_new = repo.add_measurement(db, m, ws)
        stored.append(out)
        if was_new:
            created += 1
            touched.add((m.device_id, m.precision))
    reports = [refit(db, d, p, ws) for d, p in sorted(touched)]
    return stored, created, reports


def replay_committed(db: Session) -> tuple[int, int]:
    """Import measurement files committed under data/measurements (idempotent on external_id).
    Returns (created, skipped)."""
    if not MEASUREMENTS_DIR.is_dir():
        return 0, 0
    records: list[MeasurementIn] = []
    for f in sorted(MEASUREMENTS_DIR.glob("*.json")):
        try:
            records.extend(parse_bench_json(f.read_text(encoding="utf-8")))
        except MeasurementParseError:
            continue
    if not records:
        return 0, 0
    _, created, _ = ingest(db, records)
    # Library fits must exist even when nothing new was imported (e.g. after the 0002 migration
    # moved fits that included a user's own data into that user's workspace).
    for d, p in sorted({(r.device_id, r.precision) for r in records}):
        if not repo.has_calibration(db, d, p, None):
            refit(db, d, p, None)
    return created, len(records) - created


def active_calibrations(
    db: Session, device_id: str, ws: int | None = None
) -> dict[str, Calibration]:
    out: dict[str, Calibration] = {}
    for prec in ("fp32", "fp16", "int8"):
        c = repo.active_calibration(db, device_id, prec, ws)
        if c is not None:
            out[prec] = c
    return out


def measurements_for(
    db: Session, device_id: str, model_id: str, ws: int | None = None
) -> list[MeasurementIn]:
    return [
        MeasurementIn.model_validate(m.model_dump(exclude={"id", "created_at", "library"}))
        for m in repo.list_measurements(db, device_id=device_id, model_id=model_id, workspace_id=ws)
        if not m.synthetic
    ]


def measured_retention(db: Session, model_id: str, ws: int | None = None) -> dict[str, float]:
    """Latest measured accuracy retention per precision for this model."""
    out: dict[str, float] = {}
    for m in repo.list_measurements(db, model_id=model_id, workspace_id=ws):
        if m.accuracy_retention is not None and m.resolution_scale == 1.0 and not m.synthetic:
            out[m.precision] = m.accuracy_retention  # list is id-ordered: later wins
    return out


def verify(db: Session, device_id: str | None = None, ws: int | None = None) -> VerifyOut:
    rows: list[VerifyRow] = []
    summaries: list[VerifySummary] = []
    all_m = repo.list_measurements(db, device_id=device_id, workspace_id=ws)
    groups = sorted({(m.device_id, m.precision) for m in all_m if m.batch_latency_ms is not None})
    for dev, prec in groups:
        items = build_items(db, dev, prec, ws)
        if not items:
            continue
        try:
            hw = get_device(dev, ws)
        except KeyError:
            continue  # a measurement of a device that was since removed
        active = repo.active_calibration(db, dev, prec, ws)
        version = active.version if active else 0
        report = fit_calibration(items, hw, dev, prec, version)
        by_id = {m.id: m for m in all_m}
        inside: list[bool] = []
        for p in report.points:
            meas = by_id[p.measurement_id]
            inb = None
            if p.band_lo_ms is not None and p.band_hi_ms is not None:
                inb = p.band_lo_ms <= p.measured_ms <= p.band_hi_ms
                inside.append(inb)
            rows.append(
                VerifyRow(
                    measurement=meas,
                    predicted_ms=p.predicted_ms,
                    predicted_cal_ms=p.predicted_cal_ms,
                    error_before=(p.predicted_ms - p.measured_ms) / p.measured_ms,
                    error_after=(
                        (p.predicted_cal_ms - p.measured_ms) / p.measured_ms
                        if p.predicted_cal_ms is not None
                        else None
                    ),
                    inside_band=inb,
                    measured_power_w=p.measured_power_w,
                    predicted_power_w=p.predicted_power_w,
                    predicted_cal_power_w=None,
                )
            )
        lat = report.latency
        if active is None:
            note = lat.reason
        else:
            note = (
                f"Calibration v{active.version} is applied to {dev}/{prec} predictions. "
                + lat.reason
            )
        summaries.append(
            VerifySummary(
                device_id=dev,
                precision=prec,
                n=lat.n,
                mape_before=lat.error_before,
                mape_after=lat.error_after,
                coverage=(sum(inside) / len(inside)) if inside else None,
                calibration_version=active.version if active else None,
                calibration_active=active is not None,
                note=note,
            )
        )
    return VerifyOut(rows=rows, summaries=summaries)
