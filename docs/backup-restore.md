# Backup and restore

A complete backup has three parts. Lose any one and the restore is incomplete:

1. **The database**: every account, workspace, project, analysis, plan, measurement, audit entry
   and device.
2. **The data directory** (`GC_DATA_DIR`): uploaded files and exports (`blobs/ws<id>/…`), plus the
   server's own keys:
   - `server.key` seals authenticator secrets. Without it, users with two-step sign-in must be reset
     by an administrator (clear `mfa_*` on their user row) and enrol again;
   - `agent_signing.key` signs device-agent requests. Without it, every device must pair again;
   - `installation_id` is only for telemetry.
3. **Configuration**: your `.env` or environment, including `GC_SECRET_KEY` and
   `GC_AGENT_SIGNING_SECRET` if you set them instead of using the key files.

Protect backups like the live system: they hold password hashes, sealed secrets and every
workspace's data.

## PostgreSQL

Back up (consistent, while running):

```
pg_dump --format=custom --no-owner --file=gc-$(date +%F).dump "$GC_DB_URL_PLAIN"
tar -czf gc-files-$(date +%F).tgz -C "$GC_DATA_DIR" .
```

`GC_DB_URL_PLAIN` is the same URL without `+psycopg`.

Restore into an empty database, then start the app:

```
createdb green_computer
pg_restore --no-owner --dbname "$GC_DB_URL_PLAIN" gc-2026-10-19.dump
tar -xzf gc-files-2026-10-19.tgz -C "$GC_DATA_DIR"
python -m alembic upgrade head          # newer app version? migrations are additive
curl -s https://your-host/api/v1/ready  # {"ready": true, ...}
```

With Docker Compose:

```
docker compose exec db pg_dump -U gc -Fc green_computer > gc.dump
docker compose cp app:/data ./gc-data-backup
```

## SQLite (single-machine installs)

Stop the app, or use SQLite's online backup, then copy the database file and the data directory
together:

```
sqlite3 green_computer.db ".backup gc-backup.db"
tar -czf gc-files.tgz -C gc_data .
```

## Check a restore

1. `GET /api/v1/ready` answers 200.
2. Sign in as a known user; open a project and its latest analysis.
3. Download an export and compare its SHA-256 with the export history (unchanged files hash the
   same).
4. Export a reproducibility manifest and check it with `scripts/reproduce.py`: the numbers recompute
   exactly.
5. A user with two-step sign-in can sign in, which proves `server.key` was restored.

## Per-workspace copies

Owners can download everything their workspace holds from **Account → Workspace data → Download
workspace export**: every record and stored file, with a hashed manifest. This is a portable copy
for the workspace's owners, not a server backup, and it cannot be re-imported automatically yet.

## Retention and deletion

- Retention rules (Account → Workspace data) remove old exports, traces, device runs, unreferenced
  uploads and audit entries older than 90 days or more. Run `scripts/retention.py --apply` on a
  schedule. Backups taken before a deletion still contain the deleted data, so expire old backups
  on the same schedule.
- Account deletion returns a verification: rows left per table (expected: none) and file folders
  left (expected: none).
