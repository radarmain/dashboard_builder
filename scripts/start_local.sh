#!/usr/bin/env bash
# Starts the rdb API (8000), Builder UI (5173) and Viewer (5174). Ctrl+C stops all three.
# Usage: ./scripts/start_local.sh [--refresh-env]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_PROFILE=concrete AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2

# Refuse to start if the ports are taken (usually a leftover server)
for port in 8000 5173 5174; do
  if lsof -i ":$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop whatever is using it and retry." >&2
    exit 1
  fi
done

# Generate the frontend env files on first run (or with --refresh-env)
if [ ! -f "$ROOT/frontend/builder/.env.local" ] || [ ! -f "$ROOT/frontend/viewer/.env.local" ] || [ "${1:-}" = "--refresh-env" ]; then
  echo "Writing frontend .env.local files ..."
  "$ROOT/.venv/bin/python" "$ROOT/scripts/write_env.py"
fi

# Make sure Tailwind's lightningcss platform binary is present (npm skips optional deps sometimes)
(cd "$ROOT/frontend" && npm run setup --silent)

# Stop everything in this script's process group on exit
trap 'trap - INT TERM EXIT; kill 0 2>/dev/null || true' INT TERM EXIT

(cd "$ROOT/backend" && "$ROOT/.venv/bin/uvicorn" app.main:app --reload --port 8000 2>&1 | sed -u 's/^/[api] /') &
(cd "$ROOT/frontend/builder" && npm run dev 2>&1 | sed -u 's/^/[ui] /') &
(cd "$ROOT/frontend/viewer" && npm run dev 2>&1 | sed -u 's/^/[viewer] /') &

echo "API:     http://localhost:8000/health"
echo "Builder: http://localhost:5173"
echo "Viewer:  http://localhost:5174"
wait
