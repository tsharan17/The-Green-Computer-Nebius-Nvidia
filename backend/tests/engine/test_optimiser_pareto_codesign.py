import numpy as np
import pytest

from app.data_loader import get_device, get_model
from app.engine.optimise import _rank_key, optimise, pareto_mask, search_space
from app.schemas.metrics import Feasibility
from app.schemas.project import Constraints, ProjectSpec

DEV = "jetson-orin-nx"


def run(**cons):
    spec = ProjectSpec(name="t", model_id="yolov8s", device_id=DEV, cooling_id="passive_small",
                       constraints=Constraints(max_latency_ms=100, **cons))  # fmt: skip
    return optimise(spec, get_model("yolov8s"), get_device(DEV))


def test_pareto_mask_on_known_points():
    pts = np.array([[1, 5], [2, 2], [5, 1], [3, 3], [2, 2]], dtype=float)
    keep = pareto_mask(pts)
    # (3,3) is dominated by (2,2); the duplicate (2,2) points do not dominate each other.
    assert keep.tolist() == [True, True, True, False, True]


def test_sweep_is_thousands_and_fast():
    res = run()
    assert res.evaluated >= 1000
    assert res.evaluated == len(res.points) == sum(res.counts.values())
    assert res.search_seconds < 10


def test_pareto_points_are_non_dominated_and_eligible():
    res = run()
    front = [p for p in res.points if p.pareto]
    assert front
    assert all(p.state in (Feasibility.PREDICTED_FEASIBLE, Feasibility.UNCERTAIN,
                           Feasibility.VERIFIED_FEASIBLE) for p in front)  # fmt: skip
    eligible = [p for p in res.points if p.state != Feasibility.INFEASIBLE]
    for p in front:
        for q in eligible:
            dominates = (
                q.energy_mj <= p.energy_mj and q.latency_ms <= p.latency_ms
                and q.accuracy >= p.accuracy
                and (q.energy_mj < p.energy_mj or q.latency_ms < p.latency_ms
                     or q.accuracy > p.accuracy)
            )  # fmt: skip
            assert not dominates
    assert sum(p.best for p in res.points) == 1


def test_best_respects_every_hard_constraint_and_is_not_infeasible():
    res = run()
    b = res.best
    assert b is not None
    assert b.feasibility in (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)
    assert b.metrics["power_avg"].high <= 55
    assert b.metrics["latency"].high <= 100
    assert not b.solver.throttled


def test_codesign_combined_is_at_least_as_good_as_either_alone():
    cd = run().codesign
    assert cd.combined and cd.software_only and cd.hardware_only
    base = cd.baseline.config
    assert _rank_key(cd.combined, base) <= _rank_key(cd.software_only, base)
    assert _rank_key(cd.combined, base) <= _rank_key(cd.hardware_only, base)


def test_interaction_factor_matches_its_definition():
    cd = run().codesign
    e = lambda r: r.metrics["energy_per_inference"].value  # noqa: E731
    expected = e(cd.combined) * e(cd.baseline) / (e(cd.software_only) * e(cd.hardware_only))
    assert cd.interaction_factor == pytest.approx(expected)
    assert "Interaction factor" in cd.interpretation


def test_software_only_keeps_hardware_fixed_and_vice_versa():
    res = run()
    base = res.baseline.config
    sw, hw = res.codesign.software_only.config, res.codesign.hardware_only.config
    assert (sw.power_mode_id, sw.cooling_id, sw.clock_trim) == (
        base.power_mode_id, base.cooling_id, base.clock_trim)  # fmt: skip
    assert (hw.precision, hw.batch, hw.resolution_scale) == (
        base.precision, base.batch, base.resolution_scale)  # fmt: skip


def test_thermal_awareness_changes_the_answer_in_hot_ambient():
    """A search that ignored throttling would pick a config the governor then cripples."""
    from app.engine.evaluator import evaluate
    from app.schemas.project import Workload

    spec = ProjectSpec(name="t", model_id="yolov8s", device_id=DEV, cooling_id="passive_small",
                       workload=Workload(ambient_c=45),
                       constraints=Constraints(max_latency_ms=100, forbid_throttling=False))  # fmt: skip
    model, hw = get_model("yolov8s"), get_device(DEV)
    res = optimise(spec, model, hw)
    assert res.best is not None and not res.best.solver.throttled
    # Among configs whose *nominal* clocks would be fastest, many throttle at 45 C.
    throttled = [
        c for c in search_space(model, hw)
        if evaluate(model, hw, c, spec.workload, spec.constraints).solver.throttled
    ][:50]  # fmt: skip
    assert throttled, "expected some configs to throttle at 45 C on a small passive heatsink"


def test_search_space_respects_non_scalable_resolution():
    m = get_model("yolov8s").model_copy(update={"resolution_scalable": False})
    assert {c.resolution_scale for c in search_space(m, get_device(DEV))} == {1.0}


def test_no_feasible_result_still_has_codesign_and_points():
    from app.schemas.project import Workload

    spec = ProjectSpec(name="t", model_id="yolov8m", device_id="jetson-orin-nano",
                       workload=Workload(fps=600))  # fmt: skip
    res = optimise(spec, get_model("yolov8m"), get_device("jetson-orin-nano"))
    assert res.best is None and "not relaxed" in res.message
    assert res.codesign.combined is None and res.codesign.interaction_factor is None
    assert res.points
