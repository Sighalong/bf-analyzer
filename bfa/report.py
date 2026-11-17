"""Reports: CSV, Markdown, compact text (for terminals/agents) and a small HTML page."""
import csv
import html
import re
from datetime import datetime

from . import analyze, net



def _p(x):
    return "{:.0f} %".format(x * 100)


class Type(object):
    """One kind of finding: a single name used everywhere, what it means, and how it is measured."""
    __slots__ = ("rule", "name", "one", "many", "tone", "badge", "means", "measured", "levels")

    def __init__(self, *args):
        for k, v in zip(self.__slots__, args):
            setattr(self, k, v)

    def count(self, n):
        return "{} {}".format(n, self.one if n == 1 else self.many)


# The definitions follow the Norwegian price-indication rule (prisopplysningsforskriften § 9a: a
# "før-pris" must be the lowest price the shop used in the 30 days before the reduction) and the
# "lurepris" pattern it targets: raise the price first, then "discount" it back. Normal price =
# median price in the baseline window (see analyze.py). Order = importance.
TYPES = [
    Type("fake_discount", "Lurepris", "lurepris", "lurepriser", "bad", "hevet før «tilbudet»",
         "Prisen ble satt opp, og «tilbudet» tar den bare tilbake til omtrent vanlig pris. Du sparer ingenting.",
         "Minst {} over normalpris i minst {} dager, så satt ned minst {} fra toppen, men ikke mer enn {} "
         "under normalpris.".format(_p(analyze.HIKE), analyze.MIN_RAISED_DAYS, _p(analyze.DROP), _p(analyze.DEAL)),
         (0.20, 0.40)),
    Type("before_price", "Oppblåst før-pris", "oppblåst før-pris", "oppblåste før-priser", "bad",
         "over laveste 30 d",
         "Butikken oppgir en før-pris som er høyere enn det den selv tok de siste 30 dagene. Det er ikke lov: "
         "før-prisen skal være den laveste prisen siste 30 dager.",
         "Oppgitt før-pris er mer enn 1 % over butikkens laveste pris de 30 dagene før prisen ble satt ned "
         "(prisopplysningsforskriften § 9a).",
         (0.10, 0.25)),
    Type("hike", "Satt opp i pris", "satt opp i pris", "satt opp i pris", "warn", "over normalpris",
         "Prisen ligger klart over det varen vanligvis kostet. Ikke ulovlig, men slik starter en lurepris: "
         "følg med om den «settes ned» til Black Friday.",
         "Nå minst {} over normalpris, og hevet i minst {} dager.".format(_p(analyze.HIKE), analyze.MIN_RAISED_DAYS),
         (0.20, 0.40)),
    Type("deal", "Ekte prisfall", "ekte prisfall", "ekte prisfall", "good", "under vanlig lavpris",
         "Prisen er lavere enn den har vært, også lavere enn de vanlige lavprisene. Et reelt godt kjøp.",
         "Nå minst {} under det vanlige lavprisnivået (prisen de {} billigste dagene i normalperioden)."
         .format(_p(analyze.DEAL), _p(analyze.LOW_QUANTILE)),
         (0.10, 0.20)),
]
TYPE = {t.rule: t for t in TYPES}
RULES = [(t.rule, t.name) for t in TYPES]
LABEL = dict(RULES)
# Grad: how big the finding is within its type, from its score (levels above = where Tydelig and Kraftig start).
# Score: hike = now over normal, fake_discount = how far it was raised, before_price = claimed over the
# 30-day low, deal = below the usual low.
LEVEL_NAMES = ("Litt", "Tydelig", "Kraftig")


def level(f):
    """1 (Litt), 2 (Tydelig) or 3 (Kraftig)."""
    lo, hi = TYPE[f.rule].levels
    score = round(f.score or 0, 6)   # 1400 / 1000 - 1 is 0.3999…: a shown "+40 %" must count as 40 %
    return 3 if score >= hi else 2 if score >= lo else 1


def level_text(t):
    """'Tydelig fra 20 %, kraftig fra 40 %.'"""
    return "Grad: tydelig fra {}, kraftig fra {}.".format(_p(t.levels[0]), _p(t.levels[1]))
SHOPS_TITLE = "Butikker med flest mistenkelige produkter"


def kr(x):
    return "" if x is None else "{:,.0f}".format(x).replace(",", " ")


def pct(x):
    return "" if x is None else "{:+.0f} %".format(x * 100)


def change(f):
    """Price change in the direction a reader expects: deals are negative."""
    return pct(-f.score if f.rule == "deal" else f.score)


def _what(f):
    if f.rule == "before_price":
        return "før-pris {} kr, laveste 30 d {} kr, nå {} kr".format(kr(f.claimed), kr(f.low30), kr(f.now))
    return "normal {} ({}) → topp {} ({}) → nå {} kr".format(
        kr(f.base_median), f.baseline, kr(f.peak), f.peak_day, kr(f.now))


def _shop(f):
    return "laveste hos alle butikker" if f.shop.startswith("(") else f.shop


def _shop_parts(r):
    return ", ".join(TYPE[k].count(r[k]) for k in ("fake_discount", "before_price", "hike") if r[k])


def _shop_line(r):
    return "{} av {} produkter ({:.0f} %): {}".format(r["flagged"], r["listed"], r["share"] * 100, _shop_parts(r))


def text(findings, stats, shops=(), top=15):
    lines = ["Data: {products} produkter, {first_day} → {last_day}".format(**stats)]
    for t in TYPES:
        rows = [f for f in findings if f.rule == t.rule]
        lines.append("")
        lines.append("== {} ({}) == {}".format(t.name, len(rows), t.measured))
        for f in rows[:top]:
            lines.append("{:>5} {:<7} {} | {} [{}] | {} | {} d".format(
                change(f), LEVEL_NAMES[level(f) - 1], f.title[:60], _shop(f), f.source, _what(f), f.days))
    lines += ["", "== {} ({}) ==".format(SHOPS_TITLE, len(shops))]
    for r in list(shops)[:top]:
        lines.append("{} [{}] | {}".format(r["shop"], r["source"], _shop_line(r)))
    return "\n".join(lines)


def save_csv(path, findings):
    cols = ["rule", "source", "title", "shop", "base_median", "base_low", "baseline", "peak", "peak_day",
            "now", "claimed", "low30", "score", "grad", "days", "category", "url"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in findings:
            d = f.as_dict()
            d["score"] = None if f.score is None else round(f.score * 100, 1)
            d["grad"] = LEVEL_NAMES[level(f) - 1]
            w.writerow(["" if d[c] is None else d[c] for c in cols])


def save_markdown(path, findings, stats, shops=(), top=25, groups=None):
    """groups: analyze.rank_products(...) output, adds the top 5 lists."""
    lines = ["# Black Friday-analyse", "",
             "Data: {products} produkter, {first_day} → {last_day}.".format(**stats), ""]
    for key, head in (("trick", "Topp 5 lureri"), ("buy", "Topp 5 beste kjøp")):
        if groups is None:
            break
        lines += ["## " + head, ""]
        rows = analyze.top(groups, key)
        lines += ["{}. [{}]({}) – {}".format(i, g["title"].replace("|", "/"), g["url"], _top_line(g, key))
                  for i, g in enumerate(rows, 1)] or ["_Ingen ennå._"]
        lines.append("")
    if groups is not None:
        lines += ["_" + TOP_WHY + "_", ""]
    for t in TYPES:
        rows = [f for f in findings if f.rule == t.rule][:top]
        lines += ["## " + t.name, "", t.means + " _Kriterium: " + t.measured + " " + level_text(t) + "_", ""]
        if not rows:
            lines += ["_Ingen funn._", ""]
            continue
        lines += ["| Produkt | Butikk | Kilde | Endring | Grad | Detaljer | Dager |", "|---|---|---|---:|---|---|---:|"]
        for f in rows:
            lines.append("| [{}]({}) | {} | {} | {} | {} | {} | {} |".format(
                f.title.replace("|", "/"), f.url, shop_name(f), f.source, change(f), LEVEL_NAMES[level(f) - 1],
                story(f), f.days))
        lines.append("")
    lines += ["## " + SHOPS_TITLE, ""]
    if shops:
        lines += ["| Butikk | Kilde | Flagget |", "|---|---|---|"]
        lines += ["| {} | {} | {} |".format(r["shop"], r["source"], _shop_line(r)) for r in list(shops)[:top]]
    else:
        lines.append("_Ingen funn._")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---- evidence chart -------------------------------------------------------

def _d(s):
    return datetime.strptime(s, "%Y-%m-%d")


def baseline_window(label, last_day):
    """(first, last) day of the normal-price window from a finding's baseline label, or None."""
    from datetime import timedelta
    if not label:
        return None
    if label.startswith("før "):
        end = _d(label[4:])
        return end - timedelta(days=60), end - timedelta(days=1)
    if label.startswith("tidlig ") and "–" in label:
        a, b = label[7:].split("–")
        y = last_day[:4]
        return _d(y + "-" + a), _d(y + "-" + b)
    if label.startswith("dag 31"):
        t = _d(last_day)
        return t - timedelta(days=90), t - timedelta(days=31)
    return None


def chart_svg(f, w=320, h=132, bf=None):
    """Inline SVG of the shop's price series with date and price axes.

    Shaded band = the days the normal price is measured from, dashed line = normal price,
    dots = highest price and today's price, red line = Black Friday when it is inside the range.
    """
    pts = f.series or []
    if len(pts) < 2:
        return ""
    t0, t1 = _d(pts[0][0]), _d(pts[-1][0])
    end = t1                                     # before Black Friday: run the axis up to it
    if bf and 0 < (_d(bf) - t1).days <= 60:
        end = _d(bf)
    days = float(max((end - t0).days, 1))
    refs = [v for v in (f.base_median, f.claimed) if v]
    lo = min([p for _, p in pts] + refs)
    hi = max([p for _, p in pts] + refs)
    if hi - lo < hi * 0.02:                      # flat series: give it some room
        lo, hi = lo * 0.98, hi * 1.02
    L, R, T, B = 64, 10, 18, 20                 # room for price labels left, peak label top, dates below

    def X(day):
        return L + (w - L - R) * (_d(day) - t0).days / days

    def Y(p):
        return T + (h - T - B) * (1 - (p - lo) / float(hi - lo))

    def label(x, y, txt, cls="lb", anchor="middle"):
        if anchor == "middle":                  # keep centred labels inside the chart
            half = len(txt) * 2.9
            x = max(L + half, min(w - R - half, x))
        return '<text class="{}" x="{:.1f}" y="{:.1f}" text-anchor="{}">{}</text>'.format(
            cls, x, y, anchor, html.escape(txt))

    out = ['<svg class="ch" viewBox="0 0 {0} {1}" width="{0}" height="{1}" role="img" aria-label="{2}">'.format(
        w, h, html.escape("Pris {} til {}: {}nå {} kr".format(
            nice_day(pts[0][0]), nice_day(pts[-1][0]),
            "normal {} kr, høyeste {} kr {}, ".format(kr(f.base_median), kr(f.peak), nice_day(f.peak_day))
            if f.base_median and f.peak else "", kr(pts[-1][1]))))]
    # normal-price window
    win = baseline_window(f.baseline, pts[-1][0])
    if win:
        a, b = max(win[0], t0), min(win[1], t1)
        if a < b:
            xa, xb = X(a.strftime("%Y-%m-%d")), X(b.strftime("%Y-%m-%d"))
            out.append('<rect class="base" x="{:.1f}" y="{}" width="{:.1f}" height="{}"><title>normalpris målt '
                       '{}–{}</title></rect>'.format(xa, T, xb - xa, h - T - B, nice_day(a.strftime("%Y-%m-%d")),
                                                    nice_day(b.strftime("%Y-%m-%d"))))
            if xb - xa > 60:
                out.append(label((xa + xb) / 2, T + 11, "normalpris målt", "lb soft"))
    # price axis: top and bottom of the range
    for v in (hi, lo):
        out.append('<line class="grid" x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}"/>'.format(L, w - R, Y(v), Y(v)))
        out.append(label(L - 4, Y(v) + 4, kr(v) + " kr", anchor="end"))
    # date axis: today (and Black Friday when the axis runs to it) always, month starts where they fit
    xt = X(pts[-1][0])
    if end > t1:
        out.append('<rect class="fut" x="{:.1f}" y="{}" width="{:.1f}" height="{}"><title>fram til Black Friday'
                   '</title></rect>'.format(xt, T, w - R - xt, h - T - B))
        dates = [(xt, nice_day(pts[-1][0]), "lb strong", "middle"), (w - R, nice_day(bf), "lb bft", "end")]
    else:
        dates = [(w - R, nice_day(pts[-1][0]), "lb strong", "end")]
    m = datetime(t0.year, t0.month, 1)
    while m <= end:
        if t0 < m < end:
            day = m.strftime("%Y-%m-%d")
            out.append('<line class="grid" x1="{0:.1f}" x2="{0:.1f}" y1="{1}" y2="{2}"/>'.format(X(day), T, h - B))
            dates.append((X(day), nice_day(day), "lb", "middle"))
        m = datetime(m.year + (m.month == 12), m.month % 12 + 1, 1)
    taken = []
    for x, txt, cls, anchor in dates:
        half = len(txt) * 3.0 + 2            # 11px text is about 6px per character
        lo_x, hi_x = (x - 2 * half, x) if anchor == "end" else (x - half, x + half)
        if all(hi_x < a or lo_x > b for a, b in taken):
            taken.append((lo_x, hi_x))
            out.append(label(x, h - 5, txt, cls, anchor))
    if bf and t0 <= _d(bf) <= end:
        out.append('<line class="bfl" x1="{0:.1f}" x2="{0:.1f}" y1="{1}" y2="{2}"><title>Black Friday</title>'
                   '</line>'.format(X(bf), T, h - B))
        out.append(label(X(bf), T - 6, "BF", "lb bft"))
    if f.base_median:
        y = Y(f.base_median)
        out.append('<line class="ref" x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}"><title>normal {} kr</title></line>'.format(
            L, w - R, y, y, kr(f.base_median)))
        out.append(label(L - 4, y + 4, kr(f.base_median) + " kr", "lb refl", "end")
                   if abs(y - Y(hi)) > 12 and abs(y - Y(lo)) > 12 else "")
    if f.claimed:
        y = Y(f.claimed)
        out.append('<line class="claim" x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}"><title>før-pris {} kr</title></line>'.format(
            L, w - R, y, y, kr(f.claimed)))
        out.append(label(w - R, y - 4, "oppgitt før-pris " + kr(f.claimed) + " kr", "lb claiml", "end"))
    mk = [(d, p) for d, p in (getattr(f, "market", None) or []) if lo <= p <= hi]
    if len(mk) > 1 and not f.shop.startswith("("):   # median of all shops: is this shop out of line?
        out.append('<path class="mk" d="{}"><title>median alle butikker</title></path>'.format(
            " ".join("{}{:.1f},{:.1f}".format("M" if i == 0 else "L", X(d), Y(p)) for i, (d, p) in enumerate(mk))))
    path = "M{:.1f},{:.1f}".format(X(pts[0][0]), Y(pts[0][1]))
    for d, p in pts[1:]:                         # step line: a price holds until the next observation
        path += " H{:.1f} V{:.1f}".format(X(d), Y(p))
    out.append('<path class="ln" d="{}"/>'.format(path))
    if f.peak and f.peak_day and f.peak_day >= pts[0][0]:
        x, y = X(f.peak_day), Y(f.peak)
        out.append('<circle class="pk" cx="{:.1f}" cy="{:.1f}" r="3.5"><title>høyeste {} kr</title></circle>'.format(
            x, y, kr(f.peak)))
        out.append(label(x, y - 7, "{} kr {}".format(kr(f.peak), nice_day(f.peak_day)), "lb pkl"))
    x, y = X(pts[-1][0]), Y(pts[-1][1])
    out.append('<circle class="now" cx="{:.1f}" cy="{:.1f}" r="3.5"><title>nå {} kr</title></circle>'.format(
        x, y, kr(pts[-1][1])))
    if not (f.peak and abs(f.peak - pts[-1][1]) < 1):
        out.append(label(x - 6, y + (14 if y < h - B - 14 else -8), "nå " + kr(pts[-1][1]) + " kr", "lb nowl", "end"))
    out.append("</svg>")
    return "".join(out)


# ---- dashboard -------------------------------------------------------------

WARMUP_ORDER = ("hike", "fake_discount", "before_price", "deal")   # before Black Week a raised price
                                                                    # is the warning sign


def sections(phase):
    """Finding types in the order the dashboard shows them."""
    if phase == "warmup":
        return [TYPE[r] for r in WARMUP_ORDER]
    return list(TYPES)


CATEGORY_NAMES = {
    "tv": "TV", "mobil": "Mobil", "laptop": "Bærbar PC", "hodetelefoner": "Hodetelefoner",
    "skjerm": "PC-skjerm", "smartklokke": "Smartklokke", "robotstovsuger": "Robotstøvsuger",
    "nettbrett": "Nettbrett", "spillkonsoll": "Spillkonsoll", "kaffemaskin": "Kaffemaskin",
    "stovsuger": "Støvsuger", "soundbar": "Soundbar", "hoyttaler": "Høyttaler", "kamera": "Kamera",
    "skjermkort": "Skjermkort", "lopesko": "Løpesko", "jakke": "Jakke", "varmeovn": "Varmeovn",
    "parfyme": "Parfyme",
}
MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]


def nice_day(d):
    """'2026-11-05' -> '5. nov'"""
    if not d or len(d) < 10:
        return d or ""
    return "{}. {}".format(int(d[8:10]), MONTHS[int(d[5:7]) - 1])


def category_name(key):
    return CATEGORY_NAMES.get(key or "", (key or "Uten kategori").capitalize())


def nice_baseline(label):
    """Readable form of Finding.baseline."""
    label = label or ""
    if label.startswith("før "):
        return "før " + nice_day(label[4:])
    if label.startswith("tidlig "):
        a, _, b = label[7:].partition("–")
        return "kun {}–{} (lite data)".format(nice_day("0000-" + a), nice_day("0000-" + b))
    if label == "dag 31–90":
        return "1–3 måneder siden"
    if label == "30 dager":
        return "siste 30 dager"
    return label


def bf_rel(day, bf):
    """'(44 dager før Black Friday)' for a day in the season, '' otherwise."""
    if not day or not bf:
        return ""
    n = (analyze._day(bf) - analyze._day(day)).days
    if n == 0:
        return " (på Black Friday)"
    if abs(n) > 75:
        return ""
    return " ({} {} {} Black Friday)".format(abs(n), "dag" if abs(n) == 1 else "dager", "før" if n > 0 else "etter")


def story(f, bf=None):
    """One plain sentence explaining the finding. bf: Black Friday date, adds how far the hike was from it."""
    if f.rule == "before_price":
        return "Oppgir før-pris {} kr, men laveste pris de 30 dagene før var {} kr. Nå {} kr.".format(
            kr(f.claimed), kr(f.low30), kr(f.now))
    if f.rule == "deal":
        return "Var vanligvis rundt {} kr (laveste {} kr). Nå {} kr.".format(
            kr(f.base_median), kr(f.base_low), kr(f.now))
    now = "nå «tilbud» {} kr".format(kr(f.now)) if f.rule == "fake_discount" else "nå {} kr".format(kr(f.now))
    return "Var vanligvis {} kr. Hevet til {} kr {}{}, {}.".format(
        kr(f.base_median), kr(f.peak), nice_day(f.peak_day), bf_rel(f.peak_day, bf), now)


def shop_name(f):
    return "Laveste pris blant butikkene" if f.shop.startswith("(") else f.shop


def _nb(txt):
    """Keep amounts like '10 000 kr' and '+20 %' on one line in the HTML page."""
    return re.sub(r"(?<=\d) (?=\d{3}\b|kr\b|%)", "\u00a0", txt)


def _prices(f):
    """Key prices on one line: what it usually cost, where it went, what it costs now."""
    if f.rule == "before_price":
        txt = "Før-pris {} · laveste 30 d {} → nå <b>{} kr</b>".format(kr(f.claimed), kr(f.low30), kr(f.now))
    elif f.rule == "deal":
        txt = "Normal {} · lavpris {} → nå <b>{} kr</b>".format(kr(f.base_median), kr(f.base_low), kr(f.now))
    else:
        txt = "Normal {} → topp {} → nå <b>{} kr</b>".format(kr(f.base_median), kr(f.peak), kr(f.now))
    return _nb(txt)


def amount(f):
    """The finding in kroner, for sorting: how much the price was pushed up (or how much a deal saves)."""
    if f.rule == "before_price":
        a = (f.claimed or 0) - (f.low30 or 0)
    elif f.rule == "deal":
        a = (f.base_low or 0) - (f.now or 0)
    elif f.rule == "fake_discount":
        a = (f.peak or 0) - (f.base_median or 0)
    else:
        a = (f.now or 0) - (f.base_median or 0)
    return max(0, int(round(a)))


def flag_key(f):
    return (f.rule, f.source, f.product_id, f.shop)


def first_day(f, flags, day):
    """Day this finding was first flagged without a break (store.flag_info); not recorded yet = `day`."""
    return ((flags or {}).get("first") or {}).get(flag_key(f), day)


def is_new(f, flags, day):
    """Flagged for the first time in the report for `day` (never on the day tracking started)."""
    since = (flags or {}).get("since")
    first = first_day(f, flags, day)
    return bool(since) and first == day and first > since


def _when(f, bf=None):
    """What the card adds to its price line: when the price was raised (the full story is the line's tooltip)."""
    if f.rule in ("hike", "fake_discount") and f.peak_day:
        return "Satt opp {}{}.".format(nice_day(f.peak_day), bf_rel(f.peak_day, bf))
    return ""


TONE_ORDER = {"bad": 0, "warn": 1, "good": 2}
TRICK_RULES = ("fake_discount", "before_price", "hike")


def _tone(g):
    return min((TYPE[f.rule].tone for f in g["findings"]), key=TONE_ORDER.get)


def product_id_attr(g):
    return "p-{}-{}".format(g["source"], re.sub(r"[^A-Za-z0-9_-]", "", str(g["product_id"])))


def _window_text(f):
    win = baseline_window(f.baseline, (f.series or [[""]])[-1][0]) if f.series else None
    if win:
        return "{}–{}".format(nice_day(win[0].strftime("%Y-%m-%d")), nice_day(win[1].strftime("%Y-%m-%d")))
    return nice_baseline(f.baseline)


def basis(f):
    """What the finding is measured against, in plain words, so it can be checked by hand."""
    who = "laveste pris blant butikkene" if f.shop.startswith("(") else f.shop
    if f.rule == "before_price":
        return ("Butikken oppgir før-pris {} kr. Laveste pris de 30 dagene før prisen ble satt ned var {} kr "
                "({} dager med priser). Nå {} kr.").format(kr(f.claimed), kr(f.low30), f.days, kr(f.now))
    out = "Normalpris {} kr = median for {} {}. Bygger på {} dager med priser.".format(
        kr(f.base_median), who, _window_text(f), f.days)
    if f.rule == "deal":
        out += " Vanlig lavpris {} kr.".format(kr(f.base_low))
    if f.peak and f.rule != "deal":
        out += " Høyeste {} kr {}.".format(kr(f.peak), nice_day(f.peak_day))
    return out + " Nå {} kr.".format(kr(f.now))


def price_steps(series, n=10):
    """The days the price changed: [(day, price)], the last n."""
    out = []
    for d, p in series or []:
        if not out or abs(out[-1][1] - p) >= 1:
            out.append((d, p))
    return out[-n:]


def _steps_html(f):
    steps = price_steps(f.series)
    if len(steps) < 2:
        return ""
    return "<p class='steps'><b>Prisendringer:</b> {}</p>".format(_nb(" → ".join(
        "{} {} kr".format(nice_day(d), kr(p)) for d, p in steps)))


def _available(g, prices):
    """Today's in-stock offers [(shop, price)], cheapest first; from the findings when prices are missing."""
    rows = [(s, p) for s, p, _, stock in (prices or {}).get((g["source"], g["product_id"]), [])
            if stock and not analyze.plan_shop(s)]
    if not rows:
        rows = [(f.shop, f.now) for f in g["findings"] if f.now and not f.shop.startswith("(")]
    return sorted(rows, key=lambda r: r[1])


def _shop_table(g, prices):
    """Every shop's price today; shops with a finding get its normal price, change and type."""
    e = html.escape
    by_shop = {}
    for i, f in enumerate(g["findings"]):
        by_shop.setdefault(f.shop, []).append((i, f))
    rows, seen = [], set()
    offers = [(s, p, stock) for s, p, _, stock in (prices or {}).get((g["source"], g["product_id"]), [])
              if not analyze.plan_shop(s)]
    for f in g["findings"]:
        if f.shop not in {o[0] for o in offers}:
            offers.append((f.shop, f.now, 1))
    offers.sort(key=lambda o: (not o[2], o[1]))
    for shop, price, stock in offers[:30]:
        if shop in seen:
            continue
        seen.add(shop)
        fs = by_shop.get(shop, [])
        if not fs:
            rows.append("<tr class='quiet'><td>{}</td><td class='num'>{} kr{}</td><td>–</td><td></td><td></td></tr>".format(
                e(shop), kr(price), "" if stock else " <small>(utsolgt)</small>"))
            continue
        for i, f in fs:
            ref = f.low30 if f.rule == "before_price" else f.base_median
            rows.append("<tr class='hit' data-i='{i}' tabindex='0' title='Vis grunnlag og graf'><td>{shop}</td>"
                        "<td class='num'>{now} kr</td><td class='num'>{ref}</td><td class='num'>{chg}</td>"
                        "<td title='{name}'><span class='dot {tone}'></span><span class='fnm'>{name}</span></td></tr>".format(
                            i=i, shop=e(shop_name(f)), now=kr(f.now), ref=(kr(ref) + " kr") if ref else "–",
                            chg=e(change(f)), tone=TYPE[f.rule].tone, name=e(TYPE[f.rule].name)))
    return ("<div class='tw'><table class='shops'><tr><th scope='col'>Butikk</th><th scope='col'>Nå</th>"
            "<th scope='col'>Normal</th><th scope='col'>Endring</th><th scope='col'>Funn</th></tr>{}</table></div>"
            ).format("".join(rows))


def _top_fig(g, which):
    """The number a top-5 row shows on the right."""
    f = g[which + "_f"]
    return _nb(change(f) if which == "trick" else kr(f.now) + " kr")


def _top_why(g, which):
    """The short reason under a top-5 row."""
    f = g[which + "_f"]
    if which == "trick":
        shops = len({x.shop for x in g["findings"] if x.rule in TRICK_RULES})
        where = "laveste pris blant butikkene" if f.shop.startswith("(") else f.shop
        return "{} · {}{}".format(TYPE[f.rule].name, where, " +{} butikker".format(shops - 1) if shops > 1 else "")
    return "{} under vanlig lavpris · {}".format(_nb(pct(f.score).lstrip("+")), f.shop)


def _top_line(g, which):
    return "{} – {}".format(_top_fig(g, which), _top_why(g, which))


def _card(g, bf=None, flags=None, day=None, prices=None, collected=None):
    e = html.escape
    fs = g["findings"]
    main = fs[0]
    firsts = [first_day(f, flags, day) for f in fs] if (flags or {}).get("since") else []
    new = any(is_new(f, flags, day) for f in fs)
    lv = max(level(f) for f in fs)
    avail = _available(g, prices)
    listed = {s for s, _, _, _ in (prices or {}).get((g["source"], g["product_id"]), []) if not analyze.plan_shop(s)}
    shops = sorted(listed | {s for s, _ in avail} | {shop_name(f) for f in fs})
    labels = []
    for f in sorted(fs, key=lambda x: (TONE_ORDER[TYPE[x.rule].tone], -(x.score or 0))):
        key = (f.rule, TYPE[f.rule].tone)
        if key in [l[0] for l in labels]:
            continue
        n = sum(1 for x in fs if x.rule == f.rule)
        labels.append((key, "<span class='lab {}'>{}{}</span>".format(
            TYPE[f.rule].tone, e(TYPE[f.rule].name), " <small>· {} butikker</small>".format(n) if n > 1 else "")))
    cheap = ("Billigst nå <b>{} kr</b> hos {}".format(kr(avail[0][1]), e(avail[0][0])) if avail else "")
    if len(avail) > 1:
        cheap += " · {} butikker".format(len(avail))
    meta = [e(g["source"].capitalize()), e(category_name(g["category"]))]
    if firsts:
        meta.append("flagget siden " + e(nice_day(min(firsts))))
    evidence = "".join(
        "<div class='ev'{hid} data-i='{i}'><p class='basis'><b>{shop}, {name}:</b> {basis}</p>{chart}{steps}</div>".format(
            hid="" if i == 0 else " hidden", i=i, shop=e(shop_name(f)), name=e(TYPE[f.rule].name).lower(),
            basis=e(_nb(basis(f))), chart=chart_svg(f, bf=bf), steps=_steps_html(f))
        for i, f in enumerate(fs))
    src = "Hentet fra {}{}.".format(g["source"].capitalize(),
                                     " " + nice_day(collected[:10]) + " kl. " + collected[11:16] if collected else "")
    q = " ".join([g["title"], category_name(g["category"])] + shops).lower()
    return ("<article class='card {tone}{nw}' id='{id}' data-cat='{cat}' data-shops='|{shops}|' data-q='{q}' "
            "data-rules=' {rules} ' data-first='{first}' data-level='{lv}' data-amount='{amt}' data-pop='{pop:.3f}' "
            "data-trick='{trick:.4f}' data-buy='{buy:.4f}' data-tf='{tf}' data-tl='{tl}' data-bf='{bfig}' data-bl='{bl}' "
            "data-title='{title}'>"
            "<div class='head'><p class='hd'>{tag}<span class='tag seen' hidden>Ny siden sist</span>"
            "<a class='t' href='{url}'>{title}</a></p><p class='badge'><b>{chg}</b><span class='cap'>{cap}</span></p></div>"
            "<p class='labs'>{labels}<span class='lvl l{lv}' title='Grad: {lvn}'><i></i><i></i><i></i>{lvn}</span></p>"
            "<p class='pr'>{cheap}</p><p class='st'>{story}</p>"
            "<details class='chk'><summary><span class='meta'>{meta}</span><span class='open'>Kontroller</span></summary>"
            "<p class='hint'>Trykk på en butikk med funn for å se grunnlaget og grafen.</p>{table}{evidence}"
            "<p class='src'>{src} <a href='{url}'>Åpne produktsiden og sjekk prisene selv</a></p></details>"
            "</article>").format(
        tone=_tone(g), nw=" new" if new else "", id=product_id_attr(g), cat=e(g["category"] or ""),
        shops=e("|".join(shops)), q=e(q), rules=" ".join(sorted({f.rule for f in fs})),
        first=e(max(firsts) if firsts else ""), lv=lv, amt=max(amount(f) for f in fs), pop=g.get("pop", 0),
        trick=g.get("trick", 0), buy=g.get("buy", 0),
        tl=e(_top_why(g, "trick")) if g.get("trick_f") else "", bl=e(_top_why(g, "buy")) if g.get("buy_f") else "",
        tf=e(_top_fig(g, "trick")) if g.get("trick_f") else "", bfig=e(_top_fig(g, "buy")) if g.get("buy_f") else "",
        title=e(g["title"]), tag="<span class='tag ny'>NY</span>" if new else "", url=e(g["url"]),
        labels="".join(l[1] for l in labels), cheap=cheap, story=e(_nb(story(main, bf))), meta=" · ".join(meta),
        chg=e(_nb(change(main))), cap=e(TYPE[main.rule].badge), lvn=LEVEL_NAMES[lv - 1],
        table=_shop_table(g, prices), evidence=evidence, src=e(src))


def countdown(days):
    if days > 1:
        return "om {} dager".format(days)
    return {1: "i morgen", 0: "i dag"}.get(days, "for {} dager siden".format(-days))


PHASE_TEXT = {
    "warmup": "Nå er det oppkjøring. En butikk som vil lage en falsk Black Friday-rabatt, setter opp "
              "prisen i disse ukene. Black Week starter {week}.",
    "black_week": "Det er Black Week ({week} – Cyber Monday {cyber}). Nå vises det om «tilbudene» er ekte.",
    "after": "Black Friday er over. Siden viser hva som skjedde, og sesongen avsluttes {end}.",
    "off": "Utenfor Black Friday-sesongen. Normalprisen er det butikken tok for 1–3 måneder siden. "
           "Sesongen starter {start}.",
    "christmas": "Black Friday er over, men prisene følges gjennom julesalget og romjula. "
                 "Prisvakten tar pause fra nyttår.",
    "asleep": "Prisvakten sover og henter ingen priser nå. Den våkner {wake}, så normalprisen kan "
              "måles før sesongen starter {start}.",
}


def _phase_key(ph, today):
    if ph["phase"] != "off":
        return ph["phase"]
    if ph["wake"]:
        return "asleep"
    return "christmas" if today[5:7] == "12" else "off"


def _timeline(ph, today):
    """A bar from the season start to its end, with today and Black Friday marked (the countdown is in the header)."""
    text = PHASE_TEXT[_phase_key(ph, today)].format(
        week=nice_day(ph["week_start"]), cyber=nice_day(ph["week_end"]), end=nice_day(ph["end"]),
        start=nice_day(ph["start"]), wake=nice_day(ph["wake"] or ph["start"]))
    if ph["phase"] == "off":
        return "<section class='bf'><p class='sub'>{}</p></section>".format(text)
    d = analyze._day
    start, end = d(ph["start"]), d(ph["end"])
    total = float((end - start).days)

    def at(day):
        return max(0.0, min(100.0, (d(day) - start).days / total * 100))
    week0, week1 = at(ph["week_start"]), at(ph["week_end"]) + 100 / total
    return ("<section class='bf'><div class='tl' role='img' aria-label='Black Friday-sesongen: oppkjøring "
            "fra {s}, Black Week fra {w}, Black Friday {bf}. I dag er {t}.'>"
            "<span class='seg warm' style='width:{w0:.1f}%'>Oppkjøring</span>"
            "<span class='seg week' style='left:{w0:.1f}%;width:{ww:.1f}%'></span>"
            "<span class='seg tail' style='left:{w1:.1f}%;width:{wt:.1f}%'></span>"
            "<i class='bfmark' style='left:{b:.1f}%' title='Black Friday {bf}'></i><i class='now' style='left:{n:.1f}%'><b>i dag</b></i></div>"
            "<div class='tlx'><span style='left:0'>{s}</span><span class='wk' style='left:{w1:.1f}%'>"
            "Black Week {w}–{we}</span></div><p class='sub'>{text}</p></section>").format(
        s=nice_day(ph["start"]), w=nice_day(ph["week_start"]), bf=nice_day(ph["bf"]),
        t=nice_day(today), we=nice_day(ph["week_end"]), w0=week0, ww=week1 - week0, w1=week1, wt=100 - week1,
        b=at(ph["bf"]) + 50 / total, n=at(today) + 50 / total, text=text)


def _news(findings, flags, day):
    """'Siden 25. okt: +3 nye · 2 borte', with the ones that went away in a small list."""
    e = html.escape
    if not flags or not flags.get("since"):
        return ""
    if not flags.get("prev"):
        return "<p class='news'>Nye funn merkes fra neste innsamling.</p>"
    now = {flag_key(f) for f in findings}
    n_new = sum(1 for f in findings if is_new(f, flags, day))
    gone = [g for g in flags.get("gone", []) if tuple(g[:4]) not in now]
    head = "<b>Siden {}:</b> {}".format(nice_day(flags["prev"]), "+{} nye".format(n_new) if n_new else "ingen nye")
    if not gone:
        return "<p class='news'>{} · ingen borte</p>".format(head)
    items = "".join("<li>{}: {} <small>({})</small></li>".format(
        e(LABEL.get(g[0], g[0])), e(g[4] or g[2]), e("laveste pris" if g[3].startswith("(") else g[3]))
        for g in gone[:30])
    return ("<details class='news'><summary>{} · {} borte</summary><p class='why'>Ikke lenger flagget, "
            "f.eks. fordi prisen er tilbake til normalt eller varen er utsolgt.</p><ul>{}</ul></details>").format(
        head, len(gone), items)


def _summary(findings, shops, ph, news=""):
    e = html.escape
    n = {t.rule: sum(1 for f in findings if f.rule == t.rule) for t in TYPES}
    if not findings and ph["wake"]:
        return ("<section class='verdict calm'><b>Ingen ferske priser.</b> Prisvakten sover til {} "
                "og samler ikke inn før da.</section>").format(nice_day(ph["wake"]))
    if not findings:
        return ("<section class='verdict calm'><b>Ingen mistenkelige priser funnet ennå.</b> "
                "Verktøyet bygger prishistorikk hver dag, og flere funn kommer nærmere Black Friday "
                "({}).{}</section>").format(nice_day(ph["bf"]), news)
    worst = ", ".join("<button type='button' class='link' data-shop='{0}'>{0}</button>".format(e(r["shop"]))
                      for r in list(shops)[:3])
    if ph["phase"] == "warmup":
        # before Black Week the warning sign is a raised price; a "discount" now is not a Black Friday one
        head = ("<b>{} priser er satt opp før Black Friday</b>".format(n["hike"]) if n["hike"]
                else "<b>Ingen priser satt opp før Black Friday ennå</b>")
        cls = " warn" if n["hike"] or n["before_price"] else " calm"
    else:
        bad = n["fake_discount"] + n["before_price"]
        shopset = {(f.source, f.shop) for f in findings
                   if f.rule in ("fake_discount", "before_price") and not f.shop.startswith("(")}
        head = ("<b>{} mistenkelige tilbud</b> hos {} butikker".format(bad, len(shopset)) if bad
                else "<b>Ingen lurepriser funnet</b>")
        cls = "" if bad else " calm"
    return "<section class='verdict{}'>{}{}{}</section>".format(
        cls, head, "<p class='worst'>Flest flagg: " + worst + "</p>" if worst else "", news)


def _shops_table(shops):
    e = html.escape
    if not shops:
        return ""
    rows = "".join(
        "<tr><td><button type='button' class='link' data-shop='{0}'>{0}</button></td><td>{1}</td>"
        "<td class='num'>{2} av {3}</td><td>{4}</td></tr>".format(
            e(r["shop"]), e(r["source"].capitalize()), r["flagged"], r["listed"],
            e(_shop_parts(r)))
        for r in list(shops)[:20])
    return ("<details class='sec'><summary><h2>{}</h2> <span class='n'>{}</span></summary>"
            "<p class='why'>Andel av produktene vi følger hos butikken som har fått et mistenkelig flagg. "
            "Trykk på et navn for å vise bare den butikken.</p><table><caption class='sr'>{}</caption>"
            "<tr><th scope='col'>Butikk</th><th scope='col'>Kilde</th><th scope='col'>Mistenkelige</th>"
            "<th scope='col'>Hva</th></tr>{}</table></details>").format(
        e(SHOPS_TITLE), len(shops), e(SHOPS_TITLE), rows)


def _finder(groups):
    """Search box and category buttons, always visible at the top."""
    e = html.escape
    if not groups:
        return ""
    count = {}
    for g in groups:
        count[g["category"] or ""] = count.get(g["category"] or "", 0) + 1
    cats = sorted(count, key=lambda c: (-count[c], category_name(c)))
    btn = "<button type='button' class='cat' data-cat='{}' aria-pressed='{}'>{} <b>{}</b></button>"
    return ("<section class='finder'><input id='fq' type='search' placeholder='Søk etter produkt, f.eks. iPhone 17' "
            "aria-label='Søk etter produkt eller butikk'><nav class='cats' aria-label='Kategori'>{}{}</nav>"
            "<select id='fcat' class='sr' aria-label='Kategori'><option value=''>Alle</option>{}</select></section>"
            ).format(btn.format("", "true", "Alle", len(groups)),
                     "".join(btn.format(e(c), "false", e(category_name(c)), count[c]) for c in cats),
                     "".join("<option value='{0}'>{0}</option>".format(e(c)) for c in cats))


TOP_WHY = ("Rangert etter hvor stort avviket er (hopp over 100 % teller mindre, de er oftere feil i dataene), hvor populær varen er (antall butikker som selger den og "
           "plassering i kategorilisten) og hvor mange butikker som gjør det samme. Beste kjøp må være billigst "
           "i dag, på lager, minst {} under vanlig lavpris, og teller mindre med under {} dager prishistorikk "
           "eller når en annen butikk lurer på samme vare.").format(_p(analyze.BUY_MIN), analyze.BUY_FULL_DAYS)


TOP_ROW = ("<li><a href='#{id}'><span class='tt'>{title}</span><b class='fig'>{fig}</b></a>"
           "<small>{why}</small></li>")


def _top_list(groups, key):
    e = html.escape
    rows = analyze.top(groups, key)
    if not rows:
        return "<li class='none'>Ingen ennå.</li>"
    return "".join(TOP_ROW.format(id=product_id_attr(g), title=e(g["title"]), fig=e(_top_fig(g, key)),
                                  why=e(_top_why(g, key))) for g in rows)


def _tops(groups):
    if not groups:
        return ""
    return ("<section class='tops'><div class='top bad'><h2>Topp 5 lureri</h2><ol id='toptrick'>{}</ol></div>"
            "<div class='top good'><h2>Topp 5 beste kjøp</h2><ol id='topbuy'>{}</ol></div>"
            "<details class='why'><summary>Slik rangeres listene</summary><p>{}</p></details></section>").format(
                _top_list(groups, "trick"), _top_list(groups, "buy"), html.escape(TOP_WHY))


def _filters(findings):
    """One compact row: what to show and how to sort; the rest folds away under "Flere filter"."""
    e = html.escape
    if not findings:
        return ""
    shops = sorted({shop_name(f) for f in findings}, key=str.lower)
    types = "".join("<option value='{}'>{}</option>".format(t.rule, e(t.name)) for t in TYPES)
    seg = "".join("<button type='button' data-type='{}' aria-pressed='{}'>{}</button>".format(v, "true" if not v else "false", n)
                  for v, n in (("", "Alle"), ("bad", "Lureri"), ("deal", "Gode kjøp")))
    return ("<form class='bar' role='search' onsubmit='return false'><div class='vis' role='group' aria-label='Vis'>{seg}"
            "</div><label class='inl'>Sorter <select id='fsort'><option value=''>Størst avvik</option>"
            "<option value='pop'>Mest populær</option><option value='new'>Nyeste først</option>"
            "<option value='cat'>Kategori</option><option value='kr'>Flest kroner</option></select></label>"
            "<details class='moref'><summary>Flere filter <span id='fact'></span></summary><div class='filters'>"
            "<label>Type <select id='ftype'><option value=''>Alle funn</option><option value='bad'>Bare lureri"
            "</option><option value='deal'>Bare gode kjøp</option>{types}</select></label>"
            "<label>Grad <select id='flvl'><option value=''>Alle</option><option value='2'>Tydelig og kraftig"
            "</option><option value='3'>Bare kraftig</option></select></label>"
            "<label>Butikk <select id='fshop'><option value=''>Alle</option>{shops}</select></label>"
            "<label class='box'><input type='checkbox' id='fnew'> Bare nye</label>"
            "<button type='button' id='freset'>Nullstill</button></div></details></form>").format(
        seg=seg, types=types, shops="".join("<option value='{0}'>{0}</option>".format(e(x)) for x in shops))


# Plain ES5, no libraries. Without it the page still shows every card, top list and check section.
SCRIPT = """<script>
(function(){var D=document;D.documentElement.className+=' js';
var day=D.body.getAttribute('data-day'),prev=null;
try{var v=JSON.parse(localStorage.getItem('bfp.visit')||'{}');if(!v.cur)v.cur=day;
else if(v.cur!==day){v.prev=v.cur;v.cur=day;}prev=v.prev||null;localStorage.setItem('bfp.visit',JSON.stringify(v));}catch(e){}
var list=D.getElementById('funn'),all=list?[].slice.call(list.querySelectorAll('.card')):[];
all.forEach(function(c,i){c._i=i;
if(prev&&!/\\bnew\\b/.test(c.className)&&c.getAttribute('data-first')>prev){c.className+=' seen';
c.querySelector('.tag.seen').hidden=false;}
[].forEach.call(c.querySelectorAll('tr.hit'),function(r){r.onclick=r.onkeydown=function(ev){
if(ev.type==='keydown'&&ev.keyCode!==13)return;var k=r.getAttribute('data-i');
[].forEach.call(c.querySelectorAll('.ev'),function(x){x.hidden=x.getAttribute('data-i')!==k;});
[].forEach.call(c.querySelectorAll('tr.hit'),function(x){x.className=x===r?'hit on':'hit';});};});});
if(!all.length)return;
var q=D.getElementById('fq'),c=D.getElementById('fcat'),ty=D.getElementById('ftype'),lv=D.getElementById('flvl'),
so=D.getElementById('fsort'),s=D.getElementById('fshop'),nw=D.getElementById('fnew'),fn=D.getElementById('fn'),
more=D.getElementById('more'),cats=[].slice.call(D.querySelectorAll('.cat')),SHOW=20,n=SHOW,
segs=[].slice.call(D.querySelectorAll('.vis button')),fa=D.getElementById('fact');
var BAD=[' fake_discount ',' before_price ',' hike '];
function hasType(r){var t=ty.value,rs=r.getAttribute('data-rules');if(!t)return true;
if(t==='bad')return BAD.some(function(b){return rs.indexOf(b)>=0;});return rs.indexOf(' '+t+' ')>=0;}
function pass(r,skipCat){return (skipCat||!c.value||r.getAttribute('data-cat')===c.value)&&
(!s.value||r.getAttribute('data-shops').indexOf('|'+s.value+'|')>=0)&&
(!q.value||r.getAttribute('data-q').indexOf(q.value.toLowerCase())>=0)&&hasType(r)&&
(!lv.value||+r.getAttribute('data-level')>=+lv.value)&&(!nw.checked||/\\b(new|seen)\\b/.test(r.className));}
function key(x){var v=so.value;return v==='new'?x.getAttribute('data-first'):v==='kr'?+x.getAttribute('data-amount'):
v==='pop'?+x.getAttribute('data-pop'):v==='cat'?'':-x._i;}
function cmp(a,b){if(so.value==='cat'){var x=a.getAttribute('data-cat'),y=b.getAttribute('data-cat');
if(x!==y)return x<y?-1:1;return a._i-b._i;}var p=key(a),r=key(b);return p<r?1:p>r?-1:a._i-b._i;}
function top(id,attr,line,fig){var ol=D.getElementById(id);if(!ol)return;
var cs=all.filter(function(r){return pass(r)&&+r.getAttribute(attr)>0;});
cs.sort(function(a,b){return b.getAttribute(attr)-a.getAttribute(attr);});ol.innerHTML='';
cs.slice(0,5).forEach(function(r){var li=D.createElement('li'),a=D.createElement('a'),t=D.createElement('span'),
f=D.createElement('b'),sm=D.createElement('small');a.href='#'+r.id;t.className='tt';t.textContent=r.getAttribute('data-title');
f.className='fig';f.textContent=r.getAttribute(fig);sm.textContent=r.getAttribute(line);a.appendChild(t);a.appendChild(f);
li.appendChild(a);li.appendChild(sm);ol.appendChild(li);});
if(!cs.length)ol.innerHTML="<li class='none'>Ingen med dette utvalget.</li>";}
function run(){var cs=all.slice().sort(cmp),i=0,per={},tot=0;
cs.forEach(function(r){list.appendChild(r);var ok=pass(r);r.hidden=!ok||i>=n;if(ok)i++;
if(pass(r,true)){var k=r.getAttribute('data-cat');per[k]=(per[k]||0)+1;tot++;}});
cats.forEach(function(b){var k=b.getAttribute('data-cat');b.setAttribute('aria-pressed',k===c.value);
var m=k?(per[k]||0):tot;b.querySelector('b').textContent=m;b.className=m||k===c.value?'cat':'cat zero';if(k===c.value&&k)b.parentNode.scrollLeft=b.offsetLeft-b.parentNode.offsetLeft-40;});
fn.textContent=i>n?n+' av '+i:i+' produkter';
more.hidden=i<=n;more.textContent='Vis alle '+i;
top('toptrick','data-trick','data-tl','data-tf');top('topbuy','data-buy','data-bl','data-bf');
segs.forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-type')===ty.value);});
var act=[lv.value,s.value,nw.checked?'1':'',(ty.value&&ty.value!=='bad'&&ty.value!=='deal')?'1':''].filter(Boolean).length;fa.textContent=act?'('+act+')':'';
try{history.replaceState(null,'',c.value?'#k='+c.value:location.pathname);}catch(e){}}
function reset(){n=SHOW;run();}
cats.forEach(function(b){b.onclick=function(){c.value=b.getAttribute('data-cat');reset();};});
segs.forEach(function(b){b.onclick=function(){ty.value=b.getAttribute('data-type');reset();};});
[ty,lv,so,s,nw].forEach(function(x){x.onchange=reset;});q.oninput=reset;
more.onclick=function(){n=1e9;run();};
D.getElementById('freset').onclick=function(){c.value=s.value=q.value=lv.value=so.value=ty.value='';nw.checked=false;reset();};
[].forEach.call(D.querySelectorAll('button.link[data-shop]'),function(b){b.onclick=function(){
s.value=b.getAttribute('data-shop');reset();list.scrollIntoView();};});
D.addEventListener('click',function(ev){var a=ev.target.closest&&ev.target.closest('.tops a');if(!a)return;
var card=D.getElementById(a.getAttribute('href').slice(1));if(!card)return;ev.preventDefault();
card.hidden=false;var d=card.querySelector('details');if(d)d.open=true;card.scrollIntoView();});
function fromHash(){var h=location.hash.match(/^#k=([\\w-]*)/);if(h)c.value=h[1];}
window.onhashchange=function(){if(/^#k=/.test(location.hash)){fromHash();reset();}};fromHash();
run();})();
</script>"""


def _howto(ph):
    lines = ["<li><span class='dot {}'></span><b>{}</b>: {} <small>Kriterium: {} {}</small></li>".format(
        t.tone, html.escape(t.name), html.escape(t.means), html.escape(_nb(t.measured)), html.escape(_nb(level_text(t))))
        for t in sections(ph["phase"])]
    lines.append("<li>Normalprisen er hva butikken tok for 1–3 måneder siden.</li>" if ph["phase"] == "off" else
                 "<li>Normalprisen er hva butikken vanligvis tok, målt før sesongen startet 1. oktober.</li>")
    lines.append("<li><span class='tag ny'>NY</span> = flagget for første gang i siste innsamling. "
                 "<span class='tag seen'>Ny siden sist</span> = kommet til siden du sist åpnet siden på denne enheten. "
                 "Grad (Litt, Tydelig, Kraftig) sier hvor stort avviket er innenfor typen.</li>")
    return "\n".join(lines)


def html_page(findings, stats, files, running, shops=(), top=300, today=None, data_day=None, flags=None,
              pop=None, prices=None, collected=None):
    """The dashboard: search and categories, top 5 lists, then one card per product.

    flags = store.flag_info(day): marks new findings and lists the ones that went away.
    pop = store.popularity(day), prices = store.shop_prices(day, ...), collected = {source: "YYYY-MM-DD HH:MM"}.
    """
    e = html.escape
    shops = list(shops)
    today = today or stats.get("last_day") or net.today_oslo()
    day = data_day or today
    ph = analyze.bf_phase(today)
    groups = analyze.rank_products(analyze.group_findings(findings)[:top], pop or {}, prices,
                                   strong=lambda f: level(f) >= 2)
    cards = "".join(_card(g, ph["bf"], flags, day, prices, (collected or {}).get(g["source"])) for g in groups)
    body = ("<section class='sec prods'><h2>Produkter <span id='fn'>{n} produkter</span></h2>{filters}"
            "<div id='funn' class='list'>{cards}</div><button type='button' id='more' hidden>Vis alle</button>"
            "</section>").format(n=len(groups), filters=_filters(findings), cards=cards) if groups else ""
    file_links = "".join("<li><a href='files/{0}'>{0}</a></li>".format(e(n)) for n in files)
    return """<!doctype html><html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Black Friday-prisvakt</title>
<style>:root{{--bg:#fff;--fg:#1a1a1a;--muted:#5c5c5c;--rule:#e2e2e2;--card:#f6f6f7;--link:#0645ad;--line:#2b6cb0;
--ref:#888;--claim:#c53030;--peak:#c05621;--now:#2f855a;--bad:#fde8e8;--badfg:#9b1c1c;--ok:#e6f4ea;--okfg:#1e6b34;
--warn:#fff4e0;--warnfg:#7a4100;--band:#e6edf6;--new:#2b6cb0;--newbg:#e3eefb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#e6e6e6;--muted:#a8a8a8;--rule:#2e2e30;--card:#1c1c1e;
--link:#8ab4ff;--line:#63b3ed;--ref:#999;--claim:#fc8181;--peak:#f6ad55;--now:#68d391;--bad:#3b1d1d;--badfg:#ffb4b4;
--ok:#1c3324;--okfg:#9ae6b4;--warn:#3a2a12;--warnfg:#fbd38d;--band:#1f2c3a;--new:#8ab4ff;--newbg:#1b2a40}}}}
*{{box-sizing:border-box}}body{{font:16px/1.45 system-ui,sans-serif;margin:0 auto;padding:12px 16px 24px;max-width:1000px;
color:var(--fg);background:var(--bg)}}a{{color:var(--link)}}p{{margin:0}}
.hdr{{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 12px}}
h1{{margin:0;font-size:22px}}.pill{{font-size:15px;padding:2px 10px;border-radius:12px;background:var(--card)}}
h2{{font-size:18px;margin:0 0 4px}}small,.why,.sub{{color:var(--muted)}}.sub{{font-size:14px;margin:2px 0 8px}}
.bf{{margin:8px 0}}.bf .sub{{margin:4px 0 0}}.tl{{position:relative;height:16px;border-radius:5px;background:var(--card);
margin-top:18px}}.seg{{position:absolute;top:0;bottom:0;font-size:11px;line-height:16px;padding-left:6px;white-space:nowrap;
overflow:hidden}}.seg.warm{{left:0;background:var(--warn);color:var(--warnfg);border-radius:5px 0 0 5px}}
.seg.week{{background:var(--fg)}}.seg.tail{{background:var(--rule);border-radius:0 5px 5px 0}}
.tl i{{position:absolute;top:-3px;bottom:-3px;width:0;border-left:3px solid}}.tl .bfmark{{border-color:var(--claim)}}
.tl .now{{border-color:var(--line)}}.tl .now b{{position:absolute;top:-17px;left:0;transform:translateX(-50%);
white-space:nowrap;font:600 11px system-ui;color:var(--line);font-style:normal}}.tlx{{position:relative;height:16px;
font-size:11px;color:var(--muted);margin:2px 0 0}}.tlx span{{position:absolute;transform:translateX(-50%);white-space:nowrap}}
.tlx span:first-child{{transform:none}}.tlx .wk{{transform:translateX(-100%);font-weight:600;color:var(--fg)}}
.verdict{{background:var(--bad);color:var(--badfg);padding:10px 12px;border-radius:8px;margin:10px 0}}
.verdict.calm{{background:var(--ok);color:var(--okfg)}}.verdict.warn{{background:var(--warn);color:var(--warnfg)}}
.verdict .link{{color:inherit;font-weight:600}}.worst{{font-size:14px;margin-top:2px}}
.news{{margin-top:6px;padding-top:6px;border-top:1px solid currentColor;font-size:15px}}.news ul{{margin:4px 0;padding-left:18px}}
.news .why{{color:inherit;opacity:.85;font-size:14px}}
select,input,button{{font:inherit;padding:5px 8px;border:1px solid var(--rule);border-radius:6px;background:var(--bg);
color:var(--fg)}}input[type=checkbox]{{width:18px;height:18px;padding:0}}button{{cursor:pointer}}
button.link{{border:0;padding:0;background:none;color:var(--link);text-decoration:underline}}
.sec{{margin:8px 0 16px}}.why{{font-size:14px;margin-bottom:8px}}.why small{{display:block}}summary{{cursor:pointer}}
.n{{display:inline-block;min-width:24px;text-align:center;padding:0 7px;border-radius:10px;background:var(--card);
font-size:15px}}.none{{color:var(--muted);margin:6px 0}}
[hidden]{{display:none!important}}
.finder{{position:sticky;top:0;z-index:2;background:var(--bg);padding:8px 0 4px;margin:4px 0 0;box-shadow:0 8px 8px -8px rgba(0,0,0,.15)}}#fq{{width:100%;font-size:16px;padding:9px 12px;border-radius:8px}}
.cats{{display:flex;gap:6px;overflow-x:auto;padding:8px 0 4px;scrollbar-width:none}}.cats::-webkit-scrollbar{{display:none}}
.cat{{flex:0 0 auto;border-radius:16px;padding:3px 11px;font-size:14px;background:var(--card);border-color:transparent}}
.cat b{{font-weight:500;color:var(--muted);margin-left:3px}}.cat[aria-pressed=true]{{background:var(--fg);color:var(--bg)}}
.cat[aria-pressed=true] b{{color:var(--bg)}}.cat.zero{{opacity:.45}}
.tops{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;margin:14px 0 6px}}.tops>.why{{grid-column:1/-1;font-size:13px;margin:0}}
.tops>.why p{{margin-top:4px}}.top{{min-width:0;border:1px solid var(--rule);border-radius:10px;padding:10px 12px 6px}}
.top h2{{font-size:15px;margin:0 0 4px;display:flex;align-items:center;gap:6px}}.top h2:before{{content:'';width:8px;height:8px;border-radius:50%;background:var(--muted)}}.top.bad h2:before{{background:var(--badfg)}}.top.good h2:before{{background:var(--okfg)}}
.top ol{{margin:0;padding:0;list-style:none;counter-reset:r}}.top li{{counter-increment:r;position:relative;padding:6px 0 6px 26px;border-top:1px solid var(--rule)}}.top li:first-child{{border-top:0}}
.top li:before{{content:counter(r);position:absolute;left:0;top:7px;width:18px;height:18px;border-radius:50%;background:var(--card);font-size:12px;font-weight:600;text-align:center;line-height:18px;color:var(--muted)}}
.top li a{{display:flex;gap:8px;align-items:baseline;color:var(--fg);text-decoration:none}}.top li a:hover .tt{{text-decoration:underline}}
.tt{{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;font-size:15px}}
.fig{{white-space:nowrap;font-size:15px}}.top.bad .fig{{color:var(--badfg)}}.top.good .fig{{color:var(--okfg)}}
.top li small{{display:block;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.top .none{{padding-left:0;color:var(--muted)}}.top .none:before{{display:none}}
.prods{{scroll-margin-top:120px}}.prods h2{{display:flex;align-items:baseline;gap:8px;margin:18px 0 8px}}#fn{{font-size:14px;font-weight:400;color:var(--muted)}}
.bar{{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:0 0 6px}}.vis{{display:inline-flex;border:1px solid var(--rule);border-radius:8px;overflow:hidden}}.vis button{{border:0;border-radius:0;padding:5px 12px;font-size:14px;background:var(--bg)}}
.vis button+button{{border-left:1px solid var(--rule)}}.vis button[aria-pressed=true]{{background:var(--fg);color:var(--bg)}}
.inl{{font-size:14px;color:var(--muted);display:flex;gap:6px;align-items:center}}.inl select{{font-size:14px}}
.moref{{font-size:14px}}.moref>summary{{color:var(--link)}}.moref[open]{{flex-basis:100%}}
.filters{{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:end;margin-top:8px;padding:10px;border-radius:8px;background:var(--card)}}
.filters label{{display:flex;flex-direction:column;font-size:13px;color:var(--muted)}}.filters .box{{flex-direction:row;gap:6px;align-items:center;color:var(--fg);font-size:14px;padding-bottom:6px}}
.card{{scroll-margin-top:120px;background:var(--bg);border:1px solid var(--rule);border-left:3px solid var(--rule);border-radius:10px;padding:10px 14px 8px;margin:8px 0}}
.card.bad{{border-left-color:var(--badfg)}}.card.warn{{border-left-color:var(--warnfg)}}.card.good{{border-left-color:var(--okfg)}}
.card.new,.card.seen{{box-shadow:0 0 0 1px var(--new)}}.card p{{margin:3px 0}}
.head{{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}}.hd{{flex:1;min-width:0}}
.t{{font-weight:600;font-size:16px;color:var(--fg);text-decoration:none}}.t:hover{{text-decoration:underline}}
.tag{{font-size:11px;font-weight:700;letter-spacing:.03em;padding:1px 6px;border-radius:6px;background:var(--new);color:var(--bg);margin-right:6px;vertical-align:2px}}.tag.seen{{background:var(--newbg);color:var(--new)}}
.badge{{text-align:right;white-space:nowrap;color:var(--badfg);margin:0!important}}.badge b{{display:block;font-size:20px;line-height:1.15}}
.badge .cap{{display:block;font-size:12px;color:var(--muted)}}.card.warn .badge{{color:var(--warnfg)}}.card.good .badge{{color:var(--okfg)}}
.labs{{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center}}.lab{{font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px}}
.lab.bad{{background:var(--bad);color:var(--badfg)}}.lab.warn{{background:var(--warn);color:var(--warnfg)}}.lab.good{{background:var(--ok);color:var(--okfg)}}.lab small{{font-weight:400}}
.lvl{{display:inline-flex;gap:2px;align-items:center;font-size:12px;color:var(--muted);margin-left:2px}}.lvl i{{display:inline-block;width:6px;height:11px;border-radius:2px;background:var(--rule)}}.lvl i:last-of-type{{margin-right:4px}}
.card.bad .lvl{{color:var(--badfg)}}.card.warn .lvl{{color:var(--warnfg)}}.card.good .lvl{{color:var(--okfg)}}.l1 i:nth-child(1),.l2 i:nth-child(-n+2),.l3 i{{background:currentColor}}
.pr{{font-size:15px}}.st{{font-size:14px;color:var(--muted)}}
.chk{{margin-top:4px;border-top:1px solid var(--rule);padding-top:6px}}.chk>summary{{display:flex;gap:8px;align-items:baseline;list-style:none;font-size:13px}}.chk>summary::-webkit-details-marker{{display:none}}
.chk .meta{{flex:1;color:var(--muted)}}.chk .open{{color:var(--link);font-weight:600;white-space:nowrap}}.chk .open:after{{content:' ▾'}}.chk[open] .open:after{{content:' ▴'}}
.hint{{font-size:13px;color:var(--muted);margin:8px 0 2px!important}}.tw{{overflow-x:auto}}.shops{{font-size:14px;margin:4px 0 8px}}.shops td,.shops th{{padding:4px 6px}}
.shops tr.quiet{{color:var(--muted)}}.shops tr.hit{{cursor:pointer}}.shops tr.hit:hover,.shops tr.on{{background:var(--band)}}
.ev{{display:grid;grid-template-columns:1fr 300px;gap:6px 14px;align-items:start}}.basis,.steps{{font-size:14px}}
.steps{{grid-column:1/-1;color:var(--muted)}}.src{{font-size:13px;color:var(--muted);margin-top:6px}}
.ch .mk{{fill:none;stroke:var(--ref);stroke-width:1.5;stroke-dasharray:1 3}}
#more{{display:block;width:100%;margin:10px 0;padding:9px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}.dot.bad{{background:var(--badfg)}}
.dot.warn{{background:var(--warnfg)}}.dot.good{{background:var(--okfg)}}.more{{margin:4px 0}}
.how{{margin:16px 0}}.how ul{{margin:6px 0;padding-left:20px}}.how li{{margin:4px 0}}.how small{{display:block}}
table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{border-bottom:1px solid var(--rule);
padding:6px;text-align:left;vertical-align:top}}.num{{white-space:nowrap}}.sr{{position:absolute;left:-9999px}}
details.sec>summary h2{{display:inline}}
.ch .ln{{fill:none;stroke:var(--line);stroke-width:2}}.ch .ref{{stroke:var(--ref);stroke-dasharray:4 3}}
.ch .claim{{stroke:var(--claim);stroke-dasharray:5 2}}.ch .pk{{fill:var(--peak)}}.ch .now{{fill:var(--now)}}
.ch .grid{{stroke:var(--rule);stroke-width:1}}.ch .base{{fill:var(--band)}}.ch .fut{{fill:none;stroke:var(--rule);stroke-dasharray:3 4}}
.ch .bfl{{stroke:var(--claim);stroke-width:1.5;stroke-dasharray:2 2}}
.ch text{{font:11px system-ui,sans-serif;fill:var(--muted);paint-order:stroke;stroke:var(--card);stroke-width:3px;stroke-linejoin:round}}.ch .strong{{font-weight:600;fill:var(--fg)}}
.ch .pkl{{fill:var(--peak);font-weight:600}}.ch .nowl{{fill:var(--now);font-weight:600}}
.ch .claiml{{fill:var(--claim)}}.ch .bft{{fill:var(--claim);font-weight:600}}.ch .soft{{font-size:10px}}
.ch{{width:100%;max-width:300px;height:auto;display:block}}.key{{font-size:14px;color:var(--muted)}}
.key span{{margin-right:12px;white-space:nowrap}}footer{{margin-top:24px;border-top:1px solid var(--rule);
padding-top:10px;font-size:15px}}footer p{{margin:6px 0}}
@media (max-width:640px){{body{{padding:10px 12px 20px}}.fnm{{display:none}}.shops td,.shops th{{padding:4px 3px}}.tops{{grid-template-columns:1fr}}.ev{{grid-template-columns:1fr}}.ch{{max-width:480px}}table{{font-size:14px}}.badge .cap{{display:none}}.badge b{{font-size:18px}}.filters label{{flex:1 1 40%}}.inl{{margin-left:auto}}}}</style>
</head><body data-day="{day}">
<header class="hdr"><h1>Black Friday-prisvakt</h1><p class="pill"><b>Black Friday {bfday}</b> · {countdown}</p></header>
<p class="sub">{products} produkter fulgt · priser fra {first} til {last}{status}</p>
{timeline}{verdict}{finder}{tops}{body}{shops}
<details class="how"><summary>Slik leser du siden</summary><ul>
{howto}
<li>Rapporten viser bare prisdata. Sjekk alltid selv før du konkluderer.</li></ul>
<p class="key"><span style="color:var(--line)">━ pris</span><span style="color:var(--ref)">┅ normalpris</span>
<span>▒ dagene normalprisen er målt</span><span style="color:var(--claim)">┅ oppgitt før-pris</span>
<span style="color:var(--ref)">┈ snitt alle butikker</span><span style="color:var(--peak)">● høyeste</span><span style="color:var(--now)">● nå</span>
<span style="color:var(--claim)">┆ Black Friday</span></p></details>
<footer><p>{auto} Sist oppdatert: {last}.</p>
<form method="post" action="run"><button {disabled}>{btn}</button></form>
<p>Last ned:</p><ul>{files}</ul></footer>{script}</body></html>""".format(
        day=e(day), bfday=e(nice_day(ph["bf"])), countdown=e(countdown(ph["days"])),
        products=e(str(stats.get("products", 0))), first=e(nice_day(stats.get("first_day")) or "–"),
        last=e(nice_day(stats.get("last_day")) or "–"),
        auto=("Prisene hentes automatisk hver morgen fra {} til nyttår.".format(nice_day(ph["wake"]))
              if ph["wake"] else "Prisene hentes automatisk hver morgen."),
        status=(" · <b>henter priser nå …</b>" if running else "") + (
            " · <b>funnene gjelder prisene fra {}</b>, dagens priser hentes i løpet av morgenen".format(
                e(nice_day(data_day))) if data_day and data_day != today else ""),
        verdict=_summary(findings, shops, ph, _news(findings, flags, day)),
        finder=_finder(groups), tops=_tops(groups), timeline=_timeline(ph, today), howto=_howto(ph),
        shops=_shops_table(shops), body=body,
        disabled="disabled" if running else "", btn="Henter …" if running else "Hent priser nå",
        files=file_links, script=SCRIPT)
