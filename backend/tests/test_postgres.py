"""Production database check: migrations and a full journey on PostgreSQL.

Skipped unless GC_TEST_PG_URL points at a PostgreSQL server the test may create databases on, e.g.
    GC_TEST_PG_URL=postgresql+psycopg://postgres:@127.0.0.1:5432/postgres
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

PG = os.environ.get("GC_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set GC_TEST_PG_URL to run against PostgreSQL")
P = "/api/v1"


@pytest.fixture()
def pg_url():
    assert PG
    name = f"gc_test_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(PG, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = PG.rsplit("/", 1)[0] + f"/{name}"
    yield url
    sa.create_engine(url).dispose()
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_migrations_create_every_table_and_are_idempotent(pg_url):
    from app.db.migrate import current_revision, head_revision, upgrade
    from app.db.models import Base

    e = sa.create_engine(pg_url)
    upgrade(e)
    upgrade(e)
    insp = sa.inspect(e)
    for t in Base.metadata.sorted_tables:
        assert {c.name for c in t.columns} <= {c["name"] for c in insp.get_columns(t.name)}, t.name
    fks = {fk["referred_table"] for fk in insp.get_foreign_keys("projects")}
    assert {"workspaces", "users"} <= fks  # real constraints on PostgreSQL
    assert current_revision(e) == head_revision()


def test_full_journey_on_postgres(pg_url, tmp_path):
    from app.data_loader import clear_custom_devices
    from app.main import create_app
    from tests.helpers import TEST_PASSWORD, AuthedClient

    app = create_app(pg_url, data_dir=str(tmp_path / "d"))
    try:
        with AuthedClient(app, email="pg@x.io") as c:
            pid = c.get(f"{P}/projects").json()[0]["id"]
            assert c.post(f"{P}/projects/{pid}/optimise").status_code == 200
            assert c.post(f"{P}/projects/{pid}/traces?which=best").status_code == 201
            opts = c.get(f"{P}/projects/{pid}/plan-options").json()
            plan = c.post(f"{P}/projects/{pid}/plans", json={"change_ids": opts["recommended"], "name": "pg"}).json()
            assert c.post(f"{P}/projects/{pid}/exports", json={"kind": "plan_json", "plan_id": plan["id"]}).status_code == 201
            m = {"device_id": "jetson-orin-nano", "model_id": "resnet-50", "precision": "fp16", "latency_ms_mean": 9.0}
            assert c.post(f"{P}/measurements", json=m).status_code == 201
            assert c.get(f"{P}/usage").json()["this_period"]["analysis"] >= 1
            # Release 2: guided start, example projects, health, search and preferences.
            g = c.post(f"{P}/guide/start", json={"use_case": "mcu-voice", "priority": "lower_power"})
            assert g.status_code == 201, g.text
            assert c.post(f"{P}/guide/demo").status_code == 201
            assert any(h["analysed"] for h in c.get(f"{P}/projects-health").json())
            assert c.get(f"{P}/search", params={"q": "voice"}).json()
            assert c.put(f"{P}/auth/preferences", json={"mode": "engineering"}).json()["mode"] == "engineering"
            # Release 2 slice B: a design file into a versioned system graph.
            v = (Path(__file__).parent / "fixtures" / "design" / "accel.v").read_bytes()
            up = c.post(f"{P}/intake/upload?filename=accel.v&project_id={pid}", content=v,
                        headers={"Content-Type": "application/octet-stream"}).json()  # fmt: skip
            assert up["status"] == "valid"
            g = c.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": [up["artifact_id"]]})
            assert g.status_code == 201 and g.json()["version"] == 1
            assert c.get(f"{P}/projects/{pid}/system-graph").json()["links"] is not None
            # Release 2 slice D: physics views, evidence and calibration quality (the measurement above counts).
            assert c.get(f"{P}/projects/{pid}/physics").json()["thermal"]["status"] in ("lumped only", "validated", "not validated")
            assert c.get(f"{P}/projects/{pid}/evidence").json()["setups"]
            q = c.get(f"{P}/calibration/quality", params={"device_id": "jetson-orin-nano"}).json()
            assert any(p["measurements"] >= 1 for p in q["precisions"])
            # Release 2 slice E: the environment is stored in the spec JSON and drives the cooling catalogue.
            spec = c.get(f"{P}/projects/{pid}").json()["spec"]
            spec["environment"] = {"sealed_enclosure": True}
            assert c.put(f"{P}/projects/{pid}", json=spec).status_code == 200
            cool = c.get(f"{P}/projects/{pid}/cooling").json()
            assert cool["environment"]["sealed_enclosure"] and any("sealed" in " ".join(x["reasons"]) for x in cool["excluded"])
            # Release 2 slice G: a reproducibility manifest stored on PostgreSQL recomputes exactly.
            m = c.post(f"{P}/projects/{pid}/exports", json={"kind": "manifest_json"}).json()
            doc = c.get(f"{P}/projects/{pid}/exports/{m['id']}/download").json()
            assert c.post(f"{P}/reproduce/verify", json=doc).json()["ok"]
            # Release 2 slice F: register and pair a device agent, then revoke it.
            reg = c.post(f"{P}/remote-devices", json={"name": "pg board", "device_id": "jetson-orin-nano"}).json()
            from fastapi.testclient import TestClient

            with TestClient(app) as agent:
                paired = agent.post(f"{P}/device-agent/pair", json={"code": reg["pairing_code"], "agent": {"hostname": "pg"}})
                assert paired.status_code == 200, paired.text
                tok = {"Authorization": f"Device {paired.json()['device_token']}"}
                assert agent.get(f"{P}/device-agent/jobs/next", headers=tok).status_code == 204
                assert c.delete(f"{P}/remote-devices/{reg['id']}").json()["status"] == "revoked"
                assert agent.get(f"{P}/device-agent/jobs/next", headers=tok).status_code == 401
            # Release 2 slice H: shared limiter on PostgreSQL, email confirmation, MFA, retention, export.
            assert app.state.rate_limit_backend == "database"
            from app.auth import totp

            link = c.post(f"{P}/auth/email/resend").json()["dev_verify_url"]
            assert c.post(f"{P}/auth/email/verify", json={"token": link.split("token=")[1]}).json()["verified"]
            secret = c.post(f"{P}/auth/mfa/setup").json()["secret"]
            assert c.post(f"{P}/auth/mfa/enable", json={"code": totp.code_at(secret, totp.current_step())}).json()["enabled"]
            assert c.put(f"{P}/workspaces/current/retention", json={"exports_days": 30}).status_code == 200
            assert c.post(f"{P}/workspaces/current/retention/apply").json()["dry_run"] is True
            assert c.get(f"{P}/workspaces/current/export").status_code == 200
            assert c.get(f"{P}/ready").json()["ready"]
            off = {"password": TEST_PASSWORD, "code": totp.code_at(secret, totp.current_step() + 1)}
            assert c.post(f"{P}/auth/mfa/disable", json=off).json()["enabled"] is False
        with AuthedClient(app, email="other@x.io") as o:
            assert o.get(f"{P}/projects/{pid}").status_code == 404
            assert not [h for h in o.get(f"{P}/search", params={"q": "voice"}).json() if h["type"] == "project"]
        with AuthedClient(app, email="pg@x.io") as c:
            r = c.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"})
            assert r.status_code == 200, r.text
            assert r.json()["verification"]["ok"], r.json()["verification"]
        with sa.create_engine(pg_url).connect() as conn:
            for t in ("projects", "design_plans", "exports", "execution_traces", "runs", "system_graphs", "remote_jobs"):
                assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {t} WHERE project_id = :p" if t != "projects"
                                            else "SELECT COUNT(*) FROM projects WHERE id = :p"), {"p": pid}).scalar() == 0
    finally:
        clear_custom_devices()
        app.state.engine.dispose()
