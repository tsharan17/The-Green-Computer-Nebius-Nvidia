"""Start the isolated worker (app/execution/worker.py) and collect its JSON answer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]


def run_worker(args: list[str], timeout_s: float) -> tuple[dict[str, Any], float]:
    """(result, wall seconds). Never raises for worker failures: they come back as ok=False."""
    t0 = time.perf_counter()
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    try:
        p = subprocess.run(
            [sys.executable, "-m", "app.execution.worker", *args], cwd=str(BACKEND_DIR), env=env,
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"stopped after the {timeout_s:.0f} s time limit"}, time.perf_counter() - t0
    wall = time.perf_counter() - t0
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        out = {}
    if not out:
        out = {"ok": False, "error": (p.stderr or "the worker produced no result").strip()[-800:]}
    return out, wall
