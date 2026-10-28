import itertools
import math

import pytest

from app.data_loader import get_device, get_model
from app.engine.evaluator import evaluate
from app.engine.solver import NOMINAL, corners, simulate
from app.engine.thermal import time_to_limit, warmup_trace
from app.schemas.metrics import Feasibility
from app.schemas.project import Constraints, Workload
from app.schemas.results import Config

HW = get_device("jetson-orin-nx")
M = get_model("yolov8s")


def cfg(**kw):
    base = dict(precision="fp16", batch=1, power_mode_id="MAXN", cooling_id="passive_small")
    return Config(**{**base, **kw})


def test_fixed_point_converges_or_is_flagged_across_the_grid():
    for amb, mode, cool, prec in itertools.product(
        (10, 25, 40, 55),
        [m.id for m in HW.power_modes],
        [c.id for c in HW.cooling_options],
        ("fp32", "fp16", "int8"),
    ):
        s = simulate(M, HW, cfg(power_mode_id=mode, cooling_id=cool, precision=prec),
                     Workload(ambient_c=amb), NOMINAL)  # fmt: skip
        assert s.converged or s.overheated, (amb, mode, cool, prec)
        if s.throttled and not s.overheated:
            assert s.temp_c <= HW.t_throttle_c + 0.5
            assert 0 < s.clock_factor < 1
        if not s.throttled:
            assert s.clock_factor == 1.0


def test_solver_is_deterministic():
    a = simulate(M, HW, cfg(precision="fp32"), Workload(), NOMINAL)
    b = simulate(M, HW, cfg(precision="fp32"), Workload(), NOMINAL)
    assert a == b


def test_throttling_cuts_capacity_and_raises_latency():
    hot = Workload(ambient_c=40)
    throttled = simulate(M, HW, cfg(precision="fp32"), hot, NOMINAL)
    assert throttled.throttled
    from app.engine.roofline import roofline

    free = roofline(M, HW, HW.mode("MAXN"), "fp32", 1)
    assert throttled.rf.t_batch_s > free.t_batch_s


def test_leakage_raises_power_with_ambient():
    cool_cfg = cfg(power_mode_id="15W", cooling_id="fan_heatsink")
    cold = simulate(M, HW, cool_cfg, Workload(ambient_c=10), NOMINAL)
    hot = simulate(M, HW, cool_cfg, Workload(ambient_c=50), NOMINAL)
    assert not cold.throttled and not hot.throttled
    assert hot.pw.p_avg_w > cold.pw.p_avg_w


def test_bands_come_from_corner_resolves_and_are_ordered():
    opt, nom, pess = (simulate(M, HW, cfg(), Workload(), c) for c in corners(M))
    assert opt.rf.t_batch_s < nom.rf.t_batch_s < pess.rf.t_batch_s
    assert opt.pw.p_avg_w < nom.pw.p_avg_w < pess.pw.p_avg_w
    r = evaluate(M, HW, cfg(), Workload(), Constraints())
    for k in ("latency", "power_avg", "temperature", "energy_per_inference"):
        m = r.metrics[k]
        assert m.low <= m.value <= m.high
    assert r.metrics["latency"].high > r.metrics["latency"].low


def test_manual_models_get_wider_bands():
    lib = M
    manual = M.model_copy(update={"id": "manual-copy", "fidelity": "manual"})
    a = evaluate(lib, HW, cfg(power_mode_id="15W"), Workload(), Constraints())
    b = evaluate(manual, HW, cfg(power_mode_id="15W"), Workload(), Constraints())
    wa = a.metrics["latency"].high - a.metrics["latency"].low
    wb = b.metrics["latency"].high - b.metrics["latency"].low
    assert wb > wa
    assert any("widened" in n for n in b.notes)


def test_resolution_scaling_speeds_up_and_is_capped_uncertain():
    full = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink"), Workload(),
                    Constraints())  # fmt: skip
    half = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink",
                               resolution_scale=0.9), Workload(), Constraints())  # fmt: skip
    assert half.metrics["latency"].value < full.metrics["latency"].value
    assert half.metrics["accuracy"].value < full.metrics["accuracy"].value
    assert half.feasibility == Feasibility.UNCERTAIN
    assert any("resolution" in n for n in half.notes)


def test_resolution_rejected_for_non_scalable_models():
    m = M.model_copy(update={"id": "x", "resolution_scalable": False})
    with pytest.raises(ValueError):
        evaluate(m, HW, cfg(resolution_scale=0.5), Workload(), Constraints())


def test_warmup_trace_matches_closed_form():
    p, r, c, amb, lim = 30.0, 2.0, 100.0, 25.0, 70.0  # steady 85 C > 70 C
    t_star = time_to_limit(p, r, c, amb, lim)
    assert t_star is not None
    assert amb + p * r * (1 - math.exp(-t_star / (r * c))) == pytest.approx(lim)
    trace = warmup_trace(p, r, c, amb, lim)
    temps = [t for _, t in trace]
    assert temps[0] == pytest.approx(amb)
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(temps))
    assert max(temps) == pytest.approx(lim)
    assert time_to_limit(10.0, 2.0, 100.0, 25.0, 70.0) is None  # steady 45 C never reaches 70 C


def test_evaluation_reports_time_to_throttle_when_throttled():
    r = evaluate(M, HW, cfg(precision="fp32"), Workload(ambient_c=40), Constraints())
    assert r.solver.throttled and r.time_to_throttle_s and r.time_to_throttle_s > 0
    assert r.thermal_trace and r.thermal_trace[-1][1] == pytest.approx(HW.t_throttle_c)
    ok = evaluate(M, HW, cfg(power_mode_id="10W", cooling_id="fan_heatsink"), Workload(),
                  Constraints())  # fmt: skip
    assert not ok.solver.throttled and ok.time_to_throttle_s is None


def test_streaming_batch_adds_assembly_wait_to_latency():
    one = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink"),
                   Workload(fps=30), Constraints())  # fmt: skip
    eight = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink", batch=8),
                     Workload(fps=30), Constraints())  # fmt: skip
    wait = 7 / 30 * 1e3
    assert eight.metrics["latency"].value == pytest.approx(
        eight.metrics["batch_time"].value + wait)  # fmt: skip
    assert one.metrics["latency"].value == pytest.approx(one.metrics["batch_time"].value)
    offline = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink", batch=8),
                       Workload(fps=30, streaming=False), Constraints())  # fmt: skip
    assert offline.metrics["latency"].value == pytest.approx(offline.metrics["batch_time"].value)


def test_latency_limit_rejects_big_batches_on_a_stream():
    cons = Constraints(max_latency_ms=100)
    big = evaluate(M, HW, cfg(power_mode_id="25W", cooling_id="fan_heatsink", batch=16),
                   Workload(fps=30), cons)  # fmt: skip
    lat = next(c for c in big.checks if c.name == "latency")
    assert lat.status == "fail"  # 15 frames at 30 fps is already 500 ms
