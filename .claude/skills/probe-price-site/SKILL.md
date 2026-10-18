---
name: probe-price-site
description: Check whether a Norwegian price-comparison page (Prisjakt, Klarna, or a new site) can be read without a browser, whether robots.txt allows it, and where the price/offer data sits. Use before adding or fixing a site parser.
---

# Probe a price site

Never dump raw HTML into the conversation. Use the probe script; it prints a ~30-line summary.

```bash
python3 -I .claude/skills/probe-price-site/probe.py "<url>" --grep '"offerRows"' '"historyItems"' --save /tmp/page.html
```

1. Check `robots.txt allows *`. If it says False, stop: the source must not use that URL pattern.
2. `status` 403/503 or `markers` showing `cf-chl`/`captcha` means bot protection. Report it and stop; never try to get around it.
3. Find the data: `ld+json` Product with offers is the most stable; next is embedded state (`self.__next_f.push` = Next.js flight data, decode each string with `json.loads` and concatenate).
4. Use `--grep` with candidate keys and a small `--ctx` to see the shape. Work on the saved file with short Python snippets (`python3 -I`), never `cat` it.
5. Wait at least 2 s between requests to the same site.

Report back in under 15 lines: URL pattern, allowed or not, where the data is, the field names for price, shop, claimed before-price and date, and a sample value.
