#!/usr/bin/env python3
"""Runner-enforceable hardening keys — the first schema-check suite.

  - verify.checks[]            (named evidence-artifact contracts)
  - dispatch.depends_on_policy ("all" | "completed_or_terminal")
  - dispatch.model_fallbacks / reasoning_fallbacks (degrade ladder)
  - acceptance_review, Codex model tokens, --rebuild --preserve-state

Uses a real spec fixture so the backward-compat baseline is a genuine plan.
The version gates and the research-tool probe are in test_schema_gates.py;
the shared harness (BASE, expect_valid/expect_invalid, RESULTS) is in
schema_check_harness.py.

run_core_checks() returns only the names IT recorded, so this suite's manifest
stays independent of its sibling's even though both record into one RESULTS.

Run: pytest skills/plan-builder/scripts/test_schema_hardening.py -q
"""
import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_plan  # noqa: E402
from schema_check_harness import (  # noqa: E402
    BASE, RAW, RESULTS, bad, counts, expect_invalid, expect_valid, ok, sess,
)


def _check_hardening_keys():
    """verify.checks, depends_on_policy and the fallback ladders — the keys this suite is named for."""
    expect_valid(copy.deepcopy(BASE), "backward-compat: briefed base spec validates")
    expect_invalid(copy.deepcopy(RAW),
                   "checkpoint brief: un-briefed human gate rejected (policy 2026-07-11)",
                   "checkpoint brief")

    # ── 1b. Checkpoint decision-brief shape ──────────────────────────────────────
    s = copy.deepcopy(BASE)
    sess(s, "s06")["dispatch"]["checkpoint"]["options"] = ["Approve", "Re-scope"]
    expect_valid(s, "checkpoint brief: optional options list accepted")
    s = copy.deepcopy(BASE)
    del sess(s, "s06")["dispatch"]["checkpoint"]["decision"]
    expect_invalid(s, "checkpoint brief: missing decision rejected", "decision")
    s = copy.deepcopy(BASE)
    sess(s, "s06")["dispatch"]["checkpoint"]["reason"] = "  "
    expect_invalid(s, "checkpoint brief: blank reason rejected", "reason")
    s = copy.deepcopy(BASE)
    sess(s, "s06")["dispatch"]["checkpoint"]["oops"] = "x"
    expect_invalid(s, "checkpoint brief: unknown key rejected", "unknown keys")
    s = copy.deepcopy(BASE)
    sess(s, "s06")["dispatch"]["checkpoint"]["options"] = []
    expect_invalid(s, "checkpoint brief: empty options rejected", "options")

    # ── 2. verify.checks accepted (valid shape) ─────────────────────────────────
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = [
        {"name": "commit-boundary", "evidence_path": "_evidence/s05/cb.txt", "assert": "surfaced not retried"},
        {"name": "classifier", "evidence_path": "_evidence/s05/tests.txt"},  # assert optional
    ]
    expect_valid(s, "verify.checks: valid list accepted (assert optional)")

    # ── 3-6. malformed checks rejected ──────────────────────────────────────────
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = [{"name": "x"}]
    expect_invalid(s, "verify.checks: missing evidence_path rejected", "evidence_path")
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = [{"evidence_path": "p"}]
    expect_invalid(s, "verify.checks: missing name rejected", "name")
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = "not-a-list"
    expect_invalid(s, "verify.checks: non-list rejected", "checks")
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = [{"name": "x", "evidence_path": "p", "bogus": 1}]
    expect_invalid(s, "verify.checks: unknown key rejected", "unknown keys")
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = [{"name": "x", "evidence_path": "p", "assert": 5}]
    expect_invalid(s, "verify.checks: non-string assert rejected", "assert")
    s = copy.deepcopy(BASE)
    sess(s, "s05")["verify"]["checks"] = []
    expect_invalid(s, "verify.checks: empty list rejected", "non-empty")

    # ── 7. checks-only verify block (no gates/require_evidence) is valid ─────────
    s = copy.deepcopy(BASE)
    sess(s, "s08")["verify"] = {"on_fail": "rework", "max_rework": 2,
                                "checks": [{"name": "taxonomy", "evidence_path": "_evidence/s08/tax.md"}]}
    expect_valid(s, "verify: checks-only block (no gates/require_evidence) valid")

    # ── 8. depends_on_policy enum ───────────────────────────────────────────────
    s = copy.deepcopy(BASE)
    sess(s, "s09")["dispatch"]["depends_on_policy"] = "completed_or_terminal"
    expect_valid(s, "dispatch.depends_on_policy: valid enum accepted")
    s = copy.deepcopy(BASE)
    sess(s, "s09")["dispatch"]["depends_on_policy"] = "sometimes"
    expect_invalid(s, "dispatch.depends_on_policy: invalid enum rejected", "depends_on_policy")

    # ── 9. model_fallbacks / reasoning_fallbacks ────────────────────────────────
    s = copy.deepcopy(BASE)
    sess(s, "s10")["dispatch"]["model_fallbacks"] = ["Opus"]
    sess(s, "s10")["dispatch"]["reasoning_fallbacks"] = ["xhigh"]
    expect_valid(s, "dispatch.model_fallbacks/reasoning_fallbacks: list-of-str accepted")
    s = copy.deepcopy(BASE)
    sess(s, "s10")["dispatch"]["model_fallbacks"] = "Opus"
    expect_invalid(s, "dispatch.model_fallbacks: non-list rejected", "model_fallbacks")
    s = copy.deepcopy(BASE)
    sess(s, "s10")["dispatch"]["reasoning_fallbacks"] = [3]
    expect_invalid(s, "dispatch.reasoning_fallbacks: non-string items rejected", "reasoning_fallbacks")


def _check_build_and_acceptance():
    """build() key propagation into the manifest, and the closing acceptance-review session."""
    # ── 10. build() carries keys into manifest + renders prompt evidence contract ─
    s = copy.deepcopy(BASE)
    d = sess(s, "s01")["dispatch"]
    d["depends_on_policy"] = "completed_or_terminal"
    d["model_fallbacks"] = ["Opus"]
    d["reasoning_fallbacks"] = ["xhigh"]
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
    s = copy.deepcopy(BASE)
    sess(s, "s10")["acceptance_review"] = True
    expect_valid(s, "acceptance_review: bool accepted")
    s = copy.deepcopy(BASE)
    sess(s, "s10")["acceptance_review"] = "yes"
    expect_invalid(s, "acceptance_review: non-bool rejected", "acceptance_review")
    s = copy.deepcopy(BASE)
    sess(s, "s09")["acceptance_review"] = True
    sess(s, "s10")["acceptance_review"] = True
    expect_invalid(s, "acceptance_review: two markers rejected", "exactly ONE")

    # The ≥4-session no-acceptance-session warning must FIRE (and only then) — a gate
    # that cannot fail is worse than no gate, so prove both directions.
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
    s = copy.deepcopy(BASE)
    sess(s, "s10")["acceptance_review"] = True
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

    s = copy.deepcopy(BASE)
    sess(s, "s10")["acceptance_review"] = True
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


def _check_codex_tokens():
    """Codex models as first-class dispatchable tokens (dual-harness S03, CL-01)."""
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
        s2 = copy.deepcopy(BASE)
        sess(s2, "s10")["model"] = tok
        expect_valid(s2, f"codex model: {tok} validates")

    # RETIRED MODEL (2026-08-13, operator directive: the OpenAI lane is 5.6-ONLY).
    # gpt-5.5 left CODEX_MODEL_TOKENS, so a NEW plan pinning it must WARN at build time
    # rather than sail through and only fail at dispatch. It still VALIDATES — an
    # already-built manifest is not retroactively unbuildable; it is blocked at dispatch
    # with guidance instead.
    s2 = copy.deepcopy(BASE)
    sess(s2, "s10")["model"] = "gpt-5.5"
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


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"))
import article_block as _ab  # noqa: E402 — needs the path insert above

# The mixed executed state every --rebuild case starts from, and the helpers that
# build and read it. Module level because the preserve-state checks are split
# across two functions and both drive the same fixture.
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


def _check_rebuild_preserve_state():
    """--rebuild --preserve-state carries an executed plan's status forward.

    Regression for the 2026-07-28 defect: per-session/item status lives ONLY in
    PLAN.html's data-status attributes, and every rebuild re-rendered them at
    TODO, silently destroying dispatch state. Both directions are proven — the
    preserving build keeps status, and the SAME assertions fail against a build
    with the carry-over neutered.
    """
    # ── 13. --rebuild --preserve-state carries an executed plan's status forward ──
    # Regression for the 2026-07-28 defect: per-session/item status lives ONLY in
    # PLAN.html's data-status attributes, and every rebuild re-rendered them at TODO.
    # /plan-execute's dispatcher and its structural DONE-gate read that attribute, so
    # a rebuild of a live plan destroyed dispatch state, silently. Both directions are
    # proven below: the preserving build keeps status, and the SAME assertions fail
    # against a build with the carry-over neutered (the pre-fix behaviour).
    # The mixed executed state every case below starts from: one DONE, one PARTIAL,
    # one BLOCKED (with notes + a shipping badge), the other seven left TODO.
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


def _check_rebuild_edge_cases():
    """The three --rebuild cases that are NOT the happy path: a removed session
    must not resurrect, rebuilding an executed plan without --preserve-state is
    refused, and a never-executed plan is unaffected."""
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


# Shared by the shell-grant and scope suites: both build a spec and assert the
# build either refuses with a named reason or lands the key in the manifest.
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


def _check_codex_shell_grant():
    """dispatch.codex_shell — declared shell capabilities for a
    `codex exec` dispatch. The one invariant: `danger-full-access` (an
    UNSANDBOXED dispatched agent) is legal only on a session a human gates."""
    # ---------------------------------------------------------------------------
    # dispatch.codex_shell — declared shell capabilities for a
    # `codex exec` dispatch. The one invariant: `danger-full-access` (an UNSANDBOXED
    # dispatched agent) is legal only on a session a human already gates.
    # ---------------------------------------------------------------------------
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
    _build_fails(_spec_with_shell({"writable_roots": "~/.gearbox-state"}), "list of non-empty path",
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
        assert sh == {"writable_roots": ["~/.gearbox-state"], "network": True}, sh
    _build_ok(_spec_with_shell({"writable_roots": ["~/.gearbox-state"], "network": True}),
              "codex_shell writable_roots+network needs no gate and reaches manifest.json",
              _check_narrow)

    # Absent => the key is absent from the manifest (existing plans unchanged).
    def _check_absent(m):
        assert "codex_shell" not in m["sessions"][0]["dispatch"], "codex_shell leaked into manifest"
    _build_ok(BASE, "no codex_shell => no codex_shell key in manifest.json", _check_absent)


def _check_scope_and_open_questions():
    """out_of_scope / open_questions (2026-08-01, wayfinder-derived)."""
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

    s = copy.deepcopy(BASE)
    s["out_of_scope"] = "not-a-list"
    expect_invalid(s, "out_of_scope: non-list rejected", "out_of_scope")
    s = copy.deepcopy(BASE)
    s["open_questions"] = ["ok", "  "]
    expect_invalid(s, "open_questions: blank entry rejected", "open_questions")
    def _check_no_scope(m):
        assert "out_of_scope" not in m and "open_questions" not in m, "scope keys leaked into manifest"
    _build_ok(BASE, "no scope fields => no scope keys in manifest.json", _check_no_scope)


def _run_all_checks():
    """Run every core check once, in order."""
    _check_hardening_keys()
    _check_build_and_acceptance()
    _check_codex_tokens()
    _check_rebuild_preserve_state()
    _check_rebuild_edge_cases()
    _check_codex_shell_grant()
    _check_scope_and_open_questions()

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

def run_core_checks():
    """Run the whole core pass once, reporting only what THIS suite recorded."""
    before = set(RESULTS)
    _run_all_checks()
    return {k: v for k, v in RESULTS.items() if k not in before}


EXPECTED_CHECKS = tuple(
    ln for ln in (Path(__file__).resolve().parent / "fixtures" / "expected-checks-core.txt")
    .read_text().splitlines() if ln.strip()
)


@pytest.fixture(scope="session")
def check_results():
    """Run the whole check pass once. Kept OUT of import (it costs ~48 s), so
    collection stays cheap and an unrelated `-k` run pays nothing for it."""
    return run_core_checks()


@pytest.mark.parametrize("name", EXPECTED_CHECKS)
def test_check(name, check_results):
    assert name in check_results, "check never ran"
    assert check_results[name] is None, check_results[name]


def test_no_check_was_added_or_lost(check_results):
    """Drift guard: the manifest is the contract. Update
    fixtures/expected-checks-core.txt in the same commit that adds or removes a
    check."""
    got, want = set(check_results), set(EXPECTED_CHECKS)
    assert got == want, (
        f"missing: {sorted(want - got)}\nunexpected: {sorted(got - want)}"
    )


if __name__ == "__main__":
    run_core_checks()
    passed, failed = counts()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
