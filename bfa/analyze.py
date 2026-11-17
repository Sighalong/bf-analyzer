"""Gouging detection over per-shop daily price series.

Which rows count: only in-stock offers, and not offers priced more than OUTLIER x the
median in-stock price for that product that day. A stale price on a sold-out listing, or
a mislisted bundle, would otherwise look like a price hike.

Baseline (the "normal" price) depends on the time of year:
  season (1 Oct .. 2 weeks after Black Friday): the 60 days before the season start, so a
      shop that raises its price early (October) can't push the raised price into its own
      baseline. A series with too little data before the season start falls back to its
      earliest FALLBACK_DAYS days before 1 November, and the finding says so ("tidlig").
  rest of the year: rolling, days 31..90 back.
Recent = every day after the baseline, at most 60 days back.

Rules (normal = baseline median, low = baseline LOW_QUANTILE, so a one-day dip doesn't count).
Reader-facing names and definitions live in report.TYPES; keep them in step with these:
  hike            peak(recent) >= normal * (1 + HIKE), held for >= MIN_RAISED_DAYS days, and the
                  price is still raised: now >= normal * (1 + HIKE)
  fake_discount   same hike, then dropped >= DROP from the peak to about normal (not more than DEAL
                  below it): the "discount" only undoes the hike. A drop well below normal is a real
                  cut and is left to "deal"
  before_price    the shop's stated før-pris is above its own lowest price in the previous 30 days
                  (the reference the EU price-indication rule uses)
  deal            now is >= DEAL below the baseline low: a real price cut

The site's cheapest-across-shops history (pseudo-shop "(laveste)") jumps whenever the cheapest
shop sells out or drops off the listing, so hike / fake_discount there need more evidence: the
per-shop market median must have risen >= HIKE too (once it covers the baseline), and without
that data the peak may be at most LOWEST_MAX_JUMP x the baseline median.
"""
import math
from datetime import datetime, timedelta

HIKE = 0.10
DROP = 0.08
LOW_QUANTILE = 0.1         # "baseline low" = this quantile of the baseline, robust to one-off dips
MIN_RAISED_DAYS = 2         # a hike must hold this many recent days; one-day spikes are noise
DEAL = 0.05
OUTLIER = 2.0
MIN_BASELINE_DAYS = 7
MIN_RECENT_DAYS = 3
FALLBACK_DAYS = 14
SEASON_START = (10, 1)      # month, day
SEASON_TAIL_DAYS = 14       # season ends this long after Black Friday (covers Cyber Monday)
WAKE = (8, 1)               # daily collection wakes here: the 1 Oct baseline needs August-September prices
SLEEP = (1, 1)              # ... and sleeps from New Year, after the Christmas and romjul sales
CHART_DAYS = 90
# Phone operators: their phone prices on the comparison sites usually need a plan ("med 12 md.
# rabattavtale", "Telenorkunde"), so they are no price anyone can pay. Matched on the start of the
# shop name, ignoring case ("Telenor.no" too). Chilimobil sells at normal prices and stays.
PLAN_SHOPS = ("telia", "telenor", "talkmore", "ice", "onecall", "mycall")


def plan_shop(name):
    n = (name or "").strip().lower()
    return any(n == p or n.startswith(p + ".") or n.startswith(p + " ") for p in PLAN_SHOPS)
LOWEST_MAX_JUMP = 1.6       # cheapest-price series: a bigger jump is a shop dropping out, not a hike


class Finding(object):
    __slots__ = ("rule", "source", "product_id", "title", "url", "category", "shop",
                 "base_median", "base_low", "peak", "peak_day", "now", "claimed",
                 "low30", "days", "score", "baseline", "series", "market")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__ if k not in ("series", "market")}


def _quantile(xs, q):
    """Low quantile by rank (no interpolation): q=0.1 of 20 values is the 3rd lowest."""
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


_DAYS = {}


def _day(s):
    # strptime costs ~1 ms on the Pi and runs ~400 000 times per analysis; there are only a few hundred days
    d = _DAYS.get(s)
    if d is None:
        d = _DAYS[s] = datetime.strptime(s, "%Y-%m-%d")
    return d


def _fmt(d):
    return d.strftime("%Y-%m-%d")


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def black_friday(year):
    """The day after the fourth Thursday of November (US Thanksgiving)."""
    d = datetime(year, 11, 1)
    first_thursday = d + timedelta(days=(3 - d.weekday()) % 7)
    return _fmt(first_thursday + timedelta(weeks=3, days=1))


BLACK_WEEK = (-4, 3)        # Monday before Black Friday .. Cyber Monday, in days from Black Friday


def bf_phase(today):
    """Where today sits relative to Black Friday.

    Returns dict(phase, bf, days, start, week_start, week_end, end, wake), days = days until Black Friday
    (negative after), wake = asleep(today). phase: 'warmup' (1 Oct .. before Black Week), 'black_week', 'after' (rest of
    the season) or 'off' (rest of the year; bf is then the next Black Friday).
    """
    t = _day(today)
    bf = _day(black_friday(t.year))
    if t > bf + timedelta(days=SEASON_TAIL_DAYS):
        bf = _day(black_friday(t.year + 1))
    start = datetime(bf.year, *SEASON_START)
    week_start, week_end = bf + timedelta(days=BLACK_WEEK[0]), bf + timedelta(days=BLACK_WEEK[1])
    end = bf + timedelta(days=SEASON_TAIL_DAYS)
    if t < start:
        phase = "off"
    elif t < week_start:
        phase = "warmup"
    elif t <= week_end:
        phase = "black_week"
    else:
        phase = "after"
    return dict(phase=phase, bf=_fmt(bf), days=(bf - t).days, start=_fmt(start),
                week_start=_fmt(week_start), week_end=_fmt(week_end), end=_fmt(end), wake=asleep(today))


STALE_DAYS = 2   # until today's prices arrive, keep showing the latest collected day if it is at most this old


def data_day(today, last_day):
    """Day to analyse: today, or the latest collected day while today's prices are not in yet (up to STALE_DAYS old)."""
    if last_day and last_day < today and (_day(today) - _day(last_day)).days <= STALE_DAYS:
        return last_day
    return today


def asleep(today):
    """Next wake-up day (YYYY-MM-DD) while collection sleeps (New Year to 1 August), else None."""
    t = _day(today)
    wake = datetime(t.year, *WAKE)
    return _fmt(wake) if datetime(t.year, *SLEEP) <= t < wake else None


def season_baseline_end(today, override=None):
    """Day the baseline ends (exclusive), or None for rolling windows.

    override: 'auto' / None = season rules, 'rolling' = always rolling, or a YYYY-MM-DD date.
    """
    if override and override not in ("auto", "rolling"):
        _day(override)  # validate
        return override
    if override == "rolling":
        return None
    t = _day(today)
    start = datetime(t.year, *SEASON_START)
    end = _day(black_friday(t.year)) + timedelta(days=SEASON_TAIL_DAYS)
    return _fmt(start) if start <= t <= end else None


def clean_rows(rows, market=None):
    """Keep in-stock rows that aren't outliers against the day's market median."""
    out = []
    for day, price, orig, in_stock in rows:
        if not in_stock:
            continue
        m = market.get(day) if market else None
        if m and price > m * OUTLIER:
            continue
        out.append((day, price, orig))
    return out


def _windows(rows, t, baseline_end):
    """Split cleaned rows into (baseline rows, recent rows, label)."""
    if baseline_end is None:
        base = [r for r in rows if 31 <= (t - _day(r[0])).days <= 90]
        recent = [r for r in rows if 0 <= (t - _day(r[0])).days <= 30]
        return base, recent, "dag 31–90"
    be = _day(baseline_end)
    base_rows = [r for r in rows if be - timedelta(days=60) <= _day(r[0]) < be]
    label = "før " + baseline_end
    if len(base_rows) < MIN_BASELINE_DAYS:
        cutoff = datetime(be.year, 11, 1)
        base_rows = [r for r in rows if _day(r[0]) < cutoff][:FALLBACK_DAYS]
        if base_rows:
            label = "tidlig {}–{}".format(base_rows[0][0][5:], base_rows[-1][0][5:])
    last_base = _day(base_rows[-1][0]) if base_rows else be - timedelta(days=1)
    recent = [r for r in rows if _day(r[0]) > last_base and 0 <= (t - _day(r[0])).days <= 60]
    return base_rows, recent, label


def _lowest_hike_ok(base_rows, peak_day, peak, base_med, confirm):
    """Is a hike in the cheapest-price series real rather than the cheapest shop dropping out?

    confirm: {day: per-shop market median}. Once it covers the baseline and the peak day, the
    market must have risen as well; until then only jumps up to LOWEST_MAX_JUMP x pass.
    """
    if confirm:
        base_m = [confirm[d] for d, _, _ in base_rows if d in confirm]
        if len(base_m) >= MIN_BASELINE_DAYS and peak_day in confirm:
            return confirm[peak_day] >= _median(base_m) * (1 + HIKE)
    return peak <= base_med * LOWEST_MAX_JUMP


def analyze_series(rows, today, market=None, baseline_end=None, lowest=False, confirm=None):
    """rows: [(day, price, original_price, in_stock)] sorted by day. Returns [(rule, fields)].

    lowest: rows are the cheapest-across-shops history; confirm is then {day: per-shop market
    median} used to tell real hikes from the cheapest shop dropping out (see _lowest_hike_ok).
    """
    t = _day(today)
    rows = clean_rows(rows, market)
    if not rows or rows[-1][0] != today:
        return []          # not listed (in stock) today: nothing to say about the current price
    now_day, now, claimed = rows[-1]
    base_rows, recent, label = _windows(rows, t, baseline_end)
    base = [p for _, p, _ in base_rows]
    out = []

    if len(base) >= MIN_BASELINE_DAYS and len(recent) >= MIN_RECENT_DAYS:
        base_med, base_low = _median(base), _quantile(base, LOW_QUANTILE)
        raised = base_med * (1 + HIKE)
        peak_day, peak = max(((d, p) for d, p, _ in recent), key=lambda x: x[1])
        info = dict(base_median=base_med, base_low=base_low, peak=peak, peak_day=peak_day, now=now,
                    days=len(base) + len(recent), baseline=label)
        hike = peak / base_med - 1 if base_med else 0
        held = sum(1 for _, p, _ in recent if p >= raised) >= MIN_RAISED_DAYS
        if hike >= HIKE and held and (not lowest or _lowest_hike_ok(base_rows, peak_day, peak, base_med, confirm)):
            if now >= raised:
                out.append(("hike", dict(info, score=now / base_med - 1)))
            elif now <= peak * (1 - DROP) and now >= base_med * (1 - DEAL):
                out.append(("fake_discount", dict(info, score=hike)))
        if base_low and now <= base_low * (1 - DEAL):
            out.append(("deal", dict(info, score=1 - now / base_low)))

    if claimed:
        # reference = the 30 days before the current price started, not including the sale itself
        i = len(rows) - 1
        while i > 0 and abs(rows[i - 1][1] - now) < 1:
            i -= 1
        start = _day(rows[i][0])
        prior = [p for d, p, _ in rows[:i] if 1 <= (start - _day(d)).days <= 30]
        if len(prior) >= 14:
            low30 = min(prior)
            if claimed > low30 * 1.01:
                out.append(("before_price", dict(now=now, claimed=claimed, low30=low30, days=len(prior),
                                                 baseline="30 dager", score=claimed / low30 - 1)))
    chart = [(d, p) for d, p, _ in rows if (t - _day(d)).days <= CHART_DAYS]
    return [(rule, dict(f, series=chart)) for rule, f in out]


def _market(series):
    """{day: median in-stock shop price} over the per-shop series (not the site's cheapest)."""
    by_day = {}
    for shop, rows in series.items():
        if shop.startswith("("):
            continue
        for day, price, _, in_stock in rows:
            if in_stock:
                by_day.setdefault(day, []).append(price)
    return {day: _median(prices) for day, prices in by_day.items()}


def analyze(store, today, baseline_end="auto"):
    """Findings for `today`, worst first.

    Per-shop series from phone operators (plan prices) are skipped, and for a product the parser marked
    as having variants, per-shop rows from before that day are ignored (they held a random variant).
    """
    be = season_baseline_end(today, baseline_end)
    clean = store.clean_since()
    findings = []
    for source, pid, title, url, category, shops in store.iter_products(today):
        since = clean.get((source, pid))
        series = {}
        for shop, rows in shops.items():
            if not shop.startswith("("):
                if plan_shop(shop):
                    continue
                if since:
                    rows = [r for r in rows if r[0] >= since]
            series[shop] = rows
        mk = _market(series)
        for shop, rows in series.items():
            # the site's own cheapest-price history is already a market figure: no outlier check
            lowest = shop.startswith("(")
            for rule, f in analyze_series(rows, today, None if lowest else mk, be,
                                          lowest=lowest, confirm=mk if lowest else None):
                market = [(d, mk[d]) for d, _ in f["series"] if d in mk]   # for the chart's market line
                findings.append(Finding(rule=rule, source=source, product_id=pid, title=title, url=url,
                                        category=category, shop=shop, market=market, **f))
    findings.sort(key=lambda f: -f.score)
    return findings


BAD_RULES = ("fake_discount", "before_price", "hike")


def rank_shops(findings, listed):
    """Shops by share of their listed products that got a gouging flag.

    listed: {(source, shop): products in stock today} from Store.shop_product_counts.
    """
    per = {}
    for f in findings:
        if f.rule not in BAD_RULES or f.shop.startswith("("):
            continue
        s = per.setdefault((f.source, f.shop), {"products": set(), "fake_discount": 0,
                                                "before_price": 0, "hike": 0})
        s["products"].add(f.product_id)
        s[f.rule] += 1
    out = []
    for (source, shop), s in per.items():
        n = max(listed.get((source, shop), 0), len(s["products"]))
        out.append({"source": source, "shop": shop, "flagged": len(s["products"]), "listed": n,
                    "share": len(s["products"]) / float(n), "fake_discount": s["fake_discount"],
                    "before_price": s["before_price"], "hike": s["hike"]})
    # weigh the share by evidence so 1 of 1 doesn't beat 8 of 20
    out.sort(key=lambda r: (-(r["flagged"] * r["share"]), -r["flagged"], r["shop"]))
    return out


# ---- one entry per product, and the top lists ------------------------------

TRICK_WEIGHT = {"fake_discount": 1.0, "before_price": 0.9, "hike": 0.7}
BUY_MIN = 0.10          # a best buy is at least this far below the usual low price
BUY_FULL_DAYS = 30      # history needed for full confidence in a best buy


def group_findings(findings):
    """One group per product (source, product_id), worst finding first, groups in findings order.

    {"source", "product_id", "title", "url", "category", "findings": [Finding]}.
    """
    groups, order = {}, []
    for f in findings:
        k = (f.source, f.product_id)
        if k not in groups:
            groups[k] = {"source": f.source, "product_id": f.product_id, "title": f.title, "url": f.url,
                         "category": f.category, "findings": []}
            order.append(k)
        groups[k]["findings"].append(f)
    return [groups[k] for k in order]


def popularity_score(pop, source, product_id):
    """0..1 from Store.popularity: shops selling it (log, against the most-listed product of the source)
    and, when known, its place in the site's category listing."""
    n, rank = pop.get((source, product_id), (0, None))
    top = max([v[0] for k, v in pop.items() if k[0] == source] or [1])
    shops = math.log(1 + n) / math.log(1 + max(top, 1))
    return shops if rank is None else 0.6 * shops + 0.4 * rank


def _size(score):
    """Ranking weight of a deviation: grows to 100 %, then falls again (bigger jumps are more often a
    data error, such as a wrong model on the shop's listing, than a real trick). Never below 0.5."""
    s = score or 0
    return s if s <= 1 else max(0.5, 2 - s)


def rank_products(groups, pop, prices=None, strong=None):
    """Adds "pop", "trick" and "buy" (0 = not a candidate) and "trick_f"/"buy_f" (the finding behind it)
    to each group.

    pop: Store.popularity(day). prices: Store.shop_prices(day, keys), to check a best buy is the cheapest.
    strong(f): True when a finding is at least "Tydelig" (report.level >= 2); default all.
    trick = type weight x _size(score) x (0.5 + 0.5 pop) x spread (+10 % per extra shop, max 1.5).
    buy = below the usual low x (0.5 + 0.5 pop) x confidence (days of history / 30, halved when another
    shop on the same product has a trick finding).
    """
    strong = strong or (lambda f: True)
    for g in groups:
        p = popularity_score(pop or {}, g["source"], g["product_id"])
        g["pop"], g["trick"], g["buy"], g["trick_f"], g["buy_f"] = p, 0.0, 0.0, None, None
        bad = [f for f in g["findings"] if f.rule in TRICK_WEIGHT]
        tricks = [f for f in bad if strong(f)]
        if tricks:
            f = max(tricks, key=lambda x: TRICK_WEIGHT[x.rule] * _size(x.score))
            shops = len({x.shop for x in bad})
            spread = min(1.5, 1 + 0.1 * (shops - 1))
            g["trick"] = TRICK_WEIGHT[f.rule] * _size(f.score) * (0.5 + 0.5 * p) * spread
            g["trick_f"] = f
        deals = [f for f in g["findings"] if f.rule == "deal" and not f.shop.startswith("(")
                 and (f.score or 0) >= BUY_MIN]
        if deals:
            f = min(deals, key=lambda x: (x.now, -(x.score or 0)))
            offers = [o for o in (prices or {}).get((g["source"], g["product_id"]), []) if o[3] and not plan_shop(o[0])]
            if offers and min(o[1] for o in offers) < f.now - 0.5:
                continue        # another shop is cheaper today: not the best buy for this product
            conf = min(1.0, (f.days or 0) / float(BUY_FULL_DAYS))
            if bad:
                conf *= 0.5
            g["buy"] = (f.score or 0) * (0.5 + 0.5 * p) * conf
            g["buy_f"] = f
    return groups


def top(groups, key, n=5):
    return sorted([g for g in groups if g[key] > 0], key=lambda g: -g[key])[:n]
