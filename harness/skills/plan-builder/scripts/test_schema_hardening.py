#!/usr/bin/env python3
"""Tests for the runner-enforceable hardening keys added 2026-07-03:
  - verify.checks[]            (named evidence-artifact contracts)
  - dispatch.depends_on_policy ("all" | "completed_or_terminal")
  - dispatch.model_fallbacks / reasoning_fallbacks (degrade ladder)

Run: python3 test_schema_hardening.py   (exit 0 = all pass)
Uses a real spec fixture so the backward-compat baseline is a genuine plan.
"""
import copy, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plan

# A committed, synthetic sample spec — structurally a real plan (same session
# graph, gates, checkpoints and fallback ladders) with every free-text field
# replaced by filler. Committed rather than pointed at a live plan directory so
# the test runs anywhere, and so the fixture cannot drift or leak plan content.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-spec.json"
BASE = json.loads(FIXTURE.read_text())

_p = _f = 0
def ok(name):
    global _p; _p += 1; print(f"  PASS  {name}")
def bad(name, detail):
    global _f; _f += 1; print(f"  FAIL  {name}: {detail}")

def expect_valid(spec, name):
    try:
        build_plan.validate_spec(spec); ok(name)
    except Exception as e:
        bad(name, f"unexpectedly raised {type(e).__name__}: {e}")

def expect_invalid(spec, name, needle=None):
    try:
        build_plan.validate_spec(spec); bad(name, "expected ValueError, none raised")
    except ValueError as e:
        if needle and needle not in str(e):
            bad(name, f"raised but message lacked {needle!r}: {e}")
        else:
            ok(name)
    except Exception as e:
        bad(name, f"raised wrong type {type(e).__name__}: {e}")

def sess(spec, sid):
    return next(s for s in spec["sessions"] if s["id"] == sid)

# The fixture predates the checkpoint-brief policy (2026-07-11): its s06 human
# gate has no `checkpoint` decision brief, which is now a deliberate build
# error. Keep the raw fixture for the rejection test, then patch BASE once so
# every other test runs against the policy-conformant baseline.
RAW = copy.deepcopy(BASE)
sess(BASE, "s06")["dispatch"]["checkpoint"] = {
    "reason": "Program-level judgment call the owner reserved for himself.",
    "decision": "Approve the s06 rollout as scoped, or re-scope before dispatch?",
}

# ── 1. Backward compat: real spec with NO new keys still validates ──────────
# (with the one deliberate exception: an un-briefed human gate is now rejected)
expect_valid(copy.deepcopy(BASE), "backward-compat: briefed base spec validates")
expect_invalid(copy.deepcopy(RAW),
               "checkpoint brief: un-briefed human gate rejected (policy 2026-07-11)",
               "checkpoint brief")

# ── 1b. Checkpoint decision-brief shape ──────────────────────────────────────
s = copy.deepcopy(BASE); sess(s, "s06")["dispatch"]["checkpoint"]["options"] = ["Approve", "Re-scope"]
expect_valid(s, "checkpoint brief: optional options list accepted")
s = copy.deepcopy(BASE); del sess(s, "s06")["dispatch"]["checkpoint"]["decision"]
expect_invalid(s, "checkpoint brief: missing decision rejected", "decision")
s = copy.deepcopy(BASE); sess(s, "s06")["dispatch"]["checkpoint"]["reason"] = "  "
expect_invalid(s, "checkpoint brief: blank reason rejected", "reason")
s = copy.deepcopy(BASE); sess(s, "s06")["dispatch"]["checkpoint"]["oops"] = "x"
expect_invalid(s, "checkpoint brief: unknown key rejected", "unknown keys")
s = copy.deepcopy(BASE); sess(s, "s06")["dispatch"]["checkpoint"]["options"] = []
expect_invalid(s, "checkpoint brief: empty options rejected", "options")

# ── 2. verify.checks accepted (valid shape) ─────────────────────────────────
s = copy.deepcopy(BASE)
sess(s, "s05")["verify"]["checks"] = [
    {"name": "commit-boundary", "evidence_path": "_evidence/s05/cb.txt", "assert": "surfaced not retried"},
    {"name": "classifier", "evidence_path": "_evidence/s05/tests.txt"},  # assert optional
]
expect_valid(s, "verify.checks: valid list accepted (assert optional)")

# ── 3-6. malformed checks rejected ──────────────────────────────────────────
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = [{"name": "x"}]
expect_invalid(s, "verify.checks: missing evidence_path rejected", "evidence_path")
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = [{"evidence_path": "p"}]
expect_invalid(s, "verify.checks: missing name rejected", "name")
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = "not-a-list"
expect_invalid(s, "verify.checks: non-list rejected", "checks")
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = [{"name": "x", "evidence_path": "p", "bogus": 1}]
expect_invalid(s, "verify.checks: unknown key rejected", "unknown keys")
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = [{"name": "x", "evidence_path": "p", "assert": 5}]
expect_invalid(s, "verify.checks: non-string assert rejected", "assert")
s = copy.deepcopy(BASE); sess(s, "s05")["verify"]["checks"] = []
expect_invalid(s, "verify.checks: empty list rejected", "non-empty")

# ── 7. checks-only verify block (no gates/require_evidence) is valid ─────────
s = copy.deepcopy(BASE)
sess(s, "s08")["verify"] = {"on_fail": "rework", "max_rework": 2,
                            "checks": [{"name": "taxonomy", "evidence_path": "_evidence/s08/tax.md"}]}
expect_valid(s, "verify: checks-only block (no gates/require_evidence) valid")

# ── 8. depends_on_policy enum ───────────────────────────────────────────────
s = copy.deepcopy(BASE); sess(s, "s09")["dispatch"]["depends_on_policy"] = "completed_or_terminal"
expect_valid(s, "dispatch.depends_on_policy: valid enum accepted")
s = copy.deepcopy(BASE); sess(s, "s09")["dispatch"]["depends_on_policy"] = "sometimes"
expect_invalid(s, "dispatch.depends_on_policy: invalid enum rejected", "depends_on_policy")

# ── 9. model_fallbacks / reasoning_fallbacks ────────────────────────────────
s = copy.deepcopy(BASE)
sess(s, "s10")["dispatch"]["model_fallbacks"] = ["Opus"]
sess(s, "s10")["dispatch"]["reasoning_fallbacks"] = ["xhigh"]
expect_valid(s, "dispatch.model_fallbacks/reasoning_fallbacks: list-of-str accepted")
s = copy.deepcopy(BASE); sess(s, "s10")["dispatch"]["model_fallbacks"] = "Opus"
expect_invalid(s, "dispatch.model_fallbacks: non-list rejected", "model_fallbacks")
s = copy.deepcopy(BASE); sess(s, "s10")["dispatch"]["reasoning_fallbacks"] = [3]
expect_invalid(s, "dispatch.reasoning_fallbacks: non-string items rejected", "reasoning_fallbacks")

# ── 10. build() carries keys into manifest + renders prompt evidence contract ─
s = copy.deepcopy(BASE)
d = sess(s, "s01")["dispatch"]
d["depends_on_policy"] = "completed_or_terminal"; d["model_fallbacks"] = ["Opus"]; d["reasoning_fallbacks"] = ["xhigh"]
sess(s, "s01")["verify"]["checks"] = [{"name": "baseline-tag", "evidence_path": "_evidence/s01/baseline-tag.txt",
                                       "assert": "records the pre-program git tag"}]
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(s, out)
        man = json.loads((out / "manifest.json").read_text())
        m01 = next(x for x in man["sessions"] if x["id"] == "s01")["dispatch"]
        assert m01["depends_on_policy"] == "completed_or_terminal", m01
        assert m01["model_fallbacks"] == ["Opus"], m01
        assert m01["reasoning_fallbacks"] == ["xhigh"], m01
        ok("build: manifest carries depends_on_policy + fallbacks")
        # backward-compat default in manifest for a session without the keys
        m02 = next(x for x in man["sessions"] if x["id"] == "s02")["dispatch"]
        assert m02["depends_on_policy"] == "all" and m02["model_fallbacks"] == [], m02
        ok("build: manifest defaults depends_on_policy='all', fallbacks=[]")
        m06 = next(x for x in man["sessions"] if x["id"] == "s06")["dispatch"]
        assert m06["checkpoint"] == sess(s, "s06")["dispatch"]["checkpoint"], m06
        assert m02["checkpoint"] is None, m02
        ok("build: manifest carries the checkpoint decision brief verbatim")
        pm = (out / "sessions" / "s01.prompt.md").read_text()
        assert "Evidence contracts" in pm, "prompt missing 'Evidence contracts'"
        assert "_evidence/s01/baseline-tag.txt" in pm, "prompt missing evidence_path"
        assert "records the pre-program git tag" in pm, "prompt missing assert text"
        ok("build: prompt.md renders the Evidence contracts list")
    except AssertionError as e:
        bad("build: manifest/prompt propagation", e)
    except Exception as e:
        bad("build: manifest/prompt propagation", f"{type(e).__name__}: {e}")

# ── 11. acceptance_review — the closing plan-level validation session ────────
s = copy.deepcopy(BASE); sess(s, "s10")["acceptance_review"] = True
expect_valid(s, "acceptance_review: bool accepted")
s = copy.deepcopy(BASE); sess(s, "s10")["acceptance_review"] = "yes"
expect_invalid(s, "acceptance_review: non-bool rejected", "acceptance_review")
s = copy.deepcopy(BASE)
sess(s, "s09")["acceptance_review"] = True; sess(s, "s10")["acceptance_review"] = True
expect_invalid(s, "acceptance_review: two markers rejected", "exactly ONE")

# The ≥4-session no-acceptance-session warning must FIRE (and only then) — a gate
# that cannot fail is worse than no gate, so prove both directions.
import contextlib, io
def _warned(spec):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        build_plan.validate_spec(spec)
    return "no closing acceptance-review session" in buf.getvalue()

s = copy.deepcopy(BASE)  # fixture is a big plan with no acceptance session
if len(s["sessions"]) >= 4 and _warned(s):
    ok("acceptance_review: missing-on-big-plan warning fires")
else:
    bad("acceptance_review: missing-on-big-plan warning", "expected a stderr warning, got none")
s = copy.deepcopy(BASE); sess(s, "s10")["acceptance_review"] = True
if _warned(s):
    bad("acceptance_review: warning silent when declared", "warned despite a declared session")
else:
    ok("acceptance_review: warning silent when a session declares it")
s = copy.deepcopy(BASE)  # small plan: below the threshold, no nagging
s["sessions"] = [x for x in s["sessions"] if x["id"] in ("s01", "s02")]
kept = {x["id"] for x in s["sessions"]}
s["items"] = [it for it in s["items"] if any(it["id"] in x.get("items", []) for x in s["sessions"])]
for x in s["sessions"]:
    x["dispatch"]["depends_on"] = [d for d in x["dispatch"].get("depends_on", []) if d in kept]
if _warned(s):
    bad("acceptance_review: warning silent under threshold", "warned on a 2-session plan")
else:
    ok("acceptance_review: warning silent on a plan under 4 sessions")

s = copy.deepcopy(BASE); sess(s, "s10")["acceptance_review"] = True
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(s, out)
        man = json.loads((out / "manifest.json").read_text())
        by_id = {x["id"]: x for x in man["sessions"]}
        assert by_id["s10"]["acceptance_review"] is True, by_id["s10"]
        assert by_id["s01"]["acceptance_review"] is False, by_id["s01"]
        ok("build: manifest carries acceptance_review (default False)")
    except AssertionError as e:
        bad("build: acceptance_review propagation", e)
    except Exception as e:
        bad("build: acceptance_review propagation", f"{type(e).__name__}: {e}")

# ── 12. Codex models are first-class dispatchable tokens (dual-harness S03, CL-01) ──
# gpt-5.6-sol/-terra/-luna must validate with NO warning (previously any
# non-Claude model string triggered the "won't normalize" WARNING), and a built
# dashboard must render a distinct model-codex chip while still passing the
# no-undef ESLint gate (guards the 2026-06-06 "isOpus is not defined" class of bug —
# see validate_dashboard_js docstring).
s = copy.deepcopy(BASE)
sess(s, "s10")["model"] = "gpt-5.6-sol"
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    build_plan.validate_spec(s)
if "will not normalize to a dispatchable model token" in buf.getvalue():
    bad("codex model: gpt-5.6-sol", "unexpected dispatchable-token WARNING")
else:
    ok("codex model: gpt-5.6-sol emits no dispatchable-token WARNING")

for tok in ("gpt-5.6-terra", "gpt-5.6-luna"):
    s2 = copy.deepcopy(BASE); sess(s2, "s10")["model"] = tok
    expect_valid(s2, f"codex model: {tok} validates")

# RETIRED MODEL (2026-08-13, operator directive: the OpenAI lane is 5.6-ONLY).
# gpt-5.5 left CODEX_MODEL_TOKENS, so a NEW plan pinning it must WARN at build time
# rather than sail through and only fail at dispatch. It still VALIDATES — an
# already-built manifest is not retroactively unbuildable; it is blocked at dispatch
# with guidance instead.
s2 = copy.deepcopy(BASE); sess(s2, "s10")["model"] = "gpt-5.5"
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    build_plan.validate_spec(s2)
if "will not normalize to a dispatchable model token" in buf.getvalue():
    ok("retired model: gpt-5.5 emits the dispatchable-token WARNING")
else:
    bad("retired model: gpt-5.5", "expected a dispatchable-token WARNING, got none")

s = copy.deepcopy(BASE)
sess(s, "s10")["model"] = "gpt-5.6-sol"
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(s, out)  # raises if the assembled dashboard JS fails no-undef
        html_text = (out / "PLAN.html").read_text()
        assert 'model-codex' in html_text, "no model-codex chip class in rendered HTML"
        assert ">gpt-5.6-sol<" in html_text, "model label not rendered verbatim"
        ok("build: Codex session renders a model-codex chip; ESLint no-undef gate stays green")
    except AssertionError as e:
        bad("build: Codex session dashboard render", e)
    except Exception as e:
        bad("build: Codex session dashboard render", f"{type(e).__name__}: {e}")

# A RETIRED token is no longer dispatchable (the WARNING above) but an already-built
# plan pinned to one still RE-RENDERS. Its chip class must stay dot-free: a raw
# `model-gpt-5.5` class cannot be selected by the dashboard CSS, which is the whole
# reason the codex mapping exists.
_css = build_plan._model_css_class
if _css("gpt-5.5") == "codex" and _css("gpt-5.6-sol") == "codex" and _css("opus") == "opus":
    ok("retired model: gpt-5.5 still maps to the dot-free 'codex' chip class")
else:
    bad("retired model chip class", f"gpt-5.5 -> {_css('gpt-5.5')!r} (want 'codex')")

# fork + Codex model still warns — P4's fork/model-pairing rule extends to Codex
# tokens too: a fork ALWAYS runs the orchestrator's model, so pairing it with a
# dispatchable Codex token is the same contradiction as pairing it with Opus/Sonnet.
s = copy.deepcopy(BASE)
sess(s, "s10")["model"] = "gpt-5.6-sol"
sess(s, "s10")["dispatch"]["subagent_type"] = "fork"
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    build_plan.validate_spec(s)
if "subagent_type is 'fork'" in buf.getvalue():
    ok("codex model: fork+gpt-5.6-sol still warns (fork ignores model)")
else:
    bad("codex model: fork+gpt-5.6-sol warning", "expected fork-pairing WARNING, got none")

# ── 13. --rebuild --preserve-state carries an executed plan's status forward ──
# Regression for the 2026-07-28 defect: per-session/item status lives ONLY in
# PLAN.html's data-status attributes, and every rebuild re-rendered them at TODO.
# /plan-execute's dispatcher and its structural DONE-gate read that attribute, so
# a rebuild of a live plan destroyed dispatch state, silently. Both directions are
# proven below: the preserving build keeps status, and the SAME assertions fail
# against a build with the carry-over neutered (the pre-fix behaviour).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"))
import article_block as _ab

# The mixed executed state every case below starts from: one DONE, one PARTIAL,
# one BLOCKED (with notes + a shipping badge), the other seven left TODO.
EXECUTED = {"s01": "DONE", "s02": "PARTIAL", "s03": "BLOCKED"}

def executed_plan(td, spec=None):
    """Build the fixture plan, then drive it to EXECUTED via the real runtime
    writer (article_block), exactly as /plan-execute would. Returns the dir."""
    out = Path(td) / "plan"
    build_plan.build(spec or BASE, out)
    html = out / "PLAN.html"
    for i, (sid, st) in enumerate(sorted(EXECUTED.items())):
        _ab.apply_mutation(html, sid, status=st, note=f"note for {sid}",
                           updated=f"2026-07-2{i}")
    _ab.apply_mutation(html, "qw-01", status="DONE", note="item done", updated="2026-07-20")
    _ab.update_shipping_badge(html, "s01", "pushed")
    return out

def added_session_spec():
    """A rebuild that ADDS a session — the main reason to rebuild a live plan."""
    s = copy.deepcopy(BASE)
    new = copy.deepcopy(sess(s, "s01"))
    new["id"] = "s11"
    new["items"] = []
    new["dispatch"] = {"subagent_type": None, "depends_on": ["s10"],
                       "requires_human_checkpoint": False}
    new.pop("post_session", None)
    s["sessions"].append(new)
    return s

def statuses_of(plan_dir):
    return _ab.read_all_statuses((plan_dir / "PLAN.html").read_text())

def check_preserved(plan_dir, label):
    """The invariant, asserted identically in the real case and the control."""
    st = statuses_of(plan_dir)
    html = (plan_dir / "PLAN.html").read_text()
    for sid, want in {**EXECUTED, "qw-01": "DONE"}.items():
        assert st.get(sid) == want, f"{sid}: expected {want}, got {st.get(sid)!r}"
    assert "note for s01" in html and "item done" in html, "closeout notes lost"
    assert 'data-shipping="pushed"' in html, "shipping badge lost"
    assert 'data-updated="2026-07-20"' in html, "updated stamp lost"
    for sid in ("s04", "s05"):
        assert st.get(sid) == "TODO", f"{sid} should still be TODO, got {st.get(sid)!r}"
    return st

# (a) + (b): prior status survives exactly; the ADDED session lands TODO.
with tempfile.TemporaryDirectory() as td:
    try:
        plan = executed_plan(td)
        build_plan.build(added_session_spec(), plan, preserve_state=True)
        st = check_preserved(plan, "preserve")
        assert st.get("s11") == "TODO", f"added s11 should be TODO, got {st.get('s11')!r}"
        ok("rebuild --preserve-state: mixed statuses + notes + ship badge survive; added s11 is TODO")
    except AssertionError as e:
        bad("rebuild --preserve-state preserves status", e)
    except Exception as e:
        bad("rebuild --preserve-state preserves status", f"{type(e).__name__}: {e}")

# (c) The test can fail: neuter the carry-over (restoring the pre-fix code path)
# and the SAME assertions must report the wipe.
with tempfile.TemporaryDirectory() as td:
    real = build_plan.carry_over_recorded_state
    try:
        plan = executed_plan(td)
        build_plan.carry_over_recorded_state = lambda prior, html, path, preserve: html
        build_plan.build(added_session_spec(), plan, preserve_state=True)
        try:
            check_preserved(plan, "control")
            bad("control: rebuild WITHOUT carry-over must wipe status",
                "statuses survived a build with carry-over disabled — the test is vacuous")
        except AssertionError:
            assert all(v == "TODO" for v in statuses_of(plan).values()), \
                "control wiped only some statuses"
            ok("control: rebuild WITHOUT carry-over demonstrably wipes every status (test can fail)")
    except Exception as e:
        bad("control: rebuild WITHOUT carry-over", f"{type(e).__name__}: {e}")
    finally:
        build_plan.carry_over_recorded_state = real

# A removed session must not resurrect, and the survivors keep their status.
with tempfile.TemporaryDirectory() as td:
    try:
        plan = executed_plan(td)
        s = copy.deepcopy(BASE)
        s["sessions"] = [x for x in s["sessions"] if x["id"] != "s03"]
        for x in s["sessions"]:
            x["dispatch"]["depends_on"] = [d for d in x["dispatch"].get("depends_on", [])
                                           if d != "s03"]
        build_plan.build(s, plan, preserve_state=True)
        st = statuses_of(plan)
        assert "s03" not in st, "removed session s03 resurrected in the rebuilt dashboard"
        assert st.get("s01") == "DONE" and st.get("s02") == "PARTIAL", \
            f"survivors lost status: {st.get('s01')!r}/{st.get('s02')!r}"
        ok("rebuild --preserve-state: a REMOVED session does not resurrect; survivors keep status")
    except AssertionError as e:
        bad("rebuild --preserve-state with a removed session", e)
    except Exception as e:
        bad("rebuild --preserve-state with a removed session", f"{type(e).__name__}: {e}")

# Fail-closed: rebuilding an executed plan WITHOUT --preserve-state is refused,
# never silently reset. This is the path /plan-harden's backport takes.
with tempfile.TemporaryDirectory() as td:
    try:
        plan = executed_plan(td)
        before = statuses_of(plan)
        try:
            build_plan.build(added_session_spec(), plan)
            bad("rebuild without --preserve-state is refused", "expected ValueError, none raised")
        except ValueError as e:
            assert "--preserve-state" in str(e), f"refusal must name the fix: {e}"
            assert statuses_of(plan) == before, "PLAN.html was modified despite the refusal"
            ok("rebuild without --preserve-state is REFUSED and leaves PLAN.html untouched")
    except AssertionError as e:
        bad("rebuild without --preserve-state is refused", e)
    except Exception as e:
        bad("rebuild without --preserve-state is refused", f"{type(e).__name__}: {e}")

# A never-executed plan is unaffected: --rebuild with no flag still just works.
with tempfile.TemporaryDirectory() as td:
    try:
        out = Path(td) / "plan"
        build_plan.build(BASE, out)
        first = (out / "PLAN.html").read_bytes()
        build_plan.build(BASE, out)
        assert (out / "PLAN.html").read_bytes() == first, "fresh rebuild is not byte-stable"
        ok("rebuild of a never-executed plan needs no flag and is byte-stable")
    except AssertionError as e:
        bad("rebuild of a never-executed plan", e)
    except Exception as e:
        bad("rebuild of a never-executed plan", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# dispatch.codex_shell (2026-07-29) — declared shell capabilities for a
# `codex exec` dispatch. The one invariant: `danger-full-access` (an UNSANDBOXED
# dispatched agent) is legal only on a session a human already gates.
# ---------------------------------------------------------------------------
def _spec_with_shell(shell, **session_extra):
    import copy
    spec = copy.deepcopy(BASE)
    s = spec["sessions"][0]
    d = s.setdefault("dispatch", {})
    d["codex_shell"] = shell
    for k, v in session_extra.items():
        (d if k in ("guards_irreversible", "requires_human_checkpoint") else s)[k] = v
    return spec

def _build_fails(spec, needle, label):
    with tempfile.TemporaryDirectory() as td:
        try:
            build_plan.build(spec, Path(td) / "plan")
        except ValueError as e:
            try:
                assert needle in str(e), f"refusal must name the fix: {e}"
                ok(label)
            except AssertionError as e2:
                bad(label, e2)
            return
        except Exception as e:
            bad(label, f"{type(e).__name__}: {e}")
            return
        bad(label, "build SUCCEEDED where it had to refuse")

def _build_ok(spec, label, check=None):
    with tempfile.TemporaryDirectory() as td:
        try:
            out = Path(td) / "plan"
            build_plan.build(spec, out)
            if check:
                check(json.loads((out / "manifest.json").read_text()))
            ok(label)
        except AssertionError as e:
            bad(label, e)
        except Exception as e:
            bad(label, f"{type(e).__name__}: {e}")

# The gate, and its ALLOW CONTROL — the same session, ungated then gated.
_build_fails(_spec_with_shell({"sandbox": "danger-full-access"}),
             "no human gates it",
             "codex_shell danger-full-access is REFUSED on an ungated session")
_build_ok(_spec_with_shell({"sandbox": "danger-full-access"}, guards_irreversible=True),
          "codex_shell danger-full-access BUILDS when guards_irreversible gates it",
          lambda m: (_ for _ in ()).throw(AssertionError("codex_shell missing from manifest"))
                    if m["sessions"][0]["dispatch"].get("codex_shell", {}).get("sandbox")
                       != "danger-full-access" else None)

# Redundant narrower keys beside full access => one command, two readings.
_build_fails(_spec_with_shell({"sandbox": "danger-full-access", "network": True},
                              guards_irreversible=True),
             "one reading",
             "codex_shell rejects writable_roots/network beside danger-full-access")

# Shape validation.
_build_fails(_spec_with_shell({"sandbox": "read-only"}), "workspace-write",
             "codex_shell rejects a sandbox mode the runner does not emit")
_build_fails(_spec_with_shell({"writable_roots": "~/.dyno"}), "list of non-empty path",
             "codex_shell rejects writable_roots that is not a list")
_build_fails(_spec_with_shell({"nework": True}), "unknown key",
             "codex_shell rejects a misspelled key instead of ignoring it")
_build_fails(_spec_with_shell({"env_include": ["*_API_KEY"]}), "environment variable names",
             "codex_shell rejects wildcard environment grants")

def _check_env_include(m):
    sh = m["sessions"][0]["dispatch"].get("codex_shell")
    assert sh == {"env_include": ["ANTHROPIC_API_KEY"]}, sh
_build_ok(_spec_with_shell({"env_include": ["ANTHROPIC_API_KEY"]}),
          "codex_shell env_include reaches manifest.json without credential values",
          _check_env_include)

# The narrow grants need no gate, and land in the manifest verbatim.
def _check_narrow(m):
    sh = m["sessions"][0]["dispatch"].get("codex_shell")
    assert sh == {"writable_roots": ["~/.dyno"], "network": True}, sh
_build_ok(_spec_with_shell({"writable_roots": ["~/.dyno"], "network": True}),
          "codex_shell writable_roots+network needs no gate and reaches manifest.json",
          _check_narrow)

# Absent => the key is absent from the manifest (existing plans unchanged).
def _check_absent(m):
    assert "codex_shell" not in m["sessions"][0]["dispatch"], "codex_shell leaked into manifest"
_build_ok(BASE, "no codex_shell => no codex_shell key in manifest.json", _check_absent)

# ---------------------------------------------------------------------------
# out_of_scope / open_questions (2026-08-01, wayfinder-derived) — flat string
# lists, carried into manifest.json and rendered as a static dashboard section.
# ---------------------------------------------------------------------------
s = copy.deepcopy(BASE)
s["out_of_scope"] = ["Rewrite the billing engine — separate effort"]
s["open_questions"] = ["Which auth provider? — sharpens after the s02 spike"]
def _check_scope(m):
    assert m["out_of_scope"] == s["out_of_scope"], m.get("out_of_scope")
    assert m["open_questions"] == s["open_questions"], m.get("open_questions")
_build_ok(s, "out_of_scope/open_questions reach manifest.json verbatim", _check_scope)

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    build_plan.build(s, out)
    html = (out / "PLAN.html").read_text()
    if "Out of scope" in html and "Open decisions" in html:
        ok("out_of_scope/open_questions render on the dashboard")
    else:
        bad("out_of_scope/open_questions render on the dashboard", "section missing from PLAN.html")

s = copy.deepcopy(BASE); s["out_of_scope"] = "not-a-list"
expect_invalid(s, "out_of_scope: non-list rejected", "out_of_scope")
s = copy.deepcopy(BASE); s["open_questions"] = ["ok", "  "]
expect_invalid(s, "open_questions: blank entry rejected", "open_questions")
def _check_no_scope(m):
    assert "out_of_scope" not in m and "open_questions" not in m, "scope keys leaked into manifest"
_build_ok(BASE, "no scope fields => no scope keys in manifest.json", _check_no_scope)

# ---------------------------------------------------------------------------
# Prior-art decision (RS-01/RS-02, 2026-08-12 hardening; VERSION-GATED as of
# the same-day rework below) — a spec that OPTS IN via a top-level
# `"plan_schema_version" >= PRIOR_ART_MIN_SCHEMA` (4) must carry, on every
# item, EITHER prior_art (decision+source) OR research_status: "skipped" (+
# research_reason). A spec that does NOT opt in — no `plan_schema_version` key
# at all, or one below 4 — is completely unaffected; this is the conservative
# default the first (unconditional) version of this gate got wrong, breaking
# 11/15 plan-execute test shards and the live mutation engine on every
# pre-existing plan the same session it shipped. BASE (the fixture) now
# stamps `"plan_schema_version": 4` at the top level and every one of its 14
# items carries one of the two shapes -- the earlier `expect_valid` calls
# above are themselves an allow-control proving a fully-decided v4 spec
# validates clean. The tests below prove: the version gate itself (the fix),
# the refusal side (unchanged plants), and the two positive shapes.
# ---------------------------------------------------------------------------

def item(spec, iid):
    return next(it for it in spec["items"] if it["id"] == iid)

# ── THE VERSION GATE (2026-08-12 rework) ────────────────────────────────────
# Pre-bump control: no top-level plan_schema_version at all -- an item missing
# prior_art entirely still validates clean. This is the control that was
# missing when the gate first shipped unconditionally.
s = copy.deepcopy(BASE)
del s["plan_schema_version"]
del item(s, "qw-01")["prior_art"]
expect_valid(s, "prior_art: pre-bump spec (no plan_schema_version) with a missing prior_art still validates clean")

# Off-by-one guard: an explicit version BELOW the threshold is equally inert.
s = copy.deepcopy(BASE)
s["plan_schema_version"] = 3
del item(s, "qw-01")["prior_art"]
expect_valid(s, "prior_art: explicit plan_schema_version=3 (below threshold) also unenforced")

# Mirror: the SAME missing item at plan_schema_version=4 IS refused -- proves
# the gate is load-bearing, not just permanently open.
s = copy.deepcopy(BASE)
s["plan_schema_version"] = 4
del item(s, "qw-01")["prior_art"]
expect_invalid(s, "prior_art: explicit plan_schema_version=4 with a missing prior_art still refused",
               "must carry either prior_art")

# The exact regression this rework fixes: build_plan.build() on an UNRELATED,
# pre-bump, single-item spec with no prior_art notion at all (the shape every
# other skill's test suite uses build() to materialize) must not raise.
_minimal_prebump_spec = {
    "title": "Unrelated fixture", "categories": [{"key": "c", "label": "C"}],
    "items": [{"id": "it-1", "title": "IT-1", "category": "c"}],
    "sessions": [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["it-1"],
                  "prompt": "...", "dispatch": {"subagent_type": None, "depends_on": []}}],
    "infographic": {"type": "phase-journey", "title": "T",
                     "phases": [{"num": 1, "name": "P", "tagline": "t", "items": ["it-1"]}],
                     "anchor_now": {"name": "n", "tagline": "t"},
                     "anchor_goal": {"name": "g", "tagline": "t"}},
}
with tempfile.TemporaryDirectory() as td:
    try:
        build_plan.build(copy.deepcopy(_minimal_prebump_spec), Path(td) / "plan")
        ok("build(): an unrelated pre-bump 1-item spec with no prior_art notion builds clean (the regression)")
    except Exception as e:
        bad("build(): unrelated pre-bump spec regression", f"{type(e).__name__}: {e}")

# Plant 1: omission refused -- neither prior_art nor research_status present.
s = copy.deepcopy(BASE)
it = item(s, "qw-01")
del it["prior_art"]
expect_invalid(s, "prior_art: item with neither prior_art nor research_status refused",
               "must carry either prior_art")

# Plant 2: a build decision with no source refused.
s = copy.deepcopy(BASE)
item(s, "ft-03")["prior_art"] = {"decision": "build"}
expect_invalid(s, "prior_art: build decision with no source refused", "source is required")
s = copy.deepcopy(BASE)
item(s, "ft-03")["prior_art"] = {"decision": "build", "source": "   "}
expect_invalid(s, "prior_art: build decision with blank source refused", "source is required")

# Plant 3: research_status "skipped" + research_reason is the explicit
# degrade path -- accepted, and distinct from a silent pass (the reason is
# mandatory too, proving the notice can't itself be silent/empty).
s = copy.deepcopy(BASE)
it = item(s, "qw-01")
del it["prior_art"]
it["research_status"] = "skipped"
it["research_reason"] = "Synthetic: no research MCP tier was reachable (simulated MCP-absent run)."
expect_valid(s, "prior_art: research_status='skipped' + research_reason accepted (explicit degrade)")
s = copy.deepcopy(BASE)
it = item(s, "qw-01")
del it["prior_art"]
it["research_status"] = "skipped"
expect_invalid(s, "prior_art: research_status='skipped' with no research_reason refused",
               "research_reason is required")
s = copy.deepcopy(BASE)
item(s, "qw-01")["research_status"] = "not-a-real-status"
expect_invalid(s, "prior_art: research_status other than 'skipped' refused", "research_status")

# Shape validation on prior_art itself.
s = copy.deepcopy(BASE)
item(s, "qw-01")["prior_art"]["decision"] = "buy"
expect_invalid(s, "prior_art: decision outside adopt|adapt|build refused", "decision")
s = copy.deepcopy(BASE)
item(s, "qw-01")["prior_art"]["bogus"] = "x"
expect_invalid(s, "prior_art: unknown key refused", "unknown key")
s = copy.deepcopy(BASE)
item(s, "qw-01")["prior_art"] = "not-an-object"
expect_invalid(s, "prior_art: non-object refused", "prior_art")

# Allow control: a fully-decided spec (BASE itself, every item carries one of
# the two shapes) validates clean -- restated explicitly here as the named
# control, even though every earlier expect_valid(BASE-derived spec) already
# exercised it implicitly.
expect_valid(copy.deepcopy(BASE), "prior_art: allow control — fully-decided spec validates clean")

# ── build: prior_art renders in the agent-spec; research-skipped renders too ──
s = copy.deepcopy(BASE)
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(s, out)
        html = (out / "PLAN.html").read_text()
        assert "<h4>Prior art</h4>" in html, "prior_art block missing from agent-spec"
        assert "Synthetic OSS project B" in html, "prior_art.source not rendered"
        assert "project B lacks the feature" in html, "prior_art.note not rendered"
        assert "Prior art — research skipped" in html, "research_status=skipped block missing"
        assert "no research MCP tier was reachable during authoring" in html, \
            "research_reason not rendered"
        ok("build: prior_art and research-skipped both render in the agent-spec")
    except AssertionError as e:
        bad("build: prior_art rendering", e)
    except Exception as e:
        bad("build: prior_art rendering", f"{type(e).__name__}: {e}")

# ── Decision hotspots: a build-with-note item is pulled in WITHOUT tweak_likelihood ──
s = copy.deepcopy(BASE)  # ft-01, or-03, sa-01 all carry prior_art build+note
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(s, out)
        html = (out / "PLAN.html").read_text()
        assert "Decision hotspots" in html, "decision-hotspots section missing"
        assert "built despite" in html, "prior-art hotspot line missing"
        assert "library H is unmaintained" in html, "sa-01's build-despite note not surfaced"
        ok("build: decision hotspots pull in build-despite-alternatives items (no tweak_likelihood needed)")
        # ft-03 / ps-02 are build WITHOUT a note (no credible alternative surfaced) --
        # must NOT be pulled in as a hotspot merely for being a build decision.
        import re as _re
        hotspot_section = html[html.index('data-cat="decision-hotspots"'):]
        hotspot_section = hotspot_section[:hotspot_section.index("</section>")]
        assert "no established solution found" not in hotspot_section, \
            "a plain build (no note) leaked into decision hotspots"
        ok("build: a build decision with no note (no alternative found) stays OUT of decision hotspots")
    except AssertionError as e:
        bad("build: decision hotspots prior-art pull-in", e)
    except Exception as e:
        bad("build: decision hotspots prior-art pull-in", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# S07 / PL-03 — contract-first shaping: the pure-chain warning, `serial_reason`,
# the auto-emitted integration session, and the build-time half of the FROZEN
# parallel-group contract (S06 / PL-02).
#
# The four fixtures live in `parallel_shaping_fixtures.py` so the evidence
# artifact and these assertions run the SAME cases — an evidence file generated
# by code nothing asserts is how a transcript starts describing behaviour the
# build no longer has.
# ---------------------------------------------------------------------------
import parallel_shaping_fixtures as _shaping  # noqa: E402

for _name, _desc, _res, _failures in _shaping.run_all():
    if _failures:
        bad(f"shaping[{_name}]: {_desc}", "; ".join(_failures))
    else:
        ok(f"shaping[{_name}]: {_desc}")

# The version gate on the contract itself, with the same two directions the
# prior-art gate needed: a pre-bump spec carrying a violation must still
# validate (that is what keeps `plan_mutate` working on the 5 live plans in
# this repo that violate M1/M2a/M5), while the SAME violation at v4 is refused.
_ships = _shaping.member_ships()
del _ships["plan_schema_version"]
expect_valid(_ships, "parallel contract: pre-bump spec with an M1 violation still validates (mutation-safe)")
_ships["plan_schema_version"] = 4
expect_invalid(_ships, "parallel contract: the same M1 violation at v4 is refused", "[M1]")

# `serial_reason` shape: it exists only to silence the pure-chain warning, so an
# empty one would silence it while answering nothing.
_sr = _shaping.chained_disjoint(); _sr["serial_reason"] = "   "
expect_invalid(_sr, "serial_reason: blank string refused", "serial_reason")
_sr = _shaping.chained_disjoint(); _sr["serial_reason"] = ["a"]
expect_invalid(_sr, "serial_reason: non-string refused", "serial_reason")

# Emission is IDEMPOTENT — a --rebuild must not stack a second integration
# session on the group (and an author-written integrator must be left alone).
_wt = _shaping.worktree_group()
with _shaping.Isolated():
    _once = build_plan.synthesize_integration_sessions(_wt)
    _twice = build_plan.synthesize_integration_sessions(dict(_wt, sessions=_once))
if len(_once) == len(_wt["sessions"]) + 1 and len(_twice) == len(_once):
    ok("integration session: emitted once, and a re-run adds no duplicate")
else:
    bad("integration session: idempotence",
        f"{len(_wt['sessions'])} -> {len(_once)} -> {len(_twice)} sessions")

# Unparseable `touches` must NOT read as file-disjoint (the conservative rule):
# prose in one session's touches removes it from the groupable-pair suggestions.
_prose = _shaping.chained_disjoint()
_prose["items"][1]["touches"] = "the beta module and anything it imports"
_rep = build_plan.parallelism_report(_prose)
if "s02" in _rep["opaque"] and not any("s02" in (a, b) for a, b, _x, _y in _rep["groupable"]):
    ok("touches: unparseable prose is treated as conflicting with everything")
else:
    bad("touches: unparseable prose", f"opaque={_rep['opaque']} groupable={_rep['groupable']}")

# ---------------------------------------------------------------------------
# Research-tool availability probe (RS-06, 2026-08-13)
# ---------------------------------------------------------------------------
# The gap this closes: `research_status` was a field the authoring agent
# hand-wrote. "The research tools were absent, so the pass was skipped" was an
# assertion the builder took on trust — there was no probe anywhere in the
# builder, so the skip record was never emitted by the system.
#
# What is now enforced, stated honestly: `probe_research_tools()` reads
# CONFIGURATION ONLY (MCP server names + permission deny-lists). It cannot test
# reachability, and nothing here claims it does. The structured claim
# `research_status: "unavailable"` is refused when that probe can see research
# capability configured, and any skip claim gets the probe's own record stamped
# into spec.json / manifest.json as `research_env`.
#
# NOT version-gated, and it needs no gate: "unavailable" was an ILLEGAL value
# until this change, so no spec that ever built can carry it. The pre-bump
# controls below prove the old paths are bit-for-bit unaffected.
# ---------------------------------------------------------------------------

class _patched_probe:
    """Swap build_plan.probe_research_tools for a deterministic stub, so these
    tests assert the LOGIC rather than whatever MCP servers happen to be
    configured on the machine running them."""
    def __init__(self, available, signals=()):
        self.record = {"available": available, "signals": list(signals),
                       "sources": ["<stub>"], "method": "stub"}
        self.calls = 0
    def __enter__(self):
        self._real = build_plan.probe_research_tools
        def _stub(*a, **k):
            self.calls += 1
            return dict(self.record)
        build_plan.probe_research_tools = _stub
        return self
    def __exit__(self, *exc):
        build_plan.probe_research_tools = self._real
        return False

def _write(root, rel, obj):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return p

# ── The probe itself, against synthetic config trees ────────────────────────
# Known POSITIVE first (a check whose all-clear was never proved to be able to
# turn red is worse than no check).
with tempfile.TemporaryDirectory() as td:
    home, proj = Path(td) / "home", Path(td) / "proj"
    proj.mkdir(parents=True)
    _write(home, ".claude.json", {"mcpServers": {"exa": {"command": "x"}}})
    r = build_plan.probe_research_tools(project_root=proj, home=home)
    if r["available"] and "mcp:exa" in r["signals"]:
        ok("probe: known positive — a configured research MCP server is detected")
    else:
        bad("probe: known positive (mcp)", r)

with tempfile.TemporaryDirectory() as td:
    home, proj = Path(td) / "home", Path(td) / "proj"
    proj.mkdir(parents=True)
    _write(home, ".claude/settings.json", {"permissions": {"allow": []}})
    r = build_plan.probe_research_tools(project_root=proj, home=home)
    if r["available"] and "builtin:WebSearch" in r["signals"]:
        ok("probe: known positive — built-in WebSearch counts when nothing denies it")
    else:
        bad("probe: known positive (builtin)", r)

# Known NEGATIVE — no config at all. This is the ONLY shape that legitimises an
# `unavailable` claim, and it fails permissive on purpose (observed nothing).
with tempfile.TemporaryDirectory() as td:
    home, proj = Path(td) / "home", Path(td) / "proj"
    home.mkdir(); proj.mkdir()
    r = build_plan.probe_research_tools(project_root=proj, home=home)
    if not r["available"] and r["signals"] == []:
        ok("probe: known negative — a bare environment reports no research capability")
    else:
        bad("probe: known negative (bare)", r)

# Known NEGATIVE — harness present, but the built-ins are denied and the only
# configured MCP server is not research-capable.
with tempfile.TemporaryDirectory() as td:
    home, proj = Path(td) / "home", Path(td) / "proj"
    proj.mkdir(parents=True)
    _write(home, ".claude.json", {"mcpServers": {"transcriber": {}, "chrome-devtools": {}}})
    _write(home, ".claude/settings.json",
           {"permissions": {"deny": ["WebSearch", "WebFetch"]}})
    r = build_plan.probe_research_tools(project_root=proj, home=home)
    if not r["available"]:
        ok("probe: known negative — built-ins denied + no research-capable MCP server")
    else:
        bad("probe: known negative (denied)", r)

# The probe reads NAMES and permission entries only — never a value out of an
# MCP server block. A credential parked in the config must not reach the record.
with tempfile.TemporaryDirectory() as td:
    home, proj = Path(td) / "home", Path(td) / "proj"
    proj.mkdir(parents=True)
    _write(home, ".claude.json",
           {"mcpServers": {"exa": {"env": {"EXA_API_KEY": "sk-CANARY-do-not-leak"}}}})
    r = build_plan.probe_research_tools(project_root=proj, home=home)
    if "CANARY" not in json.dumps(r):
        ok("probe: never carries a config VALUE (credential canary absent from the record)")
    else:
        bad("probe: credential canary leaked into the record", r)

# Name matching is token-based, not substring: a server merely containing the
# letters of a token must not read as research-capable.
if build_plan._is_research_server("claude_ai_Exa") and not build_plan._is_research_server("preferences"):
    ok("probe: research-server matching is token-based (claude_ai_Exa hits, 'preferences' does not)")
else:
    bad("probe: research-server matching", "token matching wrong")

# ── PLANT: the contradicted `unavailable` claim is refused ──────────────────
s = copy.deepcopy(BASE)
it = item(s, "qw-01"); del it["prior_art"]
it["research_status"] = "unavailable"
with _patched_probe(True, ["mcp:exa", "builtin:WebSearch"]) as pp:
    expect_invalid(s, "RS-06 PLANT: research_status='unavailable' refused when the probe sees research capability",
                   "the build-time probe found research capability")
    if pp.calls:
        ok("RS-06 PLANT: the refusal came from the probe (it was actually consulted)")
    else:
        bad("RS-06 PLANT: probe not consulted", "validate_spec refused without probing")

# ── ALLOW: the same claim stands when the probe agrees ──────────────────────
s = copy.deepcopy(BASE)
it = item(s, "qw-01"); del it["prior_art"]
it["research_status"] = "unavailable"
with _patched_probe(False):
    expect_valid(s, "RS-06 ALLOW: research_status='unavailable' accepted when the probe finds nothing")

# ── ALLOW: the pre-existing 'skipped' path is untouched, and never probes ───
s = copy.deepcopy(BASE)
it = item(s, "qw-01"); del it["prior_art"]
it["research_status"] = "skipped"
it["research_reason"] = "Synthetic: author chose not to research this one-line item."
with _patched_probe(True, ["mcp:exa"]) as pp:
    expect_valid(s, "RS-06 ALLOW: research_status='skipped' unaffected by the probe verdict")
    if pp.calls == 0:
        ok("RS-06: validate_spec does not probe at all for a 'skipped' item (zero added I/O)")
    else:
        bad("RS-06: probe called for 'skipped'", f"{pp.calls} calls")

# ── VERSION CONTROL: an older plan is untouched ─────────────────────────────
# A pre-bump spec (no plan_schema_version at all) carrying the OLD legal value
# validates exactly as it did before RS-06 existed, without probing.
s = copy.deepcopy(BASE)
del s["plan_schema_version"]
it = item(s, "qw-01"); del it["prior_art"]
it["research_status"] = "skipped"
it["research_reason"] = "Synthetic pre-bump degrade."
with _patched_probe(True, ["mcp:exa"]) as pp:
    expect_valid(s, "RS-06 VERSION CONTROL: pre-bump spec with research_status='skipped' validates unchanged")
    if pp.calls == 0:
        ok("RS-06 VERSION CONTROL: pre-bump spec never reaches the probe")
    else:
        bad("RS-06 VERSION CONTROL: pre-bump spec probed", f"{pp.calls} calls")

# A blank research_reason alongside 'unavailable' is still a hollow record.
s = copy.deepcopy(BASE)
it = item(s, "qw-01"); del it["prior_art"]
it["research_status"] = "unavailable"; it["research_reason"] = "   "
with _patched_probe(False):
    expect_invalid(s, "RS-06: blank research_reason alongside 'unavailable' refused", "must be a non-empty string")

# ── research_env: emitted for a skip claim, absent otherwise ────────────────
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        with _patched_probe(False):
            build_plan.build(copy.deepcopy(BASE), out)
        man = json.loads((out / "manifest.json").read_text())
        spec_out = json.loads((out / "spec.json").read_text())
        env = man.get("research_env")
        assert isinstance(env, dict), "manifest.research_env missing on a spec with skip claims"
        assert env.get("probed_at"), "research_env carries no probed_at"
        assert env == spec_out.get("research_env"), "spec.json and manifest.json disagree"
        # The plan_mutate consistency invariant: manifest.json must be exactly
        # gen_manifest(spec.json). If gen_manifest re-probed instead of reading
        # the spec, this would drift on any other machine.
        regen = build_plan.gen_manifest(spec_out)
        assert regen == man, "gen_manifest(spec.json) != manifest.json (research_env is not pure)"
        ok("RS-06: research_env stamped into spec.json + manifest.json, and gen_manifest stays pure")
    except AssertionError as e:
        bad("RS-06: research_env emission", e)
    except Exception as e:
        bad("RS-06: research_env emission", f"{type(e).__name__}: {e}")

# The byte-identical guarantee for every plan built before RS-06: a spec with no
# research_status anywhere gains NO new key.
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    try:
        build_plan.build(copy.deepcopy(_minimal_prebump_spec), out)
        man = json.loads((out / "manifest.json").read_text())
        spec_out = json.loads((out / "spec.json").read_text())
        assert "research_env" not in man, "manifest gained research_env on a spec with no skip claim"
        assert "research_env" not in spec_out, "spec.json gained research_env on a spec with no skip claim"
        ok("RS-06: a spec with no research_status gains no research_env (pre-RS-06 plans unchanged)")
    except AssertionError as e:
        bad("RS-06: no-skip-claim spec untouched", e)
    except Exception as e:
        bad("RS-06: no-skip-claim spec untouched", f"{type(e).__name__}: {e}")

# The system-verified heading renders only for the machine-adjudicated value.
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "plan"
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01"); del it["prior_art"]
    it["research_status"] = "unavailable"
    try:
        with _patched_probe(False):
            build_plan.build(s, out)
        html = (out / "PLAN.html").read_text()
        assert "no research tooling available (verified at build time)" in html, \
            "the verified-unavailable block did not render"
        ok("RS-06: an 'unavailable' item renders the build-time-verified prior-art block")
    except AssertionError as e:
        bad("RS-06: unavailable rendering", e)
    except Exception as e:
        bad("RS-06: unavailable rendering", f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
# INFOGRAPHIC_COVERAGE_MIN_SCHEMA (2026-08-13) — the progress bar counts through
# the infographic's group list, so an item in no group is missing from the
# DENOMINATOR: the bar reports progress over a subset while looking like it
# covers the plan. Refused at build time, where it is still cheap to fix.
# ---------------------------------------------------------------------------
def _spec_coverage(stamp, drop_item=None):
    import copy
    spec = copy.deepcopy(BASE)
    spec["plan_schema_version"] = stamp
    if drop_item:
        key = build_plan.INFOGRAPHIC_GROUP_KEYS[spec["infographic"]["type"]]
        for g in spec["infographic"][key]:
            g["items"] = [i for i in (g.get("items") or []) if i != drop_item]
    return spec

# PLANT — one item pulled out of every group is refused, and the refusal names
# the item and the real counts rather than saying "invalid infographic".
expect_invalid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA, drop_item="ft-02"),
               "coverage: PLANT — an item in no group is refused at the gating version",
               needle="ft-02")
expect_invalid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA, drop_item="ft-02"),
               "coverage: the refusal states the denominator it would have counted",
               needle="13 of 14")

# ALLOW — the same spec with every item placed builds at the same version, so
# the plant fails for the stated reason and not because the version bumped.
expect_valid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA),
             "coverage: ALLOW — a fully-placed spec validates at the gating version")

# CONTROL — the identical defect one version below is untouched. Four real
# plans on disk carry unplaced items; none carries a stamp, so none is newly
# refused (measured 2026-08-13 across all 25 spec.json under _plans/).
expect_valid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA - 1, drop_item="ft-02"),
             "coverage: CONTROL — the same defect below the gate still validates")
expect_valid(_spec_coverage(None, drop_item="ft-02"),
             "coverage: CONTROL — an unstamped spec (every plan on disk) still validates")

# The mapping has ONE definition: the mutation engine aliases the builder's, so
# a new infographic shape cannot be placeable by one and invisible to the other.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"))
    import plan_mutate as _pm
    if _pm._INFOGRAPHIC_GROUPS is build_plan.INFOGRAPHIC_GROUP_KEYS:
        ok("coverage: plan_mutate aliases the builder's group-key map (one definition)")
    else:
        bad("coverage: group-key map is duplicated", "plan_mutate holds its own copy")
except Exception as e:
    bad("coverage: group-key map is shared", f"{type(e).__name__}: {e}")

print(f"\n{_p} passed, {_f} failed")

# `sys.exit` is confined to __main__ DELIBERATELY. This module is a top-to-bottom
# SCRIPT — every check runs at import — so an unconditional exit here raised
# SystemExit during pytest's COLLECTION, which aborts the entire run with
# `INTERNALERROR> SystemExit: 0` and reports zero tests. The suite then looks
# green by looking like nothing at all, which is worse than a red one.
if __name__ == "__main__":
    sys.exit(1 if _f else 0)


def test_schema_hardening_checks_all_passed():
    """pytest entry point. Collection alone has already run every check above;
    this only has to assert the tally, so the suite is visible to `pytest` as well
    as to `python3 test_schema_hardening.py`."""
    assert _f == 0, f"{_f} of {_p + _f} schema-hardening checks FAILED (see captured stdout)"
