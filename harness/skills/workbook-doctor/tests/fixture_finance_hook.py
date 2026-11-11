#!/usr/bin/env python3
"""
S05 functional fixture — Workbook Doctor WD-04 (finance-model domain hook).

This is the deferred `finance:*` functional fixture the capability inventory
scoped to S05 (the Workbook Doctor domain hook). It proves the HOOK we built:

  * detection — a financial model (Q3 board model: financial metrics, a Board
    sheet, a source packet of financial figures) is detected as financial;
    a NON-financial workbook (a generic contact list) is NOT;
  * dispatch — when financial, the hook recommends the right finance:* validators
    with their trigger signal + inputs (reconciliation when figures conflict;
    audit-support when an unsupported number is presented as fact; etc.);
  * opt-in — with the hook DISABLED (`--no-finance` / enabled=False), no
    finance:* validators are recommended even on a financial model: the core
    stays domain-agnostic.

`finance:*` themselves are invokable Cowork skills (markdown), proven available
in the session registry (capability-inventory.md); a live session that detects a
financial model invokes them via the Skill tool. This fixture proves the
deterministic detection + dispatch that decides WHETHER and WHICH to invoke.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh.
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

from openpyxl import Workbook  # noqa: E402
import build_fixtures   # noqa: E402
import workbook_map     # noqa: E402
import formula_risk     # noqa: E402
import domain_finance   # noqa: E402

# assess() is off unless a finance:* skill is installed; this fixture must pass without one.
os.environ["WD_FINANCE_SKILLS"] = "1"

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


FIN_PACKET = {
    "sources": [{"id": "S1", "owner": "FP&A", "date": "Q3-FY26", "source_status": "current"},
                {"id": "S2", "owner": "FP&A", "date": "Q3-FY26", "source_status": "estimate"}],
    "claims": [
        {"id": "C1", "class": "fact", "review_status": "conflicting", "metric": "revenue",
         "period": "Q3-FY26", "value": "€48.2M",
         "provenance": {"claim": "Q3 revenue €48.2M", "supporting": [{"source_id": "S1"}],
                        "conflict": {"with": ["S2"], "note": "€47.5M"}}},
        {"id": "C6", "class": "open-question", "review_status": "unsupported", "metric": "revenue_yoy",
         "period": "Q3-FY26", "value": "+12% YoY",
         "provenance": {"claim": "up 12% YoY", "supporting": [], "conflict": None}},
    ],
    "conflicts": [{"id": "CONFLICT-001", "topic": "Q3-FY26 revenue",
                   "competing": [{"value": "€48.2M", "source_id": "S1"}, {"value": "€47.5M", "source_id": "S2"}]}],
}


def build_non_financial(out_dir):
    """A generic, non-financial workbook — a contact list. No financial terms,
    no metrics, no period axis."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws["A1"] = "Team directory"
    ws["A3"] = "Name"; ws["B3"] = "Role"; ws["C3"] = "Email"
    ws["A4"] = "Alice"; ws["B4"] = "Designer"; ws["C4"] = "alice@x.io"
    ws["A5"] = "Bob"; ws["B5"] = "Writer"; ws["C5"] = "bob@x.io"
    path = os.path.join(out_dir, "contacts.xlsx")
    wb.save(path)
    return path


def scan(workbook, out, packet=None):
    os.makedirs(out, exist_ok=True)
    pk = None
    if packet is not None:
        pk = os.path.join(out, "pk.json")
        with open(pk, "w") as fh:
            json.dump(packet, fh)
    wm = workbook_map.run(workbook, out, source_packet=pk, run_id="dh")
    fr = formula_risk.run(workbook, out, source_packet=pk, run_id="dh", do_recalc=False)
    return wm, fr, pk


def run():
    work = tempfile.mkdtemp(prefix="wd_finance_")

    # --- financial model: the Q3 board model + financial packet ---
    fin_wb = build_fixtures.build_q3_board_model(work)
    fin_out = os.path.join(work, "fin")
    wm_f, fr_f, _pk = scan(fin_wb, fin_out, FIN_PACKET)

    det = domain_finance.detect(wm_f, fr_f, FIN_PACKET)
    check(det["is_financial"] is True,
          "Q3 board model must be detected as a financial model; signals=%s" % det["signals"])
    check(det["score"] >= 2, "financial detection needs >=2 independent signals; got %s" % det["score"])

    enabled = domain_finance.assess(wm_f, fr_f, FIN_PACKET, enabled=True)
    validators = {h["validator"] for h in enabled["finance_hooks"]}
    check("reconciliation" in validators,
          "conflicting financial figures must recommend finance:reconciliation; got %s" % validators)
    check("audit-support" in validators,
          "an unsupported number presented as fact must recommend finance:audit-support; got %s" % validators)
    # every hook names a trigger + inputs
    for h in enabled["finance_hooks"]:
        check(h.get("why") and h.get("inputs"),
              "finance hook %r must name a trigger ('why') and inputs" % h.get("validator"))

    # --- opt-in: disabled hook recommends nothing even on a financial model ---
    off = domain_finance.assess(wm_f, fr_f, FIN_PACKET, enabled=False)
    check(off["enabled"] is False and off["finance_hooks"] == [],
          "with the hook disabled, NO finance:* validators may be recommended (domain-agnostic core)")

    # --- non-financial workbook is NOT detected as financial ---
    nf_wb = build_non_financial(work)
    nf_out = os.path.join(work, "nf")
    wm_n, fr_n, _ = scan(nf_wb, nf_out, None)
    det_n = domain_finance.detect(wm_n, fr_n, None)
    check(det_n["is_financial"] is False,
          "a generic contact list must NOT be detected as a financial model; signals=%s" % det_n["signals"])
    nf_assess = domain_finance.assess(wm_n, fr_n, None, enabled=True)
    check(nf_assess["finance_hooks"] == [],
          "no finance:* validators on a non-financial workbook")

    return work


if __name__ == "__main__":
    run()
    if _errs:
        print("FAIL — WD-04 finance domain hook:")
        for e in _errs:
            print("  -", e)
        sys.exit(1)
    print("PASS — WD-04: financial model detected (reconciliation + audit-support dispatched with triggers+inputs); "
          "non-financial workbook correctly NOT flagged; opt-out (--no-finance) recommends nothing.")
    sys.exit(0)
