"""Measure what this host can actually do, to build a device profile for it.

Reports achievable fp32 GEMM throughput (compute roof) and copy bandwidth (memory roof).
These are measured, but they are roofs for dense numpy kernels, not vendor peaks.

    python probe_host.py > host_probe.json
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time

import numpy as np


def _best(fn, repeats: int = 5) -> float:
    fn()  # warm-up
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def gemm_gflops(n: int = 2048) -> float:
    a = np.random.rand(n, n).astype(np.float32)
    b = np.random.rand(n, n).astype(np.float32)
    t = _best(lambda: a @ b)
    return 2.0 * n**3 / t / 1e9


def copy_gbs(mb: int = 256) -> float:
    n = mb * 1024 * 1024 // 4
    src = np.random.rand(n).astype(np.float32)
    dst = np.empty_like(src)
    t = _best(lambda: np.copyto(dst, src))
    return 2.0 * src.nbytes / t / 1e9  # read + write


def cpu_name() -> str:
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout.strip()
            if out:
                return out
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or platform.machine()


def main() -> None:
    print(
        json.dumps(
            {
                "cpu": cpu_name(),
                "logical_cores": os.cpu_count(),
                "os": f"{platform.system()} {platform.release()}",
                "fp32_gemm_gflops": round(gemm_gflops(), 1),
                "copy_bandwidth_gbs": round(copy_gbs(), 1),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
