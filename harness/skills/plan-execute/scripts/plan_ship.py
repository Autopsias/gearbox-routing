"""ISO-02 — HOW an isolated plan's work LANDS on its own branch.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).
The other half is ``plan_scope.py`` (WHERE the work happens); this file is the
git mechanism and the shipping step descriptors that reach it.

**Why the git sub-steps become ARGV steps under isolation.**
``/commit-orchestrate`` is a Skill directive executed BY the orchestrating
conversation, so its cwd is the conversation's cwd and it takes no directory
argument (measured: its ``argument-hint`` carries none, and its staging line is a
bare ``git add -A``). An argv step is the only shape in the existing shipping
state machine that carries a ``cwd`` at all — ``shipping.ship_run_argv`` already
runs one with ``cwd=sd["cwd"]`` — AND the only one that can stage with the
explicit pathspec §8.b requires. Nothing about ``/commit-orchestrate`` changes:
an unisolated plan still gets the identical skill directive it always did.

``record_plan`` is §8.c's mechanism. s08 calls it inside the LAND worktree; it is
defined and exercised here because §8.b removes ``git add -A`` from the job of
committing the plan record, and a rule that removes the only writer without
naming the replacement leaves ``PLAN.html``, ``run_state.json`` and every closeout
permanently uncommitted.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import plan_scope as ps
import shipping_adapter as adapter
import worktree as wt
# §8.c lives in its own module (file-size rule); re-exported so every caller
# — land_push, land_steps, the CLI, the tests — is unchanged.
from plan_record import _names, record_plan  # noqa: F401

# §8.b — the ONE pathspec. A commit made from inside a plan worktree may not
# stage any path under `_plans/`: the worktree's copy was checked out at the
# pinned base and the orchestrator has been writing the OUTER one ever since, so
# staging it commits state that was already stale when the worktree was created.
# `.gitignore` cannot express this — the path is TRACKED, and ignoring a tracked
# path does nothing.
PLANS_DIR = ps.PLANS_DIR              # ONE definition, in plan_scope
EXCLUDE_PLANS = f":(exclude){PLANS_DIR}"

GIT_TIMEOUT = 900

# --------------------------------------------------------------------------
# Shipping steps (§2, §8.b) — argv, with an explicit cwd
# --------------------------------------------------------------------------
def _self_argv(*args):
    return [sys.executable, str(Path(__file__).resolve()), *args]


def git_step(plan_dir, sub, resource, session_id=None):
    """The shipping step descriptor for one git sub-step.

    NOT isolated -> the skill-kind descriptor, byte-identical to what
    ``compute_steps`` built before this module existed. Isolated -> an argv step
    running this file's CLI with ``cwd`` set to the plan worktree.

    Every git sub-step declares the credential-bearing env it needs — ``commit``
    too, because a repo that signs commits with an ssh-agent key, or keeps its
    git config outside ``$HOME``, fails at commit under the bare default set;
    only the gh tokens stay scoped to the sub-steps that talk to GitHub.
    ``run_deploy_argv`` builds an ALLOW-LISTED env
    (PATH/HOME/LANG/LC_ALL/TMPDIR/USER/SHELL) rather than inheriting
    ``os.environ``, so an ssh remote would otherwise fail with no agent socket.
    Naming the vars here keeps that opt-in explicit and auditable instead of
    widening the default for every argv step in the system.

    ``session_id`` becomes the commit's ``--message``: every session of an
    isolated plan commits to the ONE shared plan branch, so without it each
    session's commit reads identically and the flag is dead on the shipping path.
    """
    entry = adapter.git_adapter(sub)
    # A plan that CLAIMS a worktree which is gone must not quietly fall through to
    # the skill descriptor: that would commit the operator's shared checkout.
    ps.require_live(plan_dir)
    tree = ps.plan_worktree(plan_dir)
    branch = ps.plan_branch(plan_dir)
    if not tree or not branch:
        return {"name": sub, "kind": "skill", "resource": resource,
                **{k: entry[k] for k in ("skill", "args", "probe_flags", "timeout")}}
    slug = Path(plan_dir).resolve().name
    argv = {
        # Resolved HERE, in the orchestrator's cwd: the argv step re-invokes this
        # file with cwd = the plan worktree, where a relative plan dir points at
        # the FROZEN copy (or at nothing, when the plan dir is not yet committed).
        "commit": _self_argv("commit", "--worktree", tree,
                             "--plan-dir", str(Path(plan_dir).resolve()),
                             "--message",
                             f"feat({slug}): {session_id or 'plan'} session work"),
        "push": _self_argv("push", "--worktree", tree, "--branch", branch),
        "pr": _self_argv("pr", "--worktree", tree, "--branch", branch,
                         "--base", _default_branch(plan_dir)),
    }[sub]
    env = ["SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_SSH_COMMAND",
           "GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME"]
    if sub in ("push", "pr"):
        env += ["GH_TOKEN", "GITHUB_TOKEN"]
    return {"name": sub, "kind": "argv", "resource": resource, "argv": argv,
            "cwd": tree, "timeout": entry["timeout"], "failure_mode": "fail-halt",
            "env_allowlist": env}


def _default_branch(plan_dir):
    return ps.claim(plan_dir).get("default_branch") or "main"


# --------------------------------------------------------------------------
# The git operations themselves
# --------------------------------------------------------------------------
def _git(args, cwd, **kw):
    return wt.git(args, cwd, timeout=GIT_TIMEOUT, **kw)




def _nothing_staged(tree):
    """True when the index holds no change against HEAD. `--quiet` exits 0 for
    "no difference", 1 for "there is one" — so this is git's own answer, not a
    reading of git's prose."""
    return _git(["diff", "--cached", "--quiet"], tree)[0] == 0


def plans_paths(tree, base, head="HEAD", default=None):
    """Paths under ``_plans/`` carried by ``base..head`` — §8.b's assertion.

    With ``default`` (the default branch name), a path counts only when ``head``
    ALSO differs from the default-branch commit it last merged —
    ``merge-base(head, origin/<default>)``. A plan branch that merges main picks
    up main's own `_plans/` edits (harvest commits); the tree diff from the base
    counted those as the plan's, and refused the ship. A path the
    plan itself changed still differs from that merge-base, including one main
    ALSO changed, so it is still refused. The merge-base only moves when
    ``head`` merges again — never because main moved — so a retry gets the same
    answer.

    Bounded at the PINNED BASE rather than at one commit, because the refusal it
    feeds must give the SAME answer on a retry as on the attempt that failed. A
    single-commit check (`git show HEAD`) stops seeing the offending commit the
    moment it is no longer the one just made — which is exactly what a resumed
    ship looks like — so the refusal would fire once and then wave the bad commit
    through on the retry that follows it.

    Read from committed history, never from the staging area: the staging area is
    empty by the time the commit exists, so a check pointed there would return
    "clean" because its input was empty rather than because the rule held. A git
    failure RAISES — an unreadable range must not read as an all-clear.
    """
    names = _names(["diff", "--name-only", f"{base}..{head}"], tree,
                   f"{base}..{head} to assert §8.b")
    swept = [p for p in names if Path(p).parts[:1] == (PLANS_DIR,)]
    if swept and default:
        mb = _merged_default(tree, head, default)
        ours = set(_names(["diff", "--name-only", f"{mb}..{head}"],
                          tree, f"{mb}..{head} to assert §8.b"))
        swept = [p for p in swept if p in ours]
    return swept


def _merged_default(tree, head, default):
    """The newest default-branch commit ``head`` contains. `origin/` first: the
    local branch can lag what the plan merged. A git failure RAISES."""
    for ref in (f"origin/{default}", default):
        rc, mb, _ = _git(["merge-base", head, ref], tree)
        if rc == 0 and mb:
            return mb
    raise wt.WorktreeError(
        f"cannot find the merge-base of {head} and {default} in {tree} "
        "to assert §8.b")


def _assert_no_plans_paths(tree, base, default=None):
    """§8.b, asserted over the whole ``base..HEAD`` range — see ``plans_paths``."""
    swept = plans_paths(tree, base, default=default)
    if not swept:
        return
    raise wt.WorktreeError(
        f"{len(swept)} path(s) under {PLANS_DIR}/ are carried by {base[:12]}..HEAD in "
        f"{tree} ({swept[:5]}). The plan directory inside a worktree is a FROZEN copy "
        "of the pinned base while the orchestrator writes the outer one (contract "
        "§8.a/§8.b), so committing it commits state that was already stale. Undo those "
        "commits in that worktree (`git reset --soft`) and re-run. This refusal is "
        "over the RANGE, so it fires again on every retry until it is actually fixed."
    )


def _unstaged_outside_plans(tree):
    """Paths OUTSIDE `_plans/` that `git add` left behind, from the INDEX.

    `-z` is not decoration: under the default `core.quotePath` a non-ASCII path
    comes back as an escaped, DOUBLE-QUOTED token, and every prefix comparison
    here silently flips the wrong way — the same measurement `_names` records.
    `strip=False` for the same reason `worktree.git`'s docstring gives: the two
    status columns may legitimately be blank (`" M path"`), and stripping shifts
    every entry left by one so each path loses its first character.
    """
    rc, out, err = _git(["status", "--porcelain", "--untracked-files=all", "-z"],
                        tree, strip=False)
    if rc != 0:
        raise wt.WorktreeError(
            f"could not read the index in {tree} to judge staging (rc={rc}): "
            f"{(err or out).strip()}")
    left, entries = [], [e for e in out.split("\0") if e]
    skip_next = False
    for entry in entries:
        if skip_next:                      # a rename's second NUL-separated path
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            skip_next = True
        if path == PLANS_DIR or path.startswith(PLANS_DIR + "/"):
            continue
        if xy == "??" or xy[1] != " ":     # untracked, or dirty in the WORKTREE
            left.append(path)
    return left


def _stage(tree):
    """Stage everything outside `_plans/`, tolerating git's ignored-path exit 1.

    `:(exclude)_plans` NAMES an ignored path, so in a repo whose `.gitignore`
    covers `_plans/` git warns and exits 1 while staging every file correctly.
    Measured, git 2.48.1:

        git add -A -- . ':(exclude)_plans'   exit 1   staged: real.py
        git add -A -- .                      exit 0   staged: real.py
        git add -A                           exit 0   staged: real.py

    Judging that by the exit code halted four sessions of one overnight run
    on commits that had already succeeded. Neither `--ignore-errors` nor
    `advice.addIgnoredFile=false` changes the exit code, and simply DROPPING
    the exclusion is wrong: with a file under `_plans/` force-added,
    `git check-ignore _plans` reports NOT ignored and the add stages
    `_plans/<slug>/record.json` into the session commit — the §8.b breach this
    pathspec exists to prevent.

    So keep the command and stop asking it for the verdict. ASK THE INDEX,
    NEVER THE MESSAGE — the same rule `commit` already states three lines
    below, and locale-proof, because git's message text is translated.
    """
    rc, _, err = _git(["add", "-A", "--", ".", EXCLUDE_PLANS], tree)
    if rc == 0:
        return
    leftover = _unstaged_outside_plans(tree)
    if leftover:
        raise wt.WorktreeError(
            f"git add failed in {tree} (rc={rc}) and left {len(leftover)} "
            f"path(s) unstaged: {leftover[:5]}\n{(err or '').strip()}")


def stage(tree):
    """`_stage`, then True when something is staged. For `worktree.commit_member`:
    a member tree dirty ONLY under `_plans/` has nothing to commit."""
    _stage(tree)
    return not _nothing_staged(tree)


def commit(tree, message, plan_dir):
    """Stage and commit the plan worktree ON ITS OWN BRANCH, §8.b-safe.

    Returns ``{"status": ..., "sha": ..., "files": [...]}``. ``status`` is
    ``committed`` or ``nothing-to-commit``; anything else raises.

    The hook-rewrote-the-tree retry is REUSED from ``worktree.py`` (patched
    2026-08-21, 81f7f5d/ba82fe5) rather than re-derived: a formatting pre-commit
    hook fails the first commit and fixes the files in place, and the standard
    response is to re-stage and commit again.
    """
    tree = str(tree)
    base = ps.pinned_base(plan_dir)
    if not base:
        raise wt.WorktreeError(
            f"refusing to commit in {tree}: no pinned base is recorded for "
            f"{plan_dir}, and §8.b's assertion is bounded at `base..HEAD`. Without "
            "the bound it would either report nothing or report the base commit's "
            "own unrelated history.")
    default = _default_branch(plan_dir)
    _assert_no_plans_paths(tree, base, default)     # BEFORE: a retry sees the same refusal
    _stage(tree)
    if _nothing_staged(tree):
        # ASK THE INDEX, NEVER THE MESSAGE. `git commit` has three refusals here
        # and only one of them says "nothing to commit" (measured):
        # a tree dirty ONLY under `_plans/` — which the EXCLUDE_PLANS pathspec
        # stages nothing from, i.e. the §8.a case this ships for — gets "no
        # changes added to commit" (tracked) or "nothing added to commit but
        # untracked files present" (new). Both fell through to `commit failed`
        # and HALTED the plan, after `_hooks_rewrote_the_tree` saw the unstaged
        # dirt as a hook's doing and paid for a pointless second attempt.
        return {"status": "nothing-to-commit", "sha": None, "files": []}
    rc, out, err = _git(["commit", "-m", message], tree)
    if rc != 0 and wt._hooks_rewrote_the_tree(tree):
        _stage(tree)
        rc, out, err = _git(["commit", "-m", message], tree)
    if rc != 0:
        # Unstage: a manual `git commit` in this worktree would otherwise sweep
        # the session's files in. The working tree keeps every edit.
        _git(["reset", "-q"], tree)
        raise wt.WorktreeError(
            f"commit failed in {tree}: {(err or out).strip()}\n"
            "The index was reset, so nothing is staged; the working tree is unchanged.")
    _, sha, _ = _git(["rev-parse", "HEAD"], tree, check=True)
    _assert_no_plans_paths(tree, base, default)     # AFTER: this commit did not add one
    return {"status": "committed", "sha": sha, "plan_dir": str(plan_dir),
            "files": _names(["show", "--name-only", "--format=", sha], tree, sha)}


def has_upstream(tree, branch):
    return _git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"], tree)[0] == 0


def push_remote(tree):
    """The remote a FIRST push sets upstream to — never the literal ``origin``.

    Same rule as ``land_state.context``: ``origin`` when it exists, otherwise the
    SOLE remote. Hardcoding ``origin`` failed the first push outright in a repo
    whose single remote is named anything else, and the two halves of one land
    disagreeing about the push target is worse than either answer. Several
    remotes with no ``origin`` REFUSES rather than guesses, for the reason
    ``land._preflight`` parks that case: there is no single ref the push can mean.
    """
    remotes = _git(["remote"], str(tree))[1].split()
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    if not remotes:
        raise wt.WorktreeError(
            f"{tree} has no git remote, so the plan branch cannot be pushed. Add one, "
            "or run the plan with shipping's push step disabled.")
    raise wt.WorktreeError(
        f"{tree} has {len(remotes)} remotes ({', '.join(remotes)}) and none is `origin`, "
        "so there is no single push target. Name the intended one:\n"
        "  git remote rename <the-one-you-push-to> origin")


def push(tree, branch):
    """§2.2 — first push sets upstream, later pushes are plain, NEVER ``--force``.

    A non-fast-forward rejection is a PARK with the reason, not a retry: the only
    way a plan branch diverges is that something outside this plan wrote it, and
    §1.5 says that is not ours to overwrite.
    """
    tree = str(tree)
    args = (["push"] if has_upstream(tree, branch)
            else ["push", "-u", push_remote(tree), branch])
    rc, out, err = _git(args, tree)
    text = (out + "\n" + err).strip()
    if rc == 0:
        return {"status": "pushed", "argv": args, "output": adapter.redact(text)}
    # Match the NON-FAST-FORWARD case specifically. A bare "rejected" would also
    # catch `! [remote rejected]` (protected branch, pre-receive hook) and report
    # it as a §1.5 ownership violation that never happened; that case falls
    # through to the generic failure below and reports itself as what it is.
    if "non-fast-forward" in text or "fetch first" in text:
        raise wt.WorktreeError(
            f"push of {branch} was REJECTED as non-fast-forward. Something outside this "
            "plan wrote the branch; §1.5 forbids adopting or force-updating a ref this "
            f"plan does not own. Park and resolve by hand.\n{adapter.redact(text)}"
        )
    raise wt.WorktreeError(f"push of {branch} failed: {adapter.redact(text)}")


def open_pr(tree, branch, base):
    """§2 — a declared PR step targets the PLAN branch, not whatever branch the
    orchestrating conversation happens to be on.

    ``/pr create`` derives its branch from `git branch --show-current` in the
    conversation's cwd and takes no directory or head argument (measured in
    ``commands/pr.md``), so under isolation the step calls ``gh`` directly with an
    explicit ``--head``. A missing ``gh`` fails the step loudly.
    """
    rc, out, err = _run(["gh", "pr", "create", "--head", branch, "--base", base, "--fill"],
                        tree)
    if rc != 0:
        # The ONE branch is shared by every session of the plan, so the second
        # session declaring commit-push-pr finds the first session's PR — which
        # already covers the newly pushed commits. That is the shipped state,
        # not a failure; this is the only git sub-step that is not naturally
        # idempotent (commit says nothing-to-commit, push is plain).
        if "already exists" in (out + "\n" + err):
            return {"status": "pr-exists", "head": branch, "base": base,
                    "output": adapter.redact((err or out).strip())}
        raise wt.WorktreeError(
            f"gh pr create --head {branch} --base {base} failed in {tree}: "
            f"{adapter.redact((err or out).strip())}")
    return {"status": "pr-opened", "head": branch, "base": base,
            "output": adapter.redact(out.strip())}


def _run(argv, cwd):
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           timeout=GIT_TIMEOUT, check=False)
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        return 124, "", str(e)
    return p.returncode, p.stdout or "", p.stderr or ""


# --------------------------------------------------------------------------
# CLI — the argv shipping steps land here
# --------------------------------------------------------------------------
# subcommand -> (required flags, optional flags). Table-driven so the parser
# stays one screen and a new step is one row.
_CLI = {
    "commit": (["--worktree", "--plan-dir"], ["--message"]),
    "push": (["--worktree", "--branch"], []),
    "pr": (["--worktree", "--branch"], ["--base"]),
    "record-plan": (["--target", "--plan-dir"], ["--message"]),
}


def _cli(argv=None):
    p = argparse.ArgumentParser(prog="plan_ship", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, (required, optional) in _CLI.items():
        sp = sub.add_parser(name)
        for flag in required:
            sp.add_argument(flag, required=True)
        for flag in optional:
            sp.add_argument(flag, default="main" if flag == "--base" else None)
    a = p.parse_args(argv)
    if a.cmd == "commit":
        slug = Path(a.plan_dir).resolve().name
        return commit(a.worktree, a.message or f"feat({slug}): plan session work", a.plan_dir)
    if a.cmd == "push":
        return push(a.worktree, a.branch)
    if a.cmd == "pr":
        return open_pr(a.worktree, a.branch, a.base)
    return record_plan(a.target, a.plan_dir, a.message)


def main(argv=None):
    try:
        print(json.dumps(_cli(argv), indent=2))
    except wt.WorktreeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
