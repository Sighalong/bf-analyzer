# bf-analyzer

Catches Black Friday price gouging in Norway: shops that raise a price in the weeks before Black Friday and then "discount" it back. Runs daily on a **Raspberry Pi 1B** (armv6, 512 MB, Python 3.7+). It collects per-shop prices into SQLite and builds its own history over time.

## Hard constraints
- **Stdlib only.** No Playwright/Chromium (no armv6 build), no FastAPI/pydantic, no requests. `requirements.txt` stays empty of runtime deps.
- Python 3.7 syntax: `Optional[...]`, `List[...]`, no walrus, no `X | None`.
- Obey robots.txt. Never get around bot protection. Wait ≥2 s between requests per site.

## Sources (verified 2026-10-18)
- **Prisjakt** `/c/<slug>` category pages (paging with `?offset=N`); product `product.php?p=ID`. `/search?*` is disallowed by robots.txt, so don't use it. Product data is Next.js flight data (`self.__next_f.push([1,"..."])`, json-decode each string and join them): `offerRows` (per-shop `price.amount`, `originalPrice.amount`, `priceType`, `stockStatus`, `shop.name`), `historyItems` (cheapest across shops, price in **øre**, change points over 3 months), `lowestPrice` (kr).
- **Klarna** (prisguide.no redirects here): `/no/shopping/cl/cl<ID>/<Name>/?page=N` category pages, `/no/shopping/pl/...` product pages. Per-shop offers are in JSON-LD `Product.offers.offers[]` (`price`, `seller.name`, `availability`). Price-history URLs are disallowed by robots.txt. There's no GTIN, so products aren't matched across sites.

## Model use (keep tokens low)
- Delegate with the project agents in `.claude/agents/`: `site-prober` (haiku) for any page investigation, `pi-checker` (haiku) after code changes, `test-writer` (sonnet) for tests, `pi-deployer` (sonnet) for Pi setup, `deal-analyst` (haiku) for reading results, `ux-reviewer` (sonnet) for dashboard clarity (design judgement needs more than haiku; opus not needed).
- Keep design and detection-logic changes in the main session.
- Never paste raw HTML or full DB dumps into the conversation. Use the skill scripts, which print summaries.

## Privacy
- Commits use `Sighalong <194371259+Sighalong@users.noreply.github.com>` (repo-local config). Never a real name, private email or Mac user name.
- No home-directory paths or user names in tracked files. Collected data (`outputs/`, `*.sqlite`) and logs are never committed.
- `.githooks/pre-commit` runs `tools/privacy-check.sh`. Enable it once per clone: `git config core.hooksPath .githooks`.
