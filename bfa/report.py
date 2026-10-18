"""Reports: CSV, Markdown, compact text (for terminals/agents) and a small HTML page."""
import csv
import html
from datetime import datetime

from . import analyze, net

RULES = [
    ("fake_discount", "Falsk rabatt: prisen ble hevet før Black Friday og så «satt ned» igjen"),
    ("before_price", "Før-prisen er høyere enn butikkens egen laveste pris siste 30 dager"),
    ("hike", "Prisen er hevet nylig (følg med inn mot Black Friday)"),
    ("deal", "Ekte tilbud: under laveste pris i perioden før"),
]
LABEL = dict(RULES)
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
                f.title.replace("|", "/"), f.url, shop_name(f), f.source, change(f), story(f), f.days))
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
        w, h, html.escape("Pris {} til {}: {}nå {} kr".format(
            nice_day(pts[0][0]), nice_day(pts[-1][0]),
            "normal {} kr, høyeste {} kr, ".format(kr(f.base_median), kr(f.peak)) if f.base_median and f.peak else "",
            kr(pts[-1][1]))))]
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


# ---- dashboard -------------------------------------------------------------

# rule -> (short title, one line on what it means). Order = importance.
SECTIONS = [   # Black Week and after: the "discount" has happened, so a fake one is the headline
    ("fake_discount", "Falsk rabatt",
     "Prisen ble hevet i ukene før, og «tilbudet» tar den bare tilbake til vanlig pris. Sterkest mistanke."),
    ("before_price", "Villedende før-pris",
     "Butikken oppgir en før-pris som er høyere enn det den selv tok de siste 30 dagene."),
    ("hike", "Prishopp",
     "Prisen er hevet nylig. Ikke galt i seg selv, men følg med om den «settes ned» til Black Friday."),
    ("deal", "Ekte tilbud",
     "Prisen er tydelig lavere enn den har vært. Dette er gode kjøp."),
]
WARMUP_SECTIONS = [   # before Black Week: a hike now is the set-up for a fake Black Friday discount
    ("hike", "Hevet før Black Friday",
     "Prisen er satt opp etter 1. oktober og ligger fortsatt høyt. Slik starter en falsk "
     "Black Friday-rabatt: følg med om disse «settes ned» til Black Friday."),
    ("fake_discount", "Hevet og satt ned igjen",
     "Prisen ble satt opp etter 1. oktober og så ned igjen til omtrent vanlig pris. "
     "En «kampanje» nå er ingen ekte rabatt."),
    ("before_price", "Villedende før-pris",
     "Butikken oppgir en før-pris som er høyere enn det den selv tok de siste 30 dagene."),
    ("deal", "Billigere enn i sommer",
     "Lavere enn før 1. oktober. Et vanlig tilbud, ikke knyttet til Black Friday."),
]


def sections(phase):
    return WARMUP_SECTIONS if phase == "warmup" else SECTIONS


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
    return "Billigste butikk (historikk)" if f.shop.startswith("(") else f.shop


def _card(f, bf=None):
    e = html.escape
    meta = [e(f.source.capitalize()), e(category_name(f.category))]
    meta.append("{} dager med priser".format(f.days) if f.days else "mangler data")
    if f.baseline:
        meta.append("normalpris målt " + e(nice_baseline(f.baseline)))
    text = " ".join([f.title, shop_name(f), category_name(f.category)]).lower()
    return ("<article class='card {r}' data-cat='{cat}' data-shop='{shop}' data-q='{q}'>"
            "<div class='main'><a class='t' href='{url}'>{title}</a>"
            "<div class='shop'>{shopname}</div><p>{story}</p><small>{meta}</small></div>"
            "<div class='badge' title='Endring'>{chg}</div><div class='chart'>{chart}</div></article>").format(
        r=f.rule, cat=e(f.category or ""), shop=e(shop_name(f)), q=e(text), url=e(f.url), title=e(f.title),
        shopname=e(shop_name(f)), story=e(story(f, bf)), meta=" · ".join(meta), chg=e(change(f)), chart=chart_svg(f))


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
}


def _timeline(ph, today):
    """Countdown and a bar from the season start to its end, with today and Black Friday marked."""
    text = PHASE_TEXT[ph["phase"]].format(week=nice_day(ph["week_start"]), cyber=nice_day(ph["week_end"]),
                                          end=nice_day(ph["end"]), start=nice_day(ph["start"]))
    head = "<p class='bfhead'><b>Black Friday {}</b> · {}</p>".format(nice_day(ph["bf"]), countdown(ph["days"]))
    if ph["phase"] == "off":
        return "<section class='bf'>{}<p class='sub'>{}</p></section>".format(head, text)
    d = analyze._day
    start, end = d(ph["start"]), d(ph["end"])
    total = float((end - start).days)

    def at(day):
        return max(0.0, min(100.0, (d(day) - start).days / total * 100))
    week0, week1 = at(ph["week_start"]), at(ph["week_end"]) + 100 / total
    return ("<section class='bf'>{head}<div class='tl' role='img' aria-label='Black Friday-sesongen: oppkjøring "
            "fra {s}, Black Week fra {w}, Black Friday {bf}. I dag er {t}.'>"
            "<span class='seg warm' style='width:{w0:.1f}%'>Oppkjøring</span>"
            "<span class='seg week' style='left:{w0:.1f}%;width:{ww:.1f}%'></span>"
            "<span class='seg tail' style='left:{w1:.1f}%;width:{wt:.1f}%'></span>"
            "<i class='bfmark' style='left:{b:.1f}%' title='Black Friday {bf}'></i><i class='now' style='left:{n:.1f}%'><b>i dag</b></i></div>"
            "<div class='tlx'><span style='left:0'>{s}</span><span class='wk' style='left:{w1:.1f}%'>"
            "Black Week {w}–{we}</span></div><p class='sub'>{text}</p></section>").format(
        head=head, s=nice_day(ph["start"]), w=nice_day(ph["week_start"]), bf=nice_day(ph["bf"]),
        t=nice_day(today), we=nice_day(ph["week_end"]), w0=week0, ww=week1 - week0, w1=week1, wt=100 - week1,
        b=at(ph["bf"]) + 50 / total, n=at(today) + 50 / total, text=text)


def _summary(findings, shops, ph):
    e = html.escape
    n = {rule: sum(1 for f in findings if f.rule == rule) for rule, _, _ in SECTIONS}
    titles = {rule: title for rule, title, _ in sections(ph["phase"])}
    if not findings:
        return ("<section class='verdict calm'><b>Ingen mistenkelige priser funnet ennå.</b> "
                "Verktøyet bygger prishistorikk hver dag, og flere funn kommer nærmere Black Friday "
                "({}).</section>").format(nice_day(ph["bf"]))
    worst = ", ".join("<button type='button' class='link' data-shop='{0}'>{0}</button>".format(e(r["shop"]))
                      for r in list(shops)[:3])
    if ph["phase"] == "warmup":
        # before Black Week the warning sign is a raised price; a "discount" now is not a Black Friday one
        head = ("<b>{} priser er satt opp før Black Friday</b>".format(n["hike"]) if n["hike"]
                else "<b>Ingen priser satt opp før Black Friday ennå</b>")
        extra = ["{} {}".format(n[r], titles[r].lower()) for r in ("fake_discount", "before_price", "deal") if n[r]]
        cls = " warn" if n["hike"] or n["before_price"] else " calm"
    else:
        bad = n["fake_discount"] + n["before_price"]
        shopset = {(f.source, f.shop) for f in findings
                   if f.rule in ("fake_discount", "before_price") and not f.shop.startswith("(")}
        head = ("<b>{} mistenkelige tilbud</b> hos {} butikker".format(bad, len(shopset)) if bad
                else "<b>Ingen falske rabatter funnet</b>")
        extra = []
        if n["hike"]:
            extra.append("{} prishopp å følge med på".format(n["hike"]))
        if n["deal"]:
            extra.append("{} ekte tilbud".format(n["deal"]))
        cls = "" if bad else " calm"
    return "<section class='verdict{}'>{}{}{}</section>".format(
        cls, head, "".join(" · " + x for x in extra), "<br>Verst: " + worst if worst else "")


def _shops_table(shops):
    e = html.escape
    if not shops:
        return ""
    rows = "".join(
        "<tr><td><button type='button' class='link' data-shop='{0}'>{0}</button></td><td>{1}</td>"
        "<td class='num'>{2} av {3}</td><td>{4}</td></tr>".format(
            e(r["shop"]), e(r["source"].capitalize()), r["flagged"], r["listed"],
            e(", ".join(p for p in (
                "{} falsk rabatt".format(r["fake_discount"]) if r["fake_discount"] else "",
                "{} villedende før-pris".format(r["before_price"]) if r["before_price"] else "",
                "{} prishopp".format(r["hike"]) if r["hike"] else "") if p)))
        for r in list(shops)[:20])
    return ("<details class='sec'><summary><h2>{}</h2> <span class='n'>{}</span></summary>"
            "<p class='why'>Andel av produktene vi følger hos butikken som har fått et mistenkelig flagg. "
            "Trykk på et navn for å vise bare den butikken.</p><table><caption class='sr'>{}</caption>"
            "<tr><th scope='col'>Butikk</th><th scope='col'>Kilde</th><th scope='col'>Mistenkelige</th>"
            "<th scope='col'>Hva</th></tr>{}</table></details>").format(
        e(SHOPS_TITLE), len(shops), e(SHOPS_TITLE), rows)


def _filters(findings):
    e = html.escape
    cats = sorted({f.category or "" for f in findings}, key=category_name)
    shops = sorted({shop_name(f) for f in findings}, key=str.lower)
    if not findings:
        return ""
    opt = lambda v, label: "<option value='{}'>{}</option>".format(e(v), e(label))  # noqa: E731
    return ("<form class='filters' role='search' onsubmit='return false'>"
            "<label>Kategori <select id='fcat'><option value=''>Alle</option>{}</select></label>"
            "<label>Butikk <select id='fshop'><option value=''>Alle</option>{}</select></label>"
            "<label>Søk <input id='fq' type='search' placeholder='f.eks. Samsung'></label>"
            "<button type='button' id='freset'>Nullstill</button></form>").format(
        "".join(opt(c, category_name(c)) for c in cats), "".join(opt(s, s) for s in shops))


SCRIPT = """<script>
(function(){var c=document.getElementById('fcat');if(!c)return;
var s=document.getElementById('fshop'),q=document.getElementById('fq'),SHOW=10;
function run(){var on=c.value||s.value||q.value;
document.querySelectorAll('.sec[data-rule]').forEach(function(sec){var i=0,all=sec.classList.contains('all');
sec.querySelectorAll('.card').forEach(function(r){var ok=(!c.value||r.dataset.cat===c.value)&&
(!s.value||r.dataset.shop===s.value)&&(!q.value||r.dataset.q.indexOf(q.value.toLowerCase())>=0);
r.hidden=!ok||(!on&&!all&&i>=SHOW);if(ok)i++;});
sec.querySelector('.n').textContent=i;var m=sec.querySelector('.more');
if(m)m.hidden=on||all||i<=SHOW;if(on&&i)sec.open=true;});}
[c,s].forEach(function(x){x.onchange=run;});q.oninput=run;
document.getElementById('freset').onclick=function(){c.value=s.value=q.value='';run();};
document.querySelectorAll('.more').forEach(function(b){b.onclick=function(){
b.closest('.sec').classList.add('all');run();};});
document.querySelectorAll('button.link[data-shop]').forEach(function(b){b.onclick=function(){
s.value=b.dataset.shop;run();document.getElementById('funn').scrollIntoView();};});
run();})();
</script>"""


def _howto(ph):
    lines = ["<li><b>{}</b>: {}</li>".format(html.escape(t), html.escape(w)) for _, t, w in sections(ph["phase"])]
    lines.append("<li>Normalprisen er hva butikken tok for 1–3 måneder siden.</li>" if ph["phase"] == "off" else
                 "<li>Normalprisen er hva butikken vanligvis tok, målt før sesongen startet 1. oktober.</li>")
    return "\n".join(lines)


def html_page(findings, stats, files, running, shops=(), top=50, today=None):
    e = html.escape
    shops = list(shops)
    today = today or stats.get("last_day") or net.today_oslo()
    ph = analyze.bf_phase(today)
    secs = []
    for i, (rule, title, why) in enumerate(sections(ph["phase"])):
        rows = [f for f in findings if f.rule == rule][:top]
        if not rows:
            secs.append("<p class='none'><b>{}:</b> ingen funnet.</p>".format(e(title)))
            continue
        secs.append("<details class='sec' data-rule='{}'{}><summary><h2>{}</h2> <span class='n'>{}</span>"
                    "</summary><p class='why'>{}</p>{}<button type='button' class='more'>Vis alle {}</button>"
                    "</details>".format(rule, " open" if i == 0 else "", e(title), len(rows),
                                         e(why), "".join(_card(f, ph["bf"]) for f in rows), len(rows)))
    file_links = "".join("<li><a href='files/{0}'>{0}</a></li>".format(e(n)) for n in files)
    return """<!doctype html><html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Black Friday-prisvakt</title>
<style>:root{{--bg:#fff;--fg:#1a1a1a;--muted:#595959;--rule:#ddd;--card:#f7f7f8;--link:#0645ad;--line:#2b6cb0;
--ref:#888;--claim:#c53030;--peak:#c05621;--now:#2f855a;--bad:#fde8e8;--badfg:#9b1c1c;--ok:#e6f4ea;--okfg:#1e6b34;--warn:#fff4e0;--warnfg:#7a4100}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#e6e6e6;--muted:#b0b0b0;--rule:#333;--card:#1d1d1f;
--link:#8ab4ff;--line:#63b3ed;--ref:#999;--claim:#fc8181;--peak:#f6ad55;--now:#68d391;--bad:#3b1d1d;--badfg:#ffb4b4;
--ok:#1c3324;--okfg:#9ae6b4;--warn:#3a2a12;--warnfg:#fbd38d}}}}
*{{box-sizing:border-box}}body{{font:16px/1.45 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1000px;
color:var(--fg);background:var(--bg)}}a{{color:var(--link)}}h1{{margin:0 0 4px;font-size:24px}}
h2{{display:inline;font-size:19px;margin:0}}small,.why,.sub{{color:var(--muted)}}.sub{{margin:0 0 12px}}
.verdict{{background:var(--bad);color:var(--badfg);padding:12px 14px;border-radius:8px;margin:12px 0}}
.verdict.calm{{background:var(--ok);color:var(--okfg)}}.verdict.warn{{background:var(--warn);color:var(--warnfg)}}.bf{{margin:12px 0}}.bfhead{{font-size:18px;margin:0 0 8px}}.tl{{position:relative;height:26px;border-radius:6px;background:var(--card);overflow:visible}}.seg{{position:absolute;top:0;bottom:0;font-size:13px;line-height:26px;padding-left:6px;white-space:nowrap;overflow:hidden}}.seg.warm{{left:0;background:var(--warn);color:var(--warnfg);border-radius:6px 0 0 6px}}.seg.week{{background:var(--fg);color:var(--bg);font-weight:600}}.seg.tail{{background:var(--rule);border-radius:0 6px 6px 0}}.tl i{{position:absolute;top:-4px;bottom:-4px;width:0;border-left:3px solid}}.tl .bfmark{{border-color:var(--claim)}}.tl .now{{border-color:var(--line)}}.tl .now b{{position:absolute;top:-20px;left:0;transform:translateX(-50%);white-space:nowrap;font:600 12px system-ui;color:var(--line);font-style:normal}}.tlx{{position:relative;height:18px;font-size:12px;color:var(--muted);margin:2px 0 4px}}.tlx span{{position:absolute;transform:translateX(-50%);white-space:nowrap}}.tlx span:first-child{{transform:none}}.tlx .wk{{transform:translateX(-100%);font-weight:600;color:var(--fg)}}.tl{{margin-top:20px}}.verdict .link{{color:inherit;font-weight:600}}
.how{{margin:12px 0}}.how ul{{margin:6px 0;padding-left:20px}}
.filters{{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:end;margin:16px 0;padding:10px;
border:1px solid var(--rule);border-radius:8px}}.filters label{{display:flex;flex-direction:column;font-size:14px}}
select,input,button{{font:inherit;padding:6px 8px;border:1px solid var(--rule);border-radius:6px;
background:var(--bg);color:var(--fg)}}button{{cursor:pointer}}
button.link{{border:0;padding:0;background:none;color:var(--link);text-decoration:underline}}
.sec{{margin:14px 0;border-top:1px solid var(--rule);padding-top:10px}}summary{{cursor:pointer}}
.n{{display:inline-block;min-width:26px;text-align:center;padding:1px 8px;border-radius:12px;background:var(--card);
font-weight:600}}.none{{color:var(--muted);margin:8px 0}}
.card{{display:grid;grid-template-columns:1fr auto 240px;gap:6px 14px;align-items:start;background:var(--card);
border-radius:8px;padding:10px 12px;margin:8px 0}}.card p{{margin:4px 0}}.t{{font-weight:600}}
.shop{{font-weight:600}}.badge{{font-size:20px;font-weight:700;white-space:nowrap;color:var(--badfg)}}
.deal .badge{{color:var(--okfg)}}.more{{margin:4px 0}}
table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{border-bottom:1px solid var(--rule);
padding:6px;text-align:left;vertical-align:top}}.num{{white-space:nowrap}}.sr{{position:absolute;left:-9999px}}
.ch .ln{{fill:none;stroke:var(--line);stroke-width:1.5}}.ch .ref{{stroke:var(--ref);stroke-dasharray:3 3}}
.ch .claim{{stroke:var(--claim);stroke-dasharray:5 2}}.ch .pk{{fill:var(--peak)}}.ch .now{{fill:var(--now)}}
.ch{{width:100%;max-width:240px;height:auto;display:block}}.key{{font-size:14px;color:var(--muted)}}
.key span{{margin-right:12px;white-space:nowrap}}footer{{margin-top:28px;border-top:1px solid var(--rule);
padding-top:10px;font-size:15px}}
@media (max-width:640px){{.card{{grid-template-columns:1fr auto}}.chart{{grid-column:1/-1}}
.ch{{max-width:none}}table{{font-size:14px}}}}</style>
</head><body>
<h1>Black Friday-prisvakt</h1>
<p class="sub">{products} produkter fulgt · priser fra {first} til {last}{status}</p>
{timeline}{verdict}
<details class="how"><summary>Slik leser du siden</summary><ul>
{howto}
<li>Rapporten viser bare prisdata. Sjekk alltid selv før du konkluderer.</li></ul>
<p class="key"><span style="color:var(--line)">━ pris</span><span style="color:var(--ref)">┅ normalpris</span>
<span style="color:var(--claim)">┅ oppgitt før-pris</span><span style="color:var(--peak)">● høyeste</span>
<span style="color:var(--now)">● nå</span></p></details>
{filters}{shops}<div id="funn">{body}</div>
<footer><p>Prisene hentes automatisk hver morgen. Sist oppdatert: {last}.</p>
<form method="post" action="run"><button {disabled}>{btn}</button></form>
<p>Last ned: </p><ul>{files}</ul></footer>{script}</body></html>""".format(
        products=e(str(stats.get("products", 0))), first=e(nice_day(stats.get("first_day")) or "–"),
        last=e(nice_day(stats.get("last_day")) or "–"),
        status=" · <b>henter priser nå …</b>" if running else "",
        verdict=_summary(findings, shops, ph), timeline=_timeline(ph, today), howto=_howto(ph), filters=_filters(findings), shops=_shops_table(shops),
        body="".join(secs), disabled="disabled" if running else "",
        btn="Henter …" if running else "Hent priser nå", files=file_links, script=SCRIPT)
