"""Sample GPU power and temperature with nvidia-smi while a benchmark runs.

Returns None summaries when nvidia-smi is not available (e.g. CPU-only hosts), never zeros.
"""

from __future__ import annotations

import shutil
import subprocess
import threading


class PowerLogger:
    QUERY = "power.draw,temperature.gpu,utilization.gpu"

    def __init__(self, interval_ms: int = 200):
        self.interval_ms = interval_ms
        self.samples: list[tuple[float, float, float]] = []
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.available = shutil.which("nvidia-smi") is not None

    def start(self) -> None:
        if not self.available:
            return
        self._proc = subprocess.Popen(
            [
                "nvidia-smi",
                f"--query-gpu={self.QUERY}",
                "--format=csv,noheader,nounits",
                f"-lms={self.interval_ms}",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )

        def read() -> None:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                try:
                    p, t, u = (float(x) for x in line.strip().split(","))
                    self.samples.append((p, t, u))
                except ValueError:
                    continue

        self._thread = threading.Thread(target=read, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        if self._thread:
            self._thread.join(timeout=2)
        busy = [s for s in self.samples if s[2] >= 50.0] or self.samples
        if not busy:
            return {
                "power_w_avg": None,
                "power_w_peak": None,
                "temperature_c_max": None,
                "power_samples": 0,
            }
        return {
            "power_w_avg": round(sum(s[0] for s in busy) / len(busy), 2),
            "power_w_peak": round(max(s[0] for s in busy), 2),
            "temperature_c_max": round(max(s[1] for s in self.samples), 1),
            "power_samples": len(busy),
        }
