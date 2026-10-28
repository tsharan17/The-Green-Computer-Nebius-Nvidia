"""Password hashing with scrypt (Python standard library; no third-party crypto dependency).

Stored format: ``scrypt$<log2 n>$<r>$<p>$<salt b64>$<hash b64>`` so parameters can be raised later
and old hashes still verify (and are re-hashed on the next sign-in).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

LOG2_N = int(os.environ.get("GC_SCRYPT_LOG2_N", "14"))  # 16 MiB with r=8
R, P = 8, 1
DKLEN = 32
MIN_LENGTH = 10
MAX_LENGTH = 256

# A short list of passwords that pass the length rule but are guessed first.
_COMMON = {
    "password123", "password1234", "1234567890", "qwertyuiop", "letmein123", "iloveyou12",
    "administrator", "changeme123", "welcome123", "passw0rd123", "qwerty12345", "0987654321",
}  # fmt: skip


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _scrypt(password: str, salt: bytes, log2n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**log2n, r=r, p=p, dklen=DKLEN,
        maxmem=256 * 1024 * 1024,
    )  # fmt: skip


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = _scrypt(password, salt, LOG2_N, R, P)
    return f"scrypt${LOG2_N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, log2n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = _scrypt(password, base64.b64decode(salt), int(log2n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        _, log2n, r, p, *_ = stored.split("$")
        return (int(log2n), int(r), int(p)) != (LOG2_N, R, P)
    except ValueError:
        return True


def password_problem(password: str, email: str = "") -> str | None:
    """Why a new password is not acceptable, or None."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if password.lower() in _COMMON or len(set(password)) < 4:
        return "This password is too easy to guess; choose a less common one."
    if email and password.lower() == email.lower():
        return "Do not use your email address as the password."
    return None
