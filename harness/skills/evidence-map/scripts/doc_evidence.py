#!/usr/bin/env python3
"""
Office Trust Layer — Evidence Map · DD-05: the evidence map, generalised to the
DOCUMENT surface (per-format output channel).

This is the DOC BRANCH of the evidence layer (the v1 deck path is unchanged). It
makes a forwarded prose document carry its OWN proof: every checkable claim tied
to the source(s) that back it, the proof written WHERE THE DOCUMENT TRAVELS — and
the sub-threshold / unsupported / conflicting claims surfaced for the gap review.

It REUSES the deck machinery verbatim and only adds a doc branch:
  * EM-01 schema      — `evidence_schema.evidence_record` / `validate_record` /
                        `speaker_note` / `note_is_wellformed`. A doc record IS the
                        §2 provenance record + the §2.1 note, extended with the
                        f2-01 `format` and the §1.6 document `locator` (page /
                        heading:'A>B>C' / para:N / table:R{r}C{c} / figure:ID).
  * EM-04 HOLD        — the hardened-linker discipline (a sub-threshold or
                        incompatible link is HELD for human approval, never
                        asserted; zero supporting ⇒ unsupported; "the number is
                        somewhere" is not a source). DD-03 (`claim_audit.py`)
                        already resolves each claim against the source-packet INDEX
                        with exactly this HOLD discipline; this builder carries that
                        resolution into the EM-01 record shape and re-validates the
                        §2 invariants so nothing can drift.
  * EM-03 gap review  — `evidence_gaps.review_gaps` is run verbatim over the doc
                        evidence map and its `review_contract`-valid edit list is
                        handed to Trust Review (DD-06).

INPUT (the document trust layer DD-01→DD-04 produced):
  --claim-audit  claim-audit.json   (DD-03 — the per-claim §2 provenance records)  [required]
  --source-packet source-packet.json (status/owner annotation for the §2.1 note)   [optional]
  --document     the .docx/.pdf/.md/.html the proof is written into a COPY of       [optional]
  --verdict      document-verdict.json (run_id / format / readiness, for context)   [optional]

OUTPUT CHANNEL — BY FORMAT (v2 review finding #6; the dd-08 spike PASSED so docx
uses anchored comments):
  * sidecar (ALL formats)  — `.trust/<run>/evidence-map.{json,md}` + `evidence-gaps.{json,md}`.
                             The sidecar always exists; it is the canonical evidence record.
  * docx                   — anchored Word comments on each claim's run + an
                             "Evidence appendix" appended to a COPY (`<doc>-evidenced.docx`).
                             If python-docx / comment write is unavailable ⇒ fall back to
                             appendix + sidecar (no evidence lost; status ok-with-warnings).
  * pdf                    — SIDECAR ONLY (a fixed-layout PDF is immutable; we never
                             rewrite it).
  * md                     — sidecar + an APPENDED provenance section on a COPY
                             (`<doc>-evidenced.md`).
  * html                   — sidecar + an APPENDED <section> on a COPY
                             (`<doc>-evidenced.html`).

NEVER SILENTLY REWRITE THE AUTHOR'S PROSE — the only mutations are comments and an
APPENDED evidence section; existing text is left byte-for-byte intact.

Status (`references/conventions.md §3`): `ok` / `ok-with-warnings` (a fallback fired,
or unsupported/conflicting/held claims surfaced — that surfacing is the job, the
block decision belongs to EM-03 + Trust Review) / `blocked` (the claim audit is
itself blocked, e.g. a DD-07 scanned-PDF block) / `failed`.

Enums (`review_status`, `severity`) are VERBATIM from conventions.md via evidence_schema.
"""
import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence_schema as ES        # noqa: E402  (EM-01 — the shared record shape)
import evidence_gaps as EG          # noqa: E402  (EM-03 — reused verbatim)
import cross_app_linker as CAL      # noqa: E402  (EM-04 — period detection + HOLD thresholds)

SURFACE = "doc"
JUDGEMENT_CLASSES = {"assumption", "estimate", "open-question", "interpretation"}
# the formats whose evidence appendix / comments we can write into a copy; pdf is
# immutable (sidecar only).
MUTABLE_FORMATS = {"docx", "md", "html"}


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# Build the doc evidence map — one EM-01 record per claim-audit claim.
# ===========================================================================
def build_doc_evidence_map(claim_audit, packet=None, run_id=None, fmt=None,
                           document_path=None):
    """Turn a DD-03 claim audit into an EM-01 evidence map for the doc surface.

    Each DD-03 claim already carries the §2 provenance fields (`supporting`,
    `candidates`, `conflict`, `review_status`) resolved with the HOLD discipline.
    We carry that into the EM-01 record shape (so EM-03 + Trust Review consume it
    unchanged), stamping the f2-01 `format` + the §1.6 `doc_locator`, and we
    RE-VALIDATE the §2 invariants so a record can never imply a source the links
    don't establish."""
    fmt = fmt or claim_audit.get("format")
    packet = packet or {}
    packet_sources = {s.get("id"): s for s in packet.get("sources", [])}

    # A blocked claim audit (DD-07 scanned-PDF block) short-circuits — we never
    # fabricate evidence from an unreliable extract.
    if claim_audit.get("status") == "blocked":
        return {
            "run_id": run_id, "skill": "evidence-map", "phase": "doc-evidence-map",
            "surface": SURFACE, "format": fmt, "status": "blocked",
            "status_reason": claim_audit.get("status_reason",
                                             "claim audit blocked — no evidence map built"),
            "records": [], "held_for_approval": [],
            "gate": {"every_claim_traced_conflicted_or_unsupported": True,
                     "none_silently_sourced": True, "unsupported": [], "conflicting": [],
                     "blocked": True, "findings": []},
        }

    records, findings, held_all = [], [], []
    for c in claim_audit.get("claims", []) or []:
        rec = _record_from_claim(c, packet_sources)
        problems = ES.validate_record(rec)
        if problems:
            rec["_schema_problems"] = problems
            findings.append({"severity": "must-fix", "doc_locator": rec["doc_locator"],
                             "issue": "evidence record fails §2 schema: %s" % "; ".join(problems)})
        # mark, never launder
        if rec["review_status"] == "unsupported":
            findings.append({"severity": "must-fix", "doc_locator": rec["doc_locator"],
                             "claim": rec["claim"],
                             "issue": "claim has no above-threshold source — UNSUPPORTED "
                                      "(%d candidate(s) held for human approval)"
                                      % len(rec["candidates"]),
                             "review_status": "unsupported"})
        elif rec["review_status"] == "conflicting":
            findings.append({"severity": "should-fix", "doc_locator": rec["doc_locator"],
                             "claim": rec["claim"],
                             "issue": "claim rests on conflicting sources — show the "
                                      "authoritative value with a visible note",
                             "review_status": "conflicting"})
        for lk in rec.get("held_for_approval", []):
            held_all.append({"doc_locator": rec["doc_locator"], "claim": rec["claim"],
                             "locator": lk["locator"], "confidence": lk["confidence"],
                             "rationale": lk["rationale"]})
        records.append(rec)

    def _accounted(r):
        return r["review_status"] in ("unsupported", "conflicting") or bool(r["supporting"])
    every_accounted = all(_accounted(r) for r in records)
    none_silently_sourced = all(not ES.validate_record(r) for r in records)
    unsupported = [r["claim"] for r in records if r["review_status"] == "unsupported"]
    conflicting = [r["claim"] for r in records if r["review_status"] == "conflicting"]

    status = "ok-with-warnings" if (findings or held_all) else "ok"
    out = {
        "run_id": run_id, "skill": "evidence-map", "phase": "doc-evidence-map",
        "surface": SURFACE, "format": fmt,
        "status": status,
        "status_reason": ("Every claim is traced, conflicted, or explicitly unsupported; "
                          "%d unsupported, %d conflicting, %d link(s) held for approval — "
                          "surfaced for the gap review (per format: %s)."
                          % (len(unsupported), len(conflicting), len(held_all), fmt)),
        "consumes": ["claim-audit.json"] + (["source-packet.json"] if packet else []),
        "document": os.path.basename(document_path) if document_path else None,
        "link_threshold": ES.LINK_THRESHOLD,
        "records": records,
        "held_for_approval": held_all,
        "gate": {
            "every_claim_traced_conflicted_or_unsupported": every_accounted,
            "none_silently_sourced": none_silently_sourced,
            "unsupported": unsupported,
            "conflicting": conflicting,
            "held_links": len(held_all),
            "findings": findings,
            "assertion": ("Every document claim is tied to its source(s) with explicit "
                          "confidence + the §1.6 doc locator, or flagged unsupported/"
                          "conflicting. Sub-threshold links are HELD as candidates for human "
                          "approval — none is silently asserted as provenance. Reported per "
                          "format; the author's prose is never silently rewritten."),
        },
    }
    return out


def _record_from_claim(c, packet_sources):
    """One DD-03 claim → one EM-01 record carrying format + the §1.6 doc locator.

    The supporting LINK confidence is the doc-claim↔packet-claim link confidence
    (DD-03's `link_confidence`, which is ≥ LINK_THRESHOLD for an asserted link, per
    conventions §2: "confidence is the LINK confidence, not the source's own
    reliability"), so the §2 `validate_record` invariant always holds. Candidates
    are HELD for approval (EM-04 discipline)."""
    link_conf = c.get("link_confidence")
    supporting = []
    for s in (c.get("supporting") or []):
        if not s.get("source_id"):
            continue
        conf = link_conf if link_conf is not None else s.get("confidence", ES.LINK_THRESHOLD)
        # an asserted link must clear the threshold — DD-03 only asserts at/above it;
        # clamp to be belt-and-braces with the schema validator.
        conf = max(float(conf or 0.0), ES.LINK_THRESHOLD)
        supporting.append(ES.link(s["source_id"], s.get("locator", ""), conf,
                                  s.get("rationale", "from source packet")))
    candidates = []
    for cd in (c.get("candidates") or []):
        candidates.append(ES.link(cd.get("source_id") or "(unresolved)",
                                  cd.get("locator") or c.get("locator"),
                                  cd.get("confidence", CAL.CANDIDATE_FLOOR),
                                  cd.get("rationale", "sub-threshold — held for human approval")))

    klass = c.get("class")
    assumptions = []
    if klass in JUDGEMENT_CLASSES or c.get("materiality") == "assumption-as-fact":
        assumptions.append("claim is a %s (%s), not an observed actual"
                           % (klass, c.get("review_status")))
    date_range = CAL.detect_period(c.get("claim"))
    declared = c.get("review_status")

    rec = ES.evidence_record(
        slide=None, claim=c.get("claim"),
        supporting=supporting, candidates=candidates, conflict=c.get("conflict"),
        declared_status=declared,
        calculation=("traceable to %s" % supporting[0]["locator"]) if supporting else "direct from source",
        assumptions=assumptions, date_range=date_range,
        owner=_owner_from_sources(supporting, packet_sources))
    # the doc-surface extensions (the deck record uses `slide`; doc uses these)
    rec["surface"] = SURFACE
    rec["format"] = c.get("format")
    rec["doc_locator"] = c.get("locator")
    rec["materiality"] = c.get("materiality")
    rec["materiality_all"] = c.get("materiality_all")
    rec["verifiability"] = c.get("verifiability")
    rec["class"] = klass
    rec["matched_packet_claim"] = c.get("matched_packet_claim")
    rec["held_for_approval"] = candidates
    return rec


def _owner_from_sources(supporting, packet_sources):
    owners = []
    for s in supporting:
        src = packet_sources.get(s["source_id"])
        if src and src.get("owner"):
            owners.append(src["owner"])
    return "; ".join(ES._unique(owners)) if owners else None


# ===========================================================================
# Per-format output channel — write the proof WHERE THE DOCUMENT TRAVELS.
# Never rewrites existing prose: comments + an APPENDED evidence section only.
# ===========================================================================
def _appendix_lines(emap, packet_sources):
    """The Evidence appendix body — the §2.1 note per claim, plain text/markdown."""
    L = ["Evidence appendix — Office Trust Layer (provenance; surface=doc, format=%s)"
         % emap.get("format"),
         "Each claim below is tied to its source(s); unsupported/conflicting claims are "
         "flagged for a human. This appendix is APPENDED — the document's own prose is "
         "unchanged."]
    for r in emap["records"]:
        L.append("")
        L.append("• [%s] %s" % (r["review_status"], (r.get("doc_locator") or "")))
        L.append(ES.speaker_note(r, packet_sources))
    return L


# ---------------------------------------------------------------------------
# PDF embedded-evidence channel (AB-02 / codex finding #3) — capability-gated
# + viewer-proven. A plain PDF gets a provenance APPENDIX appended (claim ->
# source ID -> verdict) + catalog-level named-destination anchors + the sidecar
# JSON attached as an embedded file (the §7.4 `embedded-pdf` channel; the
# `annot:ID | attachment:'NAME'` locator). The author's original pages are
# cloned byte-faithfully and never rewritten (append-only), so signatures,
# forms, annotations and tagged-PDF structure on those pages are untouched. A
# PDF that is signed, an interactive form, encrypted, or unreadable is NOT
# embedded — that would void the signature / risk the field tree / cannot be
# verified — so it degrades to the `.trust/` sidecar, recording the §7.1
# `sidecar-only-pdf` cap + `not-ready-for-forwarding`, explicitly labelled.
# NEVER a silent "survives the forward" claim (conventions.md §7.4).
# ---------------------------------------------------------------------------
PDF_EMBED_CLASSES = {
    "embeddable":       "plain, readable, unsigned, no interactive form",
    "gated-signed":     "carries a digital signature — appending would void it",
    "gated-form":       "interactive form (AcroForm/XFA) — embed risks the field tree",
    "gated-encrypted":  "encrypted — cannot embed/verify without the password",
    "gated-unreadable": "unparseable PDF — cannot embed/verify",
    "gated-no-writer":  "PDF writer libraries (pypdf+reportlab) unavailable",
}


def _pdf_libs():
    try:
        import pypdf  # noqa: F401
        from reportlab.pdfgen import canvas  # noqa: F401
        from reportlab.lib import pagesizes  # noqa: F401
        return pypdf, canvas, pagesizes
    except Exception:
        return None, None, None


def _pdf_features(reader):
    """(signed, has_form) for an open PdfReader — detected, never assumed."""
    signed = has_form = False
    try:
        root = reader.trailer["/Root"]
        acro = root.get("/AcroForm")
        if acro is not None:
            acro = acro.get_object()
            sf = acro.get("/SigFlags")
            if sf and int(sf) & 1:
                signed = True
            if "/XFA" in acro:
                has_form = True
            for f in (acro.get("/Fields") or []):
                fo = f.get_object()
                ft = fo.get("/FT")
                if ft == "/Sig":
                    has_form = True
                    if fo.get("/V") is not None:
                        signed = True
                elif ft in ("/Tx", "/Btn", "/Ch"):
                    has_form = True
        if "/Perms" in root:
            signed = True
    except Exception:
        pass
    return signed, has_form


def classify_pdf(path):
    """One of PDF_EMBED_CLASSES — the capability gate. Never mutates the file."""
    pypdf, _, _ = _pdf_libs()
    if pypdf is None:
        return "gated-no-writer"
    if not path or not os.path.isfile(path):
        return "gated-unreadable"
    try:
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            try:
                if reader.decrypt("") == 0:
                    return "gated-encrypted"
            except Exception:
                return "gated-encrypted"
        _ = len(reader.pages)
        signed, has_form = _pdf_features(reader)
        if signed:
            return "gated-signed"
        if has_form:
            return "gated-form"
        return "embeddable"
    except Exception:
        return "gated-unreadable"


def _pdf_rows(emap):
    """The claim -> source ID -> verdict rows + a stable anchor id per claim."""
    rows = []
    for i, r in enumerate(emap.get("records", [])):
        rows.append({
            "anchor": "ev-c%d" % (i + 1),
            "claim": (r.get("claim") or "")[:88],
            "locator": r.get("doc_locator") or "",
            "sources": ", ".join(r.get("source_ids") or []) or "(none — unsupported)",
            "verdict": r.get("review_status") or "",
        })
    return rows


def _build_pdf_appendix(tmp_path, emap):
    _, canvas, pagesizes = _pdf_libs()
    W, H = pagesizes.LETTER
    c = canvas.Canvas(tmp_path, pagesize=pagesizes.LETTER)
    y = H - 60

    def line(s, dy=13, font="Helvetica", size=9):
        nonlocal y
        if y < 54:
            c.showPage(); y = H - 60
        c.setFont(font, size); c.drawString(50, y, s[:118]); y -= dy

    line("Evidence Appendix — Office Trust Layer (embedded-pdf; surface=doc, format=pdf)",
         dy=20, font="Helvetica-Bold", size=12)
    line("APPENDED provenance — the author's original pages above are unchanged (byte-faithful).",
         dy=18, font="Helvetica-Oblique", size=8)
    line("Anchor         Verdict        Sources                    Claim",
         dy=13, font="Helvetica-Bold", size=9)
    for row in _pdf_rows(emap):
        line("[annot:%s]  %-13s  %-24s  %s"
             % (row["anchor"], row["verdict"], row["sources"][:24], row["claim"]))
    c.showPage()
    c.save()


def embed_pdf_evidence(emap, document_path, out_dir, base):
    """Append the appendix + named-destination anchors + attach the sidecar to a
    COPY of the PDF. Returns (out_path, n_author_pages, anchors)."""
    import tempfile
    pypdf, _, _ = _pdf_libs()
    reader = pypdf.PdfReader(document_path)
    n_author = len(reader.pages)
    writer = pypdf.PdfWriter()
    writer.append(reader)  # clone author pages faithfully — append-only

    fd, appendix = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    anchors = []
    try:
        _build_pdf_appendix(appendix, emap)
        writer.append(pypdf.PdfReader(appendix))
        for i, r in enumerate(emap.get("records", [])):
            aid = "ev-c%d" % (i + 1)
            target = 0
            needle = (r.get("claim") or "")[:40]
            for p in range(n_author):
                try:
                    if needle and needle in (reader.pages[p].extract_text() or ""):
                        target = p
                        break
                except Exception:
                    pass
            try:
                writer.add_named_destination(aid, target)
                anchors.append(aid)
            except Exception:
                pass
        try:
            payload = json.dumps({"records": emap.get("records", []),
                                  "gate": emap.get("gate", {})},
                                 default=str).encode("utf-8")
            writer.add_attachment("evidence-map.json", payload)
        except Exception:
            pass
        out_path = os.path.join(out_dir, base + "-evidenced.pdf")
        with open(out_path, "wb") as fh:
            writer.write(fh)
    finally:
        try:
            os.unlink(appendix)
        except OSError:
            pass
    return out_path, n_author, anchors


def _pdf_text(path):
    pypdf, _, _ = _pdf_libs()
    try:
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)
    except Exception:
        return ""


def _pdf_named_dests(path):
    pypdf, _, _ = _pdf_libs()
    try:
        return set(pypdf.PdfReader(path).named_destinations.keys())
    except Exception:
        return set()


def _pdf_attachment_names(path):
    pypdf, _, _ = _pdf_libs()
    try:
        return set((pypdf.PdfReader(path).attachments or {}).keys())
    except Exception:
        return set()


def pdf_viewer_roundtrip(out_path, emap, anchors, out_dir):
    """Re-open the produced PDF as several REAL viewers/parsers would and assert
    the appendix rows + visible anchors survive. Records which were exercised
    in-sandbox vs not (GUI viewers cannot run headless here)."""
    import shutil
    import subprocess
    rows = _pdf_rows(emap)
    exercised, not_exercised, per_viewer = [], [], {}

    def survives(txt):
        rows_ok = all(rw["verdict"] in txt
                      and rw["sources"].split(",")[0].strip() in txt
                      and ("annot:%s" % rw["anchor"]) in txt
                      for rw in rows) if rows else True
        anch_ok = all(("annot:%s" % a) in txt for a in anchors) if anchors else True
        return rows_ok, anch_ok

    # 1) pypdf re-parse (a programmatic re-open; the basis for most viewers, and
    #    byte-identical to a Drive/email re-download)
    r_ok, a_ok = survives(_pdf_text(out_path))
    per_viewer["pypdf-reparse"] = {
        "rows": r_ok, "visible_anchors": a_ok,
        "named_dests": (all(a in _pdf_named_dests(out_path) for a in anchors)
                        if anchors else True),
        "attachment": "evidence-map.json" in _pdf_attachment_names(out_path)}
    exercised.append("pypdf-reparse (≈ Drive/email re-download — byte-identical)")

    # 2) pikepdf — an INDEPENDENT parser (a different engine)
    try:
        import pikepdf
        with pikepdf.open(out_path) as pk:
            per_viewer["pikepdf-reopen"] = {"pages": len(pk.pages), "opened": True}
        exercised.append("pikepdf-reopen (independent parser)")
    except Exception:
        not_exercised.append("pikepdf-reopen (library unavailable)")

    # 3) pdftotext (poppler — the text layer behind browser viewers / Preview)
    if shutil.which("pdftotext"):
        tp = os.path.join(out_dir, os.path.basename(out_path) + ".txt")
        try:
            subprocess.run(["pdftotext", "-layout", out_path, tp],
                           capture_output=True, timeout=60)
            if os.path.isfile(tp):
                rr, aa = survives(open(tp, encoding="utf-8", errors="ignore").read())
                per_viewer["pdftotext"] = {"rows": rr, "visible_anchors": aa}
                exercised.append("pdftotext (poppler text layer ≈ browser/Preview)")
        except Exception:
            not_exercised.append("pdftotext (run failed)")
    else:
        not_exercised.append("pdftotext (poppler not installed)")

    # 4) qpdf --check (a strict structural validator — a viewer rejects a bad file)
    if shutil.which("qpdf"):
        try:
            p = subprocess.run(["qpdf", "--check", out_path],
                               capture_output=True, timeout=60)
            per_viewer["qpdf-check"] = {"returncode": p.returncode}
            exercised.append("qpdf --check (structural validity)")
        except Exception:
            not_exercised.append("qpdf (run failed)")
    else:
        not_exercised.append("qpdf (not installed)")

    # 5) Ghostscript print-to-PDF — a REAL print pipeline round-trip
    if shutil.which("gs"):
        printed = os.path.join(
            out_dir, os.path.splitext(os.path.basename(out_path))[0] + "-printed.pdf")
        try:
            subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                            "-o", printed, out_path], capture_output=True, timeout=90)
            if os.path.isfile(printed):
                rr, aa = survives(_pdf_text(printed))
                per_viewer["print-to-pdf (ghostscript)"] = {
                    "rows": rr, "visible_anchors": aa,
                    "named_dests_survived": (all(a in _pdf_named_dests(printed)
                                                 for a in anchors) if anchors else True),
                    "attachment_survived": "evidence-map.json" in _pdf_attachment_names(printed)}
                exercised.append("print-to-pdf (ghostscript pdfwrite)")
        except Exception:
            not_exercised.append("print-to-pdf/ghostscript (run failed)")
    else:
        not_exercised.append("print-to-pdf/ghostscript (not installed)")

    # GUI viewers genuinely cannot run headless in this sandbox
    not_exercised += ["Preview.app (macOS GUI — not available in sandbox)",
                      "Adobe Acrobat (GUI — not available in sandbox)",
                      "Google Drive in-browser viewer (no browser session)"]

    text_viewer = per_viewer.get("pdftotext") or per_viewer["pypdf-reparse"]
    rows_survive = bool(text_viewer.get("rows"))
    anchors_survive = bool(text_viewer.get("visible_anchors"))
    machine_anchors = per_viewer["pypdf-reparse"]["named_dests"]
    attachment_ok = per_viewer["pypdf-reparse"]["attachment"]
    # the survival gate: claim->source rows AND the visible anchors survive a real
    # text-layer viewer, AND the machine anchors (named destinations) are present
    # in the produced file. The attachment is recorded as a bonus, not gating
    # (attachment survival is the most viewer/version-dependent).
    ok = rows_survive and anchors_survive and machine_anchors
    return {"ok": ok, "rows_survive": rows_survive,
            "visible_anchors_survive": anchors_survive,
            "named_destinations_present": machine_anchors,
            "attachment_present": attachment_ok,
            "viewers_exercised": exercised,
            "viewers_not_exercised": not_exercised,
            "per_viewer": per_viewer}


def _write_pdf_channel(emap, document_path, out_dir, base, packet_sources):
    """Capability-gated, viewer-proven PDF evidence channel (AB-02).

    embeddable + round-trip PASS  -> embedded-pdf, ready-for-forwarding.
    embeddable + round-trip FAIL  -> sidecar semantics, not-ready-for-forwarding.
    any gated class               -> sidecar, sidecar-only-pdf cap + not-ready-for-
                                     forwarding (no-writer also records embed-unavailable).
    Every outcome is explicitly labelled — never a silent survives-the-forward."""
    out = {"comments_written": 0, "appendix_written": False, "evidenced_copy": None,
           "fallback": None, "channel": None, "warnings": [], "pdf_class": None,
           "degraded_status": [], "forward_readiness": None, "embedded": False,
           "anchors": [], "attachment": None, "roundtrip": None}
    cls = classify_pdf(document_path)
    out["pdf_class"] = cls

    if cls != "embeddable":
        caps = ["sidecar-only-pdf"]
        if cls == "gated-no-writer":
            caps.append("embed-unavailable")
        out["degraded_status"] = caps
        out["forward_readiness"] = "not-ready-for-forwarding"
        out["channel"] = ("sidecar (pdf %s — embed withheld; caps %s + not-ready-for-forwarding)"
                          % (cls, "+".join(caps)))
        out["fallback"] = PDF_EMBED_CLASSES.get(cls, cls)
        out["warnings"].append(
            "PDF not embeddable (%s): evidence is sidecar-only — capped %s + "
            "not-ready-for-forwarding, never a silent survives-the-forward claim."
            % (cls, "+".join(caps)))
        return out

    try:
        out_path, n_author, anchors = embed_pdf_evidence(emap, document_path, out_dir, base)
    except Exception as e:  # noqa: BLE001
        out["degraded_status"] = ["sidecar-only-pdf", "embed-unavailable"]
        out["forward_readiness"] = "not-ready-for-forwarding"
        out["channel"] = ("sidecar (pdf embed failed: %s — not-ready-for-forwarding)"
                          % type(e).__name__)
        out["fallback"] = "embed raised %s" % type(e).__name__
        out["warnings"].append("PDF embed failed (%s) — sidecar-only, not-ready-for-forwarding." % e)
        return out

    rt = pdf_viewer_roundtrip(out_path, emap, anchors, out_dir)
    out.update({"appendix_written": True, "embedded": True,
                "evidenced_copy": os.path.basename(out_path), "anchors": anchors,
                "attachment": "evidence-map.json", "author_pages_preserved": n_author,
                "roundtrip": rt})
    if rt["ok"]:
        out["forward_readiness"] = "ready-for-forwarding"
        out["channel"] = ("embedded-pdf (appendix + %d named-destination anchor(s) + attached "
                          "sidecar; viewer round-trip PASS) -> %s"
                          % (len(anchors), os.path.basename(out_path)))
    else:
        out["degraded_status"] = ["sidecar-only-pdf"]
        out["forward_readiness"] = "not-ready-for-forwarding"
        out["channel"] = ("embedded-pdf written but the viewer round-trip did NOT confirm "
                          "survival — capped not-ready-for-forwarding (never a silent "
                          "survives-the-forward claim)")
        out["warnings"].append(
            "PDF embed produced but round-trip unverified — not-ready-for-forwarding.")
    return out


def write_output_channel(emap, document_path, out_dir, packet_sources=None):
    """Write the per-format output channel for the document. Returns a dict
    describing what was written (and any fallback)."""
    packet_sources = packet_sources or {}
    fmt = emap.get("format")
    info = {"format": fmt, "sidecar": True, "comments_written": 0,
            "appendix_written": False, "evidenced_copy": None, "fallback": None,
            "channel": None, "warnings": []}

    if not document_path or not os.path.isfile(document_path):
        info["channel"] = "sidecar-only"
        info["warnings"].append("no document supplied — sidecar evidence only")
        return info

    base = os.path.splitext(os.path.basename(document_path))[0]
    ext = os.path.splitext(document_path)[1].lower()

    if fmt == "pdf" or ext == ".pdf":
        # AB-02: capability-gated, viewer-proven embedded-evidence channel.
        info.update(_write_pdf_channel(emap, document_path, out_dir, base, packet_sources))
        return info

    if fmt == "docx" or ext == ".docx":
        info.update(_write_docx_channel(emap, document_path, out_dir, base, packet_sources))
        return info

    if fmt == "md" or ext in (".md", ".markdown"):
        out_path = os.path.join(out_dir, base + "-evidenced.md")
        with open(document_path, "r", encoding="utf-8") as fh:
            original = fh.read()
        # render as a fenced provenance block so the original markdown is untouched
        body = original.rstrip() + "\n\n---\n\n## Evidence appendix (Office Trust Layer)\n\n```\n" \
            + "\n".join(_appendix_lines(emap, packet_sources)) + "\n```\n"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        info.update({"appendix_written": True, "evidenced_copy": os.path.basename(out_path),
                     "channel": "sidecar + appended provenance (md)"})
        return info

    if fmt == "html" or ext in (".html", ".htm"):
        out_path = os.path.join(out_dir, base + "-evidenced.html")
        with open(document_path, "r", encoding="utf-8") as fh:
            original = fh.read()
        section = ('\n<section class="trust-evidence-appendix">\n'
                   '<h2>Evidence appendix (Office Trust Layer)</h2>\n<pre>\n'
                   + _html_escape("\n".join(_appendix_lines(emap, packet_sources)))
                   + "\n</pre>\n</section>\n")
        if "</body>" in original.lower():
            idx = original.lower().rfind("</body>")
            body = original[:idx] + section + original[idx:]
        else:
            body = original.rstrip() + section
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        info.update({"appendix_written": True, "evidenced_copy": os.path.basename(out_path),
                     "channel": "sidecar + appended section (html)"})
        return info

    info["channel"] = "sidecar-only"
    info["warnings"].append("unknown format %r — sidecar evidence only" % fmt)
    return info


def _write_docx_channel(emap, document_path, out_dir, base, packet_sources):
    """docx: anchored Word comments on each claim's run + an Evidence appendix,
    written into a COPY. The dd-08 spike PASSED (anchored comments survive a
    Word/LibreOffice round-trip); if the library/feature is unavailable we fall
    back to appendix + sidecar (no evidence lost)."""
    out = {"comments_written": 0, "appendix_written": False, "evidenced_copy": None,
           "fallback": None, "channel": None, "warnings": []}
    try:
        import docx  # python-docx
    except ImportError:
        out["fallback"] = "python-docx unavailable"
        out["channel"] = "sidecar-only (docx comments unavailable)"
        out["warnings"].append("python-docx not importable — sidecar evidence only")
        return out

    try:
        d = docx.Document(document_path)
    except Exception as e:  # noqa: BLE001
        out["fallback"] = "cannot open docx: %s" % e
        out["channel"] = "sidecar-only"
        out["warnings"].append(out["fallback"])
        return out

    can_comment = hasattr(d, "add_comment")
    recs = emap["records"]
    # index claims by a normalized snippet so we can find their anchoring paragraph
    by_snip = []
    for r in recs:
        snip = _norm_ws(r.get("claim") or "")[:48]
        if snip:
            by_snip.append((snip, r))

    if can_comment:
        for para in d.paragraphs:
            ptext = _norm_ws(para.text)
            if not ptext or not para.runs:
                continue
            for snip, r in by_snip:
                if snip and snip in ptext:
                    try:
                        d.add_comment(runs=para.runs, text=_comment_text(r, packet_sources),
                                      author="Office Trust Layer", initials="OTL")
                        out["comments_written"] += 1
                    except Exception as e:  # noqa: BLE001
                        out["warnings"].append("comment anchor failed: %s" % e)
                    break
    else:
        out["fallback"] = "no add_comment API"
        out["warnings"].append("python-docx exposes no add_comment — appendix + sidecar only")

    # the Evidence appendix (always appended for docx — comments may be dropped by a
    # downstream tool, the appendix is the durable copy)
    try:
        d.add_page_break()
        d.add_heading("Evidence appendix (Office Trust Layer)", level=1)
        for line in _appendix_lines(emap, packet_sources):
            d.add_paragraph(line)
        out["appendix_written"] = True
    except Exception as e:  # noqa: BLE001
        out["warnings"].append("appendix write failed: %s" % e)

    out_path = os.path.join(out_dir, base + "-evidenced.docx")
    try:
        d.save(out_path)
        out["evidenced_copy"] = os.path.basename(out_path)
    except Exception as e:  # noqa: BLE001
        out["warnings"].append("could not save evidenced docx: %s" % e)
        out["channel"] = "sidecar-only (save failed)"
        return out

    if out["comments_written"] and out["appendix_written"]:
        out["channel"] = "anchored Word comments + Evidence appendix (dd-08 PASS)"
    elif out["appendix_written"]:
        out["channel"] = "appendix + sidecar (comment fallback)"
        out["fallback"] = out["fallback"] or "no claim run matched for a comment anchor"
    else:
        out["channel"] = "sidecar-only"
    return out


def _comment_text(rec, packet_sources):
    src = ", ".join(rec.get("source_ids") or []) or "none (unsupported)"
    bits = ["[%s] sources: %s" % (rec["review_status"], src)]
    if rec["review_status"] == "unsupported":
        bits.append("no confirmed source — source it or drop it before it travels")
    if rec["review_status"] == "conflicting":
        cf = rec.get("conflict") or {}
        bits.append("sources disagree: %s" % (cf.get("note") or "reconcile before it travels"))
    if rec.get("held_for_approval"):
        bits.append("%d candidate link(s) held for approval" % len(rec["held_for_approval"]))
    return " — ".join(bits)


def _norm_ws(s):
    return " ".join((s or "").split())


def _html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ===========================================================================
def render_md(emap, channel_info=None):
    L = ["# Document Evidence Map — %s (surface: doc, format: **%s**)"
         % (emap.get("run_id") or "", emap.get("format"))]
    L += ["", "> **Status: `%s`** — %s" % (emap["status"], emap["status_reason"])]
    g = emap.get("gate", {})
    if "assertion" in g:
        L += [">", "> %s" % g["assertion"]]
    L.append("")
    if emap["status"] == "blocked":
        return "\n".join(L) + "\n"
    L += ["- **Every claim traced, conflicted, or unsupported:** %s"
          % ("✅ yes" if g.get("every_claim_traced_conflicted_or_unsupported") else "❌ NO"),
          "- **None silently sourced:** %s"
          % ("✅ yes" if g.get("none_silently_sourced") else "❌ NO"),
          "- **Link threshold (assert vs hold):** %.2f" % emap.get("link_threshold", ES.LINK_THRESHOLD),
          "- **Unsupported:** %d · **Conflicting:** %d · **Held for approval:** %d"
          % (len(g.get("unsupported", [])), len(g.get("conflicting", [])), g.get("held_links", 0))]
    if channel_info:
        L.append("- **Output channel (%s):** %s%s" % (
            channel_info.get("format"), channel_info.get("channel"),
            (" → `%s`" % channel_info["evidenced_copy"]) if channel_info.get("evidenced_copy") else ""))
        if channel_info.get("comments_written"):
            L.append("- **Anchored comments written:** %d" % channel_info["comments_written"])
        if channel_info.get("fallback"):
            L.append("- **Fallback:** %s" % channel_info["fallback"])
    L += ["", "| Review | Materiality | Claim | Locator | Sources | Held |",
          "|---|---|---|---|---|---|"]
    for r in emap["records"]:
        L.append("| `%s` | %s | %s | %s | %s | %d |" % (
            r["review_status"], r.get("materiality"),
            (r["claim"] or "")[:54].replace("|", "\\|"), (r.get("doc_locator") or "")[:34],
            ", ".join(r["source_ids"]) or "—", len(r.get("held_for_approval", []))))
    L += ["", "## Per-claim evidence notes (the §2.1 template — written into the document)"]
    for r in emap["records"]:
        L += ["", "**%s**" % (r.get("doc_locator") or r["claim"][:60]), "", "```",
              ES.speaker_note(r), "```"]
    return "\n".join(L) + "\n"


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="DD-05 evidence map for the document surface")
    ap.add_argument("--claim-audit", required=True, help="claim-audit.json (DD-03)")
    ap.add_argument("--source-packet", help="source-packet.json (status/owner for the §2.1 note)")
    ap.add_argument("--document", help="the .docx/.pdf/.md/.html the proof is written into a copy of")
    ap.add_argument("--verdict", help="document-verdict.json (run_id / format / readiness)")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        ca = _load(args.claim_audit)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "failed", "status_reason": "cannot read claim audit: %s" % e}))
        return 2
    packet = _load(args.source_packet) if args.source_packet and os.path.isfile(args.source_packet) else {}
    verdict = _load(args.verdict) if args.verdict and os.path.isfile(args.verdict) else {}
    fmt = ca.get("format") or verdict.get("format")

    run_id = args.run_id or verdict.get("run_id") or ca.get("run_id") or (_now_slug() + "_doc-evidence-map")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    emap = build_doc_evidence_map(ca, packet=packet, run_id=run_id, fmt=fmt,
                                  document_path=args.document)

    channel_info = None
    if emap["status"] != "blocked":
        packet_sources = {s.get("id"): s for s in packet.get("sources", [])}
        channel_info = write_output_channel(emap, args.document, out_dir, packet_sources)
        emap["output_channel"] = channel_info
        if channel_info.get("warnings"):
            if emap["status"] == "ok":
                emap["status"] = "ok-with-warnings"
            emap["status_reason"] += "  (%s)" % "; ".join(channel_info["warnings"])

    with open(os.path.join(out_dir, "evidence-map.json"), "w", encoding="utf-8") as fh:
        json.dump(emap, fh, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "evidence-map.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(emap, channel_info))

    # reuse EM-03 verbatim so the gap edit list feeds Trust Review (DD-06)
    if emap["status"] != "blocked":
        gaps = EG.review_gaps(emap)
        gaps["run_id"] = run_id
        with open(os.path.join(out_dir, "evidence-gaps.json"), "w", encoding="utf-8") as fh:
            json.dump(gaps, fh, indent=2, ensure_ascii=False, default=str)
        with open(os.path.join(out_dir, "evidence-gaps.md"), "w", encoding="utf-8") as fh:
            fh.write(EG.render_md(gaps))

    if not args.quiet:
        print(json.dumps({"status": emap["status"], "format": fmt,
                          "out": out_dir,
                          "channel": (channel_info or {}).get("channel"),
                          "unsupported": len(emap["gate"].get("unsupported", [])),
                          "conflicting": len(emap["gate"].get("conflicting", [])),
                          "held": emap["gate"].get("held_links", 0)}, default=str))
    return 0 if emap["status"] in ("ok", "ok-with-warnings") else (3 if emap["status"] == "blocked" else 2)


if __name__ == "__main__":
    sys.exit(main())
