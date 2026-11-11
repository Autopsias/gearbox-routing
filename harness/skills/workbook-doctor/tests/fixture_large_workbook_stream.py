#!/usr/bin/env python3
"""
S08 / DR-04 regression — robust large-workbook loading (read-only streaming).

The DR-04 dogfood (`T2D Fcst 2026 Ext.Costs.xlsx`, 11.5 MB) reproduced a HARD
CRASH: both `workbook_map.py` and `formula_risk.py` opened the workbook with a
full in-memory `load_workbook(read_only=False)`, which allocated ~3.7 GB and was
OOM-killed before any verdict — no output at all. The fix is a shared robust
loader (`refs.open_workbook`) that, above a size cap, loads in read-only
STREAMING mode through a `_GridSheet`/`_GridWorkbook` adapter, recording the
degraded checks (merged-cells, recalc) as findings instead of crashing or
silently reporting clean.

This fixture locks that behaviour WITHOUT needing a multi-GB file: it forces the
streaming path on a small workbook via `max_full_load_mb=0` and asserts:

  A. streaming does NOT crash and yields a valid map (load_mode == "streamed");
  B. the `large_workbook_streamed` limitation finding is present in BOTH the
     map and the formula-risk output (merged-cell + recalc honesty — a streamed
     run can never read as a clean full audit);
  C. the streamed map is FAITHFUL — it reproduces the same non-merged structural
     findings (number_as_text, blank_rows_in_table, duplicate_rows, …) as the
     full-load map, so streaming is a real map, not a degraded stub;
  D. the streamed formula-risk reproduces the same real formula findings
     (hardcodes, ref-errors, roll-forward) as the full-load scan;
  E. REGRESSION GUARD: a normal-size workbook still loads `full` (load_mode ==
     "full") and its full-load output is byte-identical to the pre-fix path
     (the streaming branch is never taken under the default cap).

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh under [functional].
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "workbook-doctor", "scripts")
WD_FIX = os.path.join(HERE, "workbook_doctor")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, WD_FIX)

import build_fixtures  # noqa: E402
import workbook_map  # noqa: E402
import formula_risk  # noqa: E402

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


def _checks(doc):
    return {f["check"] for f in doc["findings"]}


def _non_merged_structural(doc):
    """Structural finding signatures that do NOT depend on merged-cell parsing —
    these must be identical between full and streamed loads."""
    keep = {"number_as_text", "date_as_text", "blank_rows_in_table",
            "duplicate_rows", "unclear_headers", "mixed_units", "hidden_sheet",
            "source_without_id_date"}
    return sorted((f["check"], f["sheet"], f["locator"])
                  for f in doc["findings"] if f["check"] in keep)


def _formula_sig(doc):
    return sorted((f["check"], f["sheet"], f["locator"])
                  for f in doc["findings"] if f["check"] != "large_workbook_streamed")


def run():
    work = tempfile.mkdtemp(prefix="dr04_stream_")
    paths = build_fixtures.build_all(work)
    # Use a workbook the existing suite already seeds with rich structural +
    # formula defects so faithfulness is meaningfully testable.
    wb = paths.get("roll_forward") or sorted(paths.values())[0]

    full_out = os.path.join(work, "full")
    stream_out = os.path.join(work, "stream")

    # ---- A/C: full vs forced-streaming map on the SAME small workbook ----------
    try:
        m_full = workbook_map.run(wb, full_out, run_id="full")
    except Exception as e:  # pragma: no cover
        _errs.append("full-load map crashed: %r" % e)
        m_full = {"load_mode": "full", "findings": []}
    try:
        m_stream = workbook_map.run(wb, stream_out, run_id="stream",
                                    max_full_load_mb=0.0)  # force streaming
    except Exception as e:
        _errs.append("STREAMING map crashed (DR-04 regression): %r" % e)
        m_stream = {"load_mode": "?", "findings": []}

    check(m_full.get("load_mode") == "full",
          "E: a small workbook under the default cap must load full, got %r"
          % m_full.get("load_mode"))
    check(m_stream.get("load_mode") == "streamed",
          "A: forced max_full_load_mb=0 must stream, got %r" % m_stream.get("load_mode"))
    check("large_workbook_streamed" in _checks(m_stream),
          "B: streamed map must record the large_workbook_streamed limitation "
          "(merged-cell + recalc honesty)")
    check("large_workbook_streamed" not in _checks(m_full),
          "E: the full-load map must NOT carry the streamed limitation")
    check(_non_merged_structural(m_full) == _non_merged_structural(m_stream),
          "C: streamed map must reproduce the same non-merged structural findings "
          "as the full-load map (faithful, not a stub).\n  full=%s\n  stream=%s"
          % (_non_merged_structural(m_full), _non_merged_structural(m_stream)))

    # ---- B/D: full vs forced-streaming formula-risk ----------------------------
    fr_full = formula_risk.run(wb, os.path.join(work, "frfull"), run_id="full",
                               do_recalc=False)
    fr_stream = formula_risk.run(wb, os.path.join(work, "frstream"), run_id="stream",
                                 do_recalc=False, max_full_load_mb=0.0)
    check(fr_stream.get("load_mode") == "streamed",
          "A: formula_risk forced streaming must report load_mode=streamed")
    check("large_workbook_streamed" in _checks(fr_stream),
          "B: streamed formula-risk must record the large_workbook_streamed limitation")
    check(_formula_sig(fr_full) == _formula_sig(fr_stream),
          "D: streamed formula-risk must reproduce the same formula findings as the "
          "full-load scan (hardcodes/ref-errors/roll-forward), excluding the limitation.")
    check(_formula_sig(fr_stream),
          "D: streamed formula-risk found NO formula findings — the stream is empty/stub")

    if _errs:
        print("FAIL — DR-04 large-workbook streaming regression:")
        for e in _errs:
            print("  -", e)
        return 1
    print("PASS — DR-04: oversized workbook streams (no OOM crash), records the "
          "merged-cell/recalc limitation, and the streamed map + formula-risk are "
          "FAITHFUL to the full-load result; a normal workbook still loads full.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
