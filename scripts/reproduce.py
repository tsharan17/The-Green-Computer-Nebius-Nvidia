"""Recompute a project's numbers from a reproducibility manifest and check them. Offline.

    cd backend && .venv/Scripts/python ../scripts/reproduce.py path/to/<project>-reproducibility.json

Exit code 0 when every headline number matches (relative tolerance 1e-9) and the captured inputs
match their hashes; 1 otherwise, with the differences printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.handoff_service import verify_manifest  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    doc = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out = verify_manifest(doc)
    print(f"{'Reproduced' if out['ok'] else 'NOT reproduced'}: {out['compared']} numbers compared.")
    for p in out["problems"]:
        print(f"  - {p}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
