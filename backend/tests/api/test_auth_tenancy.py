"""Accounts, sessions, CSRF, password reset, versions, tenant isolation and account deletion."""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import TEST_PASSWORD, AuthedClient, tiny_cnn_bytes

P = "/api/v1"
H = {"X-GC-CSRF": "1"}


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'auth.db'}", data_dir=str(tmp_path / "data"))
    yield a
    clear_custom_devices()


def _anon(app) -> TestClient:
    return TestClient(app)


# --- sign-up, sign-in, sessions ----------------------------------------------------------


def test_signup_validates_and_refuses_duplicates(app):
    with _anon(app) as c:
        assert c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "nope", "password": TEST_PASSWORD},
                      headers=H).status_code == 422  # fmt: skip
        r = c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "a@x.io", "password": "short"}, headers=H)
        assert r.status_code == 422 and "10 characters" in r.json()["detail"]
        r = c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "a@x.io", "password": "password123"}, headers=H)
        assert r.status_code == 422 and "easy to guess" in r.json()["detail"]
        ok = c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "A@X.io ", "password": TEST_PASSWORD}, headers=H)
        assert ok.status_code == 201 and ok.json()["user"]["email"] == "a@x.io"
        assert ok.json()["role"] == "owner"
        dup = c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "a@x.io", "password": TEST_PASSWORD}, headers=H)
        assert dup.status_code == 409


def test_everything_but_health_and_auth_needs_a_session(app):
    with _anon(app) as c:
        assert c.get(f"{P}/health").status_code == 200
        assert c.get(f"{P}/auth/config").status_code == 200
        for path in ("/projects", "/models/library", "/hardware/library", "/measurements",
                     "/verify", "/audit", "/usage", "/artifacts", "/auth/me"):  # fmt: skip
            assert c.get(f"{P}{path}").status_code == 401, path


def test_signin_errors_do_not_reveal_whether_an_account_exists(app):
    with AuthedClient(app, email="real@x.io"):
        pass
    with _anon(app) as c:
        a = c.post(f"{P}/auth/signin", json={"email": "real@x.io", "password": "wrong password!!"}, headers=H)
        b = c.post(f"{P}/auth/signin", json={"email": "ghost@x.io", "password": "wrong password!!"}, headers=H)
        assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_csrf_header_and_origin_are_enforced_for_cookie_sessions(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        del c.headers["X-GC-CSRF"]
        assert c.post(f"{P}/projects/{pid}/evaluate").status_code == 403
        assert c.get(f"{P}/projects/{pid}").status_code == 200  # reads need no header
        r = c.post(f"{P}/projects/{pid}/evaluate", headers={**H, "Origin": "https://evil.example"})
        assert r.status_code == 403
        assert c.post(f"{P}/projects/{pid}/evaluate", headers=H).status_code == 200
    with _anon(app) as c:  # sign-in itself needs the header (login CSRF)
        r = c.post(f"{P}/auth/signin", json={"email": "tester@example.com", "password": TEST_PASSWORD})
        assert r.status_code == 403


def test_origin_check_uses_the_forwarded_scheme_behind_a_tls_proxy(app, monkeypatch):
    # Cloud Run and Render end TLS and pass plain http on, so the browser's https Origin must still match.
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        url = f"{P}/projects/{pid}/evaluate"
        origin = {"Origin": "https://testserver"}
        monkeypatch.delenv("GC_TRUST_PROXY", raising=False)
        assert c.post(url, headers={**H, **origin}).status_code == 403
        monkeypatch.setenv("GC_TRUST_PROXY", "1")
        assert c.post(url, headers={**H, **origin, "X-Forwarded-Proto": "https"}).status_code == 200
        r = c.post(url, headers={**H, "Origin": "https://evil.example", "X-Forwarded-Proto": "https"})
        assert r.status_code == 403


def test_api_token_works_without_cookie_or_csrf_header(app):
    with AuthedClient(app) as c:
        tok = c.post(f"{P}/auth/tokens", json={"label": "ci"}).json()["token"]
    with _anon(app) as c:
        auth = {"Authorization": f"Bearer {tok}"}
        pid = c.get(f"{P}/projects", headers=auth).json()[0]["id"]
        assert c.post(f"{P}/projects/{pid}/evaluate", headers=auth).status_code == 200
        assert c.get(f"{P}/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_signout_revokes_the_session(app):
    with AuthedClient(app) as c:
        assert c.get(f"{P}/auth/me").status_code == 200
        cookie = c.cookies.get("gc_session")
        assert c.post(f"{P}/auth/signout").status_code == 200
    with _anon(app) as c:
        c.cookies.set("gc_session", cookie)
        assert c.get(f"{P}/auth/me").status_code == 401


def test_password_reset_flow_is_single_use_and_signs_everyone_out(app, monkeypatch):
    with AuthedClient(app) as c:
        old_cookie = c.cookies.get("gc_session")
    with _anon(app) as c:
        r = c.post(f"{P}/auth/password/forgot", json={"email": "tester@example.com"}, headers=H).json()
        unknown = c.post(f"{P}/auth/password/forgot", json={"email": "nobody@x.io"}, headers=H).json()
        assert r["message"] == unknown["message"] and "dev_reset_url" not in unknown
        token = r["dev_reset_url"].split("token=")[1]
        assert app.state.email_sender.outbox[-1].to == "tester@example.com"
        new = "a brand new passphrase"
        assert c.post(f"{P}/auth/password/reset", json={"token": token, "password": new},
                      headers=H).status_code == 200  # fmt: skip
        again = c.post(f"{P}/auth/password/reset", json={"token": token, "password": new}, headers=H)
        assert again.status_code == 400
        assert c.post(f"{P}/auth/signin", json={"email": "tester@example.com", "password": TEST_PASSWORD},
                      headers=H).status_code == 401  # fmt: skip
        assert c.post(f"{P}/auth/signin", json={"email": "tester@example.com", "password": new},
                      headers=H).status_code == 200  # fmt: skip
    with _anon(app) as c:
        c.cookies.set("gc_session", old_cookie)
        assert c.get(f"{P}/auth/me").status_code == 401
    monkeypatch.setenv("GC_ENV", "production")
    with _anon(app) as c:
        r = c.post(f"{P}/auth/password/forgot", json={"email": "tester@example.com"}, headers=H).json()
        assert "dev_reset_url" not in r  # never in production


def test_change_password_signs_out_other_sessions(app):
    with AuthedClient(app) as a:
        with AuthedClient(app) as b:  # same account, second browser
            r = a.post(f"{P}/auth/password/change",
                       json={"current_password": TEST_PASSWORD, "new_password": "another long one!"})  # fmt: skip
            assert r.status_code == 200 and r.json()["other_sessions_signed_out"] >= 1
            assert b.get(f"{P}/auth/me").status_code == 401
            assert a.get(f"{P}/auth/me").status_code == 200


# --- projects, versions, history ----------------------------------------------------------


def test_versions_are_immutable_and_runs_point_at_them(app):
    with AuthedClient(app) as c:
        p = c.get(f"{P}/projects").json()[0]
        pid, spec = p["id"], p["spec"]
        assert p["version"] == 1
        assert c.put(f"{P}/projects/{pid}", json=spec).json()["version"] == 1  # unchanged: no version
        spec["workload"]["fps"] = 15
        assert c.put(f"{P}/projects/{pid}", json=spec).json()["version"] == 2
        c.post(f"{P}/projects/{pid}/optimise")
        vs = c.get(f"{P}/projects/{pid}/versions").json()
        assert [v["version"] for v in vs] == [2, 1] and vs[1]["spec"]["workload"]["fps"] == 30
        restored = c.post(f"{P}/projects/{pid}/versions/1/restore").json()
        assert restored["version"] == 3 and restored["spec"]["workload"]["fps"] == 30
        runs = c.get(f"{P}/projects/{pid}/runs").json()
        assert runs[0]["kind"] == "optimise" and runs[0]["version"] == 2
        full = c.get(f"{P}/projects/{pid}/runs/{runs[0]['id']}").json()
        assert full["result"]["baseline"]["config"]
        actions = [e["action"] for e in c.get(f"{P}/audit").json()]
        assert {"project.version_created", "analysis.run", "project.version_restored"} <= set(actions)


def test_archive_hides_a_project_from_the_default_list(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        c.post(f"{P}/projects/{pid}/archive", json={"archived": True})
        assert c.get(f"{P}/projects").json() == []
        assert len(c.get(f"{P}/projects?include_archived=true").json()) == 1


# --- tenant isolation ---------------------------------------------------------------------


def test_one_tenant_never_reaches_another_tenants_data(app):
    with AuthedClient(app, email="alice@x.io") as alice:
        a_pid = alice.post(f"{P}/projects", json={"name": "Alice secret", "model_id": "yolov8s",
                                                  "device_id": "jetson-orin-nx"}).json()["id"]  # fmt: skip
        up = alice.post(f"{P}/models/upload?filename=secret.onnx", content=tiny_cnn_bytes(),
                        headers={"Content-Type": "application/octet-stream"}).json()  # fmt: skip
        model_id, art_id = up["profile"]["id"], up["artifact_id"]
        dev = alice.post(f"{P}/hardware/custom", json={
            "name": "Alice Board", "style": "generic", "peak_tops": {"fp16": 2.0},
            "mem_bw_gbs": 20, "mem_gb": 2, "tdp_w": 8}).json()  # fmt: skip
        meas = alice.post(f"{P}/measurements", json={
            "device_id": "jetson-orin-nx", "model_id": "yolov8s", "precision": "fp16",
            "latency_ms_mean": 12.0, "external_id": "alice-1"}).json()["measurements"][0]  # fmt: skip
        alice.post(f"{P}/projects/{a_pid}/optimise")

    with AuthedClient(app, email="bob@x.io") as bob:
        assert all(p["id"] != a_pid for p in bob.get(f"{P}/projects").json())
        for method, path in (("get", f"/projects/{a_pid}"), ("put", f"/projects/{a_pid}"),
                             ("post", f"/projects/{a_pid}/evaluate"), ("post", f"/projects/{a_pid}/optimise"),
                             ("get", f"/projects/{a_pid}/versions"), ("get", f"/projects/{a_pid}/runs"),
                             ("get", f"/projects/{a_pid}/report"), ("get", f"/projects/{a_pid}/recipe"),
                             ("post", f"/projects/{a_pid}/layers"), ("get", f"/artifacts/{art_id}"),
                             ("get", f"/artifacts/{art_id}/download"),
                             ("delete", f"/measurements/{meas['id']}"),
                             ("delete", f"/hardware/custom/{dev['id']}")):  # fmt: skip
            kw = {"json": {"name": "x", "model_id": "yolov8s", "device_id": "jetson-orin-nx"}} if method == "put" else {}
            r = getattr(bob, method)(f"{P}{path}", **kw)
            assert r.status_code == 404, (method, path, r.status_code)
        assert all(m["id"] != model_id for m in bob.get(f"{P}/models/library").json())
        assert all(d["id"] != dev["id"] for d in bob.get(f"{P}/hardware/library").json())
        assert all(m["id"] != meas["id"] for m in bob.get(f"{P}/measurements").json())
        assert bob.get(f"{P}/artifacts").json() == []
        assert all("Alice" not in str(e) for e in bob.get(f"{P}/audit").json())
        # Using Alice's ids in Bob's own project is refused as unknown.
        for spec in ({"name": "x", "model_id": model_id, "device_id": "jetson-orin-nx"},
                     {"name": "x", "model_id": "yolov8s", "device_id": dev["id"]}):  # fmt: skip
            assert bob.post(f"{P}/projects", json=spec).status_code == 422
        # Bob's measurement with the same external id is his own, not a duplicate of Alice's.
        r = bob.post(f"{P}/measurements", json={
            "device_id": "jetson-orin-nx", "model_id": "yolov8s", "precision": "fp16",
            "latency_ms_mean": 30.0, "external_id": "alice-1"})  # fmt: skip
        assert r.status_code == 201 and r.json()["created"] == 1
        # Another workspace id in the header is refused.
        ws_a = 1
        assert bob.get(f"{P}/projects", headers={"X-GC-Workspace": str(ws_a)}).status_code == 403


def test_team_workspace_roles(app):
    with AuthedClient(app, email="viewer@x.io"):
        pass
    with AuthedClient(app, email="owner@x.io") as owner:
        team = owner.post(f"{P}/workspaces", json={"name": "Robotics team"}).json()
        hdr = {"X-GC-Workspace": str(team["id"])}
        pid = owner.post(f"{P}/projects", headers=hdr, json={
            "name": "shared", "model_id": "yolov8n", "device_id": "jetson-orin-nano"}).json()["id"]  # fmt: skip
        r = owner.post(f"{P}/workspaces/current/members", headers=hdr,
                       json={"email": "viewer@x.io", "role": "viewer"})  # fmt: skip
        assert r.status_code == 201
        personal = {"X-GC-Workspace": str(owner.me["active_workspace_id"])}
        assert owner.post(f"{P}/workspaces/current/members", headers=personal,
                          json={"email": "viewer@x.io"}).status_code == 422  # fmt: skip
    with AuthedClient(app, email="viewer@x.io") as viewer:
        assert viewer.get(f"{P}/projects/{pid}", headers=hdr).status_code == 200
        assert viewer.post(f"{P}/projects/{pid}/optimise", headers=hdr).status_code == 403
        assert viewer.get(f"{P}/projects/{pid}").status_code == 404  # not in their own workspace


# --- deletion -----------------------------------------------------------------------------


def test_account_deletion_removes_data_files_and_access(app, tmp_path):
    with AuthedClient(app, email="gone@x.io") as c:
        up = c.post(f"{P}/models/upload?filename=m.onnx", content=tiny_cnn_bytes(),
                    headers={"Content-Type": "application/octet-stream"}).json()  # fmt: skip
        ws = c.me["active_workspace_id"]
        blob_dir = tmp_path / "data" / "blobs" / f"ws{ws}"
        assert any(blob_dir.iterdir())
        assert c.post(f"{P}/auth/account/delete", json={"password": "wrong", "confirm": "DELETE"}).status_code == 403
        assert c.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "no"}).status_code == 422
        r = c.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"})
        assert r.status_code == 200 and r.json()["workspaces_deleted"] == 1
        assert c.get(f"{P}/auth/me").status_code == 401
    assert not blob_dir.exists()
    db = sqlite3.connect(tmp_path / "auth.db")
    for table in ("users", "projects", "custom_models", "artifacts", "project_versions", "auth_sessions"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='account.deleted'").fetchone()[0] == 1
    assert up["profile"]["id"]
    with _anon(app) as c:
        assert c.post(f"{P}/auth/signin", json={"email": "gone@x.io", "password": TEST_PASSWORD},
                      headers=H).status_code == 401  # fmt: skip


def test_deleting_an_owner_hands_a_shared_workspace_over(app):
    with AuthedClient(app, email="member@x.io"):
        pass
    with AuthedClient(app, email="founder@x.io") as f:
        team = f.post(f"{P}/workspaces", json={"name": "Team"}).json()
        hdr = {"X-GC-Workspace": str(team["id"])}
        f.post(f"{P}/workspaces/current/members", headers=hdr, json={"email": "member@x.io"})
        pid = f.post(f"{P}/projects", headers=hdr, json={
            "name": "kept", "model_id": "yolov8n", "device_id": "jetson-orin-nano"}).json()["id"]  # fmt: skip
        f.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"})
    with AuthedClient(app, email="member@x.io") as m:
        ws = {w["id"]: w for w in m.get(f"{P}/workspaces").json()}
        assert ws[team["id"]]["role"] == "owner"
        assert m.get(f"{P}/projects/{pid}", headers=hdr).status_code == 200


# --- usage, uploads, migrations -----------------------------------------------------------


def test_uploads_are_stored_audited_and_metered(app):
    with AuthedClient(app) as c:
        r = c.post(f"{P}/models/upload?filename=tiny.onnx", content=tiny_cnn_bytes(),
                   headers={"Content-Type": "application/octet-stream"})  # fmt: skip
        assert r.status_code == 201
        v = r.json()["validation"]
        assert v["format"] == "onnx" and v["inputs"][0]["shape"] == [1, 3, 8, 8]
        assert v["op_histogram"]["Conv"] == 1
        bad = c.post(f"{P}/models/upload?filename=bad.onnx", content=b"not onnx",
                     headers={"Content-Type": "application/octet-stream"})  # fmt: skip
        assert bad.status_code == 422
        arts = c.get(f"{P}/artifacts").json()
        assert [a["status"] for a in arts] == ["invalid", "valid"]
        assert arts[1]["stored"] and not arts[0]["stored"]  # refused files keep no bytes
        dl = c.get(f"{P}/artifacts/{arts[1]['id']}/download")
        assert dl.status_code == 200 and dl.content == tiny_cnn_bytes()
        u = c.get(f"{P}/usage").json()
        assert u["storage_bytes_in_use"] == len(tiny_cnn_bytes())
        assert u["platform_cost"]["configured"] is False and u["platform_cost"]["total"] == 0
        assert c.delete(f"{P}/artifacts/{arts[1]['id']}").status_code == 200
        assert c.get(f"{P}/usage").json()["storage_bytes_in_use"] == 0
        assert {"file.uploaded", "file.deleted"} <= {e["action"] for e in c.get(f"{P}/audit").json()}


def test_migrations_create_every_mapped_table_and_column(tmp_path):
    from app.db.migrate import head_revision, upgrade
    from app.db.models import Base

    e = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade(e)
    insp = sa.inspect(e)
    for table in Base.metadata.sorted_tables:
        cols = {c["name"] for c in insp.get_columns(table.name)}
        assert {c.name for c in table.columns} <= cols, table.name
    with e.connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == head_revision()


def test_legacy_data_is_claimed_by_the_first_account_only(tmp_path):
    """A 0.7.0 database (tables from create_all, no accounts) upgrades in place."""
    from app.db.migrate import upgrade

    path = tmp_path / "legacy.db"
    e = sa.create_engine(f"sqlite:///{path}")
    upgrade(e, "0001_baseline")
    with e.begin() as conn:
        conn.execute(sa.text("INSERT INTO projects (name, spec, created_at, updated_at) VALUES "
                             "('Old work', :s, '2026-01-01', '2026-01-01')"),
                     {"s": '{"name": "Old work", "model_id": "yolov8n", "device_id": "jetson-orin-nano"}'})  # fmt: skip
    app = create_app(f"sqlite:///{path}", data_dir=str(tmp_path / "d"))
    with AuthedClient(app, email="first@x.io") as first:
        ws = {w["id"]: w for w in first.get(f"{P}/workspaces").json()}
        legacy = [w for w in ws.values() if w["legacy"]]
        assert len(legacy) == 1
        names = [p["spec"]["name"] for p in first.get(
            f"{P}/projects", headers={"X-GC-Workspace": str(legacy[0]["id"])}).json()]  # fmt: skip
        assert names == ["Old work"]
    with AuthedClient(app, email="second@x.io") as second:
        assert all(not w["legacy"] for w in second.get(f"{P}/workspaces").json())
