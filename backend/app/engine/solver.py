"""Coupled performance-power-thermal solver.

Power sets temperature, temperature sets leakage, and exceeding the throttle point makes the
governor lower the clock, which changes latency and power again. Each candidate is solved as a
fixed point, not a single pass:

1. For a clock multiplier theta, iterate  T <- T_amb + P_avg(T) * R_th  (damped) until it settles.
2. If T at theta = 1 exceeds the throttle point, bisect theta in [GOVERNOR_MIN_CLOCK, 1] for the
   highest clock that holds T <= T_throttle.
3. If even the minimum clock cannot hold the limit, or leakage runs away, the state is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine import assumptions as A
from app.engine.power import PowerResult, power
from app.engine.roofline import RooflineResult, roofline_at
from app.schemas.hardware import HardwareProfile
from app.schemas.measurement import Calibration
from app.schemas.model import ModelProfile
from app.schemas.project import Workload
from app.schemas.results import Config


@dataclass(frozen=True)
class Corner:
    """Multipliers applied to uncertain parameters when bounding a prediction."""

    eta: float = 1.0  # achieved efficiency
    power: float = 1.0  # all power terms
    rth: float = 1.0  # thermal resistance


NOMINAL = Corner()


def corners(
    model: ModelProfile, calib: Calibration | None = None
) -> tuple[Corner, Corner, Corner]:
    """(optimistic, nominal, pessimistic) corners.

    Uncalibrated, the widths are the stated priors (widened for low-information models). With a
    calibration the centre moves to the fitted factors and the width to the empirical
    leave-one-out error, so bands shrink only when measurements justify it.
    """
    w = A.FIDELITY_WIDEN[model.fidelity]
    eta_c = calib.eta_scale if calib else 1.0
    pw_c = calib.power_scale if calib else 1.0
    e = calib.eta_width if calib and calib.eta_width is not None else A.CORNER_ETA * w
    p = calib.power_width if calib and calib.power_width is not None else A.CORNER_POWER * w
    r = A.CORNER_RTH * w
    return (
        Corner(eta=eta_c * (1 + e), power=pw_c * (1 - p), rth=1 - r),
        Corner(eta=eta_c, power=pw_c, rth=1.0),
        Corner(eta=eta_c * max(1 - e, 0.05), power=pw_c * (1 + p), rth=1 + r),
    )


@dataclass(frozen=True)
class Sim:
    rf: RooflineResult
    pw: PowerResult
    temp_c: float  # steady temperature after any throttling
    temp_unthrottled_c: float  # what the config would reach with no governor
    p_avg_unthrottled_w: float
    clock_factor: float
    throttled: bool
    converged: bool
    overheated: bool  # even the minimum clock cannot hold the throttle point
    iterations: int
    freq_scale: float


def _steady(
    model: ModelProfile,
    hw: HardwareProfile,
    cfg: Config,
    wl: Workload,
    corner: Corner,
    theta: float,
) -> tuple[RooflineResult, PowerResult, float, bool, int, float]:
    mode = hw.mode(cfg.power_mode_id)
    cooling = hw.cooling(cfg.cooling_id)
    f = mode.freq_scale * cfg.clock_trim * theta
    rf = roofline_at(
        model, hw, f, mode.mem_scale, cfg.precision, cfg.batch, cfg.resolution_scale, corner.eta,
        cfg.sparsity,
    )
    r_th = cooling.r_th_k_per_w * corner.rth

    t = wl.ambient_c
    converged = False
    it = 0
    pw = None
    for _ in range(A.SOLVER_MAX_ITERS):
        it += 1
        pw = power(
            hw, mode, cooling, rf.t_batch_s, cfg.batch, rf.util_compute, rf.util_memory,
            wl.fps, freq_scale=f, power_scale=corner.power, temp_c=t,
        )  # fmt: skip
        t_new = wl.ambient_c + pw.p_avg_w * r_th
        if t_new > A.RUNAWAY_TEMP_C:
            t = t_new
            break
        delta = t_new - t
        t += A.SOLVER_DAMPING * delta
        if abs(delta) < A.SOLVER_TOL_C:
            converged = True
            break
    assert pw is not None
    return rf, pw, t, converged, it, f


def simulate(
    model: ModelProfile,
    hw: HardwareProfile,
    cfg: Config,
    wl: Workload,
    corner: Corner = NOMINAL,
) -> Sim:
    rf, pw, t, conv, iters, f = _steady(model, hw, cfg, wl, corner, 1.0)
    # A runaway (non-converged) full-clock state is reported at a clamped temperature.
    t_unthrottled, p_unthrottled = min(t, A.REPORT_TEMP_CAP_C), pw.p_avg_w
    theta, throttled, overheated = 1.0, False, False

    if (not conv) or t > hw.t_throttle_c:
        throttled = True
        lo, hi = A.GOVERNOR_MIN_CLOCK, 1.0
        rf_lo, pw_lo, t_lo, conv_lo, it_lo, f_lo = _steady(model, hw, cfg, wl, corner, lo)
        if not conv_lo or t_lo > hw.t_throttle_c:
            theta, rf, pw, t, conv, iters, f = lo, rf_lo, pw_lo, t_lo, conv_lo, it_lo, f_lo
            overheated = True
        else:
            best: tuple[float, RooflineResult, PowerResult, float, bool, int, float] = (
                lo, rf_lo, pw_lo, t_lo, conv_lo, it_lo, f_lo
            )
            for _ in range(A.BISECT_ITERS):
                mid = 0.5 * (lo + hi)
                state = _steady(model, hw, cfg, wl, corner, mid)
                if state[4] and state[2] <= hw.t_throttle_c:
                    lo = mid
                    best = (mid, *state[:2], state[2], state[3], state[4], state[5])
                else:
                    hi = mid
            theta, rf, pw, t, conv, iters, f = best

    return Sim(
        rf=rf,
        pw=pw,
        temp_c=t,
        temp_unthrottled_c=t_unthrottled,
        p_avg_unthrottled_w=p_unthrottled,
        clock_factor=theta,
        throttled=throttled,
        converged=conv,
        overheated=overheated,
        iterations=iters,
        freq_scale=f,
    )
