#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — Document Doctor S03 fixtures (DD-03 claim audit + DD-04 verdict)
#
# Proves the document trust-path DECISION layer, end to end, on self-made files:
#
#   DD-03 claim_audit.py — pulls checkable claims from the TARGET DOCUMENT + the
#     source-packet INDEX only (the bounded read, review #10); per claim a class
#     / review_status / materiality tier / verifiability + a §2 provenance record
#     (zero supporting ⇒ unsupported; a packet conflict ⇒ conflicting); dedupes
#     near-identical claims; holds sub-threshold links for approval.
#
#   DD-04 assessment.py — mirrors the Workbook Doctor verdict for surface=doc:
#     assumptions table + repair plan + readiness verdict + must_own; propagates
#     format + any DD-07 PDF-quality block (which forces not-ready/blocked).
#
# The two acceptance cases the S03 prompt calls out:
#   (A) a seeded doc with a PLANTED UNSOURCED NUMBER must NOT reach 'ready';
#   (B) a BLOCKED scanned PDF — the verdict reflects the block.
# Plus: a traced claim is supported, a packet-conflict claim is `conflicting`,
# dedupe collapses a repeat, the gate FLIPS to `ready` on a clean doc, and a
# §4 waiver caps it at `ready-with-limitations`.
#
# Exit 0 only if every assertion holds. Self-contained; no network.
# =============================================================================
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "document-doctor", "scripts")
FX = os.path.join(HERE, "document_doctor")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import document_map as dm        # noqa: E402
import claim_audit as ca         # noqa: E402
import assessment as asmt        # noqa: E402

PASS = 0
FAIL = 0
FAILS = []
TODAY = "2026-05-31"


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        FAILS.append(label)
        print("  FAIL  %s" % label)


# ---------------------------------------------------------------------------
# seeded source-packet INDEX + documents
# ---------------------------------------------------------------------------
def seeded_packet():
    """A minimal but real source packet: a current source backing revenue, and a
    conflict on headcount. The planted cloud-spend number is DELIBERATELY absent."""
    return {
        "run_id": "seed", "skill": "office-source-packet", "phase": "source-packet",
        "status": "ok-with-warnings", "task_context": "S03 doc-verdict fixture",
        "sources": [
            {"id": "S1", "name": "q2_actuals.csv", "type": "data export (CSV)",
             "date": "Q2-2026", "owner": "FP&A (close pack)", "source_status": "current",
             "sensitivity": "internal", "can_support": "Q2 actuals: revenue, headcount.",
             "cannot_support": "cloud-migration spend; Q3 outlook."},
            {"id": "S2", "name": "hr_prelim.xlsx", "type": "data export",
             "date": "Q2-2026 (prelim)", "owner": "HR", "source_status": "superseded",
             "sensitivity": "internal", "can_support": "preliminary headcount",
             "cannot_support": "final headcount"},
        ],
        "claims": [
            {"id": "C1", "class": "fact", "review_status": "verified", "metric": "revenue",
             "period": "Q2-2026", "value": "€48.2M", "sensitivity": "internal",
             "provenance": {"claim": "Q2-2026 revenue = €48.2M",
                            "supporting": [{"source_id": "S1", "locator": "q2_actuals.csv#revenue",
                                            "confidence": 1.0, "rationale": "FP&A close-pack actual."}],
                            "conflict": None}},
            {"id": "C2", "class": "fact", "review_status": "conflicting", "metric": "headcount",
             "period": "Q2-2026", "value": "1,195", "sensitivity": "internal",
             "provenance": {"claim": "Q2-2026 headcount = 1,195",
                            "supporting": [{"source_id": "S1", "locator": "q2_actuals.csv#headcount",
                                            "confidence": 1.0, "rationale": "FP&A final."}],
                            "conflict": {"with": ["S2"], "note": "S2 prelim says 1,180; S1 final says 1,195."}}},
        ],
        "conflicts": [], "open_questions": [],
    }


def build_seeded_doc():
    """A board update: one TRACED claim, one CONFLICTING claim, one PLANTED
    unsourced number, and a DUPLICATE sentence (for dedupe)."""
    p = os.path.join(FX, "seeded_claims.md")
    text = (
        "# Board Update\n\n"
        "## Summary\n\n"
        "Revenue reached €48.2M in the period, as of 30 June 2026.\n\n"
        "Headcount stood at 1,195 at quarter end.\n\n"
        "Cloud migration spend hit €9.9M this quarter.\n\n"
        "Cloud migration spend hit €9.9M this quarter.\n\n"
    )
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def build_clean_doc():
    """A clean doc: the one traced, dated, non-conflicting claim — no must-fix."""
    p = os.path.join(FX, "seeded_clean.md")
    text = ("# Board Update\n\n## Summary\n\n"
            "Revenue reached €48.2M in the period, as of 30 June 2026.\n\n")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


# ---------------------------------------------------------------------------
# DD-03 — claim audit assertions
# ---------------------------------------------------------------------------
def assert_claim_audit(doc_path, packet):
    print("\n[DD-03] claim audit on the seeded doc (+ packet INDEX)")
    doc = dm.build_map(doc_path, "md", 4000, 0.10)
    rep = ca.audit(doc, packet)

    claims = rep["claims"]
    check(rep["status"] in ("ok", "ok-with-warnings"), "DD-03: ran (status ok/with-warnings)")
    # every claim carries the full DD-03 record shape
    shape_ok = all(
        c.get("surface") == "doc" and c.get("format") == "md"
        and c.get("class") in ca.CLASSES
        and c.get("review_status") in ("verified", "needs-review", "unsupported", "conflicting")
        and c.get("materiality") in ca.MATERIALITY_TIERS
        and c.get("verifiability") in ("external-checkable", "internal-checkable", "none")
        and "locator" in c and "supporting" in c and "conflict" in c
        for c in claims)
    check(shape_ok, "DD-03: every claim = §2 record + format + materiality + verifiability")

    def find(substr):
        return next((c for c in claims if substr in c["claim"]), None)

    traced = find("Revenue reached")
    planted = find("Cloud migration spend")
    conflicting = find("Headcount")

    check(traced is not None and traced["supporting"] and
          traced["review_status"] in ("verified", "needs-review"),
          "DD-03: traced claim (revenue) has supporting + review_status verified/needs-review")
    check(traced is not None and traced["matched_packet_claim"] == "C1",
          "DD-03: revenue claim resolved against packet claim C1")

    check(planted is not None, "DD-03: planted cloud-spend number extracted as a claim")
    check(planted is not None and planted["review_status"] == "unsupported"
          and planted["supporting"] == [],
          "DD-03: PLANTED unsourced number ⇒ unsupported, zero supporting (§2)")
    check(planted is not None and "numeric" in (planted.get("materiality_all") or [])
          and planted["materiality"] in asmt._MATERIAL_MUSTFIX
          and planted["class"] == "fact",
          "DD-03: planted number tagged a material tier (numeric/headline) + class=fact")
    check(planted is not None and planted["verifiability"] in ("internal-checkable",),
          "DD-03: planted numeric claim tagged internal-checkable")

    check(conflicting is not None and conflicting["review_status"] == "conflicting"
          and conflicting["conflict"] is not None,
          "DD-03: packet-conflict claim (headcount) ⇒ conflicting + §2 conflict object")

    check(rep["summary"]["deduped_merged"] >= 1,
          "DD-03: near-identical claim DEDUPED (S06 inherits a deduped list)")
    return doc, rep


def assert_held_links(packet):
    print("\n[DD-03] sub-threshold link is HELD, never promoted")
    # a doc claim that PARTIALLY overlaps a packet claim but cannot cleanly match
    # (a different metric, no number agreement) -> sub-threshold -> held, not promoted
    p = os.path.join(FX, "seeded_held.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# Note\n\nThe headcount narrative in the revenue review referenced "
                 "several FP&A workstreams.\n")
    doc = dm.build_map(p, "md", 4000, 0.10)
    rep = ca.audit(doc, packet)
    held = rep["held_for_approval"]
    check(isinstance(held, list), "DD-03: held_for_approval channel present")
    check(all((not c.get("candidates")) or (c["review_status"] == "unsupported")
              for c in rep["claims"]),
          "DD-03: a candidate-only claim is NOT silently promoted (stays unsupported)")


# ---------------------------------------------------------------------------
# DD-04 — verdict assertions
# ---------------------------------------------------------------------------
def run_chain(doc_path, packet, out_dir, travel_class="Travels", resolutions=None):
    os.makedirs(out_dir, exist_ok=True)
    fmt = dm.detect_format(doc_path, "auto")
    dm.build_map  # noqa
    m = dm.build_map(doc_path, fmt, 4000, 0.10)
    _write(out_dir, "document-map.json", m)
    import prose_landmines as pl
    lm = {"findings": pl.scan_paragraphs(m, _date(), 365) + pl.scan_figures(m)}
    _write(out_dir, "prose-landmines.json", lm)
    cadoc = ca.audit(m, packet)
    cadoc = {"claims": cadoc["claims"], "held_for_approval": cadoc.get("held_for_approval", []),
             "status": cadoc["status"], "format": cadoc.get("format")}
    _write(out_dir, "claim-audit.json", cadoc)
    res_path = None
    if resolutions is not None:
        res_path = os.path.join(out_dir, "resolutions.json")
        _write(out_dir, "resolutions.json", resolutions)
    v = asmt.run(os.path.join(out_dir, "document-map.json"), out_dir,
                 landmines_path=os.path.join(out_dir, "prose-landmines.json"),
                 claim_audit_path=os.path.join(out_dir, "claim-audit.json"),
                 source_packet=None, resolutions_path=res_path,
                 run_id="fix", travel_class=travel_class)
    return v


def assert_verdict_not_ready(doc_path, packet):
    print("\n[DD-04] (A) planted unsourced number ⇒ NOT-READY")
    v = run_chain(doc_path, packet, os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd04_seed"))
    check(v["readiness"] == "not-ready", "DD-04: seeded doc readiness == not-ready")
    check(v["verdict_detail"]["must_fix_total"] >= 1, "DD-04: at least one must-fix")
    owns = " ".join(o["what"] for o in v["must_own"])
    check("unsupported-claim" in owns or "Cloud migration" in owns or
          any(o["kind"] == "open-must-fix" for o in v["must_own"]),
          "DD-04: the unsupported claim is in the must_own list")
    # memo: claim provenance must FAIL
    memo = {c["check"]: c["result"] for c in v["verification_memo"]}
    check(memo.get("claim_provenance") == "fail",
          "DD-04: verification memo — claim provenance = FAIL")
    return v


def assert_verdict_ready_flip(packet):
    print("\n[DD-04] gate FLIPS — a clean doc reaches READY")
    clean = build_clean_doc()
    v = run_chain(clean, packet, os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd04_clean"))
    check(v["readiness"] == "ready",
          "DD-04: clean doc readiness == ready (no must-fix) — the gate is not stuck")
    return v


def assert_verdict_waiver(doc_path, packet):
    print("\n[DD-04] a §4 waiver caps at READY-WITH-LIMITATIONS")
    base = run_chain(doc_path, packet, os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd04_seed2"))
    blocking = base["verdict_detail"]["must_fix_blocking"]
    check(len(blocking) >= 1, "DD-04: there is a blocking must-fix to waive")
    resolutions = {"resolutions": [
        {"finding_id": fid, "lifecycle": "waived-by-human",
         "waiver": {"owner": "Alex Example", "decision": "accept residual risk",
                    "due_date": "2026-06-30", "signoff": "approved",
                    "rationale": "figure confirmed verbally; sourcing to follow."}}
        for fid in blocking]}
    v = run_chain(doc_path, packet, os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd04_waived"),
                  travel_class="Travels", resolutions=resolutions)
    check(v["readiness"] == "ready-with-limitations",
          "DD-04: all must-fix waived ⇒ ready-with-limitations (Travels)")
    check(any(o["kind"] == "waived-limitation" for o in v["must_own"]),
          "DD-04: the waived limitation travels in must_own")


def assert_verdict_blocked_pdf():
    print("\n[DD-04] (B) blocked scanned PDF — the verdict reflects the block")
    # reuse / build the scanned image-only PDF from the DD-01 fixture
    scanned = os.path.join(FX, "scanned.pdf")
    if not os.path.isfile(scanned):
        import fixture_document_doctor as fdd
        scanned = fdd.build_pdf_scanned()
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd04_scan")
    os.makedirs(out, exist_ok=True)
    m = dm.build_map(scanned, "pdf", 4000, 0.10, disable_ocr=True)
    _write(out, "document-map.json", m)
    check(m["status"] == "blocked", "DD-04: scanned PDF map is blocked (DD-07)")
    cadoc = ca.audit(m, None)
    check(cadoc["status"] == "blocked",
          "DD-04: claim audit on a blocked map returns blocked (no fabricated claims)")
    _write(out, "claim-audit.json", cadoc)
    v = asmt.run(os.path.join(out, "document-map.json"), out,
                 claim_audit_path=os.path.join(out, "claim-audit.json"),
                 run_id="scan", travel_class="Travels")
    check(v["status"] == "blocked", "DD-04: verdict status == blocked")
    check(v["readiness"] == "not-ready", "DD-04: verdict readiness == not-ready")
    fids = " ".join(o["what"] for o in v["must_own"])
    check("DOC-PBW-01" in fids or "extraction-loss" in fids,
          "DD-04: the DOC-PBW-01 extraction-loss must-fix is carried into must_own")
    check(v["extraction"]["map_blocked"] is True and v["extraction"]["pdf_quality"] is not None,
          "DD-04: PDF-quality block propagated into the verdict's extraction section")


# ---------------------------------------------------------------------------
def _write(out_dir, name, obj):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str, ensure_ascii=False)


def _date():
    import datetime
    return datetime.date(2026, 5, 31)


def main():
    os.makedirs(FX, exist_ok=True)
    packet = seeded_packet()
    doc = build_seeded_doc()

    assert_claim_audit(doc, packet)
    assert_held_links(packet)
    assert_verdict_not_ready(doc, packet)
    assert_verdict_ready_flip(packet)
    assert_verdict_waiver(doc, packet)
    assert_verdict_blocked_pdf()

    print("\n" + "-" * 60)
    print("document-verdict (S03) fixtures: %d passed, %d failed" % (PASS, FAIL))
    for f in FAILS:
        print("   - FAILED:", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
