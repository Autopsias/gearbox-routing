"""The `gate-indeterminate` brief for a SHORT review: the reviewer read fewer
files than the land surface held, so the gate could not decide.

Its own module because land_gate.py sits at the 500-line bound. Measured cost of
the old brief (review-gate-ground-truth-2026-10-17): it said "re-run" on seven
short reads of 5-15 of 61 files, each run 20-45 minutes, every answer the same.
"""

import re

#: `llm_review_gate`'s note when the reviewer attested fewer files than it was
#: handed. test_land_short_review drives the real gate, so a reworded note fails it.
_SHORT = re.compile(r"REVIEWED_FILES=(\d+) but was handed (\d+) file")


def short_review(out):
    """[read, handed] when the reviewer read FEWER files than its surface, else None.
    The same surface gives the same short read, so "re-run" is the wrong advice."""
    hits = _SHORT.findall(out)
    n, total = map(int, hits[-1]) if hits else (0, 0)
    return [n, total] if n < total else None


def brief(short, base, plan_dir):
    """The park text for ``short`` ({gate_id: [read, handed]})."""
    return (
        "LAND PARKED — the re-gate COULD NOT DECIDE, because the reviewer read only part "
        "of the surface: " + "; ".join(f"{g} reviewed {n} of {t} files" for g, (n, t) in short.items())
        + ". Nothing was pushed and nothing is claimed about the merged tree.\n\n"
        "A re-run on the same surface will most likely give the same result. Make the "
        "surface smaller first, with one of two levers:\n"
        "  1. List data paths (stored samples, generated files) in `.review-exclude` on the "
        f"BASE branch. The land reads that list from the base ({base}), never from the plan "
        "branch (contract §5.1b).\n"
        "  2. Point PLAN_EXECUTE_INTENT_FILE at a note that tells the reviewer which files "
        f"are data.\nThen run `run.py land {plan_dir}`.\n\n")
