#!/usr/bin/env bash
# Apply common fixes after reviewing server-audit report:
# - nginx -t, recreate nginx + backend (picks up .env.prod)
# - optional: rebuild frontend (set REBUILD_FRONTEND=1)
#
# Usage (on server, from project root):
#   bash scripts/deploy/server-fix.sh
#   REBUILD_FRONTEND=1 bash scripts/deploy/server-fix.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f docker-compose.prod.yml ] || [ ! -f .env.prod ]; then
  echo "Run from saas-store-sync directory with docker-compose.prod.yml and .env.prod"
  exit 1
fi

echo "=== nginx config test (container) ==="
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T nginx nginx -t 2>&1 || {
  echo "nginx -t failed — fix backend/config/nginx.conf then re-run"
  exit 1
}

if [ "${REBUILD_FRONTEND:-0}" = "1" ]; then
  echo "=== npm build frontend ==="
  (cd frontend && npm ci && VITE_API_URL=/api/v1 npm run build)
fi

echo "=== recreate nginx + backend + celery ==="
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --force-recreate nginx backend celery_worker celery_beat

echo "=== migrate ==="
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T backend python manage.py migrate --noinput

echo "Done. Run: bash scripts/deploy/server-validate-loop.sh"
