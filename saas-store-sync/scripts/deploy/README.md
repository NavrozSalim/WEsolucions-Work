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

- **`backend/config/nginx.conf`** is the **full** main config (`events` + `http`) for Docker. It **listens on 80 and 443** and includes **`backend/config/nginx-app-server.inc`** (also mounted into the container).
- TLS files are read from the host mount **`/etc/letsencrypt/live/wesolucions.com/`** (`fullchain.pem`, `privkey.pem`). If your Certbot directory name differs (e.g. `www.wesolucions.com`), edit the `ssl_certificate` paths in `nginx.conf` to match.
- Edit **`server_name`** and replace **`173.212.218.31`** if your Contabo IP changes.
- **HTTPS refused / `ERR_CONNECTION_REFUSED` on 443:** usually nginx in Docker was only listening on 80, or certs are missing/wrong path — fix certs on the host, then `nginx -t` inside the nginx container and recreate nginx.

### If certificates are missing on the VPS

On the **host** (not inside Docker), install Certbot and obtain a cert for your domain, then ensure `/etc/letsencrypt/live/wesolucions.com/` exists (or adjust `nginx.conf` to your live directory name). Recreate the nginx service after files are in place.
