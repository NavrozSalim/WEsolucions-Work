"""Copy scraped product images into the Bunny storage zone and return CDN URLs.

Settings (environment):
    BUNNY_STORAGE_ZONE        storage zone name
    BUNNY_STORAGE_ACCESS_KEY  storage zone password (FTP & API access → Password)
    BUNNY_STORAGE_HOST        storage endpoint, default storage.bunnycdn.com
                              (ny.storage.bunnycdn.com, la.storage.bunnycdn.com … for other regions)
    BUNNY_CDN_URL             pull zone URL, e.g. https://image123cdn.b-cdn.net
    BUNNY_STORAGE_FOLDER      folder inside the zone, default "listings"

Files are named ``{product id}_{n}.jpg`` so the same product always lands on
the same URL and a second export skips images that are already uploaded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_DOWNLOAD_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
}
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


class BunnyNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class BunnyConfig:
    zone: str
    access_key: str
    host: str
    cdn_url: str
    folder: str

    def storage_url(self, path: str) -> str:
        return f'https://{self.host}/{quote(self.zone)}/{quote(path)}'

    def cdn(self, path: str) -> str:
        return f'{self.cdn_url}/{quote(path)}'

    def path_for(self, product_id: str, index: int) -> str:
        safe = re.sub(r'[^A-Za-z0-9_-]+', '-', str(product_id or 'item')).strip('-') or 'item'
        name = f'{safe}_{index}.jpg'
        return f'{self.folder}/{name}' if self.folder else name


def config() -> BunnyConfig:
    zone = (getattr(settings, 'BUNNY_STORAGE_ZONE', '') or '').strip()
    key = (getattr(settings, 'BUNNY_STORAGE_ACCESS_KEY', '') or '').strip()
    cdn_url = (getattr(settings, 'BUNNY_CDN_URL', '') or '').strip().rstrip('/')
    missing = [
        name for name, value in (
            ('BUNNY_STORAGE_ZONE', zone),
            ('BUNNY_STORAGE_ACCESS_KEY', key),
            ('BUNNY_CDN_URL', cdn_url),
        ) if not value
    ]
    if missing:
        raise BunnyNotConfigured(
            'Bunny CDN is not set up on the server. Add ' + ', '.join(missing) + ' to the environment.'
        )
    if not cdn_url.startswith('http'):
        cdn_url = f'https://{cdn_url}'
    host = (getattr(settings, 'BUNNY_STORAGE_HOST', '') or 'storage.bunnycdn.com').strip()
    host = re.sub(r'^https?://', '', host).strip('/')
    folder = (getattr(settings, 'BUNNY_STORAGE_FOLDER', '') or '').strip().strip('/')
    return BunnyConfig(zone=zone, access_key=key, host=host, cdn_url=cdn_url, folder=folder)


def is_configured() -> bool:
    try:
        config()
    except BunnyNotConfigured:
        return False
    return True


def _already_uploaded(session: requests.Session, cfg: BunnyConfig, path: str) -> bool:
    try:
        response = session.get(
            cfg.storage_url(path),
            headers={'AccessKey': cfg.access_key, 'Range': 'bytes=0-0'},
            timeout=20,
            stream=True,
        )
        response.close()
        return response.status_code in (200, 206)
    except requests.RequestException:
        return False


def copy_image(session: requests.Session, cfg: BunnyConfig, source_url: str, path: str) -> str:
    """Upload one image. Returns the CDN URL, or '' when the source or upload fails."""
    source_url = str(source_url or '').strip()
    if not source_url.startswith('http'):
        return ''
    if _already_uploaded(session, cfg, path):
        return cfg.cdn(path)
    try:
        response = session.get(source_url, headers=_DOWNLOAD_HEADERS, timeout=40)
    except requests.RequestException as exc:
        logger.warning('bunny: download failed %s: %s', source_url, exc)
        return ''
    if response.status_code != 200 or not response.content:
        logger.warning('bunny: download %s returned %s', source_url, response.status_code)
        return ''
    if len(response.content) > _MAX_IMAGE_BYTES:
        logger.warning('bunny: %s is too large', source_url)
        return ''
    kind = (response.headers.get('Content-Type') or '').lower()
    if kind and not kind.startswith('image/') and 'octet-stream' not in kind:
        logger.warning('bunny: %s is %s, not an image', source_url, kind)
        return ''
    try:
        upload = session.put(
            cfg.storage_url(path),
            data=response.content,
            headers={'AccessKey': cfg.access_key, 'Content-Type': 'application/octet-stream'},
            timeout=60,
        )
    except requests.RequestException as exc:
        logger.warning('bunny: upload failed %s: %s', path, exc)
        return ''
    if upload.status_code not in (200, 201):
        logger.warning('bunny: upload %s returned %s %s', path, upload.status_code, upload.text[:200])
        if upload.status_code in (401, 403):
            raise BunnyNotConfigured('Bunny rejected the storage key. Check BUNNY_STORAGE_ACCESS_KEY and BUNNY_STORAGE_HOST.')
        return ''
    return cfg.cdn(path)
