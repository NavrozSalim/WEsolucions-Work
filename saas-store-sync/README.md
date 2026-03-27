# SaaS Store Sync

Full-stack SaaS app: connect stores, scrape vendor URLs, apply pricing/stock rules, sync listings via Celery. Multi-tenant (users, stores, catalog, orders, analytics, sync logs).

**Stack:** React (Vite), Django REST, PostgreSQL, Redis, Celery.

This repo uses **two setups**:

| Environment | Compose file           | Env file   | Command |
|-------------|------------------------|------------|---------|
| **Development** (your machine) | `docker-compose.yml` | `.env` | `docker compose up -d` |
| **Production** (server)        | `docker-compose.prod.yml` | `.env.prod` | `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` |

Templates you can commit or copy: **`.env.example`** (dev), **`.env.prod.example`** (prod). Real **`.env`** / **`.env.prod`** are gitignored.

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
