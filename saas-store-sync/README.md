# SaaS Store Sync

Multi-tenant SaaS for **e‑commerce store connectivity**, **vendor price and stock intelligence**, and **syncing listings** to marketplaces. Teams connect stores, map catalog rows to vendor URLs, run scrapes (or alternate ingest flows), apply pricing and inventory rules, and push updates via background workers—with **analytics**, **audit**, and **sync** visibility.

**Stack:** React (Vite) · Django REST Framework · PostgreSQL · Redis · Celery · Nginx (production).

---

## Contents

- [What it does](#what-it-does)
- [Repository layout](#repository-layout)
- [Development and production setups](#development-and-production-setups)
- [Quick start (development)](#quick-start-development)
- [Production](#production)
- [VPS deploy helpers](#vps-deploy-helpers)
- [Environment variables](#environment-variables-reference)
- [Useful commands](#useful-commands)
- [CI](#ci)
- [Known limitations](#known-limitations)
- [Diagnostics](#diagnostics-when-something-fails)
- [License](#license)

---

## What it does

| Area | Capabilities |
|------|----------------|
| **Stores & marketplaces** | Connect stores to marketplace adapters (e.g. **Reverb**, **Walmart**, **Sears**, **Etsy**, **Kogan** via Google Sheets). Encrypted API tokens; optional Google OAuth for sign-in. |
| **Catalog** | Product mappings, vendor URLs, marketplace CSV templates, upload history, activity and sync-oriented workflows. |
| **Pricing & inventory** | Per-store vendor price/inventory rules; tiered and marketplace-specific behavior where implemented. |
| **Scraping & ingest** | Server-side scrapers for vendors such as **Amazon** and **eBay** (region-aware URL handling). **HEB** and **Costco AU** use separate ingest paths (not plain datacenter HTTP scrape); **Vevor AU** can use a public feed ingest. See `backend/scrapers/__init__.py` for the dispatcher and notes. |
| **Sync** | Celery-backed jobs for scrape, push, and related pipelines; health and readiness endpoints for orchestration. |
| **Observability** | Analytics API, sync logs, audit trail—aligned with the Django apps under `backend/`. |

---

## Repository layout

```
saas-store-sync/
├── docker-compose.yml           # Local development
├── docker-compose.prod.yml     # Production (Gunicorn, Nginx, etc.)
├── .env.example                # Dev template (commit or copy)
├── .env.prod.example           # Production template
├── backend/                    # Django project (API, Celery, scrapers, store_adapters)
│   ├── config/                 # e.g. nginx.conf for the prod nginx container
│   ├── store_adapters/         # Marketplace-specific integration code
│   ├── scrapers/               # HTTP/Selenium/Playwright scrapers + dispatcher
│   └── …                       # users, stores, catalog, sync, analytics, …
├── frontend/                   # Vite + React SPA
├── scripts/                    # entrypoint, deploy helpers, …
│   └── deploy/                 # server-audit / server-fix / validate scripts (see below)
└── README.md                   # This file
```

---

## Development and production setups

| Environment | Compose file | Env file | Command |
|-------------|--------------|----------|---------|
| **Development** (your machine) | `docker-compose.yml` | `.env` | `docker compose up -d` |
| **Production** (server) | `docker-compose.prod.yml` | `.env.prod` | `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` |

Copy **`.env.example` → `.env`** (dev) and **`.env.prod.example` → `.env.prod`** (prod). Real `.env` / `.env.prod` files are not committed.

**Production compose** is tuned for operations: Redis AOF, health checks (DB, Redis, backend, Nginx), log rotation, resource limits, graceful `stop_grace_period`, `init: true` on app containers, and Celery started after the backend is healthy. Set **`ALLOWED_HOSTS` to include `backend`** so the backend container health check (`Host: backend`) succeeds. Set **`BACKEND_IMAGE`** to pull a prebuilt image instead of building locally.

**Nginx** for Docker: **`backend/config/nginx.conf`** (full `events` + `http`). Update **`server_name`** and the placeholder IP (**`173.212.218.31`**) if your host or DNS differs. See **`backend/config/nginx.conf.example`** for SSL-oriented layout and Let’s Encrypt–style paths.

---

## Quick start (development)

1. From the project root:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`: set `POSTGRES_PASSWORD`, `JWT_SECRET`, `ENCRYPTION_KEY`, and optional Google OAuth values.

2. Start the stack:

   ```bash
   docker compose up -d
   ```

3. Open the app at **http://localhost:3001** (Vite in Docker). API base: **http://localhost:8000/api/v1**. Postgres is exposed on the host at **localhost:5433** by default (see `POSTGRES_PORT` in `.env`).

**How dev is wired**

- **Backend:** Django `runserver` with `./backend` mounted (reloads on Python changes).
- **Celery:** Worker + beat; migrations run on container start via `scripts/entrypoint.py`.
- **Frontend:** `npm run dev` in Docker with `./frontend` mounted; `node_modules` uses a named volume so the host does not clobber installs.

**Optional: run the frontend on the host** (often faster HMR): bring up `db`, `redis`, `backend`, `celery_worker`, and `celery_beat` only, then `cd frontend && npm install && npm run dev` (commonly **http://localhost:3000**). Set `VITE_API_URL=http://localhost:8000/api/v1` and add that origin to `CORS_ALLOWED_ORIGINS`.

---

## Production

1. On the server, fill **`.env.prod`** from **`.env.prod.example`**: strong `POSTGRES_PASSWORD`, `JWT_SECRET`, `ENCRYPTION_KEY`, `DOMAIN_NAME`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, and `GOOGLE_*` if used. Register the same `GOOGLE_REDIRECT_URI` in Google Cloud Console.

2. Set **`server_name`** (and SSL paths when using HTTPS) in **`backend/config/nginx.conf`**.

3. Build the SPA (typical same-origin API behind Nginx):

   ```bash
   cd frontend
   npm ci
   VITE_API_URL=/api/v1 npm run build
   cd ..
   ```

   If the client must call a full URL, use e.g. `VITE_API_URL=https://your-domain.com/api/v1` instead.

4. Start:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
   ```

**Prod stack:** PostgreSQL, Redis, **Gunicorn** app server, **Nginx** on **80/443** serving **`frontend/dist`** and proxying `/api/`, `/admin/`, and health routes to the backend, plus Celery worker and beat.

Ensure TLS certificate files exist on the host and Nginx points at them before depending on **HTTPS** (port **443** may be published even while only HTTP is configured—verify `nginx.conf` and certs).

**Catalog bulk uploads and workers**

- Large CSV/XLSX uploads are stored under **`MEDIA_ROOT`** (`/app/media` in Docker). The **backend** and **celery_worker** services share a **`catalog_media`** volume so the ingest task can read the file after the API saves it. Apply migrations after pull (`catalog.0020_*`).
- Celery **queues** (see `core/settings.py`): **`ingest`** (chunked `bulk_create` of upload rows), **`light`** (catalog DB sync, Vevor feed), **`heavy`** (browser/Selenium scrapes and scrape chunks), **`celery`** (default, e.g. `sync` beat tasks). The compose files run one worker with `-Q celery,ingest,light,heavy` so all queues are drained; on a small VPS you can add a **second** worker with **`-Q heavy -c 1`** so scrapes do not starve ingest/sync.
- Tunables: **`CATALOG_UPLOAD_CHUNK_SIZE`** (default 1000), **`CATALOG_SYNC_LOG_BATCH`**, **`CATALOG_SYNC_PROGRESS_EVERY`**, **`PG_CONN_MAX_AGE`** (use `0` with **PgBouncer** transaction pooling).

---

## VPS deploy helpers

Run these from the **`saas-store-sync`** directory (where `docker-compose.prod.yml` lives), on the server.

| Step | Command | Notes |
|------|---------|--------|
| **Audit** | `bash scripts/deploy/server-audit.sh` | Writes `reports/server-audit-<timestamp>.txt`—useful when asking for help. |
| **Fix** | `bash scripts/deploy/server-fix.sh` | Run after you review the audit. Optional: `REBUILD_FRONTEND=1 bash scripts/deploy/server-fix.sh` to rebuild the SPA. |
| **Validate** | `bash scripts/deploy/server-validate-loop.sh 20` | Exits `0` when `GET /health/` returns **200** for your domain or public IP. |

**Nginx in production**

- **`backend/config/nginx.conf`** is the **full** main config (`events` + `http`) for the container. It should include **`backend/config/nginx-app-server.inc`** (mounted with the app).
- TLS is usually read from the host, e.g. **`/etc/letsencrypt/live/<your-domain>/`** (`fullchain.pem`, `privkey.pem`). If Certbot used a different name (e.g. `www.example.com`), adjust `ssl_certificate` paths. Match **`server_name`** and replace any **example IP** in the file if the server address changes.
- **`ERR_CONNECTION_REFUSED` on 443** often means Nginx is not listening on 443, or certificate paths are wrong—fix files on the host, run `nginx -t` inside the Nginx container, then recreate the service.
- If certificates are missing: on the **host**, install Certbot, obtain a cert, ensure the live directory exists, then recreate the Nginx service.

**Deploy checklist (summary)**

- Install Docker and the Compose plugin.
- Clone the repo, configure `.env.prod`, secrets, and domain.
- Point DNS **A** record at the server.
- Adjust **`backend/config/nginx.conf`**, build **`frontend/dist`** with the correct `VITE_API_URL`, then run the production `docker compose` command above.

---

## Environment variables (reference)

| Variable | Purpose |
|----------|---------|
| `POSTGRES_*` | Database connection |
| `POSTGRES_PORT` / `REDIS_PORT` / `BACKEND_PORT` / `FRONTEND_PORT` | Host port mappings (dev compose) |
| `HTTP_PORT` / `HTTPS_PORT` | Nginx published ports in prod (default 80 / 443) |
| `BACKEND_IMAGE` | Optional registry image for backend + Celery in prod (skip local build) |
| `REDIS_URL` | Host-side tooling; containers typically use `redis://redis:6379/0` via compose |
| `DEBUG` | `True` in dev, `False` in prod |
| `JWT_SECRET` | JWT / Django signing (must be strong in production) |
| `ENCRYPTION_KEY` | Fernet key for store secrets at rest |
| `ALLOWED_HOSTS` | Comma-separated hostnames (**must include `backend`** for container health in prod) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins |
| `FRONTEND_URL` | OAuth redirects and related frontend base URL |
| `DOMAIN_NAME` | Default CORS fallback in prod compose if `CORS_ALLOWED_ORIGINS` is unset |
| `GOOGLE_*` | Optional Google OAuth |
| `VITE_API_URL` | Frontend API base; **baked in at `npm run build` time** for production |

Additional scraper- or feature-specific variables may appear in `.env.example` (e.g. proxy pools, catalog flags). Prefer the committed templates as the source of truth.

---

## Useful commands

```bash
# Development
docker compose up -d
docker compose logs -f backend
docker compose exec backend python manage.py migrate
docker compose exec db psql -U saas -d saas_sync

# Production
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend
```

---

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on push and pull requests:

- **pre-commit** (lint/format hooks) on the repo
- **Django tests** under `backend/` with SQLite and env vars set for CI

Run the same checks locally: `pre-commit run --all-files` and `python manage.py test` from `backend/` with appropriate env vars.

---

## Known limitations

- **Repository weight:** Debug or snapshot-style paths (e.g. `backend/scrapers/debug_html/`, or server snapshot–style trees) can bloat clones if they remain tracked; prefer `.gitignore` and out-of-band storage for large captures.
- **Secrets:** `JWT_SECRET` and encryption key handling must be set for production; code may expose insecure fallbacks in development—see `backend/core/settings.py` and `backend/core/fields.py` and do not rely on defaults in production.
- **Session / HTTPS:** Some defaults favor local development; behind reverse proxies, configure secure cookies and TLS explicitly.
- **Tests:** Test coverage is limited relative to app size; critical paths (sync, scrapers) benefit from focused tests and manual verification.
- **Scrapers:** External sites (bot protection, CAPTCHAs, layout changes) can break automation; plan for monitoring, retries, and alternative ingest flows where documented.

---

## Diagnostics (when something fails)

Run in order for a typical “it does not work” investigation.

### 1) Environment

```powershell
# From project root
Test-Path .env
docker --version
docker compose version
```

If `.env` is missing, copy from `.env.example` first.

### 2) Containers (dev)

```powershell
docker compose up -d --build
docker compose ps
```

Expect: `db`, `redis`, `backend`, `celery_worker`, `celery_beat`, `frontend`.

### 3) Health and API

```powershell
curl http://localhost:8000/health/
curl http://localhost:8000/ready/
curl http://localhost:8000/api/v1/
```

### 4) Logs

```powershell
docker compose logs --tail=200 backend
docker compose logs --tail=200 celery_worker
docker compose logs --tail=200 celery_beat
docker compose logs --tail=100 db
docker compose logs --tail=100 redis
docker compose logs --tail=100 frontend
```

### 5) Django

```powershell
docker compose exec backend python manage.py check
docker compose exec backend python manage.py showmigrations
docker compose exec backend python manage.py migrate --noinput
```

### 6) Celery

```powershell
docker compose exec celery_worker celery -A core inspect ping
docker compose exec celery_worker celery -A core report
```

### 7) Frontend API URL (dev)

```powershell
docker compose exec frontend printenv VITE_API_URL
```

Should align with `http://localhost:8000/api/v1` for the default Docker setup.

### 8) Port conflicts (Windows)

```powershell
netstat -ano | findstr :8000
netstat -ano | findstr :3001
netstat -ano | findstr :5433
netstat -ano | findstr :6379
```

### 9) Disk

```powershell
docker system df
```

### 10) Production

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200 backend
docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200 nginx
curl -H "Host: backend" http://127.0.0.1:8000/health/
```

Confirm `.env.prod` secrets, `ALLOWED_HOSTS` (domain + `backend`), `CORS_*`, `FRONTEND_URL`, and Nginx `server_name` / TLS.

### 11) Reset (dev only)

```powershell
docker compose down
docker compose up -d --build
```

Avoid removing DB volumes unless you intend to wipe data.

---

## License

Private / internal unless stated otherwise.
