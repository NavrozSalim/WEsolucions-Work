"""Drop products that fail the user's rating, review, price, or category rules.

A missing value does not fail a numeric rule. eBay category cards often have no
star rating, and those rows stay until a later page actually has the number.
"""

from __future__ import annotations

import re

_NUMERIC_FIELDS = {
    'rating': 'rating',
    'reviews': 'review_count',
    'price': 'price',
}


def empty_rules() -> dict:
    return {
        'rating': {'op': '', 'value': None, 'min': None, 'max': None},
        'reviews': {'op': '', 'value': None, 'min': None, 'max': None},
        'price': {'op': '', 'value': None, 'min': None, 'max': None},
        'exclude_categories': [],
    }


def _num(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(',', '')
    text = re.sub(r'[A-Za-z$£€]+', ' ', text)
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _clause(raw) -> dict:
    if not isinstance(raw, dict):
        return {'op': '', 'value': None, 'min': None, 'max': None}
    op = str(raw.get('op') or '').strip().lower()
    if op not in ('lt', 'gt', 'between'):
        op = ''
    return {
        'op': op,
        'value': _num(raw.get('value')),
        'min': _num(raw.get('min')),
        'max': _num(raw.get('max')),
    }


def normalize_rules(raw) -> dict:
    base = empty_rules()
    if not isinstance(raw, dict):
        return base
    for field in ('rating', 'reviews', 'price'):
        base[field] = _clause(raw.get(field))
    excluded = raw.get('exclude_categories') or []
    if isinstance(excluded, str):
        excluded = re.split(r'[\n,;]+', excluded)
    cleaned = []
    seen = set()
    for item in excluded:
        text = str(item or '').strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    base['exclude_categories'] = cleaned
    return base


def rules_active(rules: dict) -> bool:
    rules = normalize_rules(rules)
    if rules['exclude_categories']:
        return True
    return any(rules[field]['op'] for field in ('rating', 'reviews', 'price'))


def _fails_numeric(row: dict, field: str, clause: dict) -> bool:
    op = clause.get('op') or ''
    if not op:
        return False
    column = _NUMERIC_FIELDS[field]
    number = _num(row.get(column))
    if number is None:
        return False
    if op == 'lt':
        limit = clause.get('value')
        return limit is not None and number < limit
    if op == 'gt':
        limit = clause.get('value')
        return limit is not None and number > limit
    if op == 'between':
        low = clause.get('min')
        high = clause.get('max')
        if low is None or high is None:
            return False
        if low > high:
            low, high = high, low
        return number < low or number > high
    return False


def _fails_category(row: dict, excluded: list[str]) -> bool:
    if not excluded:
        return False
    category = str(row.get('category') or '').strip()
    if not category:
        return False
    haystack = category.casefold()
    return any(name.casefold() in haystack for name in excluded)


def row_removed(row: dict, rules: dict) -> bool:
    rules = normalize_rules(rules)
    for field in ('rating', 'reviews', 'price'):
        if _fails_numeric(row, field, rules[field]):
            return True
    return _fails_category(row, rules['exclude_categories'])


def apply_rules(rows: list[dict], rules: dict) -> tuple[list[dict], int]:
    rules = normalize_rules(rules)
    if not rules_active(rules):
        return list(rows), 0
    kept = []
    removed = 0
    for row in rows:
        if row_removed(row, rules):
            removed += 1
            continue
        kept.append(row)
    return kept, removed


def row_has_rule_fields(row: dict, rules: dict) -> bool:
    """True when this input row already carries a value the active rules can judge."""
    rules = normalize_rules(rules)
    for field, column in _NUMERIC_FIELDS.items():
        if rules[field]['op'] and _num(row.get(column)) is not None:
            return True
    if rules['exclude_categories'] and str(row.get('category') or '').strip():
        return True
    return False
