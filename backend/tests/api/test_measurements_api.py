import pytest

from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture()
def client(tmp_path):
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 't.db'}")) as c:
        yield c


def meas(**kw):
    base = {"device_id": "jetson-agx-orin", "model_id": "yolov8s", "precision": "fp16", "batch": 1,
            "latency_ms_mean": 9.0}  # fmt: skip
    return {**base, **kw}


def test_committed_real_measurements_are_loaded_once_and_verified(client):
    rows = client.get(f"{P}/measurements?device_id=host-cpu").json()
    assert len(rows) >= 8 and all(r["source"] == "local_bench" for r in rows)
    again = client.post(f"{P}/measurements/replay").json()
    assert again["created"] == 0 and again["skipped_duplicates"] >= 8  # idempotent

    v = client.get(f"{P}/verify?device_id=host-cpu").json()
    s = v["summaries"][0]
    assert s["device_id"] == "host-cpu" and s["calibration_active"] and s["n"] == len(rows)
    assert s["mape_after"] < s["mape_before"]
    assert all(r["predicted_cal_ms"] is not None for r in v["rows"])


def test_manual_measurement_validation_and_unknown_refs(client):
    assert client.post(f"{P}/measurements", json=meas()).status_code == 201
    assert client.post(f"{P}/measurements", json={"device_id": "jetson-agx-orin", "model_id": "yolov8s",
                                                  "precision": "fp16"}).status_code == 422  # no metric
    assert client.post(f"{P}/measurements", json=meas(device_id="nope")).status_code == 422
    assert client.post(f"{P}/measurements", json=meas(model_id="nope")).status_code == 422
    assert client.post(f"{P}/measurements", json=meas(latency_ms_mean=-1)).status_code == 422
    assert client.post(f"{P}/measurements", json=meas(precision="fp8")).status_code == 422
    # The host CPU profile has no fp16 support, so an fp16 measurement there is refused.
    assert client.post(f"{P}/measurements", json=meas(device_id="host-cpu")).status_code == 422


def test_external_id_makes_ingest_idempotent(client):
    body = meas(external_id="abc123")
    a = client.post(f"{P}/measurements", json=body).json()
    b = client.post(f"{P}/measurements", json=body).json()
    assert a["created"] == 1 and b["created"] == 0 and b["skipped_duplicates"] == 1
    assert a["measurements"][0]["id"] == b["measurements"][0]["id"]


def test_calibration_appears_after_enough_consistent_measurements_and_changes_predictions(client):
    pid = client.get(f"{P}/projects").json()[0]["id"]
    client.put(f"{P}/projects/{pid}", json={"name": "j", "model_id": "yolov8s",
                                            "device_id": "jetson-agx-orin", "cooling_id": "fan_heatsink"})  # fmt: skip
    cfg = {"precision": "fp16", "batch": 1, "power_mode_id": "MAXN", "cooling_id": "fan_heatsink"}
    before = client.post(f"{P}/projects/{pid}/evaluate", json=cfg).json()["metrics"]["batch_time"]
    assert before["provenance"] == "analytical"

    # Four models measured at a consistent fraction of the model's predicted speed.
    recs = []
    for model, ms in (("yolov8n", 3.1), ("yolov8s", 9.4), ("resnet-50", 3.9), ("yolov8m", 21.5)):
        recs.append(meas(model_id=model, latency_ms_mean=ms, power_mode_id="MAXN"))
    out = client.post(f"{P}/measurements", json=recs).json()
    assert out["created"] == 4 and out["calibrations"][0]["version"] >= 1
    state = client.get(f"{P}/calibration?device_id=jetson-agx-orin").json()
    assert len(state["history"]) >= 1  # every refit is audited, accepted or not
    accepted = out["calibrations"][0]["latency"]["accepted"]

    after = client.post(f"{P}/projects/{pid}/evaluate", json=cfg).json()["metrics"]["batch_time"]
    if accepted:
        assert "jetson-agx-orin/fp16" in state["active"]
        assert after["value"] != before["value"]
    # The yolov8s fp16 batch-1 MAXN point was measured exactly, so it overrides the prediction.
    assert after["provenance"] == "measured" and after["value"] == 9.4


def test_deleting_a_measurement_refits(client):
    out = client.post(f"{P}/measurements", json=meas()).json()
    mid = out["measurements"][0]["id"]
    assert client.delete(f"{P}/measurements/{mid}").status_code == 200
    assert client.delete(f"{P}/measurements/{mid}").status_code == 404


def test_measured_accuracy_lets_an_int8_candidate_become_feasible(client):
    pid = client.get(f"{P}/projects").json()[0]["id"]
    client.put(f"{P}/projects/{pid}", json={"name": "a", "model_id": "yolov8s",
                                            "device_id": "jetson-agx-orin", "cooling_id": "fan_heatsink"})  # fmt: skip
    cfg = {"precision": "int8", "batch": 1, "power_mode_id": "30W", "cooling_id": "fan_heatsink"}
    unmeasured = client.post(f"{P}/projects/{pid}/evaluate", json=cfg).json()
    assert unmeasured["feasibility"] == "uncertain"
    client.post(f"{P}/measurements", json={"device_id": "jetson-agx-orin", "model_id": "yolov8s",
                                           "precision": "int8", "accuracy_retention": 0.97,
                                           "source": "manual"})  # fmt: skip
    measured = client.post(f"{P}/projects/{pid}/evaluate", json=cfg).json()
    assert measured["feasibility"] == "predicted_feasible"
    acc = measured["metrics"]["accuracy"]
    assert acc["provenance"] == "measured" and acc["value"] == pytest.approx(0.97)
    # A measured retention below the limit makes it a fail, not a silent pass.
    client.post(f"{P}/measurements", json={"device_id": "jetson-agx-orin", "model_id": "yolov8s",
                                           "precision": "int8", "accuracy_retention": 0.80})  # fmt: skip
    bad = client.post(f"{P}/projects/{pid}/evaluate", json=cfg).json()
    assert bad["feasibility"] in ("infeasible", "verified_infeasible")


def test_trace_imports_and_bench_json_import(client):
    smi = "power.draw [W], temperature.gpu, utilization.gpu [%]\n200 W, 55, 99 %\n220 W, 60, 98 %\n"
    r = client.post(f"{P}/measurements/import?format=nvidia_smi_csv&device_id=nebius-l40s"
                    "&model_id=resnet-50&precision=fp16&batch=8&latency_ms_mean=4.2", content=smi)  # fmt: skip
    assert r.status_code == 201, r.text
    m = r.json()["measurements"][0]
    assert m["power_w_avg"] == pytest.approx(210) and m["source"] == "nvidia_smi"
    assert client.post(f"{P}/measurements/import?format=tegrastats", content="x").status_code == 422
    assert client.post(f"{P}/measurements/import?format=bogus", content="x").status_code == 422
    assert client.post(f"{P}/measurements/import?format=bench_json", content="not json").status_code == 422


def test_jobs_are_reported_unavailable_not_faked(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)  # pretend the CLI is not installed
    s = client.get(f"{P}/benchmarks/status").json()
    assert s["jobs_configured"] is False and s["cli_found"] is False and s["replay_available"]
    r = client.post(f"{P}/benchmarks", json={"device_id": "nebius-l40s"})
    assert r.status_code == 503 and "bench/" in r.json()["detail"] and "nebius" in r.json()["detail"]
    assert client.get(f"{P}/health").json()["jobs"]["configured"] is False
    assert client.get(f"{P}/benchmarks").json() == []
