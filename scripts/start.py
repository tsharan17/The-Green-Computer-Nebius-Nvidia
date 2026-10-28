"""Start The Green Computer and open it in the browser. One command, safe to run again.

    Windows:      double-click "Start The Green Computer.bat" (in the project folder)
    macOS/Linux:  ./start.sh
    Any system:   python scripts/start.py [--port 8000] [--rebuild] [--no-browser]

First run: creates backend/.venv, installs the backend, and builds the web interface (needs Node.js).
Later runs: start in a few seconds. Data stays in green_computer.db and gc_data/ in the project folder.
Stop with Ctrl+C (or close the window).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
STATIC = BACKEND / "app" / "static" / "index.html"
WIN = os.name == "nt"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def run(cmd: list[str], cwd: Path, quiet: bool = True) -> None:
    out = subprocess.run(cmd, cwd=cwd, capture_output=quiet, text=True, shell=WIN and cmd[0] in ("npm", "npx"))
    if out.returncode != 0:
        if quiet:
            print(out.stdout[-3000:], out.stderr[-3000:], sep="\n")
        sys.exit(f"\nThis step failed: {' '.join(cmd)}  (in {cwd})")


def ensure_backend() -> None:
    marker = BACKEND / ".venv" / ".gc-installed"
    if not VENV_PY.exists():
        say("First run: creating the Python environment (one minute)…")
        run([sys.executable, "-m", "venv", ".venv"], BACKEND)
    if not marker.exists() or marker.stat().st_mtime < (BACKEND / "pyproject.toml").stat().st_mtime:
        say("Installing the backend (first run or after an update)…")
        run([str(VENV_PY), "-m", "pip", "install", "-q", "-e", "."], BACKEND)
        marker.write_text("ok")


def ensure_frontend(rebuild: bool) -> None:
    newest_src = max((p.stat().st_mtime for p in (FRONTEND / "src").rglob("*") if p.is_file()), default=0)
    stale = not STATIC.exists() or STATIC.stat().st_mtime < newest_src
    if not (rebuild or stale):
        return
    if shutil.which("npm") is None:
        if STATIC.exists():
            say("Node.js not found: using the interface that is already built.")
            return
        sys.exit("\nThe web interface is not built yet and Node.js is not installed. Install Node.js 20 or newer "
                 "from https://nodejs.org, then run this again.")
    if not (FRONTEND / "node_modules").exists():
        say("First run: installing the interface's packages…")
        run(["npm", "ci"], FRONTEND)
    say("Building the web interface…")
    run(["npm", "run", "build"], FRONTEND)


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    sys.exit(f"No free port between {start} and {start + 19}.")


def wait_ready(url: str, proc: subprocess.Popen, seconds: int = 90) -> bool:
    for _ in range(seconds * 2):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url + "/api/v1/ready", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Start The Green Computer")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--rebuild", action="store_true", help="rebuild the web interface")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    print("\n  The Green Computer\n")
    ensure_backend()
    ensure_frontend(a.rebuild)
    port = free_port(a.port)
    url = f"http://localhost:{port}"
    env = {**os.environ, "GC_DB_URL": os.environ.get("GC_DB_URL", f"sqlite:///{(ROOT / 'green_computer.db').as_posix()}"),
           "GC_DATA_DIR": os.environ.get("GC_DATA_DIR", str(ROOT / "gc_data")), "GC_PUBLIC_URL": url}  # fmt: skip
    say(f"Starting on {url} …")
    proc = subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                            cwd=BACKEND, env=env)  # fmt: skip
    try:
        if not wait_ready(url, proc):
            proc.terminate()
            sys.exit("\nThe server did not start. The messages above say why.")
        print(f"\n  Ready: {url}\n  Leave this window open while you use it. Press Ctrl+C to stop.\n", flush=True)
        if not a.no_browser:
            webbrowser.open(url)
        return proc.wait()
    except KeyboardInterrupt:
        say("Stopping…")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    sys.exit(main())
