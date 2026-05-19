#!/usr/bin/env bash
# Deploy Amazon AU / eBay AU / Costco AU scraper on the AU VPS only.
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

echo "==> Verify Costco AU proxy configuration"
docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod exec -T celery_worker_au \
  python -c "from scrapers.costco_au_proxies import load_proxy_urls; \
    urls = load_proxy_urls(); \
    print(f'Costco AU proxies loaded: {len(urls)}'); \
    [print(f'  - {u.split(\"@\")[-1]}') for u in urls]" || true

echo "==> Done. AU worker listens on queue: heavy-au"
docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod ps celery_worker_au

echo ""
echo "Smoke test (run after setting COSTCO_AU_PROXY_URLS in .env.prod and restarting):"
echo "  docker compose -f docker-compose.au-scraper.prod.yml --env-file .env.prod exec -T celery_worker_au \\"
echo "    python manage.py test_vendor_scrape --url \"https://www.costco.com.au/p/173734\" --region AU"
