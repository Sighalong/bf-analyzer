"""Shared helpers for the bfa tests (stdlib only, Python 3.7+)."""
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


def day_back(today, n):
    """The day `n` days before `today` (YYYY-MM-DD)."""
    return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def series(today, spans):
    """Build analyze_series rows. spans = [(age_from, age_to, price)] inclusive ages, oldest first.

    Rows come out sorted by day; original_price None, in_stock 1.
    """
    rows = {}
    for a, b, price in spans:
        for age in range(min(a, b), max(a, b) + 1):
            rows[day_back(today, age)] = price
    return [(d, rows[d], None, 1) for d in sorted(rows)]
