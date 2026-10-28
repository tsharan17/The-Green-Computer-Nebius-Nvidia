"""The device-agent protocol, `green-computer.agent/1`. Pure functions, no I/O.

The agent polls the server over HTTPS; the server never connects to a device.

Pairing   A workspace editor registers a device and gets a one-time pairing code (15 minutes).
          The agent exchanges it for a device token (sent as `Authorization: Device <token>`,
          stored only as a SHA-256 hash) and a per-device signing key.
Signing   Every run request the server issues, and every result the agent returns, is a JSON
          payload with an HMAC-SHA256 signature over its canonical form (sorted keys, no spaces,
          UTF-8). The per-device key is HMAC(server secret, "device:<id>:<key version>"), so the
          server can recompute it without storing it; re-pairing bumps the key version.
Requests  carry a nonce, an expiry, one action from a fixed allow-list ("benchmark_onnx"), bounded
          parameters, and the approved artifact's SHA-256 and size. The agent refuses anything
          else and never runs a shell or arbitrary code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any

PROTOCOL = "green-computer.agent/1"
ACTIONS = {"benchmark_onnx"}
LIMITS = {"batch": (1, 64), "warmup": (0, 200), "runs": (1, 5000), "max_seconds": (1, 1800)}
MAX_SAMPLES = 5000


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def device_key(server_secret: bytes, remote_device_id: int, key_version: int) -> bytes:
    return hmac.new(server_secret, f"device:{remote_device_id}:{key_version}".encode(), hashlib.sha256).digest()


def sign(key: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()


def verify(key: bytes, payload: dict[str, Any], signature: str) -> bool:
    return hmac.compare_digest(sign(key, payload), signature or "")


def check_params(params: dict[str, Any]) -> list[str]:
    """Bounds every run request must respect (the agent applies the same or stricter ones)."""
    errs = []
    for k, (lo, hi) in LIMITS.items():
        v = params.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            errs.append(f"{k} must be an integer from {lo} to {hi}")
    if params.get("precision") not in ("fp32", "fp16", "int8"):
        errs.append("precision must be fp32, fp16 or int8")
    shape = params.get("input_shape")
    if shape is not None and (not isinstance(shape, list) or not 1 <= len(shape) <= 8
                              or not all(isinstance(d, int) and 1 <= d <= 65536 for d in shape)):  # fmt: skip
        errs.append("input_shape must be 1 to 8 positive integers")
    return errs


def check_result(result: dict[str, Any], job_payload: dict[str, Any]) -> list[str]:
    """Structural checks on a result before it is trusted (the signature is checked separately)."""
    errs = []
    if result.get("protocol") != PROTOCOL:
        errs.append("unknown protocol")
    if result.get("job_id") != job_payload.get("job_id") or result.get("nonce") != job_payload.get("nonce"):
        errs.append("the result does not answer this request (job id or nonce differ)")
    if result.get("mode") not in ("measured", "simulated"):
        errs.append("mode must be measured or simulated")
    if result.get("status") not in ("succeeded", "failed", "rejected"):
        errs.append("status must be succeeded, failed or rejected")
    if result.get("status") == "succeeded":
        if result.get("artifact_sha256") != job_payload.get("artifact", {}).get("sha256"):
            errs.append("the agent ran a file whose SHA-256 differs from the approved artifact")
        lat = result.get("latencies_ms")
        if not isinstance(lat, list) or not 1 <= len(lat) <= MAX_SAMPLES:
            errs.append(f"latencies_ms must hold 1 to {MAX_SAMPLES} samples")
        elif not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 < x < 3.6e6 for x in lat):
            errs.append("latencies_ms must be positive, finite milliseconds")
        for k in ("temperatures_c", "power_w", "warmup_ms"):
            v = result.get(k)
            if v is not None and (not isinstance(v, list) or len(v) > MAX_SAMPLES
                                  or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)):  # fmt: skip
                errs.append(f"{k} must be a list of numbers")
    return errs
