"""§9 of the plan-isolation contract — hooks in a plan worktree, proven to run.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).
Split out of ``plan_worktree.py`` only to keep both files under this repo's
500-LOC file-size gate; the two are one mechanism and ``plan_worktree`` is the
sole caller.

    "A plan worktree where `pre-commit` silently exits 0 is strictly worse than
    today's collision, because it looks green."

Two documented mechanisms make that plausible — anthropics/claude-code#60620
(the harness writes an absolute worktree-scoped ``core.hooksPath`` that BEATS the
shared value) and lefthook#1398 (shared ``.git/hooks`` shims bake absolute paths).
Neither is re-measured here, and §9 is written so nothing depends on either being
true right now: the canary proves the hook path is honoured in THIS worktree, at
THIS moment, whatever the upstream bug status.
"""

import shutil
import tempfile
import time
import uuid
from pathlib import Path

import ship_locks as sl
from worktree import WorktreeError, git

CANARY_MARKER = "PLAN-HOOK-CANARY"
# Exits 1 and prints a token the caller matches, so a rejection that did NOT come
# from this hook is distinguishable from one that did (§9.3.3).
CANARY_HOOK = "#!/bin/sh\necho '{marker}' >&2\nexit 1\n"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# §9 — hooks: worktree-scoped path, canary, and validation of the final path
# --------------------------------------------------------------------------
def shared_hooks_dir(root):
    """The repo's REAL shared hooks directory, resolved absolute (§9.1).

    The shared config's own ``core.hooksPath`` wins when it is set — measured in
    this repo 2026-08-21: `git config --show-origin core.hooksPath` resolves from
    `.git/config` to `<repo>/githooks`, while `$GIT_COMMON_DIR/hooks` holds only
    `*.sample`. Pointing the plan worktree at the latter would silently disable
    the repo's real gitleaks + quality-ratchet gates — the exact "looks green"
    failure §9 exists to prevent."""
    rc, value, _ = git(["config", "--local", "--get", "core.hooksPath"], root)
    if rc == 0 and value:
        p = Path(value)
        return p if p.is_absolute() else (Path(root) / p).resolve()
    common = sl._git_common_dir(root)
    return (common / "hooks").resolve() if common else (Path(root) / ".git" / "hooks")


def _assert_hooks_path(root, path, expected):
    """§9.3 step 5 — the value we set is the one in effect, and the directory it
    names actually holds hooks. §9.2's "no foreign inherited value survives" is
    NOT re-checked here: the overwrite in `hook_gate` IS that control (probed:
    `git config --worktree core.hooksPath` REPLACES the existing value in
    config.worktree, '/foreign/hooks' -> final), so a line-scan for a surviving
    foreign value could never fire — a check that cannot fail is not coverage.
    The effective-value read below still catches a write that did not land."""
    rc, effective, _ = git(["config", "--get", "core.hooksPath"], path)
    if rc != 0 or effective != str(expected):
        raise WorktreeError(
            f"refusing {path}: core.hooksPath is {effective!r}, not the {expected!r} "
            "creation just set (contract §9.1)."
        )
    if not Path(expected).is_dir():
        raise WorktreeError(
            f"refusing {path}: core.hooksPath {expected} is not a directory. Every commit "
            "here would be hook-free while every command still exits 0 (contract §9.3.5)."
        )
    if not any(f.is_file() and f.stat().st_mode & 0o111 for f in Path(expected).iterdir()):
        raise WorktreeError(
            f"refusing {path}: core.hooksPath {expected} holds no executable hook. "
            "A hook-free worktree looks green and checks nothing (contract §9.3.5)."
        )


def _hook_canary(path, hook_body=None):
    """§9.3 — prove the hook path is honoured HERE, NOW. Returns a transcript.

    Never touches ``$GIT_COMMON_DIR/hooks``: that directory is shared by every
    worktree AND the operator, so a canary planted there can reject an unrelated
    commit and two concurrent `begin`s overwrite each other's canary. The temp
    directory is unique per call, which makes both impossible."""
    token = uuid.uuid4().hex[:12]
    marker = f"{CANARY_MARKER}-{token}"
    tmp = Path(tempfile.mkdtemp(prefix=f"hooks-canary-{token}-"))
    canary_file = Path(path) / f".plan-canary-{token}"
    # Sampled so the `finally` can put it BACK. Sampled here rather than taken
    # from the caller: undoing this write is this function's own duty, and a
    # caller refactor must not be able to silently drop it.
    prev_rc, prev, _ = git(["config", "--worktree", "--get", "core.hooksPath"], path)
    _, head_before, _ = git(["rev-parse", "HEAD"], path)
    lines = [f"# hook canary {token} @ {_now()}", f"worktree: {path}",
             f"temp hooks dir: {tmp}"]
    try:
        hook = tmp / "pre-commit"
        hook.write_text((hook_body or CANARY_HOOK).replace("{marker}", marker))
        # A non-executable hook is skipped SILENTLY by git, which looks exactly
        # like a passing canary. chmod is load-bearing (§9.3.1).
        hook.chmod(0o755)
        git(["config", "--worktree", "core.hooksPath", str(tmp)], path, check=True)
        # A REAL staged change (§9.3.3): an empty commit can exit non-zero for
        # having nothing to commit, which is not the hook firing, and
        # --allow-empty would make a different commit than the real hooks see.
        canary_file.write_text(f"{marker}\n")
        # `-f` is REQUIRED, not convenience. An allow-list `.gitignore` — one
        # that ignores `/*` and re-includes the directories it wants, which is
        # this repo's own shape (`.gitignore:6`) — ignores every new root-level
        # file, so a plain `git add` of the canary exits 1 with "paths are
        # ignored by one of your .gitignore files" and plan isolation fails
        # outright at `begin`. Measured by the s10 canary against a
        # real clone of this repo; every in-repo fixture missed it because a
        # hand-built fixture's `.gitignore` has no such rule. Forcing is safe
        # here and nowhere else: the path is one this function just created
        # under a uuid name, it is staged only to make the hook fire, and the
        # `finally` below unstages and deletes it whichever way the canary goes.
        git(["add", "-f", "--", canary_file.name], path, check=True)
        rc, out, err = git(["commit", "-m", f"hook canary {token}"], path)
        combined = f"{out}\n{err}".strip()
        lines += [f"$ git commit -m 'hook canary {token}'  -> rc={rc}", combined]
        if rc == 0:
            lines.append("VERDICT: REFUSE — the canary commit SUCCEEDED; hooks are not "
                         "running in this worktree (contract §9.4).")
            raise WorktreeError("\n".join(lines))
        if marker not in combined:
            lines.append(f"VERDICT: REFUSE — rejected, but without the marker {marker}. "
                         "That rejection did not come from the canary, so the hook path is "
                         "unproven (contract §9.3.3).")
            raise WorktreeError("\n".join(lines))
        lines.append(f"VERDICT: PASS — commit REJECTED and the marker {marker} is present; "
                     "the worktree-scoped hook path is honoured.")
        return {"ok": True, "token": token, "marker": marker, "rc": rc,
                "temp_hooks_dir": str(tmp), "transcript": "\n".join(lines),
                "checked_at": _now()}
    finally:
        # Deterministic: a refusal must not leave canary debris or a temp
        # hooksPath behind (§9.3.4). The hooksPath goes back FIRST, because the
        # temp directory below is about to be deleted: a worktree left pointing
        # at a deleted hooks directory runs NO hooks at all and every commit
        # still exits 0 — the "looks green, checks nothing" state §9 exists to
        # prevent, reached through the refusal path instead of the bug. The
        # success path overwrites this again with the final value.
        if prev_rc == 0 and prev:
            git(["config", "--worktree", "core.hooksPath", prev], path)
        else:
            git(["config", "--worktree", "--unset", "core.hooksPath"], path)
        git(["reset", "-q"], path)
        canary_file.unlink(missing_ok=True)
        _, head_after, _ = git(["rev-parse", "HEAD"], path)
        if head_before and head_after and head_after != head_before:
            git(["reset", "--hard", "-q", head_before], path)
        shutil.rmtree(tmp, ignore_errors=True)


def _enable_worktree_config(root):
    """§9.2's ``extensions.worktreeConfig``, WITH the migration git-worktree(1)
    requires: "in this file, the exception for core.bare and core.worktree is
    gone. If they exist in $GIT_DIR/config, you must move them to the
    config.worktree of the main worktree."

    Measured on git 2.48.1 in a scratch repo, because the consequence is not
    obvious from the sentence: with ``core.worktree`` in the shared config and
    the extension ON, ``git rev-parse --show-toplevel`` inside a LINKED worktree
    returns the MAIN checkout's path — so a plan session's commits land in the
    very tree it was isolated from. With the value moved to the main worktree's
    ``config.worktree``, both resolve correctly again.

    ``core.bare`` is moved only when it is ``true``, per the same page's bullet
    ("core.bare should not be shared if the value is core.bare=true"); a shared
    ``core.bare=false`` — what ``git init`` writes, and what this repo has — is
    already the value every worktree computes. ``core.sparseCheckout`` is a
    sharing RECOMMENDATION on that page, not part of the exception: it applies to
    every worktree with or without the extension, so moving it would CHANGE
    behaviour rather than preserve it. Left alone deliberately.

    Not undone afterwards, and that is a real cost the transcript records: the
    same page warns that "older Git versions will refuse to access repositories
    with this extension". Turning it back off would make the worktree-scoped
    ``core.hooksPath`` §9.1 depends on inert — hook-free and green."""
    rc, enabled, _ = git(["config", "--local", "--type=bool", "--get",
                          "extensions.worktreeConfig"], root)
    if rc == 0 and enabled == "true":
        return {"extension": "already-enabled", "migrated": {}}
    moved = {}
    for key, kind in (("core.worktree", "path"), ("core.bare", "bool")):
        rc, value, _ = git(["config", "--local", f"--type={kind}", "--get", key], root)
        if rc == 0 and value and not (key == "core.bare" and value != "true"):
            moved[key] = value
    if moved:
        common = sl._git_common_dir(root)
        if not common:
            raise WorktreeError(
                f"refusing to enable extensions.worktreeConfig in {root}: {sorted(moved)} "
                "must move to the main worktree's config.worktree first and the git common "
                "dir cannot be resolved (git-worktree(1), CONFIGURATION FILE)."
            )
        main_cfg = common / "config.worktree"
        # Written BEFORE the extension is enabled (inert until then) and removed
        # from the shared config only AFTER, so the main worktree never has an
        # instant where neither file supplies the value.
        for key, value in moved.items():
            git(["config", "--file", str(main_cfg), key, value], root, check=True)
    git(["config", "extensions.worktreeConfig", "true"], root, check=True)
    for key in moved:
        git(["config", "--local", "--unset", key], root, check=True)
    return {"extension": "enabled", "migrated": moved}


def hook_gate(root, path, *, hook_body=None):
    """The whole of §9 for one worktree, in the order the contract fixes."""
    # `--worktree` config is inert without this, so enabling it is part of
    # creation (§9.2). It is a SHARED-config write; the caller holds the `git:`
    # lease for the whole sequence (§4.1a, §11).
    worktree_config = _enable_worktree_config(root)
    # Sampled BEFORE the canary writes its own, because this is the only moment
    # the claude-code#60620 value is still visible: a worktree-scoped hooksPath
    # somebody else stamped, which BEATS the shared config. §9.1 overrides it
    # either way; recording it is what makes the override legible afterwards.
    rc_i, inherited, _ = git(["config", "--worktree", "--get", "core.hooksPath"], path)
    result = _hook_canary(path, hook_body)
    final = shared_hooks_dir(root)
    # §9.2 — this write is the control against an inherited foreign value:
    # `git config --worktree` REPLACES the existing entry (probed),
    # so after it no foreign hooksPath can survive in config.worktree. The
    # assert then proves the EFFECTIVE value end to end.
    git(["config", "--worktree", "core.hooksPath", str(final)], path, check=True)
    _assert_hooks_path(root, path, final)
    result["hooks_path"] = str(final)
    result["worktree_config"] = worktree_config
    result["inherited_hookspath"] = inherited if rc_i == 0 and inherited else None
    result["temp_hooks_dir_removed"] = not Path(result["temp_hooks_dir"]).exists()
    # §9.5 — the temp-dir canary proves the hook PATH is honoured. It says
    # nothing about whether the repo's REAL hooks do useful work once step 5
    # swaps to them (the lefthook#1398 class). No portable violation exists to
    # probe that with, so it is a NAMED RESIDUAL GAP, never silent coverage.
    result["real_hooks_proven"] = False
    result["residual_gap"] = (
        "contract §9.5: the real shared hooks at "
        f"{final} were NOT proven to check anything — only that the hook path is "
        "honoured. Do not read this PASS as covering them."
    )
    result["transcript"] += (
        f"\ninherited worktree-scoped core.hooksPath at creation: "
        f"{result['inherited_hookspath'] or 'none'}"
        f"\nfinal core.hooksPath (worktree-scoped): {final}"
        f"\nextensions.worktreeConfig: {worktree_config['extension']}"
        f" (migrated to the main worktree's config.worktree: "
        f"{worktree_config['migrated'] or 'nothing'})"
        f"\ntemp hooks dir removed: {result['temp_hooks_dir_removed']}"
        f"\nRESIDUAL GAP (§9.5): {result['residual_gap']}"
    )
    return result


