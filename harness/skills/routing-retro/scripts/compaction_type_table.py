#!/usr/bin/env python3
"""compaction_type_table.py — the per-session-type 'Compaction' table.

Extracted from compaction_report.py when the file crossed its size bound; the
one consumer imports it back as `_type_table`, so the report's shape is
unchanged.
"""

import compaction_retro as cr


def type_table(rows):
    """A 'Compaction' table per session_type. Rows whose type came from
    retro-classification are counted separately so a retro type can never be
    confused with a live policy file."""
    buckets = {}
    for row in rows.values():
        key = row.get("session_type") or "untyped"
        b = buckets.setdefault(key, {"session_type": key, "n": 0, "compactions": 0,
                                     "vetoes": 0, "allows": 0, "ctx_pairs": [],
                                     "ctx_one_sided": 0, "costs": [], "reread": [],
                                     "type_source": {}})
        b["n"] += 1
        b["compactions"] += row.get("compaction_records") or 0
        b["vetoes"] += row.get("vetoes") or 0
        b["allows"] += row.get("allows") or 0
        b["ctx_pairs"] += row.get("ctx_pairs") or []
        b["ctx_one_sided"] += row.get("ctx_one_sided") or 0
        if row.get("cost_usd") is not None:
            b["costs"].append(row["cost_usd"])
        if row.get("reread_cost_pct") is not None:
            b["reread"].append(row["reread_cost_pct"])
        src = row.get("type_source") or "none"
        b["type_source"][src] = b["type_source"].get(src, 0) + 1
    out = []
    for b in buckets.values():
        out.append({
            "session_type": b["session_type"],
            "n": b["n"],
            "compactions": b["compactions"],
            "vetoes": b["vetoes"],
            "allows": b["allows"],
            # MOVE 1's "did compaction shrink context" pair — BOTH stats are
            # computed over the PAIRED records only (rows carrying ctx_before
            # AND ctx_after; compact_reorient.py writes both on the `compacted`
            # row), so they always describe the same compactions. n_ctx_pairs
            # is their shared N, printed on the page beside them; one-sided
            # records are excluded from both and counted, never absorbed.
            # BOTH are the SAME statistic (median): a mean after against a
            # median before reverses the sign on a skewed sample - pairs
            # (100k,90k),(100k,90k),(900k,800k) all shrank, yet median-vs-mean
            # renders 100000 -> 326667, a 3x increase.
            "p50_ctx_at_compaction": cr._p50([p[0] for p in b["ctx_pairs"]]),
            "p50_ctx_after_compaction": cr._p50([p[1] for p in b["ctx_pairs"]]),
            "n_ctx_pairs": len(b["ctx_pairs"]),
            "ctx_one_sided_records": b["ctx_one_sided"],
            "cost_per_session": cr._mean(b["costs"]),
            "reread_pct": cr._mean(b["reread"], 1),
            "records_per_observed_session": round(b["compactions"] / b["n"], 3) if b["n"] else None,
            "type_source": b["type_source"],
        })
    return sorted(out, key=lambda r: -r["n"])
