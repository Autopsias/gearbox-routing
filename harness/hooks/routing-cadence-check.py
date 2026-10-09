#!/usr/bin/env python3
"""SessionStart hook: nudge when routing outcomes have piled up unanalyzed.

STAT-ONLY by design. Reads `.last-aggregated` (a small JSON stamp
written by `aggregate_outcomes.py`: offset/size/mtime/mean_record_size) and
os.stat()'s the ledger — it NEVER opens or reads the ledger's content. An
unbounded read of a growing ndjson file inside a ~200ms SessionStart budget
is exactly the failure mode this avoids.

Threshold is HONESTLY approximate: 30 * the stamp's mean record size, in
bytes — never an exact record count (the ledger can gain/lose bytes per
record; only aggregate_outcomes.py, which actually parses lines, knows a
real count). [HARDENED:codex — an estimated count is not a count]

BOOTSTRAP. The seeded stamp ships `mean_record_size: 0`, and
returning early on that made the nudge impossible to ever fire: the only
thing that writes a real mean is `/routing-retro`, which is the very thing
this nudge exists to prompt. A deadlock, not a safeguard. With no mean yet
the hook falls back to BOOTSTRAP_RECORD_SIZE and says so in the wording;
the first real aggregation replaces the estimate with a measured mean.

Measured: a few tens of ms (python3 subprocess cold-start included) —
comfortably inside the ~200ms
SessionStart budget from CLAUDE.md.

Silent (no output, exit 0) on: missing ledger, missing/unreadable stamp, a
stamp with no usable offset, or growth below threshold — an empty banner is
the all-clear contract.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "evals", "routing", "outcomes.ndjson")
STAMP = os.path.join(ROOT, "evals", "routing", ".last-aggregated")

GROWTH_MULTIPLE = 30

# Stand-in mean record size, in bytes, for a stamp that has never been written
# by a real aggregation. MEASURED, not guessed, from early ledger records.
# Only used until the first `/routing-retro` writes a real mean.
BOOTSTRAP_RECORD_SIZE = 760


def _emit(text):
    print(json.dumps({
        "systemMessage": text,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        },
    }))


def main():
    try:
        current_size = os.path.getsize(LEDGER)
    except OSError:
        return  # no ledger yet — nothing to say

    try:
        with open(STAMP, "r", encoding="utf-8") as f:
            stamp = json.load(f)
    except (OSError, ValueError):
        return  # no stamp yet — first aggregation run establishes it

    offset = stamp.get("offset")
    mean_record_size = stamp.get("mean_record_size")
    if not isinstance(offset, (int, float)):
        return

    # Truncation/rotation/test override: the ledger is now smaller than the
    # last analyzed boundary. Fires every session until a re-aggregation
    # advances the stamp past the new (smaller) size — that re-aggregation
    # IS the natural "warn once per event" reset, no extra state file needed.
    if current_size < offset:
        _emit("ledger rotated or truncated — re-run aggregation")
        return

    estimated = not isinstance(mean_record_size, (int, float)) or mean_record_size <= 0
    if estimated:
        mean_record_size = BOOTSTRAP_RECORD_SIZE

    growth = current_size - offset
    if growth >= GROWTH_MULTIPLE * mean_record_size:
        _emit("roughly 30+ routing outcomes unanalyzed — run /routing-retro"
              + (" (estimated: this ledger has never been aggregated)" if estimated else ""))


if __name__ == "__main__":
    main()
