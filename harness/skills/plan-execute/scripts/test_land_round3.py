"""LND-01 round 3 — regression tests for the seven s08 attempt-3 findings.

One per finding, each with a KNOWN POSITIVE beside its refusal, and each
neutered once in ``_evidence_land_rework.NEUTERS`` (the fix reverted on a COPY
of the tree, this file re-run there, the assertion required to fail):

  r3f1  `already_landed` must not require the land worktree DIRECTORY — a
        resume after the operator removes a preserved land worktree re-walked
        the whole protocol on a plan already on origin/main.
  r3f2  a TIMED-OUT argv gate is INDETERMINATE, never a fail (returncode None,
        `timed_out` True — the module docstring's own motivating case).
  r3f3  every option in the land decision brief is a command that RUNS —
        `run.py retire-plan` was not a registered subcommand.
  r3f4  the `by-name-dispatch-park` evidence record describes the probe it
        actually ran: the premature ack fires BEFORE any land, and `ok`
        asserts its refusal.
  r3f5  an upstream-only repo still fetches and pushes — "not origin" is not
        "no remote"; and >1 remotes with no origin PARKS rather than guessing.
  r3f6  `land_state.range_files` fails CLOSED (None on git error, like its
        sibling `gate_files.range_files`), and §5.1a's caller parks on it.
  r3f7  `land_brief.review` carries no dead `catch_up_info` argument, and
        `step_ack` no longer computes one to discard.

    pytest plan-execute/scripts/test_land_round3.py -q
"""

import inspect
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_brief as lb  # noqa: E402
import land_state as lst  # noqa: E402
import land_steps  # noqa: E402
import ship_state_io as ssio  # noqa: E402
from _land_fixture import (build_repo, isolate, local_main,  # noqa: E402
                           origin_main, run_cli, work)
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _gates(fx, spec):
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(spec))


def _land_with_ack(iso):
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    return run_cli("land", iso["plan_dir"])


# --------------------------------------------------------------------- r3f1
def test_a_landed_plan_resumes_to_landed_after_its_worktree_is_removed_by_hand(fx):
    """The push HAPPENED; only the teardown parked. The operator then removes
    the preserved land worktree by hand — which removes nothing git needs to
    answer "is the merge on the target?". MEASURED before the fix: run 1 parked
    `land-worktree-failed`, runs 2-3 parked `gate-empty-surface` forever, and
    `state` never became `landed` for a plan already on origin/main.
    """
    _gates(fx, {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 120,
                                "argv": ["/bin/sh", "-c", "echo ran > gate.log; exit 0"]}})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], ".gitignore", "__pycache__/\n*.log\n")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _land_with_ack(iso)
    # The gate's own git-ignored artefact makes the final teardown refuse: the
    # land SUCCEEDED (pushed, record published) and parked only on cleanup.
    assert (rc, out.get("kind")) == (1, "land-worktree-preserved"), out
    st = lst.load(iso["plan_dir"])
    assert st["landed_sha"] and st["final_record_sha"]
    after_push = origin_main(fx)
    assert after_push != local_main(fx)                 # the push really happened

    shutil.rmtree(st["land_path"])                       # the operator's hand

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed"), out
    assert lst.load(iso["plan_dir"])["state"] == "landed"
    assert origin_main(fx) == after_push                 # resume pushed NOTHING new
    # KNOWN POSITIVE — and it stays landed: the next run short-circuits.
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed"), out


# --------------------------------------------------------------------- r3f2
def test_a_timed_out_gate_parks_indeterminate_not_failed(fx):
    """`timeout: 1` against `sleep 5`: `returncode` None, `timed_out` True.
    Scored `fail` before the fix — `findings_count: 1` for a review that never
    answered, and no timeout could reach the `gate-indeterminate` park the
    module docstring and the brief both describe as THE motivating case.
    """
    slow = fx["root"] / ".SLOW-GATE"
    slow.touch()
    _gates(fx, {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 1,
                                "argv": ["/bin/sh", "-c",
                                         f"[ -f '{slow}' ] && sleep 5; exit 0"]}})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "gate-indeterminate"), out
    g = lst.load(iso["plan_dir"])["gates"][0]
    assert (g["outcome"], g["findings_count"], g["returncode"]) == ("skipped", 0, None), g
    assert origin_main(fx) == local_main(fx)

    # KNOWN POSITIVE — the reviewer answers in time and the same gate passes.
    slow.unlink()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out


def test_argv_outcome_classifies_timeout_and_error_as_indeterminate():
    """The shared classifier (`verify._run_gate` inherits the same fix)."""
    ao = ssio.argv_outcome
    assert ao({"returncode": None, "timed_out": True, "error": "timeout"}) == "indeterminate"
    assert ao({"returncode": None, "timed_out": False, "error": "exec-error"}) == "indeterminate"
    assert ao({"returncode": 2}, indeterminate_exit=2) == "indeterminate"
    assert ao({"returncode": 2}) == "fail"               # a real answer still fails
    assert ao({"returncode": 0}) == "pass"


# --------------------------------------------------------------------- r3f3
def test_the_abandon_option_in_the_review_brief_actually_runs(fx):
    """The one human approval surface must not offer a command argparse exits 2
    on. The abandon command is EXTRACTED from the brief and EXECUTED verbatim.
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    brief = out["brief"]
    assert "retire-plan" not in brief, "the brief names a subcommand that does not exist"
    line = next(ln for ln in brief.splitlines() if ln.lstrip().startswith("abandon"))
    cmd = shlex.split(line.split("->", 1)[1].strip())
    land_path = Path(lst.load(iso["plan_dir"])["land_path"])
    assert cmd[:1] == ["git"] and str(land_path) in cmd, cmd

    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert not land_path.is_dir()
    # KNOWN POSITIVE — abandon discarded ONLY the candidate: the plan branch and
    # its worktree survive, and the next land simply rebuilds.
    assert git(["rev-parse", "--verify", iso["branch"]], fx["root"])[0] == 0
    assert iso["tree"].is_dir()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out


# --------------------------------------------------------------------- r3f4
def test_proof_checkpoint_runs_the_premature_ack_before_any_land_and_checks_it(tmp_path):
    """The shipped evidence record must describe the probe that ran: the
    premature ack fires while NO candidate exists, is refused (exit 1, action
    `error`), `positive['ok']` asserts that refusal, and the negative control's
    own ack is the accepted first ack of a real candidate.
    """
    import _evidence_land_proofs as ep
    rec = ep.proof_checkpoint(tmp_path)
    pa = rec["positive"]["premature_land_ack"]
    assert (pa["exit_code"], pa["action"]) == (1, "error"), pa
    assert "BEFORE any land" in pa["command"], pa
    neg = rec["negative_control"]
    assert (neg["ack_exit_code"], neg["ack_action"]) == (0, "land-acked"), neg
    assert rec["holds"] is True, rec


# --------------------------------------------------------------------- r3f5
def test_an_upstream_only_repo_still_pushes_the_land(fx):
    """`origin` is a convention, not a fact about the repo. Before the fix an
    upstream-only repo never fetched, pushed NOTHING, wrote only
    `refs/plan-lands/<slug>` and exited 0 claiming "this repo has no remote".
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    git(["remote", "rename", "origin", "upstream"], fx["root"], check=True)
    before = origin_main(fx)

    rc, out = _land_with_ack(iso)
    assert (rc, out["action"]) == (0, "landed"), out
    assert out["landed_on"] == "upstream/main", out
    assert origin_main(fx) != before                     # the push LEFT the machine
    assert "no remote" not in (out.get("brief") or "")


def test_two_remotes_and_no_origin_parks_rather_than_guessing(fx):
    """>1 remotes and none named `origin`: guessing a push target is worse than
    refusing one, and "no remote" would be a lie. Park, name the fix."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    git(["remote", "rename", "origin", "upstream"], fx["root"], check=True)
    git(["remote", "add", "mirror", str(fx["origin"])], fx["root"], check=True)
    before = origin_main(fx)

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "remote-ambiguous"), out
    assert origin_main(fx) == before
    # KNOWN POSITIVE — the brief's own command clears it.
    git(["remote", "rename", "upstream", "origin"], fx["root"], check=True)
    git(["remote", "remove", "mirror"], fx["root"], check=True)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out


# --------------------------------------------------------------------- r3f6
def test_range_files_fails_closed_and_the_pathspec_check_parks_on_it(fx):
    """"git errored" and "no stray paths" must never be the same answer: the
    §5.1a refusal reads this list, and `[]` on failure fails OPEN — the
    candidate walks on to the human ack past a check that never ran.
    """
    assert lst.range_files(fx["root"], "0" * 40) is None          # git failed
    assert lst.range_files(fx["root"], "HEAD") == []              # a real empty range

    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    plan_dir = str(iso["plan_dir"])
    ctx, st = lst.context(plan_dir), lst.load(iso["plan_dir"])
    broken, orig = (lambda *a, **k: None), lst.range_files
    try:
        lst.range_files = broken                        # git cannot answer
        parked = land_steps.step_record(plan_dir, ctx, st)
    finally:
        lst.range_files = orig
    assert parked and parked["kind"] == "record-range-unreadable", parked
    # KNOWN POSITIVE — with git answering, the same entry continues (None).
    assert land_steps.step_record(plan_dir, ctx, lst.load(iso["plan_dir"])) is None


# --------------------------------------------------------------------- r3f7
def test_the_review_brief_takes_no_dead_catch_up_argument():
    """Same dead-declaration class as the fixed OWNER_QUERY finding: `review`
    accepted `catch_up_info` and never read it, while `step_ack` paid three git
    calls per awaits-review park to compute it and discard it."""
    assert "catch_up_info" not in inspect.signature(lb.review).parameters
    import land_push
    assert Path(land_steps.__file__).read_text().count("lb.catch_up(") == 0, \
        "step_ack must not compute a catch_up nothing reads"
    assert Path(land_push.__file__).read_text().count("lb.catch_up(") == 1, \
        "step_finish's live use is the only catch_up computation left"
