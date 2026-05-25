#!/usr/bin/env bash
# Deploy Amazon US / eBay US scraper on the US VPS only.
# HEB is handled by a separate Windows desktop runner, not this worker.
# Do NOT run docker-compose.prod.yml on this host.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Pull latest code"
git pull --ff-only origin main

echo "==> Stop accidental main stack if present"
docker compose -f docker-compose.prod.yml --env-file .env.prod down 2>/dev/null || true

echo "==> Build US scraper worker"
docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod build celery_worker_us

echo "==> Start US scraper"
docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod up -d celery_worker_us

echo "==> Done. US worker listens on queue: heavy-us (Amazon/eBay US only)"
docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod ps celery_worker_us

echo ""
echo "Smoke test (eBay/Amazon):"
echo "  docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod exec -T celery_worker_us \\"
echo "    python manage.py test_vendor_scrape --url \"https://www.ebay.com/itm/1234567890\" --region USA"
