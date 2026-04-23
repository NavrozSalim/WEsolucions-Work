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
# 0 = always one task (legacy). Typical prod: 300–600 with worker concurrency ≥ 2.
try:
    CATALOG_SCRAPE_CHUNK_SIZE = max(0, int(os.getenv('CATALOG_SCRAPE_CHUNK_SIZE', '400')))
except ValueError:
    CATALOG_SCRAPE_CHUNK_SIZE = 400

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

# DB: use CONN_MAX_AGE=0 when sitting behind PgBouncer (transaction pool).
if os.getenv('DATABASE_URL'):
    DATABASES['default']['CONN_MAX_AGE'] = int(os.getenv('PG_CONN_MAX_AGE', '0'))
elif not DEBUG:
    DATABASES['default']['CONN_MAX_AGE'] = int(os.getenv('PG_CONN_MAX_AGE', '0'))

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

# --- Celery queues (no K8s: run a second worker with -Q heavy -c 1 for browser scrapes) ---
from kombu import Queue  # noqa: E402

CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_TASK_QUEUES = (
    Queue('celery'),
    Queue('ingest'),
    Queue('light'),
    Queue('heavy'),
)
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_ROUTES = {
    'catalog.ingest_upload_file': {'queue': 'ingest'},
    'catalog.tasks.catalog_sync_task': {'queue': 'light'},
    'catalog.tasks.catalog_scrape_task': {'queue': 'heavy'},
    'catalog.tasks.catalog_scrape_store_task': {'queue': 'heavy'},
    'catalog.tasks.catalog_scrape_upload_chunk_task': {'queue': 'heavy'},
    'catalog.tasks.catalog_scrape_store_chunk_task': {'queue': 'heavy'},
    'catalog.run_vevor_au_ingest': {'queue': 'light'},
}
