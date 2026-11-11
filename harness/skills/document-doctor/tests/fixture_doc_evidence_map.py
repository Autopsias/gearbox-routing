#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — DD-05 acceptance fixture: evidence map on the DOCUMENT surface
#
# THE GATE (plan DD-05): a forwarded document carries its OWN proof — every claim
# tied to its source(s) with a §1.6 doc locator, written WHERE THE DOCUMENT TRAVELS
# per format, never silently rewriting the author's prose:
#
#   * every claim is traced / conflicted / explicitly unsupported — NONE silently
#     sourced (the EM gate, now on the doc surface);
#   * a PLANTED unsourced number ⇒ unsupported, zero supporting, top must-fix gap;
#   * a packet-conflict claim ⇒ conflicting;
#   * a sub-threshold link is HELD for approval, never promoted (EM-04 discipline);
#   * the §2.1 evidence note round-trips well-formed;
#   * the OUTPUT CHANNEL behaves BY FORMAT (review #6, dd-08 PASS):
#       docx ⇒ anchored Word comments + an Evidence appendix in a -evidenced.docx
#       pdf  ⇒ sidecar ONLY (immutable — no -evidenced copy)
#       md   ⇒ sidecar + an APPENDED provenance block; the original prose intact
#       html ⇒ sidecar + an APPENDED <section>; the original prose intact
#   * EM-03's gap review is reused verbatim and its edit list validates against
#     trust-review's review_contract (doc-aware locators).
#
# Runs the REAL chain (document_map DD-01 → claim_audit DD-03 → doc_evidence DD-05)
# on self-made files. Self-contained; no network. Exit 0 = PASS, 1 = FAIL.
# =============================================================================
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

import document_map as dm        # noqa: E402  (DD-01)
import claim_audit as ca         # noqa: E402  (DD-03)
import evidence_schema as ES     # noqa: E402  (EM-01)
import doc_evidence as DE        # noqa: E402  (DD-05)
import evidence_gaps as EG       # noqa: E402  (EM-03 reused)
import review_contract as rc     # noqa: E402  (trust-review edit-list validator)

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
        print("  FAIL", msg)
    else:
        print("  ok  ", msg)


# ---------------------------------------------------------------------------
def seeded_packet():
    """A current source backing revenue; a conflict on headcount; the cloud-spend
    number is DELIBERATELY absent (so the document's claim is unsupported)."""
    return {
        "run_id": "dd05", "skill": "office-source-packet", "phase": "source-packet",
        "sources": [
            {"id": "S1", "name": "q2_actuals.csv", "source_status": "current",
             "date": "Q2-2026", "owner": "FP&A (close pack)"},
            {"id": "S2", "name": "hr_prelim.xlsx", "source_status": "superseded",
             "date": "Q2-2026 (prelim)", "owner": "HR"},
        ],
        "claims": [
            {"id": "C1", "class": "fact", "review_status": "verified", "metric": "revenue",
             "period": "Q2-2026", "value": "€48.2M",
             "provenance": {"claim": "Q2-2026 revenue = €48.2M",
                            "supporting": [{"source_id": "S1", "locator": "q2_actuals.csv#revenue",
                                            "confidence": 1.0, "rationale": "FP&A close-pack actual"}],
                            "conflict": None}},
            {"id": "C2", "class": "fact", "review_status": "conflicting", "metric": "headcount",
             "period": "Q2-2026", "value": "1,195",
             "provenance": {"claim": "Q2-2026 headcount = 1,195",
                            "supporting": [{"source_id": "S1", "locator": "q2_actuals.csv#headcount",
                                            "confidence": 1.0, "rationale": "FP&A final"}],
                            "conflict": {"with": ["S2"], "note": "S2 prelim 1,180 vs S1 final 1,195"}}},
        ],
        "conflicts": [], "open_questions": [],
    }


DOC_TEXT_LINES = [
    "Revenue reached €48.2M in the period, as of 30 June 2026.",
    "Headcount stood at 1,195 at quarter end.",
    "Cloud migration spend hit €9.9M this quarter.",
]


def build_md(work):
    p = os.path.join(work, "board.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# Board Update\n\n## Summary\n\n" + "\n\n".join(DOC_TEXT_LINES) + "\n")
    return p


def build_docx(work):
    import docx
    d = docx.Document()
    d.add_heading("Board Update", 0)
    d.add_heading("Summary", 1)
    for s in DOC_TEXT_LINES:
        d.add_paragraph(s)
    p = os.path.join(work, "board.docx")
    d.save(p)
    return p


def build_html(work):
    p = os.path.join(work, "board.html")
    body = "".join("<p>%s</p>" % s for s in DOC_TEXT_LINES)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("<html><body><h1>Board Update</h1><h2>Summary</h2>" + body + "</body></html>")
    return p


def audit_doc(doc_path, fmt, packet):
    m = dm.build_map(doc_path, fmt, 4000, 0.10)
    rep = ca.audit(m, packet)
    return {"format": rep["format"], "status": rep["status"], "claims": rep["claims"],
            "held_for_approval": rep.get("held_for_approval", []), "run_id": "dd05_" + fmt}


# ---------------------------------------------------------------------------
def main():
    work = tempfile.mkdtemp(prefix="otl_dd05_", dir=os.environ.get("TMPDIR", "/tmp"))
    packet = seeded_packet()
    packet_sources = {s["id"]: s for s in packet["sources"]}

    # ===================================================================
    # MD — the core evidence-map gate (real chain DD-01 → DD-03 → DD-05)
    # ===================================================================
    print("[DD-05] md — the doc evidence map (gate)")
    md = build_md(work)
    ca_md = audit_doc(md, "md", packet)
    emap = DE.build_doc_evidence_map(ca_md, packet=packet, run_id="dd05_md", fmt="md",
                                     document_path=md)
    recs = emap["records"]
    g = emap["gate"]

    check(g["every_claim_traced_conflicted_or_unsupported"],
          "GATE: every doc claim is traced, conflicted, or explicitly unsupported")
    check(g["none_silently_sourced"], "GATE: none silently sourced (all records pass §2 schema)")
    # every record carries format + the §1.6 doc locator
    check(all(r.get("format") == "md" and r.get("doc_locator") for r in recs),
          "every doc record carries format=md + a §1.6 doc_locator")
    # every record is a valid §2 record + a well-formed §2.1 note
    check(all(not ES.validate_record(r) for r in recs), "every record is a valid §2 record")
    check(all(ES.note_is_wellformed(ES.speaker_note(r, packet_sources)) for r in recs),
          "every record renders a well-formed §2.1 evidence note")

    def find(sub):
        return next((r for r in recs if sub in (r["claim"] or "")), None)

    planted = find("Cloud migration")
    conflicting = find("Headcount")
    traced = find("Revenue reached")
    check(planted is not None and planted["review_status"] == "unsupported"
          and not planted["supporting"],
          "PLANTED unsourced number ⇒ unsupported, zero supporting (none silently sourced)")
    check(conflicting is not None and conflicting["review_status"] == "conflicting"
          and conflicting["conflict"],
          "packet-conflict claim (headcount) ⇒ conflicting with a §2 conflict object")
    check(traced is not None and traced["supporting"]
          and traced["review_status"] in ("verified", "needs-review"),
          "traced claim (revenue) carries supporting from the matched packet claim")
    # the §2 supporting-link invariant holds (link confidence ≥ LINK_THRESHOLD)
    check(traced is not None and all(s["confidence"] >= ES.LINK_THRESHOLD for s in traced["supporting"]),
          "an asserted supporting link clears LINK_THRESHOLD (the §2 invariant)")

    # ----- per-format output CHANNEL: md = sidecar + appended provenance ------
    print("[DD-05] md — output channel (sidecar + appended provenance, prose intact)")
    info_md = DE.write_output_channel(emap, md, work, packet_sources)
    check(info_md["appendix_written"] and info_md["evidenced_copy"],
          "md: an evidenced copy with an appended provenance block is written")
    with open(md, encoding="utf-8") as fh:
        original = fh.read()
    with open(os.path.join(work, info_md["evidenced_copy"]), encoding="utf-8") as fh:
        evidenced = fh.read()
    check(evidenced.startswith(original.rstrip()),
          "md: the author's ORIGINAL prose is intact (appendix is APPENDED, never a rewrite)")
    check("Evidence appendix" in evidenced and "Cloud migration spend hit" in original,
          "md: the appendix carries the §2.1 notes after the original text")

    # ----- EM-03 gap review reused verbatim; edit list valid + doc-aware ------
    print("[DD-05] EM-03 gap review (reused) + edit-list validity")
    gaps = EG.review_gaps(emap)
    ok, eprobs = rc.validate_edit_list(gaps["edit_list_for_trust_review"])
    check(ok, "the doc gap edit list validates against trust-review's review_contract: %s" % eprobs)
    top = gaps["gaps_ranked"][0] if gaps["gaps_ranked"] else {}
    check(top.get("type") == "unsupported-claim" and top.get("severity") == "must-fix",
          "the unsupported claim is the top-ranked must-fix gap")
    check(all(("para:" in (gp["location"] or "") or "heading:" in (gp["location"] or ""))
              for gp in gaps["gaps_ranked"]),
          "gap locations use the §1.6 DOCUMENT locator grammar (not 'slide N')")

    # ===================================================================
    # DOCX — anchored Word comments + Evidence appendix (dd-08 PASS)
    # ===================================================================
    print("[DD-05] docx — anchored comments + Evidence appendix")
    docx_path = build_docx(work)
    ca_docx = audit_doc(docx_path, "docx", packet)
    emap_d = DE.build_doc_evidence_map(ca_docx, packet=packet, run_id="dd05_docx",
                                       fmt="docx", document_path=docx_path)
    outd = os.path.join(work, "emd")
    os.makedirs(outd, exist_ok=True)
    info_d = DE.write_output_channel(emap_d, docx_path, outd, packet_sources)
    check(info_d["comments_written"] >= 1 and info_d["appendix_written"]
          and info_d["evidenced_copy"],
          "docx: ≥1 anchored Word comment + an Evidence appendix written to a -evidenced.docx")
    check("anchored Word comments" in (info_d.get("channel") or ""),
          "docx: channel is 'anchored Word comments + Evidence appendix' (dd-08 PASS)")
    # the OOXML anchoring markers survive (the dd-08 contract)
    import zipfile
    z = zipfile.ZipFile(os.path.join(outd, info_d["evidenced_copy"]))
    docxml = z.read("word/document.xml").decode("utf-8", "replace")
    check("word/comments.xml" in z.namelist() and "commentRangeStart" in docxml
          and "commentReference" in docxml,
          "docx: comments.xml + commentRangeStart/commentReference present (anchored, not floating)")
    try:
        import docx as _docx
        reloaded = list(_docx.Document(os.path.join(outd, info_d["evidenced_copy"])).comments)
        appx = "\n".join(p.text for p in _docx.Document(os.path.join(outd, info_d["evidenced_copy"])).paragraphs)
        check(len(reloaded) >= 1, "docx: comments reload via python-docx")
        check("Evidence appendix" in appx, "docx: the Evidence appendix is present in the copy")
    except Exception as e:  # noqa: BLE001
        check(False, "docx: reload failed: %s" % e)

    # ===================================================================
    # HTML — sidecar + appended <section>, prose intact
    # ===================================================================
    print("[DD-05] html — sidecar + appended <section>, prose intact")
    html_path = build_html(work)
    ca_html = audit_doc(html_path, "html", packet)
    emap_h = DE.build_doc_evidence_map(ca_html, packet=packet, run_id="dd05_html",
                                       fmt="html", document_path=html_path)
    outh = os.path.join(work, "emh")
    os.makedirs(outh, exist_ok=True)
    info_h = DE.write_output_channel(emap_h, html_path, outh, packet_sources)
    with open(html_path, encoding="utf-8") as fh:
        html_orig = fh.read()
    with open(os.path.join(outh, info_h["evidenced_copy"]), encoding="utf-8") as fh:
        html_ev = fh.read()
    check(info_h["appendix_written"] and "trust-evidence-appendix" in html_ev,
          "html: an appended <section class='trust-evidence-appendix'> is written")
    check(html_orig.replace("</body></html>", "") in html_ev,
          "html: the author's ORIGINAL markup is intact (section is inserted, prose untouched)")

    # ===================================================================
    # PDF — sidecar ONLY (immutable)
    # ===================================================================
    print("[DD-05] pdf — sidecar only (immutable)")
    ca_pdf = dict(ca_md); ca_pdf["format"] = "pdf"
    emap_p = DE.build_doc_evidence_map(ca_pdf, packet=packet, run_id="dd05_pdf", fmt="pdf",
                                       document_path=md)
    outp = os.path.join(work, "emp")
    os.makedirs(outp, exist_ok=True)
    info_p = DE.write_output_channel(emap_p, md, outp, packet_sources)
    check(info_p["evidenced_copy"] is None and "sidecar-only" in (info_p.get("channel") or ""),
          "pdf: NO -evidenced copy — sidecar only (a fixed-layout PDF is immutable)")

    # ===================================================================
    # HELD — a sub-threshold link is never promoted to a supporting source
    # ===================================================================
    print("[DD-05] a sub-threshold link is HELD, never promoted")
    held_doc = os.path.join(work, "held.md")
    with open(held_doc, "w", encoding="utf-8") as fh:
        fh.write("# Note\n\nThe headcount narrative in the revenue review referenced "
                 "several FP&A workstreams.\n")
    ca_held = audit_doc(held_doc, "md", packet)
    emap_held = DE.build_doc_evidence_map(ca_held, packet=packet, run_id="dd05_held",
                                          fmt="md", document_path=held_doc)
    check(all((not r.get("held_for_approval")) or (r["review_status"] == "unsupported")
              for r in emap_held["records"]),
          "a candidate-only claim stays unsupported — the held link is NOT promoted to supporting")
    check(all(not ES.validate_record(r) for r in emap_held["records"]),
          "held-link records still pass the §2 schema (no sub-threshold link in supporting)")

    # ===================================================================
    # BLOCKED — a DD-07 scanned-PDF block short-circuits (no fabricated evidence)
    # ===================================================================
    print("[DD-05] a blocked claim audit short-circuits (no fabricated evidence)")
    emap_b = DE.build_doc_evidence_map(
        {"status": "blocked", "status_reason": "scanned PDF, no OCR", "format": "pdf", "claims": []},
        packet=packet, run_id="dd05_blocked", fmt="pdf")
    check(emap_b["status"] == "blocked" and emap_b["records"] == [],
          "a blocked claim audit ⇒ blocked evidence map, zero records (no evidence fabricated)")

    print("-" * 64)
    if _errs:
        print("FAIL fixture_doc_evidence_map: %d check(s) failed" % len(_errs))
        return 1
    print("PASS DD-05: the document carries its own proof — every claim traced/conflicted/"
          "unsupported, none silently sourced; the §2.1 note travels per format (docx anchored "
          "comments + appendix · pdf sidecar-only · md/html appended section) with the author's "
          "prose intact; held links never promoted; EM-03 gaps valid against review_contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
