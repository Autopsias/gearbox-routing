"""§10.1 — the set of files a quality gate is actually pointed at.

Authority: ``../references/plan-isolation-contract.md`` §10 (v1, frozen
2026-08-21). One definition, used by the gates themselves (``gate_files.py`` is
runnable: ``scripts/session-quality-gate.sh`` calls it) and by the tests that
prove a gate is scoped to its own plan.

TWO RULES, AND BOTH ARE ABOUT A GATE THAT CANNOT FAIL.

**`git diff HEAD` is forbidden as a gate input.** It excludes untracked files
ENTIRELY. A session whose output is all-new files — which describes most
new-module sessions — produces an EMPTY `git diff HEAD`, passes every gate that
reads it, and is then committed by `git add -A` with nothing having examined it.
The failure signature is indistinguishable from a genuinely clean run: zero
findings, exit 0. So the pre-commit input is `git status --porcelain=v1 -z`,
which reports staged, unstaged, renamed, deleted AND untracked alike.

**`-z` is normative, not decoration.** Under the default `core.quotePath` git
renders a non-ASCII path as an escaped, double-quoted token — measured
2026-08-22, `_plans/plâno-2026/a.txt` comes back as
`"_plans/pl\\303\\242no-2026/a.txt"` — and a path containing a space or a
newline is unparseable from line-oriented output at all. NUL-delimited output is
the only form that survives both, and it makes a rename record's two paths
unambiguous.

An EMPTY result is a fact the caller must surface (§10.3: `files_examined: 0`),
never a pass. Nothing here hides it: the list is returned as it is.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

TIMEOUT = 120

# `-uall`, never the default. Plain `git status --porcelain` collapses a wholly
# new untracked DIRECTORY to a single entry ending in `/`, so a session that adds
# a whole new package of Python is reported as one path and a gate that filters
# by extension sees zero files to check.
_STATUS = ["status", "--porcelain=v1", "-z", "-uall"]


def _git(args, cwd):
    """(rc, stdout). stdout is NOT stripped — `-z` output ends in a NUL."""
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return p.returncode, p.stdout


def status_entries(cwd, *, ignored=False):
    """``[(xy, path), ...]`` from ``git status --porcelain=v1 -z -uall``, or None
    when git could not answer (not a work tree, git missing).

    A rename or copy record is ``XY <new>\\0<orig>\\0`` — TWO fields for one
    entry. Both are returned: the destination is the file a gate reads, and the
    origin is a deletion, which §10.1 puts in the set as surely as any other.
    Consuming only the first field would silently mis-frame every following
    record, since the orig field would be parsed as the next entry's status.
    """
    rc, out = _git([*_STATUS, *(["--ignored=matching"] if ignored else [])], cwd)
    if rc != 0:
        return None
    fields = [f for f in out.split("\0") if f]
    entries, i = [], 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:                       # not `XY <path>` — skip, don't guess
            continue
        xy, path = rec[:2], rec[3:]
        entries.append((xy, path))
        if ("R" in xy or "C" in xy) and i < len(fields):
            entries.append((xy, fields[i]))    # the rename/copy ORIGIN
            i += 1
    return entries


def range_files(cwd, base, head="HEAD"):
    """Paths carried by ``base..head``, or None when git could not answer.

    §10.1's POST-COMMIT input. Committed work is invisible to `status`, so a gate
    that spans the boundary — the land re-gate is one — takes the UNION of this
    and the status set, which is what `changed_files(base=...)` returns.
    """
    rc, out = _git(["diff", "--name-only", "-z", f"{base}..{head}"], cwd)
    return None if rc != 0 else [p for p in out.split("\0") if p]


def changed_files(cwd, base=None):
    """The gate's file set: sorted, de-duplicated, §10.1-shaped. None on failure.

    None is NOT an empty set and callers must not conflate them — a gate that
    cannot read its own file set has to fail closed (check everything, or refuse),
    where an honestly empty set is §10.3's warning.
    """
    entries = status_entries(cwd)
    if entries is None:
        return None
    paths = {p for _, p in entries}
    if base:
        ranged = range_files(cwd, base)
        if ranged is None:
            return None
        paths |= set(ranged)
    return sorted(paths)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=".", help="the tree to read (a gate's cwd)")
    ap.add_argument("--base", default=None,
                    help="also include paths carried by <base>..HEAD (§10.1 post-commit)")
    ap.add_argument("-0", "--null", action="store_true",
                    help="NUL-delimit the output, for `xargs -0`")
    args = ap.parse_args(argv)
    files = changed_files(args.cwd, args.base)
    if files is None:
        # FAIL CLOSED, LOUDLY. A caller that reads an empty stdout as "nothing
        # changed" is the silent green this module exists to refuse.
        print(f"gate_files: cannot read the file set in {args.cwd!r} — "
              "not a git work tree, or git failed.", file=sys.stderr)
        return 2
    sys.stdout.write("".join(f + ("\0" if args.null else "\n") for f in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
