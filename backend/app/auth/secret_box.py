"""Small secrets at rest (TOTP seeds) and short signed tokens, with the standard library only.

Key: `GC_SECRET_KEY` if set, else a random 32-byte key created once in the data directory
(`server.key`, owner-only). It is never in source. Back it up with the data: without it, enrolled
authenticators must be set up again.

Sealing is encrypt-then-MAC: a fresh 16-byte nonce, a keystream from HMAC-SHA256(enc_key, nonce ||
counter) XORed with the plaintext (HMAC as a PRF in counter mode), then HMAC-SHA256(mac_key, purpose
|| nonce || ciphertext). The encryption and MAC keys are derived separately from the server key, and
the purpose string binds each sealed value to its use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

_KEY_CACHE: dict[str, bytes] = {}


def server_key(data_dir: Path) -> bytes:
    env = os.environ.get("GC_SECRET_KEY")
    if env:
        return hashlib.sha256(env.encode("utf-8")).digest()
    path = Path(data_dir) / "server.key"
    cached = _KEY_CACHE.get(str(path))
    if cached:
        return cached
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows permissions
            pass
    key = path.read_bytes()
    _KEY_CACHE[str(path)] = key
    return key


def _sub(key: bytes, label: str) -> bytes:
    return hmac.new(key, f"green-computer:{label}".encode(), hashlib.sha256).digest()


def _stream(k: bytes, nonce: bytes, n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hmac.new(k, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def seal(key: bytes, purpose: str, plaintext: bytes) -> str:
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(plaintext, _stream(_sub(key, "enc"), nonce, len(plaintext)), strict=True))
    tag = hmac.new(_sub(key, "mac"), purpose.encode() + nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(b"\x01" + nonce + tag + ct).decode()


def open_sealed(key: bytes, purpose: str, sealed: str) -> bytes:
    raw = base64.urlsafe_b64decode(sealed.encode())
    if len(raw) < 49 or raw[0] != 1:
        raise ValueError("not a sealed value")
    nonce, tag, ct = raw[1:17], raw[17:49], raw[49:]
    want = hmac.new(_sub(key, "mac"), purpose.encode() + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, want):
        raise ValueError("sealed value was altered or sealed with another key")
    return bytes(a ^ b for a, b in zip(ct, _stream(_sub(key, "enc"), nonce, len(ct)), strict=True))


def sign_token(key: bytes, purpose: str, payload: str) -> str:
    sig = hmac.new(_sub(key, f"token:{purpose}"), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def check_token(key: bytes, purpose: str, token: str) -> str | None:
    payload, _, sig = (token or "").rpartition(".")
    if not payload or not sig:
        return None
    want = hmac.new(_sub(key, f"token:{purpose}"), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return payload if hmac.compare_digest(sig, want) else None
