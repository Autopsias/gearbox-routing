#!/usr/bin/env python3
"""The PROMPT half of the Codex reviewer — what `codex exec` is asked, and the
one path spelling every side of the attestation is compared in.

Split out of `codex_review_backend.py` at the seam that file already drew, when
the module hit its size bound: this half decides what the reviewer is TOLD, the
other half runs it and reads the answer back. `_norm` lives here because the
prepared list is where a path enters the gate's vocabulary; `attest` and `adapt`
import it so both sides of every comparison are normalised by ONE function.
"""
from __future__ import annotations

import re

import llm_review_payload as pay


def prepared_paths(cwd, changed, new_files):
    """THE list the reviewer is held to — the same paths, in the same order, that
    `prepare_surface` counted into `expected`. Captured run output is listed for
    the reviewer but never deep-read, so it is not attestable and is not here."""
    return [_norm(p) for p in list(changed) + pay.split_untracked(cwd, list(new_files))[0]]


def _norm(path):
    """Strip a leading `./` PREFIX only — the ledger's own rule, so a path that
    round-trips through Codex still matches the one the gate prepared."""
    return re.sub(r"^(\./)+", "", str(path).strip())


def contract(paths):
    """The output contract appended to the gate's prompt.

    The gate's prompt is written for a reviewer answering in PROSE with a fenced
    array at the end. Under `--output-schema` the final message IS the JSON
    object, so those instructions are not merely redundant, they contradict the
    schema — this section says which half wins, in the one place a reader will
    look for it.
    """
    listing = "\n".join(f"  - {p}" for p in paths)
    return (
        "\n\n===OUTPUT CONTRACT (CODEX BACKEND) — OVERRIDES EVERY OUTPUT "
        "INSTRUCTION ABOVE===\n"
        "Everything above about a ```json fenced array, about a REVIEWED_FILES "
        "line, and about what your final message must END with is written for a "
        "different reviewer. Ignore it. Your final message here is a SINGLE JSON "
        "object matching the schema you were given:\n"
        '  {"verdict": "PASS"|"FINDINGS", "reviewed": [...], "findings": '
        '[{"file", "line", "severity", "title", "why"}], "priors": '
        '[{"id", "status", "evidence"}]}\n'
        "`verdict` is PASS if and only if `findings` is empty. `severity` is one "
        "of blocker, critical, high, medium, low, info. `title` names the defect "
        "in one short phrase; `why` gives the defect and its evidence in one or "
        "two sentences.\n"
        "You are READ-ONLY. Do not edit, create or delete a file, and do not run "
        "a command that writes.\n"
        "If a PRIOR REVIEW section above lists findings to verify, answer for "
        "EACH one in `priors`: its id, `status` fixed or open, and one line of "
        "`evidence`. Ignore the `prior_id`/`new_in` fields that section asks for. "
        "A prior that is still a real defect is ALSO re-reported in `findings` "
        "with the same file and the same title. A prior you leave out of "
        "`priors` stays OPEN: silence is not a fix. With no PRIOR REVIEW "
        "section, `priors` is [].\n"
        f"THE FILES UNDER REVIEW ARE EXACTLY THESE {len(paths)} PATHS. `reviewed` "
        "MUST list every one of them, verbatim as written here. The gate compares "
        "your list against this one and a shortfall FAILS the run whatever your "
        "findings say — so if you could not read one, that is the answer it needs, "
        "not a list padded to length:\n" + listing + "\n"
        "===END OUTPUT CONTRACT===")
