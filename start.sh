#!/usr/bin/env sh
# Start The Green Computer and open it in the browser (macOS/Linux).
cd "$(dirname "$0")" && exec python3 scripts/start.py "$@"
