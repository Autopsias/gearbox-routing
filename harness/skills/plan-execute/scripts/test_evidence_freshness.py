"""LND-03 — the evidence check proves THIS run wrote the artifact.

A member worktree is a checkout of the group's PINNED BASE, and
``worktree.resolve_evidence_path`` searches it first, so an artifact an earlier
run committed is sitting at the declared path before the session starts. Every
test here runs against a real git repository and a real worktree.

THE ORDERING IS THE POINT. ``run._commit_member_branch`` calls
``worktree.commit_member`` at ``apply`` time — BEFORE the verify loop — so by the
time the evidence check runs the worktree is clean. A previous attempt at this
fix compared worktree-vs-HEAD, passed its own fixture (which never committed),
and rejected every genuine artifact in production; it was reverted. So the
genuine-evidence cases below commit the member FIRST and only then ask.

Both directions are planted on purpose: a stale artifact must REFUSE (a check
that cannot fail is worse than none) and genuine evidence must GRANT (a check
that cannot pass strands every session).

    pytest skills/plan-execute/scripts/test_evidence_freshness.py -q
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import closeout_pipeline as cp  # noqa: E402
import evidence_proof as evp  # noqa: E402
import plan_scope as pscope  # noqa: E402
import review_context as rc  # noqa: E402
import verify as vfy  # noqa: E402
import worktree as wt  # noqa: E402

DONE = {"session": "s01", "result": "DONE", "items_completed": ["i1"],
        "items_blocked": [], "notes": {"i1": "done"}, "dispatch_next": True,
        "human_checkpoint_reason": None}

# Committed AT THE BASE, so it is in every member's checkout from the first
# instant — the artifact an "earlier run" left behind.
STALE = "docs/proof.txt"


def _manifest():
    return {
        "plan_schema_version": 4,
        "sessions": [{"id": "s01", "items": ["i1"],
                      "dispatch": {"parallel_group": "g", "isolation": "worktree",
                                   "depends_on": []}}],
        "items": [{"id": "i1", "touches": "docs"}],
    }


@pytest.fixture
def member(tmp_path, monkeypatch):
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "proof.txt").write_text("written by an EARLIER run\n")
    (root / ".gitignore").write_text("*.log\n")
    wt.git(["init", "-q", "-b", "main"], root, check=True)
    wt.git(["config", "user.email", "t@example.com"], root, check=True)
    wt.git(["config", "user.name", "T"], root, check=True)
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "base"], root, check=True)
    plan_dir = root / "_plans" / "fixture"
    plan_dir.mkdir(parents=True)
    m = _manifest()
    paths = wt.prepare_members(plan_dir, m, ["s01"], project_root=root)
    return {"root": root, "plan_dir": str(plan_dir), "manifest": m,
            "tree": Path(paths["s01"])}


def _commit(member):
    """What `apply` does before verify ever runs."""
    return wt.commit_member(member["plan_dir"], member["manifest"], "s01", "member work")


def _ask(member, declared):
    cp.persist(member["plan_dir"], "s01", {**DONE, "evidence": [declared]})
    return vfy._check_evidence(member["plan_dir"], "s01")


# ------------------------------------------------------- the planted failure
def test_an_artifact_from_the_base_is_refused_even_though_it_exists(member):
    """NEUTER, direction one. The session writes NOTHING; the file is in its
    worktree because the checkout was made at a base that already had it. The
    old existence-only check granted this."""
    assert (member["tree"] / STALE).is_file(), "PLANT FAILED: no frozen copy to catch"
    ok, why = _ask(member, STALE)
    assert not ok
    assert "unchanged since" in why and STALE in why


def test_the_refusal_survives_the_orchestrator_commit(member):
    """The stale artifact is still stale after `commit_member` runs over an
    unrelated edit — the commit must not launder it into proof."""
    (member["tree"] / "docs" / "other.txt").write_text("unrelated work\n")
    assert _commit(member)
    ok, why = _ask(member, STALE)
    assert not ok and "unchanged since" in why


# ------------------------------------------------------ the genuine evidence
def test_genuine_evidence_still_passes_after_commit_member(member):
    """NEUTER, direction two — AND the exact ordering that defeated the previous
    attempt: the artifact is written by the session, the orchestrator commits the
    worktree, and only THEN is the evidence check asked. Comparing against HEAD
    here answers "clean" and rejects real work."""
    (member["tree"] / STALE).write_text("grep -c [engaged] => 7\n")
    assert _commit(member)
    assert not wt.git(["status", "--porcelain"], member["tree"])[1], \
        "PLANT FAILED: the worktree is still dirty, so this never tests the post-commit state"
    ok, why = _ask(member, STALE)
    assert ok, why


def test_evidence_written_after_the_commit_passes(member):
    """Untracked in a pinned checkout means written during the session."""
    (member["tree"] / "docs" / "late.txt").write_text("late proof\n")
    _commit(member)
    (member["tree"] / "docs" / "later.txt").write_text("written after the commit\n")
    ok, why = _ask(member, "docs/later.txt")
    assert ok, why


def test_gitignored_evidence_passes(member):
    """A .gitignore'd artifact is invisible to `diff` and to `--exclude-standard`;
    in a tracked-files-only checkout it can only have been produced here."""
    (member["tree"] / "docs" / "run.log").write_text("engaged=3\n")
    ok, why = _ask(member, "docs/run.log")
    assert ok, why


# --------------------------------------------------------------- fail CLOSED
def test_a_broken_git_dir_refuses_rather_than_grants(member):
    """The reverted attempt's line was `rc != 0 or bool(out.strip())` — a git
    error GRANTED the check. Break the worktree's gitdir pointer so git really
    fails, and confirm the answer is a refusal."""
    (member["tree"] / STALE).write_text("looks like fresh proof\n")
    (member["tree"] / ".git").write_text("gitdir: /nonexistent/broken\n")
    assert wt.git(["ls-files", "--others"], member["tree"])[0] != 0, \
        "PLANT FAILED: git still works, so this proves nothing about the error path"
    ok, why = _ask(member, STALE)
    assert not ok and "INDETERMINATE" in why


def test_a_missing_base_sha_refuses_instead_of_falling_back_to_head(member):
    """The abort condition, mechanised: with no pinned base recoverable the check
    must refuse, never substitute HEAD."""
    (member["tree"] / STALE).write_text("fresh, but unprovable\n")
    _commit(member)
    state_file = next((Path(member["plan_dir"]) / "_worktrees").glob("*.json"))
    text = state_file.read_text().replace('"base_ref"', '"was_base_ref"')
    state_file.write_text(text)
    ok, why = _ask(member, STALE)
    assert not ok and "INDETERMINATE" in why and "HEAD" in why


# ----------------------------------------------------------------- no regress
def test_a_path_outside_every_checkout_with_no_run_boundary_is_unaffected(member, tmp_path):
    """Outside every checkout AND no `dispatch_started` anywhere in the plan:
    there is no run for the artifact to be proof OF, so the pre-existing
    existence contract stands. The ordinary-plan tests below cover the case this
    one used to be mistaken for — an artifact the plan CAN date."""
    outside = tmp_path / "proof.txt"
    outside.write_text("proof\n")
    assert evp.stale_reason(member["plan_dir"], "s01", outside) is None
    ok, why = _ask(member, str(outside))
    assert ok, why


def test_absent_and_empty_artifacts_still_fail_with_their_own_reasons(member, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.touch()
    ok, why = _ask(member, str(empty))
    assert not ok and "empty file" in why
    ok, why = _ask(member, str(tmp_path / "nope.txt"))
    assert not ok and "does not exist" in why


# ===========================================================================
# THE ORDINARY PLAN — no isolation, shared checkout. Every session in the plan
# that shipped this check has this shape, and the first cut of the module was a
# NO-OP for all of them: `_evidence/...` resolves in the outer plan directory,
# which lives in no worktree, so freshness was never asked. These tests fail
# against that version.
# ===========================================================================
import json  # noqa: E402
import os  # noqa: E402

# Fixed dates so the test never races the clock: the repo's history is planted in
# 2020 and the session is dispatched in mid-2020, which puts "now" (any file the
# test writes) unambiguously after the dispatch.
BASE_DATE = "2020-01-01T00:00:00+00:00"
DISPATCH_AT = "2020-06-01T00:00:00+00:00"
BEFORE_DISPATCH = 1577923200.0            # 2020-01-02T00:00:00Z, as an mtime


@pytest.fixture
def ordinary(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    plan_dir = root / "_plans" / "fixture"
    (plan_dir / "_evidence" / "s01").mkdir(parents=True)
    # Committed BEFORE the session was dispatched: the artifact an earlier
    # session left behind, sitting at exactly the path s01 declares.
    (plan_dir / "_evidence" / "s01" / "from-last-week.json").write_text('{"old": true}\n')
    wt.git(["init", "-q", "-b", "main"], root, check=True)
    wt.git(["config", "user.email", "t@example.com"], root, check=True)
    wt.git(["config", "user.name", "T"], root, check=True)
    wt.git(["add", "-A"], root, check=True)
    monkeypatch.setenv("GIT_AUTHOR_DATE", BASE_DATE)
    monkeypatch.setenv("GIT_COMMITTER_DATE", BASE_DATE)
    wt.git(["commit", "-q", "-m", "everything an earlier session left"], root, check=True)
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    # s02 is dispatched too and has NO cached base — the cache-MISS path, which is
    # the only one that can write. Without it the write probe is vacuous: s01's
    # entry already exists and `record_base` is first-write-wins.
    (plan_dir / "run.ndjson").write_text("".join(json.dumps(
        {"ts": DISPATCH_AT, "event": "dispatch_started", "session_ids": [sid]}) + "\n"
        for sid in ("s01", "s02")))
    return {"root": root, "plan_dir": str(plan_dir)}


def _ask_ordinary(ordinary, declared):
    cp.persist(ordinary["plan_dir"], "s01", {**DONE, "evidence": [declared]})
    return vfy._check_evidence(ordinary["plan_dir"], "s01")


def test_ordinary_plan_evidence_from_an_earlier_session_is_refused(ordinary):
    """THE NO-OP CATCHER. No worktree anywhere, and the declared artifact is
    committed at the commit this session was dispatched from. The shipped-then-
    reviewed version of this module returned None here for every path in this
    plan's manifest."""
    declared = "_evidence/s01/from-last-week.json"
    assert (Path(ordinary["plan_dir"]) / declared).is_file(), \
        "PLANT FAILED: no earlier-session artifact to catch"
    assert not pscope.plan_worktree(ordinary["plan_dir"]), \
        "PLANT FAILED: this fixture must be the NON-isolated shape"
    ok, why = _ask_ordinary(ordinary, declared)
    assert not ok, "an artifact committed before this session started is not proof of it"
    assert "unchanged since" in why and declared in why


def test_ordinary_plan_evidence_written_by_the_session_passes(ordinary):
    """Direction two: a check that cannot pass strands every session."""
    (Path(ordinary["plan_dir"]) / "_evidence" / "s01" / "run.json").write_text('{"engaged": 3}\n')
    ok, why = _ask_ordinary(ordinary, "_evidence/s01/run.json")
    assert ok, why


def test_ordinary_plan_untracked_artifact_predating_dispatch_is_refused(ordinary):
    """Untracked in a SHARED checkout means only "not committed" — it can be any
    earlier session's leftover, so it is dated against the dispatch."""
    art = Path(ordinary["plan_dir"]) / "_evidence" / "s01" / "leftover.json"
    art.write_text('{"left": "by someone else"}\n')
    os.utime(art, (BEFORE_DISPATCH, BEFORE_DISPATCH))
    ok, why = _ask_ordinary(ordinary, "_evidence/s01/leftover.json")
    assert not ok
    assert "BEFORE this session was dispatched" in why


def test_a_dispatched_plan_with_no_record_for_this_session_refuses(ordinary, tmp_path):
    """A plan the orchestrator IS running, an artifact it cannot date, and no
    dispatch record for the session: undecidable, so refuse."""
    outside = tmp_path / "unplaceable.txt"
    outside.write_text("proof\n")
    why = evp.stale_reason(ordinary["plan_dir"], "s99", outside)
    assert why and "INDETERMINATE" in why and "s99" in why


def test_named_checks_keep_the_existence_only_contract(ordinary):
    """`verify.checks[].evidence_path` means "this file must show X" and may name
    a document the session only READS. Freshness there would fail unfixably and
    burn a rework attempt. The check here points at an artifact that IS stale, so
    the second assertion proves the split is real rather than vacuous: the same
    path in the closeout's `evidence` array is refused."""
    plan_dir = Path(ordinary["plan_dir"])
    (plan_dir / "manifest.json").write_text(json.dumps({
        "plan_schema_version": 4,
        "sessions": [{"id": "s01", "items": ["i1"],
                      "verify": {"checks": [{"name": "doc", "assert": "documents the rule",
                                             "evidence_path": "_evidence/s01/from-last-week.json"}]},
                      "dispatch": {"parallel_group": None, "depends_on": []}}],
        "items": [{"id": "i1"}]}))
    ok, why = vfy._check_named_checks(str(plan_dir), "s01")
    assert ok, why
    assert evp.artifact_problem(str(plan_dir), "s01",
                                "_evidence/s01/from-last-week.json"), \
        "PLANT FAILED: that path is fresh, so passing the named check proves nothing"


def test_an_unrelated_unreadable_checkout_does_not_blank_a_decidable_artifact(
        ordinary, monkeypatch):
    """A checkout that fails to resolve and does NOT contain the artifact must
    not turn a decidable answer into INDETERMINATE."""
    real = evp._checkouts
    monkeypatch.setattr(evp, "_checkouts",
                        lambda pd, sid: [("/no/such/tree", True)] + real(pd, sid))
    monkeypatch.setattr(evp, "_resolve",
                        lambda p: None if str(p) == "/no/such/tree" else Path(p).resolve())
    why = evp.artifact_problem(ordinary["plan_dir"], "s01",
                               "_evidence/s01/from-last-week.json")
    assert why and "unchanged since" in why, why


# ===========================================================================
# THE BASE ITSELF CAN BE WRONG. Both cases below were reproduced by review
# against real git fixtures before they were fixed.
# ===========================================================================
def _orphan_commit(root):
    """A real commit on NO branch, with an EMPTY tree and no parent — so it is
    not an ancestor of HEAD and every tracked path reads as "added" against it.
    Built with `commit-tree` rather than `checkout --orphan` so the fixture's
    working tree is never disturbed."""
    empty = wt.git(["hash-object", "-w", "-t", "tree", os.devnull], root, check=True)[1].strip()
    return wt.git(["commit-tree", empty, "-m", "unrelated history"], root, check=True)[1].strip()


def test_a_cached_base_off_this_checkouts_history_cannot_reach_the_diff(ordinary):
    """`git diff <base>` against a commit HEAD cannot reach is a SYMMETRIC diff:
    every file that merely DIFFERS between two unrelated histories reads as this
    session's work. An earlier cut of this module took the base from the review
    gate's cached `session_base`, so a drifted cache granted an artifact the
    session never touched. The base is now DERIVED IN THIS TREE, so the cache
    cannot reach the answer at all — asserted here rather than assumed, because
    the guard that used to catch it is gone."""
    import run_state_io as rsi
    root, plan_dir = Path(ordinary["root"]), ordinary["plan_dir"]
    orphan = _orphan_commit(root)
    rsi.save_state(plan_dir, {**rsi.load_state(plan_dir),
                              "session_base": {"s01": {"base_ref": orphan}}})

    declared = "_evidence/s01/from-last-week.json"
    assert not rc.usable_base(orphan, str(root)), "PLANT FAILED: the base IS an ancestor"
    assert wt.git(["diff", "--name-only", orphan, "--",
                   str((Path(plan_dir) / declared).relative_to(root))], root)[1].strip(), \
        "PLANT FAILED: the symmetric diff is empty, so a cache-reading check would " \
        "refuse anyway and this proves nothing"

    ok, why = _ask_ordinary(ordinary, declared)
    assert not ok, "a poisoned cache must never grant"
    assert "unchanged since" in why, why
    used, _ = evp._base(plan_dir, "s01", str(root), False)
    assert used != orphan, "the unusable cached base must not be the one diffed against"
    assert used == rc.derive_base(plan_dir, "s01", str(root)), "it must fall through to derivation"
    assert rc.usable_base(used, str(root)), \
        "the derived base must be an ancestor of this tree's HEAD BY CONSTRUCTION"


def test_a_usable_recorded_base_is_the_one_used(ordinary):
    """The reviewer's window and the freshness window must be the SAME window
    wherever the tree can reach it. Re-deriving today is not always the same
    answer: after a merge, `rev-list --before` walks a history that now
    interleaves the other branch, and on this plan three of ten sessions derived
    a base neither an ancestor nor a descendant of the recorded one."""
    import run_state_io as rsi
    root, plan_dir = str(ordinary["root"]), ordinary["plan_dir"]
    (Path(root) / "src.py").write_text("x = 1\n")
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "a later commit the recorded base can point at"],
           root, check=True)
    recorded = wt.git(["rev-parse", "HEAD"], root, check=True)[1].strip()
    rsi.save_state(plan_dir, {**rsi.load_state(plan_dir),
                              "session_base": {"s01": {"base_ref": recorded}}})
    assert recorded != rc.derive_base(plan_dir, "s01", root), \
        "PLANT FAILED: recorded and derived agree, so this cannot tell them apart"
    assert rc.usable_base(recorded, root), "PLANT FAILED: the recorded base is unreachable here"
    assert evp._base(plan_dir, "s01", root, False)[0] == recorded


def test_a_member_base_off_the_worktrees_history_refuses_instead_of_granting(member):
    """The one base this module does NOT derive from the tree's own history: the
    group's pinned sha, read from a state file. Ancestry there is asserted, and
    this proves that assertion can still fail — poison the state file with a
    commit the worktree cannot reach and the symmetric diff must be refused, not
    read as authorship."""
    root, tree = member["root"], member["tree"]
    orphan = _orphan_commit(root)
    state_file = next((Path(member["plan_dir"]) / "_worktrees").glob("*.json"))
    state = json.loads(state_file.read_text())
    state["members"]["s01"]["base_ref"] = orphan
    state_file.write_text(json.dumps(state))

    assert not rc.usable_base(orphan, str(tree)), "PLANT FAILED: the base IS an ancestor"
    assert wt.git(["diff", "--name-only", orphan, "--", STALE], tree)[1].strip(), \
        "PLANT FAILED: the symmetric diff is empty, so an ungated check would refuse anyway"

    ok, why = _ask(member, STALE)
    assert not ok, "a base off this checkout's history must never grant"
    assert "INDETERMINATE" in why and "not an ancestor" in why


# ===========================================================================
# PLAN ISOLATION — the configuration this module is hardened for, and the one
# the suite did not exercise: the review gate caches a `session_base` DERIVED IN
# THE PLAN WORKTREE, while `_evidence/**` resolves in the OUTER checkout (§8.a).
# Routing the base through `review_context.get_base` there fell back to the
# plan's PINNED base — an ancestor of the outer HEAD by construction, so the
# ancestry guard could not fire — and every artifact committed to the outer
# checkout since the branch was cut passed as this session's work.
# ===========================================================================
PIN_DATE = "2020-01-01T00:00:00+00:00"
LANDED_AT = "2020-02-01T00:00:00+00:00"        # an earlier session's record commit
BRANCH_AT = "2020-03-01T00:00:00+00:00"        # work already on the plan branch
ISO_SLUG = "iso-fixture-2026-08-20"
LANDED = "_evidence/s01/from-an-earlier-session.json"


def _commit_at(cwd, when, msg):
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        wt.git(["add", "-A"], cwd, check=True)
        wt.git(["commit", "-q", "-m", msg], cwd, check=True)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return wt.git(["rev-parse", "HEAD"], cwd, check=True)[1].strip()


@pytest.fixture
def iso(tmp_path):
    import plan_worktree as pwt
    import run_state_io as rsi
    origin = tmp_path / "origin.git"
    origin.mkdir()
    wt.git(["init", "-q", "--bare", str(origin)], tmp_path, check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    plan_dir = root / "_plans" / ISO_SLUG
    (plan_dir / "_evidence" / "s01").mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html>plan</html>\n")
    (plan_dir / "manifest.json").write_text(json.dumps(
        {"plan_schema_version": 7, "sessions": [], "items": []}))
    for argv in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "T"]):
        wt.git(argv, root, check=True)
    _commit_at(root, PIN_DATE, "the commit the plan branch is cut from")
    wt.git(["remote", "add", "origin", str(origin)], root, check=True)
    wt.git(["push", "-q", "-u", "origin", "main"], root, check=True)

    tree = Path(pwt.ensure_plan_worktree(plan_dir, project_root=root)["path"])
    # An EARLIER session of this plan landed its evidence onto the outer checkout,
    # after the branch was cut. This is the artifact that must not pass as s01's.
    (plan_dir / LANDED).write_text('{"written_by": "s00, last week"}\n')
    landed = _commit_at(root, LANDED_AT, "an earlier session's landed record")
    # ...and the plan branch has moved on, so a base derived there is NOT an
    # ancestor of the outer checkout's HEAD.
    (tree / "src" / "b.py").write_text("B = 2\n")
    _commit_at(tree, BRANCH_AT, "work already on the plan branch")
    # s02 is dispatched too and has NO cached base — the cache-MISS path, which is
    # the only one that can write. Without it the write probe is vacuous: s01's
    # entry already exists and `record_base` is first-write-wins.
    (plan_dir / "run.ndjson").write_text("".join(json.dumps(
        {"ts": DISPATCH_AT, "event": "dispatch_started", "session_ids": [sid]}) + "\n"
        for sid in ("s01", "s02")))

    # SEEDED THE WAY THE GATE SEEDS IT: `review_context.gate_env` calls `get_base`
    # with the gate's cwd — the plan worktree — before verify ever runs.
    cached = rc.get_base(str(plan_dir), "s01", str(tree))
    assert (rsi.load_state(str(plan_dir)).get("session_base") or {}).get("s01"), \
        "PLANT FAILED: the gate's cache was never written, so this fixture tests nothing"
    return {"root": root, "plan_dir": str(plan_dir), "tree": tree,
            "pin": pscope.pinned_base(str(plan_dir)), "cached": cached, "landed": landed}


def _ask_iso(iso, declared):
    cp.persist(iso["plan_dir"], "s01", {**DONE, "evidence": [declared]})
    return vfy._check_evidence(iso["plan_dir"], "s01")


def test_isolated_evidence_from_an_earlier_session_is_refused(iso):
    """THE FINDING, mechanised. Every premise of the defeat is asserted first, so
    a fixture that stops reproducing it fails loudly instead of passing green."""
    root, plan_dir = str(iso["root"]), iso["plan_dir"]
    assert str(pscope.resolve_evidence_path(plan_dir, "s01", LANDED)).startswith(root), \
        "PLANT FAILED: the artifact must resolve in the OUTER checkout"
    assert not rc.usable_base(iso["cached"], root), \
        "PLANT FAILED: the gate's cached base is usable here, so there is no fallback"
    assert rc.get_base(plan_dir, "s01", root) == iso["pin"], \
        "PLANT FAILED: get_base did not fall back to the pin"
    assert rc.usable_base(iso["pin"], root), \
        "PLANT FAILED: the pin is not an ancestor, so the old guard COULD have fired"
    assert wt.git(["diff", "--name-only", iso["pin"], "--",
                   str((Path(plan_dir) / LANDED).relative_to(iso["root"]))], root)[1].strip(), \
        "PLANT FAILED: the diff against the pin is empty, so the old code refused anyway"

    used, _ = evp._base(plan_dir, "s01", root, False)
    assert used != iso["cached"], "the gate's plan-branch base is not usable here"
    assert used != iso["pin"], "and the pin must never become the base freshness diffs against"
    assert used == rc.derive_base(plan_dir, "s01", root)

    ok, why = _ask_iso(iso, LANDED)
    assert not ok, "an artifact an earlier session committed is not proof of this one"
    assert "unchanged since" in why and LANDED in why


def test_the_freshness_check_never_records_a_base(iso):
    """A read-only question must not mutate shared run state. `get_base` cached a
    base DERIVED IN THE EVIDENCE TREE, and first-write-wins made it permanent —
    the review gate then found it unusable in its worktree and re-reviewed every
    earlier session."""
    import run_state_io as rsi
    plan_dir = iso["plan_dir"]
    before = rsi.load_state(plan_dir).get("session_base") or {}
    assert "s02" not in before and rc.derive_base(plan_dir, "s02", str(iso["root"])), \
        "PLANT FAILED: s02 must be a cache MISS the evidence tree can answer, or nothing writes"
    evp.artifact_problem(plan_dir, "s01", LANDED)
    evp.artifact_problem(plan_dir, "s02", LANDED)
    after = rsi.load_state(plan_dir).get("session_base") or {}
    assert after == before, f"the freshness check rewrote the reviewer's base: {before} -> {after}"
    assert rc.usable_base(iso["cached"], str(iso["tree"])), \
        "the gate's own base must still be the one it can diff against in its worktree"


def test_isolated_evidence_the_session_actually_wrote_passes(iso):
    """Direction two, both shapes: a freshly written artifact (untracked in the
    outer checkout) and an edit to a TRACKED one, which is the only shape that
    reaches the derived base at all."""
    fresh = "_evidence/s01/this-session.json"
    (Path(iso["plan_dir"]) / fresh).write_text('{"engaged": 3}\n')
    ok, why = _ask_iso(iso, fresh)
    assert ok, why

    (Path(iso["plan_dir"]) / LANDED).write_text('{"rewritten_by": "s01, now"}\n')
    assert not wt.git(["ls-files", "--others", "--", str(Path(LANDED))],
                      Path(iso["plan_dir"]))[1].strip(), \
        "PLANT FAILED: that artifact is untracked, so it never reaches the base"
    ok, why = _ask_iso(iso, LANDED)
    assert ok, why


def test_a_never_dispatched_plan_grants_inside_a_checkout_too(ordinary):
    """The documented never-dispatched carve-out has to hold WHERE EVIDENCE
    ACTUALLY LIVES. With no dispatch record anywhere there is no run boundary, so
    the pre-existing existence contract stands — and an artifact written seconds
    ago inside the repo was being refused with "base is not recoverable", a check
    no session could possibly pass."""
    plan_dir = Path(ordinary["plan_dir"])
    (plan_dir / "run.ndjson").unlink()
    assert not evp._dispatched_anywhere(str(plan_dir)), "PLANT FAILED: still a run boundary"

    # TRACKED and committed — the branch that still asks for a base, and the only
    # one that reaches the carve-out. An untracked artifact never gets that far.
    tracked = "_evidence/s01/from-last-week.json"
    assert not wt.git(["ls-files", "--others", "--", tracked],
                      Path(ordinary["root"]))[1].strip(), \
        "PLANT FAILED: that artifact is untracked, so it never reaches the base at all"
    assert evp.artifact_problem(str(plan_dir), "s01", tracked) is None, \
        evp.artifact_problem(str(plan_dir), "s01", tracked)
    ok, why = _ask_ordinary(ordinary, tracked)
    assert ok, why

    # The untracked half of the same promise, and the outside-every-checkout half:
    # all three must agree, which is the whole finding.
    (plan_dir / "_evidence" / "s01" / "just-written.txt").write_text("proof\n")
    assert evp.artifact_problem(str(plan_dir), "s01",
                                "_evidence/s01/just-written.txt") is None
    outside = Path(ordinary["root"]).parent / "outside.txt"
    outside.write_text("proof\n")
    assert evp.stale_reason(str(plan_dir), "s01", outside) is None


def test_the_carve_out_stops_at_a_member_worktree(member):
    """The exception to the exception: a member worktree IS a run boundary (it was
    created for this run at a pinned base), so a stale artifact there is still
    refused even with no dispatch record anywhere in the plan."""
    assert not evp._dispatched_anywhere(member["plan_dir"]), \
        "PLANT FAILED: this fixture has dispatch records, so it tests nothing"
    ok, why = _ask(member, STALE)
    assert not ok and "unchanged since" in why
