#!/usr/bin/env python3
"""
Office Trust Layer — Evidence Map · EM-04: the cross-app linker (Excel → deck), HARDENED.

The signature move of the whole product — auto-connect a vetted workbook to its deck
so a board number on a slide traces to the model cell that produced it — made SAFE.

review-1 finding 9: *semantic matching can manufacture convincing but FALSE provenance.*
A single tidy "this cell backs this claim" link is exactly the move that, done wrong,
makes a deck **more** dangerous, not less: a wrong link launders an unsourced or
mis-period number into something that *looks* audited. So this linker never asserts a
link it cannot stand behind, and it shows its work.

What "hardened" means here, concretely, beyond the EM-02 builder:

  1. CONFIDENCE SCORES — every candidate link carries a 0–1 confidence with a fully
     itemised breakdown (label · value · unit · period · anchor), not a black-box number.
  2. MULTIPLE CANDIDATE LINKS — a claim that plausibly matches several workbook outputs
     keeps ALL of them, ranked, each with its own rationale — never silently collapses
     to one "best" guess.
  3. SOURCE / DATE / UNIT COMPATIBILITY CHECKS — a match is not just label+value. The
     linker checks the UNIT (€M vs % vs headcount) and the PERIOD/date (FY25 vs FY24,
     Q3 vs Q2) actually agree. An INCOMPATIBLE unit or period is a HARD CAP: the link
     can never reach the assert threshold, no matter how well the label and the number
     line up. This is the specific guard against "same label, same number, wrong thing."
  4. DETERMINISTIC ANCHORS PREFERRED — a named range or an exact single-cell locator
     (a deterministic anchor) scores above a fuzzy label match. A workbook HARDCODE
     (an unsourced literal in a calc column) is capped below threshold — "the number is
     in a cell" is not a source.
  5. NEVER AUTO-ACCEPT BELOW THRESHOLD — a link at/above `ASSERT_THRESHOLD` is asserted
     as `supporting` provenance; anything below it is HELD as a candidate for human
     approval (`held_for_approval`), never silently promoted. A match too weak to even
     surface (below `CANDIDATE_FLOOR`) is dropped. NEVER GUESS.

I/O (`references/conventions.md §3`):
  in : workbook outputs index — either a Workbook Doctor `workbook-map.json` that carries
       an `outputs[]` section, or the EM-02 `build_cell_index()` structure (adapted), or a
       normalized `{outputs:[{label,cell,named,value,unit,period,is_hardcode}]}` list.
       deck claims — the DA/EM `deck-architecture.json` slide map, or a
       `{claims:[{slide,claim,value,unit,period,source_ids}]}` list.
  out: <out>/evidence-map.json  (one record per claim, with confidence + HELD links + the
       per-link compatibility breakdown + the gate)
       <out>/evidence-map.md     (human memo)

Status: `ok` / `ok-with-warnings` (held links present — a human must approve) / `blocked`
(a required input missing) / `failed`.

Enums (`review_status`, `severity`, `source_status`) are VERBATIM from conventions.md.
Pure stdlib + the EM-01 schema (`evidence_schema`); no third-party import at link time.
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

# ---------------------------------------------------------------------------
# Thresholds. ASSERT_THRESHOLD == the EM-01 LINK_THRESHOLD so the assert/hold cut
# is the SAME number the schema validator enforces (a sub-threshold link in
# `supporting` is rejected there too — belt and braces).
ASSERT_THRESHOLD = ES.LINK_THRESHOLD          # >= this  -> asserted provenance
CANDIDATE_FLOOR = 0.25                         # below this -> too weak to even surface
# Hard caps applied when a compatibility check FAILS. A failed check can never be
# argued back above the assert line by a strong label/value — that is the point.
CAP_UNIT_MISMATCH = 0.30
CAP_PERIOD_MISMATCH = 0.30
CAP_VALUE_MISMATCH = 0.30
CAP_HARDCODE = 0.35                            # an unsourced literal is never asserted


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# Normalisation helpers (label / number / unit / period)
# ===========================================================================
_STOP = {"the", "of", "a", "total", "net", "gross"}


def _norm_label(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)            # drop parenthetical units "(€m)"
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in _STOP)


def _label_tokens(s):
    return set(_norm_label(s).split())


def _parse_number(text):
    """Leading numeric magnitude from a value like '€48.2M', '18.5%', '1,195',
    '12% YoY' -> float, or None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(text).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# ---- UNIT detection + compatibility ---------------------------------------
# A small, explicit unit vocabulary. Unknown -> None (we do NOT guess a unit).
_CCY = ("€", "$", "£", "eur", "usd", "gbp")


def detect_unit(value_text, label=None):
    """Best-effort unit of a value/label: 'percent' | 'currency' | 'count' | None.
    Never guesses beyond these explicit signals — an undetectable unit is None, and
    None never CONFIRMS compatibility (it can't veto, but it can't assert either)."""
    blob = "%s %s" % (value_text if value_text is not None else "", label or "")
    low = blob.lower()
    if "%" in blob or re.search(r"\b(pct|percent|margin|yoy|growth|rate|bps|pp)\b", low):
        return "percent"
    if any(c in blob for c in _CCY) or re.search(r"\b(revenue|ebitda|cost|capex|opex|cash|€m|eur|usd|gbp)\b", low):
        # a currency word OR symbol. Note: a bare number with a € label is currency.
        if any(c in blob for c in ("€", "$", "£")) or re.search(r"\b(revenue|ebitda|cost|capex|opex|cash|€m|eur|usd|gbp)\b", low):
            return "currency"
    if re.search(r"\b(headcount|fte|employees|count|units|seats|people)\b", low):
        return "count"
    return None


def unit_compat(claim_unit, cell_unit):
    """Are the two units compatible? True / False / None(unknown).
    Two KNOWN, DIFFERENT units are incompatible (the false-provenance trap). If
    either side is unknown we return None — not confirmed, not vetoed."""
    if claim_unit is None or cell_unit is None:
        return None
    return claim_unit == cell_unit


# ---- PERIOD / date detection + compatibility ------------------------------
_PERIOD_RE = re.compile(r"(FY\s?\d{2,4}|Q[1-4]\s?[- ]?(?:FY)?\s?\d{2,4}|H[12]\s?\d{2,4}|"
                        r"\b20\d{2}\b|\bQ[1-4]\b|\bFY\d{2}\b)", re.I)


def detect_period(*texts):
    """Pull a period/date token (FY25, Q3-FY26, 2025, H1 2025) from any of the
    texts. None if none present — we do NOT invent a period."""
    for t in texts:
        if not t:
            continue
        m = _PERIOD_RE.search(str(t))
        if m:
            return re.sub(r"\s+", "", m.group(0).upper())
    return None


def period_compat(claim_period, cell_period):
    """Compatible periods? True / False / None(unknown). Two KNOWN, DIFFERENT
    periods are incompatible. Substring tolerance (Q3FY26 ⊇ FY26) counts as
    compatible; FY25 vs FY24 is incompatible."""
    if not claim_period or not cell_period:
        return None
    a, b = claim_period, cell_period
    if a == b:
        return True
    if a in b or b in a:
        return True
    return False


def _values_agree(claim_num, cell_num):
    """Magnitudes agree, tolerating %/fraction (0.185 ~ 18.5) and €M scaling?
    Returns True / False / None(not comparable)."""
    if claim_num is None or cell_num is None:
        return None
    cands = {cell_num}
    if abs(cell_num) <= 1.0:
        cands.add(cell_num * 100.0)        # fraction stored as 0.185 -> 18.5%
    for c in cands:
        if c == 0:
            if claim_num == 0:
                return True
            continue
        if abs(claim_num - c) / max(abs(c), 1e-9) < 0.01:
            return True
    return False


# ===========================================================================
# Input adapters — meet the workbook index where it lives
# ===========================================================================
def outputs_from_workbook_map(wbmap):
    """A Workbook Doctor workbook-map.json may carry an explicit `outputs` section
    (label/cell/value/unit/period/named/is_hardcode). Use it verbatim if present."""
    outs = wbmap.get("outputs")
    if outs:
        return [_normalize_output(o) for o in outs]
    return None


def outputs_from_cell_index(cell_index):
    """Adapt the EM-02 build_cell_index() structure ({norm_label: [entry]}) into the
    normalized outputs list this linker consumes."""
    outs = []
    for _, entries in (cell_index or {}).items():
        for e in entries:
            outs.append(_normalize_output({
                "label": e.get("label"),
                "cell": e.get("cell"),
                "value": e.get("value"),
                "is_hardcode": e.get("is_hardcode", False),
                "named": False,
            }))
    return outs


def _normalize_output(o):
    label = o.get("label")
    value = o.get("value")
    unit = o.get("unit") or detect_unit(value if isinstance(value, str) else None, label)
    period = o.get("period") or detect_period(label, o.get("cell"))
    cell = o.get("cell") or ""
    # a deterministic anchor: an explicit named range, or a single-cell locator
    named = bool(o.get("named")) or bool(re.search(r"![A-Z]{1,3}\d+$", str(cell)))
    return {
        "label": label,
        "cell": cell,
        "named": named,
        "value": value,
        "value_num": _parse_number(value),
        "unit": unit,
        "period": period,
        "is_hardcode": bool(o.get("is_hardcode", False)),
    }


def claims_from_architecture(arch):
    """Pull the evidence-bearing rows from a DA/EM slide map as linkable claims."""
    rows = []
    for r in arch.get("slide_map", []):
        if r.get("role") in ("title", "section"):
            continue
        note_req = (r.get("note_requirement") or "")
        if not (note_req.startswith("required") or r.get("review_status") == "unsupported"):
            continue
        headline = r.get("claim_headline") or ""
        rows.append({
            "slide": r.get("slide"),
            "claim": headline,
            "claim_id": r.get("claim_id"),
            "value": _value_from_text(headline) or r.get("value"),
            "unit": r.get("unit") or detect_unit(headline, headline),
            "period": r.get("period") or detect_period(headline),
            "source_ids": r.get("source_ids", []),
            "review_status": r.get("review_status"),
            "chart": r.get("chart"),
        })
    return rows


def _value_from_text(h):
    """Pull the VALUE token from a claim headline, NOT a period token. 'Q3-FY26 revenue =
    €48.2M' must yield '€48.2M', never the '3' in 'Q3'. Period labels are stripped first;
    then a currency/percent/scaled number is preferred, then the figure after '='."""
    if not h:
        return None
    h2 = re.sub(r"\b(Q[1-4]\s?-?\s?(?:FY)?\s?\d{2,4}|FY\s?\d{2,4}|H[12]\s?\d{2,4})\b", " ", h, flags=re.I)
    h2 = re.sub(r"\bQ[1-4]\b", " ", h2, flags=re.I)
    for pat in (r"[€$£]\s?-?\d[\d,]*\.?\d*\s?[MmKkBb]?",     # €48.2M
                r"-?\d[\d,]*\.?\d*\s?%",                       # 12%
                r"-?\d[\d,]*\.?\d*\s?[MmKkBb]\b"):            # 52M
        m = re.search(pat, h2)
        if m:
            return m.group(0).strip()
    m = re.search(r"=\s*([€$£]?\s?-?\d[\d,]*\.?\d*\s?[%MmKkBb]?)", h2)
    if m:
        return m.group(1).strip()
    m = re.search(r"-?\d[\d,]*\.?\d*\s?[%MmKkBb]?", h2)
    return m.group(0).strip() if m else None


def _normalize_claim(c):
    val = c.get("value") if c.get("value") is not None else _value_from_text(c.get("claim"))
    return {
        "slide": c.get("slide"),
        "claim": c.get("claim"),
        "claim_id": c.get("claim_id"),
        "value": val,
        "value_num": _parse_number(val),
        "unit": c.get("unit") or detect_unit(val if isinstance(val, str) else c.get("claim"), c.get("claim")),
        "period": c.get("period") or detect_period(c.get("claim"), str(val)),
        "source_ids": c.get("source_ids", []) or [],
        "review_status": c.get("review_status"),
        "chart": c.get("chart"),
    }


# ===========================================================================
# The scored, compatibility-checked match — the hardened core
# ===========================================================================
def score_match(claim, output):
    """Score one (claim, output) pair. Returns (confidence, breakdown, rationale).

    The confidence is built up transparently from five components and then HARD-CAPPED
    by any failed compatibility check, so a strong label+value can never argue an
    incompatible unit or period back above the assert line."""
    bd = {}        # the itemised breakdown that travels with the link
    caps = []      # hard caps that fired (each is a ceiling on the final confidence)
    reasons = []

    # --- label compatibility --------------------------------------------------
    ct = _label_tokens(claim["claim"]) | _label_tokens(claim.get("value") or "")
    ot = _label_tokens(output["label"])
    overlap = ct & ot
    label_score = (len(overlap) / max(len(ot), 1)) if ot else 0.0
    bd["label"] = {"score": round(label_score, 3), "overlap": sorted(overlap)}
    label_pts = 0.40 * label_score
    if overlap:
        reasons.append("label overlap %s" % sorted(overlap))
    else:
        reasons.append("no label overlap")

    # --- value compatibility --------------------------------------------------
    v = _values_agree(claim.get("value_num"), output.get("value_num"))
    bd["value"] = {"agree": v, "claim": claim.get("value_num"), "cell": output.get("value_num")}
    if v is True:
        value_pts = 0.30
        reasons.append("value matches (%s≈%s)" % (claim.get("value_num"), output.get("value_num")))
    elif v is False:
        value_pts = 0.0
        caps.append(CAP_VALUE_MISMATCH)
        reasons.append("value DIFFERS (%s vs %s) — capped" % (claim.get("value_num"), output.get("value_num")))
    else:
        value_pts = 0.0
        reasons.append("value not comparable")

    # --- unit compatibility (the false-provenance guard) ----------------------
    u = unit_compat(claim.get("unit"), output.get("unit"))
    bd["unit"] = {"compatible": u, "claim": claim.get("unit"), "cell": output.get("unit")}
    if u is True:
        unit_pts = 0.15
        reasons.append("unit compatible (%s)" % claim.get("unit"))
    elif u is False:
        unit_pts = 0.0
        caps.append(CAP_UNIT_MISMATCH)
        reasons.append("unit INCOMPATIBLE (%s vs %s) — HELD, not asserted"
                       % (claim.get("unit"), output.get("unit")))
    else:
        unit_pts = 0.0
        reasons.append("unit unknown on one side (not confirmed)")

    # --- period / date compatibility ------------------------------------------
    p = period_compat(claim.get("period"), output.get("period"))
    bd["period"] = {"compatible": p, "claim": claim.get("period"), "cell": output.get("period")}
    if p is True:
        period_pts = 0.15
        reasons.append("period compatible (%s)" % claim.get("period"))
    elif p is False:
        period_pts = 0.0
        caps.append(CAP_PERIOD_MISMATCH)
        reasons.append("period INCOMPATIBLE (%s vs %s) — HELD, not asserted"
                       % (claim.get("period"), output.get("period")))
    else:
        period_pts = 0.0
        reasons.append("period unknown on one side (not confirmed)")

    # --- deterministic anchor preference + hardcode cap -----------------------
    anchor_pts = 0.0
    if output.get("named"):
        anchor_pts = 0.10
        bd["anchor"] = {"deterministic": True, "cell": output.get("cell")}
        reasons.append("deterministic anchor at %s" % output.get("cell"))
    else:
        bd["anchor"] = {"deterministic": False, "cell": output.get("cell")}
    if output.get("is_hardcode"):
        caps.append(CAP_HARDCODE)
        bd["hardcode"] = True
        reasons.append("cell is an UNSOURCED HARDCODE — capped (the number being in a cell is not a source)")

    raw = label_pts + value_pts + unit_pts + period_pts + anchor_pts
    conf = raw
    for cap in caps:
        conf = min(conf, cap)
    conf = round(max(0.0, min(1.0, conf)), 3)
    bd["raw_score"] = round(raw, 3)
    bd["caps_applied"] = sorted(set(round(c, 3) for c in caps))
    return conf, bd, "; ".join(reasons)


def link_claim(claim, outputs, wb_source_id="WB"):
    """Produce (supporting[], candidates[], held[], conflict) for one claim against all
    workbook outputs. Every plausible output is kept (multiple candidates); each link
    carries its confidence + compatibility breakdown + rationale. The assert/hold cut is
    ASSERT_THRESHOLD; sub-threshold links are HELD, never asserted."""
    scored = []
    for o in outputs:
        conf, bd, why = score_match(claim, o)
        # NEVER GUESS: a cell with ZERO label overlap is not about this claim at all —
        # a compatible unit/period/anchor on an unrelated line must not manufacture a
        # candidate. A link must share at least one label token to be recorded.
        if bd["label"]["score"] <= 0.0:
            continue
        if conf < CANDIDATE_FLOOR:
            continue   # too weak to even surface — not a guess we record
        link = ES.link(wb_source_id, o["cell"], conf, why)
        link["compatibility"] = bd
        link["output_label"] = o["label"]
        scored.append((conf, bd["label"]["score"], o, link))

    scored.sort(key=lambda t: t[0], reverse=True)
    supporting, candidates, held = [], [], []
    for conf, _ls, o, link in scored:
        if conf >= ASSERT_THRESHOLD and not o["is_hardcode"]:
            supporting.append(link)
        else:
            candidates.append(link)
            held.append(link)

    # cross-cell conflict: two cells that BOTH strongly match this claim's LABEL and are
    # UNIT-compatible but carry DIFFERENT values — a genuine disagreement the human must
    # reconcile (e.g. headcount 1,195 vs 1,180). Restricted to strong-label + unit-compatible
    # matches so an unrelated line (a YoY% sharing the word 'revenue') never fabricates one.
    conflict = _cross_value_conflict(claim, scored)
    return supporting, candidates, held, conflict


def _CONFLICT_LABEL_MIN():
    return 0.66


def _cross_value_conflict(claim, scored):
    """If two STRONG-label, UNIT-COMPATIBLE outputs back the claim at DIFFERENT values, that
    is a conflict the human must reconcile — surface it rather than pick one."""
    cu = (claim or {}).get("unit")
    strong = [(o, lk) for conf, ls, o, lk in scored
              if ls >= _CONFLICT_LABEL_MIN() and o.get("value_num") is not None
              and unit_compat(cu, o.get("unit")) is not False]
    vals = {}
    for o, lk in strong:
        vals.setdefault(round(float(o["value_num"]), 6), []).append(o)
    if len(vals) >= 2:
        parts = ["%s=%s" % (os_[0]["cell"], v) for v, os_ in vals.items()]
        return {"with": [os_[0]["cell"] for os_ in vals.values()],
                "note": "claim matches cells carrying different values: " + " vs ".join(parts)}
    return None


# ===========================================================================
# Build
# ===========================================================================
def build_link_map(claims, outputs, wb_source_id="WB", wb_status="current",
                   run_id=None, packet=None):
    packet = packet or {}
    packet_claims = {c.get("id"): c for c in packet.get("claims", [])}
    records, held_all, findings = [], [], []

    for raw in claims:
        claim = _normalize_claim(raw)
        supporting, candidates, held, conflict = link_claim(claim, outputs, wb_source_id)

        # carry packet provenance verbatim ALONGSIDE the workbook links (multi-source)
        pc = packet_claims.get(claim.get("claim_id"))
        declared = None
        if pc:
            declared = pc.get("review_status")
            prov = pc.get("provenance", {}) or {}
            for s in prov.get("supporting", []):
                if s.get("source_id"):
                    supporting.insert(0, ES.link(s["source_id"], s.get("locator", ""),
                                                 s.get("confidence", 0.0),
                                                 s.get("rationale", "from source packet")))
            if prov.get("conflict") and not conflict:
                conflict = prov.get("conflict")

        rec = ES.evidence_record(
            slide=claim.get("slide"), claim=claim.get("claim"),
            supporting=supporting, candidates=candidates, conflict=conflict,
            declared_status=declared,
            calculation=_calc(supporting), assumptions=_assumptions(candidates),
            date_range=claim.get("period"), owner=None)
        rec["claim_id"] = claim.get("claim_id")
        rec["chart"] = claim.get("chart")
        rec["held_for_approval"] = held
        problems = ES.validate_record(rec)
        if problems:
            rec["_schema_problems"] = problems
            findings.append({"severity": "must-fix", "slide": rec["slide"],
                             "issue": "record fails schema: %s" % "; ".join(problems)})

        for lk in held:
            held_all.append({"slide": rec["slide"], "claim": rec["claim"],
                             "locator": lk["locator"], "confidence": lk["confidence"],
                             "rationale": lk["rationale"], "compatibility": lk["compatibility"]})

        if rec["review_status"] == "unsupported":
            findings.append({"severity": "must-fix", "slide": rec["slide"],
                             "claim": rec["claim"], "claim_id": rec.get("claim_id"),
                             "issue": "no above-threshold link — UNSUPPORTED (%d candidate(s) "
                                      "held for human approval)" % len(candidates),
                             "review_status": "unsupported"})
        elif rec["review_status"] == "conflicting":
            findings.append({"severity": "should-fix", "slide": rec["slide"],
                             "claim": rec["claim"], "claim_id": rec.get("claim_id"),
                             "issue": "matches conflict — reconcile before it travels",
                             "review_status": "conflicting"})
        records.append(rec)

    # ---- the acceptance GATE ---------------------------------------------
    # No held (sub-threshold) link is ever in a `supporting` bucket; every link carries
    # a compatibility breakdown; nothing below the floor was guessed into a link.
    def _held_locs(r):
        return {lk["locator"] for lk in r.get("held_for_approval", [])}
    no_silent_link = all(
        not (_held_locs(r) & {s["locator"] for s in r["supporting"]}) for r in records)
    all_have_breakdown = all(
        ("compatibility" in lk) for r in records
        for lk in (r["candidates"] + [s for s in r["supporting"] if s.get("source_id", "").startswith(("WB", wb_source_id))]))
    none_silently_sourced = all(not ES.validate_record(r) for r in records)

    status = "ok-with-warnings" if (held_all or findings) else "ok"
    out = {
        "run_id": run_id,
        "skill": "evidence-map",
        "phase": "cross-app-link",
        "status": status,
        "status_reason": ("%d link(s) HELD for human approval (sub-threshold / incompatible "
                          "unit or period); %d claim(s) unsupported."
                          % (len(held_all), sum(1 for r in records if r["review_status"] == "unsupported"))),
        "assert_threshold": ASSERT_THRESHOLD,
        "candidate_floor": CANDIDATE_FLOOR,
        "workbook_source": {"id": wb_source_id, "status": wb_status},
        "records": records,
        "held_for_approval": held_all,
        "gate": {
            "no_silent_link_below_threshold": no_silent_link,
            "every_link_has_compatibility_breakdown": all_have_breakdown,
            "auto_accept_requires_threshold": True,
            "asserted_links": sum(len([s for s in r["supporting"]
                                       if s.get("source_id") == wb_source_id]) for r in records),
            "held_links": len(held_all),
            "unsupported": [r["claim"] for r in records if r["review_status"] == "unsupported"],
            "conflicting": [r["claim"] for r in records if r["review_status"] == "conflicting"],
            "findings": findings,
            "assertion": ("Every workbook→claim link carries an itemised confidence "
                          "(label·value·unit·period·anchor). An incompatible unit or period "
                          "is a hard cap below the assert threshold, so a convincing label+value "
                          "match with the wrong unit/period is HELD for human approval — never "
                          "silently asserted as provenance. Never guess."),
        },
    }
    return out


def _calc(supporting):
    for s in supporting:
        loc = s.get("locator", "")
        if "!" in loc and re.search(r"![A-Z]+\d", loc):
            return "traceable to %s" % loc
    return "direct from source"


def _assumptions(candidates):
    out = []
    for c in candidates:
        bd = c.get("compatibility", {})
        if bd.get("hardcode"):
            out.append("a candidate cell (%s) is an unsourced hardcode — not asserted" % c["locator"])
        if bd.get("unit", {}).get("compatible") is False:
            out.append("a candidate cell (%s) has an incompatible unit — held for approval" % c["locator"])
        if bd.get("period", {}).get("compatible") is False:
            out.append("a candidate cell (%s) has an incompatible period — held for approval" % c["locator"])
    return ES._unique(out)


# ===========================================================================
def render_md(emap):
    L = ["# Cross-App Link Map (EM-04, hardened) — %s" % (emap.get("run_id") or "")]
    g = emap["gate"]
    L += ["", "> **Status: `%s`** — %s" % (emap["status"], emap["status_reason"]),
          ">", "> %s" % g["assertion"], ""]
    L += ["- **No sub-threshold link silently asserted:** %s" %
          ("✅ yes" if g["no_silent_link_below_threshold"] else "❌ NO"),
          "- **Every link carries a compatibility breakdown:** %s" %
          ("✅ yes" if g["every_link_has_compatibility_breakdown"] else "❌ NO"),
          "- **Assert threshold:** %.2f · **Candidate floor:** %.2f"
          % (emap["assert_threshold"], emap["candidate_floor"]),
          "- **Asserted links:** %d · **Held for approval:** %d"
          % (g["asserted_links"], g["held_links"]), ""]
    L += ["| # | Claim | Status | Conf | Asserted cells | Held (for approval) |",
          "|---|---|---|---|---|---|"]
    for r in emap["records"]:
        held = "; ".join("%s (%.2f)" % (h["locator"], h["confidence"])
                         for h in r.get("held_for_approval", [])) or "—"
        L.append("| %s | %s | `%s` | %.2f | %s | %s |" % (
            r["slide"], (r["claim"] or "")[:48].replace("|", "\\|"), r["review_status"],
            r["confidence"], ", ".join(r["cells"]) or "—", held))
    L.append("")
    if emap["held_for_approval"]:
        L += ["## Held for human approval (NOT linked)", ""]
        for h in emap["held_for_approval"]:
            L.append("- slide %s · **%s** · `%s` conf **%.2f** — %s"
                     % (h["slide"], h["claim"], h["locator"], h["confidence"], h["rationale"]))
        L.append("")
    return "\n".join(L)


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="EM-04 hardened cross-app linker (Excel -> deck)")
    ap.add_argument("--workbook-map", help="Workbook Doctor workbook-map.json (with outputs[]) "
                                           "OR a normalized {outputs:[...]} index")
    ap.add_argument("--architecture", help="deck-architecture.json (deck claims)")
    ap.add_argument("--claims", help="a {claims:[...]} json (alternative to --architecture)")
    ap.add_argument("--source-packet", help="source-packet.json (provenance carried alongside)")
    ap.add_argument("--wb-source-id", default="WB")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    if not args.workbook_map:
        print(json.dumps({"status": "blocked",
                          "status_reason": "no workbook index (--workbook-map) provided"}))
        return 0
    wbmap = _load(args.workbook_map)
    outputs = outputs_from_workbook_map(wbmap)
    if outputs is None:
        # maybe it's already a normalized {outputs:[...]} list
        outputs = [_normalize_output(o) for o in wbmap.get("outputs", [])]
    if not outputs:
        print(json.dumps({"status": "blocked",
                          "status_reason": "workbook index carries no outputs[] to link against"}))
        return 0

    claims = None
    if args.architecture and os.path.exists(args.architecture):
        claims = claims_from_architecture(_load(args.architecture))
    elif args.claims and os.path.exists(args.claims):
        claims = _load(args.claims).get("claims", [])
    if not claims:
        print(json.dumps({"status": "blocked", "status_reason": "no deck claims provided"}))
        return 0

    packet = _load(args.source_packet) if args.source_packet and os.path.exists(args.source_packet) else None
    run_id = args.run_id or wbmap.get("run_id") or (_now_slug() + "_cross-app-link")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    emap = build_link_map(claims, outputs, wb_source_id=args.wb_source_id,
                          run_id=run_id, packet=packet)
    with open(os.path.join(out_dir, "evidence-map.json"), "w", encoding="utf-8") as fh:
        json.dump(emap, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "evidence-map.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(emap))

    print("cross-app-link -> %s (status: %s)" % (out_dir, emap["status"]))
    print("  asserted: %d | held for approval: %d | unsupported: %d"
          % (emap["gate"]["asserted_links"], emap["gate"]["held_links"],
             len(emap["gate"]["unsupported"])))
    print("  no sub-threshold link silently asserted: %s"
          % emap["gate"]["no_silent_link_below_threshold"])
    return 0 if emap["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
