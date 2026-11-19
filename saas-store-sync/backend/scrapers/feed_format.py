"""Stop a vendor feed (Costway AU CSV, Vevor AU XLSX) whose format has changed.

A changed layout must not turn into wrong prices or stock on the marketplace. The feed fails
(``FeedFormatError``) when:

* a required column is not in the header (no guessing by position or by a different price column);
* fewer than 80% of the product rows have a price above 0;
* more than 10% of the stock cells are not whole numbers;
* more than 10% of the rows cannot be read (Costway);
* the file has less than half the rows of the last good file for that vendor
  (``python manage.py accept_vendor_feed_size vevor|costway`` accepts a smaller file).

When a feed fails, nothing from it is applied: the vendor's products keep their last good price and
stock, stay Pending and are not sent to the marketplace. Other vendors are not affected.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("scrapers.feed_format")

MIN_PRICED_SHARE = 0.8
MAX_BAD_STOCK_SHARE = 0.1
MAX_SKIPPED_SHARE = 0.1
MIN_ROWS_SHARE = 0.5
LAST_GOOD_TTL = 60 * 60 * 24 * 90
_WHOLE_NUMBER_RE = re.compile(r"^\d+(\.0+)?$")


class FeedFormatError(RuntimeError):
    """The vendor file's format changed; nothing from it may be applied."""

    def __init__(self, label: str, problem: str):
        self.label = label
        self.problem = problem
        super().__init__(
            f"{label} feed format changed: {problem} "
            f"{label} prices and stock were not updated and nothing was sent to the marketplace."
        )


def is_whole_number(value) -> bool:
    """Stock cell check: an int, a whole float, or text like '117' / '117.0'. Blank counts as 0 (fine)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return value >= 0 and value.is_integer()
    text = str(value).strip().replace(",", "")
    return not text or bool(_WHOLE_NUMBER_RE.match(text))


def require_columns(label: str, header_row, found: dict) -> None:
    """``found`` maps the column names the feed must have to their index (None when missing)."""
    missing = [name for name, idx in found.items() if idx is None]
    if not missing:
        return
    seen = [str(c).strip() for c in (header_row or ()) if c is not None and str(c).strip()]
    shown = ", ".join(seen[:15]) + (", …" if len(seen) > 15 else "")
    names = ", ".join(f'"{m}"' for m in missing)
    raise FeedFormatError(
        label,
        f"column{'s' if len(missing) > 1 else ''} {names} not found "
        f"(columns now: {shown or 'none'}).",
    )


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001 - outside Django (scripts) the row-count check is skipped
        return None


def _last_good_key(kind: str) -> str:
    return f"vendor_feed_last_good_rows:{kind}"


def check_feed_quality(
    kind: str,
    label: str,
    *,
    rows: int,
    priced: int,
    bad_stock: int = 0,
    skipped: int = 0,
) -> None:
    """Raise FeedFormatError when the values do not look like this vendor's file. On success, remember
    the row count for the next comparison."""
    if rows <= 0:
        raise FeedFormatError(label, "the file has no product rows.")
    read = rows - skipped
    if skipped and skipped / rows > MAX_SKIPPED_SHARE:
        raise FeedFormatError(
            label, f"{skipped} of {rows} rows could not be read (price, stock or link not where expected).")
    if read <= 0 or priced / read < MIN_PRICED_SHARE:
        raise FeedFormatError(
            label, f"only {priced} of {max(read, 0)} rows have a price above 0 (the price column may have moved).")
    if bad_stock and bad_stock / read > MAX_BAD_STOCK_SHARE:
        raise FeedFormatError(
            label, f"{bad_stock} of {read} stock values are not whole numbers (the stock column may have moved).")

    cache = _cache()
    previous = None
    if cache is not None:
        try:
            previous = cache.get(_last_good_key(kind))
        except Exception:  # noqa: BLE001
            previous = None
    if previous and rows < previous * MIN_ROWS_SHARE:
        raise FeedFormatError(
            label, f"the file has {rows} rows, less than half of the last good file ({previous} rows).")
    if cache is not None:
        try:
            cache.set(_last_good_key(kind), rows, LAST_GOOD_TTL)
        except Exception:  # noqa: BLE001
            logger.debug("Could not remember the %s feed row count", label)


def forget_last_good(kind: str) -> None:
    """Accept a smaller vendor file: the next feed sets the new row count."""
    cache = _cache()
    if cache is not None:
        cache.delete(_last_good_key(kind))


def last_good_rows(kind: str):
    cache = _cache()
    return cache.get(_last_good_key(kind)) if cache is not None else None


__all__ = [
    "FeedFormatError",
    "forget_last_good",
    "last_good_rows",
    "check_feed_quality",
    "is_whole_number",
    "require_columns",
]
