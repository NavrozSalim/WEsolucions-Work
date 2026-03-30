# Server deploy helpers

Run on the VPS from the **`saas-store-sync`** directory (where `docker-compose.prod.yml` lives).

## Audit (generate report)

```bash
bash scripts/deploy/server-audit.sh
```

Opens `reports/server-audit-<timestamp>.txt` — share this when asking for help.

## Fix (after you reviewed the audit)

```bash
bash scripts/deploy/server-fix.sh
```

Optional rebuild SPA:

```bash
REBUILD_FRONTEND=1 bash scripts/deploy/server-fix.sh
```

## Validate in a loop

```bash
bash scripts/deploy/server-validate-loop.sh 20
```

Exits `0` when `GET /health/` returns **200** for `Host: wesolucions.com` or your public IPv4.

## Nginx

- **`backend/config/nginx.conf`** is the **full** main config (`events` + `http`) for Docker.
- Edit **`server_name`** and replace **`173.212.218.31`** if your Contabo IP changes.
- For HTTPS, add certificates and a `listen 443 ssl` server block (see `nginx.conf.example` for ACME ideas).
