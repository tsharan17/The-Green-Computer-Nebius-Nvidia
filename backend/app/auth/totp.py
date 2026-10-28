"""Time-based one-time passwords (RFC 6238: HMAC-SHA1, 6 digits, 30-second steps). Pure."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

STEP = 30
DIGITS = 6
WINDOW = 1  # accept one step either side for clock drift


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    pad = "=" * (-len(secret_b32) % 8)
    return base64.b32decode(secret_b32.upper() + pad)


def code_at(secret_b32: str, step: int) -> str:
    mac = hmac.new(_key(secret_b32), struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[o : o + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return str(n).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP)


def verify(secret_b32: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """The matching step if `code` is valid and newer than `last_step` (no reuse), else None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    s = current_step(now)
    for step in range(s - WINDOW, s + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret_b32, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, account: str, issuer: str = "The Green Computer") -> str:
    label = urllib.parse.quote(f"{issuer}:{account}")
    q = urllib.parse.urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{q}"


def recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2)) for _ in range(n)]
