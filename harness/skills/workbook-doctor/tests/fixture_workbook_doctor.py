#!/usr/bin/env python3
"""
S04 acceptance-gate fixture — Workbook Doctor (WD-01 + WD-02).

Builds one workbook per defect type, runs the map + formula-risk scanners, and
asserts against fixtures/workbook_doctor/answer_key.json that:

  * every seeded defect is detected with the right check + severity
    (roll-forward, hardcoded-output, stale-reference, mixed-unit), AND the
    source-packet linkage flags the unsupported +12% YoY number; AND
  * THE TRAP (review-1 finding 5): the consistently-wrong row is shape-consistent
    (a sibling/consistency check passes it) yet the roll-forward model flags it,
    while the correct contrast row is NOT flagged; AND
  * recalculation-capability validation works: recalc either runs and catches a
    seeded #DIV/0!, or degrades gracefully to "recalc unverified".

Exit 0 = PASS (all assertions hold). Exit 1 = FAIL. Wired into run_fixtures.sh.
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
import refs as R  # noqa: E402

PACKET = os.path.join(HERE, "trust_runs", "2026-05-29T1615_q3-boardpack", "source-packet.json")

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


def findings_index(doc):
    """(check, CELL) -> finding, expanding comma/locator lists to individual cells."""
    idx = {}
    import re
    for f in doc["findings"]:
        cells = re.findall(r"[A-Z]{1,3}\d+", f["locator"] or "")
        for cell in cells or [f["locator"]]:
            idx[(f["check"], cell)] = f
    return idx


def run():
    work = tempfile.mkdtemp(prefix="wd_gate_")
    paths = build_fixtures.build_all(work)

    # --- run scanners ---
    docs = {}
    for key, p in paths.items():
        out = os.path.join(work, "out_" + key)
        pk = PACKET if key == "q3_board_model" and os.path.isfile(PACKET) else None
        wm = workbook_map.run(p, out, source_packet=pk, run_id=key)
        # formula risk: skip recalc here for speed/determinism; recalc tested separately
        fr = formula_risk.run(p, out, source_packet=pk, run_id=key, do_recalc=False)
        docs[key] = {"workbook_map": wm, "formula_risk": fr,
                     "idx_map": findings_index(wm), "idx_fr": findings_index(fr)}

    # --- assert answer key ---
    with open(os.path.join(WD_FIX, "answer_key.json")) as fh:
        key = json.load(fh)
    for fxname, spec in key["fixtures"].items():
        idx = docs[fxname]["idx_" + ("fr" if spec["scanner"] == "formula_risk" else "map")]
        for det in spec["must_detect"]:
            cells = det.get("cells") or ([] if "column" in det else [None])
            if det.get("column"):
                # column-level finding (mixed_units): match by check+sheet, locator names the col
                doc = docs[fxname]["workbook_map" if spec["scanner"] == "workbook_map" else "formula_risk"]
                hit = next((f for f in doc["findings"]
                            if f["check"] == det["check"] and det["column"] in (f["locator"] or "")), None)
                check(hit is not None,
                      "%s: missing %s on col %s" % (fxname, det["check"], det["column"]))
                if hit:
                    check(hit["severity"] == det["severity"],
                          "%s: %s severity %s != expected %s" % (fxname, det["check"], hit["severity"], det["severity"]))
                continue
            for cell in cells:
                f = idx.get((det["check"], cell))
                if check(f is not None, "%s: missing %s at %s" % (fxname, det["check"], cell)):
                    check(f["severity"] == det["severity"],
                          "%s: %s@%s severity %s != %s" % (fxname, det["check"], cell, f["severity"], det["severity"]))

    # --- THE TRAP: roll-forward vs shape consistency ---
    from openpyxl import load_workbook
    wb = load_workbook(paths["roll_forward"], data_only=False)
    ws = wb["Model"]
    sig7 = {c: R.shape_signature(ws["%s7" % c].value, 7, R.col_to_idx(c)) for c in ("C", "D", "E")}
    check(len(set(sig7.values())) == 1,
          "TRAP: row 7 should be shape-CONSISTENT (a consistency check passes the bug); sigs=%s" % sig7)
    rf_idx = docs["roll_forward"]["idx_fr"]
    check(("roll_forward", "D7") in rf_idx and ("roll_forward", "E7") in rf_idx,
          "TRAP: roll-forward model must flag D7 and E7 despite shape consistency")
    for good in ("C8", "D8", "E8"):
        check(("roll_forward", good) not in rf_idx,
              "TRAP: correct contrast cell %s must NOT be roll-forward-flagged (false positive)" % good)

    # --- recalc capability (both branches acceptable) ---
    rc_out = os.path.join(work, "out_recalc")
    div0 = os.path.join(work, "div0.xlsx")
    from openpyxl import Workbook
    w = Workbook(); s = w.active; s["A1"] = 10; s["A2"] = 0; s["A3"] = "=A1/A2"; w.save(div0)
    frd = formula_risk.run(div0, rc_out, run_id="recalc_probe", do_recalc=True)
    rc = frd["recalc"]
    if rc.get("ran"):
        hit = any(f["check"] == "recalc_error" for f in frd["findings"])
        check(hit, "recalc ran but did not surface the seeded #DIV/0! as a recalc_error finding")
        check(frd["status"] in ("ok", "ok-with-warnings"), "recalc-ran status should be ok/ok-with-warnings")
    else:
        check(frd["status"] == "ok-with-warnings" and "recalc" in (rc.get("note", "").lower()),
              "recalc unavailable must degrade to ok-with-warnings + 'recalc unverified'")

    # --- JSON validity of every emitted artifact ---
    for key in paths:
        out = os.path.join(work, "out_" + key)
        for fn in ("workbook-map.json", "formula-risk.json"):
            p = os.path.join(out, fn)
            if os.path.isfile(p):
                try:
                    json.load(open(p))
                except Exception as e:
                    check(False, "emitted %s/%s is not valid JSON: %s" % (key, fn, e))

    return work


if __name__ == "__main__":
    workdir = run()
    if _errs:
        print("FAIL — Workbook Doctor acceptance gate:")
        for e in _errs:
            print("  -", e)
        sys.exit(1)
    print("PASS — Workbook Doctor: roll-forward (incl. consistently-wrong trap), hardcoded-output, "
          "stale-reference, mixed-unit, source-packet unsupported-number, and recalc-capability all detected.")
    sys.exit(0)
