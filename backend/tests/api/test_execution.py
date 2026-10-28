"""Local execution: real runs, measured traces, comparison, CSV import, gating, isolation."""

from __future__ import annotations

import time

import pytest

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'e.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()


def _wait(c, pid, job_id, limit=180):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(f"{P}/projects/{pid}/executions/{job_id}").json()
        if j["status"] in ("succeeded", "failed"):
            return j
        time.sleep(0.3)
    raise AssertionError("run did not finish")


def _project(c, model_id, device_id="jetson-orin-nano"):
    return c.post(f"{P}/projects", json={"name": "run", "model_id": model_id, "device_id": device_id}).json()["id"]


def test_backends_report_local_cpu_and_honest_optional_ones(app):
    with AuthedClient(app) as c:
        b = {x["id"]: x for x in c.get(f"{P}/execution/backends").json()}
        assert b["local-cpu"]["status"] == "available" and "latency" in " ".join(b["local-cpu"]["measures"])
        assert b["local-cuda"]["status"] in ("unavailable", "disabled")
        assert b["remote-device"]["status"] == "available"


def test_uploaded_model_runs_and_produces_measured_trace_and_comparison(app):
    with AuthedClient(app) as c:
        up = c.post(f"{P}/models/upload?filename=net.onnx", content=tiny_cnn_bytes(random_weights=True),
                    headers=OCTET).json()  # fmt: skip
        pid = _project(c, up["profile"]["id"])
        r = c.post(f"{P}/projects/{pid}/executions", json={"runs": 10, "warmup": 2, "batch": 2})
        assert r.status_code == 202, r.text
        j = _wait(c, pid, r.json()["id"])
        assert j["status"] == "succeeded", j["error"]
        res = j["result"]
        assert res["latency_ms"]["mean"] > 0 and res["runs"] >= 3 and res["operators"] >= 1
        assert "this server" in res["hardware_label"]
        cmp = res["comparison"]
        assert cmp["measured_batch_ms"] == res["latency_ms"]["mean"] and cmp["by_op"]
        meas = [m for m in c.get(f"{P}/measurements?device_id=host-cpu").json() if m["id"] == j["measurement_id"]]
        assert meas and meas[0]["source"] == "local_bench" and not meas[0]["library"] and meas[0]["batch"] == 2
        assert meas[0]["power_w_avg"] is None  # never filled in
        t = c.get(f"{P}/projects/{pid}/traces/{j['trace_id']}").json()["trace"]
        assert t["kind"] == "measured" and t["provenance"] == "measured" and t["thermal"] is None
        assert t["events"] and any("not measured" in a for a in t["assumptions"])
        assert c.get(f"{P}/usage").json()["this_period"]["execution_seconds"] > 0
        assert "execution.finished" in {e["action"] for e in c.get(f"{P}/audit").json()}
    with AuthedClient(app, email="intruder@x.io") as o:
        assert o.get(f"{P}/projects/{pid}/executions/{j['id']}").status_code == 404
        assert o.post(f"{P}/projects/{pid}/executions", json={}).status_code == 404


def test_library_models_run_only_when_a_reference_graph_exists(app):
    with AuthedClient(app) as c:
        pid = _project(c, "yolov8s", "jetson-orin-nx")
        r = c.post(f"{P}/projects/{pid}/executions", json={})
        assert r.status_code == 409 and "Upload its ONNX" in r.json()["detail"]
        pid = _project(c, "mobilenet-v2")
        r = c.post(f"{P}/projects/{pid}/executions", json={"runs": 5, "warmup": 1})
        assert r.status_code == 202
        j = _wait(c, pid, r.json()["id"])
        assert j["status"] == "succeeded", j["error"]
        assert j["params"]["source"] == "reference"
        r = c.post(f"{P}/projects/{pid}/executions", json={"backend": "local-cuda"})
        assert r.status_code == 409


def test_measurement_csv_import_and_nebius_gate(app):
    with AuthedClient(app) as c:
        csv = ("device_id,model_id,precision,batch,latency_ms,power_w\n"
               "jetson-orin-nano,resnet-50,fp16,1,9.5,11.2\njetson-orin-nano,resnet-50,fp16,4,30.1,\n")  # fmt: skip
        r = c.post(f"{P}/measurements/import?format=csv", content=csv, headers={"Content-Type": "text/plain"})
        assert r.status_code == 201 and r.json()["created"] == 2
        m = [x for x in c.get(f"{P}/measurements?device_id=jetson-orin-nano").json() if x["batch"] == 4][0]
        assert m["power_w_avg"] is None and m["latency_ms_mean"] == 30.1
        bad = c.post(f"{P}/measurements/import?format=csv", content="device_id,latncy\nx,1\n",
                     headers={"Content-Type": "text/plain"})  # fmt: skip
        assert bad.status_code == 422 and "latncy" in bad.json()["detail"]
        r = c.post(f"{P}/benchmarks", json={"device_id": "nebius-l40s"})
        assert r.status_code == 503 and "GC_ENABLE_NEBIUS" in r.json()["detail"]


def test_runs_interrupted_by_a_restart_are_marked_failed(tmp_path):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    from app.db.models import ExecutionJobRow

    with AuthedClient(create_app(url, data_dir=str(tmp_path / "d"))) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        db = c.app.state.session_factory()
        db.add(ExecutionJobRow(workspace_id=c.me["active_workspace_id"], project_id=pid, backend="local-cpu",
                               status="running", params={}))  # fmt: skip
        db.commit()
        db.close()
    with AuthedClient(create_app(url, data_dir=str(tmp_path / "d"))) as c:
        jobs = c.get(f"{P}/projects/{pid}/executions").json()
        assert jobs[0]["status"] == "failed" and "restarted" in jobs[0]["error"]
