#!/bin/bash
set -euo pipefail

LOG_DIR="${WORKER_LOG_DIR:-/home/LogFiles}"
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/worker-startup.log" 2>&1

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] startup_worker.sh boot"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="${APP_PATH:-$SCRIPT_DIR}"
if [[ ! -f "$APP_ROOT/worker_entrypoint.py" && -f "/home/site/wwwroot/worker_entrypoint.py" ]]; then
  APP_ROOT="/home/site/wwwroot"
fi

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT:${PYTHONPATH:-}"

PYTHON_BIN=""
if [[ -x "/opt/python/3.12.12/bin/python" ]]; then
  PYTHON_BIN="/opt/python/3.12.12/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif [[ -x "$APP_ROOT/antenv/bin/python" ]]; then
  PYTHON_BIN="$APP_ROOT/antenv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] no usable python interpreter found"
  exit 127
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] app_root=$APP_ROOT"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] python_bin=$PYTHON_BIN"
ls -l "$APP_ROOT/worker_entrypoint.py" || true
ls -l "$APP_ROOT/antenv.tar.gz" || true

exec "$PYTHON_BIN" "$APP_ROOT/worker_entrypoint.py"
