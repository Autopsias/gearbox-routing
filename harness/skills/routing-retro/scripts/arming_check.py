#!/usr/bin/env python3
"""arming_check.py — is each acceptance criterion ARMED yet? (s07/PF-01, MOVE 5)

s09 must not re-judge a criterion whose evidence cannot exist yet. This script
answers that PER CRITERION, against that criterion's OWN evidence and its OWN
intervention's after-sessions. It lives here, in code beside its test suite,
rather than as a snippet inside s09's prompt — a program inside a markdown
prompt sits outside every test discipline this plan has.

There is NO single aggregate floor: 15 hooks-only sessions can satisfy an
aggregate count while base_context and routing have no exposed sessions at all.

Contract:
  - Every ledger is OPTIONAL. A missing file is a reported result, never an
    exception.
  - Every line is parsed defensively; a non-JSON or non-object line is REJECTED
    and COUNTED, never dropped silently and never fatal.
  - Timestamps are real UTC datetimes, never string comparisons.
  - "an activation object exists" is tracked SEPARATELY from "a valid activation
    was derived": BLOCKED is only for a genuinely EMPTY ledger; a malformed-only
    ledger is activation_unknown + PARTIAL.
  - Criterion 8 (ledger integrity) is always armed and NEVER contributes to the
    ANY-ARMED / PARTIAL / BLOCKED decision.

Usage:
  python3 arming_check.py [--scan /tmp/retro-scan.json] [--root ~/.dyno/compaction]
                          [--outcomes evals/routing/outcomes.ndjson] [--json]
"""

import argparse
import json
import os
import sys

from compaction_ledger import (
    INTERVENTIONS,
    REASON_HOOK_ERROR,
    cohort_label,
    compaction_rows,
    current_policy_version,
    load_activations,
    load_decisions,
    load_heartbeats,
    load_ndjson,
    parse_ts,
    paths,
)

# The compaction-ledger floor, from s09's prompt: 40 live records at the CURRENT
# policy_version over at least 15 distinct sessions, restricted to the
# intervention's after-sessions.
LEDGER_FLOOR_RECORDS = 40
LEDGER_FLOOR_SESSIONS = 15

# The default floor, used ONLY where a criterion has no floor of its own. It is
# never a hidden extra gate on top of another floor.
DEFAULT_AFTER_FLOOR = 5

# The nine criteria, identified by the EVIDENCE each is bound to. The prose text
# of criteria 1-7 belongs to the plan's acceptance review (s08/s09); what this
# script owns is which ledger answers each one and what floor it must clear.
CRITERIA = {
    1: {"label": "the veto is honoured by Claude Code", "evidence": "compaction_ledger",
        "interventions": ("hooks",)},
    2: {"label": "compaction happens at safe points, by session type",
        "evidence": "compaction_ledger", "interventions": ("hooks",)},
    3: {"label": "base context per call actually fell", "evidence": "observed_sessions",
        "interventions": ("base_context", "repo_diet")},
    4: {"label": "orchestrator context per dispatch actually fell",
        "evidence": "orchestrator_ctx", "interventions": ("hooks",)},
    5: {"label": "task-class routing is exercised", "evidence": "outcome_cohorts",
        "interventions": ("routing",)},
    6: {"label": "fan-out dispatches carry a pinned model", "evidence": "outcome_cohorts",
        "interventions": ("routing",)},
    7: {"label": "the retro produces a per-intervention verdict",
        "evidence": "compaction_ledger", "interventions": INTERVENTIONS},
    8: {"label": "activation-ledger integrity", "evidence": "integrity",
        "interventions": ()},
    9: {"label": "predicted versus realized", "evidence": "compaction_ledger",
        "interventions": INTERVENTIONS},
}


def _after_sessions(rows, activations, intervention):
    """Session ids whose START is at or after THIS intervention's activation.
    A session that straddles the activation is `spanning` and is in no cohort."""
    act = activations["interventions"][intervention]
    if act["status"] != "exposed":
        return set(), act["status"]
    after = set()
    for sid, row in rows.items():
        if cohort_label(row.get("started"), row.get("ended"), act["ts"]) == "after":
            after.add(sid)
    return after, "exposed"


def _sessions_from(scan, heartbeats):
    """The INSTRUMENT-OBSERVED universe: one row per heartbeat, with its start and
    end enriched from the scan when the scanner also saw it.

    A scanned transcript with no heartbeat is `unknown` — the instrument never saw
    it — and is excluded here for the same reason compaction_report.py excludes it
    from every verdict: counting it would arm a criterion on sessions that cannot
    speak to it.
    """
    scanned = {}
    for s in (scan or {}).get("sessions", []):
        sid = s.get("session_id")
        if sid:
            scanned[sid] = {"started": parse_ts(s.get("started")), "ended": parse_ts(s.get("ended"))}
    rows = {}
    for sid, hb in heartbeats.items():
        row = scanned.get(sid) or {"started": hb["_ts"], "ended": None}
        if row.get("started") is None:
            row["started"] = hb["_ts"]
        rows[sid] = row
    return rows


def _ledger_criterion(decisions, dec_rejected, after_by_iv, ivs):
    """Criteria 1/2/7/9. Refused OUTRIGHT if the decisions ledger has ANY rejected
    line: a partly corrupt ledger must never arm a criterion.

    Only COMPACTION rows count toward the floor (compaction_rows, the one
    accessor). A prompt-classification line is not evidence about vetoes or safe
    points: 48 prompt rows once armed "the veto is honoured" with zero blocks
    in the ledger — the exact evidence-that-cannot-exist this gate refuses."""
    if dec_rejected:
        return False, f"decisions ledger has {dec_rejected} rejected line(s) — refused", {}
    unexposed = [iv for iv in ivs if after_by_iv[iv][1] != "exposed"]
    if unexposed:
        return False, "not exposed: " + ", ".join(
            f"{iv}={after_by_iv[iv][1]}" for iv in unexposed), {}
    # For a criterion bound to several interventions, only a session exposed to
    # ALL of them can speak to it — the intersection, never the union.
    sids = set.intersection(*[after_by_iv[iv][0] for iv in ivs]) if ivs else set()
    # A fail-open crash row (reason 'hook-error') is spine-valid and sits inside
    # COMPACTION_EVENTS, but it is evidence the instrument was DOWN, not that
    # the veto was honoured — a ledger of pure crashes must never clear a floor.
    recs = [d for d in compaction_rows(decisions)
            if d["session"] in sids and d.get("reason") != REASON_HOOK_ERROR]
    n_rec, n_sess = len(recs), len({d["session"] for d in recs})
    armed = n_rec >= LEDGER_FLOOR_RECORDS and n_sess >= LEDGER_FLOOR_SESSIONS
    return armed, (f"{n_rec}/{LEDGER_FLOOR_RECORDS} live compaction records at the current "
                   f"policy_version over {n_sess}/{LEDGER_FLOOR_SESSIONS} sessions"), \
        {"records": n_rec, "sessions": n_sess}


def _count_criterion(count, floor, what):
    return count >= floor, f"{count}/{floor} {what}", {"count": count}


def _evaluate(live, dec_rejected, hb_rejected, after_by_iv, activations, outcomes):
    """One result per criterion, each against its OWN evidence and its OWN
    intervention's after-sessions."""
    results = {}
    for num, spec in sorted(CRITERIA.items()):
        ivs = spec["interventions"]
        if spec["evidence"] == "integrity":
            armed, detail, facts = True, (
                f"activations: {activations['malformed_rows']} malformed row(s), "
                f"{activations['rejected_lines']} rejected line(s); decisions: "
                f"{dec_rejected} rejected; sessions: {hb_rejected} rejected"), {}
        elif spec["evidence"] == "compaction_ledger":
            armed, detail, facts = _ledger_criterion(live, dec_rejected, after_by_iv, ivs)
        elif spec["evidence"] == "observed_sessions":
            exposed = [iv for iv in ivs if after_by_iv[iv][1] == "exposed"]
            # UNION, deliberately — unlike _ledger_criterion's intersection.
            # That rule needs a session exposed to ALL its interventions because
            # its evidence (one shared compaction ledger) cannot say which
            # intervention a record speaks for. Criterion 3's evidence is
            # per-session base context, which EITHER intervention lowers on its
            # own, so a session exposed to either one can speak to it — and a
            # repo_diet-only rollout must be able to arm it.
            sids = set().union(*[after_by_iv[iv][0] for iv in exposed]) if exposed else set()
            armed, detail, facts = _count_criterion(
                len(sids), DEFAULT_AFTER_FLOOR, "sessions after its own activation")
            if not exposed:
                armed, detail = False, "not exposed: " + ", ".join(
                    f"{iv}={after_by_iv[iv][1]}" for iv in ivs)
            elif "repo_diet" in exposed:
                # The compaction ledgers carry no working directory, so a
                # repo_diet after-session cannot be shown to have opened any of
                # the three covered repositories. Say so beside the count rather
                # than letting a session arm a criterion it may not speak to.
                detail += ("; CAVEAT: repo_diet sessions cannot be attributed to a "
                           "repository — no cwd is recorded in the compaction ledgers")
        elif spec["evidence"] == "orchestrator_ctx":
            act = activations["interventions"][ivs[0]]
            n = 0
            if act["status"] == "exposed":
                n = sum(1 for o in outcomes
                        if isinstance(o.get("orchestrator_ctx_tokens"), int)
                        and not isinstance(o.get("orchestrator_ctx_tokens"), bool)
                        and _is_after(o, act["ts"]))
            armed, detail, facts = _count_criterion(
                n, DEFAULT_AFTER_FLOOR, "dispatch records carrying orchestrator_ctx_tokens "
                f"after the {ivs[0]} activation (status={act['status']})")
        elif spec["evidence"] == "outcome_cohorts":
            act = activations["interventions"][ivs[0]]
            n = 0
            if act["status"] == "exposed":
                n = sum(1 for o in outcomes if _is_after(o, act["ts"]))
            armed, detail, facts = _count_criterion(
                n, DEFAULT_AFTER_FLOOR,
                f"outcome records after the {ivs[0]} activation (status={act['status']})")
        results[num] = {"criterion": num, "label": spec["label"], "armed": armed,
                        "detail": detail, "interventions": list(ivs), **facts}

    return results


def _is_after(record, activation_ts):
    """ONE definition of "this record post-dates the activation", used by every
    criterion. An UNDATED record is NOT after: `(parse_ts(ts) or activation_ts)
    >= activation_ts` is unconditionally true when ts is missing, so criterion 4
    armed on 6 undated records while its sibling refused the identical rows. This
    is the gate whose whole job is to refuse evidence that cannot exist yet, so
    the two branches must not be able to disagree again."""
    ts = parse_ts(record.get("ts"))
    return ts is not None and ts >= activation_ts


def check(scan=None, root=None, outcomes_path=None):
    p = paths(root)
    decisions, dec_rejected, dec_exists = load_decisions(p["decisions"])
    heartbeats, hb_rejected, hb_exists = load_heartbeats(p["sessions"])
    activations = load_activations(p["activations"])
    version = current_policy_version(p["version"])

    live = [d for d in decisions
            if d.get("source") == "live" and (version is None or d.get("policy_version") == version)]

    rows = _sessions_from(scan, heartbeats)
    after_by_iv = {iv: _after_sessions(rows, activations, iv) for iv in INTERVENTIONS}

    outcomes, out_rejected, out_exists = load_ndjson(outcomes_path) if outcomes_path else ([], 0, False)

    results = _evaluate(live, dec_rejected, hb_rejected, after_by_iv, activations, outcomes)

    # Criterion 8 is always armed and must never mask "nothing was ever live".
    decisive = [r for n, r in results.items() if n != 8]
    any_armed = any(r["armed"] for r in decisive)
    if not activations["any_object"]:
        decision = "BLOCKED"
    elif any_armed:
        decision = "ANY ARMED"
    else:
        decision = "PARTIAL"

    return {
        "decision": decision,
        "policy_version": version,
        "ledgers": {
            "decisions": {"exists": dec_exists, "rejected_lines": dec_rejected,
                          "records": len(decisions), "live_at_current_version": len(live)},
            "sessions": {"exists": hb_exists, "rejected_lines": hb_rejected,
                         "sessions_with_heartbeats": len(heartbeats)},
            "activations": {"exists": activations["exists"],
                            "rejected_lines": activations["rejected_lines"],
                            "malformed_rows": activations["malformed_rows"],
                            "any_object": activations["any_object"],
                            "any_valid": activations["any_valid"]},
            "outcomes": {"exists": out_exists, "rejected_lines": out_rejected,
                         "records": len(outcomes)},
        },
        "interventions": {
            iv: {"status": after_by_iv[iv][1], "after_sessions": len(after_by_iv[iv][0]),
                 "spanning": sum(1 for sid, row in rows.items()
                                 if activations["interventions"][iv]["status"] == "exposed"
                                 and cohort_label(row.get("started"), row.get("ended"),
                                                  activations["interventions"][iv]["ts"]) == "spanning"),
                 "before_sessions": sum(1 for sid, row in rows.items()
                                        if activations["interventions"][iv]["status"] == "exposed"
                                        and cohort_label(row.get("started"), row.get("ended"),
                                                         activations["interventions"][iv]["ts"]) == "before")}
            for iv in INTERVENTIONS
        },
        "observed_sessions": len(rows),
        "criteria": [results[n] for n in sorted(results)],
    }


def format_report(rep):
    out = [f"ARMING CHECK — {rep['decision']}", ""]
    out.append(f"observed sessions: {rep['observed_sessions']}   policy_version: {rep['policy_version']}")
    for name, led in rep["ledgers"].items():
        out.append(f"  {name}: exists={led['exists']} rejected_lines={led['rejected_lines']} "
                   + " ".join(f"{k}={v}" for k, v in led.items()
                              if k not in ("exists", "rejected_lines")))
    out.append("")
    for iv, e in rep["interventions"].items():
        out.append(f"  {iv:<13} {e['status']:<19} before={e['before_sessions']:<4} "
                   f"after={e['after_sessions']:<4} spanning={e['spanning']}")
    out.append("")
    for c in rep["criteria"]:
        mark = "ARMED    " if c["armed"] else "not armed"
        note = "  (integrity — never decides ANY ARMED/PARTIAL/BLOCKED)" if c["criterion"] == 8 else ""
        out.append(f"  C{c['criterion']} {mark} {c['label']}{note}")
        out.append(f"       {c['detail']}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", default=None, help="retro_scan.py JSON (for session start/end times)")
    ap.add_argument("--root", default=None, help="dyno compaction root (default ~/.dyno/compaction)")
    ap.add_argument("--outcomes", default=None, help="evals/routing/outcomes.ndjson")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # A --scan the caller asked for and this reader could not use must NEVER be
    # discarded in silence: without it the gate runs on heartbeat-only dates and
    # prints a COMPLETE report over a different population (measured: before=0 on
    # all four interventions, spanning inflated to 13-43, exit 0). Guard the
    # PARSED VALUE too — a JSON scalar parses fine and then reads as an empty
    # scan in _sessions_from's `(scan or {}).get(...)`.
    scan = None
    if args.scan is not None:
        why = None
        if not os.path.exists(args.scan):
            why = "no such file"
        else:
            try:
                with open(args.scan, encoding="utf-8") as f:
                    scan = json.load(f)
            except (ValueError, OSError) as exc:
                why, scan = f"unreadable ({exc})", None
            else:
                if not isinstance(scan, dict):
                    why, scan = f"not a scan object (got {type(scan).__name__})", None
        if why is not None:
            print(f"arming_check: --scan {args.scan}: {why}. Refusing to report "
                  f"over a different population.", file=sys.stderr)
            return 2
    outcomes = args.outcomes
    if outcomes is None:
        guess = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "evals", "routing", "outcomes.ndjson")
        outcomes = guess if os.path.exists(guess) else None
    rep = check(scan=scan, root=args.root, outcomes_path=outcomes)
    print(json.dumps(rep, indent=1, default=str) if args.json else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
