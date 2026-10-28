"""Regenerate docs/api.md from the live OpenAPI schema (so the reference never drifts).

    backend/.venv/Scripts/python scripts/api_docs.py
"""

import sys
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))

from app.main import create_app  # noqa: E402

spec = create_app("sqlite://").openapi()
PUBLIC = {"/api/v1/health", "/api/v1/auth/config", "/api/v1/auth/signup", "/api/v1/auth/signin",
          "/api/v1/auth/password/forgot", "/api/v1/auth/password/reset"}
groups: dict[str, list[str]] = defaultdict(list)
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        tag = (op.get("tags") or ["core"])[0]
        summary = (op.get("description") or op.get("summary") or "").strip().splitlines()[0]
        auth = "public" if path in PUBLIC else "session"
        groups[tag].append(f"| `{method.upper()}` | `{path}` | {auth} | {summary.replace('|', '/')} |")
lines = [
    "# API reference (v1)", "",
    "Generated from the OpenAPI schema by `scripts/api_docs.py`; the live schema and interactive docs are at",
    "`/docs` on a running server.", "",
    "## Conventions", "",
    "- **Versioning.** Everything is under `/api/v1`. Within v1 changes are additive (new endpoints, new optional",
    "  fields); a breaking change gets `/api/v2`. Stored documents carry their own schema ids",
    "  (`green-computer.trace/1`, `green-computer.plan/1`, `green-computer.deployment/1`, ...).",
    "- **Authentication.** Browser: `POST /auth/signin` sets an HttpOnly session cookie; every state-changing call",
    "  must send `X-GC-CSRF: 1`. Scripts: create a token with `POST /auth/tokens` and send",
    "  `Authorization: Bearer <token>` (no CSRF header needed).",
    "- **Workspace.** `X-GC-Workspace: <id>` selects a workspace you belong to (default: your own). Objects in",
    "  other workspaces answer 404.",
    "- **Errors.** JSON `{\"detail\": \"...\"}` with a plain-language message: 401 sign in, 403 not allowed /",
    "  CSRF, 404 not found (or not yours), 409 conflict / backend unavailable, 413 too large, 422 invalid,",
    "  429 rate limited (with `Retry-After`), 503 optional integration not configured.",
    "- **Honesty.** Every metric is `{value, low, high, unit, provenance}`; provenance is one of analytical,",
    "  calibrated, measured, vendor_spec, assumed, extracted_unverified. Traces are `predicted` or `measured`.",
    "",
]
for tag in sorted(groups):
    lines += [f"## {tag.title()}", "", "| Method | Path | Auth | What it does |", "|---|---|---|---|", *sorted(groups[tag]), ""]
(root / "docs" / "api.md").write_text("\n".join(lines), encoding="utf-8")
print(f"{sum(len(v) for v in groups.values())} operations written to docs/api.md")
