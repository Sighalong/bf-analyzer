import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401  (sys.path)
from bfa import store as store_mod
from bfa.store import Store, _daily_steps


def product(pid="1", source="prisjakt", title="T", offers=None, history=None):
    return {"source": source, "product_id": pid, "title": title, "url": "http://u/" + pid,
            "offers": offers if offers is not None else [],
            "lowest_history": history or [], "lowest_now": None}


def offer(shop, price, orig=None, stock=True):
    return {"shop": shop, "price": price, "original_price": orig, "in_stock": stock}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = Store(os.path.join(self.tmp, "sub", "t.sqlite"))   # parent dir is created
        self.addCleanup(self.s.close)


class SaveProduct(StoreCase):
    def test_round_trip(self):
        p = product(offers=[offer("A", 100.0, 150.0), offer("B", 120.0, None, False)])
        self.s.save_product(p, "2026-10-01", category="tv")
        self.assertEqual(self.s.series("prisjakt", "1", "A"), [("2026-10-01", 100.0, 150.0, 1)])
        self.assertEqual(self.s.series("prisjakt", "1", "B"), [("2026-10-01", 120.0, None, 0)])
        keys = sorted(self.s.series_keys())
        self.assertEqual([k[2] for k in keys], ["A", "B"])
        self.assertEqual(keys[0][3:], ("T", "http://u/1", "tv"))
        st = self.s.stats()
        self.assertEqual((st["products"], st["price_rows"], st["first_day"], st["last_day"], st["days_collected"]),
                         (1, 2, "2026-10-01", "2026-10-01", 1))

    def test_same_day_rerun_replaces_row(self):
        self.s.save_product(product(offers=[offer("A", 100.0)]), "2026-10-01")
        self.s.save_product(product(offers=[offer("A", 90.0, 120.0)]), "2026-10-01")
        self.assertEqual(self.s.series("prisjakt", "1", "A"), [("2026-10-01", 90.0, 120.0, 1)])
        self.assertEqual(self.s.stats()["price_rows"], 1)

    def test_next_day_adds_row_and_keeps_first_seen_and_category(self):
        self.s.save_product(product(title="Old", offers=[offer("A", 100.0)]), "2026-10-01", category="tv")
        self.s.save_product(product(title="New", offers=[offer("A", 95.0)]), "2026-10-02", category=None)
        self.assertEqual([r[0] for r in self.s.series("prisjakt", "1", "A")], ["2026-10-01", "2026-10-02"])
        row = self.s.db.execute("SELECT title, category, first_seen, last_seen FROM products").fetchone()
        self.assertEqual(row, ("New", "tv", "2026-10-01", "2026-10-02"))

    def test_lowest_history_saved_as_pseudo_shop(self):
        p = product(offers=[offer("A", 100.0)],
                    history=[("2026-09-29", 120.0), ("2026-10-01", 110.0)])
        self.s.save_product(p, "2026-10-02", lowest_shop="(laveste)")
        self.assertEqual(self.s.series("prisjakt", "1", "(laveste)"), [
            ("2026-09-29", 120.0, None, 1), ("2026-09-30", 120.0, None, 1),
            ("2026-10-01", 110.0, None, 1), ("2026-10-02", 110.0, None, 1)])

    def test_no_pseudo_shop_without_lowest_shop(self):
        self.s.save_product(product(offers=[], history=[("2026-09-29", 120.0)]), "2026-10-02")
        self.assertEqual(self.s.series_keys(), [])

    def test_history_rerun_overwrites_same_days(self):
        p = product(history=[("2026-10-01", 100.0)])
        self.s.save_product(p, "2026-10-02", lowest_shop="(l)")
        self.s.save_product(p, "2026-10-02", lowest_shop="(l)")
        self.assertEqual(len(self.s.series("prisjakt", "1", "(l)")), 2)


class Tracked(StoreCase):
    def test_ordering_and_cutoff(self):
        self.s.save_product(product("a"), "2026-10-05", category="tv")
        self.s.save_product(product("b"), "2026-10-01", category="mobil")
        self.s.save_product(product("c"), "2026-10-01", category="tv")
        self.s.save_product(product("old"), "2026-08-01")                  # outside 45 days
        self.s.save_product(product("k", source="klarna"), "2026-10-02")
        got = self.s.tracked("prisjakt", today="2026-10-06")
        self.assertEqual([r[0] for r in got], ["b", "c", "a"])             # least recently refreshed first
        self.assertEqual(got[0], ("b", "http://u/b", "mobil"))
        self.assertEqual([r[0] for r in self.s.tracked("klarna", today="2026-10-06")], ["k"])

    def test_since_days(self):
        self.s.save_product(product("a"), "2026-10-05")
        self.s.save_product(product("b"), "2026-10-01")
        self.assertEqual([r[0] for r in self.s.tracked("prisjakt", since_days=3, today="2026-10-06")], ["a"])
        self.assertEqual([r[0] for r in self.s.tracked("prisjakt", since_days=5, today="2026-10-06")], ["b", "a"])


class MarketAndCounts(StoreCase):
    def test_market_medians_odd_even_and_exclusions(self):
        self.s.save_product(product("1", offers=[offer("A", 100.0), offer("B", 300.0), offer("C", 200.0)]), "2026-10-01")
        self.s.save_product(product("1", offers=[offer("A", 100.0), offer("B", 300.0), offer("C", 50.0, None, False)]),
                            "2026-10-02")
        self.s.save_product(product("1", offers=[offer("A", 100.0)], history=[("2026-10-03", 1.0)]),
                            "2026-10-03", lowest_shop="(laveste)")
        got = self.s.market_medians()
        self.assertEqual(got[("prisjakt", "1", "2026-10-01")], 200.0)    # odd
        self.assertEqual(got[("prisjakt", "1", "2026-10-02")], 200.0)    # even: (100+300)/2, sold-out C ignored
        self.assertEqual(got[("prisjakt", "1", "2026-10-03")], 100.0)    # pseudo-shop ignored

    def test_shop_product_counts(self):
        self.s.save_product(product("1", offers=[offer("A", 1.0), offer("B", 2.0, None, False)]), "2026-10-01")
        self.s.save_product(product("2", offers=[offer("A", 1.0)]), "2026-10-01")
        self.s.save_product(product("3", source="klarna", offers=[offer("A", 1.0)]), "2026-10-01")
        self.s.save_product(product("2", offers=[offer("A", 1.0)], history=[("2026-10-01", 1.0)]),
                            "2026-10-01", lowest_shop="(laveste)")
        self.s.save_product(product("1", offers=[offer("A", 1.0)]), "2026-10-02")
        self.assertEqual(self.s.shop_product_counts("2026-10-01"),
                         {("prisjakt", "A"): 2, ("klarna", "A"): 1})
        self.assertEqual(self.s.shop_product_counts("2026-10-02"), {("prisjakt", "A"): 1})
        self.assertEqual(self.s.shop_product_counts("2030-01-01"), {})


class DailySteps(unittest.TestCase):
    def test_fill_forward(self):
        self.assertEqual(_daily_steps([("2026-01-01", 10.0), ("2026-01-03", 8.0)], "2026-01-05"), [
            ("2026-01-01", 10.0), ("2026-01-02", 10.0), ("2026-01-03", 8.0),
            ("2026-01-04", 8.0), ("2026-01-05", 8.0)])

    def test_unsorted_and_same_day_takes_min(self):
        got = _daily_steps([("2026-01-03", 9.0), ("2026-01-01", 10.0), ("2026-01-03", 7.0)], "2026-01-03")
        self.assertEqual(got, [("2026-01-01", 10.0), ("2026-01-02", 10.0), ("2026-01-03", 7.0)])

    def test_empty(self):
        self.assertEqual(_daily_steps([], "2026-01-05"), [])

    def test_until_before_first_point(self):
        self.assertEqual(_daily_steps([("2026-02-01", 5.0)], "2026-01-01"), [])

    def test_crosses_month_and_year(self):
        got = _daily_steps([("2025-12-30", 1.0)], "2026-01-02")
        self.assertEqual([d for d, _ in got], ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"])

    def test_future_change_points_ignored(self):
        self.assertEqual(_daily_steps([("2026-01-01", 1.0), ("2026-01-09", 5.0)], "2026-01-02"),
                         [("2026-01-01", 1.0), ("2026-01-02", 1.0)])


class Misc(unittest.TestCase):
    def test_shift(self):
        self.assertEqual(store_mod._shift("2026-03-01", -1), "2026-02-28")

    def test_default_path_env(self):
        old = dict(os.environ)
        try:
            os.environ.pop("BFA_DB", None)
            os.environ["OUTPUT_DIR"] = "somewhere"
            self.assertEqual(store_mod.default_path(), os.path.join("somewhere", "bfa.sqlite"))
            os.environ["BFA_DB"] = "/x/y.sqlite"
            self.assertEqual(store_mod.default_path(), "/x/y.sqlite")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
