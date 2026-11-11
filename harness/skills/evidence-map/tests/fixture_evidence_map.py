#!/usr/bin/env python3
"""
S10 acceptance-gate fixture — Evidence Map (EM-01 + EM-02 + EM-03).

THE GATE (plan S10): a fixture deck + workbook yields a map where EVERY claim is
traced, conflicted, or explicitly UNSUPPORTED — none silently sourced.

Self-contained: it synthesizes a source packet, a deck-architecture slide map, a
backing workbook (.xlsx) and a deck (.pptx) in a scratch dir, then runs the three
EM scripts the way the session would, and asserts:

EM-01 (schema):
  * a valid record round-trips and derives its review_status + confidence from links;
  * the discipline invariants HOLD — a sub-threshold link in `supporting` is rejected,
    and zero supporting links cannot read as anything but unsupported/conflicting.

EM-02 (builder + linker + notes):
  * POSITIVE — every claim is traced / conflicted / unsupported; none silently sourced.
  * MULTI-SOURCE — a claim rests on the packet source AND the matching workbook cell.
  * CONFLICT — a line carried at two different values across tabs is `conflicting`.
  * THE TRUST PROPERTY — a claim the packet flags unsupported, whose number ALSO sits
    in a workbook HARDCODE cell, stays `unsupported`: the cell is HELD as a candidate,
    never asserted as provenance ("the number is in a cell" is not a source).
  * NOTES — the §2.1 evidence note is injected into the deck and round-trips well-formed.
  * NEGATIVE — plant a claim that asserts a number with ZERO sources mislabeled
    `verified`; the schema must refuse to let it read as sourced.

EM-03 (gap review):
  * the unsupported claim is the top-ranked must-fix gap;
  * the emitted edit list validates against trust-review's review_contract.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EM_SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "evidence-map", "scripts")
TR_SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "trust-review", "scripts")
sys.path.insert(0, EM_SCRIPTS)
sys.path.insert(0, TR_SCRIPTS)

import evidence_schema as ES      # noqa: E402
import evidence_builder as EB     # noqa: E402
import evidence_gaps as EG        # noqa: E402

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
def _packet():
    return {
        "run_id": "fixture_evidence_map",
        "task_context": "Q1 FY26 board update (fixture)",
        "sources": [
            {"id": "S1", "label": "q1_actuals.csv", "source_status": "current",
             "date": "end Q1-FY26", "owner": "FP&A (close pack)"},
            {"id": "S2", "label": "forecast.csv", "source_status": "estimate",
             "date": "mid Q1-FY26", "owner": "FP&A"},
            {"id": "S3", "label": "cfo_email.md", "source_status": "current",
             "date": "Q1 close week", "owner": "CFO"},
        ],
        "claims": [
            {"id": "C1", "class": "fact", "review_status": "conflicting",
             "metric": "revenue", "period": "Q1-FY26", "value": "€50.0M",
             "provenance": {"claim": "Q1-FY26 revenue = €50.0M",
                            "supporting": [{"source_id": "S1", "locator": "q1_actuals.csv#revenue",
                                            "confidence": 1.0, "rationale": "FP&A close-pack actual"}],
                            "conflict": {"with": ["S2"], "note": "S2 forecast says €48.5M"}}},
            {"id": "C2", "class": "fact", "review_status": "verified",
             "metric": "headcount", "period": "Q1-FY26", "value": "1,200",
             "provenance": {"claim": "Q1-FY26 headcount = 1,200",
                            "supporting": [{"source_id": "S1", "locator": "q1_actuals.csv#headcount",
                                            "confidence": 1.0, "rationale": "close-pack actual"}],
                            "conflict": None}},
            {"id": "C3", "class": "interpretation", "review_status": "unsupported",
             "metric": "revenue growth", "period": "Q1-FY26", "value": "10% YoY",
             "provenance": {"claim": "Q1 revenue up 10% YoY",
                            "supporting": [], "conflict": None}},
            {"id": "C4", "class": "fact", "review_status": "verified",
             "metric": "EBITDA margin", "period": "Q1-FY26", "value": "20%",
             "provenance": {"claim": "Q1-FY26 EBITDA margin = 20%",
                            "supporting": [
                                {"source_id": "S1", "locator": "q1_actuals.csv#margin",
                                 "confidence": 1.0, "rationale": "close-pack actual"},
                                {"source_id": "S3", "locator": "cfo_email.md#margin",
                                 "confidence": 0.9, "rationale": "CFO confirmed in close email"}],
                            "conflict": None}},
        ],
        "conflicts": [], "open_questions": [],
    }


def _architecture():
    # the DA/EM slide map — content rows only, mirroring deck_architecture's shape
    return {
        "run_id": "fixture_evidence_map",
        "slide_map": [
            {"slide": 1, "claim_headline": "Q1 FY26 Board Update", "role": "title",
             "source_ids": [], "review_status": "verified",
             "note_requirement": "none (no claim asserted)"},
            {"slide": 2, "claim_headline": "Q1-FY26 revenue = €50.0M", "role": "evidence",
             "claim_id": "C1", "source_ids": ["S1"], "review_status": "conflicting",
             "note_requirement": "required: evidence speaker note"},
            {"slide": 2, "claim_headline": "Q1-FY26 headcount = 1,200", "role": "evidence",
             "claim_id": "C2", "source_ids": ["S1"], "review_status": "verified",
             "note_requirement": "required: evidence speaker note"},
            {"slide": 2, "claim_headline": "Q1 revenue up 10% YoY", "role": "evidence",
             "claim_id": "C3", "source_ids": [], "review_status": "unsupported",
             "note_requirement": "required: evidence speaker note"},
            {"slide": 3, "claim_headline": "Q1-FY26 EBITDA margin = 20%", "role": "evidence",
             "claim_id": "C4", "source_ids": ["S1", "S3"], "review_status": "verified",
             "note_requirement": "required: evidence speaker note"},
        ],
    }


def _build_workbook(path):
    import openpyxl
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "Actuals"
    a["A1"] = "Metric"; a["B1"] = "Q1-FY26"
    a["A2"] = "Revenue (€M)"; a["B2"] = 50.0
    a["A3"] = "Headcount"; a["B3"] = 1200
    a["A4"] = "EBITDA margin"; a["B4"] = 0.20
    b = wb.create_sheet("Board")
    b["A1"] = "Line"; b["B1"] = "Value"
    b["A2"] = "Revenue (€M)"; b["B2"] = "=Actuals!B2"      # formula (mirror)
    b["A3"] = "Revenue YoY %"; b["B3"] = 0.10               # HARDCODE in a formula column
    b["A4"] = "Headcount"; b["B4"] = 1185                   # HARDCODE — conflicts w/ Actuals 1200
    b["A5"] = "EBITDA margin"; b["B5"] = "=Actuals!B4"      # formula (mirror)
    wb.save(path)
    wb.close()


def _build_deck(path):
    from pptx import Presentation
    prs = Presentation()
    for title in ["Q1 FY26 Board Update", "Results", "Profitability"]:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = title
    prs.save(path)


# ===========================================================================
def main():
    scratch = tempfile.mkdtemp(prefix="otl_em_", dir=os.environ.get("TMPDIR", "/tmp"))
    packet = _packet()
    arch = _architecture()
    wb_path = os.path.join(scratch, "model.xlsx")
    deck_path = os.path.join(scratch, "deck.pptx")
    _build_workbook(wb_path)
    _build_deck(deck_path)

    print("[EM-01] schema + note contract")
    rec = ES.evidence_record(
        2, "Revenue is ahead of plan",
        supporting=[ES.link("S1", "model.xlsx#Actuals!B2", 0.9, "label+value match")],
        candidates=[ES.link("S2", "plan.xlsx#Plan!B2", 0.4, "period ambiguous")],
        conflict=None, declared_status="verified",
        calculation="Actuals!B2", assumptions=[], date_range="Q1-FY26", owner="FP&A")
    check(not ES.validate_record(rec), "a well-formed record validates")
    check(rec["review_status"] == "verified" and rec["confidence"] == 0.9,
          "review_status + confidence are derived from the links")
    note = ES.speaker_note(rec)
    check(ES.note_is_wellformed(note), "speaker note carries all five §2.1 lines in order")

    # discipline: a sub-threshold link cannot live in `supporting`
    bad = ES.evidence_record(2, "X", supporting=[ES.link("S9", "a#B1", 0.3, "weak")])
    check(any("LINK_THRESHOLD" in p for p in ES.validate_record(bad)),
          "a sub-threshold link in supporting is rejected (must be a candidate)")
    # discipline: zero supporting can only read unsupported/conflicting
    forced = ES.evidence_record(2, "X", supporting=[], conflict=None)
    check(forced["review_status"] == "unsupported",
          "zero supporting links derive to 'unsupported' (cannot imply a source)")

    print("[EM-02] builder + cross-app linker + notes")
    cell_index, warn = EB.build_cell_index(wb_path)
    check(cell_index is not None, "workbook cell index built")
    emap = EB.build_evidence_map(arch=arch, packet=packet, cell_index=cell_index,
                                 wb_source_id="WB", run_id="fixture_evidence_map",
                                 workbook_path=wb_path)
    recs = {r.get("claim_id"): r for r in emap["records"]}
    g = emap["gate"]

    # THE GATE
    check(g["every_claim_traced_conflicted_or_unsupported"],
          "GATE: every claim is traced, conflicted, or explicitly unsupported")
    check(g["none_silently_sourced"], "GATE: none silently sourced (all records pass schema)")

    # multi-source
    c4 = recs.get("C4")
    check(c4 and len(c4["supporting"]) >= 3,
          "C4 margin rests on multiple sources (S1 + S3 packet + WB cell): %s"
          % (c4["source_ids"] if c4 else None))

    # conflict (cross-cell): headcount 1200 (Actuals) vs 1185 (Board hardcode)
    c2 = recs.get("C2")
    check(c2 and c2["review_status"] == "conflicting",
          "C2 headcount is conflicting (cross-cell 1200 vs 1185)")

    # THE TRUST PROPERTY — C3 unsupported despite the 0.10 hardcode in Board!B3
    c3 = recs.get("C3")
    check(c3 and c3["review_status"] == "unsupported" and not c3["supporting"],
          "C3 '10% YoY' stays UNSUPPORTED with zero supporting links")
    held = any("HARDCODE" in (cd.get("rationale") or "") or cd["confidence"] < ES.LINK_THRESHOLD
               for cd in (c3["candidates"] if c3 else []))
    check(c3 and c3["candidates"] and held,
          "C3's number IS found in a workbook cell but HELD as a candidate, not asserted "
          "(the hardcode is not laundered into a source)")
    check(c3 and "Board!B3" not in (c3["cells"] or []),
          "C3 lists NO backing cell — the held candidate never appears as evidence")

    # notes injected + round-trip
    out_deck = os.path.join(scratch, "deck-evidenced.pptx")
    packet_sources = {s["id"]: s for s in packet["sources"]}
    written, probs = EB.inject_notes(deck_path, emap["records"], out_deck, packet_sources)
    check(written >= 2 and not probs, "evidence notes injected into the deck (%d slides)" % written)
    from pptx import Presentation
    prs = Presentation(out_deck)
    note2 = prs.slides[1].notes_slide.notes_text_frame.text  # slide 2 (0-based idx 1)
    check(ES.note_is_wellformed(note2), "injected §2.1 note round-trips well-formed from the .pptx")
    check("none (unsupported" in Presentation(out_deck).slides[1].notes_slide.notes_text_frame.text
          or "S1" in note2, "the slide-2 note carries real source IDs")

    # NEGATIVE — a number, zero sources, mislabeled verified: schema refuses to source it
    planted = ES.evidence_record(9, "Margin hit 35%", supporting=[], conflict=None,
                                 declared_status="verified")
    check(planted["review_status"] == "unsupported",
          "NEGATIVE: a zero-source claim mislabeled 'verified' is forced to 'unsupported'")

    print("[EM-03] evidence-gap review")
    rep = EG.review_gaps(emap)
    top = rep["gaps_ranked"][0] if rep["gaps_ranked"] else {}
    check(top.get("type") == "unsupported-claim" and top.get("severity") == "must-fix",
          "the unsupported claim is the top-ranked must-fix gap")
    check(any(gp["type"] == "conflicting-sources" for gp in rep["gaps_ranked"]),
          "the conflict is surfaced as a gap")
    # the edit list validates against trust-review
    try:
        import review_contract as RC
        ok, eprobs = RC.validate_edit_list(rep["edit_list_for_trust_review"])
        check(ok, "the gap edit list validates against trust-review's review_contract: %s" % eprobs)
    except Exception as e:  # noqa: BLE001
        check(False, "could not validate edit list against review_contract: %s" % e)

    print("-" * 60)
    if _errs:
        print("FAIL evidence-map: %d check(s) failed" % len(_errs))
        return 1
    print("PASS evidence-map: every claim traced, conflicted, or explicitly unsupported — "
          "none silently sourced; notes injected; gaps ranked + fed to Trust Review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
