#!/usr/bin/env python3
"""The two size gates the collector measures itself: file length and function length.

Split out of health.py so that file stays under the house 500-LOC limit — it sat
at exactly 500, so even a one-line repair tripped the ratchet. The grouping is
real and not arbitrary: both functions here answer "is this repo's OWN source
inside its size bounds", both read the same exclude list, and nothing else in the
collector measures a length.

health.py imports both check functions and calls them from `collect`. It does NOT
re-export SRC_EXT — unlike card.py's names, nothing outside this module ever
reached that constant through `health.<name>`, so moving it is not a facade
question. `JUNK_DIRS` went the other way, down into common.py, because
hyg.tracked-junk needs the same five names and two hand-written copies drift.

Imports common only; health.py imports this, never the other way round.
"""
import ast
from pathlib import Path

from common import JUNK_DIRS, check, excluded, quality_excludes, unmeasured

SRC_EXT = {".py", ".ts", ".tsx", ".js", ".jsx"}

# The house ratchet's defaults (scripts/quality/check_file_sizes.py,
# check_function_lengths.py). Deliberately NOT read from the target repo's
# pyproject: these two are the generic reading, and `cq.ratchet` is the check
# that runs the repo's OWN checkers with its OWN committed baselines. A repo that
# has recorded a baseline gets both numbers — the raw one here and the ratcheted
# one there — which is the point of having two checks rather than one.
FILE_LIMIT = 500
FUNC_LIMIT = 100


def cq_file_size(repo, files):
    """Source files over the house line limit — over the files really READ.

    The scope is computed ONCE, before anything is measured, and the verdict is
    gated on that same list (`measured=ours`). It used to be gated on nothing at
    all: `files` went in, every candidate could be filtered out inside the loop,
    and the check still reported "no oversized source files" having opened zero
    of them — a repo whose only tracked .py sits under build/ read a confident
    pass. A file in scope that will not open is a file this check did NOT
    measure, so it is counted and named rather than `continue`d past.
    """
    excl = JUNK_DIRS + quality_excludes(repo)
    ours = [f for f in files if Path(f).suffix in SRC_EXT and not excluded(f, excl)]
    big, unread = [], []
    for f in ours:
        try:
            n = len((repo / f).read_text(errors="replace").splitlines())
        except OSError:
            unread.append(f)
            continue
        if n > FILE_LIMIT:
            big.append(f"{f} ({n})")
    return unmeasured(
        check("cq.file-size", "code_quality", "advisory",
              f"Source files under {FILE_LIMIT} lines",
              "warn" if big else "pass",
              (f"over {FILE_LIMIT} lines: " + ", ".join(big[:8])
               + ("…" if len(big) > 8 else "")) if big
              else "no oversized source files",
              "split with /code-quality --fix (safe-refactor agents)" if big else "",
              measured=ours,
              nothing="no first-party source file was in scope to measure — "
                      "every tracked .py/.ts/.js is build output or inside the "
                      "repo's own excludes"),
        len(ours) - len(unread), unread,
        f" ({len(unread)} file(s) could not be read and were NOT measured: "
        + ", ".join(sorted(unread)[:3]) + ")")


def cq_function_length(repo, shape):
    """Functions longer than the house limit — the one size bound never emitted.

    repo-health emitted cq.file-size, cq.complexity, cq.lint, cq.slop and
    cq.ratchet, and function length only ever reached the scorecard folded inside
    cq.ratchet's combined advisory — which is `na` on every repo with no
    scripts/quality/ of its own. By this skill's own rule a check that is never
    emitted can never fail, so it got its own id and its own fix route.

    `na` without Python rather than a bare `pass`, and for the same reason: `ast`
    reads Python only, so a TypeScript repo with a 300-line function would
    otherwise read "pass — no function over 100 lines", which is a check
    reporting health it never measured.

    That gate reads the list the loop ACTUALLY walks. It used to read shape["py"]
    while the loop then dropped every junk-dir path, so a repo whose only tracked
    .py lived under build/ or .venv/ parsed ZERO files and still reported "no
    function over 100 lines" (found by review). Scope is decided once,
    up front, and `measured=ours` is what common.check() refuses a verdict over.
    """
    ours = [f for f in shape["py"] if not excluded(f, JUNK_DIRS)]
    long, unread = [], []
    for f in ours:
        try:
            tree = ast.parse((repo / f).read_text(errors="replace"), filename=f)
        except (OSError, SyntaxError, ValueError):
            # A file that does not parse is cq.lint's finding, not this one — but
            # it is a file this check did NOT measure, and saying "no function
            # over 100 lines" over a tree it could not read is the exact lie this
            # skill exists to catch. So it is counted and named. Caught live in
            # this check's own end-to-end run, on a fixture that was accidentally
            # invalid Python: the scorecard read a confident `pass`.
            unread.append(f)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n = node.end_lineno - node.lineno + 1
                if n > FUNC_LIMIT:
                    long.append(f"{f}:{node.name}() ({n})")
    # Both shared guards, in order: `measured=` refuses a verdict over an empty
    # scope, then unmeasured() appends the unparsed files to WHICHEVER detail was
    # produced and stops a clean reading standing as `pass`. The note used to be
    # built by hand on the no-findings branch only, so one long function hid the
    # fact that another file had never been read.
    return unmeasured(
        check("cq.function-length", "code_quality", "advisory",
              f"Functions under {FUNC_LIMIT} lines",
              "warn" if long else "pass",
              (f"over {FUNC_LIMIT} lines: " + ", ".join(sorted(long)[:8])
               + ("…" if len(long) > 8 else "")) if long
              else f"no function over {FUNC_LIMIT} lines",
              "extract helpers with /code-quality --fix --focus=function-length "
              "(safe-refactor agents)" if long else "",
              measured=ours,
              nothing="no first-party .py files tracked (outside the repo's own "
                      "excludes and its build output) — function length is read "
                      "from the Python AST"),
        len(ours) - len(unread), unread,
        f" ({len(unread)} file(s) did not parse and were NOT measured: "
        + ", ".join(sorted(unread)[:3]) + " — see cq.lint)")
