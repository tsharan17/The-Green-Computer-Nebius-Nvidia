# One-command local start (Windows PowerShell): installs deps, builds the UI, runs the server.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root/backend"
if (-not (Test-Path .venv)) { python -m venv .venv }
& .venv/Scripts/python -m pip install -q -e ".[dev]"
Set-Location "$root/frontend"
if (-not (Test-Path node_modules)) { npm install }
npm run build
Set-Location "$root/backend"
Write-Host "Open http://localhost:8000"
& .venv/Scripts/python -m uvicorn app.main:app --port 8000
