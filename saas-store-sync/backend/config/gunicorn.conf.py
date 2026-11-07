"""
Gunicorn config for production. Use:
  gunicorn core.wsgi:application -c config/gunicorn.conf.py
"""
import os

bind = "0.0.0.0:8000"
# Default 3: matches prod Docker CPU budget (~2 vCPU); override via GUNICORN_WORKERS in .env.prod.
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
# gthread: each worker serves several requests at once, so a few slow uploads or
# scrapes no longer leave the laptops' quick job polls with no free worker.
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
max_requests = 1000
max_requests_jitter = 50
# 300s matches nginx proxy_read_timeout so a full Bunnings category template can finish.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
# Let in-flight requests finish when a worker is recycled or the container stops.
graceful_timeout = 30
# Longer than nginx's upstream keepalive_timeout (60s), so gunicorn never closes an
# idle connection that nginx is about to reuse (that race shows up as a random 502).
keepalive = 75
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
