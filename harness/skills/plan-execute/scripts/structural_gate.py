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

RP-04 — the CONTAINMENT half
----------------------------
The two checks above close the *status* half of "you didn't update the plan
HTML". ``containment_report`` closes the *membership* half: the six surfaces
that silently drifted when six sessions were hand-added to a live plan on
2026-08-02, every one of which passed the status gate and an anchor-balance
check (see ``PROPOSED-add-session.md``). Each surface is named as a concrete
artifact rather than as a category:

  1. ``[data-cat]``      an item ``<article data-cat>`` inside a ``<section
                         data-cat>`` that does not match — the defect that put
                         every new article in whichever section came last.
  2. ``[session-strip]`` the build-time ``<nav class="session-strip">`` chip
                         set vs the manifest session set (it stuck at S01–S10
                         while the plan had 20 sessions).
  3. ``[section-blurb]`` ``<span class="section-blurb">N sessions · M
                         items</span>`` vs the manifest totals.
  4. ``[workstreams]``   the infographic const array (``WORKSTREAMS`` /
                         ``PHASES`` / ``PILLARS`` / …) that drives the progress
                         bar — it read 92% · 12-of-13 against a real 70% ·
                         19-of-27 because new items were in no group.
  5. ``[document-order]``session-article document order, which is what the
                         "Up next" panel actually scans (``sessions.find(…)``
                         over ``querySelectorAll('article.session')``) — NOT
                         the dependency graph.
  6. ``[header-totals]`` the ``<header class="page">`` meta line and the
                         ``SESSION_TOTAL_COUNT`` build stamp.

Plus two checks that are not page surfaces but fail the same way — silently,
by dissolving a guarantee the operator believes is still there:

  7. ``[membership]``    manifest ⇄ article, both directions.
  8. ``[parallel-group]``every member of a ``parallel_group`` must have an
                         IDENTICAL ``depends_on`` set. ``dispatch.next_action``
                         batches "the first ready session + ready peers sharing
                         its group": asymmetric deps make peers ready at
                         different times, so the group quietly degrades to
                         sequential dispatch with no error anywhere.

``containment_problems`` returns ``(check, subject, message)`` triples — the
``(check, subject)`` pair is a stable identity, so a caller can tell a problem a
change INTRODUCED from one it inherited even when the message wording shifts.
``containment_report`` is the same thing as display strings. It is consumed two
ways:

  * ``plan_mutate._run`` runs it on the COMPUTED generation before the commit
    point, and refuses a mutation that introduces a NEW problem — the gate
    that polices the mutation engine, checked before any live file is touched.
  * ``run_gate`` carries it as ``containment`` WARNINGS on every apply /
    verify-finalize. Deliberately non-blocking there: plans built before this
    check exists routinely have pre-existing problems (a live plan in this very
    repo has 5 items in no infographic group), and turning those into DONE
    refusals would strand running plans on a defect no session introduced.
"""

import json
import re
import os
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import article_block as ab
import parallel_contract as pcon

SCRIPT_DIR = Path(__file__).resolve().parent
# Reuse plan-builder's hand-maintained allowlist rather than forking it — the
# two skills already live side by side under ~/.claude/skills/.
_SKIP_ENV = "PLAN_EXECUTE_SKIP_BROWSER_CHECKS"

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
    # Same unit-suite opt-out as render_verify.check (see conftest.py).
    # MEASURED: three `npx eslint` spawns cost 5.2 s per test. "skipped"
    # is already the honest value for unavailable tooling here and is
    # deliberately NOT "passed", so nothing is turned green by this.
    # DEFAULT IS ON; only the unit suite sets the variable.
    if os.environ.get(_SKIP_ENV) == "1":
        return {"status": "skipped",
                "reason": f"eslint check skipped — {_SKIP_ENV}=1 (unit suite)"}
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


# --------------------------------------------------------------------------
# RP-04 — containment
# --------------------------------------------------------------------------
# The infographic renderers each name their group list differently; the progress
# bar counts item statuses through whichever one this plan uses.
_GROUP_CONSTS = ("WORKSTREAMS", "PHASES", "LEVELS", "SPOKES", "PILLARS", "GROUPS")
_COUNTS_RE = re.compile(r"(\d+)\s+sessions?\s*·\s*(\d+)\s+items?")
_TOTAL_COUNT_RE = re.compile(r"SESSION_TOTAL_COUNT:\s*(\d+)")


class _PageIndex(HTMLParser):
    """The handful of PLAN.html facts the containment checks compare.

    HTMLParser (not regex) on purpose: the dashboard's inline script contains
    string literals like ``'</section>'`` and ``'<strong>'``, which a regex scan
    would happily mistake for markup. HTMLParser puts ``<script>`` in CDATA mode
    and never sees them.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles = []          # ordered: {id, kind, cat, section_cat}
        self.strip_chips = []       # ordered session ids in the nav strip
        self.blurbs = {}            # section data-cat -> blurb text
        self.header_meta = None     # <header class="page"> … <div class="meta">
        self.session_total = None   # SESSION_TOTAL_COUNT build stamp
        self._sections = []
        self._in_strip = False
        self._in_header = False
        self._capture = None

    @staticmethod
    def _classes(attrs):
        return (attrs.get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = self._classes(a)
        if tag == "section":
            self._sections.append(a.get("data-cat"))
        elif tag == "header" and "page" in cls:
            self._in_header = True
        elif tag == "nav" and "session-strip" in cls:
            self._in_strip = True
        elif tag == "a" and self._in_strip and "strip-chip" in cls:
            self.strip_chips.append(a.get("data-session"))
        elif tag == "article":
            kind = "session" if "session" in cls else "item" if "item" in cls else None
            if kind:
                self.articles.append({
                    "id": a.get("id"),
                    "kind": kind,
                    "cat": a.get("data-cat"),
                    "section_cat": self._sections[-1] if self._sections else None,
                })
        elif tag == "span" and "section-blurb" in cls:
            self._capture = ("blurb", self._sections[-1] if self._sections else None, [])
        elif tag == "div" and "meta" in cls and self._in_header:
            self._capture = ("meta", None, [])

    def handle_endtag(self, tag):
        if tag == "section" and self._sections:
            self._sections.pop()
        elif tag == "nav":
            self._in_strip = False
        elif tag == "header":
            self._in_header = False
        elif tag in ("span", "div") and self._capture:
            kind, key, buf = self._capture
            text = "".join(buf).strip()
            if kind == "blurb":
                self.blurbs.setdefault(key, text)
            elif self.header_meta is None:
                self.header_meta = text
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            self._capture[2].append(data)

    def handle_comment(self, data):
        m = _TOTAL_COUNT_RE.search(data)
        if m:
            self.session_total = int(m.group(1))


def index_page(html_text):
    idx = _PageIndex()
    idx.feed(html_text)
    return idx


def infographic_groups(html_text):
    """``(const_name, groups)`` for the array driving the Plan-Achievement
    progress bar, or ``(None, None)`` when the page carries no group array.

    Parsed with ``json.JSONDecoder().raw_decode`` — the array is written by
    ``json.dumps``, so the JSON decoder itself is the correct reader; a
    hand-rolled bracket matcher would trip on braces inside strings.
    """
    for name in _GROUP_CONSTS:
        m = re.search(r"\bconst\s+%s\s*=\s*" % name, html_text)
        if not m:
            continue
        try:
            groups, _ = json.JSONDecoder().raw_decode(html_text, m.end())
        except ValueError:
            return name, None
        if isinstance(groups, list):
            return name, groups
    return None, None


def _p(check, subject, message, tag=None):
    """One problem: ``(check, subject, "[tag] message")``.

    The ``(check, subject)`` pair is a STABLE identity — that is what lets a
    mutation tell a problem it introduced from one the plan already had. Comparing
    the message TEXT instead does not work: "items ['a','b'] appear in no group"
    becomes "items ['a','b','c'] …" when an unrelated item is added, which reads
    as a brand-new problem and refuses a mutation that broke nothing. So every
    per-entity problem is emitted once PER ENTITY, keyed by it.

    ``tag`` is the DISPLAY label when it differs from the identity — the two
    ``workstreams-*`` checks are separate identities but one named surface.
    """
    return (check, subject, f"[{tag or check}] {message}")


def containment_problems(html_text, manifest):
    """Every containment problem as ``(check, subject, message)`` triples.

    Pure: takes the page TEXT and the manifest DICT, so a mutation can check the
    generation it is about to write before writing it.
    """
    idx = index_page(html_text)
    sessions = [s["id"] for s in manifest.get("sessions", [])]
    items = [it["id"] for it in manifest.get("items", [])]
    session_set, item_set = set(sessions), set(items)
    problems = []

    # 1 — every item article inside the <section> its own data-cat names.
    for a in idx.articles:
        if a["kind"] != "item":
            if a["section_cat"] != "sessions":
                problems.append(_p(
                    "data-cat", a["id"],
                    f"session article {a['id']!r} sits in section "
                    f"data-cat={a['section_cat']!r}, not the 'sessions' section"))
            continue
        if a["cat"] != a["section_cat"]:
            problems.append(_p(
                "data-cat", a["id"],
                f"item article {a['id']!r} declares data-cat={a['cat']!r} but sits inside "
                f"<section data-cat={a['section_cat']!r}> — it renders under the wrong "
                "heading and its category filter chip will not find it"))

    # 2 — the nav strip is the whole session set, in order.
    chips = [c for c in idx.strip_chips if c]
    for sid in sessions:
        if sid not in set(chips):
            problems.append(_p(
                "session-strip", sid,
                f'session {sid!r} has no chip in <nav class="session-strip"> — the nav is '
                f"stuck at {len(chips)} of {len(sessions)} sessions"))
    for c in chips:
        if c not in session_set:
            problems.append(_p(
                "session-strip", c,
                f"nav chip {c!r} names a session that is in no manifest"))
    if set(chips) == session_set and chips != sessions:
        problems.append(_p(
            "session-strip", "order",
            f"chip order {chips} does not match manifest session order {sessions}"))

    # 3 — "N sessions · M items" under the Session plan heading.
    blurb = idx.blurbs.get("sessions")
    if blurb:
        m = _COUNTS_RE.search(blurb)
        if m and (int(m.group(1)), int(m.group(2))) != (len(sessions), len(items)):
            problems.append(_p(
                "section-blurb", "sessions",
                f"session-plan blurb reads {blurb!r} but the manifest has "
                f"{len(sessions)} sessions · {len(items)} items"))

    # 4 — the const array the progress bar counts through.
    const_name, groups = infographic_groups(html_text)
    if const_name and groups is None:
        problems.append(_p(
            "workstreams", const_name,
            f"const {const_name} is not parseable JSON — the progress bar renders from "
            "it, so a broken array silently freezes it"))
    elif groups is not None:
        placed = [i for g in groups if isinstance(g, dict) for i in (g.get("items") or [])]
        counted = len(set(placed) & item_set)
        for ghost in sorted(set(placed) - item_set):
            problems.append(_p(
                "workstreams-ghost", ghost,
                f"const {const_name} references item {ghost!r}, which is in no manifest — "
                "the progress bar counts a denominator that does not exist",
                tag="workstreams"))
        # Only meaningful when this plan actually binds items to groups; an
        # infographic with no bindings at all drives the bar from the item cards.
        if placed:
            for iid in items:
                if iid not in set(placed):
                    problems.append(_p(
                        "workstreams-unplaced", iid,
                        f"item {iid!r} appears in no {const_name} group, so the progress "
                        f"bar counts {counted} of {len(items)} items and overstates "
                        "completion", tag="workstreams"))

    # 5 — "Up next" reads document order, not the dependency graph.
    doc_sessions = [a["id"] for a in idx.articles if a["kind"] == "session"]
    if doc_sessions != sessions and set(doc_sessions) == session_set:
        problems.append(_p(
            "document-order", "sessions",
            f"session articles appear as {doc_sessions} but the manifest order is "
            f"{sessions} — the 'Up next' panel scans document order, so it would name "
            "the wrong session"))

    # 6 — header totals.
    if idx.header_meta:
        m = _COUNTS_RE.search(idx.header_meta)
        if m and (int(m.group(1)), int(m.group(2))) != (len(sessions), len(items)):
            problems.append(_p(
                "header-totals", "meta",
                f"header meta line reads {idx.header_meta!r} but the manifest has "
                f"{len(sessions)} sessions · {len(items)} items"))
    if idx.session_total is not None and idx.session_total != len(sessions):
        problems.append(_p(
            "header-totals", "session_total_count",
            f"SESSION_TOTAL_COUNT stamp is {idx.session_total} but the manifest has "
            f"{len(sessions)} sessions"))

    # 7 — manifest ⇄ article, both directions.
    doc_ids = {a["id"] for a in idx.articles}
    for aid in [*sessions, *items]:
        if aid not in doc_ids:
            kind = "session" if aid in session_set else "item"
            problems.append(_p(
                "membership", aid, f"manifest {kind} {aid!r} has no article on the page"))
    for a in idx.articles:
        known = session_set if a["kind"] == "session" else item_set
        if a["id"] not in known:
            problems.append(_p(
                "membership", a["id"],
                f"{a['kind']} article {a['id']!r} is on the page but in no manifest entry "
                "— it can never be dispatched or closed out"))

    problems += _group_coherence(manifest)
    return problems


def _group_coherence(manifest):
    """``parallel_group`` members must share an identical ``depends_on`` set.

    Rule R1 of the frozen parallel-group contract. The rule itself lives in
    ``parallel_contract`` (the module both this skill and plan-builder import,
    so the build-time and dispatch-time gates cannot drift); this wrapper only
    re-labels it as a containment problem with its own stable
    ``(check, subject)`` identity.
    """
    return [_p("parallel-group", pg, msg) for pg, msg in pcon.symmetry_problems(manifest)]


def group_coherence_problems(manifest):
    """``_group_coherence`` as display messages. Guards the MANIFEST — the copy
    ``dispatch.next_action`` reads, and the one a hand edit touches.
    plan-builder's ``validate_spec`` guards the same rule in ``spec.json``."""
    return [msg for _, _, msg in _group_coherence(manifest)]


def containment_report(html_text, manifest):
    """``containment_problems`` as display messages."""
    return [msg for _, _, msg in containment_problems(html_text, manifest)]


def check_containment(plan_dir):
    """``containment_report`` over the plan on disk. ``{status, problems}``;
    ``skipped`` when either input is missing/unreadable (never a silent pass)."""
    html_path = _html_path(plan_dir)
    manifest_path = Path(plan_dir) / "manifest.json"
    if not html_path.exists() or not manifest_path.is_file():
        return {"status": "skipped", "reason": "PLAN.html or manifest.json not found"}
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError as e:
        return {"status": "skipped", "reason": f"manifest.json is not valid JSON: {e}"}
    try:
        problems = containment_report(html_path.read_bytes().decode("utf-8"), manifest)
    except Exception as e:  # noqa: BLE001 — never let a page quirk break `status`
        return {"status": "skipped", "reason": f"could not read PLAN.html's structure: {e}"}
    return {"status": "failed" if problems else "passed", "problems": problems}


def run_gate(plan_dir, expected):
    """The single PRIMARY DONE-gate result. Blocking iff either sub-check
    fails; tooling-unavailable is a non-blocking ``skipped`` note, never a
    silent pass baked into ``ok``.

    ``containment`` rides along as WARNINGS — see the module docstring for why
    it is not blocking here but IS blocking on the mutation path."""
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
    # Carried under its own key, NOT folded into `warnings` — one problem printed
    # twice in one JSON blob reads as two problems.
    containment = check_containment(plan_dir)
    return {
        "status": "passed" if ok else "failed",
        "expected": expected,
        "mismatches": mismatches,
        "warnings": warnings,
        "js_check": js,
        "containment": containment,
        "reasons": reasons,
    }
