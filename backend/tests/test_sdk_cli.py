import json

import pytest
import yaml

from app.cli import main
from app.engine.layers import layer_profile, quant_sensitivity_script
from app.engine.optimise import baseline_config
from app.sdk import ProjectFileError, analyse, gate, load_project, markdown_summary, snapshot

OK = {
    "name": "t", "model": "mobilenet-v2", "device": "nvidia-t4",
    "workload": {"fps": 30, "ambient_c": 25},
    "requirements": {"max_power_w": 200, "min_accuracy": 0.9},
}
BAD = {**OK, "device": "raspberry-pi-5", "workload": {"fps": 400, "ambient_c": 25},
       "requirements": {"max_power_w": 3.0, "min_accuracy": 0.9}}


def test_gate_pass_and_fail():
    p = load_project(OK)
    g = gate(p, analyse(p))
    assert g.status in ("pass", "uncertain") and g.passed
    bad = load_project(BAD)
    gb = gate(bad, analyse(bad))
    assert gb.status == "fail" and not gb.passed and gb.messages


def test_regression_gate():
    p = load_project(OK)
    res = analyse(p)
    prev = snapshot(res)
    assert gate(p, res, previous=prev, tolerance_percent=5).status != "fail"
    worse = {**prev, "energy_mj": prev["energy_mj"] * 0.5}  # we are now 2x the old energy
    g = gate(p, res, previous=worse, tolerance_percent=5)
    assert g.status == "fail" and any("regressed" in m for m in g.messages)


def test_custom_block_and_errors():
    spec = {**OK, "device": None, "custom": {"name": "My MCU", "style": "mcu", "core_mhz": 200,
            "int8_macs_per_cycle": 2, "flash_kb": 2048, "sram_kb": 512, "run_power_mw": 150},
            "model": "tiny-kws-dscnn", "workload": {"fps": 2}, "requirements": {"max_power_w": 1}}
    spec.pop("device")
    p = load_project(spec)
    assert p.hw.kind == "mcu"
    assert gate(p, analyse(p)).status in ("pass", "uncertain", "fail")
    for bad in ({**OK, "device": "nope"}, {**OK, "model": "nope"}, {**OK, "model": None}):
        with pytest.raises(ProjectFileError):
            load_project(bad)


def test_markdown_has_table_and_caveat():
    p = load_project(OK)
    res = analyse(p)
    md = markdown_summary(p, res, gate(p, res))
    assert "| Latency |" in md and "not measurements" in md


def test_cli_roundtrip(tmp_path, capsys):
    f = tmp_path / "gc.yaml"
    f.write_text(yaml.safe_dump(OK), encoding="utf-8")
    snap, md = tmp_path / "s.json", tmp_path / "pr.md"
    code = main(["check", str(f), "--save", str(snap), "--markdown", str(md), "--json"])
    assert code in (0, 2)
    assert json.loads(capsys.readouterr().out)["status"] in ("pass", "uncertain")
    assert md.read_text(encoding="utf-8").startswith("### Green Computer check")
    assert main(["check", str(f), "--compare", str(snap), "--tolerance", "1"]) in (0, 2)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(BAD), encoding="utf-8")
    assert main(["check", str(bad)]) == 1
    assert main(["layers", str(f)]) == 0
    assert main(["devices"]) == 0
    assert main(["init", str(tmp_path / "x.yaml")]) == 0
    assert main(["check", str(tmp_path / "missing.yaml")]) == 1


def test_layer_profile_sums_to_batch_time_and_flags():
    from app.data_loader import get_device, get_model
    from app.engine.workload.onnx_parser import parse_onnx  # noqa: F401  (graph models need onnx)

    hw = get_device("jetson-orin-nano")
    lib = get_model("yolov8n")
    cfg = baseline_config(hw, None)
    assert layer_profile(lib, hw, cfg)["has_graph"] is False  # library entry has no graph


def test_layer_profile_with_graph():
    import io

    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    from app.data_loader import get_device
    from app.engine.workload.onnx_parser import parse_onnx

    w1 = numpy_helper.from_array(np.random.rand(8, 3, 3, 3).astype(np.float32), "w1")
    w2 = numpy_helper.from_array(np.random.rand(10, 8 * 4 * 4).astype(np.float32), "w2")
    nodes = [
        helper.make_node("Conv", ["x", "w1"], ["c"], kernel_shape=[3, 3], pads=[1, 1, 1, 1]),
        helper.make_node("Relu", ["c"], ["r"]),
        helper.make_node("AveragePool", ["r"], ["p"], kernel_shape=[4, 4], strides=[4, 4]),
        helper.make_node("Flatten", ["p"], ["f"]),
        helper.make_node("Gemm", ["f", "w2"], ["y"], transB=1),
    ]
    g = helper.make_graph(nodes, "g", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 16, 16])],
                          [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 10])], [w1, w2])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    buf = io.BytesIO(); onnx.save(m, buf)  # noqa: E702
    prof = parse_onnx(buf.getvalue(), "toy", dynamic_dim_default=1).profile
    hw = get_device("jetson-orin-nano")
    cfg = baseline_config(hw, None)
    out = layer_profile(prof, hw, cfg)
    assert out["has_graph"] and out["layers"]
    total = sum(r["time_ms"] for r in out["layers"]) + out["overhead_ms"]
    assert total == pytest.approx(out["total_ms"], rel=1e-6)
    assert any(r["quant_risk"] >= 0.6 for r in out["layers"])  # first / last layer flagged


def test_sensitivity_script_compiles():
    compile(quant_sensitivity_script(), "quant_sensitivity.py", "exec")
