#!/usr/bin/env bash
# Deploy eBay/Amazon AU scraper on the AU VPS only.
# Do NOT run docker-compose.prod.yml on this host.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Pull latest code"
git pull --ff-only origin main

echo "==> Stop accidental main stack if present"
docker compose -f docker-compose.prod.yml --env-file .env.prod down 2>/dev/null || true

echo "==> Build AU scraper worker"
docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod build celery_worker_au

echo "==> Start AU scraper"
docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod up -d celery_worker_au

echo "==> Done. AU worker listens on queue: heavy-au"
docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod ps celery_worker_au
