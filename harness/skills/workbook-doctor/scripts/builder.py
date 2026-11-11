#!/usr/bin/env python3
"""
Workbook Doctor — BUILDER path (AB-01, v3 finding #4): a trustworthy-workbook
builder that is RECALC-GATED.

The Trust Layer owns the four-tab architecture + the check logic; the actual
.xlsx write is delegated to the `xlsx` skill (openpyxl is the same engine that
skill uses) — wrap-don't-rebuild. We scaffold:

    Raw  ->  Assumptions  ->  Calc  ->  Checks

and write a LIVE checks tab (the "smoke alarm"). Every check category is a real
worksheet FORMULA whose green/red is computed by the workbook — never a
hardcoded PASS. The five categories are the frozen §7.3 controlled enum:

    tie-outs · formula-consistency · error-scan · stale-date · scenario

THE RECALC GATE (the whole point of AB-01):
    A freshly written .xlsx has formulas but NO computed values — openpyxl
    cannot evaluate them. The checks-tab verdict (is the alarm green?) is only
    TRUSTWORTHY once a live engine actually recalculated the file. So:

      * recalc VERIFIED (LibreOffice ran, cached values present)
            -> read the engine-computed checks-tab values; that is the verdict.
      * recalc UNAVAILABLE / FAILED
            -> set the `recalc-unverified` degraded-status cap (conventions
               §7.1); the checks-tab readiness CLAIM is BLOCKED (the alarm was
               never powered). We DO still emit STATIC formula assertions as a
               labelled fallback — `equivalent_to_recalc: false` — NEVER passed
               off as a lit alarm.

Static assertions are a Python re-derivation of what each check formula SHOULD
evaluate to from the in-memory model. They are an honesty floor, NOT a recalc.

Source IDs and assumption owner/date/status are carried from the source packet
into the Raw / Assumptions tabs and recorded in the build report.

Pure stdlib + openpyxl. The real recalc binding wraps `xlsx/scripts/recalc.py`;
fixtures inject a deterministic binding so the gate logic is tested without
LibreOffice.

CLI:  python builder.py --spec spec.json --out OUTDIR [--no-recalc]
      python builder.py --selftest
"""
import argparse
import copy
import datetime as _dt
import glob
import json
import os
import subprocess
import sys

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))

# Frozen §7.3 controlled enum (the smoke-alarm categories) + the §7.1 cap id.
# Read from the contract when reachable so the builder can NEVER drift from it;
# fall back to the literal frozen values otherwise (we never write conventions).
CHECKS_TAB_ENUM = ["tie-outs", "formula-consistency", "error-scan",
                   "stale-date", "scenario"]
RECALC_UNVERIFIED_CAP = "recalc-unverified"


def _load_contract():
    """Read the §7.3 enum + §7.1 cap from references/conventions.json if present.

    Returns (enum_list, cap_id). Never raises; never writes. If the contract is
    not reachable, returns the frozen literals above so a packaged/standalone
    builder still honours the same controlled set.
    """
    for cand in (
        os.path.normpath(os.path.join(HERE, "..", "..", "trust-review", "references", "conventions.json")),
    ):
        try:
            d = json.load(open(cand, encoding="utf-8"))
            enum = list(d["checks_tab_spec"]["categories"])
            caps = list(d["degraded_status_caps"]["caps"])
            cap = RECALC_UNVERIFIED_CAP if RECALC_UNVERIFIED_CAP in caps else RECALC_UNVERIFIED_CAP
            if enum:
                return enum, cap
        except Exception:
            continue
    return list(CHECKS_TAB_ENUM), RECALC_UNVERIFIED_CAP


# ---------------------------------------------------------------------------
# Seed spec (a compact but REAL four-tab financial mini-model).
# ---------------------------------------------------------------------------
def seed_spec():
    """A deterministic seeded build spec used by the fixtures + --selftest.

    Models a tiny revenue workbook: four quarters of revenue (Raw, from S1),
    a QoQ growth assumption + a downside scenario factor (Assumptions, with
    owner/date/status carried from the packet), a Calc tab (total, advancing
    QoQ growth, a next-quarter projection, a scenario total), and a stated
    control total for the tie-out.
    """
    return {
        "run_id": "ab01-seed",
        "title": "Harbor FY26 revenue mini-model",
        "as_of": "2026-05-01",
        "today": "2026-05-31",
        "stale_after_days": 120,
        "raw": {
            "metric": "Revenue",
            "periods": ["Q1", "Q2", "Q3", "Q4"],
            "values": [100.0, 120.0, 140.0, 165.0],
            "source_id": "S1",
        },
        "control_total": 525.0,           # stated control figure (= sum of the four)
        "control_source_id": "S1",
        "scenario": "base",               # base | downside
        "assumptions": [
            {"key": "growth", "label": "QoQ growth (proj.)", "value": 0.10,
             "owner": "FP&A", "date": "2026-05-01", "status": "current", "source_id": "S2"},
            {"key": "scenario_factor", "label": "Downside factor", "value": 0.85,
             "owner": "CFO office", "date": "2026-05-01", "status": "current", "source_id": "S3"},
        ],
        # inject: an honest single-formula defect for the acceptance fixture.
        #   "tie-break"  -> Calc revenue-total formula drops a component (=SUM B:D)
        #   "consistency"-> one QoQ growth cell freezes its refs (roll-forward bug)
        #   "error"      -> a divide-by-zero is introduced into Calc
        "inject": None,
    }


# ---------------------------------------------------------------------------
# STATIC engine — a faithful Python re-derivation of every check formula.
#   * the builder uses it for the labelled static-assertion fallback;
#   * fixtures reuse it to simulate a verified engine deterministically.
# It is NOT a recalc and is always labelled as such where it is used as a
# fallback. (Simulating LibreOffice in a unit test is a separate, declared use.)
# ---------------------------------------------------------------------------
def evaluate_model(spec):
    raw = spec["raw"]
    vals = list(raw["values"])
    inj = spec.get("inject")
    assumptions = {a["key"]: a["value"] for a in spec["assumptions"]}
    growth = assumptions.get("growth", 0.0)
    factor = assumptions.get("scenario_factor", 1.0)
    downside = spec.get("scenario") == "downside"

    # Calc tab values (mirrors the worksheet formulas) -----------------------
    if inj == "tie-break":
        revenue_total = sum(vals[:3])          # formula drops the 4th component
    else:
        revenue_total = sum(vals)
    control_total = spec["control_total"]

    # advancing QoQ growth (periods 2..n); inject "consistency" freezes one ref
    qoq = []
    err_cells = []
    for i in range(1, len(vals)):
        prev = vals[i - 1]
        if inj == "consistency" and i == 2:
            # roll-forward bug: numerator/denominator both frozen to period 2/1
            num, den = vals[1], vals[0]
        elif inj == "error" and i == 1:
            num, den = vals[i], 0.0            # divide-by-zero -> #DIV/0!
        else:
            num, den = vals[i], prev
        if den == 0:
            qoq.append(("#DIV/0!"))
            err_cells.append("Calc!growth[%d]" % i)
        else:
            qoq.append(num / den - 1.0)

    projection = vals[-1] * (1.0 + growth)
    scenario_total = revenue_total * (factor if downside else 1.0)

    # Check results (each mirrors the worksheet IF()) ------------------------
    def _num(x):
        return isinstance(x, (int, float))

    # tie-outs: total == control AND total == sum(components)
    tie = (_num(revenue_total) and abs(revenue_total - control_total) < 1e-9
           and abs(revenue_total - sum(vals)) < 1e-9)

    # formula-consistency: each calc QoQ equals an independent re-derivation
    consistent = True
    for i in range(1, len(vals)):
        indep = (vals[i] / vals[i - 1] - 1.0) if vals[i - 1] else "#DIV/0!"
        got = qoq[i - 1]
        if not (_num(indep) and _num(got) and abs(indep - got) < 1e-9):
            consistent = False
            break

    # error-scan: no Excel error survives in the Calc outputs
    error_free = len(err_cells) == 0

    # stale-date: (today - as_of) <= stale_after_days
    asof = _dt.date.fromisoformat(spec["as_of"])
    today = _dt.date.fromisoformat(spec["today"])
    fresh = (today - asof).days <= spec["stale_after_days"]

    # scenario: scenario_total == revenue_total * applied factor
    applied = factor if downside else 1.0
    scenario_ok = _num(scenario_total) and abs(scenario_total - revenue_total * applied) < 1e-9

    checks = {
        "tie-outs": "PASS" if tie else "FAIL",
        "formula-consistency": "PASS" if consistent else "FAIL",
        "error-scan": "PASS" if error_free else "FAIL",
        "stale-date": "PASS" if fresh else "FAIL",
        "scenario": "PASS" if scenario_ok else "FAIL",
    }
    overall = "ALL PASS" if all(v == "PASS" for v in checks.values()) else "FAIL"
    return {
        "calc": {
            "revenue_total": revenue_total,
            "control_total": control_total,
            "qoq": qoq,
            "projection": projection,
            "scenario_total": scenario_total,
            "error_cells": err_cells,
        },
        "checks": checks,
        "overall": overall,
    }


# ---------------------------------------------------------------------------
# BUILD — scaffold the four-tab workbook with LIVE formulas.
# ---------------------------------------------------------------------------
# Worksheet cell map (kept stable so the Checks formulas can reference it):
#   Raw!B4:E4   revenue components   Raw!F4 source id
#   Calc!B3     revenue total        Calc!B4 control total
#   Calc!C6:E6  advancing QoQ growth
#   Calc!B7     projection           Calc!B8 scenario total
#   Assumptions!C4 growth  C5 factor  C6 scenario  C7 as_of(date)  C8 stale_days
#   Checks!C4:C8 the five checks     Checks!C10 overall   Checks!G2 today(date)
def build_workbook(spec, path):
    spec = copy.deepcopy(spec)
    inj = spec.get("inject")
    raw = spec["raw"]
    vals = raw["values"]
    n = len(vals)
    last_col = chr(ord("B") + n - 1)            # e.g. 4 periods -> "E"

    wb = openpyxl.Workbook()

    # ---- Raw -------------------------------------------------------------
    rs = wb.active
    rs.title = "Raw"
    rs["A1"] = "Raw inputs (as delivered — each row carries its Source ID)"
    rs["A3"] = "Metric"
    for i, p in enumerate(raw["periods"]):
        rs.cell(row=3, column=2 + i, value=p)
    rs.cell(row=3, column=2 + n, value="Source")
    rs["A4"] = raw["metric"]
    for i, v in enumerate(vals):
        rs.cell(row=4, column=2 + i, value=v)
    rs.cell(row=4, column=2 + n, value=raw["source_id"])

    # ---- Assumptions -----------------------------------------------------
    as_ = wb.create_sheet("Assumptions")
    as_["A1"] = "Assumptions (owner / date / status carried from the source packet)"
    for j, h in enumerate(["Key", "Label", "Value", "Owner", "Date", "Status", "Source"]):
        as_.cell(row=3, column=1 + j, value=h)
    # row 4 = growth, row 5 = scenario_factor (cell refs the Checks tab relies on)
    by_key = {a["key"]: a for a in spec["assumptions"]}
    order = ["growth", "scenario_factor"]
    r = 4
    rowref = {}
    for k in order:
        a = by_key[k]
        as_.cell(row=r, column=1, value=a["key"])
        as_.cell(row=r, column=2, value=a["label"])
        as_.cell(row=r, column=3, value=a["value"])
        as_.cell(row=r, column=4, value=a.get("owner", ""))
        as_.cell(row=r, column=5, value=a.get("date", ""))
        as_.cell(row=r, column=6, value=a.get("status", ""))
        as_.cell(row=r, column=7, value=a.get("source_id", ""))
        rowref[k] = r
        r += 1
    # scenario toggle + stale-date controls
    as_["A6"] = "scenario"; as_["B6"] = "base|downside toggle"; as_["C6"] = spec.get("scenario", "base")
    as_["A7"] = "as_of"; as_["B7"] = "workbook as-of date"
    as_["C7"] = _dt.date.fromisoformat(spec["as_of"])
    as_["C7"].number_format = "yyyy-mm-dd"
    as_["A8"] = "stale_after_days"; as_["B8"] = "freshness threshold (days)"; as_["C8"] = spec["stale_after_days"]
    GROWTH = "Assumptions!C%d" % rowref["growth"]
    FACTOR = "Assumptions!C%d" % rowref["scenario_factor"]

    # ---- Calc ------------------------------------------------------------
    cs = wb.create_sheet("Calc")
    cs["A1"] = "Calculations (derived — live formulas only)"
    cs["A3"] = "Revenue total"
    if inj == "tie-break":
        # the single broken formula: drops the last component
        drop_col = chr(ord("B") + n - 2)
        cs["B3"] = "=SUM(Raw!B4:%s4)" % drop_col
    else:
        cs["B3"] = "=SUM(Raw!B4:%s4)" % last_col
    cs["A4"] = "Control total (stated, %s)" % spec.get("control_source_id", "")
    cs["B4"] = spec["control_total"]
    cs["A6"] = "QoQ growth (advancing)"
    for i in range(1, n):
        col = chr(ord("B") + i)
        prevcol = chr(ord("B") + i - 1)
        if inj == "consistency" and i == 2:
            # roll-forward bug: refs frozen to the first step (does not advance)
            cs["%s6" % col] = "=Raw!C4/Raw!B4-1"
        elif inj == "error" and i == 1:
            cs["%s6" % col] = "=Raw!%s4/0" % col       # divide-by-zero
        else:
            cs["%s6" % col] = "=Raw!%s4/Raw!%s4-1" % (col, prevcol)
    cs["A7"] = "Next-period projection"
    cs["B7"] = "=Raw!%s4*(1+%s)" % (last_col, GROWTH)
    cs["A8"] = "Scenario total"
    cs["B8"] = '=B3*IF(Assumptions!C6="downside",%s,1)' % FACTOR

    # ---- Checks (the smoke alarm — LIVE formulas; green/red from the sheet)
    ks = wb.create_sheet("Checks")
    ks["A1"] = "Checks (the smoke alarm — every result is a live formula)"
    ks["G1"] = "today"
    ks["G2"] = _dt.date.fromisoformat(spec["today"]); ks["G2"].number_format = "yyyy-mm-dd"
    for j, h in enumerate(["Check", "Category", "Result", "Detail"]):
        ks.cell(row=3, column=1 + j, value=h)
    growth_last = chr(ord("B") + n - 1)
    # independent QoQ re-derivation terms for the consistency check
    consistency_terms = []
    for i in range(1, n):
        col = chr(ord("B") + i)
        prevcol = chr(ord("B") + i - 1)
        consistency_terms.append(
            "ROUND(Calc!%s6-(Raw!%s4/Raw!%s4-1),9)=0" % (col, col, prevcol))
    rows = [
        ("Totals tie-out", "tie-outs",
         '=IF(AND(Calc!B3=Calc!B4,Calc!B3=SUM(Raw!B4:%s4)),"PASS","FAIL")' % last_col,
         "total = control AND = sum(components)"),
        ("Row / parallel-formula consistency", "formula-consistency",
         '=IF(AND(%s),"PASS","FAIL")' % ",".join(consistency_terms),
         "each QoQ cell = independent re-derivation"),
        ("Error scan (ISERROR)", "error-scan",
         '=IF(SUMPRODUCT(--ISERROR(Calc!B3:%s8))=0,"PASS","FAIL")' % growth_last,
         "no Excel error survives recalc"),
        ("Stale-date flag", "stale-date",
         '=IF(($G$2-Assumptions!C7)<=Assumptions!C8,"PASS","FAIL")',
         "(today - as_of) <= stale_after_days"),
        ("Scenario toggle", "scenario",
         '=IF(ROUND(Calc!B8-Calc!B3*IF(Assumptions!C6="downside",%s,1),9)=0,"PASS","FAIL")' % FACTOR,
         "scenario total recomputes consistently"),
    ]
    for i, (name, cat, formula, detail) in enumerate(rows):
        rr = 4 + i
        ks.cell(row=rr, column=1, value=name)
        ks.cell(row=rr, column=2, value=cat)
        ks.cell(row=rr, column=3, value=formula)
        ks.cell(row=rr, column=4, value=detail)
    ks["A10"] = "ALL CHECKS"
    ks["C10"] = '=IF(COUNTIF(C4:C8,"FAIL")=0,"ALL PASS","FAIL")'

    wb.save(path)
    wb.close()
    # cell map the recalc binding reads back
    return {
        "checks_cells": {
            "tie-outs": "Checks!C4", "formula-consistency": "Checks!C5",
            "error-scan": "Checks!C6", "stale-date": "Checks!C7",
            "scenario": "Checks!C8", "overall": "Checks!C10",
        },
        "tabs": ["Raw", "Assumptions", "Calc", "Checks"],
        "path": path,
    }


# ---------------------------------------------------------------------------
# RECALC binding — the real one wraps xlsx/scripts/recalc.py.
# Returns: {ran: bool, note, status, total_errors, error_summary,
#           computed: {checks_cell: value}}  (computed only when ran).
# ---------------------------------------------------------------------------
def _find_xlsx_recalc():
    cands = []
    env = os.environ.get("SKILLS_ROOT")
    if env:
        cands.append(os.path.join(env, "xlsx", "scripts"))
    cands += glob.glob("/sessions/*/mnt/.claude/skills/xlsx/scripts")
    cands += [os.path.expanduser("~/.claude/skills/xlsx/scripts")]
    cands += glob.glob("/var/folders/*/T/claude-hostloop-plugins/*/skills/xlsx/scripts")
    for d in cands:
        if os.path.isfile(os.path.join(d, "recalc.py")):
            return d
    return None


def default_recalc_binding(path, checks_cells, timeout=120):
    """Run the xlsx recalc engine IN PLACE, then read back the cached check
    values. A failure to run is reported honestly as ran=False (the gate then
    applies the recalc-unverified cap) — never a fabricated green."""
    scripts_dir = _find_xlsx_recalc()
    if not scripts_dir:
        return {"ran": False, "note": "recalc unverified — xlsx/scripts/recalc.py not found in this environment"}
    try:
        proc = subprocess.run(
            [sys.executable, "recalc.py", os.path.abspath(path), "60"],
            cwd=scripts_dir, capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        data = json.loads(out) if out.startswith("{") else {"error": out or proc.stderr}
    except subprocess.TimeoutExpired:
        return {"ran": False, "note": "recalc unverified — LibreOffice timed out"}
    except Exception as e:
        return {"ran": False, "note": "recalc unverified — %s" % e}
    if "error" in data:
        return {"ran": False, "note": "recalc unverified — %s" % data["error"]}

    # recalc.py recalculated + saved in place; read back the engine's values.
    computed = {}
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        for cat, ref in checks_cells.items():
            sheet, cell = ref.split("!")
            computed[cat] = wb[sheet][cell].value
        wb.close()
    except Exception as e:
        return {"ran": False, "note": "recalc unverified — could not read cached values: %s" % e}

    # If the engine wrote no cached value for the alarm, treat as NOT verified
    # (an unevaluated formula must never read as a lit alarm).
    if any(computed.get(c) is None for c in ("tie-outs", "overall")):
        return {"ran": False, "note": "recalc unverified — engine produced no cached checks-tab values"}
    return {
        "ran": True,
        "engine": "libreoffice",
        "status": data.get("status"),
        "total_errors": data.get("total_errors", 0),
        "error_summary": data.get("error_summary", {}),
        "computed": computed,
    }


def make_injected_recalc(spec):
    """Build a DETERMINISTIC recalc binding that simulates a verified engine by
    computing the cached check values from the static model. Used ONLY by the
    fixtures (and --selftest) to test the gate without LibreOffice. It is a
    declared test stand-in for the engine — NOT the product's static fallback."""
    model = evaluate_model(spec)

    def _binding(path, checks_cells, timeout=120):
        computed = dict(model["checks"])
        computed["overall"] = model["overall"]
        return {
            "ran": True,
            "engine": "injected-deterministic (test stand-in for LibreOffice)",
            "status": "success" if model["checks"]["error-scan"] == "PASS" else "errors_found",
            "total_errors": len(model["calc"]["error_cells"]),
            "error_summary": {},
            "computed": computed,
        }
    return _binding


# ---------------------------------------------------------------------------
# ORCHESTRATOR — build -> recalc gate -> verdict + caps + static fallback.
# ---------------------------------------------------------------------------
def run_builder(spec, out_dir, recalc_binding=None, run_id=None):
    enum, cap_id = _load_contract()
    os.makedirs(out_dir, exist_ok=True)
    run_id = run_id or spec.get("run_id") or "ab01-build"
    wb_path = os.path.join(out_dir, "model.xlsx")
    built = build_workbook(spec, wb_path)
    checks_cells = built["checks_cells"]

    # Honesty guard: the builder's categories MUST be a subset of the frozen
    # §7.3 controlled enum (no ad-hoc / empty checks-tab claim).
    cats = [c for c in checks_cells if c != "overall"]
    assert set(cats) == set(enum), (
        "checks-tab categories %r must equal the §7.3 enum %r" % (sorted(cats), sorted(enum)))

    # ---- STATIC formula assertions (the labelled fallback) ----------------
    model = evaluate_model(spec)
    static_assertions = {
        "basis": "static-formula-assertion",
        "equivalent_to_recalc": False,
        "note": ("Static re-derivation of the check formulas from the in-memory "
                 "model. NOT a recalc — the live engine never powered the alarm."),
        "results": dict(model["checks"]),
        "overall": model["overall"],
    }

    # ---- THE RECALC GATE --------------------------------------------------
    binding = recalc_binding or default_recalc_binding
    rc = binding(wb_path, checks_cells)
    degraded_status = []
    if rc.get("ran"):
        computed = rc["computed"]
        checks_tab = {
            "recalc_verified": True,
            "engine": rc.get("engine", "libreoffice"),
            "results": {c: computed.get(c) for c in enum},
            "overall": computed.get("overall"),
            "claim": "valid",            # the alarm was actually powered
            "source": "live-recalc (engine-computed cached values)",
        }
        alarm_green = (computed.get("overall") == "ALL PASS")
        checks_tab_readiness = "trusted"
    else:
        # recalc unavailable/failed -> CAP, do not bless the alarm.
        computed = None
        degraded_status.append({
            "cap": cap_id,
            "condition": rc.get("note", "live recalc unavailable"),
            "capped_at": "ready-with-limitations",
            "surface": "excel",
            "note": ("the checks-tab readiness CLAIM is blocked (the smoke alarm "
                     "was never powered); only static formula assertions ran"),
        })
        checks_tab = {
            "recalc_verified": False,
            "engine": None,
            "results": None,                 # NOT read from an unevaluated sheet
            "overall": None,
            "claim": "blocked",              # §7.3 — claim invalid without recalc
            "claim_reason": rc.get("note", "live recalc unavailable"),
            "static_assertions": static_assertions,   # labelled, non-equivalent
        }
        alarm_green = None                   # unknown — we refuse to assert it
        checks_tab_readiness = "blocked-recalc-unverified"

    # ---- readiness (a cap can only lower, never raise) --------------------
    if rc.get("ran"):
        readiness = "ready" if alarm_green else "not-ready"
        readiness_reason = ("checks tab green on a verified recalc" if alarm_green
                            else "checks tab FAILED on a verified recalc — a real defect")
    else:
        readiness = "ready-with-limitations"   # capped by recalc-unverified
        readiness_reason = ("recalc-unverified cap applied — checks-tab readiness "
                            "claim blocked; static assertions are a non-equivalent fallback")

    # ---- carry source IDs + assumption owner/date/status ------------------
    provenance = {
        "raw_source_id": spec["raw"]["source_id"],
        "control_source_id": spec.get("control_source_id"),
        "assumptions": [
            {"key": a["key"], "owner": a.get("owner"), "date": a.get("date"),
             "status": a.get("status"), "source_id": a.get("source_id")}
            for a in spec["assumptions"]
        ],
    }

    report = {
        "run_id": run_id,
        "skill": "workbook-doctor",
        "phase": "build",
        "path": "builder",
        "title": spec.get("title"),
        "workbook": wb_path,
        "tabs": built["tabs"],
        "checks_tab_enum": enum,
        "checks_tab": checks_tab,
        "checks_tab_readiness": checks_tab_readiness,
        "recalc": {"ran": bool(rc.get("ran")), "engine": rc.get("engine"),
                   "note": rc.get("note"), "status": rc.get("status"),
                   "total_errors": rc.get("total_errors")},
        "degraded_status": degraded_status,
        "static_assertions": static_assertions,
        "provenance": provenance,
        "readiness": readiness,
        "readiness_reason": readiness_reason,
    }

    _emit_report(out_dir, report)
    _emit_manifest(out_dir, run_id, report)
    return report


def _emit_report(out_dir, report):
    json.dump(report, open(os.path.join(out_dir, "build-report.json"), "w", encoding="utf-8"),
              indent=2, default=str)
    L = ["# Workbook builder — build report (AB-01)", ""]
    L.append("**Title:** %s  " % report.get("title"))
    L.append("**Tabs:** %s  " % " -> ".join(report["tabs"]))
    L.append("**Readiness:** `%s` — %s  " % (report["readiness"], report["readiness_reason"]))
    rc = report["recalc"]
    L.append("**Recalc:** ran=%s engine=%s %s  " % (rc["ran"], rc.get("engine"),
             "(%s)" % rc["note"] if rc.get("note") else ""))
    ct = report["checks_tab"]
    L.append("")
    L.append("## Checks tab (the smoke alarm)")
    L.append("- recalc_verified: **%s** — claim: **%s**" % (ct["recalc_verified"], ct["claim"]))
    if ct.get("results"):
        for c in report["checks_tab_enum"]:
            L.append("  - `%s`: **%s**" % (c, ct["results"].get(c)))
        L.append("  - **overall: %s**" % ct.get("overall"))
    else:
        L.append("  - results: _blocked — recalc not verified; see static assertions below_")
    if report["degraded_status"]:
        L.append("")
        L.append("## Degraded-status caps")
        for d in report["degraded_status"]:
            L.append("- `%s` @ %s — %s" % (d["cap"], d["capped_at"], d["note"]))
    sa = report["static_assertions"]
    L.append("")
    L.append("## Static formula assertions — **NOT equivalent to recalc** (%s)" % sa["basis"])
    for c in report["checks_tab_enum"]:
        L.append("- `%s`: %s" % (c, sa["results"].get(c)))
    L.append("- overall: %s" % sa["overall"])
    L.append("")
    L.append("## Provenance carried from the source packet")
    p = report["provenance"]
    L.append("- Raw source: `%s`; control source: `%s`" % (p["raw_source_id"], p["control_source_id"]))
    for a in p["assumptions"]:
        L.append("- assumption `%s` — owner=%s date=%s status=%s source=%s"
                 % (a["key"], a["owner"], a["date"], a["status"], a["source_id"]))
    open(os.path.join(out_dir, "build-report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def _emit_manifest(out_dir, run_id, report):
    manifest = {
        "schema_version": 3,
        "run_id": run_id,
        "skill": "workbook-doctor",
        "phase": "build",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "artifact": report["workbook"],
        "readiness": report["readiness"],
        "degraded_status": report["degraded_status"],
        "checks_tab": {"readiness": report["checks_tab_readiness"],
                       "claim": report["checks_tab"]["claim"],
                       "recalc_verified": report["checks_tab"]["recalc_verified"]},
        "migration_notes": "v3 builder path (ab-01); recalc-gated checks tab",
        "gate": "ok",
    }
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"),
              indent=2, default=str)


# ---------------------------------------------------------------------------
def _selftest():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="otl_builder_self_", dir=os.environ.get("TMPDIR", "/tmp"))
    ok = True

    # 1) clean build, injected verified recalc -> alarm green, ready
    spec = seed_spec()
    rep = run_builder(spec, os.path.join(tmp, "clean"), recalc_binding=make_injected_recalc(spec))
    ok &= rep["readiness"] == "ready" and rep["checks_tab"]["overall"] == "ALL PASS"
    ok &= rep["checks_tab"]["claim"] == "valid" and not rep["degraded_status"]

    # 2) broken formula, injected verified recalc -> tie-out FAIL, not-ready
    bad = seed_spec(); bad["inject"] = "tie-break"
    rep2 = run_builder(bad, os.path.join(tmp, "broken"), recalc_binding=make_injected_recalc(bad))
    ok &= rep2["readiness"] == "not-ready"
    ok &= rep2["checks_tab"]["results"]["tie-outs"] == "FAIL"
    ok &= rep2["checks_tab"]["overall"] == "FAIL"

    # 3) recalc unavailable -> cap applied, claim blocked, static labelled
    rep3 = run_builder(spec, os.path.join(tmp, "norecalc"),
                       recalc_binding=lambda *a, **k: {"ran": False, "note": "forced-unavailable"})
    caps = [d["cap"] for d in rep3["degraded_status"]]
    ok &= RECALC_UNVERIFIED_CAP in caps
    ok &= rep3["checks_tab"]["claim"] == "blocked"
    ok &= rep3["readiness"] == "ready-with-limitations"
    ok &= rep3["static_assertions"]["equivalent_to_recalc"] is False

    print("builder --selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Workbook Doctor builder (AB-01, recalc-gated)")
    ap.add_argument("--spec", help="path to a build-spec JSON (default: seed spec)")
    ap.add_argument("--out", help="output dir (.trust/<run_id>/)")
    ap.add_argument("--no-recalc", action="store_true",
                    help="force the recalc-unavailable path (demonstrates the cap)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.out:
        ap.error("--out is required (unless --selftest)")
    spec = json.load(open(args.spec, encoding="utf-8")) if args.spec else seed_spec()
    binding = (lambda *a, **k: {"ran": False, "note": "recalc disabled (--no-recalc)"}) if args.no_recalc else None
    rep = run_builder(spec, args.out, recalc_binding=binding)
    print(json.dumps({"readiness": rep["readiness"],
                      "checks_tab_claim": rep["checks_tab"]["claim"],
                      "recalc_ran": rep["recalc"]["ran"],
                      "degraded_status": [d["cap"] for d in rep["degraded_status"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
