"""E2E-01 — TWO PLANS, ONE REPO, END TO END. The plan's known-positive proof.

Everything else in this suite proves one mechanism in isolation. This file runs
two plans at the same time through the PRODUCTION path — `run.cmd_begin`, the
real plan worktrees, the real argv ship steps, the real `land` protocol with its
recorded human ack — inside ONE throwaway git repository with a real bare
`origin`, and asserts that neither plan can reach the other's files or the
operator's checkout.

WHY THE PLAN DIRECTORIES LIVE INSIDE THE FIXTURE REPO AS TRACKED PATHS.
`_plans/<slug>/` being a tracked path in the very repo the plan isolates is the
whole subject of contract §8: `git worktree add` checks out a FROZEN copy of it
while the orchestrator keeps writing the outer one. A fixture whose plan
directories sat beside the repo in `tmp_path` could not observe §8 at all — it
would prove isolation of a thing that was never in the way. So `_build` runs
plan-builder's own `build_plan.build` into `<repo>/_plans/<slug>` and commits it
before anything begins.

WHAT "THE OUTER CHECKOUT IS BYTE-IDENTICAL" MEANS HERE, said before it is
asserted. §8.a REQUIRES the orchestrator to keep writing `_plans/<slug>/` in the
primary checkout for the whole run — PLAN.html, run_state.json, run.ndjson, the
closeouts. A whole-tree hash would therefore contradict the contract rather than
test it. The assertion is split instead, and is stronger for it:

  * every outer path OUTSIDE `_plans/` is byte-identical before and after
    (`_tree_hash`, which is `git hash-object` over the real bytes on disk, not a
    git index read — an index read would miss a file git does not track);
  * and the set of outer paths that changed AT ALL is a subset of `_plans/`.

A cross-write into the other plan's source file fails the first; a cross-write
into the other plan's record fails the second.

Marked PER TEST, never per module: a module-level skip reads as green on a CI
host with no git, which is the one place these would silently stop running.

Run: pytest skills/plan-execute/scripts/test_two_plan_e2e.py -v
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import build_plan  # noqa: E402
import finish  # noqa: E402
import gate_files as gf  # noqa: E402
import land  # noqa: E402
import manifest_io as mio  # noqa: E402
import plan_scope as ps  # noqa: E402
import plan_ship as pship  # noqa: E402
import registry as reg  # noqa: E402
import run  # noqa: E402
import verify as vfy  # noqa: E402
import ship_locks as sl  # noqa: E402
import ship_state_io as ssio  # noqa: E402
from worktree import git  # noqa: E402

MARKER = "PLANTED_GATE_VIOLATION"


def _host_git_ok():
    """Does THIS host give us the git a plan worktree needs?

    Probed, not assumed: `git worktree add --lock` is the operation every test
    below is built on, and a host that cannot do it must skip rather than fail.
    """
    if shutil.which("git") is None:
        return False, "git is not on PATH"
    try:
        p = subprocess.run(["git", "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=30, cwd=str(SCRIPTS))
    except (OSError, subprocess.SubprocessError) as e:      # noqa: BLE001
        return False, f"git is unusable here: {e}"
    return (p.returncode == 0), f"`git worktree list` exited {p.returncode}"


_GIT_OK, _GIT_WHY = _host_git_ok()
# Applied to EVERY test individually — see the module docstring.
HOST_GIT = pytest.mark.skipif(not _GIT_OK, reason=f"host git/filesystem: {_GIT_WHY}")


# --------------------------------------------------------------------------
# the fixture repo — real git, a real bare origin, real built plans
# --------------------------------------------------------------------------
def _spec(slug, touches):
    item = f"{slug}-i1"
    return {
        "title": f"Plan {slug}",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": item, "title": "I1", "category": "work", "touches": touches}],
        "phases": [],
        "sessions": [{"id": "s01", "title": "S01", "model": "Sonnet", "items": [item],
                      "prompt": "do the work", "verify": {"gates": ["marker-gate"]}}],
        "infographic": {"type": "phase-journey", "title": "t",
                        "phases": [{"num": 1, "name": "P1", "items": [item]}],
                        "anchor_now": {"name": "a", "tagline": "b"},
                        "anchor_goal": {"name": "c", "tagline": "d"}},
    }


def _write_gate(root):
    """A REAL argv gate whose file set comes from `gate_files.changed_files` —
    the same function `scripts/session-quality-gate.sh` calls in production. It
    goes RED on a planted marker and reports how many files it examined, so
    "zero findings" and "I looked at nothing" can never be the same result."""
    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "marker_gate.py").write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import gate_files as gf\n"
        "files = gf.changed_files('.')\n"
        "if files is None:\n"
        "    print('INDETERMINATE'); sys.exit(2)\n"
        "hits = [f for f in files if "
        f"{MARKER!r} in (open(f, errors='ignore').read() if __import__('os').path.isfile(f) else '')]\n"
        "print(json.dumps({'files_examined': len(files), 'files': sorted(files),"
        " 'findings': sorted(hits)}))\n"
        "sys.exit(1 if hits else 0)\n"
    )
    (root / ".claude").mkdir(exist_ok=True)
    # An EMPTY shared default in the repo's own tree, as _land_fixture.build_repo
    # does: land reads it before the runtime one, so the runtime's at_land
    # llm-review-high never starts a real model review inside this test.
    ref = root / "skills" / "plan-execute" / "references"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "eval-gates.default.json").write_text("{}\n")
    (root / ".claude" / "eval-gates.json").write_text(json.dumps({
        "marker-gate": {"kind": "argv", "argv": [sys.executable, "tools/marker_gate.py"],
                        "cwd": ".", "timeout": 300}}))
    (root / ".claude" / "deploy-targets.json").write_text("{}")


def _add_plan(root, slug, version, touches):
    plan_dir = root / "_plans" / slug
    build_plan.build(_spec(slug, touches), plan_dir, project_root=str(root))
    manifest = json.loads((plan_dir / "manifest.json").read_text())
    # Stamped EXPLICITLY, never inherited from `build_plan.PLAN_SCHEMA_VERSION`:
    # these tests say what each plan's version MEANS, so they must keep meaning it
    # after the constant moves.
    manifest["plan_schema_version"] = version
    (plan_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return plan_dir


def _build(tmp_path, versions=(("plan-a", 7), ("plan-b", 7)), defer=()):
    """A primary checkout on `main` with a real bare `origin` and N built plans.

    `defer` names plans that are built and committed only AFTER the returned
    fixture's `base` commit — the §8.c2 shape test 5c needs, where one plan's
    branch is cut before the other plan's directory exists at all.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "untouched.py").write_text("UNTOUCHED = 1\n")
    _write_gate(root)
    plans, deferred = {}, dict(versions)
    for slug, version in versions:
        if slug not in defer:
            plans[slug] = _add_plan(root, slug, version, f"src/{slug.replace('-', '_')}.py")
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    # `git init` + `push -u` does NOT create refs/remotes/origin/HEAD, and §4.0's
    # "was the default branch renamed" assertion can never fire without it.
    git(["remote", "set-head", "origin", "main"], root, check=True)
    fx = {"root": root, "origin": origin, "plans": plans, "tmp": tmp_path,
          "versions": deferred, "base": git(["rev-parse", "HEAD"], root, check=True)[1]}
    return fx


def _commit_deferred(fx, slug):
    """Build and commit a plan the fixture deliberately withheld (see `defer`)."""
    root = fx["root"]
    fx["plans"][slug] = _add_plan(root, slug, fx["versions"][slug],
                                  f"src/{slug.replace('-', '_')}.py")
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", f"add {slug}"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)
    return fx["plans"][slug]


# --------------------------------------------------------------------------
# reading the fixture back
# --------------------------------------------------------------------------
def _declared(plan_dir):
    """What the PLAN says this session writes — read from the manifest's own
    `touches`, never re-typed here. A hard-coded list would pass even if the
    plan and the work disagreed, which is the thing under test."""
    manifest = mio.load_manifest(plan_dir)
    out = set()
    for item in manifest.get("items", []):
        for token in str(item.get("touches") or "").replace(",", " ").split():
            out.add(token)
    return out


def _tree_hash(root, skip=("_plans", ".git", ".plan-worktrees")):
    """A content hash of every file actually on disk under `root`, outside
    `skip`. Deliberately NOT `git write-tree`: the index cannot see a file git
    does not track, and an untracked cross-write is exactly what this must
    catch."""
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in skip:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(rel.as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _outer_paths(root):
    """Every file on disk under `root` outside `.git`/`.plan-worktrees`."""
    return {p.relative_to(root).as_posix() for p in Path(root).rglob("*")
            if p.is_file() and not p.is_symlink()
            and p.relative_to(root).parts[0] not in (".git", ".plan-worktrees")}


def _branch_files(root, base, branch):
    rc, out, _ = git(["diff", "--name-only", f"{base}..{branch}"], root)
    return sorted(p for p in out.splitlines() if p) if rc == 0 else None


def _commits(root, base, branch):
    return git(["rev-list", f"{base}..{branch}"], root, check=True)[1].split()


def _commit_files(root, sha):
    return sorted(p for p in git(
        ["show", "--name-only", "--format=", sha], root, check=True)[1].splitlines() if p)


def _events(plan_dir):
    text = (Path(plan_dir) / "run.ndjson").read_text()
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def _event_names(plan_dir):
    return [e["event"] for e in _events(plan_dir)]


# --------------------------------------------------------------------------
# driving the plans
# --------------------------------------------------------------------------
def _begin(plan_dir, **kw):
    out = run.cmd_begin(plan_dir, ["s01"], **kw)
    run.cmd_release(plan_dir)
    return out


def _session_writes(plan_dir, files):
    """A session doing its work INSIDE its own plan worktree — the only place a
    dispatched agent is told to write (see `plan_scope.plan_preamble`)."""
    tree = Path(ps.plan_worktree(plan_dir))
    for rel, text in files.items():
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return tree


def _ship_commit(plan_dir, session_id="s01"):
    """The REAL ship step for an isolated plan: `plan_ship.git_step` builds the
    descriptor and `ship_state_io.run_deploy_argv` executes it, exactly as
    `shipping.ship_run_argv` does. Never `/commit-orchestrate` — §8.b replaces
    the skill directive with an argv step precisely because the skill's
    `git add -A` would stage the operator's shared checkout."""
    step = pship.git_step(plan_dir, "commit", "git:repo", session_id)
    assert step["kind"] == "argv", step
    res = ssio.run_deploy_argv(step["argv"], cwd=step["cwd"],
                               env_allowlist=step["env_allowlist"], timeout=600)
    assert res["returncode"] == 0, res
    return res


def _ack_and_land(plan_dir):
    """The land, driven through the SAME recorded-state route `land-resume`
    uses. `land_ack` is the only writer of the approval and it refuses unless a
    candidate is actually awaiting review, so there is no bypass here and none
    is added to production code for the test's benefit."""
    first = land.land(plan_dir)
    assert first["action"] == "land-awaits-review", first
    acked = land.land_ack(plan_dir, note="e2e approval")
    assert acked["action"] == "land-acked", acked
    return land.land(plan_dir)


def _close_done(plan_dir, session_id="s01"):
    """Close the session the way the orchestrator does: apply the closeout, then
    walk the REAL verify loop (`verify-begin` -> `verify-run` -> `verify-finalize`).

    Both halves are needed. `apply` parks the session at `verify_pending` — it
    stays DOING until the gates pass — so a test that stopped at `apply` would
    report the plan ACTIVE forever, and "the repo is empty" would be a claim
    about branches only. And the verify loop is where a gate is run the way
    production runs it: `verify_run_argv` resolves the gate cwd through
    `plan_scope.gate_cwd`, which is the function that puts it in the plan's own
    worktree rather than the operator's checkout.
    """
    item = f"{Path(plan_dir).name}-i1"
    payload = {"session": session_id, "result": "DONE", "items_completed": [item],
               "items_blocked": [], "notes": {item: "done", session_id: "done"},
               "dispatch_next": True, "human_checkpoint_reason": None}
    out = Path(plan_dir) / "_closeouts" / f"{session_id}.raw.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<plan-execute-closeout>\n" + json.dumps(payload)
                   + "\n</plan-execute-closeout>\n")
    run.cmd_apply(plan_dir, session_id, str(out))
    run.cmd_release(plan_dir)
    return _verify(plan_dir, session_id)


def _verify(plan_dir, session_id="s01"):
    """The verify sub-loop, run for real. Returns each gate's verdict plus the
    finalize result, so a caller can assert on what the gate actually saw."""
    started = vfy.verify_begin(plan_dir, session_id)
    assert started["action"] in ("run-argv", "invoke-skill", "done"), started
    verdicts = {}
    while started.get("action") == "run-argv":
        gate = started["gate"]
        verdicts[gate] = vfy.verify_run_argv(plan_dir, session_id, gate)
        started = vfy.verify_begin(plan_dir, session_id, resume=True)
    return {"gates": verdicts, "final": vfy.verify_finalize(plan_dir, session_id)}


# --------------------------------------------------------------------------
# 1 — both plans begin, and the override is still required where it must be
# --------------------------------------------------------------------------
@HOST_GIT
def test_two_v7_plans_begin_in_one_repo_with_no_override(tmp_path):
    """The FIXED behaviour, asserted as one outcome and not as "either". Two
    isolated plans have no shared working tree to race, so REG-02's refusal has
    nothing to refuse — and `--concurrent`, the flag that used to be the only
    way through, is not passed. Both end up with their OWN locked worktree."""
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    assert [o["plan"] for o in reg.other_active_plans(b)] == ["plan-a"]
    _begin(b)                                       # no --concurrent, no refusal

    assert "concurrent_begin_isolated" in _event_names(b)
    assert "concurrent_begin_override" not in _event_names(b)
    assert run._statuses(a)["s01"] == "DOING" and run._statuses(b)["s01"] == "DOING"
    listing = git(["worktree", "list"], fx["root"], check=True)[1]
    for slug in ("plan-a", "plan-b"):
        assert f"[plan/{slug}] locked" in listing, listing
        assert Path(ps.plan_worktree(fx["plans"][slug])).is_dir()


@HOST_GIT
def test_a_sub7_overlap_still_refuses_and_the_override_is_still_logged(tmp_path):
    """KNOWN NEGATIVE for the test above, on the identical fixture with ONE
    thing changed: plan-b's manifest below §6's gate. A plan that does not
    isolate dispatches into the operator's checkout, so the two DO share a tree
    and the refusal must still fire, name plan-a, and dispatch nothing.

    plan-b is DEFERRED so plan-a can begin first: a plan is registry-ACTIVE from
    the moment it is built (its sessions are TODO), so a sub-7 neighbour sitting
    in the repo refuses whichever of the two goes first. Deferring it makes the
    refusal fire on the plan the docstring names."""
    fx = _build(tmp_path, versions=(("plan-a", 7), ("plan-b", 6)), defer=("plan-b",))
    a = fx["plans"]["plan-a"]
    _begin(a)
    b = _commit_deferred(fx, "plan-b")

    with pytest.raises(SystemExit) as exc:
        _begin(b)
    assert str(a) in str(exc.value) and "another plan is active" in str(exc.value)
    assert run._statuses(b)["s01"] == "TODO"

    # FALSIFICATION CONTROL — the same fixture, `--concurrent` passed: it must
    # proceed AND log the override naming plan-a, never quietly skip the log.
    _begin(b, concurrent=True)
    overrides = [e for e in _events(b) if e["event"] == "concurrent_begin_override"]
    assert len(overrides) == 1 and overrides[0]["active_plans"] == [str(a)]
    assert run._statuses(b)["s01"] == "DOING"


@HOST_GIT
def test_a_v7_plan_run_with_no_isolate_is_treated_as_sharing_the_tree(tmp_path):
    """`--no-isolate` is a flag `begin` alone sees, and it wins over the version
    gate (§6.2). A v7 plan run that way dispatches into the shared checkout, so
    the neighbour guard must judge it by what it will DO, not by its stamp."""
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    with pytest.raises(SystemExit, match="another plan is active"):
        _begin(b, isolate=False)


# --------------------------------------------------------------------------
# 2 — commit isolation, and the outer checkout
# --------------------------------------------------------------------------
@HOST_GIT
def test_every_commit_carries_only_its_own_plans_declared_files(tmp_path):
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    _begin(b)
    before_hash, before_paths = _tree_hash(fx["root"]), _outer_paths(fx["root"])

    _session_writes(a, {"src/plan_a.py": "A = 1\n", "src/a_helper.py": "AH = 1\n"})
    _session_writes(b, {"src/plan_b.py": "B = 1\n"})
    _ship_commit(a)
    _ship_commit(b)

    for slug, plan_dir in (("plan-a", a), ("plan-b", b)):
        branch = f"plan/{slug}"
        commits = _commits(fx["root"], fx["base"], branch)
        assert commits, f"{branch} has no commits"
        owned = {p for p in _declared(plan_dir)}
        others = set().union(*[_declared(fx["plans"][s]) for s in fx["plans"] if s != slug])
        for sha in commits:                          # EVERY commit, not just the tip
            files = _commit_files(fx["root"], sha)
            assert files, sha
            assert not (set(files) & others), (slug, sha, files)
            assert owned & set(files), (slug, sha, files, owned)

    # The operator's checkout: byte-identical outside `_plans/`, and the only
    # paths that changed at all are the plans' own records (§8.a).
    assert _tree_hash(fx["root"]) == before_hash
    changed = (_outer_paths(fx["root"]) ^ before_paths)
    assert all(p.startswith("_plans/") for p in changed), sorted(changed)


# --------------------------------------------------------------------------
# 3 — gate-diff isolation
# --------------------------------------------------------------------------
@HOST_GIT
def test_a_gate_for_one_plan_never_sees_the_other_plans_dirty_worktree(tmp_path,
                                                                       monkeypatch):
    """B's worktree is dirty with the very violation the gate looks for, in a
    tracked file AND an untracked one. A's gate must see neither — and the run
    is worth nothing without that plant, which is why it is there."""
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    _begin(b)
    monkeypatch.chdir(fx["root"])                    # verify runs at the project root

    _session_writes(a, {"src/plan_a.py": "A = 1\n"})
    _session_writes(b, {"src/plan_b.py": f"# {MARKER}\n",
                        "src/untouched.py": f"UNTOUCHED = 2  # {MARKER}\n"})

    gate = {"kind": "argv", "argv": [sys.executable, "tools/marker_gate.py"], "cwd": "."}
    a_cwd, b_cwd = ps.gate_cwd(a, "s01", gate), ps.gate_cwd(b, "s01", gate)
    assert a_cwd == ps.plan_worktree(a) and b_cwd == ps.plan_worktree(b)

    a_files = gf.changed_files(a_cwd)
    assert a_files == ["src/plan_a.py"], a_files
    assert sorted(gf.changed_files(b_cwd)) == ["src/plan_b.py", "src/untouched.py"]

    green = ssio.run_deploy_argv(gate["argv"], cwd=a_cwd, timeout=300)
    red = ssio.run_deploy_argv(gate["argv"], cwd=b_cwd, timeout=300)
    assert green["returncode"] == 0, green
    assert json.loads(green["stdout"])["findings"] == []
    assert red["returncode"] == 1, red                # KNOWN POSITIVE: it CAN fail
    assert json.loads(red["stdout"])["findings"] == ["src/plan_b.py", "src/untouched.py"]

    # And through the REAL verify loop, which is what production runs: A's
    # session closes green with B's violation still sitting in B's worktree.
    closed = _close_done(a)
    assert closed["gates"]["gate:marker-gate"]["action"] == "passed", closed
    assert closed["final"]["final_status"] == "DONE", closed
    assert MARKER in (Path(b_cwd) / "src" / "plan_b.py").read_text()


# --------------------------------------------------------------------------
# 5b — the untracked case
# --------------------------------------------------------------------------
@HOST_GIT
def test_a_session_whose_output_is_all_new_files_is_still_examined(tmp_path,
                                                                   monkeypatch):
    """§10.1's forbidden input, measured rather than asserted: a session whose
    output is ALL NEW FILES produces an empty `git diff HEAD`, so a gate reading
    it passes with exit 0 and zero findings before the commit stages the files
    unexamined. The production gate input sees them and goes RED."""
    fx = _build(tmp_path)
    a = fx["plans"]["plan-a"]
    _begin(a)
    monkeypatch.chdir(fx["root"])
    tree = _session_writes(a, {"src/plan_a.py": f"A = 1  # {MARKER}\n",
                               "src/pkg/__init__.py": "", "src/pkg/mod.py": "M = 1\n"})

    assert git(["diff", "HEAD", "--name-only"], tree, check=True)[1] == ""
    examined = gf.changed_files(str(tree))
    assert sorted(examined) == ["src/pkg/__init__.py", "src/pkg/mod.py", "src/plan_a.py"]

    res = ssio.run_deploy_argv([sys.executable, "tools/marker_gate.py"],
                               cwd=str(tree), timeout=300)
    report = json.loads(res["stdout"])
    assert res["returncode"] == 1, res
    assert report["files_examined"] == 3 and report["findings"] == ["src/plan_a.py"]


# --------------------------------------------------------------------------
# 4 + 7 — serial landing, and the operator's checkout
# --------------------------------------------------------------------------
@HOST_GIT
def test_landing_is_serial_and_the_second_plan_resyncs_over_the_first(tmp_path):
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    _begin(b)
    _session_writes(a, {"src/plan_a.py": "A = 1\n"})
    _session_writes(b, {"src/plan_b.py": "B = 1\n"})
    _ship_commit(a)
    _ship_commit(b)

    before_branch = git(["rev-parse", "--abbrev-ref", "HEAD"], fx["root"], check=True)[1]
    before_hash = _tree_hash(fx["root"])
    origin_before = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]

    landed_a = _ack_and_land(a)
    assert landed_a["action"] == "landed", landed_a
    after_a = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]
    assert after_a != origin_before

    landed_b = _ack_and_land(b)                      # B re-syncs over A's commits
    assert landed_b["action"] == "landed", landed_b
    after_b = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]

    files = git(["ls-tree", "-r", "--name-only", after_b], fx["origin"],
                check=True)[1].split()
    assert "src/plan_a.py" in files and "src/plan_b.py" in files
    assert "src/untouched.py" in files
    # The LAND merges specifically. B also carries its own `sync` merge of
    # origin/main into the plan branch (§4.1) — that one is the re-sync working,
    # not a second land, so it is named and excluded rather than counted.
    merges = git(["log", "--merges", "--format=%s", f"{origin_before}..{after_b}"],
                 fx["origin"], check=True)[1].splitlines()
    lands = sorted(m for m in merges if m.startswith("plan(plan-"))
    assert lands == ["plan(plan-a): land", "plan(plan-b): land"], merges
    assert any(m.startswith("Merge remote-tracking branch") for m in merges), merges

    # 7 — the operator's checkout is never moved. Both lands report it, and the
    # tree on disk says the same thing independently.
    assert landed_a["local_default_unchanged"] and landed_b["local_default_unchanged"]
    assert git(["rev-parse", "--abbrev-ref", "HEAD"], fx["root"],
               check=True)[1] == before_branch
    assert _tree_hash(fx["root"]) == before_hash


# --------------------------------------------------------------------------
# 5 — zero leftovers
# --------------------------------------------------------------------------
@HOST_GIT
def test_a_finished_run_leaves_no_branch_worktree_or_lock_behind(tmp_path):
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    _begin(b)
    _session_writes(a, {"src/plan_a.py": "A = 1\n"})
    _session_writes(b, {"src/plan_b.py": "B = 1\n"})
    _ship_commit(a)
    _ship_commit(b)
    for plan_dir in (a, b):
        assert _close_done(plan_dir)["final"]["final_status"] == "DONE"
    for plan_dir in (a, b):
        assert _ack_and_land(plan_dir)["cleanup"]["worktree"] == "removed"

    root = fx["root"]
    branches = git(["for-each-ref", "--format=%(refname)", "refs/heads/plan/"],
                   root, check=True)[1].split()
    assert branches == [], branches
    assert git(["for-each-ref", "--format=%(refname)", "refs/heads/plan/"],
               fx["origin"], check=True)[1].split() == []
    listing = git(["worktree", "list"], root, check=True)[1].splitlines()
    assert len(listing) == 1 and str(root) in listing[0], listing

    common = Path(git(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                      root, check=True)[1])
    locks = common / sl.REPO_LOCK_DIRNAME
    assert not locks.exists() or list(locks.iterdir()) == [], list(locks.iterdir())
    for plan_dir in (a, b):
        assert not (Path(plan_dir) / ".lock").exists()

    data = reg.collect(root, root / "_plans")
    assert data["branches"] == [] and [w["path"] for w in data["worktrees"]] == [str(root)]
    # Landed stays live until finish records `finished_at` (s07's registry rule).
    assert {p["plan"]: p["lifecycle"] for p in data["plans"]} == {
        "plan-a": "active", "plan-b": "active"}
    for plan_dir in (a, b):
        finish._save(finish.context(plan_dir), {"armed_at": "t", "finished_at": "t"})
    data = reg.collect(root, root / "_plans")
    assert {p["plan"]: p["lifecycle"] for p in data["plans"]} == {
        "plan-a": "done", "plan-b": "done"}


# --------------------------------------------------------------------------
# 5c — the second plan's branch predates the first plan's directory
# --------------------------------------------------------------------------
@HOST_GIT
def test_a_branch_cut_before_the_other_plans_directory_existed_lands_cleanly(tmp_path):
    """Probe 3's scenario 1, end to end: plan-b's branch is cut from a base that
    never saw `_plans/plan-a/`. §8.c stages only the plan's OWN directory, so
    plan-a's record is "a file B's base never saw, simply carried through" — it
    must neither conflict nor be deleted by B's merge."""
    fx = _build(tmp_path, versions=(("plan-a", 7), ("plan-b", 7)), defer=("plan-a",))
    b = fx["plans"]["plan-b"]
    _begin(b)                                        # branch cut here: no plan-a yet
    b_base = ps.pinned_base(b)
    assert git(["cat-file", "-e", f"{b_base}:_plans/plan-a"], fx["root"])[0] != 0

    a = _commit_deferred(fx, "plan-a")
    _begin(a)
    _session_writes(a, {"src/plan_a.py": "A = 1\n"})
    _session_writes(b, {"src/plan_b.py": "B = 1\n"})
    _ship_commit(a)
    _ship_commit(b)

    assert _ack_and_land(a)["action"] == "landed"
    landed_b = _ack_and_land(b)
    assert landed_b["action"] == "landed", landed_b

    head = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]
    files = git(["ls-tree", "-r", "--name-only", head], fx["origin"], check=True)[1].split()
    assert "_plans/plan-a/PLAN.html" in files and "_plans/plan-b/PLAN.html" in files
    assert "src/plan_a.py" in files and "src/plan_b.py" in files


# --------------------------------------------------------------------------
# 6 — the plan's own directory (contract §8)
# --------------------------------------------------------------------------
@HOST_GIT
def test_no_plan_branch_commit_touches_the_plans_directory_and_the_merge_is_a_noop(
        tmp_path):
    fx = _build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    _begin(a)
    _begin(b)
    _session_writes(a, {"src/plan_a.py": "A = 1\n"})
    _session_writes(b, {"src/plan_b.py": "B = 1\n"})
    _ship_commit(a)
    _ship_commit(b)

    # 8.b — no commit on either plan branch stages any `_plans/` path.
    for slug in ("plan-a", "plan-b"):
        for sha in _commits(fx["root"], fx["base"], f"plan/{slug}"):
            offending = [p for p in _commit_files(fx["root"], sha)
                         if p.startswith("_plans/")]
            assert offending == [], (slug, sha, offending)

    # 8.a — the LIVE state is the outer one. The worktree's copy is the frozen
    # base copy, and `run_state.json`/`_closeouts/` never appear in it at all.
    for plan_dir in (a, b):
        tree = Path(ps.plan_worktree(plan_dir))
        slug = Path(plan_dir).name
        assert (Path(plan_dir) / "run_state.json").is_file()
        assert list((Path(plan_dir) / "_closeouts").iterdir()) is not None
        assert not (tree / "_plans" / slug / "run_state.json").exists()
        assert (Path(plan_dir) / "PLAN.html").read_text() \
            != (tree / "_plans" / slug / "PLAN.html").read_text()

    # 8.c — the land MERGE is a no-op on `_plans/`; only the separate record
    # commit writes there, and only under the plan's own directory.
    before_main = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]
    assert _ack_and_land(a)["action"] == "landed"
    after_main = git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]
    merges = git(["rev-list", "--merges", f"{before_main}..{after_main}"],
                 fx["origin"], check=True)[1].split()
    assert len(merges) == 1
    merge_files = _commit_files(fx["origin"], merges[0])
    assert [p for p in merge_files if p.startswith("_plans/")] == [], merge_files
    # `before..after` also contains the plan branch's OWN commits, which the
    # merge brought along. `--not <merge>^2` drops everything reachable from the
    # plan branch tip, leaving exactly what the LAND worktree wrote.
    records = [sha for sha in git(["rev-list", f"{before_main}..{after_main}",
                                   "--not", f"{merges[0]}^2"],
                                  fx["origin"], check=True)[1].split()
               if sha not in merges]
    written = {p for sha in records for p in _commit_files(fx["origin"], sha)}
    assert written and all(p.startswith("_plans/plan-a/") for p in written), written


if __name__ == "__main__":                            # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
