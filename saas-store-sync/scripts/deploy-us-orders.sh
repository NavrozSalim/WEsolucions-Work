#!/usr/bin/env bash
# Deploy US orders + tickets Celery worker (queue orders-us).
# Target host example: 89.117.147.147
# Do NOT run docker-compose.prod.yml on this host.
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="${DEPLOY_BRANCH:-stg}"

echo "==> Pull latest code ($BRANCH)"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Stop accidental main stack if present"
docker compose -f docker-compose.prod.yml --env-file .env.prod down 2>/dev/null || true

echo "==> Build US orders worker"
docker compose -f docker-compose.us-orders.prod.yml --env-file .env.prod build celery_worker_orders_us

echo "==> Start US orders worker"
docker compose -f docker-compose.us-orders.prod.yml --env-file .env.prod up -d celery_worker_orders_us

echo "==> Done. US orders worker listens on queue: orders-us"
docker compose -f docker-compose.us-orders.prod.yml --env-file .env.prod ps celery_worker_orders_us

echo ""
echo "Smoke test (enqueue from main or here):"
echo "  docker compose -f docker-compose.us-orders.prod.yml --env-file .env.prod exec -T celery_worker_orders_us \\"
echo "    python -c \"from listings.tasks import fetch_us_store_tickets; print(fetch_us_store_tickets())\""
