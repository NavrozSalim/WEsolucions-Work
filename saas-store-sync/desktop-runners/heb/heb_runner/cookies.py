from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def load_cookies(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Cookies file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{p}: expected JSON array of cookies")
    out: list[dict[str, Any]] = []
    now = int(time.time())
    for raw in data:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        exp = raw.get("expirationDate") or raw.get("expiry")
        if isinstance(exp, (int, float)) and int(exp) < now:
            continue
        cookie: dict[str, Any] = {
            "name": raw["name"],
            "value": raw.get("value", ""),
            "path": raw.get("path") or "/",
        }
        domain = raw.get("domain")
        if domain:
            cookie["domain"] = domain
        if raw.get("secure"):
            cookie["secure"] = True
        if raw.get("httpOnly"):
            cookie["httpOnly"] = True
        same = raw.get("sameSite")
        if same in ("None", "Lax", "Strict"):
            cookie["sameSite"] = same
        elif str(same).lower() in ("no_restriction", "none"):
            cookie["sameSite"] = "None"
        out.append(cookie)
    return out


def inject_cookies(driver, cookies: list[dict[str, Any]]) -> int:
    added = 0
    for c in cookies:
        try:
            domain = (c.get("domain") or "").lower()
            if domain and "heb.com" not in domain:
                continue
            driver.add_cookie(c)
            added += 1
        except Exception:
            continue
    return added
