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

SECRET_KEY = os.getenv('JWT_SECRET', 'django-insecure-default')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

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

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Optional: Fernet key for encrypting store API tokens (generate with cryptography.fernet.Fernet.generate_key().decode())
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')
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

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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

CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001').split(',')

# Session cookies: Lax allows OAuth redirect from Google back to our callback
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False  # True only when using HTTPS

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
# Prefork pool causes PermissionError on Windows; use solo for local dev
if sys.platform == 'win32':
    CELERY_WORKER_POOL = 'solo'
