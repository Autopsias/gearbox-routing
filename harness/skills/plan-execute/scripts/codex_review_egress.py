#!/usr/bin/env python3
"""The EGRESS half of the Codex reviewer: may this review's bytes go to Codex?

Split out of `codex_review_backend.py` at its size bound, at the seam the
backend already drew ("availability and egress, both decided BEFORE any codex
process is started"). Two checks, because codex is sent two kinds of bytes: the
reviewed TREE (`egress_reason`) and TEXT built outside it (`text_egress_reason`).
The backend re-exports both names, and tests pin them there.
"""
from __future__ import annotations

import atexit
import os
import shutil
import stat
import tempfile
from pathlib import Path

#: clean copy -> the tree it was made from, so a per-repo opt-in still names the real repo
_ORIGIN = {}


def clean_copy(cwd):
    """The tree Codex reviews: the reviewed surface of `cwd` (git-tracked plus
    untracked-not-ignored files), WITHOUT any `.env*` path and without symlinks, in a
    private temp dir — or None when `cwd` is not a git tree (the caller then reviews
    `cwd` itself under the strict rule). A file is left out when any part of its path is
    a `.env*` name or a symlink (round 1: a symlinked PARENT imported an outside file).

    Operator decision, replacing a placeholder test on committed `.env`
    templates that failed five review rounds: Codex never needs a `.env*` file to
    review code, so it is simply not in the folder it is handed, in every repo, with
    no per-repo setup. The tree scan then runs on exactly these bytes: they are real,
    independent copies, never hard links, so a later write to the original cannot reach
    what Codex reads (round 1). Measured: example-project's 15,856 files in 8.1 s. ponytail: a
    submodule's files are not listed by the parent's `git ls-files`, so they are absent;
    see `_copy_one` for how a symlink swapped in during the copy is refused.
    """
    from egress import _git_candidates, _is_env_name  # local: keeps import light

    real = os.path.realpath(cwd)
    rels = _git_candidates(real)
    if rels is None:
        return None
    dst = tempfile.mkdtemp(prefix="codex-review-tree-")
    atexit.register(shutil.rmtree, dst, True)
    for rel in sorted(rels):
        if not any(_is_env_name(seg) for seg in rel.split("/")):
            _copy_one(os.path.join(real, rel), os.path.join(dst, rel))
    _ORIGIN[dst] = real
    return dst


def _copy_one(src, out):
    """Copy `src` only if the file actually OPENED is the regular file at a link-free
    `src`. Checked AFTER the open, on the open descriptor (round 3: a parent directory
    swapped for a symlink between a pre-check and the copy imported an outside file):
    a link anywhere in the path now, or a different file than was opened, leaves it out."""
    try:
        fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False                      # gone, unreadable, or a final-component link
    with os.fdopen(fd, "rb") as fh:
        opened = os.fstat(fh.fileno())
        try:
            here = os.stat(src, follow_symlinks=False)
        except OSError:
            return False
        if os.path.realpath(src) != src or not stat.S_ISREG(opened.st_mode) \
                or (here.st_dev, here.st_ino) != (opened.st_dev, opened.st_ino):
            return False
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as dst_fh:
            shutil.copyfileobj(fh, dst_fh)
    return True


_DIFF_PATH_LINES = ("diff --git ", "--- ", "+++ ", "rename from ", "rename to ", "copy from ",
                    "copy to ", "Binary files ")


def _env_path(token):
    """Any `/`-segment of `token` a `.env*` name — git's C-quoting (`".env\\tx"`) and its
    `a/` `b/` prefixes ignored (round 2: a quoted name slipped past the check)."""
    return any(_is_env(seg.strip('"').removeprefix("a/").removeprefix("b/"))
               for seg in str(token).strip('"').split("/"))


def _is_env(seg):
    from egress import _is_env_name

    return _is_env_name(seg.strip('"'))


def env_change_reason(paths, diff_file=None):
    """A change that edits a `.env*` path, or `.gitleaksignore`, is never sent to Codex: the copy leaves the
    file out, but the diff Codex reads would still carry its lines (round 1). Checked on
    the changed-path list AND on the path headers of the diff file itself, which is
    the payload. Fail closed to the on-box disposition, like any other restricted tree."""
    names = [str(p) for p in paths]
    for line in _read(diff_file).splitlines():
        if line.startswith(_DIFF_PATH_LINES):
            names += line.split()[1:]
    hit = next((n for n in names if _env_path(n)), None)
    if hit:
        return f"{hit} (the change edits a .env* file, which a Codex review never receives)"
    # Operator decision (security reviews, MEDIUM): the tree scan forgives what
    # `.gitleaksignore` pins and what the repo's `.gitleaks.toml` allowlists, so a session
    # that edits either could silence the scan. (Inline `gitleaks:allow` is ignored by the
    # scan itself: egress._gitleaks_pass passes --ignore-gitleaks-allow.)
    pin = next((n for n in names if _seg(n).startswith(".gitleaks")), None)
    return (f"{pin} (the change edits a .gitleaks* file, which tells the secret scan what to "
            "ignore; a person reviews that change, never Codex)") if pin else None


def _seg(token):
    """The last `/`-segment of a path token, git quoting and `a/` `b/` prefixes removed."""
    return str(token).strip('"').rsplit("/", 1)[-1].strip('"').lower()


def egress_reason(cwd):
    """The data_sensitivity_guard's reason to keep this tree off Codex, or None.

    RE-RUN AT GATE TIME, never read from a stamp `begin` left behind: the tree
    changes between dispatch and verify, and a `.env` written by the session
    under review is exactly the case a stale verdict would miss. The scanned root
    is the REVIEWED tree (`--cwd`), not the process cwd, so the tree that was
    cleared and the tree that would be sent are the same one by construction.

    `PLAN_EXECUTE_EGRESS_ROOT` is deliberately NOT honoured here. On the dispatch
    path it is safe because `_codex_cmd` `cd`s into that same root; here codex
    runs in `cwd` whatever the variable says, so honouring it cleared one tree and
    shipped another (a `.env` in `cwd` passed with the variable on a clean dir).
    """
    from egress import _egress_verdict  # local: keeps import light
    from run import _load_routing
    from ssot_policy import _egress_opt_in

    ssot = _load_routing()[1]
    origin = _ORIGIN.get(cwd)
    hit = real_env(origin) if origin else None
    if hit and not _egress_opt_in(ssot, Path(origin)):
        return (f"{hit} (a real .env* file beside the reviewed code: Codex's read-only sandbox "
                f"can read any file on this machine, so the copy alone does not keep it out) "
                f"(tree {origin})")
    eg = _egress_verdict(ssot, Path(cwd))
    if eg["restricted_hit"] and not _egress_opt_in(ssot, Path(origin or cwd)):
        return f"{eg['restricted_hit']} (tree {eg['root']})"
    return None


def real_env(root):
    """The first REAL secrets path the reviewed code sits beside, or None. Codex's read-only
    sandbox reads any file on this machine (measured), so a real `.env` in the
    ORIGINAL tree stays one read away from the copy (security review, HIGH). Real means:
    an untracked `.env*` (a plan worktree never holds one; a main checkout usually does);
    a tracked `.env*` not named as a template (`.env`, `.envrc` can hold secrets); a
    template whose content trips the secret scan; or a directory the walk cannot read.
    Searched from the repo's TOP, not `root`: reviewing a subdirectory must still see
    `repo/.env` (second review, MEDIUM). If git cannot list tracked files, every `.env*`
    counts as real (fail closed)."""
    top = _git_out(root, "rev-parse", "--show-toplevel")
    top = top.strip() if top else root
    p = _git_out(top, "ls-files", "-z")
    tracked = frozenset(x for x in p.split("\0") if x) if p is not None else frozenset()
    errors = []
    for d, dirs, files in os.walk(top, onerror=errors.append):   # links listed, not followed
        dirs[:] = [x for x in dirs if x != ".git"]
        for name in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            path = os.path.join(d, name)
            if _is_real(os.path.relpath(path, top), path, tracked):
                return path
    return f"{errors[0].filename} (unreadable directory)" if errors else None


def _is_real(rel, path, tracked):
    from egress import _is_env_name, _text_hit

    segs = [x for x in rel.split("/") if _is_env_name(x)]
    if not segs:
        return False
    if rel not in tracked or not all(x.lower().endswith(_TEMPLATE) for x in segs):
        return True
    try:                                  # a committed template: scan what it really holds
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return True
    return _text_hit(text, rel.replace("/", "_")) is not None


_TEMPLATE = (".example", ".sample", ".template")


def _git_out(root, *args):
    import subprocess

    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def text_egress_reason(cwd, texts):
    """The guard's reason to keep TEXT from outside the tree off Codex, or None.

    The tree scan reads the tree as it is NOW. What codex is handed also holds
    bytes that scan never sees: the diff file (its `-` lines are content the
    session deleted, so a secret removed from the tree still rides in the diff),
    the prior-findings section and the intent text, both from the plan
    directory. Same scanner and the same per-repo opt-in as the dispatch path's
    `codex_command._refuse_restricted_text`.
    """
    from egress import _text_hit
    from run import _load_routing
    from ssot_policy import _egress_opt_in

    if _egress_opt_in(_load_routing()[1], Path(cwd)):
        return None
    for name, text in texts.items():
        hit = _text_hit(text, name) if text else None
        if hit:
            return f"restricted content in the {name} this review would send: {hit}"
    return None


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""      # codex cannot read it either, so nothing of it is sent
