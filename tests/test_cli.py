import csv
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from _helpers import series
from bfa import cli, net
from bfa.store import Store

TODAY = "2026-11-10"


class ResolveCategories(unittest.TestCase):
    def test_known_names(self):
        keys, unknown = cli.resolve_categories(["TV", "Bærbare PC-er", "Skjermer", "Robotstøvsugere"])
        self.assertEqual(keys, ["tv", "laptop", "skjerm", "robotstovsuger"])
        self.assertEqual(unknown, [])

    def test_unknown_reported_and_dups_dropped(self):
        keys, unknown = cli.resolve_categories(["tv", "TV", "Kaffetrakter", "mobiltelefoner", "???"])
        self.assertEqual(keys, ["tv", "mobil"])
        self.assertEqual(unknown, ["Kaffetrakter", "???"])

    def test_default_categories_all_resolve(self):
        keys, unknown = cli.resolve_categories(cli.DEFAULT_CATEGORIES)
        self.assertEqual(keys, cli.DEFAULT_CATEGORIES)
        self.assertEqual(unknown, [])

    def test_norm_key(self):
        self.assertEqual(cli.norm_key("Bærbare PC-er"), "baerbarepcer")
        self.assertEqual(cli.norm_key("Robotstøvsugere"), "robotstovsugere")
        self.assertEqual(cli.norm_key("Håndverk"), "handverk")

    def test_every_alias_has_a_source_category(self):
        from bfa import klarna, prisjakt
        for key in set(cli.ALIASES.values()):
            self.assertIn(key, prisjakt.CATEGORIES)
            self.assertIn(key, klarna.CATEGORIES)


class ReportEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "t.sqlite")
        self.delay = net.DELAY_S
        self.addCleanup(setattr, net, "DELAY_S", self.delay)
        s = Store(self.db)
        for day, price, _, _ in series(TODAY, [(90, 11, 1000.0), (10, 0, 1400.0)]):
            s.save_product({"source": "klarna", "product_id": "9", "title": "Pipe | TV", "url": "http://u/9",
                            "offers": [{"shop": "Butikk", "price": price, "original_price": None,
                                        "in_stock": True}],
                            "lowest_history": [], "lowest_now": None}, day, category="tv")
        s.close()

    def run_main(self, *extra):
        out = os.path.join(self.tmp, "out")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTPUT_DIR": out}), redirect_stdout(buf):
            rc = cli.main(["report", "--db", self.db, "--today", TODAY, "--format", "none"] + list(extra))
        return rc, out, buf.getvalue()

    def test_writes_csv_and_md(self):
        rc, out, stdout = self.run_main()
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "bfa_report.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["rule"], rows[0]["source"], rows[0]["shop"], rows[0]["peak"], rows[0]["score"]),
                         ("hike", "klarna", "Butikk", "1400.0", "40.0"))
        with open(os.path.join(out, "bfa_report.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("# Black Friday-analyse", md)
        self.assertIn("[Pipe / TV](http://u/9)", md)         # pipe escaped for the table
        self.assertIn("bfa_report.csv", stdout)
        self.assertNotIn("Data:", stdout)                    # --format none prints no summary

    def test_text_format_prints_summary(self):
        buf = io.StringIO()
        out = os.path.join(self.tmp, "out2")
        with mock.patch.dict(os.environ, {"OUTPUT_DIR": out}), redirect_stdout(buf):
            cli.main(["report", "--db", self.db, "--today", TODAY])
        self.assertIn("Data: 1 produkter", buf.getvalue())

    def test_min_price_filters_findings(self):
        _, out, _ = self.run_main("--min-price-nok", "5000", "--out-prefix", os.path.join(self.tmp, "p"))
        with open(os.path.join(self.tmp, "p.csv"), encoding="utf-8") as fh:
            self.assertEqual(len(list(csv.DictReader(fh))), 0)

    def test_product_view(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTPUT_DIR": os.path.join(self.tmp, "o3")}), redirect_stdout(buf):
            rc = cli.main(["--db", self.db, "--product", "klarna:9"])
        self.assertEqual(rc, 0)
        self.assertIn("Butikk:", buf.getvalue())
        self.assertIn("11-10=1 400", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
