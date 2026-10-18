"""Render the dashboard from a synthetic Black Friday season (no network) and print a short outline.

    python3 -I .claude/skills/ux-review/render.py /tmp/bfa_ux.html [--today 2026-11-27]
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from bfa import analyze, report  # noqa: E402
from bfa.store import Store  # noqa: E402


def days(a, b):
    d, end = datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
    while d <= end:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


PRODUCTS = [  # (pid, title, category, normal price)
    ("1", 'Samsung 65" QLED Q80D', "tv", 10000), ("2", "LG OLED55C4", "tv", 14000),
    ("3", "iPhone 16 128GB", "mobil", 11000), ("4", "Sony WH-1000XM5", "hodetelefoner", 3500),
    ("5", "Roborock S8", "robotstovsuger", 6000), ("6", "Lenovo IdeaPad 5", "laptop", 8000),
]
SHOPS = ["Elkjøp", "Power", "Komplett", "NetOnNet", "Proshop"]


def offers(day, i, base):
    out = []
    for j, shop in enumerate(SHOPS):
        p = base * (1 + 0.02 * j)
        if (i + j) % 4 == 0:            # gouger: +20 % in late October, "sale" back on Black Friday week
            p = p * 1.2 if "2026-10-20" <= day < "2026-11-24" else p
            orig = round(p * 1.2) if day >= "2026-11-24" else None
        elif (i + j) % 5 == 1:          # honest deal
            orig = round(p) if day >= "2026-11-24" else None
            p = p * 0.85 if day >= "2026-11-24" else p
        else:
            orig = None
        out.append(dict(shop=shop, price=round(p), original_price=orig, in_stock=True))
    return out


def build(db, today):
    s = Store(db)
    for d in days("2026-08-01", today):
        for i, (pid, title, cat, base) in enumerate(PRODUCTS):
            s.save_product(dict(source="prisjakt", product_id=pid, title=title,
                                url="https://www.prisjakt.no/product.php?p=" + pid,
                                offers=offers(d, i, base), lowest_history=[], lowest_now=None), d, category=cat)
    return s


def outline(page):
    lines = ["size: {} KB".format(len(page.encode("utf-8")) // 1024)]
    for tag, txt in re.findall(r"<(h1|h2|h3|summary|button|label|select|input)[^>]*>(.*?)(?=<)", page, re.S):
        lines.append("{:8} {}".format(tag, re.sub(r"\s+", " ", txt).strip()[:90]))
    lines.append("table rows: {}".format(page.count("<tr")))
    lines.append("svg charts: {}".format(page.count("<svg")))
    return "\n".join(lines)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "bfa_ux.html")
    today = sys.argv[sys.argv.index("--today") + 1] if "--today" in sys.argv else "2026-11-27"
    tmp = tempfile.mkdtemp()
    s = build(os.path.join(tmp, "bfa.sqlite"), today)
    findings = analyze.analyze(s, today)
    shops = analyze.rank_shops(findings, s.shop_product_counts(today))
    page = report.html_page(findings, s.stats(), ["bfa_report.csv", "bfa_report.md"], False, shops, today=today)
    s.close()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote " + out)
    print(outline(page))


if __name__ == "__main__":
    main()
