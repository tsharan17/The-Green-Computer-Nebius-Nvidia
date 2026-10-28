import itertools

import pytest

from app.data_loader import get_device, get_model, load_devices, load_models
from app.engine.evaluator import evaluate
from app.engine.optimise import optimise
from app.schemas.metrics import Feasibility, Provenance
from app.schemas.project import Constraints, ProjectSpec, Workload
from app.schemas.results import Config

INFEASIBLE = (Feasibility.INFEASIBLE, Feasibility.VERIFIED_INFEASIBLE)
FEASIBLE = (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)


def cfgs(hw):
    for p, b, m, c in itertools.product(
        [p for p in ("fp32", "fp16", "int8") if hw.peak_tflops.get(p, 0) > 0],
        (1, 2, 4, 8),
        [m.id for m in hw.power_modes],
        [c.id for c in hw.cooling_options],
    ):
        yield Config(precision=p, batch=b, power_mode_id=m, cooling_id=c)


def test_every_metric_has_provenance_and_ordered_band():
    hw, m = get_device("jetson-orin-nx"), get_model("yolov8s")
    cfg = Config(precision="fp16", batch=1, power_mode_id="25W", cooling_id="fan_heatsink")
    r = evaluate(m, hw, cfg, Workload(), Constraints())
    assert r.metrics
    for k, v in r.metrics.items():
        assert v.provenance in Provenance, k
        assert v.low <= v.value <= v.high, k


@pytest.mark.parametrize("dev", ["jetson-agx-orin", "nebius-l40s"])
def test_over_55w_is_never_feasible(dev):
    """Property: nothing whose power band exceeds the limit may be certified feasible."""
    hw = get_device(dev)
    for model in load_models().values():
        for cfg in cfgs(hw):
            r = evaluate(model, hw, cfg, Workload(), Constraints())
            p = r.metrics["power_avg"]
            if p.low > 55:
                assert r.feasibility in INFEASIBLE
            if r.feasibility in FEASIBLE:
                assert p.high <= 55


def test_accuracy_below_limit_is_never_feasible():
    hw, m = get_device("jetson-agx-orin"), get_model("yolov8s")
    cons = Constraints(min_accuracy=0.999)
    for cfg in cfgs(hw):
        r = evaluate(m, hw, cfg, Workload(), cons)
        acc = r.metrics["accuracy"]
        if acc.high < 0.999:
            assert r.feasibility in INFEASIBLE
        if r.feasibility in FEASIBLE:
            assert acc.low >= 0.999


def test_measured_accuracy_below_limit_fails_the_accuracy_check():
    hw, m = get_device("jetson-agx-orin"), get_model("yolov8s")
    cfg = Config(precision="int8", batch=1, power_mode_id="MAXN", cooling_id="fan_heatsink")
    r = evaluate(m, hw, cfg, Workload(), Constraints(), measured_retention=0.80)
    assert next(c for c in r.checks if c.name == "accuracy").status == "fail"
    assert r.feasibility in INFEASIBLE


def test_unmeasured_int8_capped_at_uncertain():
    hw, m = get_device("jetson-agx-orin"), get_model("yolov8s")
    cfg = Config(precision="int8", batch=1, power_mode_id="30W", cooling_id="fan_heatsink")
    r = evaluate(m, hw, cfg, Workload(), Constraints())
    assert r.feasibility == Feasibility.UNCERTAIN
    assert any("unmeasured" in n for n in r.notes)


def test_measured_int8_can_become_feasible():
    hw, m = get_device("jetson-agx-orin"), get_model("yolov8s")
    cfg = Config(precision="int8", batch=1, power_mode_id="30W", cooling_id="fan_heatsink")
    r = evaluate(m, hw, cfg, Workload(), Constraints(), measured_retention=0.97)
    assert r.feasibility == Feasibility.PREDICTED_FEASIBLE  # power etc. still analytical


def test_passive_cooling_throttles_in_hot_ambient_and_is_rejected():
    hw, m = get_device("jetson-orin-nx"), get_model("yolov8s")
    cfg = Config(precision="fp32", batch=1, power_mode_id="MAXN", cooling_id="passive_small")
    r = evaluate(m, hw, cfg, Workload(ambient_c=40), Constraints())
    assert r.throttle_risk == "high" and r.bottleneck == "heat"
    assert r.feasibility == Feasibility.INFEASIBLE


def test_absolute_accuracy_mode_uses_model_metric():
    hw, m = get_device("jetson-agx-orin"), get_model("yolov8s")
    cfg = Config(precision="fp16", batch=1, power_mode_id="30W", cooling_id="fan_heatsink")
    cons = Constraints(min_accuracy=0.90, accuracy_mode="absolute")
    r = evaluate(m, hw, cfg, Workload(), cons)
    assert r.metrics["accuracy"].value < 0.5  # mAP, not retention
    assert r.feasibility == Feasibility.INFEASIBLE


def test_optimise_picks_certified_candidate_and_respects_constraints():
    spec = ProjectSpec(
        name="t",
        model_id="yolov8s",
        device_id="jetson-orin-nx",
        constraints=Constraints(max_latency_ms=100),
    )
    res = optimise(spec, get_model("yolov8s"), get_device("jetson-orin-nx"))
    assert res.best is not None
    assert res.best.feasibility in FEASIBLE
    assert res.best.metrics["power_avg"].high <= 55
    assert res.best.metrics["latency"].high <= 100
    best_e = res.best.metrics["energy_per_inference"].value
    assert best_e < res.baseline.metrics["energy_per_inference"].value
    assert res.evaluated == sum(res.counts.values())


def test_near_equal_energy_prefers_lower_latency():
    spec = ProjectSpec(
        name="t",
        model_id="yolov8s",
        device_id="jetson-orin-nx",
        constraints=Constraints(max_latency_ms=100),
    )
    res = optimise(spec, get_model("yolov8s"), get_device("jetson-orin-nx"))
    best = res.best
    assert best is not None
    for r in res.top:
        if r.feasibility == best.feasibility:
            e_ratio = r.metrics["energy_per_inference"].value / best.metrics["energy_per_inference"].value
            if e_ratio < 1.0:  # a strictly cheaper config may only exist inside the tie band
                assert e_ratio > 1 / 1.03
                assert r.metrics["latency"].value >= best.metrics["latency"].value


def test_optimise_never_relaxes_constraints_when_nothing_is_feasible():
    spec = ProjectSpec(
        name="t",
        model_id="yolov8m",
        device_id="jetson-orin-nano",
        workload=Workload(fps=500),
    )
    res = optimise(spec, get_model("yolov8m"), get_device("jetson-orin-nano"))
    assert res.best is None and res.recommendation is None
    assert "not relaxed" in res.message
    assert spec.constraints.max_power_w == 55 and spec.constraints.min_accuracy == 0.90


def test_all_library_pairs_evaluate_without_error():
    for dev, model in itertools.product(load_devices().values(), load_models().values()):
        r = evaluate(model, dev, next(iter(cfgs(dev))), Workload(), Constraints())
        assert r.checks
