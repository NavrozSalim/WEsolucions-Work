FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    chromium chromium-driver \
    xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Optional real Google Chrome next to Chromium, for eBay US (EBAY_US_GOOGLE_CHROME=1).
# Only the US scraper builds with INSTALL_GOOGLE_CHROME=1. Google publishes Linux
# Chrome for amd64 only. The chromedriver comes from Chrome for Testing for the
# same major version, so the two always match.
ARG INSTALL_GOOGLE_CHROME=0
RUN if [ "$INSTALL_GOOGLE_CHROME" = "1" ]; then \
      set -eux; \
      arch="$(dpkg --print-architecture)"; \
      if [ "$arch" != "amd64" ]; then echo "Google Chrome for Linux needs amd64, this build is $arch"; exit 1; fi; \
      apt-get update; \
      apt-get install -y --no-install-recommends curl gnupg unzip ca-certificates; \
      curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg; \
      echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends google-chrome-stable; \
      major="$(google-chrome --version | grep -oE '[0-9]+' | head -1)"; \
      ver="$(curl -fsSL "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${major}")"; \
      curl -fsSL -o /tmp/chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/${ver}/linux64/chromedriver-linux64.zip"; \
      unzip -q /tmp/chromedriver.zip -d /tmp/chromedriver; \
      install -m 0755 /tmp/chromedriver/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver-google; \
      rm -rf /tmp/chromedriver /tmp/chromedriver.zip /var/lib/apt/lists/*; \
      google-chrome --version; /usr/local/bin/chromedriver-google --version; \
    fi
ENV GOOGLE_CHROME_BIN=/usr/bin/google-chrome
ENV GOOGLE_CHROMEDRIVER_PATH=/usr/local/bin/chromedriver-google

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Warm static files when DATABASE_URL is unset (e.g. image-only smoke build)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
