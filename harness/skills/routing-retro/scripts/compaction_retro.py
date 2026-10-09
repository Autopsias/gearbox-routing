#!/usr/bin/env python3
"""compaction_retro.py — the "did it help" half of /routing-retro for the
compaction work.

Builds ONE JSON block from retro_scan.py's session stats plus the
~/.gearbox-state/compaction/ ledgers. render_report.py renders it; arming_check.py reads
the same functions. Nothing here computes a cohort from a single ledger
timestamp: exposure is per intervention, from that intervention's own
activation row (see compaction_ledger.cohort_label).

Usage:
  retro_scan.py --last 200 --since 2026-08-01 > /tmp/scan.json
  compaction_retro.py --scan /tmp/scan.json > /tmp/compaction.json
"""

import glob
import json
import os

from compaction_ledger import (
    INTERVENTIONS,
    cohort_label,
    compaction_rows,
    ctx_pair_rows,
    decision_counts,
    measurement_unavailable,
    parse_ts,
)
# Re-exported: every caller reaches these as cr.load_classifier/cr.retro_classify.
from retro_classifier import load_classifier, retro_classify  # noqa: F401

# The same statistical floor retro_signals.DID_IT_HELP_MIN_N uses for the
# routing did-it-help check — one floor, one vocabulary, both halves of the skill.
MIN_N = 6

# An intervention that acts THROUGH compaction cannot be credited — or blamed —
# for a cost change in sessions where no compaction was ever attempted. Below
# this many compaction records in the after cohort, the hooks' cost delta is a
# statement about what work happened to run, not about the hooks.
MECHANISM_MIN_RECORDS = 5
MECHANISM_INTERVENTIONS = ("hooks",)

VERDICTS = ("helped", "no_improvement", "underpowered", "confounded",
            "no_baseline", "no_exposure", "activation_unknown")

# The rollout's projection: a retrospective model over historical sessions put a
# 250k window at ~29% saving and 300k at ~22%. PROJECTED, NOT MEASURED — it is
# the number the rollout checkpoint chose from, so the report prints predicted vs
# realized rather than treating it as an established result.
PREDICTION = {250000: 29.0, 300000: 22.0}

# THE HOOK OWNS THIS DEFINITION. dispatch-audit.py writes the ledger and exports
# is_unpinned_generic; a second copy of the set here is what let the two drift —
# the local tuple omitted None, so 80 of 1413 live rows (15.1% vs the hook's
# 20.7%) were scored compliant on the ONE measure that can say "cheaper AND
# worse". Import it; never restate it.
def _load_is_unpinned_generic():
    import importlib.util                                       # noqa: PLC0415
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "hooks", "dispatch-audit.py"))
    spec = importlib.util.spec_from_file_location("_dispatch_audit", path)
    if spec is None or spec.loader is None:                      # pragma: no cover
        raise ImportError(f"dispatch-audit.py not importable at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.is_unpinned_generic


is_unpinned_generic = _load_is_unpinned_generic()

DISPATCH_LEDGER = os.path.expanduser("~/.gearbox-state/routing/dispatch-audit.ndjson")

# repo_diet's repositories: set GEARBOX_REPO_DIET_REPOS to a comma-separated list of
# repo directory names. Unset (the default), the repo-scoped cohort matches nothing.
REPO_DIET_REPOS = tuple(r.strip() for r in os.environ.get("GEARBOX_REPO_DIET_REPOS", "").split(",")
                        if r.strip())

REPO_ATTRIBUTION_LIMIT = (
    "decisions.ndjson and sessions.ndjson record no working directory, so a "
    "compaction ledger line CANNOT be attributed to a repository. repo_diet "
    "covers three named repositories only; its apparent after-cohort "
    "counts sessions that may never have opened any of them. Reported, not "
    "fixed: adding a cwd field now would change shipped code inside the plan's "
    "own measurement session and would give nothing retroactively."
)


def _pct(num, den, digits=1):
    return round(100.0 * num / den, digits) if den else None


def _mean(values, digits=4):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), digits) if vals else None


def _p50(values):
    vals = sorted(v for v in values if v is not None)
    return vals[len(vals) // 2] if vals else None


# --------------------------------------------------------------------------
# Per-session facts
# --------------------------------------------------------------------------
def index_sessions(scan, heartbeats, policy_types):
    """session id -> the merged per-session row the cohorts are built from.

    Universe = every session the scanner saw UNION every session that left a
    heartbeat. THE DENOMINATOR IS SESSIONS OBSERVED, NOT COMPACTION RECORDS: a
    compaction record only exists when a compaction is attempted, so the better
    the change works the thinner the ledger, and a fully successful rollout
    would otherwise look identical to a hook that silently stopped firing.
    """
    rows = {}
    for s in scan.get("sessions", []):
        sid = s.get("session_id")
        if not sid:
            continue
        rows[sid] = {
            "session_id": sid,
            "started": parse_ts(s.get("started")),
            "ended": parse_ts(s.get("ended")),
            "cost_usd": s.get("cost_usd"),
            "base_context_tokens": s.get("base_context_tokens"),
            "context_peak_tokens": s.get("context_peak_tokens"),
            "reread_cost_pct": s.get("reread_cost_pct"),
            "transcript_compactions": s.get("compactions"),
            "dispatches": len(s.get("agent_dispatches") or []),
            "pinned_dispatches": sum(1 for d in (s.get("agent_dispatches") or []) if d.get("model")),
            "entrypoint": s.get("entrypoint"),
            "scanned": True,
        }
    for sid, hb in heartbeats.items():
        row = rows.setdefault(sid, {"session_id": sid, "started": hb["_ts"], "ended": None,
                                    "scanned": False})
        # NOT setdefault: the scanned row already HAS a 'started' key, whose value
        # is None when the transcript's timestamps would not parse. setdefault
        # could therefore never fire, and the session stayed undated here while
        # arming_check._sessions_from dated it from the same heartbeat — the two
        # tools SKILL.md tells the operator to run together then disagreed about
        # the same session (n_after=0 vs after_sessions=1).
        if row.get("started") is None:
            row["started"] = hb["_ts"]
        # Only the two fields classify_silence reads. The heartbeat also carries
        # `type` (first classification — superseded by the policy file, which is
        # the one type source) and `policy_version`; carrying them here gave them
        # no reader, and a field nobody reads is a claim nobody checks.
        row["heartbeat"] = {"kill_switch": hb.get("kill_switch"),
                            "hooks_registered": hb.get("hooks_registered")}
    for sid, row in rows.items():
        # Live type from the policy file; the heartbeat holds only the FIRST
        # classification, which an upgrade may have superseded.
        if sid in policy_types:
            row["session_type"] = policy_types[sid]
            row["type_source"] = "policy"
        else:
            row["session_type"] = None      # retro-classified later, or 'untyped'
            row["type_source"] = None
    return rows


def attach_decisions(rows, decisions, policy_version):
    """Fold spine-valid decisions into their sessions. `source: 'probe'` lines are
    hand tests, not a rollout, and are excluded from every cohort."""
    excluded_probe = 0
    stale_version = 0
    for d in decisions:
        if d.get("source") == "probe":
            excluded_probe += 1
            continue
        sid = d["session"]
        row = rows.setdefault(sid, {"session_id": sid, "started": d["_ts"], "ended": None,
                                    "scanned": False, "session_type": None, "type_source": None})
        if policy_version is not None and d.get("policy_version") != policy_version:
            stale_version += 1
            row.setdefault("stale_records", 0)
            row["stale_records"] += 1
            continue
        row.setdefault("records", []).append(d)
    for row in rows.values():
        recs = row.get("records") or []
        comp = compaction_rows(recs)
        counts = decision_counts(recs)
        row["ledger_records"] = len(recs)
        row["compaction_records"] = len(comp)
        # The writer emits "block"/"allow" on compaction rows and nothing else;
        # decision_counts is the ONE reader of that vocabulary, and it counts a
        # value it does not recognise instead of silently zeroing a column.
        row["vetoes"] = counts["vetoes"]
        row["allows"] = counts["allows"]
        row["unrecognized_decisions"] = counts["unrecognized"]
        # A crash row's decision='allow' is the fail-open wrapper's stamp, not a
        # decision; decision_counts separates it so the allow rate stays honest.
        row["hook_errors"] = counts["hook_errors"]
        row["measurement_unavailable"] = sum(
            1 for r in comp if measurement_unavailable(r))
        # ONE POPULATION FOR THE SHRINK QUESTION. ctx_before and ctx_after are
        # compared on the page, so both stats must come from the SAME rows: the
        # ones carrying BOTH fields (compact_reorient writes both on the auto
        # `compacted` row; a manual compaction writes ctx_after alone). Two
        # independent lists here let p50-before (7 auto rows) be read against
        # mean-after (those 7 plus 4 manual rows) — different sets of
        # compactions, and on live data the mismatch reversed the sign of the
        # answer. One-sided rows are excluded from both stats and counted.
        #
        # THE PAIR POPULATION IS NOT `comp`. A `compacted` row now always
        # carries ctx_after=None: the post-compaction context cannot be read
        # inside the PostCompact hook, so a later prompt emits a
        # `compaction-measured` row with the real number. Reading pairs off
        # `comp` alone would report every compaction as one-sided forever.
        pairs = ctx_pair_rows(recs)
        # A deferred `compacted` row is a placeholder for a measurement that is
        # still owed, not a one-sided record — it must not inflate that count.
        def _awaiting(r):
            return bool(r.get("ctx_after_deferred")) and r.get("ctx_after") is None

        settled = [r for r in pairs if not _awaiting(r)]
        n_pending = sum(1 for r in pairs if _awaiting(r))
        row["ctx_pairs"] = [
            (r["ctx_before"], r["ctx_after"]) for r in settled
            if isinstance(r.get("ctx_before"), int) and isinstance(r.get("ctx_after"), int)]
        row["ctx_one_sided"] = sum(
            1 for r in settled
            if isinstance(r.get("ctx_before"), int) != isinstance(r.get("ctx_after"), int))
        row["ctx_awaiting_measurement"] = n_pending
    return {"probe_lines_excluded": excluded_probe, "stale_policy_version_lines": stale_version}


def classify_silence(row):
    """Every session with NO compaction record classifies as exactly one of these.

    `unknown` (no heartbeat) is excluded from every verdict. Its count reaches
    the report as `excluded_unknown_sessions`, computed by build() from the same
    rule — every caller HERE passes only heartbeat-bearing rows, so this
    function's own `unknown` branch is a guard, not the source of that number."""
    if row.get("compaction_records"):
        return "has_records"
    hb = row.get("heartbeat")
    if hb is None:
        return "unknown"
    if hb.get("kill_switch") == "on" or hb.get("hooks_registered") is False:
        return "hook_inactive"
    return "no_compaction"


# --------------------------------------------------------------------------
# Exposure + verdicts
# --------------------------------------------------------------------------
def build_cohorts(rows, activations):
    """{intervention: {status, before[], after[], spanning[], undated}} — built
    ONLY from that intervention's own activation row. No global boundary exists."""
    out = {}
    for iv in INTERVENTIONS:
        act = activations["interventions"][iv]
        entry = {"status": act["status"], "activation_ts": None,
                 "before": [], "after": [], "spanning": [], "undated": 0,
                 "prior_stages": [r.get("event_id") for r in act.get("prior_stages", [])]}
        if act["status"] == "exposed":
            entry["activation_ts"] = act["ts"].isoformat()
            for sid, row in rows.items():
                label = cohort_label(row.get("started"), row.get("ended"), act["ts"])
                if label is None:
                    entry["undated"] += 1
                else:
                    entry[label].append(sid)
        out[iv] = entry
    return out


# The distinguishing set is a COHORT, so it carries the same floor every other
# cohort does: two sessions between two deploys are not a contrast.
SEPARATION_MIN_SESSIONS = MIN_N


def bundled_with(cohorts):
    """`confounded` is REQUIRED whenever no isolating contrast exists — that is,
    whenever two interventions' after-cohorts are INDISTINGUISHABLE.

    NOT a subset test. Activations are ordered in time, so a later intervention's
    after-cohort is a subset of an earlier one's BY CONSTRUCTION, always — which
    made every intervention but the first read `confounded` forever, and made the
    staggered re-deploy this session's own decision card recommends incapable of
    ever lifting the flag. What separates two interventions is the sessions
    exposed to one and not the other: that symmetric difference IS the isolating
    contrast, and it is empty exactly when the two went live together.
    """
    out = {}
    for iv, c in cohorts.items():
        mine = set(c["after"])
        peers = []
        if c["status"] == "exposed" and mine:
            for other, oc in cohorts.items():
                if other == iv or oc["status"] != "exposed":
                    continue
                if len(mine ^ set(oc["after"])) < SEPARATION_MIN_SESSIONS:
                    peers.append(other)
        out[iv] = peers
    return out


def co_active(cohorts):
    """SAY THE ATTRIBUTION LIMIT OUT LOUD; DO NOT LET `confounded` STAND IN FOR IT.

    Lifting `confounded` off a staggered rollout does NOT make the contrast
    clean: another intervention may still have changed state part-way through
    this one's before- or after-cohort. That is a measurable fact — how many of
    this cohort's sessions were already exposed to the other — and it is stated
    beside the verdict rather than collapsed into a verdict word.
    """
    out = {}
    for iv, c in cohorts.items():
        notes = []
        if c["status"] == "exposed":
            for other, oc in cohorts.items():
                if other == iv or oc["status"] != "exposed":
                    continue
                exposed_to_other = set(oc["after"])
                for side in ("before", "after"):
                    sids = c[side]
                    k = sum(1 for s in sids if s in exposed_to_other)
                    if 0 < k < len(sids):
                        notes.append({"intervention": other, "side": side,
                                      "already_exposed": k, "of": len(sids)})
        out[iv] = notes
    return out


# Two activations this close together were the SAME deploy: their cohorts are
# identical BY CONSTRUCTION, today and forever, whatever the sample size.
SAME_DEPLOY_SECONDS = 60


def same_deploy_bundles(activations):
    """Interventions that went live in one deploy. This is a fact about the
    ROLLOUT, not about the sample: it holds even when both after-cohorts are
    empty, so it must be stated whether or not `confounded` ever fires."""
    live = [(iv, e["ts"]) for iv, e in activations["interventions"].items()
            if e["status"] == "exposed" and e["ts"] is not None]
    live.sort(key=lambda p: p[1])
    bundles, cur = [], []
    for iv, ts in live:
        if cur and abs((ts - cur[-1][1]).total_seconds()) <= SAME_DEPLOY_SECONDS:
            cur.append((iv, ts))
        else:
            if len(cur) > 1:
                bundles.append([i for i, _ in cur])
            cur = [(iv, ts)]
    if len(cur) > 1:
        bundles.append([i for i, _ in cur])
    return bundles


def cohort_stats(rows, sids):
    sel = [rows[s] for s in sids if s in rows]
    obs = len(sel)
    comp_records = sum(r.get("compaction_records") or 0 for r in sel)
    return {
        "n_sessions_observed": obs,
        "cost_usd_mean": _mean([r.get("cost_usd") for r in sel]),
        "base_context_tokens_p50": _p50([r.get("base_context_tokens") for r in sel]),
        "context_peak_tokens_p50": _p50([r.get("context_peak_tokens") for r in sel]),
        "reread_cost_pct_mean": _mean([r.get("reread_cost_pct") for r in sel], 1),
        "compaction_records": comp_records,
        "records_per_observed_session": round(comp_records / obs, 3) if obs else None,
        "transcript_compactions": sum(r.get("transcript_compactions") or 0 for r in sel),
        "dispatches": sum(r.get("dispatches") or 0 for r in sel),
        "pinned_dispatches": sum(r.get("pinned_dispatches") or 0 for r in sel),
        "silence": _silence_counts(sel),
    }


def _silence_counts(sel):
    counts = {"has_records": 0, "no_compaction": 0, "hook_inactive": 0, "unknown": 0}
    for row in sel:
        counts[classify_silence(row)] += 1
    return counts


def verdict_for(iv, cohorts, stats_before, stats_after, peers):
    """ONE vocabulary: helped / no_improvement / underpowered / confounded /
    no_baseline / no_exposure / activation_unknown. `PREDICTION-NOT-MET` is NOT in it — it is a
    separate boolean beside the verdict, because "helped less than forecast" and
    "did not help" are different statements."""
    status = cohorts[iv]["status"]
    if status in ("no_exposure", "activation_unknown"):
        return status, f"no valid activation row ({status})"
    n_after = stats_after["n_sessions_observed"]
    n_before = stats_before["n_sessions_observed"]
    spanning = len(cohorts[iv]["spanning"])
    if n_after == 0:
        # The clock DID start. 'nobody has run a clean session since' is a sample
        # problem, not an absence of exposure — never no_exposure here.
        return "underpowered", (
            f"activated, but 0 sessions have started since; {spanning} session(s) "
            "span the activation and belong to no cohort")
    if peers:
        # `confounded` is REQUIRED here and takes precedence over `underpowered`:
        # a thin sample can be fixed by waiting, an indistinguishable cohort
        # cannot. The floor is still reported, so the reader loses neither fact.
        thin = ("; the after cohort is also below the floor of "
                f"{MIN_N} (N={n_after})" if n_after < MIN_N else "")
        return "confounded", (
            f"fewer than {SEPARATION_MIN_SESSIONS} sessions separate this cohort from "
            f"{', '.join(peers)} — the exposures are indistinguishable, so no amount "
            f"of further data can tell them apart{thin}")
    if n_before < MIN_N:  # the before cohort CLOSED at activation: waiting cannot fill it,
        # so `underpowered` ("let it soak") was a false promise (August 2026, before N=0).
        return "no_baseline", (f"only {n_before} observed session(s) started before the activation "
                               f"(floor {MIN_N}); the before cohort closed when it went live, so no "
                               "amount of waiting can fill it")
    if iv in MECHANISM_INTERVENTIONS and (stats_after["compaction_records"] or 0) < MECHANISM_MIN_RECORDS:
        return "underpowered", (
            f"only {stats_after['compaction_records']} compaction record(s) in the after "
            f"cohort (floor {MECHANISM_MIN_RECORDS}); this intervention acts THROUGH "
            "compaction, so a cost delta over sessions that never compacted measures "
            "what work ran, not the hooks")
    if n_after < MIN_N:
        return "underpowered", (
            f"below the floor of {MIN_N} on at least one side "
            f"(before N={n_before}, after N={n_after}); the sample cannot answer, "
            "which is NOT the same as the change failing")
    before_cost, after_cost = stats_before["cost_usd_mean"], stats_after["cost_usd_mean"]
    if before_cost is None or after_cost is None or not before_cost:
        return "underpowered", "no comparable cost on one side"
    delta = _pct(before_cost - after_cost, before_cost)
    if delta is not None and delta > 0:
        return "helped", f"mean cost/session {before_cost} -> {after_cost} ({delta}% lower)"
    return "no_improvement", f"mean cost/session {before_cost} -> {after_cost} ({delta}% change)"


# --------------------------------------------------------------------------
# Counter-metric — so the answer can be 'cheaper and worse'
# --------------------------------------------------------------------------
def adherence(activation_ts, dispatch_rows, closeouts, repo_scoped=False):
    """The behaviours the moved "Why:" paragraphs govern. Every other measure in
    this session points one way (tokens down is good); this one can point back."""
    def split(items):
        before = [i for i in items if i["ts"] and i["ts"] < activation_ts]
        after = [i for i in items if i["ts"] and i["ts"] >= activation_ts]
        return before, after

    if repo_scoped:
        dispatch_rows = [d for d in dispatch_rows
                         if any(("/" + r) in (d.get("cwd") or "") for r in REPO_DIET_REPOS)]
    d_before, d_after = split(dispatch_rows)
    c_before, c_after = split(closeouts)

    def side(dsp, clo):
        return {
            "dispatches_observed": len(dsp),
            "unpinned_generic_fanout_pct": _pct(
                sum(1 for d in dsp if is_unpinned_generic(
                    d.get("requested_model"), d.get("subagent_type"))), len(dsp)),
            "closeouts_observed": len(clo),
            "closeouts_without_numbers_pct": _pct(
                sum(1 for c in clo if not c["has_numbers"]), len(clo)),
        }
    return {"before": side(d_before, c_before), "after": side(d_after, c_after)}


def load_dispatch_ledger(path=DISPATCH_LEDGER):
    from compaction_ledger import load_ndjson                   # noqa: PLC0415
    rows, rejected, exists = load_ndjson(path)
    out = []
    for row in rows:
        ts = parse_ts(row.get("ts"))
        if ts is None:
            rejected += 1
            continue
        row = dict(row)
        row["ts"] = ts
        out.append(row)
    return out, rejected, exists


def load_closeouts(plans_glob):
    """A closeout carrying no re-measured number is the third adherence signal.
    A crude proxy by design: "notes text contains no digit" — labelled as such
    rather than dressed up as a measurement of rigour."""
    out = []
    # glob.glob does NOT expand "~", and SKILL.md documents the pattern
    # single-quoted so the shell does not expand it either. Unexpanded, the
    # pattern matched 0 files and the whole closeout half of the counter-metric
    # silently rendered "n/a". Expand here, where the path is consumed.
    for path in sorted(glob.glob(os.path.expanduser(plans_glob))):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
        except (ValueError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        ts = parse_ts(obj.get("_persisted_at"))
        if ts is None:
            continue
        notes = json.dumps(obj.get("notes") or {})
        out.append({"ts": ts, "path": path,
                    "has_numbers": any(ch.isdigit() for ch in notes)})
    return out
