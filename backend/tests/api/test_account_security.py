"""Email verification, optional TOTP two-step sign-in, sealed secrets, shared rate limiting."""

from __future__ import annotations

import base64

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.auth import secret_box, totp
from app.data_loader import clear_custom_devices
from app.main import create_app
from app.security import LIMITER, DatabaseRateLimiter
from tests.helpers import TEST_PASSWORD, AuthedClient

P = "/api/v1"
H = {"X-GC-CSRF": "1"}


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'as.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def test_totp_matches_rfc6238_and_refuses_reuse():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"  # RFC 6238 test vector (SHA1, T=59), 6 digits
    assert totp.code_at(secret, 1111111109 // 30) == "081804"
    step = totp.verify(secret, "287082", None, now=59)
    assert step == 1 and totp.verify(secret, "287082", last_step=1, now=59) is None
    assert totp.verify(secret, "12345", None, now=59) is None
    assert totp.otpauth_uri(secret, "a@b.c").startswith("otpauth://totp/")
    codes = totp.recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10


def test_secret_box_detects_tampering_and_wrong_purpose(tmp_path):
    key = secret_box.server_key(tmp_path)
    assert (tmp_path / "server.key").exists() and secret_box.server_key(tmp_path) == key
    sealed = secret_box.seal(key, "p", b"top secret")
    assert b"top secret" not in sealed.encode() and secret_box.open_sealed(key, "p", sealed) == b"top secret"
    raw = bytearray(base64.urlsafe_b64decode(sealed))
    raw[-1] ^= 1
    with pytest.raises(ValueError):
        secret_box.open_sealed(key, "p", base64.urlsafe_b64encode(bytes(raw)).decode())
    with pytest.raises(ValueError):
        secret_box.open_sealed(key, "other", sealed)
    tok = secret_box.sign_token(key, "x", "1:2")
    assert secret_box.check_token(key, "x", tok) == "1:2" and secret_box.check_token(key, "y", tok) is None
    assert secret_box.check_token(key, "x", tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_email_verification_flow_and_optional_enforcement(app, monkeypatch):
    with TestClient(app) as c:
        r = c.post(f"{P}/auth/signup", headers=H, json={"accept_terms": True, "email": "v@x.io", "password": TEST_PASSWORD})
        assert r.status_code == 201
        body = r.json()
        assert body["user"]["email_verified"] is False and "dev_verify_url" in body
        token = body["dev_verify_url"].split("token=")[1]
        # Not enforced by default: an unverified account can work.
        assert c.post(f"{P}/projects", headers=H, json={"name": "a", "model_id": "resnet-50", "device_id": "jetson-orin-nano"}).status_code == 201
        monkeypatch.setenv("GC_REQUIRE_EMAIL_VERIFICATION", "1")
        blocked = c.post(f"{P}/projects", headers=H, json={"name": "b", "model_id": "resnet-50", "device_id": "jetson-orin-nano"})
        assert blocked.status_code == 403 and "Confirm your email" in blocked.json()["detail"]
        assert c.get(f"{P}/projects").status_code == 200  # reading still works
        resend = c.post(f"{P}/auth/email/resend", headers=H).json()
        assert resend["sent"] and resend["dev_verify_url"]
        assert c.post(f"{P}/auth/email/verify", headers=H, json={"token": "nope"}).status_code == 400
        assert c.post(f"{P}/auth/email/verify", headers=H, json={"token": token}).json()["verified"]
        assert c.post(f"{P}/auth/email/verify", headers=H, json={"token": token}).status_code == 400  # single use
        assert c.get(f"{P}/auth/me").json()["user"]["email_verified"] is True
        assert c.post(f"{P}/projects", headers=H, json={"name": "c", "model_id": "resnet-50", "device_id": "jetson-orin-nano"}).status_code == 201


def test_two_step_signin_with_codes_and_recovery(app):
    with AuthedClient(app, email="m@x.io") as c:
        setup = c.post(f"{P}/auth/mfa/setup").json()
        secret = setup["secret"]
        assert setup["otpauth_uri"].startswith("otpauth://totp/")
        with app.state.engine.connect() as conn:
            stored = conn.execute(sa.text("SELECT mfa_secret FROM users WHERE email = 'm@x.io'")).scalar()
        assert stored and secret not in stored  # sealed, not plain
        assert c.post(f"{P}/auth/mfa/enable", json={"code": "000000" if totp.code_at(secret, totp.current_step()) != "000000" else "111111"}).status_code == 422
        en = c.post(f"{P}/auth/mfa/enable", json={"code": totp.code_at(secret, totp.current_step())}).json()
        codes = en["recovery_codes"]
        assert en["enabled"] and len(codes) == 10
        assert c.get(f"{P}/auth/me").json()["user"]["mfa_enabled"] is True
    with TestClient(app) as anon:
        first = anon.post(f"{P}/auth/signin", headers=H, json={"email": "m@x.io", "password": TEST_PASSWORD}).json()
        assert first["mfa_required"] and "user" not in first
        assert anon.get(f"{P}/auth/me").status_code == 401  # no session yet
        tok = first["mfa_token"]
        assert anon.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok, "code": "123456"}).status_code == 401
        assert anon.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok + "x", "code": codes[0]}).status_code == 401
        ok = anon.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok, "code": codes[0]})
        assert ok.status_code == 200 and ok.json()["user"]["email"] == "m@x.io"
        assert anon.get(f"{P}/auth/me").status_code == 200
    with TestClient(app) as again:
        tok = again.post(f"{P}/auth/signin", headers=H, json={"email": "m@x.io", "password": TEST_PASSWORD}).json()["mfa_token"]
        assert again.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok, "code": codes[0]}).status_code == 401  # used
        nxt = totp.code_at(secret, totp.current_step() + 1)  # newer than the step used to enable
        assert again.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok, "code": nxt}).status_code == 200
        assert again.post(f"{P}/auth/signin/mfa", headers=H, json={"mfa_token": tok, "code": nxt}).status_code == 401  # replay
        assert again.post(f"{P}/auth/mfa/disable", headers=H, json={"password": "wrong password!", "code": codes[1]}).status_code == 401
        assert again.post(f"{P}/auth/mfa/disable", headers=H, json={"password": TEST_PASSWORD, "code": codes[1]}).json()["enabled"] is False
    with TestClient(app) as plain:
        assert "user" in plain.post(f"{P}/auth/signin", headers=H, json={"email": "m@x.io", "password": TEST_PASSWORD}).json()


def test_api_tokens_cannot_enrol_mfa(app):
    with AuthedClient(app, email="t@x.io") as c:
        tok = c.post(f"{P}/auth/tokens", json={"label": "ci", "days": 1}).json()["token"]
    with TestClient(app) as bearer:
        r = bearer.post(f"{P}/auth/mfa/setup", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403


def test_database_rate_limiter_is_shared_between_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_RATE_LIMIT_BACKEND", "database")
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    a = create_app(url, data_dir=str(tmp_path / "d1"))
    b = create_app(url, data_dir=str(tmp_path / "d2"))  # a second worker on the same database
    try:
        with TestClient(a), TestClient(b):  # start-up runs the migrations
            pass
        assert a.state.rate_limit_backend == "database" and LIMITER.kind == "database"
        la, lb = DatabaseRateLimiter(a.state.engine), DatabaseRateLimiter(b.state.engine)
        allowed = [la.check("k", 5, 60) is None for _ in range(3)] + [lb.check("k", 5, 60) is None for _ in range(2)]
        assert all(allowed)
        assert lb.check("k", 5, 60) is not None and la.check("k", 5, 60) is not None
        assert la.check("other", 5, 60) is None
        la.reset()
        assert lb.check("k", 5, 60) is None
    finally:
        monkeypatch.setenv("GC_RATE_LIMIT_BACKEND", "memory")
        create_app(f"sqlite:///{tmp_path / 'reset.db'}", data_dir=str(tmp_path / "d3")).state.engine.dispose()
        a.state.engine.dispose()
        b.state.engine.dispose()
    assert LIMITER.kind == "memory"


def test_account_deletion_removes_verification_rows(app):
    with TestClient(app) as c:
        c.post(f"{P}/auth/signup", headers=H, json={"accept_terms": True, "email": "d@x.io", "password": TEST_PASSWORD})
        assert c.post(f"{P}/auth/account/delete", headers=H, json={"password": TEST_PASSWORD, "confirm": "DELETE"}).status_code == 200
    with app.state.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM email_verifications")).scalar() == 0
