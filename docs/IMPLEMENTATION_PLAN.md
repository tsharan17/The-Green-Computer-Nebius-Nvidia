# Implementation plan: from hackathon build to product

Source of truth for intended UX: `docs/original-system-map.pdf.pdf` (one hand-drawn page). This plan
maps every box on that page to work, keeps the existing engine, and lands in vertical slices that
leave the app runnable after each phase. Status lives in `IMPLEMENTATION_STATUS.md`.

## The map, condensed

| Map box | What it asks for | Where it lands |
|---|---|---|
| Security page | Sign up / create account, log in, "all security features", delete account | Phase 2 |
| Intro page | Welcome, short intro, get started | Phase 2 (workspace / onboarding) |
| Input page | Upload AI model files (system says which files; minimum verification); upload HW files (board description, MCU architecture, assembly, chip design) with required file types and verification; performance and environment constraints | Phase 3 |
| System working | Build a virtual HW from the files; run the model on it across all ranges | Phase 4 (digital twin v1, analytical); Phase 6 (real execution) |
| Outputs | Heat over all ranges + heat maps; energy on all metrics with industry-grade plots; memory map per stage; time, delays; cooling analysis (sustainable only), cooling needed per spot of the HW | Phases 4–5 |
| Top note | 3D map of the SW–HW working in real time | Phase 4 data model is 3D-ready; 2D view first; 3D is not a blocker |
| Recommendations | SW: model optimisation, quantisation with HW, no accuracy loss claims, implementation changes. HW: MCU architecture, chip design, RAM/ROM size, circuit changes. Tabs of recommended changes with all output params; help the user choose | Phase 5 |
| Final export | After selecting changes: new vs old params like the output panel; export new model files, new HW design files, changes and params; history of each use; account usage tracking | Phase 5–6 |

## Architecture decisions

1. **Keep the engine pure.** `backend/app/engine/` stays deterministic, no I/O. New pure modules:
   `app/twin/` (execution trace, memory timeline, components), `app/engine/plan.py` (design plans),
   `app/engine/cooling_catalogue.py`.
2. **Provider-neutral interfaces** (`app/providers/`):
   - `ExecutionBackend`: `local-cpu` (ONNX Runtime CPU, required), `local-cuda` / `tensorrt`
     (only when ORT reports the provider), `nebius` (feature flag, wraps the existing job launcher),
     `remote-device` (interface only, listed as planned).
   - `AIProvider`: `none` (default) or any OpenAI-compatible endpoint (existing Token Factory client).
     The engine computes every number; the existing guardrail stays.
   - `BlobStore`: local content-addressed files; the interface admits S3-compatible stores later.
   - `EmailSender`: console/dev outbox by default, SMTP when configured.
   - `ArtifactGenerator`: every generated file is labelled `generated+validated`, `generated` or
     `template` (instructions only).
3. **Auth is self-hosted** in FastAPI: scrypt password hashes (stdlib), opaque session tokens stored
   hashed, HttpOnly SameSite cookie + custom-header CSRF check, bearer API tokens for the CLI. No SaaS
   dependency; a hosted identity provider can be added behind the same `current_user` dependency.
4. **Tenancy:** user → personal workspace (organisation) → projects. Every owned row carries
   `workspace_id`; every query filters by it; library data (built-in devices/models, committed
   measurements) has `workspace_id = NULL` and is read-only. Legacy rows from before accounts are
   moved to a "Local workspace" claimed by the first account created on that database.
5. **Storage:** SQLAlchemy 2 + Alembic migrations; SQLite for local development, PostgreSQL via
   `GC_DB_URL=postgresql+psycopg://...`. Migrations are additive; existing SQLite files upgrade in
   place (the baseline revision adopts tables created by the old `create_all`).
6. **Immutability:** project versions, analysis runs, traces, design plans and exports are append-only.
   Editing a project creates a new version; runs point at the version they analysed.
7. **Honesty rules in code:** every metric carries provenance (`analytical`, `estimated`, `measured`,
   `calibrated`, `imported`) and a band; analytical traces are labelled *predicted*; measured traces
   come only from an execution backend or an imported log; a measured value is never silently
   replaced by a prediction (the UI shows both, labelled).
8. **Frontend:** React + Vite (no new UI framework); a small hash router; hand-built SVG charts
   (existing idiom) with CSV/JSON export of the data behind every chart.

## Phases (each ends runnable, tested, documented)

| Phase | Deliverable |
|---|---|
| 1 | Audit, `CLAUDE.md`, this plan, `IMPLEMENTATION_STATUS.md`, baseline test/build run |
| 2 | Alembic; users, workspaces, memberships, sessions, password reset (dev outbox), account deletion; projects owned by workspaces; immutable versions; artifact metadata + blob store; audit log; usage events; auth UI (sign up, sign in, reset, delete); tenant-isolation tests |
| 3 | Intake: ONNX validation report and graph summary; hardware categories (board/SoC, MCU, FPGA, accelerator, custom) with a supported-formats matrix; profile validation (units, required fields, consistency, size); scanned-PDF detection with OCR when available else a manual-review path; honest "planned" responses for schematic/PCB/RTL/netlist/assembly/firmware; capability detection endpoint and screen |
| 4 | Digital twin v1: trace schema (versioned), per-operator events, tensor lifetimes, working-memory timeline, energy attribution, hardware components and memory paths; execution timeline, memory map, thermal and energy views; 2D system map with playback; raw-data export |
| 5 | Design plans: select recommendations, re-analyse baseline / each change / combined, old-vs-new parameter table, versioned plans; data-driven cooling catalogue with stated sustainability criteria; exports (report MD/HTML/PDF, plan JSON, trace CSV/JSON, comparison CSV/JSON, runtime config, recipe zip, INT8 ONNX only when quantisation runs and validates) |
| 6 | Local ONNX Runtime CPU execution in a sandboxed subprocess with timeouts; measured per-operator trace from ORT profiling; measurement import (CSV/JSON); predicted vs measured and calibration per workspace; optional CUDA/TensorRT and Nebius adapters behind flags; usage minutes |
| 7 | Security review, accessibility pass, Playwright end-to-end suite, docs, demo data, deployment config (Docker + Postgres compose) |

## Explicitly out of scope (honest limits)

- Parsing schematics, PCB layouts, RTL, netlists, assembly or firmware images: shown as *planned*
  with the exact format we would need. Vendor *reports* (Vivado utilisation, ST Edge AI, Vela) are
  supported because a real parser exists.
- Generating changed PCB/RTL/chip files: design changes export as specifications and parameter
  files, labelled directional.
- 3D view: the trace and component schema carry layout hints so a 3D view can consume them later.
- Real power telemetry on the host: only where the OS exposes it (Linux RAPL); otherwise power is
  imported from logs or predicted, and labelled so.

# Release 2: simple first, powerful on demand

Audit (start of release 2): the platform is complete for experts but expert-first. Missing: guided
first run, beginner/expert presentation, decision bar, plain-language layer, global search, demo
projects, versioned hardware-system graph, KiCad/Verilog/HLS evidence parsers, 3D map, multi-node
thermal, runtime/memory-hierarchy effects, calibration-quality and evidence views, remote-device
agent, measurement-quality grading, handoff/founder/reproducibility exports, MFA, email
verification, shared rate limits, observability, retention. Docker is not installed in the
development environment, so the image/compose run must be done on a machine with Docker.

| Slice | Content |
|---|---|
| A | Kelly-green/white identity; first-run wizard (use case → model → hardware → priority → analyse → one action); priority-aware ranking in the engine; beginner/expert density preference; decision bar; plain-language glossary and "Why does this matter?" help; project health; global search; five demo projects |
| B | Versioned hardware-system graph (components, connections, attributes, source, confidence); bounded parsers: KiCad PCB metadata, Verilog module/port subset, Vivado HLS reports; links from trace events to graph components |
| C | 3D system map (three.js) on the same graph + trace; playback, overlays, baseline-vs-plan; logical vs physical layout labelling; 2D/table fallback |
| D | Multi-node thermal network when thermal paths exist (labelled estimate); memory hierarchy and runtime effects (load, transfer, fusion, queueing, offline); calibration-quality dashboard; per-value evidence panel |
| E | Cooling catalogue v2: applicability by power, enclosure, ambient, maintenance; lifecycle and repairability; per-heat-source rationale |
| F | Remote-device agent protocol (device tokens, signed run requests, approved artifacts only, no shell) + reference agent + simulator; measurement-quality grading |
| G | Hardware handoff package, founder/customer summary, reproducibility manifest, evidence package, system-graph export |
| H | TOTP MFA, email verification, database-backed rate limiter, structured logs, liveness/readiness, metrics, error-reporting adapter, retention and workspace data export, threat model, dependency scanning, deployment docs |
