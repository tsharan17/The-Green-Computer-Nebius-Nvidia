# Security model

What protects users and their data, how it is tested, and what is still open. Report a problem
privately to the maintainers rather than in a public issue.

## Accounts and sessions

- Passwords: scrypt (Python standard library, N=2^14, r=8, p=1, 16-byte salt), at least 10
  characters, a short block-list of common passwords, re-hashed on sign-in when parameters change.
- Sessions and API tokens: 256-bit random tokens; only their SHA-256 is stored. Browser sessions use
  an HttpOnly, SameSite=Lax cookie (Secure in production or over HTTPS). Sessions expire
  (`GC_SESSION_DAYS`); each can be revoked; a password change or reset signs out other sessions.
- Password reset: single-use, one-hour token, stored hashed. The response is identical whether or not
  the address has an account. The reset link appears in the response only outside production and
  only when no mail server is configured (development convenience); otherwise it is emailed.
- Sign-in errors do not reveal whether an account exists; failed sign-ins are audited and rate
  limited per address and per email. Sign-up reveals that an email is taken (a deliberate usability
  trade-off; set `GC_ALLOW_SIGNUP=0` to close sign-up).
- Account deletion requires the password and a typed confirmation; it deletes every workspace the
  user owns alone, with all rows and stored files, and anonymises the user in shared workspaces.

- Email confirmation: a single-use, 48-hour link (stored hashed) is sent at sign-up and on request.
  With `GC_REQUIRE_EMAIL_VERIFICATION=1` an unconfirmed account can read but not change anything.
- Optional two-step sign-in (TOTP, RFC 6238). After the password, the server answers with a signed,
  five-minute challenge instead of a session. The session starts only after a current code (a step
  newer than the last one used, so a code cannot be replayed) or a single-use recovery code.
  - The authenticator seed is sealed at rest (encrypt-then-MAC with keys derived from the server key).
  - Recovery codes are stored as SHA-256 hashes.
  - Code attempts are rate-limited per user and per address.
  - Turning it off needs the password and a code. API tokens cannot enrol.

## Server secrets

- `server.key` (or `GC_SECRET_KEY`) seals authenticator seeds and signs sign-in challenges.
  `agent_signing.key` (or `GC_AGENT_SIGNING_SECRET`) derives per-device keys for device agents.
  Both are random keys created once in the data directory with owner-only permissions. They are
  never in source, and backups must include them (docs/backup-restore.md).

## Rate limiting

- Sign-in (per address and per email), second-step codes (per user and per address), sign-up,
  password reset, email confirmation, pairing codes and the AI assistant are limited.
- On PostgreSQL the limiter is shared by every worker and machine: sliding-window counters in the
  `rate_limits` table with atomic upserts (`GC_RATE_LIMIT_BACKEND=database`). On SQLite (one
  process) it stays in memory.

## Device agents

- Devices pair with a one-time code (15 minutes, stored hashed, rate-limited) and then authenticate
  with a device token (stored hashed). They never use cookies, so a browser session cannot act as a
  device.
- Every run request is HMAC-signed with a per-device key, carries a nonce and an expiry, names one
  allow-listed action, and is bound to an approved file's SHA-256. Results come back signed and are
  quality-checked before they become measurements. The agent runs no shell and enforces its own
  local policy. Revocation is immediate. See docs/remote-agent.md.

## Cross-site protections

- State-changing requests authenticated by cookie must carry `X-GC-CSRF: 1` (browsers cannot add
  custom headers cross-site without a CORS pre-flight, which the API never grants); sign-in and
  sign-up need it too (login CSRF). Requests whose `Origin` is neither the server nor an allowed
  origin are refused.
- Headers: Content-Security-Policy (`default-src 'self'`, no inline scripts, `object-src 'none'`),
  X-Frame-Options DENY, nosniff, no referrer. Fonts are bundled; the UI loads nothing from third
  parties. User text in reports is HTML-escaped; downloads are served as attachments with sanitised
  file names.

## Tenant isolation

- Every owned row carries `workspace_id`; repository and service functions take the caller's
  workspace and filter by it; objects in another workspace answer 404 so their existence is not
  revealed. Membership is checked for the `X-GC-Workspace` header on every request. Roles: owner,
  editor, viewer (read-only).
- Shared library data (built-in devices, models, committed measurements) has no owner and is
  read-only. Measurements, calibrations, custom devices and uploaded models are private.
- Uploaded files live in a per-workspace folder of the blob store, so deleting a tenant cannot touch
  another's files.

## Untrusted input

- Uploads have size limits (100 MB models, 20 MB other files); pickle/torch archives are refused;
  ONNX is parsed as protobuf without executing anything; YAML uses `safe_load`; PDFs are read as
  text; ELF is read from its section table only.
- Running a model happens only in a separate worker process with a wall-clock limit, one run per
  workspace at a time; ONNX Runtime loads no custom-operator libraries. Arguments are passed without
  a shell.
- The optional AI assistant can only call engine tools; it cannot change hard requirements, and a
  guardrail rejects any number not present in a tool result.

## Audit and usage

Uploads (accepted or refused), analyses, versions, plans, exports, measurement imports, runs,
member changes and account events are recorded with the acting user and address. Usage events
meter analyses, exports, execution seconds and storage. Account security events are audited too:
sign-in challenges, failed second steps, enabling and disabling MFA, email confirmation, device
pairing and revocation, and approved device runs.

## Data lifecycle

- Retention: owners set days per category (exports, traces, device runs, unreferenced uploads,
  audit log). Applying the rules shows a dry run first; the audit log keeps at least 90 days.
  `scripts/retention.py` applies the rules on a schedule.
- Export: owners download every record and stored file of a workspace as one zip with a SHA-256
  manifest. Secrets and other members' emails are left out.
- Deletion: deleting an account returns a verification that counts what is left of its
  workspaces, projects and user records in every table that can hold them, and checks that the
  file folders are gone.

## Observability and privacy

- Every response has an `X-Request-ID`. Logs (text or JSON) carry method, route template, status
  and duration, never raw paths with ids, queries, bodies, cookies or (unless `GC_LOG_IPS=1`)
  addresses.
- `/metrics` (Prometheus) is off unless `GC_METRICS_TOKEN` is set, and then needs that bearer
  token. Labels are route templates only.
- Unhandled errors are answered with a request id and nothing else. Reports (log, your own webhook,
  or Sentry with `send_default_pii=False`) carry the exception type, a message scrubbed of emails
  and long tokens, and file:line frames.
- Telemetry is off. It is sent only when the operator sets both `GC_TELEMETRY=1` and a destination,
  and it contains aggregate counts and versions only. `GET /api/v1/telemetry/preview` and
  `scripts/telemetry.py` show exactly what would be sent.

## Threat model

| Asset | Threat | Mitigation | Residual risk |
|---|---|---|---|
| Accounts | password guessing, credential stuffing | scrypt, rate limits (shared on PostgreSQL), optional TOTP, uniform errors | users without MFA and with reused passwords |
| Sessions | theft via XSS or CSRF | HttpOnly SameSite cookie, CSRF header, Origin check, strict CSP, no third-party scripts | a compromised browser or extension |
| Tenant data | cross-tenant access (IDOR) | workspace filter in every query, 404 for foreign ids, tests over every route | logic bugs in new endpoints (tests guard every owned object) |
| Uploaded files | malicious ONNX, PDFs, archives | size limits, no pickle, protobuf parsing, worker process with time limit, safe YAML, bounded design-file parsers | parser bugs in pypdf/onnx; no malware scan |
| Devices | forged or replayed run requests, rogue server commands | per-device HMAC, nonce, expiry, action allow-list, SHA-256 pinning, local policy, no shell | a compromised device can send false (but signed) timings; quality checks flag noise, not lies |
| Measurements | fabricated results | provenance labels, quality checks, only paired devices or imports; calibration accepted only when leave-one-out error improves | a dishonest member of the workspace |
| Server secrets | key theft from disk or backups | keys outside the database, owner-only files, env override; backups must be protected | whoever holds the data directory and the database can read sealed values |
| Operations | log or metric leakage of personal data | route templates, scrubbed error reports, tokens on /metrics, telemetry off | operator-configured webhooks receive scrubbed reports |
| Supply chain | vulnerable dependencies | pinned frontend dependencies, `pip-audit` and `npm audit` in CI | zero-day vulnerabilities |

## Tests that guard this

## Tests that guard this

- `tests/api/test_security.py`: every API operation (from the OpenAPI schema) returns 401 without a
  session except an explicit public list; every state-changing operation returns 403 without the
  CSRF header; cookie flags and headers; escaping and safe file names.
- `tests/api/test_auth_tenancy.py`: cross-tenant access to every owned endpoint returns 404; roles;
  reset and session revocation; deletion removes rows and files.
- `tests/api/test_account_security.py`: RFC 6238 vectors, sealed secrets refuse tampering, email
  confirmation with and without enforcement, two-step sign-in (bad codes, replay, single-use
  recovery codes, forged challenges), API tokens cannot enrol, the database limiter shared by two
  app instances.
- `tests/api/test_remote_agent.py`: signed requests, tampering, replay, revocation and tenancy for
  device agents.
- `tests/api/test_workspace_data.py` and `tests/api/test_operations.py`:
  - retention dry run and apply, an export free of secrets, owner-only data tools, deletion
    verification;
  - readiness, request ids, metrics behind a token, scrubbed error reports, telemetry off and free
    of personal data.
- `frontend/e2e`: a second account cannot open the first account's project in the browser;
  email confirmation and two-step sign-in in the browser (`account-security.spec.ts`).

## Known limits

- On SQLite the rate limiter is per process (fine for one worker). Use PostgreSQL (or
  `GC_RATE_LIMIT_BACKEND=database`) before running several workers.
- Two-step sign-in is optional per account; there is no workspace policy that forces it yet. There
  are no WebAuthn or passkeys.
- No malware scanning of uploaded files (they are never executed except ONNX models in the worker).
- The datasheet reader and OCR operate on untrusted PDFs in-process (pypdf); keep the upload limit.
