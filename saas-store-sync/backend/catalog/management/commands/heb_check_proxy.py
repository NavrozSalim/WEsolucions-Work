"""
Health-check HEB US residential proxies from inside the worker container.

For each proxy URL configured via HEB_US_PROXY_URLS / PROXY_URLS / etc., this
command reports:

  1. Egress IP via https://api.ipify.org?format=json
     (confirms the proxy actually proxies and shows the IP HEB will see)
  2. ASN / org / city / type via https://ipinfo.io/json
     (residential providers look like "Comcast"/"Spectrum"/"AT&T";
      datacenter providers look like "DigitalOcean"/"OVH"/"Hetzner")
  3. HEB homepage status + first 200 chars of body
     (a 200 with HEB HTML = good; 401/403 = the IP is bot-flagged by Akamai)
  4. Optional product-detail URL with --product-url
     (same probe but against a real PDP — what production traffic actually hits)

Use this when scrapes start returning ``blocked_http_401`` /
``blocked_akamai_incident`` to tell whether the proxy itself is the problem.

Usage::

    docker compose -f docker-compose.us-scraper.prod.yml --env-file .env.prod \\
        exec -T celery_worker_us \\
        python manage.py heb_check_proxy \\
            --product-url https://www.heb.com/product-detail/377497
"""
from __future__ import annotations

from urllib.parse import urlparse

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Diagnose HEB US residential proxies (egress IP, ASN, HEB reachability)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--product-url",
            type=str,
            default="",
            help="Optional HEB product URL to fetch through each proxy.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=15.0,
            help="Per-request timeout in seconds (default 15).",
        )

    def handle(self, *args, **options):
        from scrapers.heb_us_proxies import load_proxy_urls

        session_factory, impersonate = self._pick_http_session()

        proxies = load_proxy_urls()
        if not proxies:
            self.stderr.write(
                self.style.ERROR(
                    "No HEB_US_PROXY_URLS / PROXY_URLS configured in this container."
                )
            )
            return

        product_url = (options.get("product_url") or "").strip()
        timeout = float(options.get("timeout") or 15.0)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Checking {len(proxies)} proxy URL(s) (timeout={timeout:.0f}s)..."
            )
        )
        if impersonate:
            self.stdout.write(f"HTTP client: curl_cffi (impersonate={impersonate})")
        else:
            self.stdout.write(
                "HTTP client: stdlib requests (curl_cffi unavailable in this container)"
            )

        for i, url in enumerate(proxies, start=1):
            parsed = urlparse(url)
            label = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"[{i}/{len(proxies)}] {label}"))
            proxies_arg = {"http": url, "https": url}

            probes = [
                ("egress IP", "https://api.ipify.org?format=json"),
                ("ipinfo  ", "https://ipinfo.io/json"),
                ("HEB home", "https://www.heb.com/"),
            ]
            if product_url:
                probes.append(("HEB PDP ", product_url))

            for desc, target in probes:
                self._probe(session_factory, target, proxies_arg, timeout, impersonate, desc)

    def _pick_http_session(self):
        """Prefer curl_cffi (matches the scraper's TLS fingerprint)."""
        try:
            from curl_cffi import requests as cc_requests

            return cc_requests.Session, "chrome131"
        except ImportError:
            import requests

            return requests.Session, None

    def _probe(self, session_factory, url, proxies_arg, timeout, impersonate, desc):
        session = session_factory()
        kwargs = {
            "timeout": timeout,
            "proxies": proxies_arg,
            "allow_redirects": True,
        }
        if impersonate:
            kwargs["impersonate"] = impersonate
        try:
            resp = session.get(url, **kwargs)
            status = getattr(resp, "status_code", None)
            body = (getattr(resp, "text", "") or "")
            excerpt = " ".join(body.split())[:200]
            line = f"  {desc} status={status:>4}  {url}"
            if status == 200:
                self.stdout.write(self.style.SUCCESS(line))
            elif status in (401, 403, 407, 429):
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)
            if excerpt:
                self.stdout.write(f"           body: {excerpt!r}")
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"  {desc} ERROR  {url}\n"
                    f"           {type(exc).__name__}: {exc}"
                )
            )
