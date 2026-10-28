"""Start the app for browser end-to-end tests on a fresh, throwaway database.

    python scripts/e2e_server.py [port]

Never touches a developer's database: everything lives in a new temporary folder.
"""

import os
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="gc_e2e_"))
os.environ.update({
    "GC_DB_URL": f"sqlite:///{(root / 'e2e.db').as_posix()}",
    "GC_DATA_DIR": str(root / "data"),
    "GC_ENV": "development",
    "GC_SCRYPT_LOG2_N": "12",
    "GC_SIGNUP_RATE_PER_HOUR": "1000",
    "GC_ANALYSE_RATE_PER_MIN": "0",
})
backend = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend))
os.chdir(backend)

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8020, log_level="warning")
