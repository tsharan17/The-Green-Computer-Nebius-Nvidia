"""Calibration tests.

The synthetic 'devices' here exist only to test the fitting code: they are generated from the
simulator itself with a known hidden efficiency, so they say nothing about real-world accuracy.
Real accuracy is covered by tests against the committed host measurements.
"""

import json
import random
from pathlib import Path

import pytest

from app.data_loader import get_device, get_model
from app.engine.calibration import CalibItem, fit_calibration, saturated_workload
from app.engine.evaluator import evaluate
from app.engine.solver import Corner, simulate
from app.schemas.measurement import Calibration, MeasurementIn
from app.schemas.metrics import Provenance
from app.schemas.project import Constraints, Workload
from app.schemas.results import Config

HW = get_device("jetson-orin-nx")
MODELS = ["yolov8n", "yolov8s", "resnet-50", "mobilenet-v2", "yolov8m"]


def synth_items(eta: float, noise: float, seed: int = 1, n_batches=(1, 2, 4)):
    """Measurements from the simulator at hidden efficiency `eta`, with multiplicative noise."""
    rng = random.Random(seed)
    items, mid = [], 0
    for name in MODELS:
        model = get_model(name)
        for b in n_batches:
            cfg = Config(precision="fp16", batch=b, power_mode_id="MAXN", cooling_id="fan_heatsink")
            true_ms = simulate(model, HW, cfg, saturated_workload(None), Corner(eta=eta)).rf.t_batch_s * 1e3
            ms = true_ms * (1 + rng.uniform(-noise, noise))
            mid += 1
            m = MeasurementIn(
                device_id=HW.id, model_id=name, precision="fp16", batch=b, latency_ms_mean=ms,
                power_mode_id="MAXN", cooling_id="fan_heatsink", synthetic=True,
            )  # fmt: skip
            items.append(CalibItem(mid, model, m))
    return items


def test_recovers_a_known_efficiency_and_passes_the_gate():
    rep = fit_calibration(synth_items(eta=0.6, noise=0.03), HW, HW.id, "fp16", 1)
    assert rep.latency.accepted
    assert rep.latency.error_after < 0.5 * rep.latency.error_before
    assert rep.calibration.eta_scale == pytest.approx(0.6, rel=0.12)
    assert rep.calibration.latency_calibrated and not rep.calibration.power_calibrated
    assert rep.calibration.eta_width is not None and rep.calibration.eta_width < 0.25


def test_held_out_points_are_predicted_better_after_calibration():
    items = synth_items(eta=0.55, noise=0.04)
    train, test = items[:-3], items[-3:]
    rep = fit_calibration(train, HW, HW.id, "fp16", 1)
    cal = rep.calibration
    for it in test:
        cfg = Config(precision="fp16", batch=it.m.batch, power_mode_id="MAXN", cooling_id="fan_heatsink")
        base = simulate(it.model, HW, cfg, saturated_workload(None), Corner()).rf.t_batch_s * 1e3
        fit = simulate(it.model, HW, cfg, saturated_workload(None), Corner(eta=cal.eta_scale)).rf.t_batch_s * 1e3
        assert abs(fit - it.m.latency_ms_mean) < abs(base - it.m.latency_ms_mean)


def test_inconsistent_data_is_rejected_and_factors_stay_at_one():
    rng = random.Random(7)
    items = synth_items(eta=0.6, noise=0.0)
    noisy = []
    for it in items:
        m = it.m.model_copy(update={"latency_ms_mean": it.m.latency_ms_mean * rng.choice([0.2, 5.0, 0.5, 3.0])})
        noisy.append(CalibItem(it.measurement_id, it.model, m))
    rep = fit_calibration(noisy, HW, HW.id, "fp16", 1)
    assert not rep.latency.accepted and "rejected" in rep.latency.reason
    assert rep.calibration.eta_scale == 1.0 and rep.calibration.eta_width is None
    assert not rep.calibration.latency_calibrated


def test_too_few_points_are_not_calibrated():
    rep = fit_calibration(synth_items(0.6, 0.0)[:2], HW, HW.id, "fp16", 1)
    assert not rep.latency.accepted and "at least 3" in rep.latency.reason
    assert rep.calibration.eta_scale == 1.0


def test_power_calibration_needs_power_measurements_and_recovers_the_scale():
    items = []
    for it in synth_items(eta=0.6, noise=0.0, n_batches=(1, 2, 4, 8)):
        cfg = Config(precision="fp16", batch=it.m.batch, power_mode_id="MAXN", cooling_id="fan_heatsink")
        true_w = simulate(it.model, HW, cfg, saturated_workload(None), Corner(eta=0.6, power=1.3)).pw.p_avg_w
        items.append(CalibItem(it.measurement_id, it.model, it.m.model_copy(update={"power_w_avg": true_w})))
    rep = fit_calibration(items, HW, HW.id, "fp16", 1)
    assert rep.power.accepted
    assert rep.calibration.power_scale == pytest.approx(1.3, rel=0.12)
    assert rep.calibration.power_calibrated


def test_calibrated_evaluation_is_labelled_and_re_banded():
    cfg = Config(precision="fp16", batch=1, power_mode_id="MAXN", cooling_id="fan_heatsink")
    m = get_model("yolov8s")
    plain = evaluate(m, HW, cfg, Workload(), Constraints())
    cal = Calibration(device_id=HW.id, precision="fp16", version=1, eta_scale=0.6, eta_width=0.05,
                      latency_calibrated=True, n_points=9)  # fmt: skip
    out = evaluate(m, HW, cfg, Workload(), Constraints(), calibration=cal)
    assert plain.metrics["latency"].provenance == Provenance.ANALYTICAL
    assert out.metrics["latency"].provenance == Provenance.CALIBRATED
    assert out.metrics["power_avg"].provenance == Provenance.ANALYTICAL  # power not calibrated
    w_plain = plain.metrics["batch_time"].high - plain.metrics["batch_time"].low
    w_cal = out.metrics["batch_time"].high - out.metrics["batch_time"].low
    assert w_cal < w_plain  # bands shrink only because the calibration earned a small width


def test_measured_overlay_marks_metrics_measured_and_can_verify_a_check():
    cfg = Config(precision="fp16", batch=2, power_mode_id="MAXN", cooling_id="fan_heatsink")
    m = get_model("yolov8s")
    meas = MeasurementIn(device_id=HW.id, model_id="yolov8s", precision="fp16", batch=2,
                         power_mode_id="MAXN", latency_ms_mean=20.0, latency_ms_p50=19.0,
                         latency_ms_p95=24.0, power_w_avg=18.0, power_w_peak=21.0)  # fmt: skip
    r = evaluate(m, HW, cfg, Workload(fps=30), Constraints(), measured=meas)
    assert r.metrics["batch_time"].provenance == Provenance.MEASURED
    assert r.metrics["batch_time"].value == 20.0 and r.metrics["batch_time"].high == 24.0
    assert r.metrics["capacity_fps"].value == pytest.approx(100.0)
    assert r.metrics["power_peak"].value == 18.0 and r.metrics["power_peak"].provenance == Provenance.MEASURED
    assert r.metrics["power_avg"].provenance == Provenance.ANALYTICAL  # not measured at this duty


def test_real_host_measurements_calibrate_and_beat_the_uncalibrated_model():
    """The committed bench results are real runs on the development machine's CPU."""
    from app.engine.measure_parsers import parse_bench_json

    files = sorted((Path(__file__).parents[2] / "app" / "data" / "measurements").glob("host-cpu-*.json"))
    assert files, "committed host measurements are missing"
    items, i = [], 0
    for f in files:
        for m in parse_bench_json(f.read_text(encoding="utf-8")):
            i += 1
            assert m.source == "local_bench" and not m.synthetic and m.power_w_avg is None
            items.append(CalibItem(i, get_model(m.model_id), m))
    rep = fit_calibration(items, get_device("host-cpu"), "host-cpu", "fp32", 1)
    assert rep.latency.n == len(items) >= 8
    assert rep.latency.accepted, rep.latency.reason
    assert rep.latency.error_after < 0.5 * rep.latency.error_before  # leave-one-out, out of sample
    assert not rep.power.accepted and "at least 3" in rep.power.reason  # no power meter on the host
    json.dumps(rep.model_dump())  # serialisable
