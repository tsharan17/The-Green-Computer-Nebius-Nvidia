"""Nebius Serverless Jobs launcher (real NVIDIA GPU benchmarks) via the `nebius` CLI.

Built from the documented CLI (`nebius ai job create|get|logs|cancel`, flags `--image`,
`--platform`, `--preset`, `--container-command`, `--args`, `--inject-file`, `--timeout`,
`--restart-policy`, `--async`, `--format json`). HONEST STATUS: the command construction is unit
tested, but it has never been run against a real Nebius account (none was available), and the
exact JSON field names returned by `job get` / `create` are not documented, so state and id
extraction search the JSON flexibly. `scripts/check_nebius.py` exercises the real CLI with
`--dry-run`; run it first.

How a job works: the stock CUDA image is started with `bench/job_entrypoint.sh` and the benchmark
scripts injected as read-only files (64 KiB limit each), so no image has to be built or pushed. The
entrypoint prints the result JSON between GC_RESULT_BEGIN / GC_RESULT_END and the backend reads it
back from `nebius ai job logs`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas.hardware import HardwareProfile

BENCH_DIR = Path(__file__).resolve().parents[3] / "bench"
INJECTED = ("job_entrypoint.sh", "run_benchmark.py", "power_logger.py", "reference_models.py")
RESULT_BEGIN, RESULT_END = "GC_RESULT_BEGIN", "GC_RESULT_END"
JOB_MODELS = ("resnet-50", "mobilenet-v2")  # the reference graphs the job can build itself
JOB_PRECISIONS = ("fp32", "fp16")
DEFAULT_IMAGE = "nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04"  # tag NOT verified

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class JobsError(RuntimeError):
    """A Serverless Jobs operation failed or is not possible."""


@dataclass(frozen=True)
class JobsStatus:
    configured: bool
    reason: str
    cli_found: bool = False


@dataclass(frozen=True)
class JobSpec:
    device_id: str
    model_id: str
    precision: str
    batches: tuple[int, ...]
    runs: int = 50


@dataclass(frozen=True)
class JobState:
    state: str  # running | succeeded | failed | cancelled | unknown
    raw: str


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)


def _find(obj: Any, keys: tuple[str, ...]) -> Any:
    """First value for any of `keys`, searching nested dicts/lists (field names are undocumented)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        for v in obj.values():
            hit = _find(v, keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, keys)
            if hit is not None:
                return hit
    return None


def classify_state(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except ValueError:
        return "unknown"
    s = str(_find(data, ("state", "status", "phase")) or "").upper()
    if any(w in s for w in ("COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED")):
        return "succeeded"
    if any(w in s for w in ("FAIL", "ERROR")):
        return "failed"
    if "CANCEL" in s:
        return "cancelled"
    if any(w in s for w in ("RUN", "START", "PROVISION", "PENDING", "CREAT", "QUEUE", "SCHEDUL")):
        return "running"
    return "unknown"


def extract_result_json(logs: str) -> str:
    """The JSON the entrypoint printed between the result markers."""
    if RESULT_BEGIN not in logs or RESULT_END not in logs:
        raise JobsError("the job logs contain no result block (job failed or still running)")
    body = logs.split(RESULT_BEGIN, 1)[1].split(RESULT_END, 1)[0].strip()
    if not body:
        raise JobsError("the job's result block is empty")
    return body


class NebiusJobs:
    def __init__(
        self,
        runner: Runner | None = None,
        cli: str | None = None,
        image: str | None = None,
        project_id: str | None = None,
        bench_dir: Path | None = None,
    ):
        self._run = runner or _default_runner
        self.cli = cli or "nebius"
        self.image = image or os.environ.get("GC_BENCH_IMAGE") or DEFAULT_IMAGE
        self.project_id = project_id or os.environ.get("NEBIUS_PROJECT_ID") or None
        self.bench_dir = bench_dir or BENCH_DIR
        self._injected_runner = runner is not None

    # ---- status ---------------------------------------------------------------------------

    def status(self) -> JobsStatus:
        found = self._injected_runner or shutil.which(self.cli) is not None
        if not found:
            return JobsStatus(
                False,
                "the `nebius` CLI is not installed or not on PATH "
                "(install it and run `nebius profile create`)",
                False,
            )
        if not self.project_id:
            return JobsStatus(False, "set NEBIUS_PROJECT_ID (or configure the CLI profile)", True)
        missing = [f for f in INJECTED if not (self.bench_dir / f).is_file()]
        if missing:
            return JobsStatus(False, f"bench files missing: {', '.join(missing)}", True)
        return JobsStatus(True, "ready (untested against a live account)", True)

    # ---- commands -------------------------------------------------------------------------

    def _platform(self, hw: HardwareProfile) -> tuple[str, str | None]:
        platform = os.environ.get("GC_NEBIUS_PLATFORM_OVERRIDE") or hw.nebius_platform
        preset = os.environ.get("GC_NEBIUS_PRESET_OVERRIDE") or hw.nebius_preset
        if not platform:
            raise JobsError(
                f"{hw.id} has no Nebius GPU platform: only devices that exist on Nebius can be "
                "benchmarked by a job"
            )
        return platform, preset

    def validate(self, spec: JobSpec, hw: HardwareProfile) -> None:
        if spec.model_id not in JOB_MODELS:
            raise JobsError(f"jobs can build only {', '.join(JOB_MODELS)}; got '{spec.model_id}'")
        if spec.precision not in JOB_PRECISIONS:
            raise JobsError(f"jobs support {', '.join(JOB_PRECISIONS)}; got '{spec.precision}'")
        if not spec.batches or any(b < 1 or b > 256 for b in spec.batches):
            raise JobsError("batches must be between 1 and 256")
        if not 5 <= spec.runs <= 1000:
            raise JobsError("runs must be between 5 and 1000")
        self._platform(hw)

    def build_create_command(
        self, spec: JobSpec, hw: HardwareProfile, name: str, dry_run: bool = False
    ) -> list[str]:
        platform, preset = self._platform(hw)
        cmd = [
            self.cli, "ai", "job", "create",
            "--name", name,
            "--image", self.image,
            "--platform", platform,
            "--container-command", "bash",
            "--args", "/bench/job_entrypoint.sh "
            f"{spec.model_id} {spec.precision} {','.join(str(b) for b in spec.batches)} "
            f"{spec.device_id} {spec.runs}",
            "--restart-policy", "never",
            "--timeout", "1h",
            "--async",
            "--format", "json",
        ]  # fmt: skip
        if preset:
            cmd += ["--preset", preset]
        if self.project_id:
            cmd += ["--parent-id", self.project_id]
        for f in INJECTED:
            cmd += ["--inject-file", f"{self.bench_dir / f}:/bench/{f}"]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    # ---- operations -----------------------------------------------------------------------

    def _call(self, cmd: list[str]) -> str:
        try:
            p = self._run(cmd)
        except FileNotFoundError as e:
            raise JobsError("the `nebius` CLI was not found on PATH") from e
        except subprocess.TimeoutExpired as e:
            raise JobsError("the `nebius` CLI timed out") from e
        if p.returncode != 0:
            msg = (p.stderr or p.stdout or "").strip()
            raise JobsError(f"`nebius` failed ({p.returncode}): {msg[:500]}")
        return p.stdout

    def dry_run(self, spec: JobSpec, hw: HardwareProfile) -> str:
        self.validate(spec, hw)
        return self._call(self.build_create_command(spec, hw, "gc-bench-dryrun", dry_run=True))

    def submit(self, spec: JobSpec, hw: HardwareProfile) -> tuple[str, str]:
        """Create the job. Returns (job_name, job_id)."""
        self.validate(spec, hw)
        name = f"gc-bench-{int(time.time())}"
        out = self._call(self.build_create_command(spec, hw, name))
        job_id = None
        try:
            job_id = _find(json.loads(out), ("resource_id", "id"))
        except ValueError:
            pass
        if not job_id:  # field names are undocumented: fall back to finding the job by name
            listing = self._call([self.cli, "ai", "job", "list", "--format", "json"])
            job_id = self._id_by_name(listing, name)
        if not job_id:
            raise JobsError(
                f"created job '{name}' but could not read its id from the CLI output; "
                "find it with `nebius ai job list`"
            )
        return name, str(job_id)

    @staticmethod
    def _id_by_name(listing_json: str, name: str) -> str | None:
        try:
            data = json.loads(listing_json)
        except ValueError:
            return None

        def walk(o: Any) -> str | None:
            if isinstance(o, dict):
                if o.get("name") == name and o.get("id"):
                    return str(o["id"])
                for v in o.values():
                    r = walk(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = walk(v)
                    if r:
                        return r
            return None

        return walk(data)

    def get(self, job_id: str) -> JobState:
        raw = self._call([self.cli, "ai", "job", "get", job_id, "--format", "json"])
        return JobState(state=classify_state(raw), raw=raw)

    def logs(self, job_id: str) -> str:
        return self._call([self.cli, "ai", "job", "logs", job_id])

    def cancel(self, job_id: str) -> None:
        self._call([self.cli, "ai", "job", "cancel", job_id])


def get_jobs() -> NebiusJobs:
    return NebiusJobs()


def status() -> JobsStatus:
    return get_jobs().status()
