"""Read category and product pages into flat rows.

Live HTML from Amazon and eBay changes often. These selectors cover the
server-rendered cards and product fields the discovery job needs. Local sample
jobs do not use this module.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .columns import HOSTS, is_amazon
from .identity import extract_asin, extract_ebay_item_id

_PRICE_RE = re.compile(r'(?:US\s*)?(?:AU\s*)?\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)')
_RATING_RE = re.compile(r'(\d(?:\.\d)?)\s*out of\s*5', re.I)
_REVIEWS_RE = re.compile(r'([\d,]+)\s*(?:ratings?|reviews?|product ratings?)', re.I)
_INVENTORY_RE = re.compile(
    r'only\s+(\d+)\s+left in stock|(\d+)\s+left in stock|more than\s+(\d+)\s+available|(\d+)\s+available|last one',
    re.I,
)


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html or '', 'lxml')
    except Exception:
        return BeautifulSoup(html or '', 'html.parser')


def _text(node) -> str:
    if node is None:
        return ''
    return ' '.join(node.get_text(' ', strip=True).split())


def _price(text: str):
    if not text:
        return None
    match = _PRICE_RE.search(text.replace('\xa0', ' '))
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', ''))
    except ValueError:
        return None


def _inventory_count(*parts) -> int | None:
    """Quantity left, when the page states it. “In stock” alone is not a count."""
    blob = ' '.join(part for part in parts if part)
    if not blob:
        return None
    if re.search(r'last one', blob, re.I):
        return 1
    match = _INVENTORY_RE.search(blob)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group.replace(',', ''))
    return None


def _amazon_host(page_url: str) -> str:
    parts = urlsplit(page_url or '')
    if parts.scheme and parts.netloc and 'amazon.' in parts.netloc:
        return f'{parts.scheme}://{parts.netloc}'
    return HOSTS['amazon_us']


def _amazon_product_url(page_url: str, asin: str) -> str:
    return f'{_amazon_host(page_url)}/dp/{asin}'


def _amazon_card_price(card) -> float | None:
    """Read the offer price from the whole and fraction spans.

    Amazon prints ``429`` and ``38`` as two spans. Joining those digits makes
    ``42938`` and the comma form of that number parses as ``42938.88``.
    """
    whole = card.select_one('.a-price-whole')
    fraction = card.select_one('.a-price-fraction')
    if whole:
        whole_digits = re.sub(r'[^\d]', '', _text(whole))
        fraction_digits = re.sub(r'[^\d]', '', _text(fraction))[:2] if fraction else ''
        if whole_digits:
            if fraction_digits:
                return float(f'{int(whole_digits)}.{fraction_digits}')
            return float(int(whole_digits))
    offscreen = card.select_one('.a-price .a-offscreen')
    if offscreen:
        value = _price(_text(offscreen))
        if value is not None:
            return value
    return _price(_text(card.select_one('.a-price')))


def _abs(base: str, href: str) -> str:
    if not href:
        return ''
    return urljoin(base, href)


_FOLLOW_PHRASES = (
    'view product',
    'view all',
    'see all',
    'shop all',
    'see more',
    'show more',
)


def amazon_results_url(page_url: str) -> str:
    """Turn an Amazon category landing page into the full product grid.

    Pages like ``/b/?node=8882491011`` only render a few featured items. The
    same node on ``/s?rh=n:…`` is the list behind View products.
    """
    text = str(page_url or '').strip()
    if not text or 'amazon.' not in text.lower():
        return ''
    parts = urlsplit(text)
    if not parts.netloc:
        return ''
    host = f'{parts.scheme or "https"}://{parts.netloc}'
    path = parts.path.rstrip('/')
    if path == '/s' or path.startswith('/s/'):
        return ''
    node = ''
    query = parse_qs(parts.query)
    if query.get('node'):
        node = str(query['node'][0]).strip()
    if not node:
        match = re.search(r'/b/(\d{5,})', parts.path)
        node = match.group(1) if match else ''
    if not node or not node.isdigit():
        return ''
    return f'{host}/s?rh=n%3A{node}&fs=true'


_AMAZON_TOTAL_PATTERNS = (
    re.compile(r'of(?:\s+over)?\s+([\d,]+)\+?\s+results', re.I),
    re.compile(r'"totalResultCount"\s*:\s*"?([\d,]+)', re.I),
    re.compile(r'([\d,]{2,})\+\s+results', re.I),
)


def amazon_reported_total(html: str) -> int | None:
    """How many products Amazon says this search has, when the page states it."""
    raw = html or ''
    flat = re.sub(r'<[^>]+>', ' ', raw).replace('\xa0', ' ').replace('&nbsp;', ' ')
    found = []
    for blob in (raw, flat):
        for pattern in _AMAZON_TOTAL_PATTERNS:
            for match in pattern.finditer(blob):
                try:
                    found.append(int(match.group(1).replace(',', '')))
                except ValueError:
                    continue
    return max(found) if found else None


def amazon_with_price(url: str, low_cents: int, high_cents: int) -> str:
    """Same Amazon search limited to a price band. Amounts are cents."""
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    low = max(0, int(low_cents))
    high = max(low, int(high_cents))
    rh_bits = [
        bit for bit in (query.get('rh') or [''])[0].split(',')
        if bit and not bit.startswith('p_36:')
    ]
    rh_bits.append(f'p_36:{low}-{high}')
    query['rh'] = [','.join(rh_bits)]
    query.pop('page', None)
    query.pop('low-price', None)
    query.pop('high-price', None)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path or '/',
        urlencode(query, doseq=True),
        '',
    ))


def with_page(url: str, page: int) -> str:
    """Search result page N. Amazon uses ``page``, eBay uses ``_pgn``."""
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if 'ebay.' in (parts.netloc or '').lower():
        query['_pgn'] = [str(page)]
    else:
        query['page'] = [str(page)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', urlencode(query, doseq=True), ''))


def listing_follow_urls(html: str, page_url: str) -> list[str]:
    """Links labeled View products / See all / Shop all on a category landing page."""
    soup = _soup(html)
    found = []
    seen = set()
    for link in soup.select('a[href]'):
        label = _text(link).lower()
        if not any(phrase in label for phrase in _FOLLOW_PHRASES):
            continue
        href = link.get('href') or ''
        if not href or href.startswith('javascript'):
            continue
        absolute = _abs(page_url, href)
        if '/dp/' in absolute.lower() or '/itm/' in absolute.lower():
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return found


def parse_amazon_category(html: str, page_url: str) -> list[dict]:
    soup = _soup(html)
    rows = []
    seen = set()
    for card in soup.select('[data-asin]'):
        asin = (card.get('data-asin') or '').strip().upper()
        if not re.fullmatch(r'B[A-Z0-9]{9}', asin) or asin in seen:
            continue
        seen.add(asin)
        link = card.select_one('h2 a[href*="/dp/"]') or card.select_one('h2 a') or card.select_one('a.a-link-normal')
        title = _text(link) or _text(card.select_one('h2'))
        title = re.sub(r'^Sponsored\s+Ad\s+', '', title, flags=re.I).strip()
        if not title:
            continue
        rating_node = card.select_one('.a-icon-alt')
        rating = None
        if rating_node:
            match = _RATING_RE.search(_text(rating_node))
            rating = float(match.group(1)) if match else None
        reviews = None
        review_node = card.select_one('[aria-label*="rating"]') or card.select_one('.a-size-base.s-underline-text')
        blob = _text(card)
        review_match = _REVIEWS_RE.search(_text(review_node) if review_node else '') or _REVIEWS_RE.search(blob)
        if review_match:
            reviews = int(review_match.group(1).replace(',', ''))
        image = card.select_one('img')
        rows.append({
            'asin': asin,
            'url': _amazon_product_url(page_url, asin),
            'title': title,
            'price': _amazon_card_price(card),
            'rating': rating,
            'review_count': reviews,
            'image': (image.get('src') if image else '') or '',
            'category': '',
        })
    # Category landing pages sometimes list products only as /dp/ links.
    for link in soup.select('a[href*="/dp/"]'):
        href = _abs(page_url, link.get('href') or '')
        asin = extract_asin(href)
        if not asin or asin in seen:
            continue
        title = _text(link)
        if len(title) < 3:
            continue
        seen.add(asin)
        image = link.select_one('img')
        rows.append({
            'asin': asin,
            'url': _amazon_product_url(page_url, asin),
            'title': title,
            'price': None,
            'rating': None,
            'review_count': None,
            'image': (image.get('src') if image else '') or '',
            'category': '',
        })
    return rows


def parse_amazon_product(html: str, page_url: str) -> dict:
    soup = _soup(html)
    asin = extract_asin(page_url)
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get('href'):
        asin = extract_asin(canonical['href']) or asin
    title = _text(soup.select_one('#productTitle'))
    price_node = soup.select_one('.a-price .a-offscreen') or soup.select_one('#corePrice_feature_div')
    rating_node = soup.select_one('#acrPopover')
    rating = None
    rating_text = (rating_node.get('title') if rating_node else '') or _text(rating_node)
    match = _RATING_RE.search(rating_text)
    if match:
        rating = float(match.group(1))
    review_text = _text(soup.select_one('#acrCustomerReviewText'))
    reviews = None
    review_match = _REVIEWS_RE.search(review_text)
    if review_match:
        reviews = int(review_match.group(1).replace(',', ''))
    brand = _text(soup.select_one('#bylineInfo'))
    brand = re.sub(r'^(Brand:\s*|Visit the\s+)', '', brand, flags=re.I).strip()
    brand = re.sub(r'\s+Store$', '', brand).strip()
    bullets = [
        _text(node)
        for node in soup.select('#feature-bullets li span.a-list-item')
        if _text(node)
    ]
    description = _text(soup.select_one('#productDescription'))
    crumbs = [
        _text(node)
        for node in soup.select('#wayfinding-breadcrumbs_feature_div a')
        if _text(node)
    ]
    images = []
    for image in soup.select('#altImages img, #landingImage'):
        src = image.get('src') or ''
        if src and src not in images:
            images.append(src)
    availability = _text(soup.select_one('#availability'))
    quantity = soup.select_one('#quantity')
    inventory = _inventory_count(availability, _text(quantity))
    seller = _text(soup.select_one('#sellerProfileTriggerId')) or _text(soup.select_one('#merchant-info'))
    return {
        'asin': asin,
        'url': page_url,
        'title': title,
        'brand': brand,
        'price': _amazon_card_price(soup) if soup.select_one('.a-price') else _price(_text(price_node)),
        'rating': rating,
        'review_count': reviews,
        'availability': availability,
        'inventory': inventory,
        'description': description,
        'bullets': ' | '.join(bullets),
        'images': ' | '.join(images),
        'image': images[0] if images else '',
        'category': ' > '.join(crumbs),
        'seller': seller,
    }


def _ebay_next_price(card) -> float | None:
    node = card.select_one('.s-item__price')
    return _price(_text(node))


def parse_ebay_category(html: str, page_url: str) -> list[dict]:
    soup = _soup(html)
    rows = []
    for card in soup.select('li.s-item, .s-item'):
        link = card.select_one('a.s-item__link') or card.select_one('a[href*="/itm/"]')
        href = _abs(page_url, link.get('href') if link else '')
        item_id = extract_ebay_item_id(href) or extract_ebay_item_id(card.get('data-listingid') or '')
        title = _text(card.select_one('.s-item__title'))
        if not item_id or not title or title.lower() == 'shop on ebay':
            continue
        image = card.select_one('img')
        rows.append({
            'item_id': item_id,
            'url': href,
            'title': title,
            'price': _ebay_next_price(card),
            'rating': None,
            'review_count': None,
            'image': (image.get('src') if image else '') or '',
            'condition': _text(card.select_one('.SECONDARY_INFO')),
            'category': '',
        })
    return rows


def parse_ebay_product(html: str, page_url: str) -> dict:
    soup = _soup(html)
    item_id = extract_ebay_item_id(page_url)
    title = _text(soup.select_one('h1.x-item-title__mainTitle')) or _text(soup.select_one('h1'))
    price = _price(_text(soup.select_one('.x-price-primary')))
    seller = _text(soup.select_one('.x-sellercard-atf__info__about-seller')) or _text(
        soup.select_one('span.ux-textspans--BOLD')
    )
    condition = _text(soup.select_one('.x-item-condition-text'))
    crumbs = [
        _text(node)
        for node in soup.select('nav.breadcrumbs a, .seo-breadcrumb-text')
        if _text(node)
    ]
    specifics = []
    for row in soup.select('.ux-labels-values'):
        label = _text(row.select_one('.ux-labels-values__labels'))
        value = _text(row.select_one('.ux-labels-values__values'))
        if label and value:
            specifics.append(f'{label}: {value}')
    description = _text(soup.select_one('#desc_ifr, .d-item-description'))
    images = []
    for image in soup.select('.ux-image-carousel img, img#icImg'):
        src = image.get('src') or ''
        if src and src not in images:
            images.append(src)
    availability = _text(soup.select_one('.x-quantity__availability'))
    inventory = _inventory_count(availability)
    return {
        'item_id': item_id,
        'url': page_url,
        'title': title,
        'brand': '',
        'price': price,
        'rating': None,
        'review_count': None,
        'availability': availability,
        'inventory': inventory,
        'condition': condition,
        'description': description,
        'bullets': ' | '.join(specifics),
        'images': ' | '.join(images),
        'image': images[0] if images else '',
        'category': ' > '.join(crumbs),
        'seller': seller,
    }


def next_page_url(html: str, page_url: str) -> str:
    soup = _soup(html)
    for selector in (
        'a.s-pagination-next',
        'a.pagination__next',
        'a[aria-label="Next page"]',
        'a[rel="next"]',
    ):
        node = soup.select_one(selector)
        href = node.get('href') if node else ''
        if href and 'javascript' not in href:
            return _abs(page_url, href)
    return ''
