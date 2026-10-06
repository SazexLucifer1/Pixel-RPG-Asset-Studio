#!/usr/bin/env bash
# Run the application from source (Linux/macOS development).  ./scripts/run_dev.sh [--mock]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements-dev.txt"
fi
PYTHONPATH="$ROOT/src" exec "$ROOT/.venv/bin/python" -m pixel_rpg_studio "$@"
