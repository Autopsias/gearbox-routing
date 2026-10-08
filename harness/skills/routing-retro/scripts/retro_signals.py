#!/usr/bin/env python3
"""Canaries, the apex revisit callout, time-to-signal and did-it-help.

Split out of aggregate_outcomes.py, which stood at 1026 lines against a 500-line
house limit. Everything here answers a question ABOUT the aggregated cells
rather than producing them: which experiments are ready to adopt, when a cell
could next fire, and whether a past adoption actually helped.

Canaries are keyed by proposal_id and NEVER inferred from the cell shape — an
inferred key silently merges two experiments that happened to share a cell.
"""
import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from retro_cohorts import _as_epoch, _parse_ts, _stats, _touches_fable
from retro_common import (
    ADOPTION_ATTEMPTS_PER_SUCCESS_MAX, ADOPTION_MIN_N, ADOPTION_PASS_MIN,
    APEX_REVISIT_THRESHOLD, EXCLUDED_TERMINAL_RESULTS,
    MIN_N_DOWNGRADE_OPEN, MIN_N_UPGRADE, PROPOSAL_ELIGIBLE_RESULTS, SMOKE_N,
    default_adoptions_path, stamp_path,
)

# --------------------------------------------------------------------------
# Canaries — experimental cohorts, keyed by proposal_id (never inferred from
# class defaults).
# --------------------------------------------------------------------------
def _effort_unbound(cohort):
    """A Claude cohort whose attempt 1 did not run through a tier agent never ran at the
    effort under test — a definition-less subagent inherits the orchestrator's session
    effort. Codex is exempt: that lane binds effort through `-c model_reasoning_effort=`
    and records `prompt_directive_advisory` on every row by design."""
    a1 = cohort["attempt1"]
    return a1.get("backend") == "claude" and a1.get("effort_mechanism") != "tier_agent"


def aggregate_canaries(experimental_cohorts, adoptions):
    by_pid = defaultdict(list)
    for c in experimental_cohorts:
        pid = ((c["attempt1"].get("routing_experiment") or {}).get("proposal_id"))
        if pid:
            by_pid[pid].append(c)

    out = []
    for pid, cohorts in by_pid.items():
        cohorts_sorted = sorted(cohorts, key=lambda c: c["terminal"].get("ts") or "")
        # v25 (2026-09-11): 4abfa765d726 counted five opus@low cohorts that all ran at the
        # orchestrator's effort. An unbound cohort is no evidence either way, so it
        # leaves the smoke batch and every statistic and is counted once, by name.
        unbound = [c for c in cohorts_sorted if _effort_unbound(c)]
        bound = [c for c in cohorts_sorted if not _effort_unbound(c)]
        eligible = [c for c in bound if c["terminal"].get("result") in PROPOSAL_ELIGIBLE_RESULTS]
        excluded = Counter(c["terminal"].get("result") for c in bound
                            if c["terminal"].get("result") in EXCLUDED_TERMINAL_RESULTS)
        if unbound:
            excluded["effort_unbound"] = len(unbound)
        stats = _stats(eligible)
        # DELIBERATE CHOICE [HARDENED finding 1]: the smoke batch is drawn from
        # `eligible` (gate-checked passed/exhausted only), never `cohorts_sorted`
        # (which also holds blocked/done_unverified/wontfix). A `blocked` session
        # is administrative absence of evidence, not a failure of the model
        # under test — it must not be able to flip a canary to smoke-failed. It
        # simply doesn't count toward the 3-cohort smoke batch at all.
        first_batch = eligible[:SMOKE_N]
        # [S06 finding 2] "any failure aborts the experiment" (plan text) means
        # the MOMENT a failure appears in the smoke batch, not once 3 gate-
        # checked cohorts have accumulated — requiring len(first_batch)>=SMOKE_N
        # let a cohort-1 or cohort-2 failure report "smoke-in-progress" (this
        # skill's own SKILL.md defines that as "no action") instead of the
        # documented immediate abort.
        smoke_failed = any(c["terminal"].get("result") != "passed" for c in first_batch)
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
        # [S06 finding 3] proposals gate on the attested/default-current-epoch
        # pool (see cell_proposal, which compares MIN_N_UPGRADE/
        # MIN_N_DOWNGRADE_OPEN against attested_pool_n), not the FACTS pool —
        # facts.n also counts pinned_override/prior-epoch cohorts that can
        # never feed a proposal. Using facts.n here made a cell with 8
        # pinned_override cohorts report "sample-size floor already met" while
        # its status stayed below_min_n forever (proposal_pool_n: 0).
        n = agg["attested_pool_n"] if agg else 0
        # [S06 round-3 finding 2] the cadence MUST come from the same pool as
        # `n`. Taking N from the attested pool but the rate from the facts pool
        # projects a date that can never arrive: if nothing is ever attested the
        # attested pool stays at 0 while the facts pool keeps growing, so the
        # projection promised "earliest 2026-09-19 at 5.218/mo" against a
        # current_n frozen at 0. A zero-growth pool must read as "never at the
        # current cadence", which is the honest answer.
        cadence = _cadence_per_month(agg["attested_pool_ts"]) if agg else None
        out.append({
            "class": task_class,
            "cell": f"{cell['model']}@{cell['reasoning'] or 'unset'}",
            "current_n": n,
            "cadence_per_month": cadence,
            "upgrade_projection": _project(n, MIN_N_UPGRADE, cadence, now),
            "downgrade_open_projection": _project(n, MIN_N_DOWNGRADE_OPEN, cadence, now),
        })
    return out


DID_IT_HELP_MIN_N = 6  # matches MIN_N_UPGRADE — the same statistical floor this
                        # module already treats as "enough to trust a rate"


def did_it_help(non_experimental_cohorts, adoptions):
    """For every ADOPT action in `adoptions` (one row per proposal_id — the dict
    a `read_adoptions()`-shaped mapping already keeps only the LATEST record per
    id, so a rollback naturally supersedes its own prior adopt), compare the
    affected class's VERIFIED (gate-checked passed/exhausted) cohorts BY
    `ssot_version_ran` — before vs after the SSOT version THAT adoption produced.

    Never by calendar date [HARDENED:codex-verify-r3 — a source adoption is not
    live in the ledger until `gearbox deploy` ships it; grouping by wall-clock
    date would let pre-deploy outcomes (still running the OLD routing) leak into
    the "after" cohort just because they happened to run late]. Canary-tagged
    (experimental) cohorts are excluded here — they are already reported
    per-proposal_id in `canaries[]`; this comparison is the class-wide DEFAULT
    population before/after the adopted change. Always reports N on both sides;
    below `DID_IT_HELP_MIN_N` on either side reports "underpowered" rather than
    a verdict — a comparison that can't support a claim must say so, not guess.
    """
    by_class = defaultdict(list)
    for c in non_experimental_cohorts:
        if c["terminal"].get("result") in PROPOSAL_ELIGIBLE_RESULTS:
            by_class[c["attempt1"].get("task_class")].append(c)

    out = []
    for pid, rec in sorted(adoptions.items()):
        if rec.get("action") != "adopt":
            continue  # a rollback record supersedes the adopt above via dict overwrite
        task_class = rec.get("class")
        boundary = _as_epoch(rec.get("ssot_version"))
        if task_class is None or boundary is None:
            continue  # unusable adoption record -- skip IT, never the whole run
        pool = by_class.get(task_class, [])
        # NO `default=` here, deliberately. An absent/null ssot_version_ran is an
        # UNKNOWN version, not version 0: outcomes._ssot_version_ran() returns None
        # whenever the routing file is unreadable or carries no `version:` line, and
        # that condition is GLOBAL, not per-record. Defaulting it to 0 put every
        # cohort written during such a period into "before" -- counting new-routing
        # outcomes as evidence for the old routing, which inflates the before pass
        # rate into a false "no_improvement", or leaves "after" at N=0 reporting
        # "underpowered" forever with no sign that data is missing. Unknown means
        # unplaceable: excluded from BOTH buckets, so the reported Ns are honest.
        placed = [(c, _as_epoch(c["attempt1"].get("ssot_version_ran"))) for c in pool]
        before = [c for c, v in placed if v is not None and v < boundary]
        after = [c for c, v in placed if v is not None and v >= boundary]
        before_stats, after_stats = _stats(before), _stats(after)
        underpowered = before_stats["n"] < DID_IT_HELP_MIN_N or after_stats["n"] < DID_IT_HELP_MIN_N
        if underpowered:
            verdict = "underpowered"
        else:
            b_rate = before_stats["first_attempt_pass_rate"] or 0.0
            a_rate = after_stats["first_attempt_pass_rate"] or 0.0
            verdict = "helped" if a_rate > b_rate else "no_improvement"
        out.append({
            "proposal_id": pid, "class": task_class,
            "old_rung": rec.get("old_rung"), "new_rung": rec.get("new_rung"),
            "ssot_version": boundary, "date": rec.get("date"),
            "before": before_stats, "after": after_stats, "verdict": verdict,
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
    try:
        stamp_path(ledger_path).write_text(json.dumps({
            "offset": boundary, "size": boundary, "mtime": mtime,
            "mean_record_size": mean, "stamped_at": now.isoformat(),
        }, indent=1) + "\n")
    except OSError:
        # [S06 finding 6] the ledger's parent directory not existing yet is
        # handled everywhere else in this module by degrading to a valid
        # empty result (snapshot_boundary/read_prefix both return 0/"" on a
        # missing path) — writing the stamp should degrade the same way
        # instead of raising an uncaught FileNotFoundError and killing the
        # whole run over a housekeeping side effect.
        pass


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
