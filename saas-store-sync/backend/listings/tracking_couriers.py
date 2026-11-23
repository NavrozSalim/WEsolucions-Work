"""Couriers for Upload tracking: which courier to pick on Kogan / Big W for the carrier Amazon shows.

The carrier text the Tracking Bot reads ("Delivery By Amazon", "Shipped with Australia Post") is turned into one
carrier name (``carrier_name``). The user's saved table (``TrackingCourier``) says what to pick on the marketplace
for that carrier. Only saved carriers are uploaded; ``SUGGESTED`` is shown as a starting point.
"""
from __future__ import annotations

import re

# Carrier text on the vendor's tracking page -> one carrier name. First match wins.
CARRIER_ALIASES = (
    ('Australia Post', ('australia post', 'auspost', 'aus post', 'australiapost')),
    ('StarTrack', ('startrack', 'star track')),
    ('CouriersPlease', ('couriers please', 'couriersplease')),
    ('Aramex', ('aramex', 'fastway')),
    ('DHL', ('dhl',)),
    ('Team Global Express', ('team global express', 'tge', 'toll')),
    ('TNT', ('tnt',)),
    ('FedEx', ('fedex', 'fed ex')),
    ('Sendle', ('sendle',)),
    ('Allied Express', ('allied',)),
    ('Hunter Express', ('hunter express',)),
    ('Direct Freight Express', ('direct freight',)),
    ('Amazon', ('amazon',)),
)
CARRIERS = [name for name, _ in CARRIER_ALIASES if name != 'Amazon']

# Suggested courier names (not used until the user saves them).
SUGGESTED = {
    'kogan': {'Australia Post': 'AUP', 'StarTrack': 'StarTrack', 'CouriersPlease': 'Couriers Please',
              'Aramex': 'Aramex', 'DHL': 'DHL', 'Team Global Express': 'Team Global Express', 'TNT': 'TNT',
              'FedEx': 'FedEx', 'Sendle': 'Sendle', 'Allied Express': 'Allied Express',
              'Hunter Express': 'Hunter Express', 'Direct Freight Express': 'Direct Freight Express'},
    'bigw': {'Australia Post': 'Australia Post', 'StarTrack': 'StarTrack', 'CouriersPlease': 'Couriers Please',
             'Aramex': 'Aramex', 'DHL': 'DHL', 'Team Global Express': 'Team Global Express', 'TNT': 'TNT',
             'FedEx': 'FedEx', 'Sendle': 'Sendle', 'Allied Express': 'Allied Express',
             'Hunter Express': 'Hunter Express', 'Direct Freight Express': 'Direct Freight Express'},
}
# Australia Post article numbers: 23 letters / digits (36QT4180587301000931505, 3697B306387401002010905).
AUSPOST_NUMBER_RE = re.compile(r'^[0-9][0-9A-Z]{22}$')


def clean_number(number) -> str:
    return re.sub(r'\s+', '', str(number or '')).upper()


def is_tba(number) -> bool:
    """Amazon's own delivery numbers (TBA...): only Amazon can track them, so they are never uploaded."""
    return clean_number(number).startswith('TBA')


def carrier_name(raw, number='') -> str:
    """One carrier name for the vendor's carrier text ('' when unknown). A number Amazon labels "Amazon" (or
    shows no carrier for) that is not a TBA one is named by its format (Australia Post)."""
    text = ' '.join(str(raw or '').split()).lower()
    name = ''
    for canon, words in CARRIER_ALIASES:
        if text and any(w in text for w in words):
            name = canon
            break
    if name in ('', 'Amazon') and number and not is_tba(number):
        if AUSPOST_NUMBER_RE.match(clean_number(number)):
            return 'Australia Post'
    return name


def saved_couriers(user, marketplace) -> dict:
    """{carrier name: courier} the user saved for this marketplace."""
    from .models import TrackingCourier

    if user is None:
        return {}
    return {c.carrier: c.courier for c in TrackingCourier.objects.filter(user=user, marketplace=marketplace)}


def courier_table(user, marketplace, seen=()) -> list[dict]:
    """Rows for the Couriers window: every known carrier plus any seen on orders, saved value and suggestion."""
    saved = saved_couriers(user, marketplace)
    names = list(dict.fromkeys([*CARRIERS, *[s for s in seen if s and s != 'Amazon'], *saved]))
    sugg = SUGGESTED.get(marketplace, {})
    return [{'carrier': n, 'courier': saved.get(n, ''), 'suggestion': sugg.get(n, '')} for n in names]


def save_couriers(user, marketplace, rows) -> int:
    """Save the Couriers window: a blank courier removes the carrier. Returns how many are saved now."""
    from .models import TrackingCourier

    for row in rows or []:
        carrier = ' '.join(str((row or {}).get('carrier') or '').split())[:100]
        courier = ' '.join(str((row or {}).get('courier') or '').split())[:255]
        if not carrier:
            continue
        if courier:
            TrackingCourier.objects.update_or_create(user=user, marketplace=marketplace, carrier=carrier,
                                                     defaults={'courier': courier})
        else:
            TrackingCourier.objects.filter(user=user, marketplace=marketplace, carrier=carrier).delete()
    return TrackingCourier.objects.filter(user=user, marketplace=marketplace).count()


def name_tokens(name) -> set:
    return {t for t in re.findall(r'[a-z]+', (name or '').lower()) if len(t) > 1}


def names_differ(ship_name, customer) -> bool:
    """True only when both names are known and they do not share their words (middle names allowed)."""
    a, b = name_tokens(ship_name), name_tokens(customer)
    if not a or not b:
        return False
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return not small <= big
