#!/usr/bin/env bash
# Deploy Vevor bulk ingest + light worker on the MAIN app server.
# Run from saas-store-sync/ on the production host (NOT the AU scraper VPS).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Pull latest code"
git pull --ff-only origin main

echo "==> Build backend + celery_worker_light (no cache — ensures new code)"
docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache celery_worker_light backend

echo "==> Restart light worker + backend"
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d celery_worker_light backend

echo "==> Done. Vevor runs on queue: light"
docker compose -f docker-compose.prod.yml --env-file .env.prod ps celery_worker_light backend
