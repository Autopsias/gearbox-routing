#!/usr/bin/env python3
"""Shared harness for the plan-spec schema checks.

Extracted when test_schema_hardening.py (1091 lines, one 973-line function) was
split into the two suites it had grown into: the hardening keys, and the version
gate plus the research probe.

RESULTS is module-level and SHARED by both suites on purpose — each records into
it and each asserts only over its own manifest, so a check that moves between
suites shows up as a manifest drift rather than vanishing.
"""
import copy
import json
import sys
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
# name -> None (passed) or the failure detail. One entry per check, so a lost
# or renamed check is caught by the manifest guard below instead of vanishing.
RESULTS: dict = {}


def ok(name):
    global _p
    _p += 1
    RESULTS[name] = None
    print(f"  PASS  {name}")


def bad(name, detail):
    global _f
    _f += 1
    RESULTS[name] = detail
    print(f"  FAIL  {name}: {detail}")


def expect_valid(spec, name):
    try:
        build_plan.validate_spec(spec)
        ok(name)
    except Exception as e:
        bad(name, f"unexpectedly raised {type(e).__name__}: {e}")


def expect_invalid(spec, name, needle=None):
    try:
        build_plan.validate_spec(spec)
        bad(name, "expected ValueError, none raised")
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


def item(spec, iid):
    """One item by id. Lives here because both suites reach for it — it was a
    nested helper in the core half that the gate half already used."""
    return next(it for it in spec["items"] if it["id"] == iid)


def counts():
    """(passed, failed) as of now.

    A function, not the bare counters: `from schema_check_harness import _f`
    binds the INT at import time and never sees a later increment, so an exit
    code derived from it always reads 0.
    """
    return _p, _f
