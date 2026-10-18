---
name: ux-review
description: Render the bf-analyzer dashboard from a synthetic Black Friday season (no network) and review it for clarity on phone and desktop. Use before and after UI changes.
---

# Review the dashboard

```bash
python3 -I .claude/skills/ux-review/render.py /tmp/bfa_ux.html            # Black Friday day
python3 -I .claude/skills/ux-review/render.py /tmp/bfa_ux.html --today 2026-10-25   # early season, few flags
```

It prints the page size, headings, controls and counts. Read `bfa/report.py` for the markup; never paste the HTML.
To look at it, serve `/tmp` with `python3 -m http.server` and open `/bfa_ux.html` in the browser pane at 375 px and desktop width.
