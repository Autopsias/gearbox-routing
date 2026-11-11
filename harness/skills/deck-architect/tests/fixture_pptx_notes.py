#!/usr/bin/env python3
"""
Fixture: python-pptx (the engine behind the pptx skill) — PROVES that a deck's
speaker notes can be WRITTEN and round-tripped. Speaker notes are the evidence
layer in the Trust Layer (Claim / Source IDs / Calculation / Assumptions /
Needs review), so the harness must be able to author them, not just read them.

Contract proven:  create deck -> set slide.notes_slide.notes_text_frame.text
                  -> save -> reload -> notes text matches.
Failure mode:     python-pptx missing -> cannot author notes.
Fallback:         emit evidence artifact as .trust/<run_id>/evidence.md instead.

Exit: 0 = PASS, 1 = FAIL.
"""
import os
import sys
import tempfile

try:
    from pptx import Presentation
except ImportError:
    print("FAIL pptx-notes: python-pptx not importable")
    sys.exit(1)

EVIDENCE = (
    "Claim: Revenue grew 12% YoY in FY25\n"
    "Source IDs: S1\n"
    "Calculation: Outputs!B14 = (FY25-FY24)/FY24\n"
    "Assumptions: FY25 figures are current (S1, status=current)\n"
    "Needs review: none"
)


def main():
    scratch = tempfile.mkdtemp(prefix="otl_pptx_", dir=os.environ.get("TMPDIR", "/tmp"))
    path = os.path.join(scratch, "deck.pptx")

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # title-only
    slide.shapes.title.text = "Revenue grew 12% YoY in FY25"
    slide.notes_slide.notes_text_frame.text = EVIDENCE
    prs.save(path)

    prs2 = Presentation(path)
    got = prs2.slides[0].notes_slide.notes_text_frame.text

    if got == EVIDENCE:
        print("PASS pptx-notes: evidence-layer speaker notes written and round-tripped")
        return 0
    print("FAIL pptx-notes: notes did not round-trip")
    print("got:", repr(got[:120]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
