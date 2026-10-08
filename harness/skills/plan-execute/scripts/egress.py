#!/usr/bin/env python3
"""data_sensitivity_guard: the egress secret-scan that gates Codex dispatch.

Split out of run.py, which had grown 634 lines past its size baseline. The seam
is real, not arbitrary: nothing here calls back into run.py, and run.py reaches
only _egress_root and _egress_verdict. Every check FAILS CLOSED.
"""
import contextlib
import json
import os
import re
import shutil
import stat as statmod
import subprocess
import tempfile
from pathlib import Path

from ssot_policy import _content_allowlist, _egress_opt_in


# --------------------------------------------------------------------------
# executor_policy enforcement (s06, EXE-01). The SSOT's `executor_policy:` block
# is POLICY PROSE; THIS is its enforcement point in the dispatch path (the SSOT
# names these functions). All checks FAIL CLOSED:
#   * dial-driven Codex execution (active_provider: openai) requires the session's
#     task_class to be opted into executor_policy.executor_for — otherwise the
#     session dispatches Claude (never Codex);
#   * linchpin / irreversible sessions NEVER auto-dispatch to Codex — dial-driven
#     ones fall back to Claude, explicitly-pinned ones BLOCK loudly (silently
#     rerouting an explicit pin would hide the contradiction);
#   * data_sensitivity_guard egress: a working tree carrying a real secret (or a
#     `.env*` file) is DO-NOT-SEND — the codex command is refused BEFORE
#     construction unless the SSOT carries an unexpired per-repo egress_opt_ins
#     entry for exactly this tree.
# --------------------------------------------------------------------------
_EGRESS_ROOT_ENV = "PLAN_EXECUTE_EGRESS_ROOT"  # test override; default = cwd. The scanned
# root is BOUND into the codex command via `cd <root> &&` so the scanned tree and the
# shipped tree are the same path by construction (adversarial-review 2026-07-10, consensus).
#
# WHAT IS MATCHED (rewritten 2026-07-28 — was filename substrings):
#   1. FILENAME — `.env*`, anywhere in the tree (full walk, symlinks followed).
#      Cheap, near-zero false positives, and a content scanner will NOT catch it:
#      a `.env` full of plain KEY=value trips no secret rule (measured). A `.env`
#      file is a decision, not a guess. Deliberately NOT git-scoped — `.env` is
#      almost always git-ignored, so a tracked-files-only rule would miss it.
#   2. CONTENT — `gitleaks dir` (the same scanner this repo's githooks/pre-commit
#      already runs) over the REVIEWED SURFACE only: git-tracked files plus
#      untracked-but-not-ignored files, minus per-file entries in the SSOT's
#      data_sensitivity_guard.content_scan_allowlist.
# The old `corpus`/`creds`/`credential`/`secret` NAME substrings are gone. They
# were the crying-wolf half: they blocked a repo over an analysis script named
# `migrate_corpus.py` while a live API key in `config.py` sailed straight past,
# because nothing ever read a byte of content. A guard that cries wolf gets
# routed around, and then it protects nothing.
#
# SCOPE (2026-07-28, second pass — the first one shipped rule 2 over the FULL
# tree and was unusable on a real repo). Measured on a 16 GB / 103,727-file plan
# repo: whole-tree = 6 min 5 s and 826 findings, every one of them inside
# git-ignored build output (`dist/` 2.9 GB, `_workspace/` 2.6 GB). Git-scoped =
# 570 files / 6.4 MB / 1.0 s and 1 finding. Build output that git ignores is not
# part of the repo and is not what a reviewer or a `git push` would ship, so it
# is not scanned. `.env` is the one thing that IS shipped-by-upload while being
# git-ignored, which is exactly why rule 1 stays a full-tree walk.
_GITLEAKS_LEAK_EXIT = 7  # distinct from gitleaks' own error exit (1) — a bad path,
# a broken config and "leaks found" all exit 1 under the default, which would make
# a crashed scan indistinguishable from a clean one.
_GITLEAKS_TIMEOUT_S = 300
_GIT_TIMEOUT_S = 60
# gitleaks prints `scanned ~<N> bytes (…) in …` to stderr on every pass. We assert
# it against the byte total of the surface we MEANT to scan. THE TRAP this exists
# for, measured the hard way: `gitleaks dir a b c d` takes ONE path argument — the
# extra three are silently ignored and it scans the CWD tree instead (6 min 40 s
# over 16 GB, vs 1.8 s for the four directories scanned one at a time). A scan
# that silently widens to the whole filesystem is the same defect class as a gate
# that reports green: it looks like it ran, and it did — over the wrong thing.
_SCANNED_BYTES_RE = re.compile(r"scanned ~(\d+) bytes")
# Compiled bytecode is not the reviewed surface. Two of the three findings on the
# real repo above were `__pycache__/*.pyc` copies of ONE Python docstring. Nothing
# a human would read as source is skipped — only build products of source we do
# scan (and in a normal repo git ignores these anyway, so this rarely fires).
_SCAN_SKIP_RE = re.compile(r"(^|/)__pycache__(/|$)|\.pyc$")


def _egress_root():
    return Path(os.environ.get(_EGRESS_ROOT_ENV) or Path.cwd())


def _egress_verdict(ssot_text, root):
    """`{root, restricted_hit, opted_in}` for one tree — the ONE egress scan, shared
    by `begin`'s refusal/verifier stamps and `plan --harness codex`'s turn-one
    disclosure (D4d). `opted_in` is only consulted when there IS a hit."""
    hit = _find_restricted(root, _content_allowlist(ssot_text))
    return {
        "root": str(root),
        "restricted_hit": hit,
        "opted_in": _egress_opt_in(ssot_text, root) if hit else False,
    }


def _is_env_name(name):
    return name.lower().startswith(".env")


def _under(path, root):
    root = str(root)
    return path == root or path.startswith(root + os.sep)


def _walk_tree(real_root):
    """ONE traversal, two products: `(env_hit, extra_roots)`.

    `env_hit` is the first `.env*` path found — matched on the entry name AND on
    any symlink's resolved target, over the FULL tree including git-ignored files,
    traversing directory symlinks (cycle-guarded). `extra_roots` are the resolved
    targets of directory symlinks that land OUTSIDE the tree: gitleaks does NOT
    descend into symlinked directories (measured, v8.30), so without a separate
    pass a `vendored -> ~/private` link would be a content blind spot this walk
    can already see through.

    os.scandir rather than os.walk, and an (st_dev, st_ino) cycle guard rather
    than a per-directory realpath: 2.2 s instead of 4.8 s over the 103k-file repo
    measured above, because realpath lstat()s every path component of every
    directory. The identity pair is also the stronger guard — it catches a cycle
    through a bind mount, which realpath does not."""
    real_root = str(real_root)
    extra_roots, seen, stack = [], set(), [real_root]
    while stack:
        d = stack.pop()
        try:
            st = os.stat(d)
        except OSError:
            continue                       # vanished / unreadable — nothing to inspect
        ident = (st.st_dev, st.st_ino)
        if ident in seen:
            continue                       # already traversed via another link
        seen.add(ident)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if _is_env_name(e.name):
                return f"{e.path} (.env* filename rule)", []
            try:
                is_link = e.is_symlink()
                is_dir = e.is_dir()        # follows the link, which is what we want
            except OSError:
                continue
            if is_link:
                target = os.path.realpath(e.path)
                if any(_is_env_name(seg) for seg in Path(target).parts):
                    return f"{e.path} -> {target} (.env* filename rule)", []
                if is_dir and not _under(target, real_root):
                    extra_roots.append(target)
            if is_dir:
                stack.append(e.path)
    return None, sorted(set(extra_roots))


def _git_candidates(real_root):
    """Repo-relative paths git considers part of `real_root`'s subtree — tracked
    files plus untracked-but-not-ignored files — or None when `real_root` is not
    inside a git work tree (or git is unusable, or the listing fails).

    None means "fall back to the full walk", which is the safe direction: it
    over-scans rather than under-scans. Non-git trees are small in practice; the
    16 GB tree that motivated the scoping is a git repo whose bulk is ignored."""
    git = shutil.which("git")
    if git is None:
        return None
    out = set()
    for extra in ([], ["--others", "--exclude-standard"]):
        try:
            proc = subprocess.run([git, "-C", str(real_root), "ls-files", "-z"] + extra,
                                  capture_output=True, text=True, timeout=_GIT_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:           # not a work tree (128), or a broken index
            return None
        out.update(p for p in proc.stdout.split("\0") if p)
    return out


def _scoped_link_tree(real_root, stack):
    """`(link_root, expected_bytes, submodule_roots)` — a throwaway directory of
    symlinks mirroring the git-tracked + untracked-not-ignored files under
    `real_root`, or None when the tree is not git-scoped.

    Why a link tree and not something cleverer: `gitleaks dir` takes exactly ONE
    path, so a 570-path candidate set is either 570 processes (~30 s of process
    startup) or one process over one directory. Symlinks cost 0.19 s to lay down
    for that set, `--follow-symlinks` makes gitleaks read the targets, and — the
    part that makes this work at all — gitleaks reports the RESOLVED target in
    `File`, so a finding names the real repo path with no mapping back (probed,
    v8.30.0). `stack` is an ExitStack; the tree is removed when it unwinds."""
    rels = _git_candidates(real_root)
    if rels is None:
        return None
    link_root = stack.enter_context(tempfile.TemporaryDirectory(prefix="plan-egress-scan-"))
    expected, submodules = 0, []
    for rel in sorted(rels):
        if _SCAN_SKIP_RE.search(rel):
            continue
        src = os.path.join(str(real_root), rel)
        try:
            st = os.stat(src)              # follows links: a tracked symlink is
        except OSError:                    # scanned as whatever it points at
            continue                       # tracked-but-deleted, or dangling
        if statmod.S_ISDIR(st.st_mode):
            # A gitlink (submodule): `git ls-files` names the directory, not the
            # files inside it, and the inner repo has its own index. Hand it a
            # full pass of its own rather than leave a hole in the scan.
            submodules.append(src)
            continue
        if not statmod.S_ISREG(st.st_mode):
            continue                       # fifo/socket/device — nothing to scan
        dst = os.path.join(link_root, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
        except OSError:
            return None                    # can't build it — fall back to the full walk
        expected += st.st_size
    return link_root, expected, submodules


def _gitleaks_pin_budget(real_root):
    """How many findings each `<repo-relative path>:<rule>` pair may forgive,
    counted from the scanned tree's own `.gitleaksignore` — whose entries keep
    the gitleaks-native form `githooks/pre-commit` writes,
    `<repo-relative path>:<rule>:<line>`.

    We match these OURSELVES because gitleaks' `-i` structurally cannot, here. It
    compares its own fingerprints, which are built from the path it was handed —
    and we hand it ABSOLUTE paths, so it produces absolute fingerprints that can
    never equal the relative ones `gitleaks protect --staged` writes. Measured on
    this repo 2026-07-28: scanning `.` honours all 16 pinned false positives and
    reports 0 findings; scanning the identical tree by absolute path honours 0 and
    reports all 16. That silently made every repo carrying a `.gitleaksignore`
    permanently DO-NOT-SEND — including this one — while both the SSOT and the
    contract stated the file was honoured. `-i` stays passed for the case where
    the path forms do line up; this is the belt that actually fits.

    The LINE is deliberately dropped from the key (2026-08-15). A gitleaks
    fingerprint is line-anchored, so ANY edit above a reviewed false positive
    silently unpins it and turns the whole tree DO-NOT-SEND — for a reason the
    refusal message cannot state, because it looks identical to a fresh secret.
    That is what stranded `profile-a-brain`: commit 4cfb55a moved a flagged
    docstring from line 143 to 142 and the pin stopped matching, though the line,
    its text and its entropy (3.81) were unchanged. Same defect the quality gate
    fixed in 77278cd by forgiving a function by NAME instead of by line number.

    ponytail: the COUNT is what replaces the line as the bound. N pins for a
    `(path, rule)` pair forgive N findings; finding N+1 still refuses. So a
    reviewed false positive that merely MOVES stays pinned, while a real secret
    added beside it does not ride in on the same pin. The ceiling: a new secret
    that REPLACES the pinned one, one-for-one, keeps the count and is forgiven.
    Anchoring on finding content would close that, but every content field
    gitleaks emits under `--redact` is redacted except `Entropy`, and recording
    entropy would break the gitleaks-native file format the commit hooks share."""
    try:
        text = (Path(real_root) / ".gitleaksignore").read_text(encoding="utf-8")
    except OSError:
        return {}
    budget = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        # `<path>:<rule>:<line>` — rsplit, because a path may contain a colon.
        path, _, rule = ln.rpartition(":")[0].rpartition(":")
        if not path or not rule:
            continue          # malformed pin: forgives nothing, fails closed.
        budget[(path, rule)] = budget.get((path, rule), 0) + 1
    return budget


def _gitleaks_pass(exe, target, ignore_root, expected_bytes, link_root, allow,
                   pinned=None):
    """One `gitleaks dir` process. Returns a display string for the first
    non-allowlisted finding (or for any degraded outcome — see `_content_scan`),
    or None when the target is clean."""
    cmd = [
        exe, "dir", target,               # ONE path argument. See _SCANNED_BYTES_RE.
        "--follow-symlinks",              # file symlinks; dir symlinks come in as extra roots
        "--no-banner", "--redact",
        "--report-format", "json", "--report-path", "-",
        # Honor the SCANNED tree's own .gitleaksignore (not the orchestrator's
        # cwd, which gitleaks defaults to) — same false-positive workflow the
        # pre-commit hook documents. The SSOT allowlist below is the auditable,
        # EXPIRING key; this one is the in-repo, permanent one.
        "--gitleaks-ignore-path", str(ignore_root),
        "--exit-code", str(_GITLEAKS_LEAK_EXIT),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_GITLEAKS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return (f"gitleaks could not scan {target} ({type(e).__name__}) — this tree "
                "cannot be cleared for egress (fail-closed)")
    if proc.returncode not in (0, _GITLEAKS_LEAK_EXIT):
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return (f"gitleaks failed on {target} (exit {proc.returncode}: {tail[0]}) — "
                "this tree cannot be cleared for egress (fail-closed)")
    if expected_bytes is not None:
        # SCOPE ASSERTION — the scan must have read the surface we selected and
        # not silently widened past it. Fails closed both ways: no byte line at
        # all (a gitleaks output-format change) is as unclearable as an overshoot.
        m = _SCANNED_BYTES_RE.search(proc.stderr or "")
        if m is None:
            return ("gitleaks did not report a scanned-byte count, so the scan SCOPE "
                    "cannot be confirmed — this tree cannot be cleared for egress "
                    "(fail-closed; check the gitleaks version, tested against 8.30.0)")
        scanned = int(m.group(1))
        # Generous bound on purpose: the failure it must catch is a scan that fell
        # back to the whole tree (16 GB vs 6.4 MB — three orders of magnitude), not
        # a few KB of gitleaks' own base64/archive re-decoding.
        if scanned > 2 * expected_bytes + (1 << 20):
            return (f"gitleaks scanned {scanned} bytes for a {expected_bytes}-byte "
                    f"candidate set — the scan WIDENED beyond the reviewed surface, so "
                    f"this tree cannot be cleared for egress (fail-closed)")
    try:
        findings = json.loads(proc.stdout or "[]")
    except ValueError:
        return (f"gitleaks produced unparseable output for {target} — this tree "
                "cannot be cleared for egress (fail-closed)")
    for f in findings:
        path = f.get("File") or target
        alias = f.get("SymlinkFile") or ""
        # `File` is the resolved target, `SymlinkFile` the link that reached it.
        # Name whichever the operator can act on: an IN-TREE symlink (`link.txt ->
        # ~/keys.txt`) is the thing they'd delete; a link inside our own scratch
        # tree is an implementation detail they should never see.
        if alias and not (link_root and _under(alias, link_root)):
            path = alias
        if os.path.realpath(path).lower() in allow:
            continue
        if pinned and _spends_pin(pinned, ignore_root, f, path):
            continue
        return (f"{path} (gitleaks rule {f.get('RuleID', '?')}, "
                f"line {f.get('StartLine', '?')})")
    return None


def _spends_pin(budget, real_root, finding, display_path):
    """True iff `.gitleaksignore` still has budget for this finding's
    repo-relative `<path>:<rule>` pair — in which case one pin is SPENT, so the
    next finding on the same pair has to carry its own pin. Both the resolved
    file and the link that reached it are tried, since either may be the in-repo
    path the commit hook would have pinned. Candidate order is fixed, not a set,
    so which pin gets spent never depends on iteration order."""
    rule = finding.get("RuleID", "?")
    seen = []
    for p in (finding.get("File") or "", finding.get("SymlinkFile") or "",
              display_path):
        if not p or p in seen:
            continue
        seen.append(p)
        if not _under(os.path.realpath(p), str(real_root)):
            continue
        key = (os.path.relpath(os.path.realpath(p), str(real_root)), rule)
        if budget.get(key, 0) > 0:
            budget[key] -= 1
            return True
    return False


_NO_GITLEAKS = (
    "gitleaks is NOT on PATH — the data_sensitivity_guard content scan cannot "
    "run, so this tree cannot be cleared for egress (fail-closed; there is no "
    "fallback scanner by design). Install it: `brew install gitleaks`"
)


def _text_hit(text, name):
    """First gitleaks finding in TEXT that a Codex prompt carries from outside the
    scanned tree (the findings digest, the rework feedback file), or None.

    The tree scan never sees this text: under plan isolation it comes from the
    outer plan directory, which is not the root the egress guard scans. Same
    scanner, same one-path `gitleaks dir` pass, same fail-closed outcomes — the
    text is written to a throwaway directory under `name` so a finding names its
    source, never the temp path."""
    exe = shutil.which("gitleaks")
    if exe is None:
        return _NO_GITLEAKS
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        f = Path(d) / name
        f.write_text(text, encoding="utf-8")
        hit = _gitleaks_pass(exe, d, d, f.stat().st_size, None, frozenset())
    return hit.replace(d + os.sep, "") if hit else None


def _content_scan(real_root, extra_roots, allow):
    """First non-allowlisted gitleaks finding on the REVIEWED SURFACE of
    `real_root` (and under any out-of-tree symlinked directory), as a display
    string naming the offending path; None if the content is clean.

    The reviewed surface is the git-scoped candidate set — tracked plus
    untracked-not-ignored — scanned through a throwaway symlink tree. When
    `real_root` is not a git work tree there is no ignore information to use, so
    the whole tree is scanned exactly as before.

    FAILS CLOSED in every degraded case — a missing `gitleaks`, a scan crash, a
    timeout, unparseable output, a scanned-byte count that does not match the
    surface we selected — by returning a hit that names the problem. An unrunnable
    or mis-scoped scanner must never read as "nothing found"; the only way past is
    the operator's own unexpired egress_opt_ins entry for this tree.

    Findings are `--redact`ed: the refusal names the file, rule and line, never
    the secret itself (the message lands in run.ndjson and the orchestrator's
    transcript)."""
    exe = shutil.which("gitleaks")
    if exe is None:
        return _NO_GITLEAKS
    with contextlib.ExitStack() as stack:
        scoped = _scoped_link_tree(real_root, stack)
        if scoped is None:
            # (target, expected_bytes, link_root) — no ignore data, scan it all.
            passes = [(str(real_root), None, None)]
        else:
            link_root, expected, submodules = scoped
            passes = [(link_root, expected, link_root)]
            extra_roots = list(extra_roots) + submodules
        passes += [(t, None, None) for t in extra_roots]
        # ONE budget across every pass — N pins forgive N findings for the whole
        # tree, not N per symlinked root.
        pinned = _gitleaks_pin_budget(real_root)
        for target, expected, link_root in passes:
            hit = _gitleaks_pass(exe, target, real_root, expected, link_root, allow,
                                 pinned)
            if hit:
                return hit
    return None


def _find_restricted(root, allow=frozenset()):
    """First restricted path under `root`, or None — the filename rule first
    (it rides the full-tree walk that also finds out-of-tree symlinked
    directories), then the content scan over the git-scoped surface. `allow` is
    the SSOT's per-file content allowlist; it never clears a `.env*`, which only a
    whole-repo egress_opt_ins entry can.

    Measured 2026-07-28 on the 16 GB / 103,727-file plan repo, gitleaks 8.30.0:
    2.2 s walk + 1.0 s scan ≈ 3.4 s, against 6 min 5 s before the scoping.

    ponytail: NOT cached across processes, deliberately. The walk is 2/3 of the
    cost and has to run every time anyway (rule 1 is the whole tree), so a cache
    could only save the ~1 s gitleaks pass — and the only honest key for it is
    (path, size, mtime) over the same candidate set the scan already stats. A
    stale-cache false PASS in a DO-NOT-SEND gate costs more than a second."""
    real_root = Path(os.path.realpath(root))
    env_hit, extra_roots = _walk_tree(real_root)
    return env_hit or _content_scan(real_root, extra_roots, allow)
