"""SQLite storage: one price per (source, product, shop, day). Builds per-shop history over time."""
import os
import sqlite3
from datetime import datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    source TEXT NOT NULL, product_id TEXT NOT NULL,
    title TEXT, url TEXT, category TEXT,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (source, product_id)
);
CREATE TABLE IF NOT EXISTS prices (
    source TEXT NOT NULL, product_id TEXT NOT NULL, shop TEXT NOT NULL, day TEXT NOT NULL,
    price REAL NOT NULL, original_price REAL, in_stock INTEGER,
    PRIMARY KEY (source, product_id, shop, day)
);
CREATE INDEX IF NOT EXISTS prices_by_day ON prices (day);
CREATE TABLE IF NOT EXISTS flags (
    rule TEXT NOT NULL, source TEXT NOT NULL, product_id TEXT NOT NULL, shop TEXT NOT NULL,
    first_day TEXT NOT NULL, last_day TEXT NOT NULL,
    PRIMARY KEY (rule, source, product_id, shop)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def default_path():
    return os.environ.get("BFA_DB", os.path.join(os.environ.get("OUTPUT_DIR", "outputs"), "bfa.sqlite"))


class Store(object):
    def __init__(self, path=None):
        self.path = path or default_path()
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")   # fewer SD-card rewrites, readers don't block the collector

    def close(self):
        self.db.close()

    def save_product(self, p, day, category=None, lowest_shop=None):
        """Save one parsed product: today's offers, plus the site's own lowest-price history if any."""
        c = self.db
        c.execute(
            "INSERT INTO products (source, product_id, title, url, category, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(source, product_id) DO UPDATE SET title=excluded.title, url=excluded.url, "
            "category=COALESCE(excluded.category, products.category), last_seen=excluded.last_seen",
            (p["source"], p["product_id"], p["title"], p["url"], category, day, day))
        rows = [(p["source"], p["product_id"], o["shop"], day, o["price"], o["original_price"],
                 1 if o["in_stock"] else 0) for o in p["offers"]]
        if lowest_shop:
            for hday, price in _daily_steps(p["lowest_history"], day):
                rows.append((p["source"], p["product_id"], lowest_shop, hday, price, None, 1))
        c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
        c.commit()

    def tracked(self, source, since_days=45, today=None):
        """Products seen recently, least recently refreshed first."""
        cutoff = _shift(today, -since_days)
        cur = self.db.execute(
            "SELECT product_id, url, category FROM products WHERE source=? AND last_seen>=? "
            "ORDER BY last_seen, first_seen", (source, cutoff))
        return cur.fetchall()

    def series_keys(self):
        return self.db.execute(
            "SELECT DISTINCT p.source, p.product_id, p.shop, pr.title, pr.url, pr.category "
            "FROM prices p JOIN products pr USING (source, product_id)").fetchall()

    def series(self, source, product_id, shop):
        return self.db.execute(
            "SELECT day, price, original_price, in_stock FROM prices "
            "WHERE source=? AND product_id=? AND shop=? ORDER BY day",
            (source, product_id, shop)).fetchall()

    def market_medians(self):
        """{(source, product_id, day): median in-stock shop price}, used to drop outlier offers."""
        by_key = {}
        for source, pid, day, price in self.db.execute(
                "SELECT source, product_id, day, price FROM prices WHERE in_stock=1 AND shop NOT LIKE '(%'"):
            by_key.setdefault((source, pid, day), []).append(price)
        out = {}
        for k, prices in by_key.items():
            prices.sort()
            n = len(prices)
            out[k] = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2.0
        return out

    def shop_product_counts(self, day):
        """{(source, shop): number of products the shop listed in stock on `day`}."""
        return {(s, shop): n for s, shop, n in self.db.execute(
            "SELECT source, shop, COUNT(DISTINCT product_id) FROM prices "
            "WHERE day=? AND in_stock=1 AND shop NOT LIKE '(%' GROUP BY source, shop", (day,))}

    def stats(self):
        q = self.db.execute
        return {
            "products": q("SELECT COUNT(*) FROM products").fetchone()[0],
            "price_rows": q("SELECT COUNT(*) FROM prices").fetchone()[0],
            "days_collected": q("SELECT COUNT(DISTINCT last_seen) FROM products").fetchone()[0],
            "first_day": q("SELECT MIN(day) FROM prices").fetchone()[0],
            "last_day": q("SELECT MAX(day) FROM prices").fetchone()[0],
        }

    def _meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _prev_report(self, day):
        """The report day before `day` (a second report on the same day compares with the same earlier one)."""
        last = self._meta("flags_last")
        return self._meta("flags_prev") if last == day else last

    def record_flags(self, findings, day):
        """Remember since when each finding has been flagged without a break (one report per collection).

        A finding that was also in the previous report keeps its first day; otherwise it starts today.
        The first report ever sets flags_since, so findings from before tracking started are not "new".
        """
        last = self._meta("flags_last")
        if last and last > day:
            return                      # a report for an older day (--today) must not rewrite history
        prev = self._prev_report(day)
        c = self.db
        for f in findings:
            key = (f.rule, f.source, f.product_id, f.shop)
            row = c.execute("SELECT first_day, last_day FROM flags WHERE rule=? AND source=? AND product_id=? "
                            "AND shop=?", key).fetchone()
            first = row[0] if row and (row[1] == day or (prev and row[1] >= prev)) else day
            c.execute("INSERT OR REPLACE INTO flags VALUES (?,?,?,?,?,?)", key + (first, day))
        if self._meta("flags_since") is None:
            c.execute("INSERT INTO meta VALUES ('flags_since', ?)", (day,))
        c.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [("flags_prev", prev), ("flags_last", day)])
        c.commit()

    def flag_info(self, day):
        """What the page needs to mark new findings and list the ones that went away since the previous report.

        first: {(rule, source, product_id, shop): first_day} for running streaks (a finding missing here
        starts on `day`); since: day tracking started (None = never);
        prev: previous report day; gone: [(rule, source, product_id, shop, title)] flagged in that report.
        """
        since = self._meta("flags_since")
        if since is None:
            return {"first": {}, "since": None, "prev": None, "gone": []}
        prev = self._prev_report(day)
        # only streaks still running (flagged in the report for `day` or the one before) carry their first day
        first = {tuple(r[:4]): r[4] for r in self.db.execute(
            "SELECT rule, source, product_id, shop, first_day FROM flags WHERE last_day=? OR last_day>=?",
            (day, prev or day))}
        gone = self.db.execute(
            "SELECT f.rule, f.source, f.product_id, f.shop, p.title FROM flags f "
            "LEFT JOIN products p USING (source, product_id) WHERE f.last_day=? ORDER BY p.title",
            (prev,)).fetchall() if prev else []
        return {"first": first, "since": since, "prev": prev, "gone": gone}


def _shift(day, n):
    base = datetime.strptime(day, "%Y-%m-%d") if day else datetime.now()
    return (base + timedelta(days=n)).strftime("%Y-%m-%d")


def _daily_steps(points, until):
    """Change points [(day, price)] -> one price per day up to `until` (price holds until next change)."""
    by_day = {}
    for d, price in points:
        by_day[d] = min(price, by_day.get(d, price))
    if not by_day:
        return []
    out, day, cur = [], min(by_day), None
    while day <= until:
        cur = by_day.get(day, cur)
        out.append((day, cur))
        day = _shift(day, 1)
    return out
