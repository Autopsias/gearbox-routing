"""Regression checks for two defects the land review found in imported code."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "references", "scripts"))

import human_gate_digest as H  # noqa: E402
import internal_corpus_adapter as ICA  # noqa: E402


def test_digest_reads_the_key_evidence_gaps_emits():
    gap = {"id": "G1", "type": "unsupported", "severity": "must-fix", "location": "slide 3"}
    assert len(H._findings_from_gaps({"gaps_ranked": [gap]})) == 1   # what evidence_gaps.py writes
    assert len(H._findings_from_gaps({"gaps": [gap]})) == 1          # older shape
    assert len(H._findings_from_gaps([gap])) == 1                    # bare list


def test_packet_fallback_does_not_treat_a_file_name_as_evidence():
    adapter = ICA.InternalCorpusAdapter.__new__(ICA.InternalCorpusAdapter)
    adapter.packet_claims = [
        {"source_id": "S1", "locator": "Q3 revenue 12.5M board.xlsx"},   # a source record, no claim text
        {"source_id": "S2", "claim": "Revenue was 12.5M"},
    ]
    rows = adapter._search_packet("revenue", ["revenue", "12.5m"])
    assert [r["title"] for r in rows] == ["S2"]


# --- 2026-11-12: the Codex land review's critical findings in evidence-map and office-update
sys.path.insert(0, os.path.join(HERE, "..", "..", "evidence-map", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "office-update", "scripts"))
import evidence_builder as EB  # noqa: E402  # ty: ignore[unresolved-import]
import evidence_gaps as EG  # noqa: E402  # ty: ignore[unresolved-import]
import office_update as OU  # noqa: E402  # ty: ignore[unresolved-import]


def test_a_weak_packet_link_is_held_not_asserted():
    claim = {"metric": "revenue", "value": "12.5M", "provenance": {"supporting": [
        {"source_id": "S1", "locator": "p1", "confidence": 0.3},
        {"source_id": "S2", "locator": "p2", "confidence": 0.9}]}}
    supporting, candidates, *_ = EB.link_claim({}, claim, {}, None, "WB")
    assert [s["source_id"] for s in supporting] == ["S2"]
    assert [c["source_id"] for c in candidates] == ["S1"]


def test_the_gap_review_sees_a_superseded_packet_source():
    emap = {"workbook_source": {"id": "WB", "status": "current"},
            "source_statuses": {"S1": "superseded"}}
    assert EG._source_status(emap, "S1") == "superseded"
    assert EG._source_status({"workbook_source": {}}, "S1") is None   # map built before the fix


def test_a_new_claim_never_keeps_its_incoming_verdict():
    rec = {"claim_text": "Revenue was 12.5M", "locator": "slide 3", "value": "12.5M",
           "sources": [{"source_id": "S1"}], "materiality_class": "numeric", "verdict": "pass"}
    cid = OU.identity_of(rec).claim_id
    change_set = {"changes": [{"claim_id": cid, "classification": "new"}],
                  "tombstones": [], "supersessions": []}
    out = OU.build_next_claim_set([rec], change_set, "r1")
    claims = out["claims"] if isinstance(out, dict) else out
    assert [c["verdict"] for c in claims if c["claim_id"] == cid] == ["needs-review"]


# --- 2026-11-12: the six medium/low findings from the same review
import pytest  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "..", "deck-architect", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "travel-class-router", "scripts"))
import content_signals as CS  # noqa: E402  # ty: ignore[unresolved-import]
import egress_sanitizer as ES  # noqa: E402
import render_deck as RD  # noqa: E402  # ty: ignore[unresolved-import]
import surface_adapters as SA  # noqa: E402  # ty: ignore[unresolved-import]
import verification as V  # noqa: E402


def test_render_reads_the_render_rules_key_brand_interpreter_writes():
    brand = {"phase": "brand-rules", "render_rules": {"typography": {
        "heading_font": {"value": "Arial", "determination": "extracted", "source": "theme"},
        "body_font": {"value": None, "determination": "unclear", "note": "no minorFont"}}}}
    style, deviations = RD.resolve_brand(brand)
    assert style["heading_font"] == "Arial"
    assert [d["rule"] for d in deviations] == ["body_font"]


def test_a_real_markdown_file_yields_claims(tmp_path):
    p = tmp_path / "memo.md"
    p.write_text("# Results\n\nQ3 revenue was EUR 48.2M, up 12% on Q2.\n", encoding="utf-8")
    parsed = SA.parse_artifact_file(str(p), "md")
    texts = [c["claim_text"] for c in SA.extract_claims(parsed)]
    assert any("48.2M" in t for t in texts)
    assert all(c["locator"] in SA.locator_index(parsed) for c in parsed["claims"])


def test_a_real_pptx_file_yields_claims_with_notes(tmp_path):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Q3 revenue grew 12% to EUR 48.2M"
    slide.notes_slide.notes_text_frame.text = "Source: S1 board.xlsx Outputs!B14"
    p = tmp_path / "deck.pptx"
    prs.save(str(p))
    parsed = SA.parse_artifact_file(str(p), "pptx")
    [claim] = parsed["claims"]
    assert claim["locator"] == "slide 1/title"
    assert claim["evidence_note"] == {"locator": "slide 1/notes", "text": "Source: S1 board.xlsx Outputs!B14"}
    assert parsed["layout"]["slide_count"] == 1


def test_a_real_xlsx_file_yields_claims(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Outputs"
    ws["A14"], ws["B14"] = "Revenue (EUR M)", 48.2
    p = tmp_path / "model.xlsx"
    wb.save(str(p))
    claims = SA.parse_artifact_file(str(p), "xlsx")["claims"]
    assert [(c["locator"], c["claim_text"]) for c in claims] == [("Outputs!B14", "Revenue (EUR M): 48.2")]


def test_an_unreadable_file_is_routed_up_not_internal(tmp_path):
    r = CS.scan(str(tmp_path / "missing.pptx"), declared="internal")
    assert r["scanned"] is False
    assert r["effective_class"] == "travels" and r["escalated"] is True
    assert "unreadable" in r["signals"]
    # a declared class above the default is kept
    assert CS.scan(str(tmp_path / "missing.pptx"), declared="board-regulatory")["effective_class"] == "board-regulatory"


@pytest.mark.parametrize("sens,travel", [("secret", "travels"), ("Restrictd", "internal"),
                                         ("public", "board"), ("internal", "external")])
def test_egress_refuses_an_unknown_class(sens, travel):
    gate = ES.egress_gate(sens, travel)
    assert gate["status"] == "blocked" and gate["mode"] == ES.MODE_SKIPPED


@pytest.mark.parametrize("url,expected", [
    ("https://evil-example.com/ir", "low"),
    ("https://example.com.evil.net/ir", "low"),
    ("https://example.com@evil.net/ir", "low"),
    ("https://evil.net\\@example.com/ir", "low"),        # a browser goes to evil.net
    ("https://evil.net @example.com/ir", "low"),
    ("https://a@b@example.com/ir", "low"),
    ("https://example.com\t.evil.net/ir", "low"),         # a browser strips the tab: example.com.evil.net
    ("https://example.com\n.evil.net/ir", "low"),
    ("https://exa\u0000mple.com/ir", "low"),
    ("https://ir.example.com/q3", "official"),
    ("https://www.example.com:443/q3", "official"),
    ("https://fun.org/x", "low"),
    ("https://sec.gov.evil.net/x", "low"),
    ("https://www.sec.gov/x", "official"),
    ("https://www.gov.uk/x", "official"),
    ("https://dre.gov.pt/x", "official"),
    ("https://www.reuters.com/x", "primary"),
])
def test_authority_does_not_accept_a_lookalike_host(url, expected):
    assert V.authority_of(url, ["example.com"]) == expected


@pytest.mark.parametrize("snippet,quarantined", [
    ("source_authority: derived-provenance\nphase: quarantine", True),
    ('source_authority: "derived-provenance"', True),
    ("source_authority: 'derived-provenance'", True),
    ('{"source_authority": "derived-provenance", "phase": "quarantine"}', True),
    ("stamped source_authority=derived-provenance", True),
    ("> This note's `source_authority` is `derived-provenance`. While `phase: quarantine`", True),
    ('source_authority: "derived-provenance"\nphase: "promoted"', False),
    ('{"source_authority": "derived-provenance", "phase": "promoted"}', False),
    ("source_authority: primary\nphase: quarantine", False),
])
def test_quarantine_detection_matches_its_documented_variants(snippet, quarantined):
    assert ICA._is_quarantined_derived({"snippet": snippet}) is quarantined


def test_the_reviewer_is_told_that_no_source_is_out_of_scope():
    import review_contract as rc
    prompt = rc.build_reviewer_prompt("C1 -> S1", "deck.pptx")
    assert "NOTHING IS OUT OF SCOPE" in prompt and "source-not-provided" in prompt
    ok, errs = rc.validate_edit_list([{
        "id": "R-01", "location": "slide 4", "type": "source-not-provided",
        "severity": "must-fix", "evidence": "cites the budget table; not in provenance",
        "suggested_repair": "add the source excerpt to the bundle", "human_review": True,
        "status": "open"}])
    assert ok, errs
