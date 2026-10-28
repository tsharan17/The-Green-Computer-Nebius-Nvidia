import pytest

from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture()
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 't.db'}")
    with AuthedClient(app) as c:
        yield c


def test_health_and_libraries(client):
    assert client.get(f"{P}/health").json()["status"] == "ok"
    assert len(client.get(f"{P}/models/library").json()) >= 5
    assert len(client.get(f"{P}/hardware/library").json()) >= 5


def test_example_project_is_seeded_once(client):
    projects = client.get(f"{P}/projects").json()
    assert len(projects) == 1
    assert projects[0]["spec"]["constraints"]["max_power_w"] == 55


def test_evaluate_baseline_and_optimise_round_trip(client):
    pid = client.get(f"{P}/projects").json()[0]["id"]
    ev = client.post(f"{P}/projects/{pid}/evaluate")
    assert ev.status_code == 200
    body = ev.json()
    assert body["feasibility"] == "infeasible"  # baseline throttles on a small passive heatsink
    assert body["metrics"]["power_avg"]["provenance"] == "analytical"

    op = client.post(f"{P}/projects/{pid}/optimise").json()
    assert op["best"] is not None
    assert op["best"]["feasibility"] in ("predicted_feasible", "verified_feasible")
    assert op["evaluated"] == sum(op["counts"].values())


def test_create_update_validate(client):
    spec = {"name": "mine", "model_id": "resnet-50", "device_id": "jetson-agx-orin"}
    r = client.post(f"{P}/projects", json=spec)
    assert r.status_code == 201
    pid = r.json()["id"]
    spec["constraints"] = {"max_power_w": 30}
    assert client.put(f"{P}/projects/{pid}", json=spec).json()["spec"]["constraints"]["max_power_w"] == 30
    bad = dict(spec, model_id="nope")
    assert client.post(f"{P}/projects", json=bad).status_code == 422
    assert client.get(f"{P}/projects/9999").status_code == 404


def test_projects_persist_across_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'p.db'}"
    with AuthedClient(create_app(url)) as c:
        c.post(f"{P}/projects", json={"name": "keep", "model_id": "yolov8n", "device_id": "jetson-orin-nano"})
    with AuthedClient(create_app(url)) as c:
        names = [p["spec"]["name"] for p in c.get(f"{P}/projects").json()]
        assert "keep" in names and len([n for n in names if n.startswith("Example")]) == 1


def test_evaluate_with_unsupported_config_is_a_clean_error(client):
    pid = client.get(f"{P}/projects").json()[0]["id"]
    bad = {"precision": "fp16", "batch": 1, "power_mode_id": "NOPE", "cooling_id": "passive_small"}
    assert client.post(f"{P}/projects/{pid}/evaluate", json=bad).status_code == 422
