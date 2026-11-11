#!/usr/bin/env python3
"""
Office Trust Layer — Evidence Map · EM-03: evidence-gap review.

The skeptic's pass over a populated evidence map (EM-02's `evidence-map.json`). It
is where a forwarded deck quietly becomes wrong, so it scans for the gaps a polished
slide hides and ranks them by risk:

  * UNSUPPORTED      — a claim with no above-threshold source (zero supporting links).
  * UNDATED NUMBER   — a claim that asserts a figure but carries no date range.
  * UNTRACEABLE CHART— a slide with a chart whose data has no workbook/source locator.
  * ASSUMPTION-AS-FACT— a load-bearing assumption / estimate presented as `verified`,
                        or an estimate-class source treated as an actual.
  * STALE / SUPERSEDED— a supporting source whose `source_status` is `superseded`
                        (or an `estimate` standing in for an actual).
  * CONFLICT         — a claim whose sources disagree (`review_status: conflicting`).
  * CANDIDATE-ONLY   — a claim supported only by sub-threshold candidate links held
                        for human approval — provenance not yet established.

Each gap is emitted as a Trust-Review edit-list finding — the exact
`{id, location, type, severity, evidence, suggested_repair, human_review, status}`
shape `trust-review`'s `review_contract.validate_edit_list` accepts — so the gap
review feeds straight into the M2 hostile-review loop. `severity` and `status` use
the conventions enums verbatim; `status` starts at `open` (the issue_lifecycle entry
state). Gaps are RANKED by a risk score (severity weight x recency/scope multiplier).

I/O (`references/conventions.md §3`):
  in : evidence-map.json
  out: <out>/evidence-gaps.json   (ranked gaps + the edit list for Trust Review)
       <out>/evidence-gaps.md      (human memo)
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence_schema as ES  # noqa: E402

# severity -> base risk weight (must-fix dominates)
_SEV_WEIGHT = {"must-fix": 100, "should-fix": 30, "polish": 5}
# the conventions issue_lifecycle entry state
_OPEN = "open"

_NUMBER_RE = re.compile(r"[€$£]?\s?-?\d[\d,]*\.?\d*\s?(%|bps|pp|m|bn|k)?", re.I)


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _asserts_number(claim):
    return bool(_NUMBER_RE.search(claim or "")) and bool(re.search(r"\d", claim or ""))


# ---------------------------------------------------------------------------
def review_gaps(emap, workbook_source=None):
    """Scan the map and return the ranked gap list + edit list."""
    workbook_source = workbook_source or emap.get("workbook_source") or {}
    wb_status = workbook_source.get("status")
    records = emap.get("records", [])
    gaps = []
    n = 0

    def add(rec, gtype, severity, evidence, repair, human, scope=1.0):
        nonlocal n
        n += 1
        risk = round(_SEV_WEIGHT.get(severity, 1) * scope, 1)
        # SURFACE-AWARE LOCATION (additive doc branch): a doc-surface record carries
        # a `doc_locator` (the §1.6 page/heading/para grammar) + `format`; a deck
        # record carries `slide`. Render whichever is present — deck behaviour is
        # unchanged when no doc_locator is set.
        if rec.get("doc_locator"):
            location = "%s :: %s" % (rec.get("doc_locator"), (rec.get("claim") or "")[:70])
        else:
            location = "slide %s :: %s" % (rec.get("slide"), (rec.get("claim") or "")[:70])
        gaps.append({
            "id": "GAP-%02d" % n,
            "location": location,
            "type": gtype,
            "severity": severity,
            "evidence": evidence,
            "suggested_repair": repair,
            "human_review": human,
            "status": _OPEN,
            "risk_score": risk,
            "slide": rec.get("slide"),
            "doc_locator": rec.get("doc_locator"),
            "format": rec.get("format"),
            "claim": rec.get("claim"),
        })

    for r in records:
        rs = r.get("review_status")
        claim = r.get("claim") or ""
        supporting = r.get("supporting") or []
        candidates = r.get("candidates") or []

        # 1. UNSUPPORTED — the cardinal defect
        if rs == "unsupported":
            held = (" %d candidate link(s) are held for approval but none clears the %.2f "
                    "threshold." % (len(candidates), emap.get("link_threshold", ES.LINK_THRESHOLD))
                    ) if candidates else " No candidate links at all."
            add(r, "unsupported-claim", "must-fix",
                "Claim asserts content with ZERO above-threshold sources." + held,
                "Source it (confirm a candidate or add a packet source) or drop it before the deck travels.",
                human=True, scope=1.2 if _asserts_number(claim) else 1.0)

        # 2. CONFLICT
        if rs == "conflicting":
            cf = r.get("conflict") or {}
            add(r, "conflicting-sources", "should-fix",
                "Sources disagree: %s" % (cf.get("note") or "conflict recorded on the claim"),
                "Show the authoritative value with a visible note of the disagreement; resolve at the human gate.",
                human=True, scope=1.3)

        # 3. UNDATED NUMBER
        if _asserts_number(claim) and not r.get("date_range") and rs != "unsupported":
            add(r, "undated-number", "should-fix",
                "Claim states a figure but no date range / as-of is attached to its source(s).",
                "Stamp the figure's period (the source's date) on the slide and in the speaker note.",
                human=False, scope=1.0)

        # 4. UNTRACEABLE CHART
        if r.get("chart") and not r.get("cells") and not any(
                "!" in (s.get("locator") or "") for s in supporting):
            add(r, "untraceable-chart", "must-fix",
                "Slide carries a chart (%s) but no source/workbook locator backs its data." % r.get("chart"),
                "Tie the chart series to a workbook range or source; a chart with no traceable data must not render.",
                human=True, scope=1.1)

        # 5. ASSUMPTION-AS-FACT
        assum = r.get("assumptions") or []
        if assum and rs == "verified":
            add(r, "assumption-as-fact", "should-fix",
                "Claim rests on assumption(s) [%s] yet is presented as verified." % "; ".join(assum)[:120],
                "Label the assumption on the slide; do not present a judgement as an observed actual.",
                human=True, scope=1.1)

        # 6. STALE / SUPERSEDED source
        for s in supporting:
            sid = s.get("source_id")
            st = _source_status(emap, sid) or (wb_status if sid == workbook_source.get("id") else None)
            if st == "superseded":
                add(r, "superseded-source", "must-fix",
                    "Supporting source %s is SUPERSEDED but still backs this claim." % sid,
                    "Re-point the claim at the current source, or drop it.",
                    human=True, scope=1.2)
            elif st == "estimate" and _asserts_number(claim) and "forecast" not in claim.lower() \
                    and "outlook" not in claim.lower() and "≈" not in claim:
                add(r, "estimate-as-actual", "should-fix",
                    "Source %s is an ESTIMATE/forecast but the claim reads as a committed actual." % sid,
                    "Label it 'forecast/estimate' on the slide and in the note.",
                    human=False, scope=1.0)

        # 7. CANDIDATE-ONLY (supported only by held candidates)
        if not supporting and candidates and rs != "unsupported":
            add(r, "candidate-only", "should-fix",
                "Claim has only sub-threshold candidate links (%d) — provenance not yet established." % len(candidates),
                "Confirm or reject each candidate at the human gate before the claim travels.",
                human=True, scope=1.1)

    gaps.sort(key=lambda g: g["risk_score"], reverse=True)

    blocking = [g for g in gaps if g["severity"] == "must-fix"]
    status = "ok-with-warnings" if gaps else "ok"
    status_reason = ("%d evidence gap(s): %d must-fix, %d should-fix, %d polish. "
                     "Ranked by risk and handed to Trust Review." % (
                         len(gaps), len(blocking),
                         sum(1 for g in gaps if g["severity"] == "should-fix"),
                         sum(1 for g in gaps if g["severity"] == "polish")))

    # the edit list = the exact trust-review shape (drop the ranking-only keys)
    edit_keys = ("id", "location", "type", "severity", "evidence",
                 "suggested_repair", "human_review", "status")
    edit_list = [{k: g[k] for k in edit_keys} for g in gaps]

    return {
        "run_id": emap.get("run_id"),
        "skill": "evidence-map",
        "phase": "evidence-gaps",
        "status": status,
        "status_reason": status_reason,
        "consumes": ["evidence-map.json"],
        "summary": {
            "total": len(gaps),
            "must_fix": len(blocking),
            "by_type": _count_by(gaps, "type"),
            "highest_risk": gaps[0]["id"] + " :: " + gaps[0]["type"] if gaps else None,
        },
        "gaps_ranked": gaps,
        "edit_list_for_trust_review": edit_list,
        "feeds": "trust-review (review_contract.validate_edit_list-compatible edit list)",
    }


def _source_status(emap, sid):
    # workbook source status lives on the map; packet source statuses are not
    # re-carried into the map, so a superseded packet source must come from the
    # locator's rationale. Here we read the workbook source + any per-record hint.
    ws = emap.get("workbook_source") or {}
    if sid == ws.get("id"):
        return ws.get("status")
    return None


def _count_by(items, key):
    out = {}
    for it in items:
        out[it[key]] = out.get(it[key], 0) + 1
    return out


# ---------------------------------------------------------------------------
def render_md(rep):
    L = []
    L.append("# Evidence-Gap Review — %s" % (rep.get("run_id") or ""))
    L.append("")
    L.append("> **Status: `%s`** — %s" % (rep["status"], rep["status_reason"]))
    L.append("")
    s = rep["summary"]
    L.append("- **Total gaps:** %d  ·  **must-fix:** %d" % (s["total"], s["must_fix"]))
    L.append("- **By type:** %s" % ", ".join("%s×%d" % (k, v) for k, v in s["by_type"].items()))
    L.append("- **Highest risk:** %s" % (s["highest_risk"] or "—"))
    L.append("")
    L.append("| Rank | Risk | Severity | Type | Where | Repair |")
    L.append("|---|---|---|---|---|---|")
    for g in rep["gaps_ranked"]:
        L.append("| %s | %.0f | `%s` | %s | %s | %s |" % (
            g["id"], g["risk_score"], g["severity"], g["type"],
            (g["location"] or "").replace("|", "\\|")[:48],
            (g["suggested_repair"] or "").replace("|", "\\|")[:70]))
    L.append("")
    L.append("## Detail")
    for g in rep["gaps_ranked"]:
        L.append("")
        L.append("**%s — `%s` (%s, risk %.0f)**" % (g["id"], g["type"], g["severity"], g["risk_score"]))
        L.append("  - where: %s" % g["location"])
        L.append("  - evidence: %s" % g["evidence"])
        L.append("  - repair: %s" % g["suggested_repair"])
        L.append("  - human review: %s" % ("yes" if g["human_review"] else "no"))
    L.append("")
    L.append("_Edit list (%d) handed to Trust Review in `review_contract.validate_edit_list` shape._"
             % len(rep["edit_list_for_trust_review"]))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="EM-03 evidence-gap review")
    ap.add_argument("--evidence-map", required=True)
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    try:
        emap = _load(args.evidence_map)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "failed", "status_reason": "cannot read evidence map: %s" % e}))
        return 2

    run_id = args.run_id or emap.get("run_id") or (_now_slug() + "_evidence-gaps")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    rep = review_gaps(emap)
    rep["run_id"] = run_id

    with open(os.path.join(out_dir, "evidence-gaps.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "evidence-gaps.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(rep))

    print("evidence-gaps -> %s (status: %s)" % (out_dir, rep["status"]))
    print("  %s" % rep["status_reason"])
    print("  highest risk: %s" % rep["summary"]["highest_risk"])
    return 0 if rep["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
