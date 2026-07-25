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

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
