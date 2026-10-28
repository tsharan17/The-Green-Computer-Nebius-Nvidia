# API reference (v1)

Generated from the OpenAPI schema by `scripts/api_docs.py`; the live schema and interactive docs are at
`/docs` on a running server.

## Conventions

- **Versioning.** Everything is under `/api/v1`. Within v1 changes are additive (new endpoints, new optional
  fields); a breaking change gets `/api/v2`. Stored documents carry their own schema ids
  (`green-computer.trace/1`, `green-computer.plan/1`, `green-computer.deployment/1`, ...).
- **Authentication.** Browser: `POST /auth/signin` sets an HttpOnly session cookie; every state-changing call
  must send `X-GC-CSRF: 1`. Scripts: create a token with `POST /auth/tokens` and send
  `Authorization: Bearer <token>` (no CSRF header needed).
- **Workspace.** `X-GC-Workspace: <id>` selects a workspace you belong to (default: your own). Objects in
  other workspaces answer 404.
- **Errors.** JSON `{"detail": "..."}` with a plain-language message: 401 sign in, 403 not allowed /
  CSRF, 404 not found (or not yours), 409 conflict / backend unavailable, 413 too large, 422 invalid,
  429 rate limited (with `Retry-After`), 503 optional integration not configured.
- **Honesty.** Every metric is `{value, low, high, unit, provenance}`; provenance is one of analytical,
  calibrated, measured, vendor_spec, assumed, extracted_unverified. Traces are `predicted` or `measured`.

## Auth

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/auth/config` | public | Auth Config |
| `GET` | `/api/v1/auth/legal/{doc}` | session | Legal Doc |
| `GET` | `/api/v1/auth/legal` | session | The legal documents and their current versions (public: shown before sign-up). |
| `GET` | `/api/v1/auth/me` | session | Me |
| `GET` | `/api/v1/auth/preferences` | session | Get Preferences |
| `GET` | `/api/v1/auth/sessions` | session | Sessions |
| `POST` | `/api/v1/auth/account/delete` | session | Delete Account |
| `POST` | `/api/v1/auth/email/resend` | session | Resend Verification |
| `POST` | `/api/v1/auth/email/verify` | session | Verify Email |
| `POST` | `/api/v1/auth/mfa/disable` | session | Mfa Disable |
| `POST` | `/api/v1/auth/mfa/enable` | session | Mfa Enable |
| `POST` | `/api/v1/auth/mfa/setup` | session | Start two-step sign-in: the secret to add to an authenticator app (shown once). |
| `POST` | `/api/v1/auth/password/change` | session | Change |
| `POST` | `/api/v1/auth/password/forgot` | public | Forgot |
| `POST` | `/api/v1/auth/password/reset` | public | Reset |
| `POST` | `/api/v1/auth/sessions/{session_id}/revoke` | session | Revoke |
| `POST` | `/api/v1/auth/signin/mfa` | session | Second step of sign-in: a code from the authenticator app, or a one-time recovery code. |
| `POST` | `/api/v1/auth/signin` | public | Signin |
| `POST` | `/api/v1/auth/signout` | session | Signout |
| `POST` | `/api/v1/auth/signup` | public | Signup |
| `POST` | `/api/v1/auth/terms/accept` | session | Accept the current versions of the Terms and Privacy Policy (after they changed). |
| `POST` | `/api/v1/auth/tokens` | session | An API token for the CLI or scripts (Authorization: Bearer ...). Shown once. |
| `PUT` | `/api/v1/auth/preferences` | session | Presentation only: display density and whether the first-run guide was completed. |

## Core

| Method | Path | Auth | What it does |
|---|---|---|---|
| `DELETE` | `/api/v1/hardware/custom/{device_id}` | session | Custom Delete |
| `DELETE` | `/api/v1/measurements/{measurement_id}` | session | Delete one of your own measurements (library measurements are read-only). |
| `GET` | `/api/v1/agent/status` | session | Agent Status |
| `GET` | `/api/v1/benchmarks/status` | session | Benchmark Status |
| `GET` | `/api/v1/benchmarks/{row_id}` | session | Get Benchmark |
| `GET` | `/api/v1/benchmarks` | session | List Benchmarks |
| `GET` | `/api/v1/calibration` | session | Calibration State |
| `GET` | `/api/v1/hardware/drafts/{draft_id}` | session | Draft |
| `GET` | `/api/v1/hardware/drafts` | session | Drafts |
| `GET` | `/api/v1/hardware/library` | session | Hardware Library |
| `GET` | `/api/v1/health` | public | Health |
| `GET` | `/api/v1/measurements` | session | List Measurements |
| `GET` | `/api/v1/models/library` | session | Model Library |
| `GET` | `/api/v1/projects/{project_id}/recipe.zip` | session | Recipe Zip |
| `GET` | `/api/v1/projects/{project_id}/recipe` | session | Recipe |
| `GET` | `/api/v1/projects/{project_id}/report` | session | Report |
| `GET` | `/api/v1/projects/{project_id}/runs/{run_id}` | session | Project Run |
| `GET` | `/api/v1/projects/{project_id}/runs` | session | Project Runs |
| `GET` | `/api/v1/projects/{project_id}/versions` | session | Project Versions |
| `GET` | `/api/v1/projects/{project_id}` | session | Get Project |
| `GET` | `/api/v1/projects` | session | List Projects |
| `GET` | `/api/v1/verify` | session | Verify |
| `POST` | `/api/v1/agent/chat` | session | Agent Chat |
| `POST` | `/api/v1/benchmarks/{row_id}/cancel` | session | Cancel Benchmark |
| `POST` | `/api/v1/benchmarks` | session | Launch a Nebius Serverless Job on an NVIDIA GPU. 503 when jobs are not available. |
| `POST` | `/api/v1/calibration/refit` | session | Refit Calibration |
| `POST` | `/api/v1/files/inspect` | session | Inspect File |
| `POST` | `/api/v1/hardware/custom/preview` | session | What the wizard would create, without saving it (live preview of derived values). |
| `POST` | `/api/v1/hardware/custom` | session | Custom Create |
| `POST` | `/api/v1/hardware/drafts/{draft_id}/confirm` | session | Only a person can confirm: the agent has no tool for this. |
| `POST` | `/api/v1/hardware/drafts/{draft_id}/reject` | session | Reject Draft |
| `POST` | `/api/v1/hardware/import` | session | Import Hardware |
| `POST` | `/api/v1/hardware/manual` | session | Manual Draft |
| `POST` | `/api/v1/hardware/parse-report` | session | Pull numbers out of a Vivado / ST Edge AI / Vela report or a power-meter CSV. |
| `POST` | `/api/v1/hardware/profile` | session | Register a full device profile the user uploaded (after seeing what it contains). |
| `POST` | `/api/v1/measurements/import` | session | Body is the raw text. `bench_json` carries everything; the power-trace formats need the |
| `POST` | `/api/v1/measurements/replay` | session | Re-import the library measurements committed with the app (shared, idempotent). |
| `POST` | `/api/v1/measurements` | session | Add Measurements |
| `POST` | `/api/v1/models/manual` | session | Add Manual Model |
| `POST` | `/api/v1/models/upload` | session | Raw ONNX bytes in the request body (Content-Type: application/octet-stream). |
| `POST` | `/api/v1/projects/{project_id}/archive` | session | Archive Project |
| `POST` | `/api/v1/projects/{project_id}/evaluate` | session | Evaluate Project |
| `POST` | `/api/v1/projects/{project_id}/layers` | session | Where the time goes, layer by layer, for a configuration (default: the current setup). |
| `POST` | `/api/v1/projects/{project_id}/optimise` | session | Optimise Project |
| `POST` | `/api/v1/projects/{project_id}/versions/{version}/restore` | session | Make an old version's inputs current again (as a new version: history is never rewritten). |
| `POST` | `/api/v1/projects` | session | Create Project |
| `PUT` | `/api/v1/projects/{project_id}` | session | Update Project |

## Digital Twin

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/traces/{trace_id}/export` | session | Export Trace |
| `GET` | `/api/v1/projects/{project_id}/traces/{trace_id}/figures/{name}` | session | A matplotlib/seaborn figure (PNG) drawn from a stored trace's own numbers. |
| `GET` | `/api/v1/projects/{project_id}/traces/{trace_id}` | session | Get Trace |
| `GET` | `/api/v1/projects/{project_id}/traces` | session | List Traces |
| `POST` | `/api/v1/projects/{project_id}/traces` | session | Predict the operator-level execution of one batch for the current setup (`baseline`), the |

## Execution

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/execution/backends` | session | Backends |
| `GET` | `/api/v1/projects/{project_id}/executions/{job_id}` | session | Get Run |
| `GET` | `/api/v1/projects/{project_id}/executions` | session | List Runs |
| `POST` | `/api/v1/projects/{project_id}/executions` | session | Run |

## Guide

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/guide/use-cases` | session | Use Cases |
| `GET` | `/api/v1/projects-health` | session | Projects Health |
| `GET` | `/api/v1/search` | session | Search |
| `POST` | `/api/v1/guide/demo` | session | Add five analysed example projects (MCU, edge camera, FPGA, GPU server, cloud). Idempotent. |
| `POST` | `/api/v1/guide/start` | session | Create a project from a use case (or from scratch) and, by default, analyse it at once. |

## Intake

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/capabilities` | session | Capability Report |
| `GET` | `/api/v1/intake/formats` | session | Formats |
| `GET` | `/api/v1/intake/templates/device-profile.yaml` | session | Profile Template |
| `POST` | `/api/v1/intake/upload` | session | Upload |
| `POST` | `/api/v1/intake/validate/device-profile` | session | Validate Device Profile |

## Operations

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/ready` | session | 200 when this instance can serve: database reachable, schema at the latest migration, file |
| `GET` | `/api/v1/telemetry/preview` | session | Exactly what would be sent, and whether sending is on (off unless the operator enabled it). |

## Physics And Evidence

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/calibration/quality` | session | Calibration Quality |
| `GET` | `/api/v1/projects/{project_id}/evidence` | session | Every headline metric with its band, provenance, the profile facts and inputs behind it, and |
| `GET` | `/api/v1/projects/{project_id}/physics` | session | Multi-node thermal attribution (labelled against the lumped decision model and any measured |

## Plans And Exports

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/exports/optimised-models` | session | Optimised model files and whether the platform produces them (only real, checked ones are). |
| `GET` | `/api/v1/projects/{project_id}/cooling` | session | Every applicable catalogue cooling option evaluated for this project, ranked by stated criteria. |
| `GET` | `/api/v1/projects/{project_id}/exports/catalog` | session | Export Catalog |
| `GET` | `/api/v1/projects/{project_id}/exports/{export_id}/download` | session | Download Export |
| `GET` | `/api/v1/projects/{project_id}/exports` | session | List Exports |
| `GET` | `/api/v1/projects/{project_id}/plan-options` | session | Every change that can go into a plan, from the last analysis plus applicable catalogue cooling. |
| `GET` | `/api/v1/projects/{project_id}/plans/{plan_id}` | session | Get Plan |
| `GET` | `/api/v1/projects/{project_id}/plans` | session | List Plans |
| `POST` | `/api/v1/projects/{project_id}/exports` | session | Create Export |
| `POST` | `/api/v1/projects/{project_id}/plans/analyse` | session | Re-analyse a selection: baseline, each change alone, and all of them combined (not saved). |
| `POST` | `/api/v1/projects/{project_id}/plans` | session | Save Plan |
| `POST` | `/api/v1/reproduce/verify` | session | Recompute the numbers in a reproducibility manifest with this engine and compare them. |

## Remote Devices

| Method | Path | Auth | What it does |
|---|---|---|---|
| `DELETE` | `/api/v1/remote-devices/{device_id}` | session | Revoke |
| `GET` | `/api/v1/device-agent/jobs/next` | session | Agent Next |
| `GET` | `/api/v1/device-agent/jobs/{job_id}/artifact` | session | Agent Artifact |
| `GET` | `/api/v1/remote-devices` | session | List Devices |
| `GET` | `/api/v1/remote-jobs` | session | List Jobs |
| `POST` | `/api/v1/device-agent/heartbeat` | session | Agent Heartbeat |
| `POST` | `/api/v1/device-agent/jobs/{job_id}/result` | session | Agent Result |
| `POST` | `/api/v1/device-agent/pair` | session | Exchange a one-time pairing code for a device token and signing key (shown to the agent once). |
| `POST` | `/api/v1/remote-devices/{device_id}/jobs` | session | Approve one run of a validated ONNX file on this device; the request is signed and expires in an hour. |
| `POST` | `/api/v1/remote-devices/{device_id}/pairing-code` | session | Repair |
| `POST` | `/api/v1/remote-devices` | session | Register a device. The pairing code is shown once and works for 15 minutes. |

## System Graph

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/system-graph/diff` | session | Graph Diff |
| `GET` | `/api/v1/projects/{project_id}/system-graph/sources` | session | Uploaded, parsed design files and reports in this workspace that the graph can take in. |
| `GET` | `/api/v1/projects/{project_id}/system-graph/versions` | session | Graph Versions |
| `GET` | `/api/v1/projects/{project_id}/system-graph/{version}` | session | Graph Version |
| `GET` | `/api/v1/projects/{project_id}/system-graph` | session | The latest saved graph, or version 0 built from the device profile (not saved). |
| `POST` | `/api/v1/projects/{project_id}/system-graph/from-files` | session | Add what uploaded design files state (KiCad, Verilog, Vivado, HLS) as a new graph version. |
| `POST` | `/api/v1/projects/{project_id}/system-graph` | session | Save an edited graph as a new version (validated: unique ids, existing parents and ends). |

## Workflow

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/cooling-sweep` | session | Temperature over ambient x input rate for every cooling option, and power mode x cooling. |
| `GET` | `/api/v1/projects/{project_id}/figures/{name}` | session | Seaborn/matplotlib cooling figures (PNG) drawn from the engine's cooling sweep. |
| `GET` | `/api/v1/projects/{project_id}/pipeline` | session | Each workflow stage with its stored record and whether it is done, ready, blocked or stale. |
| `GET` | `/api/v1/projects/{project_id}/recommendations` | session | Software and hardware recommendations from the latest analysis, ranked by the project's |
| `GET` | `/api/v1/projects/{project_id}/verification` | session | Verification and Requirements Manager report for the project's current inputs. |
| `POST` | `/api/v1/projects/{project_id}/recommendations/ai` | session | Candidate changes from the optional language model. Sends only the engine's numbers and the |

## Workspace

| Method | Path | Auth | What it does |
|---|---|---|---|
| `DELETE` | `/api/v1/artifacts/{artifact_id}` | session | Remove the stored bytes; the metadata stays (marked deleted) for the audit trail. |
| `DELETE` | `/api/v1/workspaces/current/members/{user_id}` | session | Remove Member |
| `GET` | `/api/v1/artifacts/{artifact_id}/download` | session | Download Artifact |
| `GET` | `/api/v1/artifacts/{artifact_id}` | session | Artifact |
| `GET` | `/api/v1/artifacts` | session | Artifacts |
| `GET` | `/api/v1/audit` | session | Audit Log |
| `GET` | `/api/v1/usage` | session | Usage |
| `GET` | `/api/v1/workspaces/current/export` | session | Owner only. Everything the workspace holds, as one zip with a hashed manifest. |
| `GET` | `/api/v1/workspaces/current/members` | session | Members |
| `GET` | `/api/v1/workspaces/current/retention` | session | Retention Rules |
| `GET` | `/api/v1/workspaces` | session | List Workspaces |
| `PATCH` | `/api/v1/workspaces/current` | session | Rename Workspace |
| `POST` | `/api/v1/workspaces/current/members` | session | Add an existing account to this workspace (it must already have signed up). |
| `POST` | `/api/v1/workspaces/current/retention/apply` | session | Owner only. With dry_run (the default) nothing is deleted; the counts say what would go. |
| `POST` | `/api/v1/workspaces` | session | Create Workspace |
| `PUT` | `/api/v1/workspaces/current/retention` | session | Owner only. Days to keep each category; empty keeps it for ever. |
