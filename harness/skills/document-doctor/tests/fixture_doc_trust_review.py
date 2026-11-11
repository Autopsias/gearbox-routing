#!/usr/bin/env python3
"""
DD-06 acceptance fixture — Trust Review on the DOCUMENT surface (surface="doc").

THE GATE (plan DD-06): the SAME two-context hostile loop that reviews a workbook
(M1) or a deck (M2) reviews a DOCUMENT — carrying the format, running the doc
Pretty-But-Wrong checklist, catching a repair-introduced regression a naive rule
would ship, and ending at the human gate. The convergence / independence / model-
split / §4-waiver / language-pass machinery is UNCHANGED across surfaces.

It runs the REAL chain end to end on a self-made document —
   source packet → document-doctor (map → landmines → claim audit → verdict)
                 → doc evidence map + gaps (DD-05)
                 → trust-review loop (DD-06, surface="doc")
— so the loop is driven by a real `review_contract`-valid edit list, then a builder
plants a regression to prove the SAFE rule continues where the naive rule stops.

Asserts:
  A. surface="doc" + the document format are CARRIED into the (hashed) per-round
     reviewer prompt + recorded on every round and the result (review #6).
  B. the seven doc Pretty-But-Wrong rows are present in checklist_text("doc"),
     alongside the v1 both-surface items (§1.7).
  C. the loop CATCHES a repair-induced regression (naive_stop True / safe_stop
     False at the regression round), then converges to `ready`.
  D. the HUMAN GATE behaves: an open doc must-fix ⇒ not-ready; a valid §4
     Board/Regulatory waiver ⇒ ready-with-limitations (the limitation travels);
     a waiver missing named-owner signoff ⇒ stays not-ready.

Exit 0 = PASS, 1 = FAIL. Wired into run_fixtures.sh.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DD = os.path.join(PLUGIN_ROOT, "skills", "document-doctor", "scripts")
EM = os.path.join(PLUGIN_ROOT, "skills", "evidence-map", "scripts")
TR = os.path.join(PLUGIN_ROOT, "skills", "trust-review", "scripts")
for p in (DD, EM, TR):
    sys.path.insert(0, p)

import document_map as dm        # noqa: E402
import claim_audit as ca         # noqa: E402
import prose_landmines as pl     # noqa: E402
import assessment as asmt        # noqa: E402
import doc_evidence as DE        # noqa: E402
import evidence_gaps as EG       # noqa: E402
import review_contract as rc     # noqa: E402
import loop_controller as lc     # noqa: E402

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


# ---------------------------------------------------------------------------
# A real document + packet, run through the whole document trust path.
# ---------------------------------------------------------------------------
def seeded_packet():
    return {
        "run_id": "dd06", "sources": [
            {"id": "S1", "name": "q2_actuals.csv", "source_status": "current",
             "date": "Q2-2026", "owner": "FP&A"}],
        "claims": [
            {"id": "C1", "class": "fact", "review_status": "verified", "metric": "revenue",
             "period": "Q2-2026", "value": "€48.2M",
             "provenance": {"claim": "Q2-2026 revenue = €48.2M",
                            "supporting": [{"source_id": "S1", "locator": "q2_actuals.csv#revenue",
                                            "confidence": 1.0, "rationale": "FP&A close-pack actual"}],
                            "conflict": None}}],
        "conflicts": [], "open_questions": [],
    }


def build_doc(work):
    p = os.path.join(work, "board.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# Board Update\n\n## Summary\n\n"
                 "Revenue reached €48.2M in the period, as of 30 June 2026.\n\n"
                 "Cloud migration spend hit €9.9M this quarter.\n")   # PLANTED unsourced number
    return p


def run_doc_chain(work, packet):
    """source packet → DD-01 map → DD-02 landmines → DD-03 claim audit → DD-04 verdict
    → DD-05 doc evidence map + gaps. Returns (verdict, gaps, out_dir)."""
    out = os.path.join(work, "chain")
    os.makedirs(out, exist_ok=True)
    doc = build_doc(work)
    pk = os.path.join(out, "source-packet.json")
    json.dump(packet, open(pk, "w"))
    m = dm.build_map(doc, "md", 4000, 0.10)
    json.dump(m, open(os.path.join(out, "document-map.json"), "w"), default=str)
    lm = {"findings": pl.scan_paragraphs(m, _date(), 365) + pl.scan_figures(m)}
    json.dump(lm, open(os.path.join(out, "prose-landmines.json"), "w"), default=str)
    rep = ca.audit(m, packet)
    ca_doc = {"format": rep["format"], "status": rep["status"], "claims": rep["claims"],
              "held_for_approval": rep.get("held_for_approval", []), "run_id": "dd06"}
    json.dump(ca_doc, open(os.path.join(out, "claim-audit.json"), "w"), default=str)
    verdict = asmt.run(os.path.join(out, "document-map.json"), out,
                       landmines_path=os.path.join(out, "prose-landmines.json"),
                       claim_audit_path=os.path.join(out, "claim-audit.json"),
                       source_packet=None, run_id="dd06", travel_class="Board")
    emap = DE.build_doc_evidence_map(ca_doc, packet=packet, run_id="dd06", fmt="md",
                                     document_path=doc)
    gaps = EG.review_gaps(emap)
    return verdict, gaps, out


def _date():
    import datetime
    return datetime.date(2026, 5, 31)


# ---------------------------------------------------------------------------
# A doc-surface defect set (doc Pretty-But-Wrong vocabulary). sig = "location|type".
# ---------------------------------------------------------------------------
DEFECTS = {
    "D1": ("must-fix", "para:2", "unsupported-claim"),                  # the real planted gap
    "REG": ("must-fix", "table:R3C2", "untraceable-figure-or-table"),  # the planted regression
    "D3": ("must-fix", "page:2", "extraction-loss"),
}


def _edit_for(dkey):
    sev, loc, typ = DEFECTS[dkey]
    return {"id": "R-%s" % dkey, "location": loc, "type": typ, "severity": sev,
            "evidence": "seeded doc defect %s" % dkey,
            "suggested_repair": "repair %s" % dkey,
            "human_review": (typ in ("unsupported-claim",)), "status": "open"}


def _sig(dkey):
    sev, loc, typ = DEFECTS[dkey]
    return "%s|%s" % (loc, typ)


def make_reviewer():
    def _r(state, provenance, k):
        return [_edit_for(d) for d in sorted(state["defects"])]
    return _r


def builder_regression_then_fix(state, open_edits, k):
    defects = set(state["defects"])
    claimed = set()
    for e in open_edits:
        claimed.add(e["sig"])
        for d in DEFECTS:
            if _sig(d) == e["sig"]:
                defects.discard(d)
    if k == 1:
        defects.add("REG")     # the regression: fixing D1 introduced an untraceable table
    return {"defects": defects}, claimed


def doc_manifest(travel="board-regulatory"):
    return {
        "manifest_version": "1", "run_id": "dd06", "created": "2026-05-31T00:00:00Z",
        "artifact": {"path": "board.md", "sha256": "0" * 64, "kind": "document"},
        "travel_class": {"effective": travel, "escalation_signals": ["financial-metric"]},
        "sensitivity": {"run_max": "confidential",
                        "sources": [{"source_id": "S1", "sensitivity": "confidential",
                                     "redaction": "excerpt"}]},
        "routing": {"policy": "two-context hostile review"},
    }


def run():
    work = tempfile.mkdtemp(prefix="otl_dd06_", dir=os.environ.get("TMPDIR", "/tmp"))

    # ======================================================================
    # PART 0 — the REAL chain produces a valid edit list for the loop
    # ======================================================================
    packet = seeded_packet()
    verdict, gaps, chain_out = run_doc_chain(work, packet)
    ok, eprobs = rc.validate_edit_list(gaps["edit_list_for_trust_review"])
    check(ok, "the real doc chain's gap edit list validates against review_contract: %s" % eprobs)
    check(verdict["readiness"] == "not-ready" and verdict["surface"] == "doc",
          "the document verdict is not-ready (planted unsourced number) on surface=doc")
    check(any(g["type"] == "unsupported-claim" and g["severity"] == "must-fix"
              for g in gaps["gaps_ranked"]),
          "the chain surfaces the planted unsupported claim as a must-fix gap (seeds the loop)")

    # ======================================================================
    # PART B — doc Pretty-But-Wrong rows present (§1.7)
    # ======================================================================
    cl = rc.checklist_text("doc")
    for did in ("DOC-PBW-01", "DOC-PBW-02", "DOC-PBW-03", "DOC-PBW-04",
                "DOC-PBW-05", "DOC-PBW-06", "DOC-PBW-07"):
        check(did in cl, "doc checklist carries %s" % did)
    check("PBW-01" in cl and "PBW-06" in cl,
          "the v1 both-surface items also travel on the doc checklist (§1.7)")

    # ======================================================================
    # PART A + C — the loop on surface="doc": regression caught, then ready
    # ======================================================================
    out1 = os.path.join(work, "loop1")
    provenance = verdict                          # the document verdict is the provenance
    artifact = os.path.join(work, "board.md")
    r1 = lc.run_loop(
        reviewer=make_reviewer(), builder=builder_regression_then_fix,
        initial_state={"defects": {"D1"}}, provenance=provenance,
        out_dir=out1, manifest=doc_manifest(), max_rounds=5,
        run_id="2026-05-31T_dd06-doc", artifact_path=artifact, provenance_path=None,
        surface="doc", fmt="md")

    check(r1["status"] in ("ok", "ok-with-warnings") and r1["readiness"] == "ready",
          "doc loop converges to ready once the regression is fixed; got %r/%r"
          % (r1["status"], r1["readiness"]))
    check(r1.get("surface") == "doc" and r1.get("format") == "md",
          "the result records surface=doc + format=md (per-format reporting, review #6)")
    check(len(r1["regression_caught_in_rounds"]) >= 1,
          "THE GATE: the doc loop catches ≥1 repair-induced regression; caught in %s"
          % r1["regression_caught_in_rounds"])
    reg_round = r1["regression_caught_in_rounds"][0]
    with open(os.path.join(out1, "review-round-%d.json" % reg_round)) as fh:
        rr = json.load(fh)
    check(rr["naive_stop"] is True and rr["safe_stop"] is False,
          "at the regression round a NAIVE verify-only rule would SHIP; the SAFE rule continues "
          "(naive=%s safe=%s)" % (rr["naive_stop"], rr["safe_stop"]))
    check(rr.get("surface") == "doc" and rr.get("format") == "md",
          "each round log records surface=doc + format")

    # A — the format is carried into the (hashed) per-round reviewer prompt
    with open(os.path.join(out1, "manifest.json")) as fh:
        man = json.load(fh)
    expect_prompt = rc.build_reviewer_prompt(
        provenance_summary=lc._provenance_summary(provenance),
        artifact_label=artifact, checklist=rc.checklist_text("doc"),
        surface="doc", fmt="md")
    check("format: md" in expect_prompt and "surface: doc" in expect_prompt
          and "DOC-PBW-01" in expect_prompt,
          "the doc reviewer prompt carries the format + the doc Pretty-But-Wrong checklist")
    check(man["rounds"] and man["rounds"][0]["prompt_hashes"]["reviewer_prompt_sha256"]
          == rc.sha256_text(expect_prompt),
          "the loop's per-round hashed reviewer prompt IS the doc+format prompt (carried, provable)")
    # the model split is unchanged on the doc surface
    check(r1["routing"]["builder_model"]["name"] == "Opus"
          and r1["routing"]["reviewer_model"]["name"] == "Sonnet",
          "the Opus/Sonnet two-context split is unchanged on surface=doc")

    # ======================================================================
    # PART D — the HUMAN GATE behaves on the doc surface
    # ======================================================================
    # (i) an open doc must-fix, no resolution -> not-ready
    g_open = lc.run_loop(
        reviewer=make_reviewer(), builder=None, initial_state={"defects": {"D1"}},
        provenance=provenance, out_dir=os.path.join(work, "gate_open"),
        manifest=doc_manifest(), max_rounds=1, run_id="gate-open",
        surface="doc", fmt="md")
    check(g_open["readiness"] == "not-ready",
          "an open doc must-fix with no resolution ⇒ not-ready; got %r" % g_open["readiness"])

    # (ii) a valid Board/Regulatory waiver (signoff approved) -> ready-with-limitations
    resolutions_ok = {"resolutions": [
        {"finding_id": "R-D1", "lifecycle": "waived-by-human",
         "waiver": {"owner": "Alex Example", "decision": "accept residual risk",
                    "due_date": "2026-06-30", "signoff": "approved",
                    "rationale": "figure confirmed verbally; sourcing to follow."}}]}
    g_waived = lc.run_loop(
        reviewer=make_reviewer(), builder=None, initial_state={"defects": {"D1"}},
        provenance=provenance, out_dir=os.path.join(work, "gate_waived"),
        manifest=doc_manifest(), max_rounds=1, run_id="gate-waived",
        resolutions=resolutions_ok, surface="doc", fmt="md")
    check(g_waived["readiness"] == "ready-with-limitations",
          "a valid §4 Board/Regulatory waiver on a doc must-fix ⇒ ready-with-limitations; got %r"
          % g_waived["readiness"])
    check(g_waived["limitations"] and any("LIMITATION" in lim for lim in g_waived["limitations"]),
          "the waived limitation TRAVELS with the document (limitation text recorded)")

    # (iii) a Board/Regulatory waiver MISSING signoff -> stays not-ready (gate holds)
    resolutions_bad = {"resolutions": [
        {"finding_id": "R-D1", "lifecycle": "waived-by-human",
         "waiver": {"owner": "Alex Example", "decision": "accept residual risk",
                    "due_date": "2026-06-30", "signoff": "pending",
                    "rationale": "awaiting CFO signoff."}}]}
    g_bad = lc.run_loop(
        reviewer=make_reviewer(), builder=None, initial_state={"defects": {"D1"}},
        provenance=provenance, out_dir=os.path.join(work, "gate_bad"),
        manifest=doc_manifest(), max_rounds=1, run_id="gate-bad",
        resolutions=resolutions_bad, surface="doc", fmt="md")
    check(g_bad["readiness"] == "not-ready" and g_bad["human_gate"]["problems"],
          "a Board/Regulatory waiver with no named-owner signoff ⇒ stays not-ready (§4.3 holds)")

    return work


if __name__ == "__main__":
    run()
    if _errs:
        print("FAIL — DD-06 doc trust-review:")
        for e in _errs:
            print("  -", e)
        sys.exit(1)
    print("PASS — DD-06: the two-context loop reviews a DOCUMENT (surface=doc) — the real "
          "source-packet→document-doctor→evidence-map chain feeds a valid edit list; the format "
          "+ the doc Pretty-But-Wrong checklist are carried in the hashed reviewer prompt and "
          "recorded per round; the SAFE loop catches a repair-induced regression a naive rule "
          "would ship, then converges to ready; the human gate holds (open⇒not-ready, valid "
          "Board/Regulatory waiver⇒ready-with-limitations + limitation travels, missing "
          "signoff⇒not-ready). Model split + independence manifest unchanged.")
    sys.exit(0)
