"""L0 power and energy.

P_active = P_idle + P_fan + P_leak(T) + P_dyn * f^gamma * u_c + P_mem * s_m * u_m
P_avg    = P_floor + duty * (P_active - P_floor),   P_floor = P_idle + P_fan + P_leak(T)
P_leak(T) = P_leak(25C) * exp(K_LEAK * (T - 25))

All terms are multiplied by `power_scale` (the uncertainty corner).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.engine import assumptions as A
from app.schemas.hardware import CoolingOption, HardwareProfile, PowerMode


@dataclass(frozen=True)
class PowerResult:
    p_floor_w: float
    p_active_w: float
    p_avg_w: float
    duty: float
    capacity_fps: float
    served_fps: float
    energy_per_inf_mj: float  # average, including idle share at the served rate
    incr_energy_per_inf_mj: float  # dynamic energy above the floor per inference


def power(
    hw: HardwareProfile,
    mode: PowerMode,
    cooling: CoolingOption,
    t_batch_s: float,
    batch: int,
    util_compute: float,
    util_memory: float,
    target_fps: float,
    *,
    freq_scale: float | None = None,
    power_scale: float = 1.0,
    temp_c: float = 25.0,
) -> PowerResult:
    f = mode.freq_scale if freq_scale is None else freq_scale
    ps = power_scale
    leak = hw.leak_w_25c * ps * math.exp(A.K_LEAK * (temp_c - 25.0))
    p_floor = (hw.idle_w + cooling.fan_w) * ps + leak
    p_dyn = hw.dyn_w_max * ps * (f**hw.gamma) * util_compute
    p_mem = hw.mem_w_max * ps * mode.mem_scale * util_memory
    p_active = p_floor + p_dyn + p_mem

    capacity = batch / t_batch_s
    served = min(target_fps, capacity)
    duty = min(1.0, target_fps / capacity)
    p_avg = p_floor + duty * (p_active - p_floor)

    return PowerResult(
        p_floor_w=p_floor,
        p_active_w=p_active,
        p_avg_w=p_avg,
        duty=duty,
        capacity_fps=capacity,
        served_fps=served,
        energy_per_inf_mj=p_avg / served * 1e3,
        incr_energy_per_inf_mj=(p_active - p_floor) * t_batch_s / batch * 1e3,
    )
