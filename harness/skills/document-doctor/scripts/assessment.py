#!/usr/bin/env python3
"""
Office Trust Layer — Document Doctor · DD-04: assessment + readiness verdict (surface=doc).

The prose MIRROR of skills/workbook-doctor/scripts/assessment.py (WD-03). It
consumes the document trust-path artifacts —

  * document-map.json   (DD-01 structure + DD-07 PDF extraction quality)
  * prose-landmines.json (DD-02 prose landmine findings)
  * claim-audit.json     (DD-03 per-claim provenance records)

and, optionally, the source-packet.json the claim audit resolved against — and
turns their raw findings into a DECISION, identical in shape to the workbook
verdict but keyed to the `doc` surface:

  1. an ASSUMPTIONS TABLE — every load-bearing assumption the document rests on
     (name, value, unit, source, owner, date, status), assembled from the
     source-packet judgement claims AND from the document's OWN claims that
     assert an assumption/estimate as fact without a source;
  2. a REPAIR PLAN — every unresolved finding, grouped by the conventions.md
     `severity` enum (must-fix -> should-fix -> polish), each with a concrete
     recommended action and its `issue_lifecycle` state;
  3. a VERIFICATION MEMO — pass / fail per check (claim provenance · conflicts ·
     dating · extraction quality · cross-references · attribution);
  4. a READINESS VERDICT — `not-ready` / `ready-with-limitations` / `ready`
     computed strictly from the must-fix lifecycle per conventions.md §1.4/§4,
     paired with the explicit `must_own` list. NEVER `ready` while any must-fix
     is open / fixed-claimed / still-open; a waiver caps it at
     `ready-with-limitations` (Board/Regulatory additionally needs named-owner
     signoff).

PROPAGATES `surface=doc` + the document `format`, and HONOURS a DD-07 PDF block:
a scanned/below-threshold PDF with no OCR carries `status=blocked` /
`readiness=not-ready` with its DOC-PBW-01 extraction-loss must-fix, exactly as
the map reported it — never softened into a clean verdict.

Enums (severity / review_status / issue_lifecycle / readiness / status) come
from references/conventions.md and are used VERBATIM — never redefined here.

Outputs, per the §3 I/O contract:
  <out>/document-verdict.json   (machine)
  <out>/document-verdict.md     (human memo)

Usage:
  python assessment.py --map document-map.json [--landmines prose-landmines.json] \
      [--claim-audit claim-audit.json] [--source-packet packet.json] \
      [--resolutions resolutions.json] [--travel-class Travels] [--out DIR] [--run-id ID]
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

# ---- conventions enums (verbatim from references/conventions.md) -------------
SEVERITY_ORDER = {"must-fix": 0, "should-fix": 1, "polish": 2}
BLOCKING_LIFECYCLE = {"open", "fixed-claimed", "still-open"}   # §1.4 / §1.5
WAIVED = "waived-by-human"
WAIVER_REQUIRED_FIELDS = ("owner", "decision", "due_date", "signoff", "rationale")  # §4.2

SURFACE = "doc"

# Which verification-memo check each finding `type` belongs to — a complete
# partition over the doc-surface finding types (DD-02 + DD-03 + DD-07).
CHECK_TO_MEMO = {
    "unsupported-claim": "claim_provenance",
    "numeric-token-candidate": "claim_provenance",
    "conflicting-claim": "conflicts",
    "verification-contradiction-unresolved": "conflicts",
    "undated-number": "dating",
    "stale-date": "dating",
    "extraction-loss": "extraction_quality",
    "table-as-image": "extraction_quality",
    "per-format-limit-hidden": "extraction_quality",
    "broken-cross-ref": "cross_references",
    "unattributed-quote": "attribution",
    "quote-or-citation-misattribution": "attribution",
}
MEMO_CHECKS = ["claim_provenance", "conflicts", "dating",
               "extraction_quality", "cross_references", "attribution"]
MEMO_LABEL = {
    "claim_provenance": "Claim provenance (every material claim traces to a current source)",
    "conflicts": "Conflicts (disagreements preserved + resolved, never blended)",
    "dating": "Dating (material figures carry an as-of date; none stale)",
    "extraction_quality": "Extraction quality (no content lost/garbled; tables not pictures)",
    "cross_references": "Cross-references (every 'see Table/Appendix X' resolves)",
    "attribution": "Attribution (quotations / cited statistics name their source)",
}
REPAIR_ACTION = {
    "unsupported-claim": "Cite a current source for the figure (or correct it to the "
                         "authoritative value, or drop the claim) — it is stated as fact with no source.",
    "conflicting-claim": "Resolve the disagreement — state which source is authoritative and "
                         "reconcile, or escalate to a human decision; never average the values.",
    "verification-contradiction-unresolved": "Preserve the contradiction as a conflict for a human; "
                                             "never blend the claim with the contradicting source.",
    "undated-number": "Add the as-of date / period the figure refers to.",
    "stale-date": "Refresh the figure to a current period, or restate the as-of date if it still holds.",
    "table-as-image": "Re-insert the table/chart as extractable text/data so its numbers can be traced.",
    "extraction-loss": "Provide an OCR'd / text-layer copy (or confirm the content manually) — "
                       "extraction is below threshold; the document cannot be certified as-is.",
    "broken-cross-ref": "Fix the cross-reference to point at an existing table/figure/section, "
                        "or add the missing referent.",
    "unattributed-quote": "Attribute the quotation / cited statistic to its source.",
    "quote-or-citation-misattribution": "Re-attribute the quotation/citation to a source that "
                                        "actually supports it, or remove it.",
    "numeric-token-candidate": "Source / verify the numeric claim against a current source.",
}

# severity for an UNSUPPORTED claim, by materiality + class (DD-03 → finding)
_MATERIAL_MUSTFIX = {"headline", "regulatory", "market", "numeric",
                     "assumption-as-fact", "date"}


# ---- value / unit helpers (mirrors the workbook assessment) ------------------
def infer_unit(value):
    if isinstance(value, bool):
        return "flag"
    if isinstance(value, (int, float)):
        v = float(value)
        return "ratio/percent" if 0 < abs(v) < 1 else "number"
    if not isinstance(value, str):
        return "—"
    s = value.strip()
    if "%" in s:
        return "percent"
    if re.search(r"€|EUR", s, re.I) or s.startswith("€"):
        return "EUR"
    if s.startswith("$") or re.search(r"\bUSD\b", s, re.I):
        return "USD"
    if s.startswith("£"):
        return "GBP"
    if re.search(r"\bbps\b", s, re.I):
        return "bps"
    if re.search(r"\d", s):
        return "number/text"
    return "—"


def _load(path, what):
    if not path:
        return None, ("%s not provided" % what)
    if not os.path.isfile(path):
        return None, ("%s not found at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as e:
        return None, ("%s unreadable: %s" % (what, e))


# ---- finding assembly --------------------------------------------------------
def _claim_number(claim_text, nums_hint=None):
    m = re.search(r"[€$£]?\s?\d[\d,\.]*\s?(?:%|k|m|mn|bn|b|million|billion|thousand|x)?",
                  claim_text or "", re.I)
    return m.group(0).strip() if m else None


def findings_from_claims(claim_audit):
    """Turn DD-03 unsupported/conflicting claims into severity-keyed findings."""
    out = []
    i = 0
    for c in (claim_audit.get("claims") if claim_audit else []) or []:
        rs = c.get("review_status")
        if rs == "unsupported":
            material = c.get("materiality") in _MATERIAL_MUSTFIX
            checkable = c.get("class") in ("fact", "estimate", "assumption")
            sev = "must-fix" if (material and checkable) else "should-fix"
            i += 1
            out.append({
                "id": "DD03-U%d" % i, "surface": SURFACE, "format": c.get("format"),
                "doc_pbw": "unsupported-claim", "type": "unsupported-claim",
                "severity": sev, "review_status": "unsupported", "issue_lifecycle": "open",
                "locator": c.get("locator"),
                "message": "claim stated as fact with no supporting source: “%s”"
                           % (c.get("claim", "")[:120]),
                "detail": {"class": c.get("class"), "materiality": c.get("materiality"),
                           "verifiability": c.get("verifiability"),
                           "value": _claim_number(c.get("claim"))},
            })
        elif rs == "conflicting":
            i += 1
            out.append({
                "id": "DD03-C%d" % i, "surface": SURFACE, "format": c.get("format"),
                "doc_pbw": "verification-contradiction-unresolved", "type": "conflicting-claim",
                "severity": "must-fix", "review_status": "conflicting", "issue_lifecycle": "open",
                "locator": c.get("locator"),
                "message": "claim conflicts with the source packet: “%s” (%s)"
                           % (c.get("claim", "")[:100], json.dumps(c.get("conflict"))[:80]),
                "detail": {"class": c.get("class"), "materiality": c.get("materiality"),
                           "conflict": c.get("conflict")},
            })
    return out


def normalize_landmine(f, idx):
    g = dict(f)
    g.setdefault("id", "DD02-%d" % idx)
    g.setdefault("issue_lifecycle", "open")
    g.setdefault("message", f.get("evidence", ""))
    g["phase"] = "prose-landmines"
    return g


def normalize_map_finding(f, idx):
    g = dict(f)
    g.setdefault("id", f.get("id") or "DD07-%d" % idx)
    g.setdefault("issue_lifecycle", "open")
    g.setdefault("type", f.get("type") or f.get("id"))
    g.setdefault("message", f.get("evidence", ""))
    g["phase"] = "document-map"
    return g


# ---- resolutions / human gate (verbatim mirror of the workbook gate) ---------
def apply_resolutions(findings, resolutions):
    by_id = {f["id"]: f for f in findings}
    waiver_problems, waivers = [], []
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
                waiver_problems.append("waiver on %s INVALID — missing %s; finding stays blocking"
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


# ---- verdict (verbatim mirror of conventions §1.4 + §4) ----------------------
def compute_verdict(findings, travel_class):
    must = [f for f in findings if f.get("severity") == "must-fix"]
    blocking = [f for f in must if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    waived = [f for f in must if f.get("effective_lifecycle") == WAIVED]

    board_class = (travel_class or "").lower() in (
        "board", "regulatory", "board/regulatory", "board-regulatory")
    signoff_gap = []
    if board_class:
        for f in waived:
            so = (f.get("waiver") or {}).get("signoff", "")
            if str(so).lower() not in ("approved", "signed", "signed-off"):
                signoff_gap.append(f["id"])

    if blocking or signoff_gap:
        verdict = "not-ready"
        if blocking:
            reason = ("%d must-fix unresolved (lifecycle open/fixed-claimed/still-open) "
                      "— do not let it travel." % len(blocking))
        else:
            reason = ("%d Board/Regulatory waiver(s) lack named-owner signoff (%s) — held "
                      "at not-ready per conventions §4.3." % (len(signoff_gap), ", ".join(signoff_gap)))
    elif waived:
        verdict = "ready-with-limitations"
        reason = ("no open must-fix, but %d must-fix resolved by human WAIVER — the named "
                  "limitations travel with the file." % len(waived))
    else:
        verdict = "ready"
        reason = ("every must-fix is verified-fixed; no waivers in play." if must
                  else "no must-fix findings; should-fix/polish items (if any) are limitations to own.")
    return {
        "readiness": verdict, "readiness_reason": reason,
        "must_fix_total": len(must),
        "must_fix_blocking": [f["id"] for f in blocking],
        "must_fix_waived": [f["id"] for f in waived],
        "signoff_gap": signoff_gap, "travel_class": travel_class,
    }


# ---- assumptions table -------------------------------------------------------
ASSUMPTION_CLASSES = {"assumption", "estimate", "open-question"}
ASSUMPTION_STATUSES = {"unsupported", "conflicting"}


def _src_index(packet):
    return {s.get("id"): s for s in (packet.get("sources") or [])} if packet else {}


def build_assumptions(packet, claim_audit):
    rows = []
    src_by_id = _src_index(packet)
    # 1) source-packet claims a human must own
    for cl in (packet.get("claims") if packet else []) or []:
        cls = (cl.get("class") or "").lower()
        rv = (cl.get("review_status") or "").lower()
        if cls not in ASSUMPTION_CLASSES and rv not in ASSUMPTION_STATUSES:
            continue
        prov = cl.get("provenance") or {}
        src_ids = [s.get("source_id") for s in (prov.get("supporting") or []) if s.get("source_id")]
        owner, date = "unattributed", cl.get("period") or "—"
        if src_ids and src_ids[0] in src_by_id:
            owner = src_by_id[src_ids[0]].get("owner", owner)
            date = src_by_id[src_ids[0]].get("date", date)
        rows.append({"name": prov.get("claim") or cl.get("metric") or cl.get("id"),
                     "value": cl.get("value"), "unit": infer_unit(cl.get("value")),
                     "source": ", ".join(src_ids) if src_ids else "none",
                     "owner": owner, "date": date, "status": rv or "needs-review",
                     "origin": "source-packet", "class": cls or None})
    # 2) the document's OWN claims that assert an assumption/estimate as fact
    for c in (claim_audit.get("claims") if claim_audit else []) or []:
        cls = (c.get("class") or "").lower()
        is_aaf = c.get("materiality") == "assumption-as-fact"
        if cls not in ("assumption", "estimate") and not is_aaf:
            continue
        if c.get("review_status") not in ("unsupported", "conflicting", "needs-review"):
            continue
        src_ids = [s.get("source_id") for s in (c.get("supporting") or []) if s.get("source_id")]
        rows.append({"name": c.get("claim", "")[:80], "value": _claim_number(c.get("claim")),
                     "unit": infer_unit(_claim_number(c.get("claim"))),
                     "source": ", ".join(src_ids) if src_ids else "none",
                     "owner": "document author", "date": "—",
                     "status": c.get("review_status"), "origin": "document-claim",
                     "class": cls})
    return rows


# ---- verification memo -------------------------------------------------------
def build_memo(findings, pdf_quality):
    unresolved = [f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    by_memo = {}
    for f in unresolved:
        by_memo.setdefault(CHECK_TO_MEMO.get(f.get("type"), "extraction_quality"), []).append(f)
    checks = []
    for name in MEMO_CHECKS:
        group = by_memo.get(name, [])
        musts = [f for f in group if f["severity"] == "must-fix"]
        concerns = [f["id"] for f in group if f["severity"] != "must-fix"]
        if name == "extraction_quality" and pdf_quality:
            v = pdf_quality.get("verdict")
            if v and v.startswith("blocked"):
                checks.append({"check": name, "label": MEMO_LABEL[name], "result": "fail",
                               "note": "PDF extraction blocked (%s)." % v,
                               "drivers": [f["id"] for f in musts]})
                continue
        if musts:
            result, note = "fail", "%d must-fix unresolved." % len(musts)
        elif concerns:
            result, note = "pass", "no must-fix; %d should-fix/polish to own." % len(concerns)
        else:
            result, note = "pass", "clean."
        checks.append({"check": name, "label": MEMO_LABEL[name], "result": result,
                       "note": note, "drivers": [f["id"] for f in musts], "concerns": concerns})
    return checks


# ---- repair plan + must-own --------------------------------------------------
def build_repair_plan(findings):
    unresolved = [f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]
    unresolved.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))
    plan = []
    for f in unresolved:
        plan.append({"severity": f["severity"], "finding_id": f["id"], "type": f.get("type"),
                     "format": f.get("format"), "locator": f.get("locator"),
                     "lifecycle": f.get("effective_lifecycle"),
                     "action": REPAIR_ACTION.get(f.get("type"), (f.get("message") or "")[:160])})
    return plan


def build_must_own(findings, assumptions, verdict, held):
    own = []
    by_id = {f["id"]: f for f in findings}
    for fid in verdict["must_fix_blocking"]:
        f = by_id[fid]
        own.append({"kind": "open-must-fix", "ref": fid,
                    "what": "%s @ %s — %s" % (f.get("type"), f.get("locator"),
                                              (f.get("message") or "")[:120])})
    for fid in verdict["must_fix_waived"]:
        f = by_id[fid]
        own.append({"kind": "waived-limitation", "ref": fid,
                    "what": "WAIVED: %s @ %s — travels with the file (%s)"
                            % (f.get("type"), f.get("locator"),
                               (f.get("waiver") or {}).get("rationale", ""))})
    for a in assumptions:
        if (a.get("status") or "") in ("unsupported", "conflicting"):
            own.append({"kind": "assumption", "ref": a.get("class"),
                        "what": "%s = %r [%s] — source %s, owner %s"
                                % (str(a["name"])[:70], a["value"], a["status"], a["source"], a["owner"])})
    for h in (held or []):
        own.append({"kind": "held-link", "ref": h.get("locator"),
                    "what": "sub-threshold provenance HELD for approval: “%s…”" % h.get("claim", "")[:70]})
    return own


# ---- run ---------------------------------------------------------------------
def run(map_path, out_dir, landmines_path=None, claim_audit_path=None, source_packet=None,
        resolutions_path=None, run_id=None, travel_class="Travels"):
    map_doc, map_err = _load(map_path, "document-map.json")
    if map_doc is None:
        return _emit(out_dir, {
            "run_id": run_id, "skill": "document-doctor", "phase": "document-verdict",
            "surface": SURFACE, "status": "blocked",
            "status_reason": "precondition unmet — run DD-01 first: %s" % map_err,
            "inputs": {"document_map": map_path},
            "readiness": "not-ready",
            "readiness_reason": "cannot assess without the upstream document map.",
            "assumptions": [], "repair_plan": [], "verification_memo": [], "must_own": []})

    fmt = map_doc.get("format")
    landmines, _ = _load(landmines_path, "prose-landmines.json") if landmines_path else (None, None)
    claim_audit, _ = _load(claim_audit_path, "claim-audit.json") if claim_audit_path else (None, None)
    packet, _ = _load(source_packet, "source-packet.json") if source_packet else (None, None)
    resolutions, _ = _load(resolutions_path, "resolutions.json") if resolutions_path else (None, None)

    pdf_quality = map_doc.get("pdf_quality") if fmt == "pdf" else None
    map_blocked = (map_doc.get("status") == "blocked")

    # assemble findings from all three doc-path phases
    findings = []
    for idx, f in enumerate((map_doc.get("findings") or []), 1):
        findings.append(normalize_map_finding(f, idx))
    for idx, f in enumerate((landmines.get("findings") if landmines else []) or [], 1):
        findings.append(normalize_landmine(f, idx))
    findings.extend(findings_from_claims(claim_audit))

    waivers, waiver_problems = apply_resolutions(findings, resolutions)
    verdict = compute_verdict(findings, travel_class)

    # HONOUR the DD-07 block: a blocked map forces blocked/not-ready regardless
    # (the DOC-PBW-01 must-fix already does this, but we make it explicit).
    if map_blocked and verdict["readiness"] == "ready":
        verdict["readiness"] = "not-ready"
        verdict["readiness_reason"] = ("document-map is blocked (PDF extraction below "
                                       "threshold, no OCR) — not-ready until resolved.")

    held = (claim_audit.get("held_for_approval") if claim_audit else []) or []
    assumptions = build_assumptions(packet, claim_audit)
    memo = build_memo(findings, pdf_quality)
    repair = build_repair_plan(findings)
    must_own = build_must_own(findings, assumptions, verdict, held)

    # run-status (NOT the readiness verdict)
    warnings = []
    if map_blocked:
        warnings.append("document-map blocked (extraction below threshold)")
    if claim_audit is None:
        warnings.append("no claim audit — provenance findings not included")
    if landmines is None:
        warnings.append("no prose landmines — landmine findings not included")
    if source_packet is None:
        warnings.append("no source packet — assumptions from document claims only")
    if waiver_problems:
        warnings.extend(waiver_problems)
    if map_blocked:
        status = "blocked"
    else:
        status = "ok-with-warnings" if (warnings or findings) else "ok"
    status_reason = ("assessed; %s" % "; ".join(warnings)) if warnings else "assessed; clean."

    sev_counts = {"must-fix": 0, "should-fix": 0, "polish": 0}
    for f in findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1

    doc = {
        "run_id": run_id, "skill": "document-doctor", "phase": "document-verdict",
        "surface": SURFACE, "format": fmt,
        "status": status, "status_reason": status_reason,
        "inputs": {
            "document": (map_doc.get("input") or {}).get("path") if isinstance(map_doc.get("input"), dict) else map_doc.get("input"),
            "document_map": os.path.basename(map_path),
            "prose_landmines": os.path.basename(landmines_path) if landmines_path else None,
            "claim_audit": os.path.basename(claim_audit_path) if claim_audit_path else None,
            "source_packet": os.path.basename(source_packet) if source_packet else None,
            "resolutions": os.path.basename(resolutions_path) if resolutions_path else None,
            "travel_class": travel_class,
        },
        "readiness": verdict["readiness"], "readiness_reason": verdict["readiness_reason"],
        "verdict_detail": verdict,
        "extraction": {  # propagate the per-format extraction caveats (finding #6)
            "format": fmt,
            "format_reports": map_doc.get("format_reports"),
            "pdf_quality": pdf_quality,
            "map_status": map_doc.get("status"),
            "map_blocked": map_blocked,
        },
        "must_own": must_own, "assumptions": assumptions,
        "repair_plan": repair, "verification_memo": memo,
        "waivers": waivers, "waiver_problems": waiver_problems,
        "summary": {
            "finding_count": len(findings), "by_severity": sev_counts,
            "unresolved": len([f for f in findings if f.get("effective_lifecycle") in BLOCKING_LIFECYCLE]),
            "assumption_count": len(assumptions), "held_for_approval": len(held),
        },
    }
    return _emit(out_dir, doc)


def _emit(out_dir, doc):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "document-verdict.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, default=str, ensure_ascii=False)
    with open(os.path.join(out_dir, "document-verdict.md"), "w", encoding="utf-8") as fh:
        fh.write(_markdown(doc))
    return doc


_VERDICT_BADGE = {"ready": "READY", "ready-with-limitations": "READY-WITH-LIMITATIONS",
                  "not-ready": "NOT-READY"}


def _markdown(doc):
    L = ["# Document Verdict — assessment & readiness (surface: doc)", ""]
    L.append("- **Run:** `%s`" % doc.get("run_id"))
    L.append("- **Format:** `%s`" % doc.get("format"))
    if doc["inputs"].get("document"):
        L.append("- **Document:** `%s`" % doc["inputs"]["document"])
    L.append("- **Travel class:** %s" % doc["inputs"].get("travel_class"))
    L.append("- **Run status:** `%s` — %s" % (doc["status"], doc["status_reason"]))
    L.append("")
    L.append("## ⟶ Readiness: **%s**" % _VERDICT_BADGE.get(doc["readiness"], doc["readiness"]))
    L.append("")
    L.append("> %s" % doc["readiness_reason"])
    L.append("")
    ex = doc.get("extraction") or {}
    if ex.get("pdf_quality"):
        q = ex["pdf_quality"]
        L.append("- **PDF extraction:** verdict `%s`, text-coverage %s, image-only %s, OCR %s"
                 % (q.get("verdict"), q.get("text_coverage"), q.get("image_only_pages"),
                    q.get("ocr_available")))
        L.append("")
    if doc.get("status") == "blocked" and not doc["must_own"]:
        return "\n".join(L) + "\n"

    L.append("## What a human must still own (%d)" % len(doc["must_own"]))
    L.append("")
    if not doc["must_own"]:
        L.append("_Nothing outstanding — every must-fix is verified-fixed and no assumptions "
                 "are unsupported/conflicting._")
    else:
        for o in doc["must_own"]:
            L.append("- **[%s]** %s" % (o["kind"], o["what"]))
    L.append("")

    L.append("## Assumptions (%d)" % len(doc["assumptions"]))
    L.append("")
    if not doc["assumptions"]:
        L.append("_No load-bearing assumptions surfaced._")
    else:
        L.append("| Name | Value | Unit | Source | Owner | Date | Status | Origin |")
        L.append("|---|---|---|---|---|---|---|---|")
        for a in doc["assumptions"]:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                str(a["name"])[:60], a["value"], a["unit"], a["source"], a["owner"],
                a["date"], a["status"], a["origin"]))
    L.append("")

    L.append("## Verification memo")
    L.append("")
    L.append("| Check | Result | Detail |")
    L.append("|---|---|---|")
    for c in doc["verification_memo"]:
        badge = {"pass": "PASS", "fail": "FAIL", "unverified": "UNVERIFIED"}.get(c["result"], c["result"].upper())
        L.append("| %s | %s | %s |" % (c["label"], badge, c["note"]))
    L.append("")

    L.append("## Repair plan (%d unresolved)" % len(doc["repair_plan"]))
    L.append("")
    if not doc["repair_plan"]:
        L.append("_Nothing to repair — no unresolved findings._")
    else:
        L.append("| Severity | ID | Type | Where | Action |")
        L.append("|---|---|---|---|---|")
        for p in doc["repair_plan"]:
            L.append("| %s | %s | %s | %s | %s |" % (
                p["severity"], p["finding_id"], p["type"], (p["locator"] or "")[:36], p["action"]))
    L.append("")

    if doc.get("waivers"):
        L.append("## Waivers in play (cap verdict at ready-with-limitations)")
        L.append("")
        for w in doc["waivers"]:
            L.append("- **%s** — owner %s, signoff %s, due %s: %s" % (
                w.get("finding_id"), w.get("owner"), w.get("signoff"), w.get("due_date"),
                w.get("rationale")))
        L.append("")
    L.append("> Readiness is computed strictly from the must-fix lifecycle (conventions.md "
             "§1.4/§4). Never `ready` while a must-fix is open; a waiver caps it at "
             "`ready-with-limitations`. Limits are reported per `format` (surface=doc).")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True, help="document-map.json (DD-01/DD-07)")
    ap.add_argument("--landmines", default=None, help="prose-landmines.json (DD-02)")
    ap.add_argument("--claim-audit", default=None, help="claim-audit.json (DD-03)")
    ap.add_argument("--source-packet", default=None)
    ap.add_argument("--resolutions", default=None, help="human-gate decisions (verified-fixed / waivers)")
    ap.add_argument("--travel-class", default="Travels")
    ap.add_argument("--out", default=None)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_doc-verdict")
    out_dir = args.out or os.path.dirname(os.path.abspath(args.map))
    doc = run(args.map, out_dir, landmines_path=args.landmines, claim_audit_path=args.claim_audit,
              source_packet=args.source_packet, resolutions_path=args.resolutions,
              run_id=run_id, travel_class=args.travel_class)
    print(json.dumps({"status": doc["status"], "readiness": doc["readiness"],
                      "unresolved": doc.get("summary", {}).get("unresolved"), "out": out_dir},
                     indent=2))
    sys.exit(0 if doc["status"] in ("ok", "ok-with-warnings") else 3)


if __name__ == "__main__":
    main()
