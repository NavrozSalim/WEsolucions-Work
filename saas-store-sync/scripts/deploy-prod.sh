#!/usr/bin/env bash
# Production deploy — run on the VPS from the saas-store-sync directory.
# Prerequisites: .env.prod, Docker, Node (for frontend build on host).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Pull latest code"
git pull --ff-only

echo "==> Build frontend (API path /api/v1)"
cd frontend
if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
VITE_API_URL="${VITE_API_URL:-/api/v1}" npm run build
cd "$ROOT"

echo "==> Build and start containers"
docker compose -f docker-compose.prod.yml --env-file .env.prod build
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

echo "==> Run database migrations"
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T backend python manage.py migrate --noinput

echo "==> Done. Check: docker compose -f docker-compose.prod.yml --env-file .env.prod ps"
echo "    If migrations failed on unique store constraint, resolve duplicate (user,name,marketplace) in Postgres first."
