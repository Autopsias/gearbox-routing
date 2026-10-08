"""What the reviewer is asked to READ, separated from what the surface IS.

`llm_review_surface` answers which paths are under review. This answers how
each one is presented: source to read in full, captured run output to list and
leave alone. Split out at the 500-LOC bound when the git-isolation branch's
`prepare_surface` move and this classification landed in the same file.
"""

from __future__ import annotations

import os


# A session that must produce evidence writes its receipts into the tree, and
# `git ls-files --others` cannot tell a receipt from a new module. Measured
# 2026-08-23 on s09b: 8 untracked files, of which 6 were the session's own
# probe output -- 32,993 bytes, including a 12,238-byte neuter-probe log --
# all handed over under "Read each one in full and review it as ADDED code".
# Two costs, both real: the reviewer spends agentic Read calls on machine
# output (the prompt lists PATHS, so it reads them with its own tools), and
# `surface_size` counts every one of those lines toward the detection band.
# Dropping the four logs moved that band from ~28% to ~42% on the same run.
_ARTIFACT_EXT = (".txt", ".log", ".out", ".ndjson", ".csv", ".diff", ".patch")


def is_captured_output(cwd, path):
    """True when `path` is a receipt to LIST, not source to deep-read.

    ALL THREE must hold: the path is inside a session's `_evidence/`, its
    extension is a known receipt extension, and it is NOT executable. Any one
    of them alone is wrong.

    * Not the directory alone -- an executable under `_evidence/` IS source.
      The s09b review found a real data-loss bug in a `run_probes.sh` there (a
      failed backup `cp` plus a `trap restore EXIT` that could copy a
      zero-byte file over a real module).
    * Not the extension alone -- `.txt` is a receipt extension AND a source
      extension. Without the directory test, `requirements.txt`,
      `constraints.txt` and `CMakeLists.txt` were all listed-not-read, so a new
      dependency pin shipped unreviewed (found by the gate's own review of this
      change, 2026-08-23, and reproduced).

    Everything else reads as SOURCE. A misfiled receipt costs some reading; a
    misfiled module costs a review, and that is the direction that ships
    defects.
    """
    if "/_evidence/" not in path and not path.startswith("_evidence/"):
        return False
    if os.path.splitext(path)[1].lower() not in _ARTIFACT_EXT:
        return False
    return not os.access(os.path.join(cwd, path), os.X_OK)


def split_untracked(cwd, paths):
    """-> (source, artifacts). Order preserved; every path lands in exactly one."""
    source, artifacts = [], []
    for p in paths:
        (artifacts if is_captured_output(cwd, p) else source).append(p)
    return source, artifacts


def _sized(cwd, path):
    try:
        return f"  - {path} ({os.path.getsize(os.path.join(cwd, path))} bytes)"
    except OSError:
        return f"  - {path}"


def untracked_block(cwd, new_files):
    """The prompt's NEW FILES section, or "" when there are none.

    Two lists, because they earn two different instructions. Source is read in
    full -- that is the blind spot `untracked_files` exists to close. Captured
    output is NAMED AND SIZED and nothing more: still visible, still openable if
    a finding needs it, never a standing instruction to read machine output as
    authored code. Nothing is hidden; a surface that shrinks silently is the
    same silent-green class this module refuses everywhere else.
    """
    if not new_files:
        return ""
    source, artifacts = split_untracked(cwd, new_files)
    listing = "\n".join(_sized(cwd, p) for p in source)
    if not source:
        block = ("Review that diff. Every UNTRACKED file in this session's "
                 "surface is captured run output, listed below.")
    else:
        block = (
        "Review that diff AND the NEW FILES listed below.\n\n"
        "These files are UNTRACKED, so `git diff HEAD` does not contain a "
        "single line of them. Read each one in full and review it as ADDED "
        "code — this is where a whole new module hides from a review that "
        "trusts the diff:\n" + listing + "\n\nIf a listed path is generated "
        "output or runtime state rather than authored source, say so in one "
        "line and move on; do not raise findings against it.\n\n"
        "Their UNTRACKEDNESS is never itself a finding. A session's work is "
        "reviewed BEFORE it is committed — the orchestrator commits this whole "
        "surface, these files included, once the gates pass. \"file X is not "
        "committed\", \"a fresh checkout would not have X\", \"run git add X\" "
        "describe the review point in the workflow, not a defect, and each one "
        "costs the session a rework attempt it never owed. Review the CONTENT. "
        "(A genuinely unreachable new file — one a .gitignore rule would keep "
        "out of the commit — is a real finding; say which rule catches it.)")
    if artifacts:
        block += ("\n\nThese UNTRACKED paths are CAPTURED RUN OUTPUT the session "
                  "was required to produce -- probe logs, transcripts, receipts. "
                  "They are listed so nothing is hidden, and you may open one if a "
                  "finding depends on it. DO NOT read them in full and DO NOT "
                  "review them as added code:\n"
                  + "\n".join(_sized(cwd, p) for p in artifacts))
    return block
