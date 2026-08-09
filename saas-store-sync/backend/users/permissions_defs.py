"""Product permissions and seat-plan helpers for Super User orgs."""

from __future__ import annotations

PERMISSION_KEYS = (
    'dashboard',
    'stores',
    'catalog',
    'orders',
    'tickets',
    'team',
)

PERMISSION_LABELS = {
    'dashboard': 'Dashboard',
    'stores': 'Store settings',
    'catalog': 'Catalog & listings',
    'orders': 'Orders',
    'tickets': 'Tickets',
    'team': 'Team management',
}

ALL_PERMISSIONS = {key: True for key in PERMISSION_KEYS}

DEFAULT_MEMBER_PERMISSIONS = {
    'dashboard': True,
    'stores': False,
    'catalog': True,
    'orders': True,
    'tickets': True,
    'team': False,
}

FREE_SEAT_PACK = 5
SEAT_PACK_SIZE = 5
PRICE_PER_PACK_USD = 10


def normalize_permissions(raw) -> dict[str, bool]:
    if not isinstance(raw, dict):
        raw = {}
    return {key: bool(raw.get(key, False)) for key in PERMISSION_KEYS}


def price_for_seats(seats: int) -> int:
    """
    Seat pricing in whole USD:
      5 → $0, 10 → $10, 15 → $20, 20 → $30, …
    """
    if seats < FREE_SEAT_PACK or seats % SEAT_PACK_SIZE != 0:
        raise ValueError(
            f'Seats must be a multiple of {SEAT_PACK_SIZE} and at least {FREE_SEAT_PACK}.'
        )
    if seats <= FREE_SEAT_PACK:
        return 0
    packs_above_free = (seats - FREE_SEAT_PACK) // SEAT_PACK_SIZE
    return packs_above_free * PRICE_PER_PACK_USD


def seat_plan_options(max_seats: int = 50) -> list[dict]:
    options = []
    seats = FREE_SEAT_PACK
    while seats <= max_seats:
        price = price_for_seats(seats)
        options.append({
            'seats': seats,
            'price_usd': price,
            'label': f'{seats} user accounts',
            'is_free': price == 0,
        })
        seats += SEAT_PACK_SIZE
    return options
