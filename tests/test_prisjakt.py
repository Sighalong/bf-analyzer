import json
import unittest

from _helpers import fixture
from bfa import prisjakt


def flight_page(obj_text, title="X - Sammenlign priser hos Prisjakt"):
    return ('<html><head><title>{}</title></head><body><script>self.__next_f.push([1,{}])</script>'
            '</body></html>').format(title, json.dumps(obj_text))


class ParseProductFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = prisjakt.parse_product(fixture("prisjakt_product.html"), "14520667")

    def test_basics(self):
        self.assertEqual(self.p["source"], "prisjakt")
        self.assertEqual(self.p["product_id"], "14520667")
        self.assertEqual(self.p["url"], "https://www.prisjakt.no/product.php?p=14520667")
        self.assertEqual(self.p["title"], 'LG C5 65" 4K Ultra HD OLED evo AI Smart TV (2025) - OLED65C54LA')

    def test_offers(self):
        got = [(o["shop"], o["price"], o["original_price"], o["in_stock"]) for o in self.p["offers"]]
        self.assertEqual(got, [
            ("Johnsen Elektro", 17990.0, None, True),
            ("CDON", 18763.0, None, True),
            ("CS MEGASTORE", 18893.0, None, True),
            ("Multicom", 32625.0, None, False),
        ])

    def test_history_ore_to_kr_with_oslo_day(self):
        h = self.p["lowest_history"]
        self.assertEqual(len(h), 24)
        # first item: 2026-07-19T22:00:00Z is 2026-07-20 in Oslo; 1 770 800 ore = 17 708 kr
        self.assertEqual(tuple(h[0]), ("2026-07-20", 17708.0))
        self.assertEqual(tuple(h[-1]), ("2026-10-16", 17990.0))

    def test_lowest_now(self):
        self.assertEqual(self.p["lowest_now"], 17990.0)


class ParseProductSynthetic(unittest.TestCase):
    def offer(self, **kw):
        o = {"id": "1-1", "productId": 5, "price": {"amount": 100}, "originalPrice": {"amount": 100},
             "condition": "New", "isPaidMembership": False, "stockStatus": "InStock",
             "shop": {"name": "A"}, "shopId": 1}
        o.update(kw)
        return o

    def parse(self, offers, pid="5"):
        return prisjakt.parse_product(flight_page('0:{"offerRows":%s}' % json.dumps(offers)), pid)

    def test_original_price_only_when_higher(self):
        p = self.parse([
            self.offer(id="a", originalPrice={"amount": 150}, shop={"name": "Hi"}),
            self.offer(id="b", originalPrice={"amount": 100}, shop={"name": "Same"}),
            self.offer(id="c", originalPrice={"amount": 80}, shop={"name": "Lower"}),
            self.offer(id="d", originalPrice=None, shop={"name": "None"}),
        ])
        got = dict((o["shop"], o["original_price"]) for o in p["offers"])
        self.assertEqual(got, {"Hi": 150.0, "Same": None, "Lower": None, "None": None})

    def test_filters(self):
        p = self.parse([
            self.offer(id="ok", shop={"name": "Ok"}),
            self.offer(id="ok", shop={"name": "Dup"}),                         # duplicate id
            self.offer(id="m", isPaidMembership=True),                         # membership price
            self.offer(id="u", condition="Used"),                              # not new
            self.offer(id="o", productId=99),                                  # other product
            self.offer(id="z", price={"amount": 0}),                           # no price
            self.offer(id="s", stockStatus="OutOfStock", shop={"name": "Out"}),
        ])
        self.assertEqual([(o["shop"], o["in_stock"]) for o in p["offers"]], [("Ok", True), ("Out", False)])

    def test_shop_falls_back_to_shop_id(self):
        p = self.parse([self.offer(shop=None, shopId=77)])
        self.assertEqual(p["offers"][0]["shop"], "77")

    def test_missing_data_gives_empty_result_and_title_fallback(self):
        p = prisjakt.parse_product("<html><head><title>Foo &amp; Bar - Sammenlign priser hos X</title></head></html>", "9")
        self.assertEqual(p["offers"], [])
        self.assertEqual(p["lowest_history"], [])
        self.assertIsNone(p["lowest_now"])
        self.assertEqual(p["title"], "Foo & Bar")

    def test_title_falls_back_to_url_without_anything(self):
        p = prisjakt.parse_product("<html></html>", "9")
        self.assertEqual(p["title"], prisjakt.product_url("9"))

    def test_history_skips_membership_and_zero_prices(self):
        text = '0:{"historyItems":%s,"lowestPrice":0}' % json.dumps([
            {"date": "2026-08-01T10:00:00Z", "price": 12345, "isPaidMembership": False},
            {"date": "2026-08-02T10:00:00Z", "price": 9900, "isPaidMembership": True},
            {"date": "2026-08-03T10:00:00Z", "price": 0, "isPaidMembership": False},
        ])
        p = prisjakt.parse_product(flight_page(text), "5")
        self.assertEqual([tuple(x) for x in p["lowest_history"]], [("2026-08-01", 123.45)])
        self.assertIsNone(p["lowest_now"])

    def test_flight_chunks_are_joined(self):
        full = '0:{"lowestPrice":4321}'
        page = ''.join('<script>self.__next_f.push([1,%s])</script>' % json.dumps(c)
                       for c in (full[:8], full[8:]))
        self.assertEqual(prisjakt.parse_product(page, "5")["lowest_now"], 4321.0)


class Discover(unittest.TestCase):
    def test_paging_stops_when_nothing_new(self):
        calls = []
        pages = {
            "https://www.prisjakt.no/c/tv": "product.php?p=1 product.php?p=2 product.php?p=1",
            "https://www.prisjakt.no/c/tv?offset=44": "product.php?p=3 product.php?p=2",
            "https://www.prisjakt.no/c/tv?offset=88": "product.php?p=3",
        }

        def fake(url):
            calls.append(url)
            return pages[url]

        self.assertEqual(prisjakt.discover("tv", 10, fetch=fake), ["1", "2", "3"])
        self.assertEqual(len(calls), 3)

    def test_limit(self):
        fake = lambda url: "product.php?p=1 product.php?p=2 product.php?p=3"
        self.assertEqual(prisjakt.discover("tv", 2, fetch=fake), ["1", "2"])

    def test_urls(self):
        self.assertEqual(prisjakt.category_url("tv"), "https://www.prisjakt.no/c/tv")
        self.assertEqual(prisjakt.category_url("tv", 44), "https://www.prisjakt.no/c/tv?offset=44")


if __name__ == "__main__":
    unittest.main()
