from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")


def _ensure_ssl_env() -> None:
    """Use certifi CA bundle when SSL_CERT_FILE is unset (common on Windows VPS)."""
    if os.environ.get("SSL_CERT_FILE") and os.environ.get("REQUESTS_CA_BUNDLE"):
        return
    try:
        import certifi

        ca = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)
    except Exception:
        pass


_ensure_ssl_env()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


COOKIES_FILE = _env("COOKIES_FILE", str(_ROOT / "cookies.json"))
API_BASE_URL = _env("API_BASE_URL").rstrip("/")
INGEST_TOKEN = _env("INGEST_TOKEN")
POLL_INTERVAL_SEC = max(5, _env_int("POLL_INTERVAL_SEC", 30))
MIN_GAP_SEC = max(0.0, float(_env("MIN_GAP_SEC", "15") or "15"))
URL_TIMEOUT_SEC = max(5, _env_int("URL_TIMEOUT_SEC", 20))
MAX_RETRIES = max(1, _env_int("MAX_RETRIES", 2))
HEADLESS = _env_bool("HEADLESS", False)
CHROME_VERSION_MAIN = _env("CHROME_VERSION_MAIN")
HEB_HOME = "https://www.heb.com/"
INGEST_BATCH_SIZE = max(1, _env_int("INGEST_BATCH_SIZE", 50))

# Parallel chunk-pool poller (legacy ingest.env names supported)
POLLER_WORKERS = max(1, _env_int("POLLER_WORKERS", _env_int("POLLER_FOLDERS", 10)))
POLLER_CHUNK_SIZE = _env_int("POLLER_CHUNK_SIZE", 25)
POLLER_UPLOAD_EVERY = max(30, _env_int("POLLER_UPLOAD_EVERY", 120))
POLLER_CANCEL_CHECK_EVERY = max(0, _env_int("POLLER_CANCEL_CHECK_EVERY", 5))
