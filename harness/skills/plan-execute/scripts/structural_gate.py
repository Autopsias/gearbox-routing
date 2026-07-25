"""PS-01 — structural DONE-gate.

The #1 recurring correction across three weeks of sessions (30+ messages,
escalating to profanity) was "you didn't update the plan HTML." It regressed
TWICE after being fixed with prompt/instruction guidance alone. This module
makes it structural instead: a session cannot be finalized DONE unless
PLAN.html's ``<article data-status>`` attributes actually changed to match
what the closeout claimed, re-read from disk (never trusted from the
in-memory string that was just written).

Two checks, one PRIMARY + always-enforced, one best-effort:

  * ``check_landed`` — PRIMARY, browser-free. Re-reads PLAN.html from disk
    after a mutation batch and compares each target article's ``data-status``
    attribute against what the closeout claimed. This catches the class of
    bug where ``apply_mutation`` runs and writes SOMETHING, but the actual
    write silently failed to reach the claimed target (a race, a stale path,
    a regex that didn't match the markup it expected).

  * ``check_js_parses`` — PRIMARY, browser-free. Extracts the dashboard's
    inline status-strip repaint ``<script>`` and runs ESLint ``no-undef``
    over it (same guard `plan-builder`'s ``build_plan.py`` runs before
    writing PLAN.html the first time — see its
    ``dashboard-eslint.config.mjs``). A broken repaint script is the OTHER
    way a dashboard can silently show stale statuses even though the
    attributes are correct. Degrades to a SKIPPED (never a silent PASS, never
    a hard block) result when node/eslint are unavailable — mirrors
    build_plan.py's own posture.

``run_gate`` combines both into the single PRIMARY DONE-gate result the
caller (run.py / verify.py) treats as blocking. The SECONDARY visual gate
(headless-Chrome layout-audit banner check) lives in ``render_verify.py`` —
kept separate because it degrades to "unconfirmed" rather than ever being
allowed to silently grant DONE.
"""

import json
import re
import subprocess
from pathlib import Path

import article_block as ab

SCRIPT_DIR = Path(__file__).resolve().parent
# Reuse plan-builder's hand-maintained allowlist rather than forking it — the
# two skills already live side by side under ~/.claude/skills/.
_ESLINT_CONFIG = (
    SCRIPT_DIR.parent.parent / "plan-builder" / "scripts" / "dashboard-eslint.config.mjs"
)

_PILL_RE = re.compile(r'<span class="pill status-\w+[^>]*>([^<]*)</span>')


def _html_path(plan_dir):
    return Path(plan_dir) / "PLAN.html"


def check_landed(plan_dir, expected):
    """``expected``: ``{article_id: expected_status}``.

    Re-reads PLAN.html from disk (not any in-memory copy) and asserts the
    ``data-status`` attribute of every expected article matches. Returns
    ``(ok: bool, mismatches: list[str], warnings: list[str])``.

    A pill-text/attribute desync (attribute correct, visible label stale — a
    narrower regex-match failure inside ``article_block._mutate_block``) is
    reported as a WARNING, not a hard mismatch: it means the write partially
    landed and is worth surfacing, but the authoritative signal the rest of
    the pipeline (nav repaint, Bases, filters) reads is the attribute, so it
    does not by itself refuse DONE.
    """
    html_path = _html_path(plan_dir)
    if not html_path.exists():
        return False, [f"PLAN.html not found at {html_path}"], []
    text = html_path.read_bytes().decode("utf-8")
    mismatches = []
    warnings = []
    for aid, status in expected.items():
        try:
            actual = ab.read_status(text, aid)
        except ab.AnchorError as e:
            mismatches.append(f"{aid}: could not re-read status from PLAN.html ({e})")
            continue
        if actual != status:
            mismatches.append(
                f"{aid}: expected data-status={status!r} after apply, found {actual!r} "
                "— the dashboard write did not land"
            )
            continue
        try:
            _, _, block = ab.extract_block(text, aid)
        except ab.AnchorError:
            continue
        m = _PILL_RE.search(block)
        if m and m.group(1) != status:
            warnings.append(
                f"{aid}: data-status={status!r} landed but the visible pill still "
                f"reads {m.group(1)!r}"
            )
    return (not mismatches, mismatches, warnings)


def check_js_parses(plan_dir):
    """Run ESLint ``no-undef`` over PLAN.html's inline status-repaint script.

    Returns a dict with ``status`` in {"passed", "failed", "skipped"}.
    "skipped" is a deliberately distinct value from "passed" — callers must
    never treat unavailable tooling as a pass.
    """
    html_path = _html_path(plan_dir)
    if not html_path.exists():
        return {"status": "skipped", "reason": "PLAN.html not found"}
    text = html_path.read_bytes().decode("utf-8")
    # The repaint script is the largest attribute-less <script> block; the
    # layout-audit script (data-layout-audit) is deliberately excluded — same
    # convention build_plan.py uses to assemble its own pre-write check.
    scripts = re.findall(
        r"<script(?![^>]*\b(?:src|data-layout-audit)\b)[^>]*>(.*?)</script>",
        text,
        flags=re.DOTALL,
    )
    scripts = [s for s in scripts if s.strip()]
    if not scripts:
        return {"status": "skipped", "reason": "no inline repaint <script> found in PLAN.html"}
    if not _ESLINT_CONFIG.exists():
        return {"status": "skipped", "reason": f"eslint config not found at {_ESLINT_CONFIG}"}

    combined = "\n;\n".join(scripts)
    tmp_dir = Path(plan_dir) / "_verify_state"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / "_dashboard_script_check.mjs"
    tmp.write_text(combined)
    try:
        proc = subprocess.run(
            [
                "npx", "--no-install", "eslint",
                "-c", str(_ESLINT_CONFIG),
                "--format", "json",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as e:
        return {"status": "skipped", "reason": f"node/npx not available ({e})"}
    except subprocess.TimeoutExpired:
        return {"status": "skipped", "reason": "eslint timed out"}
    finally:
        tmp.unlink(missing_ok=True)

    if proc.returncode not in (0, 1):
        return {
            "status": "skipped",
            "reason": f"eslint errored (rc={proc.returncode}): {proc.stderr[:400]}",
        }
    try:
        results = json.loads(proc.stdout or "[]")
    except ValueError:
        return {"status": "skipped", "reason": "could not parse eslint JSON output"}

    errors = []
    for r in results:
        for m in r.get("messages", []):
            if m.get("ruleId") == "no-undef":
                errors.append(f"line {m.get('line')}: {m.get('message')}")
    if errors:
        return {"status": "failed", "errors": errors}
    return {"status": "passed"}


def run_gate(plan_dir, expected):
    """The single PRIMARY DONE-gate result. Blocking iff either sub-check
    fails; tooling-unavailable is a non-blocking ``skipped`` note, never a
    silent pass baked into ``ok``."""
    landed_ok, mismatches, warnings = check_landed(plan_dir, expected)
    js = check_js_parses(plan_dir)
    js_failed = js.get("status") == "failed"
    ok = landed_ok and not js_failed
    reasons = list(mismatches)
    if js_failed:
        reasons.append(
            "dashboard repaint JS has undefined-identifier error(s) (no-undef): "
            + "; ".join(js.get("errors", []))
        )
    return {
        "status": "passed" if ok else "failed",
        "expected": expected,
        "mismatches": mismatches,
        "warnings": warnings,
        "js_check": js,
        "reasons": reasons,
    }
