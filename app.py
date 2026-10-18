# app.py - tiny web UI for bf-analyzer (stdlib only, runs on a Raspberry Pi 1B)
# Everything lives under BASE_PATH (default /bfpris), so the app can sit behind a web server
# such as nginx next to other sites (see pi/nginx-bfpris.conf):
#   GET  /bfpris/            latest analysis as HTML ("/" and "/bfpris" redirect here)
#   GET|POST /bfpris/run     start a collection run in the background (one at a time)
#   GET  /bfpris/files/<n>   download a file from OUTPUT_DIR (reports, database)
#   GET  /bfpris/status      plain-text status
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bfa import analyze, net, report
from bfa.store import Store, default_path

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.abspath(os.environ.get("OUTPUT_DIR", os.path.join(APP_DIR, "outputs")))
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.environ["OUTPUT_DIR"] = OUTPUT_DIR
BASE = os.environ.get("BASE_PATH", "/bfpris").rstrip("/")   # "" serves from the root

_run_lock = threading.Lock()
_last_run = {"code": None}
_page_cache = {}
_page_lock = threading.Lock()   # one page build at a time
REBUILD_SECONDS = 300           # while a run writes to the database, rebuild at most this often
WAIT_PAGE = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="10">'
             '<title>Black Friday-prisvakt</title><p style="font-family:sans-serif">'
             'Lager rapporten … Siden lastes på nytt av seg selv.</p>')


def _db_mtime(path):
    # WAL mode writes go to the -wal file first
    return max([os.path.getmtime(p) for p in (path, path + "-wal") if os.path.exists(p)] or [0])


def _collect(args):
    cmd = [sys.executable, "-m", "bfa", "run", "--format", "none"] + args
    with open(os.path.join(OUTPUT_DIR, "last_run.log"), "w", encoding="utf-8") as log:
        res = subprocess.run(cmd, cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT)
    _last_run["code"] = res.returncode
    _run_lock.release()


def start_run(query):
    if not _run_lock.acquire(blocking=False):
        return False
    args = []
    cats = query.get("categories", [])
    if cats:
        args += ["--categories"] + cats
    for key in ("max_per_category", "max_products"):
        if key in query and query[key][0].isdigit():
            args += ["--" + key.replace("_", "-"), query[key][0]]
    threading.Thread(target=_collect, args=(args,), daemon=True).start()
    return True


def _files():
    return sorted(n for n in os.listdir(OUTPUT_DIR) if not n.startswith(".") and not n.endswith(("-wal", "-shm")))


def _build_page():
    """Run the analysis and cache the page. The caller holds _page_lock."""
    try:
        store = Store()
        try:
            key = (_db_mtime(store.path), net.today_oslo(), _run_lock.locked())
            findings = analyze.analyze(store, key[1])
            shops = analyze.rank_shops(findings, store.shop_product_counts(key[1]))
            page = report.html_page(findings, store.stats(), _files(), key[2], shops)
        finally:
            store.close()
        _page_cache.update(key=key, page=page, at=time.time())
    finally:
        _page_lock.release()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _index(self):
        # Analysis takes ~40 s on a busy Pi 1B, longer than a browser waits. Serve the last page at
        # once and rebuild it in the background when the database, the day or the run state changes.
        key = (_db_mtime(default_path()), net.today_oslo(), _run_lock.locked())
        old = _page_cache.get("key")
        if old != key and (old is None or old[1:] != key[1:] or time.time() - _page_cache["at"] > REBUILD_SECONDS):
            if _page_lock.acquire(blocking=False):
                threading.Thread(target=_build_page, daemon=True).start()
        return _page_cache.get("page", WAIT_PAGE)

    def _sub(self, url):
        """Path below BASE, or None outside it."""
        p = url.path
        if not p.startswith(BASE + "/"):
            return None
        return p[len(BASE):]

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if BASE and url.path in ("/", BASE):
            return self._send(302, "", extra={"Location": BASE + "/"})
        sub = self._sub(url)
        if sub == "/":
            return self._send(200, self._index())
        if sub == "/status":
            return self._send(200, "running: {}\nlast exit code: {}\noutput dir: {}\n".format(
                _run_lock.locked(), _last_run["code"], OUTPUT_DIR), "text/plain; charset=utf-8")
        if sub == "/run":
            return self._run(url)
        if sub and sub.startswith("/files/"):
            name = os.path.basename(urllib.parse.unquote(sub[len("/files/"):]))
            path = os.path.join(OUTPUT_DIR, name)
            if not name or not os.path.isfile(path):
                return self._send(404, "not found", "text/plain")
            ctype = {".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                     ".log": "text/plain; charset=utf-8"}.get(os.path.splitext(name)[1], "application/octet-stream")
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self._sub(urllib.parse.urlsplit(self.path)) == "/run":
            return self._run(urllib.parse.urlsplit(self.path))
        return self._send(404, "not found", "text/plain")

    def _run(self, url):
        started = start_run(urllib.parse.parse_qs(url.query))
        if self.command == "POST" and "text/html" in self.headers.get("Accept", ""):
            return self._send(303, "", extra={"Location": BASE + "/"})
        return self._send(202 if started else 409,
                          "started\n" if started else "a run is already in progress\n", "text/plain")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print("bf-analyzer on http://{}:{}{}/  (OUTPUT_DIR={})".format(host, port, BASE, OUTPUT_DIR), flush=True)
    _page_lock.acquire()
    threading.Thread(target=_build_page, daemon=True).start()   # warm the cache before the first visit
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
