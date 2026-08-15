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
attempt-1's `routing_provenance`) whose `ssot_version_ran` matches the SSOT
this run reads (`--ssot`, default the live file) — pinned_override and
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
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro_scan  # noqa: E402 — sibling script; reused for its `prices:` parser

# --------------------------------------------------------------------------
# Constants — the aggregator's own statistical policy (NOT SSOT-sourced; class
# defaults and prices ARE read live from model-routing.yaml, never hardcoded —
# see class_defaults_from_ssot / _prices below).
# --------------------------------------------------------------------------
RESULTS = {"passed", "rework", "exhausted", "blocked", "done_unverified", "wontfix"}
PROPOSAL_ELIGIBLE_RESULTS = {"passed", "exhausted"}       # gate-checked outcomes
EXCLUDED_TERMINAL_RESULTS = {"blocked", "done_unverified", "wontfix"}

MIN_N_UPGRADE = 6
UPGRADE_PASS_MAX = 0.60
UPGRADE_ESCALATION_MIN = 0.50

MIN_N_DOWNGRADE_OPEN = 6            # deliberate call — see module docstring
DOWNGRADE_OPEN_PASS_MIN = 0.90
DOWNGRADE_OPEN_ESCALATION_MAX = 0.0

SMOKE_N = 3
ADOPTION_MIN_N = 10
ADOPTION_PASS_MIN = 0.90
ADOPTION_ATTEMPTS_PER_SUCCESS_MAX = 1.1

ATTESTED_SHARE_MIN = 0.5

MALFORMED_MAX_LINES = 3             # >= this many -> abort
MALFORMED_MAX_RATIO = 0.02          # > this ratio -> abort

APEX_REVISIT_THRESHOLD = 5


# --------------------------------------------------------------------------
# Paths (same anchor pattern as skills/plan-execute/scripts/outcomes.py and
# scripts/resolve_route.py — this file lives at <root>/skills/routing-retro/
# scripts/, same depth as skills/plan-execute/scripts/, so parents[3] == root).
# --------------------------------------------------------------------------
def _root():
    return Path(__file__).resolve().parents[3]


def default_ledger_path():
    return _root() / "evals" / "routing" / "outcomes.ndjson"


def default_ssot_path():
    return _root() / "model-routing.yaml"


def default_adoptions_path():
    return default_ledger_path().parent / "adoptions.ndjson"


def stamp_path(ledger_path):
    return Path(ledger_path).parent / ".last-aggregated"


def _import_resolver():
    """Import scripts/resolve_route.py — same lazy-sys.path trick outcomes.py's
    ``_run._import_resolver`` uses."""
    scripts_dir = str(_root() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import resolve_route  # noqa: PLC0415

    return resolve_route


# --------------------------------------------------------------------------
# SSOT reads — class defaults + prices, live, never hardcoded.
# --------------------------------------------------------------------------
def ssot_version(ssot_text):
    m = re.search(r"^version:\s*(\d+)", ssot_text, re.M)
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
            out[tc] = {"model": baseline.get("model_id"), "reasoning": baseline.get("native_effort")}
    return out


def prices_from_ssot(ssot_path):
    """{tier_name: (in_rate, out_rate)} — reused verbatim from retro_scan's
    parser rather than re-implemented (ladder rung 2: already in this codebase)."""
    try:
        return retro_scan.parse_ssot_prices(str(ssot_path))
    except SystemExit:
        return {}


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
    """(records, malformed_count, total_lines). A malformed line is skipped,
    never raises — the bound check in run() decides whether that's tolerable."""
    records, malformed, total = [], 0, 0
    for ln in text.split("\n"):
        if not ln.strip():
            continue
        total += 1
        try:
            records.append(json.loads(ln))
        except ValueError:
            malformed += 1
    return records, malformed, total


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


def _parse_ts(ts):
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_since_last(records, since, last):
    if since:
        floor = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        records = [r for r in records if (_parse_ts(r.get("ts")) or floor) >= floor]
    records = sorted(records, key=lambda r: r.get("ts") or "")
    if last:
        records = records[-last:]
    return records


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
        a1.get("reasoning_authored"),
        a1.get("backend") or "unknown",
    )


def is_experimental(cohort):
    a1 = cohort["attempt1"]
    return a1.get("routing_provenance") == "experimental" and bool(a1.get("routing_experiment"))


def is_default_current_epoch(cohort, epoch):
    a1 = cohort["attempt1"]
    return a1.get("routing_provenance") == "default_resolved" and a1.get("ssot_version_ran") == epoch


def is_attested(cohort):
    return cohort["attempt1"].get("model_ran_source") == "attested"


def _touches_fable(rec):
    ef = rec.get("escalated_from") or {}
    ran = ef.get("ran") or {}
    candidates = [rec.get("model_ran"), rec.get("tier_ran"), ran.get("model"), ran.get("model_id")]
    return any(isinstance(v, str) and "fable" in v.lower() for v in candidates)


# --------------------------------------------------------------------------
# Stats over a pool of cohorts
# --------------------------------------------------------------------------
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
    costs = [r["cost_usd"] for c in cohorts for r in c["records"] if isinstance(r.get("cost_usd"), (int, float))]
    cost_per_success = round(sum(costs) / len(successful), 4) if costs and successful else "n/a"
    return {
        "n": n,
        "first_attempt_pass_rate": round(passes / n, 4),
        "escalation_rate": round(escalated / n, 4),
        "attempts_per_success": attempts_per_success,
        "successful": len(successful),
        "cost_per_success": cost_per_success,
    }


def aggregate_cell(cohorts, epoch):
    facts_pool = [c for c in cohorts if c["terminal"].get("result") in PROPOSAL_ELIGIBLE_RESULTS]
    excluded = Counter(c["terminal"].get("result") for c in cohorts
                        if c["terminal"].get("result") in EXCLUDED_TERMINAL_RESULTS)
    proposal_pool = [c for c in facts_pool if is_default_current_epoch(c, epoch)]
    attested_pool = [c for c in proposal_pool if is_attested(c)]
    attested_share = round(len(attested_pool) / len(proposal_pool), 4) if proposal_pool else 0.0
    return {
        "facts": _stats(facts_pool),
        "facts_pool_ts": [c["terminal"].get("ts") for c in facts_pool],
        "excluded": dict(excluded),
        "proposal_pool_n": len(proposal_pool),
        "attested_pool_n": len(attested_pool),
        "attested_share": attested_share,
        "attested_stats": _stats(attested_pool),
    }


def _proposal_id(task_class, model, reasoning, backend, epoch, kind):
    blob = "|".join(str(x) for x in (task_class, model, reasoning, backend, epoch, kind))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def cell_proposal(key, agg, epoch, existing_canary_pids):
    """(proposal | None, status). status is always set, proposal only on fire."""
    task_class, model, reasoning, backend = key
    if agg["proposal_pool_n"] == 0:
        return None, "below_min_n"
    if agg["attested_share"] < ATTESTED_SHARE_MIN:
        return None, "attribution-limited"

    stats = agg["attested_stats"]
    n = stats["n"]
    if n < min(MIN_N_UPGRADE, MIN_N_DOWNGRADE_OPEN):
        return None, "below_min_n"
    pass_rate = stats["first_attempt_pass_rate"] or 0.0
    esc_rate = stats["escalation_rate"] or 0.0
    cell_label = f"{model}@{reasoning or 'unset'}"    # 'unset' matches escalation.py's rung_key convention (haiku has no dial)

    if n >= MIN_N_UPGRADE and (pass_rate < UPGRADE_PASS_MAX or esc_rate >= UPGRADE_ESCALATION_MIN):
        pid = _proposal_id(task_class, model, reasoning, backend, epoch, "upgrade")
        return {
            "proposal_id": pid, "kind": "upgrade", "class": task_class, "backend": backend,
            "current_cell": cell_label, "ssot_version": epoch, "n": n,
            "first_attempt_pass_rate": pass_rate, "escalation_rate": esc_rate,
            "recommendation": f"raise the {task_class} class default one rung above {cell_label}",
            "apply_path": "/routing-update",
        }, "upgrade"

    if (n >= MIN_N_DOWNGRADE_OPEN and pass_rate >= DOWNGRADE_OPEN_PASS_MIN
            and esc_rate <= DOWNGRADE_OPEN_ESCALATION_MAX):
        pid = _proposal_id(task_class, model, reasoning, backend, epoch, "downgrade")
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


# --------------------------------------------------------------------------
# Canaries — experimental cohorts, keyed by proposal_id (never inferred from
# class defaults).
# --------------------------------------------------------------------------
def aggregate_canaries(experimental_cohorts, adoptions):
    by_pid = defaultdict(list)
    for c in experimental_cohorts:
        pid = ((c["attempt1"].get("routing_experiment") or {}).get("proposal_id"))
        if pid:
            by_pid[pid].append(c)

    out = []
    for pid, cohorts in by_pid.items():
        cohorts_sorted = sorted(cohorts, key=lambda c: c["terminal"].get("ts") or "")
        eligible = [c for c in cohorts_sorted if c["terminal"].get("result") in PROPOSAL_ELIGIBLE_RESULTS]
        excluded = Counter(c["terminal"].get("result") for c in cohorts_sorted
                            if c["terminal"].get("result") in EXCLUDED_TERMINAL_RESULTS)
        stats = _stats(eligible)
        first_batch = cohorts_sorted[:SMOKE_N]
        smoke_failed = len(first_batch) >= SMOKE_N and any(
            c["terminal"].get("result") != "passed" for c in first_batch
        )
        n = stats["n"]
        adoption_ready = (
            not smoke_failed and n >= ADOPTION_MIN_N
            and (stats["first_attempt_pass_rate"] or 0) >= ADOPTION_PASS_MIN
            and stats["attempts_per_success"] is not None
            and stats["attempts_per_success"] <= ADOPTION_ATTEMPTS_PER_SUCCESS_MAX
        )
        if smoke_failed:
            stage = "smoke-failed"
        elif n < SMOKE_N:
            stage = "smoke-in-progress"
        elif n < ADOPTION_MIN_N:
            stage = "smoke-passed-awaiting-adoption-evidence"
        elif adoption_ready:
            stage = "adoption-ready"
        else:
            stage = "adoption-evidence-insufficient"

        a1 = cohorts_sorted[0]["attempt1"]
        out.append({
            "proposal_id": pid,
            "class": a1.get("task_class"),
            "kind": (a1.get("routing_experiment") or {}).get("kind"),
            "cell": f"{a1.get('model_authored')}@{a1.get('reasoning_authored')}",
            "backend": a1.get("backend"),
            "stage": stage,
            "smoke_failed": smoke_failed,
            "adoption_ready": adoption_ready,
            "stats": stats,
            "excluded": dict(excluded),
            "adopted": pid in adoptions,
            "apply_path": "/routing-update",
        })
    return out


# --------------------------------------------------------------------------
# Apex revisit callout (SSOT-01 trigger — ESC-01's fable escalation apex).
# --------------------------------------------------------------------------
def apex_revisit(complete_cohorts):
    count = sum(1 for c in complete_cohorts if any(_touches_fable(r) for r in c["records"]))
    fires = count >= APEX_REVISIT_THRESHOLD
    return {
        "cumulative_fable_escalation_cohorts": count,
        "callout": fires,
        "message": ("apex revisit due — review whether the fable climbs converted (ssot-01 trigger)"
                    if fires else None),
    }


# --------------------------------------------------------------------------
# Time-to-signal — projects when each threshold class could next fire, so an
# empty proposals[] reads as healthy patience rather than a dead loop.
# --------------------------------------------------------------------------
def _cadence_per_month(ts_list):
    ts = sorted(t for t in (_parse_ts(t) for t in ts_list) if t is not None)
    if len(ts) < 2:
        return None
    span_days = max((ts[-1] - ts[0]).total_seconds() / 86400, 1.0)
    return round(len(ts) / (span_days / 30.44), 3)


def _project(n, min_n, cadence, now):
    if n >= min_n:
        return {"status": "threshold_reachable_now", "message": "sample-size floor already met"}
    if not cadence:
        return {"status": "no_data", "message": "insufficient history to estimate cadence (need >= 2 resolved cohorts)"}
    months = (min_n - n) / cadence
    fire_date = (now + timedelta(days=months * 30.44)).date().isoformat()
    return {
        "status": "silence_expected", "months": round(months, 1), "earliest_fire_date": fire_date,
        "message": f"silence expected until ~{round(months, 1)} months (earliest {fire_date}) "
                   f"at current cadence ({cadence}/mo)",
    }


def time_to_signal(class_defaults, provider, cells_by_key, now):
    backend = "claude" if provider == "anthropic" else "codex"
    out = []
    for task_class, cell in sorted(class_defaults.items()):
        key = (task_class, cell["model"], cell["reasoning"], backend)
        agg = cells_by_key.get(key)
        n = agg["facts"]["n"] if agg else 0
        cadence = _cadence_per_month(agg["facts_pool_ts"]) if agg else None
        out.append({
            "class": task_class,
            "cell": f"{cell['model']}@{cell['reasoning'] or 'unset'}",
            "current_n": n,
            "cadence_per_month": cadence,
            "upgrade_projection": _project(n, MIN_N_UPGRADE, cadence, now),
            "downgrade_open_projection": _project(n, MIN_N_DOWNGRADE_OPEN, cadence, now),
        })
    return out


# --------------------------------------------------------------------------
# Adoption registry (read-only, best-effort — s07 wires the writer)
# --------------------------------------------------------------------------
def read_adoptions(path=None):
    path = Path(path) if path else default_adoptions_path()
    ids = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ids
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        pid = rec.get("proposal_id")
        if pid:
            ids[pid] = rec
    return ids


# --------------------------------------------------------------------------
# .last-aggregated stamp
# --------------------------------------------------------------------------
def write_stamp(ledger_path, boundary, total_lines, now):
    """Schema matches the placeholder `evals/routing/.last-aggregated` stub
    committed alongside the ledger writer itself (TEL-01, b5d2dc2):
    `{"offset": 0, "size": 0, "mtime": 0, "mean_record_size": 0}` — same keys,
    filled in for real, plus `stamped_at` (additive, nothing reads it yet)."""
    mean = round(boundary / total_lines, 1) if total_lines else 0
    try:
        mtime = os.path.getmtime(ledger_path)
    except OSError:
        mtime = None
    stamp_path(ledger_path).write_text(json.dumps({
        "offset": boundary, "size": boundary, "mtime": mtime,
        "mean_record_size": mean, "stamped_at": now.isoformat(),
    }, indent=1) + "\n")


# --------------------------------------------------------------------------
# Markdown proposal block
# --------------------------------------------------------------------------
def render_markdown(cell_entries, canaries, apex, epoch):
    lines = [
        "# Routing outcome proposals",
        "",
        f"SSOT epoch v{epoch}. **Apply path: `/routing-update` only** — this script "
        "never edits model-routing.yaml.",
        "",
    ]
    fired = [c for c in cell_entries if c.get("proposal")]
    canary_calls = [c for c in canaries if c["stage"] in ("adoption-ready", "smoke-failed")]
    if not fired and not canary_calls and not apex.get("callout"):
        lines.append("No proposals this run — every cell is either below its minimum sample "
                      "size or within the healthy range. See `time_to_signal` for when that "
                      "could next change.")
        lines.append("")
    for c in fired:
        p = c["proposal"]
        lines.append(f"## {p['kind'].upper()} — {p['class']} ({p['current_cell']}) "
                      f"— proposal_id `{p['proposal_id']}`")
        lines.append(f"- N={p['n']}, first-attempt pass={p['first_attempt_pass_rate']}, "
                      f"escalation={p['escalation_rate']}")
        lines.append(f"- {p['recommendation']}")
        lines.append("")
    for cn in canary_calls:
        lines.append(f"## CANARY {cn['stage'].upper()} — {cn['class']} ({cn['cell']}) "
                      f"— proposal_id `{cn['proposal_id']}`")
        st = cn["stats"]
        lines.append(f"- N={st['n']}, pass={st['first_attempt_pass_rate']}, "
                      f"attempts/success={st['attempts_per_success']}")
        lines.append("")
    if apex.get("callout"):
        lines.append(f"## APEX REVISIT DUE — {apex['message']}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Orchestration
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


def run(ledger_path, ssot_path, since=None, last=None, now=None, adoptions_path=None):
    now = now or datetime.now(UTC)
    ledger_path = Path(ledger_path)
    boundary = snapshot_boundary(ledger_path)
    text = read_prefix(ledger_path, boundary)
    records, malformed, total_lines = parse_lines(text)
    ratio = (malformed / total_lines) if total_lines else 0.0
    if malformed >= MALFORMED_MAX_LINES or ratio > MALFORMED_MAX_RATIO:
        return _fail(f"malformed lines {malformed}/{total_lines} ({ratio:.1%}) exceeds the "
                     f"tolerance ({MALFORMED_MAX_LINES} lines / {MALFORMED_MAX_RATIO:.0%})",
                     malformed, total_lines, boundary)

    records, conflict = dedup_records(records)
    if conflict:
        return _fail(f"conflicting duplicate record_id {conflict['record_id']!r} — two DIFFERENT "
                     "records share one id (ledger corruption, not a replay)",
                     malformed, total_lines, boundary, conflict=conflict)

    records = filter_since_last(records, since, last)

    ssot_text = Path(ssot_path).read_text(encoding="utf-8")
    epoch = ssot_version(ssot_text)
    provider = active_provider(ssot_text)
    class_defaults = class_defaults_from_ssot(ssot_path, provider)
    prices = prices_from_ssot(ssot_path)
    adoptions = read_adoptions(adoptions_path)

    complete, incomplete, open_cohorts = build_cohorts(records)
    experimental = [c for c in complete if is_experimental(c)]
    non_experimental = [c for c in complete if not is_experimental(c)]
    existing_canary_pids = {
        pid for c in experimental
        if (pid := (c["attempt1"].get("routing_experiment") or {}).get("proposal_id"))
    }

    cells_by_key = defaultdict(list)
    for c in non_experimental:
        cells_by_key[cell_key(c)].append(c)

    cell_aggs = {key: aggregate_cell(cohorts, epoch) for key, cohorts in cells_by_key.items()}

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

    canaries = aggregate_canaries(experimental, adoptions)
    apex = apex_revisit(complete)
    tts = time_to_signal(class_defaults, provider, cell_aggs, now)
    markdown = render_markdown(cell_entries, canaries, apex, epoch)

    out = {
        "ok": True,
        "generated_at": now.isoformat(),
        "ssot_version": epoch,
        "active_provider": provider,
        "ledger": {"snapshot_bytes": boundary, "total_lines": total_lines, "malformed": malformed,
                   "malformed_ratio": round(ratio, 4), "records_parsed": len(records)},
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
        "proposals": (
            [c["proposal"] for c in cell_entries if c["proposal"]]
            + [c for c in canaries if c["stage"] == "adoption-ready"]
        ),
        "markdown": markdown,
    }
    write_stamp(ledger_path, boundary, total_lines, now)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ledger", nargs="?", default=None, help="path to outcomes.ndjson (default: evals/routing/outcomes.ndjson)")
    ap.add_argument("--since", default=None, help="only records with ts on/after YYYY-MM-DD")
    ap.add_argument("--last", type=int, default=None, help="keep only the last N records by ts")
    ap.add_argument("--ssot", default=None, help="path to model-routing.yaml (default: the live repo file)")
    args = ap.parse_args()

    ledger_path = Path(args.ledger) if args.ledger else default_ledger_path()
    ssot_path = Path(args.ssot) if args.ssot else default_ssot_path()

    out = run(ledger_path, ssot_path, since=args.since, last=args.last)
    json.dump(out, sys.stdout, indent=1)
    print()
    sys.exit(0 if out.get("ok", True) else 1)


if __name__ == "__main__":
    main()
