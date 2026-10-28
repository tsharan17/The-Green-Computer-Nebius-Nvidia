"""Lumped RC thermal model.

Steady state:  T_ss = T_amb + P_avg * R_th
Warm-up:       T(t) = T_amb + P * R_th * (1 - exp(-t / (R_th * C_th)))
Time to reach a limit T_lim (if T_ss > T_lim):
               t* = -R_th C_th * ln(1 - (T_lim - T_amb) / (P R_th))
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.schemas.hardware import CoolingOption, HardwareProfile


@dataclass(frozen=True)
class ThermalResult:
    t_ss_c: float
    rise_c: float
    headroom_c: float


def steady_state(
    hw: HardwareProfile,
    cooling: CoolingOption,
    p_avg_w: float,
    ambient_c: float,
    rth_scale: float = 1.0,
) -> ThermalResult:
    rise = p_avg_w * cooling.r_th_k_per_w * rth_scale
    t = ambient_c + rise
    return ThermalResult(t_ss_c=t, rise_c=rise, headroom_c=hw.t_throttle_c - t)


def time_to_limit(
    p_w: float, r_th: float, c_th: float, ambient_c: float, limit_c: float
) -> float | None:
    """Seconds until the limit is reached at constant power, or None if never."""
    rise_ss = p_w * r_th
    need = limit_c - ambient_c
    if rise_ss <= need or need <= 0:
        return 0.0 if need <= 0 else None
    return -r_th * c_th * math.log(1.0 - need / rise_ss)


def warmup_trace(
    p_w: float,
    r_th: float,
    c_th: float,
    ambient_c: float,
    limit_c: float | None = None,
    points: int = 60,
) -> list[tuple[float, float]]:
    """Temperature vs time at constant load; flat at `limit_c` once the governor engages."""
    tau = r_th * c_th
    t_star = (
        time_to_limit(p_w, r_th, c_th, ambient_c, limit_c) if limit_c is not None else None
    )
    horizon = max(4.0 * tau, 1.5 * t_star if t_star else 0.0)
    out = []
    for i in range(points + 1):
        t = horizon * i / points
        temp = ambient_c + p_w * r_th * (1.0 - math.exp(-t / tau))
        if limit_c is not None and t_star is not None and t >= t_star:
            temp = limit_c
        out.append((round(t, 2), round(temp, 3)))
    return out
