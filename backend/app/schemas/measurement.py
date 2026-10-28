from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MeasurementSource = Literal[
    "bench_job", "local_bench", "nvidia_smi", "tegrastats", "manual", "replay", "remote_agent"
]


class MeasurementIn(BaseModel):
    """One real measurement of a (device, model, precision, batch) point.

    Fields are optional where a tool cannot supply them; nothing is ever filled with a guess.
    """

    device_id: str
    model_id: str
    precision: Literal["fp32", "fp16", "int8"]
    batch: int = Field(1, ge=1)
    resolution_scale: float = Field(1.0, gt=0, le=1.0)
    power_mode_id: str | None = None
    cooling_id: str | None = None
    source: MeasurementSource = "manual"
    hardware_label: str = ""
    ambient_c: float | None = None

    latency_ms_mean: float | None = Field(None, gt=0)
    latency_ms_p50: float | None = Field(None, gt=0)
    latency_ms_p95: float | None = Field(None, gt=0)
    latency_ms_p99: float | None = Field(None, gt=0)
    throughput_fps: float | None = Field(None, gt=0)
    power_w_avg: float | None = Field(None, gt=0)
    power_w_peak: float | None = Field(None, gt=0)
    power_boundary: str | None = None  # e.g. "module" (Jetson VDD_IN) or "gpu_board"
    temperature_c_max: float | None = None
    # Accuracy retained vs the same model's fp32 result on the same validation set (0..1.x).
    accuracy_retention: float | None = Field(None, gt=0, le=1.5)

    n_runs: int | None = Field(None, ge=1)
    measured_at: datetime | None = None
    notes: str = ""
    synthetic: bool = False  # test data only; never shown as evidence
    external_id: str | None = None  # makes imports idempotent

    @model_validator(mode="after")
    def _has_a_metric(self) -> MeasurementIn:
        got = any(
            v is not None
            for v in (
                self.latency_ms_mean,
                self.throughput_fps,
                self.power_w_avg,
                self.temperature_c_max,
                self.accuracy_retention,
            )
        )
        if not got:
            raise ValueError(
                "a measurement needs at least one of latency, throughput, power, "
                "temperature or accuracy_retention"
            )
        return self

    @property
    def batch_latency_ms(self) -> float | None:
        """Time for one batch, from latency or, failing that, from throughput."""
        if self.latency_ms_mean is not None:
            return self.latency_ms_mean
        if self.throughput_fps is not None:
            return self.batch / self.throughput_fps * 1e3
        return None


class MeasurementOut(MeasurementIn):
    id: int
    created_at: datetime
    # True for measurements committed with the app (shared, read-only); False for your own.
    library: bool = False


class Calibration(BaseModel):
    """Correction factors for one (device, precision), fitted from measurements."""

    device_id: str
    precision: Literal["fp32", "fp16", "int8"]
    version: int = 0
    eta_scale: float = 1.0  # multiplies achieved compute/memory efficiency
    power_scale: float = 1.0  # multiplies every power term
    eta_width: float | None = None  # empirical band half-width replacing the default corner
    power_width: float | None = None
    latency_calibrated: bool = False
    power_calibrated: bool = False
    n_points: int = 0


class FitPoint(BaseModel):
    """One measurement compared with the uncalibrated and the leave-one-out-calibrated model."""

    measurement_id: int
    model_id: str
    batch: int
    measured_ms: float
    predicted_ms: float  # uncalibrated analytical model
    predicted_cal_ms: float | None = None  # calibrated using the *other* points only
    band_lo_ms: float | None = None  # best/worst-case band around predicted_cal_ms
    band_hi_ms: float | None = None
    measured_power_w: float | None = None
    predicted_power_w: float | None = None
    predicted_cal_power_w: float | None = None


class MetricFit(BaseModel):
    n: int
    error_before: float | None  # mean absolute relative error, uncalibrated
    error_after: float | None  # leave-one-out, calibrated
    factor: float
    accepted: bool
    reason: str


class CalibrationReport(BaseModel):
    device_id: str
    precision: str
    version: int
    n_points: int
    latency: MetricFit
    power: MetricFit
    calibration: Calibration
    points: list[FitPoint]


class VerifyRow(BaseModel):
    """Predicted vs measured for one measurement (the Verify screen)."""

    measurement: MeasurementOut
    predicted_ms: float
    predicted_cal_ms: float | None
    error_before: float | None  # signed relative error (pred - meas) / meas
    error_after: float | None
    inside_band: bool | None  # measured latency within the calibrated best/worst-case band
    measured_power_w: float | None
    predicted_power_w: float | None
    predicted_cal_power_w: float | None


class VerifySummary(BaseModel):
    device_id: str
    precision: str
    n: int
    mape_before: float | None
    mape_after: float | None
    coverage: float | None  # share of points whose measurement falls inside the band
    calibration_version: int | None
    calibration_active: bool
    note: str


class VerifyOut(BaseModel):
    rows: list[VerifyRow]
    summaries: list[VerifySummary]
