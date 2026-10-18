import json
import unittest

from _helpers import fixture
from bfa import klarna


def page_with(ld):
    return '<html><head><script type="application/ld+json">{}</script></head></html>'.format(json.dumps(ld))


def offer(shop, price, avail="https://schema.org/InStock"):
    return {"@type": "Offer", "price": price, "availability": avail, "seller": {"@type": "Organization", "name": shop}}


class ParseFixtures(unittest.TestCase):
    def test_aggregate_offer(self):
        url = "https://www.klarna.com/no/shopping/pl/cl2/3523785140/TV/Samsung/"
        p = klarna.parse_product(fixture("klarna_aggregate.html"), "3523785140", url)
        self.assertEqual(p["source"], "klarna")
        self.assertEqual(p["product_id"], "3523785140")
        self.assertEqual(p["url"], url)
        self.assertEqual(p["title"], "Samsung 65 Inch 4K LED TV TU65U7005HUXNA")
        self.assertEqual([(o["shop"], o["price"]) for o in p["offers"]],
                         [("NetOnNet", 6490.0), ("Komplett", 7990.0), ("Samsung", 7990.0), ("POWER", 7999.0)])
        self.assertTrue(all(o["in_stock"] and o["original_price"] is None for o in p["offers"]))
        self.assertEqual(p["lowest_now"], 6490.0)
        self.assertEqual(p["lowest_history"], [])

    def test_single_offer(self):
        p = klarna.parse_product(fixture("klarna_single.html"), "3400799077", "u")
        self.assertEqual(p["title"], 'LG 65" C5 AI 4K OLED Smart TV')
        self.assertEqual([(o["shop"], o["price"], o["in_stock"]) for o in p["offers"]],
                         [("CS MEGASTORE", 18893.0, True)])
        self.assertEqual(p["lowest_now"], 18893.0)


class ParseSynthetic(unittest.TestCase):
    def parse(self, offers_obj):
        return klarna.parse_product(page_with({"@type": "Product", "name": "N", "offers": offers_obj}), "1", "u")

    def test_dedup_keeps_cheapest_variant_per_shop(self):
        p = self.parse({"@type": "AggregateOffer", "offers": [
            offer("Shop A", "1200.00"), offer("Shop B", "900.00"), offer("Shop A", "1000.00"),
            offer("Shop A", "1100.00")]})
        got = dict((o["shop"], o["price"]) for o in p["offers"])
        self.assertEqual(got, {"Shop A": 1000.0, "Shop B": 900.0})
        self.assertEqual(len(p["offers"]), 2)
        self.assertEqual(p["lowest_now"], 900.0)

    def test_bare_list_of_offers(self):
        p = self.parse([offer("A", "10"), offer("B", "5")])
        self.assertEqual(sorted(o["shop"] for o in p["offers"]), ["A", "B"])

    def test_skips_bad_offers(self):
        p = self.parse({"@type": "AggregateOffer", "offers": [
            offer("", "10"), offer("Zero", "0"), offer("NaN", "abc"), offer("None", None), offer("Good", "7")]})
        self.assertEqual([o["shop"] for o in p["offers"]], ["Good"])

    def test_stock_flag(self):
        p = self.parse({"@type": "AggregateOffer", "offers": [
            offer("In", "10"), offer("Out", "10", "https://schema.org/OutOfStock")]})
        self.assertEqual(dict((o["shop"], o["in_stock"]) for o in p["offers"]), {"In": True, "Out": False})

    def test_no_product_ld_json(self):
        page = page_with({"@type": "BreadcrumbList"})
        p = klarna.parse_product(page, "1", "http://u")
        self.assertEqual(p["offers"], [])
        self.assertIsNone(p["lowest_now"])
        self.assertEqual(p["title"], "http://u")

    def test_invalid_json_is_skipped(self):
        page = ('<script type="application/ld+json">{oops</script>'
                + page_with({"@type": "Product", "name": "N", "offers": offer("A", "5")}))
        self.assertEqual(klarna.parse_product(page, "1", "u")["offers"][0]["shop"], "A")


class Discover(unittest.TestCase):
    def test_pages_and_dedup(self):
        link = '"/no/shopping/pl/cl2/{0}/TV/Name-{0}/"'
        pages = {
            "https://www.klarna.com/no/shopping/cl/cl2/TV/": link.format(11) + link.format(12) + link.format(11),
            "https://www.klarna.com/no/shopping/cl/cl2/TV/?page=2": link.format(13) + link.format(12),
            "https://www.klarna.com/no/shopping/cl/cl2/TV/?page=3": link.format(13),
        }
        got = klarna.discover("/no/shopping/cl/cl2/TV/", 10, fetch=lambda u: pages[u])
        self.assertEqual([pid for pid, _ in got], ["11", "12", "13"])
        self.assertEqual(got[0][1], "https://www.klarna.com/no/shopping/pl/cl2/11/TV/Name-11/")

    def test_limit(self):
        page = ''.join('"/no/shopping/pl/cl2/{0}/TV/N/"'.format(i) for i in range(1, 6))
        got = klarna.discover("/x/", 3, fetch=lambda u: page)
        self.assertEqual(len(got), 3)


if __name__ == "__main__":
    unittest.main()
