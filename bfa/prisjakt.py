"""Prisjakt.no: category discovery + product parsing from embedded Next.js flight data."""
import html as htmllib
import json
import re

from . import net

BASE = "https://www.prisjakt.no"
NAME = "prisjakt"

# canonical category -> /c/<slug>  (slugs verified 2026-10-18)
CATEGORIES = {
    "tv": "tv",
    "mobil": "mobiltelefoner",
    "laptop": "baerbare-datamaskiner",
    "hodetelefoner": "hodetelefoner",
    "skjerm": "pc-skjermer",
    "smartklokke": "smartklokker",
    "robotstovsuger": "robotstovsugere",
    "nettbrett": "nettbrett",
    "spillkonsoll": "spillkonsoller",
}

PRODUCT_RE = re.compile(r"product\.php\?p=(\d+)")
_FLIGHT_MARK = "self.__next_f.push([1,"
_DEC = json.JSONDecoder()

LOWEST = "(laveste)"   # pseudo-shop for Prisjakt's cheapest-across-shops history


def category_url(slug, offset=0):
    return "{}/c/{}{}".format(BASE, slug, "?offset={}".format(offset) if offset else "")


def product_url(pid):
    return "{}/product.php?p={}".format(BASE, pid)


def discover(slug, limit, fetch=None):
    """Product ids from a category listing, in page order, following ?offset= pages."""
    fetch = fetch or net.fetch
    ids, offset = [], 0
    while len(ids) < limit and offset < 400:
        page = fetch(category_url(slug, offset))
        before = len(ids)
        for pid in PRODUCT_RE.findall(page):
            if pid not in ids:
                ids.append(pid)
        if len(ids) == before:
            break
        offset += 44  # Prisjakt lists 44 products per page
    return ids[:limit]


def flight_text(page):
    """Join the JSON-string chunks of the Next.js flight payload into one string.

    Uses the C JSON decoder on each chunk instead of a regex: a `"(?:[^"\\\\]|\\\\.)*"` regex keeps
    backtracking state per character and peaked at ~75 MB per page, too much for a 512 MB Pi.
    """
    parts, i = [], page.find(_FLIGHT_MARK)
    while i != -1:
        try:
            s, end = _DEC.raw_decode(page, i + len(_FLIGHT_MARK))
            if isinstance(s, str):
                parts.append(s)
            i = page.find(_FLIGHT_MARK, end)
        except ValueError:
            i = page.find(_FLIGHT_MARK, i + len(_FLIGHT_MARK))
    return "".join(parts)


def _arrays_after(text, key):
    """Yield every JSON value that follows `"key":` in text."""
    needle = '"{}":'.format(key)
    i = text.find(needle)
    while i != -1:
        try:
            val, _ = _DEC.raw_decode(text, i + len(needle))
            yield val
        except ValueError:
            pass
        i = text.find(needle, i + len(needle))


def _amount(x):
    if isinstance(x, dict):
        x = x.get("amount")
    return float(x) if isinstance(x, (int, float)) and x > 0 else None


def _title(page, text):
    m = re.search(r'<script[^>]*application/ld\+json[^>]*>(\{"@context":"https://schema.org","@type":"Product".*?)</script>',
                  page, re.S)
    if m:
        try:
            return json.loads(m.group(1)).get("name")
        except ValueError:
            pass
    t = re.search(r"<title>(.*?)</title>", page, re.S)
    return htmllib.unescape(re.sub(r"\s*-\s*Sammenlign priser.*$", "", t.group(1)).strip()) if t else None


def parse_product(page, pid):
    text = flight_text(page)

    offers, seen = [], set()
    for rows in _arrays_after(text, "offerRows"):
        for o in rows if isinstance(rows, list) else []:
            if not isinstance(o, dict) or str(o.get("productId")) != str(pid):
                continue
            if o.get("id") in seen or o.get("isPaidMembership") or o.get("condition") not in (None, "New"):
                continue
            seen.add(o.get("id"))
            price = _amount(o.get("price"))
            if price is None:
                continue
            orig = _amount(o.get("originalPrice"))
            shop = (o.get("shop") or {}).get("name") or str(o.get("shopId"))
            offers.append({
                "shop": shop,
                "price": price,
                "original_price": orig if orig and orig > price else None,
                "in_stock": o.get("stockStatus") == "InStock",
            })

    history = []
    for items in _arrays_after(text, "historyItems"):
        if isinstance(items, list) and items:
            for h in items:
                if isinstance(h, dict) and h.get("price") and h.get("date") and not h.get("isPaidMembership"):
                    history.append((net.oslo_day(h["date"]), h["price"] / 100.0))  # øre -> kr
            break

    lowest_now = None
    for v in _arrays_after(text, "lowestPrice"):
        if isinstance(v, (int, float)) and v > 0:
            lowest_now = float(v)
            break

    return {
        "source": NAME,
        "product_id": str(pid),
        "title": _title(page, text) or product_url(pid),
        "url": product_url(pid),
        "offers": offers,
        "lowest_history": history,
        "lowest_now": lowest_now,
    }
