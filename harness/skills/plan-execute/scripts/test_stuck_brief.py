"""S11 — the stuck protocol (RS-03) and BLOCKED decision-brief validation (RS-04).

Each block pairs a PLANT (what must be refused / must NOT fire) with an ALLOW
(the same machinery succeeding), because a refusal test that would also pass
against a no-op implementation proves nothing.

  RS-03  two consecutive failures with the SAME normalised root-cause signature
         ARM the stuck protocol (research pass injected into the rework feedback
         + a `stuck_protocol_armed` event); two DIFFERENT errors do not.
  RS-04  a BLOCKED closeout on a v5 plan without a `decision_brief` is refused
         by name; a well-formed brief is accepted AND rendered into
         HALT_NOTICE.txt; an old-schema plan is unaffected.

Run: pytest skills/plan-execute/scripts/test_stuck_brief.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import stuck_protocol as sp  # noqa: E402
import verify as vfy  # noqa: E402
from test_shipping import make_plan  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
GOOD_BRIEF = {
    "attempts": [
        "Ran the migration against the local replica — schema matches",
        "Tried the read-only app role — it cannot CREATE INDEX",
    ],
    "findings": [
        {"source": "PostgreSQL 16 docs, CREATE INDEX CONCURRENTLY",
         "takeaway": "CONCURRENTLY still needs table ownership"},
        {"source": "docs/ops/db-access.md", "takeaway": "prod DDL is DBA-gated by design"},
    ],
    "options": [
        "Ask the on-call DBA to run it in the next window — slowest, documented path",
        "Ship behind a flag on the un-indexed table — works now, degrades at ~50k rows",
        "Move the index to the nightly job — no human needed, lands a day later",
    ],
    "recommendation": "Option 1 — the runbook makes DBA-gated DDL a deliberate control",
}


def _sessions():
    return [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"}]


@pytest.fixture
def plan(tmp_path):
    """A freshly built plan — stamped at the fresh pre-v8 stamp (rad.PRE_V8_STAMP)."""
    return str(make_plan(tmp_path, _sessions()))


def _restamp(plan_dir, version):
    p = Path(plan_dir) / "manifest.json"
    m = json.loads(p.read_text())
    m["plan_schema_version"] = version
    p.write_text(json.dumps(m, indent=2))


def _closeout_text(sid="s01", *, result="BLOCKED", brief=None, checkpoint=None):
    body = {
        "session": sid, "result": result,
        "items_completed": ["w-01"] if result == "DONE" else [],
        "items_blocked": ["w-01"] if result == "BLOCKED" else [],
        "notes": {"w-01": "outcome", sid: "session outcome"},
        "dispatch_next": result != "BLOCKED",
        "human_checkpoint_reason": checkpoint,
    }
    if brief is not None:
        body["decision_brief"] = brief
    return f"work done.\n\n<plan-execute-closeout>\n{json.dumps(body)}\n</plan-execute-closeout>\n"


def _apply(plan_dir, tmp_path, capsys, **kw):
    """Drive the REAL begin -> apply path. Returns (printed_json, exit_code)."""
    out = Path(tmp_path) / "s01.out.md"
    out.write_text(_closeout_text(**kw))
    run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    code = 0
    try:
        run.cmd_apply(plan_dir, "s01", str(out))
    except SystemExit as e:
        code = e.code
    printed = json.loads(capsys.readouterr().out)
    return printed, code


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    evs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [e for e in evs if e.get("event") == name]


# ==========================================================================
# RS-03 — the stuck protocol has a real trigger, not a paragraph
# ==========================================================================
# The whole feature rests on "same root cause" being computable. These pin the
# normalisation: volatile detail (paths, line numbers, tmpdirs, whitespace,
# timings) must NOT change the signature; a different error class must.
SAME_A = """
  File "/Users/rc/proj/src/db.py", line 41, in connect
    raise ConnectionError("could not connect to host db-1 port 5432")
ConnectionError: could not connect to host db-1 port 5432
"""
SAME_B = """
  File "/tmp/pytest-of-rc/pytest-88/proj/src/db.py", line 57, in connect
        raise ConnectionError("could not connect to host db-1 port 5432")
ConnectionError: could not connect to host db-1 port 5432
"""
DIFFERENT = """
  File "/Users/rc/proj/src/parse.py", line 12, in load
    return json.loads(text)
ValueError: Expecting value: line 1 column 1
"""


# Same failure, but the volatile detail is IN the error line itself — the case
# that proves paths are stripped rather than merely absent.
PERM_A = "PermissionError: [Errno 13] Permission denied: /Users/rc/proj/build/out.txt"
PERM_B = "PermissionError: [Errno 13] Permission denied: /tmp/pytest-88/proj/build/out.txt"


def test_allow_same_root_cause_normalises_to_one_signature():
    a, b = sp.signature(SAME_A), sp.signature(SAME_B)
    assert a["sig"] == b["sig"], (a, b)
    assert a["class"] == "ConnectionError"
    # The locus survived; the volatile half (line numbers, ports, tmpdirs) did not.
    assert "could not connect" in a["locus"]
    assert "5432" not in a["locus"] and "<n>" in a["locus"]

    # Paths in the error line itself are stripped too...
    p, q = sp.signature(PERM_A), sp.signature(PERM_B)
    assert p["sig"] == q["sig"], (p, q)
    assert "<path>" in p["locus"] and "/users/rc" not in p["locus"]
    # ...but not so aggressively that two genuinely different failures collide.
    assert p["sig"] != a["sig"]


def test_plant_a_different_error_is_a_different_signature():
    assert sp.signature(SAME_A)["sig"] != sp.signature(DIFFERENT)["sig"]


# The excerpt this protocol actually sees in THIS repo is a pytest tail, and the
# original normaliser broke on exactly that shape: `\b\d+\b` left the digit glued
# to a letter in `in 12.34s`, and pytest's summary banner (which matches _ERRORISH
# and comes last) won the locus. Two runs of the SAME failing test therefore
# signed differently and the protocol never armed. Regression found by
# the acceptance review of plan-framework-upgrade.
_PYTEST_FAIL = (
    "FAILED test_worktree.py::test_merge_producer_first - "
    "AssertionError: expected 3 members, got 2\n"
    "=========== 1 failed, 396 passed in {t}s ==========="
)
_PYTEST_OTHER = (
    "FAILED test_replan.py::test_retire_refuses_live_dependent - "
    "ValueError: dependent s07 is live\n"
    "=========== 1 failed, 396 passed in {t}s ==========="
)


def test_plant_pytest_tail_signs_stably_across_runs():
    a = sp.signature(_PYTEST_FAIL.format(t="12.34"))
    b = sp.signature(_PYTEST_FAIL.format(t="431.07"))
    # Same broken test, two runs, different wall times -> one signature, so the
    # second failure arms the protocol.
    assert a["sig"] == b["sig"], (a, b)
    # The locus is the failing test, NOT the summary banner — which would reduce
    # every failing run of every different test to the same string.
    assert "test_merge_producer_first" in a["locus"]
    assert "passed in" not in a["locus"]
    # ...and two genuinely different pytest failures still differ.
    assert a["sig"] != sp.signature(_PYTEST_OTHER.format(t="9.81"))["sig"]


def test_allow_two_identical_failures_arm_the_protocol(plan):
    first = sp.record_failure(plan, "s01", SAME_A)
    assert first["consecutive"] == 1 and not first["triggered"]
    second = sp.record_failure(plan, "s01", SAME_B)
    assert second["consecutive"] == 2 and second["triggered"]
    # Persisted in RUN state (never a digest input).
    assert rsi.load_state(plan)["stuck"]["s01"]["consecutive"] == 2


def test_plant_two_different_failures_do_not_arm_the_protocol(plan):
    sp.record_failure(plan, "s01", SAME_A)
    second = sp.record_failure(plan, "s01", DIFFERENT)
    assert second["consecutive"] == 1, "a different error is progress, not a repeat"
    assert not second["triggered"]
    # ...and the counter genuinely resets rather than merely pausing: a THIRD
    # attempt that repeats the new error is the second of ITS kind, not the third.
    third = sp.record_failure(plan, "s01", DIFFERENT)
    assert third["consecutive"] == 2 and third["triggered"]


# The mechanism, exercised through the REAL rework path (verify._gate_failed),
# not through the helper in isolation.
def _fail_gate(plan_dir, excerpt):
    state = {"session_id": "s01", "gates": ["g"], "gate_status": {"g": "pending"},
             "on_fail": "rework", "max_rework": 5, "rework_count": 0, "failures": {}}
    return vfy._gate_failed(plan_dir, "s01", state, "g", excerpt)


def test_allow_rework_feedback_carries_the_research_pass_on_a_repeat(plan):
    first = _fail_gate(plan, SAME_A)
    fb = Path(first["feedback_file"]).read_text()
    assert "STUCK PROTOCOL" not in fb, "one failure must not arm it"
    assert "stuck_protocol" not in first

    second = _fail_gate(plan, SAME_B)
    fb = Path(second["feedback_file"]).read_text()
    assert "STUCK PROTOCOL — ARMED" in fb
    # The research pass is tiered and names its degradation path.
    for needle in ("Perplexity", "Exa", "Ref", "10 minutes", "decision_brief"):
        assert needle in fb, needle
    assert second["stuck_protocol"]["consecutive"] == 2
    armed = _events(plan, "stuck_protocol_armed")
    assert len(armed) == 1 and armed[0]["error_class"] == "ConnectionError"


def test_plant_rework_feedback_stays_quiet_on_two_different_errors(plan):
    _fail_gate(plan, SAME_A)
    second = _fail_gate(plan, DIFFERENT)
    assert "STUCK PROTOCOL" not in Path(second["feedback_file"]).read_text()
    assert "stuck_protocol" not in second
    assert _events(plan, "stuck_protocol_armed") == []


# --------------------------------------------------------------------------
# RS-03 / D2 — a REVIEW gate's root cause is its FINDINGS, not its banner
# --------------------------------------------------------------------------
# Found by running the loop, not by reading it. `llm-review-*` ends
# every failing run with the same sentence, and the findings themselves contain no
# error word — so that sentence was the only line `_ERRORISH` matched and EVERY
# review-gate failure signed `6c61d90dec14`. Two unrelated findings read as one
# recurring root cause. That counter is what `escalation.climb_steps` buys a model
# rung with, so the ladder would have climbed on evidence that did not exist.
#
# These drive the REAL gate (llm_review_gate.py as a subprocess, against a `claude`
# stand-in on PATH) and the REAL rework path, because a hand-written excerpt is
# exactly the synthetic shortcut that let this ship.
_FIND_A = [{"file": "skills/routing-retro/scripts/aggregate_outcomes.py", "line": 118,
            "severity": "medium",
            "summary": "ssot_version is compared with < against an int with no type check"}]
_FIND_B = [{"file": "skills/routing-retro/scripts/render_report.py", "line": 61,
            "severity": "minor",
            "summary": "a smoke-in-progress proposal ranks equal to an actionable one"}]


def _stuck_rec(plan_dir, sid="s01"):
    """The PERSISTED stuck record — what `escalation` reads, not the return value."""
    return (rsi.load_state(plan_dir).get("stuck") or {}).get(sid) or {}


def _review_gate_excerpt(tmp_path, findings, tag):
    """The excerpt `verify._run_gate` builds from a REAL failing llm-review run."""
    import os
    import subprocess

    bindir = Path(tmp_path) / f"bin-{tag}"
    bindir.mkdir(parents=True, exist_ok=True)
    # `REVIEWED_FILES: 1` because this fixture hands the gate exactly ONE
    # changed file (reviewed.py below). Since 5a1da8f the gate compares the
    # reviewer's attested count to the surface it handed over and scores a
    # mismatch -- or a missing line -- INDETERMINATE, so a stub that only emits
    # the fence now reads as "did not review the surface" and never reaches the
    # FINDINGS path this fixture exists to drive.
    answer = ("Reviewed the diff.\n\nREVIEWED_FILES: 1\n\n```json\n"
              + json.dumps(findings) + "\n```")
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n"
                    + json.dumps({"type": "result", "is_error": False, "result": answer})
                    + "\nEOF\n")
    stub.chmod(0o755)
    # A real git work tree: since 2026-08-15 the gate refuses to review a tree it
    # cannot enumerate the NEW files of (D1).
    tree = Path(tmp_path) / "tree"
    if not (tree / ".git").exists():
        tree.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        # A NON-EMPTY reviewed surface. Since 805938e the gate returns
        # INDETERMINATE when `git diff HEAD` has 0 changed files and there are 0
        # untracked ones, because a green verdict over nothing is a check that
        # cannot fail. This fixture drives the FINDINGS path, so it needs one
        # real committed-then-modified file; an empty repo now (correctly)
        # refuses before the reviewer is ever called.
        (tree / "reviewed.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "reviewed.py"], cwd=tree, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "base"], cwd=tree, check=True)
        (tree / "reviewed.py").write_text("x = 2\n")
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "llm_review_gate.py"), "--level", "low",
         "--cwd", str(tree)],
        capture_output=True, text=True,
        env=dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}"),
    )
    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    return vfy.adapter.redact((res.stderr or "") + (res.stdout or ""),
                              max_len=vfy.GATE_EXCERPT_MAX)


def test_plant_two_different_review_findings_are_not_one_root_cause(plan, tmp_path):
    a = _review_gate_excerpt(tmp_path, _FIND_A, "a")
    b = _review_gate_excerpt(tmp_path, _FIND_B, "b")
    # The gate's own banner IS in both excerpts, byte-identical — that is the trap.
    banner = "llm-review-low: FAILED with 1 finding(s)."
    assert banner in a and banner in b

    assert sp.signature(a)["sig"] != sp.signature(b)["sig"], "different findings collided"
    _fail_gate(plan, a)
    second = _fail_gate(plan, b)
    assert _stuck_rec(plan)["consecutive"] == 1, _stuck_rec(plan)
    assert "stuck_protocol" not in second
    assert "STUCK PROTOCOL" not in Path(second["feedback_file"]).read_text()
    assert _events(plan, "stuck_protocol_armed") == []


def test_allow_two_identical_review_findings_still_arm(plan, tmp_path):
    """The other control. Fixing the collision by never arming on a review gate
    would be a worse bug than the one it replaced, so the same finding twice must
    STILL arm — and must still buy the ladder its rung."""
    a1 = _review_gate_excerpt(tmp_path, _FIND_A, "a1")
    a2 = _review_gate_excerpt(tmp_path, _FIND_A, "a2")
    _fail_gate(plan, a1)
    second = _fail_gate(plan, a2)
    assert second["stuck_protocol"]["consecutive"] == 2
    assert second["stuck_protocol"]["triggered"]
    fb = Path(second["feedback_file"]).read_text()
    assert "STUCK PROTOCOL — ARMED" in fb
    # The RESEARCH PASS names what recurred, not the gate's boilerplate. Sliced off
    # the gate excerpt above it, which legitimately quotes the banner verbatim.
    brief = fb.split("## STUCK PROTOCOL", 1)[1]
    assert "aggregate_outcomes.py" in brief and "no type check" in brief
    assert "for a deliberate choice" not in brief
    assert len(_events(plan, "stuck_protocol_armed")) == 1
    # ...and the expensive consumer agrees: this is a rung the ladder may buy.
    assert sp.armed(_stuck_rec(plan))


# --------------------------------------------------------------------------
# RS-03 / D3 — the same root cause SURVIVING a rework is a repeat
# --------------------------------------------------------------------------
# Measured over 182 outcome records: 40 cohorts needed >=2 attempts,
# exactly ONE ever changed model or effort, and `escalated_from` was null on all
# 182. The ladder was fine; the signature was not. Signing the SET of findings
# makes `consecutive` reset to 1 on every attempt of a working rework loop —
# the loop changes the finding set by construction, so byte-identical finding
# sets twice running never happen and the counter can never reach TRIGGER_AT.
#
# The line drawn here: two attempts share a root cause when a finding CARRIED
# OVER — the same (file, severity) appears in both sets. Line numbers and
# summary prose are dropped from that key because both drift on a rework.

# Attempt N+1 of _FIND_A's defect: same file, same severity, RE-WORDED summary,
# moved line, plus one newly-raised unrelated finding. This is what a rework
# round genuinely looks like.
_FIND_A_REWORDED = [
    {"file": "skills/routing-retro/scripts/aggregate_outcomes.py", "line": 124,
     "severity": "medium",
     "summary": "the ssot_version comparison still assumes an int; a str raises TypeError"},
    {"file": "skills/routing-retro/scripts/render_report.py", "line": 88,
     "severity": "minor",
     "summary": "the proposal table has no stable sort key"},
]


def test_allow_a_finding_that_survives_a_rework_arms_the_protocol(plan, tmp_path):
    """THE KNOWN POSITIVE. The same defect, reported in different words on the
    next attempt, is the same root cause — and must arm, or the whole escalation
    ladder below it is unreachable."""
    a1 = _review_gate_excerpt(tmp_path, _FIND_A, "carry1")
    a2 = _review_gate_excerpt(tmp_path, _FIND_A_REWORDED, "carry2")
    # The prose really did change: identity signing would see two different runs.
    assert sp.signature(a1)["sig"] != sp.signature(a2)["sig"]

    _fail_gate(plan, a1)
    second = _fail_gate(plan, a2)
    assert _stuck_rec(plan)["consecutive"] == 2, _stuck_rec(plan)
    assert second["stuck_protocol"]["triggered"]
    assert sp.armed(_stuck_rec(plan))
    fb = Path(second["feedback_file"]).read_text()
    assert "STUCK PROTOCOL — ARMED" in fb
    # The brief names WHAT carried over, not just that something did.
    assert "aggregate_outcomes.py" in fb.split("## STUCK PROTOCOL", 1)[1]
    # ...and the expensive consumer buys exactly one rung off it.
    import escalation as esca
    _restamp(plan, esca.ESCALATION_MIN_SCHEMA)
    rsi.save_state(plan, {**rsi.load_state(plan),
                          "escalation": {"s01": {"refused": [], "generation": 0,
                                                 "climb": 0, "last_rung": 0,
                                                 "last_attempts": 0}}})
    assert esca.climb_steps(plan, "s01") == 1


# --------------------------------------------------------------------------
# RS-03 / D4 — an INDETERMINATE review verdict is a signable, armable failure
# --------------------------------------------------------------------------
# repo-health s13 repeated `verdict=INDETERMINATE` three times and never armed:
# the banner names no error class and carries no findings block, so it fell to
# the unattributable branch and `armable` was False. A reviewer that cannot
# answer, twice at the same gate and level, is the clearest "this is stuck"
# signal the system has. Signed explicitly — the general `armable` guard below
# is NOT loosened to reach it.


def _indeterminate_excerpt(tmp_path, tag, level="medium"):
    """A REAL indeterminate run: the reviewer answers, but never in the pinned
    shape, so the gate exhausts its attempts and returns INDETERMINATE (2)."""
    import os
    import subprocess

    bindir = Path(tmp_path) / f"ibin-{tag}"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n"
                    + json.dumps({"type": "result", "is_error": False,
                                  "result": f"I had trouble reading the diff ({tag})."})
                    + "\nEOF\n")
    stub.chmod(0o755)
    tree = Path(tmp_path) / "tree"
    if not (tree / ".git").exists():
        tree.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        (tree / "reviewed.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "reviewed.py"], cwd=tree, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "base"], cwd=tree, check=True)
        (tree / "reviewed.py").write_text("x = 2\n")
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "llm_review_gate.py"), "--level", level,
         "--cwd", str(tree)],
        capture_output=True, text=True,
        env=dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}"),
    )
    assert res.returncode == 2, (res.returncode, res.stdout, res.stderr)
    return vfy.adapter.redact((res.stderr or "") + (res.stdout or ""),
                              max_len=vfy.GATE_EXCERPT_MAX)


def test_allow_a_repeated_indeterminate_verdict_arms(plan, tmp_path):
    """The reviewer failing to answer twice at the same gate and level IS a
    repeat, even though the two runs quote different attempt notes."""
    a = _indeterminate_excerpt(tmp_path, "i1")
    b = _indeterminate_excerpt(tmp_path, "i2")
    sig = sp.signature(a)
    assert sig["class"] == "review-indeterminate", sig
    assert sig["armable"] is True, sig
    _fail_gate(plan, a)
    second = _fail_gate(plan, b)
    assert _stuck_rec(plan)["consecutive"] == 2, _stuck_rec(plan)
    assert second["stuck_protocol"]["triggered"] and sp.armed(_stuck_rec(plan))


def test_plant_indeterminate_at_a_different_level_is_a_different_root_cause(tmp_path):
    """The paired PLANT: the signature keys on the GATE and its LEVEL, so a
    low-level and a medium-level indeterminate are not one recurring cause."""
    lo = sp.signature(_indeterminate_excerpt(tmp_path, "i3", level="low"))
    med = sp.signature(_indeterminate_excerpt(tmp_path, "i4", level="medium"))
    assert lo["sig"] != med["sig"], (lo, med)


UNATTRIBUTABLE = "```\nRe-run the gate after fixing the issues above.\n```"


def test_plant_an_unattributable_locus_never_arms(plan):
    """s05 latched the signature onto a markdown code fence. A locus that names
    neither an error class nor a finding is not evidence of a repeat — twice in a
    row it is still not evidence, so the protocol refuses rather than arming."""
    sig = sp.signature(UNATTRIBUTABLE)
    assert sig["armable"] is False, sig
    first = _fail_gate(plan, UNATTRIBUTABLE)
    second = _fail_gate(plan, UNATTRIBUTABLE)
    # The COUNTER stays honest — it really was the same text twice...
    assert _stuck_rec(plan)["consecutive"] == 2
    # ...but nothing arms off it, in either consumer.
    assert "stuck_protocol" not in first and "stuck_protocol" not in second
    assert _events(plan, "stuck_protocol_armed") == []
    assert not sp.armed(_stuck_rec(plan))


def test_allow_a_classifiable_locus_repeated_twice_does_arm(plan):
    """The paired ALLOW for the refusal above: identical text, but this one names
    its error class, so the protocol still arms exactly as it always did."""
    _fail_gate(plan, SAME_A)
    second = _fail_gate(plan, SAME_B)
    assert second["stuck_protocol"]["triggered"] and sp.armed(_stuck_rec(plan))


def test_allow_generated_prompt_carries_the_stuck_clause():
    """RS-03's ONE source of truth: the wrapper, not per-session prompt text."""
    md = build_plan.gen_session_prompt_md(
        {"id": "s01", "title": "T", "items": ["w-01"], "prompt": "do the thing"},
        "fixture", {"w-01": {"title": "W"}},
    )
    assert "the stuck protocol" in md
    assert "SAME root-cause signature" in md
    for needle in ("Perplexity", "Exa", "Ref", "decision_brief", "escalation ladder"):
        assert needle in md, needle


# ==========================================================================
# RS-04 — a BLOCKED closeout must carry a decision brief
# ==========================================================================
def test_plant_blocked_without_a_brief_is_refused_by_name(plan, tmp_path, capsys):
    printed, code = _apply(plan, tmp_path, capsys, result="BLOCKED")
    assert code == 1
    assert printed["failure"] == "semantic_error"
    assert "decision_brief" in printed["reason"]
    assert ab.read_status((Path(plan) / "PLAN.html").read_text(), "s01") == "BLOCKED"
    assert rsi.is_halted(plan)


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda b: {**b, "attempts": []}, "decision_brief.attempts"),
        (lambda b: {k: v for k, v in b.items() if k != "findings"}, "decision_brief.findings"),
        (lambda b: {**b, "findings": [{"source": "x"}]}, "findings[0].takeaway"),
        (lambda b: {**b, "options": b["options"] + ["a fourth"]}, "at most 3"),
        (lambda b: {**b, "options": []}, "decision_brief.options"),
        (lambda b: {**b, "recommendation": "  "}, "decision_brief.recommendation"),
        (lambda b: {**b, "next_steps": ["x"]}, "unknown key"),
        (lambda b: "stuck, please advise", "must be an object"),
    ],
)
def test_plant_malformed_briefs_are_refused(plan, tmp_path, capsys, mutate, needle):
    printed, code = _apply(plan, tmp_path, capsys, result="BLOCKED", brief=mutate(GOOD_BRIEF))
    assert code == 1
    assert printed["failure"] == "semantic_error"
    assert needle in printed["reason"], printed["reason"]


def test_allow_a_well_formed_brief_is_accepted_and_rendered_in_the_halt_notice(
    plan, tmp_path, capsys
):
    printed, code = _apply(plan, tmp_path, capsys, result="BLOCKED", brief=GOOD_BRIEF)
    assert code == 0, printed
    assert printed["applied"] is True and printed["result"] == "BLOCKED"
    assert printed["decision_brief"] == GOOD_BRIEF

    notice = (Path(plan) / "HALT_NOTICE.txt").read_text()
    assert "DECISION BRIEF" in notice
    # The operator sees the SOURCES, the options and the recommendation without
    # opening _closeouts/s01.json — that is the whole point of rendering it here.
    assert "PostgreSQL 16 docs" in notice
    assert "1. Ask the on-call DBA" in notice
    assert "3. Move the index" in notice
    assert "RECOMMENDATION: Option 1" in notice


def test_allow_an_old_schema_plan_is_unaffected(plan, tmp_path, capsys):
    """The control: the SAME briefless BLOCKED closeout that is refused above must
    still be accepted on a plan built before this feature existed."""
    _restamp(plan, cp.DECISION_BRIEF_MIN_SCHEMA - 1)
    printed, code = _apply(plan, tmp_path, capsys, result="BLOCKED")
    assert code == 0, printed
    assert printed["applied"] is True and printed["result"] == "BLOCKED"
    assert "decision_brief" not in printed


def test_allow_a_brief_is_optional_on_done_and_on_a_checkpoint(plan, tmp_path, capsys):
    """Recommended, not required, when the session is not BLOCKED — and the miss
    leaves a trace rather than a refusal."""
    printed, code = _apply(plan, tmp_path, capsys, result="DONE",
                           checkpoint="Ship the copy as written, or soften it?")
    assert code == 0, printed
    assert printed["applied"] is True
    assert len(_events(plan, "decision_brief_missing")) == 1


def test_allow_a_brief_never_changes_the_closeout_digest():
    """State-drift guard: every plan on disk was digested without this field."""
    base = {"session": "s01", "result": "BLOCKED", "items_completed": [],
            "items_blocked": ["w-01"], "notes": {"w-01": "x"}}
    assert cp.closeout_digest(base) == cp.closeout_digest({**base, "decision_brief": GOOD_BRIEF})


def test_allow_the_builder_stamp_and_the_gate_move_together():
    """The bug this plan already paid for twice: a new requirement enforced at a
    version older than the one the builder stamps would refuse plans on disk.

    RELAXED FROM `==` TO `<=` (2026-08-13, ESC-02 bumped the stamp to 6). Equality
    was over-tight: it pinned the builder's stamp to THIS ONE feature's gate, so the
    next version-gated feature broke it mechanically without anything being wrong.
    The invariant that actually protects plans on disk is the inequality — a gate
    must never sit ABOVE the stamp a fresh plan carries, or the requirement would be
    enforced on plans that can never satisfy it. It must also not be ABOVE 0, i.e.
    the gate has to exist; both halves are asserted."""
    assert 0 < cp.DECISION_BRIEF_MIN_SCHEMA <= build_plan.PLAN_SCHEMA_VERSION


def test_the_PRODUCTION_indeterminate_route_arms_and_still_charges_nothing(plan, tmp_path):
    """THE WIRING. `_fail_gate` above calls `vfy._gate_failed` directly, but a
    gate that DECLARES `indeterminate_exit` never reaches it -- verify routes it
    to `rework._indeterminate`, which is the only path production takes, since
    every llm-review-* entry in both registries declares that exit. So the arming
    proved above was unreachable: a reviewer could time out forever and nothing
    would suggest splitting the session. Measured on s04 -- two 900s
    timeouts on a 5,543-line surface, nothing armed (review).

    Both halves matter: it must ARM (a repeat is a repeat) and it must NOT charge
    the agent's rework budget for the harness's own timeout."""
    a = _indeterminate_excerpt(tmp_path, "p1")
    b = _indeterminate_excerpt(tmp_path, "p2")
    state = {"session_id": "s01", "gates": ["g"], "gate_status": {"g": "pending"},
             "on_fail": "rework", "max_rework": 5, "rework_count": 0, "failures": {}}
    import rework
    rework._indeterminate(plan, "s01", state, "g", a)
    rework._indeterminate(plan, "s01", state, "g", b)
    rec = _stuck_rec(plan)
    assert rec.get("consecutive") == 2, rec          # it arms through the real route
    assert sp.armed(rec), rec
    assert state["rework_count"] == 0                # and charges nobody
    assert state["gate_status"]["g"] == "pending"    # gate stays re-runnable
