"""Command line: collect prices, analyze, write reports."""
import argparse
import os
import re
import sys

from . import analyze, klarna, net, prisjakt, report
from .store import Store

SOURCES = {"prisjakt": prisjakt, "klarna": klarna}
DEFAULT_CATEGORIES = ["tv", "mobil", "laptop", "hodetelefoner", "skjerm", "smartklokke", "robotstovsuger",
                      "nettbrett", "spillkonsoll", "kaffemaskin", "stovsuger", "soundbar", "hoyttaler",
                      "kamera", "skjermkort", "lopesko", "jakke", "varmeovn", "parfyme"]

ALIASES = {
    "tv": "tv", "tver": "tv",
    "mobil": "mobil", "mobiltelefon": "mobil", "mobiltelefoner": "mobil",
    "laptop": "laptop", "laptoper": "laptop", "baerbarepcer": "laptop", "baerbarpc": "laptop",
    "baerbaredatamaskiner": "laptop",
    "hodetelefoner": "hodetelefoner", "headset": "hodetelefoner",
    "skjerm": "skjerm", "skjermer": "skjerm", "pcskjermer": "skjerm",
    "smartklokke": "smartklokke", "smartklokker": "smartklokke",
    "robotstovsuger": "robotstovsuger", "robotstovsugere": "robotstovsuger",
    "nettbrett": "nettbrett",
    "spillkonsoll": "spillkonsoll", "spillkonsoller": "spillkonsoll",
    "kaffemaskin": "kaffemaskin", "kaffemaskiner": "kaffemaskin", "espressomaskin": "kaffemaskin",
    "espressomaskiner": "kaffemaskin",
    "stovsuger": "stovsuger", "stovsugere": "stovsuger",
    "soundbar": "soundbar", "lydplanke": "soundbar", "lydplanker": "soundbar",
    "hoyttaler": "hoyttaler", "hoyttalere": "hoyttaler", "mobilhoyttalere": "hoyttaler",
    "kamera": "kamera", "kameraer": "kamera", "digitalkameraer": "kamera",
    "skjermkort": "skjermkort",
    "lopesko": "lopesko", "joggesko": "lopesko",
    "jakke": "jakke", "jakker": "jakke",
    "varmeovn": "varmeovn", "varmeovner": "varmeovn",
    "parfyme": "parfyme", "parfymer": "parfyme",
}


def norm_key(s):
    s = s.lower()
    for a, b in (("æ", "ae"), ("ø", "o"), ("å", "a")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s)


def resolve_categories(names):
    keys, unknown = [], []
    for n in names:
        k = ALIASES.get(norm_key(n))
        if k:
            if k not in keys:
                keys.append(k)
        else:
            unknown.append(n)
    return keys, unknown


def log(msg):
    print(msg, flush=True)


def collect(store, sources, categories, per_category, max_products, today, product_urls=()):
    total = 0
    for name in sources:
        mod = SOURCES[name]
        queue = {}  # product_id -> (url, category)
        for pid, url, cat in store.tracked(name, today=today):
            queue[pid] = (url, cat)
        for u in product_urls:
            m = mod.PRODUCT_RE.search(u) if mod.BASE.split("//")[1] in u else None
            if m and name == "prisjakt":
                queue.setdefault(m.group(1), (prisjakt.product_url(m.group(1)), None))
            elif m:
                queue.setdefault(m.group(2), (klarna.BASE + m.group(1), None))
        for cat in categories:
            target = mod.CATEGORIES.get(cat)
            if not target:
                continue
            try:
                found = mod.discover(target, per_category)
            except net.Blocked as e:
                log("[stop] {}: {}".format(name, e))
                break
            except Exception as e:
                log("[warn] {} {}: discovery failed: {}".format(name, cat, e))
                continue
            for item in found:
                pid, url = (item, prisjakt.product_url(item)) if name == "prisjakt" else item
                queue.setdefault(pid, (url, cat))
            log("[+] {} {}: {} produkter".format(name, cat, len(found)))

        items = list(queue.items())[:max_products]
        log("[i] {}: henter {} produkter".format(name, len(items)))
        for i, (pid, (url, cat)) in enumerate(items, 1):
            try:
                page = net.fetch(url)
                p = mod.parse_product(page, pid) if name == "prisjakt" else mod.parse_product(page, pid, url)
                del page   # keep peak memory at one page on the Pi
                store.save_product(p, today, category=cat,
                                   lowest_shop=prisjakt.LOWEST if name == "prisjakt" else None)
                total += 1
                log("    ({}/{}) {} – {} tilbud".format(i, len(items), p["title"][:60], len(p["offers"])))
            except net.Blocked as e:
                log("[stop] {}: {}".format(name, e))
                break
            except Exception as e:
                log("[warn] {}: {}".format(url, e))
    return total


def write_reports(store, today, out_prefix, top, min_price=0, baseline_end="auto"):
    findings = [f for f in analyze.analyze(store, today, baseline_end) if (f.now or 0) >= min_price]
    shops = analyze.rank_shops(findings, store.shop_product_counts(today))
    stats = store.stats()
    report.save_csv(out_prefix + ".csv", findings)
    report.save_markdown(out_prefix + ".md", findings, stats, shops, top=top)
    return findings, stats, shops


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bfa", description="Black Friday price-gouging tracker (Prisjakt + Klarna).")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "collect", "report"],
                    help="run = collect + report (default)")
    ap.add_argument("--categories", nargs="*", action="append",
                    help="kategorier, f.eks. TV Mobiltelefoner 'Bærbare PC-er' (can be repeated)")
    ap.add_argument("--sources", nargs="*", default=list(SOURCES), choices=list(SOURCES))
    ap.add_argument("--max-per-category", type=int, default=8)
    ap.add_argument("--max-products", type=int, default=200, help="per source, per run")
    ap.add_argument("--product-urls", help="fil med produkt-URL-er (Prisjakt eller Klarna), én per linje")
    ap.add_argument("--min-price-nok", type=int, default=500, help="ignore findings where the price now is lower than this")
    ap.add_argument("--db", default=None, help="SQLite file (default $OUTPUT_DIR/bfa.sqlite)")
    ap.add_argument("--out-prefix", default=None, help="default $OUTPUT_DIR/bfa_report")
    ap.add_argument("--today", default=None, help="analysis day YYYY-MM-DD (default: today in Oslo)")
    ap.add_argument("--baseline-end", default="auto",
                    help="auto (Oct 1 in Black Friday season, else rolling), 'rolling', or YYYY-MM-DD")
    ap.add_argument("--delay", type=float, default=net.DELAY_S, help="seconds between requests per site")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--format", choices=["text", "none"], default="text", help="print a summary after report")
    ap.add_argument("--product", help="show the per-shop series of one product: source:id")
    a = ap.parse_args(argv)

    net.DELAY_S = max(2.0, a.delay)
    today = a.today or net.today_oslo()
    out_dir = os.environ.get("OUTPUT_DIR", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_prefix = a.out_prefix or os.path.join(out_dir, "bfa_report")
    store = Store(a.db)

    if a.product:
        source, _, pid = a.product.partition(":")
        for s, p, shop, title, url, _ in store.series_keys():
            if s == source and p == pid:
                rows = store.series(s, p, shop)
                print("{} | {}: {}".format(title[:60], shop, " ".join(
                    "{}={}".format(d[5:], report.kr(pr)) for d, pr, _, _ in rows[-40:])))
        return 0

    if a.command in ("run", "collect"):
        names = [c for group in (a.categories or [DEFAULT_CATEGORIES]) for c in group]
        cats, unknown = resolve_categories(names)
        if unknown:
            log("[warn] ukjente kategorier (ignorert): {}. Kjente: {}".format(
                ", ".join(unknown), ", ".join(sorted(set(ALIASES.values())))))
        urls = []
        if a.product_urls:
            with open(a.product_urls, encoding="utf-8") as fh:
                urls = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
        n = collect(store, a.sources, cats, a.max_per_category, a.max_products, today, urls)
        log("[✓] lagret {} produkter for {}".format(n, today))

    if a.command in ("run", "report"):
        findings, stats, shops = write_reports(store, today, out_prefix, a.top, a.min_price_nok, a.baseline_end)
        log("[✓] skrev {0}.csv og {0}.md".format(out_prefix))
        if a.format == "text":
            print(report.text(findings, stats, shops, top=min(a.top, 15)))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
