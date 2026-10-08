"""LND-01 — the land stage's RESUME paths (contract §4.4, §5.1a, §12.4).

These four are one defect wearing four hats, and the review that found them said
so: *"the defects are all in its resume paths, where a refusal that must persist
does not, or a cache that must expire does not."* Three of the four are the shape
this repo has recorded before — **a state write that happens BEFORE the check
that should gate it** — so the refusal survives exactly one run and then
evaporates.

Each test therefore asserts the SECOND run, not the first. A first run that
refuses proves nothing about a refusal that is supposed to persist, and every one
of these bugs passed a single-run test.

Each is paired with a KNOWN POSITIVE that makes the same assertion report the
other way, and each was NEUTERED once — the fix reverted, the test re-run, the
failure recorded — in ``_evidence/s08/land-proofs.json``. A passing neuter is a
broken probe.

    pytest plan-execute/scripts/test_land_resume.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
from _land_fixture import (build_repo, isolate, local_main,  # noqa: E402
                           origin_main, run_cli, work)
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _gates(fx, spec):
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(spec))


def _runs(counter):
    return len(counter.read_text().split()) if counter.exists() else 0


def _commits_above_merge(iso):
    """How many commits the land worktree carries above the approved merge."""
    st = lst.load(iso["plan_dir"])
    return int(git(["rev-list", "--count", f"{st['merge_sha']}..HEAD"],
                   st["land_path"], check=True)[1])


# ------------------------------------------------------------------ finding 1
def test_an_indeterminate_regate_re_runs_until_it_can_decide(fx):
    """The re-gate cache must not be able to replay "I could not decide".

    MEASURED before the fix: three `run.py land` invocations, ONE gate execution.
    The digest was written before the verdict was computed, so an indeterminate
    exit cached like a pass. The park's own brief says "re-run `run.py land`",
    and re-running could not clear it — only a moved plan head, a moved default
    branch or an edited gate definition could.

    So the undecided condition here lives OUTSIDE the worktree and is cleared
    with no git operation at all: exactly what an operator does when the reviewer
    that timed out comes back. If clearing it required a commit this test would
    pass against the bug.
    """
    counter, undecided = fx["root"] / ".gate-runs", fx["root"] / ".UNDECIDED"
    undecided.touch()
    _gates(fx, {"marker-gate": {
        "kind": "argv", "cwd": ".", "timeout": 120, "indeterminate_exit": 2,
        "argv": ["/bin/sh", "-c",
                 f"echo x >> '{counter}'; [ -f '{undecided}' ] && exit 2; exit 0"]}})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")

    for attempt in (1, 2, 3):
        rc, out = run_cli("land", iso["plan_dir"])
        assert (rc, out.get("kind")) == (1, "gate-indeterminate"), out
        assert _runs(counter) == attempt, \
            f"run {attempt} replayed a cached INDETERMINATE instead of re-running the gate"
    assert origin_main(fx) == local_main(fx)          # and nothing advanced

    # KNOWN POSITIVE — the reviewer answers. Nothing in git moves: no commit, no
    # fetch, no edited gate definition. The land must clear on the operator's
    # own advice, and only a gate that actually re-ran can do that.
    plan_head_before = lst.load(iso["plan_dir"])["plan_head"]
    undecided.unlink()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _runs(counter) == 4
    assert lst.load(iso["plan_dir"])["plan_head"] == plan_head_before


# ------------------------------------------------------------------ finding 2
def test_the_stray_pathspec_refusal_fires_on_every_run_not_just_the_first(fx):
    """§5.1a bounds the approved candidate to the merge plus one commit confined
    to `_plans/<slug>/`. That refusal was ONE-SHOT: `record_sha` was written
    before the stray check and `park()` persisted it, so the next run
    short-circuited at the top of `step_record` and walked to the human with the
    stray path still in the candidate that would be pushed.

    The hook here is the formatter shape the check exists to catch — a
    `pre-commit` that stages a file nobody asked for.
    """
    hook = fx["root"] / ".git" / "hooks" / "pre-commit"
    hook.write_text('#!/bin/sh\ncase "$PWD" in *__land-*) : ;; *) exit 0 ;; esac\n'
                    'echo stray > stray.txt\ngit add -- stray.txt\nexit 0\n')
    hook.chmod(0o755)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")

    for attempt in (1, 2, 3):
        rc, out = run_cli("land", iso["plan_dir"])
        assert (rc, out.get("kind")) == (1, "record-outside-pathspec"), \
            f"run {attempt} let the stray path through: {out}"
        assert out["stray"] == ["stray.txt"], out
        # §5.1a's OWN bound — the merge PLUS AT MOST ONE commit. A retry that
        # stacks a second refused record commit breaks the rule from inside the
        # check that enforces it: `park()` rewrites LAND_NOTICE.txt in the plan
        # dir, so `record_plan` finds a difference and commits again every time.
        assert _commits_above_merge(iso) <= 1, f"run {attempt} stacked record commits"
    assert origin_main(fx) == local_main(fx)
    assert not lst.load(iso["plan_dir"]).get("record_sha"), \
        "record_sha was persisted for a record commit the check refused"

    # KNOWN POSITIVE — the hook goes, and NOTHING ELSE. No commit, no moved plan
    # head, no rebuilt worktree: exactly what the brief tells the operator to do.
    # A refusal that clears only when the candidate changes is a deadlock — the
    # refused record commit sits inside `merge_sha..HEAD` forever unless the land
    # worktree is reset back to the merge.
    plan_head_before = lst.load(iso["plan_dir"])["plan_head"]
    hook.unlink()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    st = lst.load(iso["plan_dir"])
    assert st["plan_head"] == plan_head_before
    recorded = lst.range_files(st["land_path"], st["merge_sha"], "HEAD")
    assert st["record_sha"] and recorded
    assert all(p.startswith("_plans/plan-a/") for p in recorded), recorded
    assert _commits_above_merge(iso) == 1


# ------------------------------------------------------------------ finding 3
def test_a_stale_land_worktree_holding_content_is_never_force_removed(fx):
    """§12.4 — `teardown_worktree`'s dirty-content refusal IS the safety check.

    A candidate that moved makes the land worktree stale and it has to be
    rebuilt; the guard above the teardown covered a conflict and a half-finished
    merge, and `force=True` walked past everything else. MEASURED: untracked
    notes and an edited tracked file in a parked land worktree were destroyed on
    the next `land`, and the output said nothing at all.
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    land_path = Path(lst.load(iso["plan_dir"])["land_path"])
    (land_path / "notes.txt").write_text("mine\n")
    (land_path / "src" / "a.py").write_text("A = 99  # hand edit\n")
    work(iso["tree"], "src/late.py", "LATE = 1\n")        # the candidate moves

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "land-worktree-dirty"), out
    assert (land_path / "notes.txt").read_text() == "mine\n"
    assert "99" in (land_path / "src" / "a.py").read_text()
    assert land_path.is_dir()

    # KNOWN POSITIVE — with the content dealt with, the SAME stale worktree is
    # rebuilt and the land proceeds. The refusal is about the content, not about
    # the rebuild.
    (land_path / "notes.txt").unlink()
    git(["checkout", "--", "src/a.py"], land_path, check=True)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert (land_path / "src" / "late.py").exists()


# ------------------------------------------------------------------ finding 4
def test_a_passing_argv_gate_is_not_re_run_by_the_skill_gate_round_trip(fx):
    """The `invoke-skill` return discarded the `results` accumulated ahead of it,
    and `land-record` popped the digest, so every argv gate ran again after the
    round trip. MEASURED: one argv gate, two executions, one unchanged candidate
    — with the shipped 900 s llm-review gates that is a whole extra review, and
    the discarded runs are non-deterministic.
    """
    counter = fx["root"] / ".gate-runs"
    _gates(fx, {"a-argv-gate": {"kind": "argv", "cwd": ".", "timeout": 120,
                                "argv": ["/bin/sh", "-c", f"echo x >> '{counter}'; exit 0"]},
                "z-skill-gate": {"kind": "skill", "skill": "test-orchestrate"}})
    iso = isolate(fx, "plan-a")
    (iso["plan_dir"] / "manifest.json").write_text(json.dumps(
        {"plan_schema_version": 7,
         "sessions": [{"id": "s01", "verify": {"gates": ["a-argv-gate", "z-skill-gate"]}}]}))
    work(iso["tree"], "src/f.py", "F = 1\n")

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["gate"]) == (1, "invoke-skill", "z-skill-gate"), out
    assert _runs(counter) == 1
    rc, rec = run_cli("land-record", iso["plan_dir"], "--gate", "z-skill-gate",
                      "--status", "passed")
    assert (rc, rec["action"]) == (0, "land-gate-recorded"), rec
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _runs(counter) == 1, \
        "the argv gate re-ran for a candidate that did not change"
    assert [g["outcome"] for g in lst.load(iso["plan_dir"])["gates"]] == ["pass", "pass"]

    # KNOWN POSITIVE — a DIFFERENT candidate re-runs it. The cache is keyed, not
    # unconditional, and this is what proves the counter can still grow.
    work(iso["tree"], "src/late.py", "LATE = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "invoke-skill"), out
    assert _runs(counter) == 2


def test_a_git_ignored_gate_artefact_parks_with_a_command_that_can_see_it(fx):
    """§12.4's refusal reads `git status --porcelain --ignored`, so the content
    that arms it is routinely git-IGNORED — a gate's own artefact, written into
    the land worktree because that is where the re-gate runs.

    The brief used to tell the operator to run plain `git status --porcelain`.
    MEASURED: that prints NOTHING, so they re-run `land-resume` and get the
    identical park, with the offending file named nowhere except a raw Python
    dict interpolated into the text. A recovery command that cannot show the
    problem is not a recovery command.
    """
    _gates(fx, {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 120,
                                "argv": ["/bin/sh", "-c", "echo ran > gate.log; exit 0"]}})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], ".gitignore", "__pycache__/\n*.log\n")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    land_path = Path(lst.load(iso["plan_dir"])["land_path"])
    assert (land_path / "gate.log").exists()                    # the gate ran here
    assert git(["status", "--porcelain"], land_path)[1] == ""   # ...and git is silent
    work(iso["tree"], "src/late.py", "LATE = 1\n")             # the candidate moves

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "land-worktree-dirty"), out
    # the RUNNABLE command, not a mention of the flag in prose: the whole
    # defect was an operator copy-pasting a command that shows nothing.
    assert "git status --porcelain --ignored=matching" in out["brief"], out["brief"]
    assert "gate.log" in out["brief"], out["brief"]
    assert (land_path / "gate.log").exists()

    # KNOWN POSITIVE — the artefact goes and the same stale worktree rebuilds.
    (land_path / "gate.log").unlink()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
