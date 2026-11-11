#!/usr/bin/env python3
"""
Acceptance fixture — AB-01 (v3 finding #4): the recalc-GATED workbook builder.

The builder (skills/workbook-doctor/scripts/builder.py) scaffolds a four-tab
workbook (Raw -> Assumptions -> Calc -> Checks) with a LIVE checks tab whose
green/red is computed by worksheet formulas — never a hardcoded PASS — and it
is honest about whether a real engine ever powered the alarm.

This fixture is DETERMINISTIC (pure stdlib + openpyxl; no LibreOffice) so it
gates the suite everywhere. It injects a recalc binding to stand in for the
engine. The REAL LibreOffice binding is exercised by the in-session live smoke
(recorded) + the environment-gated fixture_recalc.py — this fixture tests the
GATE + checks logic, not LibreOffice itself.

Two headline scenarios from the AB-01 spec, plus the contract guards:

  (a) build from a seeded packet, BREAK one formula, and assert the checks tab
      flips FAIL on a VERIFIED recalc (and the clean build is ALL PASS / ready);
  (b) FORCE recalc-unavailable and assert the `recalc-unverified` cap is applied,
      the checks-tab readiness CLAIM is BLOCKED, and the static formula
      assertions are present + labelled NOT equivalent to recalc.

Guards: checks-tab categories == the frozen §7.3 enum; green/red is never a
hardcoded literal in the sheet; source IDs + assumption owner/date/status are
carried from the packet; references/ (the FROZEN §5 thresholds) byte-identical.

Exit 0 = PASS, non-zero = FAIL.
"""
import hashlib
import json
import os
import sys
import tempfile

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "skills", "workbook-doctor", "scripts"))
import builder  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _tree_fp(root):
    fp = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                fp[p] = hashlib.sha256(fh.read()).hexdigest()
    return fp


def main():
    tmp = tempfile.mkdtemp(prefix="otl_builder_fx_", dir=os.environ.get("TMPDIR", "/tmp"))
    refs_before = _tree_fp(os.path.join(ROOT, "skills", "trust-review", "references"))

    # ---- contract: the §7.3 controlled enum ------------------------------
    enum, cap_id = builder._load_contract()
    check(set(enum) == {"tie-outs", "formula-consistency", "error-scan",
                        "stale-date", "scenario"},
          "checks-tab enum is the frozen §7.3 set {tie-outs, formula-consistency, "
          "error-scan, stale-date, scenario}: %r" % sorted(enum))
    check(cap_id == "recalc-unverified", "recalc cap id is 'recalc-unverified'")

    # =====================================================================
    # (a) CLEAN build, VERIFIED (injected) recalc -> alarm green, ready
    # =====================================================================
    spec = builder.seed_spec()
    clean = builder.run_builder(spec, os.path.join(tmp, "clean"),
                                recalc_binding=builder.make_injected_recalc(spec))
    ct = clean["checks_tab"]
    check(ct["recalc_verified"] is True, "(a-clean) recalc verified")
    check(ct["claim"] == "valid", "(a-clean) checks-tab claim VALID on a verified recalc")
    check(set(ct["results"].keys()) == set(enum),
          "(a-clean) checks tab reports exactly the five enum categories")
    check(all(v == "PASS" for v in ct["results"].values()) and ct["overall"] == "ALL PASS",
          "(a-clean) every live check PASS; overall ALL PASS")
    check(clean["readiness"] == "ready" and not clean["degraded_status"],
          "(a-clean) readiness=ready, no caps")

    # ---- the four-tab architecture + LIVE (non-hardcoded) checks ---------
    wb = openpyxl.load_workbook(clean["workbook"])  # formulas, not values
    check(wb.sheetnames == ["Raw", "Assumptions", "Calc", "Checks"],
          "(a-clean) tabs scaffolded Raw -> Assumptions -> Calc -> Checks: %r" % wb.sheetnames)
    # every check RESULT cell is a formula starting with '=IF(' — never a literal
    result_cells = ["C4", "C5", "C6", "C7", "C8", "C10"]
    live = []
    for c in result_cells:
        v = wb["Checks"][c].value
        live.append(isinstance(v, str) and v.startswith("="))
    check(all(live), "(a-clean) every checks-tab result is a LIVE formula (no hardcoded PASS): %r"
          % {c: wb["Checks"][c].value for c in result_cells})
    # the alarm cell must NOT literally be the string PASS/FAIL anywhere
    hard = any(str(wb["Checks"][c].value).strip().upper() in ("PASS", "FAIL", "ALL PASS")
               for c in result_cells)
    check(not hard, "(a-clean) no checks-tab result cell is a hardcoded PASS/FAIL literal")
    wb.close()

    # ---- provenance carried from the packet ------------------------------
    prov = clean["provenance"]
    check(prov["raw_source_id"] == "S1", "(a-clean) raw source id S1 carried")
    owners = {a["key"]: (a["owner"], a["date"], a["status"], a["source_id"])
              for a in prov["assumptions"]}
    check(owners.get("growth") == ("FP&A", "2026-05-01", "current", "S2"),
          "(a-clean) assumption 'growth' carries owner/date/status/source from the packet")
    check(owners.get("scenario_factor") == ("CFO office", "2026-05-01", "current", "S3"),
          "(a-clean) assumption 'scenario_factor' carries owner/date/status/source from the packet")

    # =====================================================================
    # (a) BROKEN formula, VERIFIED recalc -> checks tab flips FAIL
    # =====================================================================
    broken = builder.seed_spec(); broken["inject"] = "tie-break"
    rb = builder.run_builder(broken, os.path.join(tmp, "broken"),
                             recalc_binding=builder.make_injected_recalc(broken))
    bt = rb["checks_tab"]
    check(bt["recalc_verified"] is True, "(a-broken) recalc verified")
    check(bt["results"]["tie-outs"] == "FAIL",
          "(a-broken) the broken tie-out formula -> tie-outs FAIL on the verified recalc")
    check(bt["overall"] == "FAIL", "(a-broken) overall alarm FAIL")
    check(rb["readiness"] == "not-ready",
          "(a-broken) a verified-recalc FAIL drives readiness=not-ready (a real defect, not capped)")
    # a second, independent break: a divide-by-zero must trip error-scan
    err = builder.seed_spec(); err["inject"] = "error"
    re = builder.run_builder(err, os.path.join(tmp, "err"),
                             recalc_binding=builder.make_injected_recalc(err))
    check(re["checks_tab"]["results"]["error-scan"] == "FAIL",
          "(a-broken) an injected #DIV/0! -> error-scan FAIL")

    # =====================================================================
    # (b) FORCE recalc-unavailable -> cap applied, claim blocked, fallback labelled
    # =====================================================================
    unv = builder.run_builder(builder.seed_spec(), os.path.join(tmp, "unverified"),
                              recalc_binding=lambda *a, **k: {"ran": False,
                                                              "note": "forced-unavailable (LibreOffice absent)"})
    caps = [d["cap"] for d in unv["degraded_status"]]
    check("recalc-unverified" in caps,
          "(b) recalc unavailable -> the 'recalc-unverified' degraded-status cap is applied")
    check(unv["checks_tab"]["claim"] == "blocked",
          "(b) the checks-tab readiness CLAIM is BLOCKED (the smoke alarm was never powered)")
    check(unv["checks_tab"]["results"] is None,
          "(b) NO green/red is read from the unevaluated sheet (no fabricated alarm)")
    check(unv["readiness"] == "ready-with-limitations",
          "(b) readiness is CAPPED at ready-with-limitations (never plain 'ready')")
    sa = unv["checks_tab"]["static_assertions"]
    check(sa is not None and sa["equivalent_to_recalc"] is False,
          "(b) static formula assertions are present AND labelled NOT equivalent to recalc")
    check(sa["basis"] == "static-formula-assertion" and set(sa["results"].keys()) == set(enum),
          "(b) static assertions cover the five enum categories, basis=static-formula-assertion")
    # the manifest records the cap (degraded_status[]) honestly
    man = json.load(open(os.path.join(tmp, "unverified", "manifest.json")))
    check(any(d["cap"] == "recalc-unverified" for d in man["degraded_status"]),
          "(b) the manifest records the recalc-unverified cap in degraded_status[]")

    # ---- FROZEN §5 / contract untouched ----------------------------------
    refs_after = _tree_fp(os.path.join(ROOT, "skills", "trust-review", "references"))
    check(refs_before == refs_after,
          "references/ is byte-identical — no §5 release threshold (or any contract file) was touched")

    if FAILURES:
        print("RESULT: FAIL (%d check(s) failed)" % len(FAILURES))
        for f in FAILURES:
            print("   -", f)
        return 1
    print("RESULT: PASS — recalc-gated builder: a broken formula flips the checks tab FAIL on a "
          "verified recalc; recalc-unavailable applies the recalc-unverified cap + blocks the "
          "checks-tab claim with a labelled non-equivalent static fallback; checks are live "
          "formulas (no hardcoded PASS); source IDs + assumption owner/date/status carried; §5 untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
