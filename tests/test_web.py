import http.client
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

os.environ.setdefault("OUTPUT_DIR", tempfile.mkdtemp())
import app  # noqa: E402


class BasePathTest(unittest.TestCase):
    """Everything is served below app.BASE (/bfpris), so nginx can route just that path to us."""

    def setUp(self):
        quiet = app.Handler.log_message
        app.Handler.log_message = lambda *a: None
        self.addCleanup(setattr, app.Handler, "log_message", quiet)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        c.request(method, path)
        r = c.getresponse()
        r.read()
        return r

    def test_default_base(self):
        self.assertEqual(app.BASE, "/bfpris")

    def test_root_and_bare_base_redirect(self):
        for p in ("/", "/bfpris"):
            r = self.req(p)
            self.assertEqual(r.status, 302, p)
            self.assertEqual(r.getheader("Location"), "/bfpris/")

    def test_routes_below_base(self):
        self.assertEqual(self.req("/bfpris/status").status, 200)
        self.assertEqual(self.req("/bfpris/files/missing.csv").status, 404)

    def test_outside_base_not_served(self):
        for p in ("/status", "/files/bfa.sqlite", "/other/", "/bfprisx/status"):
            self.assertEqual(self.req(p).status, 404, p)
        self.assertEqual(self.req("/run", "POST").status, 404)


if __name__ == "__main__":
    unittest.main()
