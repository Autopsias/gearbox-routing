#!/usr/bin/env python3
"""compaction_report.py — assembles the compaction did-it-help block.

One function, `build()`, turns retro_scan.py's JSON plus the ~/.dyno/compaction/
ledgers into the dict render_report.py renders. Everything it reports is either
measured here or explicitly labelled as not measurable.

Usage:
  retro_scan.py --last 200 --since 2026-08-01 > /tmp/scan.json
  compaction_report.py --scan /tmp/scan.json > /tmp/compaction.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import compaction_retro as cr
from compaction_blindness import _blindness
from compaction_type_table import type_table as _type_table
from compaction_ledger import (
    INTERVENTIONS,
    autocompact_window as _autocompact_window,
    current_policy_version,
    load_activations,
    load_decisions,
    load_heartbeats,
    load_policy_types,
    paths,
)




def _prediction(window, hooks_verdict, before, after):
    """cp-05's projection vs what actually happened. A plan that never compares
    its own forecast to its result cannot be wrong, which is the same as not
    being a measurement. `prediction_met` is a FLAG BESIDE the verdict, never a
    member of the verdict vocabulary — and it is a verdict ON THE FORECAST, so
    it is True/False ONLY when a projected and a realized number were both
    measurable. When either side is missing there was no comparison: the flag is
    None, and the line says n/a. A False here once made the shipped page read
    'realized NOT MEASURABLE ... prediction_met=False' — asserting a missed
    forecast where the honest statement is 'not measurable'."""
    predicted = cr.PREDICTION.get(window)
    realized = None
    # A realized number is only printed when the hooks verdict is a comparison.
    # Printing a percentage beside `underpowered` would hand the operator a
    # direction the sample cannot support — the one outcome this session prevents.
    if hooks_verdict in ("helped", "no_improvement") \
            and before.get("cost_usd_mean") and after.get("cost_usd_mean") is not None:
        realized = cr._pct(before["cost_usd_mean"] - after["cost_usd_mean"],
                           before["cost_usd_mean"])
    met = (realized >= predicted) if (predicted is not None and realized is not None) \
        else None
    # PREDICTION holds projections for two windows only. Any other window — or a
    # missing settings file — has NO projected side; rendering the literal
    # `predicted None%` printed absence as though it were a value, bypassing the
    # n/a handling this report applies everywhere else.
    if predicted is not None:
        pred_part = f"predicted {predicted}% (window {window})"
    elif window is None:
        pred_part = "predicted n/a (autocompact window not measured)"
    else:
        pred_part = (f"predicted n/a (no projection exists for window {window}; "
                     f"projections cover {', '.join(str(w) for w in sorted(cr.PREDICTION))})")
    if realized is None:
        line = (f"{pred_part}, realized NOT MEASURABLE "
                f"(hooks verdict is {hooks_verdict}; N before="
                f"{before.get('n_sessions_observed')}, after="
                f"{after.get('n_sessions_observed')})")
    else:
        line = (f"{pred_part}, realized {realized}% "
                f"(N before={before.get('n_sessions_observed')}, "
                f"after={after.get('n_sessions_observed')})")
    return {"window": window, "predicted_pct": predicted, "realized_pct": realized,
            "prediction_met": met, "line": line,
            "basis_note": ("the ~29%/~22% figures are a PROJECTION over 189 historical "
                           "sessions made at plan time; no artifact in this repo "
                           "reproduces that model, so the predicted side is quoted, "
                           "not re-measured")}


def _decision_card(interventions, blindness, bundles):
    """At most three options, each one the operator can act on."""
    cards = []
    in_bundle = {iv for b in bundles for iv in b}
    # An intervention already named in a same-deploy bundle is not ALSO listed as
    # a separate indistinguishable cohort: that said the same thing twice, in the
    # vocabulary of the old subset test.
    nested = [iv for iv, e in interventions.items()
              if e["verdict"] == "confounded" and iv not in in_bundle]
    if bundles or nested:
        names = sorted(in_bundle | set(nested))
        cards.append({
            "title": f"Stagger the re-activation of {' + '.join(names)}",
            "body": ("; ".join(
                [f"{' + '.join(b)} went live in ONE deploy — identical cohorts, "
                 "not separable by any amount of further data" for b in bundles]
                + [f"too few sessions separate {iv}'s cohort from another "
                   "intervention's for the two to be told apart" for iv in nested])
                + ". The ONLY design that separates them is one intervention per "
                  "deploy, days apart: revert one, re-deploy it alone, record it with "
                  "`compact-policy.py activation --intervention <name> --restage`, "
                  "and let it soak."),
            # One extra deploy PER intervention separated: re-deploying one alone
            # does not isolate its partner, whose own activation row still sits
            # inside the original bundle.
            "cost": f"{len(names)} extra deploy(s), and nothing else.",
            "do_nothing": "the bundle stays unattributable forever — more data cannot fix it.",
        })
    closed = [iv for iv, e in interventions.items() if e["verdict"] == "no_baseline"]
    if closed:
        cards.append({
            "title": f"Retire the before/after verdict ({', '.join(closed)})",
            "body": (f"Fewer than {cr.MIN_N} observed sessions started before these went "
                     "live, and the before cohort closed at activation — waiting grows "
                     "only the after side. Judge them with a forward re-measure instead, "
                     f"and record at least {cr.MIN_N} observed sessions before the next "
                     "activation."),
            "cost": "no work now; the next activation waits for its baseline.",
            "do_nothing": "every retro keeps listing a verdict that cannot arrive.",
        })
    if any(e["verdict"] == "underpowered" for e in interventions.values()):
        pend = [iv for iv, e in interventions.items() if e["verdict"] == "underpowered"]
        cards.append({
            "title": f"Let it soak, then run s09 ({', '.join(pend)})",
            "body": ("The clock started but the sample cannot answer yet. s09 exists "
                     "to convert these; run `arming_check.py` first — it says per "
                     "criterion what is armed and what is still below its floor."),
            "cost": "no work now; one session later.",
            "do_nothing": "PENDING-SOAK silently becomes permanent.",
        })
    if blindness["blind"]:
        # The SAME holes list that raised the flag — a hole that can blind the
        # page can never be missing from the card that asks to close it.
        named = "; ".join(f"{h['count']} {h['what']}"
                          for h in blindness.get("holes") or [] if h["count"])
        cards.append({
            "title": "Close the instrument's blind spots before trusting a direction",
            "body": named + ". A healthy allow rate over a blind hook looks "
                            "exactly like success.",
            "cost": "one fix to the writer; no revert.",
            "do_nothing": "a later verdict rests on records that were never taken.",
        })
    return cards[:3]


def _retro_type(rows, scan):
    """Retro-classify any session with no policy file, tagging it type_source
    'retro' so it can never be confused with a live classification."""
    classifier = cr.load_classifier()
    by_id = {s.get("session_id"): s for s in scan.get("sessions", [])}
    typed = 0
    for sid, row in rows.items():
        if row.get("session_type"):
            continue
        guess = cr.retro_classify((by_id.get(sid) or {}).get("first_prompt"), classifier)
        if guess:
            row["session_type"], row["type_source"] = guess, "retro"
            typed += 1
    return typed


def _intervention_entries(observed, cohorts, peers, dispatches, closeouts, coactive):
    """One entry per intervention, each built from ITS OWN activation row."""
    out = {}
    for iv in INTERVENTIONS:
        before = cr.cohort_stats(observed, cohorts[iv]["before"])
        after = cr.cohort_stats(observed, cohorts[iv]["after"])
        spanning = cr.cohort_stats(observed, cohorts[iv]["spanning"])
        verdict, why = cr.verdict_for(iv, cohorts, before, after, peers[iv])
        entry = {
            "intervention": iv,
            "status": cohorts[iv]["status"],
            "activation_ts": cohorts[iv]["activation_ts"],
            "prior_stages": cohorts[iv]["prior_stages"],
            "verdict": verdict,
            "why": why,
            "bundled_with": peers[iv],
            # Lifting `confounded` off a staggered rollout does not make the
            # contrast clean — this names, and counts, what else moved inside
            # the window.
            "co_active": coactive[iv],
            "n_before": before["n_sessions_observed"],
            "n_after": after["n_sessions_observed"],
            "n_spanning": len(cohorts[iv]["spanning"]),
            # Sessions build_cohorts could not date: in NO cohort, and said so
            # rather than silently absent (0 in practice — every observed row is
            # dated from its heartbeat when the transcript would not parse).
            "n_undated": cohorts[iv]["undated"],
            "before": before,
            "after": after,
            "spanning": spanning,
            # A cohort pair that holds only the cheap sessions cannot answer a
            # cost question, however many sessions it has. Report the share of
            # total spend that sits in the EXCLUDED spanning bucket.
            "spanning_cost_share_pct": cr._pct(
                (spanning["cost_usd_mean"] or 0) * spanning["n_sessions_observed"],
                sum((c["cost_usd_mean"] or 0) * c["n_sessions_observed"]
                    for c in (before, after, spanning))),
        }
        if cohorts[iv]["status"] == "exposed" and iv in ("base_context", "repo_diet"):
            entry["adherence"] = cr.adherence(
                cr.parse_ts(cohorts[iv]["activation_ts"]), dispatches, closeouts,
                repo_scoped=(iv == "repo_diet"))
        if iv == "repo_diet":
            entry["attribution_limit"] = cr.REPO_ATTRIBUTION_LIMIT
        out[iv] = entry
    return out


def build(scan, root=None, settings_path=None, plans_glob=None,
          dispatch_path=cr.DISPATCH_LEDGER):
    p = paths(root)
    decisions, dec_rejected, dec_exists = load_decisions(p["decisions"])
    heartbeats, hb_rejected, hb_exists = load_heartbeats(p["sessions"])
    activations = load_activations(p["activations"])
    policy_types = load_policy_types(p["policy_dir"])
    version = current_policy_version(p["version"])

    rows = cr.index_sessions(scan, heartbeats, policy_types)
    filtered = cr.attach_decisions(rows, decisions, version)

    retro_typed = _retro_type(rows, scan)

    # EVERY VERDICT IS COMPUTED OVER INSTRUMENT-OBSERVED SESSIONS ONLY.
    # A scanned transcript with no heartbeat is `unknown` — excluded from every
    # verdict and counted. Mixing the two populations would compare 1400 short
    # subagent transcripts against 40 hook-observed sessions and call the
    # difference an effect; that is exactly the measured-sounding non-measurement
    # this session exists to prevent.
    observed = {sid: r for sid, r in rows.items() if r.get("heartbeat") is not None}
    unobserved = {sid: r for sid, r in rows.items() if r.get("heartbeat") is None}

    cohorts = cr.build_cohorts(observed, activations)
    peers = cr.bundled_with(cohorts)
    coactive = cr.co_active(cohorts)
    dispatches, disp_rejected, _ = cr.load_dispatch_ledger(dispatch_path)
    closeouts = cr.load_closeouts(plans_glob) if plans_glob else []

    out_iv = _intervention_entries(observed, cohorts, peers, dispatches, closeouts, coactive)

    hooks_ts = cr.parse_ts(cohorts["hooks"]["activation_ts"])
    blindness = _blindness(observed, unobserved, hooks_ts)
    window = _autocompact_window(settings_path) if settings_path else None
    hooks = out_iv["hooks"]
    prediction = _prediction(window, hooks["verdict"], hooks["before"], hooks["after"])

    bundles = cr.same_deploy_bundles(activations)
    cheaper_and_worse = _cheaper_and_worse(out_iv)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from": {"scan_sessions": len(scan.get("sessions", [])), "root": root or os.path.expanduser("~/.dyno/compaction")},
        "policy_version": version,
        "ledgers": {
            "decisions": {"exists": dec_exists, "rejected_lines": dec_rejected,
                          "records": len(decisions)},
            "sessions": {"exists": hb_exists, "rejected_lines": hb_rejected,
                         "sessions_with_heartbeats": len(heartbeats)},
            "activations": {"exists": activations["exists"],
                            "rejected_lines": activations["rejected_lines"],
                            "malformed_rows": activations["malformed_rows"],
                            "any_object": activations["any_object"],
                            "any_valid": activations["any_valid"]},
            "dispatch_audit": {"rejected_lines": disp_rejected, "rows": len(dispatches)},
        },
        "filtered": filtered,
        "observed_sessions": len(observed),
        # Count what each name claims: `rows` is scan UNION heartbeats UNION
        # ledger-only sessions, and printing its size as "scanned" counted
        # sessions no transcript was ever read for.
        "scanned_sessions": sum(1 for r in rows.values() if r.get("scanned")),
        "universe_sessions": len(rows),
        "excluded_unknown_sessions": len(unobserved),
        "retro_typed_sessions": retro_typed,
        "type_table": _type_table(observed),
        "blindness": blindness,
        "interventions": out_iv,
        "prediction": prediction,
        "cheaper_and_worse": cheaper_and_worse,
        "same_deploy_bundles": bundles,
        "verdict_line": _verdict_line(out_iv, prediction, blindness, cheaper_and_worse, bundles),
        "decision_card": _decision_card(out_iv, blindness, bundles),
        "vocabulary": list(cr.VERDICTS),
    }


def _cheaper_and_worse(interventions):
    """If cost fell and adherence fell with it, the verdict line must say so —
    currently a result this plan has no other way to express.

    Both halves are read from the MEASUREMENTS, never from the verdict word.
    Gating this on `helped` silenced the apex line for exactly the interventions
    where it matters most: a cost fall beside an adherence fall is the finding
    whether the sample was thin (`underpowered`) or the cohort inseparable
    (`confounded`). The verdict is carried INTO the signal instead, so the
    reader sees on one line what fell and how much weight the sample carries."""
    hits = []
    # An all-clear is only claimable where the comparison could have FIRED: a
    # cost pair on both sides plus at least one adherence pair on both sides.
    # "No signal" over zero comparable interventions is an unexamined zero, and
    # the renderer must say NOT MEASURED instead of clean.
    comparable = 0
    for iv, e in interventions.items():
        adh = e.get("adherence")
        if not adh:
            continue
        b_cost = (e.get("before") or {}).get("cost_usd_mean")
        a_cost = (e.get("after") or {}).get("cost_usd_mean")
        b, a = adh["before"], adh["after"]
        keys = [k for k in ("unpinned_generic_fanout_pct", "closeouts_without_numbers_pct")
                if b.get(k) is not None and a.get(k) is not None]
        if b_cost and a_cost is not None and keys:
            comparable += 1
        # 'worse' alone is not 'cheaper and worse'.
        if not b_cost or a_cost is None or a_cost >= b_cost:
            continue
        drop = cr._pct(b_cost - a_cost, b_cost)
        for key in keys:
            if a[key] > b[key]:
                hits.append(f"{iv} [{e['verdict']}]: cost/session {b_cost} -> {a_cost} "
                            f"({drop}% lower) while {key} rose {b[key]}% -> {a[key]}%")
    return {"triggered": bool(hits), "signals": hits,
            "comparable_interventions": comparable}


def _verdict_line(interventions, prediction, blindness, caw, bundles=()):
    """`identified` never contains a same-deploy bundle member: naming one as
    identified would be the per-component claim the bundle cannot support."""
    in_bundle = {iv for b in bundles for iv in b}
    # `identified` means an effect was actually measured and attributed. An
    # `underpowered` intervention is separable but its sample cannot answer —
    # naming it identified told the reader an effect was found where none could
    # be. Only the two verdicts that state a measured direction qualify.
    identified = [iv for iv, e in interventions.items()
                  if e["verdict"] in ("helped", "no_improvement")
                  and iv not in in_bundle]
    bundled = [iv for iv, e in interventions.items() if e["verdict"] == "confounded"]
    parts = ["; ".join(f"{iv}: {e['verdict']} (before N={e['n_before']}, after "
                       f"N={e['n_after']}, spanning {e['n_spanning']})"
                       for iv, e in interventions.items())]
    parts.append(prediction["line"])
    # None means NO COMPARISON WAS POSSIBLE (the prediction line just said which
    # side was missing) — printed as n/a, because `prediction_met=False` beside
    # 'realized NOT MEASURABLE' reads as a missed forecast that was never tested.
    met = prediction["prediction_met"]
    parts.append(f"prediction_met={met}" if met is not None else
                 "prediction_met=n/a (no comparison was possible — not a verdict "
                 "on the forecast)")
    same = [" + ".join(b) for b in bundles]
    parts.append("identified: " + (", ".join(identified) or "none")
                 + " | indistinguishable cohort (not attributable alone): "
                 + (", ".join(bundled) or "none")
                 + " | SAME-DEPLOY BUNDLE, never separable by more data: "
                 + (", ".join(same) or "none"))
    # An `identified` verdict is still not an isolated one if something else
    # changed state part-way through the window. Say it out loud on the line.
    contaminated = ["; ".join(
        f"{iv}'s {n['side']} cohort: {n['already_exposed']}/{n['of']} sessions were "
        f"already exposed to {n['intervention']}" for n in e["co_active"])
        for iv, e in interventions.items() if e.get("co_active")]
    if contaminated:
        parts.append("CONTRAST NOT ISOLATING: " + " || ".join(contaminated))
    if blindness["blind"]:
        # The SAME holes list that raised the flag
        # (compaction_blindness.BLINDNESS_HOLES) — only the
        # non-zero ones are named here. Printing every phrase unconditionally
        # made the line assert "N session(s) left NO ledger line" even when N
        # was 0; hand-listing three of five let the flag fire with no reason
        # printed at all. The rendered banner prints the full list.
        parts.append("INSTRUMENT BLIND: "
                     + "; ".join(f"{h['count']} {h['what']}"
                                 for h in blindness.get("holes") or [] if h["count"]))
    if caw["triggered"]:
        parts.append("CHEAPER AND WORSE: " + "; ".join(caw["signals"]))
    return " || ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", required=True, help="retro_scan.py JSON")
    ap.add_argument("--root", default=None, help="dyno compaction root (default ~/.dyno/compaction)")
    ap.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    ap.add_argument("--plans-glob", default=None,
                    help="closeout glob for the adherence row; pass it SINGLE-QUOTED "
                         "so the shell expands neither the globs nor '~' — the script "
                         "expands '~' itself")
    ap.add_argument("--dispatch", default=cr.DISPATCH_LEDGER)
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args(argv)

    with open(args.scan, encoding="utf-8") as f:
        scan = json.load(f)
    block = build(scan, root=args.root, settings_path=args.settings,
                  plans_glob=args.plans_glob, dispatch_path=args.dispatch)
    text = json.dumps(block, indent=1, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
