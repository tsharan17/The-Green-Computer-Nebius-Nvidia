"""Readiness, request ids, structured logs, metrics, error capture and telemetry."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.main import create_app
from app.observability import JsonFormatter
from app.providers import errors
from app.services import telemetry
from tests.helpers import AuthedClient

P = "/api/v1"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'ops.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def test_readiness_reports_each_check(app, tmp_path):
    with TestClient(app) as c:
        r = c.get(f"{P}/ready")
        assert r.status_code == 200 and r.json()["ready"]
        assert {x["check"] for x in r.json()["checks"]} == {"database", "schema", "file_storage"}
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x")
        real = app.state.blobs.root
        app.state.blobs.root = blocker / "blobs"
        try:
            bad = c.get(f"{P}/ready")
            assert bad.status_code == 503 and not bad.json()["ready"]
            assert next(x for x in bad.json()["checks"] if x["check"] == "file_storage")["ok"] is False
        finally:
            app.state.blobs.root = real


def test_request_ids_and_structured_logs(app):
    with TestClient(app) as c:
        r = c.get(f"{P}/health")
        assert len(r.headers["X-Request-ID"]) == 32
        assert c.get(f"{P}/health", headers={"X-Request-ID": "trace-abc-123"}).headers["X-Request-ID"] == "trace-abc-123"
        assert c.get(f"{P}/health", headers={"X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"] != "bad id with spaces"
    rec = logging.LogRecord("green_computer.http", logging.INFO, __file__, 1, "GET /x 200", None, None)
    rec.request_id, rec.route, rec.status, rec.ms = "abc", "/api/v1/projects/{project_id}", 200, 1.5
    line = json.loads(JsonFormatter().format(rec))
    assert line["route"] == "/api/v1/projects/{project_id}" and line["request_id"] == "abc" and line["level"] == "info"


def test_metrics_need_a_token_and_use_route_templates(app, monkeypatch):
    monkeypatch.delenv("GC_METRICS_TOKEN", raising=False)  # a developer .env may set one
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        c.get(f"{P}/projects/{pid}")
        assert c.get("/metrics").status_code == 404  # off by default
        monkeypatch.setenv("GC_METRICS_TOKEN", "metrics-secret-token")
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        m = c.get("/metrics", headers={"Authorization": "Bearer metrics-secret-token"})
        assert m.status_code == 200 and "gc_http_requests_total" in m.text
        assert 'route="/api/v1/projects/{project_id}"' in m.text and f"/projects/{pid}\"" not in m.text
        assert "gc_http_request_duration_seconds_bucket" in m.text and "gc_uptime_seconds" in m.text


def test_unhandled_errors_are_reported_scrubbed_and_answered_with_an_id(app, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(errors.LogReporter, "capture", lambda self, exc, ctx: seen.append(errors.report_of(exc, ctx)))

    def boom(*_a, **_k):
        raise RuntimeError("failed for jane@example.com with token abcdefghijklmnopqrstuvwxyz0123456789")

    monkeypatch.setattr("app.services.guide_service.health", boom)
    with AuthedClient(app) as c:
        r = c.get(f"{P}/projects-health")
        assert r.status_code == 500 and r.json()["request_id"] == r.headers["X-Request-ID"]
        assert "jane" not in r.text
    assert seen and seen[0]["type"] == "RuntimeError"
    assert "[email]" in seen[0]["message"] and "[redacted]" in seen[0]["message"] and "jane" not in json.dumps(seen[0])
    assert seen[0]["context"]["route"] == "/api/v1/projects-health" and seen[0]["frames"]


def test_telemetry_is_off_by_default_and_contains_no_personal_data(app, monkeypatch, tmp_path):
    monkeypatch.delenv("GC_TELEMETRY", raising=False)
    with AuthedClient(app, email="private.person@example.com") as c:
        out = c.get(f"{P}/telemetry/preview").json()
        assert out["enabled"] is False and out["payload"]["schema"] == "green-computer.telemetry/1"
        text = json.dumps(out["payload"])
        assert "private.person" not in text and "example.com" not in text and "Example" not in text
        assert out["payload"]["counts"]["users"] >= 1
        assert out["payload"]["installation"] == c.get(f"{P}/telemetry/preview").json()["payload"]["installation"]
    assert telemetry.send(out["payload"]) is False
    monkeypatch.setenv("GC_TELEMETRY", "1")
    assert telemetry.enabled() is False  # still off without a destination
    with TestClient(app) as anon:
        assert anon.get(f"{P}/telemetry/preview").status_code == 401


def test_operator_scripts_run(tmp_path):
    env = {"GC_DB_URL": f"sqlite:///{tmp_path / 's.db'}", "GC_DATA_DIR": str(tmp_path / "d"), "PATH": ""}
    import os

    env = {**os.environ, **env}
    create_app(env["GC_DB_URL"], env["GC_DATA_DIR"])
    with TestClient(create_app(env["GC_DB_URL"], env["GC_DATA_DIR"])):
        pass
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "telemetry.py")], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and json.loads(r.stdout)["schema"] == "green-computer.telemetry/1"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "retention.py")], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0
