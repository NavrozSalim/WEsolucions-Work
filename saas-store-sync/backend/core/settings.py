import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import urllib.parse
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables (project root and backend folder)
for _p in [BASE_DIR.parent / '.env', BASE_DIR / '.env']:
    if _p.exists():
        load_dotenv(str(_p))

def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


def _require_env(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DEBUG = _env_bool('DEBUG', False)

if DEBUG:
    SECRET_KEY = os.getenv('JWT_SECRET', 'dev-only-change-me')
else:
    SECRET_KEY = _require_env('JWT_SECRET')

# Store-wide catalog scrape in the web worker (Gunicorn) — unsafe for production; default follows DEBUG.
_raw_inline_scrape = os.getenv('CATALOG_ALLOW_INLINE_STORE_WIDE_SCRAPE')
if _raw_inline_scrape is None or str(_raw_inline_scrape).strip() == '':
    CATALOG_ALLOW_INLINE_STORE_WIDE_SCRAPE = DEBUG
else:
    CATALOG_ALLOW_INLINE_STORE_WIDE_SCRAPE = str(_raw_inline_scrape).strip().lower() in (
        '1',
        'true',
        'yes',
        'on',
    )

# Split catalog scrapes across parallel Celery tasks (separate Amazon/eBay sessions per chunk).
# 0 = always one task (legacy). Prod US VPS 30: 80 with CELERY_US_SCRAPER_CONCURRENCY=6.
try:
    CATALOG_SCRAPE_CHUNK_SIZE = max(0, int(os.getenv('CATALOG_SCRAPE_CHUNK_SIZE', '80')))
except ValueError:
    CATALOG_SCRAPE_CHUNK_SIZE = 80

# After user clicks Stop on a server-side catalog scrape, re-queue the same scrape after
# this many seconds if listings are still Pending (0 = disabled).
try:
    CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS = max(
        0, int(os.getenv('CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS', '600')),
    )
except ValueError:
    CATALOG_SCRAPE_RESUME_AFTER_STOP_SECONDS = 600

# No server-scrapable listing leaves ``pending`` for this many minutes → stall (see ``catalog.tasks``).
# Wider default reduces false stops on slow vendor pages; clamp 5–120 in code.
try:
    CATALOG_SCRAPE_STALL_MINUTES = max(
        5, min(120, int(os.getenv('CATALOG_SCRAPE_STALL_MINUTES', '20'))),
    )
except ValueError:
    CATALOG_SCRAPE_STALL_MINUTES = 20

if DEBUG:
    ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1,backend')
else:
    ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS')
    if not ALLOWED_HOSTS:
        raise RuntimeError("Missing required environment variable: ALLOWED_HOSTS")

# Required in production. Dev fallback is generated in core.fields only when DEBUG=True.
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '').strip()
if not DEBUG and not ENCRYPTION_KEY:
    raise RuntimeError("Missing required environment variable: ENCRYPTION_KEY")

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000').strip()
# Optional: force redirect URI to match Google Console exactly (e.g. http://localhost:8000/api/v1/auth/google/callback/)
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', '')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    
    # Local apps
    'users',
    'stores',
    'marketplace',
    'vendor',
    'products',
    'catalog',
    'sync',
    'analytics',
    'audit',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
AUTH_USER_MODEL = 'users.User'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith(("postgres://", "postgresql://")):
    parsed = urllib.parse.urlparse(db_url)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': urllib.parse.unquote(parsed.path.lstrip('/') or 'postgres'),
            'USER': urllib.parse.unquote(parsed.username or ''),
            'PASSWORD': urllib.parse.unquote(parsed.password or ''),
            'HOST': parsed.hostname or 'localhost',
            'PORT': parsed.port or 5432,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'static'

# User-uploaded catalog files (async ingest). Mount this path in Docker for multi-container workers.
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Chunk size for catalog file ingest (bulk_create batching). See catalog.services.get_catalog_upload_chunk_size
try:
    CATALOG_UPLOAD_CHUNK_SIZE = max(200, int(os.getenv('CATALOG_UPLOAD_CHUNK_SIZE', '1000')))
except ValueError:
    CATALOG_UPLOAD_CHUNK_SIZE = 1000

try:
    CATALOG_SYNC_LOG_BATCH = max(8, int(os.getenv('CATALOG_SYNC_LOG_BATCH', '32')))
except ValueError:
    CATALOG_SYNC_LOG_BATCH = 32
try:
    CATALOG_SYNC_PROGRESS_EVERY = max(0, int(os.getenv('CATALOG_SYNC_PROGRESS_EVERY', '32')))
except ValueError:
    CATALOG_SYNC_PROGRESS_EVERY = 32

# After catalog sync: reset scrape state on active listings in chunks (avoids one huge UPDATE).
try:
    CATALOG_POST_SYNC_PENDING_RESET_BATCH = max(
        200, min(20000, int(os.getenv('CATALOG_POST_SYNC_PENDING_RESET_BATCH', '2500'))),
    )
except ValueError:
    CATALOG_POST_SYNC_PENDING_RESET_BATCH = 2500
try:
    CATALOG_POST_SYNC_PENDING_RESET_SLEEP_MS = max(
        0, min(5000, int(os.getenv('CATALOG_POST_SYNC_PENDING_RESET_SLEEP_MS', '0'))),
    )
except ValueError:
    CATALOG_POST_SYNC_PENDING_RESET_SLEEP_MS = 0

# Catalog scrape progress API cache TTL (seconds). Invalidated on scrape state changes.
try:
    SCRAPE_PROGRESS_CACHE_SECONDS = max(
        5, min(120, int(os.getenv('SCRAPE_PROGRESS_CACHE_SECONDS', '12'))),
    )
except ValueError:
    SCRAPE_PROGRESS_CACHE_SECONDS = 12

# ``vendor.prune_old_vendor_prices`` retention (days). Keeps latest row per product always.
try:
    VENDOR_PRICE_RETENTION_DAYS = max(
        7, min(3650, int(os.getenv('VENDOR_PRICE_RETENTION_DAYS', '90'))),
    )
except ValueError:
    VENDOR_PRICE_RETENTION_DAYS = 90

# DB persistent connections (seconds). Default 60 reduces new TCP/TLS handshakes to remote Postgres.
# Use PG_CONN_MAX_AGE=0 when sitting behind PgBouncer (transaction pool) or if you see connection exhaustion.
# Only apply for PostgreSQL (CI uses sqlite DATABASE_URL without touching CONN_MAX_AGE here).
if 'postgresql' in DATABASES['default'].get('ENGINE', ''):
    DATABASES['default']['CONN_MAX_AGE'] = int(os.getenv('PG_CONN_MAX_AGE', '60'))

# Cache: Celery uses REDIS_URL (typically …/0). Optional app cache on logical DB 1 when enabled.
_redis_url = os.getenv('REDIS_URL', '').strip()
if (
    (not DEBUG)
    and _env_bool('USE_REDIS_CACHE', False)
    and _redis_url.startswith('redis://')
    and 'postgresql' in DATABASES['default'].get('ENGINE', '')
):
    def _redis_logical_db_url(url: str, db_index: int) -> str:
        p = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse((p.scheme, p.netloc, f'/{db_index}', p.params, p.query, p.fragment))

    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_logical_db_url(_redis_url, 1),
            'KEY_PREFIX': 'ss',
            'TIMEOUT': 60,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'ss-local',
        }
    }

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': '1000/hour',
        'anon': '100/hour',
        'login': '5/minute',
        'sync_trigger': '10/minute',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'SIGNING_KEY': SECRET_KEY,
}

CORS_ALLOWED_ORIGINS = _env_list(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001',
)

# Security
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'

if DEBUG:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False
else:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', True)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', True)
    SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', True)

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
# So AsyncResult leaves PENDING while the task runs; frontend job poll can detect a live worker.
CELERY_TASK_TRACK_STARTED = True
# Prefork pool causes PermissionError on Windows; use solo for local dev
if sys.platform == 'win32':
    CELERY_WORKER_POOL = 'solo'

# --- Celery queues ---
# Browser scrapes: heavy-us (USA stores) vs heavy-au (AU stores), routed by Store.region
# (see catalog.celery_routing.CatalogScrapeTaskRouter). Chord finalizers use ``light``.
# Main server: four workers (-Q celery | -Q ingest | -Q sync | -Q light).
# - ``ingest``: file ingest + catalog_sync + catalog_update (fast API paths).
# - ``sync``: store scrape/push/update tasks (``run_store_*``) — isolated from ingest uploads.
# - ``light``: scrape chord finalizers, resume-after-stop, Beat ``check_scheduled_updates``.
# - ``celery``: default (analytics, vendor price prune).
# US VPS: -Q heavy-us. AU VPS: -Q heavy-au. Vevor AU feed → ``light`` (no browser).
from kombu import Queue  # noqa: E402

from catalog.celery_routing import CatalogScrapeTaskRouter  # noqa: E402

CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_TASK_QUEUES = (
    Queue('celery'),
    Queue('ingest'),
    Queue('sync'),
    Queue('light'),
    Queue('heavy-us'),
    Queue('heavy-au'),
)
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_ROUTES = (
    CatalogScrapeTaskRouter(),
    {
        'catalog.ingest_upload_file': {'queue': 'ingest'},
        # DB-heavy row loop + chunked pending reset; keep off default/light queues.
        'catalog.tasks.catalog_sync_task': {'queue': 'ingest'},
        'catalog.tasks.catalog_update_task': {'queue': 'ingest'},
        'catalog.tasks.resume_catalog_scrape_after_stop': {'queue': 'light'},
        'catalog.run_vevor_au_ingest': {'queue': 'light'},
        # Store-wide scrape + marketplace push: separate from catalog file ingest.
        'sync.tasks.run_store_sync': {'queue': 'sync'},
        'sync.tasks.run_store_update': {'queue': 'sync'},
        'sync.tasks.run_store_push_listings_only': {'queue': 'sync'},
        'sync.tasks.run_store_critical_zero_inventory': {'queue': 'sync'},
        'sync.tasks.run_store_failed_zero_inventory': {'queue': 'sync'},
        # Minute tick enqueues ``run_store_update`` → ``sync`` queue.
        'sync.tasks.check_scheduled_updates': {'queue': 'light'},
        'vendor.prune_old_vendor_prices': {'queue': 'celery'},
    },
)

# AliExpress Drop Shipping + optional Affiliate API (price lookup by product ID).
ALIEXPRESS_APP_KEY = os.getenv('ALIEXPRESS_APP_KEY', '').strip()
ALIEXPRESS_APP_SECRET = os.getenv('ALIEXPRESS_APP_SECRET', '').strip()
ALIEXPRESS_TRACKING_ID = os.getenv('ALIEXPRESS_TRACKING_ID', '').strip()
ALIEXPRESS_DEFAULT_MARKET = (os.getenv('ALIEXPRESS_DEFAULT_MARKET', 'UK') or 'UK').strip().upper()
ALIEXPRESS_SIGN_METHOD = (os.getenv('ALIEXPRESS_SIGN_METHOD', 'md5') or 'md5').strip().lower()
# Overseas gateway (api.taobao.com). gw.api.taobao.com times out on many EU VPS hosts.
ALIEXPRESS_API_URL = (
    os.getenv('ALIEXPRESS_API_URL', 'https://api.taobao.com/router/rest') or 'https://api.taobao.com/router/rest'
).strip()
# Drop Shipping OAuth (api-sg.aliexpress.com IOP).
ALIEXPRESS_IOP_GATEWAY = (
    os.getenv('ALIEXPRESS_IOP_GATEWAY', 'https://api-sg.aliexpress.com') or 'https://api-sg.aliexpress.com'
).strip()
ALIEXPRESS_OAUTH_REDIRECT_URI = os.getenv('ALIEXPRESS_OAUTH_REDIRECT_URI', '').strip()
# Optional single-tenant fallback when per-user OAuth is not set up yet.
ALIEXPRESS_ACCESS_TOKEN = os.getenv('ALIEXPRESS_ACCESS_TOKEN', '').strip()
ALIEXPRESS_REFRESH_TOKEN = os.getenv('ALIEXPRESS_REFRESH_TOKEN', '').strip()
# Drop Shipping: only count in-stock when shipping ETA max days is <= this (default 7).
try:
    ALIEXPRESS_MAX_DELIVERY_DAYS = max(1, int(os.getenv('ALIEXPRESS_MAX_DELIVERY_DAYS', '7') or '7'))
except (TypeError, ValueError):
    ALIEXPRESS_MAX_DELIVERY_DAYS = 7
