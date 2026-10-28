"""Apply every workspace's retention rules (for cron or a systemd timer).

    cd backend && .venv/Scripts/python ../scripts/retention.py            # dry run: report only
    cd backend && .venv/Scripts/python ../scripts/retention.py --apply    # delete what the rules say

Uses GC_DB_URL and GC_DATA_DIR like the server. Workspaces without rules are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import WorkspaceRow  # noqa: E402
from app.db.session import make_engine, make_session_factory  # noqa: E402
from app.providers.storage import LocalBlobStore, default_data_dir  # noqa: E402
from app.services.workspace_data import apply_retention, get_retention  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete (default is a dry run)")
    a = ap.parse_args(argv)
    s = get_settings()
    engine = make_engine(s.db_url)
    blobs = LocalBlobStore(default_data_dir(s.db_url, s.data_dir) / "blobs")
    with make_session_factory(engine)() as db:
        for ws in db.scalars(select(WorkspaceRow.id)):
            if not any(get_retention(db, ws).values()):
                continue
            out = apply_retention(db, blobs, ws, None, dry_run=not a.apply)
            print(json.dumps({"workspace": ws, **out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
