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
        self.assertIn("Butikker med flest mistenkelige produkter", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("<svg", page)

    def test_html_empty_state_and_running(self):
        page = report.html_page([], STATS, [], True)
        self.assertIn("Ingen mistenkelige priser funnet ennå", page)
        self.assertIn("27. nov", page)               # Black Friday 2026
        self.assertIn("Henter …", page)
        self.assertNotIn("id='fcat'", page)          # no filters without findings

    def test_html_sections_filters_and_story(self):
        fs = [finding(), finding(rule="deal", score=0.1, now=900.0, category="mobil", shop="Ærlig"),
              finding(rule="before_price", claimed=1500.0, low30=1000.0, base_median=None, peak=None)]
        page = report.html_page(fs, STATS, ["bfa_report.csv"], False, shops=[shop_row()])
        self.assertIn("2 mistenkelige tilbud</b> hos 1 butikker", page)
        self.assertIn("<option value='mobil'>Mobil</option>", page)
        self.assertIn("data-rule='fake_discount' open", page)
        self.assertIn("Var vanligvis 1 000 kr. Hevet til 1 300 kr 5. nov, nå «tilbud» 1 000 kr.", page)
        self.assertIn("Oppgir før-pris 1 500 kr", page)
        self.assertIn("normalpris målt før 1. okt", page)
        self.assertIn("<b>Prishopp:</b> ingen funnet.", page)
        self.assertIn("href='files/bfa_report.csv'", page)   # relative, so it works under BASE_PATH

    def test_nice_baseline(self):
        self.assertEqual(report.nice_baseline("tidlig 10-18–10-31"), "kun 18. okt–31. okt (lite data)")
        self.assertEqual(report.nice_baseline("dag 31–90"), "1–3 måneder siden")
        self.assertEqual(report.nice_day("2026-11-05"), "5. nov")

    def test_text_has_shop_ranking(self):
        out = report.text([finding()], STATS, shops=[shop_row()])
        self.assertIn("== Butikker med flest mistenkelige produkter (1) ==", out)
        self.assertIn("3 av 6 produkter (50 %): 2 falsk rabatt, 1 før-pris", out)

    def test_markdown_has_shop_ranking(self):
        path = os.path.join(self.tmp, "r.md")
        report.save_markdown(path, [finding()], STATS, shops=[shop_row()])
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("## Butikker med flest mistenkelige produkter", md)
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
