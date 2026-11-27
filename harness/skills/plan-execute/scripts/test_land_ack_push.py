"""R6 §17.3 — `land-ack` pushes in the same step as the approval.

    pytest plan-execute/scripts/test_land_ack_push.py -q

Every case runs the real `run.py` (or the real `land.CLI` row) against a real bare origin.
"""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land  # noqa: E402
import land_push as steps  # noqa: E402
import land_reservation as lr  # noqa: E402
import land_state as lst  # noqa: E402
import lease_guard as lg  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import run_cli as run_cli_subprocess  # noqa: E402,F401  (the real boundary)
from land_test_support import _inproc_session, inproc_cli as run_cli  # noqa: E402,F401
from _land_fixture import (_arm_prepush, ack_only, build_repo, isolate,  # noqa: E402
                           main_side, origin_main, work)
from worktree import git  # noqa: E402

HARVEST = ("projects/p/memory/note.md", "harvested\n", "harvest: 1 live-state")


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("event") == name]


@pytest.fixture
def one(tmp_path):
    """A land waiting for its owner: nothing acked yet."""
    fx = build_repo(tmp_path)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review", out
    return fx, iso


@pytest.fixture
def two(tmp_path):
    fx = build_repo(tmp_path, slugs=("plan-a", "plan-b"))
    a, b = isolate(fx, "plan-a"), isolate(fx, "plan-b")
    work(a["tree"], "src/fa.py", "A = 1\n")
    work(b["tree"], "src/fb.py", "B = 1\n")
    return fx, a["plan_dir"], b["plan_dir"], f"git:{fx['root'].resolve()}"


def _on_origin(fx, sha):
    return git(["merge-base", "--is-ancestor", sha, "refs/heads/main"], fx["origin"])[0] == 0


def test_ack_on_a_current_candidate_pushes_in_the_same_command(one):
    fx, iso = one
    before = origin_main(fx)
    rc, out = run_cli_subprocess("land-ack", iso["plan_dir"], "--note", "ok")
    assert (rc, out["action"], out["outcome"]) == (0, "landed", "pushed"), out
    assert origin_main(fx) != before and _on_origin(fx, out["merge_sha"])
    assert out["ack"]["note"] == "ok"
    assert not lr._path(iso["plan_dir"], f"git:{fx['root'].resolve()}").exists()
    assert [e["outcome"] for e in _events(iso["plan_dir"], "land_ack_push")] == ["pushed"]


def test_ack_after_a_harvest_only_move_pushes_onto_the_new_main(one):
    fx, iso = one
    moved = main_side(fx, *HARVEST)
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "ok")
    assert (rc, out["action"], out["outcome"]) == (0, "landed", "carried"), out
    assert _on_origin(fx, moved) and _on_origin(fx, out["merge_sha"])   # the harvest is kept
    assert out["ack"]["carries"] and out["ack"]["main_head"] == moved


def test_ack_after_a_code_move_refuses_names_the_input_and_keeps_the_ack(one):
    fx, iso = one
    moved = main_side(fx, "hooks/guard.py", "x = 1\n")
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "ok")
    assert (rc, out["action"], out["moved"]) == (1, "land-awaits-review", "harness-class"), out
    assert origin_main(fx) == moved                                      # nothing pushed
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["invalidated_from"]["note"] == "ok"  # the ack stays on record
    assert [e["outcome"] for e in _events(iso["plan_dir"], "land_ack_push")] == ["awaits-review"]


def test_no_path_pushes_without_an_ack(one, monkeypatch):
    fx, iso = one
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])                           # land alone
    assert (out["action"], origin_main(fx)) == ("land-awaits-review", before)
    ctx = lst.context(iso["plan_dir"])
    st = lst.load(iso["plan_dir"], ctx)
    monkeypatch.setattr(steps, "_push_once",
                        lambda *a: pytest.fail("pushed without an ack"))
    lr.acquire(iso["plan_dir"], ctx["resource"], guarded_timeout=60, holder_kind=lg.LAND)
    try:                                                                  # the push step itself
        assert steps.step_push(iso["plan_dir"], ctx, st)["action"] == "land-awaits-review"
    finally:
        lr.release(iso["plan_dir"], ctx["resource"])
    assert origin_main(fx) == before


def _hold_run_lock(plan_dir):
    p = subprocess.Popen([sys.executable, "-c", (
        "import sys, time, lease_guard as lg\n"
        "with lg.run_lock(sys.argv[1]):\n    print('held', flush=True)\n    time.sleep(120)\n"),
        str(plan_dir)], stdout=subprocess.PIPE, text=True, cwd=str(SCRIPTS))
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


def test_ack_while_a_land_of_the_same_plan_runs_records_nothing(one):
    fx, iso = one
    ctx = lst.context(iso["plan_dir"])
    state_file = lst.state_path(ctx["root"], ctx["slug"])
    before, events = state_file.read_bytes(), len(_events(iso["plan_dir"], "plan_land_acked"))
    holder = _hold_run_lock(iso["plan_dir"])
    try:
        rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "ok")
    finally:
        holder.kill()
        holder.wait()
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "land-running"), out
    assert state_file.read_bytes() == before                             # byte-identical
    assert len(_events(iso["plan_dir"], "plan_land_acked")) == events
    assert run_cli("land-ack", iso["plan_dir"], "--note", "ok")[1]["action"] == "landed"


def test_two_land_acks_started_together_run_once(one):
    fx, iso = one
    cmd = [sys.executable, str(SCRIPTS / "run.py"), "land-ack", str(iso["plan_dir"]), "--note", "x"]
    procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, cwd=str(SCRIPTS))
             for _ in range(2)]
    outs = [p.communicate(timeout=600)[0] for p in procs]
    assert sum('"action": "landed"' in o for o in outs) == 1, outs          # exactly one ran
    assert all(('"action": "landed"' in o) or ("land-running" in o) or ('"action": "error"' in o)
               for o in outs), outs      # the other parked, or came after and found nothing to ack
    assert len(_events(iso["plan_dir"], "plan_land_acked")) == 1       # one ack, one run
    assert _on_origin(fx, lst.load(iso["plan_dir"])["landed_sha"])


def test_ack_on_a_lease_another_plan_holds_refuses_and_keeps_the_ack(two):
    fx, a, b, res = two
    assert land.land(a)["action"] == "land-awaits-review"
    assert lr._path(a, res).exists()
    sl.acquire_ship_lock(b, res, guarded_timeout=60, holder_kind=lg.LAND)   # B is mid-land
    before = origin_main(fx)
    out = land.CLI["land-ack"](a, Namespace(note="ok"))
    assert out["action"] == "land-locked", out
    assert out["expires_at"] and 1 <= out["retry_after_seconds"] <= 3600
    st = lst.load(a)
    assert st["state"] == "acked" and st["ack"]["note"] == "ok"              # the ack is kept
    assert not lr._path(a, res).exists()        # §17.2 inv. 5: the answer ended it (answered)
    assert '"why": "answered"' in (Path(a) / "run.ndjson").read_text()
    assert origin_main(fx) == before
    sl.release_ship_lock(b, res)
    assert land.CLI["land"](a, None)["action"] == "landed"                   # starts from the ack


def test_no_other_plan_can_take_the_lease_between_the_answer_and_the_next_step(two, monkeypatch):
    fx, a, b, res = two
    assert land.land(a)["action"] == "land-awaits-review"
    seen = {}

    def probe(plan_dir, ctx, st):                       # the first thing the continued land does
        try:
            lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
            seen["b"] = "took it"
        except rsi.LockError as e:
            seen["b"] = type(e).__name__
        seen["holder"] = json.loads(sl._ship_lock_path(a, res).read_text())["plan_dir"]
        return None

    monkeypatch.setattr(steps, "STEPS", (probe, *steps.STEPS))
    out = land.CLI["land-ack"](a, Namespace(note="ok"))
    assert out["action"] == "landed", out
    assert seen["b"] == "LockError"                    # B never got in
    assert Path(seen["holder"]).resolve() == Path(a).resolve()    # and A held it throughout


def test_a_harvest_inside_the_push_window_is_absorbed_by_the_resync(one):
    fx, iso = one
    before = origin_main(fx)
    _arm_prepush(fx, times=1)
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "ok")
    assert (out["action"], out["outcome"]) == ("landed", "carried"), out
    st = lst.load(iso["plan_dir"])
    assert st["ack"]["resyncs"] and _on_origin(fx, st["landed_base"])
    assert git(["merge-base", "--is-ancestor", st["landed_base"], st["landed_sha"]],
               fx["root"])[0] == 0                      # expected is a commit the push contains
    assert origin_main(fx) != before


def test_a_plan_branch_that_moves_in_the_push_window_is_refused(one, monkeypatch):
    fx, iso = one
    real, calls = steps._push_once, []

    def racing(ctx, st, path):
        calls.append(1)
        if len(calls) == 1:                              # an out-of-band commit, then a stale reject
            work(iso["tree"], "src/g.py", "G = 1\n")
            return 1, "", "! [rejected] HEAD -> main (stale info)"
        return real(ctx, st, path)

    monkeypatch.setattr(steps, "_push_once", racing)
    before = origin_main(fx)
    out = land.CLI["land-ack"](iso["plan_dir"], Namespace(note="ok"))
    assert out["action"] == "land-awaits-review" and len(calls) == 1, out
    assert origin_main(fx) == before                    # nothing pushed


def test_a_lease_naming_a_commit_the_head_lacks_never_pushes(one, monkeypatch):
    fx, iso = one
    assert ack_only(iso["plan_dir"], "ok")[1]["action"] == "land-acked"
    moved = main_side(fx, *HARVEST)
    git(["fetch", "origin", "main"], fx["root"])
    ctx = lst.context(iso["plan_dir"])
    st = lst.load(iso["plan_dir"], ctx)
    st["expected"] = st["ack"]["main_head"] = moved       # a fetch named a commit nobody merged
    monkeypatch.setattr(steps, "_push_once", lambda *a: pytest.fail("pushed"))
    lr.acquire(iso["plan_dir"], ctx["resource"], guarded_timeout=60, holder_kind=lg.LAND)
    try:
        out = steps.step_push(iso["plan_dir"], ctx, st)
    finally:
        lr.release(iso["plan_dir"], ctx["resource"])
    assert (out["action"], out["kind"]) == ("land-parked", "expected-not-in-head"), out
    assert origin_main(fx) == moved


def test_a_main_move_between_the_land_and_the_final_record_keeps_the_new_commit(one, monkeypatch):
    fx, iso = one
    real, moved = steps._push_once, []

    def push_then_move(ctx, st, path):
        res = real(ctx, st, path)
        if not moved:                                    # the land push is done; main moves on
            moved.append(main_side(fx, *HARVEST))
        return res

    monkeypatch.setattr(steps, "_push_once", push_then_move)
    out = land.CLI["land-ack"](iso["plan_dir"], Namespace(note="ok"))
    assert out["action"] == "landed", out
    st = lst.load(iso["plan_dir"])
    assert _on_origin(fx, moved[0]) and _on_origin(fx, st["landed_sha"])
    assert _on_origin(fx, st["final_record_sha"])        # the record rode a push that kept it


@pytest.mark.parametrize("answered", [True, False])
def test_an_early_park_ends_the_reservation_answered_or_not(two, monkeypatch, answered):
    fx, a, b, res = two
    assert land.land(a)["action"] == "land-awaits-review"
    assert lr._path(a, res).exists()
    monkeypatch.setattr(land, "_preflight", lambda *x: {"action": "land-parked", "kind": "x"})
    assert land.land(a, answered=answered)["kind"] == "x"
    assert not lr._path(a, res).exists()


def test_an_answer_for_a_path_that_is_not_a_plan_creates_no_lock_file(tmp_path):
    out = land.CLI["land-ack"](str(tmp_path), Namespace(note="ok"))
    assert out["action"] == "error", out
    assert not (tmp_path / ".land-run.lock").exists()
