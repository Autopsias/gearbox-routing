#!/usr/bin/env python3
"""Ledger read, cohort assembly and the stats over a pool of cohorts.

Split out of aggregate_outcomes.py, which stood at 1026 lines against a 500-line
house limit. The seam is the two-layer model described in that file's docstring:
everything here turns raw ATTEMPT records into SESSION COHORTS and then into
per-cell rates. It reads no SSOT and writes no output.

Snapshot discipline: read_prefix never reads past the boundary snapshot_boundary
recorded, so a writer appending mid-run cannot change what this run aggregated.
"""
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median

from compaction_ledger import parse_ts
from retro_common import (
    ADMISSIBLE_SOURCES, ADOPTION_ATTEMPTS_PER_SUCCESS_MAX, ADOPTION_MIN_N,
    ADOPTION_PASS_MIN, DOWNGRADE_OPEN_ESCALATION_MAX, DOWNGRADE_OPEN_PASS_MIN,
    EXCLUDED_TERMINAL_RESULTS, MIN_N_DOWNGRADE_OPEN, MIN_N_UPGRADE,
    PROPOSAL_ELIGIBLE_RESULTS, SMOKE_N, UPGRADE_ESCALATION_MIN,
    UPGRADE_PASS_MAX, _norm_reasoning,
)

# --------------------------------------------------------------------------
# Ledger read — snapshot discipline (see module docstring).
# --------------------------------------------------------------------------
def snapshot_boundary(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def read_prefix(path, boundary):
    if boundary <= 0:
        return ""
    with open(path, "rb") as f:
        data = f.read(boundary)
    return data.decode("utf-8", errors="replace")


def parse_lines(text):
    """(records, malformed_count, total_lines, sole_trailing_torn_write).
    A malformed line is skipped, never raises — the bound check in run()
    decides whether that's tolerable. `sole_trailing_torn_write` is True only
    when there is EXACTLY ONE malformed line and it is the LAST physical
    line — the shape the live writer leaves after a crash mid-append — so
    run() can special-case it (see MALFORMED-LINE BOUND / finding 5)."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    records, malformed_at = [], []
    for i, ln in enumerate(lines):
        try:
            records.append(json.loads(ln))
        except ValueError:
            malformed_at.append(i)
    total = len(lines)
    sole_trailing = len(malformed_at) == 1 and malformed_at[0] == total - 1
    return records, len(malformed_at), total, sole_trailing


def dedup_records(records):
    """Drop exact-duplicate record_ids (a replayed resolution); FAIL LOUDLY (return
    a conflict descriptor) on two DIFFERENT records sharing one record_id — that is
    ledger corruption, not a normal replay."""
    seen = {}
    out = []
    for r in records:
        rid = r.get("record_id")
        if rid is None:
            out.append(r)
            continue
        canon = json.dumps(r, sort_keys=True)
        if rid in seen:
            if seen[rid] != canon:
                return out, {"record_id": rid}
            continue
        seen[rid] = canon
        out.append(r)
    return out, None


# [S06 finding 5] a bare (offset-less) ts must parse as UTC, never crash a
# tz-aware comparison. compaction_ledger.parse_ts IS that rule — one
# definition, aliased here rather than re-typed (a review rework: this body was a
# third copy of the same parse rule).
_parse_ts = parse_ts


def _cohort_repr_ts(cohort):
    """The cohort's representative timestamp for --since/--last filtering: its
    LAST record's ts (the terminal record for complete/open cohorts, the
    latest-seen attempt for incomplete ones)."""
    recs = cohort.get("records") or []
    return recs[-1].get("ts") if recs else None


def filter_cohorts_since_last(cohorts, since, last):
    """Filter at the COHORT level, keyed on each cohort's representative ts
    [HARDENED finding 3]: the old record-level filter ran BEFORE cohorts were
    built, so it could drop an early attempt of a session while keeping its
    later (terminal) attempt — orphaning the terminal record and reporting the
    whole cohort INCOMPLETE, which this module's own docs define as ledger
    corruption. A cohort is now kept or dropped as one unit. `--last N` reads
    as "the last N cohorts" (the unit of analysis), not "the last N raw
    records"."""
    if since:
        floor = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        cohorts = [c for c in cohorts if (_parse_ts(_cohort_repr_ts(c)) or floor) >= floor]
    cohorts = sorted(cohorts, key=lambda c: _cohort_repr_ts(c) or "")
    if last:
        cohorts = cohorts[-last:]
    return cohorts


# --------------------------------------------------------------------------
# Cohorts
# --------------------------------------------------------------------------
def _cohort_key(rec):
    return (rec.get("project"), rec.get("plan"), rec.get("session"), rec.get("generation"))


def build_cohorts(records):
    """(complete, incomplete, open_) — each a list of
    {"key", "records" (sorted by attempt), "attempt1", "terminal"} dicts
    (attempt1/terminal omitted for incomplete cohorts)."""
    groups = defaultdict(list)
    for r in records:
        groups[_cohort_key(r)].append(r)

    complete, incomplete, open_ = [], [], []
    for key, recs in groups.items():
        recs = sorted(recs, key=lambda r: r.get("attempt") or 0)
        attempts = [r.get("attempt") for r in recs]
        if attempts and attempts == list(range(1, len(attempts) + 1)):
            cohort = {"key": key, "records": recs, "attempt1": recs[0], "terminal": recs[-1]}
            if cohort["terminal"].get("result") == "rework":
                open_.append(cohort)
            else:
                complete.append(cohort)
        else:
            incomplete.append({"key": key, "records": recs})
    return complete, incomplete, open_


def cell_key(cohort):
    a1 = cohort["attempt1"]
    return (
        a1.get("task_class") or "unknown",
        a1.get("model_authored") or "unknown",
        _norm_reasoning(a1.get("reasoning_authored")),
        a1.get("backend") or "unknown",
    )


def is_experimental(cohort):
    a1 = cohort["attempt1"]
    return a1.get("routing_provenance") == "experimental" and bool(a1.get("routing_experiment"))


def _as_epoch(value, default=None):
    """An SSOT version as an int, or None when it cannot be one.

    adoptions.ndjson is HAND-AUTHORED (routing-update/SKILL.md gives the record
    shape but no types), so `ssot_version` arrives as whatever the operator
    typed -- 16, "16", or "v16". The did-it-help boundary is an ORDERED
    comparison, so a string crashed the whole aggregation run with a TypeError
    instead of skipping one unusable record. Coerce both sides through here;
    `default` is what an ABSENT value means, while an unparseable one is always
    None so the caller can drop it rather than silently sort it to one side.
    """
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip().lstrip("vV"))
    except (TypeError, ValueError):
        return None


def is_default_current_epoch(cohort, epoch, floor=None):
    """Admissible for the proposal pool: default-resolved AND ran under a
    version whose routing values equal the current ones (floor..epoch, where
    floor is the SSOT's value_epoch; floor=None keeps the exact-match rule)."""
    a1 = cohort["attempt1"]
    if a1.get("routing_provenance") != "default_resolved":
        return False
    ran = _as_epoch(a1.get("ssot_version_ran"))
    if ran is None or epoch is None:
        return False
    lo = floor if floor is not None else epoch
    return lo <= ran <= epoch


def is_attested(cohort):
    """STRICTLY attested — proof of the served model. Reporting only."""
    return cohort["attempt1"].get("model_ran_source") == "attested"


def is_attributable(cohort):
    """Admissible as proposal evidence — see ADMISSIBLE_SOURCES."""
    return cohort["attempt1"].get("model_ran_source") in ADMISSIBLE_SOURCES


def _touches_fable(rec):
    ef = rec.get("escalated_from") or {}
    ran = ef.get("ran") or {}
    candidates = [rec.get("model_ran"), rec.get("tier_ran"), ran.get("model"), ran.get("model_id")]
    return any(isinstance(v, str) and "fable" in v.lower() for v in candidates)


# --------------------------------------------------------------------------
# Stats over a pool of cohorts
# --------------------------------------------------------------------------
def _cost(rec):
    """One attempt's worker cost from its rule-6 `usage` block, or None."""
    c = (rec.get("usage") or {}).get("cost_usd")
    return c if isinstance(c, (int, float)) and not isinstance(c, bool) else None


_DONE_RESULTS = ("passed", "done_unverified")


def _ran_cell(rec):
    return (rec.get("task_class") or "unknown", rec.get("model_ran") or "unknown",
            _norm_reasoning(rec.get("reasoning_ran")))


def cost_cells(records):
    """Worker cost per (task_class, model_ran, reasoning_ran) cell, two ways.

    Per attempt: the median `usage.cost_usd` over the cell's attempts that carry
    one, beside how many attempts there are and how many carry usage at all.

    Per completed session (operator decision): a session whose final
    attempt ended DONE sums the cost of EVERY attempt, rework and escalation
    included, and the sum goes to the cell of its FIRST attempt — so a cheap
    cell that fails and escalates is not reported as cheap. A session with a
    null-cost attempt, or a gap in its attempt numbers, is counted as
    incomplete-cost and kept out of the median."""
    cells = defaultdict(lambda: {"attempts": 0, "with_usage": 0, "costs": [], "sessions": [],
                                 "incomplete": 0})
    sessions = defaultdict(list)
    for r in records:
        cell = cells[_ran_cell(r)]
        cell["attempts"] += 1
        cell["with_usage"] += (r.get("usage") or {}).get("source") not in (None, "unavailable")
        if _cost(r) is not None:
            cell["costs"].append(_cost(r))
        sessions[(r.get("project"), r.get("plan"), r.get("session"))].append(r)
    for recs in sessions.values():
        recs.sort(key=lambda r: (r.get("generation") or 0, r.get("attempt") or 0))
        if recs[-1].get("result") not in _DONE_RESULTS:
            continue
        by_gen = defaultdict(list)
        for r in recs:
            by_gen[r.get("generation") or 0].append(r.get("attempt"))
        costs = [_cost(r) for r in recs]
        gapless = all(a == list(range(1, len(a) + 1)) for a in by_gen.values())
        cell = cells[_ran_cell(recs[0])]
        if None in costs or not gapless:
            cell["incomplete"] += 1
        else:
            cell["sessions"].append(sum(costs))
    return [{"task_class": k[0], "model_ran": k[1], "reasoning_ran": k[2],
             "attempts": c["attempts"], "attempts_with_usage": c["with_usage"],
             "attempts_with_cost": len(c["costs"]),
             "median_cost_per_attempt": round(median(c["costs"]), 6) if c["costs"] else None,
             "completed_sessions": len(c["sessions"]),
             "median_cost_per_completed_session": (round(median(c["sessions"]), 6)
                                                   if c["sessions"] else None),
             "incomplete_cost_sessions": c["incomplete"]}
            for k, c in sorted(cells.items())]


def _stats(cohorts):
    n = len(cohorts)
    if n == 0:
        return {"n": 0, "first_attempt_pass_rate": None, "escalation_rate": None,
                "attempts_per_success": None, "successful": 0, "cost_per_success": "n/a"}
    passes = sum(1 for c in cohorts if c["records"][0].get("result") == "passed")
    escalated = sum(1 for c in cohorts if any(r.get("escalated_from") for r in c["records"]))
    successful = [c for c in cohorts if c["terminal"].get("result") == "passed"]
    total_attempts = sum(len(c["records"]) for c in cohorts)
    # DOCUMENTED INTERPRETATION: "attempts-per-successful-outcome = total attempts
    # / successful cohorts" (plan text, literal) — total attempts across the WHOLE
    # pool (successes AND failures), divided by successful-cohort count. This is a
    # throughput/waste metric ("attempts burned per success delivered"), not a
    # "how many tries does a WINNING session usually take" average.
    attempts_per_success = round(total_attempts / len(successful), 3) if successful else None
    costs = [_cost(r) for c in cohorts for r in c["records"]]
    # Every attempt must carry a cost: a sum over the priced subset would read cheap.
    cost_per_success = (round(sum(costs) / len(successful), 4)
                        if successful and costs and None not in costs else "n/a")
    return {
        "n": n,
        "first_attempt_pass_rate": round(passes / n, 4),
        "escalation_rate": round(escalated / n, 4),
        "attempts_per_success": attempts_per_success,
        "successful": len(successful),
        "cost_per_success": cost_per_success,
    }


def aggregate_cell(cohorts, epoch, value_floor=None):
    facts_pool = [c for c in cohorts if c["terminal"].get("result") in PROPOSAL_ELIGIBLE_RESULTS]
    excluded = Counter(c["terminal"].get("result") for c in cohorts
                        if c["terminal"].get("result") in EXCLUDED_TERMINAL_RESULTS)
    proposal_pool = [c for c in facts_pool if is_default_current_epoch(c, epoch, value_floor)]
    attested_pool = [c for c in proposal_pool if is_attributable(c)]
    attested_share = (round(len([c for c in proposal_pool if is_attested(c)]) / len(proposal_pool), 4)
                      if proposal_pool else 0.0)
    return {
        "facts": _stats(facts_pool),
        "facts_pool_ts": [c["terminal"].get("ts") for c in facts_pool],
        "attested_pool_ts": [c["terminal"].get("ts") for c in attested_pool],
        "excluded": dict(excluded),
        "proposal_pool_n": len(proposal_pool),
        "attested_pool_n": len(attested_pool),
        "attested_share": attested_share,
        "attested_stats": _stats(attested_pool),
    }


def _proposal_id(task_class, model, reasoning, backend, kind):
    """Deliberately EPOCH-FREE [HARDENED finding 4]: a proposal_id used to hash
    in the SSOT epoch. A version bump mid-canary then re-issued a DIFFERENT id
    for the same cell/kind, so records already tagged
    `routing_experiment: {proposal_id: <old id>}` no longer matched
    `existing_canary_pids` computed under the new epoch — the aggregator opened
    a duplicate STAGE 1 SMOKE instead of recognizing the in-flight canary. The
    id is a pure function of (class, cell, kind); the epoch a proposal fired
    under is already reported separately as `ssot_version`."""
    blob = "|".join(str(x) for x in (task_class, model, reasoning, backend, kind))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def cell_proposal(key, agg, epoch, existing_canary_pids):
    """(proposal | None, status). status is always set, proposal only on fire."""
    task_class, model, reasoning, backend = key
    if agg["proposal_pool_n"] == 0:
        return None, "below_min_n"
    stats = agg["attested_stats"]
    n = stats["n"]
    if n < min(MIN_N_UPGRADE, MIN_N_DOWNGRADE_OPEN):
        return None, "below_min_n"
    pass_rate = stats["first_attempt_pass_rate"] or 0.0
    esc_rate = stats["escalation_rate"] or 0.0
    cell_label = f"{model}@{reasoning or 'unset'}"    # 'unset' matches escalation.py's rung_key convention (haiku has no dial)

    if n >= MIN_N_UPGRADE and (pass_rate < UPGRADE_PASS_MAX or esc_rate >= UPGRADE_ESCALATION_MIN):
        pid = _proposal_id(task_class, model, reasoning, backend, "upgrade")
        return {
            "proposal_id": pid, "kind": "upgrade", "class": task_class, "backend": backend,
            "current_cell": cell_label, "ssot_version": epoch, "n": n,
            "first_attempt_pass_rate": pass_rate, "escalation_rate": esc_rate,
            "recommendation": f"raise the {task_class} class default one rung above {cell_label}",
            "apply_path": "/routing-update",
        }, "upgrade"

    if (n >= MIN_N_DOWNGRADE_OPEN and pass_rate >= DOWNGRADE_OPEN_PASS_MIN
            and esc_rate <= DOWNGRADE_OPEN_ESCALATION_MAX):
        pid = _proposal_id(task_class, model, reasoning, backend, "downgrade")
        already = pid in existing_canary_pids
        return {
            "proposal_id": pid, "kind": "downgrade", "stage": "smoke-in-progress" if already else "open",
            "class": task_class, "backend": backend, "current_cell": cell_label,
            "ssot_version": epoch, "n": n, "first_attempt_pass_rate": pass_rate, "escalation_rate": esc_rate,
            "recommendation": (
                f"canary already underway for proposal_id {pid} — see canaries[]" if already else
                f"STAGE 1 SMOKE: route the next {SMOKE_N} sessions of {task_class} one rung below "
                f"{cell_label}, gates on, tagging routing_experiment: {{kind: 'canary', proposal_id: '{pid}'}}. "
                "Any failure aborts the experiment. A passing smoke alone NEVER adopts (rule of three) — "
                f"stage 2 needs N>={ADOPTION_MIN_N}, pass>={ADOPTION_PASS_MIN}, "
                f"attempts-per-success<={ADOPTION_ATTEMPTS_PER_SUCCESS_MAX}."
            ),
            "apply_path": "/routing-update",
        }, "downgrade-open"

    return None, "healthy"


