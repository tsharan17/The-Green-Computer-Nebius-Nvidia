"""Physics views, evidence per value and calibration quality: API, labels, tenancy."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'ph.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def test_physics_evidence_and_calibration_quality(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        # Before analysis: evidence says so; the plan's physics needs an analysis first.
        ev0 = c.get(f"{P}/projects/{pid}/evidence").json()
        assert ev0["setups"] == [] and "Analyse" in ev0["message"]
        assert c.get(f"{P}/projects/{pid}/physics", params={"which": "best"}).status_code == 409

        assert c.post(f"{P}/projects/{pid}/optimise").status_code == 200
        ph = c.get(f"{P}/projects/{pid}/physics").json()
        th = ph["thermal"]
        assert th["status"] == "lumped only" and {n["label"] for n in th["nodes"]} == {"attribution"}
        assert th["hottest"] in ("compute", "memory")
        assert any(lv["id"] == "ram" for lv in ph["memory"])
        eff = {e["id"]: e for e in ph["runtime"]["effects"]}
        assert eff["launch"]["in_headline"] and eff["loading"]["provenance"] == "assumed"
        best = c.get(f"{P}/projects/{pid}/physics", params={"which": "best"}).json()
        assert best["label"] == "recommended setup" and best["thermal"]["lumped_c"] <= th["lumped_c"]

        ev = c.get(f"{P}/projects/{pid}/evidence").json()
        labels = [s["label"] for s in ev["setups"]]
        assert labels == ["Today", "With the plan"]
        lat = next(m for m in ev["setups"][0]["metrics"] if m["key"] == "latency")
        assert lat["low"] <= lat["value"] <= lat["high"] and lat["provenance"] == "analytical"
        assert {f["fact"] for f in lat["facts"]} >= {"Peak compute", "Memory bandwidth"} and lat["calibrated"] is False
        temp = next(m for m in ev["setups"][0]["metrics"] if m["key"] == "temperature")
        assert any(f["source"] == "your input" for f in temp["facts"])
        cq = ev["calibration"]
        assert cq["device_id"] == "jetson-orin-nx" and all(p["latency"]["grade"] == "none" for p in cq["precisions"])

        # A measured run at today's exact setup with a temperature validates (or not) the lumped model.
        cfg = ph["config"]
        meas = {"device_id": "jetson-orin-nx", "model_id": "yolov8s", "precision": cfg["precision"], "batch": cfg["batch"],
                "power_mode_id": cfg["power_mode_id"], "cooling_id": cfg["cooling_id"], "latency_ms_mean": 40.0,
                "temperature_c_max": th["lumped_c"] - 1.5, "source": "manual", "hardware_label": "bench unit"}  # fmt: skip
        assert c.post(f"{P}/measurements", json=meas).status_code == 201
        th2 = c.get(f"{P}/projects/{pid}/physics").json()["thermal"]
        assert th2["measured"]["label"] == "measured" and th2["measured"]["source"].endswith("(bench unit)")
        assert th2["status"] in ("validated", "not validated")

        q = c.get(f"{P}/calibration/quality", params={"device_id": "jetson-orin-nx"}).json()
        row = next(p for p in q["precisions"] if p["precision"] == cfg["precision"])
        assert row["measurements"] >= 1 and row["with_temperature"] >= 1
        assert row["latency"]["grade"] in ("too few", "none", "rejected", "fair", "good", "strong")
        assert c.get(f"{P}/calibration/quality", params={"device_id": "no-such-device"}).status_code == 404


def test_physics_is_private_and_needs_sign_in(app):
    with AuthedClient(app, email="a@x.io") as a:
        pid = a.get(f"{P}/projects").json()[0]["id"]
    with AuthedClient(app, email="b@x.io") as b:
        for path in ("physics", "evidence"):
            assert b.get(f"{P}/projects/{pid}/{path}").status_code == 404, path
    with TestClient(app) as anon:
        assert anon.get(f"{P}/projects/{pid}/physics").status_code == 401
        assert anon.get(f"{P}/calibration/quality", params={"device_id": "jetson-orin-nx"}).status_code == 401
