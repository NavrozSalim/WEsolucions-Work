#!/usr/bin/env bash
# =============================================================================
# Disaster recovery: restore main DB from old-server standby backups and start app.
#
# Run on the OLD standby server (173.212.218.31) when the NEW main DB is lost:
#
#   bash ~/db-standby/scripts/recover-from-standby-backup.sh
#   # or from the repo:
#   bash scripts/recover-from-standby-backup.sh
#
# Then point your domain A record to this server's public IP.
# =============================================================================
set -euo pipefail

STANDBY_DIR="${STANDBY_DIR:-$HOME/db-standby}"
APP_DIR="${APP_DIR:-/var/www/WEsolucions-Work/saas-store-sync}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
OLD_SERVER_IP="${OLD_SERVER_IP:-173.212.218.31}"

echo "=============================================="
echo "  WEsolucions DB disaster recovery"
echo "=============================================="
echo

if [[ ! -d "$STANDBY_DIR/backups" ]]; then
  echo "ERROR: standby backups folder not found: $STANDBY_DIR/backups"
  exit 1
fi

LATEST="$(ls -1t "$STANDBY_DIR"/backups/main-*.sql.gz 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST" ]]; then
  echo "ERROR: no main-*.sql.gz backup found in $STANDBY_DIR/backups"
  exit 1
fi

echo "==> Using backup: $LATEST"
ls -lh "$LATEST"
echo

if [[ ! -f "$APP_DIR/$ENV_FILE" ]]; then
  echo "ERROR: app env not found: $APP_DIR/$ENV_FILE"
  exit 1
fi

# Prefer credentials from standby .env (synced from main); fall back to app .env.prod
if [[ -f "$STANDBY_DIR/.env" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$STANDBY_DIR/.env"; set +a
fi

POSTGRES_DB="${POSTGRES_DB:-$(grep -E '^POSTGRES_DB=' "$APP_DIR/$ENV_FILE" | cut -d= -f2-)}"
POSTGRES_USER="${POSTGRES_USER:-$(grep -E '^POSTGRES_USER=' "$APP_DIR/$ENV_FILE" | cut -d= -f2-)}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(grep -E '^POSTGRES_PASSWORD=' "$APP_DIR/$ENV_FILE" | cut -d= -f2-)}"

if [[ -z "$POSTGRES_DB" || -z "$POSTGRES_USER" ]]; then
  echo "ERROR: could not resolve POSTGRES_DB / POSTGRES_USER"
  exit 1
fi

cd "$APP_DIR"

echo "==> Starting Postgres (app stack)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d db

echo "==> Waiting for Postgres..."
for i in $(seq 1 60); do
  if docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
    pg_isready -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; then
    echo "    Postgres is ready."
    break
  fi
  if [[ "$i" -eq 60 ]]; then
    echo "ERROR: Postgres did not become ready in time"
    exit 1
  fi
  sleep 2
done

echo "==> Recreating database \"$POSTGRES_DB\"..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();" \
  >/dev/null || true

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c \
  "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c \
  "CREATE DATABASE \"${POSTGRES_DB}\";"

echo "==> Restoring backup (this may take a few minutes)..."
gunzip -c "$LATEST" | docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 >/dev/null

echo "==> Verifying restore..."
ROWS="$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM django_migrations;")"
echo "    django_migrations rows: $ROWS"

echo "==> Starting full app stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo
echo "=============================================="
echo "  RECOVERY COMPLETE"
echo "=============================================="
echo "Backup restored: $LATEST"
echo "Migrations rows: $ROWS"
echo
echo "NEXT STEPS:"
echo "  1) Point your domain DNS A record to: $OLD_SERVER_IP"
echo "  2) Wait 5–30 min for DNS, then open https://YOUR_DOMAIN"
echo "  3) On US/AU scraper VPS, set DATABASE_URL / REDIS_URL to this server"
echo "  4) Check: docker compose -f $COMPOSE_FILE --env-file $ENV_FILE ps"
echo "=============================================="
