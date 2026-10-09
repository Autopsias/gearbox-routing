"""ISO-03 — a parallel group NESTED under an isolated plan, and the gate scoping
that follows from it.

Contracts: ``../references/parallel-group-contract.md`` §9 (v3) —
V3-1 nests the member's base, checkout and merge target under the plan; V3-2
redefines every "the shared tree" rule to mean the PLAN WORKTREE — and
``../references/plan-isolation-contract.md`` §1.1a, §10, §12.1.

Real git throughout. Each check is paired with the wrong answer it rejects,
computed in the same fixture: the outer checkout is deliberately made dirty in
every scoping test, so an assertion that "the group saw nothing" cannot pass
because there was nothing to see.

    pytest plan-execute/scripts/test_group_scope.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import gate_files as gf  # noqa: E402
import group_scope as gs  # noqa: E402
import plan_scope as ps  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import verify as vfy  # noqa: E402
import worktree as wt  # noqa: E402
from worktree import git  # noqa: E402

SLUG = "demo-plan"


def _manifest(version):
    return {
        "plan_schema_version": version,
        "sessions": [
            {"id": "m01", "items": ["i1"],
             "dispatch": {"parallel_group": "g", "isolation": "worktree",
                          "depends_on": []}},
            {"id": "i99", "items": [],
             "dispatch": {"integrates_group": "g", "depends_on": ["m01"]}},
            {"id": "s07", "items": [], "dispatch": {"depends_on": []}},
        ],
        "items": [{"id": "i1", "touches": "src/alpha.py"}],
    }


def _repo(tmp_path, version):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text("A = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    plan_dir = root / "_plans" / SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / "manifest.json").write_text(json.dumps(_manifest(version)))
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    return {"root": root, "plan_dir": plan_dir, "manifest": _manifest(version)}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """A schema-7 plan already isolated, with one commit on the plan branch that
    exists NOWHERE else — the marker that tells the plan branch from `main`."""
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    r = _repo(tmp_path, 7)
    state = pwt.ensure_plan_worktree(r["plan_dir"], project_root=r["root"])
    r["tree"] = Path(state["path"])
    (r["tree"] / "src" / "on_plan_branch.py").write_text("ONLY_ON_THE_PLAN_BRANCH = 1\n")
    git(["add", "-A"], r["tree"], check=True)
    git(["commit", "-q", "-m", "feat: plan-branch work"], r["tree"], check=True)
    return r


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """The same shape at schema 4 — below the gate, so nothing may change."""
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    return _repo(tmp_path, 4)


# ------------------------------------------------ V3-1: base, name, location
def test_the_group_bases_on_the_PLAN_BRANCH_and_not_on_the_outer_HEAD(iso):
    """V3-1's `Member base` row. The plan-branch commit exists only in the plan
    worktree, so a group that pinned the outer HEAD gets a base that cannot reach
    it — and every member would then branch off work the plan already did."""
    outer_head = git(["rev-parse", "HEAD"], iso["root"], check=True)[1]
    plan_head = git(["rev-parse", "HEAD"], iso["tree"], check=True)[1]
    assert plan_head != outer_head                       # the fixture really differs

    state = wt.ensure_group(iso["plan_dir"], "g", project_root=iso["root"])
    assert state["base_ref"] == plan_head
    assert state["base_branch"] == pwt.plan_branch(SLUG)
    assert Path(state["merge_root"]) == iso["tree"].resolve()
    assert Path(state["repo_root"]) == iso["root"]       # git admin stays OUTSIDE
    assert state["plan_slug"] == SLUG


def test_the_member_checkout_is_a_SIBLING_of_the_plan_worktree_never_a_child(iso):
    """§1.1a. Nesting one worktree inside another SUCCEEDS quietly and then makes
    the parent unremovable without `--force`, which §12.4 forbids; it also makes
    every file of the child untracked content of the parent, which §10.1 would
    feed to the parent's gates."""
    paths = wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                               project_root=iso["root"])
    member = Path(paths["m01"])
    assert member.name == f"{SLUG}__g__m01"
    assert member.parent == iso["tree"].parent           # a SIBLING directory
    with pytest.raises(ValueError):                      # ...and not inside it
        member.resolve().relative_to(iso["tree"].resolve())
    # The member really is based on the plan branch: the plan-branch-only file
    # is checked out here, and it is not on `main`.
    assert (member / "src" / "on_plan_branch.py").exists()
    assert not (iso["root"] / "src" / "on_plan_branch.py").exists()


def test_the_member_BRANCH_is_flat_because_git_refuses_the_nested_name(iso):
    """THE AMENDED CONTRACT ROW (operator decision), kept as a running
    measurement of BOTH halves.

    V3-1 first asked for `plan/<plan-slug>/<group>/<sid>` while the plan's own
    branch is `plan/<plan-slug>`. Git cannot hold both refs — asserted here
    against real git, so the day that changes this test says so. The amended
    name is flat, and the point of the amendment is that it CREATES: a name that
    merely reads better but git still refuses would be the same defect renamed.
    """
    root = iso["root"]
    assert git(["rev-parse", "--verify", "--quiet",
                f"refs/heads/{pwt.plan_branch(SLUG)}"], root)[0] == 0
    rc, _, err = git(["branch", f"plan/{SLUG}/g/m01"], root)
    assert rc != 0
    assert "cannot create" in err and f"refs/heads/plan/{SLUG}" in err

    flat = wt.member_branch("g", "m01", SLUG)
    assert flat == f"plan/{SLUG}__g__m01"
    assert git(["branch", flat], root)[0] == 0, "the amended name must be creatable"
    assert flat.startswith(f"{wt.BRANCH_PREFIX}/"), "still enumerated by registry_git"

    # ONE derivation behind the checkout directory and the branch — they cannot
    # drift, which is why member_branch delegates to member_dirname.
    assert flat == f"{wt.BRANCH_PREFIX}/{gs.member_dirname('g', 'm01', SLUG)}"


def test_every_caller_resolves_the_member_branch_from_RECORDED_state(iso):
    """`plan_scope.member_branch` is the one resolver run.py, codex_command and
    the dispatch preamble share. It reads the slug the group RECORDED, not live
    isolation, so the name a session is told is the name its branch has."""
    wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                       project_root=iso["root"])
    expected = f"plan/{SLUG}__g__m01"
    assert ps.member_branch(iso["plan_dir"], "g", "m01") == expected
    state = wt.load_state(iso["plan_dir"], "g")
    assert state["members"]["m01"]["branch"] == expected
    assert git(["rev-parse", "--verify", "--quiet", f"refs/heads/{expected}"],
               iso["root"])[0] == 0, "the recorded branch must be the one git holds"

    # A group pinned with NO slug keeps the v2 name even though the plan claims
    # isolation now — the recorded state wins over what is true today.
    state["plan_slug"] = None
    wt._save(iso["plan_dir"], "g", state)
    assert ps.member_branch(iso["plan_dir"], "g", "m01") == "plan/g/m01"


# --------------------------------------- V3-2: "the shared tree" is the plan's
def test_a_neighbour_plans_dirty_file_in_the_OUTER_checkout_is_not_a_stray(iso):
    """The headline of ISO-03. Contract §3 rule 6 measured the wrong tree: a file
    another plan left dirty in the operator's checkout matched a member's declared
    `touches` and REFUSED the merge. Both halves are asserted — the outer edit is
    invisible, and the same edit inside the plan worktree still fires."""
    wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                       project_root=iso["root"])
    (iso["root"] / "src" / "alpha.py").write_text("A = 'a NEIGHBOUR plan wrote this'\n")
    rep = wt.containment_report(iso["plan_dir"], iso["manifest"], "g")
    assert rep["stray"] == [] and rep["shared_tree_dirty"] == []

    # KNOWN POSITIVE — the check can still fail, in the tree it is pointed at.
    (iso["tree"] / "src" / "alpha.py").write_text("A = 'a MEMBER wrote outside'\n")
    rep = wt.containment_report(iso["plan_dir"], iso["manifest"], "g")
    assert [s["session"] for s in rep["stray"]] == ["m01"]


def test_the_merge_lands_on_the_plan_branch_and_never_in_the_outer_checkout(iso):
    """V3-2 §3 rule 5. A merge into the primary checkout is the collision plan
    isolation exists to prevent, and it would arrive as a commit on the
    operator's own branch."""
    paths = wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                               project_root=iso["root"])
    (Path(paths["m01"]) / "src" / "beta.py").write_text("B = 1\n")
    git(["add", "-A"], paths["m01"], check=True)
    git(["commit", "-q", "-m", "feat: beta"], paths["m01"], check=True)
    outer_before = git(["rev-parse", "HEAD"], iso["root"], check=True)[1]
    plan_before = git(["rev-parse", "HEAD"], iso["tree"], check=True)[1]

    res = wt.merge_group(iso["plan_dir"], iso["manifest"], "g",
                         integration_session="i99")
    assert res["status"] == "merged"
    # The PLAN BRANCH carries the merge...
    assert git(["rev-parse", "--abbrev-ref", "HEAD"],
               iso["tree"], check=True)[1] == pwt.plan_branch(SLUG)
    plan_after = git(["rev-parse", "HEAD"], iso["tree"], check=True)[1]
    assert plan_after != plan_before
    assert "merge m01 for i99" in git(["log", "-1", "--format=%s"],
                                      iso["tree"], check=True)[1]
    assert git(["merge-base", "--is-ancestor", plan_before, plan_after],
               iso["tree"])[0] == 0
    assert (iso["tree"] / "src" / "beta.py").exists()
    # ...and the operator's checkout saw none of it: same commit, no new file.
    assert git(["rev-parse", "HEAD"], iso["root"], check=True)[1] == outer_before
    assert not (iso["root"] / "src" / "beta.py").exists()


# ------------------------------------------- §12.1: the gitdir POINTER FILE
def test_the_exclude_is_written_to_the_SHARED_git_dir_so_it_works_in_a_worktree(iso):
    """`<root>/.git` is a FILE in a worktree, so the old
    `<root>/.git/info/exclude` write raised OSError and was swallowed — under
    plan isolation that rare branch is the DEFAULT. Known positive: the entry is
    removed first and the ignore really stops working, so a no-op write would be
    visible."""
    assert (iso["tree"] / ".git").is_file()              # the premise, measured
    common = Path(gs.common_dir(iso["tree"]))
    exclude = common / "info" / "exclude"
    exclude.write_text("")                               # neuter it
    assert git(["check-ignore", "-q", f"{wt.WORKTREE_DIRNAME}/probe"],
               iso["tree"])[0] != 0                      # ...and it is really off

    wt._exclude_worktree_dir(iso["tree"])
    assert f"/{wt.WORKTREE_DIRNAME}/" in exclude.read_text()
    assert git(["check-ignore", "-q", f"{wt.WORKTREE_DIRNAME}/probe"],
               iso["tree"])[0] == 0
    assert common != iso["tree"] / ".git"                # it went to the SHARED dir


# -------------------------------------------------------- §10: gate scoping
def test_a_plain_sessions_gate_sees_the_plan_worktree_and_none_of_the_outer_dirt(iso):
    """`gate_cwd` + §10.1's file set, together. Either half alone is not the
    property: a correct cwd with a `git diff HEAD` file set still misses every new
    file, and a correct file set in the wrong tree still reviews the neighbour."""
    (iso["root"] / "src" / "neighbour_edit.py").write_text("N = 1\n")
    (iso["root"] / "src" / "alpha.py").write_text("A = 'outer dirt'\n")
    assert len(gf.changed_files(iso["root"])) >= 2       # the dirt is really there

    cwd = vfy.gate_cwd(iso["plan_dir"], "s07", {"kind": "skill", "skill": "x"})
    assert cwd == str(iso["tree"])
    assert gf.changed_files(cwd) == []                   # §10.3: an EMPTY set, said

    # KNOWN POSITIVE, both shapes §10.1 exists for.
    (iso["tree"] / "src" / "alpha.py").write_text("A = 2\n")          # tracked edit
    (iso["tree"] / "src" / "brand_new.py").write_text("NEW = 1\n")    # untracked
    assert gf.changed_files(cwd) == ["src/alpha.py", "src/brand_new.py"]


def test_a_MEMBERs_gate_still_wins_over_the_plan_worktree(iso):
    """M4 nests one level further down: a member's gate reads that member's
    checkout, not the plan-level tree its branch was cut from."""
    paths = wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                               project_root=iso["root"])
    assert vfy.gate_cwd(iso["plan_dir"], "m01",
                        {"kind": "skill", "skill": "x"}) == paths["m01"]


# ------------------------------------------------- below the gate: NOTHING moves
def test_a_sub7_plans_names_and_targets_are_unchanged(legacy):
    """§6's whole promise. Measured against the v2 strings literally, not against
    a helper that could drift with the code it is checking."""
    state = wt.ensure_group(legacy["plan_dir"], "g", project_root=legacy["root"])
    assert state["plan_slug"] is None
    assert Path(state["merge_root"]) == Path(state["repo_root"]) == legacy["root"]
    assert state["base_ref"] == git(["rev-parse", "HEAD"], legacy["root"], check=True)[1]
    assert state["base_branch"] == "main"

    paths = wt.prepare_members(legacy["plan_dir"], legacy["manifest"], ["m01"],
                               project_root=legacy["root"])
    assert Path(paths["m01"]) == legacy["root"] / ".plan-worktrees" / "g" / "m01"
    assert wt.member_branch("g", "m01") == "plan/g/m01"
    assert ps.member_branch(legacy["plan_dir"], "g", "m01") == "plan/g/m01"
    assert gs.member_dirname("g", "m01") == "g/m01"


def test_a_sub7_group_still_measures_the_outer_checkout(legacy):
    """The mirror of the scoping test: below the gate a dirty shared tree IS the
    group's tree, and a stray write there must still refuse the merge."""
    wt.prepare_members(legacy["plan_dir"], legacy["manifest"], ["m01"],
                       project_root=legacy["root"])
    (legacy["root"] / "src" / "alpha.py").write_text("A = 'stray'\n")
    rep = wt.containment_report(legacy["plan_dir"], legacy["manifest"], "g")
    assert [s["session"] for s in rep["stray"]] == ["m01"]


# ------------------------------------------------------------------ cleanup
def test_cleanup_finds_the_nested_layout_member_it_created(iso):
    """`cleanup_group` recomputes the member path STRUCTURALLY rather than
    trusting the recorded string. It has to recompute the SAME name
    `prepare_members` used, or teardown silently leaves every member checkout and
    branch behind while reporting success."""
    paths = wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"],
                               project_root=iso["root"])
    assert Path(paths["m01"]).is_dir()
    res = wt.cleanup_group(iso["plan_dir"], "g")
    assert res["status"] == "cleaned" and res["removed"] == ["m01"]
    assert not Path(paths["m01"]).exists()
    assert git(["rev-parse", "--verify", "--quiet", "refs/heads/plan/g/m01"],
               iso["root"])[0] != 0                      # the branch went too


# --------------------------------------------- §6.2: the gate wins over a claim
def test_no_isolate_stops_routing_into_a_worktree_that_already_exists(iso):
    """`--no-isolate` is a flag on `begin` alone, so before the decision was
    recorded it won only until the next command returned: `apply`, verify and
    shipping still dispatched, gated and committed inside the worktree. Known
    positive: the claim and the directory both still exist throughout."""
    assert ps.plan_worktree(iso["plan_dir"]) == str(iso["tree"])

    pwt.record_isolation_gate(iso["plan_dir"], False, "--no-isolate", 7)
    assert iso["tree"].is_dir()                          # nothing was torn down
    assert ps.plan_worktree(iso["plan_dir"]) is None
    assert ps.plan_cwd(iso["plan_dir"], "s07") is None
    assert vfy.gate_cwd(iso["plan_dir"], "s07", {"kind": "argv", "cwd": "."}) == "."

    pwt.record_isolation_gate(iso["plan_dir"], True, "--isolate", 7)
    assert ps.plan_worktree(iso["plan_dir"]) == str(iso["tree"])


def test_a_refused_merge_never_aims_reset_hard_at_the_operators_checkout(iso):
    """A containment refusal carried no `merge_root`, so begin's recovery note fell
    back to the primary checkout and told the operator to `reset --hard` their own
    uncommitted work — when nothing had merged at all. Known positive: a conflict
    (a merge may have landed) still names the reset, against the plan worktree."""
    wt.prepare_members(iso["plan_dir"], iso["manifest"], ["m01"], project_root=iso["root"])
    (iso["tree"] / "src" / "alpha.py").write_text("A = 'a MEMBER wrote outside'\n")
    res = wt.merge_group(iso["plan_dir"], iso["manifest"], "g", integration_session="i99")
    assert res["status"] == "containment"
    assert res["merge_root"] == str(iso["tree"])
    assert "reset --hard" not in gs.merge_recovery(res, "i99")
    note = gs.merge_recovery({**res, "status": "conflict"}, "i99")
    assert f"git -C {iso['tree']} reset --hard" in note
    assert str(iso["root"]) + " " not in note


def test_begin_records_the_isolation_gate_only_under_the_plan_lock(iso, monkeypatch):
    """`_plan_worktree_prep` ran before begin took the plan lock, so the gate's
    run_state write raced a concurrent begin. With the lock held elsewhere it must
    write nothing; known positive: with the lock free it records the decision."""
    import run
    import run_state_io as rsi
    real = rsi.acquire_lock

    def held(_plan_dir):
        raise rsi.LockError("another /plan-execute appears to be running")

    monkeypatch.setattr(rsi, "acquire_lock", held)
    with pytest.raises(rsi.LockError):
        run._plan_worktree_prep(iso["plan_dir"], iso["manifest"], False)
    assert "plan_isolation" not in rsi.load_state(iso["plan_dir"])
    monkeypatch.setattr(rsi, "acquire_lock", real)
    assert run._plan_worktree_prep(iso["plan_dir"], iso["manifest"], False) is None
    assert rsi.load_state(iso["plan_dir"])["plan_isolation"]["enabled"] is False
    assert not rsi._lock_path(iso["plan_dir"]).exists()     # released after the write
