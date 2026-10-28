"""Check the Nebius / NVIDIA connections with YOUR credentials. This is the Day-1 spike, automated.

    backend/.venv/Scripts/python scripts/check_nebius.py              # Windows
    backend/.venv/bin/python scripts/check_nebius.py                  # macOS / Linux
    ... --submit-job                                                  # also launch a real GPU job

Put keys in `.env` (copy `.env.example`); nothing is printed except pass/fail and model names.

What it checks
  1. Token Factory: the API key works, the model list loads, Nemotron models resolve.
  2. Token Factory: a chat completion works, and a tool call round-trips (the agent depends on it).
  3. Nebius CLI: installed, and `nebius ai job create --dry-run` accepts our benchmark job.
  4. (--submit-job) a real benchmark job is created. This costs GPU time.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.client import LLMError, TokenFactoryClient  # noqa: E402
from app.agent.router import resolve_models  # noqa: E402
from app.config import get_llm_settings, load_dotenv  # noqa: E402
from app.data_loader import get_device  # noqa: E402
from app.integrations.jobs import JobsError, JobSpec, NebiusJobs  # noqa: E402

OK, BAD, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""))


def check_token_factory() -> None:
    s = get_llm_settings()
    if not s.configured:
        report(SKIP, "Token Factory", "TOKEN_FACTORY_API_KEY is not set in .env")
        return
    try:
        client = TokenFactoryClient(s.api_key or "", s.base_url, s.timeout_s)
        models = client.list_models()
        report(OK, "model list", f"{len(models)} models at {s.base_url}")
    except LLMError as e:
        report(BAD, "model list", str(e))
        return
    resolved = resolve_models(client, s)
    for tier, mid in resolved.by_tier.items():
        report(OK if mid else BAD, f"Nemotron {tier}", mid or "not found in the model list")
    try:
        model = resolved.for_task("plan")
    except LLMError as e:
        report(BAD, "usable Nemotron model", str(e))
        return

    t = time.perf_counter()
    try:
        r = client.chat(model, [{"role": "user", "content": "Reply with the single word: ready"}],
                        max_tokens=32)  # fmt: skip
        report(OK, "chat completion", f"{model} replied in {time.perf_counter() - t:.1f}s: {r.content[:40]!r}")
    except LLMError as e:
        report(BAD, "chat completion", str(e))
        return

    tool = {"type": "function", "function": {
        "name": "get_power", "description": "Return the power draw of a device in watts",
        "parameters": {"type": "object", "properties": {"device": {"type": "string"}},
                       "required": ["device"]}}}  # fmt: skip
    try:
        msgs = [{"role": "user", "content": "What is the power draw of the jetson? Use the tool."}]
        r1 = client.chat(model, msgs, tools=[tool], tool_choice="auto", max_tokens=256)
        if not r1.tool_calls:
            report(BAD, "tool calling", f"the model did not call the tool (replied: {r1.content[:60]!r})")
            return
        tc = r1.tool_calls[0]
        msgs += [r1.message, {"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                              "content": '{"watts": 12.5}'}]  # fmt: skip
        r2 = client.chat(model, msgs, tools=[tool], max_tokens=128)
        report(OK, "tool calling", f"called {tc.name}({tc.arguments}); final: {r2.content[:60]!r}")
    except LLMError as e:
        report(BAD, "tool calling", str(e))


def check_jobs(submit: bool) -> None:
    jobs = NebiusJobs()
    st = jobs.status()
    if not st.cli_found:
        report(SKIP, "Nebius CLI", st.reason)
        return
    report(OK, "Nebius CLI", "found on PATH")
    if not st.configured:
        report(BAD, "Nebius jobs config", st.reason)
        return
    spec = JobSpec("nebius-l40s", "resnet-50", "fp32", (1, 8), 20)
    try:
        out = jobs.dry_run(spec, get_device(spec.device_id))
        report(OK, "job dry run", "the CLI accepted the create request" + (f" ({out[:60]!r})" if out.strip() else ""))
    except JobsError as e:
        report(BAD, "job dry run", str(e))
        return
    if not submit:
        report(SKIP, "job submit", "re-run with --submit-job to launch a real (billed) job")
        return
    try:
        name, job_id = jobs.submit(spec, get_device(spec.device_id))
        report(OK, "job submit", f"{name} -> {job_id}. Track it in the app (Verify) or `nebius ai job logs {job_id}`")
    except JobsError as e:
        report(BAD, "job submit", str(e))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit-job", action="store_true", help="launch a real GPU benchmark job (billed)")
    a = ap.parse_args()
    load_dotenv()
    check_token_factory()
    check_jobs(a.submit_job)
    failed = [r for r in results if r[0] == BAD]
    print(f"\n{len([r for r in results if r[0] == OK])} passed, {len(failed)} failed, "
          f"{len([r for r in results if r[0] == SKIP])} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
