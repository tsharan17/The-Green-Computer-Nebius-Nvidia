"""Retention rules, a full workspace export, and verified deletion."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
import sqlalchemy as sa

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import TEST_PASSWORD, AuthedClient, tiny_cnn_bytes

P = "/api/v1"
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'wd.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def _age(app, table: str, days: int) -> None:
    with app.state.engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE {table} SET created_at = datetime('now', '-{days} days')"))  # noqa: S608 - test-only table names


def test_retention_rules_dry_run_and_apply(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        assert c.post(f"{P}/projects/{pid}/optimise").status_code == 200
        assert c.post(f"{P}/projects/{pid}/exports", json={"kind": "report_md"}).status_code == 201
        assert c.post(f"{P}/projects/{pid}/traces?which=baseline").status_code == 201
        model = c.post(f"{P}/intake/upload?filename=m.onnx&project_id={pid}", content=tiny_cnn_bytes(), headers=OCTET).json()
        csv = c.post(f"{P}/intake/upload?filename=p.csv&project_id={pid}", content=b"time_s,power_w\n0,1\n1,2\n", headers=OCTET).json()
        for t in ("exports", "execution_traces", "artifacts", "audit_log"):
            _age(app, t, 400)

        r = c.get(f"{P}/workspaces/current/retention").json()
        assert all(v is None for v in r["rules"].values()) and r["audit_min_days"] == 90
        assert c.put(f"{P}/workspaces/current/retention", json={"audit_days": 30}).status_code == 422
        assert c.put(f"{P}/workspaces/current/retention", json={"forever": 3}).status_code == 422
        rules = {"exports_days": 365, "traces_days": 365, "unused_files_days": 365, "audit_days": 365, "device_runs_days": 30}
        assert c.put(f"{P}/workspaces/current/retention", json=rules).json()["rules"]["exports_days"] == 365

        dry = c.post(f"{P}/workspaces/current/retention/apply").json()
        assert dry["dry_run"] and dry["removed"]["exports"] == 1 and dry["removed"]["traces"] == 1
        assert dry["removed"]["unused_files"] == 1 and dry["removed"]["audit_entries"] >= 1  # the ONNX backs a model: kept
        assert len(c.get(f"{P}/projects/{pid}/exports").json()) == 1  # nothing deleted yet

        done = c.post(f"{P}/workspaces/current/retention/apply", params={"dry_run": False}).json()
        assert done["removed"] == dry["removed"]
        assert c.get(f"{P}/projects/{pid}/exports").json() == [] and c.get(f"{P}/projects/{pid}/traces").json() == []
        arts = {a["id"]: a for a in c.get(f"{P}/artifacts").json()}
        assert model["artifact_id"] in arts and arts[model["artifact_id"]]["stored"]
        assert csv["artifact_id"] not in arts  # marked deleted, bytes gone
        assert any(e["action"] == "retention.applied" for e in c.get(f"{P}/audit").json())


def test_workspace_export_is_complete_hashed_and_secret_free(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        c.post(f"{P}/intake/upload?filename=m.onnx&project_id={pid}", content=tiny_cnn_bytes(), headers=OCTET)
        c.post(f"{P}/remote-devices", json={"name": "b", "device_id": "jetson-orin-nano"})
        r = c.get(f"{P}/workspaces/current/export")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        man = json.loads(z.read("MANIFEST.json"))
        assert man["schema"] == "green-computer.workspace-export/1" and man["row_counts"]["projects"] >= 1
        for name, h in man["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == h
        assert any(n.startswith("files/uploads/") and n.endswith("m.onnx") for n in z.namelist())
        blob = b"".join(z.read(n) for n in z.namelist())
        for secret in (b"token_hash", b"pairing_hash", b"password_hash", b"mfa_secret"):
            assert secret not in blob
        assert json.loads(z.read("data/remote_devices.json"))[0]["name"] == "b"


def test_only_owners_manage_data_and_deletion_is_verified(app):
    with AuthedClient(app, email="ed@x.io"):
        pass
    with AuthedClient(app, email="own@x.io") as o:
        ws = o.post(f"{P}/workspaces", json={"name": "Team"}).json()["id"]
        o.headers["X-GC-Workspace"] = str(ws)
        assert o.post(f"{P}/workspaces/current/members", json={"email": "ed@x.io", "role": "editor"}).status_code == 201
        assert o.get(f"{P}/workspaces/current/export").status_code == 200  # the owner can
    with AuthedClient(app, email="ed@x.io") as e:
        e.headers["X-GC-Workspace"] = str(ws)
        assert e.get(f"{P}/workspaces/current/export").status_code == 403
        assert e.put(f"{P}/workspaces/current/retention", json={"exports_days": 10}).status_code == 403
        assert e.post(f"{P}/workspaces/current/retention/apply").status_code == 403
    with AuthedClient(app, email="solo@x.io") as s:
        pid = s.get(f"{P}/projects").json()[0]["id"]
        s.post(f"{P}/intake/upload?filename=m.onnx&project_id={pid}", content=tiny_cnn_bytes(), headers=OCTET)
        out = s.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"}).json()
        v = out["verification"]
        assert out["deleted"] and v["ok"] and v["rows_left"] == {} and v["file_folders_left"] == []
        assert {"projects", "artifacts", "remote_jobs", "users", "email_verifications"} <= set(v["checked_tables"])
