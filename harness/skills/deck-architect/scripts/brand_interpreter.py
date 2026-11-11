#!/usr/bin/env python3
"""
Office Trust Layer — Deck Architect · DA-02: brand-system interpreter.

Reads ANY PowerPoint template — a client's, a partner's, your own, an
unbranded stock deck — and extracts the PRACTICAL RENDER RULES a later step can
follow: masters/layouts, typography, palette + usage, chart/table style,
logo/legal/footer, density, banned moves, accessibility.

This GENERALISES a hand-authored brand spec for ONE company to arbitrary
templates we do not pre-own. The crucial discipline (review-1 finding 11): brand
is an INPUT CONSTRAINT on rendering, not a final checklist, and the interpreter
must **never invent**. Every rule it emits carries a `determination`:

  * `extracted` — read directly from the file's XML (theme colours, theme fonts,
                  slide size, layout names + placeholder types, embedded images,
                  footer/date/slide-number placeholders). Authoritative.
  * `inferred`  — derived by a stated heuristic from what's in the file (e.g.
                  "accent1 is the primary accent", a density band from placeholder
                  counts). Carries the heuristic so a human can overrule it.
  * `unclear`   — NOT determinable from the file (logo usage rules, legal/footer
                  wording, banned moves, the *intended* role of each palette
                  colour, approved chart palette). Emitted as `unclear` with the
                  reason — NEVER guessed. These are exactly the rules that must
                  come from a brand manual / the brand owner.

A template named `brand_template.pptx` that actually ships the stock Office theme
(Calibri, #4472C4) is extracted as Calibri/#4472C4 — the interpreter reports what
is in the file, not what the filename implies. That is the whole point.

Output, per the I/O contract (`references/conventions.md §3`):
  <out>/brand-rules.json   (machine — the render-rules contract)
  <out>/brand-rules.md     (human memo, grouped by determination)

Optionally `--brand-doc FILE.md` supplies an authored brand spec (like a
`brand-system.md`); when present, the interpreter records that
human-authored rules are available to RESOLVE `unclear` items — it still does not
fabricate them from the binary.

Usage:
  python brand_interpreter.py --template path/to/template.pptx \
      [--brand-doc brand-system.md] [--out DIR] [--run-id ID]
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import zipfile


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def rule(value, determination, source, note=None):
    """A single render rule with its provenance/determination."""
    r = {"value": value, "determination": determination, "source": source}
    if note:
        r["note"] = note
    return r


UNCLEAR = "unclear"
EXTRACTED = "extracted"
INFERRED = "inferred"

# ---- accessibility: relative luminance + WCAG contrast ----------------------
def _rel_lum(hexstr):
    h = hexstr.lstrip("#")
    if len(h) != 6:
        return None
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(h1, h2):
    l1, l2 = _rel_lum(h1), _rel_lum(h2)
    if l1 is None or l2 is None:
        return None
    a, b = max(l1, l2), min(l1, l2)
    return round((a + 0.05) / (b + 0.05), 2)


# ---- theme extraction (colours + fonts) -------------------------------------
_CLR_KEYS = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3",
             "accent4", "accent5", "accent6", "hlink", "folHlink"]


def _extract_theme(z):
    themes = sorted(n for n in z.namelist() if re.match(r"ppt/theme/theme\d+\.xml", n))
    if not themes:
        return None, None, None
    xml = z.read(themes[0]).decode("utf-8", "ignore")
    colours = {}
    scheme = re.search(r"<a:clrScheme[^>]*>(.*?)</a:clrScheme>", xml, re.S)
    block = scheme.group(1) if scheme else xml
    for key in _CLR_KEYS:
        m = re.search(r"<a:{k}>(.*?)</a:{k}>".format(k=key), block, re.S)
        if not m:
            continue
        seg = m.group(1)
        srgb = re.search(r'srgbClr val="([0-9A-Fa-f]{6})"', seg)
        sysl = re.search(r'lastClr="([0-9A-Fa-f]{6})"', seg)
        if srgb:
            colours[key] = "#" + srgb.group(1).upper()
        elif sysl:
            colours[key] = "#" + sysl.group(1).upper()
    fonts = {}
    fmaj = re.search(r"<a:majorFont>.*?<a:latin typeface=\"([^\"]*)\"", xml, re.S)
    fmin = re.search(r"<a:minorFont>.*?<a:latin typeface=\"([^\"]*)\"", xml, re.S)
    if fmaj:
        fonts["heading"] = fmaj.group(1)
    if fmin:
        fonts["body"] = fmin.group(1)
    return colours, fonts, os.path.basename(themes[0])


# ---- layouts + masters (python-pptx) ----------------------------------------
def _ph_type_name(ph):
    try:
        t = ph.placeholder_format.type
        return str(t).split()[0].split(".")[-1] if t is not None else "BODY?"
    except Exception:  # noqa: BLE001
        return "?"


def _extract_layouts(pptx_path):
    try:
        from pptx import Presentation
        from pptx.util import Emu  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return None, "python-pptx unavailable: {}".format(e)
    pres = Presentation(pptx_path)
    sw, sh = pres.slide_width, pres.slide_height
    info = {
        "slide_width_in": round(sw / 914400, 2),
        "slide_height_in": round(sh / 914400, 2),
        "aspect_ratio": "16:9" if abs((sw / sh) - (16 / 9)) < 0.02 else
                        ("4:3" if abs((sw / sh) - (4 / 3)) < 0.02 else "{}:{}".format(sw, sh)),
        "n_masters": len(pres.slide_masters),
        "layouts": [],
        "has_footer_ph": False,
        "has_slidenum_ph": False,
        "has_date_ph": False,
        "body_font_sizes_pt": [],
    }
    for m in pres.slide_masters:
        for lay in list(m.slide_layouts):
            phs = []
            for ph in lay.placeholders:
                tname = _ph_type_name(ph)
                phs.append(tname)
                if "FOOTER" in tname.upper():
                    info["has_footer_ph"] = True
                if "SLIDE_NUMBER" in tname.upper() or "SLIDENUM" in tname.upper():
                    info["has_slidenum_ph"] = True
                if "DATE" in tname.upper():
                    info["has_date_ph"] = True
                # collect any explicit run sizes on the layout placeholder
                try:
                    for para in ph.text_frame.paragraphs:
                        if para.font.size is not None:
                            info["body_font_sizes_pt"].append(round(para.font.size.pt, 1))
                        for run in para.runs:
                            if run.font.size is not None:
                                info["body_font_sizes_pt"].append(round(run.font.size.pt, 1))
                except Exception:  # noqa: BLE001
                    pass
            info["layouts"].append({"name": lay.name, "placeholders": phs})
    info["body_font_sizes_pt"] = sorted(set(info["body_font_sizes_pt"]))
    return info, None


# ---- media (logo candidates) ------------------------------------------------
def _extract_media(z):
    media = [n for n in z.namelist() if n.startswith("ppt/media/")]
    # images referenced by the slide master(s) are logo candidates
    master_rels = [n for n in z.namelist() if re.match(r"ppt/slideMasters/_rels/.*\.rels", n)]
    master_imgs = set()
    for r in master_rels:
        rx = z.read(r).decode("utf-8", "ignore")
        for m in re.finditer(r'Target="\.\./media/([^"]+)"', rx):
            master_imgs.add(m.group(1))
    return media, sorted(master_imgs)


# ---------------------------------------------------------------------------
def interpret(template_path, brand_doc=None, run_id=None):
    rules = {}
    warnings = []
    status = "ok"

    if not os.path.exists(template_path):
        return {"run_id": run_id, "skill": "deck-architect", "phase": "brand-rules",
                "status": "failed",
                "status_reason": "template not found: {}".format(template_path)}

    try:
        z = zipfile.ZipFile(template_path)
    except Exception as e:  # noqa: BLE001
        return {"run_id": run_id, "skill": "deck-architect", "phase": "brand-rules",
                "status": "failed",
                "status_reason": "not a readable .pptx (zip) file: {}".format(e)}

    colours, fonts, theme_file = _extract_theme(z)
    media, master_imgs = _extract_media(z)
    layout_info, layout_err = _extract_layouts(template_path)

    # ---- 1. masters / layouts -------------------------------------------
    if layout_info:
        rules["masters_layouts"] = {
            "slide_size": rule(
                {"width_in": layout_info["slide_width_in"],
                 "height_in": layout_info["slide_height_in"],
                 "aspect_ratio": layout_info["aspect_ratio"]},
                EXTRACTED, "presentation dimensions"),
            "n_masters": rule(layout_info["n_masters"], EXTRACTED, "slide masters"),
            "n_layouts": rule(len(layout_info["layouts"]), EXTRACTED, "slide layouts"),
            "layouts": rule([{"name": l["name"], "placeholders": l["placeholders"]}
                             for l in layout_info["layouts"]],
                            EXTRACTED, "each layout's name + placeholder types"),
            "render_guidance": rule(
                "Use these named layouts; do not invent layouts. Match content to the "
                "layout whose placeholder set fits (title-only, title+body, two-content, "
                "section header, blank).", INFERRED,
                "heuristic: render onto existing layouts rather than free-form shapes"),
        }
    else:
        rules["masters_layouts"] = {
            "layouts": rule(None, UNCLEAR, "could not parse layouts",
                            note=layout_err or "python-pptx parse failed")}
        warnings.append("layouts unreadable: {}".format(layout_err))
        status = "ok-with-warnings"

    # ---- 2. typography ---------------------------------------------------
    typ = {}
    if fonts.get("heading"):
        typ["heading_font"] = rule(fonts["heading"], EXTRACTED, "theme majorFont latin")
    else:
        typ["heading_font"] = rule(None, UNCLEAR, "no theme majorFont")
    if fonts.get("body"):
        typ["body_font"] = rule(fonts["body"], EXTRACTED, "theme minorFont latin")
    else:
        typ["body_font"] = rule(None, UNCLEAR, "no theme minorFont")
    sizes = (layout_info or {}).get("body_font_sizes_pt") or []
    if sizes:
        typ["font_sizes_pt_seen"] = rule(sizes, EXTRACTED, "explicit run sizes on layout placeholders")
        typ["min_body_size_pt"] = rule(min(sizes), INFERRED,
                                       "smallest explicit size found on a layout placeholder")
    else:
        typ["font_sizes_pt_seen"] = rule(None, UNCLEAR,
                                         "layout placeholders inherit sizes from the master/theme; "
                                         "no explicit per-size override to read")
    typ["type_scale"] = rule(None, UNCLEAR,
                             "the intended H1/H2/body/caption scale is a brand decision not "
                             "recoverable from an empty template; confirm with the brand owner")
    rules["typography"] = typ

    # ---- 3. palette + usage ---------------------------------------------
    pal = {}
    if colours:
        pal["theme_colors"] = rule(colours, EXTRACTED, "theme clrScheme ({})".format(theme_file))
        # usage is INFERRED from the standard Office colour-scheme roles
        usage = {}
        role_map = {
            "dk1": "primary text / dark", "lt1": "primary background / light",
            "dk2": "secondary dark (headers / bars)", "lt2": "secondary light (fills)",
            "accent1": "primary accent (first chart series, key emphasis)",
            "accent2": "second accent (second chart series)",
            "accent3": "third accent", "accent4": "fourth accent",
            "accent5": "fifth accent", "accent6": "sixth accent",
            "hlink": "hyperlink", "folHlink": "followed hyperlink",
        }
        for k in colours:
            usage[k] = "{} = {}".format(colours[k], role_map.get(k, "supporting"))
        pal["usage"] = rule(usage, INFERRED,
                            "standard Office colour-scheme role mapping — the *intended* brand "
                            "role of each colour is NOT in the file; confirm against a brand manual")
        # chart sequence inferred from accent order
        accents = [colours[k] for k in ["accent1", "accent2", "accent3", "accent4",
                                        "accent5", "accent6"] if k in colours]
        if accents:
            pal["chart_color_sequence"] = rule(accents, INFERRED,
                                               "accent1..accent6 order (Office default chart cycle); "
                                               "a brand may mandate a different sequence")
        pal["semantic_status_colors"] = rule(None, UNCLEAR,
                                             "red/amber/green status colours are a brand/comms "
                                             "decision, not in the theme; confirm with the owner")
    else:
        pal["theme_colors"] = rule(None, UNCLEAR, "no theme clrScheme found")
        warnings.append("no theme colours extracted")
        status = "ok-with-warnings"
    rules["palette"] = pal

    # ---- 4. chart / table style -----------------------------------------
    has_charts = any("ppt/charts/" in n or "/chart" in n for n in z.namelist())
    rules["chart_table_style"] = {
        "embedded_chart_styles": rule(
            "present — extract from embedded charts" if has_charts else None,
            EXTRACTED if has_charts else UNCLEAR,
            "ppt/charts/* present" if has_charts else
            "no embedded charts in the template to read a chart style from"),
        "table_style": rule(None, UNCLEAR,
                            "table banding/border style is set per-table at render time; the "
                            "template carries no authored table style to copy — confirm with owner"),
        "chart_color_sequence_ref": rule("see palette.chart_color_sequence (inferred from accents)",
                                         INFERRED, "no explicit chart-palette override in the file"),
    }

    # ---- 5. logo / legal / footer ---------------------------------------
    logo = {}
    if master_imgs:
        logo["logo_candidates"] = rule(master_imgs, EXTRACTED,
                                       "images referenced by the slide master (likely the logo)")
        logo["logo_placement_rules"] = rule(None, UNCLEAR,
                                            "clear-space, min-size, mono/reverse variants and which "
                                            "logo on which background are brand-manual rules, not in "
                                            "the binary")
    else:
        logo["logo_candidates"] = rule(media or None,
                                       EXTRACTED if media else UNCLEAR,
                                       "media present but none master-referenced" if media
                                       else "no media in the template")
    li = layout_info or {}
    logo["footer_placeholder"] = rule(li.get("has_footer_ph", False), EXTRACTED,
                                      "FOOTER placeholder present on a layout")
    logo["slide_number_placeholder"] = rule(li.get("has_slidenum_ph", False), EXTRACTED,
                                            "SLIDE_NUMBER placeholder present on a layout")
    logo["date_placeholder"] = rule(li.get("has_date_ph", False), EXTRACTED,
                                    "DATE placeholder present on a layout")
    logo["footer_legal_text"] = rule(None, UNCLEAR,
                                     "the required footer/legal/confidentiality wording is a brand/"
                                     "legal decision, not recoverable from empty placeholders")
    rules["logo_legal_footer"] = logo

    # ---- 6. density ------------------------------------------------------
    if layout_info:
        ph_counts = [len(l["placeholders"]) for l in layout_info["layouts"]]
        avg = round(sum(ph_counts) / max(len(ph_counts), 1), 1)
        band = "low" if avg <= 2 else ("medium" if avg <= 4 else "high")
        rules["density"] = {
            "avg_placeholders_per_layout": rule(avg, EXTRACTED, "mean placeholder count across layouts"),
            "density_band": rule(band, INFERRED,
                                 "heuristic from placeholder count; a brand's real density rule "
                                 "(words/slide, one-idea-per-slide) is editorial — confirm"),
            "max_density_rule": rule(None, UNCLEAR,
                                     "max words/bullets per slide is a brand editorial rule, not in "
                                     "the template"),
        }
    else:
        rules["density"] = {"density_band": rule(None, UNCLEAR, "layouts unreadable")}

    # ---- 7. banned moves -------------------------------------------------
    rules["banned_moves"] = rule(
        None, UNCLEAR,
        "prohibited treatments (stretching the logo, off-palette colours, drop shadows, "
        "clip-art, gradient fills, etc.) are brand-manual rules. NOT inferable from a "
        "template — must be supplied by the brand owner. Do NOT invent.")

    # ---- 8. accessibility -----------------------------------------------
    acc = {}
    if colours.get("dk1") and colours.get("lt1"):
        c = _contrast(colours["dk1"], colours["lt1"])
        acc["text_on_background_contrast"] = rule(
            {"pair": "dk1 on lt1", "ratio": c,
             "wcag_aa_normal": (c is not None and c >= 4.5),
             "wcag_aa_large": (c is not None and c >= 3.0)},
            EXTRACTED, "computed contrast of theme dk1 vs lt1")
    else:
        acc["text_on_background_contrast"] = rule(None, UNCLEAR, "dk1/lt1 not both present")
    if sizes:
        acc["min_font_size_pt"] = rule(min(sizes), EXTRACTED,
                                       "smallest explicit layout size; flag body < 12pt at render")
    else:
        acc["min_font_size_pt"] = rule(None, UNCLEAR, "no explicit sizes in template")
    acc["accent_on_background_contrast"] = {}
    for k in ["accent1", "accent2", "accent3", "accent4", "accent5", "accent6"]:
        if colours.get(k) and colours.get("lt1"):
            cc = _contrast(colours[k], colours["lt1"])
            acc["accent_on_background_contrast"][k] = {
                "ratio": cc, "wcag_aa_large_text": (cc is not None and cc >= 3.0)}
    acc["accent_on_background_contrast"] = rule(
        acc["accent_on_background_contrast"], EXTRACTED,
        "computed contrast of each accent vs lt1 (use for accent text/large text only)")
    rules["accessibility"] = acc

    # ---- brand-doc resolution pointer -----------------------------------
    brand_doc_present = bool(brand_doc and os.path.exists(brand_doc))
    if brand_doc_present:
        rules["_brand_doc"] = rule(
            os.path.basename(brand_doc), "human-authored",
            "an authored brand spec is available to RESOLVE the `unclear` items above; the "
            "interpreter still does not fabricate values from the binary")

    # ---- tally -----------------------------------------------------------
    counts = {"extracted": 0, "inferred": 0, "unclear": 0, "human-authored": 0}

    def _tally(obj):
        if isinstance(obj, dict):
            if "determination" in obj and isinstance(obj.get("determination"), str):
                counts[obj["determination"]] = counts.get(obj["determination"], 0) + 1
            else:
                for v in obj.values():
                    _tally(v)
    _tally(rules)

    unclear_items = []

    def _collect_unclear(path, obj):
        if isinstance(obj, dict):
            if obj.get("determination") == UNCLEAR:
                unclear_items.append({"rule": path, "reason": obj.get("source"),
                                      "note": obj.get("note")})
            else:
                for k, v in obj.items():
                    _collect_unclear("{}.{}".format(path, k) if path else k, v)
    _collect_unclear("", rules)

    reason = ("Extracted {extracted} render rules from the file; {inferred} inferred by stated "
              "heuristic; {unclear} marked UNCLEAR (not in the binary — need the brand owner). "
              "Nothing invented.".format(**counts))
    return {
        "run_id": run_id,
        "skill": "deck-architect",
        "phase": "brand-rules",
        "status": status,
        "status_reason": reason,
        "template": os.path.basename(template_path),
        "determination_counts": counts,
        "render_rules": rules,
        "unclear_items": unclear_items,
        "warnings": warnings,
        "discipline": "Every rule carries a `determination` ∈ {extracted, inferred, unclear}. "
                      "`unclear` is emitted with a reason and NEVER guessed — brand is an input "
                      "constraint, and a value not in the file must come from the brand owner.",
    }


# ---------------------------------------------------------------------------
def render_md(b):
    L = []
    L.append("# Brand Rules — {}".format(b.get("template")))
    L.append("")
    L.append("> **Status: `{}`** — {}".format(b["status"], b["status_reason"]))
    L.append(">")
    L.append("> {}".format(b["discipline"]))
    L.append("")
    c = b["determination_counts"]
    L.append("**Determination tally:** {} extracted · {} inferred · {} unclear{}".format(
        c.get("extracted", 0), c.get("inferred", 0), c.get("unclear", 0),
        " · {} human-authored".format(c["human-authored"]) if c.get("human-authored") else ""))
    L.append("")

    def emit(title, obj, depth=2):
        L.append("{} {}".format("#" * depth, title))
        L.append("")
        if isinstance(obj, dict) and "determination" in obj:
            _emit_rule(L, title, obj)
            L.append("")
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict) and "determination" in v:
                    _emit_rule(L, k, v)
                elif isinstance(v, dict):
                    emit(k, v, depth + 1)
            L.append("")

    section_titles = {
        "masters_layouts": "1. Masters / layouts",
        "typography": "2. Typography",
        "palette": "3. Palette + usage",
        "chart_table_style": "4. Chart / table style",
        "logo_legal_footer": "5. Logo / legal / footer",
        "density": "6. Density",
        "banned_moves": "7. Banned moves",
        "accessibility": "8. Accessibility",
    }
    for key, title in section_titles.items():
        if key in b["render_rules"]:
            emit(title, b["render_rules"][key])

    L.append("## Unclear items — must come from the brand owner (not invented)")
    L.append("")
    if b["unclear_items"]:
        for u in b["unclear_items"]:
            L.append("- **{}** — {}{}".format(
                u["rule"], u.get("reason") or "",
                " _({})_".format(u["note"]) if u.get("note") else ""))
    else:
        L.append("- (none — everything was extractable)")
    L.append("")
    return "\n".join(L)


def _emit_rule(L, label, r):
    det = r.get("determination")
    badge = {"extracted": "✅ extracted", "inferred": "≈ inferred",
             "unclear": "⚠️ UNCLEAR", "human-authored": "📄 authored"}.get(det, det)
    val = r.get("value")
    if isinstance(val, (dict, list)):
        val = "`{}`".format(json.dumps(val, ensure_ascii=False)[:400])
    elif val is None:
        val = "—"
    L.append("- **{}** [{}]: {}".format(label, badge, val))
    if r.get("source"):
        L.append("  - _{}_".format(r["source"]))
    if r.get("note"):
        L.append("  - note: {}".format(r["note"]))


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="DA-02 brand-system interpreter (any .pptx template)")
    ap.add_argument("--template", required=True)
    ap.add_argument("--brand-doc")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    run_id = args.run_id or (_now_slug() + "_brand-rules")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    b = interpret(args.template, brand_doc=args.brand_doc, run_id=run_id)
    jpath = os.path.join(out_dir, "brand-rules.json")
    mpath = os.path.join(out_dir, "brand-rules.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(b, fh, indent=2, ensure_ascii=False)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(render_md(b) if b["status"] != "failed" else "# Brand Rules — FAILED\n\n{}\n".format(
            b["status_reason"]))

    print("brand-rules -> {} (status: {})".format(out_dir, b["status"]))
    if b.get("determination_counts"):
        print("  {extracted} extracted · {inferred} inferred · {unclear} unclear".format(
            **b["determination_counts"]))
    return 0 if b["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
