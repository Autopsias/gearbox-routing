"""LND-01 — the land stage (contract §4 land, §5 approval binding, §8.c record).

Every check runs against a REAL bare origin, a REAL primary checkout and REAL
linked worktrees, because every failure this module exists to prevent is a git
behaviour and not a Python one: a ``worktree add`` git refuses outright, a
compare-and-swap push that must reject on stale info, a merge that must never be
auto-resolved, a local branch that must not move. Each check that could only ever
report "clean" is paired with a KNOWN POSITIVE that makes it report something.

The fixture is IMPORTED from ``_land_fixture`` rather than re-derived, so these
tests and the shipped evidence exercise one fixture and cannot drift apart.

    pytest plan-execute/scripts/test_land.py -q
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish  # noqa: E402
import land  # noqa: E402
import land_brief as lb  # noqa: E402
import land_state as lst  # noqa: E402
import dispatch as dsp  # noqa: E402
import land_steps  # noqa: E402
from _land_fixture import (_arm_prepush, build_repo,  # noqa: E402
                           checked_out_branch, isolate, local_main, main_side,
                           origin_main, run_cli, work)
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _ack_and_land(plan_dir):
    """Park -> ack -> land, ASSERTING each step. Discarding the first two results
    was a real hole: a setup whose park or ack silently failed would leave the
    final assertion testing a different path than the one named."""
    rc, parked = run_cli("land", plan_dir)
    assert (rc, parked["action"]) == (1, "land-awaits-review"), parked
    rc, acked = run_cli("land-ack", plan_dir, "--note", "approved")
    assert (rc, acked["action"]) == (0, "land-acked"), acked
    return run_cli("land", plan_dir)


# ---------------------------------------------------- §5.1c the digest, pinned
def test_gate_digest_is_order_and_noise_independent():
    a = [{"gate_id": "b", "outcome": "pass"}, {"gate_id": "a", "outcome": "fail",
                                               "findings_count": 3}]
    b = [{"gate_id": "a", "outcome": "fail", "findings_count": 3, "duration": 91,
          "host": "laptop"}, {"gate_id": "b", "outcome": "pass", "excerpt": "noise"}]
    assert land.gate_digest(a) == land.gate_digest(b)
    # KNOWN POSITIVE — a digest that never changes is not a binding. Change the
    # one thing §5.2 calls "a changed gate result" and it MUST move.
    flipped = [{"gate_id": "b", "outcome": "fail"}, {"gate_id": "a", "outcome": "fail",
                                                     "findings_count": 3}]
    assert land.gate_digest(flipped) != land.gate_digest(a)
    assert len(land.gate_digest(a)) == 64


# union_gates coverage lives in test_land_union.py — pure-dict tests that need
# no git fixture, extracted 2026-08-27 when this file hit its size bound.


# ------------------------------------------- the trigger: land BEFORE complete
def test_complete_becomes_land_on_an_isolated_plan_and_is_untouched_below_the_gate(fx):
    iso = isolate(fx, "plan-a")
    acted = land.land_action(iso["plan_dir"], {"action": "complete"})
    assert acted["action"] == "land"
    assert acted["plan_branch"] == "plan/plan-a"
    # A non-complete action passes through untouched — land never pre-empts a
    # dispatch, a checkpoint or a blocker.
    assert land.land_action(iso["plan_dir"], {"action": "dispatch"})["action"] == "dispatch"
    # KNOWN NEGATIVE — a plan with no isolation claim (every plan on disk today,
    # below §6's version gate) completes exactly as it does now.
    plain = fx["root"] / "_plans" / "legacy"
    plain.mkdir(parents=True)
    assert land.land_action(plain, {"action": "complete"})["action"] == "complete"


def test_a_landed_plan_completes_instead_of_landing_again(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _ack_and_land(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert land.land_action(iso["plan_dir"], {"action": "complete"})["action"] == "complete"
    again = land.land(iso["plan_dir"])                    # idempotent, not a re-push
    assert again["action"] == "landed" and again["message"].startswith("already landed")


# ------------------------------------------------------------ §4.6 the sync step
def test_sync_parks_on_a_conflicting_main_edit_and_merges_a_disjoint_one(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "shared.txt", "the plan's line\n")
    main_side(fx, "shared.txt", "main's line\n")          # SAME file, both sides
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "sync-conflict")
    assert "shared.txt" in out["conflicts"]
    assert "git merge --abort" in out["brief"] and "land-resume" in out["brief"]
    # The plan worktree is restored, not left mid-merge, and NOTHING was pushed.
    assert not lst.merge_in_progress(iso["tree"])
    # KNOWN NEGATIVE — a DISJOINT main-side edit merges clean and lands.
    fx2 = build_repo(fx["tmp"] / "disjoint")
    iso2 = isolate(fx2, "plan-a")
    work(iso2["tree"], "src/f.py", "F = 1\n")
    main_side(fx2, "src/other.py", "OTHER = 1\n")
    before = origin_main(fx2)
    rc2, out2 = _ack_and_land(iso2["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")
    assert origin_main(fx2) != before


def test_sync_refuses_a_dirty_plan_worktree(fx):
    iso = isolate(fx, "plan-a")
    (iso["tree"] / "src" / "uncommitted.py").write_text("LOST = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "plan-worktree-dirty")


# ------------------------------------------------------ §4.4 the re-gate, always
def test_a_failing_regate_parks_with_the_gate_output(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")     # real work, so removing the marker
    work(iso["tree"], "GATE_FAIL", "the marker the fixture gate refuses on\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-failed")
    assert out["failed"] == ["marker-gate"]
    assert origin_main(fx) == local_main(fx)              # nothing advanced
    # A CACHED RED MUST NOT AGE INTO A GREEN. Nothing changed, so the re-gate is
    # served from its cache — and the first cut of that cache returned "continue"
    # on any existing digest, which walked a failed gate straight to the approval
    # step on the very next invocation.
    rc_again, again = run_cli("land", iso["plan_dir"])
    assert (rc_again, again["action"], again["kind"]) == (1, "land-parked", "gate-failed")
    # KNOWN NEGATIVE — remove the marker and the SAME gate passes and lands, so
    # the park above is the gate deciding, not the gate being unable to run.
    git(["rm", "-q", "GATE_FAIL"], iso["tree"], check=True)
    git(["commit", "-q", "-m", "drop the marker"], iso["tree"], check=True)
    rc2, out2 = _ack_and_land(iso["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")


def test_a_moved_plan_head_rebuilds_the_candidate_instead_of_reusing_a_stale_merge(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    first = lst.load(iso["plan_dir"])
    later = work(iso["tree"], "src/late.py", "LATE = 1\n", "a session commits after the park")
    run_cli("land", iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    assert st["plan_head"] == later
    assert st["merge_sha"] != first["merge_sha"]           # REBUILT, not reused
    assert st["merged_plan_head"] == later
    assert (Path(st["land_path"]) / "src" / "late.py").exists()
    # ...and the human is shown the new candidate, never the old one.
    assert st["candidate"]["plan_head"] == later


def test_an_unresolvable_gate_parks_rather_than_counting_as_green(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    (iso["plan_dir"] / "manifest.json").write_text(json.dumps({"plan_schema_version": 7,
        "sessions": [{"id": "s01", "verify": {"gates": ["no-such-gate"]}}]}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-unresolvable")


# --------------------------------------- §5 the ack: un-skippable, and BOUND
def test_no_entry_point_pushes_without_an_ack(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    before = origin_main(fx)
    for cmd in ("land", "land-resume"):
        rc, out = run_cli(cmd, iso["plan_dir"])
        assert (rc, out["action"]) == (1, "land-awaits-review"), cmd
        assert origin_main(fx) == before, cmd
    # An ack cannot be forged ahead of the candidate it is supposed to bind to.
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "forged")
    assert out["action"] == "land-acked"          # the candidate DOES exist now
    st = lst.load(iso["plan_dir"])
    assert st["ack"]["main_head"] == st["expected"] == before
    # KNOWN POSITIVE — with the ack the SAME command lands, so the parks above
    # are the checkpoint refusing and not a fixture that cannot land at all.
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert origin_main(fx) != before


def test_land_ack_refuses_before_there_is_anything_to_approve(fx):
    iso = isolate(fx, "plan-a")
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "too early")
    assert (rc, out["action"]) == (1, "error")


def test_a_moved_main_invalidates_the_ack_and_re_parks(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved THIS candidate")
    approved = lst.load(iso["plan_dir"])["ack"]["main_head"]
    moved = main_side(fx, "src/elsewhere.py", "E = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review")
    assert out["invalidated_prior_ack"]["main_head"] == approved
    assert lst.load(iso["plan_dir"])["ack"] is None       # cleared, not warned about
    assert origin_main(fx) == moved                       # we did NOT land
    assert "RE-APPROVAL" in out["brief"] and "main_head" in out["brief"]
    # KNOWN POSITIVE — a fresh ack on the NEW candidate lands.
    run_cli("land-ack", iso["plan_dir"], "--note", "re-approved")
    rc2, out2 = run_cli("land", iso["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")


def test_a_rebuilt_merge_cannot_ride_an_ack_granted_for_the_previous_one(fx):
    """§5.1d binds the ack to the MERGE COMMIT, not only to the triple.

    Found 2026-08-23 by a review of this branch. The approval was keyed on
    plan_head + main_head + gate_digest; the merge sha was shown in the brief and
    recorded into the ack, and never compared. Removing the land worktree by hand
    — which ``land_push.already_landed`` documents as an ordinary operator move —
    makes ``step_worktree`` rebuild the merge, and that path skips the teardown
    branch that pops ``merge_sha``. None of the three bound values move, so a
    merge commit no human ever saw was pushed to the default branch.

    The rebuild must land in a LATER second than the first merge or git produces
    a byte-identical commit (same tree, same parents, same 1-second-granularity
    timestamps) and there is no second merge to catch — hence the sleep.
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved THIS merge")
    st = lst.load(iso["plan_dir"])
    approved_merge = st["ack"]["merge_sha"]
    assert approved_merge, "the ack must record the merge it approved"

    # `git worktree remove` — the operator's own tidy-up, which deletes the
    # directory AND unregisters it. (A bare `rm -rf` leaves the registration
    # behind and the next land fails cleanly at `land-worktree-failed`; it is
    # this well-formed removal that reaches the rebuild.)
    assert git(["worktree", "remove", "--force", st["land_path"]], fx["root"])[0] == 0
    time.sleep(1.1)                                # force a distinct committer second

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), (
        f"a rebuilt merge rode the old ack: {out.get('action')}/{out.get('kind')} "
        f"{json.dumps(out)[:400]}")
    after = lst.load(iso["plan_dir"])
    assert after["merge_sha"] != approved_merge    # it really did rebuild
    assert after["ack"] is None                    # cleared, not merely warned about
    assert origin_main(fx) != after["merge_sha"]   # and nothing was pushed

    # KNOWN POSITIVE — approving the REBUILT merge lands it.
    run_cli("land-ack", iso["plan_dir"], "--note", "approved the rebuild")
    rc2, out2 = run_cli("land", iso["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")


def test_a_changed_gate_result_invalidates_the_ack(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved a green gate set")
    before = lst.load(iso["plan_dir"])["gate_digest"]
    work(iso["tree"], "GATE_FAIL", "planted after approval\n")   # plan head moves too
    rc, out = run_cli("land", iso["plan_dir"])
    assert rc == 1 and out["action"] == "land-parked" and out["kind"] == "gate-failed"
    assert lst.load(iso["plan_dir"])["gate_digest"] != before
    assert lst.load(iso["plan_dir"])["ack"] is None


# --------------------------------------- §4.2/§4.3 the merge, and what it spares
def test_the_land_worktree_is_detached_uniquely_named_and_never_moves_local_main(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])                       # parks at the review
    st = lst.load(iso["plan_dir"])
    land_path = Path(st["land_path"])
    assert land_path.parent.name == ".plan-worktrees"      # a SIBLING (§1.1a)
    assert land_path.name.startswith("plan-a__land-") and land_path.name != "plan-a__land-"
    assert git(["symbolic-ref", "--quiet", "HEAD"], land_path)[0] != 0   # DETACHED
    before_main, before_branch = local_main(fx), checked_out_branch(fx)
    run_cli("land-ack", iso["plan_dir"], "--note", "ok")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert local_main(fx) == before_main                   # §4.3 — NEVER moved
    assert checked_out_branch(fx) == before_branch == "main"
    assert out["local_default_unchanged"] is True
    # ...and origin/main really does carry the merge, verified by git, not by
    # the printed sha: a command reporting success is not proof.
    assert git(["merge-base", "--is-ancestor", out["merge_sha"], "origin/main"],
               fx["root"])[0] == 0
    assert out["land_worktree_teardown"] == "removed" and not land_path.exists()
    # The PLAN worktree (distinct from the LAND worktree torn down above) and its
    # branch are §15/s09's STEP 9 — cleanup, run as `land`'s own last step once the
    # push and the final record both succeeded. A clean plan worktree tears down
    # too, and the local branch goes with it (see test_plan_teardown.py for the
    # full cleanup contract, including the preserved-and-kept refusal path).
    assert out["cleanup"]["worktree"] == "removed"
    assert not Path(iso["tree"]).is_dir()
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] != 0


def test_the_record_rides_the_same_push_and_stays_inside_the_plans_pathspec(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _ack_and_land(iso["plan_dir"])
    assert out["action"] == "landed"
    st = lst.load(iso["plan_dir"])
    landed = git(["diff", "--name-only", f"{st['merge_sha']}..{out['merge_sha']}"],
                 fx["root"])[1].splitlines()
    assert landed and all(p.startswith("_plans/plan-a/") for p in landed)
    # the live outer record is what got committed, not the worktree's frozen copy
    assert git(["show", f"{out['merge_sha']}:_plans/plan-a/run_state.json"],
               fx["root"])[0] == 0


# ------------------------------------------------- §4.5 bounded re-sync, §4.7 push
def test_a_main_that_moves_mid_push_resyncs_once_and_lands(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    _arm_prepush(fx, times=1)                 # one competing lander, then stand down
    rc, out = _ack_and_land(iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert len(st["push_attempts"]) == 1
    # The rejection must be one the classifier RECOGNISES as re-syncable. When a
    # competing lander wins inside our push window the receiving end words it
    # `cannot lock ref … but expected …`, not `fetch first` — matching only the
    # two strings §4.5 quotes classified that as a hard failure (measured here).
    assert lst.is_stale(st["push_attempts"][0]["output"])
    assert st["ack"]["resyncs"]
    assert st["ack"]["main_head"] == st["ack"]["resyncs"][-1]["main_head"]
    assert st["ack"]["main_head"] != st["push_attempts"][0]["expected"]
    assert git(["merge-base", "--is-ancestor", out["merge_sha"], "origin/main"],
               fx["root"])[0] == 0


def test_a_main_that_moves_on_every_attempt_exhausts_the_bound_and_parks(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    _arm_prepush(fx, times=99)
    rc, out = _ack_and_land(iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-parked")
    assert out["kind"] in ("push-rejected", "resync-exhausted")
    assert len(st["push_attempts"]) == lst.MAX_RESYNC == 3
    # §4.7 — nothing is cleaned up on a rejected push. The work exists only there.
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0
    assert Path(st["land_path"]).is_dir()
    assert "Branch kept" in out["brief"] and "land-resume" in out["brief"]


def test_a_repo_with_no_remote_lands_locally_and_says_so(tmp_path):
    fx = build_repo(tmp_path)
    git(["remote", "remove", "origin"], fx["root"], check=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    before = local_main(fx)
    rc, out = _ack_and_land(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert local_main(fx) == before                        # §4.3 holds here too
    ref = "refs/plan-lands/plan-a"
    st = lst.load(iso["plan_dir"])
    # §8.c2: in a no-remote repo the FINAL-record commit is what the durable ref
    # is re-pointed at, and the merge is its ancestor.
    assert git(["rev-parse", "--verify", "--quiet", ref], fx["root"])[1] == st["final_record_sha"]
    assert git(["merge-base", "--is-ancestor", out["merge_sha"], ref], fx["root"])[0] == 0
    assert "LANDED LOCALLY ONLY" in out["brief"] and ref in out["brief"]
    # §4.8a — the durable ref is the only thing keeping the commit reachable.
    git(["reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all"],
        fx["root"], check=True)
    git(["gc", "--prune=now"], fx["root"], check=True)
    assert git(["cat-file", "-t", out["merge_sha"]], fx["root"]) == (0, "commit", "")


# ------------------------------------------------------- §4.1 the lease, serial
def test_the_repo_lease_excludes_a_land_ACROSS_PROCESSES(fx):
    """An in-process fixture proves nothing here: the whole claim is that the
    lease excludes between separate `run.py` invocations."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    v6 = fx["root"] / "_plans" / "legacy-v6"
    v6.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "_land_lease_holder.py"), str(v6), "30"],
        stdout=subprocess.PIPE, text=True)
    import ship_locks as sl
    try:
        ready = holder.stdout.readline().strip()
        assert ready.startswith("READY")
        _, holder_resource, holder_lock = ready.split(" ", 2)
        rc, out = run_cli("land", iso["plan_dir"])
        assert (rc, out["action"]) == (1, "land-locked")
        # ASSERTED, not assumed: two spellings of one repo (`/var/...` vs its
        # resolved `/private/var/...`) slug to two DIFFERENT lock files and BOTH
        # sides "acquire" — s03b's `same file? False`, reproduced in this very
        # fixture on 2026-08-22 before the holder derived its resource the way
        # production does. Without this line the test can pass on nothing.
        assert holder_lock == str(sl._ship_lock_path(iso["plan_dir"], out["resource"]))
    finally:
        holder.terminate()
        holder.wait(timeout=30)
    Path(sl._ship_lock_path(v6, holder_resource)).unlink(missing_ok=True)
    # KNOWN POSITIVE — once released, the identical command proceeds.
    rc2, out2 = run_cli("land", iso["plan_dir"])
    assert out2["action"] == "land-awaits-review"


# --------------------------------------------- crash recovery: resume, not restart
def test_a_kill_between_the_merge_and_the_push_resumes_idempotently(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 300, "argv":
                         ["/bin/sh", "-c", f"[ -f '{fx['root']}/.SLOW' ] && sleep 60; "
                          "test ! -f GATE_FAIL"]}}))
    (fx["root"] / ".SLOW").touch()
    before = origin_main(fx)
    proc = subprocess.Popen([sys.executable, str(SCRIPTS / "run.py"), "land",
                             str(iso["plan_dir"])], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=str(SCRIPTS))
    merged = None
    deadline = time.time() + 60
    while time.time() < deadline:
        st = lst.load(iso["plan_dir"]) or {}
        if st.get("merge_sha") and st.get("record_sha"):
            merged = st["merge_sha"]
            break
        time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=30)
    assert merged, "the merge never happened — the kill window was wrong"
    assert origin_main(fx) == before                       # nothing was pushed
    (fx["root"] / ".SLOW").unlink()
    rc, out = _ack_and_land(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert lst.load(iso["plan_dir"])["merge_sha"] == merged   # RESUMED, not redone
    assert origin_main(fx) != before


def test_a_kill_inside_the_merge_confines_the_mess_to_the_land_worktree(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    hook = fx["root"] / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text(f"#!/bin/sh\n[ -f '{fx['root']}/.SLOW' ] && sleep 60\nexit 0\n")
    hook.chmod(0o755)
    (fx["root"] / ".SLOW").touch()
    before_local, before_origin = local_main(fx), origin_main(fx)
    proc = subprocess.Popen([sys.executable, str(SCRIPTS / "run.py"), "land",
                             str(iso["plan_dir"])], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=str(SCRIPTS))
    land_path, deadline = None, time.time() + 60
    while time.time() < deadline:
        st = lst.load(iso["plan_dir"]) or {}
        land_path = st.get("land_path")
        if land_path and lst.merge_in_progress(land_path):
            break
        time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=30)
    (fx["root"] / ".SLOW").unlink()
    assert land_path and lst.merge_in_progress(land_path)
    # The mess is CONFINED: the operator's checkout is untouched and no ref moved.
    assert local_main(fx) == before_local and origin_main(fx) == before_origin
    assert checked_out_branch(fx) == "main"
    assert not lst.merge_in_progress(fx["root"]) and not lst.merge_in_progress(iso["tree"])
    # The next land NAMES it rather than auto-concluding a merge nobody watched.
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "merge-in-progress")
    assert "git merge --abort" in out["brief"]
    # ...and cleanup owns it: the brief's own command, then the land completes.
    git(["merge", "--abort"], land_path, check=True)
    rc2, out2 = _ack_and_land(iso["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")
    assert origin_main(fx) != before_origin


# ------------------------------------- the fixes the adversarial review forced
def test_the_claim_reaches_disk_BEFORE_the_land_worktree_exists(fx, monkeypatch):
    """§4.1a step 3 — a crash between `worktree add` and the state write would
    leave a registered worktree named by a uuid4 nothing can re-derive: §1.5's
    permanently untouchable UNKNOWN, created by an ordinary interruption."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    seen = {}
    real_add = land_steps.git

    def spy(args, cwd, **kw):
        if args[:2] == ["worktree", "add"]:
            # AT THE MOMENT OF CREATION, the claim must already name this path.
            seen["state"] = lst.load(iso["plan_dir"]) or {}
        return real_add(args, cwd, **kw)

    monkeypatch.setattr(land_steps, "git", spy)
    land.land(iso["plan_dir"])
    assert seen, "the worktree was never created — the spy saw nothing"
    assert seen["state"].get("land_path"), "the claim was written AFTER the worktree"
    assert seen["state"]["land_path"] == lst.load(iso["plan_dir"])["land_path"]


def test_an_indeterminate_gate_is_not_a_failure_and_is_never_green(fx):
    """A gate exiting its DECLARED indeterminate code could not decide. Charging
    that as a `fail` bakes a false verdict into §5.1c's digest, so re-running to a
    real answer would then invalidate the human's ack (§5.2)."""
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 120, "indeterminate_exit": 2,
                         "argv": ["/bin/sh", "-c",
                                  "[ -f UNDECIDED ] && exit 2; test ! -f GATE_FAIL"]}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")     # ...still leaves a real surface
    work(iso["tree"], "UNDECIDED", "the reviewer never answered\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-indeterminate")
    assert out["undecided"] == ["marker-gate"]
    st = lst.load(iso["plan_dir"])
    assert [g["outcome"] for g in st["gates"]] == ["skipped"]
    assert origin_main(fx) == local_main(fx)                  # nothing advanced
    # KNOWN POSITIVE — the SAME gate, deciding, lands. So the park above is the
    # gate declining to answer, not the gate being unable to run.
    git(["rm", "-q", "UNDECIDED"], iso["tree"], check=True)
    git(["commit", "-q", "-m", "the reviewer answered"], iso["tree"], check=True)
    rc2, out2 = _ack_and_land(iso["plan_dir"])
    assert (rc2, out2["action"]) == (0, "landed")


def test_the_regate_excludes_the_plans_own_record_from_the_review_surface(fx):
    """§5.1b — `_plans/<slug>/` is excluded from the gate's file set. The gate runs
    on the tree that WILL be pushed, which now carries the record commit."""
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 120,
                         "env_allowlist": ["PLAN_EXECUTE_REVIEW_BASE",
                                           "PLAN_EXECUTE_REVIEW_SCOPE",
                                           "PLAN_EXECUTE_REVIEW_EXCLUDE"],
                         "argv": ["/bin/sh", "-c",
                                  'python3 -c "import os,subprocess,sys;'
                                  'ex=os.environ.get(\'PLAN_EXECUTE_REVIEW_EXCLUDE\',\'\');'
                                  'b=os.environ[\'PLAN_EXECUTE_REVIEW_BASE\'];'
                                  'sys.path.insert(0,\'%s\');'
                                  'import llm_review_surface as s;'
                                  'f=s.diff_stat(\'.\', b, (), s.parse_scope(ex));'
                                  'open(\'/tmp/land-surface.txt\',\'w\').write(ex+chr(10)'
                                  '+chr(10).join(f))" ' % SCRIPTS]}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    surface = Path("/tmp/land-surface.txt").read_text().splitlines()
    assert surface[0] == "_plans/plan-a", f"the exclusion never reached the gate: {surface[0]!r}"
    assert "src/f.py" in surface, "the gate saw none of the plan's actual work"
    assert not [p for p in surface[1:] if p.startswith("_plans/")], \
        f"the plan's own record is still in the review surface: {surface}"


def test_a_skill_kind_regate_gate_hands_the_orchestrator_a_directive(fx):
    """The skill-gate branch had ZERO coverage. It is the one place the land waits
    on the orchestrator, and an unexercised handshake is an unimplemented one."""
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "skill", "skill": "test-orchestrate", "args": "--smoke"}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["gate"]) == (1, "invoke-skill", "marker-gate")
    assert out["skill"] == "test-orchestrate" and out["cwd"] == lst.load(iso["plan_dir"])["land_path"]
    assert origin_main(fx) == local_main(fx)                  # nothing advanced
    # A FAILED skill gate parks; it never becomes a pass by having been recorded.
    run_cli("land-record", iso["plan_dir"], "--gate", "marker-gate", "--status", "failed")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-failed")
    # KNOWN POSITIVE — recorded as passed, the same land proceeds to the human.
    run_cli("land-record", iso["plan_dir"], "--gate", "marker-gate", "--status", "passed")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review")
    assert lst.load(iso["plan_dir"])["gates"][0]["outcome"] == "pass"


def test_a_renamed_default_branch_parks_rather_than_landing_on_the_wrong_one(fx):
    """§4.0 — the recorded `<default>` is asserted before the merge. This check
    could never fire while the fixture left `refs/remotes/origin/HEAD` unset."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert lst.context(iso["plan_dir"])["default"] == "main"   # read from origin/HEAD
    git(["update-ref", "refs/remotes/origin/trunk", "refs/remotes/origin/main"],
        fx["root"], check=True)
    git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"],
        fx["root"], check=True)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "default-renamed")
    assert "trunk" in out["brief"] and "main" in out["brief"]


def test_the_brief_is_readable_text_not_an_escaped_json_string(fx):
    """§4.6 requires the recovery commands VERBATIM and copy-pasteable; `_out` is
    `json.dumps`, so the only readable copy is the notice file + the banner."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "shared.txt", "the plan's line\n")
    main_side(fx, "shared.txt", "main's line\n")
    run_cli("land", iso["plan_dir"])
    notice = iso["plan_dir"] / lst.NOTICE_FILE
    assert notice.is_file() and "git merge --abort" in notice.read_text()
    assert "\n" in notice.read_text()                          # real newlines
    proc = subprocess.run([sys.executable, str(SCRIPTS / "run.py"), "land",
                           str(iso["plan_dir"])], capture_output=True, text=True,
                          cwd=str(SCRIPTS))
    assert "git diff --name-only --diff-filter=U" in proc.stdout.split("{")[0]


def test_an_unfinished_land_keeps_the_plan_out_of_the_reapable_done_class(fx):
    """§15.3 — "REPORTED, never reaped". Read straight off `next_action` a
    land-parked plan is `done`, and `registry_owners` then calls its worktree
    `leftover`/`stale` while a human still has work sitting in it."""
    import registry_plans as rp
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # ONE variable under test. With sessions still TODO the plan is non-terminal
    # for a reason that has nothing to do with landing, and the assertion below
    # would pass whatever the land state said — a check pointed at the wrong
    # thing. An empty session list makes `next_action` say `complete`, so the
    # ONLY thing that can keep this plan out of `done` is the unfinished land.
    (iso["plan_dir"] / "manifest.json").write_text(json.dumps(
        {"plan_schema_version": 7, "sessions": []}))
    assert dsp.next_action(json.loads((iso["plan_dir"] / "manifest.json").read_text()),
                           {}).get("action") == "complete"
    assert rp.plan_lifecycle_active(iso["plan_dir"]) is True   # unlanded => not done
    assert rp.land_row(iso["plan_dir"]) is None                # nothing to report yet
    run_cli("land", iso["plan_dir"])
    row = rp.land_row(iso["plan_dir"])
    assert row["state"] == "awaiting_review" and row["awaiting_ack"] is True
    assert row["land_worktree"] == lst.load(iso["plan_dir"])["land_path"]
    assert rp.plan_lifecycle_active(iso["plan_dir"]) is True
    # A RETIRED plan has no land to ack: retire-plan never touches land.json.
    import plan_worktree as pwt
    state = pwt.load_state(iso["plan_dir"])
    pwt._save(iso["plan_dir"], {**state, "retired": {"reason": "merged by PR"}})
    assert rp.land_row(iso["plan_dir"]) is None
    pwt._save(iso["plan_dir"], state)
    assert rp.land_row(iso["plan_dir"])["awaiting_ack"] is True
    # KNOWN POSITIVE — landed stays live until finish records `finished_at`.
    rc, out = _ack_and_land(iso["plan_dir"])
    assert out["action"] == "landed" and rp.land_row(iso["plan_dir"]) is None
    assert rp.plan_lifecycle_active(iso["plan_dir"]) is True
    finish._save(finish.context(iso["plan_dir"]), {"armed_at": "t", "finished_at": "t"})
    assert rp.plan_lifecycle_active(iso["plan_dir"]) is False


def test_the_final_record_rides_a_second_push_and_carries_no_code(fx):
    """§8.c2 — the record written AFTER the land push has its own CAS push, and
    its exemption from the re-gate is earned mechanically: pathspec-only."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _ack_and_land(iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert st["final_record_sha"], "§8.c2 step 2 never ran"
    landed_head = git(["rev-parse", "origin/main"], fx["root"], check=True)[1]
    assert landed_head == st["final_record_sha"]
    added = git(["diff", "--name-only", f"{st['landed_sha']}..{landed_head}"],
                fx["root"], check=True)[1].splitlines()
    assert added and all(p.startswith("_plans/plan-a/") for p in added), added
    # ...and the land worktree is removed LAST, after that push, not before it.
    assert not Path(st["land_path"]).exists()
    assert st["land_worktree_teardown"]["status"] == "removed"


def test_a_gate_cwd_outside_the_land_worktree_parks_instead_of_running_there(fx):
    """Returning the declared cwd unmapped would execute a gate argv in the
    OPERATOR'S checkout — the collision isolation exists to prevent."""
    outside = fx["tmp"] / "not-the-repo"
    outside.mkdir()
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": str(outside), "timeout": 60,
                         "argv": ["/bin/sh", "-c", "touch RAN_IN_THE_WRONG_TREE"]}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-cwd-outside-land")
    assert "missing from, or resolves outside," in out["brief"]
    assert not (outside / "RAN_IN_THE_WRONG_TREE").exists()     # it never ran
    assert not (fx["root"] / "RAN_IN_THE_WRONG_TREE").exists()


def test_a_changed_gate_SET_is_not_covered_by_an_old_green_verdict(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review"
    first_key = lst.load(iso["plan_dir"])["gate_key"]
    # Same candidate, DIFFERENT gate definition — the old green describes gates
    # that are no longer the declared ones.
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 60,
                         "argv": ["/bin/sh", "-c", "exit 1"]}}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-failed")
    assert lst.load(iso["plan_dir"])["gate_key"] != first_key


def test_a_land_that_adds_nothing_outside_its_own_record_parks(fx):
    """§10.3 — an unexamined zero is a check pointed at nothing."""
    iso = isolate(fx, "plan-a")            # no `work()`: the plan committed nothing
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-empty-surface")
    assert lst.load(iso["plan_dir"])["files_examined"] == 0
    # KNOWN POSITIVE — one real commit and the same check reports a surface.
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _ack_and_land(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert lst.load(iso["plan_dir"])["files_examined"] >= 1


def test_a_skill_gate_verdict_does_not_carry_across_candidates(fx):
    """A recorded PASS is stamped with the candidate it was issued against. Reused
    across a different merge it would be an approval of work nobody reviewed."""
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "skill", "skill": "test-orchestrate"}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-record", iso["plan_dir"], "--gate", "marker-gate", "--status", "passed")
    stamped = lst.load(iso["plan_dir"])["skill_gates"]["marker-gate"]["gate_key"]
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review"          # the stamped verdict counts
    work(iso["tree"], "src/late.py", "LATE = 1\n")       # a DIFFERENT candidate
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "invoke-skill")      # asks again, never reuses
    assert out["gate_key"] != stamped
    # ...and a PASS naming an artifact must be able to show it.
    rc, out = run_cli("land-record", iso["plan_dir"], "--gate", "marker-gate",
                      "--status", "passed", "--result-file", str(fx["tmp"] / "nope.txt"))
    assert (rc, out["action"]) == (1, "error")


def test_a_preserved_land_worktree_is_never_reported_as_a_finished_land(fx):
    """§12.4's dirty-content refusal is the safety check. Marking `landed` anyway
    would hide the unresolved worktree behind a green state forever."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    # Someone left untracked content in the land worktree before the teardown.
    (Path(lst.load(iso["plan_dir"])["land_path"]) / "hand-written.txt").write_text("mine\n")
    rc, out = run_cli("land", iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "land-worktree-preserved")
    assert st["state"] != "landed" and st["landed_sha"]        # pushed, not finished
    assert Path(st["land_path"]).is_dir()                      # nothing was forced
    assert git(["merge-base", "--is-ancestor", st["landed_sha"], "origin/main"],
               fx["root"])[0] == 0
    # KNOWN POSITIVE — clear the content and the SAME command finishes. The CODE
    # is never re-pushed: the merge commit is unchanged and everything the resume
    # adds is confined to the plan's own record (§8.c2's final record legitimately
    # grows, because it carries what was written while the land was parked).
    (Path(st["land_path"]) / "hand-written.txt").unlink()
    landed_sha = st["landed_sha"]
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    final = lst.load(iso["plan_dir"])
    assert final["landed_sha"] == landed_sha                    # the merge, not re-pushed
    added = git(["diff", "--name-only", f"{landed_sha}..origin/main"],
                fx["root"], check=True)[1].splitlines()
    assert all(p.startswith("_plans/plan-a/") for p in added), added
    assert final["state"] == "landed" and not Path(final["land_path"]).exists()


def test_a_plan_branch_that_moves_under_a_recorded_ack_invalidates_it(fx):
    """§5.1d read from GIT. The recorded plan_head describes a candidate that an
    out-of-band ref move can retire between the sync and the push."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    approved = lst.load(iso["plan_dir"])["ack"]["plan_head"]
    before = origin_main(fx)
    moved = work(iso["tree"], "src/sneaky.py", "SNEAKY = 1\n", "an out-of-band commit")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review")
    assert origin_main(fx) == before                           # nothing was pushed
    assert lst.load(iso["plan_dir"])["ack"] is None
    assert lst.load(iso["plan_dir"])["candidate"]["plan_head"] == moved != approved


# ---------------------------------------------------------------- §4.3 the brief
def test_the_catch_up_brief_resolves_the_worktree_that_actually_owns_main(fx):
    root = fx["root"]
    assert lb.owner_of_default(git, root, "main") is not None
    # KNOWN POSITIVE for the detached case: with nobody holding `main`, §4.3's
    # `update-ref` recipe is the safe one and the brief must switch to it.
    git(["checkout", "-q", "--detach"], root, check=True)
    assert lb.owner_of_default(git, root, "main") is None
    info = lb.catch_up(git, root, "main", "origin", "plan-a")
    assert info["state"] == "detached-everywhere"
    assert "update-ref refs/heads/main" in info["commands"][0]
