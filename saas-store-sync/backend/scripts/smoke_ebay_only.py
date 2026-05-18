import sys
import time

sys.path.insert(0, ".")

from scrapers import close_amazon_session, get_price_and_stock

CASES = [
    ("eBay US", "https://www.ebay.com/itm/182391181967", "USA"),
    ("eBay AU", "https://www.ebay.com.au/itm/182391181967", "AU"),
]

for label, url, region in CASES:
    s = {}
    t0 = time.perf_counter()
    r = get_price_and_stock(url, region, s)
    close_amazon_session(s)
    sec = round(time.perf_counter() - t0, 1)
    stock = r.get("inventory") if r.get("inventory") is not None else r.get("stock")
    print(f"{label}: {sec}s price={r.get('price')} stock={stock}")
