"""Email verification and optional TOTP two-step sign-in.

Email verification is always offered; it is enforced for changing data only when
GC_REQUIRE_EMAIL_VERIFICATION is on (off by default, so self-hosted installs without mail keep
working). TOTP is optional per account; recovery codes are shown once and stored as hashes.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import secret_box, totp
from app.auth.passwords import verify_password
from app.auth.service import AuthError, token_hash
from app.db.models import EmailVerificationRow, UserRow
from app.providers.email import EmailSender

VERIFY_TTL = timedelta(hours=48)
MFA_CHALLENGE_TTL = timedelta(minutes=5)
SEAL_PURPOSE = "mfa-secret"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def verification_required() -> bool:
    return os.environ.get("GC_REQUIRE_EMAIL_VERIFICATION", "").lower() in ("1", "true", "yes", "on")


# --- email verification ------------------------------------------------------------------------


def send_verification(db: Session, user: UserRow, sender: EmailSender, public_url: str) -> str:
    """A new single-use link valid for 48 hours. Returns the token (shown only in development)."""
    token = secrets.token_urlsafe(32)
    db.add(EmailVerificationRow(user_id=user.id, email=user.email, token_hash=token_hash(token), expires_at=_now() + VERIFY_TTL))
    db.commit()
    sender.send(user.email, "Confirm your email for The Green Computer",
                "Confirm that this address belongs to you by opening this link within 48 hours:\n"
                f"{public_url}/#/verify-email?token={token}\n\nIf you did not create an account, ignore this email.")  # fmt: skip
    return token


def confirm_email(db: Session, token: str) -> UserRow:
    row = db.scalars(select(EmailVerificationRow).where(EmailVerificationRow.token_hash == token_hash(token or ""))).first()
    if row is None or row.used_at is not None or _aware(row.expires_at) <= _now():
        raise AuthError(400, "This confirmation link is invalid or has expired. Ask for a new one.")
    user = db.get(UserRow, row.user_id)
    if user is None or user.email != row.email:
        raise AuthError(400, "This confirmation link no longer matches the account's email.")
    user.email_verified_at = _now()
    row.used_at = _now()
    db.commit()
    return user


# --- TOTP --------------------------------------------------------------------------------------


def mfa_enabled(user: UserRow) -> bool:
    return user.mfa_enabled_at is not None and bool(user.mfa_secret)


def mfa_setup(db: Session, user: UserRow, key: bytes) -> dict[str, Any]:
    """Start enrolment: a new secret, pending until a code from the app confirms it."""
    if mfa_enabled(user):
        raise AuthError(409, "Two-step sign-in is already on. Turn it off first to enrol a new device.")
    secret = totp.new_secret()
    user.mfa_secret = secret_box.seal(key, SEAL_PURPOSE, secret.encode())
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, user.email)}


def _secret(user: UserRow, key: bytes) -> str:
    if not user.mfa_secret:
        raise AuthError(409, "Start the set-up first.")
    try:
        return secret_box.open_sealed(key, SEAL_PURPOSE, user.mfa_secret).decode()
    except ValueError as e:
        raise AuthError(500, "The stored authenticator secret cannot be read with this server's key.") from e


def mfa_enable(db: Session, user: UserRow, key: bytes, code: str) -> list[str]:
    if mfa_enabled(user):
        raise AuthError(409, "Two-step sign-in is already on.")
    step = totp.verify(_secret(user, key), code)
    if step is None:
        raise AuthError(422, "That code does not match. Check the time on your phone and try the newest code.")
    codes = totp.recovery_codes()
    user.mfa_recovery = [token_hash(c) for c in codes]
    user.mfa_enabled_at = _now()
    user.mfa_last_step = step
    db.commit()
    return codes


def check_second_factor(db: Session, user: UserRow, key: bytes, code: str) -> str:
    """'totp' or 'recovery' when the code is good (a recovery code works once), else AuthError."""
    code = (code or "").strip().lower()
    if "-" in code:
        h = token_hash(code)
        if h in (user.mfa_recovery or []):
            user.mfa_recovery = [x for x in user.mfa_recovery or [] if x != h]
            db.commit()
            return "recovery"
        raise AuthError(401, "That recovery code is not valid (each works once).")
    step = totp.verify(_secret(user, key), code, user.mfa_last_step)
    if step is None:
        raise AuthError(401, "That code is not valid. Use the newest code from your authenticator app.")
    user.mfa_last_step = step
    db.commit()
    return "totp"


def mfa_disable(db: Session, user: UserRow, key: bytes, password: str, code: str) -> None:
    if not verify_password(password, user.password_hash):
        raise AuthError(401, "Password is incorrect.")
    if mfa_enabled(user):
        check_second_factor(db, user, key, code)
    user.mfa_secret = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_recovery = None
    db.commit()


def challenge_token(key: bytes, user: UserRow) -> str:
    exp = int((_now() + MFA_CHALLENGE_TTL).timestamp())
    return secret_box.sign_token(key, "mfa-challenge", f"{user.id}:{exp}:{_aware(user.password_changed_at).timestamp():.0f}")


def challenge_user(db: Session, key: bytes, token: str) -> UserRow:
    payload = secret_box.check_token(key, "mfa-challenge", token)
    bad = AuthError(401, "The sign-in step expired. Sign in again.")
    if payload is None:
        raise bad
    uid, exp, pwd = payload.split(":")
    if int(exp) < int(_now().timestamp()):
        raise bad
    user = db.get(UserRow, int(uid))
    if user is None or not user.is_active or f"{_aware(user.password_changed_at).timestamp():.0f}" != pwd:
        raise bad
    return user


def key_for(data_dir: Path) -> bytes:
    return secret_box.server_key(data_dir)
