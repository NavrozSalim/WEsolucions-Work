#!/usr/bin/env bash
# Deep audit: Docker, compose, nginx config, curls, logs.
# Run from anywhere after clone:
#   bash scripts/deploy/server-audit.sh
# Report: <project>/reports/server-audit-*.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

REPORT_DIR="$PROJECT_ROOT/reports"
mkdir -p "$REPORT_DIR"
OUT="$REPORT_DIR/server-audit-$(date +%Y%m%d-%H%M%S).txt"

exec > >(tee -a "$OUT") 2>&1

echo "=========================================="
echo "SERVER AUDIT — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "PROJECT_ROOT=$PROJECT_ROOT"
echo "Hostname: $(hostname -f 2>/dev/null || hostname)"
echo "=========================================="

echo -e "\n--- Uptime / OS ---"
uptime 2>/dev/null || true
uname -a 2>/dev/null || true
[ -f /etc/os-release ] && cat /etc/os-release

echo -e "\n--- Public IPv4 ---"
curl -4 -sS --max-time 8 ifconfig.me 2>/dev/null || echo "(curl failed)"

echo -e "\n--- Listening (80,443,8000) ---"
ss -tlnp 2>/dev/null | grep -E ':80 |:443|:8000' || true

echo -e "\n--- UFW ---"
ufw status verbose 2>/dev/null || echo "ufw n/a"

echo -e "\n--- Docker ---"
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | head -30 || true

if [ ! -f "$PROJECT_ROOT/docker-compose.prod.yml" ]; then
  echo "ERROR: docker-compose.prod.yml not found under $PROJECT_ROOT"
  echo "Report: $OUT"
  exit 1
fi

cd "$PROJECT_ROOT"

if [ -f .env.prod ]; then
  echo -e "\n--- .env.prod (non-secret keys) ---"
  grep -E '^(POSTGRES_|DOMAIN_|ALLOWED_HOSTS|CORS_|FRONTEND_URL|DEBUG|GOOGLE_REDIRECT|VITE_API)' .env.prod 2>/dev/null || true
  echo "(omit JWT/ENCRYPTION/GOOGLE_SECRET from logs)"
else
  echo -e "\n⚠ .env.prod missing"
fi

echo -e "\n--- docker compose ps ---"
docker compose -f docker-compose.prod.yml --env-file .env.prod ps 2>/dev/null || docker compose -f docker-compose.prod.yml ps 2>/dev/null || true

echo -e "\n--- nginx logs (tail 50) ---"
docker compose -f docker-compose.prod.yml --env-file .env.prod logs nginx --tail 50 2>/dev/null || true

echo -e "\n--- backend logs (tail 50) ---"
docker compose -f docker-compose.prod.yml --env-file .env.prod logs backend --tail 50 2>/dev/null || true

echo -e "\n--- backend ALLOWED_HOSTS ---"
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T backend printenv ALLOWED_HOSTS 2>/dev/null || true

echo -e "\n--- nginx.conf (head) ---"
sed -n '1,100p' "$PROJECT_ROOT/backend/config/nginx.conf" 2>/dev/null || true

echo -e "\n--- Test nginx config inside container ---"
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T nginx nginx -t 2>&1 || true

PUB=$(curl -4 -sS --max-time 5 ifconfig.me 2>/dev/null || echo "173.212.218.31")

echo -e "\n--- curl /health/ (Host: wesolucions.com) ---"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -H 'Host: wesolucions.com' "http://127.0.0.1/health/" 2>/dev/null || echo failed

echo -e "\n--- curl /health/ (Host: $PUB) ---"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -H "Host: ${PUB}" "http://127.0.0.1/health/" 2>/dev/null || echo failed

echo -e "\n--- curl /api/v1/ (Host: wesolucions.com) ---"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -H 'Host: wesolucions.com' "http://127.0.0.1/api/v1/" 2>/dev/null || echo failed

if [ -d "$PROJECT_ROOT/frontend/dist/assets" ]; then
  echo -e "\n--- dist: strings mentioning //api or double slash (sample) ---"
  grep -Rho '173\.212\.218\.[0-9]*//\|//api/v1' "$PROJECT_ROOT/frontend/dist" 2>/dev/null | head -5 || echo "(none found or no dist)"
fi

echo -e "\n=========================================="
echo "Report saved: $OUT"
echo "=========================================="
