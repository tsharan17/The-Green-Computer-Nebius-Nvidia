"""Show the privacy-safe telemetry payload, and send it only when the operator enabled it.

    cd backend && .venv/Scripts/python ../scripts/telemetry.py          # print what would be sent
    GC_TELEMETRY=1 GC_TELEMETRY_URL=https://your-endpoint ... ../scripts/telemetry.py --send
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.db.session import make_engine, make_session_factory  # noqa: E402
from app.providers.storage import default_data_dir  # noqa: E402
from app.services import telemetry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()
    engine = make_engine(s.db_url)
    with make_session_factory(engine)() as db:
        payload = telemetry.snapshot(db, default_data_dir(s.db_url, s.data_dir), engine.dialect.name)
    print(json.dumps(payload, indent=1))
    if a.send:
        if not telemetry.enabled():
            print("Not sent: set GC_TELEMETRY=1 and GC_TELEMETRY_URL to enable.", file=sys.stderr)
            return 1
        print("Sent." if telemetry.send(payload) else "The endpoint refused it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
