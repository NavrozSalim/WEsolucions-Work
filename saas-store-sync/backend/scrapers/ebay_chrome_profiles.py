"""eBay US Chrome: real Google Chrome and a saved profile per proxy (both switches, off by default).

Why: a scraper in someone's own browser gets through eBay's bot wall because it
is a real Chrome with a profile eBay has seen before. The server ran Debian
Chromium dressed up as Windows Chrome (no H.264, no Widevine: a mismatch bot
walls check), started every Chrome with an empty profile, and loaded the same
saved cookies on every proxy.

``EBAY_US_GOOGLE_CHROME=1``
    Launch Google Chrome (``GOOGLE_CHROME_BIN``, ``GOOGLE_CHROMEDRIVER_PATH``)
    instead of Chromium. The US scraper image installs it with the build arg
    ``INSTALL_GOOGLE_CHROME=1``. Missing files fall back to Chromium.

``EBAY_US_PROFILE_PER_PROXY=1``
    Each proxy gets its own Chrome profile under ``EBAY_US_PROFILE_DIR``, kept
    between listings and runs, so the cookies eBay gave that address stay with
    it. The saved cookie file is loaded only into a profile's first Chrome.
    A profile is used by one Chrome at a time (a file lock); a proxy has up to
    ``EBAY_US_PROFILE_SLOTS`` profiles for workers that share it. When every
    slot is busy, Chrome starts with a throwaway profile as before.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_GOOGLE_CHROME_BIN = "/usr/bin/google-chrome"
DEFAULT_GOOGLE_CHROMEDRIVER = "/usr/local/bin/chromedriver-google"
DEFAULT_PROFILE_DIR = "/app/chrome-profiles/ebay-us"
SEEDED_MARKER = ".saved-cookies-loaded"
# Chrome's own "profile in use" files. Safe to delete while we hold the profile's lock.
_CHROME_SINGLETON_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")

_warned: set[str] = set()


def _env_on(name: str) -> bool:
    return (os.environ.get(name) or "0").strip().lower() in ("1", "true", "yes", "on")


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def google_chrome_enabled() -> bool:
    return _env_on("EBAY_US_GOOGLE_CHROME")


def google_chrome_paths() -> Optional[tuple[str, str]]:
    """(chrome binary, matching chromedriver) when Google Chrome is switched on and installed."""
    if not google_chrome_enabled():
        return None
    chrome = (os.environ.get("GOOGLE_CHROME_BIN") or DEFAULT_GOOGLE_CHROME_BIN).strip()
    driver = (os.environ.get("GOOGLE_CHROMEDRIVER_PATH") or DEFAULT_GOOGLE_CHROMEDRIVER).strip()
    if os.path.isfile(chrome) and os.path.isfile(driver):
        return chrome, driver
    _warn_once(
        "google_chrome_missing",
        "eBay US: EBAY_US_GOOGLE_CHROME=1 but Google Chrome (%s) or its chromedriver (%s) is not "
        "installed; using Chromium. Rebuild the US scraper image with INSTALL_GOOGLE_CHROME=1.",
        chrome, driver,
    )
    return None


def profiles_enabled() -> bool:
    return _env_on("EBAY_US_PROFILE_PER_PROXY")


def _profile_root() -> str:
    return (os.environ.get("EBAY_US_PROFILE_DIR") or DEFAULT_PROFILE_DIR).strip()


def _slot_count() -> int:
    try:
        return max(1, min(16, int(os.environ.get("EBAY_US_PROFILE_SLOTS") or "4")))
    except ValueError:
        return 4


def proxy_profile_key(proxy_url: Optional[str]) -> str:
    """A folder name for the proxy's address (host and port only, never the password)."""
    if not proxy_url:
        return "direct"
    raw = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    parsed = urlparse(raw)
    address = f"{parsed.hostname or ''}:{parsed.port or ''}"
    return "p-" + hashlib.sha1(address.encode("utf-8")).hexdigest()[:12]


class ProfileLock:
    """An exclusive lock on one profile folder, held while its Chrome runs.

    The operating system drops it when the process ends, so a worker that dies
    never leaves a profile locked.
    """

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fh.close()


class ProxyProfile:
    """One proxy's Chrome profile, locked for this Chrome."""

    def __init__(self, path: str, lock: ProfileLock):
        self.path = path
        self.lock = lock

    def _marker(self, host: str) -> str:
        safe = "".join(ch for ch in (host or "site") if ch.isalnum() or ch in ".-") or "site"
        return os.path.join(self.path, f"{SEEDED_MARKER}.{safe}")

    def seeded(self, host: str) -> bool:
        """True once the saved cookies for ``host`` went into this profile; later Chromes keep their own."""
        return os.path.isfile(self._marker(host))

    def mark_seeded(self, host: str) -> None:
        try:
            with open(self._marker(host), "w", encoding="utf-8") as fh:
                fh.write("1")
        except OSError as exc:
            logger.debug("eBay US profile marker not written in %s: %s", self.path, exc)

    def release(self) -> None:
        self.lock.release()


def acquire_proxy_profile(proxy_url: Optional[str]) -> Optional[ProxyProfile]:
    """A free profile folder for this proxy, or None (switch off, folder unusable or every slot busy)."""
    if not profiles_enabled():
        return None
    root = _profile_root()
    key = proxy_profile_key(proxy_url)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        _warn_once("profile_root", "eBay US: cannot use profile folder %s (%s); profiles off.", root, exc)
        return None
    for slot in range(_slot_count()):
        path = os.path.join(root, f"{key}-{slot}")
        lock = ProfileLock(path + ".lock")
        try:
            got = lock.acquire()
        except OSError as exc:
            _warn_once("profile_lock", "eBay US: profile lock failed in %s (%s); profiles off.", root, exc)
            return None
        if not got:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            for name in _CHROME_SINGLETON_FILES:
                leftover = os.path.join(path, name)
                if os.path.lexists(leftover):
                    os.remove(leftover)
        except OSError as exc:
            lock.release()
            logger.warning("eBay US: profile %s unusable (%s); using a throwaway profile.", path, exc)
            return None
        return ProxyProfile(path, lock)
    logger.info("eBay US: every profile of proxy %s is in use; using a throwaway profile.", key)
    return None
