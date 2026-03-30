#!/usr/bin/env bash
# Re-run audit and check /health/ until OK or max iterations.
# Usage: bash scripts/deploy/server-validate-loop.sh [max_loops default 15]

set -euo pipefail
MAX="${1:-15}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

PUB_IP="${PUB_IP:-$(curl -4 -sS --max-time 6 ifconfig.me 2>/dev/null || echo '')}"

pass=0
for i in $(seq 1 "$MAX"); do
  echo ""
  echo "========== Validate pass $i / $MAX ($(date -u +%H:%M:%SZ)) =========="
  bash "$SCRIPT_DIR/server-audit.sh" || true

  ok_domain=$(curl -sS -o /dev/null -w "%{http_code}" -H 'Host: wesolucions.com' "http://127.0.0.1/health/" 2>/dev/null || echo 000)
  ok_ip=000
  if [ -n "$PUB_IP" ]; then
    ok_ip=$(curl -sS -o /dev/null -w "%{http_code}" -H "Host: ${PUB_IP}" "http://127.0.0.1/health/" 2>/dev/null || echo 000)
  fi

  echo "--- Quick check: /health/ domain -> HTTP $ok_domain | IP $PUB_IP -> HTTP $ok_ip ---"

  if [ "$ok_domain" = "200" ] || [ "$ok_ip" = "200" ]; then
    echo "PASS: /health/ returned 200"
    pass=1
    break
  fi
  echo "Waiting 25s..."
  sleep 25
done

if [ "$pass" -eq 1 ]; then
  exit 0
fi
echo "FAIL: /health/ did not return 200 after $MAX attempts"
exit 1
