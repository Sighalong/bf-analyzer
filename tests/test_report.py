import csv
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401  (sys.path)
from bfa import analyze, report

STATS = {"products": 2, "first_day": "2026-08-01", "last_day": "2026-11-27"}
PTS = [("2026-11-01", 1000.0), ("2026-11-05", 1300.0), ("2026-11-10", 1000.0)]


def finding(**kw):
    base = dict(rule="fake_discount", source="prisjakt", product_id="1", title="TV", url="http://u/1",
                category="tv", shop="Butikk", base_median=1000.0, base_low=1000.0, peak=1300.0,
                peak_day="2026-11-05", now=1000.0, days=40, score=0.3, baseline="før 2026-10-01",
                series=PTS)
    base.update(kw)
    return analyze.Finding(**base)


def shop_row(shop="Butikk"):
    return {"source": "prisjakt", "shop": shop, "flagged": 3, "listed": 6, "share": 0.5,
            "fake_discount": 2, "before_price": 1, "hike": 0}


class ChartSvg(unittest.TestCase):
    def test_empty_for_fewer_than_two_points(self):
        self.assertEqual(report.chart_svg(finding(series=[])), "")
        self.assertEqual(report.chart_svg(finding(series=None)), "")
        self.assertEqual(report.chart_svg(finding(series=PTS[:1])), "")

    def test_svg_elements(self):
        svg = report.chart_svg(finding())
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertIn("<path", svg)
        self.assertIn('class="now"', svg)
        self.assertIn('class="ref"', svg)
        self.assertNotIn('class="claim"', svg)

    def test_claim_line_and_no_ref(self):
        svg = report.chart_svg(finding(rule="before_price", base_median=None, claimed=1500.0, low30=1000.0))
        self.assertIn('class="claim"', svg)
        self.assertNotIn('class="ref"', svg)

    def test_flat_series_does_not_crash(self):
        flat = [("2026-11-01", 5.0), ("2026-11-02", 5.0)]
        self.assertIn("<path", report.chart_svg(finding(series=flat, base_median=None, peak=None)))


class Outputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_html_has_shop_ranking_and_escapes(self):
        page = report.html_page([finding()], STATS, [], False, shops=[shop_row("<script>alert(1)</script>")])
        self.assertIn("Butikker med flest flaggede produkter", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("<svg", page)

    def test_html_without_shops_still_renders_heading(self):
        self.assertIn(report.SHOPS_TITLE, report.html_page([], STATS, [], True))

    def test_text_has_shop_ranking(self):
        out = report.text([finding()], STATS, shops=[shop_row()])
        self.assertIn("== Butikker med flest flaggede produkter (1) ==", out)
        self.assertIn("3 av 6 produkter (50 %): 2 falsk rabatt, 1 før-pris", out)

    def test_markdown_has_shop_ranking(self):
        path = os.path.join(self.tmp, "r.md")
        report.save_markdown(path, [finding()], STATS, shops=[shop_row()])
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("## Butikker med flest flaggede produkter", md)
        self.assertIn("3 av 6 produkter", md)
        path2 = os.path.join(self.tmp, "r2.md")
        report.save_markdown(path2, [], STATS)
        with open(path2, encoding="utf-8") as fh:
            self.assertIn("_Ingen funn._", fh.read().split("## Butikker")[1])

    def test_csv_has_baseline_column(self):
        path = os.path.join(self.tmp, "r.csv")
        report.save_csv(path, [finding()])
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertIn("baseline", rows[0])
        self.assertEqual(rows[1][rows[0].index("baseline")], "før 2026-10-01")
        self.assertNotIn("series", rows[0])


if __name__ == "__main__":
    unittest.main()
