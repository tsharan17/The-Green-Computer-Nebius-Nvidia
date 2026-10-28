"""Multi-node thermal attribution, memory hierarchy and runtime effects (pure)."""

from __future__ import annotations

import pytest

from app.data_loader import get_device, get_model
from app.engine.evaluator import evaluate
from app.engine.optimise import baseline_config
from app.engine.runtime_effects import memory_hierarchy, runtime_effects
from app.engine.thermal_nodes import network, solve, thermal_view
from app.services.seed import example_spec
from app.services.twin_service import graph_model
from app.twin.trace import build_trace


def _trace(device="jetson-orin-nx", model="yolov8s", fps=None, streaming=True):
    spec = example_spec()
    hw, m = get_device(device), get_model(model)
    cfg = baseline_config(hw, hw.default_cooling)
    wl = spec.workload.model_copy(update={"fps": fps or spec.workload.fps, "streaming": streaming})
    res = evaluate(m, hw, cfg, wl, spec.constraints)
    g, src = graph_model(m)
    return hw, cfg, build_trace(g, hw, cfg, wl, res, None, src)


def test_all_heat_at_the_die_reproduces_the_lumped_model():
    hw = get_device("jetson-orin-nano")
    r_th = 3.0
    temps = solve(network(hw, r_th), {"compute": 10.0}, 25.0)
    assert temps["compute"] == pytest.approx(25.0 + 10.0 * r_th)
    assert temps["compute"] > temps["package"] > temps["sink"] > 25.0


def test_thermal_view_is_labelled_and_consistent():
    hw, _cfg, t = _trace()
    th = t.thermal
    v = thermal_view(hw, [c.model_dump() for c in t.components], th.ambient_c, th.steady_c, th.r_th_k_per_w)
    assert v["status"] == "lumped only" and v["measured"] is None
    assert {n["label"] for n in v["nodes"]} == {"attribution"}
    total = sum(n["heat_w"] for n in v["nodes"])
    assert total == pytest.approx(v["lumped_power_w"], rel=1e-4)  # heat scaled to the lumped power
    die = next(n for n in v["nodes"] if n["id"] == "compute")
    assert die["temperature_c"] <= v["lumped_c"] + 1e-6  # splitting never makes the die hotter than lumped
    assert {r["source"] for r in v["resistors"]} == {"assumed", "derived"}
    assert set(v["labels"]) == {"lumped", "attribution", "measured", "validated"}

    ok = thermal_view(hw, [c.model_dump() for c in t.components], th.ambient_c, th.steady_c, th.r_th_k_per_w,
                      measured_c=th.steady_c - 2.0, measured_label="tegrastats")  # fmt: skip
    assert ok["status"] == "validated" and ok["measured"]["label"] == "measured"
    bad = thermal_view(hw, [c.model_dump() for c in t.components], th.ambient_c, th.steady_c, th.r_th_k_per_w,
                       measured_c=th.steady_c - 12.0)  # fmt: skip
    assert bad["status"] == "not validated" and bad["measured"]["difference_c"] == pytest.approx(12.0)


def test_profile_resistances_replace_assumptions():
    hw = get_device("jetson-orin-nano").model_copy(update={"extras": {"r_jc_k_per_w": 0.4}})
    jc = network(hw, 3.0)[0]
    assert jc.k_per_w == 0.4 and jc.source == "profile"


def test_memory_hierarchy_levels():
    hw, cfg, t = _trace()
    levels = memory_hierarchy(hw, t, hw.mode(cfg.power_mode_id).mem_scale)
    ram = next(x for x in levels if x["id"] == "ram")
    assert ram["fits"] and "weights" in ram["holds"] and ram["time_ms"] > 0
    hw_m, cfg_m, t_m = _trace("stm32h743", "tiny-kws-dscnn", fps=10)
    ids = {x["id"] for x in memory_hierarchy(hw_m, t_m)}
    assert "flash" in ids
    hw_g, _c, t_g = _trace("nvidia-l4", "resnet-50")
    assert "host" in {x["id"] for x in memory_hierarchy(hw_g, t_g)}


def test_runtime_effects_queueing_and_offline():
    hw, cfg, t = _trace(fps=5)  # well within capacity
    r = runtime_effects(hw, t, 5.0, True, cfg.batch)
    eff = {e["id"]: e for e in r["effects"]}
    assert eff["launch"]["in_headline"] and eff["launch"]["ms"] > 0
    assert not eff["loading"]["in_headline"] and eff["loading"]["provenance"] == "assumed"
    assert r["queue"]["stable"] and 0 < r["queue"]["utilisation"] < 1
    assert r["queue"]["end_to_end_ms"] >= r["headline_latency_ms"]
    over = runtime_effects(hw, t, 500.0, True, cfg.batch)
    assert over["queue"]["stable"] is False and over["queue"]["end_to_end_ms"] is None
    off = runtime_effects(hw, t, 5.0, False, cfg.batch)
    assert off["queue"]["mode"] == "offline" and "queueing" not in {e["id"] for e in off["effects"]}
    hw_g, cfg_g, t_g = _trace("nvidia-l4", "resnet-50")
    tr = next(e for e in runtime_effects(hw_g, t_g, 30.0, True, cfg_g.batch)["effects"] if e["id"] == "transfer")
    assert tr["ms"] > 0 and tr["in_headline"] is False
