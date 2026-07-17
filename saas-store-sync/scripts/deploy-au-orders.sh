#!/usr/bin/env bash
# Deploy AU orders + tickets Celery worker (queue orders-au).
# Target host example: 46.250.247.31
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

echo "==> Build AU orders worker"
docker compose -f docker-compose.au-orders.prod.yml --env-file .env.prod build celery_worker_orders_au

echo "==> Start AU orders worker"
docker compose -f docker-compose.au-orders.prod.yml --env-file .env.prod up -d celery_worker_orders_au

echo "==> Done. AU orders worker listens on queue: orders-au"
docker compose -f docker-compose.au-orders.prod.yml --env-file .env.prod ps celery_worker_orders_au

echo ""
echo "Smoke test:"
echo "  docker compose -f docker-compose.au-orders.prod.yml --env-file .env.prod exec -T celery_worker_orders_au \\"
echo "    python -c \"from listings.tasks import fetch_au_store_tickets; print(fetch_au_store_tickets())\""
