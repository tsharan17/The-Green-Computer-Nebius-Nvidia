import json

import pytest

from app.engine.measure_parsers import (
    MeasurementParseError,
    parse_bench_json,
    parse_nvidia_smi_csv,
    parse_tegrastats,
)

SMI = """power.draw [W], temperature.gpu, utilization.gpu [%]
62.10 W, 41, 0 %
210.50 W, 55, 98 %
220.50 W, 61, 99 %
63.00 W, 60, 0 %
"""

TEGRA = (
    "10-14-2026 12:00:01 RAM 2448/7765MB CPU [40%@1420,30%@1420] GPU@42.5C CPU@45C "
    "VDD_IN 7210mW/7210mW VDD_CPU_CV 1000mW/1000mW\n"
    "10-14-2026 12:00:02 RAM 2450/7765MB CPU [90%@1420,80%@1420] GPU@48C CPU@47.5C "
    "VDD_IN 9400mW/8300mW VDD_CPU_CV 2000mW/1500mW\n"
)


def test_nvidia_smi_averages_busy_samples_only():
    t = parse_nvidia_smi_csv(SMI)
    assert t.samples == 2
    assert t.power_w_avg == pytest.approx((210.5 + 220.5) / 2)
    assert t.power_w_peak == 220.5
    assert t.temperature_c_max == 61  # max over every sample


def test_nvidia_smi_without_util_uses_all_rows():
    t = parse_nvidia_smi_csv("100.0, 50\n120.0, 60\n")
    assert t.power_w_avg == pytest.approx(110.0) and t.samples == 2


def test_tegrastats_reads_vdd_in_in_watts():
    t = parse_tegrastats(TEGRA)
    assert t.samples == 2
    assert t.power_w_avg == pytest.approx((7.21 + 9.4) / 2)
    assert t.power_w_peak == pytest.approx(9.4)
    assert t.temperature_c_max == 48.0


def test_unparseable_traces_raise_instead_of_guessing():
    with pytest.raises(MeasurementParseError):
        parse_nvidia_smi_csv("nothing useful here")
    with pytest.raises(MeasurementParseError, match="VDD_IN"):
        parse_tegrastats("RAM 1/2MB nothing")


def test_bench_json_validation():
    rec = {"device_id": "d", "model_id": "m", "precision": "fp32", "latency_ms_mean": 5.0}
    assert len(parse_bench_json(json.dumps({"records": [rec]}))) == 1
    assert len(parse_bench_json(json.dumps([rec]))) == 1
    with pytest.raises(MeasurementParseError):
        parse_bench_json("not json")
    with pytest.raises(MeasurementParseError, match="record 0"):
        parse_bench_json(json.dumps([{"device_id": "d", "model_id": "m", "precision": "fp32"}]))
    with pytest.raises(MeasurementParseError):
        parse_bench_json(json.dumps({"records": []}))
