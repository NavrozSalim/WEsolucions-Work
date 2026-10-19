"""How large one discovery file is allowed to be."""

MAX_CATEGORY_LINKS = 200
MAX_PRODUCT_ROWS = 100_000


def limit_detail(mode: str, count: int) -> str | None:
    """Rejection message when a file is over the cap. None when it is allowed."""
    if mode == 'category' and count > MAX_CATEGORY_LINKS:
        return (
            f'A category file can contain at most {MAX_CATEGORY_LINKS} links. '
            f'This file has {count}. Split it into smaller files.'
        )
    if mode == 'product' and count > MAX_PRODUCT_ROWS:
        shown = f'{count:,}'
        cap = f'{MAX_PRODUCT_ROWS:,}'
        return (
            f'A product file can contain at most {cap} rows. '
            f'This file has {shown}. Split it into smaller files.'
        )
    return None
