#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/contracts/.venv/Scripts/python.exe"
if [ ! -f "$PY" ]; then
  PY="${ROOT}/contracts/.venv/bin/python"
fi
"$PY" "${ROOT}/scripts/e2e_local.py"
