#!/usr/bin/env bash
# Starts the rdb API (port 8000) and Builder UI (port 5173). Ctrl+C stops both.
# Usage: ./scripts/dev.sh [--refresh-env]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_PROFILE=concrete AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2

# Refuse to start if the ports are taken (usually a leftover server)
for port in 8000 5173; do
  if lsof -i ":$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop whatever is using it and retry." >&2
    exit 1
  fi
done

# Generate the frontend env file on first run (or with --refresh-env)
ENV_LOCAL="$ROOT/frontend/builder/.env.local"
if [ ! -f "$ENV_LOCAL" ] || [ "${1:-}" = "--refresh-env" ]; then
  echo "Writing $ENV_LOCAL ..."
  "$ROOT/.venv/bin/python" "$ROOT/scripts/write_env.py"
fi

# Stop everything in this script's process group on exit
trap 'trap - INT TERM EXIT; kill 0 2>/dev/null || true' INT TERM EXIT

(cd "$ROOT/backend" && "$ROOT/.venv/bin/uvicorn" app.main:app --reload --port 8000 2>&1 | sed -u 's/^/[api] /') &
(cd "$ROOT/frontend/builder" && npm run dev 2>&1 | sed -u 's/^/[ui]  /') &

echo "API:     http://localhost:8000/health"
echo "Builder: http://localhost:5173"
wait
