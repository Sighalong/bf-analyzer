"""Polite HTTP fetching (stdlib only): robots.txt check, per-host delay, gzip, retries."""
import gzip
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (X11; Linux armv6l) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36 bf-analyzer/2.0")

DELAY_S = 3.0          # minimum gap between two requests to the same host
TIMEOUT_S = 40
RETRIES = 2

_last_hit = {}
_robots = {}


class Blocked(Exception):
    """robots.txt disallows the URL, or the site answered with bot protection."""


def parse_robots(text):
    """Rules for 'User-agent: *' as [(allow, compiled_regex, specificity)].

    Implements the Google/RFC 9309 matching that urllib.robotparser lacks: '*' and '$'
    wildcards, longest matching rule wins, Allow wins a tie.
    """
    rules, in_star, last_was_agent = [], False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = [x.strip() for x in line.split(":", 1)]
        field = field.lower()
        if field == "user-agent":
            if not last_was_agent:
                in_star = False
            in_star = in_star or value == "*"
            last_was_agent = True
            continue
        last_was_agent = False
        if in_star and field in ("allow", "disallow") and value:
            pat = re.escape(value).replace(r"\*", ".*")
            if pat.endswith(r"\$"):
                pat = pat[:-2] + "$"
            rules.append((field == "allow", re.compile(pat), len(value)))
    return rules


def robots_allows(rules, url):
    p = urllib.parse.urlsplit(url)
    path = (p.path or "/") + ("?" + p.query if p.query else "")
    best = None
    for allow, rx, spec in rules:
        if rx.match(path) and (best is None or spec > best[1] or (spec == best[1] and allow)):
            best = (allow, spec)
    return True if best is None else best[0]


def _robots_for(url):
    p = urllib.parse.urlsplit(url)
    key = p.scheme + "://" + p.netloc
    if key not in _robots:
        try:
            _robots[key] = parse_robots(_http_get(key + "/robots.txt"))
        except urllib.error.HTTPError as e:
            # RFC 9309: 4xx = no rules; 5xx = treat the whole site as disallowed for now
            _robots[key] = [] if e.code < 500 else [(False, re.compile(""), 0)]
        except (urllib.error.URLError, OSError):
            _robots[key] = [(False, re.compile(""), 0)]
    return _robots[key]


def allowed(url):
    return robots_allows(_robots_for(url), url)


def _wait_turn(url):
    host = urllib.parse.urlsplit(url).netloc
    gap = time.monotonic() - _last_hit.get(host, -1e9)
    if gap < DELAY_S:
        time.sleep(DELAY_S - gap)
    _last_hit[host] = time.monotonic()


def _http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip",
        "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body.decode("utf-8", "replace")


def fetch(url):
    """Return page HTML. Raises Blocked or the last network error."""
    if not allowed(url):
        raise Blocked("robots.txt disallows " + url)
    err = None
    for attempt in range(RETRIES + 1):
        _wait_turn(url)
        try:
            return _http_get(url)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked("HTTP {} (bot protection or rate limit) for {}".format(e.code, url))
            if e.code == 404:
                raise
            err = e
        except (urllib.error.URLError, OSError) as e:
            err = e
        time.sleep(5 * (attempt + 1))
    raise err


# ---- dates ---------------------------------------------------------------

def _last_sunday(year, month):
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def oslo_offset(utc_dt):
    """Europe/Oslo UTC offset (EU DST rules) without needing zoneinfo (Python 3.9+)."""
    y = utc_dt.year
    start = _last_sunday(y, 3).replace(hour=1)
    end = _last_sunday(y, 10).replace(hour=1)
    naive = utc_dt.replace(tzinfo=None)
    return timedelta(hours=2 if start <= naive < end else 1)


def oslo_day(iso_utc):
    """'2026-07-19T22:00:00Z' -> '2026-07-20' (the Oslo calendar day)."""
    dt = datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (dt + oslo_offset(dt)).strftime("%Y-%m-%d")


def today_oslo():
    now = datetime.now(timezone.utc)
    return (now + oslo_offset(now)).strftime("%Y-%m-%d")
