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

Rules:
  hike            peak(recent) >= baseline median * (1 + HIKE)   and the price is still near that peak
  fake_discount   same hike, then dropped >= DROP from the peak, but now is not meaningfully below
                  the baseline low: the "discount" mostly undoes the hike
  before_price    the shop's stated før-pris is above its own lowest price in the previous 30 days
                  (the reference the EU price-indication rule uses)
  deal            now is >= DEAL below the baseline low: a real price cut
"""
from datetime import datetime, timedelta

HIKE = 0.10
DROP = 0.08
NEAR_BASELINE = 0.03
DEAL = 0.05
OUTLIER = 2.0
MIN_BASELINE_DAYS = 7
MIN_RECENT_DAYS = 3
FALLBACK_DAYS = 14
SEASON_START = (10, 1)      # month, day
SEASON_TAIL_DAYS = 14       # season ends this long after Black Friday (covers Cyber Monday)
CHART_DAYS = 90


class Finding(object):
    __slots__ = ("rule", "source", "product_id", "title", "url", "category", "shop",
                 "base_median", "base_low", "peak", "peak_day", "now", "claimed",
                 "low30", "days", "score", "baseline", "series")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__ if k != "series"}


def _day(s):
    return datetime.strptime(s, "%Y-%m-%d")


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
    """Split cleaned rows into (baseline prices, recent rows, label)."""
    if baseline_end is None:
        base = [p for d, p, _ in rows if 31 <= (t - _day(d)).days <= 90]
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
    return [p for _, p, _ in base_rows], recent, label


def analyze_series(rows, today, market=None, baseline_end=None):
    """rows: [(day, price, original_price, in_stock)] sorted by day. Returns [(rule, fields)]."""
    t = _day(today)
    rows = clean_rows(rows, market)
    if not rows or rows[-1][0] != today:
        return []          # not listed (in stock) today: nothing to say about the current price
    now_day, now, claimed = rows[-1]
    base, recent, label = _windows(rows, t, baseline_end)
    out = []

    if len(base) >= MIN_BASELINE_DAYS and len(recent) >= MIN_RECENT_DAYS:
        base_med, base_low = _median(base), min(base)
        peak_day, peak = max(((d, p) for d, p, _ in recent), key=lambda x: x[1])
        info = dict(base_median=base_med, base_low=base_low, peak=peak, peak_day=peak_day, now=now,
                    days=len(base) + len(recent), baseline=label)
        hike = peak / base_med - 1 if base_med else 0
        if hike >= HIKE:
            if now <= peak * (1 - DROP) and now >= base_low * (1 - NEAR_BASELINE):
                out.append(("fake_discount", dict(info, score=hike)))
            elif now >= peak * (1 - DROP):
                out.append(("hike", dict(info, score=now / base_med - 1)))
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


def analyze(store, today, baseline_end="auto"):
    be = season_baseline_end(today, baseline_end)
    market = {}
    for (source, pid, day), m in store.market_medians().items():
        market.setdefault((source, pid), {})[day] = m
    findings = []
    for source, pid, shop, title, url, category in store.series_keys():
        rows = store.series(source, pid, shop)
        # the site's own cheapest-price history is already a market figure: no outlier check
        mk = None if shop.startswith("(") else market.get((source, pid))
        for rule, f in analyze_series(rows, today, mk, be):
            findings.append(Finding(rule=rule, source=source, product_id=pid, title=title, url=url,
                                    category=category, shop=shop, **f))
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
