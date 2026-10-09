"""ISO-02 — WHERE an isolated plan's work happens: dispatch, gates, evidence.

Contract: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).

Every check runs against a REAL git repository with a REAL linked worktree,
because every failure this module exists to prevent is a git behaviour and not a
Python one: a worktree whose ``.git`` is a POINTER FILE, a pathspec that must
exclude a TRACKED directory, a push that must not move the primary checkout's
branch, a neighbouring plan's repo-wide ``git add -A``. Each check that could
only ever report "clean" is paired with a KNOWN POSITIVE that makes it report
something.

    pytest plan-execute/scripts/test_plan_scope.py -q
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import codex_command as cc  # noqa: E402
import plan_scope as ps  # noqa: E402
import plan_teardown as pt  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import shipping as shp  # noqa: E402
import verify as vfy  # noqa: E402
import worktree as wt  # noqa: E402
from worktree import WorktreeError, git  # noqa: E402

SLUG = "demo-plan"
BRANCH = f"plan/{SLUG}"


# --------------------------------------------------------------------------
# Fixtures — an origin, a primary checkout, a plan directory, a plan worktree
# --------------------------------------------------------------------------
def _init(root):
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)


def _repo(tmp_path):
    """A primary checkout with a real `origin`, so push/upstream are testable."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    plan_dir = root / "_plans" / SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html>base</html>\n")
    (plan_dir / "manifest.json").write_text(json.dumps({"plan_schema_version": 7,
                                                        "sessions": []}))
    _init(root)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "post-commit").chmod(0o755)
    return {"root": root, "origin": origin, "plan_dir": plan_dir}


@pytest.fixture
def iso(tmp_path):
    """A repo with the plan already isolated — the state s05 leaves behind."""
    r = _repo(tmp_path)
    # No `hook_body` override: the DEFAULT canary is the rejecting one §9.3
    # requires, and a passing body would (correctly) refuse the plan.
    r["state"] = pwt.ensure_plan_worktree(r["plan_dir"], project_root=r["root"])
    r["tree"] = Path(r["state"]["path"])
    return r


def _tree_hash(root):
    return git(["rev-parse", "HEAD^{tree}"], root, check=True)[1]


def _touch(tree, rel, text):
    p = Path(tree) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --------------------------------------------------------------------------
# §12.1 — the gitdir POINTER FILE, and the root walk that must not escape it
# --------------------------------------------------------------------------
def test_repo_root_and_project_root_inside_a_gitdir_pointer_worktree(iso):
    tree, root = iso["tree"], iso["root"]
    # The premise: this really is a pointer FILE, not a directory. If git ever
    # changed that, every assertion below would be testing nothing.
    assert (tree / ".git").is_file()
    assert (tree / ".git").read_text().startswith("gitdir:")

    assert Path(wt.repo_root(tree)).resolve() == tree.resolve()
    assert Path(wt.repo_root(tree / "src")).resolve() == tree.resolve()

    inner_plan = tree / "_plans" / SLUG
    assert ps.project_root(inner_plan) == tree.resolve()
    assert Path(shp.find_project_root(inner_plan)).resolve() == tree.resolve()
    # The OUTER plan dir still resolves to the OUTER root — §8.a depends on it.
    assert Path(shp.find_project_root(iso["plan_dir"])).resolve() == root.resolve()


def test_the_unbounded_root_walk_really_would_escape_the_worktree(iso):
    """KNOWN POSITIVE for the ceiling in `project_root`.

    A guard whose only observed outcome is "did not escape" has not been shown
    to do anything. `.claude/` is untracked in most repos, so it is absent from
    a fresh worktree while present in the primary checkout one level up — and a
    plan worktree lives at `<repo>/.plan-worktrees/<slug>/`, UNDER it. Re-run the
    pre-fix algorithm verbatim and require it to land in the outer checkout.
    """
    (iso["root"] / ".claude").mkdir()
    assert not (iso["tree"] / ".claude").exists()
    inner_plan = (iso["tree"] / "_plans" / SLUG).resolve()

    def unbounded(plan_dir):                       # the shape shipped before ISO-02
        plan_dir = Path(plan_dir).resolve()
        for up in [plan_dir, *plan_dir.parents]:
            if (up / ".claude").is_dir():
                return up
        return plan_dir.parent

    assert unbounded(inner_plan) == iso["root"].resolve()      # escapes
    assert ps.project_root(inner_plan) == iso["tree"].resolve()  # bounded


# --------------------------------------------------------------------------
# Dispatch (§Verdict) — the preamble names the checkout and the branch
# --------------------------------------------------------------------------
def test_dispatch_preamble_names_the_worktree_branch_and_outer_plan_dir(iso):
    text = ps.dispatch_preamble(iso["plan_dir"], {"id": "s01"}, "s01", None)
    assert str(iso["tree"]) in text
    assert BRANCH in text
    assert iso["state"]["base_ref"][:12] in text
    assert str(Path(iso["plan_dir"]).resolve()) in text
    assert "_evidence/" in text                    # the §8 split, said out loud
    assert "Do NOT run `git commit`" in text


def test_no_isolation_means_no_preamble_at_all(tmp_path):
    r = _repo(tmp_path)
    assert ps.dispatch_preamble(r["plan_dir"], {"id": "s01"}, "s01", None) == ""
    assert ps.plan_worktree(r["plan_dir"]) is None
    assert ps.claim(r["plan_dir"]) == {}


def test_codex_dispatch_really_cds_into_the_plan_worktree(iso):
    """The Codex lane is the ONE place containment is mechanical rather than
    advisory: `codex exec` is a real shell command, so `cd <worktree> &&` actually
    binds. Asserted on the command string the wrapper agent is told to run, and on
    the runtime files, which must live UNDER that checkout or the workspace-write
    sandbox rejects them after the model has already started."""
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    session = {"id": "s01"}
    prompt = plan_dir / "sessions"
    prompt.mkdir(parents=True, exist_ok=True)
    (prompt / "s01.prompt.md").write_text("# session work\n")

    effective, runtime_dir, meta = cc._codex_worktree_files(
        plan_dir, session, prompt / "s01.prompt.md", str(tree), "stamp")
    assert meta["worktree"] == str(tree)
    assert meta["worktree_branch"] == BRANCH
    assert "worktree_group" not in meta            # a PLAN session has no group
    assert Path(effective).is_relative_to(tree) and Path(runtime_dir).is_relative_to(tree)
    body = Path(effective).read_text()
    assert str(tree) in body and BRANCH in body and "# session work" in body

    cmd = cc._codex_cmd("gpt-5.6-sol", "high", effective, str(runtime_dir / "lm.txt"),
                        workdir=str(tree))
    assert cmd.startswith(f"cd {shlex.quote(str(tree))} && ")


def test_a_claimed_worktree_whose_checkout_is_gone_REFUSES_rather_than_degrading(iso):
    """The silent-fallback trap: every reader degrades to "not isolated" when the
    directory is missing, which is right for a plan that never had one and WRONG
    for a plan that does — the caller would dispatch into, or commit, the shared
    checkout. And the refusal must re-fire: it writes nothing, so the second call
    says exactly what the first did."""
    subprocess.run(["rm", "-rf", str(iso["tree"])], check=True)
    assert ps.plan_worktree(iso["plan_dir"]) is None
    assert ps.claim(iso["plan_dir"])["path"]            # it still CLAIMS one
    for _ in range(2):
        with pytest.raises(WorktreeError, match="that directory is gone"):
            ps.dispatch_preamble(iso["plan_dir"], {"id": "s01"}, "s01", None)


def test_a_plan_that_never_claimed_a_worktree_is_untouched(tmp_path):
    r = _repo(tmp_path)
    ps.require_live(r["plan_dir"])                       # no claim -> no refusal
    assert ps.dispatch_preamble(r["plan_dir"], {"id": "s01"}, "s01", None) == ""


# --------------------------------------------------------------------------
# §8 — evidence paths SPLIT, never blanket-redirected
# --------------------------------------------------------------------------
def test_evidence_paths_split_between_the_outer_plan_dir_and_the_worktree(iso):
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    outer_ev = plan_dir / "_evidence" / "s06" / "proof.json"
    outer_ev.parent.mkdir(parents=True)
    outer_ev.write_text("{}\n")
    inner = _touch(tree, "docs/report.md", "# report\n")

    assert ps.resolve_evidence_path(plan_dir, "s06",
                                    "_evidence/s06/proof.json") == outer_ev
    assert ps.resolve_evidence_path(plan_dir, "s06", "docs/report.md") == inner


def test_a_missing_evidence_path_names_the_root_the_contract_chose(iso):
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    # Neither exists: the refusal must point where the rule says it belongs, not
    # at whichever root happened to be searched last.
    assert ps.resolve_evidence_path(plan_dir, "s06", "_evidence/s06/x.json") == \
        Path(plan_dir) / "_evidence/s06/x.json"
    assert ps.resolve_evidence_path(plan_dir, "s06", "docs/x.md") == \
        Path(tree) / "docs/x.md"


def test_an_evidence_path_written_in_the_frozen_copy_is_not_accepted_as_the_record(iso):
    """The trap the split exists to close: a session that writes its evidence
    into the worktree's FROZEN `_plans/` copy has written it where §8.a says
    nothing reads and §8.b says no commit may stage it."""
    frozen = iso["tree"] / "_plans" / SLUG / "_evidence" / "s06" / "proof.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("{}\n")
    resolved = ps.resolve_evidence_path(iso["plan_dir"], "s06",
                                        "_evidence/s06/proof.json")
    assert resolved != frozen
    assert not resolved.exists()          # -> the evidence gate REFUSES, correctly


def test_absolute_paths_keep_the_pre_existing_resolution(iso):
    absolute = iso["tree"] / "src" / "a.py"
    assert ps.resolve_evidence_path(iso["plan_dir"], "s06", str(absolute)) == absolute


def test_a_group_MEMBERs_resolution_is_unchanged_and_beats_the_plan_worktree(iso,
                                                                             monkeypatch):
    """Carried forward from 7d5632a, which shipped the member
    resolution with no test of its own. A member's artifacts live in ITS worktree,
    not the plan-level one its branch was cut from, and ISO-02 must not take that
    over — plan isolation only fills the gap for a session with no member
    checkout."""
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    manifest = {"plan_schema_version": 4, "sessions": [
        {"id": "m01", "items": ["i1"],
         "dispatch": {"parallel_group": "g", "isolation": "worktree", "depends_on": []}}],
        "items": [{"id": "i1", "touches": "src/alpha.py"}]}
    paths = wt.prepare_members(iso["plan_dir"], manifest, ["m01"], project_root=iso["root"])
    member = Path(paths["m01"])
    rel = Path(iso["plan_dir"]).resolve().relative_to(Path(iso["root"]).resolve())
    inside = member / rel / "_evidence" / "m01" / "proof.json"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}\n")

    assert ps.plan_cwd(iso["plan_dir"], "m01") == str(member)
    assert ps.resolve_evidence_path(iso["plan_dir"], "m01",
                                    "_evidence/m01/proof.json") == inside
    # The plan-level session is unaffected and still lands in the OUTER plan dir.
    assert ps.resolve_evidence_path(iso["plan_dir"], "s06", "_evidence/s06/x.json") == \
        Path(iso["plan_dir"]) / "_evidence/s06/x.json"


def test_gate_cwd_lands_in_the_plan_worktree(iso):
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    assert vfy.gate_cwd(plan_dir, "s06", {"kind": "skill", "skill": "x"}) == str(tree)
    declared = {"kind": "argv", "cwd": str(iso["root"] / "src")}
    assert vfy.gate_cwd(plan_dir, "s06", declared) == str(tree / "src")


def test_the_repo_relative_spelling_of_the_record_resolves_OUTSIDE_the_worktree(iso):
    """A manifest declares evidence repo-relative, not as a bare `_evidence/...`.

    Measured on this plan's own manifest: it declares
    `_plans/example-isolation-plan-2026-08-20/_evidence/s02`. Matching only the
    literal `_evidence/` prefix sends that spelling down the repo-relative branch,
    which searches the WORKTREE FIRST — and the worktree's `_plans/` copy is the
    frozen one from the pinned base, so the gate would accept an artifact from an
    earlier state as proof about the current one. Known positive: the frozen copy
    is populated and the live one is not, so a wrong answer is observable.
    """
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    rel = Path(plan_dir).resolve().relative_to(Path(iso["root"]).resolve())
    declared = (rel / "_evidence" / "s02" / "proof.json").as_posix()

    frozen = tree / rel / "_evidence" / "s02" / "proof.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text('{"stale": true}\n')

    resolved = ps.resolve_evidence_path(plan_dir, "s06", declared)
    assert resolved == Path(plan_dir) / "_evidence" / "s02" / "proof.json"
    assert resolved != frozen
    assert not resolved.exists()          # -> the evidence gate REFUSES, correctly
    # ...and it is the SAME destination as the short spelling of the same artifact.
    assert resolved == ps.resolve_evidence_path(plan_dir, "s06",
                                                "_evidence/s02/proof.json")


def test_the_integration_sessions_gates_run_where_the_MERGE_LANDED(iso, monkeypatch):
    """M4's carve-out, held against the mechanism rather than against prose.

    `worktree.merge_group` merges member branches into the group state's MERGE
    ROOT. Gating the integration session anywhere else tests a tree that never
    received the merge — the clean-merge-but-broken-tree failure becomes
    invisible. Known positive: a member's file exists ONLY after the merge, so a
    wrong cwd cannot see it.

    UPDATED for parallel-group contract v3 (V3-2 §3 rule 5): under plan isolation
    the merge root IS the plan worktree, so the OUTER checkout is now what must
    never see the merged file. `repo_root` stays the outer checkout — it is where
    `git worktree add` runs — and asserting the two are DIFFERENT is what makes
    this test able to fail if the merge target ever slides back out of the plan.
    """
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    plan_dir = iso["plan_dir"]
    manifest = {"plan_schema_version": 7, "sessions": [
        {"id": "m01", "items": ["i1"],
         "dispatch": {"parallel_group": "g", "isolation": "worktree", "depends_on": []}},
        {"id": "i99", "items": [], "dispatch": {"integrates_group": "g", "depends_on": []}}],
        "items": [{"id": "i1", "touches": "src/alpha.py"}]}
    (Path(plan_dir) / "manifest.json").write_text(json.dumps(manifest))
    paths = wt.prepare_members(plan_dir, manifest, ["m01"], project_root=iso["root"])
    member = Path(paths["m01"])
    (member / "src" / "alpha.py").write_text("ALPHA = 1\n")
    git(["add", "-A"], member, check=True)
    git(["commit", "-q", "-m", "feat: alpha"], member, check=True)

    state = wt.load_state(plan_dir, "g")
    merge_root = Path(state["merge_root"])
    assert merge_root == iso["tree"].resolve()                 # V3-2: the PLAN worktree
    assert Path(state["repo_root"]) != merge_root              # ...not the outer checkout
    assert not (merge_root / "src" / "alpha.py").exists()      # before the merge
    assert wt.merge_group(plan_dir, manifest, "g",
                          integration_session="i99")["status"] == "merged"
    assert (merge_root / "src" / "alpha.py").exists()          # after it

    assert ps.plan_cwd(plan_dir, "i99") == str(merge_root)
    assert vfy.gate_cwd(plan_dir, "i99", {"kind": "skill", "skill": "x"}) == str(merge_root)
    # The OUTER checkout never saw the merge — V3-2's whole point.
    assert not (iso["root"] / "src" / "alpha.py").exists()
    # A plain session under the same isolated plan lands in the same tree.
    assert ps.plan_cwd(plan_dir, "s06") == str(iso["tree"])


def test_a_pre_deploy_gate_and_a_verify_gate_agree_for_an_INTEGRATION_session(
        iso, monkeypatch):
    """The same registry entry, resolved by both callers, must name ONE tree.

    `verify.compute_gates` passed `session_id` into `shipping.resolve_gate`;
    `compute_steps` did not. So for a group's integration session the verify gate
    landed in the merge target while its `pre_deploy_gates` gate landed in the
    plan worktree — which never received the merge — and passed vacuously. Found
    by review on a path no test covered: the only pre_deploy test used
    a PLAIN session, for which both answers coincide, so the bug was invisible.

    Known positive: the member's file exists ONLY after the merge and ONLY in the
    merge target, so a gate rooted anywhere else cannot see it. Asserting the two
    callers agree is not enough on its own — they would also agree if both were
    wrong — so this pins the tree by that file, and pins it against the OUTER
    checkout, which under contract v3 is the tree the merge must never reach.
    """
    import shipping as shp
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    plan_dir, root = iso["plan_dir"], iso["root"]
    manifest = {"plan_schema_version": 7, "sessions": [
        {"id": "m01", "items": ["i1"],
         "dispatch": {"parallel_group": "g", "isolation": "worktree", "depends_on": []}},
        {"id": "i99", "items": [], "dispatch": {"integrates_group": "g", "depends_on": []},
         "post_session": {"git": "none", "pre_deploy_gates": ["smoke"]}}],
        "items": [{"id": "i1", "touches": "src/alpha.py"}]}
    (Path(plan_dir) / "manifest.json").write_text(json.dumps(manifest))
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"smoke": {"kind": "argv", "argv": ["true"]}}))

    paths = wt.prepare_members(plan_dir, manifest, ["m01"], project_root=root)
    member = Path(paths["m01"])
    (member / "src" / "alpha.py").write_text("ALPHA = 1\n")
    git(["add", "-A"], member, check=True)
    git(["commit", "-q", "-m", "feat: alpha"], member, check=True)
    assert wt.merge_group(plan_dir, manifest, "g",
                          integration_session="i99")["status"] == "merged"
    merge_root = Path(wt.load_state(plan_dir, "g")["merge_root"])

    ship_cwd = [s for s in shp.compute_steps(plan_dir, manifest, "i99")
                if s["name"] == "gate:smoke"][0]["cwd"]
    verify_cwd = vfy.gate_cwd(plan_dir, "i99", {"kind": "argv", "argv": ["true"]})

    assert ship_cwd == verify_cwd == str(merge_root)
    assert (Path(ship_cwd) / "src" / "alpha.py").exists()   # the merged work is HERE
    assert ship_cwd != str(root)                            # ...not the outer checkout
    assert not (root / "src" / "alpha.py").exists()


def test_a_vanished_plan_worktree_HALTS_shipping_instead_of_raising(iso):
    """`plan_ship.git_step` refuses through `wt.WorktreeError`; `ship_begin`,
    `ship_status` and `_reload` catch `StepResolveError`/`AdapterError` and
    nothing else. Untranslated, the refusal became an uncaught traceback — which
    in `apply --dry-run-shipping` also discards the apply output after the
    mutations have already landed. Known positive: the same call on a LIVE
    worktree resolves normally, so the refusal is the missing directory.
    """
    plan_dir = iso["plan_dir"]
    manifest = {"plan_schema_version": 7, "sessions": [
        {"id": "s06", "items": [], "post_session": {"git": "commit"}}]}
    (Path(plan_dir) / "manifest.json").write_text(json.dumps(manifest))
    assert [s["name"] for s in shp.compute_steps(plan_dir, manifest, "s06")] == ["commit"]

    subprocess.run(["rm", "-rf", str(iso["tree"])], check=True)
    for _ in range(2):                                   # and it RE-FIRES
        with pytest.raises(shp.StepResolveError, match="that directory is gone") as e:
            shp.compute_steps(plan_dir, manifest, "s06")
        assert e.value.reason == "plan-worktree-missing"
    st = shp.ship_status(plan_dir, "s06")
    assert st["steps"] == [] and "that directory is gone" in st["error"]


# --------------------------------------------------------------------------
# Teardown vs vanished — the three states, each with its own behaviour
# --------------------------------------------------------------------------
def test_a_successful_teardown_clears_the_claim_and_degrades_not_refuses(iso):
    """The refusal must be CLEARABLE by the legitimate action that resolves it.
    `remove_plan_worktree` keeps the mirror as a RECORD (with a `teardown`
    stamp); reading that record as a live claim made `require_live` refuse
    FOREVER after a clean teardown. Known negative first: a REFUSED teardown
    ("preserved" — dirty content) keeps the claim live, so the degrade really
    keys on the teardown SUCCEEDING and not on the stamp merely existing."""
    plan_dir = iso["plan_dir"]
    assert ps.claim(plan_dir)["path"]
    _touch(iso["tree"], "src/wip.py", "WIP = 1\n")
    assert pt.remove_plan_worktree(plan_dir)["status"] == "preserved"
    assert ps.claim(plan_dir)["path"]                    # refused -> still live
    assert pt.remove_plan_worktree(plan_dir, force=True)["status"] == "removed"
    for _ in range(2):                                   # the degrade is stable
        assert ps.claim(plan_dir) == {}
        assert ps.plan_worktree(plan_dir) is None
        ps.require_live(plan_dir)                        # no refusal
        assert ps.dispatch_preamble(plan_dir, {"id": "s01"}, "s01", None) == ""


def test_ship_record_and_ship_run_argv_HALT_when_the_worktree_vanishes_mid_ship(iso):
    """The prior finding named `_reload` explicitly: `ship_begin` caught the
    vanished-worktree refusal but the MID-SHIP entry points did not, so the ship
    loop tracebacked after `ship-begin` had already taken the lease — and the
    lease stayed held. This EXERCISES the catch instead of stating it."""
    plan_dir = iso["plan_dir"]
    manifest = {"plan_schema_version": 7, "sessions": [
        {"id": "s06", "items": [], "post_session": {"git": "commit"}}]}
    (Path(plan_dir) / "manifest.json").write_text(json.dumps(manifest))
    (Path(plan_dir) / "_closeouts").mkdir(exist_ok=True)
    (Path(plan_dir) / "_closeouts" / "s06.json").write_text('{"result": "DONE"}\n')
    out = shp.ship_begin(plan_dir, "s06")
    assert out["action"] == "run-argv" and out["step"] == "commit"

    subprocess.run(["rm", "-rf", str(iso["tree"])], check=True)
    for entry in (lambda: shp.ship_record(plan_dir, "s06", "commit", "done"),
                  lambda: shp.ship_run_argv(plan_dir, "s06", "commit")):
        out = entry()                                    # returns — never raises
        assert out["action"] == "failed"
        assert out["reason"] == "plan-worktree-missing"
    import ship_locks as slk
    assert slk.held_ship_locks(plan_dir) == []           # the lease is RELEASED


def test_the_codex_lane_refuses_a_vanished_worktree_like_the_claude_lane(iso):
    """`ps.plan_worktree` alone answers None for BOTH "never isolated" and
    "claimed but vanished"; built on it, the Codex lane fell through to
    `workdir=<shared checkout>` for a workspace-write process — the collision
    the Claude lane refuses. Live worktree resolves; vanished refuses, twice."""
    import run as runmod
    from ssot_policy import UnroutableCodexSession
    plan_dir = iso["plan_dir"]
    live = runmod._codex_wt_path(plan_dir, {}, "s06")
    assert Path(live).resolve() == iso["tree"].resolve()
    assert runmod._codex_wt_path(plan_dir, {"s06": "/member/tree"}, "s06") \
        == "/member/tree"                                # member shortcut intact
    subprocess.run(["rm", "-rf", str(iso["tree"])], check=True)
    for _ in range(2):
        with pytest.raises(UnroutableCodexSession, match="that directory is gone"):
            runmod._codex_wt_path(plan_dir, {}, "s06")
