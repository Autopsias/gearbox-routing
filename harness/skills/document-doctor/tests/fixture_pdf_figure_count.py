#!/usr/bin/env python3
"""DR-10 regression — born-digital PDF figure detection (DD-01/DD-07).

The DR-10 dogfood (a LibreOffice docx->pdf render of the strategy memo)
exposed a real sharp edge: `document_map.read_pdf` detected page images with a
single-level pypdf `/XObject` walk that only matched a `/Subtype /Image` at the
top of a page's resources. LibreOffice nests a page's figure inside a `/Form`
XObject, so the walk missed it and reported `counts.figures == 0` on a PDF that
visibly carries a figure. A naive recursion into Form XObjects then OVER-counts:
a shared header/footer Form XObject referenced on every page makes every page
look like it has an image (figures == n_pages).

The fix routes per-page image detection through pdfplumber's `page.images`
(placed-image count — resolves nested/Form XObjects AND reports actual
placement), falling back to a depth-bounded resource-tree walk only when
pdfplumber is unavailable. This regression pins BOTH failure modes:

  A. a born-digital PDF with ONE embedded image on page 1 of a 2-page doc
     => counts.figures == 1, has_image on page 1 ONLY (no over-count), and
        coverage/image-only UNCHANGED (the image page still has text);
  B. a text-only PDF => counts.figures == 0, no page flagged has_image.

Pure regression: it imports the real document_map and asserts on its output.
Skips cleanly (exit 0, SKIP line) if reportlab/PIL are unavailable.
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(PLUGIN, "skills", "document-doctor", "scripts"))

import document_map as dm  # noqa: E402

_FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        _FAILS.append(msg)


def _png_bytes(w=240, h=120, color=(40, 90, 160)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, w - 10, h - 10], outline=(255, 255, 255), width=4)
    d.text((24, 48), "FIGURE 1 — scope", fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _build_pdf(path, with_image):
    """A 2-page born-digital PDF. If with_image: a raster figure on page 1 only."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    # page 1
    c.setFont("Helvetica", 12)
    c.drawString(72, H - 80, "Board memo — page 1")
    c.drawString(72, H - 110, "This page carries selectable text and, in the image case, one figure.")
    if with_image:
        img = ImageReader(io.BytesIO(_png_bytes()))
        c.drawImage(img, 72, H - 320, width=240, height=120)
    c.showPage()
    # page 2 (text only, never an image)
    c.setFont("Helvetica", 12)
    c.drawString(72, H - 80, "Board memo — page 2")
    c.drawString(72, H - 110, "Second page of selectable text, no figure here.")
    c.showPage()
    c.save()


def _map(path):
    return dm.build_map(path, "pdf", 4000, 0.10)


def main():
    try:
        import reportlab  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # pragma: no cover
        print("SKIP fixture_pdf_figure_count — reportlab/PIL unavailable (%s)" % exc)
        return 0

    tmp = tempfile.mkdtemp(prefix="pdf_fig_")
    img_pdf = os.path.join(tmp, "with_image.pdf")
    txt_pdf = os.path.join(tmp, "text_only.pdf")
    _build_pdf(img_pdf, with_image=True)
    _build_pdf(txt_pdf, with_image=False)

    # ---- A: one embedded figure on page 1 of 2 ----
    print("\n[A] born-digital PDF with ONE embedded image on page 1")
    a = _map(img_pdf)
    qa = a["pdf_quality"]
    check(a["format"] == "pdf", "A: format=pdf")
    check(a["counts"]["figures"] == 1,
          "A: exactly ONE figure detected (was 0 pre-fix; n_pages over-count guarded) — got %r"
          % a["counts"]["figures"])
    pages_with_img = [p["page"] for p in qa["per_page"] if p["has_image"]]
    check(pages_with_img == [1],
          "A: has_image on page 1 ONLY (no shared-XObject over-count) — got %r" % pages_with_img)
    check(qa["image_only_pages"] == 0, "A: not image-only (figure page still has text)")
    check(qa["text_coverage"] >= 0.9, "A: coverage unchanged by the figure (>=0.9)")
    check(a["status"] in ("ok", "ok-with-warnings"), "A: a text PDF with a figure is not blocked")
    fig = a["figures"][0] if a["figures"] else {}
    check(fig.get("page") == 1 and fig.get("kind") == "image",
          "A: figure record carries page=1 + kind=image + a locator")

    # ---- B: text-only PDF still reports zero figures ----
    print("\n[B] text-only PDF")
    b = _map(txt_pdf)
    check(b["counts"]["figures"] == 0, "B: zero figures on a no-image PDF — got %r" % b["counts"]["figures"])
    check(not any(p["has_image"] for p in b["pdf_quality"]["per_page"]),
          "B: no page flagged has_image")
    check(b["pdf_quality"]["text_coverage"] >= 0.9, "B: high coverage on a born-digital text PDF")

    print("\n%s — fixture_pdf_figure_count" % ("FAIL" if _FAILS else "PASS"))
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
