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
    def test_axes_have_dates_and_prices(self):
        pts = [("2026-09-10", 1000.0), ("2026-10-05", 1300.0), ("2026-10-20", 1300.0)]
        svg = report.chart_svg(finding(series=pts, peak_day="2026-10-05", now=1300.0))
        self.assertIn(">1. okt</text>", svg)                       # month tick
        self.assertIn(">20. okt</text>", svg)                      # last day
        self.assertIn(">1 300 kr</text>", svg)                     # price axis
        self.assertIn(">1 300 kr 5. okt</text>", svg)              # peak label
        self.assertIn('class="base"', svg)                         # normal-price window band

    def test_axis_runs_to_black_friday_before_it(self):
        pts = [("2026-09-10", 1000.0), ("2026-10-20", 1300.0)]
        svg = report.chart_svg(finding(series=pts, peak_day="2026-10-20"), bf="2026-11-27")
        self.assertIn('class="bfl"', svg)
        self.assertIn(">27. nov</text>", svg)
        self.assertIn('class="fut"', svg)
        far = report.chart_svg(finding(series=pts, peak_day="2026-10-20"), bf="2027-11-26")
        self.assertNotIn('class="bfl"', far)

    def test_baseline_window(self):
        self.assertEqual([d.strftime("%m-%d") for d in report.baseline_window("før 2026-10-01", "2026-10-20")],
                         ["08-02", "09-30"])
        self.assertEqual([d.strftime("%m-%d") for d in report.baseline_window("tidlig 10-18–10-31", "2026-11-05")],
                         ["10-18", "10-31"])
        self.assertIsNone(report.baseline_window("30 dager", "2026-11-05"))

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

    def test_html_one_card_per_product_and_story(self):
        fs = [finding(), finding(rule="before_price", claimed=1500.0, low30=1000.0, base_median=None, peak=None),
              finding(rule="deal", score=0.1, now=900.0, product_id="2", title="Mobil X", category="mobil",
                      shop="Ærlig")]
        page = report.html_page(fs, STATS, ["bfa_report.csv"], False, shops=[shop_row()])
        self.assertIn("2 mistenkelige tilbud</b> hos 1 butikker", page)
        self.assertEqual(page.count("<article class='card"), 2)            # one card per product
        self.assertIn("data-rules=' before_price fake_discount '", page)   # both findings on the TV card
        self.assertIn("<button type='button' class='cat' data-cat='mobil' aria-pressed='false'>Mobil <b>1</b>", page)
        self.assertIn("aria-pressed='true'>Alle <b>2</b>", page)
        self.assertIn("Var vanligvis 1\u00a0000\u00a0kr. Hevet til 1\u00a0300\u00a0kr 5. nov (22 dager før Black "
                      "Friday), nå «tilbud» 1\u00a0000\u00a0kr.", page)   # amounts never wrap mid-number
        self.assertIn("Black Friday 27. nov</b> · i dag", page)
        self.assertIn("Det er Black Week", page)
        self.assertIn("<span class='lab bad'>Lurepris</span>", page)       # the % is in the badge, not repeated
        self.assertIn("<span class='open'>Kontroller</span></summary>", page)
        self.assertIn("<button type='button' data-type='bad' aria-pressed='false'>Lureri</button>", page)
        self.assertIn("[hidden]{display:none!important}", page)        # cards hidden by the filters really hide
        self.assertIn("Butikken oppgir før-pris 1\u00a0500\u00a0kr", page)          # check section, before-price
        self.assertIn("Normalpris 1\u00a0000\u00a0kr = median for Butikk 2. aug–30. sep", page)
        self.assertIn("<b>Prisendringer:</b> 1. nov 1\u00a0000\u00a0kr → 5. nov 1\u00a0300\u00a0kr", page)
        self.assertIn("<span class='cap'>hevet før «tilbudet»</span>", page)     # badge says what the % is
        self.assertIn("Topp 5 lureri", page)
        self.assertIn("href='files/bfa_report.csv'", page)   # relative, so it works under BASE_PATH

    def test_card_lists_every_shop_and_filters_on_all(self):
        fs = [finding(rule="deal", score=0.3, now=700.0, shop=s) for s in ("A", "B")]
        prices = {("prisjakt", "1"): [("A", 700.0, None, 1), ("B", 700.0, None, 1), ("C", 990.0, None, 1),
                                      ("D", 650.0, None, 0), ("Telenor", 1.0, None, 1)]}
        page = report.html_page(fs, STATS, [], False, prices=prices, collected={"prisjakt": "2026-11-27 06:40"})
        self.assertIn("data-shops='|A|B|C|D|'", page)          # operator plan price left out
        self.assertIn("Billigst nå <b>700\u00a0kr</b> hos A · 3 butikker", page)   # sold-out D not cheapest
        self.assertIn("<tr class='quiet'><td>C</td>", page)    # shop without a finding, greyed
        self.assertIn("D</td><td class='num'>650 kr <small>(utsolgt)</small>", page)
        self.assertIn("Hentet fra Prisjakt 27. nov kl. 06:40.", page)
        self.assertIn("<span class='lab good'>Ekte prisfall <small>· 2 butikker</small></span>", page)

    def test_price_steps(self):
        pts = [("d1", 10.0), ("d2", 10.0), ("d3", 12.0), ("d4", 12.4), ("d5", 9.0)]
        self.assertEqual(report.price_steps(pts), [("d1", 10.0), ("d3", 12.0), ("d5", 9.0)])
        self.assertEqual(len(report.price_steps([("d%02d" % i, float(i)) for i in range(30)])), 10)

    def test_levels_per_type(self):
        self.assertEqual([report.level(finding(rule="hike", score=s)) for s in (0.12, 0.2, 0.39, 0.4, 0.9)],
                         [1, 2, 2, 3, 3])
        self.assertEqual([report.level(finding(rule="deal", score=s)) for s in (0.06, 0.1, 0.2)], [1, 2, 3])
        self.assertEqual([report.level(finding(rule="before_price", score=s)) for s in (0.05, 0.1, 0.25)],
                         [1, 2, 3])
        self.assertEqual(report.level(finding(score=None)), 1)

    def test_html_marks_new_and_lists_gone(self):
        old, new = finding(product_id="1", title="Gammel TV"), finding(product_id="2", title="Ny TV", score=0.5)
        flags = {"since": "2026-11-20", "prev": "2026-11-26",
                 "first": {("fake_discount", "prisjakt", "1", "Butikk"): "2026-11-21"},
                 "gone": [("hike", "prisjakt", "3", "Butikk", "Borte <b>TV</b>"),
                          ("fake_discount", "prisjakt", "1", "Butikk", "Gammel TV")]}   # still flagged: not gone
        page = report.html_page([old, new], STATS, [], False, flags=flags)
        self.assertEqual(page.count("<span class='tag ny'>NY</span><span class='tag seen' hidden>"), 1)
        self.assertIn("data-first='2026-11-27'", page)          # new one starts on the report day
        self.assertIn("data-first='2026-11-21'", page)
        self.assertIn("flagget siden 21. nov", page)
        self.assertIn("<b>Siden 26. nov:</b> +1 nye · 1 borte", page)
        self.assertIn("Satt opp i pris: Borte &lt;b&gt;TV&lt;/b&gt;", page)
        self.assertIn("class='lvl l3' title='Grad: Kraftig'", page)
        for control in ("id='flvl'", "id='fsort'", "id='fnew'", "id='ftype'", "id='fq'", "id='fcat'"):
            self.assertIn(control, page)

    def test_html_first_report_marks_nothing_new(self):
        flags = {"since": "2026-11-27", "prev": None, "first": {}, "gone": []}
        page = report.html_page([finding()], STATS, [], False, flags=flags)
        self.assertNotIn("class='card bad new'", page)
        self.assertIn("Nye funn merkes fra neste innsamling.", page)
        self.assertNotIn("Siden ", report.html_page([finding()], STATS, [], False))   # no tracking yet: no line

    def test_html_warmup_leads_with_hikes(self):
        fs = [finding(rule="hike", now=1300.0), finding(rule="deal", score=0.1, now=900.0),
              finding(rule="fake_discount")]
        page = report.html_page(fs, STATS, [], False, today="2026-10-18")
        self.assertIn("Black Friday 27. nov</b> · om 40 dager", page)
        self.assertIn("Nå er det oppkjøring", page)
        self.assertIn("class='verdict warn'><b>1 priser er satt opp før Black Friday</b>", page)
        self.assertNotIn("mistenkelige tilbud</b>", page)
        self.assertLess(page.index("<b>Satt opp i pris</b>"), page.index("<b>Ekte prisfall</b>"))   # how-to order

    def test_html_off_season_has_countdown_only(self):
        page = report.html_page([], dict(STATS, last_day="2027-03-01"), [], False)
        self.assertIn("Black Friday 26. nov</b> · om 270 dager", page)
        self.assertIn("Prisvakten sover og henter ingen priser nå. Den våkner 1. aug", page)
        self.assertIn("Prisvakten sover til 1. aug", page)
        self.assertIn("hver morgen fra 1. aug til nyttår", page)
        self.assertNotIn("class='tl'", page)

    def test_html_off_season_texts(self):
        page = lambda d: report.html_page([], dict(STATS, last_day=d), [], False)
        self.assertIn("Utenfor Black Friday-sesongen", page("2027-09-15"))
        self.assertIn("gjennom julesalget og romjula", page("2026-12-20"))
        self.assertNotIn("sover", page("2026-12-31"))

    def test_html_says_which_day_the_prices_are_from(self):
        page = report.html_page([], dict(STATS, last_day="2026-10-20"), [], False, today="2026-10-21",
                                data_day="2026-10-20")
        self.assertIn("funnene gjelder prisene fra 20. okt", page)
        page = report.html_page([], dict(STATS, last_day="2026-10-21"), [], False, today="2026-10-21",
                                data_day="2026-10-21")
        self.assertNotIn("funnene gjelder prisene fra", page)

    def test_bf_rel(self):
        self.assertEqual(report.bf_rel("2026-11-26", "2026-11-27"), " (1 dag før Black Friday)")
        self.assertEqual(report.bf_rel("2026-11-27", "2026-11-27"), " (på Black Friday)")
        self.assertEqual(report.bf_rel("2026-11-30", "2026-11-27"), " (3 dager etter Black Friday)")
        self.assertEqual(report.bf_rel("2026-07-01", "2026-11-27"), "")
        self.assertEqual(report.countdown(1), "i morgen")
        self.assertEqual(report.countdown(-2), "for 2 dager siden")

    def test_nice_baseline(self):
        self.assertEqual(report.nice_baseline("tidlig 10-18–10-31"), "kun 18. okt–31. okt (lite data)")
        self.assertEqual(report.nice_baseline("dag 31–90"), "1–3 måneder siden")
        self.assertEqual(report.nice_day("2026-11-05"), "5. nov")

    def test_text_has_shop_ranking(self):
        out = report.text([finding()], STATS, shops=[shop_row()])
        self.assertIn("== Butikker med flest mistenkelige produkter (1) ==", out)
        self.assertIn("3 av 6 produkter (50 %): 2 lurepriser, 1 oppblåst før-pris", out)

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


class Types(unittest.TestCase):
    def test_every_rule_has_one_unique_name_and_definition(self):
        rules = {t.rule for t in report.TYPES}
        self.assertEqual(rules, set(analyze.BAD_RULES) | {"deal"})
        self.assertEqual(len({t.name for t in report.TYPES}), len(report.TYPES))
        for t in report.TYPES:
            self.assertTrue(t.means and t.measured and t.tone in ("bad", "warn", "good"), t.rule)

    def test_criteria_follow_the_thresholds(self):
        self.assertIn("{:.0f} %".format(analyze.HIKE * 100), report.TYPE["hike"].measured)
        self.assertIn("30 dagene", report.TYPE["before_price"].measured)

    def test_counts(self):
        self.assertEqual(report.TYPE["fake_discount"].count(1), "1 lurepris")
        self.assertEqual(report.TYPE["fake_discount"].count(3), "3 lurepriser")
