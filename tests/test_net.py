import unittest

from _helpers import fixture
from bfa import net


class OsloDay(unittest.TestCase):
    def test_summer(self):
        self.assertEqual(net.oslo_day("2026-07-19T22:00:00Z"), "2026-07-20")
        self.assertEqual(net.oslo_day("2026-07-19T21:59:59Z"), "2026-07-19")

    def test_winter(self):
        self.assertEqual(net.oslo_day("2026-12-01T23:00:00Z"), "2026-12-02")
        self.assertEqual(net.oslo_day("2026-12-01T22:30:00Z"), "2026-12-01")

    def test_dst_boundaries_2026(self):
        # DST starts 2026-03-29 01:00 UTC, ends 2026-10-25 01:00 UTC
        self.assertEqual(net.oslo_day("2026-03-28T23:30:00Z"), "2026-03-29")   # +1 -> 00:30
        self.assertEqual(net.oslo_day("2026-03-29T21:30:00Z"), "2026-03-29")   # +2 -> 23:30
        self.assertEqual(net.oslo_day("2026-03-29T22:30:00Z"), "2026-03-30")   # +2 -> 00:30
        self.assertEqual(net.oslo_day("2026-10-24T22:30:00Z"), "2026-10-25")   # +2 -> 00:30
        self.assertEqual(net.oslo_day("2026-10-25T22:30:00Z"), "2026-10-25")   # +1 -> 23:30
        self.assertEqual(net.oslo_day("2026-10-25T23:30:00Z"), "2026-10-26")   # +1 -> 00:30

    def test_fractional_seconds_and_other_year(self):
        self.assertEqual(net.oslo_day("2025-07-19T22:00:00.123Z"), "2025-07-20")

    def test_today_oslo_format(self):
        d = net.today_oslo()
        self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$")


class RobotsReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pj = net.parse_robots(fixture("prisjakt_robots.txt"))
        cls.kl = net.parse_robots(fixture("klarna_robots.txt"))

    def pjok(self, path):
        return net.robots_allows(self.pj, "https://www.prisjakt.no" + path)

    def klok(self, path):
        return net.robots_allows(self.kl, "https://www.klarna.com" + path)

    def test_prisjakt_allowed(self):
        self.assertTrue(self.pjok("/c/tv"))
        self.assertTrue(self.pjok("/c/tv?offset=44"))
        self.assertTrue(self.pjok("/product.php?p=14520667"))
        self.assertTrue(self.pjok("/"))

    def test_prisjakt_disallowed(self):
        self.assertFalse(self.pjok("/search?q=tv"))
        self.assertFalse(self.pjok("/c/tv?sort=price"))
        self.assertFalse(self.pjok("/c/tv?a=1&b=2&c=3"))
        self.assertFalse(self.pjok("/go-to-shop/1/offer/2"))
        self.assertFalse(self.pjok("/admin/x"))

    def test_prisjakt_offset_allow_beats_ampersand_disallow(self):
        self.assertTrue(self.pjok("/c/tv?a=1&b=2&offset=3"))
        self.assertTrue(self.pjok("/c/tv?a=1&b=2&c=3&offset=3"))

    def test_other_agent_groups_are_ignored(self):
        # "Disallow: /" belongs to MSIECrawler/HTTrack, not "*"
        self.assertTrue(self.pjok("/c/mobiltelefoner"))

    def test_klarna(self):
        self.assertTrue(self.klok("/no/shopping/pl/cl2/3400799077/TV/LG-65/"))
        self.assertTrue(self.klok("/no/shopping/cl/cl2/TV/?page=2"))
        self.assertFalse(self.klok("/no/shopping/public/pricehistory/product/1"))
        self.assertFalse(self.klok("/no/shopping/gotostore/1"))
        self.assertFalse(self.klok("/no/shopping/cl/cl2/TV/?search=x"))

    def test_klarna_wildcard_in_middle(self):
        self.assertFalse(self.klok("/no/shopping/pl/cl2/1/TV/x?p=3"))
        self.assertTrue(self.klok("/global/shop/creator/abc"))   # Allow, longer than Disallow /global/shop/
        self.assertFalse(self.klok("/global/shop/other"))


class RobotsSynthetic(unittest.TestCase):
    def test_dollar_anchor(self):
        r = net.parse_robots("User-agent: *\nDisallow: /*.pdf$\n")
        self.assertFalse(net.robots_allows(r, "https://x.no/a/b.pdf"))
        self.assertTrue(net.robots_allows(r, "https://x.no/a/b.pdf?x=1"))
        self.assertTrue(net.robots_allows(r, "https://x.no/a/b.pdf.html"))

    def test_longest_wins_and_allow_wins_tie(self):
        r = net.parse_robots("User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /c\nAllow: /c\n")
        self.assertFalse(net.robots_allows(r, "https://x.no/a/x"))
        self.assertTrue(net.robots_allows(r, "https://x.no/a/b/x"))
        self.assertTrue(net.robots_allows(r, "https://x.no/c"))

    def test_empty_disallow_and_comments_and_no_rules(self):
        r = net.parse_robots("# hi\nUser-agent: *\nDisallow:   # nothing\n")
        self.assertEqual(r, [])
        self.assertTrue(net.robots_allows(r, "https://x.no/anything?q=1"))

    def test_grouped_user_agents(self):
        r = net.parse_robots("User-agent: Foo\nUser-agent: *\nDisallow: /x\n\nUser-agent: Bar\nDisallow: /y\n")
        self.assertFalse(net.robots_allows(r, "https://x.no/x"))
        self.assertTrue(net.robots_allows(r, "https://x.no/y"))

    def test_regex_chars_are_escaped(self):
        r = net.parse_robots("User-agent: *\nDisallow: /a.b+c\n")
        self.assertFalse(net.robots_allows(r, "https://x.no/a.b+c"))
        self.assertTrue(net.robots_allows(r, "https://x.no/aXb+c"))

    def test_bare_host_is_slash(self):
        r = net.parse_robots("User-agent: *\nDisallow: /\n")
        self.assertFalse(net.robots_allows(r, "https://x.no"))


if __name__ == "__main__":
    unittest.main()
