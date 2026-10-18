"""Klarna (formerly Prisguide.no): category discovery + per-shop offers from JSON-LD."""
import json
import re

from . import net

BASE = "https://www.klarna.com"
NAME = "klarna"

# canonical category -> category path (ids verified 2026-10-18)
CATEGORIES = {
    "tv": "/no/shopping/cl/cl2/TV/",
    "mobil": "/no/shopping/cl/cl1/Mobiltelefoner/",
    "laptop": "/no/shopping/cl/cl27/Laptoper/",
    "hodetelefoner": "/no/shopping/cl/cl94/Headsets-og-oerepropper/",
    "skjerm": "/no/shopping/cl/cl25/PC-skjermer/",
    "smartklokke": "/no/shopping/cl/cl1438/Wearables/",
    "robotstovsuger": "/no/shopping/cl/cl1613/Robotstoevsugere/",
    "nettbrett": "/no/shopping/cl/cl224/Nettbrett/",
    "spillkonsoll": "/no/shopping/cl/cl52/Spillkonsoller/",
    # added 2026-10-18 (no Klarna match found for skjermkort, lopesko, jakke, varmeovn, parfyme)
    "kaffemaskin": "/no/shopping/cl/cl82/Kaffemaskiner/",
    "stovsuger": "/no/shopping/cl/cl19/Stoevsugere/",
    "soundbar": "/no/shopping/cl/cl86/Lydplanker-Hjemmekinopakker/",
    "hoyttaler": "/no/shopping/cl/cl267/Hoeyttalere/",
    "kamera": "/no/shopping/cl/cl29/Digitalkameraer/",
}

PRODUCT_RE = re.compile(r'(/no/shopping/pl/cl\d+/(\d+)/[^"\\?\s]*)')
_LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)


def category_url(path, page=1):
    return BASE + path + ("?page={}".format(page) if page > 1 else "")


def discover(path, limit, fetch=None):
    """(product_id, url) pairs from a category listing, following ?page= pages."""
    fetch = fetch or net.fetch
    found, page_no = {}, 1
    while len(found) < limit and page_no <= 10:
        page = fetch(category_url(path, page_no))
        before = len(found)
        for link, pid in PRODUCT_RE.findall(page):
            found.setdefault(pid, BASE + link)
        if len(found) == before:
            break
        page_no += 1
    return list(found.items())[:limit]


def parse_product(page, pid, url):
    product = None
    for m in _LDJSON_RE.finditer(page):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            product = d
            break

    by_shop = {}   # a shop can list several variants; keep its cheapest
    agg = (product or {}).get("offers") or {}
    # one shop: a single Offer dict; several shops: AggregateOffer with offers[]; sometimes a bare list
    if isinstance(agg, list):
        listed = agg
    elif isinstance(agg, dict) and agg.get("@type") == "Offer":
        listed = [agg]
    else:
        listed = agg.get("offers", []) if isinstance(agg, dict) else []
    for o in listed:
        try:
            price = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        shop = ((o.get("seller") or {}).get("name") or "").strip()
        if not shop or price <= 0:
            continue
        if shop not in by_shop or price < by_shop[shop]["price"]:
            by_shop[shop] = {
                "shop": shop,
                "price": price,
                "original_price": None,   # Klarna doesn't publish shop før-priser in JSON-LD
                "in_stock": str(o.get("availability", "")).endswith("InStock"),
            }
    offers = list(by_shop.values())

    lowest_now = min((o["price"] for o in offers), default=None)

    return {
        "source": NAME,
        "product_id": str(pid),
        "title": (product or {}).get("name") or url,
        "url": url,
        "offers": offers,
        "lowest_history": [],
        "lowest_now": lowest_now,
    }
