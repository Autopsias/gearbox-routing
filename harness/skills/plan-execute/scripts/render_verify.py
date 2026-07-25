"""PS-01 — SECONDARY, best-effort visual DONE-gate.

Headless-Chrome ``--dump-dom`` of PLAN.html, checking the in-page
layout-audit banner (``#layout-audit-banner``, an isolated try/catch-wrapped
``<script data-layout-audit>`` already baked into the dashboard template) for
a real post-render finding — horizontal overflow / clipped text that a static
lint cannot see.

This is deliberately SEPARATE from ``structural_gate.py``'s PRIMARY gate:

  * PRIMARY (structural_gate.py) is browser-free and ALWAYS enforced.
  * SECONDARY (this module) needs a real browser. When one isn't available
    the result is an explicit ``"unavailable"`` status — never silently
    treated as a pass, and never allowed to hard-strand a session that has
    already cleared the primary gate (an environment with no Chrome must
    still be able to finish a plan).

Only a CONFIRMED finding (banner rendered visible after JS ran) is blocking.
"""

import re
import shutil
import subprocess
from pathlib import Path

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]


def _find_chrome():
    for c in _CHROME_CANDIDATES:
        if c.startswith("/"):
            if Path(c).exists():
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def check(plan_dir, timeout=20):
    """Returns a dict with ``status`` in:
    ``confirmed`` (rendered clean), ``failed`` (banner visible — real
    layout finding), ``not_applicable`` (template has no banner element),
    ``unavailable`` (no headless Chrome on this host)."""
    html_path = Path(plan_dir) / "PLAN.html"
    if not html_path.exists():
        return {"status": "unavailable", "reason": "PLAN.html not found"}

    chrome = _find_chrome()
    if not chrome:
        return {
            "status": "unavailable",
            "reason": "render-verify unavailable — no headless Chrome/Chromium found "
            "on this host; structural gate passed, visual unconfirmed",
        }

    url = html_path.resolve().as_uri()
    try:
        proc = subprocess.run(
            [
                chrome, "--headless=new", "--disable-gpu", "--dump-dom",
                "--virtual-time-budget=4000", url,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {
            "status": "unavailable",
            "reason": f"render-verify unavailable — headless Chrome failed to run ({e}); "
            "structural gate passed, visual unconfirmed",
        }

    dom = proc.stdout or ""
    m = re.search(r'<div[^>]*id="layout-audit-banner"[^>]*>', dom)
    if not m:
        return {"status": "not_applicable", "reason": "no #layout-audit-banner in this template"}
    tag = m.group(0)
    if "hidden" in tag:
        return {"status": "confirmed"}
    # Banner rendered without `hidden` -> the in-page audit found a real finding.
    text_m = re.search(
        r'<div[^>]*id="layout-audit-banner"[^>]*>([^<]*)</div>', dom
    )
    detail = text_m.group(1).strip() if text_m else "(banner visible, text not captured)"
    return {"status": "failed", "reason": detail}
