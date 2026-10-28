#!/usr/bin/env bash
# One-command local start (macOS/Linux/Git Bash): installs deps, builds the UI, runs the server.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/backend"
[ -d .venv ] || python -m venv .venv
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m pip install -q -e ".[dev]"
cd "$root/frontend"
[ -d node_modules ] || npm install
npm run build
cd "$root/backend"
echo "Open http://localhost:8000"
exec "$PY" -m uvicorn app.main:app --port 8000
