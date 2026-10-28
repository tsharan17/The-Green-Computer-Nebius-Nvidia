"""Measurement-anchored calibration with a leave-one-out acceptance gate.

For one (device, precision) we compare the analytical model with real measurements and fit two
scalar corrections:

  eta_scale    multiplies achieved compute/memory efficiency; chosen per point so the predicted
               batch latency matches the measured one, then combined by a shrunk geometric mean
  power_scale  multiplies every power term (only from points that carry a power measurement)

A fit is **accepted only if it demonstrably predicts held-out points better**: each point is
predicted from a fit to all the *other* points, and the mean absolute relative error must fall to
CALIB_ACCEPT_RATIO of the uncalibrated error. Otherwise the factors stay at 1.0 and the report
says why. Nothing here ever edits predictions by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.engine import assumptions as A
from app.engine.solver import NOMINAL, Corner, corners, simulate
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import (
    Calibration,
    CalibrationReport,
    FitPoint,
    MeasurementIn,
    MetricFit,
)
from app.schemas.model import ModelProfile
from app.schemas.project import Workload
from app.schemas.results import Config


@dataclass(frozen=True)
class CalibItem:
    measurement_id: int
    model: ModelProfile
    m: MeasurementIn


def saturated_workload(ambient_c: float | None) -> Workload:
    """Benchmarks run flat out: demand far above capacity, so duty = 1."""
    return Workload(fps=1e7, ambient_c=25.0 if ambient_c is None else ambient_c, streaming=False)


def config_for(hw: HardwareProfile, m: MeasurementIn) -> Config:
    top = max(hw.power_modes, key=lambda p: p.cap_w)
    return Config(
        precision=m.precision,
        batch=m.batch,
        power_mode_id=m.power_mode_id or top.id,
        cooling_id=m.cooling_id or hw.default_cooling,
        resolution_scale=m.resolution_scale,
    )


def _t_ms(item: CalibItem, hw: HardwareProfile, corner: Corner) -> float:
    cfg = config_for(hw, item.m)
    wl = saturated_workload(item.m.ambient_c)
    return simulate(item.model, hw, cfg, wl, corner).rf.t_batch_s * 1e3


def _power_w(item: CalibItem, hw: HardwareProfile, corner: Corner) -> float:
    cfg = config_for(hw, item.m)
    wl = saturated_workload(item.m.ambient_c)
    return simulate(item.model, hw, cfg, wl, corner).pw.p_avg_w


def _solve_eta(item: CalibItem, hw: HardwareProfile, measured_ms: float) -> float:
    """Efficiency multiplier that makes predicted batch latency equal the measured one."""
    lo, hi = A.ETA_SOLVE_MIN, A.ETA_SOLVE_MAX
    if _t_ms(item, hw, Corner(eta=hi)) > measured_ms:
        return hi  # even a very efficient device could not be this fast: clipped
    if _t_ms(item, hw, Corner(eta=lo)) < measured_ms:
        return lo
    for _ in range(40):  # bisection in log space; latency falls as efficiency rises
        mid = math.sqrt(lo * hi)
        if _t_ms(item, hw, Corner(eta=mid)) > measured_ms:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def _shrink(logs: list[float]) -> float:
    n = len(logs)
    if n == 0:
        return 1.0
    return math.exp(sum(logs) / n * n / (n + A.CALIB_SHRINK_K))


def _mare(pairs: list[tuple[float, float]]) -> float:
    """Mean absolute relative error of (predicted, measured) pairs."""
    return sum(abs(p - m) / m for p, m in pairs) / len(pairs)


def fit_calibration(
    items: list[CalibItem], hw: HardwareProfile, device_id: str, precision: str, version: int
) -> CalibrationReport:
    lat_items = [it for it in items if it.m.batch_latency_ms is not None]
    n = len(lat_items)
    meas = [float(it.m.batch_latency_ms or 0.0) for it in lat_items]  # filtered non-None above
    s_i = [_solve_eta(it, hw, ms) for it, ms in zip(lat_items, meas, strict=True)]
    uncal = [_t_ms(it, hw, NOMINAL) for it in lat_items]
    err_before = _mare(list(zip(uncal, meas, strict=True))) if n else None

    points: list[FitPoint] = []
    loo_err: float | None = None
    eta_scale, eta_width, lat_ok = 1.0, None, False
    reason = f"need at least {A.CALIB_MIN_POINTS} latency measurements (have {n})"
    loo_pred: list[float] = []
    if n >= A.CALIB_MIN_POINTS:
        for i, it in enumerate(lat_items):
            s_minus = _shrink([math.log(s) for j, s in enumerate(s_i) if j != i])
            loo_pred.append(_t_ms(it, hw, Corner(eta=s_minus)))
        loo_err = _mare(list(zip(loo_pred, meas, strict=True)))
        assert err_before is not None
        lat_ok = loo_err <= A.CALIB_ACCEPT_RATIO * err_before
        if lat_ok:
            eta_scale = _shrink([math.log(s) for s in s_i])
            worst = max(abs(p - m) / m for p, m in zip(loo_pred, meas, strict=True))
            eta_width = min(
                A.CALIB_WIDTH_MAX, max(A.CALIB_WIDTH_MIN, worst * A.CALIB_WIDTH_MARGIN)
            )
            reason = (
                f"leave-one-out error fell from {err_before:.1%} to {loo_err:.1%}; accepted"
            )
            if eta_scale * A.ETA_COMPUTE[precision] > 1.0:
                reason += (
                    ". The fitted efficiency exceeds 100% of the profile's peak throughput, so "
                    "the profile's peak (or bandwidth) is probably conservative for this device"
                )
        else:
            reason = (
                f"leave-one-out error {loo_err:.1%} did not improve enough on the uncalibrated "
                f"{err_before:.1%} (needs <= {A.CALIB_ACCEPT_RATIO:.0%} of it); rejected"
            )

    # Power: only points that carry a power measurement; centred on the accepted eta.
    pw_items = [it for it in items if it.m.power_w_avg is not None]
    p_meas = [it.m.power_w_avg for it in pw_items]
    eta_c = Corner(eta=eta_scale)
    p_uncal = [_power_w(it, hw, eta_c) for it in pw_items]
    p_ratio = [m / p for m, p in zip(p_meas, p_uncal, strict=True)]  # type: ignore[operator]
    p_err_before = _mare(list(zip(p_uncal, p_meas, strict=True))) if pw_items else None  # type: ignore[arg-type]
    power_scale, power_width, pw_ok, p_err_after = 1.0, None, False, None
    p_reason = f"need at least {A.CALIB_MIN_POINTS} power measurements (have {len(pw_items)})"
    if len(pw_items) >= A.CALIB_MIN_POINTS:
        p_loo = []
        for i, p in enumerate(p_uncal):
            f = _shrink([math.log(r) for j, r in enumerate(p_ratio) if j != i])
            p_loo.append(p * f)
        p_err_after = _mare(list(zip(p_loo, p_meas, strict=True)))  # type: ignore[arg-type]
        assert p_err_before is not None
        pw_ok = p_err_after <= A.CALIB_ACCEPT_RATIO * p_err_before
        if pw_ok:
            power_scale = _shrink([math.log(r) for r in p_ratio])
            worst = max(abs(a - m) / m for a, m in zip(p_loo, p_meas, strict=True))  # type: ignore[operator]
            power_width = min(
                A.CALIB_WIDTH_MAX, max(A.CALIB_WIDTH_MIN, worst * A.CALIB_WIDTH_MARGIN)
            )
            p_reason = (
                f"leave-one-out error fell from {p_err_before:.1%} to {p_err_after:.1%}; accepted"
            )
        else:
            p_reason = (
                f"leave-one-out error {p_err_after:.1%} did not improve enough on the "
                f"uncalibrated {p_err_before:.1%}; rejected"
            )

    calib = Calibration(
        device_id=device_id,
        precision=precision,  # type: ignore[arg-type]
        version=version,
        eta_scale=eta_scale,
        power_scale=power_scale,
        eta_width=eta_width,
        power_width=power_width,
        latency_calibrated=lat_ok,
        power_calibrated=pw_ok,
        n_points=len(items),
    )

    # Per-point table: uncalibrated, leave-one-out calibrated, and the band around the latter.
    p_by_id = {
        it.measurement_id: (pm, pu) for it, pm, pu in zip(pw_items, p_meas, p_uncal, strict=True)
    }
    for i, it in enumerate(lat_items):
        pm, pu = p_by_id.get(it.measurement_id, (None, None))
        lo = hi = cal = None
        if loo_pred:
            cal = loo_pred[i]
            w = eta_width if eta_width is not None else A.CORNER_ETA
            s_minus = _shrink([math.log(s) for j, s in enumerate(s_i) if j != i])
            lo = _t_ms(it, hw, Corner(eta=s_minus * (1 + w)))
            hi = _t_ms(it, hw, Corner(eta=s_minus * max(1 - w, 0.05)))
        points.append(
            FitPoint(
                measurement_id=it.measurement_id,
                model_id=it.m.model_id,
                batch=it.m.batch,
                measured_ms=meas[i],
                predicted_ms=uncal[i],
                predicted_cal_ms=cal,
                band_lo_ms=lo,
                band_hi_ms=hi,
                measured_power_w=pm,
                predicted_power_w=pu if pm is not None else None,
            )
        )

    return CalibrationReport(
        device_id=device_id,
        precision=precision,
        version=version,
        n_points=len(items),
        latency=MetricFit(
            n=n,
            error_before=err_before,
            error_after=loo_err,
            factor=eta_scale,
            accepted=lat_ok,
            reason=reason,
        ),
        power=MetricFit(
            n=len(pw_items),
            error_before=p_err_before,
            error_after=p_err_after,
            factor=power_scale,
            accepted=pw_ok,
            reason=p_reason,
        ),
        calibration=calib,
        points=points,
    )


__all__ = ["CalibItem", "config_for", "corners", "fit_calibration", "saturated_workload"]
