#!/usr/bin/env bash
# Deploy Amazon US / eBay US / HEB US scraper on the US VPS only.
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

echo "==> Verify HEB US proxy configuration"
docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod exec -T celery_worker_us \
  python -c "from scrapers.heb_us_proxies import load_proxy_urls; \
    urls = load_proxy_urls(); \
    print(f'HEB US proxies loaded: {len(urls)}'); \
    [print(f'  - {u.split(\"@\")[-1]}') for u in urls]" || true

echo "==> Done. US worker listens on queue: heavy-us"
docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod ps celery_worker_us

echo ""
echo "Smoke test (run after setting HEB_US_PROXY_URLS in .env.prod and restarting):"
echo "  docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod exec -T celery_worker_us \\"
echo "    python manage.py test_vendor_scrape --url \"https://www.heb.com/product-detail/377497\" --region USA"
