# Disaster recovery (old standby server)

Use this when the **new main DB** (`169.58.17.212`) is lost and you need the site
back from the **old server** (`173.212.218.31`).

## One command

On the old server:

```bash
bash ~/db-standby/scripts/recover-from-standby-backup.sh
```

That will:

1. Pick the latest `~/db-standby/backups/main-*.sql.gz`
2. Restore it into the app Postgres
3. Start the full production stack

## After recovery

1. Point your domain **A record** to `173.212.218.31`
2. Wait for DNS (5–30 minutes)
3. Update US/AU scraper `DATABASE_URL` / `REDIS_URL` to the old server
4. Check health:

```bash
cd /var/www/WEsolucions-Work/saas-store-sync
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
curl -I http://127.0.0.1/health/ || true
```

## Install / refresh the script on the old server

From the app repo on the old server (after `git pull`):

```bash
mkdir -p ~/db-standby/scripts
cp /var/www/WEsolucions-Work/saas-store-sync/scripts/recover-from-standby-backup.sh \
  ~/db-standby/scripts/
chmod +x ~/db-standby/scripts/recover-from-standby-backup.sh
```

Or copy from this repo path if you keep the code under `/root/...`.

## Normal operation (do not run recovery)

While the new main is healthy:

- Domain stays on **`169.58.17.212`**
- Old server only runs DB sync every 6 hours (`sync-db-from-main.sh`)
- Do **not** run the recover script unless you are failing over
