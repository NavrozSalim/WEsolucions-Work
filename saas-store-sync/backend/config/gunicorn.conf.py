"""
Gunicorn config for production. Use:
  gunicorn core.wsgi:application -c config/gunicorn.conf.py
"""
import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
keepalive = 2
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
