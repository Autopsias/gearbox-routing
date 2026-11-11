#!/usr/bin/env python3
# NOTE: the plugin's consolidated benchmark harness is not part of this tree (removed 2026-11-11:
# it needed files that are not here).
# =============================================================================
# Office Trust Layer — Document Doctor fixtures (dd-01 + dd-07 + dd-02)
#
# Builds a TINY self-made file of EACH format under fixtures/document_doctor/,
# plus the two hard cases the build plan calls out:
#   * a scanned / image-only PDF that MUST block (dd-07 finding #5)
#   * an HTML file with hidden text + a nav + an embedded SVG + agent-only DOM
#     (dd-01 finding #11)
# then runs document_map.py + prose_landmines.py and asserts the contract:
#
#   dd-01  every record stamped surface=doc + format; ordered sections (heading
#          path), paragraphs, tables (cell grids), figures, footnotes, stable
#          §1.6 locators; HTML visible/semantic/raw modes + hidden / agent-only
#          scoped separately (in_scope=False) + rendered-vs-source divergence.
#   dd-07  PDF text coverage / image-only count / table confidence / OCR / locator
#          fidelity; image-only + no OCR ⇒ blocked / not-ready + DOC-PBW-01 +
#          human-review; OCR present ⇒ engine + page provenance recorded.
#   dd-02  prose landmines: undated-number, stale-date, table-as-image,
#          broken-cross-ref, numeric-token-candidate, unattributed-quote — each
#          with surface+format+locator+severity+evidence.
#
# Exit 0 only if every assertion holds. Self-contained; no network.
# =============================================================================
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "document-doctor", "scripts")
FX = os.path.join(HERE, "document_doctor")
sys.path.insert(0, SCRIPTS)

import document_map as dm          # noqa: E402
import prose_landmines as pl       # noqa: E402

PASS = 0
FAIL = 0
FAILS = []


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
# fixture builders
# ---------------------------------------------------------------------------


def build_md():
    p = os.path.join(FX, "tiny.md")
    text = (
        "# Quarterly Note\n\n"
        "## Revenue\n\n"
        "Revenue reached 4.2M in the period and margin held at 18%.\n\n"
        "The figures are current as of 12/03/2019.\n\n"
        "See Table 9 for the full breakdown.\n\n"
        '"This was the strongest quarter in the company history by a wide margin" '
        "remained the headline.\n\n"
        "| Metric | Q3 | Q4 |\n|---|---|---|\n| Revenue | 4.0 | 4.2 |\n| Margin | 17% | 18% |\n\n"
        "Net income rose to 1.1M on the year.\n\n"
        "[^1]: Source: internal management accounts.\n"
    )
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def build_docx():
    import docx

    p = os.path.join(FX, "tiny.docx")
    d = docx.Document()
    d.add_heading("Board Memo", level=1)
    d.add_heading("Summary", level=2)
    d.add_paragraph("Revenue grew to 4.2M in FY25, up from 3.9M in FY24.")
    d.add_paragraph("This margin expansion was driven by mix.")
    t = d.add_table(rows=3, cols=3)
    cells = [["Metric", "FY24", "FY25"], ["Revenue", "3.9", "4.2"], ["Margin", "16%", "18%"]]
    for r in range(3):
        for c in range(3):
            t.cell(r, c).text = cells[r][c]
    d.save(p)
    return p


def build_pdf_text():
    """A text-based PDF WITH a ruled table (pdfplumber should extract it)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    p = os.path.join(FX, "tiny_text.pdf")
    doc = SimpleDocTemplate(p, pagesize=A4)
    styles = getSampleStyleSheet()
    flow = [
        Paragraph("Board Memo — FY25", styles["Title"]),
        Paragraph("Revenue grew to 4.2M in FY25, up from 3.9M in FY24, "
                  "with margin reaching 18%.", styles["Normal"]),
        Spacer(1, 12),
        Table([["Metric", "FY24", "FY25"], ["Revenue", "3.9", "4.2"], ["Margin", "16%", "18%"]],
              style=TableStyle([
                  ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                  ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
              ])),
    ]
    doc.build(flow)
    return p


def build_pdf_scanned():
    """An image-only PDF: text rendered to a raster, then wrapped — no text layer."""
    from PIL import Image, ImageDraw

    img = os.path.join(FX, "_scan_page.png")
    out = os.path.join(FX, "scanned.pdf")
    im = Image.new("RGB", (1240, 1754), "white")
    dr = ImageDraw.Draw(im)
    for i, line in enumerate([
        "CONFIDENTIAL — BOARD PACK (scanned)",
        "Revenue 4.2M   Margin 18%",
        "This page is an image; there is no selectable text layer.",
        "A reader cannot extract or re-check any number here.",
    ]):
        dr.text((80, 120 + i * 90), line, fill="black")
    # a fake ruled 'table' drawn as pixels (invisible to text/table extractors)
    for x in range(80, 700, 120):
        dr.line([(x, 600), (x, 900)], fill="black", width=2)
    for y in range(600, 901, 100):
        dr.line([(80, y), (680, y)], fill="black", width=2)
    im.save(img)

    import img2pdf

    with open(out, "wb") as fh:
        fh.write(img2pdf.convert(img))
    return out


def build_html():
    p = os.path.join(FX, "tiny.html")
    html = """<!doctype html><html><head><title>Doc</title>
<style>.gone{display:none}</style></head>
<body>
<nav><a href="#a">Home</a> | <a href="#b">Revenue</a> | <a href="#c">Risks</a></nav>
<h1>Investor Update</h1>
<h2>Revenue</h2>
<p>Revenue grew to 4.2M in FY25, up from 3.9M.</p>
<p class="gone">HIDDENONLY confidentialfigure9999 not in the visible render.</p>
<p hidden>HIDDENATTR secrettoken8888 also suppressed.</p>
<table><tr><th>Metric</th><th>FY24</th><th>FY25</th></tr>
<tr><td>Revenue</td><td>3.9</td><td>4.2</td></tr></table>
<svg width="120" height="80"><title>Revenue chart</title><rect width="100" height="60"/></svg>
<details class="agent-spec"><summary>agent spec</summary>
<p>AGENTONLY agentsecretzzz — machine-only guidance, not for the reader.</p></details>
<p role="doc-footnote" id="fn1">Source: management accounts.</p>
</body></html>"""
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(html)
    return p


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------


def every_record_stamped(doc, fmt):
    """Every emitted structural record carries surface=doc + the right format."""
    ok = True
    for key in ("sections", "paragraphs", "tables", "figures", "footnotes"):
        for rec in doc.get(key, []):
            if rec.get("surface") != "doc" or rec.get("format") != fmt:
                ok = False
    return ok


def assert_md(path):
    print("\n[md] %s" % os.path.basename(path))
    doc = dm.build_map(path, "md", 4000, 0.10)
    check(doc["format"] == "md" and doc["surface"] == "doc", "md: surface=doc + format=md")
    check(every_record_stamped(doc, "md"), "md: every record stamped surface+format")
    check(len(doc["sections"]) >= 2 and any(">" in s["heading_path"] for s in doc["sections"]),
          "md: ordered sections with nested heading path")
    check(any(s["locator"].startswith("heading:") for s in doc["sections"]),
          "md: heading locators (§1.6 grammar)")
    check(doc["tables"] and doc["tables"][0]["cells"] and
          doc["tables"][0]["cells"][0]["locator"].startswith("table:"),
          "md: table extracted with cell grid + table locator")
    check(any(f["locator"].startswith("footnote:") for f in doc["footnotes"]),
          "md: footnote captured with footnote locator")
    return doc


def assert_docx(path):
    print("\n[docx] %s" % os.path.basename(path))
    doc = dm.build_map(path, "docx", 4000, 0.10)
    check(doc["format"] == "docx", "docx: format=docx")
    check(every_record_stamped(doc, "docx"), "docx: every record stamped surface+format")
    check(any(s.get("heading_path") for s in doc["sections"]), "docx: heading path captured")
    check(doc["paragraphs"] and doc["paragraphs"][0]["locator"].startswith("para:"),
          "docx: paragraphs with para locators")
    check(doc["tables"] and doc["tables"][0]["rows"] == 3 and doc["tables"][0]["cols"] == 3,
          "docx: 3x3 table with cell grid")
    check(doc["adapter"]["docx_read"].startswith("docx_read=python-docx"),
          "docx: primary adapter = python-docx (f2-02 chain head)")
    return doc


def assert_pdf_text(path):
    print("\n[pdf-text] %s" % os.path.basename(path))
    doc = dm.build_map(path, "pdf", 4000, 0.10)
    q = doc["pdf_quality"]
    check(doc["format"] == "pdf", "pdf: format=pdf")
    check(q["text_coverage"] >= 0.9, "pdf: high text coverage on a born-digital PDF")
    check(q["image_only_pages"] == 0, "pdf: no image-only pages")
    check(q["locator_fidelity"] == "page", "pdf: page-level locator fidelity recorded")
    check("table_extraction_confidence" in q, "pdf: table-extraction confidence reported")
    check(q.get("table_engine") and "pdfplumber" in q["table_engine"],
          "pdf: table engine recorded (pdfplumber)")
    check(doc["status"] in ("ok", "ok-with-warnings"), "pdf: text PDF not blocked")
    check(all(p["locator"].startswith("page:") for p in doc["paragraphs"]),
          "pdf: paragraphs carry page locators")
    return doc


def assert_pdf_scanned(path):
    print("\n[pdf-scanned] %s" % os.path.basename(path))
    # branch A: OCR forced unavailable -> MUST block
    blk = dm.build_map(path, "pdf", 4000, 0.10, disable_ocr=True)
    q = blk["pdf_quality"]
    check(q["image_only_pages"] >= 1, "scan: image-only page detected")
    check(q["text_coverage"] < 0.10, "scan: text coverage below threshold")
    check(blk["status"] == "blocked", "scan(no OCR): status=blocked")
    check(blk.get("readiness") == "not-ready", "scan(no OCR): readiness=not-ready")
    fids = [f.get("id") for f in blk.get("findings", [])]
    sev = {f.get("id"): f.get("severity") for f in blk.get("findings", [])}
    check("DOC-PBW-01" in fids and sev.get("DOC-PBW-01") == "must-fix",
          "scan(no OCR): DOC-PBW-01 extraction-loss must-fix raised")
    check(bool(blk.get("human_review")), "scan(no OCR): human-review item emitted")
    # branch B: OCR available -> recorded engine + provenance, not a silent pass
    ocrd = dm.build_map(path, "pdf", 4000, 0.10, disable_ocr=False)
    q2 = ocrd["pdf_quality"]
    if q2.get("ocr_available"):
        check(q2["verdict"] == "ocr-recommended", "scan(OCR avail): verdict=ocr-recommended")
        check(q2.get("ocr", {}).get("engine") and q2["ocr"].get("pages_to_ocr"),
              "scan(OCR avail): OCR engine + page provenance recorded")
    else:
        check(q2["verdict"].startswith("blocked"),
              "scan(no OCR on host): still blocks")
    return blk


def assert_html(path):
    print("\n[html] %s" % os.path.basename(path))
    doc = dm.build_map(path, "html", 4000, 0.10)
    check(doc["format"] == "html", "html: format=html")
    check(every_record_stamped(doc, "html"), "html: every record stamped surface+format")
    modes = doc.get("html_modes", {}).get("ran")
    check(modes and "semantic_dom" in modes and "visible_text" in modes and "raw" in modes,
          "html: visible-text / semantic-DOM / raw modes all run")
    check(doc["tables"] and doc["tables"][0]["cells"], "html: visible table extracted with cells")
    check(any(f.get("kind") == "svg" for f in doc["figures"]), "html: embedded SVG captured as figure")
    # hidden / agent-only scoped SEPARATELY with in_scope=False
    hid = doc.get("hidden_blocks", [])
    ag = doc.get("agent_only_blocks", [])
    check(hid and all(b.get("in_scope") is False for b in hid),
          "html: hidden blocks recorded separately, in_scope=False")
    check(ag and all(b.get("in_scope") is False for b in ag),
          "html: agent-only (<details class=agent-spec>) recorded separately, in_scope=False")
    check(bool(doc.get("nav_blocks")), "html: nav recorded separately")
    # hidden tokens must NOT leak into the visible claim set
    vis_blob = " ".join(p["text"] for p in doc["paragraphs"]).lower()
    check("confidentialfigure9999" not in vis_blob and "agentsecretzzz" not in vis_blob,
          "html: hidden/agent tokens NOT merged into visible paragraphs")
    rv = doc.get("rendered_vs_source", {})
    check(rv.get("divergent") is True and rv.get("tokens_in_source_not_rendered", 0) >= 1,
          "html: rendered-vs-source divergence reported (finding #11)")
    return doc


def assert_landmines_md(path):
    print("\n[landmines:md] %s" % os.path.basename(path))
    rep = pl.load_doc(path, "md", 4000, 0.10)
    findings = pl.scan_paragraphs(rep, _today(), 365) + pl.scan_figures(rep)
    types = {f["type"] for f in findings}
    for f in findings:
        if not (f.get("surface") == "doc" and f.get("format") and f.get("locator")
                and f.get("severity") and f.get("evidence")):
            check(False, "landmines: record missing surface/format/locator/severity/evidence")
            break
    else:
        check(True, "landmines: every finding has surface+format+locator+severity+evidence")
    check("undated-number" in types, "landmines: undated-number detected")
    check("stale-date" in types, "landmines: stale 'as of' date detected")
    check("broken-cross-ref" in types, "landmines: broken cross-ref (Table 9) detected")
    check("numeric-token-candidate" in types, "landmines: numeric-token-in-prose candidate detected")
    check("unattributed-quote" in types, "landmines: unattributed quote detected")


def assert_landmines_scan(scan_doc):
    print("\n[landmines:scanned-pdf] table-as-image")
    findings = pl.scan_figures(scan_doc)
    types = {f["type"] for f in findings}
    check("table-as-image" in types, "landmines: table-as-image flagged on image-only PDF")


def _today():
    import datetime
    return datetime.date(2026, 5, 30)


def main():
    os.makedirs(FX, exist_ok=True)
    md = build_md()
    docx_p = build_docx()
    pdf_t = build_pdf_text()
    pdf_s = build_pdf_scanned()
    html = build_html()

    assert_md(md)
    assert_docx(docx_p)
    assert_pdf_text(pdf_t)
    scan_doc = assert_pdf_scanned(pdf_s)
    assert_html(html)
    assert_landmines_md(md)
    assert_landmines_scan(scan_doc)

    print("\n" + "-" * 60)
    print("document-doctor fixtures: %d passed, %d failed" % (PASS, FAIL))
    if FAILS:
        for f in FAILS:
            print("   - FAILED:", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
