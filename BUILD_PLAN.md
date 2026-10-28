# The Green Computer: 6-Day Phased Build Plan

Companion to the Implementation Plan (design and rationale). This is the execution order for a **6-day build**: Day 1 delivers a thin but complete working product, and each following day adds a layer of capability on top of a system that already runs. Nothing is built yet.

**Assumption:** Day 1 = 14 Oct, Day 6 = 19 Oct. The submission deadline is 29 Oct (30 Oct 10:30 pm IST hard). The days from 20 Oct are a reserve for overflow, deployment hardening, the video, and the Devpost write-up. If the six days must instead end closer to the deadline, only the dates change.

## Progress

| Day | State | Notes |
|---|---|---|
| 1 | Done (committed) | Thin slice. Nebius spikes still open: need API keys |
| 2 | Done (committed) | ONNX + manual models, coupled solver, corner-envelope uncertainty, 1,000+ candidate sweep, Pareto, co-design. Deferred: LLM prefill/decode, safetensors/Hugging Face input |
| 3 | Done, with gaps (committed) | Measurement ingest, gated calibration, Verify screen, bench scripts, real CPU measurements on the dev machine. Gaps: no GPU/power measurement, Nebius Jobs launcher not implemented (needs credentials), FP16/INT8 export and accuracy scripts unrun |
| 4 | Built, unverified live (committed) | Agent client, router, 10 tools, loop, numbers guardrail, SSE chat, Agent tab, Nebius Jobs launcher and check script. Tested with fakes only: no Token Factory key, `nebius` CLI, account or GPU was available. Tavily/datasheet import not done |
| 5 | Built, unverified live (committed) | Constraint repair, pruning and model-swap knobs, what-ifs and elasticities, four-level advice, alternatives, recipes, report, datasheet import (Tavily + Nemotron, mocked only), plain-language UI redesign. Recipes are syntax-checked, never run on a device; datasheet import never run live |
| 6 | Done for what can be done offline (uncommitted) | Public-demo safeguards (rate limits, billed actions off, caps, per-visitor projects, security headers), Dockerfile / compose / Nebius endpoint scripts (never built or run), polish (errors, loading, mobile, favicon), end-to-end journey test, acceptance script, judge guide, demo script, Devpost draft, cost estimate. Found and fixed a packaging bug (httpx missing from runtime deps). Remaining and needs you: live Token Factory / Tavily / Nebius verification, building and deploying the image, the video, the Devpost submission, the patent decision |

## Principles

1. **Always runnable.** At the end of every day the app starts, the demo scenario runs, and tests pass. A day never ends with a half-built layer.
2. **Thin slice first.** Day 1 is the whole loop at minimum depth (input → evaluate → certify → recommend → show). Later days deepen each stage; they do not introduce new stages.
3. **Engine stays pure Python.** No I/O or LLM imports; everything else calls it.
4. **Schemas first (Day 1 morning).** `Metric`, `EvaluationResult`, `Candidate`, `HardwareProfile`, `WorkloadGraph`, `ProjectSpec`, `MeasurementRecord`. Later layers add fields, never rename.
5. **Honesty built in from Day 1.** Every number carries provenance; the 55 W / 90% constraints are enforced from the first evaluator and never relaxed silently.
6. **Nebius risks are tested on Day 1, built on Day 3.** A one-hour spike now avoids discovering on Day 3 that Jobs can't be launched.
7. **Repo stays private until the patent decision is made.**

## Capability ladder at a glance

| Day | Theme | After this day the product can... |
|---|---|---|
| 1 | **Basic working product** | Take a library model + library device + constraints, predict latency/power/energy/temperature, certify feasibility, apply a few software knobs, recommend the best, and show it in a simple web page |
| 2 | **Real physics and depth** | Parse real ONNX files, handle thermal throttling as a solved fixed point, show uncertainty bands, sweep thousands of candidates, Pareto frontier, co-design comparison |
| 3 | **Real hardware verification** | Run benchmarks on Nebius GPUs, ingest measurements, show predicted vs measured, calibrate with a gate |
| 4 | **The Green Agent** | Understand a typed goal, drive the engine through tools, explain results, guarantee no invented numbers |
| 5 | **Advanced capability** | Import unknown hardware via Tavily + Nemotron, constraint repair, elasticities, recipe generation, report |
| 6 | **Product polish + deploy** | Present the full journey on a public URL with all five screens, error states, README, and demo-ready seed |

---

## Day 1: Basic product (the whole loop, minimum depth)

**Goal:** a person can open a page, pick a model and a device, set constraints, and get a certified, ranked recommendation with the numbers labelled.

**Morning: foundations (~3 h)**
- Day-1 spikes: Token Factory model IDs, tool calling, structured output; Serverless Job hello-world; Object Storage access. Write findings to `docs/nebius-findings.md`. Fallbacks as in the notes at the end.
- Private repo, licence (Apache-2.0), monorepo skeleton (`backend/`, `frontend/`, `bench/`, `deploy/`), `.env.example`, CI (pytest, ruff, tsc).
- Pydantic schemas and `Metric` (value, unit, low, high, provenance), `Feasibility` enum with five states.

**Afternoon: engine v0 (~5 h)**
- Units and provenance helpers.
- **Model input:** built-in library only (MobileNetV2, ResNet-50, YOLOv8 n/s/m, Nemotron Nano) from YAML with per-model FLOPs, parameters, bytes moved. No ONNX parsing yet.
- **Device input:** library YAMLs for Jetson Orin Nano / NX / AGX with power modes and cooling options, plus two Nebius GPUs. Spec-sheet values labelled as such.
- **L0 evaluator, single pass (no fixed point yet):** roofline latency, memory fit, utilisation-based power, energy per inference, steady-state temperature `T = T_amb + P·R_th`, throttling flag if `T > T_throttle`.
- **Certification v0:** hard constraints power ≤ 55 W, accuracy ≥ 90%. Accuracy from a lookup table of published baselines and a simple precision penalty prior, marked "assumed". Quantised variants with no measurement are "uncertain".
- **Candidate generator v0:** precision (FP32/FP16/INT8) × batch × power mode, evaluated one by one; rank by energy among feasible.

**Evening: API and UI v0 (~3 h)**
- FastAPI: `POST /projects`, `POST /projects/{id}/evaluate`, `POST /projects/{id}/optimise`, `GET /health`. SQLite via SQLAlchemy. Seeds the robotics example on start.
- React page: form (model, device, ambient temp, FPS, constraints), results table with provenance badges and a feasibility chip, a "best recommendation" card.

**Tests today:** unit conversion, roofline on hand-computed cases, a 56 W candidate and an 89% candidate are never "feasible" (property test), API round trip.

**Done when:** the robotics example (YOLOv8s, 30 FPS, 40 °C, 55 W, 90%) gives a ranked list in the browser; CI green; Nebius spikes recorded.

---

## Day 2: Real physics and depth (engine gets accurate and fast)

**Goal:** the numbers behave like a real system and the search space is large.

- **ONNX parser:** per-layer workload graph (shape inference, MACs, bytes); safetensors and Hugging Face config paths; manual-parameter mode; LLM prefill/decode and KV cache. Reject pickle files. Counts match reference values to ±1%.
- **Coupled solver:** power ↔ temperature ↔ leakage ↔ throttling fixed point with damping and an iteration cap; "did not converge" surfaced as a state. RC transient: time to throttle.
- **Uncertainty:** propagate low/high through every metric; the 95% band is part of every `Metric`; certification uses pessimistic bounds (predicted feasible only if the pessimistic bound passes).
- **Optimiser:** vectorised NumPy sweep over precision, batch, resolution, power mode, clocks, cooling; thousands of candidates in seconds; Pareto frontier (energy, latency, accuracy, memory, cost); transparent weighted ranking.
- **Co-design comparison:** baseline vs software-only vs hardware-only vs combined, with the interaction factor `I = E_comb·E_base / (E_sw·E_hw)`.
- **Cost and carbon:** electricity price, grid factor, battery life.
- **UI:** Energy & Thermal screen (temperature over time, throttle point), Pareto chart, compare table, uncertainty bars.

**Tests:** fixed-point convergence across ambient × power mode grid; sweep performance budget; Pareto correctness on small known sets; thermal-blind vs thermal-aware ranking differs on the throttling example.

**Done when:** an ONNX upload works end to end and the Optimisation Lab shows a Pareto frontier and the four-way comparison.

---

## Day 3: Real hardware verification (the loop closes)

**Goal:** measured numbers from real NVIDIA GPUs flow back and improve predictions.

- **Bench image:** CUDA + `onnxruntime-gpu` (TensorRT if available); `export_models` (FP32/FP16/INT8 variants), `run_benchmark` (warm-up, timed runs, latency percentiles, throughput), `power_logger` (`nvidia-smi` power and temperature), `accuracy_check` (small fixed validation subset per precision), `upload_results` to Object Storage.
- **Job launcher + ingestion:** `integrations/nebius_jobs.py`, `object_store.py`, `benchmark_service`; API `POST /projects/{id}/benchmarks`, `GET /benchmarks/{job_id}`; ingest `MeasurementRecord` and link to the matching prediction.
- **Measurement campaign:** about 5 models × 3 precisions × 2 GPU types. Results committed to `data/measurements/` with date and GPU type (this is also the fallback if Jobs cannot be launched from the app).
- **Calibration service:** per (device, precision, metric) correction factors, shrinkage toward the prior, leave-one-out gate (version accepted only if held-out error improves); versioned.
- **UI:** Verify screen with the predicted-vs-measured chart, error before/after, calibration versions; measured values now show the "measured" badge, and accuracy for measured precisions moves from "assumed" to "measured".

**Tests:** calibration accepts when error improves and rejects when it does not; coverage of the 95% bands on held-out measurements; ingestion idempotency.

**Done when:** a button launches (or replays) a real GPU run and the chart updates after calibration.

---

## Day 4: The Green Agent (Nemotron in the main loop)

**Goal:** the user can type a goal and the agent drives everything through tools, with no invented numbers.

- **Token Factory client** and **router**: Ultra (planning, final narrative), Super/Nano (intent parsing, tool selection, chat).
- **Tool layer** (`tools.py`): `get_project`, `update_project_spec` (constraints read-only, enforced in code), `list_devices`, `list_models`, `evaluate`, `sweep`, `pareto`/`rank`, `compare_codesign`, `calibration_status`, `submit_benchmark`/`get_benchmark`. The agent passes IDs and receives structured JSON.
- **Loop:** 12-call cap per turn, streaming of steps, every call and result logged to `agent_runs` for replay.
- **Intent parsing:** typed goal → `ProjectSpec` via structured output; one question back if fields are missing; hard constraints written only from the form or the user's own words.
- **Guardrail:** every number in agent text must appear in a tool result in the same turn (tolerances for rounding and units); mismatch triggers regeneration or removal.
- **Graceful degradation:** Token Factory down → agent panel disabled, everything else works.
- **UI:** Agent panel with chat and a live tool-call timeline; project form fills from the conversation.

**Tests (mocked LLM):** guardrail rejects a planted number and accepts rounded matches; constraint write attempts are refused; step cap holds; tool dispatch round trips. One live smoke test.

**Done when:** "Run YOLOv8s at 30 FPS on a robot, under 55 W, at least 90% mAP, 40 °C ambient" produces a project, an evaluation, a sweep and an explained recommendation.

---

## Day 5: Advanced capability

**Goal:** the features that make it more than an evaluator.

- **Constraint repair:** minimum-effort edit set (effort weights per knob), certified under pessimistic bounds; "no feasible repair" reports the binding constraints. `repair_constraints` tool.
- **Elasticities:** sensitivity of latency and energy to bandwidth, compute, clock, cooling; bottleneck class (compute, memory, overhead, heat, power delivery); `elasticities` tool. Chip-level changes appear as labelled design what-ifs only.
- **Hardware import:** Tavily `find_datasheet`, then `extract_hardware_spec` (Nano; Omni for PDFs if available). Draft profile tagged "extracted, unverified" with source links; user must confirm before it enters a certified result.
- **Recommendations:** structured objects (software / configure / select / design what-if), each with change, effects, confidence, evidence, validation steps. Ultra writes explanations under the guardrail.
- **Recipe generation:** Jinja templates for export/quantise/runtime config/power mode filled by `generate_recipe`; optional smoke test via a Job.
- **Report:** Markdown (and HTML if time) from stored results only, with assumptions, uncertainty, provenance.

**Tests:** repair never returns a set whose pessimistic bound fails a constraint; extraction output always tagged unverified; report contains no value that is absent from stored results.

**Done when:** the unknown-board demo works and the robotics example yields the cheapest certified fix with reasons, a recipe and a report.

---

## Day 6: Product polish and public deploy

**Goal:** a judge can open the URL cold and complete the whole journey.

- **Five screens finished:** Project setup (form + chat), Evaluation, Energy & Thermal, Optimisation Lab, Recommendations & Report (Verify from Day 3 included). Empty, loading and error states; light/dark tokens; mobile-tolerant layout.
- **Seed:** the robotics example and a measured-results set always load on start; judge instructions on the first screen; no login.
- **Deploy:** single Dockerfile (frontend built into FastAPI), push to the Nebius registry, Serverless Endpoint, env secrets, Object Storage wiring. Clean-browser test, Token-Factory-blocked test, restart persistence test.
- **Repo and docs:** README (setup, architecture, how Nemotron and Token Factory are used, which Nebius services, honest limitations), `docs/algorithms.md`, `docs/assumptions.md`, `docs/demo-script.md`.
- **Credit check:** estimate Token Factory and Endpoint spend through 15 Dec.

**Done when:** the acceptance test (below) passes on the public URL.

---

## After Day 6 (reserve until 29 Oct)

Demo video (under 3 minutes, public on YouTube, no copyrighted music), Devpost text, track selection, the Token Factory / AI Cloud / NVIDIA feedback write-up (log friction as it happens in `docs/nebius-findings.md`), provisional patent decision, flip repo to public, submit on 29 Oct.

## Acceptance test (public URL, clean browser)

1. Type the robotics goal; a project is created.
2. Baseline shows throttling risk or a failed constraint, with bottleneck and uncertainty bands.
3. Optimise returns a Pareto frontier and the four-way comparison with interaction factor.
4. Repair gives a minimum-effort certified fix.
5. Verify shows real GPU measurements and the predicted-vs-measured chart after gated calibration.
6. An unknown board is imported via Tavily + Nemotron, tagged "extracted, unverified", and requires confirmation.
7. Every number in agent text appears in a tool result; the agent cannot change constraints.
8. With Token Factory blocked, everything except the agent panel still works.

## Decisions needed from you (before the day they bite)

| Decision | Needed by | Recommendation |
|---|---|---|
| Patent vs open source | Before going public | Build privately; consult an attorney this week |
| Licence | Day 1 | Apache-2.0 |
| Track | Day 1 | Best Apps and Agents |
| Validation subsets for `accuracy_check` (COCO/ImageNet slices) and who fetches them | Day 3 morning | Few hundred images each, licence noted |
| Any Jetson access? | Any time | Without one, edge numbers stay "analytical" and are never claimed validated |
| Credit budget | Day 6 | Ultra for planning and narrative only; Super/Nano elsewhere |

## Risks and cut ladder

With only six days, cut from the top of this list first:

1. Nano Omni PDF/image reading (HTML spec pages only)
2. Recipe smoke test in a Job (ship recipe labelled "untested")
3. HTML report (Markdown only)
4. Second Nebius GPU type
5. Chip-level design what-ifs
6. LLM prefill/decode detail (vision models only in the demo)
7. Streaming polish (show the tool timeline after the turn)

**Never cut:** Nemotron in the main loop, numbers guardrail, constraint certification, real Job measurements with predicted-vs-measured, public URL, video.

**Fallbacks decided now:**
- Token Factory not OpenAI-compatible → thin custom client behind the same interface.
- No tool calling on a model → JSON-in-prompt protocol with schema validation.
- Jobs cannot be launched from the app → run campaigns manually, commit labelled results, and have the Verify screen replay them and say so.

**Honest-limits to state in UI and README:** calibration is validated on Nebius data-centre GPUs; Jetson results are analytical with wide bands; thermal behaviour is a model with stated assumptions.
