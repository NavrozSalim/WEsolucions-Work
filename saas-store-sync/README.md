# SaaS Store Sync

Full-stack SaaS app: connect stores, scrape vendor URLs, apply pricing/stock rules, sync listings via Celery. Multi-tenant (users, stores, catalog, orders, analytics, sync logs).

**Stack:** React (Vite), Django REST, PostgreSQL, Redis, Celery.

This repo uses **two setups**:

| Environment | Compose file           | Env file   | Command |
|-------------|------------------------|------------|---------|
| **Development** (your machine) | `docker-compose.yml` | `.env` | `docker compose up -d` |
| **Production** (server)        | `docker-compose.prod.yml` | `.env.prod` | `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` |

Templates you can commit or copy: **`.env.example`** (dev), **`.env.prod.example`** (prod). Real **`.env`** / **`.env.prod`** are gitignored.

**Production VPS:** audit / fix / validate scripts live in **`scripts/deploy/`** (see **`scripts/deploy/README.md`**). Nginx for Docker is **`backend/config/nginx.conf`** (full `events` + `http`); replace **`173.212.218.31`** in `server_name` if your server IP differs.

**`docker-compose.prod.yml`** includes Redis AOF persistence, healthchecks (DB, Redis, backend, nginx), log rotation, resource limits, graceful `stop_grace_period`, **`init: true`** on app containers, and Celery **after** backend is healthy. **`ALLOWED_HOSTS` must include `backend`** (see `.env.prod.example`) so the backend container healthcheck (`Host: backend`) succeeds. Set **`BACKEND_IMAGE`** to use a registry image instead of local build.

---

## Quick start (development)

1. From the project root:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`: set `POSTGRES_PASSWORD`, `JWT_SECRET`, `ENCRYPTION_KEY`, optional Google OAuth.

2. Start everything:

   ```bash
   docker compose up -d
   ```

3. Open **http://localhost:3001** (Vite in Docker). API: **http://localhost:8000/api/v1**. Postgres on host **localhost:5433** (see `POSTGRES_PORT` in `.env`).

**Dev stack behavior**

- **Backend:** Django `runserver`, code mounted from `./backend` (reload on `.py` changes).
- **Celery:** worker + beat, same image, migrations run on container start via `scripts/entrypoint.py`.
- **Frontend:** `npm run dev` inside Docker, `./frontend` mounted, `node_modules` in a named volume so the host does not overwrite installs.

**Alternative: frontend on the host** (faster HMR): run `docker compose up -d db redis backend celery_worker celery_beat`, then `cd frontend && npm install && npm run dev` → usually **http://localhost:3000**. Set `VITE_API_URL=http://localhost:8000/api/v1` and ensure `CORS_ALLOWED_ORIGINS` includes that origin.

---

## Production

1. On the server, copy **`.env.prod.example` → `.env.prod`** and set strong `POSTGRES_PASSWORD`, `JWT_SECRET`, `ENCRYPTION_KEY`, `DOMAIN_NAME`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, `GOOGLE_*` (if used). Add the same `GOOGLE_REDIRECT_URI` in Google Cloud Console.

2. Set **`server_name`** in **`backend/config/nginx.conf`** to your domain (see **`backend/config/nginx.conf.example`** for SSL-oriented layout and Let’s Encrypt paths).

3. Build the SPA (same-origin API behind Nginx):

   ```bash
   cd frontend
   npm ci
   VITE_API_URL=/api/v1 npm run build
   cd ..
   ```

   If the browser must call a full URL, use `VITE_API_URL=https://your-domain.com/api/v1` instead.

4. Start:

   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
   ```

**Prod stack:** Postgres + Redis + **Gunicorn** backend + **Nginx** (ports 80/443) serving **`frontend/dist`** and proxying `/api/`, `/admin/`, health routes to the backend. Celery worker and beat are included.

Ensure TLS certificates exist on the host and Nginx is configured to use them before relying on HTTPS (port 443 is published; a minimal `nginx.conf` may listen on 80 only until SSL is wired).

---

## Environment variables (reference)

| Variable | Used for |
|----------|----------|
| `POSTGRES_*` | Database |
| `POSTGRES_PORT` / `REDIS_PORT` / `BACKEND_PORT` / `FRONTEND_PORT` | Host port mappings (dev compose) |
| `HTTP_PORT` / `HTTPS_PORT` | Nginx published ports in prod compose (default 80 / 443) |
| `BACKEND_IMAGE` | Optional: registry image for backend + Celery in prod (skip local build) |
| `REDIS_URL` | Host-side tooling (containers use `redis://redis:6379/0` via compose) |
| `DEBUG` | `True` dev, `False` prod |
| `JWT_SECRET` | Django / JWT signing |
| `ENCRYPTION_KEY` | Fernet key for store API tokens |
| `ALLOWED_HOSTS` | Comma-separated Django hosts |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origins |
| `FRONTEND_URL` | OAuth redirects and allowed frontend base URL |
| `DOMAIN_NAME` | Default CORS fallback in prod compose if `CORS_ALLOWED_ORIGINS` unset |
| `GOOGLE_*` | Optional Google OAuth |
| `VITE_API_URL` | Frontend API base (**build-time** for production `npm run build`; dev server reads env at start) |

---

## Useful commands

```bash
# Dev
docker compose up -d
docker compose logs -f backend
docker compose exec backend python manage.py migrate
docker compose exec db psql -U saas -d saas_sync

# Prod
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f backend
```

---

## Known flaws (current repo)

- Large debug/snapshot artifacts are tracked in git, which makes the repo heavy and slower to clone/review:
  - `backend/scrapers/debug_html/` (many HTML dumps from blocked/404 pages)
  - `root@173.212.218.31/` (server snapshot-like folder)
- Secret handling has risky fallbacks in code:
  - `JWT_SECRET` falls back to `django-insecure-default` in `backend/core/settings.py`
  - Encryption helper falls back to a SECRET_KEY-derived key in `backend/core/fields.py` when `ENCRYPTION_KEY` is invalid/missing
- Security defaults are development-friendly and easy to misconfigure in production (for example `SESSION_COOKIE_SECURE = False` in settings; safe only behind HTTPS-aware setup).
- Automated test coverage appears minimal relative to project size (very few concrete `test_*` implementations), increasing regression risk when changing sync/scraper flows.
- Scraper reliability is inherently fragile (CAPTCHA/challenge/404 pages are already present in debug captures), so production sync quality can degrade without strong monitoring and retries.

## Diagnostics playbook

Run this top-to-bottom when the project is "not working".

### 1) Quick environment sanity

```powershell
# From project root
Test-Path .env
docker --version
docker compose version
```

If `.env` is missing: copy from `.env.example` first.

### 2) Start and verify containers (development)

```powershell
docker compose up -d --build
docker compose ps
```

Expected healthy services: `db`, `redis`, `backend`, `celery_worker`, `celery_beat`, `frontend`.

### 3) Health endpoints and API reachability

```powershell
curl http://localhost:8000/health/
curl http://localhost:8000/ready/
curl http://localhost:8000/api/v1/
```

If these fail, inspect backend logs immediately.

### 4) Logs triage (most useful first)

```powershell
docker compose logs --tail=200 backend
docker compose logs --tail=200 celery_worker
docker compose logs --tail=200 celery_beat
docker compose logs --tail=100 db
docker compose logs --tail=100 redis
docker compose logs --tail=100 frontend
```

Look for: migration errors, DB connection failures, Redis refused, import/module errors, OAuth redirect mismatch, scraper timeouts/challenges.

### 5) Django checks and migrations

```powershell
docker compose exec backend python manage.py check
docker compose exec backend python manage.py showmigrations
docker compose exec backend python manage.py migrate --noinput
```

### 6) Celery worker diagnostics

```powershell
docker compose exec celery_worker celery -A core inspect ping
docker compose exec celery_worker celery -A core report
```

If ping fails, verify `REDIS_URL` and worker startup logs.

### 7) Frontend wiring checks

```powershell
docker compose exec frontend printenv VITE_API_URL
```

Confirm it points to `http://localhost:8000/api/v1` in dev.

### 8) Port conflict checks (Windows host)

```powershell
netstat -ano | findstr :8000
netstat -ano | findstr :3001
netstat -ano | findstr :5433
netstat -ano | findstr :6379
```

If ports are busy, adjust `.env` host ports or stop conflicting processes.

### 9) Resource / disk pressure checks

```powershell
docker system df
```

Large local artifacts can also hurt performance. In this repo, `backend/scrapers/debug_html/` and `root@173.212.218.31/` are prime cleanup candidates.

### 10) Production-specific checks

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200 backend
docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=200 nginx
curl -H "Host: backend" http://127.0.0.1:8000/health/
```

Also verify:
- `.env.prod` has strong non-default `JWT_SECRET` and valid `ENCRYPTION_KEY`
- `ALLOWED_HOSTS` includes domain + `backend` (container healthcheck requirement)
- `CORS_ALLOWED_ORIGINS` and `FRONTEND_URL` match real domain
- Nginx `server_name` and TLS cert paths are correct

### 11) Optional "reset and rebuild" (development only)

```powershell
docker compose down
docker compose up -d --build
```

Use this when stale containers/images are suspected. Avoid deleting DB volumes unless you intentionally want to reset data.

---

## Project layout (short)

```
saas-store-sync/
├── docker-compose.yml          # development
├── docker-compose.prod.yml     # production
├── .env.example                # dev template
├── .env.prod.example           # prod template
├── backend/                    # Django, Celery, Gunicorn, nginx.conf
├── frontend/                   # Vite + React
└── README.md                   # this file
```

---

## Deploying to a VPS (summary)

- Install Docker and the Compose plugin.
- Clone the repo, add `.env.prod`, set domain and secrets.
- Point DNS **A record** at the server.
- Adjust **`backend/config/nginx.conf`** (and SSL when ready).
- Build **`frontend/dist`** with the correct `VITE_API_URL`, then run the production compose command above.

---

## License

Private / internal unless stated otherwise.
