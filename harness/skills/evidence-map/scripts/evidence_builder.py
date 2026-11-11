#!/usr/bin/env python3
"""
Office Trust Layer — Evidence Map · EM-02: builder + speaker-notes generator.

Populates the EM-01 evidence record per slide claim, then writes the proof into
the deck's speaker notes so it travels with the file (review-1: "speaker notes are
the evidence layer that survives the forward").

The honest cross-app LINKER is the heart of it. For each deck claim it gathers
provenance from TWO apps and never asserts a link it cannot stand behind:

  1. PACKET provenance — the human-curated `source-packet.json` claim's `supporting`
     links (the S-IDs, locators, confidence, rationale, conflict) are carried VERBATIM.
  2. WORKBOOK cells — it opens the backing workbook, builds a label->cell index, and
     matches each claim against it. A match's confidence is computed from label +
     value + period agreement. At/above `LINK_THRESHOLD` it is added as a SUPPORTING
     link (multi-source provenance — a claim can rest on the packet source AND the
     model cell). BELOW threshold it is held as a CANDIDATE for human approval —
     never silently promoted to provenance (review-1 finding 9).

It refuses to launder an unsourced number. A workbook cell that is itself an
unsourced HARDCODE (a literal sitting in a column of formulas) is surfaced as a
low-confidence candidate with that fact in its rationale — so a claim the packet
flags `unsupported` stays `unsupported` even though "the number appears in a cell".
A claim with no packet source and no above-threshold cell is marked `unsupported`.

It also detects CROSS-CELL CONFLICTS the packet may not carry: the same line in two
tabs with different values (e.g. headcount 1,195 in Actuals vs 1,180 in Board) sets
the claim `conflicting`.

I/O (`references/conventions.md §3`):
  in : deck-architecture.json (the EM/DA slide map)   [preferred claim source]
       source-packet.json                              [provenance + conflict log]
       --workbook  model.xlsx                          [the cell index]
       [--deck deck.pptx]                              [notes injected into a copy]
       [--workbook-verdict workbook-verdict.json]      [assumptions table, optional]
  out: <out>/evidence-map.json   (machine — one record per claim, + the gate)
       <out>/evidence-map.md      (human memo)
       <out>/<deck>-evidenced.pptx  (the deck with §2.1 notes, if --deck given)

Status: `ok` / `ok-with-warnings` (a fallback fired, e.g. no workbook) / `blocked`
(a required input missing) / `failed`.
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence_schema as ES  # noqa: E402

JUDGEMENT_CLASSES = {"assumption", "estimate", "open-question", "interpretation"}


# ---------------------------------------------------------------------------
def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# The workbook cell index — the cross-app substrate.
# ===========================================================================
_HEADER_LABELS = {"metric", "line", "item", "kpi", "description", "measure", "name"}
_CELLREF_RE = re.compile(r"^=\s*'?([A-Za-z0-9 _]+)'?!\$?([A-Z]{1,3})\$?(\d+)\s*$")


def build_cell_index(xlsx_path):
    """Open a workbook and build {normalized_label: [entry,...]} where each entry is
    {sheet, label, cell, value, is_hardcode}. A value column that holds >=1 formula
    AND a literal flags that literal as a hardcode (an unsourced constant). Single
    '=Sheet!Cell' formulas are resolved against the literal index so a mirrored line
    reads its true value."""
    try:
        import openpyxl
    except ImportError:
        return None, "openpyxl not importable"
    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=False)
    except Exception as e:  # noqa: BLE001
        return None, "cannot open workbook: %s" % e

    literal = {}   # "Sheet!Cell" -> value (literals only)
    raw = []       # (sheet, label, value_cell, value_or_formula, col_has_formula)
    for ws in wb.worksheets:
        # find the value column = the first column right of the label column that
        # carries numbers/formulas. Label column = the leftmost text column.
        col_has_formula = {}
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    col_has_formula[c.column] = True
        for row in ws.iter_rows():
            cells = [c for c in row if c.value is not None]
            if not cells:
                continue
            # label = first text cell; value = first numeric/formula cell after it
            label_cell = next((c for c in cells if isinstance(c.value, str)
                               and not c.value.startswith("=")), None)
            value_cell = next((c for c in cells if isinstance(c.value, (int, float))
                               or (isinstance(c.value, str) and c.value.startswith("="))), None)
            if label_cell is None or value_cell is None:
                continue
            lbl = label_cell.value.strip()
            if lbl.lower().rstrip(":") in _HEADER_LABELS:
                continue
            key = "%s!%s" % (ws.title, value_cell.coordinate)
            if isinstance(value_cell.value, (int, float)):
                literal[key] = value_cell.value
            raw.append((ws.title, lbl, value_cell.coordinate, value_cell.value,
                        col_has_formula.get(value_cell.column, False)))

    index = {}
    for sheet, lbl, coord, val, col_has_f in raw:
        is_formula = isinstance(val, str) and val.startswith("=")
        resolved = val
        is_hardcode = False
        if is_formula:
            m = _CELLREF_RE.match(val)
            if m:
                ref = "%s!%s%s" % (m.group(1), m.group(2), m.group(3))
                resolved = literal.get(ref, None)
        else:
            # a literal sitting in a column that ALSO holds formulas is a hardcode
            is_hardcode = col_has_f
        entry = {
            "sheet": sheet, "label": lbl, "cell": "%s!%s" % (sheet, coord),
            "value": resolved, "raw": val, "is_formula": is_formula,
            "is_hardcode": is_hardcode,
        }
        index.setdefault(_norm_label(lbl), []).append(entry)
    return index, None


def _norm_label(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)          # drop units like "(€m)"
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in ("the", "of", "a"))


def _label_tokens(s):
    return set(_norm_label(s).split())


# ---------------------------------------------------------------------------
def _parse_number(text):
    """Pull the leading numeric magnitude from a claim value like '€48.2M', '18.5%',
    '1,195', '12% YoY' -> (magnitude, is_percent)."""
    if text is None:
        return None, False
    t = str(text)
    is_pct = "%" in t
    m = re.search(r"-?\d[\d,]*\.?\d*", t.replace(" ", ""))
    if not m:
        return None, is_pct
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None, is_pct
    return v, is_pct


def _values_agree(claim_val, cell_val):
    """Does the claim's number agree with the cell's number, tolerating %/fraction
    and €M scaling? Returns (agree, note)."""
    cv, is_pct = _parse_number(claim_val)
    if cv is None or cell_val is None or not isinstance(cell_val, (int, float)):
        return None, "value not comparable"
    cell = float(cell_val)
    candidates = {cell}
    if abs(cell) <= 1.0:           # a fraction stored as 0.185 -> 18.5%
        candidates.add(cell * 100.0)
    if is_pct:
        candidates.add(cell * 100.0 if abs(cell) <= 1.0 else cell)
    for c in candidates:
        if c == 0:
            if cv == 0:
                return True, "exact 0"
            continue
        if abs(cv - c) / max(abs(c), 1e-9) < 0.01:
            return True, "values agree (%s≈%s)" % (cv, c)
    return False, "values differ (claim %s vs cell %s)" % (cv, cell)


# ===========================================================================
# Claim extraction
# ===========================================================================
def extract_claims_from_architecture(arch):
    """Take the DA/EM slide map and yield the evidence-bearing rows (the ones DA-01
    reserved a §2.1 note for): role not title/section, asserting a claim."""
    rows = []
    for r in arch.get("slide_map", []):
        if r.get("role") in ("title", "section"):
            continue
        note_req = (r.get("note_requirement") or "")
        asserts = note_req.startswith("required") or r.get("review_status") == "unsupported"
        if not asserts:
            continue
        rows.append(r)
    return rows


def extract_claims_from_pptx(pptx_path):
    """Fallback claim extraction when there is no architecture: each slide's title
    text is taken as the claim headline (markitdown/pptx path)."""
    try:
        from pptx import Presentation
    except ImportError:
        return None
    prs = Presentation(pptx_path)
    rows = []
    for i, slide in enumerate(prs.slides, start=1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text.strip()
        if not title:
            continue
        rows.append({"slide": i, "claim_headline": title, "role": "evidence",
                     "source_ids": [], "review_status": "needs-review",
                     "note_requirement": "required"})
    return rows


# ===========================================================================
# The cross-app linker
# ===========================================================================
def _split_src_ids(raw_ids):
    out = []
    for s in raw_ids or []:
        for part in re.split(r"[/,]", s):
            part = part.strip()
            if part:
                out.append(part)
    return ES._unique(out)


def link_claim(row, packet_claim, packet_sources, cell_index, wb_source_id):
    """Produce (supporting[], candidates[], conflict, assumptions) for one claim by
    combining packet provenance with workbook cell matches."""
    supporting, candidates, assumptions = [], [], []
    conflict = None

    # ---- 1. packet provenance (carried verbatim) -------------------------
    declared_status = None
    metric = row.get("claim_headline")
    claim_value = None
    if packet_claim:
        declared_status = packet_claim.get("review_status")
        metric = packet_claim.get("metric") or metric
        claim_value = packet_claim.get("value")
        prov = packet_claim.get("provenance", {}) or {}
        for s in prov.get("supporting", []):
            if not s.get("source_id"):
                continue
            supporting.append(ES.link(s["source_id"], s.get("locator", ""),
                                      s.get("confidence", 0.0),
                                      s.get("rationale", "from source packet")))
        conflict = prov.get("conflict") or None
        if packet_claim.get("class") in JUDGEMENT_CLASSES:
            assumptions.append("%s is a %s (%s), not an observed actual" % (
                claim_value or metric, packet_claim.get("class"), declared_status))
    else:
        # no packet claim: take the source ids printed on the slide row at face value
        for sid in _split_src_ids(row.get("source_ids")):
            supporting.append(ES.link(sid, "(slide-declared)", 0.7,
                                      "source id declared on the slide; locator not in packet"))
        claim_value = _value_from_headline(row.get("claim_headline"))

    # ---- 2. workbook cell matches (the cross-app link) -------------------
    if cell_index is not None:
        match_tokens = _label_tokens(metric) or _label_tokens(row.get("claim_headline"))
        best_by_cell = {}
        for norm_label, entries in cell_index.items():
            cand_tokens = set(norm_label.split())
            if not match_tokens or not cand_tokens:
                continue
            overlap = match_tokens & cand_tokens
            if not overlap:
                continue
            label_score = len(overlap) / max(len(match_tokens), 1)
            for e in entries:
                agree, vnote = _values_agree(claim_value, e["value"])
                conf, why = _score_link(label_score, agree, e)
                best_by_cell[e["cell"]] = (conf, e, "%s; %s" % (why, vnote))
        # turn matches into supporting/candidate links
        wb_cells = []
        for cell, (conf, e, why) in sorted(best_by_cell.items(),
                                           key=lambda kv: kv[1][0], reverse=True):
            # `cell` is already a 'Sheet!Cell' locator from the index.
            link = ES.link(wb_source_id, cell, conf, why)
            if e["is_hardcode"]:
                link["rationale"] += " — cell is an UNSOURCED HARDCODE (held, not asserted)"
                assumptions.append("%s rests on an unsourced workbook hardcode at %s" % (
                    metric, e["cell"]))
            wb_cells.append((conf, e, link))
            if conf >= ES.LINK_THRESHOLD and not e["is_hardcode"]:
                supporting.append(link)
            else:
                candidates.append(link)

        # ---- 3. cross-cell conflict (same line, two tabs, different values) --
        cc = _cross_cell_conflict(metric, cell_index)
        if cc and not conflict:
            conflict = cc
        elif cc and conflict:
            # merge: note both
            conflict = dict(conflict)
            conflict["note"] = "%s; also: %s" % (conflict.get("note", ""), cc["note"])

    return supporting, candidates, conflict, assumptions, declared_status, metric


def _score_link(label_score, agree, entry):
    """Confidence for a workbook cell link, from label + value agreement. A hardcode
    is capped below threshold so it can never be silently asserted as provenance."""
    if label_score >= 0.99 and agree is True:
        conf, why = 0.9, "exact label + value match at %s" % entry["cell"]
    elif label_score >= 0.5 and agree is True:
        conf, why = 0.8, "label + value match at %s" % entry["cell"]
    elif agree is True:
        conf, why = 0.65, "value match at %s (weak label)" % entry["cell"]
    elif label_score >= 0.99 and agree is None:
        conf, why = 0.55, "label match at %s; value not comparable" % entry["cell"]
    elif label_score >= 0.5 and agree is False:
        conf, why = 0.45, "label matches %s but value differs" % entry["cell"]
    elif label_score >= 0.5:
        conf, why = 0.4, "label match at %s only" % entry["cell"]
    else:
        conf, why = 0.3, "weak match at %s" % entry["cell"]
    if entry["is_hardcode"]:
        conf = min(conf, 0.35)   # an unsourced hardcode is never asserted provenance
    return round(conf, 3), why


def _cross_cell_conflict(metric, cell_index):
    norm = _norm_label(metric)
    # find the index bucket whose tokens best cover the metric
    mt = set(norm.split())
    best_key, best_overlap = None, 0
    for k in cell_index:
        ov = len(mt & set(k.split()))
        if ov > best_overlap:
            best_key, best_overlap = k, ov
    if not best_key:
        return None
    entries = [e for e in cell_index[best_key] if isinstance(e["value"], (int, float))]
    vals = {}
    for e in entries:
        vals.setdefault(round(float(e["value"]), 6), []).append(e)
    if len(vals) >= 2:
        parts = ["%s=%s" % (es[0]["cell"], v) for v, es in vals.items()]
        return {"with": [es[0]["cell"] for es in vals.values()],
                "note": "same line carries different values across tabs: " + " vs ".join(parts)}
    return None


def _value_from_headline(h):
    if not h:
        return None
    m = re.search(r"[€$£]?\s?-?\d[\d,]*\.?\d*\s?%?", h)
    return m.group(0).strip() if m else None


# ===========================================================================
# Build
# ===========================================================================
def build_evidence_map(arch=None, packet=None, cell_index=None, pptx_rows=None,
                       wb_source_id="WB", wb_status="current", run_id=None,
                       workbook_path=None, verdict=None):
    packet = packet or {}
    claims_by_id = {c["id"]: c for c in packet.get("claims", [])}
    packet_sources = {s["id"]: s for s in packet.get("sources", [])}

    rows = (extract_claims_from_architecture(arch) if arch else None) or pptx_rows or []

    records, findings = [], []
    for row in rows:
        cid = row.get("claim_id")
        packet_claim = claims_by_id.get(cid) if cid else None
        supporting, candidates, conflict, assumptions, declared, metric = link_claim(
            row, packet_claim, packet_sources, cell_index, wb_source_id)

        # date range + owner from the packet source(s) backing the claim
        date_range, owner = _date_owner(supporting, packet_sources, packet_claim)
        calc = _calculation(row, packet_claim, supporting)
        claim_text = (packet_claim.get("provenance", {}) or {}).get("claim") if packet_claim else None
        claim_text = claim_text or row.get("claim_headline")

        rec = ES.evidence_record(
            slide=row.get("slide"), claim=claim_text,
            supporting=supporting, candidates=candidates, conflict=conflict,
            declared_status=declared, calculation=calc, assumptions=assumptions,
            date_range=date_range, owner=owner)
        rec["claim_id"] = cid
        rec["role"] = row.get("role")
        rec["chart"] = row.get("chart")
        problems = ES.validate_record(rec)
        if problems:
            rec["_schema_problems"] = problems
            findings.append({"severity": "must-fix", "slide": rec["slide"],
                             "issue": "evidence record fails schema: %s" % "; ".join(problems)})

        # the cardinal trust property — mark, never launder
        if rec["review_status"] == "unsupported":
            findings.append({"severity": "must-fix", "slide": rec["slide"],
                             "claim": rec["claim"], "claim_id": cid,
                             "issue": "claim has no above-threshold source — UNSUPPORTED "
                                      "(%d candidate(s) held for human approval)" % len(candidates),
                             "review_status": "unsupported"})
        elif rec["review_status"] == "conflicting":
            findings.append({"severity": "should-fix", "slide": rec["slide"],
                             "claim": rec["claim"], "claim_id": cid,
                             "issue": "claim rests on conflicting sources — show the authoritative "
                                      "value with a visible note", "review_status": "conflicting"})
        records.append(rec)

    # ---- the acceptance invariant ----------------------------------------
    # every claim is TRACED (>=1 supporting), CONFLICTED, or explicitly UNSUPPORTED —
    # and no record is silently sourced (a supporting link below threshold, or a
    # status that implies a source the links don't establish).
    def _accounted(r):
        if r["review_status"] == "unsupported":
            return True   # explicitly flagged
        if r["review_status"] == "conflicting":
            return True   # conflict surfaced
        return bool(r["supporting"])  # traced
    every_claim_accounted = all(_accounted(r) for r in records)
    none_silently_sourced = all(not ES.validate_record(r) for r in records)

    blocking = [f for f in findings if f["severity"] == "must-fix"]
    # The map RAN successfully even when it surfaces unsupported/conflicting claims —
    # surfacing them is the job. `findings` therefore yields ok-with-warnings, not a
    # block; the gap review (EM-03) + Trust Review own the block decision.
    status = "ok-with-warnings" if findings or cell_index is None else "ok"
    status_reason = ("Every claim is traced, conflicted, or explicitly unsupported; "
                     "%d unsupported and %d conflicting surfaced for the gap review."
                     % (sum(1 for r in records if r["review_status"] == "unsupported"),
                        sum(1 for r in records if r["review_status"] == "conflicting")))

    out = {
        "run_id": run_id,
        "skill": "evidence-map",
        "phase": "evidence-map",
        "status": status,
        "status_reason": status_reason,
        "consumes": [x for x in ["deck-architecture.json" if arch else None,
                                 "source-packet.json" if packet else None,
                                 os.path.basename(workbook_path) if workbook_path else None,
                                 "workbook-verdict.json" if verdict else None] if x],
        "link_threshold": ES.LINK_THRESHOLD,
        "workbook_source": {"id": wb_source_id, "status": wb_status,
                            "path": os.path.basename(workbook_path) if workbook_path else None},
        "records": records,
        "gate": {
            "every_claim_traced_conflicted_or_unsupported": every_claim_accounted,
            "none_silently_sourced": none_silently_sourced,
            "unsupported": [r["claim"] for r in records if r["review_status"] == "unsupported"],
            "conflicting": [r["claim"] for r in records if r["review_status"] == "conflicting"],
            "render_blockers": blocking,
            "findings": findings,
            "assertion": ("Every slide claim is tied to its source(s) with explicit confidence, "
                          "or flagged unsupported/conflicting. Sub-threshold links are HELD as "
                          "candidates for human approval — none is silently asserted as provenance."),
        },
    }
    return out


def _date_owner(supporting, packet_sources, packet_claim):
    dates, owners = [], []
    for s in supporting:
        src = packet_sources.get(s["source_id"])
        if src:
            if src.get("date") or src.get("date_range") or src.get("as_of"):
                dates.append(src.get("date") or src.get("date_range") or src.get("as_of"))
            if src.get("owner"):
                owners.append(src.get("owner"))
    date_range = "; ".join(ES._unique(dates)) if dates else None
    owner = "; ".join(ES._unique(owners)) if owners else (
        packet_claim.get("owner") if packet_claim else None)
    return date_range, owner


def _calculation(row, packet_claim, supporting):
    # a workbook formula locator implies a derivation; otherwise direct-from-source
    for s in supporting:
        loc = s.get("locator", "")
        if "!" in loc and re.search(r"![A-Z]+\d", loc):
            return "traceable to %s" % loc
    if packet_claim and packet_claim.get("class") in ("interpretation", "estimate"):
        return "derived/estimated — see assumptions"
    return "direct from source"


# ===========================================================================
# Speaker-note injection (EM-02 second half)
# ===========================================================================
def inject_notes(deck_path, records, out_path, packet_sources=None):
    """Write the §2.1 evidence note into each slide's speaker notes. Slides are
    matched to records by 1-based slide index. Returns (count_written, problems)."""
    try:
        from pptx import Presentation
    except ImportError:
        return 0, ["python-pptx not importable — emit evidence.md beside the deck instead"]
    prs = Presentation(deck_path)
    by_slide = {}
    for r in records:
        by_slide.setdefault(r["slide"], []).append(r)
    written = 0
    for i, slide in enumerate(prs.slides, start=1):
        recs = by_slide.get(i)
        if not recs:
            continue
        note = "\n\n".join(ES.speaker_note(r, packet_sources) for r in recs)
        slide.notes_slide.notes_text_frame.text = note
        written += 1
    prs.save(out_path)
    return written, []


# ===========================================================================
def render_md(emap):
    L = []
    L.append("# Evidence Map — %s" % (emap.get("run_id") or ""))
    L.append("")
    L.append("> **Status: `%s`** — %s" % (emap["status"], emap["status_reason"]))
    L.append(">")
    L.append("> %s" % emap["gate"]["assertion"])
    L.append("")
    g = emap["gate"]
    L.append("- **Every claim traced, conflicted, or unsupported:** %s" % (
        "✅ yes" if g["every_claim_traced_conflicted_or_unsupported"] else "❌ NO"))
    L.append("- **None silently sourced:** %s" % (
        "✅ yes" if g["none_silently_sourced"] else "❌ NO"))
    L.append("- **Link threshold (assert vs hold):** %.2f" % emap["link_threshold"])
    L.append("- **Unsupported claims:** %d · **Conflicting:** %d" % (
        len(g["unsupported"]), len(g["conflicting"])))
    L.append("")
    L.append("| # | Claim | Status | Conf | Sources | Cells | Candidates |")
    L.append("|---|---|---|---|---|---|---|")
    for r in emap["records"]:
        L.append("| %s | %s | `%s` | %.2f | %s | %s | %s |" % (
            r["slide"], (r["claim"] or "").replace("|", "\\|")[:60], r["review_status"],
            r["confidence"], ", ".join(r["source_ids"]) or "—",
            ", ".join(r["cells"]) or "—", str(len(r["candidates"]))))
    L.append("")
    L.append("## Per-claim evidence notes (the §2.1 template, as injected)")
    L.append("")
    for r in emap["records"]:
        L.append("**Slide %s — %s**" % (r["slide"], r["claim"]))
        L.append("")
        L.append("```")
        L.append(ES.speaker_note(r))
        L.append("```")
        if r["candidates"]:
            L.append("_Candidate links held for approval:_")
            for c in r["candidates"]:
                L.append("  - `%s` (%.2f) — %s" % (c["locator"], c["confidence"], c["rationale"]))
        L.append("")
    if g["findings"]:
        L.append("## Findings handed to the gap review / Trust Review")
        for f in g["findings"]:
            L.append("- `%s` slide %s: %s" % (f["severity"], f.get("slide"), f["issue"]))
        L.append("")
    return "\n".join(L)


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="EM-02 evidence-map builder + speaker-notes generator")
    ap.add_argument("--architecture", help="deck-architecture.json (the slide map)")
    ap.add_argument("--source-packet", required=True)
    ap.add_argument("--workbook", help="backing model .xlsx (cell index)")
    ap.add_argument("--workbook-verdict", help="workbook-verdict.json (assumptions, optional)")
    ap.add_argument("--deck", help="deck.pptx to inject evidence notes into (copy written)")
    ap.add_argument("--wb-source-id", default="WB")
    ap.add_argument("--wb-status", default="current")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    try:
        packet = _load(args.source_packet)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "failed", "status_reason": "cannot read source packet: %s" % e}))
        return 2
    arch = _load(args.architecture) if args.architecture and os.path.exists(args.architecture) else None
    verdict = _load(args.workbook_verdict) if args.workbook_verdict and os.path.exists(args.workbook_verdict) else None

    cell_index, wb_warn = (None, None)
    if args.workbook and os.path.exists(args.workbook):
        cell_index, wb_warn = build_cell_index(args.workbook)

    pptx_rows = None
    if arch is None and args.deck and os.path.exists(args.deck):
        pptx_rows = extract_claims_from_pptx(args.deck)

    run_id = args.run_id or (arch or {}).get("run_id") or packet.get("run_id") or (_now_slug() + "_evidence-map")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    emap = build_evidence_map(
        arch=arch, packet=packet, cell_index=cell_index, pptx_rows=pptx_rows,
        wb_source_id=args.wb_source_id, wb_status=args.wb_status, run_id=run_id,
        workbook_path=args.workbook, verdict=verdict)
    if wb_warn:
        emap["status"] = "ok-with-warnings"
        emap["status_reason"] += "  (workbook cell index unavailable: %s)" % wb_warn

    jpath = os.path.join(out_dir, "evidence-map.json")
    mpath = os.path.join(out_dir, "evidence-map.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(emap, fh, indent=2, ensure_ascii=False)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(render_md(emap))

    note = ""
    if args.deck and os.path.exists(args.deck):
        packet_sources = {s["id"]: s for s in packet.get("sources", [])}
        base = os.path.splitext(os.path.basename(args.deck))[0]
        out_deck = os.path.join(out_dir, base + "-evidenced.pptx")
        written, probs = inject_notes(args.deck, emap["records"], out_deck, packet_sources)
        note = "  notes injected into %d slide(s) -> %s" % (written, os.path.basename(out_deck))
        if probs:
            note += "  (%s)" % "; ".join(probs)

    print("evidence-map -> %s (status: %s)" % (out_dir, emap["status"]))
    print("  every claim traced/conflicted/unsupported: %s | none silently sourced: %s" % (
        emap["gate"]["every_claim_traced_conflicted_or_unsupported"],
        emap["gate"]["none_silently_sourced"]))
    print("  unsupported: %d | conflicting: %d" % (
        len(emap["gate"]["unsupported"]), len(emap["gate"]["conflicting"])))
    if note:
        print(note)
    return 0 if emap["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
