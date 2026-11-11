#!/usr/bin/env python3
"""
Office Trust Layer — Workbook Doctor · WD-03: assessment + verdict.

Consumes the two upstream artifacts (WD-01 `workbook-map.json` + WD-02
`formula-risk.json`) and, optionally, the `source-packet.json` that the
formula-risk linkage already used, and turns their raw findings into a
DECISION:

  1. an ASSUMPTIONS TABLE — every load-bearing assumption the workbook rests on
     (name, value, unit, source, owner, date, status), assembled from the
     source-packet claims that carry judgement (assumption/estimate/
     open-question, plus any conflicting/unsupported fact) AND from the
     undocumented constants the workbook bakes in (WD-02 hardcodes);
  2. a REPAIR PLAN — every unresolved finding, grouped by the conventions.md
     `severity` enum (must-fix -> should-fix -> polish), each with a concrete
     recommended action and its current `issue_lifecycle` state;
  3. a VERIFICATION MEMO — an explicit pass / fail per check
     (tie-outs · formula consistency · source traceability · stale data ·
     recalculation · data structure), each naming the findings that drive it; and
  4. a READINESS VERDICT — `not-ready` / `ready-with-limitations` / `ready`
     computed strictly from the must-fix lifecycle per conventions.md §1.4/§4.
     It is NEVER `ready` while any must-fix is open / fixed-claimed / still-open;
     a waived must-fix caps it at `ready-with-limitations` (Board/Regulatory
     additionally needs named-owner signoff). Always paired with the explicit
     `must_own` list a human still has to own.

The HUMAN GATE is an input, not an afterthought: `--resolutions <file>` lets a
named human move a finding to `verified-fixed` (re-checked) or `waived-by-human`
(risk accepted, with the §4 waiver record). Without it, raw scan findings are
`open` and a workbook with any must-fix is `not-ready` — which is the point.

Enums (severity / review_status / issue_lifecycle / readiness / status) come
from references/conventions.md and are used VERBATIM — never redefined here.

Outputs, per the I/O contract:
  <out>/workbook-verdict.json   (machine — verdict, assumptions, repair plan, memo)
  <out>/workbook-verdict.md     (human memo)

Usage:
  python assessment.py --map workbook-map.json --formula-risk formula-risk.json \
      [--source-packet packet.json] [--resolutions resolutions.json] \
      [--travel-class Travels] [--no-finance] [--out DIR] [--run-id ID]
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import domain_finance  # noqa: E402  (WD-04 hook — opt-in, domain-agnostic core)

# ---- conventions enums (verbatim from references/conventions.md) -------------
SEVERITY_ORDER = {"must-fix": 0, "should-fix": 1, "polish": 2}
BLOCKING_LIFECYCLE = {"open", "fixed-claimed", "still-open"}   # §1.4 / §1.5
TERMINAL_OK = {"verified-fixed"}                                # §1.5
WAIVED = "waived-by-human"
WAIVER_REQUIRED_FIELDS = ("owner", "decision", "due_date", "signoff", "rationale")  # §4.2

# Which verification-memo check each finding `check` belongs to. Every finding
# type maps to exactly one memo line so the memo is a complete partition.
# source_packet_link is split at runtime by detail.review_status.
CHECK_TO_MEMO = {
    # tie-outs — the displayed number must reconcile / recompute to the right value
    "recalc_error": "tie_outs",
    "hardcode_in_calc_zone": "tie_outs",
    # formula consistency — references behave correctly across the model
    "roll_forward": "formula_consistency",
    "shape_consistency": "formula_consistency",
    "repeated_literal_ref": "formula_consistency",
    "circular_reference": "formula_consistency",
    "hardcode_in_formula": "formula_consistency",
    # source traceability — every number traces to a current source
    "source_without_id_date": "source_traceability",
    # stale data — broken / moved / hidden / superseded
    "dangling_reference": "stale_data",
    "ref_error": "stale_data",
    "named_range": "stale_data",
    "hidden_sheet": "stale_data",
    # data structure — the WD-01 structural landmines
    "merged_cells": "data_structure",
    "blank_rows_in_table": "data_structure",
    "duplicate_rows": "data_structure",
    "number_as_text": "data_structure",
    "date_as_text": "data_structure",
    "mixed_units": "data_structure",
    "unclear_headers": "data_structure",
}
MEMO_CHECKS = ["tie_outs", "formula_consistency", "source_traceability",
               "stale_data", "recalculation", "data_structure"]
MEMO_LABEL = {
    "tie_outs": "Tie-outs (numbers reconcile / recompute to the right value)",
    "formula_consistency": "Formula consistency (references behave across the model)",
    "source_traceability": "Source traceability (every number traces to a current source)",
    "stale_data": "Stale data (no broken / moved / hidden / superseded inputs)",
    "recalculation": "Recalculation (the workbook recomputes clean)",
    "data_structure": "Data structure (no parsing landmines)",
}

# Concrete repair action per check (imperative; falls back to the finding text).
REPAIR_ACTION = {
    "roll_forward": "Re-fill the row so each period's reference advances one column; replace the stuck absolute refs.",
    "shape_consistency": "Reconcile the odd cell to its row siblings or document why it differs.",
    "repeated_literal_ref": "Replace the repeated absolute reference with a relative one that rolls forward.",
    "hardcode_in_calc_zone": "Replace the typed number with the formula its neighbours use, or move it to a labelled input.",
    "hardcode_in_formula": "Move the magic constant to a labelled, sourced assumption cell and reference it.",
    "ref_error": "Repair the broken reference; re-point it at the live precedent.",
    "named_range": "Re-point the named range at a live destination (its target contains #REF!).",
    "dangling_reference": "Re-point the formula at the live precedent cell (the current target is empty).",
    "circular_reference": "Break the circular dependency chain so the model resolves a stable value.",
    "recalc_error": "Fix the input/divisor producing the live calculation error.",
    "source_packet_link": "Source the number against a current claim, or correct it to the authoritative value.",
    "merged_cells": "Unmerge the range; keep the value in a single addressable cell.",
    "blank_rows_in_table": "Remove the blank row inside the table so ranges/fills/sorts are intact.",
    "duplicate_rows": "Remove the duplicate data row so aggregations don't double-count.",
    "number_as_text": "Convert the text to a real number so it sums and sorts correctly.",
    "date_as_text": "Convert the text to a real date so it compares and sorts.",
    "mixed_units": "Split the column by unit (or normalise) so values are comparable/addable.",
    "unclear_headers": "Give the column a clear, labelled header.",
    "source_without_id_date": "Add a source-ID and date/period column so rows trace to a source.",
    "hidden_sheet": "Surface or document the hidden sheet; confirm its numbers aren't stale.",
}


# ---- value / unit helpers ----------------------------------------------------
def infer_unit(value):
    """Coarse unit tag for an assumption value (string or number)."""
    if isinstance(value, bool):
        return "flag"
    if isinstance(value, (int, float)):
        v = float(value)
        if 0 < abs(v) < 1:
            return "ratio/percent"
        return "number"
    if not isinstance(value, str):
        return "—"
    s = value.strip()
    if "%" in s:
        return "percent"
    if re.search(r"€|EUR", s, re.I) or s.startswith("€"):
        return "EUR" + ("M" if re.search(r"\bM\b|M\b|m\b", s) else "")
    if s.startswith("$") or re.search(r"\bUSD\b", s, re.I):
        return "USD"
    if s.startswith("£"):
        return "GBP"
    if re.search(r"\d\s*[xX]\b", s):
        return "multiple"
    if re.search(r"\bbps\b", s, re.I):
        return "bps"
    if re.search(r"^\D*\d[\d,\.]*\D*$", s):
        return "number/text"
    return "—"


def _src_index(packet):
    return {s.get("id"): s for s in (packet.get("sources") or [])} if packet else {}


# ---- assumptions table -------------------------------------------------------
ASSUMPTION_CLASSES = {"assumption", "estimate", "open-question"}
ASSUMPTION_STATUSES = {"unsupported", "conflicting"}


def build_assumptions(packet, findings):
    """Every load-bearing assumption: from packet claims that carry judgement,
    and from undocumented constants the workbook bakes in (WD-02 hardcodes)."""
    rows = []
    src_by_id = _src_index(packet)

    # 1) source-packet claims a human must own
    for cl in (packet.get("claims") if packet else []) or []:
        cls = (cl.get("class") or "").lower()
        rv = (cl.get("review_status") or "").lower()
        if cls not in ASSUMPTION_CLASSES and rv not in ASSUMPTION_STATUSES:
            continue
        prov = cl.get("provenance") or {}
        supporting = prov.get("supporting") or []
        src_ids = [s.get("source_id") for s in supporting if s.get("source_id")]
        owner = "unattributed"
        date = cl.get("period") or "—"
        if src_ids and src_ids[0] in src_by_id:
            owner = src_by_id[src_ids[0]].get("owner", owner)
            date = src_by_id[src_ids[0]].get("date", date)
        rows.append({
            "name": prov.get("claim") or cl.get("metric") or cl.get("id"),
            "value": cl.get("value"),
            "unit": infer_unit(cl.get("value")),
            "source": ", ".join(src_ids) if src_ids else "none",
            "owner": owner,
            "date": date,
            "status": rv or "needs-review",      # conventions §1.3 verbatim
            "origin": "source-packet",
            "claim_id": cl.get("id"),
            "class": cls or None,
        })

    # 2) undocumented constants the workbook itself asserts (WD-02 hardcodes)
    for f in findings:
        if f.get("check") not in ("hardcode_in_formula", "hardcode_in_calc_zone"):
            continue
        m = re.search(r"\(([-+]?\d[\d,\.]*)\)", f.get("message") or "")
        const = m.group(1) if m else None
        if const is None:
            m2 = re.search(r"constant\s+([-+]?\d[\d,\.]*)", f.get("message") or "")
            const = m2.group(1) if m2 else "(see finding)"
        rows.append({
            "name": "Hardcoded value at %s!%s" % (f.get("sheet"), f.get("locator")),
            "value": const,
            "unit": infer_unit(const),
            "source": "(in-cell, no source)",
            "owner": "unknown",
            "date": "—",
            "status": "unsupported",             # an undocumented constant has no source
            "origin": "workbook-hardcode",
            "finding_id": f.get("id"),
            "class": "assumption",
        })
    return rows


# ---- resolutions / human gate ------------------------------------------------
def apply_resolutions(findings, resolutions):
    """Overlay the human-gate decisions onto raw scan findings.

    Each finding's effective `issue_lifecycle` defaults to its scan value
    (`open`). A resolution row may move it to `verified-fixed` or
    `waived-by-human`. Waivers carry the §4 record; an INVALID waiver (missing a
    required field) is rejected -> the finding stays blocking.
    """
    by_id = {f["id"]: f for f in findings}
    waiver_problems = []
    waivers = []
    for res in (resolutions.get("resolutions") if resolutions else []) or []:
        fid = res.get("finding_id")
        f = by_id.get(fid)
        if not f:
            waiver_problems.append("resolution names unknown finding %r" % fid)
            continue
        life = res.get("lifecycle")
        if life == WAIVED:
            w = res.get("waiver") or {}
            missing = [k for k in WAIVER_REQUIRED_FIELDS if not w.get(k)]
            if missing:
                waiver_problems.append(
                    "waiver on %s is INVALID — missing %s; finding stays blocking"
                    % (fid, ", ".join(missing)))
                f["effective_lifecycle"] = f.get("issue_lifecycle", "open")
                continue
            f["effective_lifecycle"] = WAIVED
            f["waiver"] = w
            waivers.append({"finding_id": fid, **w})
        elif life in ("verified-fixed", "fixed-claimed", "still-open", "open"):
            f["effective_lifecycle"] = life
            if res.get("note"):
                f["resolution_note"] = res["note"]
        else:
            waiver_problems.append("resolution on %s has unknown lifecycle %r" % (fid, life))
    for f in findings:
        f.setdefault("effective_lifecycle", f.get("issue_lifecycle", "open"))
    return waivers, waiver_problems


# ---- verdict -----------------------------------------------------------------
def compute_verdict(findings, waivers, travel_class):
    """Readiness per conventions §1.4 + §4 — driven only by must-fix lifecycle."""
    must = [f for f in findings if f.get("severity") == "must-fix"]
    blocking = [f for f in must if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    waived = [f for f in must if f.get("effective_lifecycle") == WAIVED]

    board_class = (travel_class or "").lower() in ("board", "regulatory", "board/regulatory", "board-regulatory")
    # §4.3 — a Board/Regulatory waived must-fix needs named-owner signoff
    signoff_gap = []
    if board_class:
        for f in waived:
            so = (f.get("waiver") or {}).get("signoff", "")
            if str(so).lower() not in ("approved", "signed", "signed-off"):
                signoff_gap.append(f["id"])

    if blocking or signoff_gap:
        verdict = "not-ready"
        if blocking:
            reason = "%d must-fix unresolved (lifecycle open/fixed-claimed/still-open) — do not let it travel." % len(blocking)
        else:
            reason = ("%d Board/Regulatory waiver(s) lack named-owner signoff (%s) — verdict held at not-ready per conventions §4.3."
                      % (len(signoff_gap), ", ".join(signoff_gap)))
    elif waived:
        verdict = "ready-with-limitations"
        reason = ("no open must-fix, but %d must-fix resolved by human WAIVER — the named limitations travel with the file." % len(waived))
    else:
        verdict = "ready"
        reason = ("every must-fix is verified-fixed; no waivers in play." if must
                  else "no must-fix findings; structural/should-fix items (if any) are limitations to own, not blockers.")
    return {
        "readiness": verdict,
        "readiness_reason": reason,
        "must_fix_total": len(must),
        "must_fix_blocking": [f["id"] for f in blocking],
        "must_fix_waived": [f["id"] for f in waived],
        "signoff_gap": signoff_gap,
        "travel_class": travel_class,
    }


# ---- verification memo -------------------------------------------------------
def memo_check_for(f):
    chk = f.get("check")
    if chk == "source_packet_link":
        rv = (f.get("detail") or {}).get("review_status")
        return "source_traceability" if rv == "unsupported" else "tie_outs"
    return CHECK_TO_MEMO.get(chk, "data_structure")


def build_memo(findings, recalc):
    unresolved = [f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    by_memo = {}
    for f in unresolved:
        by_memo.setdefault(memo_check_for(f), []).append(f)

    checks = []
    for name in MEMO_CHECKS:
        if name == "recalculation":
            if not recalc or not recalc.get("ran", False):
                checks.append({"check": name, "label": MEMO_LABEL[name], "result": "unverified",
                               "note": "recalc not run — %s" % (recalc or {}).get("note", "no recalc"),
                               "drivers": []})
            elif recalc.get("total_errors", 0) and any(memo_check_for(f) == "tie_outs" and f["check"] == "recalc_error" for f in unresolved):
                checks.append({"check": name, "label": MEMO_LABEL[name], "result": "fail",
                               "note": "%d live recalc error(s)." % recalc.get("total_errors"), "drivers": []})
            else:
                checks.append({"check": name, "label": MEMO_LABEL[name], "result": "pass",
                               "note": "recalc ran; %d error(s)." % recalc.get("total_errors", 0), "drivers": []})
            continue
        group = by_memo.get(name, [])
        musts = [f for f in group if f["severity"] == "must-fix"]
        concerns = [f["id"] for f in group if f["severity"] != "must-fix"]
        if musts:
            result = "fail"
            note = "%d must-fix unresolved." % len(musts)
        elif concerns:
            result = "pass"
            note = "no must-fix; %d should-fix/polish to own." % len(concerns)
        else:
            result = "pass"
            note = "clean."
        checks.append({"check": name, "label": MEMO_LABEL[name], "result": result,
                       "note": note, "drivers": [f["id"] for f in musts], "concerns": concerns})
    return checks


# ---- repair plan -------------------------------------------------------------
def build_repair_plan(findings):
    unresolved = [f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    unresolved.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))
    plan = []
    for f in unresolved:
        plan.append({
            "severity": f["severity"],
            "finding_id": f["id"],
            "check": f["check"],
            "sheet": f.get("sheet"),
            "locator": f.get("locator"),
            "lifecycle": f.get("effective_lifecycle"),
            "action": REPAIR_ACTION.get(f["check"], f.get("message", "")[:160]),
        })
    return plan


def build_must_own(findings, assumptions, verdict):
    """The explicit list a human must still own (conventions §1.4)."""
    own = []
    by_id = {f["id"]: f for f in findings}
    for fid in verdict["must_fix_blocking"]:
        f = by_id[fid]
        own.append({"kind": "open-must-fix", "ref": fid,
                    "what": "%s @ %s!%s — %s" % (f["check"], f.get("sheet"), f.get("locator"),
                                                 (f.get("message") or "")[:120])})
    for fid in verdict["must_fix_waived"]:
        f = by_id[fid]
        own.append({"kind": "waived-limitation", "ref": fid,
                    "what": "WAIVED: %s @ %s!%s — travels with the file (%s)"
                            % (f["check"], f.get("sheet"), f.get("locator"),
                               (f.get("waiver") or {}).get("rationale", ""))})
    for a in assumptions:
        if (a.get("status") or "") in ("unsupported", "conflicting"):
            own.append({"kind": "assumption", "ref": a.get("claim_id") or a.get("finding_id"),
                        "what": "%s = %r [%s] — source %s, owner %s"
                                % (a["name"], a["value"], a["status"], a["source"], a["owner"])})
    return own


# ---- run ---------------------------------------------------------------------
def _load(path, what):
    if not path:
        return None, ("%s not provided" % what)
    if not os.path.isfile(path):
        return None, ("%s not found at %s" % (what, path))
    try:
        with open(path) as fh:
            return json.load(fh), None
    except Exception as e:
        return None, ("%s unreadable: %s" % (what, e))


def run(map_path, fr_path, out_dir, source_packet=None, resolutions_path=None,
        run_id=None, travel_class="Travels", finance_enabled=True):
    map_doc, map_err = _load(map_path, "workbook-map.json")
    fr_doc, fr_err = _load(fr_path, "formula-risk.json")

    # precondition gate — WD-03 needs WD-01 + WD-02 to have run (blocked != failed)
    if map_doc is None or fr_doc is None:
        missing = [m for m in (map_err, fr_err) if m]
        return _emit(out_dir, {
            "run_id": run_id, "skill": "workbook-doctor", "phase": "workbook-verdict",
            "status": "blocked",
            "status_reason": "precondition unmet — run WD-01/WD-02 first: %s" % "; ".join(missing),
            "inputs": {"workbook_map": map_path, "formula_risk": fr_path},
            "readiness": "not-ready",
            "readiness_reason": "cannot assess without the upstream map + formula-risk artifacts.",
            "assumptions": [], "repair_plan": [], "verification_memo": [], "must_own": [],
        })

    packet, _pk_err = _load(source_packet, "source-packet.json") if source_packet else (None, None)
    resolutions, _res_err = _load(resolutions_path, "resolutions.json") if resolutions_path else (None, None)

    # combine findings from both phases, tagging origin phase; keep enums verbatim
    findings = []
    for f in (map_doc.get("findings") or []):
        g = dict(f); g["phase"] = "workbook-map"; findings.append(g)
    for f in (fr_doc.get("findings") or []):
        g = dict(f); g["phase"] = "formula-risk"; findings.append(g)

    waivers, waiver_problems = apply_resolutions(findings, resolutions)
    recalc = fr_doc.get("recalc") or {}

    assumptions = build_assumptions(packet, findings)
    verdict = compute_verdict(findings, waivers, travel_class)
    memo = build_memo(findings, recalc)
    repair = build_repair_plan(findings)
    must_own = build_must_own(findings, assumptions, verdict)

    # WD-04 — finance domain hook (opt-in; core stays domain-agnostic)
    domain = domain_finance.assess(map_doc, fr_doc, packet, enabled=finance_enabled)

    # run-status enum (NOT the readiness verdict): did the assessment run cleanly?
    recalc_unverified = not recalc.get("ran", False)
    warnings = []
    if recalc_unverified:
        warnings.append("recalc unverified (%s)" % recalc.get("note", "no recalc"))
    if waiver_problems:
        warnings.extend(waiver_problems)
    if source_packet is None:
        warnings.append("no source packet — assumptions table built from workbook hardcodes only; provenance not traced")
    status = "ok-with-warnings" if (warnings or findings) else "ok"
    status_reason = ("assessed; %s" % "; ".join(warnings)) if warnings else "assessed; clean."

    sev_counts = {"must-fix": 0, "should-fix": 0, "polish": 0}
    for f in findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1

    doc = {
        "run_id": run_id, "skill": "workbook-doctor", "phase": "workbook-verdict",
        "status": status, "status_reason": status_reason,
        "inputs": {
            "workbook": (map_doc.get("inputs") or {}).get("workbook"),
            "workbook_map": os.path.basename(map_path),
            "formula_risk": os.path.basename(fr_path),
            "source_packet": os.path.basename(source_packet) if source_packet else None,
            "resolutions": os.path.basename(resolutions_path) if resolutions_path else None,
            "travel_class": travel_class,
        },
        "readiness": verdict["readiness"],
        "readiness_reason": verdict["readiness_reason"],
        "verdict_detail": verdict,
        "must_own": must_own,
        "assumptions": assumptions,
        "repair_plan": repair,
        "verification_memo": memo,
        "waivers": waivers,
        "waiver_problems": waiver_problems,
        "domain": domain,
        "summary": {
            "finding_count": len(findings),
            "by_severity": sev_counts,
            "unresolved": len([f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]),
            "assumption_count": len(assumptions),
        },
    }
    return _emit(out_dir, doc)


def _emit(out_dir, doc):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "workbook-verdict.json"), "w") as fh:
        json.dump(doc, fh, indent=2, default=str)
    with open(os.path.join(out_dir, "workbook-verdict.md"), "w") as fh:
        fh.write(_markdown(doc))
    return doc


_VERDICT_BADGE = {"ready": "READY", "ready-with-limitations": "READY-WITH-LIMITATIONS", "not-ready": "NOT-READY"}


def _markdown(doc):
    L = ["# Workbook Verdict — assessment & readiness", ""]
    L.append("- **Run:** `%s`" % doc.get("run_id"))
    if doc["inputs"].get("workbook"):
        L.append("- **Workbook:** `%s`" % doc["inputs"]["workbook"])
    L.append("- **Travel class:** %s" % doc["inputs"].get("travel_class"))
    L.append("- **Run status:** `%s` — %s" % (doc["status"], doc["status_reason"]))
    L.append("")
    L.append("## ⟶ Readiness: **%s**" % _VERDICT_BADGE.get(doc["readiness"], doc["readiness"]))
    L.append("")
    L.append("> %s" % doc["readiness_reason"])
    L.append("")
    if doc.get("status") == "blocked":
        return "\n".join(L) + "\n"

    # must-own
    L.append("## What a human must still own (%d)" % len(doc["must_own"]))
    L.append("")
    if not doc["must_own"]:
        L.append("_Nothing outstanding — every must-fix is verified-fixed and no assumptions are unsupported/conflicting._")
    else:
        for o in doc["must_own"]:
            L.append("- **[%s]** %s" % (o["kind"], o["what"]))
    L.append("")

    # assumptions table
    L.append("## Assumptions (%d)" % len(doc["assumptions"]))
    L.append("")
    if not doc["assumptions"]:
        L.append("_No load-bearing assumptions surfaced (no source packet, no in-cell constants)._")
    else:
        L.append("| Name | Value | Unit | Source | Owner | Date | Status | Origin |")
        L.append("|---|---|---|---|---|---|---|---|")
        for a in doc["assumptions"]:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                str(a["name"])[:70], a["value"], a["unit"], a["source"], a["owner"],
                a["date"], a["status"], a["origin"]))
    L.append("")

    # verification memo
    L.append("## Verification memo")
    L.append("")
    L.append("| Check | Result | Detail |")
    L.append("|---|---|---|")
    for c in doc["verification_memo"]:
        badge = {"pass": "PASS", "fail": "FAIL", "unverified": "UNVERIFIED"}.get(c["result"], c["result"].upper())
        L.append("| %s | %s | %s |" % (c["label"], badge, c["note"]))
    L.append("")

    # repair plan
    L.append("## Repair plan (%d unresolved)" % len(doc["repair_plan"]))
    L.append("")
    if not doc["repair_plan"]:
        L.append("_Nothing to repair — no unresolved findings._")
    else:
        L.append("| Severity | ID | Check | Where | Action |")
        L.append("|---|---|---|---|---|")
        for p in doc["repair_plan"]:
            L.append("| %s | %s | %s | %s!%s | %s |" % (
                p["severity"], p["finding_id"], p["check"], p["sheet"], p["locator"], p["action"]))
    L.append("")

    # waivers
    if doc.get("waivers"):
        L.append("## Waivers in play (cap verdict at ready-with-limitations)")
        L.append("")
        for w in doc["waivers"]:
            L.append("- **%s** — owner %s, signoff %s, due %s: %s" % (
                w.get("finding_id"), w.get("owner"), w.get("signoff"), w.get("due_date"), w.get("rationale")))
        L.append("")

    # domain hook
    dom = doc.get("domain") or {}
    L.append("## Domain hook (WD-04)")
    L.append("")
    if not dom.get("enabled", True):
        L.append("_Finance hook off (`--no-finance`, or no `finance:*` skills installed) — generic, domain-agnostic checks only._")
    elif dom.get("is_financial"):
        L.append("Detected a **financial model** (score %s). Signals: %s." % (
            dom.get("score"), ", ".join(dom.get("signals", []))))
        L.append("")
        L.append("Recommended `finance:*` validators on top of the generic checks:")
        L.append("")
        for h in dom.get("finance_hooks", []):
            L.append("- **finance:%s** — %s _(inputs: %s)_" % (h["validator"], h["why"], h.get("inputs", "—")))
    else:
        L.append("_Not detected as a financial model — generic checks only (the core is domain-agnostic)._")
    L.append("")
    L.append("> Readiness is computed strictly from the must-fix lifecycle (conventions.md §1.4/§4). "
             "It is never `ready` while a must-fix is open; a waiver caps it at `ready-with-limitations`.")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True, help="workbook-map.json (WD-01)")
    ap.add_argument("--formula-risk", required=True, help="formula-risk.json (WD-02)")
    ap.add_argument("--source-packet", default=None)
    ap.add_argument("--resolutions", default=None, help="human-gate decisions (verified-fixed / waivers)")
    ap.add_argument("--travel-class", default="Travels")
    ap.add_argument("--no-finance", action="store_true", help="disable the WD-04 finance domain hook")
    ap.add_argument("--out", default=None)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_verdict")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.map)))
    doc = run(args.map, args.formula_risk, out_dir, source_packet=args.source_packet,
              resolutions_path=args.resolutions, run_id=run_id,
              travel_class=args.travel_class, finance_enabled=not args.no_finance)
    print(json.dumps({"status": doc["status"], "readiness": doc["readiness"],
                      "unresolved": doc.get("summary", {}).get("unresolved"),
                      "out": out_dir}, indent=2))
    # exit 0 on a clean run (ok/ok-with-warnings); blocked/failed -> non-zero
    sys.exit(0 if doc["status"] in ("ok", "ok-with-warnings") else 3)


if __name__ == "__main__":
    main()
