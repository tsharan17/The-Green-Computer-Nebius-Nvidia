import pytest

from app.main import create_app
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"
OCT = {"Content-Type": "application/octet-stream"}


@pytest.fixture()
def client(tmp_path):
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 't.db'}")) as c:
        yield c


def test_onnx_upload_then_optimise_a_project_on_it(client):
    r = client.post(f"{P}/models/upload?name=tiny&dynamic_dim_default=8",
                    content=tiny_cnn_bytes(), headers=OCT)  # fmt: skip
    assert r.status_code == 201, r.text
    prof = r.json()["profile"]
    assert prof["id"].startswith("custom-") and prof["fidelity"] == "graph"
    assert "layers" not in prof  # not echoed back (can be large)

    lib = client.get(f"{P}/models/library").json()
    assert prof["id"] in [m["id"] for m in lib]

    pr = client.post(f"{P}/projects", json={"name": "c", "model_id": prof["id"],
                                            "device_id": "jetson-orin-nano"})  # fmt: skip
    assert pr.status_code == 201
    pid = pr.json()["id"]
    op = client.post(f"{P}/projects/{pid}/optimise")
    assert op.status_code == 200
    assert op.json()["evaluated"] >= 100


def test_upload_refuses_pickles_garbage_and_oversize(client):
    assert client.post(f"{P}/models/upload?filename=m.pt", content=b"x", headers=OCT).status_code == 415
    assert client.post(f"{P}/models/upload", content=b"\x80\x04junk", headers=OCT).status_code == 422
    assert client.post(f"{P}/models/upload", content=b"not onnx", headers=OCT).status_code == 422
    assert client.post(f"{P}/models/upload?baseline_accuracy=2", content=tiny_cnn_bytes(),
                       headers=OCT).status_code == 422  # fmt: skip


def test_manual_model_round_trip_and_validation(client):
    r = client.post(f"{P}/models/manual", json={"name": "mine", "params_m": 4, "gflops": 3})
    assert r.status_code == 201
    assert r.json()["fidelity"] == "manual"
    assert client.post(f"{P}/models/manual", json={"name": "bad", "params_m": -1,
                                                   "gflops": 3}).status_code == 422  # fmt: skip


def test_project_with_unknown_custom_model_is_rejected(client):
    bad = {"name": "x", "model_id": "custom-999", "device_id": "jetson-orin-nano"}
    assert client.post(f"{P}/projects", json=bad).status_code == 422


def test_optimise_payload_has_codesign_points_and_trace(client):
    pid = client.get(f"{P}/projects").json()[0]["id"]
    body = client.post(f"{P}/projects/{pid}/optimise").json()
    assert body["codesign"]["baseline"]["config"]
    assert len(body["points"]) == body["evaluated"] >= 1000
    assert body["baseline"]["thermal_trace"]
    assert body["baseline"]["solver"]["throttled"] is True
