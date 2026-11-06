"""Reverb and Etsy listing policy, applied to scraped Amazon / eBay products.

The rules come from "listings policy for reverb and etsy from amazon and
ebay" (Sheet1 Reverb, Sheet2 Etsy, Sheet3 restricted brands). Steps that need
a person (checking the brand in images, editing images, the final review)
are listed in the workbook's checklist sheet instead of being guessed here.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

REVERB = 'reverb'
ETSY = 'etsy'
TARGETS = (REVERB, ETSY)
TARGET_LABELS = {REVERB: 'Reverb', ETSY: 'Etsy'}

# Step 1: the scrape must use a New York delivery ZIP.
ALLOWED_ZIPS = {
    REVERB: ('10001', '20001'),
    ETSY: ('10001',),
}

# Only the US scrapes match the New York ZIP rule.
SUPPORTED_MARKETPLACES = ('amazon_us', 'ebay_us')

FREE_DELIVERY_DAYS = 7

RESTRICTED_BRANDS = (
    'Moom', 'MUSIC', "D'Addario", 'Yamaha', 'Casio', 'Fender', 'Boss', 'Fishman',
    'Dunlop', 'Roland', 'MOOG', 'Seymour', 'Ernie Ball', 'Elixir', 'Logitech', 'Cort',
    'Logitech for Creators', 'ESP', 'Vic Firth', 'Evans', 'Martin', 'promark', 'Remo',
    'GHS', 'Meinl', 'Planet Waves', 'Sabian', 'Gibson', 'Drum Workshop', 'DW', 'TAMA',
    'Mapex', 'Laney', 'Zoom', 'ProTec', 'Zildjian', 'epiphone', 'Amazon', 'Jackson',
    'Behringer', 'Sony', 'MXR', 'Rode', 'Amazon Basics', 'Hohner', 'Squier', 'XUYANGHENG',
    'Marshall', 'Latin Percussion', 'LP', 'PRS', 'Presonus', 'Neumann', 'Martin Ranger',
    'JBL', 'ANALOG', 'Ibanez', 'Taylor', 'Audio-Technica', 'Harbinger', 'Pig Hog',
    'Mogami', 'Alesis', 'GECKO', 'MOOER', 'HyperX', 'Schecter', 'PYLE', 'MONOPRICE',
    'TONOR', 'Sennheiser', 'Rockville', 'Shure', 'Depusheng', 'talomen', 'VeGue',
    'lotame', 'GTDaudio', 'fv', 'BASN', 'D Debra', 'Bietrun', 'FIFINE', 'RockJam',
    'Donner', 'MusicNomad', 'Lixada', 'Vox', 'Blackstar', 'Line 6', 'PDP By DW',
    'M audio', 'M-Audio', 'Akai',
)

MANUAL_CHECKS = {
    REVERB: (
        'Handling time (eBay): ships within 2 business days. The scrape does not read handling time.',
        'Ships from Amazon (optional).',
        'Brand name: check every image.',
        'Edit all images one by one.',
        'Add the SKU and UPC columns.',
        'Final review of every listing before publishing.',
    ),
    ETSY: (
        'Ships from Amazon (optional).',
        'Brand name: check every image.',
        'Edit all images one by one.',
        'Add the SKU column. Fill item weight and package size.',
        'Final review of every listing before publishing.',
    ),
}


# ---------------------------------------------------------------- numbers

def to_number(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(',', '')
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(match.group(0)) if match else None


def quantity_for_stock(stock) -> int | None:
    """7–9 → 1, 10–29 → 2, 30 and above → 5. Under 7 or unknown → skip."""
    number = to_number(stock)
    if number is None or number < 7:
        return None
    if number < 10:
        return 1
    if number < 30:
        return 2
    return 5


def reverb_rating_ok(rating, reviews) -> bool:
    """Reverb table: 3.5–3.9 needs 20 reviews, 4.0–4.4 needs 10, 4.5–4.9 needs 5, 5 needs 1."""
    stars = to_number(rating)
    count = to_number(reviews) or 0
    if stars is None or stars < 3.5:
        return False
    if stars >= 5.0:
        need = 1
    elif stars >= 4.5:
        need = 5
    elif stars >= 4.0:
        need = 10
    else:
        need = 20
    return count >= need


def etsy_rating_ok(rating, reviews) -> bool:
    stars = to_number(rating)
    count = to_number(reviews) or 0
    return stars is not None and stars >= 4.0 and count >= 20


# ---------------------------------------------------------------- delivery

_MONTHS = {
    name: index
    for index, names in enumerate((
        ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
        ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
        ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'), ('dec', 'december'),
    ), start=1)
    for name in names
}
_MONTH_DAY_RE = re.compile(r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b(?:\s*[-–]\s*(\d{1,2})\b(?!\s*[:A-Za-z]))?')
_WEEKDAY_RE = re.compile(r'\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day|nesday|rsday|urday|sday)?\b', re.I)


def latest_delivery_date(text: str, scraped_on: date) -> date | None:
    """Last date in a delivery promise such as “Tuesday, November 10 - 12”."""
    raw = str(text or '').strip()
    if not raw:
        return None
    low = raw.lower()
    found = []
    for match in _MONTH_DAY_RE.finditer(raw):
        month = _MONTHS.get(match.group(1).lower())
        if not month:
            continue
        day = int(match.group(3) or match.group(2))
        try:
            when = date(scraped_on.year, month, day)
        except ValueError:
            continue
        if when < scraped_on - timedelta(days=60):
            when = date(scraped_on.year + 1, month, day)
        found.append(when)
    if found:
        return max(found)
    if 'overnight' in low or 'today' in low:
        return scraped_on
    if 'tomorrow' in low:
        return scraped_on + timedelta(days=1)
    if _WEEKDAY_RE.search(raw):
        # A bare weekday is always inside the coming week.
        return scraped_on + timedelta(days=6)
    return None


def free_delivery_within_week(row: dict, scraped_on: date) -> tuple[bool, str]:
    if str(row.get('free_delivery') or '').strip().lower() != 'yes':
        return False, 'No free delivery'
    arrives = latest_delivery_date(row.get('delivery_date'), scraped_on)
    if arrives is None:
        return False, 'Delivery date unknown'
    if (arrives - scraped_on).days > FREE_DELIVERY_DAYS:
        return False, f'Delivery later than {FREE_DELIVERY_DAYS} days'
    return True, ''


# ---------------------------------------------------------------- brands

def _brand_key(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


def _contains_phrase(haystack_key: str, phrase_key: str) -> bool:
    if not phrase_key:
        return False
    return re.search(rf'(?:^| ){re.escape(phrase_key)}(?: |$)', haystack_key) is not None


def restricted_brand(row: dict) -> str:
    """Restricted brand found in the brand field or at the start of the title."""
    brand_key = _brand_key(row.get('brand'))
    title_key = _brand_key(row.get('title'))
    for name in RESTRICTED_BRANDS:
        key = _brand_key(name)
        if brand_key and _contains_phrase(brand_key, key):
            return name
        if title_key.startswith(key + ' ') or title_key == key:
            return name
    return ''


def clean_brand(value: str) -> str:
    text = re.sub(r'^(Brand:\s*|Visit the\s+)', '', str(value or '').strip(), flags=re.I)
    text = re.sub(r'\s+Store$', '', text).strip()
    if text.lower() in ('unbranded', 'generic', 'does not apply', 'n/a', 'na', 'none'):
        return ''
    return text


def remove_brand(text: str, brand: str) -> str:
    out = str(text or '')
    if brand:
        out = re.sub(rf'(?<![A-Za-z0-9]){re.escape(brand)}(?:®|™)?(?![A-Za-z0-9])', ' ', out, flags=re.I)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    out = re.sub(r'^\s*[-,|:]\s*', '', out)
    out = re.sub(r'\(\s*\)', '', out)
    return out.strip(' ,-|')


# ---------------------------------------------------------------- warranty / contact

_CONTACT_RE = re.compile(
    r'warrant|guarantee|money[- ]back|refund|return policy|customer service|customer support|'
    r'after[- ]sales?|contact us|contact our|reach out|feel free to|please contact|email|e-mail|'
    r'phone|call us|whatsapp|support team|service team|24/7|24 hours|hassle[- ]free|worry[- ]free|'
    r'https?://|www\.|\S+@\S+\.\w+',
    re.I,
)


def strip_contact_info(text: str) -> str:
    """Drop sentences about warranty, guarantees, or how to contact the seller."""
    parts = re.split(r'(?<=[.!?])\s+', str(text or '').strip())
    kept = [part for part in parts if part and not _CONTACT_RE.search(part)]
    return ' '.join(kept).strip()


def split_points(value) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r'\s*\|\s*|\n+', str(value or ''))
    return [re.sub(r'\s+', ' ', str(part)).strip(' -•*·') for part in parts if str(part or '').strip(' -•*·')]


def clean_points(points: list[str], brand: str) -> list[str]:
    out = []
    seen = set()
    for point in points:
        cleaned = remove_brand(strip_contact_info(point), brand)
        key = cleaned.casefold()
        if len(cleaned) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


# ---------------------------------------------------------------- Etsy headings

_HEADING_RE = re.compile(r'^\s*(?:【([^】]{2,60})】|\[([^\]]{2,60})\]|([^:：.!?]{2,60})[:：])\s*(.+)$')

_HEADING_KEYWORDS = (
    (('package', 'includes', 'included', 'what you get', 'you will get', 'comes with'), 'Package Includes'),
    (('material', 'made of', 'made from', 'steel', 'aluminum', 'aluminium', 'wood', 'plastic', 'leather', 'fabric'), 'Quality Materials'),
    (('size', 'dimension', 'inch', 'weight', 'cm', 'mm', 'lightweight', 'portable', 'compact'), 'Size & Portability'),
    (('compatible', 'compatibility', 'fits', 'works with', 'suitable for'), 'Compatibility'),
    (('easy to', 'install', 'setup', 'set up', 'assemble', 'plug and play'), 'Easy Setup'),
    (('battery', 'power', 'charging', 'rechargeable', 'usb', 'voltage', 'watt'), 'Power'),
    (('sound', 'audio', 'tone', 'noise', 'bass', 'volume'), 'Sound'),
    (('light', 'led', 'color', 'colour', 'bright'), 'Lighting & Color'),
    (('gift', 'perfect for', 'ideal for', 'great for', 'beginner', 'kids', 'adults'), 'Perfect For'),
    (('durable', 'sturdy', 'long-lasting', 'long lasting', 'stable'), 'Built to Last'),
)


def _title_case(text: str) -> str:
    words = re.sub(r'\s+', ' ', text).strip(' -–:').split(' ')
    small = {'and', 'or', 'of', 'for', 'to', 'the', 'a', 'an', 'in', 'on', 'with'}
    out = []
    for index, word in enumerate(words):
        lower = word.lower()
        out.append(lower if index and lower in small else lower[:1].upper() + lower[1:])
    return ' '.join(out)


def heading_point(point: str, used: set[str]) -> tuple[str, str]:
    """Split a bullet into (heading, text). Rule: replace * with headings for all points."""
    match = _HEADING_RE.match(point)
    if match:
        heading = (match.group(1) or match.group(2) or match.group(3) or '').strip()
        body = match.group(4).strip()
        if heading and len(heading.split()) <= 8 and body:
            heading = _title_case(heading)
            used.add(heading.casefold())
            return heading, body
    low = point.lower()
    for keywords, heading in _HEADING_KEYWORDS:
        if heading.casefold() in used:
            continue
        if any(word in low for word in keywords):
            used.add(heading.casefold())
            return heading, point
    heading = 'Key Features' if 'key features' not in used else 'More Details'
    if heading.casefold() in used:
        heading = 'Additional Information'
    used.add(heading.casefold())
    return heading, point


# ---------------------------------------------------------------- Etsy tags

_STOPWORDS = {
    'a', 'an', 'and', 'or', 'the', 'for', 'with', 'of', 'to', 'in', 'on', 'by', 'at', 'from',
    'is', 'it', 'its', 'as', 'be', 'this', 'that', 'your', 'you', 'our', 'new', 'set', 'pack',
    'pcs', 'pc', 'piece', 'pieces', 'inch', 'inches', 'ft', 'feet', 'cm', 'mm', 'lb', 'lbs',
    'oz', 'kg', 'g', 'w', 'v', 'x', 'size', 'count', 'ct', 'black', 'white', 'upgraded',
    'version', 'model', 'color', 'colour', 'compatible', 'include', 'includes', 'including',
}
_MEASURE_RE = re.compile(r'\d')
GENERIC_TAGS = (
    'gift idea', 'unique gift', 'gift for him', 'gift for her', 'birthday gift',
    'holiday gift', 'christmas gift', 'home essentials', 'everyday use', 'best seller',
)


def _tag_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", str(text or '').lower())
    return [word.strip("'-") for word in words if word.strip("'-") and word not in _STOPWORDS and len(word) > 1]


def _add_tag(tags: list[str], seen: set[str], tag: str) -> None:
    tag = re.sub(r'[^a-z0-9 \-]', '', tag.lower())
    tag = re.sub(r'\s+', ' ', tag).strip(' -')
    if not tag or len(tag) > 20 or _MEASURE_RE.search(tag) or tag in seen:
        return
    seen.add(tag)
    tags.append(tag)


def etsy_tags(title: str, category: str = '', bullets: list[str] | None = None) -> list[str]:
    """Exactly 13 tags, each 20 characters or less, no numbers or duplicates."""
    tags: list[str] = []
    seen: set[str] = set()
    crumbs = [part.strip() for part in re.split(r'\s*>\s*', category or '') if part.strip()]
    title_words = _tag_words(title)
    for size in (3, 2):
        for start in range(0, max(0, len(title_words) - size + 1)):
            _add_tag(tags, seen, ' '.join(title_words[start:start + size]))
            if len(tags) >= 8:
                break
        if len(tags) >= 8:
            break
    for crumb in reversed(crumbs):
        _add_tag(tags, seen, ' '.join(_tag_words(crumb)))
    for point in bullets or []:
        words = _tag_words(point)
        if len(words) >= 2:
            _add_tag(tags, seen, ' '.join(words[:2]))
        if len(tags) >= 13:
            break
    for word in title_words:
        if len(word) >= 4:
            _add_tag(tags, seen, word)
        if len(tags) >= 13:
            break
    for tag in GENERIC_TAGS:
        if len(tags) >= 13:
            break
        _add_tag(tags, seen, tag)
    return tags[:13]


# ---------------------------------------------------------------- Reverb category

_CATEGORY_STOP = {'and', 'or', 'the', 'other', 'more', 'of', 'for', 'a', 'gear', 'parts'}


def _cat_tokens(text: str) -> set[str]:
    words = re.findall(r'[a-z0-9]+', str(text or '').lower().replace('&', ' and '))
    out = set()
    for word in words:
        if word in _CATEGORY_STOP:
            continue
        if len(word) > 3 and word.endswith('ies'):
            word = word[:-3] + 'y'
        elif len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        out.add(_CATEGORY_SYNONYMS.get(word, word))
    return out


_CATEGORY_SYNONYMS = {
    'amplifier': 'amp',
    'mic': 'microphone',
    'headphone': 'headphone',
    'keyboard': 'keyboard',
    'synthesizer': 'synth',
    'speaker': 'speaker',
    'pedal': 'pedal',
}


@lru_cache(maxsize=1)
def reverb_categories() -> tuple[str, ...]:
    path = Path(__file__).with_name('data') / 'reverb_categories.json'
    try:
        return tuple(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return ()


def reverb_category(breadcrumb: str, title: str) -> tuple[str, str]:
    """Best Reverb (Category, Subcategory) for an Amazon / eBay breadcrumb. Blank when unsure."""
    crumbs = [part.strip() for part in re.split(r'\s*>\s*', breadcrumb or '') if part.strip()]
    deepest = _cat_tokens(crumbs[-1]) if crumbs else set()
    near = _cat_tokens(' '.join(crumbs[-2:]))
    whole = _cat_tokens(' '.join(crumbs))
    title_tokens = _cat_tokens(title)
    best = (0.0, '')
    for name in reverb_categories():
        parts = [part.strip() for part in name.split('/') if part.strip()]
        if not parts:
            continue
        leaf = _cat_tokens(parts[-1])
        if not leaf:
            continue
        if leaf <= deepest:
            score = 4.0
        elif leaf <= near:
            score = 3.0
        elif leaf <= whole:
            score = 2.0
        elif leaf <= title_tokens:
            score = 1.0
        else:
            continue
        parent = _cat_tokens(' '.join(parts[:-1]))
        context = whole | title_tokens
        if parent:
            score += 0.5 * len(parent & context) / len(parent)
        score += 0.05 * len(leaf)
        if score > best[0]:
            best = (score, name)
    if not best[1]:
        return '', ''
    parts = [part.strip() for part in best[1].split('/') if part.strip()]
    return parts[0], (parts[-1] if len(parts) > 1 else '')


def leaf_category(breadcrumb: str) -> str:
    crumbs = [part.strip() for part in re.split(r'\s*>\s*', breadcrumb or '') if part.strip()]
    return crumbs[-1] if crumbs else ''


def scraped_day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.today()
