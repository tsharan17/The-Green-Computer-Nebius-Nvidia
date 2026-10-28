"""Security checks that cover the whole API surface, so a new route cannot ship unprotected."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"
# The only routes that answer without a session: health, the sign-in/up/reset/verify flow itself, and the
# legal documents (they must be readable before signing up).
PUBLIC = {
    ("GET", "/api/v1/health"), ("GET", "/api/v1/ready"), ("GET", "/api/v1/auth/config"), ("POST", "/api/v1/auth/signup"),
    ("POST", "/api/v1/auth/signin"), ("POST", "/api/v1/auth/password/forgot"),
    ("POST", "/api/v1/auth/password/reset"), ("POST", "/api/v1/auth/signin/mfa"), ("POST", "/api/v1/auth/email/verify"),
    ("GET", "/api/v1/auth/legal"), ("GET", "/api/v1/auth/legal/{doc}"),
}  # fmt: skip


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 's.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()


def _routes(app):
    """Every API operation, from the OpenAPI schema (it sees routes inside included routers)."""
    for path, ops in app.openapi()["paths"].items():
        if path.startswith(P):
            for m in ops:
                if m.upper() not in ("HEAD", "OPTIONS"):
                    yield m.upper(), path


def test_every_api_route_requires_a_session_except_the_public_ones(app):
    routes = sorted(set(_routes(app)))
    assert len(routes) > 60
    with TestClient(app) as c:
        for method, path in routes:
            if (method, path) in PUBLIC:
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = c.request(method, url, headers={"X-GC-CSRF": "1"}, json={} if method != "GET" else None)
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a session"


def test_state_changing_routes_refuse_cookie_requests_without_csrf_header(app):
    with AuthedClient(app) as c:
        del c.headers["X-GC-CSRF"]
        for method, path in sorted(set(_routes(app))):
            if method == "GET" or (method, path) in PUBLIC or path.endswith("/signout"):
                continue
            if path.startswith(f"{P}/device-agent/"):
                continue  # device agents authenticate with a device token, never a cookie (checked below)
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = c.request(method, url, json={})
            assert r.status_code == 403, f"{method} {path} answered {r.status_code} without the CSRF header"


def test_security_headers_and_cookie_flags(app):
    with TestClient(app) as c:
        r = c.post(f"{P}/auth/signup", json={"accept_terms": True, "email": "h@x.io", "password": "a long test password"},
                   headers={"X-GC-CSRF": "1"})  # fmt: skip
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        h = c.get(f"{P}/health").headers
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert "object-src 'none'" in h["content-security-policy"]


def test_hostile_names_are_escaped_and_download_names_are_safe(app):
    with AuthedClient(app) as c:
        pid = c.get(f"{P}/projects").json()[0]["id"]
        spec = c.get(f"{P}/projects/{pid}").json()["spec"] | {"name": '<script>alert(1)</script>"; evil'}
        c.put(f"{P}/projects/{pid}", json=spec)
        c.post(f"{P}/projects/{pid}/optimise")
        html = c.get(f"{P}/projects/{pid}/report?format=html").text
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
        ex = c.post(f"{P}/projects/{pid}/exports", json={"kind": "report_md"}).json()
        assert re.fullmatch(r"[a-z0-9.-]+", ex["filename"])
        up = c.post(f"{P}/intake/upload?filename=..%2F..%2Fevil%22.csv", content=b"time_s,power_w\n0,1\n1,2\n",
                    headers={"Content-Type": "application/octet-stream"}).json()  # fmt: skip
        art = c.get(f"{P}/artifacts/{up['artifact_id']}").json()
        assert "/" not in art["filename"] and '"' not in art["filename"]
        d = c.get(f"{P}/artifacts/{up['artifact_id']}/download")
        assert d.headers["content-disposition"].count('"') == 2


def test_unknown_workspace_header_and_bad_tokens_are_refused(app):
    with AuthedClient(app) as c:
        assert c.get(f"{P}/projects", headers={"X-GC-Workspace": "999"}).status_code == 403
        assert c.get(f"{P}/projects", headers={"X-GC-Workspace": "abc"}).status_code == 400
    with TestClient(app) as c:
        assert c.get(f"{P}/projects", headers={"Authorization": "Bearer " + "x" * 300}).status_code == 401


def test_device_agent_routes_ignore_browser_sessions(app):
    """A signed-in browser cannot act as a device: the agent side only accepts a device token."""
    with AuthedClient(app) as c:
        for method, path in sorted(set(_routes(app))):
            if path.startswith(f"{P}/device-agent/"):
                url = re.sub(r"\{[^}]+\}", "1", path)
                r = c.request(method, url, json={})
                assert r.status_code == 401, f"{method} {path} answered {r.status_code} to a browser session"



def test_public_pre_login_routes_still_need_the_csrf_header(app):
    with TestClient(app) as c:
        for path in ("/api/v1/auth/signin/mfa", "/api/v1/auth/email/verify"):
            assert c.post(path, json={"mfa_token": "x", "code": "1", "token": "x"}).status_code == 403, path
