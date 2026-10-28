import math

import pytest

from app.data_loader import get_device, get_model
from app.engine import assumptions as A
from app.engine.power import power
from app.engine.roofline import roofline
from app.engine.thermal import steady_state


def test_roofline_matches_hand_calculation():
    m, hw = get_model("yolov8s"), get_device("jetson-orin-nx")
    r = roofline(m, hw, hw.mode("MAXN"), "fp16", 1)
    t_c = 28.6e9 / (9.5e12 * A.ETA_COMPUTE["fp16"])
    bytes_ = 11.2e6 * 2 + 300e6 * 0.5
    t_m = bytes_ / (102.4e9 * A.ETA_MEMORY)
    expected = max(t_c, t_m) + A.KAPPA * min(t_c, t_m) + 72 * A.LAUNCH_OVERHEAD_S
    assert math.isclose(r.t_batch_s, expected, rel_tol=1e-9)


def test_lower_power_mode_is_slower():
    m, hw = get_model("yolov8s"), get_device("jetson-orin-nx")
    fast = roofline(m, hw, hw.mode("MAXN"), "int8", 1).t_batch_s
    slow = roofline(m, hw, hw.mode("10W"), "int8", 1).t_batch_s
    assert slow > fast


def test_int8_faster_than_fp32():
    m, hw = get_model("resnet-50"), get_device("jetson-agx-orin")
    mode = hw.mode("MAXN")
    assert roofline(m, hw, mode, "int8", 1).t_batch_s < roofline(m, hw, mode, "fp32", 1).t_batch_s


def test_unsupported_precision_and_bad_batch_rejected():
    m, hw = get_model("yolov8s"), get_device("jetson-orin-nx")
    with pytest.raises(ValueError):
        roofline(m, hw, hw.mode("MAXN"), "fp8", 1)
    with pytest.raises(ValueError):
        roofline(m, hw, hw.mode("MAXN"), "fp16", 0)


def test_memory_grows_with_batch():
    hw, m = get_device("jetson-orin-nano"), get_model("yolov8m")
    small = roofline(m, hw, hw.mode("MAXN"), "fp32", 1).mem_gb
    big = roofline(m, hw, hw.mode("MAXN"), "fp32", 8).mem_gb
    assert big > small > 0


def test_power_duty_cycle_and_average_between_floor_and_active():
    m, hw = get_model("yolov8s"), get_device("jetson-agx-orin")
    mode, cool = hw.mode("MAXN"), hw.cooling("fan_heatsink")
    rf = roofline(m, hw, mode, "fp16", 1)
    p = power(hw, mode, cool, rf.t_batch_s, 1, rf.util_compute, rf.util_memory, 30.0)
    assert p.p_floor_w <= p.p_avg_w <= p.p_active_w
    assert 0 < p.duty <= 1
    # Asking for far more than capacity saturates duty at 1.
    p2 = power(hw, mode, cool, rf.t_batch_s, 1, rf.util_compute, rf.util_memory, 1e6)
    assert p2.duty == 1.0 and math.isclose(p2.p_avg_w, p2.p_active_w)


def test_full_load_power_near_mode_cap_for_edge_devices():
    for dev in ("jetson-orin-nano", "jetson-orin-nx", "jetson-agx-orin"):
        hw = get_device(dev)
        for m in hw.power_modes:
            peak = (
                hw.idle_w
                + hw.leak_w_25c
                + hw.dyn_w_max * m.freq_scale**hw.gamma
                + hw.mem_w_max * m.mem_scale
            )
            assert 0.85 * m.cap_w <= peak <= 1.1 * m.cap_w, (dev, m.id, peak)


def test_thermal_steady_state():
    hw = get_device("jetson-agx-orin")
    t = steady_state(hw, hw.cooling("passive_large"), 40.0, 25.0)
    assert math.isclose(t.t_ss_c, 25 + 40 * 1.2)
    assert math.isclose(t.headroom_c, hw.t_throttle_c - t.t_ss_c)
