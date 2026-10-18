# app.py - tiny web UI for bf-analyzer (stdlib only, runs on a Raspberry Pi 1B)
#   GET  /            latest analysis as HTML
#   GET|POST /run     start a collection run in the background (one at a time)
#   GET  /files/<n>   download a file from OUTPUT_DIR (reports, database)
#   GET  /status      plain-text status
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bfa import analyze, net, report
from bfa.store import Store

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.abspath(os.environ.get("OUTPUT_DIR", os.path.join(APP_DIR, "outputs")))
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.environ["OUTPUT_DIR"] = OUTPUT_DIR

_run_lock = threading.Lock()
_last_run = {"code": None}
_page_cache = {}


def _db_mtime(path):
    # WAL mode writes go to the -wal file first
    return max(os.path.getmtime(p) for p in (path, path + "-wal") if os.path.exists(p))


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

    def _files(self):
        return sorted(n for n in os.listdir(OUTPUT_DIR) if not n.startswith(".") and not n.endswith(("-wal", "-shm")))

    def _index(self):
        # Analysis takes seconds on a Pi 1B: cache it until the database (or the day) changes.
        store = Store()
        try:
            key = (_db_mtime(store.path), net.today_oslo(), _run_lock.locked())
            if _page_cache.get("key") != key:
                findings = analyze.analyze(store, key[1])
                shops = analyze.rank_shops(findings, store.shop_product_counts(key[1]))
                _page_cache.update(key=key, page=report.html_page(
                    findings, store.stats(), self._files(), key[2], shops))
        finally:
            store.close()
        return _page_cache["page"]

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            return self._send(200, self._index())
        if url.path == "/status":
            return self._send(200, "running: {}\nlast exit code: {}\noutput dir: {}\n".format(
                _run_lock.locked(), _last_run["code"], OUTPUT_DIR), "text/plain; charset=utf-8")
        if url.path == "/run":
            return self._run(url)
        if url.path.startswith("/files/"):
            name = os.path.basename(urllib.parse.unquote(url.path[len("/files/"):]))
            path = os.path.join(OUTPUT_DIR, name)
            if not name or not os.path.isfile(path):
                return self._send(404, "not found", "text/plain")
            ctype = {".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                     ".log": "text/plain; charset=utf-8"}.get(os.path.splitext(name)[1], "application/octet-stream")
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/run":
            return self._run(url)
        return self._send(404, "not found", "text/plain")

    def _run(self, url):
        started = start_run(urllib.parse.parse_qs(url.query))
        if self.command == "POST" and "text/html" in self.headers.get("Accept", ""):
            return self._send(303, "", extra={"Location": "/"})
        return self._send(202 if started else 409,
                          "started\n" if started else "a run is already in progress\n", "text/plain")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print("bf-analyzer on http://{}:{}  (OUTPUT_DIR={})".format(host, port, OUTPUT_DIR), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
