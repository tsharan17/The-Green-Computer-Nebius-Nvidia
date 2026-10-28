"""Guided start, priorities, demo projects, health, search, preferences."""

from __future__ import annotations

import pytest

from app.data_loader import clear_custom_devices, get_device, get_model
from app.engine.optimise import optimise
from app.main import create_app
from app.services.seed import example_spec
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'g.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()


def test_priority_reorders_but_never_relaxes_requirements():
    spec = example_spec()
    m, hw = get_model(spec.model_id), get_device(spec.device_id)
    picks = {}
    for pr in ("balanced", "faster", "lower_heat"):
        r = optimise(spec.model_copy(update={"priority": pr}), m, hw)
        assert r.best is not None and r.best.feasibility.value in ("predicted_feasible", "verified_feasible")
        picks[pr] = r.best
    assert picks["faster"].metrics["latency"].value <= picks["balanced"].metrics["latency"].value
    assert (picks["lower_heat"].metrics["temperature_unthrottled"].value
            <= picks["balanced"].metrics["temperature_unthrottled"].value)  # fmt: skip
    assert picks["faster"].config != picks["balanced"].config


def test_guided_start_creates_and_analyses_a_project(app):
    with AuthedClient(app) as c:
        uc = c.get(f"{P}/guide/use-cases").json()
        assert len(uc["use_cases"]) >= 6 and {p["id"] for p in uc["priorities"]} >= {"balanced", "faster", "fit_memory"}
        r = c.post(f"{P}/guide/start", json={"use_case": "mcu-voice", "priority": "lower_power", "name": "Doorbell"})
        assert r.status_code == 201, r.text
        body = r.json()
        spec = body["project"]["spec"]
        assert spec["device_id"] == "stm32h743" and spec["priority"] == "lower_power" and spec["use_case"] == "mcu-voice"
        assert body["analysis"]["outcome"]
        r = c.post(f"{P}/guide/start", json={"use_case": None, "model_id": "resnet-50", "device_id": "nvidia-l4",
                                             "fps": 200, "analyse": False})  # fmt: skip
        assert r.status_code == 201 and "analysis" not in r.json()
        assert c.post(f"{P}/guide/start", json={"use_case": "nope"}).status_code == 422
        assert c.post(f"{P}/guide/start", json={"device_id": "nope"}).status_code == 422


def test_demo_projects_health_and_staleness(app):
    with AuthedClient(app) as c:
        made = c.post(f"{P}/guide/demo").json()["created"]
        assert len(made) == 5 and all(m["outcome"] for m in made)
        assert c.post(f"{P}/guide/demo").json()["created"] == []  # idempotent
        h = {x["project_id"]: x for x in c.get(f"{P}/projects-health").json()}
        mcu = next(m for m in made if "Voice" in m["name"])
        assert h[mcu["project_id"]]["analysed"] and h[mcu["project_id"]]["bottleneck"]
        example = c.get(f"{P}/projects").json()[0]
        assert h[example["id"]]["analysed"] is False
        spec = c.get(f"{P}/projects/{mcu['project_id']}").json()["spec"]
        c.put(f"{P}/projects/{mcu['project_id']}", json=spec | {"workload": spec["workload"] | {"fps": 8}})
        h = {x["project_id"]: x for x in c.get(f"{P}/projects-health").json()}
        assert h[mcu["project_id"]]["stale"] is True


def test_search_finds_own_things_and_never_another_tenants(app):
    with AuthedClient(app, email="a@x.io") as a:
        pid = a.get(f"{P}/projects").json()[0]["id"]
        spec = a.get(f"{P}/projects/{pid}").json()["spec"] | {"name": "Zebra secret robot"}
        a.put(f"{P}/projects/{pid}", json=spec)
        a.post(f"{P}/projects/{pid}/optimise")
        a.post(f"{P}/projects/{pid}/traces?which=best")
        types = {h["type"] for h in a.get(f"{P}/search?q=zebra").json()}
        assert "project" in types
        assert any(h["type"] == "recommendation" for h in a.get(f"{P}/search?q=power mode").json())
        assert any(h["type"] == "hardware" for h in a.get(f"{P}/search?q=jetson").json())
        assert any(h["type"] == "component" for h in a.get(f"{P}/search?q=compute engine").json())
        assert a.get(f"{P}/search?q=z").status_code == 422
    with AuthedClient(app, email="b@x.io") as b:
        assert all(h["type"] != "project" for h in b.get(f"{P}/search?q=zebra").json())


def test_preferences_are_per_user_and_validated(app):
    with AuthedClient(app) as c:
        assert c.get(f"{P}/auth/preferences").json() == {"mode": "simple", "onboarded": False}
        assert c.put(f"{P}/auth/preferences", json={"mode": "engineering", "onboarded": True}).json()["mode"] == "engineering"
        assert c.get(f"{P}/auth/preferences").json()["onboarded"] is True
        assert c.put(f"{P}/auth/preferences", json={"mode": "wizard"}).status_code == 422
        assert c.put(f"{P}/auth/preferences", json={"theme": "x"}).status_code == 422
    with AuthedClient(app, email="other@x.io") as o:
        assert o.get(f"{P}/auth/preferences").json()["mode"] == "simple"
