#!/usr/bin/env python3
"""The Green Computer device agent (protocol green-computer.agent/1).

Runs on the device you want to measure. It polls your Green Computer server over HTTPS, accepts only
run requests signed for this device, checks every request against a local policy, downloads only the
approved ONNX file (checked by SHA-256), times it with ONNX Runtime and sends back a signed result.

It never opens a port, never runs a shell or any program, and never executes anything except
ONNX Runtime inference on the approved file. Standard library only; ONNX Runtime and NumPy are needed
for real runs, not for --simulate.

    python gc_agent.py pair --server https://gc.example.com --code ABCD-EFGH-JKLM
    python gc_agent.py run                 # poll forever
    python gc_agent.py run --once          # handle at most one request, then exit
    python gc_agent.py run --simulate      # fake timings, labelled "simulated", never imported

See docs/remote-agent.md.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import random
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROTOCOL = "green-computer.agent/1"
VERSION = "1.0.0"
DEFAULT_POLICY = {
    "allowed_actions": ["benchmark_onnx"],
    "max_runs": 2000,
    "max_seconds": 600,
    "max_batch": 32,
    "max_artifact_mb": 200,
}
Transport = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]]


class Refused(Exception):
    """A request the agent will not run; the reason is reported back to the server."""


# --- signing (same canonical form as the server) ---------------------------------------------


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(key: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()


# --- configuration -----------------------------------------------------------------------------


def config_dir(path: str | None) -> Path:
    return Path(path) if path else Path(os.environ.get("GC_AGENT_HOME", Path.home() / ".green-computer-agent"))


def load_config(d: Path) -> dict[str, Any]:
    f = d / "config.json"
    if not f.exists():
        raise SystemExit(f"Not paired yet: run 'pair' first (looked in {f}).")
    return json.loads(f.read_text(encoding="utf-8"))


def save_private(f: Path, data: dict[str, Any] | list[str]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        f.chmod(0o600)
    except OSError:  # pragma: no cover - Windows
        pass


def agent_info() -> dict[str, str]:
    info = {"hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(), "python": platform.python_version(), "agent": VERSION}  # fmt: skip
    try:
        import onnxruntime  # type: ignore[import-not-found]

        info["onnxruntime"] = onnxruntime.__version__
    except ImportError:
        info["onnxruntime"] = "not installed"
    return info


# --- transport -----------------------------------------------------------------------------------


def http_transport(server: str, allow_http: bool = False) -> Transport:
    parsed = urllib.parse.urlparse(server)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (local or allow_http):
        raise SystemExit("Use an https:// server address (plain http is only allowed for localhost or with --allow-http).")

    def send(method: str, path: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        req = urllib.request.Request(server.rstrip("/") + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - scheme checked above
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    return send


class Agent:
    def __init__(self, cfg: dict[str, Any], transport: Transport, home: Path, simulate: bool = False) -> None:
        self.cfg, self.send, self.home, self.simulate = cfg, transport, home, simulate
        self.key = bytes.fromhex(cfg["signing_key"])
        self.policy = {**DEFAULT_POLICY, **cfg.get("policy", {})}

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        headers = {"Authorization": f"Device {self.cfg['device_token']}", "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self.send(method, "/api/v1/device-agent" + path, body, headers)
        if path.endswith("/artifact"):
            return status, raw
        return status, (json.loads(raw) if raw else None)

    # --- request checks ---
    def _seen(self) -> list[str]:
        f = self.home / "seen_nonces.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def _remember(self, nonce: str) -> None:
        save_private(self.home / "seen_nonces.json", [*self._seen(), nonce][-2000:])

    def check(self, job: dict[str, Any], signature: str) -> None:
        if not hmac.compare_digest(sign(self.key, job), signature or ""):
            raise Refused("signature does not verify for this device")
        if job.get("protocol") != PROTOCOL:
            raise Refused("unknown protocol")
        if job.get("remote_device_id") != self.cfg["remote_device_id"]:
            raise Refused("request addressed to another device")
        if job.get("action") not in self.policy["allowed_actions"]:
            raise Refused(f"action {job.get('action')!r} is not allowed by this device's policy")
        exp = datetime.fromisoformat(job["expires_at"])
        if exp < datetime.now(UTC):
            raise Refused("request expired")
        if job.get("nonce") in self._seen():
            raise Refused("request already seen (replay)")
        p = job.get("params") or {}
        if p.get("runs", 0) > self.policy["max_runs"] or p.get("max_seconds", 0) > self.policy["max_seconds"]:
            raise Refused("more runs or time than this device's policy allows")
        if p.get("batch", 1) > self.policy["max_batch"]:
            raise Refused("batch larger than this device's policy allows")
        if job.get("artifact", {}).get("size_bytes", 0) > self.policy["max_artifact_mb"] * 1024 * 1024:
            raise Refused("file larger than this device's policy allows")

    # --- one cycle ---
    def heartbeat(self) -> None:
        self._call("POST", "/heartbeat", {"agent": agent_info()})

    def handle_next(self) -> dict[str, Any] | None:
        status, env = self._call("GET", "/jobs/next")
        if status == 204 or not env:
            return None
        if status != 200:
            raise SystemExit(f"Server answered {status}: {env}")
        job, signature = env["job"], env["signature"]
        base = {"protocol": PROTOCOL, "job_id": job.get("job_id"), "nonce": job.get("nonce"),
                "mode": "simulated" if self.simulate else "measured", "environment": agent_info()}  # fmt: skip
        try:
            self.check(job, signature)
            self._remember(job["nonce"])
            st, data = self._call("GET", f"/jobs/{job['job_id']}/artifact")
            if st != 200:
                raise Refused(f"could not download the approved file ({st})")
            digest = hashlib.sha256(data).hexdigest()
            if digest != job["artifact"]["sha256"]:
                raise Refused("downloaded file does not match the approved SHA-256")
            result = {**base, "status": "succeeded", "artifact_sha256": digest,
                      **(simulate_run(data, job["params"]) if self.simulate else benchmark(data, job["params"]))}  # fmt: skip
        except Refused as e:
            result = {**base, "status": "rejected", "error": str(e)}
        except Exception as e:  # noqa: BLE001 - any runtime failure is reported, never hidden
            result = {**base, "status": "failed", "error": f"{type(e).__name__}: {e}"[:900]}
        _, answer = self._call("POST", f"/jobs/{job['job_id']}/result", {"result": result, "signature": sign(self.key, result)})
        return answer


# --- measurement -----------------------------------------------------------------------------------


def _temps() -> float | None:
    """Highest thermal-zone temperature on Linux, if readable; None elsewhere (never guessed)."""
    zones = Path("/sys/class/thermal")
    vals = []
    for z in zones.glob("thermal_zone*/temp") if zones.exists() else []:
        try:
            vals.append(int(z.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return max(vals) if vals else None


def benchmark(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np  # type: ignore[import-not-found]
    import onnxruntime as ort  # type: ignore[import-not-found]

    notes: list[str] = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.onnx"
        path.write_bytes(data)
        try:
            sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        except Exception as e:  # noqa: BLE001
            # A newer exporter can stamp an IR version this ONNX Runtime does not know; lowering the
            # header field (when the onnx package is installed) is reported, never silent.
            import re

            m = re.search(r"Unsupported model IR version: (\d+), max supported IR version: (\d+)", str(e))
            if not m:
                raise
            try:
                import onnx  # type: ignore[import-not-found]
            except ImportError as ie:
                raise RuntimeError(f"{e}. Install the onnx package so the agent can lower the IR version, or re-export the model.") from ie
            mp = onnx.load_from_string(data)
            mp.ir_version = int(m.group(2))
            path.write_bytes(mp.SerializeToString())
            notes.append(f"IR version lowered from {m.group(1)} to {m.group(2)} for this ONNX Runtime; the graph is unchanged")
            sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        rng = np.random.default_rng(0)
        feeds = {}
        for inp in sess.get_inputs():
            shape = params.get("input_shape") or [d if isinstance(d, int) and d > 0 else 1 for d in inp.shape]
            shape = [params.get("batch", 1), *shape[1:]] if shape else shape
            dtype = np.int64 if "int64" in inp.type else np.float32
            feeds[inp.name] = rng.standard_normal(shape).astype(dtype) if dtype == np.float32 else np.zeros(shape, dtype)
        warm, lat, temps = [], [], []
        for _ in range(params.get("warmup", 10)):
            t0 = time.perf_counter_ns()
            sess.run(None, feeds)
            warm.append((time.perf_counter_ns() - t0) / 1e6)
        start = time.perf_counter()
        for i in range(params.get("runs", 100)):
            if i % 10 == 0 and (t := _temps()) is not None:
                temps.append(t)
            t0 = time.perf_counter_ns()
            sess.run(None, feeds)
            lat.append((time.perf_counter_ns() - t0) / 1e6)
            if time.perf_counter() - start > params.get("max_seconds", 120):
                break
        wall = time.perf_counter() - start
    return {"latencies_ms": lat, "warmup_ms": warm, "timer_resolution_ns": time.get_clock_info("perf_counter").resolution * 1e9,
            "wall_seconds": wall, "temperatures_c": temps or None, "power_w": None,
            "started_at": datetime.now(UTC).isoformat(), "provider": "CPUExecutionProvider", "notes": notes}  # fmt: skip


def simulate_run(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fake timings for testing a set-up without hardware. Always labelled simulated."""
    rng = random.Random(hashlib.sha256(data).hexdigest())
    base = 2.0 + len(data) / 1e6 * 0.5 * params.get("batch", 1)
    lat = [base * (1 + rng.gauss(0, 0.02)) for _ in range(params.get("runs", 100))]
    return {"latencies_ms": lat, "warmup_ms": [base * 3] * params.get("warmup", 10), "timer_resolution_ns": 100.0,
            "wall_seconds": sum(lat) / 1000.0, "temperatures_c": None, "power_w": None,
            "started_at": datetime.now(UTC).isoformat(), "provider": "simulator"}  # fmt: skip


# --- command line ---------------------------------------------------------------------------------


def pair(server: str, code: str, home: Path, transport: Transport) -> dict[str, Any]:
    body = json.dumps({"code": code, "agent": agent_info()}).encode("utf-8")
    status, raw = transport("POST", "/api/v1/device-agent/pair", body, {"Content-Type": "application/json"})
    if status != 200:
        raise SystemExit(f"Pairing failed ({status}): {raw.decode('utf-8', 'replace')}")
    answer = json.loads(raw)
    cfg = {"server": server, "remote_device_id": answer["remote_device_id"], "device_token": answer["device_token"],
           "signing_key": answer["signing_key"], "key_version": answer["key_version"],
           "hardware_profile": answer["hardware_profile"], "policy": DEFAULT_POLICY}  # fmt: skip
    save_private(home / "config.json", cfg)
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The Green Computer device agent")
    ap.add_argument("--home", help="where the agent keeps its config (default ~/.green-computer-agent)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("--server", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--allow-http", action="store_true", help="allow plain http to a non-local server (not recommended)")
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    r.add_argument("--interval", type=float, default=10.0)
    r.add_argument("--simulate", action="store_true", help="fake timings, labelled simulated")
    r.add_argument("--allow-http", action="store_true")
    a = ap.parse_args(argv)
    home = config_dir(a.home)
    if a.cmd == "pair":
        cfg = pair(a.server, a.code, home, http_transport(a.server, a.allow_http))
        print(f"Paired as device {cfg['remote_device_id']} ({cfg['hardware_profile']}). Config: {home / 'config.json'}")
        return 0
    cfg = load_config(home)
    agent = Agent(cfg, http_transport(cfg["server"], a.allow_http), home, simulate=a.simulate)
    while True:
        agent.heartbeat()
        out = agent.handle_next()
        if out is not None:
            print(json.dumps({k: out.get(k) for k in ("id", "status", "mode", "error")}))
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
