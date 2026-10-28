"""Remote device agent: pairing, signed requests, the real agent code end to end, tampering, replay,
revocation, quality checks, measurement import, tenancy and deletion."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.db.models import RemoteJobRow
from app.engine.measurement_quality import assess
from app.main import create_app
from app.remote import protocol as P
from tests.helpers import TEST_PASSWORD, AuthedClient, tiny_cnn_bytes

API = "/api/v1"
OCTET = {"Content-Type": "application/octet-stream"}
AGENT_FILE = Path(__file__).resolve().parents[3] / "agent" / "gc_agent.py"


def _agent_module():
    spec = importlib.util.spec_from_file_location("gc_agent", AGENT_FILE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


GA = _agent_module()


def transport(client: TestClient):
    """The agent's HTTP transport, routed into the test app (no cookies: a fresh client)."""

    def send(method, path, body, headers):
        r = client.request(method, path, content=body, headers=headers)
        return r.status_code, r.content

    return send


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'ra.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def _setup(c, app, tmp_path):
    pid = c.post(f"{API}/projects", json={"name": "remote", "model_id": "resnet-50", "device_id": "jetson-orin-nano"}).json()["id"]
    up = c.post(f"{API}/intake/upload?filename=tiny.onnx&project_id={pid}", content=tiny_cnn_bytes(random_weights=True), headers=OCTET).json()
    assert up["status"] == "valid"
    reg = c.post(f"{API}/remote-devices", json={"name": "Bench Orin", "device_id": "jetson-orin-nano"})
    assert reg.status_code == 201, reg.text
    dev = reg.json()
    assert dev["status"] == "pending" and len(dev["pairing_code"]) == 14 and dev["pairing_expires_at"]
    home = tmp_path / "agent-home"
    anon = TestClient(app)
    with pytest.raises(SystemExit, match="401"):
        GA.pair("http://testserver", "WRONG-CODE-0000", home, transport(anon))
    cfg = GA.pair("http://testserver", dev["pairing_code"], home, transport(anon))
    assert cfg["remote_device_id"] == dev["id"] and cfg["hardware_profile"] == "jetson-orin-nano"
    with pytest.raises(SystemExit, match="401"):  # a code works once
        GA.pair("http://testserver", dev["pairing_code"], tmp_path / "other", transport(anon))
    assert json.loads((home / "config.json").read_text())["device_token"] == cfg["device_token"]
    return pid, up["artifact_id"], dev["id"], cfg, home, anon


def _approve(c, dev_id, pid, aid, **kw):
    body = {"project_id": pid, "artifact_id": aid, "runs": 40, "warmup": 3, "max_seconds": 30, **kw}
    return c.post(f"{API}/remote-devices/{dev_id}/jobs", json=body)


def test_pair_simulate_and_measure_end_to_end(app, tmp_path):
    with AuthedClient(app) as c:
        pid, aid, dev_id, cfg, home, anon = _setup(c, app, tmp_path)
        listed = c.get(f"{API}/remote-devices").json()[0]
        assert listed["status"] == "paired" and listed["online"] and "pairing_code" not in listed

        # Simulated: kept and labelled, never imported.
        j = _approve(c, dev_id, pid, aid)
        assert j.status_code == 201 and j.json()["status"] == "queued" and j.json()["artifact"]["sha256"]
        sim = GA.Agent(cfg, transport(anon), home, simulate=True)
        sim.heartbeat()
        out = sim.handle_next()
        assert out["status"] == "succeeded" and out["mode"] == "simulated" and out["measurement_id"] is None
        assert "never imported" in out["error"] and out["quality"]["verdict"] in ("good", "usable")
        assert sim.handle_next() is None  # nothing left

        # Measured with ONNX Runtime: quality-checked and imported as a measurement.
        _approve(c, dev_id, pid, aid)
        real = GA.Agent(cfg, transport(anon), home)
        out = real.handle_next()
        assert out["status"] == "succeeded" and out["mode"] == "measured", out.get("error")
        assert out["environment"]["onnxruntime"] != "not installed"
        assert {ch["id"] for ch in out["quality"]["checks"]} >= {"runs", "spread", "warmup", "timer"}
        if out["quality"]["verdict"] == "poor":  # a noisy machine: kept, explained, not imported
            assert out["measurement_id"] is None and "too poor" in out["error"]
        else:
            mid = out["measurement_id"]
            ms = [m for m in c.get(f"{API}/measurements", params={"device_id": "jetson-orin-nano"}).json() if m["id"] == mid]
            assert ms and ms[0]["source"] == "remote_agent" and ms[0]["n_runs"] >= 5 and "Bench Orin" in ms[0]["hardware_label"]
        jobs = c.get(f"{API}/remote-jobs", params={"project_id": pid}).json()
        assert [x["mode"] for x in jobs] == ["measured", "simulated"]


def test_agent_refuses_tampered_replayed_expired_and_over_policy_requests(app, tmp_path):
    with AuthedClient(app) as c:
        pid, aid, dev_id, cfg, home, anon = _setup(c, app, tmp_path)
        agent = GA.Agent(cfg, transport(anon), home, simulate=True)
        key = bytes.fromhex(cfg["signing_key"])
        base = {"protocol": P.PROTOCOL, "job_id": 1, "remote_device_id": dev_id, "action": "benchmark_onnx",
                "artifact": {"id": aid, "sha256": "x" * 64, "size_bytes": 10, "filename": "t.onnx"},
                "params": {"precision": "fp32", "batch": 1, "warmup": 1, "runs": 5, "max_seconds": 10},
                "nonce": "n1", "issued_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}  # fmt: skip
        agent.check(base, GA.sign(key, base))  # a well-formed request passes
        with pytest.raises(GA.Refused, match="signature"):
            agent.check({**base, "params": {**base["params"], "runs": 6}}, GA.sign(key, base))
        with pytest.raises(GA.Refused, match="signature"):
            agent.check(base, GA.sign(b"another key", base))
        shell = {**base, "action": "run_shell"}
        with pytest.raises(GA.Refused, match="not allowed"):
            agent.check(shell, GA.sign(key, shell))
        old = {**base, "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        with pytest.raises(GA.Refused, match="expired"):
            agent.check(old, GA.sign(key, old))
        big = {**base, "params": {**base["params"], "runs": 999999}}
        with pytest.raises(GA.Refused, match="policy"):
            agent.check(big, GA.sign(key, big))
        agent._remember("n1")
        with pytest.raises(GA.Refused, match="replay"):
            agent.check(base, GA.sign(key, base))

        # Tampering on the server side of the wire: the agent rejects, the job records why.
        jid = _approve(c, dev_id, pid, aid).json()["id"]
        with app.state.session_factory() as db:
            row = db.get(RemoteJobRow, jid)
            row.payload = {**row.payload, "params": {**row.payload["params"], "runs": 7}}
            db.commit()
        out = agent.handle_next()
        assert out["status"] == "rejected" and "signature" in c.get(f"{API}/remote-jobs").json()[0]["error"]


def test_server_checks_results_params_and_files(app, tmp_path):
    with AuthedClient(app) as c:
        pid, aid, dev_id, cfg, home, anon = _setup(c, app, tmp_path)
        dev_h = {"Authorization": f"Device {cfg['device_token']}"}
        assert _approve(c, dev_id, pid, aid, runs=0).status_code == 422
        assert _approve(c, dev_id, pid, aid, precision="fp64").status_code == 422
        csv = c.post(f"{API}/intake/upload?filename=p.csv&project_id={pid}", content=b"time_s,power_w\n0,1\n1,2\n", headers=OCTET).json()
        r = _approve(c, dev_id, pid, csv["artifact_id"])
        assert r.status_code == 422 and "ONNX" in r.json()["detail"]

        jid = _approve(c, dev_id, pid, aid).json()["id"]
        env = anon.get(f"{API}/device-agent/jobs/next", headers=dev_h).json()
        job = env["job"]
        good = {"protocol": P.PROTOCOL, "job_id": jid, "nonce": job["nonce"], "mode": "measured", "status": "succeeded",
                "artifact_sha256": job["artifact"]["sha256"], "latencies_ms": [5.0] * 30, "warmup_ms": [9.0],
                "timer_resolution_ns": 100, "wall_seconds": 6.0}  # fmt: skip
        bad_sig = anon.post(f"{API}/device-agent/jobs/{jid}/result", headers=dev_h, json={"result": good, "signature": "0" * 64})
        assert bad_sig.status_code == 401
        key = bytes.fromhex(cfg["signing_key"])
        wrong = {**good, "artifact_sha256": "f" * 64}
        r = anon.post(f"{API}/device-agent/jobs/{jid}/result", headers=dev_h, json={"result": wrong, "signature": GA.sign(key, wrong)})
        assert r.status_code == 422 and "SHA-256" in r.json()["detail"]
        assert c.get(f"{API}/remote-jobs").json()[0]["status"] == "failed"
        # Without a device token, or with a revoked one, the agent side answers 401.
        assert anon.get(f"{API}/device-agent/jobs/next").status_code == 401
        assert c.delete(f"{API}/remote-devices/{dev_id}").json()["status"] == "revoked"
        assert anon.get(f"{API}/device-agent/jobs/next", headers=dev_h).status_code == 401


def test_devices_and_runs_are_private_and_deleted_with_the_workspace(app, tmp_path):
    with AuthedClient(app, email="owner@x.io") as a:
        pid, aid, dev_id, cfg, home, anon = _setup(a, app, tmp_path)
        _approve(a, dev_id, pid, aid)
    with AuthedClient(app, email="other@x.io") as b:
        assert b.get(f"{API}/remote-devices").json() == [] and b.get(f"{API}/remote-jobs").json() == []
        mine = b.post(f"{API}/projects", json={"name": "x", "model_id": "resnet-50", "device_id": "jetson-orin-nano"}).json()["id"]
        assert b.post(f"{API}/remote-devices/{dev_id}/jobs", json={"project_id": mine, "artifact_id": aid}).status_code == 404
        assert b.delete(f"{API}/remote-devices/{dev_id}").status_code == 404
        mydev = b.post(f"{API}/remote-devices", json={"name": "mine", "device_id": "jetson-orin-nano"}).json()
        assert b.post(f"{API}/remote-devices/{mydev['id']}/jobs", json={"project_id": mine, "artifact_id": aid}).status_code == 409  # not paired
    with AuthedClient(app, email="owner@x.io") as a:
        assert a.post(f"{API}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"}).status_code == 200
    with app.state.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM remote_devices WHERE id = :d"), {"d": dev_id}).scalar() == 0
        assert conn.execute(sa.text("SELECT COUNT(*) FROM remote_jobs WHERE project_id = :p"), {"p": pid}).scalar() == 0


def test_measurement_quality_verdicts():
    steady = [10.0 + 0.05 * (i % 3) for i in range(60)]
    good = assess(steady, [30.0] * 5, 100, [50.0, 51.0], 12.0)
    assert good["verdict"] == "good" and good["stats"]["n"] == 60
    few = assess([10.0] * 8, [20.0], 100, None, 1.0)
    assert few["verdict"] == "usable" and {c["id"] for c in few["checks"] if c["status"] == "warn"} >= {"runs", "duration"}
    noisy = assess([10.0, 30.0] * 20, [20.0], 100)
    assert noisy["verdict"] == "poor" and any(c["id"] == "spread" and c["status"] == "fail" for c in noisy["checks"])
    drifting = assess([10.0 + i * 0.2 for i in range(60)], [12.0], 100)
    assert any(c["id"] == "drift" and c["status"] == "warn" for c in drifting["checks"])
    coarse = assess([0.01] * 30, [0.02], 1_000_000)
    assert coarse["verdict"] == "poor" and any(c["id"] == "timer" for c in coarse["checks"])
    assert assess([])["verdict"] == "poor"
    hot = assess(steady, [30.0], 100, [40.0, 52.0])
    assert any(c["id"] == "thermal" and c["status"] == "warn" for c in hot["checks"])
