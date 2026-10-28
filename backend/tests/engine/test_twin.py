"""Digital twin v1: the trace agrees with the engine and derives memory from the graph."""

from __future__ import annotations

import pytest

from app.data_loader import get_device, get_model
from app.engine.evaluator import evaluate
from app.engine.optimise import baseline_config
from app.engine.workload.onnx_parser import parse_onnx
from app.main import create_app
from app.schemas.project import Constraints, Workload
from app.services.twin_service import graph_model, trace_csv
from app.twin.trace import build_trace
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"


def _trace(model, device_id: str, fps: float = 30.0, ambient: float = 30.0, cooling: str | None = None, **cfg):
    hw = get_device(device_id)
    c = baseline_config(hw, cooling).model_copy(update=cfg)
    wl = Workload(fps=fps, ambient_c=ambient)
    r = evaluate(model, hw, c, wl, Constraints())
    g, src = graph_model(model)
    return build_trace(g, hw, c, wl, r, None, src), r


def test_tensor_lifetimes_follow_the_graph():
    m = parse_onnx(tiny_cnn_bytes(), "tiny").profile.model_copy(update={"id": "tiny"})
    t, _ = _trace(m, "jetson-orin-nano", precision="fp32")
    assert t.fidelity == "graph" and [e.name for e in t.events[:3]] == ["conv", "gap", "fc"]
    life = {x.id: (x.producer, x.last_use, x.kind) for x in t.tensors}
    assert life["x"] == (-1, 0, "input")  # read by the conv only
    assert life["c"] == (0, 1, "activation")  # conv output, freed after the pool
    assert life["g"] == (1, 2, "activation")  # flatten is a view of g
    assert life["y"] == (2, 3, "output")  # kept to the end
    b = 4.0  # fp32 bytes per element
    conv, gap, fc = t.events[:3]
    assert conv.working_set_bytes == pytest.approx((192 + 256) * b)  # input + conv output
    assert conv.free == ["x"] and gap.free == ["c"] and fc.free == ["g"]
    assert gap.working_set_bytes == pytest.approx((256 + 4) * b)
    assert fc.live_after_bytes == pytest.approx(10 * b)


def test_trace_adds_up_to_the_headline_numbers():
    for model_id, dev in (("resnet-50", "jetson-orin-nano"), ("yolov8s", "jetson-orin-nx"), ("mobilenet-v2", "host-cpu")):
        t, r = _trace(get_model(model_id), dev)
        ops = [e for e in t.events if e.kind in ("operator", "aggregate")]
        assert sum(e.duration_ms for e in ops) == pytest.approx(r.metrics["batch_time"].value, rel=1e-3)
        per_inf = sum(e.energy_mj.total for e in t.events) / t.batch
        assert per_inf == pytest.approx(r.metrics["energy_per_inference"].value, rel=1e-3)
        assert t.kind == "predicted" and t.provenance == "analytical"
        assert all(e.start_ms <= n.start_ms for e, n in zip(t.events, t.events[1:], strict=False))
        assert t.stages and t.stages[0].first_event == 0
    t, _ = _trace(get_model("resnet-50"), "jetson-orin-nano")
    assert t.fidelity == "graph" and len(t.events) > 50  # reference architecture used
    t, _ = _trace(get_model("yolov8s"), "jetson-orin-nx")
    assert t.fidelity == "aggregate" and "Upload its ONNX" in t.fidelity_note


def test_mcu_weights_live_in_flash_and_activations_in_sram():
    t, _ = _trace(get_model("tiny-kws-dscnn"), "stm32h743", fps=4, precision="int8")
    ids = {c.id for c in t.components}
    assert {"flash", "ram", "compute", "cooling", "power"} <= ids
    assert all(p.weights_bytes == 0 for p in t.memory_timeline)  # weights are not in SRAM
    assert any(p.source == "flash" for p in t.paths)


def test_thermal_and_throughput_bottlenecks_are_named():
    t, r = _trace(get_model("yolov8s"), "jetson-orin-nx", fps=30, ambient=40, cooling="passive_small")
    kinds = {b.kind for b in t.bottlenecks}
    assert r.solver.throttled and "thermal" in kinds and t.thermal.throttled
    assert t.thermal.r_th_needed_k_per_w is not None and t.thermal.r_th_needed_k_per_w < t.thermal.r_th_k_per_w
    slow, _ = _trace(get_model("yolov8m"), "jetson-orin-nano", fps=200)
    assert "throughput" in {b.kind for b in slow.bottlenecks}
    assert all(e.kind != "idle" for e in slow.events)


def test_csv_exports_carry_provenance():
    t, _ = _trace(get_model("resnet-50"), "jetson-orin-nano")
    d = t.model_dump(mode="json")
    ev = trace_csv(d, "events").splitlines()
    assert ev[0].startswith("index,name,op") and ev[0].endswith("provenance") and ev[1].endswith("predicted:analytical")
    assert len(trace_csv(d, "memory").splitlines()) == len(d["memory_timeline"]) + 1
    assert "producer" in trace_csv(d, "tensors").splitlines()[0]


def test_trace_api_stores_lists_exports_and_isolates(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 't.db'}", data_dir=str(tmp_path / "d"))
    with AuthedClient(app, email="a@x.io") as a:
        pid = a.get(f"{P}/projects").json()[0]["id"]
        r = a.post(f"{P}/projects/{pid}/traces?which=best")
        assert r.status_code == 422 and "analysis first" in r.json()["detail"]
        base = a.post(f"{P}/projects/{pid}/traces").json()
        assert base["label"] == "current setup" and base["trace"]["kind"] == "predicted"
        a.post(f"{P}/projects/{pid}/optimise")
        best = a.post(f"{P}/projects/{pid}/traces?which=best").json()
        assert best["label"] == "recommended setup"
        listed = a.get(f"{P}/projects/{pid}/traces").json()
        assert [x["id"] for x in listed] == [best["id"], base["id"]] and listed[0]["summary"]["events"] >= 1
        csv = a.get(f"{P}/projects/{pid}/traces/{base['id']}/export?format=csv&table=events")
        assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
        assert a.get(f"{P}/projects/{pid}/traces/{base['id']}").json()["trace"]["schema_id"] == "green-computer.trace/1"
        assert a.get(f"{P}/usage").json()["this_period"]["trace"] == 2
    with AuthedClient(app, email="b@x.io") as b:
        assert b.get(f"{P}/projects/{pid}/traces/{base['id']}").status_code == 404
        assert b.get(f"{P}/projects/{pid}/traces").status_code == 404
        assert b.post(f"{P}/projects/{pid}/traces").status_code == 404
