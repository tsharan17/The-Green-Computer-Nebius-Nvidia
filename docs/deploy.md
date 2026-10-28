# Deploying The Green Computer

Two supported shapes:
- self-hosted, on one machine or VM with Docker Compose, or without containers;
- one managed platform, Render, as a Docker web service with managed PostgreSQL and a persistent
  disk.

A third shape, for the free public demo, is Google Cloud Run with a free Neon PostgreSQL database and a
keep-warm job: see [deploy/cloudrun/README.md](../deploy/cloudrun/README.md) (deployed and checked
on 27 October 2026).

None of them needs any external SaaS, except that third one. GPU jobs, the AI assistant, error webhooks and telemetry are all
optional and off by default.

> Status: the Dockerfile and `docker-compose.yml` are written and checked for syntax, but Docker
> was not available in the development environment, so the images have **not** been built or run
> there. The app, migrations and a multi-user journey **were** run on PostgreSQL 16 (see
> IMPLEMENTATION_STATUS.md). Treat the first container run as a smoke test.

## What every deployment needs

| Need | Why |
|---|---|
| PostgreSQL 14+ (`GC_DB_URL=postgresql+psycopg://…`) | several workers, shared rate limits, backups |
| A persistent data directory (`GC_DATA_DIR`) | uploaded files, exports, `server.key`, `agent_signing.key`, `installation_id` |
| HTTPS in front (reverse proxy or the platform) | Secure cookies; device agents refuse plain http except to localhost |
| `GC_ENV=production`, `GC_PUBLIC_URL=https://your-host` | Secure cookies, no development links in responses, correct links in emails |
| SMTP (`GC_SMTP_*`) | password reset and email confirmation (`GC_REQUIRE_EMAIL_VERIFICATION=1` needs it) |

Health checks:
- `GET /api/v1/health` is liveness.
- `GET /api/v1/ready` is readiness: database reachable, schema at the latest migration, file
  storage writable. It answers 503 otherwise.

Migrations run at start-up (`GC_AUTO_MIGRATE=1`). With several instances, set
`GC_AUTO_MIGRATE=0` and run `python -m alembic upgrade head` once per release before starting them.

## Self-hosted with Docker Compose

```
cp .env.example .env            # set GC_PUBLIC_URL, GC_POSTGRES_PASSWORD, SMTP
docker compose up --build -d
docker compose ps               # app becomes "healthy" when /api/v1/ready answers 200
```

The compose file runs PostgreSQL 16 and the app (one uvicorn worker) with named volumes for the
database and files. Put a TLS-terminating proxy in front, for example Caddy:

```
gc.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

and set `GC_TRUST_PROXY=1` so client addresses (for rate limits and the audit log) come from
`X-Forwarded-For`.

### More workers

The rate limiter is shared through PostgreSQL, so you can raise uvicorn `--workers`. Two rules:
- run migrations once, separately (`GC_AUTO_MIGRATE=0` on the workers);
- keep `GC_DATA_DIR` on storage every worker sees.

Local execution runs one model at a time per workspace; it is enforced in the database.

## Self-hosted without containers (systemd)

```
python3.12 -m venv /opt/gc/venv
/opt/gc/venv/bin/pip install "/opt/gc/src/backend[postgres]"
cd /opt/gc/src/frontend && npm ci && npm run build      # writes backend/app/static
```

`/etc/systemd/system/green-computer.service`:

```
[Service]
User=gc
EnvironmentFile=/etc/green-computer.env
WorkingDirectory=/opt/gc/src/backend
ExecStart=/opt/gc/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers
Restart=on-failure
```

Retention on a timer (daily):

```
ExecStart=/opt/gc/venv/bin/python /opt/gc/src/scripts/retention.py --apply
```

## Managed: Render

A Docker web service, a managed PostgreSQL database and a persistent disk. Nothing below has been
deployed by this project. It is the configuration to start from, and Render bills you for it.

`render.yaml` (Blueprint) at the repository root, if you choose to use it:

```
databases:
  - name: gc-db
    plan: basic-256mb
    postgresMajorVersion: "16"
services:
  - type: web
    name: green-computer
    runtime: docker
    plan: starter
    healthCheckPath: /api/v1/ready
    disk: { name: gc-data, mountPath: /data, sizeGB: 10 }
    envVars:
      - { key: GC_ENV, value: production }
      - { key: GC_DATA_DIR, value: /data/files }
      - { key: GC_PUBLIC_URL, value: https://green-computer.onrender.com }
      - { key: GC_TRUST_PROXY, value: "1" }
      - key: GC_DB_URL
        fromDatabase: { name: gc-db, property: connectionString }   # change the scheme to postgresql+psycopg://
      - { key: GC_SECRET_KEY, generateValue: true }
      - { key: GC_AGENT_SIGNING_SECRET, generateValue: true }
```

Notes:
- Render's connection string starts with `postgresql://`. The app needs
  `postgresql+psycopg://`, so set `GC_DB_URL` by hand or add a start command that rewrites it.
- A persistent disk ties the service to one instance. For several instances, keep files on one
  instance or add an object-store blob backend (not built yet).
- `generateValue` keeps the secrets out of the repository. Store copies with your backups.

## Configuration reference

Every setting is an environment variable listed with comments in `.env.example`. Release 2 adds:
- `GC_SECRET_KEY` and `GC_AGENT_SIGNING_SECRET`;
- `GC_REQUIRE_EMAIL_VERIFICATION` and `GC_RATE_LIMIT_BACKEND`;
- `GC_LOG_FORMAT`, `GC_LOG_LEVEL`, `GC_LOG_IPS`;
- `GC_METRICS_TOKEN`;
- `GC_ERROR_REPORTER`, `GC_ERROR_WEBHOOK_URL`, `GC_SENTRY_DSN`;
- `GC_GIT_SHA`;
- `GC_TELEMETRY`, `GC_TELEMETRY_URL`.

## Monitoring

- Scrape `GET /metrics` with `Authorization: Bearer $GC_METRICS_TOKEN`. It provides:
  - request counts and duration histograms by route template;
  - unhandled errors and uptime;
  - whether rate limiting is shared.
- Alert on `/api/v1/ready` failing, on `gc_unhandled_errors_total` increasing, and on the p95 of
  `gc_http_request_duration_seconds` for `/api/v1/projects/{project_id}/optimise`.
- With `GC_LOG_FORMAT=json` each request is one JSON line with `request_id`, which users can quote
  from error messages.
