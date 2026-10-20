import os
import shutil
import tempfile
import unittest

from datetime import datetime, timedelta

from _helpers import series, day_back
from bfa import analyze
from bfa.store import Store

TODAY = "2026-11-10"


def rules(rows, today=TODAY):
    return dict(analyze.analyze_series(rows, today))


class Hike(unittest.TestCase):
    def test_hike_peak_still_current(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 0, 1300.0)])
        out = rules(rows)
        self.assertEqual(list(out), ["hike"])
        f = out["hike"]
        self.assertEqual((f["base_median"], f["base_low"], f["peak"], f["now"]), (1000.0, 1000.0, 1300.0, 1300.0))
        self.assertEqual(f["peak_day"], day_back(TODAY, 10))
        self.assertAlmostEqual(f["score"], 0.3)

    def test_hike_just_below_threshold_is_ignored(self):
        self.assertEqual(rules(series(TODAY, [(90, 11, 1000.0), (10, 0, 1090.0)])), {})

    def test_hike_with_small_drop_is_still_hike(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 1250.0)])
        self.assertEqual(list(rules(rows)), ["hike"])


class FakeDiscount(unittest.TestCase):
    def test_hike_then_back_to_baseline(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 1000.0)])
        out = rules(rows)
        self.assertEqual(list(out), ["fake_discount"])
        self.assertEqual(out["fake_discount"]["peak"], 1300.0)
        self.assertEqual(out["fake_discount"]["now"], 1000.0)
        self.assertAlmostEqual(out["fake_discount"]["score"], 0.3)

    def test_drop_well_below_baseline_is_not_fake(self):
        # now 900 is 10 % below the baseline low: a real deal, not an undone hike
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 900.0)])
        self.assertEqual(list(rules(rows)), ["deal"])

    def test_partial_drop_still_well_above_normal_is_a_hike(self):
        # peak 1300 -> now 1150: the "discount" leaves the price 15 % above normal, so it is
        # still raised, not back to normal
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 1150.0)])
        out = rules(rows)
        self.assertEqual(list(out), ["hike"])
        self.assertAlmostEqual(out["hike"]["score"], 0.15)

    def test_drop_to_within_ten_percent_of_normal_is_fake(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 1090.0)])
        self.assertEqual(list(rules(rows)), ["fake_discount"])

    def test_drop_somewhat_below_normal_is_not_fake(self):
        # 6 % below normal: a real cut, even if not deep enough for "deal"
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 940.0)])
        self.assertNotIn("fake_discount", rules(rows))

    def test_one_dip_in_baseline_does_not_widen_fake_band(self):
        # JBL PartyBox on the Pi: one cheap day in the baseline made a real price cut look fake
        rows = series(TODAY, [(90, 12, 4312.0), (11, 11, 2990.0), (10, 3, 4990.0), (2, 0, 3250.0)])
        out = rules(rows)
        self.assertNotIn("fake_discount", out)
        self.assertEqual(out["deal"]["base_low"], 4312.0)


class Hike2(unittest.TestCase):
    def test_one_day_spike_is_ignored(self):
        rows = series(TODAY, [(90, 1, 1000.0), (0, 0, 1300.0)])
        self.assertEqual(rules(rows), {})

    def test_two_day_rise_counts(self):
        rows = series(TODAY, [(90, 2, 1000.0), (1, 0, 1300.0)])
        self.assertEqual(list(rules(rows)), ["hike"])

    def test_small_rise_near_peak_below_threshold_is_not_a_hike(self):
        # peak +12 %, now +9 %: no longer raised by the 10 % threshold
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1120.0), (2, 0, 1090.0)])
        self.assertEqual(rules(rows), {})

    def test_drop_under_eight_percent_is_a_hike_not_fake(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 3, 1300.0), (2, 0, 1230.0)])
        self.assertEqual(list(rules(rows)), ["hike"])


class Deal(unittest.TestCase):
    def test_deal_five_percent_below_baseline_low(self):
        rows = series(TODAY, [(90, 1, 1000.0), (0, 0, 940.0)])
        out = rules(rows)
        self.assertEqual(list(out), ["deal"])
        self.assertAlmostEqual(out["deal"]["score"], 0.06)

    def test_exactly_five_percent_counts(self):
        self.assertEqual(list(rules(series(TODAY, [(90, 1, 1000.0), (0, 0, 950.0)]))), ["deal"])

    def test_four_percent_is_not_a_deal(self):
        self.assertEqual(rules(series(TODAY, [(90, 1, 1000.0), (0, 0, 960.0)])), {})


class BeforePrice(unittest.TestCase):
    def rows_with_claim(self, claimed, prior_days=30, prior_price=1000.0):
        rows = series(TODAY, [(90, 31, 1000.0), (prior_days, 1, prior_price)])
        rows.append((TODAY, 800.0, claimed, 1))
        return rows

    def test_claim_above_low30(self):
        out = rules(self.rows_with_claim(1500.0))
        self.assertIn("before_price", out)
        f = out["before_price"]
        self.assertEqual((f["claimed"], f["low30"], f["now"], f["days"]), (1500.0, 1000.0, 800.0, 30))
        self.assertAlmostEqual(f["score"], 0.5)

    def test_needs_14_prior_days(self):
        self.assertNotIn("before_price", rules(self.rows_with_claim(1500.0, prior_days=13)))
        self.assertIn("before_price", rules(self.rows_with_claim(1500.0, prior_days=14)))

    def test_claim_equal_or_within_one_percent_is_fine(self):
        self.assertNotIn("before_price", rules(self.rows_with_claim(1000.0)))
        self.assertNotIn("before_price", rules(self.rows_with_claim(1010.0)))
        self.assertIn("before_price", rules(self.rows_with_claim(1011.0)))

    def test_no_claim_no_finding(self):
        rows = self.rows_with_claim(None)
        self.assertNotIn("before_price", rules(rows))

    def test_works_without_baseline(self):
        rows = series(TODAY, [(30, 1, 1000.0)]) + [(TODAY, 900.0, 1400.0, 1)]
        self.assertEqual(list(rules(rows)), ["before_price"])


class Negatives(unittest.TestCase):
    def test_flat_price(self):
        self.assertEqual(rules(series(TODAY, [(90, 0, 1000.0)])), {})

    def test_not_listed_today(self):
        rows = series(TODAY, [(90, 11, 1000.0), (10, 1, 1500.0)])    # last row is yesterday
        self.assertEqual(rules(rows), {})

    def test_empty(self):
        self.assertEqual(rules([]), {})

    def test_too_little_baseline_no_hike_or_deal(self):
        rows = series(TODAY, [(36, 31, 1000.0), (10, 0, 1500.0)])    # only 6 baseline days
        self.assertEqual(rules(rows), {})
        rows = series(TODAY, [(36, 31, 1000.0), (10, 1, 1000.0), (0, 0, 700.0)])
        self.assertEqual(rules(rows), {})

    def test_baseline_of_exactly_seven_days_is_enough(self):
        rows = series(TODAY, [(37, 31, 1000.0), (10, 0, 1500.0)])
        self.assertEqual(list(rules(rows)), ["hike"])

    def test_too_few_recent_days(self):
        rows = series(TODAY, [(90, 31, 1000.0), (1, 0, 1500.0)])      # only 2 recent days
        self.assertEqual(rules(rows), {})

    def test_data_older_than_90_days_is_ignored(self):
        rows = series(TODAY, [(120, 91, 100.0), (30, 0, 1000.0)])
        self.assertEqual(rules(rows), {})


class BlackFriday(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(analyze.black_friday(2026), "2026-11-27")
        self.assertEqual(analyze.black_friday(2025), "2025-11-28")
        self.assertEqual(analyze.black_friday(2023), "2023-11-24")   # Nov 1 is a Wednesday
        self.assertEqual(analyze.black_friday(2022), "2022-11-25")   # Nov 1 is a Tuesday
        self.assertEqual(analyze.black_friday(2021), "2021-11-26")   # Nov 1 is a Monday

    def test_nov1_on_friday_uses_day_after_us_thanksgiving(self):
        # KNOWN BUG (analyze.py black_friday): when 1 Nov is a Friday the "fourth Friday" is the 22nd,
        # but Black Friday (day after the 4th Thursday) is the 29th. Applies to 2019, 2024, 2030.
        self.assertEqual(analyze.black_friday(2024), "2024-11-29")
        self.assertEqual(analyze.black_friday(2019), "2019-11-29")

class BfPhase(unittest.TestCase):
    def test_phases_2026(self):
        phase = lambda d: analyze.bf_phase(d)["phase"]
        self.assertEqual(phase("2026-09-30"), "off")
        self.assertEqual(phase("2026-10-01"), "warmup")
        self.assertEqual(phase("2026-11-22"), "warmup")
        self.assertEqual(phase("2026-11-23"), "black_week")     # Monday before Black Friday
        self.assertEqual(phase("2026-11-30"), "black_week")     # Cyber Monday
        self.assertEqual(phase("2026-12-01"), "after")
        self.assertEqual(phase("2026-12-11"), "after")

    def test_after_season_points_to_next_year(self):
        p = analyze.bf_phase("2026-12-12")
        self.assertEqual((p["phase"], p["bf"], p["days"]), ("off", "2027-11-26", 349))
        self.assertEqual(analyze.bf_phase("2026-10-18")["days"], 40)


class Asleep(unittest.TestCase):
    def test_sleeps_from_new_year_to_august(self):
        self.assertIsNone(analyze.asleep("2026-12-31"))         # romjul sales are still followed
        self.assertEqual(analyze.asleep("2027-01-01"), "2027-08-01")
        self.assertEqual(analyze.asleep("2027-07-31"), "2027-08-01")
        self.assertIsNone(analyze.asleep("2027-08-01"))
        self.assertIsNone(analyze.asleep("2026-10-18"))
        self.assertEqual(analyze.bf_phase("2027-03-01")["wake"], "2027-08-01")


class AnalyzeStore(unittest.TestCase):
    def test_findings_sorted_by_score(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Store(os.path.join(tmp, "a.sqlite"))
        self.addCleanup(s.close)
        for pid, bump in (("small", 1200.0), ("big", 1500.0), ("flat", 1000.0)):
            rows = series(TODAY, [(90, 11, 1000.0), (10, 0, bump)])
            for day, price, _, _ in rows:
                s.save_product({"source": "prisjakt", "product_id": pid, "title": pid, "url": "u" + pid,
                                "offers": [{"shop": "S", "price": price, "original_price": None, "in_stock": True}],
                                "lowest_history": [], "lowest_now": None}, day, category="tv")
        found = analyze.analyze(s, TODAY)
        self.assertEqual([(f.product_id, f.rule) for f in found], [("big", "hike"), ("small", "hike")])
        self.assertEqual(found[0].shop, "S")
        self.assertEqual(found[0].category, "tv")
        self.assertEqual(found[0].as_dict()["now"], 1500.0)


# ---- season-aware behaviour -------------------------------------------------

def span(rows, a, b, price, claimed=None, in_stock=1):
    """Append one row per day a..b (inclusive, YYYY-MM-DD) to rows."""
    d, end = datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
    while d <= end:
        rows.append((d.strftime("%Y-%m-%d"), price, claimed, in_stock))
        d += timedelta(days=1)
    return rows


BF_DAY = "2026-11-27"


def by_rule(rows, today=BF_DAY, market=None, baseline_end=None):
    return dict(analyze.analyze_series(rows, today, market, baseline_end))


class CleanRows(unittest.TestCase):
    def test_out_of_stock_dropped(self):
        rows = [("2026-11-01", 100.0, None, 1), ("2026-11-02", 500.0, None, 0)]
        self.assertEqual(analyze.clean_rows(rows), [("2026-11-01", 100.0, None)])

    def test_outlier_threshold_is_strictly_above_two_times_market(self):
        rows = [("2026-11-01", 2000.0, None, 1), ("2026-11-02", 2001.0, None, 1), ("2026-11-03", 2500.0, None, 1)]
        market = {"2026-11-01": 1000.0, "2026-11-02": 1000.0, "2026-11-03": 1000.0}
        self.assertEqual([r[0] for r in analyze.clean_rows(rows, market)], ["2026-11-01"])

    def test_day_without_market_median_is_kept(self):
        rows = [("2026-11-01", 9999.0, None, 1)]
        self.assertEqual(len(analyze.clean_rows(rows, {"2026-11-05": 10.0})), 1)

    def test_sold_out_stale_high_price_gives_no_hike(self):
        rows = span([], "2026-08-01", "2026-11-16", 10000.0)
        span(rows, "2026-11-17", "2026-11-26", 12000.0, in_stock=0)
        span(rows, "2026-11-27", "2026-11-27", 10000.0)
        self.assertEqual(by_rule(rows), {})
        # sanity: the same prices in stock would be flagged
        rows2 = [(d, p, c, 1) for d, p, c, _ in rows]
        self.assertIn("fake_discount", by_rule(rows2))

    def test_only_row_today_out_of_stock_gives_nothing(self):
        rows = span([], "2026-08-01", "2026-11-26", 10000.0)
        span(rows, BF_DAY, BF_DAY, 7000.0, claimed=12000.0, in_stock=0)
        self.assertEqual(by_rule(rows), {})

    def test_outlier_rows_ignored_in_analyze_series(self):
        rows = span([], "2026-08-01", "2026-10-31", 1000.0)
        span(rows, "2026-11-01", BF_DAY, 2500.0)
        market = {r[0]: 1000.0 for r in rows}
        self.assertEqual(by_rule(rows, market=market), {})      # 2500 > 2 x 1000: dropped; today not listed
        market = {r[0]: 1300.0 for r in rows}                   # 2500 <= 2 x 1300: real hike
        self.assertEqual(list(by_rule(rows, market=market)), ["hike"])


class SeasonBaselineEnd(unittest.TestCase):
    def test_auto_inside_season(self):
        self.assertEqual(analyze.season_baseline_end("2026-10-18"), "2026-10-01")
        self.assertEqual(analyze.season_baseline_end("2026-10-01"), "2026-10-01")
        self.assertEqual(analyze.season_baseline_end("2026-12-11"), "2026-10-01")   # BF + 14 days

    def test_auto_outside_season(self):
        self.assertIsNone(analyze.season_baseline_end("2026-09-15"))
        self.assertIsNone(analyze.season_baseline_end("2026-12-12"))

    def test_overrides(self):
        self.assertIsNone(analyze.season_baseline_end("2026-11-01", "rolling"))
        self.assertEqual(analyze.season_baseline_end("2026-11-01", "auto"), "2026-10-01")
        self.assertEqual(analyze.season_baseline_end("2026-11-01", "2026-09-20"), "2026-09-20")
        self.assertEqual(analyze.season_baseline_end("2026-02-01", "2026-09-20"), "2026-09-20")

    def test_invalid_override_raises(self):
        with self.assertRaises(ValueError):
            analyze.season_baseline_end("2026-11-01", "not-a-date")


class SeasonBaseline(unittest.TestCase):
    def gouge(self, hike_from):
        rows = span([], "2026-08-01", day_back(hike_from, 1), 10000.0)
        span(rows, hike_from, "2026-11-23", 12000.0)
        span(rows, "2026-11-24", BF_DAY, 10000.0)
        return rows

    def test_october_hike_then_bf_sale_is_fake_discount(self):
        rows = self.gouge("2026-10-20")
        out = by_rule(rows, baseline_end="2026-10-01")
        self.assertIn("fake_discount", out)
        f = out["fake_discount"]
        self.assertEqual(f["baseline"], "før 2026-10-01")
        self.assertEqual((f["base_median"], f["peak"], f["now"]), (10000.0, 12000.0, 10000.0))
        self.assertAlmostEqual(f["score"], 0.2)

    def test_early_hike_leaks_into_rolling_baseline_but_not_season_baseline(self):
        # raised on 25 Sep: by Black Friday the rolling day 31..90 window is mostly the raised price,
        # so rolling windows miss it; the season baseline (before 1 Oct) still catches it.
        rows = self.gouge("2026-09-25")
        self.assertIn("fake_discount", by_rule(rows, baseline_end="2026-10-01"))
        self.assertNotIn("fake_discount", by_rule(rows, baseline_end=None))

    def test_fallback_to_early_data_when_nothing_before_season_start(self):
        rows = span([], "2026-10-18", "2026-11-02", 9900.0)
        span(rows, "2026-11-03", "2026-11-24", 11500.0)
        span(rows, "2026-11-25", BF_DAY, 9900.0)
        out = by_rule(rows, baseline_end="2026-10-01")
        self.assertIn("fake_discount", out)
        self.assertTrue(out["fake_discount"]["baseline"].startswith("tidlig 10-18"))

    def test_no_data_before_november_gives_no_flags(self):
        rows = span([], "2026-11-10", "2026-11-12", 9900.0)
        span(rows, "2026-11-13", "2026-11-24", 11500.0)
        span(rows, "2026-11-25", BF_DAY, 9900.0)
        out = by_rule(rows, baseline_end="2026-10-01")
        for rule in ("hike", "fake_discount", "deal"):
            self.assertNotIn(rule, out)


class BeforePriceReference(unittest.TestCase):
    def test_honest_shop_with_long_stable_price_not_flagged(self):
        rows = span([], "2026-07-01", "2026-11-23", 10200.0)
        span(rows, "2026-11-24", BF_DAY, 9200.0, claimed=10200.0)
        self.assertNotIn("before_price", by_rule(rows))

    def test_claim_matching_own_raised_price_is_flagged(self):
        rows = span([], "2026-07-01", "2026-11-09", 10000.0)
        span(rows, "2026-11-10", "2026-11-23", 12000.0)
        span(rows, "2026-11-24", BF_DAY, 10000.0, claimed=12000.0)
        f = by_rule(rows)["before_price"]
        self.assertEqual((f["claimed"], f["low30"], f["now"]), (12000.0, 10000.0, 10000.0))
        self.assertEqual(f["days"], 30)

    def test_fewer_than_14_prior_days_not_flagged(self):
        rows = span([], "2026-11-15", "2026-11-23", 10000.0)         # 9 prior days
        span(rows, "2026-11-24", BF_DAY, 8000.0, claimed=12000.0)
        self.assertNotIn("before_price", by_rule(rows))


class SeriesEvidence(unittest.TestCase):
    def rows(self):
        rows = series(TODAY, [(120, 11, 1000.0), (10, 0, 1400.0)])
        # an out-of-stock row that must not show up in the chart series
        rows = [r for r in rows if r[0] != day_back(TODAY, 50)] + [(day_back(TODAY, 50), 5.0, None, 0)]
        return sorted(rows)

    def test_every_finding_has_cleaned_series_within_90_days(self):
        found = analyze.analyze_series(self.rows(), TODAY)
        self.assertTrue(found)
        for rule, f in found:
            pts = f["series"]
            self.assertEqual(pts[-1], (TODAY, 1400.0))
            self.assertGreaterEqual(pts[0][0], day_back(TODAY, 90))
            self.assertEqual(pts[0][0], day_back(TODAY, 90))
            self.assertNotIn(day_back(TODAY, 50), [d for d, _ in pts])
            self.assertTrue(all(isinstance(p, tuple) and len(p) == 2 for p in pts))

    def test_as_dict_excludes_series(self):
        f = analyze.Finding(rule="hike", series=[("2026-11-10", 1.0)], now=1.0)
        self.assertNotIn("series", f.as_dict())
        self.assertEqual(f.as_dict()["rule"], "hike")


def finding(rule, shop, pid, source="prisjakt"):
    return analyze.Finding(rule=rule, shop=shop, product_id=pid, source=source, score=0.1)


class RankShops(unittest.TestCase):
    def test_excludes_pseudo_shop_and_deals(self):
        fs = [finding("deal", "A", "1"), finding("hike", "(laveste)", "1"), finding("hike", "B", "1")]
        out = analyze.rank_shops(fs, {("prisjakt", "A"): 5, ("prisjakt", "B"): 4})
        self.assertEqual([r["shop"] for r in out], ["B"])

    def test_share_and_counts(self):
        fs = [finding("fake_discount", "B", "1"), finding("before_price", "B", "1"),
              finding("hike", "B", "2")]
        r = analyze.rank_shops(fs, {("prisjakt", "B"): 4})[0]
        self.assertEqual((r["flagged"], r["listed"], r["share"]), (2, 4, 0.5))
        self.assertEqual((r["fake_discount"], r["before_price"], r["hike"]), (1, 1, 1))

    def test_eight_of_twenty_beats_one_of_one(self):
        fs = [finding("hike", "Big", str(i)) for i in range(8)] + [finding("hike", "Tiny", "x")]
        out = analyze.rank_shops(fs, {("prisjakt", "Big"): 20, ("prisjakt", "Tiny"): 1})
        self.assertEqual([r["shop"] for r in out], ["Big", "Tiny"])
        self.assertEqual(out[1]["share"], 1.0)

    def test_listed_never_below_flagged(self):
        fs = [finding("hike", "S", "1"), finding("hike", "S", "2"), finding("hike", "S", "3")]
        for listed in ({}, {("prisjakt", "S"): 1}):
            r = analyze.rank_shops(fs, listed)[0]
            self.assertEqual((r["flagged"], r["listed"], r["share"]), (3, 3, 1.0))


class EndToEnd(unittest.TestCase):
    """Port of the five-shop simulation: gouge, sold out, honest, late data, outlier."""

    def build(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Store(os.path.join(tmp, "sim.sqlite"))
        self.addCleanup(s.close)
        d, end = datetime(2026, 8, 1), datetime(2026, 11, 27)
        while d <= end:
            day = d.strftime("%Y-%m-%d")
            d += timedelta(days=1)
            offers = []
            a = 10000 if day < "2026-10-20" else (12000 if day < "2026-11-24" else 10000)
            offers.append(dict(shop="GougeShop", price=a, in_stock=True,
                               original_price=12000 if day >= "2026-11-24" else None))
            offers.append(dict(shop="SoldOut", price=10000 if day < "2026-10-15" else 15000,
                               original_price=None, in_stock=day < "2026-10-15"))
            offers.append(dict(shop="HonestShop", price=10200 if day < "2026-11-24" else 9200,
                               original_price=10200 if day >= "2026-11-24" else None, in_stock=True))
            if day >= "2026-10-18":
                late = 9900 if day < "2026-11-03" else (11500 if day < "2026-11-25" else 9900)
                offers.append(dict(shop="LateShop", price=late, original_price=None, in_stock=True))
            offers.append(dict(shop="Outlier", price=25000 if day < "2026-10-25" else 29000,
                               original_price=None, in_stock=True))
            s.save_product({"source": "prisjakt", "product_id": "1", "title": "Test-TV 65", "url": "http://u/1",
                            "offers": offers, "lowest_history": [], "lowest_now": None}, day, category="tv")
        return s

    def test_simulation(self):
        s = self.build()
        found = analyze.analyze(s, BF_DAY)
        got = sorted((f.shop, f.rule) for f in found)
        self.assertEqual(got, [("GougeShop", "fake_discount"), ("HonestShop", "deal"),
                               ("LateShop", "fake_discount")])
        late = [f for f in found if f.shop == "LateShop"][0]
        self.assertTrue(late.baseline.startswith("tidlig 10-18"))
        ranked = analyze.rank_shops(found, s.shop_product_counts(BF_DAY))
        self.assertEqual(sorted(r["shop"] for r in ranked), ["GougeShop", "LateShop"])
        for r in ranked:
            self.assertEqual((r["flagged"], r["listed"], r["share"]), (1, 1, 1.0))


if __name__ == "__main__":
    unittest.main()


# ---- cheapest-price series "(laveste)" ----------------------------------------

def lowest(rows, today=BF_DAY, confirm=None, baseline_end="2026-10-01"):
    return dict(analyze.analyze_series(rows, today, None, baseline_end, lowest=True, confirm=confirm))


def market_span(m, a, b, price):
    for d, p, _, _ in span([], a, b, price):
        m[d] = p
    return m


class LowestSeries(unittest.TestCase):
    def test_cheapest_shop_sold_out_jump_is_not_a_hike(self):
        # Roborock Q8 Max on the Pi: normal 3 990 kr, "hevet" to 9 006 kr on 16 Oct
        rows = span([], "2026-08-01", "2026-10-15", 3990.0)
        span(rows, "2026-10-16", "2026-10-18", 9006.0)
        out = lowest(rows, today="2026-10-18")
        self.assertNotIn("hike", out)
        self.assertNotIn("fake_discount", out)
        # a normal shop doing the same is still flagged
        self.assertIn("hike", dict(analyze.analyze_series(rows, "2026-10-18", None, "2026-10-01")))

    def test_jump_back_down_after_dropout_is_not_fake_discount(self):
        # baseline with a one-off dip (Watch Ultra2: low 779 kr), dropout, then back to normal
        rows = span([], "2026-08-01", "2026-09-29", 2990.0)
        span(rows, "2026-09-30", "2026-09-30", 779.0)
        span(rows, "2026-10-20", "2026-11-20", 6490.0)
        span(rows, "2026-11-21", BF_DAY, 2990.0)
        out = lowest(rows)
        self.assertNotIn("fake_discount", out)
        self.assertNotIn("hike", out)

    def test_real_hikes_of_10_to_50_percent_still_flagged(self):
        for pct in (10, 30, 50):
            raised = 4000.0 * (1 + pct / 100.0)
            rows = span([], "2026-08-01", "2026-10-19", 4000.0)
            span(rows, "2026-10-20", BF_DAY, raised)
            self.assertIn("hike", lowest(rows), pct)
            rows = span([], "2026-08-01", "2026-10-19", 4000.0)
            span(rows, "2026-10-20", "2026-11-23", raised)
            span(rows, "2026-11-24", BF_DAY, 4000.0)
            self.assertIn("fake_discount", lowest(rows), pct)

    def test_cap_is_inclusive(self):
        rows = span([], "2026-08-01", "2026-10-19", 1000.0)
        span(rows, "2026-10-20", BF_DAY, 1000.0 * analyze.LOWEST_MAX_JUMP)
        self.assertIn("hike", lowest(rows))
        rows[-1] = rows[-1][:1] + (1000.0 * analyze.LOWEST_MAX_JUMP + 1,) + rows[-1][2:]
        self.assertNotIn("hike", lowest(rows))

    def test_deal_unaffected(self):
        rows = span([], "2026-08-01", "2026-11-20", 1000.0)
        span(rows, "2026-11-21", BF_DAY, 900.0)
        self.assertIn("deal", lowest(rows))

    def test_market_flat_vetoes_moderate_jump(self):
        rows = span([], "2026-08-01", "2026-10-19", 4000.0)
        span(rows, "2026-10-20", BF_DAY, 5000.0)
        m = market_span({}, "2026-08-01", BF_DAY, 5200.0)
        self.assertNotIn("hike", lowest(rows, confirm=m))

    def test_market_rise_confirms_even_a_big_jump(self):
        rows = span([], "2026-08-01", "2026-10-19", 4000.0)
        span(rows, "2026-10-20", BF_DAY, 7000.0)
        m = market_span({}, "2026-08-01", "2026-10-19", 4500.0)
        market_span(m, "2026-10-20", BF_DAY, 7500.0)
        self.assertIn("hike", lowest(rows, confirm=m))

    def test_market_without_baseline_falls_back_to_cap(self):
        # per-shop data only from 18 Oct: no market baseline yet, so the cap decides
        rows = span([], "2026-08-01", "2026-10-19", 4000.0)
        span(rows, "2026-10-20", BF_DAY, 5000.0)
        m = market_span({}, "2026-10-18", BF_DAY, 5200.0)
        self.assertIn("hike", lowest(rows, confirm=m))
        rows = span([], "2026-08-01", "2026-10-19", 4000.0)
        span(rows, "2026-10-20", BF_DAY, 9000.0)
        self.assertNotIn("hike", lowest(rows, confirm=m))

    def test_analyze_store_guards_lowest_but_not_shops(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Store(os.path.join(tmp, "a.sqlite"))
        self.addCleanup(s.close)
        today = "2026-10-18"
        # shop S raises 3 990 -> 4 600 (15 %), T stays at 5 000; the cheapest series jumps to 9 006
        # as if the cheapest shop dropped out. Market median rises only ~7 %, so no confirmation.
        rows = span([], "2026-08-01", "2026-10-15", 3990.0)
        span(rows, "2026-10-16", today, 4600.0)
        for day, price, _, _ in rows:
            offers = [{"shop": "S", "price": price, "original_price": None, "in_stock": True},
                      {"shop": "T", "price": 5000.0, "original_price": None, "in_stock": True}]
            s.save_product({"source": "prisjakt", "product_id": "p", "title": "p", "url": "u",
                            "offers": offers, "lowest_history": [], "lowest_now": None}, day, category="tv")
            low = 3990.0 if day < "2026-10-16" else 9006.0
            s.db.execute("INSERT INTO prices (source, product_id, shop, day, price, original_price, in_stock) "
                         "VALUES ('prisjakt', 'p', '(laveste)', ?, ?, NULL, 1)", (day, low))
        found = [(f.shop, f.rule) for f in analyze.analyze(s, today)]
        self.assertIn(("S", "hike"), found)
        self.assertNotIn(("(laveste)", "hike"), found)
