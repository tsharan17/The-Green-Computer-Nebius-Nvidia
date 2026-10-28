"""Is a set of timing samples good enough to count as a measurement? Pure.

Each check passes, warns or fails, with a plain reason. The verdict:
  good    every check passes
  usable  some checks warn; the result is imported and the warnings travel with it
  poor    a check fails; the result is kept for inspection but not imported as a measurement
"""

from __future__ import annotations

import statistics
from typing import Any

MIN_RUNS_GOOD = 20
MIN_RUNS = 5
CV_GOOD, CV_OK = 0.05, 0.15
DRIFT_WARN = 0.10
OUTLIER_WARN = 0.05
TEMP_RISE_WARN = 5.0
SHORT_RUN_S = 5.0


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    i = (len(s) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def stats(xs: list[float]) -> dict[str, float]:
    mean = statistics.fmean(xs)
    sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": mean, "std": sd, "cv": sd / mean if mean else 0.0, "min": min(xs), "max": max(xs),
            "p50": _pct(xs, 0.5), "p95": _pct(xs, 0.95), "p99": _pct(xs, 0.99)}  # fmt: skip


def assess(
    latencies_ms: list[float],
    warmup_ms: list[float] | None = None,
    timer_resolution_ns: float | None = None,
    temperatures_c: list[float] | None = None,
    wall_seconds: float | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(cid: str, status: str, detail: str) -> None:
        checks.append({"id": cid, "status": status, "detail": detail})

    if not latencies_ms:
        add("runs", "fail", "No timed runs.")
        return {"verdict": "poor", "checks": checks, "stats": None}
    s = stats(latencies_ms)
    n = s["n"]
    add("runs", "pass" if n >= MIN_RUNS_GOOD else "warn" if n >= MIN_RUNS else "fail",
        f"{n} timed runs" + ("" if n >= MIN_RUNS_GOOD else f"; at least {MIN_RUNS_GOOD} give a stable mean" if n >= MIN_RUNS
                             else f"; at least {MIN_RUNS} are needed"))  # fmt: skip
    cv = s["cv"]
    add("spread", "pass" if cv <= CV_GOOD else "warn" if cv <= CV_OK else "fail",
        f"run-to-run spread {cv:.1%} of the mean" + ("" if cv <= CV_GOOD else "; other load on the device or power saving may be interfering"))
    if n >= 8:
        q1, q3 = _pct(latencies_ms, 0.25), _pct(latencies_ms, 0.75)
        fence = q3 + 3 * (q3 - q1)
        share = sum(1 for x in latencies_ms if x > fence) / n
        add("outliers", "pass" if share <= OUTLIER_WARN else "warn", f"{share:.0%} of runs are far slower than the rest")
        third = len(latencies_ms) // 3
        a, b = statistics.median(latencies_ms[:third]), statistics.median(latencies_ms[-third:])
        drift = (b - a) / a if a else 0.0
        add("drift", "pass" if abs(drift) <= DRIFT_WARN else "warn",
            f"the last third ran {drift:+.0%} against the first" + ("" if abs(drift) <= DRIFT_WARN else " (heating up, throttling or clock changes?)"))
    if warmup_ms:
        ratio = statistics.fmean(warmup_ms) / s["p50"] if s["p50"] else 1.0
        add("warmup", "pass", f"{len(warmup_ms)} warm-up runs discarded; they took {ratio:.1f}× the steady time")
    else:
        add("warmup", "warn", "no warm-up runs: the first runs may include one-off set-up time")
    if timer_resolution_ns is not None:
        rel = timer_resolution_ns / 1e6 / s["p50"] if s["p50"] else 0.0
        add("timer", "pass" if rel <= 0.01 else "fail", f"timer resolution {timer_resolution_ns:g} ns ({rel:.2%} of a run)")
    if temperatures_c and len(temperatures_c) >= 2:
        rise = temperatures_c[-1] - temperatures_c[0]
        add("thermal", "pass" if rise <= TEMP_RISE_WARN else "warn",
            f"temperature changed {rise:+.1f} °C during the runs" + ("" if rise <= TEMP_RISE_WARN else ": not yet at a thermal steady state"))
    if wall_seconds is not None:
        add("duration", "pass" if wall_seconds >= SHORT_RUN_S else "warn",
            f"{wall_seconds:.1f} s of runs" + ("" if wall_seconds >= SHORT_RUN_S else "; too short to show sustained (thermal) behaviour"))
    verdict = "poor" if any(c["status"] == "fail" for c in checks) else "usable" if any(c["status"] == "warn" for c in checks) else "good"
    return {"verdict": verdict, "checks": checks, "stats": s}
