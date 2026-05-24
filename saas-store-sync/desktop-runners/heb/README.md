# HEB desktop runner (Windows recommended)

Scrapes HEB with **real Chrome + undetected-chromedriver** using only a **cookies file** — no proxies required. Uploads prices to your SaaS via the ingest API.

Amazon/eBay stay on the Linux US Celery worker. HEB runs on a **Windows PC or Windows VPS** with this runner.

## Setup (Windows)

1. Install [Google Chrome](https://www.google.com/chrome/).

2. Export cookies from Chrome after visiting `https://www.heb.com/` and a product page. Save as `cookies.json` in this folder (EditThisCookie JSON array format).

3. Copy config:

   ```bat
   copy config.example.env .env
   ```

4. Edit `.env`:

   ```env
   COOKIES_FILE=cookies.json
   API_BASE_URL=https://your-domain.com/api/v1
   INGEST_TOKEN=your_token_from_manage.py
   MIN_GAP_SEC=15
   HEADLESS=0
   ```

5. Create ingest token on the **main server**:

   ```bash
   cd backend
   python manage.py create_ingest_token --label heb-pc --scopes heb
   ```

   Paste the printed token into `.env` as `INGEST_TOKEN`.

6. Install deps and run:

   ```bat
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python run_poller.py
   ```

Leave the poller running. When you click **Start Scraping** in the catalog UI, it queues a HEB job; this runner claims it, scrapes, and uploads prices.

## Server config (disable HEB on Linux worker)

On the US VPS `.env.prod`, **do not** set `HEB_US_PROXY_URLS` or `HEB_COOKIES_ONLY`. HEB rows are then **ingest-only** on the server and this desktop runner handles them.

## Commands

```bat
REM Continuous poll (production)
python run_poller.py

REM One job then exit
python run_poller.py --once

REM Test one URL locally
python run_poller.py --url "https://www.heb.com/product-detail/1883565"

REM Test + upload to API
python run_poller.py --url "https://www.heb.com/product-detail/1883565" --upload
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Pardon Our Interruption` | Re-export fresh `cookies.json` from Chrome |
| Chrome version mismatch | Set `CHROME_VERSION_MAIN=146` in `.env` (from `chrome://version`) |
| `No pending job` | Click catalog scrape first; ensure token owner matches store owner |
| Proxy not needed | This runner does **not** use `proxy.txt` — cookies only |

## Files

| File | Purpose |
|------|---------|
| `cookies.json` | Your HEB session cookies (gitignored) |
| `.env` | API URL + token (gitignored) |
| `run_poller.py` | Entry point |
