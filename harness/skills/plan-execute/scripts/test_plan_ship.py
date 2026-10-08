"""ISO-02 — HOW an isolated plan's work lands: commit, push, PR, plan record.

Contract: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).

Every check runs against a REAL git repository with a REAL linked worktree,
because every failure this module exists to prevent is a git behaviour and not a
Python one: a worktree whose ``.git`` is a POINTER FILE, a pathspec that must
exclude a TRACKED directory, a push that must not move the primary checkout's
branch, a neighbouring plan's repo-wide ``git add -A``. Each check that could
only ever report "clean" is paired with a KNOWN POSITIVE that makes it report
something.

    pytest plan-execute/scripts/test_plan_ship.py -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import plan_scope as ps  # noqa: E402
import plan_record as precord  # noqa: E402
import plan_ship as pship  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import review_context as rvs  # noqa: E402
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
# §2 / §8.b — shipping lands on the plan branch and nowhere else
# --------------------------------------------------------------------------
def test_git_steps_are_argv_with_a_worktree_cwd_only_under_isolation(tmp_path):
    plain = _repo(tmp_path)
    step = pship.git_step(plain["plan_dir"], "commit", "git:x")
    assert step["kind"] == "skill" and step["skill"] == "commit-orchestrate"
    assert "cwd" not in step

    state = pwt.ensure_plan_worktree(plain["plan_dir"], project_root=plain["root"])
    for sub in ("commit", "push", "pr"):
        s = pship.git_step(plain["plan_dir"], sub, f"{sub}:x")
        assert s["kind"] == "argv"
        assert s["cwd"] == state["path"]
        assert Path(s["argv"][0]).is_absolute()      # run_deploy_argv rejects relatives
    assert "SSH_AUTH_SOCK" in pship.git_step(plain["plan_dir"], "push", "p")["env_allowlist"]
    # Attempt 4 finding 3: a commit that SIGNS (ssh-agent key, git config kept
    # outside $HOME) needs the same git/ssh env the push has — but never the gh
    # tokens, which stay scoped to the sub-steps that talk to GitHub.
    commit_env = pship.git_step(plain["plan_dir"], "commit", "c")["env_allowlist"]
    assert "SSH_AUTH_SOCK" in commit_env and "GIT_CONFIG_GLOBAL" in commit_env
    assert "GH_TOKEN" not in commit_env and "GITHUB_TOKEN" not in commit_env
    assert "GH_TOKEN" in pship.git_step(plain["plan_dir"], "pr", "p")["env_allowlist"]


def test_each_sessions_commit_message_names_the_session(iso):
    """Attempt 4 finding 4: every session of an isolated plan commits to the ONE
    shared plan branch, so `git_step` passes a per-session `--message` — without
    it the CLI default made every session's commit read identically and the
    declared flag was dead on the shipping path."""
    step = pship.git_step(iso["plan_dir"], "commit", "git:x", "s42")
    msg = step["argv"][step["argv"].index("--message") + 1]
    assert "s42" in msg and SLUG in msg


def test_an_existing_pr_is_recorded_as_success_not_a_halt(tmp_path, monkeypatch):
    """Attempt 4 finding 2: all sessions ship the same branch, so the second
    commit-push-pr session finds the first one's PR (which already covers the
    new commits). `gh pr create` refusing with 'already exists' is the shipped
    state — while a real gh failure still raises."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\necho \'a pull request for branch "plan/x" into '
                  'branch "main" already exists:\' >&2\nexit 1\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    out = pship.open_pr(tmp_path, "plan/x", "main")
    assert out["status"] == "pr-exists"
    # ... and the known negative: any OTHER gh failure still fail-halts.
    gh.write_text("#!/bin/sh\necho 'gh: auth required' >&2\nexit 1\n")
    with pytest.raises(WorktreeError, match="auth required"):
        pship.open_pr(tmp_path, "plan/x", "main")


def test_a_pre_deploy_gate_tests_the_plan_worktree_not_the_outer_checkout(iso):
    """Attempt 4 finding 1 (high): `shipping._resolve_gate`/`_resolve_deploy`
    rooted their cwd in the OUTER checkout, which under isolation never receives
    the sessions' commits — so a `pre_deploy_gates` gate passed without seeing
    the work. The root must be the plan worktree, where `verify.gate_cwd` routes
    the SAME registry entry; and a CLAIMED-but-gone worktree refuses (twice,
    identically) instead of silently testing the shared tree."""
    import shipping as shp
    root, tree, plan_dir = iso["root"], iso["tree"], iso["plan_dir"]
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"smoke": {"kind": "argv", "argv": ["true"], "cwd": "backend"}}))
    assert shp._resolve_gate(plan_dir, "smoke")["cwd"] == str(tree / "backend")
    assert shp._resolve_deploy(plan_dir, {"deploy_argv": ["true"]})["cwd"] == str(tree)

    manifest = {"sessions": [{"id": "sX", "post_session":
                              {"git": "none", "pre_deploy_gates": ["smoke"]}}]}
    subprocess.run(["rm", "-rf", str(tree)], check=True)
    messages = []
    for _ in range(2):
        with pytest.raises(shp.StepResolveError) as exc:
            shp.compute_steps(plan_dir, manifest, "sX")
        assert exc.value.reason == "plan-worktree-missing"
        messages.append(str(exc.value))
    assert messages[0] == messages[1]


def test_commit_lands_on_the_plan_branch_and_leaves_the_primary_untouched(iso):
    root, tree = iso["root"], iso["tree"]
    before_head = git(["rev-parse", "main"], root, check=True)[1]
    before_tree = _tree_hash(root)

    _touch(tree, "src/b.py", "B = 1\n")
    result = pship.commit(tree, "feat(demo): session work", iso["plan_dir"])

    assert result["status"] == "committed"
    assert result["files"] == ["src/b.py"]
    assert git(["rev-parse", BRANCH], root, check=True)[1] == result["sha"]
    assert git(["rev-parse", "main"], root, check=True)[1] == before_head
    assert _tree_hash(root) == before_tree
    # No TRACKED path in the primary moved. (The plan's own untracked runtime
    # files are the orchestrator writing the OUTER tree, which is §8.a working.)
    assert git(["status", "--porcelain", "-uno"], root, check=True)[1] == ""


def test_a_commit_from_the_worktree_never_stages_the_plans_directory(iso):
    """§8.b, with the KNOWN POSITIVE that makes the assertion mean something:
    the frozen `_plans/` copy is deliberately dirtied first, so a bare
    `git add -A` WOULD have swept it."""
    tree = iso["tree"]
    frozen = tree / "_plans" / SLUG / "PLAN.html"
    frozen.write_text("<html>STALE — written inside the worktree</html>\n")
    _touch(tree, "src/b.py", "B = 1\n")

    # Control: the staging git would have done without the pathspec.
    rc, out, _ = git(["status", "--porcelain"], tree)
    assert rc == 0 and "_plans/" in out

    result = pship.commit(tree, "feat(demo): session work", iso["plan_dir"])
    assert result["files"] == ["src/b.py"]
    assert pship.plans_paths(tree, iso["state"]["base_ref"]) == []
    # And the dirt is still sitting there unstaged — nothing was silently reset.
    assert "_plans/" in git(["status", "--porcelain"], tree, check=True)[1]


def test_the_plans_assertion_can_actually_fire(iso):
    """NEUTER-ONCE. `plans_paths` returning [] on every real commit is
    indistinguishable from a check pointed at nothing — so commit a `_plans/`
    path by hand, past the pathspec, and require it to be seen."""
    tree = iso["tree"]
    (tree / "_plans" / SLUG / "PLAN.html").write_text("<html>swept</html>\n")
    git(["add", "-A"], tree, check=True)
    git(["commit", "-q", "-m", "chore: sweep the plan dir"], tree, check=True)
    assert pship.plans_paths(tree, iso["state"]["base_ref"]) == [f"_plans/{SLUG}/PLAN.html"]


def test_commit_refuses_a_commit_that_carried_a_plans_path(iso, monkeypatch):
    """The refusal itself, driven through `pship.commit`: a hook (or anything else)
    that adds a `_plans/` path after staging must make the commit REFUSE, not
    pass with a clean-looking file list."""
    tree = iso["tree"]
    _touch(tree, "src/b.py", "B = 1\n")
    real_add = pship._git

    def sneaky(args, cwd, **kw):
        out = real_add(args, cwd, **kw)
        if args[:2] == ["add", "-A"]:
            (tree / "_plans" / SLUG / "PLAN.html").write_text("<html>x</html>\n")
            real_add(["add", "-f", "--", f"_plans/{SLUG}/PLAN.html"], cwd)
        return out

    monkeypatch.setattr(pship, "_git", sneaky)
    with pytest.raises(WorktreeError, match=r"_plans/"):
        pship.commit(tree, "feat(demo): work", iso["plan_dir"])


def test_nothing_to_commit_is_a_status_not_a_failure(iso):
    assert pship.commit(iso["tree"], "feat(demo): nothing",
                        iso["plan_dir"])["status"] == "nothing-to-commit"


def test_a_formatting_hook_that_rewrites_the_tree_is_retried_once(iso):
    """Carried forward from 81f7f5d / ba82fe5 — the retry is freshly patched and
    the plan-worktree commit path must not regress it."""
    tree, root = iso["tree"], iso["root"]
    hooks = root / ".git" / "hooks"
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\n"
        "if [ ! -f .rewrote ]; then : > .rewrote; echo 'formatted' >> src/b.py; exit 1; fi\n"
        "exit 0\n")
    (hooks / "pre-commit").chmod(0o755)
    _touch(tree, "src/b.py", "B = 1\n")

    result = pship.commit(tree, "feat(demo): work", iso["plan_dir"])
    assert result["status"] == "committed"
    assert "formatted" in (tree / "src" / "b.py").read_text()
    assert git(["status", "--porcelain"], tree, check=True)[1].strip() in ("", "?? .rewrote")


def test_a_failed_commit_leaves_nothing_staged(iso):
    """A manual `git commit` in the worktree after a refused ship commit swept the
    session's files in, because the failed attempt left them staged."""
    tree, root = iso["tree"], iso["root"]
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _touch(tree, "src/b.py", "B = 1\n")
    with pytest.raises(pship.wt.WorktreeError, match="nothing is staged"):
        pship.commit(tree, "feat(demo): work", iso["plan_dir"])
    assert pship._nothing_staged(tree)
    assert (tree / "src" / "b.py").read_text() == "B = 1\n"


def test_push_sets_upstream_on_the_first_push_and_not_after(iso):
    tree, root = iso["tree"], iso["root"]
    _touch(tree, "src/b.py", "B = 1\n")
    first_sha = pship.commit(tree, "feat(demo): one", iso["plan_dir"])["sha"]

    assert pship.has_upstream(tree, BRANCH) is False
    out = pship.push(tree, BRANCH)
    assert out["argv"] == ["push", "-u", "origin", BRANCH]
    assert pship.has_upstream(tree, BRANCH) is True
    assert git(["rev-parse", f"refs/heads/{BRANCH}"], iso["origin"], check=True)[1] == first_sha

    _touch(tree, "src/c.py", "C = 1\n")
    pship.commit(tree, "feat(demo): two", iso["plan_dir"])
    assert pship.push(tree, BRANCH)["argv"] == ["push"]
    # `main` on the remote never moved: the plan branch is the only ref written.
    assert git(["rev-parse", "refs/heads/main"], iso["origin"], check=True)[1] == \
        git(["rev-parse", "main"], root, check=True)[1]


def test_the_first_push_resolves_the_remote_instead_of_hardcoding_origin(iso):
    """`push` built `["push", "-u", "origin", branch]` from a literal, so the first
    push failed outright in a repo whose single remote is called anything else —
    while `land_state.context` in the same land resolves the sole remote by name.
    The two halves of one land must agree about the push target.

    Found 2026-08-23 by a review of this branch."""
    tree, root = iso["tree"], iso["root"]
    for cwd in (root, tree):
        git(["remote", "rename", "origin", "upstream"], cwd)
    assert "origin" not in git(["remote"], tree, check=True)[1].split()

    assert pship.push_remote(tree) == "upstream"
    _touch(tree, "src/b.py", "B = 1\n")
    sha = pship.commit(tree, "feat(demo): one", iso["plan_dir"])["sha"]
    out = pship.push(tree, BRANCH)
    assert out["argv"] == ["push", "-u", "upstream", BRANCH]
    assert git(["rev-parse", f"refs/heads/{BRANCH}"], iso["origin"], check=True)[1] == sha

    # KNOWN POSITIVE — several remotes and no `origin` REFUSES rather than guessing.
    git(["remote", "add", "mirror", str(iso["origin"])], tree, check=True)
    with pytest.raises(WorktreeError, match="none is `origin`"):
        pship.push_remote(tree)


def test_a_failed_ignored_scan_refuses_instead_of_reading_as_no_ignored_files(iso, monkeypatch):
    """`_drop_ignored` returned `[]` when `git status` exited non-zero — the same
    value "nothing was ignored" returns. The record then committed with the
    RUNTIME files (.lock, run.ndjson, _closeouts/) still in the land worktree and
    reported `dropped_ignored: []` as an all-clear, which is exactly the §12.4
    teardown park the function exists to prevent.

    Found 2026-08-23 by a review of this branch."""
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    (plan_dir / "PLAN.html").write_text("<html>live</html>\n")

    # `record_plan` lives in `plan_record` (split out under the file-size rule);
    # `pship.record_plan` re-exports it, so CALLS are unchanged but a module-level
    # patch must name the module that actually runs the git command.
    real = precord._git

    def fail_the_ignored_scan(argv, cwd, **kw):
        if argv[:2] == ["status", "--porcelain=v1"] and "--ignored=matching" in argv:
            return 128, "", "fatal: bad config"
        return real(argv, cwd, **kw)

    monkeypatch.setattr(precord, "_git", fail_the_ignored_scan)
    with pytest.raises(WorktreeError, match="could not enumerate ignored files"):
        pship.record_plan(tree, plan_dir)

    # KNOWN POSITIVE — with the scan working, the same call records normally.
    monkeypatch.setattr(precord, "_git", real)
    assert pship.record_plan(tree, plan_dir)["status"] in ("recorded", "already-recorded")


def test_an_ignore_rule_matching_the_plan_dir_refuses_rather_than_deleting_the_record(iso):
    """`--ignored=matching` reports a directory that itself matches a pattern
    INSTEAD of recursing into it, and the escape guard admitted `victim == dest`.
    So an ignore rule covering the plan's own directory made `_drop_ignored`
    rmtree the entire copied record; `git add` then staged nothing and the caller
    returned `already-recorded` with `sha: None` — the record silently gone.

    Found 2026-08-23 by a review of this branch."""
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    (plan_dir / "PLAN.html").write_text("<html>live</html>\n")
    rel = plan_dir.resolve().relative_to(iso["root"].resolve()).as_posix()
    # An ignore pattern never applies to a TRACKED path, so this is reachable only
    # while the record is still untracked — i.e. the first time a plan is recorded,
    # which is the commit that first tracks it. Model that.
    git(["rm", "-r", "-q", "--cached", "--", rel], tree, check=True)
    git(["commit", "-q", "-m", "record not yet tracked"], tree, check=True)
    assert not git(["ls-files", "--", rel], tree, check=True)[1]

    def ignore(pattern):
        """Committed, not just written: §8.c refuses any stray STAGED path, so a
        loose .gitignore would trip that guard before this one."""
        (tree / ".gitignore").write_text(pattern)
        git(["add", "--", ".gitignore"], tree, check=True)
        git(["commit", "-q", "-m", f"ignore {pattern.strip()}"], tree, check=True)

    ignore(f"{rel}/\n")
    with pytest.raises(WorktreeError, match="is itself git-ignored"):
        pship.record_plan(tree, plan_dir)
    # The record survived the refusal — this is the whole point.
    assert (Path(tree) / rel / "PLAN.html").is_file()

    # KNOWN POSITIVE — an ignore rule that does NOT cover the record still records
    # normally, so the refusal above is specific and not a blanket "any .gitignore".
    ignore("__pycache__/\n")
    assert pship.record_plan(tree, plan_dir)["status"] in ("recorded", "already-recorded")


def test_a_non_fast_forward_push_parks_and_never_forces(iso):
    tree = iso["tree"]
    _touch(tree, "src/b.py", "B = 1\n")
    pship.commit(tree, "feat(demo): one", iso["plan_dir"])
    pship.push(tree, BRANCH)
    # Somebody outside this plan writes the branch (§1.5: not ours to overwrite).
    git(["push", "-q", "origin", f"{BRANCH}:{BRANCH}", "--force-with-lease"], tree)
    git(["reset", "-q", "--hard", "HEAD~1"], tree, check=True)
    _touch(tree, "src/d.py", "D = 1\n")
    pship.commit(tree, "feat(demo): diverged", iso["plan_dir"])
    with pytest.raises(WorktreeError, match="REJECTED|failed"):
        pship.push(tree, BRANCH)


# --------------------------------------------------------------------------
# §8.c — who commits the plan RECORD, and when
# --------------------------------------------------------------------------
def test_record_plan_commits_the_live_outer_directory_and_only_it(iso):
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    (plan_dir / "PLAN.html").write_text("<html>LIVE — written by the orchestrator</html>\n")
    (plan_dir / "_closeouts").mkdir()
    (plan_dir / "_closeouts" / "s06.json").write_text('{"result": "DONE"}\n')
    # A sibling plan and the index exist and must NOT be carried (probe 3 sc. 3).
    sibling = iso["root"] / "_plans" / "other-plan"
    sibling.mkdir(parents=True)
    (sibling / "PLAN.html").write_text("<html>other</html>\n")
    (iso["root"] / "_plans_index.md").write_text("- other-plan\n")

    result = pship.record_plan(tree, plan_dir)
    assert result["status"] == "recorded"
    assert result["pathspec"] == f"_plans/{SLUG}"
    assert {f"_plans/{SLUG}/PLAN.html", f"_plans/{SLUG}/_closeouts/s06.json"} \
        <= set(result["files"])
    assert all(f.startswith(f"_plans/{SLUG}/") for f in result["files"])
    committed = git(["show", f"{result['sha']}:_plans/{SLUG}/PLAN.html"], tree, check=True)[1]
    assert "LIVE" in committed                      # the live copy, not the frozen one
    assert "_plans_index.md" not in result["files"]
    assert not any("other-plan" in f for f in result["files"])


def test_record_plan_is_a_no_op_when_a_neighbour_already_swept_it(iso):
    """§8.d's admission, made survivable: a schema-6 neighbour's `git add -A`
    can commit the record first, and `record-plan` must then do nothing rather
    than fail the land."""
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    (plan_dir / "PLAN.html").write_text("<html>LIVE</html>\n")
    first = pship.record_plan(tree, plan_dir)
    assert first["status"] == "recorded"
    again = pship.record_plan(tree, plan_dir)
    assert again["status"] == "already-recorded" and again["sha"] is None


def test_record_plan_is_a_no_op_when_the_hook_rewrite_leaves_nothing_new(iso):
    """Measured 2026-10-06 on a resumed land: the live record differed from the
    committed one only by a final newline that `end-of-file-fixer` adds back.
    The hook rewrite emptied the staged diff, the retry commit had nothing to
    commit, and the land parked `final-record-failed` on every resume."""
    plan_dir, tree, root = iso["plan_dir"], iso["tree"], iso["root"]
    (plan_dir / "PLAN.html").write_text("<html>LIVE</html>\n")
    assert pship.record_plan(tree, plan_dir)["status"] == "recorded"
    (plan_dir / "PLAN.html").write_text("<html>LIVE</html>")  # final newline gone
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "f=$(git diff --cached --name-only | grep PLAN.html) || exit 0\n"
        "[ -n \"$(tail -c1 \"$f\")\" ] && { echo >> \"$f\"; exit 1; }\n"
        "exit 0\n")
    hook.chmod(0o755)
    again = pship.record_plan(tree, plan_dir)
    assert again["status"] == "already-recorded" and again["sha"] is None


def test_record_plan_refuses_to_copy_the_primary_onto_itself(iso):
    with pytest.raises(WorktreeError, match="primary checkout"):
        pship.record_plan(iso["root"], iso["plan_dir"])


# --------------------------------------------------------------------------
# §8.d — a REAL schema-6 neighbour, running today's repo-wide staging
# --------------------------------------------------------------------------
def test_a_v6_neighbour_sweeps_only_the_record_never_the_runtime_state(iso):
    """The neighbour is real, not a stub: a second plan directory stamped
    `plan_schema_version: 6` (below the isolation gate, so it runs the shared
    checkout exactly as it does today) and the literal `git add -A` its
    `/commit-orchestrate` performs.

    What must hold is NOT "the sweep cannot happen" — §8.d says it can and
    nothing the v7 plan does can stop it. It is that every DECISION INPUT is
    out of reach, so the sweep is harmless (§8.e).
    """
    root, plan_dir = iso["root"], iso["plan_dir"]
    neighbour = root / "_plans" / "legacy-neighbour"
    neighbour.mkdir(parents=True)
    (neighbour / "manifest.json").write_text(json.dumps({"plan_schema_version": 6}))
    (neighbour / "PLAN.html").write_text("<html>neighbour</html>\n")
    assert pwt.isolation_enabled(json.loads((neighbour / "manifest.json").read_text()))[0] \
        is False

    # The v7 plan is mid-run: live record + live runtime state both exist.
    (plan_dir / "run.ndjson").write_text('{"event": "dispatch_started"}\n')
    (plan_dir / "run_state.json").write_text('{"halt": {"set": false}}\n')
    claim = pwt.plan_state_dir(root, SLUG) / "worktree.json"
    assert claim.is_file()

    git(["add", "-A"], root, check=True)
    staged = git(["diff", "--cached", "--name-only"], root, check=True)[1].splitlines()
    sha = None
    git(["commit", "-q", "-m", "chore(legacy-neighbour): sweep"], root, check=True)
    sha = git(["rev-parse", "HEAD"], root, check=True)[1]

    swept_v7 = [p for p in staged if p.startswith(f"_plans/{SLUG}/")]
    assert swept_v7, "the sweep must really have reached the v7 plan — else this proves nothing"
    # RECORD swept (harmless). RUNTIME unreachable: it is not in any working tree.
    assert git(["ls-tree", "-r", "--name-only", sha], root, check=True)[1].count("plan-state") == 0
    assert claim.is_file() and json.loads(claim.read_text())["branch"] == BRANCH
    assert ps.plan_worktree(plan_dir) == str(iso["tree"])
    assert ps.pinned_base(plan_dir) == iso["state"]["base_ref"]

    # And the same sweep from INSIDE the plan worktree cannot happen at all.
    (iso["tree"] / "_plans" / SLUG / "PLAN.html").write_text("<html>x</html>\n")
    _touch(iso["tree"], "src/b.py", "B = 1\n")
    assert pship.commit(iso["tree"], "feat(demo): work", plan_dir)["files"] == ["src/b.py"]


def test_the_runtime_claim_survives_losing_the_in_tree_state_file(iso):
    """§8.e in one line: the in-tree `_plan_worktree.json` is a mirror. Delete it
    (a neighbour's merge resolving `--theirs` does exactly this) and every
    decision input must still be answerable."""
    (Path(iso["plan_dir"]) / pwt.STATE_FILENAME).unlink()
    (Path(iso["plan_dir"]) / (pwt.STATE_FILENAME + ".bak")).unlink(missing_ok=True)
    assert ps.plan_worktree(iso["plan_dir"]) == str(iso["tree"])
    assert ps.plan_branch(iso["plan_dir"]) == BRANCH
    assert ps.pinned_base(iso["plan_dir"]) == iso["state"]["base_ref"]


# --------------------------------------------------------------------------
# The review base (handoff from s05) — PER SESSION, with the pin as the fallback
# --------------------------------------------------------------------------
def test_each_session_is_reviewed_against_its_own_work_not_the_whole_branch(iso,
                                                                            monkeypatch):
    """The bound that keeps a review gate able to converge.

    Under isolation every session commits to the SAME plan branch. A base that is
    the branch's cut point makes session sNN's gate diff cut-point..HEAD and
    re-review every earlier session's commits — the surface grows with each
    session, which is the shape that cost this repo 16 failed gate rounds in 21
    hours (2026-08-20). Three sessions commit in sequence here; the third must be
    reviewed against the SECOND's commit and see only its own file.

    The pinned base is the KNOWN NEGATIVE: the same diff taken from it is the
    whole branch, so the assertion below cannot pass by accident.
    """
    tree, plan_dir = iso["tree"], iso["plan_dir"]
    stamps = [("s1", "2031-01-01T00:00:00+00:00", "2031-01-02T00:00:00+00:00"),
              ("s2", "2031-01-03T00:00:00+00:00", "2031-01-04T00:00:00+00:00"),
              ("s3", "2031-01-05T00:00:00+00:00", "2031-01-06T00:00:00+00:00")]
    lines, shas = [], {}
    for sid, dispatched, committed in stamps:
        lines.append(json.dumps({"ts": dispatched, "event": "dispatch_started",
                                 "session_ids": [sid]}))
        (plan_dir / "run.ndjson").write_text("\n".join(lines) + "\n")
        monkeypatch.setenv("GIT_AUTHOR_DATE", committed)
        monkeypatch.setenv("GIT_COMMITTER_DATE", committed)
        _touch(tree, f"src/{sid}.py", f"# {sid}\n")
        shas[sid] = pship.commit(tree, f"feat(demo): {sid}", plan_dir)["sha"]
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")

    base = rvs.get_base(plan_dir, "s3", str(tree))
    assert base == shas["s2"], "s3 must be reviewed from s2's commit, not the cut point"
    surface = git(["diff", "--name-only", base], tree, check=True)[1].split()
    assert surface == ["src/s3.py"]

    pinned = git(["diff", "--name-only", ps.pinned_base(plan_dir)], tree, check=True)[1]
    assert {"src/s1.py", "src/s2.py", "src/s3.py"} <= set(pinned.split())
    # Every session gets its own bound, and s1's is the cut point BY DERIVATION
    # (nothing on the branch predates its dispatch) rather than by the pin winning.
    assert rvs.get_base(plan_dir, "s2", str(tree)) == shas["s1"]
    assert git(["merge-base", "--is-ancestor", rvs.get_base(plan_dir, "s1", str(tree)),
                shas["s1"]], tree)[0] == 0


def test_a_base_the_plan_branch_cannot_reach_falls_back_to_the_pin(iso):
    """What the pin is actually FOR: the case where the per-session base cannot
    bound the diff the gate is about to take.

    `derive_base` answers "what was HEAD in `cwd` at dispatch time", and a base
    recorded by a call made in the OUTER checkout can be a commit on `main` the
    plan branch never saw. `git diff` against a commit HEAD cannot reach shows the
    symmetric difference of two histories, so the pin — an ancestor of the plan
    branch by construction — takes over. Known positive: the drifted base IS the
    right answer for a diff taken in the outer checkout, and is returned there.
    """
    root, plan_dir, tree = iso["root"], iso["plan_dir"], iso["tree"]
    (plan_dir / "run.ndjson").write_text(json.dumps(
        {"ts": "2030-01-01T00:00:00+00:00", "event": "dispatch_started",
         "session_ids": ["s06"]}) + "\n")
    _touch(root, "src/unrelated.py", "U = 1\n")
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "chore: unrelated work on main"], root, check=True)
    drifted = git(["rev-parse", "HEAD"], root, check=True)[1]
    assert drifted != iso["state"]["base_ref"]

    assert rvs.derive_base(plan_dir, "s06", str(root)) == drifted
    assert rvs.get_base(plan_dir, "s06", str(root)) == drifted        # and it CACHES it
    assert git(["merge-base", "--is-ancestor", drifted, "HEAD"], tree)[0] != 0
    assert rvs.get_base(plan_dir, "s06", str(tree)) == iso["state"]["base_ref"]
    assert git(["merge-base", "--is-ancestor", iso["state"]["base_ref"], "HEAD"],
               tree)[0] == 0


def test_without_isolation_get_base_is_unchanged(tmp_path):
    r = _repo(tmp_path)
    (r["plan_dir"] / "run.ndjson").write_text(json.dumps(
        {"ts": "2030-01-01T00:00:00+00:00", "event": "dispatch_started",
         "session_ids": ["s06"]}) + "\n")
    head = git(["rev-parse", "HEAD"], r["root"], check=True)[1]
    assert ps.pinned_base(r["plan_dir"]) is None
    assert rvs.get_base(r["plan_dir"], "s06", str(r["root"])) == head


# --------------------------------------------------------------------------
# Refusals that must RE-FIRE — the defect class this plan keeps producing
# --------------------------------------------------------------------------
def test_the_plans_refusal_fires_again_on_every_retry(iso):
    """A refusal that fires ONCE and then lets the retry through is not a control.

    The offending commit is made by hand; the first `commit` call must refuse,
    and the SECOND — the resumed ship, which finds nothing new to stage and would
    otherwise return `nothing-to-commit` as a success — must refuse identically.
    """
    tree, plan_dir = iso["tree"], iso["plan_dir"]
    (tree / "_plans" / SLUG / "PLAN.html").write_text("<html>swept</html>\n")
    git(["add", "-A"], tree, check=True)
    git(["commit", "-q", "-m", "chore: sweep the plan dir"], tree, check=True)

    messages = []
    for _ in range(2):
        with pytest.raises(WorktreeError) as exc:
            pship.commit(tree, "feat(demo): work", plan_dir)
        messages.append(str(exc.value))
    assert messages[0] == messages[1]
    assert f"_plans/{SLUG}/PLAN.html" in messages[0]

    # And the recovery the message names actually clears it.
    git(["reset", "-q", "--hard", "HEAD~1"], tree, check=True)
    _touch(tree, "src/b.py", "B = 1\n")
    assert pship.commit(tree, "feat(demo): work", plan_dir)["status"] == "committed"


def test_an_unreadable_range_raises_instead_of_reading_as_clean(iso):
    with pytest.raises(WorktreeError, match="assert §8.b"):
        pship.plans_paths(iso["tree"], "not-a-sha")


def test_commit_refuses_without_a_pinned_base(tmp_path):
    """The bound is what makes the §8.b answer stable; no bound, no commit."""
    r = _repo(tmp_path)
    with pytest.raises(WorktreeError, match="no pinned base"):
        pship.commit(r["root"], "feat: x", r["plan_dir"])


def test_git_step_refuses_when_the_claimed_worktree_is_gone(iso):
    """Without this the step falls back to the SKILL descriptor and
    /commit-orchestrate commits the operator's shared checkout."""
    subprocess.run(["rm", "-rf", str(iso["tree"])], check=True)
    for _ in range(2):
        with pytest.raises(WorktreeError, match="that directory is gone"):
            pship.git_step(iso["plan_dir"], "commit", "git:x")


def test_record_plan_refuses_a_stray_staged_path_and_keeps_refusing(iso):
    """§8.c stages ONLY the plan's own directory. The refusal is on the INDEX,
    before the commit, so the retry — which re-copies and re-stages the same
    pathspec — sees the same stray and says the same thing, instead of returning
    `already-recorded` over a bad commit."""
    plan_dir, tree = iso["plan_dir"], iso["tree"]
    (plan_dir / "PLAN.html").write_text("<html>LIVE</html>\n")
    (tree / "_plans_index.md").write_text("- regenerated\n")
    git(["add", "--", "_plans_index.md"], tree, check=True)

    messages = []
    for _ in range(2):
        with pytest.raises(WorktreeError) as exc:
            pship.record_plan(tree, plan_dir)
        messages.append(str(exc.value))
    assert messages[0] == messages[1]
    assert "_plans_index.md" in messages[0]

    git(["reset", "-q", "--", "_plans_index.md"], tree, check=True)
    assert pship.record_plan(tree, plan_dir)["status"] == "recorded"


def test_a_relative_plan_dir_survives_the_argv_step_when_the_plan_is_uncommitted(
        tmp_path, monkeypatch):
    """`git_step` embeds `--plan-dir` into an argv that re-runs this module with
    cwd = the plan WORKTREE, so an unresolved relative path re-resolves in there.
    The committed case passes either way (the frozen copy sits at the same
    relative path), so this is the case that actually bites: the plan directory
    not yet committed, absent from the worktree, `pinned_base` -> None, and every
    ship for the plan fail-halting on "no pinned base"."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    _init(root)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    # The plan directory arrives AFTER the pinned base: not yet committed, so the
    # worktree's frozen checkout does not contain it.
    plan_dir = root / "_plans" / SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html>live</html>\n")
    (plan_dir / "manifest.json").write_text(json.dumps({"plan_schema_version": 7,
                                                        "sessions": []}))
    pwt.ensure_plan_worktree(plan_dir, project_root=root)

    monkeypatch.chdir(root)                                # the orchestrator's cwd
    step = pship.git_step(Path("_plans") / SLUG, "commit", "git:x")  # as typed
    assert step["kind"] == "argv"
    embedded = step["argv"][step["argv"].index("--plan-dir") + 1]
    assert Path(embedded).is_absolute()

    _touch(step["cwd"], "src/b.py", "B = 1\n")
    p = subprocess.run(step["argv"], cwd=step["cwd"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "committed"


def test_a_remote_rejected_push_reports_itself_not_a_branch_ownership_violation(iso):
    """A pre-receive/protected-branch rejection says `! [remote rejected]`. A bare
    "rejected" substring match reported it as a §1.5 non-fast-forward park and
    sent the operator chasing a divergence that never happened."""
    tree = iso["tree"]
    hook = iso["origin"] / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho declined >&2\nexit 1\n")
    hook.chmod(0o755)
    _touch(tree, "src/b.py", "B = 1\n")
    pship.commit(tree, "feat(demo): one", iso["plan_dir"])
    with pytest.raises(WorktreeError) as exc:
        pship.push(tree, BRANCH)
    assert "REJECTED as non-fast-forward" not in str(exc.value)
    assert f"push of {BRANCH} failed" in str(exc.value)


def test_gate_cwd_does_not_MAP_A_CWD_SHIPPING_ALREADY_MAPPED(iso):
    """Idempotence across the two callers of one registry entry.

    Threading `session_id` into `shipping._resolve_cwd` (attempt 5) made
    `resolve_gate` return a cwd ALREADY inside the worktree. `verify.gate_cwd`
    then mapped it a second time: `repo_root` fails on a sub-directory that does
    not exist inside the worktree, the fallback is the OUTER root, and the
    prefix lands twice -- `<repo>/.plan-worktrees/<slug>/.plan-worktrees/<slug>/backend`.

    The KNOWN POSITIVE is `backend` existing in the outer checkout but NOT in the
    worktree: that is what makes the second mapping fall back instead of quietly
    producing the right answer, which is why the pre-existing test (whose `src`
    exists in both) could not see this.
    """
    import shipping as shp
    import verify as vfy
    root, plan_dir = iso["root"], iso["plan_dir"]
    (root / ".claude").mkdir(exist_ok=True)
    (root / "backend").mkdir(exist_ok=True)
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"smoke": {"kind": "argv", "argv": ["true"], "cwd": "backend"}}))

    resolved = shp.resolve_gate(plan_dir, "smoke", "s06")
    assert resolved["cwd"] == str(iso["tree"] / "backend")
    # Mapping it again must be a no-op, not a second prefix.
    assert vfy.gate_cwd(plan_dir, "s06", resolved) == resolved["cwd"]
