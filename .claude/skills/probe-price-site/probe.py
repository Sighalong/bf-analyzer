#!/usr/bin/env python3
"""Compact probe of a price-comparison page: prints a short summary instead of raw HTML.

Usage: python3 -I probe.py URL [--grep KEY ...] [--ctx 160] [--save FILE]
Stdlib only, Python 3.7+.
"""
import argparse
import os
import gzip
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "nb-NO,nb;q=0.9"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        code, final = r.status, r.geturl()
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        code, final, body = e.code, url, e.read()
    return code, final, body.decode("utf-8", "replace")


def robots_ok(url):
    # urllib.robotparser gets Prisjakt wrong (no wildcards, and Cloudflare 403s its user agent),
    # so use the project's own matcher, as the collector does.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    from bfa import net
    try:
        return net.allowed(url)
    except Exception as e:
        return "unknown ({})".format(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--grep", nargs="*", default=[])
    ap.add_argument("--ctx", type=int, default=160)
    ap.add_argument("--save")
    a = ap.parse_args()

    print("robots.txt allows *:", robots_ok(a.url))
    code, final, html = fetch(a.url)
    print("status", code, "bytes", len(html), "final", final)
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            f.write(html)
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    print("title:", (t.group(1).strip()[:120] if t else None))

    host = urllib.parse.urlsplit(final).netloc
    links = re.findall(r'href="(/[^"#]+)"', html)
    shapes = {}
    for l in links:
        shape = re.sub(r"\d+", "N", l.split("?")[0])
        shape = "/".join(shape.split("/")[:4])
        shapes[shape] = shapes.get(shape, 0) + 1
    print("top link shapes on", host, ":")
    for s, n in sorted(shapes.items(), key=lambda x: -x[1])[:10]:
        print("  {:4d} {}".format(n, s))

    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            print("ld+json: unparsable"); continue
        for item in (d if isinstance(d, list) else [d]):
            keys = sorted(item.keys()) if isinstance(item, dict) else []
            offers = item.get("offers") if isinstance(item, dict) else None
            n = len(offers.get("offers", [])) if isinstance(offers, dict) else ""
            print("ld+json", item.get("@type") if isinstance(item, dict) else "?", "keys", keys[:12], "offers", n)

    markers = ["__NEXT_DATA__", "self.__next_f.push", "__REACT_QUERY_STATE__", "__APOLLO_STATE__",
               "__NUXT__", "window.__INITIAL_STATE__", "cf-chl", "captcha"]
    print("markers:", {k: html.count(k) for k in markers if k in html})

    for g in a.grep:
        hits = [m.start() for m in re.finditer(re.escape(g), html)]
        print("grep {!r}: {} hits".format(g, len(hits)))
        for i in hits[:2]:
            print("   ...", html[max(0, i - 40):i + a.ctx].replace("\n", " "), "...")


if __name__ == "__main__":
    sys.exit(main())
