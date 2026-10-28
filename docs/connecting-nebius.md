# Connecting Nebius and NVIDIA

Two connections, both optional: the app works without them (the engine, charts, measurements and calibration do not need them). Keys go only in `.env` (git-ignored) or your shell, never in chat or code.

## 1. Green Agent: Nemotron on Nebius Token Factory

1. Create an API key in the Token Factory console.
2. Copy `.env.example` to `.env` and set `TOKEN_FACTORY_API_KEY`. The base URL defaults to `https://api.tokenfactory.nebius.com/v1/` (from the Token Factory docs). Any OpenAI-compatible endpoint works if you set `TOKEN_FACTORY_BASE_URL`.
3. Run the check, then start the app:

   ```bash
   backend/.venv/Scripts/python scripts/check_nebius.py
   scripts/dev.ps1
   ```

   The check lists the provider's models, resolves Nemotron Ultra/Super/Nano, does a chat call and a **tool call round trip** (the agent needs tool calling).
4. Open the **Agent** tab. The status line shows which models were resolved.
5. Open a project, run the analysis, then **Recommendations → Software and hardware → Ask the AI for candidates**.
   This sends one chat request (Nemotron Super by default, at most 1,400 output tokens) with the engine's summary
   only, never file contents. The reply is checked against a schema and cached for that analysis, so asking again
   without changing anything costs nothing. Use **Ask again** to force a new call.

**Model IDs are not verified.** The defaults (`nvidia/Nemotron-3-Ultra-550b-a55b`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) come from third-party catalogues. The app fetches the real list and matches by name, so a wrong default degrades gracefully; set `GC_MODEL_ULTRA/SUPER/NANO` to pin exact IDs. Nano Omni is never used for text.

NVIDIA's own hosted API is also OpenAI-compatible: point `TOKEN_FACTORY_BASE_URL` at it with its key to use NVIDIA-hosted Nemotron instead (the hackathon requires Nebius, so keep Token Factory for the submission).

## 2. Real NVIDIA GPU measurements: Nebius Serverless Jobs

1. Install the `nebius` CLI and create a profile (`nebius profile create`); set `NEBIUS_PROJECT_ID` in `.env` if the profile has none.
2. `scripts/check_nebius.py` runs `nebius ai job create --dry-run` for the benchmark job. Fix anything it reports first.
3. In the app: **Verify → Real GPU benchmarks on Nebius → Launch** (billed GPU time), or `python scripts/check_nebius.py --submit-job`.
4. Press **Refresh** on the job. When it has finished, the result is read from the job logs and imported; calibration refits automatically. Power and temperature come from `nvidia-smi` inside the job.

How it works: the stock CUDA image (`GC_BENCH_IMAGE`, default `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`) is started with `bench/job_entrypoint.sh`; the benchmark scripts are injected with `--inject-file`, `onnxruntime-gpu` is installed at start-up, and the result JSON is printed between `GC_RESULT_BEGIN`/`GC_RESULT_END`. No image is built or pushed. GPU platform names come from the device YAML (`nebius_platform`) or `GC_NEBIUS_PLATFORM_OVERRIDE`.

## What has and has not been verified

| Piece | Status |
|---|---|
| Token Factory request/response format, base URL, tool-call format | Taken from the Nebius docs; client tested against a mocked transport |
| Real Token Factory call, Nemotron model IDs, tool calling on those models | **Not tested** (no key available when this was built). Run `check_nebius.py` |
| AI recommendation candidates (schema validation, redaction, caching, failures) | Tested with a mocked provider (`tests/api/test_workflow.py`); **not yet run against Token Factory** |
| Whole agent loop, tools, guardrail, streaming UI | Tested against a scripted fake model and a local fake provider over real HTTP |
| `nebius ai job create/get/logs/cancel` flags | Taken from the Nebius CLI docs; command construction unit tested with a fake runner |
| Real job launch, the JSON shape of `job get`/`create` output, CUDA image tag, `onnxruntime-gpu` install in the job, `nvidia-smi` sampling | **Not tested** (no CLI, account or GPU). State/id parsing searches the JSON flexibly because the field names are undocumented |
| Tavily search request format (POST /search, Bearer key, body fields) | Taken from the Tavily docs; client tested against a mocked transport |
| Real Tavily call and Nemotron datasheet extraction quality | **Not tested** (no keys). Extraction is validated and always needs your confirmation |
