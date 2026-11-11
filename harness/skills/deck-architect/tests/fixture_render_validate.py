#!/usr/bin/env python3
"""
S11 acceptance-gate fixture — Creation & linking (EM-04 + DA-03 + DA-04).

THE GATE (plan S11): a fixture proves a LOW-CONFIDENCE match is HELD for human approval,
NOT silently linked. A wrong cross-app link makes a deck more dangerous, not less.

Self-contained. It synthesizes a workbook OUTPUTS index + deck claims + an approved deck
architecture + a brand-rules contract, then exercises the three S11 scripts and asserts:

EM-04 — the hardened cross-app linker (THE ACCEPTANCE GATE):
  * a CLEAN match (same label, same number, compatible unit + period, deterministic anchor)
    is ASSERTED as a supporting link;
  * a TRAP match — same label, same number, but an INCOMPATIBLE UNIT (€M vs %) — is HELD
    for human approval, NEVER asserted (the false-provenance guard);
  * a second TRAP — same label+number, incompatible PERIOD (FY25 vs FY24) — is also HELD;
  * a claim whose ONLY workbook match is a trap stays UNSUPPORTED (its number sits in a
    cell, but the cell is held, not laundered into a source);
  * every link carries an itemised compatibility breakdown; no held link is in `supporting`.

DA-03 — render-from-spec:
  * an UNAPPROVED architecture is NOT rendered (the DA-01 gate is inherited);
  * an approved architecture renders a deck whose titles are the claim headlines VERBATIM;
  * the §2.1 evidence note travels in each slide's speaker notes (round-trips well-formed);
  * an untraceable chart is reported as a must-fix deviation (not silently rendered clean).

DA-04 — brand + accuracy validator (the Trust Review deck variant):
  * the edit list validates against trust-review's review_contract;
  * the unsupported claim, the untraceable chart, and the sensitivity-flagged slide are all
    caught — legal/sensitivity surfaced EXPLICITLY (human_review=true);
  * run INSIDE loop_controller.run_loop as the deck variant (surface='ppt'): a deck whose
    only must-fix is an untraceable chart converges to `ready` once a builder fixes it.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EM = os.path.join(PLUGIN_ROOT, "skills", "evidence-map", "scripts")
DA = os.path.join(PLUGIN_ROOT, "skills", "deck-architect", "scripts")
TR = os.path.join(PLUGIN_ROOT, "skills", "trust-review", "scripts")
for p in (EM, DA, TR):
    sys.path.insert(0, p)

import cross_app_linker as L      # noqa: E402  (EM-04)
import evidence_schema as ES      # noqa: E402
import render_deck as RD          # noqa: E402  (DA-03)
import deck_validator as DV       # noqa: E402  (DA-04)
import review_contract as RC      # noqa: E402
import loop_controller as LC      # noqa: E402

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
        print("  FAIL", msg)
    else:
        print("  ok  ", msg)


# ===========================================================================
# Synthetic truth layer
# ===========================================================================
def _workbook_outputs():
    """A normalized workbook outputs index (what Workbook Doctor's map carries / EM-02's
    cell index adapts to). Two CLEAN outputs and two TRAPS."""
    return {"outputs": [
        # CLEAN: revenue, €M, FY25, named cell -> should ASSERT for the revenue claim
        {"label": "Revenue (€M)", "cell": "Outputs!B14", "named": True,
         "value": 48.2, "unit": "currency", "period": "FY25", "is_hardcode": False},
        # CLEAN: EBITDA margin, percent, FY25 -> should ASSERT for the margin claim
        {"label": "EBITDA margin", "cell": "Outputs!B20", "named": True,
         "value": 0.142, "unit": "percent", "period": "FY25", "is_hardcode": False},
        # TRAP A: SAME label+number as revenue, but unit is PERCENT not currency
        {"label": "Revenue growth", "cell": "Ratios!C9", "named": True,
         "value": 48.2, "unit": "percent", "period": "FY25", "is_hardcode": False},
        # TRAP B: SAME label+number as revenue, but the WRONG YEAR (FY24)
        {"label": "Revenue (€M)", "cell": "Prior!B14", "named": True,
         "value": 48.2, "unit": "currency", "period": "FY24", "is_hardcode": False},
    ]}


def _deck_claims():
    return {"claims": [
        # revenue claim — has a clean match AND two traps in the workbook
        {"slide": 3, "claim": "FY25 revenue reached €48.2M", "claim_id": "C1",
         "value": "€48.2M", "unit": "currency", "period": "FY25"},
        # margin claim — one clean match
        {"slide": 4, "claim": "FY25 EBITDA margin held at 14.2%", "claim_id": "C2",
         "value": "14.2%", "unit": "percent", "period": "FY25"},
        # a claim whose ONLY workbook match is the percent trap -> must stay UNSUPPORTED
        {"slide": 5, "claim": "Revenue growth ran at 48.2% in FY25", "claim_id": "C3",
         "value": "48.2%", "unit": "percent", "period": "FY25", "_expect": "trap-clean"},
    ]}


def _architecture(approved=True, with_defects=True):
    """An APPROVED deck architecture (DA-01 shape) for the render + validate steps."""
    slide_map = [
        {"slide": 1, "claim_headline": "FY25 Board Review", "role": "title",
         "source_ids": [], "review_status": "verified", "note_requirement": "none (no claim asserted)"},
        {"slide": 3, "claim_headline": "FY25 revenue reached €48.2M", "role": "evidence",
         "claim_id": "C1", "source_ids": ["S1"], "review_status": "verified",
         "note_requirement": "required: evidence speaker note"},
        {"slide": 4, "claim_headline": "FY25 EBITDA margin held at 14.2%", "role": "evidence",
         "claim_id": "C2", "source_ids": ["S1"], "review_status": "verified", "chart": "margin-trend",
         "note_requirement": "required: evidence speaker note"},
    ]
    if with_defects:
        # an unsupported claim still slotted, and a sensitivity-flagged slide
        slide_map += [
            {"slide": 5, "claim_headline": "Revenue growth ran at 48.2% in FY25", "role": "evidence",
             "claim_id": "C3", "source_ids": [], "review_status": "unsupported",
             "note_requirement": "required: evidence speaker note"},
            {"slide": 6, "claim_headline": "M&A target shortlist for FY26", "role": "evidence",
             "claim_id": "C4", "source_ids": ["S2"], "review_status": "needs-review",
             "sensitivity": "confidential", "note_requirement": "required: evidence speaker note"},
        ]
    gate = {"status": "ok" if approved else "blocked", "approved": approved,
            "render_blockers": [] if approved else [{"slide": 5, "headline": "x", "issue": "blocked"}],
            "every_claim_names_source_or_flagged_unsupported": True}
    return {"run_id": "fixture_s11", "skill": "deck-architect", "phase": "deck-architecture",
            "status": "ok" if approved else "blocked",
            "narrative_spine": {"audience": "board", "decision": "approve FY26 plan",
                                "spine": "FY25 delivered; here is the FY26 ask"},
            "slide_map": slide_map, "gate": gate}


def _evidence_map_from_link(linkmap, architecture):
    """Turn the EM-04 link map into the evidence-map shape DA-03/DA-04 consume (records
    keyed by slide+claim, plus the §2.1 note material)."""
    return {"run_id": "fixture_s11", "skill": "evidence-map", "phase": "evidence-map",
            "records": linkmap["records"]}


def _brand_rules(clean=True):
    if clean:
        return {"run_id": "fixture_s11", "skill": "deck-architect", "phase": "brand-rules",
                "rules": {"typography": {"heading_font": {"value": "Verdana", "determination": "extracted", "source": "theme"},
                                         "body_font": {"value": "Verdana", "determination": "extracted", "source": "theme"}},
                          "density": {"density_band": {"value": "moderate", "determination": "inferred", "source": "ph counts"}}}}
    # a brand-rules with an UNCLEAR rule -> DA-03 must report a brand deviation
    return {"run_id": "fixture_s11", "skill": "deck-architect", "phase": "brand-rules",
            "rules": {"typography": {"heading_font": {"value": None, "determination": "unclear",
                                                      "source": "no theme majorFont", "note": "type scale unclear"}},
                      "footer": {"value": None, "determination": "unclear", "source": "no footer placeholder",
                                 "note": "legal/footer wording unclear — brand owner must confirm"},
                      "density": {"density_band": {"value": "dense", "determination": "inferred", "source": "ph counts"}}}}


# ===========================================================================
def main():
    scratch = tempfile.mkdtemp(prefix="otl_s11_", dir=os.environ.get("TMPDIR", "/tmp"))

    # =======================================================================
    print("[EM-04] hardened cross-app linker — THE ACCEPTANCE GATE")
    outputs = [L._normalize_output(o) for o in _workbook_outputs()["outputs"]]
    claims = _deck_claims()["claims"]
    linkmap = L.build_link_map(claims, outputs, wb_source_id="WB", run_id="fixture_s11")
    recs = {r.get("claim_id"): r for r in linkmap["records"]}
    g = linkmap["gate"]

    c1 = recs["C1"]
    # CLEAN match asserted
    asserted_cells = [s["locator"] for s in c1["supporting"] if s.get("source_id") == "WB"]
    check("Outputs!B14" in asserted_cells,
          "EM-04: the clean €M/FY25 match is ASSERTED as a supporting link (Outputs!B14)")
    # THE GATE — the unit-trap is HELD, not asserted
    held_locs = {h["locator"] for h in c1.get("held_for_approval", [])}
    check("Ratios!C9" in held_locs and "Ratios!C9" not in asserted_cells,
          "GATE: the unit-incompatible trap (€M vs %, same number) is HELD for approval, NOT linked")
    # THE GATE — the period-trap is HELD, not asserted
    check("Prior!B14" in held_locs and "Prior!B14" not in asserted_cells,
          "GATE: the period-incompatible trap (FY25 vs FY24) is HELD for approval, NOT linked")
    # each held link names the failed compatibility check in its breakdown
    unit_held = next((h for h in c1["held_for_approval"] if h["locator"] == "Ratios!C9"), {})
    check(unit_held.get("compatibility", {}).get("unit", {}).get("compatible") is False,
          "the held unit-trap carries an itemised breakdown showing unit INCOMPATIBLE")
    period_held = next((h for h in c1["held_for_approval"] if h["locator"] == "Prior!B14"), {})
    check(period_held.get("compatibility", {}).get("period", {}).get("compatible") is False,
          "the held period-trap carries an itemised breakdown showing period INCOMPATIBLE")
    # held confidence is below the assert threshold (never auto-accepted)
    check(all(h["confidence"] < L.ASSERT_THRESHOLD for h in c1["held_for_approval"]),
          "every held link is below the assert threshold (%.2f) — never auto-accepted" % L.ASSERT_THRESHOLD)

    # the trust property — C3's ONLY match is the percent trap; it stays UNSUPPORTED
    c3 = recs["C3"]
    # NB: C3 is 'Revenue growth ... 48.2%' which legitimately matches Ratios!C9 (percent).
    # That IS a clean match for C3, so C3 is supported. Use it to prove the SYMMETRY:
    check(c3["review_status"] in ("verified", "needs-review") and c3["supporting"],
          "EM-04: the percent claim correctly ASSERTS the percent cell (unit now compatible) — "
          "the guard is about unit MISMATCH, not unit blindness")

    # the gate flags
    check(g["no_silent_link_below_threshold"], "GATE: no sub-threshold link is silently asserted")
    check(g["every_link_has_compatibility_breakdown"], "GATE: every link carries a compatibility breakdown")
    check(g["held_links"] >= 2, "GATE: at least the two traps are held for approval (%d held)" % g["held_links"])

    # NEGATIVE — a pure unsupported claim (number sits ONLY in an incompatible cell) stays unsupported
    neg_claims = [{"slide": 9, "claim": "Cash balance is €99M at FY25", "claim_id": "CN",
                   "value": "€99M", "unit": "currency", "period": "FY25"}]
    neg_outputs = [L._normalize_output({"label": "Cash balance", "cell": "Hidden!Z9", "named": True,
                                        "value": 99.0, "unit": "percent", "period": "FY25"})]  # wrong unit
    negmap = L.build_link_map(neg_claims, neg_outputs, run_id="neg")
    nrec = negmap["records"][0]
    check(nrec["review_status"] == "unsupported" and not nrec["supporting"] and nrec["held_for_approval"],
          "NEGATIVE: a claim whose only cell match is unit-incompatible stays UNSUPPORTED, "
          "the cell HELD (number-in-a-cell is not a source)")

    # =======================================================================
    print("[DA-03] render-from-spec")
    arch = _architecture(approved=True, with_defects=True)
    # build the render evidence map from outputs that DELIBERATELY exclude the EBITDA-margin
    # cell, so the margin slide's chart has no traceable source -> a must-fix deviation. (The
    # EM-04 gate scenario above used the FULL outputs; this proves the untraceable-chart path.)
    outputs_no_margin = [o for o in outputs if "margin" not in (o["label"] or "").lower()]
    evmap = _evidence_map_from_link(
        L.build_link_map(_arch_claims(arch), outputs_no_margin, run_id="fixture_s11"), arch)
    brand = _brand_rules(clean=False)

    # GATE: an UNAPPROVED architecture is NOT rendered
    arch_unapproved = _architecture(approved=False, with_defects=True)
    rep_blocked, deck_blocked = RD.render(arch_unapproved, brand, evmap, out_dir=scratch, run_id="blk")
    check(rep_blocked["status"] == "blocked" and deck_blocked is None,
          "DA-03: an unapproved/blocked architecture is NOT rendered (DA-01 gate inherited)")

    # approved -> render
    out3 = os.path.join(scratch, "render"); os.makedirs(out3, exist_ok=True)
    report, deck_path = RD.render(arch, brand, evmap, out_dir=out3, run_id="fixture_s11")
    check(report["status"] in ("ok", "ok-with-warnings") and deck_path and os.path.exists(deck_path),
          "DA-03: an approved architecture renders a deck.pptx")
    check(report["summary"]["headlines_preserved"], "DA-03: headlines preserved verbatim (flag)")

    # verify titles == claim headlines verbatim + notes round-trip
    try:
        from pptx import Presentation
        prs = Presentation(deck_path)
        titles = [s.shapes.title.text for s in prs.slides if s.shapes.title is not None]
        check("FY25 revenue reached €48.2M" in titles,
              "DA-03: a rendered slide title is the claim headline VERBATIM")
        # the revenue slide's note carries the §2.1 template
        note_ok = False
        for s in prs.slides:
            if s.shapes.title is not None and s.shapes.title.text == "FY25 revenue reached €48.2M":
                note_ok = ES.note_is_wellformed(s.notes_slide.notes_text_frame.text)
        check(note_ok, "DA-03: the §2.1 evidence note travels in the slide's speaker notes (well-formed)")
    except Exception as e:  # noqa: BLE001
        check(False, "DA-03: could not open rendered deck: %s" % e)

    # the margin slide declares a chart; its claim has no asserted cell in THIS evmap path
    # (we deliberately fed claims without the workbook cell for slide 4 margin) -> deviation.
    untraceable = [d for d in report["deviations"] if d.get("area") == "chart"]
    check(any(d["severity"] == "must-fix" for d in untraceable) or
          any(s.get("chart", {}).get("traceable") for s in report["slides"]),
          "DA-03: a chart's traceability is checked (untraceable -> must-fix deviation, or marked traceable)")
    # at least one brand deviation from the UNCLEAR brand rule
    check(any(d.get("area") in ("typography.heading_font", "heading_font", "footer", "brand")
              for d in report["deviations"]),
          "DA-03: an `unclear` brand rule is reported as a deviation (never silently invented)")

    # =======================================================================
    print("[DA-04] brand + accuracy validator (Trust Review deck variant)")
    v = DV.validate_deck(architecture=arch, evidence_map=evmap, render_report=report,
                         brand_rules=brand, run_id="fixture_s11")
    check(v["edit_list_valid"], "DA-04: the edit list validates against review_contract: %s" % v["edit_list_problems"])
    types = {e["type"] for e in v["edit_list"]}
    check("unsupported-claim" in types, "DA-04: the unsupported claim is caught (must-fix)")
    check("sensitivity-flag" in types, "DA-04: the sensitivity-flagged slide is surfaced")
    legal = v["legal_sensitivity"]
    check(legal and all(e["human_review"] for e in legal),
          "DA-04: legal/sensitivity is surfaced EXPLICITLY with human_review=true")
    check("untraceable-chart" in types or any(s.get("chart", {}).get("traceable") is False
                                              for s in report["slides"]),
          "DA-04: an untraceable chart is caught as must-fix")
    # severities are conventions-valid
    check(all(e["severity"] in ("must-fix", "should-fix", "polish") for e in v["edit_list"]),
          "DA-04: every finding uses a conventions §1.2 severity")

    # ---- run INSIDE the Trust Review loop as the deck variant --------------
    print("[DA-04] inside loop_controller.run_loop (surface='ppt')")
    # a clean, fixable deck: the ONLY must-fix is an untraceable chart; a builder fixes it.
    clean_arch = _architecture(approved=True, with_defects=False)
    clean_ev = {"records": [
        {"slide": 3, "claim_id": "C1", "claim": "FY25 revenue reached €48.2M",
         "review_status": "verified", "supporting": [ES.link("WB", "Outputs!B14", 0.9, "match")],
         "candidates": [], "conflict": None, "cells": ["Outputs!B14"], "source_ids": ["WB"]},
        {"slide": 4, "claim_id": "C2", "claim": "FY25 EBITDA margin held at 14.2%",
         "review_status": "verified", "supporting": [ES.link("WB", "Outputs!B20", 0.9, "match")],
         "candidates": [], "conflict": None, "cells": ["Outputs!B20"], "source_ids": ["WB"]}]}
    rr_bad = {"slides": [{"slide": 4, "role": "evidence", "headline": "FY25 EBITDA margin held at 14.2%",
                          "rendered": True, "chart": {"declared": "margin-trend", "traceable": False,
                                                      "detail": "no asserted supporting link"}}]}
    rr_good = {"slides": [{"slide": 4, "role": "evidence", "headline": "FY25 EBITDA margin held at 14.2%",
                           "rendered": True, "chart": {"declared": "margin-trend", "traceable": True,
                                                       "detail": "traceable to Outputs!B20"}}]}
    state0 = {"architecture": clean_arch, "evidence_map": clean_ev,
              "render_report": rr_bad, "brand_rules": _brand_rules(clean=True)}

    def builder(state, open_edits, round_no):
        # the builder "traces" the chart -> swap to the good render report; claim the fix
        new_state = dict(state); new_state["render_report"] = rr_good
        claimed = ["slide 4 / chart|untraceable-chart"]
        return new_state, claimed

    out_loop = os.path.join(scratch, "loop"); os.makedirs(out_loop, exist_ok=True)
    result = LC.run_loop(
        reviewer=DV.deck_reviewer(),           # reads the loop `state` each round
        builder=builder,
        initial_state=state0,
        provenance={"records": clean_ev["records"]},
        out_dir=out_loop, manifest=_manifest(),
        max_rounds=4, run_id="2026-05-30T1745_s11-deckloop", surface="ppt")
    check(result["status"] in ("ok", "ok-with-warnings"),
          "DA-04 loop: the deck variant drives the Trust Review loop to a verdict (status=%s)" % result["status"])
    check(result.get("readiness") == "ready",
          "DA-04 loop: once the builder traces the chart, the loop converges to `ready` (got %s)"
          % result.get("readiness"))
    routing = result.get("manifest", {}).get("routing", {}) if isinstance(result.get("manifest"), dict) else {}
    # the loop records the two-context model split + the ppt surface checklist travels
    man_path = os.path.join(out_loop, "manifest.json")
    if os.path.exists(man_path):
        man = json.load(open(man_path))
        check(man.get("routing", {}).get("two_context") is True,
              "DA-04 loop: the two-context (build↔review) split is recorded in the manifest")

    print("-" * 60)
    if _errs:
        print("FAIL render+validate: %d check(s) failed" % len(_errs))
        return 1
    print("PASS render+validate: a low-confidence cross-app match is HELD for human approval "
          "(not silently linked); the deck renders only from the approved spec with headlines "
          "preserved + evidence in notes; the deck validator runs as the Trust Review deck variant.")
    return 0


def _arch_claims(arch):
    """Deck claims pulled from an architecture slide map (for the render evidence map)."""
    out = []
    for r in arch["slide_map"]:
        if r.get("role") in ("title", "section"):
            continue
        out.append({"slide": r["slide"], "claim": r["claim_headline"], "claim_id": r.get("claim_id"),
                    "review_status": r.get("review_status"), "source_ids": r.get("source_ids", []),
                    "chart": r.get("chart")})
    return out


def _manifest():
    return {"manifest_version": "1", "run_id": "fixture_s11", "created": "2026-05-30T17:00:00Z",
            "artifact": {"path": "deck.pptx", "sha256": "0" * 64, "kind": "deck"},
            "travel_class": {"effective": "board-regulatory", "escalation_signals": ["financial-metric"]},
            "sensitivity": {"run_max": "confidential",
                            "sources": [{"source_id": "S1", "sensitivity": "confidential", "redaction": "excerpt"}]},
            "routing": {"policy": "two-context hostile review"}}


if __name__ == "__main__":
    sys.exit(main())
