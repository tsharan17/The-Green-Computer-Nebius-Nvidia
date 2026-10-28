import pytest

from app.engine.workload.onnx_parser import ModelParseError, manual_profile, parse_onnx
from tests.helpers import tiny_cnn_bytes


def test_counts_match_hand_calculation():
    p = parse_onnx(tiny_cnn_bytes(), "tiny").profile
    by_name = {layer.name: layer for layer in p.layers}
    assert set(by_name) == {"conv", "gap", "fc"}  # relu fused, flatten is a view

    conv, gap, fc = by_name["conv"], by_name["gap"], by_name["fc"]
    assert conv.flops == 2 * 6912 + 256  # MACs x2, plus the fused ReLU
    assert conv.params == 108
    assert conv.act_elems == 192 + 256
    assert gap.flops == 256
    assert gap.act_elems == 256 + 4
    assert fc.flops == 2 * 40
    assert fc.params == 40 + 10
    assert fc.act_elems == 4 + 10

    assert p.params_m * 1e6 == pytest.approx(108 + 40 + 10)
    assert p.gflops * 1e9 == pytest.approx(2 * 6912 + 256 + 256 + 80)
    assert p.n_layers == 3
    assert p.fidelity == "graph"
    assert p.resolution_scalable
    assert p.input_desc == "1x3x8x8"


def test_dynamic_dims_need_a_default():
    with pytest.raises(ModelParseError, match="dynamic"):
        parse_onnx(tiny_cnn_bytes(dynamic=True), "dyn")
    p = parse_onnx(tiny_cnn_bytes(dynamic=True), "dyn", dynamic_dim_default=8).profile
    assert p.gflops * 1e9 == pytest.approx(2 * 6912 + 256 + 256 + 80)  # same as the static graph


def test_pickle_and_garbage_are_refused():
    with pytest.raises(ModelParseError, match="pickle"):
        parse_onnx(b"\x80\x04\x95rest-of-a-pickle", "x")
    with pytest.raises(ModelParseError, match="pickle"):
        parse_onnx(b"PK\x03\x04torch-zip", "x")
    with pytest.raises(ModelParseError):
        parse_onnx(b"this is not a model", "x")


def test_manual_profile_estimates_activations_and_is_flagged():
    m = manual_profile("m", params_m=5, gflops=2.0, n_layers=40, act_mb_fp32=None)
    assert m.fidelity == "manual" and m.act_mb_fp32 == pytest.approx(24.0)
    assert "estimated" in m.notes
    given = manual_profile("m", 5, 2.0, 40, 10.0)
    assert given.act_mb_fp32 == 10.0
