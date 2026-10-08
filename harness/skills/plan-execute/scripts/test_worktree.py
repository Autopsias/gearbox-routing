"""PL-01 — worktree isolation mechanism (contract §2 M3, §3 rules 5-7, §4).

The END-TO-END proof is `fixtures/worktree-parallel/run.sh`: three concurrent
members, a producer-first merge, and both planted failures. This file covers the
paths that proof cannot cheaply reach — the ones where being wrong destroys work
rather than merely failing:

  * containment DETECTION (§3 rule 6) — advisory containment is worth nothing
    unless a member that ignored its worktree is actually caught;
  * cleanup NEVER force-deleting a branch that still carries unmerged commits,
    even when the operator forced the worktree removal;
  * the base-ref VERIFICATION that S02 §4 says not to skip;
  * `verify.gate_cwd` sending a member's gates into that member's worktree and
    leaving the integration session's gates on the merged tree.

Every check runs against a real git repository. Run:
    pytest plan-execute/scripts/test_worktree.py -q
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import verify as vfy  # noqa: E402
import worktree as wt  # noqa: E402


def _repo(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text("A = 1\n")
    (root / "src" / "beta.py").write_text("B = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    wt.git(["init", "-q", "-b", "main"], root, check=True)
    wt.git(["config", "user.email", "t@example.com"], root, check=True)
    wt.git(["config", "user.name", "T"], root, check=True)
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "base"], root, check=True)
    return root


def _manifest():
    return {
        "plan_schema_version": 4,
        "sessions": [
            {"id": "s01", "items": ["i1"],
             "dispatch": {"parallel_group": "g", "isolation": "worktree", "depends_on": []}},
            {"id": "s02", "items": ["i2"],
             "dispatch": {"parallel_group": "g", "isolation": "worktree", "depends_on": []}},
            {"id": "s03", "items": ["i3"],
             "dispatch": {"integrates_group": "g", "depends_on": ["s01", "s02"]}},
        ],
        "items": [{"id": "i1", "touches": "src/alpha.py"},
                  {"id": "i2", "touches": "src/beta.py"},
                  {"id": "i3", "touches": "src"}],
    }


@pytest.fixture
def group(tmp_path, monkeypatch):
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    root = _repo(tmp_path)
    plan_dir = root / "_plans" / "fixture"
    plan_dir.mkdir(parents=True)
    m = _manifest()
    paths = wt.prepare_members(plan_dir, m, ["s01", "s02"], project_root=root)
    return {"root": root, "plan_dir": plan_dir, "manifest": m, "paths": paths}


# ------------------------------------------------------- creation + base ref
def test_members_get_distinct_worktrees_off_the_pinned_base(group):
    assert len(set(group["paths"].values())) == 2
    state = wt.load_state(group["plan_dir"], "g")
    base = state["base_ref"]
    for path in group["paths"].values():
        # `git worktree add -b` with no start-point bases on local HEAD (S02 §4).
        # These were given an explicit start-point, so each one IS the base.
        assert wt.git(["rev-parse", "HEAD"], Path(path))[1] == base


def test_pinned_base_survives_a_commit_landing_mid_flight(group):
    root, plan_dir = group["root"], group["plan_dir"]
    before = wt.load_state(plan_dir, "g")["base_ref"]
    (root / "later.txt").write_text("landed mid-flight\n")
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "later"], root, check=True)
    wt.prepare_members(plan_dir, group["manifest"], ["s01"], project_root=root)
    assert wt.load_state(plan_dir, "g")["base_ref"] == before


def test_a_drifted_worktree_is_refused_rather_than_dispatched_into(group):
    """KNOWN POSITIVE for the base-ref check: rewrite a member's branch onto an
    unrelated root and confirm `prepare_members` refuses instead of merging
    unrelated history into the integration tree later."""
    path = Path(group["paths"]["s01"])
    wt.git(["checkout", "-q", "--orphan", "detached"], path, check=True)
    wt.git(["commit", "-q", "--allow-empty", "-m", "unrelated root"], path, check=True)
    with pytest.raises(wt.WorktreeError, match="NOT based on the group's pinned base ref"):
        wt.prepare_members(group["plan_dir"], group["manifest"], ["s01"],
                           project_root=group["root"])


# ------------------------------------------------------------- containment
def test_containment_catches_a_member_that_wrote_into_the_shared_tree(group):
    """Contract §3 rule 6. Containment is ADVISORY — nothing confines a member to
    its worktree — so the integration session has to DETECT the violation. If
    this stops firing, an isolated group silently degrades to the shared tree it
    was declared to escape."""
    (group["root"] / "src" / "alpha.py").write_text("A = 'written in the SHARED tree'\n")
    rep = wt.containment_report(group["plan_dir"], group["manifest"], "g")
    assert [s["session"] for s in rep["stray"]] == ["s01"]
    assert rep["stray"][0]["paths"] == ["src/alpha.py"]


def test_containment_ignores_edits_that_predate_the_group(tmp_path, monkeypatch):
    """The baseline (§3 rule 7) is what keeps integration from sweeping up an
    unrelated edit that was already in the tree. A control, not decoration: with
    the baseline dropped this would report a stray write that no member made."""
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    root = _repo(tmp_path)
    (root / "src" / "alpha.py").write_text("A = 'dirty BEFORE the group ran'\n")
    plan_dir = root / "_plans" / "fixture"
    plan_dir.mkdir(parents=True)
    m = _manifest()
    wt.prepare_members(plan_dir, m, ["s01", "s02"], project_root=root)
    assert wt.containment_report(plan_dir, m, "g")["stray"] == []


def test_a_stray_write_refuses_the_merge(group):
    (group["root"] / "src" / "alpha.py").write_text("A = 'shared tree'\n")
    res = wt.merge_group(group["plan_dir"], group["manifest"], "g",
                         integration_session="s03")
    assert res["status"] == "containment"
    assert res["merged"] == []


# ------------------------------------------------------------------ commits
def test_the_orchestrator_commits_a_member_and_the_member_never_has_to(group):
    path = Path(group["paths"]["s01"])
    (path / "src" / "alpha.py").write_text("A = 2\n")
    sha = wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m")
    assert sha and wt.git(["rev-parse", "HEAD"], path)[1] == sha
    # And it lands on the member's OWN branch, not the shared checkout.
    assert wt.git(["rev-parse", "HEAD"], group["root"])[1] != sha
    assert wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m") is None


def test_a_member_commit_never_stages_the_plans_directory(group):
    """2026-09-24: the review gate wrote its findings ledger into the member's
    FROZEN `_plans/` copy, `git add -A` committed it, and §8.b then refused every
    later ship of the plan. A tree dirty ONLY under `_plans/` commits nothing."""
    path = Path(group["paths"]["s01"])
    ledger = path / "_plans" / "fixture" / "_verify_state" / "s01.medium.claude.findings.ndjson"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{}\n")
    assert wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m") is None
    (path / "src" / "alpha.py").write_text("A = 2\n")
    sha = wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m")
    files = wt.git(["show", "--name-only", "--format=", sha], path)[1].split()
    assert files == ["src/alpha.py"], files


def _install_hook(root, body):
    """Write a pre-commit hook into the SHARED .git (worktrees share hooks)."""
    rc, hooks, _ = wt.git(["rev-parse", "--git-common-dir"], root)
    assert rc == 0
    d = Path(root) / hooks if not Path(hooks).is_absolute() else Path(hooks)
    h = d / "hooks" / "pre-commit"
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text(body)
    h.chmod(0o755)


def test_a_formatting_hook_that_rewrites_files_does_not_fail_the_commit(group):
    """end-of-file-fixer / trailing-whitespace fail the FIRST commit and fix the
    files. One re-stage-and-retry is the standard response; without it the
    orchestrator halts the plan on a hook that already did its job."""
    path = Path(group["paths"]["s01"])
    (path / "src" / "alpha.py").write_text("A = 2")  # no trailing newline
    _install_hook(group["root"], "#!/bin/sh\n"
                  "f=src/alpha.py\n"
                  '[ -s "$f" ] && [ "$(tail -c1 "$f")" != "" ] || exit 0\n'
                  'printf "\\n" >> "$f"; exit 1\n')
    sha = wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m")
    assert sha, "a formatting hook must not strand the commit"
    assert (path / "src" / "alpha.py").read_text().endswith("\n")


def test_a_hook_that_rewrites_an_UNSTAGED_file_is_still_detected(group):
    """The `` M path`` case, which the MM case above cannot reach.

    `git status --porcelain` puts the staged code in column 1 and the worktree
    code in column 2, so a tracked file the hook rewrote but the member never
    touched prints with a LEADING SPACE. Read that output through `git(...)`'s
    default `strip=True` and the first line loses that space, every column
    shifts left, and the hook rewrite reads as a clean tree — the retry never
    fires and `commit_member` raises on a hook that already did its job.

    The file the hook rewrites must sort FIRST (`src/alpha.py` before
    `src/beta.py`), because stripping only damages the first line."""
    path = Path(group["paths"]["s02"])
    (path / "src" / "beta.py").write_text("B = 2\n")          # the member's own edit, staged
    _install_hook(group["root"], "#!/bin/sh\n"
                  "f=src/alpha.py\n"
                  'grep -q FIXED "$f" && exit 0\n'
                  'echo "# FIXED" >> "$f"; exit 1\n')
    sha = wt.commit_member(group["plan_dir"], group["manifest"], "s02", "m")
    assert sha, "a hook rewriting an unstaged tracked file must not strand the commit"
    assert "FIXED" in (path / "src" / "alpha.py").read_text()


def test_a_hook_that_GENERATES_a_file_is_still_detected(group):
    """A generating hook (pip-compile, `go mod tidy`, terraform-docs) writes a
    NEW file rather than rewriting a tracked one, so its only trace is a ``??``
    line. `git add -A` ran moments earlier, so an untracked file after the
    failed commit can only be the hook's doing — excluding ``??`` makes the
    retry miss it and strands a commit the hook already prepared."""
    path = Path(group["paths"]["s02"])
    (path / "src" / "beta.py").write_text("B = 2\n")
    _install_hook(group["root"], "#!/bin/sh\n"
                  "f=src/generated.txt\n"
                  '[ -f "$f" ] && exit 0\n'
                  'echo GENERATED > "$f"; exit 1\n')
    sha = wt.commit_member(group["plan_dir"], group["manifest"], "s02", "m")
    assert sha, "a generating hook must not strand the commit"
    rc, out, _ = wt.git(["show", "--name-only", "--format=", sha], path)
    assert "src/generated.txt" in out, "the generated file must land in the commit"


def test_a_real_hook_refusal_still_fails_the_commit(group):
    """The known-negative: a hook that refuses WITHOUT touching the tree (a
    ratchet, a lint error) must still surface, not be retried into success."""
    path = Path(group["paths"]["s01"])
    (path / "src" / "alpha.py").write_text("A = 3\n")
    _install_hook(group["root"], "#!/bin/sh\necho 'RATCHET: blocked' >&2\nexit 1\n")
    with pytest.raises(wt.WorktreeError):
        wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m")


def test_commit_member_is_a_noop_for_a_non_isolated_session(group):
    m = group["manifest"]
    m["sessions"][0]["dispatch"].pop("isolation")
    assert wt.commit_member(group["plan_dir"], m, "s01", "m") is None


# ------------------------------------------------------------------ cleanup
def test_cleanup_preserves_a_worktree_holding_git_invisible_output(group):
    """The S02 §6 shape: real output in a path git cannot see. The barred
    mechanism destroyed it silently; this one must refuse and say so."""
    path = Path(group["paths"]["s01"])
    (path / ".gitignore").write_text("__pycache__/\n*.pyc\n*.bin\n")
    wt.git(["add", "-A"], path, check=True)
    wt.git(["commit", "-q", "-m", "ignore rule"], path, check=True)
    (path / "output.bin").write_text("real, and invisible to git status\n")
    assert wt._porcelain(path) == [], "the plant must be invisible to plain git status"
    res = wt.cleanup_group(group["plan_dir"], "g")
    assert [p["session"] for p in res["preserved"]] == ["s01"]
    assert (path / "output.bin").exists()


def test_cleanup_ignores_tool_caches(group):
    cache = Path(group["paths"]["s01"]) / "src" / "__pycache__"
    cache.mkdir()
    (cache / "alpha.cpython-313.pyc").write_text("x")
    res = wt.cleanup_group(group["plan_dir"], "g")
    assert res["preserved"] == [], res["preserved"]
    assert set(res["removed"]) == {"s01", "s02"}


def test_cleanup_never_force_deletes_a_branch_carrying_unmerged_commits(group):
    """`--force` covers the WORKTREE only. Nothing overrides the branch check:
    cleanup uses `git branch -d`, never `-D`, so unmerged work always survives
    the operator's own impatience."""
    path = Path(group["paths"]["s01"])
    (path / "src" / "alpha.py").write_text("A = 'never merged'\n")
    wt.commit_member(group["plan_dir"], group["manifest"], "s01", "m")
    (path / "scratch.txt").write_text("still dirty\n")
    res = wt.cleanup_group(group["plan_dir"], "g", force=True)
    kept = {b["branch"] for b in res["branches_kept"]}
    assert "plan/g/s01" in kept, res
    assert wt.git(["rev-parse", "--verify", "refs/heads/plan/g/s01"],
                  group["root"])[0] == 0


# ----------------------------------------------------------------- gate cwd
def test_member_gates_run_inside_that_members_worktree(group):
    """Contract M4. A gate reading the working tree would otherwise test its
    peers' half-finished edits."""
    g = {"cwd": str(group["root"]), "argv": ["true"]}
    assert vfy.gate_cwd(group["plan_dir"], "s01", g) == group["paths"]["s01"]


def test_a_gate_subdirectory_keeps_its_position_under_the_worktree(group):
    g = {"cwd": str(group["root"] / "src"), "argv": ["true"]}
    assert vfy.gate_cwd(group["plan_dir"], "s01", g) == str(
        Path(group["paths"]["s01"]) / "src"
    )


def test_a_skill_gate_with_no_declared_cwd_still_lands_in_the_worktree(group):
    """`shipping._resolve_gate` gives a skill-kind gate no `cwd` at all. If that
    absence fell through as "no redirect", every skill gate would run against the
    shared tree its peers are still editing."""
    assert vfy.gate_cwd(group["plan_dir"], "s01", {"kind": "skill", "skill": "x"}) == \
        group["paths"]["s01"]
    assert vfy.gate_cwd(group["plan_dir"], "s03", {"kind": "skill", "skill": "x"}) is None


def test_the_integration_sessions_gates_stay_on_the_merged_tree(group):
    """The whole point of §3 rule 3: the integration gate set must test the
    MERGED tree. Redirecting it into a worktree would test the pre-merge state
    and let the clean-merge-but-broken failure through."""
    g = {"cwd": str(group["root"]), "argv": ["true"]}
    assert vfy.gate_cwd(group["plan_dir"], "s03", g) == str(group["root"])


# --------------------------------------------------------------------- merge
def test_producer_first_order_is_manifest_document_order(group):
    for sid, name in (("s01", "alpha"), ("s02", "beta")):
        p = Path(group["paths"][sid])
        (p / "src" / f"{name}.py").write_text(f"{name.upper()} = 2\n")
        wt.commit_member(group["plan_dir"], group["manifest"], sid, "m")
    res = wt.merge_group(group["plan_dir"], group["manifest"], "g",
                         integration_session="s03")
    assert res["status"] == "merged"
    assert res["merged"] == ["s01", "s02"]
    assert (group["root"] / "src" / "alpha.py").read_text() == "ALPHA = 2\n"
    assert (group["root"] / "src" / "beta.py").read_text() == "BETA = 2\n"


def test_the_integration_review_base_is_the_tree_before_the_member_merges(group):
    """`begin` merges the members BEFORE the integration session's first
    `dispatch_started`, so a base derived from that timestamp is the merged tip
    and the review surface is EMPTY (model-rating-and-gateways g1-integration,
    2026-09-27: INDETERMINATE twice). The base must be the pre-merge HEAD, so the
    gate reviews the members' combined diff plus any hand conflict resolution."""
    import llm_review_surface as lrs
    import review_context as rvc
    import run_state_io as rsi
    root, plan_dir = group["root"], group["plan_dir"]
    pre_merge = wt.git(["rev-parse", "HEAD"], root)[1]
    for sid, name in (("s01", "alpha"), ("s02", "beta")):
        (Path(group["paths"][sid]) / "src" / f"{name}.py").write_text(f"{name.upper()} = 2\n")
        wt.commit_member(plan_dir, group["manifest"], sid, "m")
    assert wt.merge_group(plan_dir, group["manifest"], "g",
                          integration_session="s03")["status"] == "merged"
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s03"])
    # Control: the timestamp derivation alone lands on the merged tip -- the defect.
    assert rvc.derive_base(plan_dir, "s03", root) == wt.git(["rev-parse", "HEAD"], root)[1]
    base = rvc.gate_env(plan_dir, "s03", str(root)).get(rvc.BASE_ENV)
    assert base == pre_merge
    assert lrs.diff_stat(str(root), base) == ["src/alpha.py", "src/beta.py"]


def test_a_reworked_member_is_merged_again_and_an_unchanged_one_is_not(group):
    """A member reworked after its merge has new commits on its branch. Skipping
    it by id alone left the integration tree on the pre-rework state and begin
    reported "nothing new"."""
    root, plan_dir, m = group["root"], group["plan_dir"], group["manifest"]
    for sid, name in (("s01", "alpha"), ("s02", "beta")):
        (Path(group["paths"][sid]) / "src" / f"{name}.py").write_text(f"{name.upper()} = 2\n")
        wt.commit_member(plan_dir, m, sid, "m")
    assert wt.merge_group(plan_dir, m, "g", integration_session="s03")["merged"] == ["s01", "s02"]
    (Path(group["paths"]["s01"]) / "src" / "alpha.py").write_text("ALPHA = 3\n")
    wt.commit_member(plan_dir, m, "s01", "rework")
    res = wt.merge_group(plan_dir, m, "g", integration_session="s03")
    assert res["merged"] == ["s01"]
    assert (root / "src" / "alpha.py").read_text() == "ALPHA = 3\n"
    assert wt.merge_group(plan_dir, m, "g", integration_session="s03")["merged"] == []


def test_a_containment_refusal_carries_its_merge_root(group):
    """begin's recovery note aims `reset --hard` at `merge_root`; a refusal that
    carried none fell back to the operator's own checkout."""
    (group["root"] / "src" / "alpha.py").write_text("A = 'shared tree'\n")
    res = wt.merge_group(group["plan_dir"], group["manifest"], "g", integration_session="s03")
    assert res["status"] == "containment"
    assert res["merge_root"] == str(group["root"])   # no plan worktree here: the outer repo


def test_a_second_round_conflict_resets_to_that_rounds_start_not_base_ref(group):
    """After a rework, a later integration round can conflict. Resetting to the
    group's `base_ref` would also undo the merges round one landed; the note must
    name the head THIS round started from."""
    import group_scope as gs
    root, plan_dir, m = group["root"], group["plan_dir"], group["manifest"]
    for sid, name in (("s01", "alpha"), ("s02", "beta")):
        (Path(group["paths"][sid]) / "src" / f"{name}.py").write_text(f"{name.upper()} = 2\n")
        wt.commit_member(plan_dir, m, sid, "m")
    assert wt.merge_group(plan_dir, m, "g", integration_session="s03")["status"] == "merged"
    (root / "src" / "alpha.py").write_text("ALPHA = 'hand fix on the merged tree'\n")
    wt.git(["commit", "-qam", "hand fix"], root, check=True)
    round_two = wt.git(["rev-parse", "HEAD"], root)[1]
    (Path(group["paths"]["s01"]) / "src" / "alpha.py").write_text("ALPHA = 'rework'\n")
    wt.commit_member(plan_dir, m, "s01", "rework")
    res = wt.merge_group(plan_dir, m, "g", integration_session="s03")
    assert res["status"] == "conflict"
    assert res["pre_merge"] == round_two != res["base_ref"]
    assert wt.git(["rev-parse", "HEAD"], root)[1] == round_two     # the abort left it here
    note = gs.merge_recovery(res, "s03")
    assert f"reset --hard {round_two[:12]}" in note
    assert res["base_ref"][:12] not in note
