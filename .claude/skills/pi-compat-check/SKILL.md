---
name: pi-compat-check
description: Verify the code still runs on a Raspberry Pi 1B (armv6, 512 MB RAM, Python 3.7+, stdlib only, no browser). Use after any change to Python files or requirements, before calling work done.
---

# Pi 1B compatibility check

```bash
/opt/homebrew/bin/python3 -I .claude/skills/pi-compat-check/check.py .
python3 -m unittest discover -s tests -q      # macOS system Python 3.9 = closest local stand-in for old Pi Python
```

The checker prints only problems and a summary line. Fix each problem:
- `X | None` / `list[...]` annotations → `Optional[X]` / `List[...]` from `typing`.
- `zoneinfo` → wrap in `try/except ImportError` with a fixed-offset fallback.
- Third-party imports → replace with stdlib (`urllib.request`, `json`, `sqlite3`, `http.server`, `csv`). The Pi 1B can't install Playwright/Chromium, and wheels with compiled code (pydantic-core, greenlet, lxml) often have no armv6 build.

Memory: avoid holding more than one page (~1 MB) in memory at a time; process products one by one.

Report: "pass" or the remaining problems, in under 10 lines.
