"""FIX-02 - run the repo's own two whitespace fixers on the plan record first.

example-project's pre-commit hook rejects trailing spaces and bad file ends; ``finish`` and
``land`` never retry a hook, so every plan parked. ``repair_record`` runs the
repo's own ``pre-commit`` for ``trailing-whitespace`` and ``end-of-file-fixer`` on
the record files already copied into the commit tree, then writes the fixed bytes
back to the plan folder so folder and commit stay byte-identical. It does nothing
in a repo that has no config or no installed hook: such a repo cannot refuse the
commit, and its bytes stay as they are.

Every path that does not repair leaves a one-line reason in ``note()``; a later
``push-failed`` or ``record-plan-failed`` park carries it.
"""

import contextlib
import fcntl
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import plan_worktree as pwt
from finish_paths import Park, lstat_inside, read_inside

HOOKS = ("trailing-whitespace", "end-of-file-fixer")
TIMEOUT = 180                 # seconds per fixer
MANIFEST = "manifest.json"    # verify.py binds state to its exact bytes: never repaired
_reasons = []


def note():
    """The last repair's skip reason as a suffix for a park message, or ''."""
    return f" (record repair: {_reasons[-1]})" if _reasons else ""


@contextlib.contextmanager
def record_lock(root, slug):
    """One process at a time may copy, repair and commit this plan's record."""
    path = pwt.plan_state_dir(root, slug) / "finish.record.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def only_ends_differ(a, b):
    """True when two index copies differ only where the fixers write: line and file ends.
    The owner's copy keeps its space after a repair; restoring it loses nothing else."""
    def rows(d):
        return [ln.rstrip() for ln in d.rstrip().splitlines()]
    return a is not None and b is not None and rows(a) == rows(b)


def find_tool(tree):
    """``[argv...]`` for the repo's pre-commit, or ``(None, reason)``."""
    cfg = Path(tree) / ".pre-commit-config.yaml"
    if cfg.is_symlink() or not cfg.is_file():
        return None, "no .pre-commit-config.yaml"
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=str(tree),
                         capture_output=True, text=True, timeout=60)
    hook = Path(tree) / out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    if hook is None or not hook.is_file():
        return None, "no pre-commit hook installed"
    m = re.search(r"^INSTALL_PYTHON=(.+)$", hook.read_text(errors="replace"), re.M)
    try:
        py = shlex.split(m.group(1))[0] if m else None
    except (ValueError, IndexError):
        py = None
    if py and os.access(py, os.X_OK):
        return [py, "-m", "pre_commit"], None
    exe = shutil.which("pre-commit")
    return ([exe], None) if exe else (None, "pre-commit tool not reachable")


def _run(argv, tree):
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")}
    p = subprocess.Popen(argv, cwd=str(tree), env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    try:
        p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return None
    return p.returncode


def _status(tree):
    return subprocess.run(["git", "status", "--porcelain", "-z", "-uall"], cwd=str(tree),
                          capture_output=True, text=True, timeout=120).stdout


def _paths(tree):
    return {e[3:] for e in _status(tree).split("\0") if e}


def _bytes(tree, p):
    f = Path(tree) / p
    try:
        return b"->" + os.readlink(f).encode() if f.is_symlink() else f.read_bytes()
    except OSError:
        return None   # a deleted path has no bytes


def _outside(tree, snap, files):
    """Paths outside ``files`` that are newly dirty, or whose bytes differ from ``snap``."""
    return sorted(p for p in _paths(tree) | set(snap)
                  if p not in files and (p not in snap or _bytes(tree, p) != snap[p]))


def _rollback(tree, before, snap, outside):
    """Put the listed files back and undo every outside change, so the tree stays as it was."""
    for p, data in before.items():
        (Path(tree) / p).write_bytes(data)
    for p in outside:
        if p in snap:
            if snap[p] is None:
                (Path(tree) / p).unlink(missing_ok=True)
            else:
                (Path(tree) / p).write_bytes(snap[p])
        elif subprocess.run(["git", "ls-files", "--error-unmatch", "--", p], cwd=str(tree),
                            capture_output=True).returncode == 0:
            subprocess.run(["git", "checkout", "HEAD", "--", p], cwd=str(tree), capture_output=True)
        else:
            (Path(tree) / p).unlink(missing_ok=True)


def _regular(base, path):
    try:
        st = lstat_inside(base, path)
    except Park:
        return False
    return st is not None and os.path.isfile(Path(base) / path) \
        and not os.path.islink(Path(base) / path)


def _write_back(plan_dir, inner, data):
    """Replace the plan-folder file atomically; the temp file lives in the parent.
    shortcut: a parent folder swapped for a link during this write is the same limit
    ``finish_paths.read_inside`` records; no lock can bind it. Upgrade with dirfd."""
    dst = Path(plan_dir) / inner
    mode = dst.lstat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=str(Path(plan_dir).parent), prefix=".repair-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), mode)
        os.replace(tmp, dst)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _write_all(plan_dir, rel, changed, before, after):
    """Write every repaired file back; on a failure restore the earlier ones (all or none)."""
    done = []
    try:
        for p in changed:
            _write_back(plan_dir, p[len(rel) + 1:], after[p])
            done.append(p)
    except OSError as e:
        # all or none: put every already-replaced file back; name any that will not go back
        stuck = []
        for p in done:
            try:
                _write_back(plan_dir, p[len(rel) + 1:], before[p])
            except OSError:
                stuck.append(p)
        raise Park("push-failed", f"writing the repaired files back failed ({e}); "
                   + (f"could not restore {stuck}" if stuck else "earlier files were restored"),
                   stuck) from None


def repair_record(tree, plan_dir, rel, paths, lease_ok=None, shared=()):
    """Run the two fixers on ``paths`` (repo-relative, already copied into ``tree``)
    and on ``shared``: generated repo files the commit also stages (the index,
    ``.gitignore``), which have no plan-folder copy to write back.
    Returns the paths written back. Raises ``Park`` when a plan file changed under us,
    the fixers touched a path outside the list, or the lease is lost."""
    _reasons.clear()
    files = [p for p in paths if p != f"{rel}/{MANIFEST}" and _regular(tree, p)]
    fixed = files + [p for p in shared if _regular(tree, p)]
    if not fixed:
        return []
    argv, why = find_tool(tree)
    if argv is None:
        _reasons.append(why)
        return []
    before = {p: read_inside(tree, p) for p in fixed}
    snap = {p: _bytes(tree, p) for p in _paths(tree) if p not in fixed}
    for hook in HOOKS:
        rc = _run([*argv, "run", hook, "--files", *fixed], tree)
        if rc not in (0, 1):
            _rollback(tree, before, snap, _outside(tree, snap, fixed))
            _reasons.append("the fixer timed out" if rc is None
                            else f"the fixer exited {rc}; files left as they were")
            return []
    after = {p: read_inside(tree, p) for p in files}
    changed = [p for p in files if after[p] != before[p]]
    outside = _outside(tree, snap, fixed)
    if outside:
        _rollback(tree, before, snap, outside)
        raise Park("outside-record", "the record fixers changed files outside the record", outside)
    if lease_ok is not None and changed and not lease_ok():
        raise Park("lease-lost", "the ship lease was lost before the repaired files were written back")
    for p in changed:
        inner = p[len(rel) + 1:]
        # shortcut: a person's editor can still write between this compare and
        # os.replace; no lock binds them. Upgrade only if that ever happens.
        if read_inside(plan_dir, inner) != before[p]:
            raise Park("outside-record", "a plan file changed while the fixers ran", [p])
    _write_all(plan_dir, rel, changed, before, after)
    return changed
