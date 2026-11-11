#!/usr/bin/env python3
"""
Build the Document Doctor defect fixtures (DR-01 — the doc-surface analogue of
fixtures/workbook_doctor/build_fixtures.py).

ONE seeded document PER FORMAT (docx / pdf / md / html), each carrying the SAME
planted prose+claim defects so the consolidated benchmark can score recall /
false-positive-rate / severity PER FORMAT (Review #6: a single "doc" score hides
a weak format). Each seeded doc is paired with answer_key.json, which lists the
ground-truth defects (check + format + locator-token + severity + scanner).

Planted in EVERY format (tied to the S03 q3 source packet so claim-resolution is
deterministic):
  * unsourced number        — "+12% YoY" traces to packet C6 (zero supporting) ⇒ unsupported  [must-fix]
  * cross-section contradiction — Headcount 1,195 (People) vs 1,180 (Outlook); both
                                  resolve to packet C4 (CONFLICT) ⇒ conflicting            [must-fix]
  * stale date              — "as of 12/03/2019" is years past the freshness window      [must-fix]
  * assumption-as-fact      — "Revenue will reach €52M next quarter" stated declaratively [should-fix]
  * broken cross-reference  — "See Table 9" with only one table present                  [should-fix]
  * quote/figure drift      — an unattributed headline quotation                          [should-fix]
  * undated number          — "Net income rose to €1.1M" with no date in the passage      [should-fix]

Plus the two hard cases the build plan calls out, ONE each:
  scanned.pdf   — an image-only PDF with NO text layer: must BLOCK (DD-07): status=blocked
                  + readiness=not-ready + DOC-PBW-01 must-fix + a table-as-image must-fix.
  diverge.html  — an HTML file with hidden text + a repeated nav + an embedded SVG +
                  agent-only DOM: rendered-vs-source divergence (DD-01 finding #11);
                  hidden tokens must never merge into the visible claim set.

And the CLEAN control PER FORMAT (the false-positive / negative control), scored
WITHOUT a packet — dated, attributed, no broken refs ⇒ ZERO defect-class findings.
A scanner that fires on the clean doc is a false positive, exactly as the
roll-forward contrast row works on the Excel side.

Usage: python build_fixtures.py <out_dir>
Writes the files into <out_dir> and prints their paths. Self-contained; no network.
"""
import os
import sys

# ---------------------------------------------------------------------------
# Shared seeded body — one board memo, expressed in each format. Sentences are
# discrete so claim_audit's sentence splitter yields one checkable claim each.
# ---------------------------------------------------------------------------
SEEDED_SECTIONS = [
    ("Summary", [
        "Revenue grew +12% YoY this quarter, the headline number for the board.",
    ]),
    ("People", [
        "Headcount stood at 1,195 at quarter end.",
    ]),
    ("Outlook", [
        "Headcount will fall to 1,180 next year under the reorg.",
        "Revenue will reach €52M next quarter.",
        "These projections are current as of 12/03/2019.",
        "See Table 9 for the full breakdown.",
        "Net income rose to €1.1M.",
    ]),
    ("Quote", [
        "“This was the strongest quarter in the company history by a wide margin” "
        "remained the headline.",
    ]),
]

# The single real table that ships with the memo (so "Table 9" is genuinely broken
# while "Table 1" exists). 2 data rows.
SEEDED_TABLE = [["Metric", "Q3", "Q4"], ["Revenue", "48.2", "52.0"], ["Margin", "18%", "18%"]]

# Clean control — dated, attributed, sourced, no broken refs. Scored WITHOUT a
# packet so claim-resolution stays needs-review (never unsupported/conflicting).
CLEAN_SECTIONS = [
    ("Summary", [
        "The board meeting was held in Lisbon on 30 May 2026.",
        "Revenue was steady through the period, according to the FP&A close pack.",
    ]),
]


def _seeded_paragraphs():
    """Flatten the seeded sections into (heading, sentence) order."""
    out = []
    for head, sents in SEEDED_SECTIONS:
        for s in sents:
            out.append((head, s))
    return out


# ---------------------------------------------------------------------------
# per-format builders
# ---------------------------------------------------------------------------
def build_md(out_dir):
    p = os.path.join(out_dir, "seeded.md")
    lines = ["# Board Memo", ""]
    for head, sents in SEEDED_SECTIONS:
        lines += ["## %s" % head, ""]
        for s in sents:
            lines += [s, ""]
    # one real table
    lines += ["| " + " | ".join(SEEDED_TABLE[0]) + " |",
              "|" + "---|" * len(SEEDED_TABLE[0])]
    for row in SEEDED_TABLE[1:]:
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "[^1]: Source: internal management accounts.", ""]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return p


def build_clean_md(out_dir):
    p = os.path.join(out_dir, "clean.md")
    lines = ["# Board Memo", ""]
    for head, sents in CLEAN_SECTIONS:
        lines += ["## %s" % head, ""]
        for s in sents:
            lines += [s, ""]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return p


def build_docx(out_dir):
    import docx
    p = os.path.join(out_dir, "seeded.docx")
    d = docx.Document()
    d.add_heading("Board Memo", level=1)
    for head, sents in SEEDED_SECTIONS:
        d.add_heading(head, level=2)
        for s in sents:
            d.add_paragraph(s)
    t = d.add_table(rows=len(SEEDED_TABLE), cols=len(SEEDED_TABLE[0]))
    for r, row in enumerate(SEEDED_TABLE):
        for c, val in enumerate(row):
            t.cell(r, c).text = val
    d.save(p)
    return p


def build_clean_docx(out_dir):
    import docx
    p = os.path.join(out_dir, "clean.docx")
    d = docx.Document()
    d.add_heading("Board Memo", level=1)
    for head, sents in CLEAN_SECTIONS:
        d.add_heading(head, level=2)
        for s in sents:
            d.add_paragraph(s)
    d.save(p)
    return p


def _pdf(out_dir, name, sections, with_table):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet
    p = os.path.join(out_dir, name)
    doc = SimpleDocTemplate(p, pagesize=A4)
    styles = getSampleStyleSheet()
    flow = [Paragraph("Board Memo", styles["Title"])]
    for head, sents in sections:
        flow.append(Paragraph(head, styles["Heading2"]))
        for s in sents:
            flow.append(Paragraph(s, styles["Normal"]))
    if with_table:
        flow += [Spacer(1, 12),
                 Table(SEEDED_TABLE, style=TableStyle([
                     ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                     ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))]
    doc.build(flow)
    return p


def build_pdf(out_dir):
    return _pdf(out_dir, "seeded.pdf", SEEDED_SECTIONS, with_table=True)


def build_clean_pdf(out_dir):
    return _pdf(out_dir, "clean.pdf", CLEAN_SECTIONS, with_table=False)


def build_html(out_dir):
    p = os.path.join(out_dir, "seeded.html")
    parts = ["<!doctype html><html><head><title>Board Memo</title></head><body>",
             "<h1>Board Memo</h1>"]
    for head, sents in SEEDED_SECTIONS:
        parts.append("<h2>%s</h2>" % head)
        for s in sents:
            parts.append("<p>%s</p>" % s)
    parts.append("<table><tr>" + "".join("<th>%s</th>" % h for h in SEEDED_TABLE[0]) + "</tr>")
    for row in SEEDED_TABLE[1:]:
        parts.append("<tr>" + "".join("<td>%s</td>" % c for c in row) + "</tr>")
    parts.append("</table>")
    parts.append('<p role="doc-footnote" id="fn1">Source: management accounts.</p>')
    parts.append("</body></html>")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return p


def build_clean_html(out_dir):
    p = os.path.join(out_dir, "clean.html")
    parts = ["<!doctype html><html><head><title>Board Memo</title></head><body>",
             "<h1>Board Memo</h1>"]
    for head, sents in CLEAN_SECTIONS:
        parts.append("<h2>%s</h2>" % head)
        for s in sents:
            parts.append("<p>%s</p>" % s)
    parts.append("</body></html>")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return p


def build_html_diverge(out_dir):
    """DD-01 finding #11: hidden text + a repeated nav + an embedded SVG +
    agent-only DOM ⇒ rendered-vs-source divergence; hidden tokens must NOT merge
    into the visible claim set."""
    p = os.path.join(out_dir, "diverge.html")
    html = """<!doctype html><html><head><title>Investor Update</title>
<style>.gone{display:none}</style></head>
<body>
<nav><a href="#a">Home</a> | <a href="#b">Revenue</a> | <a href="#c">Risks</a></nav>
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


def build_scanned_pdf(out_dir):
    """DD-07: an image-only PDF — text rendered to a raster, then wrapped. No text
    layer ⇒ the map must BLOCK (no OCR), raise DOC-PBW-01 + table-as-image."""
    from PIL import Image, ImageDraw
    img = os.path.join(out_dir, "_scan_page.png")
    out = os.path.join(out_dir, "scanned.pdf")
    im = Image.new("RGB", (1240, 1754), "white")
    dr = ImageDraw.Draw(im)
    for i, line in enumerate([
        "CONFIDENTIAL — BOARD PACK (scanned)",
        "Revenue 4.2M   Margin 18%",
        "This page is an image; there is no selectable text layer.",
        "A reader cannot extract or re-check any number here.",
    ]):
        dr.text((80, 120 + i * 90), line, fill="black")
    for x in range(80, 700, 120):
        dr.line([(x, 600), (x, 900)], fill="black", width=2)
    for y in range(600, 901, 100):
        dr.line([(80, y), (680, y)], fill="black", width=2)
    im.save(img)
    import img2pdf
    with open(out, "wb") as fh:
        fh.write(img2pdf.convert(img))
    return out


BUILDERS = {
    "seeded_docx": build_docx,
    "seeded_pdf": build_pdf,
    "seeded_md": build_md,
    "seeded_html": build_html,
    "clean_docx": build_clean_docx,
    "clean_pdf": build_clean_pdf,
    "clean_md": build_clean_md,
    "clean_html": build_clean_html,
    "diverge_html": build_html_diverge,
    "scanned_pdf": build_scanned_pdf,
}


def build_all(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    return {k: fn(out_dir) for k, fn in BUILDERS.items()}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    paths = build_all(target)
    for k, p in paths.items():
        print("%-14s -> %s" % (k, p))
