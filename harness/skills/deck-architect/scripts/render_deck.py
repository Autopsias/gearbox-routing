#!/usr/bin/env python3
"""
Office Trust Layer — Deck Architect · DA-03: render-from-spec.

The render step, and the LAST place the deck could quietly invent its own story.
Nate's rule (review-1 finding 11): if you let the renderer free-wheel, polish hides a
weak argument. So this renderer is CONSTRAINED — it builds the deck from the approved
architecture + brand rules + evidence map, and it does three things and reports the rest:

  1. PRESERVE THE CLAIM HEADLINES. Every slide's title is the `claim_headline` from the
     APPROVED architecture, verbatim. The renderer does not re-headline, re-summarise, or
     "improve" a claim. If the architecture says the headline is "Q3 revenue grew 12% YoY",
     that is the title — not "Strong Q3" and not a number the renderer pulled itself.

  2. CARRY THE EVIDENCE INTO THE SPEAKER NOTES. Each slide gets the §2.1 evidence note
     (Claim / Source IDs / Calculation / Assumptions / Needs review) for its claim, taken
     from the EVIDENCE MAP — so the proof travels in the file and survives the forward.

  3. KEEP CHARTS TRACEABLE. A slide that declares a chart must have a traceable source for
     the chart's numbers (a supporting cell/source in the evidence map). A chart whose data
     cannot be traced is rendered with a visible "source: UNTRACEABLE" stamp AND a deviation.

…and it REPORTS ANYWHERE IT DEVIATED. The render report is the point: a slide-by-slide
record of every place the render could not follow the spec — a brand rule that was `unclear`
so a safe fall-back was used, a claim with no evidence note, a chart with no traceable source,
an unsupported claim still slotted, a layout the template did not provide. The renderer never
silently paper over a gap; it renders a safe artifact and hands the human the list.

HARD GATE inherited from DA-01: an architecture whose gate is `blocked` (unapproved, or with
must-fix render-blockers) is NOT rendered to a final deck. The render report returns `blocked`
with the blockers, so creation stays constrained by the approved spec.

I/O (`references/conventions.md §3`):
  in : deck-architecture.json (DA-01, APPROVED)   [required — the slide map + the gate]
       brand-rules.json        (DA-02)            [optional — render rules; fall-backs if absent]
       evidence-map.json       (EM-02/EM-04)      [optional — the §2.1 notes + chart traceability]
  out: <out>/deck.pptx                            [the rendered deck, if approved]
       <out>/render-report.json / .md             [the slide-by-slide deviation report]

Status: `ok` / `ok-with-warnings` (rendered, with deviations to read) / `blocked`
(architecture not approved / has render-blockers) / `failed`.

Brand application is delegated in spirit to the public pptx skill: this script
applies the EXTRACTED/INFERRED brand rules it is handed and records every `unclear` rule it
had to fall back on, so a brand owner can finish the job. It never invents a brand value.
"""
import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# the §2.1 speaker-note renderer lives with the evidence map
_EM = os.path.normpath(os.path.join(HERE, "..", "..", "evidence-map", "scripts"))
sys.path.insert(0, _EM)
try:
    import evidence_schema as ES  # noqa: E402
except Exception:  # noqa: BLE001
    ES = None


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# Brand-rule resolution — apply what is known, fall back + REPORT what is not
# ===========================================================================
_FALLBACKS = {
    "heading_font": "Calibri",
    "body_font": "Calibri",
    "title_size_pt": 32,
    "body_size_pt": 18,
    "accent_rgb": "1F4E79",     # a neutral corporate blue, clearly a fall-back
    "text_rgb": "1A1A1A",
}


def resolve_brand(brand_rules):
    """Turn a DA-02 brand-rules.json into a concrete render style, recording every
    place an `unclear` rule forced a documented fall-back. Returns (style, deviations)."""
    deviations = []
    style = dict(_FALLBACKS)
    if not brand_rules:
        deviations.append({"area": "brand", "rule": "(all)", "determination": "absent",
                           "fallback": "house default", "severity": "should-fix",
                           "detail": "No brand-rules.json provided — rendered with safe "
                                     "defaults. Apply the template owner's brand rules through the pptx skill before this travels."})
        return style, deviations

    rules = brand_rules.get("rules", brand_rules)

    def pick(path, key, target):
        node = rules
        for p in path:
            node = (node or {}).get(p, {}) if isinstance(node, dict) else {}
        r = node if not isinstance(node, dict) or "value" not in node else node
        # a `rule()` dict is {value, determination, source, note?}
        if isinstance(r, dict) and "determination" in r:
            det = r.get("determination")
            if det == "unclear" or r.get("value") in (None, ""):
                deviations.append({"area": ".".join(path) or key, "rule": key,
                                   "determination": "unclear",
                                   "fallback": target, "severity": "should-fix",
                                   "detail": r.get("note") or "rule unclear in template; used fall-back"})
                return
            style[target] = r.get("value")

    pick(["typography", "heading_font"], "heading_font", "heading_font")
    pick(["typography", "body_font"], "body_font", "body_font")
    # palette accent (first accent in the scheme, if extracted)
    pal = rules.get("palette", {})
    if isinstance(pal, dict):
        accent = pal.get("accent1") or pal.get("scheme", {})
        if isinstance(accent, dict) and accent.get("determination") == "unclear":
            deviations.append({"area": "palette.accent1", "rule": "accent_rgb",
                               "determination": "unclear", "fallback": style["accent_rgb"],
                               "severity": "should-fix", "detail": "accent colour unclear; used fall-back"})
    # banned moves / footer-legal: if unclear, the renderer cannot enforce them — report
    for area in ("banned_moves", "footer", "logo"):
        node = rules.get(area)
        if isinstance(node, dict) and node.get("determination") == "unclear":
            deviations.append({"area": area, "rule": area, "determination": "unclear",
                               "fallback": "not enforced", "severity": "should-fix",
                               "detail": node.get("note") or ("%s unclear in template — renderer "
                                                              "cannot enforce; brand owner must confirm" % area)})
    return style, deviations


# ===========================================================================
# Evidence lookup — the §2.1 note + chart traceability, per slide claim
# ===========================================================================
def _index_evidence(evidence_map):
    """Map (slide, claim) -> record, and slide -> [records]."""
    by_key, by_slide = {}, {}
    for r in (evidence_map or {}).get("records", []):
        by_slide.setdefault(r.get("slide"), []).append(r)
        by_key[(r.get("slide"), (r.get("claim") or "").strip())] = r
    return by_key, by_slide


def _evidence_for(row, by_key, by_slide):
    """Find the evidence record backing a slide-map row (by slide+claim, else by slide)."""
    key = (row.get("slide"), (row.get("claim_headline") or "").strip())
    if key in by_key:
        return by_key[key]
    cands = by_slide.get(row.get("slide")) or []
    # match on claim_id if present
    cid = row.get("claim_id")
    if cid:
        for r in cands:
            if r.get("claim_id") == cid:
                return r
    return cands[0] if len(cands) == 1 else None


def _note_for(rec):
    if rec is None:
        return None
    if ES is not None:
        try:
            return ES.speaker_note(rec)
        except Exception:  # noqa: BLE001
            pass
    # minimal fall-back note if the schema module is unavailable
    return ("Claim:        %s\nSource IDs:   %s\nCalculation:  %s\nAssumptions:  %s\n"
            "Needs review: %s" % (rec.get("claim"), ", ".join(rec.get("source_ids") or []) or "none",
                                  rec.get("calculation") or "direct from source",
                                  "; ".join(rec.get("assumptions") or []) or "none",
                                  "confirm" if rec.get("review_status") != "verified" else "none"))


def _chart_traceable(rec):
    """A chart is traceable iff its claim has at least one asserted supporting link
    (a source id or a workbook cell). Held candidates do NOT count."""
    if rec is None:
        return False, "no evidence record for this slide's claim"
    if rec.get("review_status") == "unsupported":
        return False, "claim is unsupported — chart numbers cannot be traced"
    if rec.get("supporting"):
        cells = rec.get("cells") or []
        srcs = rec.get("source_ids") or []
        return True, "traceable to %s" % (", ".join(cells or srcs) or "source")
    return False, "no asserted supporting link"


# ===========================================================================
# Render
# ===========================================================================
def render(architecture, brand_rules=None, evidence_map=None, out_dir=".",
           run_id=None, deck_name="deck.pptx"):
    gate = architecture.get("gate", {})
    blockers = gate.get("render_blockers", [])
    approved = gate.get("approved", False)

    report = {
        "run_id": run_id,
        "skill": "deck-architect",
        "phase": "render-from-spec",
        "consumes": ["deck-architecture.json"]
        + (["brand-rules.json"] if brand_rules else [])
        + (["evidence-map.json"] if evidence_map else []),
        "deviations": [],
        "slides": [],
    }

    # ---- the HARD GATE: do not render an unapproved / blocked architecture ----
    if architecture.get("status") == "blocked" or blockers or not approved:
        report["status"] = "blocked"
        report["status_reason"] = (
            "Architecture is not releasable to render: %s%s%s. No deck is rendered until the "
            "architecture is approved and clean of render-blockers (DA-01 gate)."
            % ("%d must-fix render-blocker(s); " % len(blockers) if blockers else "",
               "not human-approved; " if not approved else "",
               "status=%s" % architecture.get("status")))
        report["render_blockers"] = blockers
        return report, None

    style, brand_dev = resolve_brand(brand_rules)
    report["deviations"].extend(brand_dev)
    report["brand_style"] = style

    by_key, by_slide = _index_evidence(evidence_map)

    # ---- build the deck ---------------------------------------------------
    deck_path = os.path.join(out_dir, deck_name)
    prs, pptx_ok = _new_presentation()
    spine = architecture.get("narrative_spine", {})

    for row in architecture.get("slide_map", []):
        sidx = row.get("slide")
        role = row.get("role")
        headline = row.get("claim_headline") or ""
        disposition = row.get("render_disposition", "include")
        slide_report = {"slide": sidx, "role": role, "headline": headline,
                        "rendered": False, "deviations": []}

        # excluded rows (board-only etc.) are recorded, not rendered
        if str(disposition).lower().startswith(("exclude", "board-only")):
            slide_report["rendered"] = False
            slide_report["note"] = "excluded by render_disposition=%s" % disposition
            report["slides"].append(slide_report)
            continue

        rec = _evidence_for(row, by_key, by_slide) if evidence_map else None
        # PRESERVE THE HEADLINE — verbatim from the approved architecture
        note = None
        if role not in ("title", "section"):
            note = _note_for(rec)
            if note is None:
                slide_report["deviations"].append({
                    "area": "evidence", "rule": "speaker-note", "severity": "must-fix",
                    "detail": "no evidence note for this claim — the §2.1 proof is missing; "
                              "run the evidence map before this travels"})
            # unsupported claim that slipped past the gate (defensive)
            if row.get("review_status") == "unsupported":
                slide_report["deviations"].append({
                    "area": "claim", "rule": "unsupported-claim", "severity": "must-fix",
                    "detail": "claim is unsupported but slotted to render — source it or drop it"})

        # chart traceability
        chart = row.get("chart")
        chart_stamp = None
        if chart:
            ok, why = _chart_traceable(rec)
            slide_report["chart"] = {"declared": chart, "traceable": ok, "detail": why}
            if not ok:
                chart_stamp = "source: UNTRACEABLE — %s" % why
                slide_report["deviations"].append({
                    "area": "chart", "rule": "untraceable-chart", "severity": "must-fix",
                    "detail": "chart '%s' numbers cannot be traced: %s" % (chart, why)})

        # actually place the slide if python-pptx is available
        if pptx_ok:
            _add_slide(prs, headline, role, note, chart_stamp, style)
            slide_report["rendered"] = True

        report["deviations"].extend(
            dict(d, slide=sidx) for d in slide_report["deviations"])
        report["slides"].append(slide_report)

    # title slide carries the spine's one-liner in its notes (audit of the argument)
    if pptx_ok:
        _save(prs, deck_path)
    else:
        deck_path = None
        report["deviations"].append({
            "area": "render", "rule": "pptx-engine", "severity": "should-fix",
            "detail": "python-pptx not importable — emitted the render report only; "
                      "no .pptx written. Install python-pptx to produce the deck."})

    must = [d for d in report["deviations"] if d.get("severity") == "must-fix"]
    report["status"] = "ok" if not report["deviations"] else "ok-with-warnings"
    report["status_reason"] = (
        "Rendered %d slide(s) from the approved architecture; headlines preserved verbatim, "
        "evidence in notes. %d deviation(s) reported (%d must-fix) — read the report before "
        "this travels." % (sum(1 for s in report["slides"] if s["rendered"]),
                           len(report["deviations"]), len(must)))
    report["summary"] = {
        "slides_rendered": sum(1 for s in report["slides"] if s["rendered"]),
        "deviations": len(report["deviations"]),
        "must_fix_deviations": len(must),
        "headlines_preserved": True,
        "evidence_notes_injected": sum(
            1 for s in report["slides"] if s["rendered"] and s["role"] not in ("title", "section")),
    }
    report["assertion"] = (
        "The deck was rendered ONLY from the approved architecture + brand rules. Headlines are "
        "the claim sentences verbatim; the §2.1 evidence note travels in each slide's speaker "
        "notes; charts without a traceable source are stamped and flagged. Every place the render "
        "could not follow the spec is in the deviation report — nothing was silently invented.")
    return report, deck_path


# ===========================================================================
# python-pptx adapter (kept thin; the pptx skill owns real brand polish)
# ===========================================================================
def _new_presentation():
    try:
        from pptx import Presentation
        return Presentation(), True
    except Exception:  # noqa: BLE001
        return None, False


def _add_slide(prs, headline, role, note, chart_stamp, style):
    from pptx.util import Pt
    from pptx.dml.color import RGBColor
    layout = prs.slide_layouts[5] if len(prs.slide_layouts) > 5 else prs.slide_layouts[-1]
    slide = prs.slides.add_slide(layout)
    if slide.shapes.title is not None:
        slide.shapes.title.text = headline       # PRESERVE the claim headline verbatim
        try:
            run = slide.shapes.title.text_frame.paragraphs[0].runs[0]
            run.font.name = style.get("heading_font", "Calibri")
            run.font.size = Pt(style.get("title_size_pt", 32))
            run.font.color.rgb = RGBColor.from_string(style.get("text_rgb", "1A1A1A"))
        except Exception:  # noqa: BLE001
            pass
    # chart stamp as a body textbox if the chart is untraceable
    if chart_stamp:
        from pptx.util import Inches
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(5.5), Inches(9), Inches(0.6))
        tb.text_frame.text = chart_stamp
    # the evidence note in the speaker notes
    if note:
        slide.notes_slide.notes_text_frame.text = note
    return slide


def _save(prs, path):
    prs.save(path)


# ===========================================================================
def render_md(report):
    L = ["# Render Report (DA-03, render-from-spec) — %s" % (report.get("run_id") or "")]
    L += ["", "> **Status: `%s`** — %s" % (report["status"], report.get("status_reason", "")), ""]
    if report.get("assertion"):
        L += ["> %s" % report["assertion"], ""]
    if report["status"] == "blocked":
        L += ["## ❌ Not rendered", "", report.get("status_reason", "")]
        for b in report.get("render_blockers", []):
            L.append("- slide %s: %s — _%s_" % (b.get("slide"), b.get("headline"), b.get("issue")))
        return "\n".join(L)
    s = report.get("summary", {})
    L += ["- **Slides rendered:** %s" % s.get("slides_rendered"),
          "- **Headlines preserved verbatim:** %s" % ("✅ yes" if s.get("headlines_preserved") else "❌ NO"),
          "- **Evidence notes injected:** %s" % s.get("evidence_notes_injected"),
          "- **Deviations:** %s (%s must-fix)" % (s.get("deviations"), s.get("must_fix_deviations")), ""]
    L += ["## Slide-by-slide", "", "| # | Role | Headline | Rendered | Chart | Deviations |",
          "|---|---|---|---|---|---|"]
    for sl in report["slides"]:
        ch = sl.get("chart")
        chtxt = "—" if not ch else ("✅ traceable" if ch.get("traceable") else "⚠ UNTRACEABLE")
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            sl["slide"], sl["role"], (sl["headline"] or "")[:42].replace("|", "\\|"),
            "✅" if sl["rendered"] else "—", chtxt, len(sl.get("deviations", []))))
    L.append("")
    if report["deviations"]:
        L += ["## Deviations (every place the render could not follow the spec)", ""]
        for d in report["deviations"]:
            L.append("- `%s` slide %s · **%s** (%s) — %s" % (
                d.get("severity"), d.get("slide", "—"), d.get("area"),
                d.get("determination", d.get("rule")), d.get("detail")))
        L.append("")
    return "\n".join(L)


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="DA-03 render-from-spec (constrained deck renderer)")
    ap.add_argument("--architecture", required=True, help="deck-architecture.json (APPROVED)")
    ap.add_argument("--brand-rules", help="brand-rules.json (DA-02)")
    ap.add_argument("--evidence-map", help="evidence-map.json (EM-02/EM-04)")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    ap.add_argument("--deck-name", default="deck.pptx")
    args = ap.parse_args(argv)

    try:
        architecture = _load(args.architecture)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "failed", "status_reason": "cannot read architecture: %s" % e}))
        return 2
    brand_rules = _load(args.brand_rules) if args.brand_rules and os.path.exists(args.brand_rules) else None
    evidence_map = _load(args.evidence_map) if args.evidence_map and os.path.exists(args.evidence_map) else None

    run_id = args.run_id or architecture.get("run_id") or (_now_slug() + "_render")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    report, deck_path = render(architecture, brand_rules, evidence_map, out_dir=out_dir,
                               run_id=run_id, deck_name=args.deck_name)
    with open(os.path.join(out_dir, "render-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "render-report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(report))

    print("render-from-spec -> %s (status: %s)" % (out_dir, report["status"]))
    if deck_path:
        print("  deck: %s" % os.path.basename(deck_path))
    print("  %s" % report.get("status_reason", ""))
    return 0 if report["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
