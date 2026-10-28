# The Green Computer: working notes for Claude

Vendor-neutral AI + hardware co-design platform: predict latency, memory, power, energy, heat,
throttling, cost and carbon for an AI model on a hardware system, recommend software/hardware
changes, compare plans, verify against real runs, export. Works fully offline; NVIDIA/Nebius and
LLM providers are optional.

Plan: `docs/IMPLEMENTATION_PLAN.md`. Progress and verification commands: `IMPLEMENTATION_STATUS.md`.
Product map (hand-drawn, image-only PDF): `docs/original-system-map.pdf.pdf`.

## Layout

- `backend/app/engine/` pure, deterministic physics + search + advice. No I/O, no network, no LLM.
- `backend/app/twin/` digital twin v1: execution trace, tensor lifetimes, memory timeline, components.
- `backend/app/providers/` interfaces: execution backends, AI provider, blob store, email.
- `backend/app/auth/` accounts, sessions, workspaces, access checks.
- `backend/app/api/v1/` FastAPI routers. `services/` orchestrates engine + DB. `db/` models + repo.
- `backend/app/migrations/` Alembic. Never edit an applied revision; add a new one.
- `backend/app/execution/` isolated worker (benchmark, quantise, compare) started as a subprocess.
- `frontend/src/` React + Vite. `pages/` and `twin/` (analysis views, hand-built SVG charts in `twin/chart.tsx`).
- `bench/` benchmark scripts for GPU jobs. `scripts/` dev, acceptance, helpers.

## Commands (Windows paths; use `bin/` instead of `Scripts/` on Linux/macOS)

```
cd backend && .venv/Scripts/python -m pytest -q          # backend tests
cd backend && .venv/Scripts/python -m ruff check .       # lint
cd backend && .venv/Scripts/python -m mypy app           # types
cd backend && .venv/Scripts/python -m alembic upgrade head  # migrate (also runs at start-up)
cd frontend && npm run typecheck && npm run lint && npm run build
cd frontend && npx playwright test                        # e2e + axe (starts its own server on :8020)
scripts/api_docs.py regenerates docs/api.md; GC_TEST_PG_URL=... runs tests/test_postgres.py
scripts/dev.ps1                                           # build UI + serve on :8000
```

## Rules that matter here

- Never present an analytical estimate as measured, simulated or generated. Every metric carries
  `provenance` and a `low..high` band. Analytical traces are "predicted"; only execution backends or
  imported logs produce "measured".
- Never fall back silently from measured to predicted; show both, labelled.
- The engine computes every number. The optional AI assistant only explains, and its guardrail
  rejects numbers that are not in tool results.
- Tenant isolation: every owned row has `workspace_id`; always resolve projects/models/devices/
  artifacts through the access helpers in `app/auth/deps.py`, `api/v1/routes.project_for`, `services/model_lookup.py` and
  `data_loader.load_devices(workspace_id)`. Built-in
  library rows have `workspace_id = NULL` and are read-only.
- Unsupported inputs (schematic, PCB, RTL, netlist, assembly, firmware) return an honest "planned"
  response naming the exact format needed; never a fake success.
- Design/chip/circuit recommendations are directional what-ifs unless backed by real tool data.
- No secrets in source. Configuration comes from env vars; `.env.example` lists them.
- Schema changes go through a new Alembic revision; keep API changes additive under `/api/v1`.
- Match the surrounding style: `from __future__ import annotations`, pydantic v2 models,
  `# fmt: skip` on dense literal blocks, plain-language user-facing strings.
