# prisjakt_agent.py
# A headless Playwright scraper that:
# 1) discovers products on prisjakt.no by searching category keywords, and/or
# 2) visits given product URLs,
# then extracts textual fields like "Laveste pris 3 mnd" (with date) and "Laveste pris nå",
# computes deltas, and outputs CSV + Markdown rankings.
#
# USAGE (quick start):
#   python prisjakt_agent.py --categories "TV" "Mobiltelefoner" --max-per-category 20
#   # OR: python prisjakt_agent.py --product-urls urls.txt
#
# Setup:
#   pip install -r requirements.txt
#   playwright install chromium
#
# Notes:
# - This script uses robust text scraping (regex over full page text) to handle DOM changes.
# - It rate-limits and retries to be polite and resilient.
# - Discovery via site search may be brittle if Prisjakt changes markup; you can also
#   supply a file of product URLs with --product-urls as a fallback.
#
# IMPORTANT: Respect the site's robots/terms. Use low concurrency and modest limits.

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import re
import csv
import argparse
from urllib.parse import quote_plus
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, asdict

# ---- Kategori-URLer (direkte listing) ----
CATEGORY_URLS = {
    "tv": "https://www.prisjakt.no/c/tv",
    "mobiltelefoner": "https://www.prisjakt.no/c/mobiltelefoner",
    "baerbarepcer": "https://www.prisjakt.no/c/baerbare-pc-er",
    "hodetelefoner": "https://www.prisjakt.no/c/hodetelefoner",
    "skjermer": "https://www.prisjakt.no/c/skjermer",
    "smartklokker": "https://www.prisjakt.no/c/smartklokker",
    "robotstovsugere": "https://www.prisjakt.no/c/robotstovsugere",
    "nettbrett": "https://www.prisjakt.no/c/nettbrett",
    "spillkonsoller": "https://www.prisjakt.no/c/spillkonsoller",
}

def norm_key(s: str) -> str:
    # grov normalisering (æ->ae, ø->o, å->a, fjerne mellomrom og spesialtegn)
    repl = (
        ("æ","ae"), ("ø","o"), ("å","a"),
        ("Æ","ae"), ("Ø","o"), ("Å","a"),
        (" ",""), ("-",""), ("/","")
    )
    out = s
    for a,b in repl:
        out = out.replace(a,b)
    return re.sub(r"[^a-z0-9]", "", out.lower())

NOR_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "des": 12
}

PRICE_RE = re.compile(r"[\d\s\.]+[,\.]?\d*")
# Flexible patterns to capture the 'Laveste pris 3 mnd' block + optional date
LAVESTE_3M_RE = re.compile(
    r"(?i)(laveste\s+pris\s*(?:siste\s*)?(?:3\s*mnd|90\s*dager))[^\n\r\d]*"
    r"([\d\s\.]+[,\.]?\d*)\s*[,\-]*\s*"
    r"(?:\(|\b)?"
    r"(?:(\d{1,2}[\.\s]?(?:jan|feb|mar|apr|mai|jun|jul|aug|sep|okt|nov|des)[a-z\.]*\s*\d{4})|"
    r"(\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}))?",
    re.UNICODE
)
# 'Laveste pris 30 dager' if present
LAVESTE_30D_RE = re.compile(
    r"(?i)(laveste\s+pris\s*(?:siste\s*)?(?:30\s*dager|1\s*mnd))[^\n\r\d]*"
    r"([\d\s\.]+[,\.]?\d*)"
)
# 'Laveste pris nå' | 'Den billigste prisen ... (nå)' | 'Nå'
NOW_RE = re.compile(
    r"(?i)(?:laveste\s+pris\s+nå|dagens\s+laveste\s+pris|den\s+billigste\s+prisen[^\n\r]*?\(\s*nå\s*\)|\bNå\b[^\n\r\d]*?)"
    r"([\d\s\.]+[,\.]?\d*)"
)

PRODUCT_URL_RE = re.compile(r"https?://www\.prisjakt\.no/product\.php\?p=\d+")

@dataclass
class ProductResult:
    product_url: str
    product_title: str
    min_3m_price: float | None
    min_3m_date: str | None
    now_price: float | None
    min_30_price: float | None
    delta_3m: float | None
    pct_3m: float | None
    delta_30d: float | None
    pct_30d: float | None
    suspicious: bool
    notes: str

def clean_price_to_float(txt: str) -> float | None:
    if not txt: 
        return None
    # keep digits, separators
    m = PRICE_RE.search(txt)
    if not m:
        return None
    raw = m.group(0)
    # remove spaces and dots as thousand sep; treat comma as decimal
    raw = raw.replace(" ", "").replace(".", "")
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None

def parse_nor_date(txt: str) -> str | None:
    if not txt:
        return None
    txt = txt.strip().lower().replace("aug.", "aug")  # normalize common dot
    # Pattern 1: 1 aug 2025 / 1. aug 2025 / 1 august 2025
    m = re.match(r"^(\d{1,2})[\.\s]*([a-zæøå\.]+)\s*(\d{4})$", txt)
    if m:
        day = int(m.group(1))
        mon_key = re.sub(r"[^a-zæøå]", "", m.group(2))[:3]
        mon = NOR_MONTHS.get(mon_key)
        year = int(m.group(3))
        if mon:
            try:
                return datetime(year, mon, day).strftime("%d.%m.%Y")
            except ValueError:
                return None
    # Pattern 2: dd.mm.yyyy or dd/mm/yyyy
    m2 = re.match(r"^(\d{1,2})[\./-](\d{1,2})[\./-](\d{2,4})$", txt)
    if m2:
        d, mth, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100: y += 2000
        try:
            return datetime(y, mth, d).strftime("%d.%m.%Y")
        except ValueError:
            return None
    return None

def smart_wait(page, secs=0.8):
    # polite delay
    time.sleep(secs)

def extract_text(page) -> str:
    # Try to prefer visible text; fall back to whole DOM text
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
        return body_text
    except Exception:
        return page.content()

def find_first(haystack: str, regex: re.Pattern):
    m = regex.search(haystack)
    return m

def compute_metrics(min_3m, now, min_30):
    delta_3m = pct_3m = delta_30d = pct_30d = None
    if min_3m is not None and now is not None:
        delta_3m = now - min_3m
        if min_3m > 0:
            pct_3m = delta_3m / min_3m * 100.0
    if min_30 is not None and now is not None:
        delta_30d = now - min_30
        if min_30 > 0:
            pct_30d = delta_30d / min_30 * 100.0
    return delta_3m, pct_3m, delta_30d, pct_30d

def is_suspicious(pct_3m, now, min_30):
    # Conservative heuristic:
    # - Flag if pct_3m >= 15%
    # - Also flag if now >> min_30 (>= 10%) when min_30 is available
    if pct_3m is not None and pct_3m >= 15.0:
        return True
    if min_30 is not None and now is not None:
        if min_30 > 0 and ((now - min_30) / min_30 * 100.0) >= 10.0:
            return True
    return False

def get_title(page):
    try:
        t = page.title()
        # trim suffixes
        return re.sub(r"\s*–\s*Prisjakt.*$", "", t).strip()
    except Exception:
        return ""

def accept_cookies(page):
    selectors = [
        "button:has-text('Godta')",
        "button:has-text('Aksepter alle')",
        "[data-testid='onetrust-accept-btn-handler']",
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept all')",
        "button:has-text('OK')"
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible():
                loc.click(timeout=1000)
                time.sleep(0.4)
                break
        except Exception:
            pass

def collect_product_links_from_search(page, keyword, max_links=30):
    search_urls = [
        f"https://www.prisjakt.no/search?q={quote_plus(keyword)}",
        f"https://www.prisjakt.no/?q={quote_plus(keyword)}",  # fallback
    ]
    product_links = set()

    for su in search_urls:
        try:
            page.goto(su, wait_until="load", timeout=30000)
            time.sleep(1.0)
            accept_cookies(page)
            # vent til ro
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # Scroll & samle i flere runder
            for _ in range(18):  # dypere scroll
                page.mouse.wheel(0, 1600)
                time.sleep(0.35)

                # 1) fra DOM
                try:
                    anchors = page.locator("a[href*='product.php?p=']")
                    hrefs = anchors.evaluate_all("(els) => els.map(e => e.getAttribute('href'))")
                except Exception:
                    hrefs = []

                for h in hrefs:
                    if not h:
                        continue
                    if h.startswith("/"):
                        h = "https://www.prisjakt.no" + h
                    if PRODUCT_URL_RE.match(h):
                        product_links.add(h)
                        if len(product_links) >= max_links:
                            return list(product_links)

                # 2) fra rå HTML (noen sider har data i innholdet uten klikkbare <a>)
                try:
                    html = page.content()
                except Exception:
                    html = ""
                for m in re.finditer(r"https?://www\.prisjakt\.no/product\.php\?p=\d+", html):
                    product_links.add(m.group(0))
                    if len(product_links) >= max_links:
                        return list(product_links)

                if len(product_links) >= max_links:
                    break

            if len(product_links) >= max_links:
                break

        except Exception:
            # prøv neste søk-URL
            continue

    return list(product_links)

def collect_product_links_from_category(page, category_name, max_links=30):
    key = norm_key(category_name)
    url = CATEGORY_URLS.get(key)
    if not url:
        return []  # ukjent kategori -> la caller bruke søke-fallback

    product_links = set()
    try:
        page.goto(url, wait_until="load", timeout=30000)
        time.sleep(1.0)
        accept_cookies(page)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # scroll dypt for å laste flere kort
        for _ in range(24):
            page.mouse.wheel(0, 1600)
            time.sleep(0.25)

            # 1) via DOM
            try:
                anchors = page.locator("a[href*='product.php?p=']")
                hrefs = anchors.evaluate_all("(els) => els.map(e => e.getAttribute('href'))")
            except Exception:
                hrefs = []
            for h in hrefs:
                if not h:
                    continue
                if h.startswith("/"):
                    h = "https://www.prisjakt.no" + h
                if PRODUCT_URL_RE.match(h):
                    product_links.add(h)
                    if len(product_links) >= max_links:
                        return list(product_links)

            # 2) via rå HTML (fallback)
            try:
                html = page.content()
            except Exception:
                html = ""
            for m in re.finditer(r"https?://www\\.prisjakt\\.no/product\\.php\\?p=\\d+", html):
                product_links.add(m.group(0))
                if len(product_links) >= max_links:
                    return list(product_links)

            if len(product_links) >= max_links:
                break

    except Exception:
        pass

    return list(product_links)

def extract_product(page, url) -> ProductResult:
    page.goto(url, wait_until="load", timeout=30000)
    time.sleep(1.0)
    accept_cookies(page)

    # Vent til siden er ferdig med å laste dynamisk innhold
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # Trigge lazy-load av pris- og tekstseksjoner
    page.mouse.wheel(0, 800)
    time.sleep(0.4)
    page.mouse.wheel(0, 1200)
    time.sleep(0.5)


    title = get_title(page)
    text = extract_text(page)

    # Find 'Laveste pris 3 mnd'
    m3 = find_first(text, LAVESTE_3M_RE)
    min_3m_val = None
    min_3m_date = None
    m3_notes = ""
    if m3:
        min_3m_val = clean_price_to_float(m3.group(2))
        # Try both date groups
        date_raw = m3.group(3) or m3.group(4)
        min_3m_date = parse_nor_date(date_raw) if date_raw else None
        m3_notes = f"Laveste 3 mnd: {m3.group(2)}"
        if date_raw:
            m3_notes += f" ({date_raw})"
    else:
        m3_notes = "Laveste 3 mnd: ikke funnet"

    # Find 'Laveste pris 30 dager' (optional)
    m30 = find_first(text, LAVESTE_30D_RE)
    min_30_val = None
    if m30:
        min_30_val = clean_price_to_float(m30.group(2))

    # Find 'Laveste pris nå' variants
    mn = find_first(text, NOW_RE)
    now_val = None
    if mn:
        now_val = clean_price_to_float(mn.group(1))

    d3, p3, d30, p30 = compute_metrics(min_3m_val, now_val, min_30_val)
    suspicious = is_suspicious(p3, now_val, min_30_val)

    notes_parts = []
    if m3_notes: notes_parts.append(m3_notes)
    if mn: notes_parts.append(f"Nå: {mn.group(1)}")
    if m30: notes_parts.append(f"Min30: {m30.group(2)}")
    notes = "; ".join(notes_parts) if notes_parts else "—"

    return ProductResult(
        product_url=url,
        product_title=title or url,
        min_3m_price=min_3m_val,
        min_3m_date=min_3m_date,
        now_price=now_val,
        min_30_price=min_30_val,
        delta_3m=d3, pct_3m=p3,
        delta_30d=d30, pct_30d=p30,
        suspicious=suspicious,
        notes=notes
    )

def fmt_money(x):
    return "" if x is None else f"{x:,.0f}".replace(",", " ").format(x)

def save_csv(path, rows: list[ProductResult]):
    fieldnames = [
        "Produkt", "URL", "Laveste 3 mnd (kr)", "Dato (3 mnd)",
        "Nå (kr)", "Δ3m (kr)", "%Δ3m", "Min30 (kr)", "Δ30d (kr)", "%Δ30d",
        "Mistenkelig", "Notater"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fieldnames)
        for r in rows:
            w.writerow([
                r.product_title, r.product_url,
                "" if r.min_3m_price is None else f"{r.min_3m_price:,.0f}".replace(",", " "),
                r.min_3m_date or "",
                "" if r.now_price is None else f"{r.now_price:,.0f}".replace(",", " "),
                "" if r.delta_3m is None else f"{r.delta_3m:,.0f}".replace(",", " "),
                "" if r.pct_3m is None else f"{r.pct_3m:.1f}%",
                "" if r.min_30_price is None else f"{r.min_30_price:,.0f}".replace(",", " "),
                "" if r.delta_30d is None else f"{r.delta_30d:,.0f}".replace(",", " "),
                "" if r.pct_30d is None else f"{r.pct_30d:.1f}%",
                "✅" if r.suspicious else "❌",
                r.notes
            ])

def save_markdown(path, rows: list[ProductResult], top_n=15):
    def md_money(x): return "—" if x is None else f"{int(round(x)):,}".replace(",", " ")
    lines = []
    lines.append("| Produkt | Laveste 3 mnd (kr) | Dato (3 mnd) | Nå (kr) | Δ3m (kr) | %Δ3m | Min30 (kr) | Δ30d (kr) | %Δ30d | Mistenkelig | Notater |")
    lines.append("|---|---:|:---:|---:|---:|---:|---:|---:|---:|:---:|---|")
    for r in rows:
        pct3 = "—" if r.pct_3m is None else f"{r.pct_3m:.1f}%"
        pct30 = "—" if r.pct_30d is None else f"{r.pct_30d:.1f}%"
        lines.append(
            f"| [{r.product_title}]({r.product_url}) | {md_money(r.min_3m_price)} | "
            f"{r.min_3m_date or '—'} | {md_money(r.now_price)} | "
            f"{md_money(r.delta_3m)} | {pct3} | {md_money(r.min_30_price)} | "
            f"{md_money(r.delta_30d)} | {pct30} | {'✅' if r.suspicious else '❌'} | {r.notes} |"
        )
    lines.append("\n")
    # Top lists
    by_abs = [r for r in rows if r.delta_3m is not None]
    by_abs.sort(key=lambda r: r.delta_3m, reverse=True)
    by_pct = [r for r in rows if r.pct_3m is not None]
    by_pct.sort(key=lambda r: r.pct_3m, reverse=True)

    lines.append("## Toppliste: Størst absolutt økning (3 mnd)")
    for i, r in enumerate(by_abs[:top_n], 1):
        lines.append(f"{i}. **[{r.product_title}]({r.product_url})** — +{md_money(r.delta_3m)} kr (**+{r.pct_3m:.1f}%**), nå: {md_money(r.now_price)} kr.")

    lines.append("\n## Toppliste: Størst prosentvis økning (3 mnd)")
    for i, r in enumerate(by_pct[:top_n], 1):
        lines.append(f"{i}. **[{r.product_title}]({r.product_url})** — +{md_money(r.delta_3m)} kr (**+{r.pct_3m:.1f}%**), nå: {md_money(r.now_price)} kr.")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    ap = argparse.ArgumentParser(description="Prisjakt price-change agent (Laveste pris 3 mnd / Nå).")
    ap.add_argument("--categories", nargs="*", default=["TV", "Mobiltelefoner", "Bærbare PC-er", "Hodetelefoner", "Robotstøvsugere", "Skjermer", "Smartklokker"])
    ap.add_argument("--max-per-category", type=int, default=20)
    ap.add_argument("--product-urls", type=str, help="Optional path to a text file with product URLs (one per line).")
    ap.add_argument("--min-price-nok", type=int, default=500)
    ap.add_argument("--out-prefix", type=str, default="prisjakt_output")
    args = ap.parse_args()

    all_urls = set()
    if args.product_urls:
        with open(args.product_urls, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if PRODUCT_URL_RE.match(u):
                    all_urls.add(u)

    print("[i] Starting Playwright…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
    locale="nb-NO",
    timezone_id="Europe/Oslo",
    viewport={"width": 1366, "height": 900},
    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36")
)
        page = context.new_page()

# Discover by categories: prøv kategoriside først, så søk
for cat in args.categories:
    if len(all_urls) >= args.max_per_category * max(1, len(args.categories)):
        break
    links = []
    # 1) kategoriside
    try:
        links = collect_product_links_from_category(page, cat, max_links=args.max_per_category)
        if links:
            print(f"[+] {cat} (category page): found {len(links)} product links")
    except Exception as e:
        print(f"[warn] category discovery failed for {cat}: {e}")

    # 2) søk som fallback
    if not links:
        try:
            links = collect_product_links_from_search(page, cat, max_links=args.max_per_category)
            print(f"[+] {cat} (search): found {len(links)} product links")
        except Exception as e:
            print(f"[warn] search discovery failed for {cat}: {e}")

    for l in links:
        all_urls.add(l)


        # Deduplicate and limit
        urls = list(all_urls)[: args.max_per_category * max(1, len(args.categories))]
        print(f"[i] Total candidate product URLs: {len(urls)}")
        results = []

        for i, url in enumerate(urls, 1):
            try:
                print(f"    ({i}/{len(urls)}) Scraping: {url}")
                r = extract_product(page, url)
                # Filter by min price
                if r.now_price is not None and r.now_price < args.min_price_nok:
                    r.notes += "; filtrert bort (< min pris)"
                results.append(r)
            except Exception as e:
                print(f"[warn] failed {url}: {e}")

        browser.close()

    # Sort results for the main table by suspicious first then by abs delta desc
    results_sorted = sorted(results, key=lambda r: ((not r.suspicious), -(r.delta_3m or -1e9)))
    out_csv = args.out_prefix + ".csv"
    out_md = args.out_prefix + ".md"
    save_csv(out_csv, results_sorted)
    save_markdown(out_md, results_sorted, top_n=20)
    print(f"[✓] Wrote: {out_csv} and {out_md}")

if __name__ == "__main__":
    main()
