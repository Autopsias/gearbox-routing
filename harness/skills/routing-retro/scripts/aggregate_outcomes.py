#!/usr/bin/env python3
"""aggregate_outcomes.py — turns evals/routing/outcomes.ndjson into per-cell facts
and threshold-gated upgrade/downgrade PROPOSALS for /routing-update. Never edits
model-routing.yaml — it proposes, the operator applies via /routing-update.

stdlib-only (json + collections) — DuckDB was considered and rejected for ~30
lines of counting, matching the house rule in retro_scan.py/resolve_route.py.

TWO-LAYER MODEL. Raw ledger lines are ATTEMPT records (one per verify/rework
resolution). They are grouped into SESSION COHORTS keyed by
``(project, plan, session, generation)`` — attempt numbering restarts on a
redispatch, so ``generation`` is load-bearing: without it two separate
executions merge into one corrupted cohort. Each COMPLETE cohort (attempt
numbers form the contiguous sequence 1..max, no gaps) yields ONE terminal
outcome, attributed to the session's AUTHORED cell (task_class, model_authored,
reasoning_authored, backend) as read off its ATTEMPT-1 record. A cohort missing
attempt 1 or with a gap is INCOMPLETE — reported separately, never in any
denominator (the ledger was disabled or redirected mid-session). A complete
cohort whose last record is still "rework" is OPEN — still in flight, also
excluded everywhere.

RESULT VOCABULARY (mirrors skills/plan-execute/scripts/outcomes.py::RESULTS —
duplicated here as a small frozen set rather than cross-skill-imported, since
routing-retro and plan-execute are independent skills):
  passed | rework | exhausted | blocked | done_unverified | wontfix
"passed"/"exhausted" are the only two GATE-CHECKED outcomes (the verify loop
actually ran to a real success-or-failure) — these are the only results that
enter any proposal or facts denominator. "blocked" (administrative),
"done_unverified" (no verify gate configured — ungated work can't be
quality-judged) and "wontfix" (retired) are reported separately and NEVER
enter a rate calculation at any level, per the plan's explicit carve-out.

ATTESTATION. A cohort counts toward a cell's PROPOSAL pool only when its
ATTEMPT-1 record's model_ran_source == "attested" (an unattested model_ran
cannot support a model-SPECIFIC proposal). A cell whose attested share among
its candidate pool falls below half reports "attribution-limited" instead of
a proposal — see _cell_proposal.

PROVENANCE + EPOCH. Proposals draw ONLY on default_resolved cohorts (per
attempt-1's `routing_provenance`) whose `ssot_version_ran` falls in the SSOT's
value-identical range `value_epoch..version` (`--ssot`, default the live file;
an SSOT without `value_epoch:` keeps the old exact-match rule) — pinned_override and
prior-epoch cohorts are FACTS-ONLY, never proposal input. ONE exception:
downgrade proposals are OPENED from default_resolved cohorts, but CLOSED
(stage-2 adoption evidence) by experimental cohorts carrying that proposal's
proposal_id — the one place experimental data feeds a decision. Experimental
cohorts never enter the normal per-cell facts/proposal pools; they aggregate
separately, keyed by routing_experiment.proposal_id (see aggregate_canaries).

DOWNGRADE-OPEN THRESHOLD — A DELIBERATE, UNDOCUMENTED-IN-SPEC CALL: the plan
text gives an explicit numeric UPGRADE trigger (N>=6, pass<0.60 or
escalation>=0.50) and an explicit stage-2 ADOPTION gate (N>=10, pass>=0.90,
attempts-per-success<=1.1), but never states what makes the aggregator open a
downgrade SMOKE recommendation in the first place. This script uses a
symmetric, conservative bar — N>=6, first-attempt pass>=0.90, escalation==0 at
the CURRENT default cell — on the reasoning that a cell already clearing (or
near) the eventual adoption bar AT ITS OWN rung is the honest signal that it
may be over-modeled. Flagged for operator confirmation; see DOWNGRADE_OPEN_*
below and the s06 closeout notes.

MALFORMED-LINE BOUND: >2% of scanned lines OR >=3 malformed lines FAILS the
run outright (exit nonzero, `"proposals": []`, prior `.last-aggregated` stamp
left untouched) — a ledger this corrupted cannot support routing statistics.
ONE NARROW CARVE-OUT [HARDENED finding 5]: a ledger under ~50 lines fails the
2% ratio bound on a SINGLE bad line (1/6 = 16.7%), but the live writer leaves
exactly that shape after a crash mid-append — one torn line at the very END
of the file. That specific case (malformed count == 1 AND it is the LAST
physical line) is tolerated regardless of ratio; a malformed line anywhere
else, or a second one, is still subject to the ordinary bound.

SNAPSHOT DISCIPLINE: the ledger's byte length is captured ONCE at the top of
the run; only that exact prefix is ever parsed, and `.last-aggregated` is
stamped with THAT boundary — never the file's live end-of-file size — and only
after a successful parse AND successful output. A record appended
concurrently between snapshot and stamp is therefore never marked "analyzed"
without having been read (see test_concurrent_append_is_excluded_from_boundary
in the test file); a malformed-run failure leaves the prior stamp untouched
(test_malformed_abort_leaves_stamp_untouched).

Usage:
  aggregate_outcomes.py [LEDGER_PATH] [--since YYYY-MM-DD] [--last N] [--ssot PATH]
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro_scan  # noqa: E402 — sibling script; reused for its `prices:` parser

from retro_cohorts import (
    aggregate_cell, build_cohorts, cell_key, cell_proposal, cost_cells, dedup_records,
    filter_cohorts_since_last, is_experimental, parse_lines, read_prefix,
    snapshot_boundary,
)
from retro_common import (
    COST_PER_SUCCESS_CAVEAT, MALFORMED_MAX_LINES, MALFORMED_MAX_RATIO,
    _import_resolver, _norm_reasoning, default_ledger_path, default_ssot_path,
)
from retro_signals import (
    aggregate_canaries, apex_revisit, did_it_help, read_adoptions,
    render_markdown, time_to_signal, write_stamp,
)

# --------------------------------------------------------------------------
# SSOT reads — class defaults + prices, live, never hardcoded.
# --------------------------------------------------------------------------
def ssot_version(ssot_text):
    m = re.search(r"^version:\s*(\d+)", ssot_text, re.M)
    return int(m.group(1)) if m else None


def ssot_value_epoch(ssot_text):
    """The SSOT version at which routing-affecting VALUES last changed.

    A record-only version bump (v17, 2026-08-18: binary-bump documentation, no
    pin moves) used to zero the proposal pool, because the pool required
    ssot_version_ran == version exactly. `value_epoch:` in the SSOT (maintained
    by /routing-update's apply step) names the floor of the value-identical
    version range; records from value_epoch..version ran the SAME routing
    values and stay admissible. Absent key -> the current version (old
    behavior, exact match)."""
    m = re.search(r"^value_epoch:\s*(\d+)", ssot_text, re.M)
    return int(m.group(1)) if m else None


def active_provider(ssot_text):
    m = re.search(r"^active_provider:\s*([\w-]+)", ssot_text, re.M)
    return m.group(1) if m else "anthropic"



def class_defaults_from_ssot(ssot_path, provider):
    """{task_class: {"model": ..., "reasoning": ...}} — the CURRENT default cell
    for every class this SSOT declares, resolved live via resolve_route (never a
    hardcoded class list)."""
    rr = _import_resolver()
    task_classes, _profile = rr._load(str(ssot_path), provider)  # noqa: SLF001
    out = {}
    for tc in task_classes:
        baseline = rr.resolve(tc, provider, ssot_path=str(ssot_path))
        if isinstance(baseline, dict):
            out[tc] = {"model": baseline.get("model_id"),
                       "reasoning": _norm_reasoning(baseline.get("native_effort"))}
    return out


def prices_from_ssot(ssot_path):
    """{tier_name: (in_rate, out_rate)} — reused verbatim from retro_scan's
    parser rather than re-implemented (ladder rung 2: already in this codebase)."""
    try:
        return retro_scan.parse_ssot_prices(str(ssot_path))
    except SystemExit:
        return {}


# --------------------------------------------------------------------------
def _fail(reason, malformed, total_lines, boundary, conflict=None):
    out = {
        "ok": False, "error": reason,
        "ledger": {"snapshot_bytes": boundary, "total_lines": total_lines, "malformed": malformed,
                   "malformed_ratio": round(malformed / total_lines, 4) if total_lines else 0.0},
        "proposals": [], "cells": [], "canaries": [],
    }
    if conflict:
        out["conflict"] = conflict
    return out


def _read_snapshot(ledger_path):
    """(records, boundary, total_lines, malformed, ratio) for the snapshot prefix,
    or a refusal dict when the ledger is too damaged to aggregate.

    Both refusals are ledger-integrity calls, kept together and ahead of every
    read of the SSOT so a corrupt ledger never half-produces a report.
    """
    boundary = snapshot_boundary(ledger_path)
    text = read_prefix(ledger_path, boundary)
    records, malformed, total_lines, sole_trailing = parse_lines(text)
    ratio = (malformed / total_lines) if total_lines else 0.0
    # [HARDENED finding 5] a lone torn line at the very END of the file (the
    # shape left by a crash mid-append) is tolerated regardless of ratio —
    # see MALFORMED-LINE BOUND in the module docstring. Anything else (a
    # malformed line elsewhere, or >=2 of them) is still subject to the bound.
    if not sole_trailing and (malformed >= MALFORMED_MAX_LINES or ratio > MALFORMED_MAX_RATIO):
        return _fail(f"malformed lines {malformed}/{total_lines} ({ratio:.1%}) exceeds the "
                     f"tolerance ({MALFORMED_MAX_LINES} lines / {MALFORMED_MAX_RATIO:.0%})",
                     malformed, total_lines, boundary)

    records, conflict = dedup_records(records)
    if conflict:
        return _fail(f"conflicting duplicate record_id {conflict['record_id']!r} — two DIFFERENT "
                     "records share one id (ledger corruption, not a replay)",
                     malformed, total_lines, boundary, conflict=conflict)
    return records, boundary, total_lines, malformed, ratio


def _report(*, now, epoch, value_floor, provider, boundary, total_lines, malformed,
            ratio, records_in_scope, complete, incomplete, open_cohorts, experimental,
            cell_entries, canaries, apex, tts, dih, markdown, ledger_path, is_filtered, costs):
    """The report dict and a commit callable for the stamp advance that only a FULL
    run earns (None otherwise).

    Every input is computed BEFORE this call, and the caller invokes the commit
    only AFTER the report is delivered: a failure in aggregation or in printing
    leaves the prior stamp untouched."""
    out = {
        "ok": True,
        "generated_at": now.isoformat(),
        "ssot_version": epoch,
        "value_epoch": value_floor if value_floor is not None else epoch,
        "active_provider": provider,
        "ledger": {"snapshot_bytes": boundary, "total_lines": total_lines, "malformed": malformed,
                   "malformed_ratio": round(ratio, 4), "records_parsed": records_in_scope},
        "cohorts": {"complete": len(complete), "incomplete": len(incomplete),
                    "open": len(open_cohorts), "experimental": len(experimental)},
        "incomplete_cohorts": [
            {"key": list(c["key"]), "attempts": [r.get("attempt") for r in c["records"]]}
            for c in incomplete
        ],
        "cells": cell_entries,
        "canaries": canaries,
        "apex_revisit": apex,
        "time_to_signal": tts,
        "did_it_help": dih,
        "proposals": (
            [c["proposal"] for c in cell_entries if c["proposal"]]
            + [c for c in canaries if c["stage"] == "adoption-ready"]
        ),
        "markdown": markdown,
        "cost_per_success_caveat": COST_PER_SUCCESS_CAVEAT,
        "cost_cells": costs,
    }
    # [HARDENED finding 7] only a FULL (unfiltered) run gets to advance the
    # incremental stamp. A --since/--last run analyzed a SUBSET of the ledger
    # by cohort, not the whole snapshot prefix — stamping the full boundary
    # would claim everything up to it was incorporated, which isn't true.
    commit = None if is_filtered else (
        lambda: write_stamp(ledger_path, boundary, total_lines, now))
    return out, commit


def run(*args, **kwargs):
    """The report dict; advances the stamp immediately. main() uses run_deferred()
    so the stamp moves only after the JSON is printed."""
    out, commit = run_deferred(*args, **kwargs)
    if commit:
        commit()
    return out


def run_deferred(ledger_path, ssot_path, since=None, last=None, now=None, adoptions_path=None):
    """(report dict, stamp-commit callable or None)."""
    now = now or datetime.now(UTC)
    ledger_path = Path(ledger_path)
    snap = _read_snapshot(ledger_path)
    if isinstance(snap, dict):        # a refusal, not a snapshot
        return snap, None
    records, boundary, total_lines, malformed, ratio = snap

    ssot_text = Path(ssot_path).read_text(encoding="utf-8")
    epoch = ssot_version(ssot_text)
    value_floor = ssot_value_epoch(ssot_text)
    provider = active_provider(ssot_text)
    class_defaults = class_defaults_from_ssot(ssot_path, provider)
    prices = prices_from_ssot(ssot_path)
    # [S06 finding 7] default to the adoptions.ndjson SIDE BY SIDE with the
    # ledger actually being analyzed, not the live repo's — read_adoptions()'s
    # own fallback (default_adoptions_path()) always points at the live repo
    # regardless of which ledger this run is scanning, so a run against an
    # alternate ledger silently inherited production adoption state.
    adoptions = read_adoptions(adoptions_path or (ledger_path.parent / "adoptions.ndjson"))

    # [HARDENED finding 3] cohorts are built from the FULL deduped record set
    # first, then filtered as whole units — never filter raw attempt records
    # before cohorts exist, or a session can be split across the --since/
    # --last boundary and its terminal attempt orphaned as INCOMPLETE.
    complete, incomplete, open_cohorts = build_cohorts(records)
    is_filtered = bool(since or last)
    if is_filtered:
        # [S06 finding 4] `--last N` means "the last N cohorts" (the unit of
        # analysis) ACROSS THE WHOLE RUN, not N from each of complete/
        # incomplete/open independently — applying the filter to each list
        # separately made `--last 2` return up to 6 cohorts. Tag each cohort
        # with its bucket, filter the union as one ranked-by-ts list, then
        # split back by tag.
        tagged = ([dict(c, _kind="complete") for c in complete]
                  + [dict(c, _kind="incomplete") for c in incomplete]
                  + [dict(c, _kind="open") for c in open_cohorts])
        tagged = filter_cohorts_since_last(tagged, since, last)
        complete = [c for c in tagged if c["_kind"] == "complete"]
        incomplete = [c for c in tagged if c["_kind"] == "incomplete"]
        open_cohorts = [c for c in tagged if c["_kind"] == "open"]
    records_in_scope = sum(len(c["records"]) for c in complete + incomplete + open_cohorts)

    experimental = [c for c in complete if is_experimental(c)]
    non_experimental = [c for c in complete if not is_experimental(c)]
    # [S06 finding 1] an in-flight canary (its session cohort is still OPEN —
    # last record "rework", not yet terminal) must still suppress a fresh
    # downgrade-open proposal for the same cell. Scanning only `experimental`
    # (built from `complete`) makes an in-flight experiment invisible and the
    # aggregator re-issues "STAGE 1 SMOKE" on top of it — so this set is drawn
    # from experimental cohorts in EITHER complete or open_cohorts.
    open_experimental = [c for c in open_cohorts if is_experimental(c)]
    canaries = aggregate_canaries(experimental, adoptions)
    # [S06 round-3 finding 3] a smoke-failed canary is FINISHED, not in flight.
    # Leaving its proposal_id in this set made the two halves of one JSON
    # document contradict each other -- canaries[] said "smoke-failed" while the
    # cell said "canary already underway" -- and permanently barred that cell
    # from ever opening another downgrade experiment. SKILL.md step 3 says
    # smoke-failed means "drop the proposal", so the cell must be free again.
    _finished_pids = {cn["proposal_id"] for cn in canaries if cn["stage"] == "smoke-failed"}
    existing_canary_pids = {
        pid for c in experimental + open_experimental
        if (pid := (c["attempt1"].get("routing_experiment") or {}).get("proposal_id"))
        and pid not in _finished_pids
    }

    cells_by_key = defaultdict(list)
    for c in non_experimental:
        cells_by_key[cell_key(c)].append(c)

    cell_aggs = {key: aggregate_cell(cohorts, epoch, value_floor) for key, cohorts in cells_by_key.items()}

    cell_entries = []
    for key, agg in sorted(cell_aggs.items()):
        proposal, status = cell_proposal(key, agg, epoch, existing_canary_pids)
        task_class, model, reasoning, backend = key
        cell_entries.append({
            "task_class": task_class, "model_authored": model, "reasoning_authored": reasoning,
            "backend": backend, "facts": agg["facts"], "excluded": agg["excluded"],
            "proposal_pool_n": agg["proposal_pool_n"], "attested_pool_n": agg["attested_pool_n"],
            "attested_share": agg["attested_share"], "status": status, "proposal": proposal,
            "price_per_mtok": ({"in": prices[model][0], "out": prices[model][1]}
                                if model in prices else None),
        })

    apex = apex_revisit(complete)
    tts = time_to_signal(class_defaults, provider, cell_aggs, now)
    dih = did_it_help(non_experimental, adoptions)
    markdown = render_markdown(cell_entries, canaries, apex, epoch)

    costs = cost_cells([r for c in complete + incomplete + open_cohorts for r in c["records"]])
    return _report(
        now=now, epoch=epoch, value_floor=value_floor, provider=provider,
        boundary=boundary, total_lines=total_lines, malformed=malformed, ratio=ratio,
        records_in_scope=records_in_scope, complete=complete, incomplete=incomplete,
        open_cohorts=open_cohorts, experimental=experimental, cell_entries=cell_entries,
        canaries=canaries, apex=apex, tts=tts, dih=dih, markdown=markdown,
        ledger_path=ledger_path, is_filtered=is_filtered, costs=costs)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ledger", nargs="?", default=None, help="path to outcomes.ndjson (default: evals/routing/outcomes.ndjson)")
    ap.add_argument("--since", default=None, help="only records with ts on/after YYYY-MM-DD")
    ap.add_argument("--last", type=int, default=None, help="keep only the last N cohorts (sessions) by ts")
    ap.add_argument("--ssot", default=None, help="path to model-routing.yaml (default: the live repo file)")
    args = ap.parse_args()

    ledger_path = Path(args.ledger) if args.ledger else default_ledger_path()
    ssot_path = Path(args.ssot) if args.ssot else default_ssot_path()

    out, commit = run_deferred(ledger_path, ssot_path, since=args.since, last=args.last)
    json.dump(out, sys.stdout, indent=1)
    print()
    sys.stdout.flush()
    if commit:
        commit()
    sys.exit(0 if out.get("ok", True) else 1)


if __name__ == "__main__":
    main()
