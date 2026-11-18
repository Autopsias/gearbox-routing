"""E2E-01 — the night of 2026-11-14 replayed in a scratch repository.

Two plans land through the real `run.py` while harvest commits arrive on main during the
owner stop. Harvest-only moves keep the approval; a code change, or a harvest that turns a
test gate red, resets it.

    pytest skills/plan-execute/scripts/test_land_busy_main_replay.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
from _land_fixture import build_repo, isolate, main_side, origin_main, run_cli, work  # noqa: E402
from worktree import git  # noqa: E402

MEMORY = ("projects/p/memory/note.md", "harvested\n", "harvest: 1 live-state")
CHURN = ("settings.json", json.dumps({"model": "opus", "effortLevel": "high"}, indent=2) + "\n",
         "harvest: machine-churn")
HOOKS = ("hooks/guard.py", "x = 1\n", "a code change on main")
BAD_SPEC = ("_plans/plan-b/spec.json", '{"BAD": true}\n', "harvest: another plan's spec")
SPEC_GATE = "! grep -q BAD _plans/*/spec.json"


def _events(plan_dir):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return [r for r in rows if r.get("event") == "land_carry"]


def _night(tmp_path, **gate_extra):
    """Plan A and plan B, each with work, and a test gate that reads every plan's spec.json."""
    gate = {"kind": "argv", "cwd": ".", "timeout": 60,
            "argv": ["/bin/sh", "-c", SPEC_GATE], **gate_extra}
    fx = build_repo(tmp_path, slugs=("plan-a", "plan-b"), gates={"marker-gate": gate})
    root = fx["root"]
    (root / "settings.json").write_text(json.dumps({"model": "opus"}, indent=2) + "\n")
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "settings"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)
    a, b = isolate(fx, "plan-a"), isolate(fx, "plan-b")
    work(a["tree"], "src/fa.py", "A = 1\n")
    work(b["tree"], "src/fb.py", "B = 1\n")
    out = run_cli("land", a["plan_dir"])[1]
    assert out["action"] == "land-awaits-review", out
    return fx, a["plan_dir"], b["plan_dir"]


def _push_harvests(fx, *commits):
    return [main_side(fx, *c) for c in commits][-1]


def test_replay_a_busy_main_keeps_the_approval_and_both_plans_land(tmp_path):
    fx, a, b = _night(tmp_path)
    moved = _push_harvests(fx, MEMORY, CHURN)
    rc, parked = run_cli("land", b)
    assert (parked["action"], parked["kind"], parked["reserved_by"]) == (
        "land-parked", "land-reserved", "plan-a"), parked
    rc, out = run_cli("land-ack", a, "--note", "ok")           # no second on-box question
    assert (rc, out["action"], out["outcome"]) == (0, "landed", "carried"), out
    assert git(["merge-base", "--is-ancestor", moved, "refs/heads/main"], fx["origin"])[0] == 0
    ev = _events(a)[-1]
    assert (ev["decision"], ev["to_main"], ev["rerun"]) == ("carry", moved, ["marker-gate"]), ev
    assert run_cli("land", b)[1]["action"] == "land-awaits-review"      # B lands after A


def test_twin_a_code_commit_among_the_harvests_resets_and_refuses(tmp_path):
    fx, a, b = _night(tmp_path)
    moved = _push_harvests(fx, MEMORY, HOOKS)
    rc, out = run_cli("land-ack", a, "--note", "ok")
    assert (rc, out["action"], out["moved"]) == (1, "land-awaits-review", "harness-class"), out
    assert origin_main(fx) == moved                            # nothing pushed
    st = lst.load(a)
    assert st["ack"] is None and not st.get("on_box_gates")
    ev = _events(a)[-1]
    assert (ev["decision"], ev["reason"], ev["carried"]) == ("reset", "harness-class", []), ev


@pytest.mark.parametrize("extra", [{}, {"indeterminate_exit": 2}], ids=["plain", "indeterminate_exit"])
def test_twin_a_harvest_that_turns_a_test_gate_red_resets_instead_of_pushing(tmp_path, extra):
    fx, a, b = _night(tmp_path, **extra)
    moved = _push_harvests(fx, MEMORY, BAD_SPEC)
    rc, out = run_cli("land-ack", a, "--note", "ok")
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-failed"), out
    assert origin_main(fx) == moved                            # nothing pushed
    assert lst.load(a)["ack"] is None
    ev = _events(a)
    assert ev[-2]["decision"] == "carry" and ev[-2]["rerun"] == ["marker-gate"], ev
    assert (ev[-1]["decision"], ev[-1]["reason"]) == ("reset", "gate-result"), ev[-1]
