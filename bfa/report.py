"""Reports: CSV, Markdown, compact text (for terminals/agents) and a small HTML page."""
import csv
import html
from datetime import datetime

RULES = [
    ("fake_discount", "Falsk rabatt: prisen ble hevet før Black Friday og så «satt ned» igjen"),
    ("before_price", "Før-prisen er høyere enn butikkens egen laveste pris siste 30 dager"),
    ("hike", "Prisen er hevet nylig (følg med inn mot Black Friday)"),
    ("deal", "Ekte tilbud: under laveste pris i perioden før"),
]
LABEL = dict(RULES)
SHOPS_TITLE = "Butikker med flest flaggede produkter"


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


def _shop_line(r):
    parts = ["{} falsk rabatt".format(r["fake_discount"]) if r["fake_discount"] else "",
             "{} før-pris".format(r["before_price"]) if r["before_price"] else "",
             "{} prishopp".format(r["hike"]) if r["hike"] else ""]
    return "{} av {} produkter ({:.0f} %): {}".format(
        r["flagged"], r["listed"], r["share"] * 100, ", ".join(p for p in parts if p))


def text(findings, stats, shops=(), top=15):
    lines = ["Data: {products} produkter, {first_day} → {last_day}".format(**stats)]
    for rule, label in RULES:
        rows = [f for f in findings if f.rule == rule]
        lines.append("")
        lines.append("== {} ({}) ==".format(label, len(rows)))
        for f in rows[:top]:
            lines.append("{:>5} {} | {} [{}] | {} | {} d".format(
                change(f), f.title[:60], _shop(f), f.source, _what(f), f.days))
    lines += ["", "== {} ({}) ==".format(SHOPS_TITLE, len(shops))]
    for r in list(shops)[:top]:
        lines.append("{} [{}] | {}".format(r["shop"], r["source"], _shop_line(r)))
    return "\n".join(lines)


def save_csv(path, findings):
    cols = ["rule", "source", "title", "shop", "base_median", "base_low", "baseline", "peak", "peak_day",
            "now", "claimed", "low30", "score", "days", "category", "url"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in findings:
            d = f.as_dict()
            d["score"] = None if f.score is None else round(f.score * 100, 1)
            w.writerow(["" if d[c] is None else d[c] for c in cols])


def save_markdown(path, findings, stats, shops=(), top=25):
    lines = ["# Black Friday-analyse", "",
             "Data: {products} produkter, {first_day} → {last_day}.".format(**stats), ""]
    for rule, label in RULES:
        rows = [f for f in findings if f.rule == rule][:top]
        lines += ["## " + label, ""]
        if not rows:
            lines += ["_Ingen funn._", ""]
            continue
        lines += ["| Produkt | Butikk | Kilde | Endring | Detaljer | Dager |", "|---|---|---|---:|---|---:|"]
        for f in rows:
            lines.append("| [{}]({}) | {} | {} | {} | {} | {} |".format(
                f.title.replace("|", "/"), f.url, _shop(f), f.source, change(f), _what(f), f.days))
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

def chart_svg(f, w=240, h=56):
    """Inline SVG of the shop's price series: baseline band, line, peak and today's price."""
    pts = f.series or []
    if len(pts) < 2:
        return ""
    t0 = datetime.strptime(pts[0][0], "%Y-%m-%d")
    xs = [(datetime.strptime(d, "%Y-%m-%d") - t0).days for d, _ in pts]
    refs = [v for v in (f.base_median, f.base_low, f.claimed, f.low30) if v]
    lo = min([p for _, p in pts] + refs)
    hi = max([p for _, p in pts] + refs)
    span_x = float(max(xs[-1], 1))
    span_y = float(hi - lo) or 1.0
    pad = 4

    def X(x):
        return pad + (w - 2 * pad) * x / span_x

    def Y(p):
        return pad + (h - 2 * pad) * (1 - (p - lo) / span_y)

    # step line: a price holds until the next observation
    path = "M{:.1f},{:.1f}".format(X(xs[0]), Y(pts[0][1]))
    for i in range(1, len(pts)):
        path += " H{:.1f} V{:.1f}".format(X(xs[i]), Y(pts[i][1]))
    parts = ['<svg class="ch" viewBox="0 0 {0} {1}" width="{0}" height="{1}" role="img" aria-label="{2}">'.format(
        w, h, html.escape("Prisutvikling " + pts[0][0] + " til " + pts[-1][0]))]
    if f.base_median:
        parts.append('<line class="ref" x1="0" x2="{}" y1="{:.1f}" y2="{:.1f}"><title>normal {} kr</title></line>'.format(
            w, Y(f.base_median), Y(f.base_median), kr(f.base_median)))
    if f.claimed:
        parts.append('<line class="claim" x1="0" x2="{}" y1="{:.1f}" y2="{:.1f}"><title>før-pris {} kr</title></line>'.format(
            w, Y(f.claimed), Y(f.claimed), kr(f.claimed)))
    parts.append('<path class="ln" d="{}"/>'.format(path))
    if f.peak and f.peak_day:
        px = (datetime.strptime(f.peak_day, "%Y-%m-%d") - t0).days
        parts.append('<circle class="pk" cx="{:.1f}" cy="{:.1f}" r="3"><title>topp {} kr</title></circle>'.format(
            X(px), Y(f.peak), kr(f.peak)))
    parts.append('<circle class="now" cx="{:.1f}" cy="{:.1f}" r="3"><title>nå {} kr</title></circle>'.format(
        X(xs[-1]), Y(pts[-1][1]), kr(pts[-1][1])))
    parts.append("</svg>")
    return "".join(parts)


def html_page(findings, stats, files, running, shops=(), top=50):
    e = html.escape
    parts = []
    shops = list(shops)
    parts.append("<h2>{} <small>({})</small></h2>".format(e(SHOPS_TITLE), len(shops)))
    if shops:
        parts.append("<table><tr><th>Butikk</th><th>Kilde</th><th>Flagget</th></tr>")
        for r in shops[:20]:
            parts.append("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                e(r["shop"]), e(r["source"]), e(_shop_line(r))))
        parts.append("</table>")
    for rule, label in RULES:
        rows = [f for f in findings if f.rule == rule][:top]
        parts.append("<h2>{} <small>({})</small></h2>".format(e(label), len(rows)))
        if rows:
            parts.append("<table><tr><th>Produkt</th><th>Endring</th><th>Prisutvikling</th></tr>")
            for f in rows:
                parts.append("<tr><td><a href='{}'>{}</a><br><b>{}</b><br><small>{} · {} dager data</small></td>"
                             "<td class='num'>{}</td><td>{}<br><small>{}</small></td></tr>".format(
                                 e(f.url), e(f.title), e(_shop(f)), e(f.source), f.days,
                                 e(change(f)), chart_svg(f), e(_what(f))))
            parts.append("</table>")
    file_links = "".join("<li><a href='/files/{0}'>{0}</a></li>".format(e(n)) for n in files)
    return """<!doctype html><html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BF-analyse</title>
<style>:root{{--bg:#fff;--fg:#222;--muted:#777;--rule:#ddd;--link:#0645ad;--line:#2b6cb0;--ref:#999;--claim:#c53030;--peak:#c05621;--now:#2f855a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#ddd;--muted:#999;--rule:#333;--link:#8bf;--line:#63b3ed;--ref:#777;--claim:#fc8181;--peak:#f6ad55;--now:#68d391}}}}
body{{font:15px system-ui,sans-serif;margin:16px;max-width:1150px;color:var(--fg);background:var(--bg)}}a{{color:var(--link)}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border-bottom:1px solid var(--rule);padding:4px 6px;text-align:left;vertical-align:top}}
small{{color:var(--muted);font-weight:normal}}button{{font-size:15px;padding:6px 12px}}
.ch .ln{{fill:none;stroke:var(--line);stroke-width:1.5}}.ch .ref{{stroke:var(--ref);stroke-dasharray:3 3}}
.ch .claim{{stroke:var(--claim);stroke-dasharray:5 2}}.ch .pk{{fill:var(--peak)}}.ch .now{{fill:var(--now)}}
.ch{{max-width:100%;height:auto;display:block}}.num{{white-space:nowrap;font-weight:600}}
.key span{{margin-right:12px}}</style>
</head><body><h1>Black Friday-analyse</h1>
<p>{products} produkter · data {first_day} → {last_day} · {status}</p>
<form method="post" action="/run"><button {disabled}>Hent priser nå</button></form>
<p class="key"><small><span style="color:var(--line)">━ pris</span><span style="color:var(--ref)">┅ normalpris</span>
<span style="color:var(--claim)">┅ før-pris</span><span style="color:var(--peak)">● topp</span><span style="color:var(--now)">● nå</span></small></p>
<div class="wrap">{body}</div><h2>Filer</h2><ul>{files}</ul></body></html>""".format(
        status="innhenting pågår …" if running else "klar",
        disabled="disabled" if running else "", body="".join(parts), files=file_links,
        **{k: e(str(v)) for k, v in stats.items()})
