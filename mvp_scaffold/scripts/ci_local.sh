#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[ci-local] compileall"
python -m compileall src tests scripts

echo "[ci-local] pytest"
python -m pytest -q

echo "[ci-local] event consistency"
python scripts/check_event_consistency.py

echo "[ci-local] done"
