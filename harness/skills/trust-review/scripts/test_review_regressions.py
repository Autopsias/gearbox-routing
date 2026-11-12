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
