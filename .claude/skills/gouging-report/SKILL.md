---
name: gouging-report
description: Summarize suspected Black Friday price gouging (prices raised before Black Friday, then "discounted") and genuine deals from the bf-analyzer SQLite database. Use when asked what the collected data shows.
---

# Gouging report

```bash
python3 -m bfa report --top 15 --format text      # compact, already ranked
python3 -m bfa report --product <source:id> --format text   # one product's per-shop series
```

Don't open the SQLite file or the CSV directly; the text report is the compact view.

Rules (see `bfa/analyze.py`):
- **hike**: peak in the last 30 days is ≥10 % above the shop's baseline median (30–90 days ago).
- **fake discount**: the price has dropped ≥8 % from that peak, but it's still not meaningfully below the baseline low (within 3 %).
- **before-price claim**: the shop's stated før-pris is above its own lowest price in the previous 30 days.
- **genuine deal**: current price ≥5 % below the 90-day baseline low.

Summarize in under 20 lines: top flagged products with shop, baseline → peak → now, rule, and days of data. Then the best genuine deals. Note when data is thin (< 21 days of history).
