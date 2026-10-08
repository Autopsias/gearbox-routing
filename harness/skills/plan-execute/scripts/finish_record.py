"""FIN-01 / FIN-02 — finish's two WRITING steps: the record push and the checkout.

Authority: ``../references/finish-contract.md`` (frozen 2026-10-01), steps 1 and 3.
``finish.py`` owns the order, the lease and the state file; this module only does
the git work, and every write it makes is gated on a check made first.

Step 1 (``record``) never touches the operator's checkout. It builds the commit in
a DETACHED temporary worktree at the pinned ``origin_sha``, stages only paths under
``_plans/<slug>/``, this plan's own ``_plans_index.md`` row and missing ignore
lines, and pushes with a compare-and-swap against the tip it was built on.

Step 3 (``checkout``) restores a dirty path in the owner of the default branch only
when its bytes already equal ``target_sha``'s copy, so a restore can lose nothing.
"""

import difflib
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import land_brief as lb
import worktree as wt
from finish_paths import Linked, Park, _exe, lstat_inside, read_inside, refuse_special  # noqa: F401
from land_state import MAX_RESYNC, is_stale, rev
from plan_record import RUNTIME_LOCAL
from worktree import git

INDEX = "_plans_index.md"
GITIGNORE = ".gitignore"
NO_IGNORE = "--ignore-submodules=none"   # a gitlink is never hidden by config
_BUILD_PLAN = Path(__file__).resolve().parents[2] / "plan-builder" / "scripts"


def _build_plan():
    """plan-builder's module, for its ``GITIGNORE_LINES`` (reused, never re-typed)."""
    if str(_BUILD_PLAN) not in sys.path:
        sys.path.insert(0, str(_BUILD_PLAN))
    import build_plan  # noqa: PLC0415
    return build_plan


def marker(slug):
    return f"](_plans/{slug}/"


def blob(cwd, sha, path):
    """``<sha>:<path>`` as bytes, or None when the commit does not have it."""
    p = subprocess.run(["git", "cat-file", "blob", f"{sha}:{path}"], cwd=str(cwd),
                       capture_output=True, timeout=120)
    return p.stdout if p.returncode == 0 else None


def is_runtime_local(rel_in_plan):
    return any(rel_in_plan == r or (r.endswith("/") and rel_in_plan.startswith(r))
               for r in RUNTIME_LOCAL)


def ancestor(cwd, a, b):
    return git(["merge-base", "--is-ancestor", a, b], cwd)[0] == 0



# --------------------------------------------------------------------------
# Step 1 — the record
# --------------------------------------------------------------------------
def apply_index(origin_text, ours, mark):
    """Origin's index with this plan's rows replaced by ``ours`` (or appended)."""
    if not ours:
        return origin_text
    ours = [ln if ln.endswith("\n") else ln + "\n" for ln in ours]
    lines = (origin_text or "").splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines) if mark in ln]
    if hits:
        rest = [ln for i, ln in enumerate(lines) if i > hits[0] and i not in hits]
        return "".join(lines[:hits[0]] + ours + rest)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join(lines + ours)


def _other_lines(text, mark):
    return [ln for ln in (text or "").splitlines() if mark not in ln]


def _append_only(old, new):
    """Refuse a ``.gitignore`` change that is not ``old``'s exact bytes plus only
    the header and ``GITIGNORE_LINES``."""
    bp = _build_plan()
    allowed = {"", bp.GITIGNORE_HEADER, *bp.GITIGNORE_LINES}
    if not new.startswith(old) or any(
            ln not in allowed for ln in new[len(old):].decode("utf-8", "replace").splitlines()):
        raise Park("outside-record", "the .gitignore change is not a pure append", [GITIGNORE])


def _gitignore(tmp):
    """Append the missing plan-builder ignore lines to origin's exact bytes through
    plan-builder's own writer, so both produce the same file."""
    old = read_inside(tmp, GITIGNORE) or b""          # a linked .gitignore parks
    new = _build_plan().gitignore_append(old)
    if new is None:
        return False
    _append_only(old, new)
    (Path(tmp) / GITIGNORE).write_bytes(new)
    return True


def _ignored(tmp, paths, *flags):
    """Which of ``paths`` git ignores in ``tmp`` (origin's rules plus the appended
    lines). Tracked paths are reported only when ``flags`` carries ``--no-index``."""
    if not paths:
        return set()
    p = subprocess.run(["git", "check-ignore", *flags, "-z", "--stdin"], cwd=str(tmp),
                       input="\0".join(paths) + "\0", capture_output=True, text=True,
                       timeout=300)
    if p.returncode not in (0, 1):
        raise Park("push-failed", f"git check-ignore failed: {p.stderr.strip()}")
    return {x for x in p.stdout.split("\0") if x}


def _staged(tmp, rel, *against):
    """The index against HEAD (or the ``against`` commits), refused when any path
    is outside the record."""
    rc, out, err = git(["diff", *(against or ["--cached"]), "--no-renames", NO_IGNORE,
                        "--name-status", "-z"], tmp, strip=False)
    if rc != 0:
        raise Park("push-failed", f"cannot read the staged record: {err.strip()}")
    tokens = [t for t in out.split("\0") if t]
    staged = dict(zip(tokens[1::2], tokens[0::2]))
    outside = [p for p, s in staged.items()
               if not p.startswith(rel + "/") and (s == "D" or p not in (INDEX, GITIGNORE))]
    if outside:
        raise Park("outside-record", "staged paths outside the record", outside)
    return staged


def _copy_record(tmp, plan_dir, files, ignored):
    """Copy each non-ignored record file into ``tmp``; return the paths written."""
    touched = []
    for path, inner in files.items():
        if path in ignored:
            continue
        try:
            live, exe = read_inside(plan_dir, inner), _exe(plan_dir, inner)
        except Linked as e:
            raise e.under(path[:-len(inner) - 1]) from None   # repo-relative
        dst = Path(tmp) / path
        if read_inside(tmp, path) != live or _exe(tmp, path) != exe:
            dst.parent.mkdir(parents=True, exist_ok=True)   # parents proven real or absent
            dst.write_bytes(live)
            os.chmod(dst, 0o755 if exe else 0o644)
            touched.append(path)
    return touched


def stage_record(tmp, plan_dir, root, rel):
    """Stage the record in ``tmp``; return ``{path: bytes}`` of what is staged."""
    slug = Path(rel).name
    touched = [GITIGNORE] if _gitignore(tmp) else []
    files = {}
    refuse_special(plan_dir, is_runtime_local)
    for f in sorted(Path(plan_dir).rglob("*")):
        inner = f.relative_to(plan_dir).as_posix()
        if (f.is_file() or f.is_symlink()) and not is_runtime_local(inner):
            files[f"{rel}/{inner}"] = inner          # a link is caught below, never skipped
    for path in files:
        lstat_inside(tmp, path)       # a link in origin's tree parks before git reads it
    ignored = _ignored(tmp, list(files), "--no-index")   # tracked runtime too
    touched += _copy_record(tmp, plan_dir, files, ignored)
    # A tracked record file gone from the plan dir is removed; runtime-local and
    # ignored files are never part of the record, so their absence changes nothing.
    tracked = git(["ls-files", "-z", "--", rel + "/"], tmp, strip=False)[1].split("\0")
    gone = [p for p in tracked if p and p not in files
            and not is_runtime_local(p[len(rel) + 1:])]
    skip = _ignored(tmp, gone, "--no-index")
    gone = [p for p in gone if p not in skip]
    for p in gone:
        lstat_inside(tmp, p)                          # a link on the path parks
    if gone:
        git(["rm", "--cached", "-q", "--", *gone], tmp, check=True)
        for p in gone:
            (Path(tmp) / p).unlink(missing_ok=True)
    mark = marker(slug)
    local = (read_inside(root, INDEX) or b"").decode("utf-8")
    ours = [ln for ln in local.splitlines(keepends=True) if mark in ln]
    idx = Path(tmp) / INDEX
    before = read_inside(tmp, INDEX)
    before = None if before is None else before.decode("utf-8")
    after = apply_index(before, ours, mark)
    if after != before:
        if _other_lines(before, mark) != _other_lines(after, mark):
            raise Park("outside-record", "the index change touches another plan's line",
                       [INDEX])
        idx.write_bytes(after.encode("utf-8"))
        touched.append(INDEX)
    if touched:
        git(["add", "--", *touched], tmp, check=True)
    return _bytes(tmp, _staged(tmp, rel))


def _bytes(tmp, staged):
    """``{path: bytes}`` of the staged paths as the temp worktree holds them."""
    return {p: read_inside(tmp, p) if s != "D" else None for p, s in staged.items()}


def _proved_tree(tmp, rel, parent, staged):
    """Write the staged tree and prove the BYTES git stored: a clean filter
    (gitattributes) rewrites files inside ``git add``, after every check above.
    Another plan's index row, the append-only ``.gitignore`` and each record file
    are re-checked on the tree's own blobs; any difference parks."""
    rc, tree, err = git(["write-tree"], tmp)
    if rc != 0:
        raise Park("push-failed", f"cannot write the staged record tree: {err.strip()}")
    mark = marker(Path(rel).name)

    def text(sha):
        return (blob(tmp, sha, INDEX) or b"").decode("utf-8", "replace")
    if INDEX in staged and _other_lines(text(parent), mark) != _other_lines(text(tree), mark):
        raise Park("outside-record", "the staged index touches another plan's line", [INDEX])
    if GITIGNORE in staged:
        _append_only(blob(tmp, parent, GITIGNORE) or b"", blob(tmp, tree, GITIGNORE) or b"")
    changed = sorted(p for p, want in staged.items() if blob(tmp, tree, p) != want)
    if changed:
        raise Park("outside-record", "git add stored bytes finish did not check "
                   "(a clean filter?)", changed)
    return tree


def _commit(tmp, rel, message, parent, staged):
    """Commit the staged record; return the commit's sha. ONE structural rule
    replaces every per-hook check: the commit must be exactly one commit on
    ``parent`` whose tree is the tree finish staged and proved. Anything a hook
    changes (a file, a deletion, a link, a gitlink, an extra commit) parks, and a
    hook that fails parks ``push-failed``: it is never retried."""
    tree = _proved_tree(tmp, rel, parent, staged)
    rc, out, err = git(["commit", "-q", "-m", message], tmp)
    if rc != 0:
        raise Park("push-failed", f"the record commit failed: {(err or out).strip()}")
    count = git(["rev-list", "--count", f"{parent}..HEAD"], tmp)[1]
    if count != "1" or rev(tmp, "HEAD^") != parent or rev(tmp, "HEAD^2"):
        raise Park("outside-record", "a commit hook changed the record commit: it is "
                   f"not one commit on {parent[:12]} ({count or '?'} commits)")
    if rev(tmp, "HEAD^{tree}") != tree:
        diff = git(["diff", "--name-only", "--no-renames", NO_IGNORE, "-z", tree,
                    "HEAD^{tree}"], tmp, strip=False)[1]
        raise Park("outside-record", "a commit hook changed the record commit: its tree "
                   "is not the tree finish staged", [d for d in diff.split("\0") if d])
    return rev(tmp, "HEAD")


def _temp_tree(root, slug, sha):
    wt._exclude_worktree_dir(root)
    path = Path(root) / wt.WORKTREE_DIRNAME / f"{slug}__finish-{uuid.uuid4().hex[:8]}"
    git(["worktree", "add", "--detach", str(path), sha], root, check=True)
    return path


def _drop_temp(root, path):
    # Only ever our own `<slug>__finish-<token>` directory; `rmtree` + `prune`
    # because `worktree remove` would need `--force`, which finish never uses.
    if path.parent.name == wt.WORKTREE_DIRNAME and "__finish-" in path.name:
        shutil.rmtree(path, ignore_errors=True)
    git(["worktree", "prune"], root)


def record(plan_dir, ctx, origin_sha, *, apply, lease_ok):
    """Step 1. Returns ``(result, target_sha, staged)``; ``staged`` is the would-be
    commit's ``{path: bytes}`` in report-only mode, else ``{}``."""
    root, slug, remote, default = ctx["root"], ctx["slug"], ctx["remote"], ctx["default"]
    res = {"status": None, "park_reason": None, "sha": None, "paths": [], "outside_record": []}
    if not remote:
        return {**res, "status": "no-remote"}, None, {}
    plan_dir = Path(plan_dir).resolve()
    rel = plan_dir.relative_to(Path(root).resolve()).as_posix()
    expected = origin_sha
    for attempt in range(1, MAX_RESYNC + 1):
        tmp = None
        try:
            tmp = _temp_tree(root, slug, expected)
            staged = stage_record(tmp, plan_dir, root, rel)
            res["paths"] = sorted(staged)
            if not staged:
                return {**res, "status": "already-recorded"}, expected, {}
            if not apply:
                return {**res, "status": "would-push"}, expected, staged
            head = _commit(tmp, rel, f"plan({slug}): finish record", expected,
                           staged)
            if not lease_ok():
                raise Park("lease-lost", f"this plan no longer holds {ctx['resource']}")
            rc, out, err = git(["push", f"--force-with-lease={default}:{expected}", remote,
                                f"HEAD:refs/heads/{default}"], tmp, timeout=900)
            text = (err or "") + (out or "")
        except Park as p:
            return {**res, "status": "parked", "park_reason": p.reason,
                    "outside_record": p.paths, "detail": p.detail}, None, {}
        except wt.WorktreeError as e:
            return {**res, "status": "parked", "park_reason": "push-failed",
                    "detail": str(e)}, None, {}
        finally:
            if tmp is not None:
                _drop_temp(root, tmp)
        git(["fetch", remote, default], root, timeout=600)
        if rc == 0:
            if not ancestor(root, head, f"refs/remotes/{remote}/{default}"):
                return {**res, "status": "parked", "park_reason": "push-failed",
                        "detail": f"push exited 0 but {head[:12]} is not on the branch"}, None, {}
            return {**res, "status": "pushed", "sha": head}, head, {}
        fresh = rev(root, f"refs/remotes/{remote}/{default}")
        if not is_stale(text) or attempt == MAX_RESYNC or not fresh \
                or not ancestor(root, origin_sha, fresh):
            reason = "resync-exhausted" if is_stale(text) and attempt == MAX_RESYNC \
                else "push-failed"
            return {**res, "status": "parked", "park_reason": reason,
                    "detail": text[:2000]}, None, {}
        expected = fresh
    return {**res, "status": "parked", "park_reason": "resync-exhausted"}, None, {}


# --------------------------------------------------------------------------
# Step 3 — the checkout that owns the default branch
# --------------------------------------------------------------------------
def _porcelain(owner):
    rc, out, err = git(["status", "--porcelain", "-z", "-uall", "--no-renames", NO_IGNORE], owner,
                       strip=False)
    if rc != 0:
        raise wt.WorktreeError(f"git status failed in {owner}: {err.strip()}")
    tokens, entries, i = out.split("\0"), [], 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) > 3:
            entries.append((tok[:2], tok[3:]))
            if "R" in tok[:2] or "C" in tok[:2]:
                i += 1                      # `-z` puts the rename source next
    return entries


def _plan_of(path):
    parts = path.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "_plans" else None


def _index_plans(work, target, slug):
    """Other plans whose index rows differ between the owner and the target."""
    found = set()
    old = (work or b"").decode("utf-8", "replace").splitlines()
    new = (target or b"").decode("utf-8", "replace").splitlines()
    for d in difflib.ndiff(old, new):
        if d[:2] in ("+ ", "- "):
            found.update(m for m in re.findall(r"\]\(_plans/([^/)]+)/", d) if m != slug)
    return sorted(found)


def _same_mode(owner, sha, path):
    """The working file's executable bit equals ``sha``'s: the one mode bit git
    records (100755 vs 100644). An unstaged chmod with unchanged bytes must block a
    restore like a byte edit does, or ``git checkout --`` silently resets it."""
    if lstat_inside(owner, path) is None:   # raises Linked on any link on the path
        return True                          # absence is the bytes check's to judge
    mode = git(["ls-tree", sha, "--", path], owner)[1].split(" ", 1)[0]
    # A regular file where the target has a link (120000) or a gitlink is a type
    # change: restoring it would replace the file. Never the same.
    return mode not in ("120000", "160000") and _exe(owner, path) == (mode == "100755")


def _old_amend(owner, work, want):
    """Is ``work`` the uncommitted ``.gitignore`` the OLD plan-builder
    ``amend_gitignore`` wrote over HEAD's? That writer made CRLF into LF, stripped
    the file's end and appended its header and lines. Restorable only when the
    target is likewise an append to HEAD that holds every line ``work`` added."""
    head, want = blob(owner, "HEAD", GITIGNORE) or b"", want or b""

    def lf(b):
        return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    def added(new):            # the lines appended to HEAD's, or None if not an append
        for base in (lf(head), lf(head).rstrip()):   # finish's writer, the old writer
            try:
                _append_only(base, lf(new))
            except Park:
                continue
            return set(lf(new)[len(base):].decode("utf-8", "replace").splitlines()) - {""}
        return None
    mine = added(work or b"")
    return (mine is not None and added(want) is not None and want != head
            and _build_plan().GITIGNORE_HEADER in mine
            and mine <= set(lf(want).decode("utf-8", "replace").splitlines()))


def _restorable(owner, target_sha, path, work, want):
    """Same bytes and mode as the target, or (``.gitignore`` only) the old
    plan-builder amendment of HEAD's copy that the target already carries."""
    if work != want and not (path == GITIGNORE and _old_amend(owner, work, want)):
        return False
    return _same_mode(owner, target_sha, path)


def _unchanged(owner, target_sha, target_bytes, path):
    """The restore re-check: still restorable, no link on the path."""
    try:
        return _restorable(owner, target_sha, path, read_inside(owner, path),
                           target_bytes(path))
    except Linked:
        return False


def _classify(owner, entries, rel, slug, target_sha, target_bytes):
    """Check b: ``(restore, blocking)`` for every dirty path in the owner."""
    restore, blocking = [], []
    for xy, path in entries:
        inner = path[len(rel) + 1:] if path.startswith(rel + "/") else None
        if xy == "??" and inner is not None and is_runtime_local(inner):
            continue                         # untracked runtime file: left in place
        plan = _plan_of(path)
        if xy[0] not in " ?":
            blocking.append({"path": path, "why": "staged", "plan": plan})
        elif plan and plan != slug:
            blocking.append({"path": path, "why": "other-plan", "plan": plan})
        elif inner is None and path not in (INDEX, GITIGNORE):
            blocking.append({"path": path, "why": "outside-record", "plan": plan})
        else:
            want, linked = target_bytes(path), False
            try:
                work = read_inside(owner, path)
                same = _restorable(owner, target_sha, path, work, want)
            except Linked:                   # a link on the path is never safe to restore
                work, same, linked = None, False, True
            if same:
                restore.append((xy, path))
                continue
            others = _index_plans(work, want, slug) if path == INDEX and not linked else []
            blocking += ([{"path": path, "why": "other-plan", "plan": o} for o in others]
                         or [{"path": path, "why": "differs-from-origin",
                              "plan": slug if inner is not None else None}])
    return restore, blocking


def checkout(ctx, rel, target_sha, target_bytes, *, apply, lease_ok):
    """Step 3. Checks a, b, c in order BEFORE any restore; then restore + ff."""
    root, default, remote, slug = ctx["root"], ctx["default"], ctx["remote"], ctx["slug"]
    res = {"owner": None, "status": None, "target_sha": target_sha, "restored": [],
           "blocking": [], "command": None}
    if not remote:
        return {**res, "status": "no-remote"}
    owner = lb.owner_of_default(git, root, default)
    res["owner"] = owner
    if owner is None:
        return {**res, "status": "no-owner"}
    cmd = (f"git -C {owner} fetch {remote} && "
           f"git -C {owner} merge --ff-only {remote}/{default}")
    head = rev(owner, "HEAD")
    if not head or not ancestor(owner, head, target_sha):                     # a
        return {**res, "status": "blocked", "command": cmd,
                "blocking": [{"path": None, "why": "owner-not-behind", "plan": None}]}
    restore, blocking = _classify(owner, _porcelain(owner), rel, slug, target_sha,
                                 target_bytes)                                 # b
    if blocking:
        return {**res, "status": "blocked", "blocking": blocking, "command": cmd}
    if apply and not lease_ok():                                               # c
        return {**res, "status": "blocked", "command": cmd,
                "blocking": [{"path": None, "why": "lease-lost", "plan": None}]}
    if head == target_sha and not restore:
        return {**res, "status": "up-to-date"}
    if not apply:
        return {**res, "status": "would-fast-forward",
                "would_restore": [p for _, p in restore]}
    for xy, path in restore:
        f = Path(owner) / path
        # Re-read right before the write: an edit made after check b blocks here
        # and is kept. Paths restored so far held target_sha's bytes: nothing lost.
        # ponytail: a write landing between this read and the next line still races;
        # closing that needs a lock the editor would honour, which git has none of.
        if not _unchanged(owner, target_sha, target_bytes, path):
            return {**res, "status": "blocked", "command": cmd, "blocking": [
                {"path": path, "why": "differs-from-origin",
                 "plan": slug if path.startswith(rel + "/") else None}]}
        if xy == "??":
            f.unlink()
        else:
            git(["checkout", "--", path], owner, check=True)
        res["restored"].append(path)
    rc, out, err = git(["merge", "--ff-only", target_sha], owner, timeout=600)
    if rc == 0:
        return {**res, "status": "fast-forwarded"}
    return {**res, "status": "ff-failed", "git": (err or out).strip()[:2000],
            "command": "\n".join(
                [f"git -C {owner} merge --ff-only {target_sha}"]
                + [f"git -C {owner} restore --source {target_sha} --worktree -- {p}"
                   for p in res["restored"]])}
