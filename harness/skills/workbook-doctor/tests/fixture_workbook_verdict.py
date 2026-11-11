#!/usr/bin/env python3
"""
S05 acceptance-gate fixture — Workbook Doctor WD-03 (assessment + verdict).

THE GATE (plan WD-03): a fixture where changing ONE assumption FLIPS the verdict.
We build a deliverable resting on a single load-bearing assumption (Revenue
YoY %), run the full WD-01 -> WD-02 -> WD-03 chain twice against two states of
the source packet, and assert the readiness verdict flips:

  * State A — the assumption is UNSUPPORTED  -> the only must-fix is open
              -> readiness == "not-ready".
  * State B — the SAME assumption is now SOURCED (verified) -> no must-fix
              -> readiness == "ready".

Plus the human-gate (conventions §4) is exercised on State A:
  * a valid WAIVER of the one must-fix -> "ready-with-limitations"
    (never plain "ready"); and
  * the same waiver under a Board travel class WITHOUT signoff -> held at
    "not-ready" (§4.3).

And the verdict artifact's shape is asserted: assumptions table carries
name/value/unit/source/owner/date/status; the verification memo gives an
explicit pass/fail per check; the repair plan is keyed to the severity enum;
both JSON + MD are emitted and the JSON parses.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh.
"""
import copy
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

import build_fixtures   # noqa: E402
import workbook_map     # noqa: E402
import formula_risk     # noqa: E402
import assessment       # noqa: E402

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


# ---- two states of the source packet (the ONLY thing that differs) -----------
def packet(state):
    pk = {
        "sources": [{"id": "S1", "name": "q3_actuals.csv", "owner": "FP&A (close pack)",
                     "date": "end Q3-FY26", "source_status": "current"}],
        "claims": [
            {"id": "C1", "class": "fact", "review_status": "conflicting", "metric": "revenue",
             "period": "Q3-FY26", "value": "€48.2M",
             "provenance": {"claim": "Q3-FY26 revenue = €48.2M",
                            "supporting": [{"source_id": "S1"}],
                            "conflict": {"with": ["S2"], "note": "forecast says €47.5M"}}},
            {"id": "C4", "class": "fact", "review_status": "conflicting", "metric": "headcount",
             "period": "Q3-FY26", "value": "1195",
             "provenance": {"claim": "Q3-FY26 headcount = 1,195",
                            "supporting": [{"source_id": "S1"}],
                            "conflict": {"with": ["S2"], "note": "plan says 1,180"}}},
            {"id": "C6", "class": "open-question", "review_status": "unsupported",
             "metric": "revenue_yoy", "period": "Q3-FY26", "value": "+12% YoY",
             "provenance": {"claim": "Q3 revenue up 12% YoY", "supporting": [], "conflict": None},
             "note": "no prior-year base in any source"},
        ],
        "conflicts": [
            {"id": "CONFLICT-001", "topic": "Q3-FY26 revenue",
             "competing": [{"value": "€48.2M", "source_id": "S1"}, {"value": "€47.5M", "source_id": "S2"}]},
            {"id": "CONFLICT-003", "topic": "Q3-FY26 headcount",
             "competing": [{"value": "1195", "source_id": "S1"}, {"value": "1180", "source_id": "S2"}]},
        ],
    }
    if state == "B":
        # the SINGLE change: the YoY assumption is now sourced (verified)
        for cl in pk["claims"]:
            if cl["id"] == "C6":
                cl["review_status"] = "verified"
                cl["class"] = "fact"
                cl["value"] = "+12% YoY"
                cl["provenance"]["supporting"] = [
                    {"source_id": "S1", "locator": "q3_actuals.csv#prior_year",
                     "confidence": 0.95, "rationale": "prior-year Q3 base added to the close pack"}]
    return pk


def run_chain(workbook, work, state, run_id, resolutions=None, travel_class="Travels"):
    out = os.path.join(work, "out_%s_%s" % (run_id, state))
    pk_path = os.path.join(work, "packet_%s.json" % state)
    with open(pk_path, "w") as fh:
        json.dump(packet(state), fh)
    res_path = None
    if resolutions is not None:
        res_path = os.path.join(work, "res_%s.json" % run_id)
        with open(res_path, "w") as fh:
            json.dump(resolutions, fh)
    workbook_map.run(workbook, out, source_packet=pk_path, run_id=run_id)
    formula_risk.run(workbook, out, source_packet=pk_path, run_id=run_id, do_recalc=False)
    verdict = assessment.run(
        os.path.join(out, "workbook-map.json"),
        os.path.join(out, "formula-risk.json"),
        out, source_packet=pk_path, resolutions_path=res_path,
        run_id=run_id, travel_class=travel_class)
    return out, verdict


def run():
    work = tempfile.mkdtemp(prefix="wd_verdict_")
    wb = build_fixtures.build_verdict_flip(work)

    # ---- THE FLIP: same workbook, one assumption changed ----
    out_a, va = run_chain(wb, work, "A", "verdict_A")
    out_b, vb = run_chain(wb, work, "B", "verdict_B")

    check(va["readiness"] == "not-ready",
          "State A (assumption UNSUPPORTED) must be not-ready; got %r" % va["readiness"])
    check(vb["readiness"] == "ready",
          "State B (assumption SOURCED) must be ready; got %r" % vb["readiness"])
    check(va["readiness"] != vb["readiness"],
          "THE GATE: changing one assumption must FLIP the verdict (A=%r, B=%r)"
          % (va["readiness"], vb["readiness"]))

    # the flip is driven by exactly the one assumption-linked must-fix
    a_blocking = va["verdict_detail"]["must_fix_blocking"]
    check(len(a_blocking) == 1,
          "State A should have exactly ONE blocking must-fix (the unsupported YoY); got %s" % a_blocking)
    check(vb["verdict_detail"]["must_fix_total"] == 0,
          "State B should have ZERO must-fix after sourcing; got %s" % vb["verdict_detail"]["must_fix_total"])

    # ---- human gate (conventions §4): waive the one must-fix on State A ----
    fid = a_blocking[0] if a_blocking else "FR-001"
    valid_waiver = {"resolutions": [{
        "finding_id": fid, "lifecycle": "waived-by-human",
        "waiver": {"owner": "CFO", "decision": "present with footnote pending YoY base",
                   "due_date": "2026-06-15", "signoff": "approved",
                   "rationale": "Board accepts the YoY figure as directional this cycle."}}]}
    _o, vw = run_chain(wb, work, "A", "verdict_waived", resolutions=valid_waiver)
    check(vw["readiness"] == "ready-with-limitations",
          "A valid waiver of the one must-fix must yield ready-with-limitations; got %r" % vw["readiness"])
    check(len(vw.get("waivers", [])) == 1 and vw["must_own"],
          "ready-with-limitations must carry the waived limitation in must_own + waivers")

    # ---- §4.3: Board/Regulatory waiver WITHOUT signoff is held at not-ready ----
    nosign = copy.deepcopy(valid_waiver)
    nosign["resolutions"][0]["waiver"]["signoff"] = "pending"
    _o2, vbr = run_chain(wb, work, "A", "verdict_board", resolutions=nosign, travel_class="Board")
    check(vbr["readiness"] == "not-ready",
          "Board-class waiver without signoff must stay not-ready (§4.3); got %r" % vbr["readiness"])

    # ---- never 'ready' while a must-fix is open (the cardinal rule) ----
    check(not (va["readiness"] == "ready" and va["verdict_detail"]["must_fix_blocking"]),
          "CARDINAL: must never be 'ready' while a must-fix is open")

    # ---- artifact shape (assumptions table / memo / repair plan) ----
    # assumptions carry the required columns
    for a in va["assumptions"]:
        for col in ("name", "value", "unit", "source", "owner", "date", "status"):
            check(col in a, "assumption row missing column %r" % col)
    check(any(a["status"] == "unsupported" for a in va["assumptions"]),
          "State A assumptions table must include the unsupported YoY assumption")
    check(any(a["status"] == "verified" for a in vb["assumptions"]) or
          all(a["status"] != "unsupported" for a in vb["assumptions"]),
          "State B assumptions table should reflect the now-sourced assumption")

    # verification memo: explicit pass/fail per named check; required checks present
    memo_names = {c["check"] for c in va["verification_memo"]}
    for required in ("tie_outs", "formula_consistency", "source_traceability", "stale_data"):
        check(required in memo_names, "verification memo missing required check %r" % required)
    src_trace = next(c for c in va["verification_memo"] if c["check"] == "source_traceability")
    check(src_trace["result"] == "fail",
          "State A source-traceability must FAIL (unsupported number); got %r" % src_trace["result"])
    src_trace_b = next(c for c in vb["verification_memo"] if c["check"] == "source_traceability")
    check(src_trace_b["result"] == "pass",
          "State B source-traceability must PASS once sourced; got %r" % src_trace_b["result"])

    # repair plan keyed to the severity enum; State A has the must-fix, State B empty
    check(all(p["severity"] in ("must-fix", "should-fix", "polish") for p in va["repair_plan"]),
          "repair plan severities must come from the conventions enum")
    check(any(p["severity"] == "must-fix" for p in va["repair_plan"]),
          "State A repair plan must contain the must-fix")
    check(len(vb["repair_plan"]) == 0,
          "State B repair plan must be empty (nothing unresolved); got %d" % len(vb["repair_plan"]))

    # ---- precondition gate: missing inputs -> blocked (not failed) ----
    blk = assessment.run(os.path.join(work, "nope-map.json"),
                         os.path.join(work, "nope-fr.json"),
                         os.path.join(work, "out_blocked"), run_id="blocked_probe")
    check(blk["status"] == "blocked",
          "missing upstream inputs must return status 'blocked' (precondition), got %r" % blk["status"])

    # ---- JSON + MD emitted and parse ----
    for out in (out_a, out_b):
        jp = os.path.join(out, "workbook-verdict.json")
        mp = os.path.join(out, "workbook-verdict.md")
        check(os.path.isfile(jp) and os.path.isfile(mp), "verdict json+md must both be emitted")
        if os.path.isfile(jp):
            try:
                json.load(open(jp))
            except Exception as e:
                check(False, "workbook-verdict.json does not parse: %s" % e)
    return work


if __name__ == "__main__":
    run()
    if _errs:
        print("FAIL — WD-03 verdict acceptance gate:")
        for e in _errs:
            print("  -", e)
        sys.exit(1)
    print("PASS — WD-03: changing one assumption flips not-ready -> ready; waiver -> ready-with-limitations; "
          "Board-no-signoff held not-ready; assumptions table, pass/fail memo, severity-keyed repair plan, "
          "and blocked-precondition all verified.")
    sys.exit(0)
