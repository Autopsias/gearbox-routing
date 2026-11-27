"""Stop a first review that is too big for one reviewer pass, before it starts.

The limit is measured, not guessed. Measurement 2026-11-23 (scripts/review_rounds.py,
368 ledgers, 128 with 3+ reviewer runs): by the plan's rule the smallest first-run
size where over half needed 3+ runs is 7146 lines. That history counts the lines of
the whole files in the first surface, not changed lines, so it is not the unit this
gate measures. The check therefore ships as a printed line only (ENFORCE = False)
until a changed-line history exists; flip ENFORCE to halt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import llm_review_ledger as ledger
import safe_json as sj
from llm_review_accept import accepted_paths

#: Changed lines in the first surface above which a first review asks for a split.
LIMIT_LINES = 7146
ENFORCE = False
INDETERMINATE = 2  # mirrors llm_review_gate (importing it back is a cycle)


def changed_lines(cwd, base, changed, new_files) -> int:
    """Changed lines of tracked files (git numstat) plus every line of each new file."""
    total = 0
    if changed:
        res = subprocess.run(["git", "diff", "--numstat", base or "HEAD", "--", *changed],
                             cwd=cwd, capture_output=True, text=True, check=False)
        for ln in (res.stdout if res.returncode == 0 else "").splitlines():   # a failed diff: size unknown, no halt
            a, _, rest = ln.partition("\t")
            b = rest.partition("\t")[0]
            total += (int(a) if a.isdigit() else 0) + (int(b) if b.isdigit() else 0)
    for f in new_files:
        try:
            total += len(Path(cwd, f).read_bytes().splitlines())
        except OSError:
            pass
    return total


def size_acceptance(plan_dir, session):
    """The reason of the first `{"size": true, "reason": ...}` entry in the accept files."""
    for p in accepted_paths(plan_dir, session):
        try:
            entries = sj.get_list(sj.load_dict(p.read_bytes()), "accepted") or []
        except OSError:
            continue
        for e in entries:
            if isinstance(e, dict) and e.get("size") and str(e.get("reason", "")).strip():
                return str(e["reason"]).strip()
    return None


def check(led, args, base, changed, new_files, limit=None, enforce=None):
    """None to carry on, or the exit code that halts the gate. Attempt 1 only."""
    if led is None or led.attempt != 1:
        return None
    limit = LIMIT_LINES if limit is None else limit
    enforce = ENFORCE if enforce is None else enforce
    n = changed_lines(args.cwd, base, changed, new_files)
    if n <= limit:
        return None
    sid = args.session.strip().split(".")[0]
    reason = size_acceptance(args.plan_dir.strip(), sid)
    line = f"[llm-review-gate] surface size: {n} changed lines, limit {limit}"
    if reason:
        with open(ledger.ledger_path(led.plan_dir, led.session), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "size_accepted", "attempt": 1, "lines": n,
                                 "limit": limit, "reason": reason}) + "\n")
        print(f"{line}; accepted on record: {reason}")
        return None
    if not enforce:
        print(f"{line}; over the limit (printed only, not enforced)")
        return None
    print(f"{line}. No reviewer was started. Exits (verify-gates.md): split the session; "
          f"accept on record (add {{\"size\": true, \"reason\": \"...\"}} to {sid}.accepted.json); "
          "or escalate to the owner.", file=sys.stderr)
    return INDETERMINATE
