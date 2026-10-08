"""ISO-01 / ISO-04 — the plan's own branch and LOCKED worktree at `begin`.

Contract: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).

Every check runs against a REAL git repository, because every failure this
module exists to prevent is a git behaviour rather than a Python one — a locked
worktree that `prune` silently refuses to reclaim, a hook path that resolves to
nothing while every command still exits 0, a `worktree add` that bases on local
HEAD when the start-point is omitted. Each check that can only ever report
"clean" is paired with a known positive that makes it report something.

    pytest plan-execute/scripts/test_plan_worktree.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import plan_hooks as ph  # noqa: E402
import plan_teardown as pt  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import worktree as wt  # noqa: E402
from worktree import WorktreeError, git  # noqa: E402

PASSING_HOOK = "#!/bin/sh\nexit 0\n"
SILENT_REJECT_HOOK = "#!/bin/sh\necho 'unrelated refusal' >&2\nexit 1\n"
REAL_HOOK_MARKER = "REAL-PRE-COMMIT-FIRED"


def _repo(tmp_path, name="proj"):
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    git(["init", "-q", "-b", "main"], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    # §9.3 step 5 validates that the FINAL hooksPath holds at least one
    # executable hook. `git init` normally lays down `*.sample` hooks, but that
    # depends on the machine's init template — so the fixture stops depending on
    # it and writes one itself.
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "post-commit").chmod(0o755)
    return root


def _plan(root, slug="demo-plan"):
    plan_dir = root / "_plans" / slug
    plan_dir.mkdir(parents=True)
    return plan_dir


def _hook_dir_fingerprint(root):
    hooks = Path(root) / ".git" / "hooks"
    return sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns)
                  for f in hooks.iterdir())


@pytest.fixture
def repo(tmp_path):
    root = _repo(tmp_path)
    return {"root": root, "plan_dir": _plan(root)}


# --------------------------------------------------------- §6 the version gate
@pytest.mark.parametrize("version,override,expected", [
    (2, None, False), (6, None, False), (7, None, True), (8, None, True),
    (None, None, False), ("7", None, False), (True, None, False),
    (6, True, True), (7, False, False), (9, False, False),
])
def test_version_gate_matrix(version, override, expected):
    manifest = {} if version is None else {"plan_schema_version": version}
    enabled, reason = pwt.isolation_enabled(manifest, override)
    assert enabled is expected
    assert reason  # never a bare boolean: run.ndjson records WHY


def test_no_isolate_wins_over_isolate():
    # `--no-isolate` is resolved to override=False by run.py's dispatch lambda
    # before it ever reaches here; this pins the precedence at the gate too.
    assert pwt.isolation_enabled({"plan_schema_version": 9}, False)[0] is False


def test_sweep_reports_what_it_looked_at_and_a_planted_v7_is_found(tmp_path):
    plans = tmp_path / "_plans"
    for n, version in enumerate([2, 2, 5, 6]):
        d = plans / f"p{n}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"plan_schema_version": version}))
    (plans / "no-manifest").mkdir()

    clean = pwt.sweep_schema_versions(plans)
    assert clean["manifests_found"] == 4          # POSITIVE: the sweep looked
    assert clean["versions"] == {"2": 2, "5": 1, "6": 1}
    assert clean["crossings"] == []

    # NEUTER-ONCE. A sweep that only ever reports zero has not been shown able to
    # report anything: plant a crossing and require it to surface.
    planted = plans / "planted"
    planted.mkdir()
    (planted / "manifest.json").write_text(json.dumps({"plan_schema_version": 7}))
    armed = pwt.sweep_schema_versions(plans)
    assert armed["manifests_found"] == 5
    assert [c["plan_schema_version"] for c in armed["crossings"]] == [7]
    (planted / "manifest.json").unlink()
    assert pwt.sweep_schema_versions(plans)["crossings"] == []


def test_sweep_survives_an_unreadable_manifest(tmp_path):
    d = tmp_path / "_plans" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{not json")
    out = pwt.sweep_schema_versions(tmp_path / "_plans")
    assert out["manifests_found"] == 1 and out["versions"] == {"UNREADABLE": 1}


# ------------------------------------------------------ §1 creation + the lock
def test_creation_makes_a_locked_branch_at_the_pinned_base(repo):
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])
    root, path = repo["root"], Path(state["path"])
    assert state["branch"] == "plan/demo-plan"
    assert path == root / ".plan-worktrees" / "demo-plan"
    assert path.is_dir()
    assert git(["rev-parse", "HEAD"], path)[1] == state["base_ref"]
    assert git(["rev-parse", "HEAD"], root)[1] == state["base_ref"]
    entry = pwt.worktree_entry(root, path)
    assert "locked" in entry and state["plan_slug"] in str(entry["locked"])
    assert pwt.is_locked(root, path)
    # §8.d — the ownership claim is RUNTIME state in the git common dir, where
    # no neighbouring plan's `git add -A` can reach it.
    claim = pwt.read_claim(root, "demo-plan")
    assert claim["branch"] == state["branch"] and claim["base_ref"] == state["base_ref"]
    assert pwt.plan_state_dir(root, "demo-plan").is_relative_to(root / ".git")
    # §1.1b — verified, not assumed.
    assert git(["check-ignore", "-q", ".plan-worktrees/probe"], root)[0] == 0


def test_creating_twice_is_idempotent(repo):
    first = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])
    second = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])
    assert second["created_at"] == first["created_at"]
    assert second["base_ref"] == first["base_ref"]
    # The canary is not re-run on re-entry — same token, same transcript.
    assert second["hook_canary"]["token"] == first["hook_canary"]["token"]
    paths = [e["worktree"] for e in pwt.list_worktrees(repo["root"])]
    assert len(paths) == len(set(paths)) == 2       # primary + the plan worktree


def test_reentry_reattaches_and_never_rewinds_to_base(repo):
    root, plan_dir = repo["root"], repo["plan_dir"]
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    path = Path(state["path"])
    (path / "src" / "b.py").write_text("B = 1\n")
    git(["add", "-A"], path, check=True)
    git(["commit", "-q", "-m", "member work"], path, check=True)
    landed = git(["rev-parse", "HEAD"], path)[1]
    assert landed != state["base_ref"]

    # The directory is destroyed out of band; the registration and branch survive.
    subprocess.run(["rm", "-rf", str(path)], check=True)
    again = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    assert Path(again["path"]).is_dir()
    assert git(["rev-parse", "HEAD"], again["path"])[1] == landed
    assert pwt.is_locked(root, path)


def test_a_failed_reattach_never_deletes_the_branch_holding_the_work(repo):
    """§4.1a scopes rollback to what THIS call created. The re-attach path runs
    with the branch ALREADY holding committed session work, so a `branch -D`
    there destroys the only copy — and its reflog with it, since the worktree
    that referenced the ref is gone too."""
    root, plan_dir = repo["root"], repo["plan_dir"]
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    path = Path(state["path"])
    (path / "src" / "b.py").write_text("B = 1\n")
    git(["add", "-A"], path, check=True)
    git(["commit", "-q", "-m", "member work"], path, check=True)
    landed = git(["rev-parse", "HEAD"], path)[1]

    # A FILE where the directory was: the registration still points here, so
    # `_reattach` runs, and `git worktree add` cannot succeed onto a file.
    subprocess.run(["rm", "-rf", str(path)], check=True)
    path.write_text("not a directory\n")
    with pytest.raises(WorktreeError):
        pwt.ensure_plan_worktree(plan_dir, project_root=root)

    assert git(["rev-parse", "--verify", "--quiet",
                "refs/heads/plan/demo-plan"], root)[1] == landed
    # The claim maps a branch that still exists, so it is kept too.
    assert pwt.read_claim(root, "demo-plan")["branch"] == "plan/demo-plan"


def test_a_reattach_with_no_branch_left_refuses_instead_of_crashing(repo):
    """`_reattach` has no start-point to offer, so a missing branch used to put
    `None` into git's argv: a TypeError, no refusal, and no rollback."""
    root, plan_dir = repo["root"], repo["plan_dir"]
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    subprocess.run(["rm", "-rf", str(state["path"])], check=True)
    # `git branch -D` refuses while a registration holds the ref; `update-ref`
    # does not consult it, which is how a ref goes missing under a live one.
    git(["update-ref", "-d", "refs/heads/plan/demo-plan"], root, check=True)
    with pytest.raises(WorktreeError, match="deleted out of band"):
        pwt.ensure_plan_worktree(plan_dir, project_root=root)


def test_the_deleted_branch_refusal_is_durable_and_keeps_the_reflog(repo):
    """A refusal that only fires once is not a control: the old shape refused,
    but its own `_reattach` had ALREADY pruned `.git/worktrees/<slug>` — the
    reflog its message named — and the RETRY then found no registration and
    silently re-created the branch at the pinned base, over committed session
    work. Now every call refuses, and the recovery it names still exists."""
    root, plan_dir = repo["root"], repo["plan_dir"]
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    path = Path(state["path"])
    (path / "src" / "b.py").write_text("B = 1\n")
    git(["add", "-A"], path, check=True)
    git(["commit", "-q", "-m", "member work"], path, check=True)
    landed = git(["rev-parse", "HEAD"], path)[1]

    subprocess.run(["rm", "-rf", str(path)], check=True)
    git(["update-ref", "-d", "refs/heads/plan/demo-plan"], root, check=True)
    for attempt in (1, 2):      # call 2 must refuse exactly like call 1
        with pytest.raises(WorktreeError, match="deleted out of band"):
            pwt.ensure_plan_worktree(plan_dir, project_root=root)
    # The refusal must not destroy the recovery it names: the per-worktree
    # reflog survives, still holding the session tip.
    head_log = root / ".git" / "worktrees" / "demo-plan" / "logs" / "HEAD"
    assert head_log.is_file() and landed in head_log.read_text()


def test_a_rollback_then_a_successful_retry_restores_the_claim(repo):
    """§4.1a step 3 on the RETRY path: a created-branch rollback clears the
    claim, so a later call that succeeds must write it again — a branch plus a
    LOCKED worktree with no claim mapping them is exactly the untouchable
    UNKNOWN (§1.5) whose consumer is s09's sweep, not this session."""
    root, plan_dir = repo["root"], repo["plan_dir"]
    path = pwt.plan_worktree_path(root, "demo-plan")
    path.parent.mkdir(parents=True)
    path.write_text("a FILE where the worktree dir should go\n")
    with pytest.raises(WorktreeError):
        pwt.ensure_plan_worktree(plan_dir, project_root=root)
    assert pwt.read_claim(root, "demo-plan") is None    # rollback cleared it

    path.unlink()
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    claim = pwt.read_claim(root, "demo-plan")
    assert claim is not None
    assert claim["branch"] == state["branch"] and claim["path"] == state["path"]


def test_create_refuses_a_none_base_outright(repo):
    """Backstop on the helper itself: whatever the caller got wrong, `None`
    must never reach git's argv (that was a TypeError, no refusal, no
    rollback). The durable refusal for the production shape is
    `_ensure_under_lease`'s, covered above."""
    root = repo["root"]
    with pytest.raises(WorktreeError, match="deleted out of band"):
        pwt._create(root, "plan/ghost", pwt.plan_worktree_path(root, "ghost"),
                    None, "reason")


def test_a_locked_worktree_is_never_prunable(repo):
    """Known positive for §12.3: `prune` alone is a DEAD END on a locked entry,
    which is why every teardown path is unlock -> remove -> prune."""
    root = repo["root"]
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    path = Path(state["path"])
    subprocess.run(["rm", "-rf", str(path)], check=True)
    git(["worktree", "prune"], root)
    assert pwt.worktree_entry(root, path) is not None       # still registered
    wt._unlock_worktree(root, path)
    git(["worktree", "prune"], root)
    assert pwt.worktree_entry(root, path) is None           # only now reclaimed


# ------------------------------------------------------------- §1.4/§1.5/§7
def test_an_unclaimed_branch_of_the_same_name_is_untouchable(repo):
    git(["branch", "plan/demo-plan"], repo["root"], check=True)
    with pytest.raises(WorktreeError, match="UNKNOWN and untouchable"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])
    # Left alone: never adopted, never deleted.
    assert git(["rev-parse", "--verify", "--quiet", "refs/heads/plan/demo-plan"],
               repo["root"])[0] == 0


def test_a_legacy_nested_group_ref_refuses(repo):
    git(["branch", "plan/demo-plan/g/s01"], repo["root"], check=True)
    with pytest.raises(WorktreeError, match="nested ref"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])


def test_a_submodule_bearing_base_is_refused(repo):
    root = repo["root"]
    (root / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = ./x\n')
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "add submodule config"], root, check=True)
    with pytest.raises(WorktreeError, match=r"\.gitmodules"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)


def test_a_non_git_target_keeps_todays_behaviour(tmp_path):
    """§7.2 — unchanged and unwarned, never a refusal."""
    plan_dir = tmp_path / "loose" / "plan"
    plan_dir.mkdir(parents=True)
    assert pwt.ensure_plan_worktree(plan_dir, project_root=tmp_path / "loose") is None


# ------------------------------------------------------------------ §9 hooks
def test_the_canary_rejects_a_commit_and_leaves_nothing_behind(repo):
    root = repo["root"]
    before = _hook_dir_fingerprint(root)
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    canary, path = state["hook_canary"], Path(state["path"])

    assert canary["ok"] is True and canary["rc"] != 0
    assert canary["marker"] in canary["transcript"]
    assert canary["temp_hooks_dir_removed"] is True
    assert not Path(canary["temp_hooks_dir"]).exists()
    # §9.5 is a NAMED residual gap, never silent coverage.
    assert canary["real_hooks_proven"] is False and canary["residual_gap"]
    # The SHARED hooks directory was never touched — a canary planted there can
    # reject an unrelated commit in the operator's own terminal.
    assert _hook_dir_fingerprint(root) == before
    # The final path is worktree-scoped, points at the real shared hooks, and the
    # worktree is clean: no `.plan-canary-*`, no stray commit.
    assert git(["config", "--get", "core.hooksPath"], path)[1] == canary["hooks_path"]
    assert Path(canary["hooks_path"]) == (root / ".git" / "hooks").resolve()
    assert wt._porcelain(path) == []
    assert git(["rev-parse", "HEAD"], path)[1] == state["base_ref"]


def test_a_canary_that_passes_refuses_the_plan(repo):
    """§9.4 — a check whose only outcome is 'clean' is not a check."""
    with pytest.raises(WorktreeError, match="canary commit SUCCEEDED"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"],
                                 hook_body=PASSING_HOOK)
    path = pwt.plan_worktree_path(repo["root"], "demo-plan")
    assert wt._porcelain(path) == []                    # debris cleaned in `finally`


def test_a_canary_refusal_leaves_the_real_hooks_in_charge(repo):
    """§9.3 step 4 — a refusal must leave behind neither canary debris NOR a
    temp hooksPath. A worktree still pointing at the temp directory the
    `finally` just deleted runs NO hook and every commit still exits 0: the
    exact "looks green, checks nothing" state, reached through the refusal path
    instead of the bug."""
    root = repo["root"]
    real = root / ".git" / "hooks" / "pre-commit"
    real.write_text(f"#!/bin/sh\necho '{REAL_HOOK_MARKER}' >&2\nexit 1\n")
    real.chmod(0o755)

    with pytest.raises(WorktreeError, match="canary commit SUCCEEDED"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root,
                                 hook_body=PASSING_HOOK)
    path = pwt.plan_worktree_path(root, "demo-plan")
    rc, value, _ = git(["config", "--get", "core.hooksPath"], path)
    assert not (rc == 0 and "hooks-canary-" in value), \
        f"core.hooksPath still names the deleted canary directory: {value}"
    # END TO END, not a config read: the repo's OWN pre-commit must still be
    # the thing that decides whether a commit here is allowed.
    (path / "after-refusal.txt").write_text("x\n")
    git(["add", "-A"], path, check=True)
    rc, out, err = git(["commit", "-m", "should be refused"], path)
    assert rc != 0 and REAL_HOOK_MARKER in f"{out}\n{err}"


def test_enabling_worktree_config_migrates_core_worktree_out_of_shared(repo):
    """git-worktree(1), CONFIGURATION FILE: once `extensions.worktreeConfig` is
    on, "the exception for core.bare and core.worktree is gone".

    Measured on git 2.48.1: a shared `core.worktree` plus the extension makes
    every LINKED worktree resolve its top level to the MAIN checkout — so a plan
    session's commits land in the very tree it was isolated from. The value has
    to move to the main worktree's own config.worktree first.

    The hijack is total, so when the migration is removed this test dies EARLIER
    than its own assertions, at the canary's `git add` refusing
    `.plan-canary-<token>` as "ignored by .gitignore" — the plan worktree asking
    the MAIN checkout about its own path. That message is the defect, not a
    side effect of it (`_evidence/s05/neuter-probe.txt`)."""
    root = repo["root"]
    git(["config", "--local", "core.worktree", str(root)], root, check=True)
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    path = Path(state["path"])

    assert git(["config", "--local", "--get", "core.worktree"], root)[0] != 0
    assert "worktree" in (root / ".git" / "config.worktree").read_text()
    migrated = state["hook_canary"]["worktree_config"]["migrated"]
    assert Path(migrated["core.worktree"]) == root
    # The known positive: without the migration BOTH of these are `root`.
    assert Path(git(["rev-parse", "--show-toplevel"], path)[1]) == path
    assert Path(git(["rev-parse", "--show-toplevel"], root)[1]) == root
    # `core.bare=false` is left alone: shared or not, every worktree computes
    # the same value from it, so moving it would be churn, not a fix.
    assert git(["config", "--local", "--get", "core.bare"], root)[1] == "false"


def test_a_rejection_without_the_marker_is_inconclusive(repo):
    with pytest.raises(WorktreeError, match="did not come from the canary"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"],
                                 hook_body=SILENT_REJECT_HOOK)


def test_an_empty_final_hooks_dir_is_refused(repo):
    """§9.3 step 5 — arriving at 'looks green' through the FIX, not the bug."""
    root = repo["root"]
    for f in (root / ".git" / "hooks").iterdir():
        f.unlink()
    with pytest.raises(WorktreeError, match="no executable hook"):
        pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)


def test_an_inherited_worktree_hookspath_is_overridden_and_recorded(repo):
    """The claude-code#60620 shape: something else writes a worktree-scoped
    hooksPath that BEATS the shared value. The control is the OVERWRITE on the
    PRODUCTION path — `hook_gate` writes `core.hooksPath = final`, and
    `git config --worktree` replaces the foreign entry — proven here with a
    known positive (a foreign value in, the shared dir in effect after), not
    with an assert the write makes unreachable. The gate also RECORDS what it
    overrode, so the override stays legible afterwards."""
    root = repo["root"]
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    path = Path(state["path"])
    git(["config", "--worktree", "core.hooksPath", "/foreign/hooks"], path, check=True)
    result = ph.hook_gate(root, path)
    assert result["inherited_hookspath"] == "/foreign/hooks"
    assert result["hooks_path"] == str(ph.shared_hooks_dir(root))
    assert git(["config", "--get", "core.hooksPath"], path)[1] \
        == str(ph.shared_hooks_dir(root))


def test_a_concurrent_second_plan_does_not_disturb_the_first(repo):
    root = repo["root"]
    first = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    fingerprint = _hook_dir_fingerprint(root)
    second_dir = _plan(root, "other-plan")
    second = pwt.ensure_plan_worktree(second_dir, project_root=root)

    assert second["hook_canary"]["marker"] != first["hook_canary"]["marker"]
    assert second["hook_canary"]["temp_hooks_dir"] != first["hook_canary"]["temp_hooks_dir"]
    # The first plan's own record and its worktree-scoped config are untouched,
    # and neither canary ever went near the shared hooks directory.
    assert pwt.load_state(repo["plan_dir"])["hook_canary"] == first["hook_canary"]
    assert git(["config", "--get", "core.hooksPath"], first["path"])[1] \
        == first["hook_canary"]["hooks_path"]
    assert _hook_dir_fingerprint(root) == fingerprint
    assert {e.get("branch") for e in pwt.list_worktrees(root)} == {
        "refs/heads/main", "refs/heads/plan/demo-plan", "refs/heads/plan/other-plan"}


# ------------------------------------------------------------ ISO-04 the setup
def test_setup_runs_once_in_the_worktree_and_is_recorded(repo):
    manifest = {"worktree_setup": ["sh", "-c", "pwd > .env && echo made-env"]}
    state = pwt.ensure_plan_worktree(repo["plan_dir"], manifest, project_root=repo["root"])
    path = Path(state["path"])
    assert state["setup"]["ok"] is True and state["setup"]["rc"] == 0
    assert "made-env" in state["setup"]["stdout"]
    # cwd was the FRESH worktree, not the primary checkout.
    assert (path / ".env").is_file()
    assert not (repo["root"] / ".env").exists()
    assert Path((path / ".env").read_text().strip()).resolve() == path.resolve()

    ran_at = state["setup"]["ran_at"]
    (path / ".env").unlink()
    again = pwt.ensure_plan_worktree(repo["plan_dir"], manifest, project_root=repo["root"])
    assert again["setup"]["ran_at"] == ran_at        # ONCE, not once per begin
    assert not (path / ".env").exists()


def test_a_failing_setup_halts_the_plan_with_its_output_attached(repo):
    manifest = {"worktree_setup": ["sh", "-c", "echo boom >&2; exit 3"]}
    with pytest.raises(WorktreeError, match="boom"):
        pwt.ensure_plan_worktree(repo["plan_dir"], manifest, project_root=repo["root"])
    recorded = pwt.load_state(repo["plan_dir"])["setup"]
    assert recorded["rc"] == 3 and recorded["ok"] is False


def test_setup_refuses_anything_that_is_not_an_argv_array(repo):
    with pytest.raises(WorktreeError, match="ARGV ARRAY"):
        pwt.ensure_plan_worktree(repo["plan_dir"], {"worktree_setup": "npm install"},
                                 project_root=repo["root"])


def test_a_missing_setup_binary_is_a_recorded_failure_not_a_crash(repo):
    with pytest.raises(WorktreeError, match="rc=127"):
        pwt.ensure_plan_worktree(repo["plan_dir"],
                                 {"worktree_setup": ["definitely-not-a-binary-xyz"]},
                                 project_root=repo["root"])


# --------------------------------------------------------- §12 teardown paths
def test_teardown_unlocks_removes_and_prunes_but_keeps_the_branch(repo):
    root = repo["root"]
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    out = pt.remove_plan_worktree(repo["plan_dir"])
    assert out["status"] == "removed"
    assert not Path(state["path"]).exists()
    assert pwt.worktree_entry(root, state["path"]) is None
    # §15.1 — an unlanded plan branch is the only copy of work nobody merged.
    assert git(["rev-parse", "--verify", "--quiet", "refs/heads/plan/demo-plan"],
               root)[0] == 0
    assert pwt.read_claim(root, "demo-plan") is None


def test_teardown_recovers_a_hand_deleted_directory(repo):
    root = repo["root"]
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=root)
    subprocess.run(["rm", "-rf", state["path"]], check=True)
    out = pt.remove_plan_worktree(repo["plan_dir"])
    assert out["status"] == "recovered"
    assert pwt.worktree_entry(root, state["path"]) is None


def test_teardown_repairs_a_moved_project_before_removing(tmp_path):
    """§12.1 — a worktree's `.git` is a POINTER FILE. Moving the project breaks
    the link in BOTH directions, so `git status` inside it fails until repair."""
    root = _repo(tmp_path, "before")
    plan_dir = _plan(root)
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    assert Path(state["path"]).is_dir()

    moved = tmp_path / "after"
    root.rename(moved)
    moved_plan = moved / "_plans" / "demo-plan"
    out = pt.remove_plan_worktree(moved_plan)
    assert out["status"] == "removed"
    assert not (moved / ".plan-worktrees" / "demo-plan").exists()
    assert pwt.worktree_entry(moved, moved / ".plan-worktrees" / "demo-plan") is None


def test_teardown_preserves_a_worktree_holding_real_content(repo):
    state = pwt.ensure_plan_worktree(repo["plan_dir"], project_root=repo["root"])
    (Path(state["path"]) / "agent-output.txt").write_text("work nobody committed\n")
    out = pt.remove_plan_worktree(repo["plan_dir"])
    assert out["status"] == "preserved"
    assert (Path(state["path"]) / "agent-output.txt").is_file()


def test_teardown_without_state_is_a_noop(repo):
    assert pt.remove_plan_worktree(repo["plan_dir"])["status"] == "noop"


# --------------------------------------------- the begin preflight ORDER (run.py)
def _begin_fixture(tmp_path):
    """A real, buildable plan, so the order test runs `run.cmd_begin` end to end."""
    import build_plan

    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text("VALUE = 1\n")
    spec = {
        "title": "Isolation Order Fixture",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": "i1", "title": "I1", "category": "work",
                   "touches": "src/alpha.py"}],
        "phases": [],
        "sessions": [{"id": "s01", "title": "Only", "model": "Opus", "items": ["i1"],
                      "prompt": "edit alpha", "dispatch": {"depends_on": []},
                      "post_session": {"git": "none"}}],
        "infographic": {"type": "phase-journey", "title": "t",
                        "phases": [{"num": 1, "name": "P1", "items": ["i1"]}],
                        "anchor_now": {"name": "a", "tagline": "b"},
                        "anchor_goal": {"name": "c", "tagline": "d"}},
    }
    plan_dir = root / "_plans" / "order-fixture"
    build_plan.build(spec, plan_dir, project_root=str(root))
    git(["init", "-q", "-b", "main"], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "fixture base"], root, check=True)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "post-commit").chmod(0o755)
    return root, plan_dir


def test_the_plan_worktree_exists_before_any_dispatch_cwd_is_computed(
    tmp_path, monkeypatch, capsys, ssot
):
    """The counter-move to this session's most likely failure. `_isolation_prep`
    and `_resolve_session_spec` are where every dispatch cwd is derived; a plan
    worktree created after that point would exist and never be dispatched into."""
    import run

    ssot()
    root, plan_dir = _begin_fixture(tmp_path)
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(root))
    seen = {}
    real = run._resolve_session_spec

    def spy(*args, **kwargs):
        seen["worktree"] = (root / ".plan-worktrees" / "order-fixture").is_dir()
        seen["branch"] = git(
            ["rev-parse", "--verify", "--quiet", "refs/heads/plan/order-fixture"], root)[0]
        return real(*args, **kwargs)

    monkeypatch.setattr(run, "_resolve_session_spec", spy)
    run.cmd_begin(plan_dir, ["s01"], isolate=True)
    capsys.readouterr()
    assert seen == {"worktree": True, "branch": 0}
    assert pwt.is_locked(root, root / ".plan-worktrees" / "order-fixture")
    run.cmd_release(plan_dir)


def test_codex_egress_scans_the_isolated_plan_worktree(
    tmp_path, monkeypatch, capsys, ssot
):
    """A primary-checkout .env is outside an isolated session's dispatch tree."""
    import run

    ssot()
    root, plan_dir = _begin_fixture(tmp_path)
    (root / ".env").write_text("LOCAL_ONLY=1\n")
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(root))

    run.cmd_plan(plan_dir, False, None, False, "codex")
    plan_payload = json.loads(capsys.readouterr().out)
    assert "/.env" in plan_payload["egress"]["restricted_hit"]
    assert plan_payload["dispatch_egress"] == {
        "plan_worktree": str(root / ".plan-worktrees" / "order-fixture"),
        "status": "gated_at_begin",
    }

    run.cmd_begin(plan_dir, ["s01"], harness="codex")
    payload = json.loads(capsys.readouterr().out)
    member = payload["batch"][0]
    worktree = root / ".plan-worktrees" / "order-fixture"

    assert member["dispatch_cmd"].startswith(f"cd {worktree} && ")
    assert not (worktree / ".env").exists()
    run.cmd_release(plan_dir)
    capsys.readouterr()

    claude_root, claude_plan = _begin_fixture(tmp_path / "claude")
    (claude_root / ".env").write_text("LOCAL_ONLY=1\n")
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(claude_root))
    run.cmd_begin(claude_plan, ["s01"])
    claude_payload = json.loads(capsys.readouterr().out)
    assert claude_payload["batch"][0]["verifier_mode"] == "cross_family"
    run.cmd_release(claude_plan)


def test_the_registry_guard_still_runs_before_anything_is_created(
    tmp_path, monkeypatch, capsys, ssot
):
    """REG-02 reads the registry in the PRIMARY checkout and must see other
    plans. Isolation prep therefore runs strictly after it — proven by refusing
    at the guard and finding neither a branch nor a worktree afterwards."""
    import run

    ssot()
    root, plan_dir = _begin_fixture(tmp_path)

    def refuse(*args, **kwargs):
        raise SystemExit("another plan is active in this repo")

    monkeypatch.setattr(run, "_begin_preflight", refuse)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"], isolate=True)
    capsys.readouterr()
    assert not (root / ".plan-worktrees").exists()
    assert git(["rev-parse", "--verify", "--quiet",
                "refs/heads/plan/order-fixture"], root)[0] != 0
    assert pwt.load_state(plan_dir) is None


def test_a_sub_seven_manifest_creates_nothing(tmp_path, monkeypatch, capsys, ssot):
    """The abort condition: NO behaviour change for a manifest below the gate."""
    import run

    ssot()
    root, plan_dir = _begin_fixture(tmp_path)
    manifest = json.loads((plan_dir / "manifest.json").read_text())
    # Stamped EXPLICITLY. This used to read the builder's own stamp and assert it
    # was below the gate — which stopped being true the moment s10's activation
    # bumped it, turning a test ABOUT sub-7 plans into a test of nothing. A test
    # that says what a version MEANS has to set that version itself.
    manifest["plan_schema_version"] = pwt.ISOLATION_MIN_SCHEMA - 1
    (plan_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(root))

    run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    assert not (root / ".plan-worktrees").exists()
    assert pwt.load_state(plan_dir) is None
    events = [json.loads(line) for line in
              (plan_dir / "run.ndjson").read_text().splitlines() if line.strip()]
    gate = [e for e in events if e["event"] == "plan_isolation_gate"]
    assert gate and gate[-1]["enabled"] is False
    run.cmd_release(plan_dir)


def test_the_builder_stamp_has_reached_the_isolation_gate():
    """§6.4 — THE STAMP IS THE LAST ACTIVATION STEP. FLIPPED BY s10, 2026-08-23.

    Until s10 this asserted `<`: bumping the stamp before `land.py` existed
    would have stamped every newly built plan as isolated with no way to land
    it. s10 flipped it after the two-plan end-to-end proof and the real-plan
    canary, which is what the tripwire was waiting for. It stays as a tripwire
    in the other direction — a stamp that DROPS back below the gate silently
    de-isolates every plan built after it, and that must not happen quietly.

    `build_plan.PLAN_SCHEMA_VERSION` still has ONE home (build_plan.py) and
    nothing here retypes it; both sides of the comparison are read from their
    own module.
    """
    import build_plan

    assert build_plan.PLAN_SCHEMA_VERSION >= pwt.ISOLATION_MIN_SCHEMA, (
        f"build_plan.PLAN_SCHEMA_VERSION ({build_plan.PLAN_SCHEMA_VERSION}) fell "
        f"below the isolation gate ({pwt.ISOLATION_MIN_SCHEMA}). Newly built plans "
        "would stop isolating. If that is deliberate, say so here; if not, restore "
        "the stamp (contract §6.4)."
    )


def test_sweep_reports_a_manifest_that_is_valid_json_but_not_an_object(tmp_path):
    """Guard the PARSE and the TYPE: `[]` parses fine and has no `.get`."""
    for name, body in (("listy", "[1, 2]"), ("stringy", '"nope"'), ("nully", "null")):
        d = tmp_path / "_plans" / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(body)
    out = pwt.sweep_schema_versions(tmp_path / "_plans")
    assert out["manifests_found"] == 3
    assert out["versions"] == {"NOT-AN-OBJECT": 3}
    assert out["crossings"] == []
