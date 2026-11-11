#!/usr/bin/env python3
"""
Office Trust Layer — Deck Architect · DA-01: deck-architecture schema + narrative-spine GATE.

Phase 1 of the Deck Architect, and the start of the M2 PowerPoint path. It makes
the ARGUMENT before any slide exists — Nate's "structure before slides" rule:
if you render first, polish hides a weak argument (review-1 finding 11).

Consumes only the truth layer (never raw sources):
  * `source-packet.json`  (SP-01) — sources (S<n> + source_status), claims with the
                                     frozen provenance record, the conflict log, open questions.
  * `file-spec.json`      (SP-02, optional but preferred for decks) — the deck slide-map
                                     skeleton (slides + headlines + claim_ids + sensitivity),
                                     the audience, the decisions-required gate.

Produces, per the I/O contract (`references/conventions.md §3`):
  <out>/deck-architecture.json   (machine — spine + slide map + the gate)
  <out>/deck-architecture.md     (human review surface)

It emits two things and one gate:

  1. THE NARRATIVE SPINE — audience · decision · what_they_know · what_they_must_believe ·
     one-sentence spine · primary_misread_risk. The spine is a JUDGEMENT proposal: it is
     emitted with `review_status: needs-review` because a human owns the argument, not the
     harness. The misread risk is COMPUTED from the packet (an estimate that could read as an
     actual, a human-decision conflict, an unsupported claim that slipped into the slide-map).

  2. THE SLIDE MAP — one row per slide, exactly the DA-01 schema:
       slide · claim_headline (a SENTENCE, a claim — never a topic) · role · source_ids ·
       workbook_refs · chart · assumptions · open_questions · note_requirement · review_status.
     The headline is the claim's provenance sentence (a full assertion), NOT the file-spec's
     short topic label ("Revenue €48.2M"); the original topic label is preserved in
     `source_headline` so the claims-not-topics transform is auditable.

  3. THE GATE — a HARD STOP. status `blocked` ("no rendering until the architecture is
     approved"). The architecture is only releasable to the render step when the invariant
     holds: EVERY claim headline either names ≥1 source OR is explicitly flagged
     `review_status: unsupported`. An unsupported headline that is still slotted to render is
     a `must-fix` that blocks. Section/agenda/ask rows that assert no number need no source.

Enums (`source_status` / `review_status` / `severity` / `readiness` / `status`) are taken
VERBATIM from `references/conventions.md` — never redefined here. The provenance record and
the speaker-note/evidence template are the frozen shapes from §2 / §2.1.

Usage:
  python deck_architecture.py --source-packet source-packet.json \
      [--file-spec file-spec.json] [--audience board-only] [--decision "..."] \
      [--approve] [--out DIR] [--run-id ID]

  --approve  records a human approval of the spine + slide map; only then can the gate clear
             to `ok-with-warnings` (clean) and the render step proceed. Absent, the gate
             stays `blocked` by design.
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

# ---- conventions enums (verbatim from references/conventions.md) -------------
REVIEW_STATUS = {"verified", "needs-review", "unsupported", "conflicting"}
SEVERITY = {"must-fix", "should-fix", "polish"}
READINESS = {"not-ready", "ready-with-limitations", "ready"}
STATUS = {"ok", "ok-with-warnings", "blocked", "failed"}
JUDGEMENT_CLASSES = {"assumption", "estimate", "open-question"}  # load-bearing, human-owned


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# claims-not-topics: a headline must be a CLAIM (an assertion that could be
# true or false), not a TOPIC (a bare noun phrase / section label).
# ---------------------------------------------------------------------------
_TOPIC_WORDS = {
    "highlights", "commentary", "strategic", "overview", "agenda", "summary",
    "introduction", "appendix", "financials", "asks", "next steps", "background",
    "context", "review", "results", "outlook", "q&a", "thank you", "title",
}
# a value token: a number, %, currency, or a comparison word implies an assertion
_VALUE_RE = re.compile(r"(\d|%|€|\$|£|YoY|bps|pp\b|×|x\b)", re.I)
_VERB_RE = re.compile(
    r"\b(is|are|was|were|grew|grow|fell|rose|recovered|improved|declined|"
    r"increased|decreased|reached|remained|held|expanded|contracted|"
    r"present|consider|considering|approve|greenlight|propose|exceed|beat|missed|"
    r"up|down|equals?)\b", re.I)
# equation / comparison operators are assertions too ("revenue = €48.2M")
_ASSERT_OP_RE = re.compile(r"[=≈≥≤<>]|->|→")


def is_claim_sentence(text):
    """True if `text` reads as a claim (assertion), not a bare topic/section label."""
    if not text:
        return False
    t = text.strip()
    words = t.split()
    low = t.lower().rstrip(":.")
    if low in _TOPIC_WORDS:
        return False
    has_value = bool(_VALUE_RE.search(t))
    has_verb = bool(_VERB_RE.search(t))
    has_op = bool(_ASSERT_OP_RE.search(t))
    # an equation ("X = Y", "margin ≈ 14%") is a full assertion
    if has_value and has_op:
        return True
    # no number and no verb -> a bare noun phrase -> topic
    if not has_value and not has_verb:
        return False
    # "Revenue €48.2M" is a value-bearing FRAGMENT, not yet a sentence — caller
    # rewrites it to the provenance claim; flag it so the transform is recorded.
    if has_value and not has_verb and len(words) <= 4:
        return False
    return True


# ---------------------------------------------------------------------------
def _index_claims(packet):
    return {c["id"]: c for c in packet.get("claims", [])}


def _source_status(packet):
    return {s["id"]: s.get("source_status", "unknown") for s in packet.get("sources", [])}


def _claim_sources(claim):
    prov = claim.get("provenance", {}) or {}
    return [s.get("source_id") for s in prov.get("supporting", []) if s.get("source_id")]


def _claim_workbook_ref(claim, file_spec, claim_id):
    """Pull a workbook locator: a provenance locator that points at tab!cell, or
    a matching row in the file-spec workbook_tab_map. Else null."""
    prov = claim.get("provenance", {}) or {}
    for s in prov.get("supporting", []):
        loc = s.get("locator", "") or ""
        if "!" in loc and re.search(r"![A-Z]+\d", loc):
            return loc
    if file_spec:
        for tab in file_spec.get("workbook_tab_map", []):
            for row in tab.get("rows", []):
                if row.get("claim_id") == claim_id:
                    return "{}!{}".format(tab.get("tab", "?"), row.get("metric", "?"))
    return None


def _slide_assumptions(claim, claims_by_id, packet):
    """Load-bearing assumptions the slide rests on: the claim itself if it is a
    judgement class, plus any conflict note that qualifies the number."""
    out = []
    cls = claim.get("class")
    if cls in JUDGEMENT_CLASSES:
        out.append("{} is a {} ({}), not an observed actual".format(
            claim.get("value", claim["id"]), cls, claim.get("review_status")))
    prov = claim.get("provenance", {}) or {}
    conf = prov.get("conflict")
    if conf:
        out.append("contested: {}".format(conf.get("note", "sources disagree")))
    return out


def _slide_open_questions(claim_id, packet):
    out = []
    for oq in packet.get("open_questions", []):
        if oq.get("claim_id") == claim_id or oq.get("ref") == claim_id:
            out.append(oq.get("question"))
    return out


def _role_for(slide_title, headlines):
    title = (slide_title or "").lower()
    if not headlines:
        if any(w in title for w in ("title", "q3", "review", "board")):
            return "title"
        return "section"
    if "ask" in title or "next step" in title:
        return "ask"
    if "strateg" in title:
        return "strategic argument"
    return "evidence"


# ---------------------------------------------------------------------------
def _normalize_skeleton(rows):
    """Accept BOTH deck_slide_map row shapes and emit the canonical one
    (`{slide, title, headlines:[{text, claim_id, ...}]}`).

    The canonical shape carries claims inside a `headlines` list. A simpler,
    equally plausible shape that a source-packet author may emit is one row per
    slide with `claim_id` + `headline` at the top level. Before this guard those
    rows had no `headlines`, so EVERY slide was silently read as a no-claim
    `section` — a vacuously clean architecture that hides an unsupported claim
    (found in the S13 dogfood). Normalise rather than misread."""
    out = []
    for sl in rows or []:
        if sl.get("headlines"):
            row = dict(sl)
            row.setdefault("title", "")
            out.append(row)
            continue
        cids = sl.get("claim_ids") or ([sl["claim_id"]] if sl.get("claim_id") else [])
        headline = sl.get("headline") or sl.get("text") or ""
        if cids:
            out.append({
                "slide": sl.get("slide"),
                "title": sl.get("title", ""),
                "headlines": [{"text": headline, "claim_id": c,
                               **({"source_id": sl["source_id"]} if sl.get("source_id") else {}),
                               **({"chart": sl["chart"]} if sl.get("chart") else {}),
                               **({"status": sl["status"]} if sl.get("status") else {})}
                              for c in cids],
            })
        else:
            # a structural row (title / section / decision / asks). Use its
            # explicit headline as the title so role detection still works
            # (e.g. an "asks" headline → role 'ask').
            title = sl.get("title") or headline
            if not title and sl.get("role"):
                title = sl["role"]
            out.append({"slide": sl.get("slide"), "title": title, "headlines": []})
    return out


def build_architecture(packet, file_spec=None, audience=None, decision=None,
                       approved=False, run_id=None):
    claims_by_id = _index_claims(packet)
    src_status = _source_status(packet)
    findings = []  # gate findings, keyed to the severity enum

    # ---- the slide-map skeleton ------------------------------------------
    if file_spec and file_spec.get("deck_slide_map"):
        skeleton = _normalize_skeleton(file_spec["deck_slide_map"])
    else:
        # no deck spec: synthesize one slide per non-section claim in id order
        skeleton = [{"slide": i + 1, "title": c.get("metric", c["id"]),
                     "headlines": [{"text": c["id"], "claim_id": c["id"]}]}
                    for i, c in enumerate(packet.get("claims", []))]

    slide_map = []
    for sl in skeleton:
        sidx = sl.get("slide")
        title = sl.get("title", "")
        headlines = sl.get("headlines", []) or []
        role = _role_for(title, headlines)

        if not headlines:
            slide_map.append({
                "slide": sidx, "claim_headline": title, "role": role,
                "source_ids": [], "workbook_refs": None, "chart": None,
                "assumptions": [], "open_questions": [],
                "note_requirement": "none (no claim asserted)",
                "review_status": "verified" if role in ("section", "title") else "needs-review",
            })
            continue

        for h in headlines:
            cid = h.get("claim_id")
            claim = claims_by_id.get(cid)
            src_headline = h.get("text", "")
            excluded = (h.get("status") or "").upper().startswith(("EXCLUDE", "BOARD-ONLY"))

            if claim is None:
                # a headline with no backing claim (an ask, a slogan, or an
                # un-tracked number). If it asserts a number it MUST be sourced.
                asserts_value = bool(_VALUE_RE.search(src_headline))
                rs = "unsupported" if asserts_value else "needs-review"
                row = {
                    "slide": sidx,
                    "claim_headline": src_headline,
                    "source_headline": src_headline,
                    "role": role,
                    "source_ids": [h["source_id"]] if h.get("source_id") else [],
                    "workbook_refs": None,
                    "chart": h.get("chart"),
                    "assumptions": [],
                    "open_questions": [],
                    "note_requirement": ("required: evidence speaker note "
                                         "(Claim / Source IDs / Calculation / "
                                         "Assumptions / Needs review)") if asserts_value
                                        else "none (no claim asserted)",
                    "review_status": rs if not h.get("source_id") else "needs-review",
                }
                if asserts_value and not h.get("source_id"):
                    findings.append({"severity": "must-fix", "slide": sidx,
                                     "headline": src_headline,
                                     "issue": "headline asserts a number with no source",
                                     "review_status": "unsupported"})
                slide_map.append(row)
                continue

            # claim-backed headline
            sources = _claim_sources(claim)
            rs = claim.get("review_status", "needs-review")
            # the emitted headline is the claim SENTENCE, not the topic label
            claim_sentence = (claim.get("provenance", {}) or {}).get("claim") or src_headline
            headline_was_topic = not is_claim_sentence(src_headline)

            row = {
                "slide": sidx,
                "claim_headline": claim_sentence,
                "source_headline": src_headline,
                "headline_rewritten_from_topic": headline_was_topic,
                "role": role,
                "source_ids": sources,
                "workbook_refs": _claim_workbook_ref(claim, file_spec, cid),
                "chart": h.get("chart"),
                "assumptions": _slide_assumptions(claim, claims_by_id, packet),
                "open_questions": _slide_open_questions(cid, packet),
                "note_requirement": ("required: evidence speaker note "
                                     "(Claim / Source IDs / Calculation / "
                                     "Assumptions / Needs review)"),
                "review_status": rs,
                "claim_id": cid,
                "sensitivity": h.get("sensitivity") or claim.get("sensitivity"),
                "render_disposition": (h.get("status") if excluded else "include"),
            }
            slide_map.append(row)

            # ---- gate findings -------------------------------------------
            if not sources and rs != "unsupported":
                # a number with no source that is NOT flagged unsupported is the
                # cardinal trust defect this whole layer exists to prevent.
                findings.append({"severity": "must-fix", "slide": sidx,
                                 "headline": claim_sentence, "claim_id": cid,
                                 "issue": "claim names no source and is not flagged unsupported",
                                 "review_status": rs})
            elif rs == "unsupported" and not excluded:
                # flagged unsupported but still slotted to render -> blocks render
                findings.append({"severity": "must-fix", "slide": sidx,
                                 "headline": claim_sentence, "claim_id": cid,
                                 "issue": "unsupported claim still slotted to render — source it or drop it",
                                 "review_status": "unsupported"})
            elif rs == "conflicting":
                findings.append({"severity": "should-fix", "slide": sidx,
                                 "headline": claim_sentence, "claim_id": cid,
                                 "issue": "claim rests on a contested figure — show authoritative value + visible note",
                                 "review_status": "conflicting"})
            if headline_was_topic:
                findings.append({"severity": "polish", "slide": sidx,
                                 "headline": src_headline, "claim_id": cid,
                                 "issue": "slide headline was a topic/fragment; rewritten to a claim sentence",
                                 "review_status": rs})
            # a source that is superseded is a finding (conventions §1.1)
            for sid in sources:
                if src_status.get(sid) == "superseded":
                    findings.append({"severity": "must-fix", "slide": sidx,
                                     "headline": claim_sentence, "claim_id": cid,
                                     "issue": "rests on a SUPERSEDED source ({})".format(sid),
                                     "review_status": rs})

    # ---- the invariant the acceptance gate checks -------------------------
    def _names_source_or_flagged(r):
        # Structural rows (title, section) are never claims — a period label in a
        # title ("Q3 FY26") is not an asserted figure and needs no source.
        if r["role"] in ("section", "title"):
            return True
        # An ask that references no factual claim is a request, not a claim.
        if r["role"] == "ask" and not r.get("claim_id"):
            return True
        # Every remaining row asserts a claim: it must name ≥1 source OR be
        # explicitly flagged unsupported. This is the invariant the gate enforces.
        return bool(r["source_ids"]) or r["review_status"] == "unsupported"

    every_claim_sourced_or_flagged = all(_names_source_or_flagged(r) for r in slide_map)

    # ---- binding guard: never let a misformatted slide map pass vacuously --
    # If the packet carries real (non-structural) claims but the slide map bound
    # NONE of them to a slide, the architecture is "clean" only because it is
    # empty of claims — the most dangerous false pass. Refuse it.
    packet_claim_ids = {c["id"] for c in packet.get("claims", [])}
    bound_claim_ids = {r.get("claim_id") for r in slide_map if r.get("claim_id")}
    if packet_claim_ids and not bound_claim_ids:
        findings.append({"severity": "must-fix", "slide": None,
                         "headline": "(slide map bound no claims)",
                         "issue": ("slide map bound 0 of the packet's {} claim(s) — the "
                                   "architecture is vacuously clean. Check the deck_slide_map "
                                   "row shape (slide/title/headlines[] or slide/claim_id/headline)."
                                   .format(len(packet_claim_ids))),
                         "review_status": "unsupported"})
        every_claim_sourced_or_flagged = False

    # ---- narrative spine (a human-owned proposal) -------------------------
    spine = _build_spine(packet, file_spec, audience, decision, slide_map)

    # ---- the gate verdict -------------------------------------------------
    blocking = [f for f in findings if f["severity"] == "must-fix"]
    if blocking:
        gate_status = "blocked"
        gate_reason = ("{} must-fix render-blockers (unsourced/unflagged or unsupported-but-"
                       "rendered claims). No slide is rendered until each is sourced, flagged, "
                       "or dropped.".format(len(blocking)))
    elif not approved:
        gate_status = "blocked"
        gate_reason = ("Architecture is clean of render-blockers but NOT yet human-approved. "
                       "The spine + slide map are a hard gate: no rendering until a named human "
                       "approves them (re-run with --approve once reviewed).")
    else:
        gate_status = "ok-with-warnings" if findings else "ok"
        gate_reason = ("Approved. {} non-blocking item(s) to carry into render."
                       .format(len(findings)) if findings else "Approved and clean.")

    decisions_required = list(file_spec.get("decisions_required", [])) if file_spec else []
    # always require the spine confirmation
    decisions_required = [{"id": "ARCH-1", "ref": "narrative-spine",
                           "decision": "Confirm the proposed spine, decision, and primary "
                                       "misread risk before any slide is rendered.",
                           "owner": "deck owner"}] + decisions_required

    gate = {
        "status": gate_status,
        "reason": gate_reason,
        "approved": bool(approved),
        "every_claim_names_source_or_flagged_unsupported": every_claim_sourced_or_flagged,
        "render_blockers": blocking,
        "findings": findings,
        "decisions_required": decisions_required,
        "assertion": ("Deck Architect is a HARD GATE (Nate's 'structure before slides'). "
                      "No slide is rendered until this architecture is approved and every "
                      "claim names a source or is explicitly flagged unsupported."),
    }

    out = {
        "run_id": run_id,
        "skill": "deck-architect",
        "phase": "deck-architecture",
        "status": gate_status,
        "status_reason": gate_reason,
        "consumes": ["source-packet.json"] + (["file-spec.json"] if file_spec else []),
        "task_context": packet.get("task_context"),
        "narrative_spine": spine,
        "slide_map": slide_map,
        "gate": gate,
    }
    return out


def _build_spine(packet, file_spec, audience, decision, slide_map):
    aud = audience or (file_spec or {}).get("audience") or "unknown — confirm audience"

    # decision / the ask
    if not decision and file_spec:
        asks = []
        for sl in file_spec.get("deck_slide_map", []):
            if "ask" in (sl.get("title", "").lower()):
                for h in sl.get("headlines", []):
                    asks.append(h.get("text"))
        if asks:
            decision = "; ".join(a for a in asks if a)
    decision = decision or "unknown — name the single decision this deck asks the audience to make"

    # what they already know vs what they must come to believe
    verified = [c for c in packet.get("claims", []) if c.get("review_status") == "verified"]
    what_they_know = [
        "{} = {}".format(c.get("metric", c["id"]), c.get("value"))
        for c in verified
    ] or ["(no verified prior context in the packet — confirm with the audience)"]

    must_believe = []
    for c in packet.get("claims", []):
        if c.get("class") in ("interpretation", "assumption") and c.get("review_status") != "unsupported":
            must_believe.append((c.get("provenance", {}) or {}).get("claim") or c.get("value"))
    if not must_believe:
        must_believe = ["(name the 1–3 beliefs the audience must hold by the last slide)"]

    # one-sentence spine — a proposal, human-owned
    spine_sentence = ("Proposed: \"{ctx}\" — confirm the through-line in one sentence "
                      "(what is true, why it matters, what we ask)."
                      .format(ctx=packet.get("task_context", "this deck")))

    # primary misread risk — COMPUTED from the packet
    misread = _primary_misread_risk(packet, file_spec, slide_map)

    return {
        "audience": aud,
        "decision": decision,
        "what_they_know": what_they_know,
        "what_they_must_believe": must_believe,
        "spine": spine_sentence,
        "primary_misread_risk": misread,
        "review_status": "needs-review",
        "note": "The spine is a JUDGEMENT proposal. A human owns the argument; the harness "
                "only structures it and computes the misread risk. Confirm before rendering.",
    }


def _primary_misread_risk(packet, file_spec, slide_map):
    """The single most likely WRONG takeaway, computed from the packet."""
    candidates = []
    # an estimate slotted to render without a 'forecast/estimate' label
    for r in slide_map:
        cid = r.get("claim_id")
        if not cid:
            continue
        claim = next((c for c in packet.get("claims", []) if c["id"] == cid), None)
        if not claim:
            continue
        if claim.get("class") == "estimate":
            candidates.append({
                "risk": "reader treats the estimate/forecast '{}' as a committed actual"
                        .format(claim.get("value")),
                "claim_id": cid, "slide": r["slide"], "weight": 3,
                "mitigation": "label it 'forecast/estimate' on the slide and in the speaker note",
            })
    # a human-decision conflict (e.g. display 18% vs actual 18.5%)
    for cf in packet.get("conflicts", []):
        if cf.get("resolution") == "human-decision":
            candidates.append({
                "risk": "reader takes the display value for {} as the true figure"
                        .format(cf.get("topic")),
                "conflict_ref": cf.get("id"), "weight": 4,
                "mitigation": "show the authoritative value with a footnote of the directive; resolve at the gate",
            })
    # an unsupported claim that is still slotted
    for r in slide_map:
        if r["review_status"] == "unsupported":
            candidates.append({
                "risk": "reader believes the unsupported claim '{}' is a sourced fact"
                        .format(r["claim_headline"]),
                "slide": r["slide"], "weight": 5,
                "mitigation": "drop it or source it before rendering (gate blocks it)",
            })
    if not candidates:
        return {"risk": "(none computed — review the spine for the most likely wrong takeaway)",
                "weight": 0}
    candidates.sort(key=lambda x: x["weight"], reverse=True)
    primary = candidates[0]
    primary["other_risks"] = candidates[1:]
    return primary


# ---------------------------------------------------------------------------
def render_md(arch):
    s = arch["narrative_spine"]
    g = arch["gate"]
    L = []
    L.append("# Deck Architecture — {}".format(arch.get("task_context") or arch.get("run_id")))
    L.append("")
    L.append("> **Status: `{}`** — {}".format(arch["status"], arch["status_reason"]))
    L.append(">")
    L.append("> {}".format(g["assertion"]))
    L.append("")
    L.append("## Narrative spine  _(needs-review — a human owns the argument)_")
    L.append("")
    L.append("- **Audience:** {}".format(s["audience"]))
    L.append("- **Decision asked:** {}".format(s["decision"]))
    L.append("- **What they already know:** {}".format("; ".join(s["what_they_know"])))
    L.append("- **What they must come to believe:** {}".format("; ".join(s["what_they_must_believe"])))
    L.append("- **One-sentence spine:** {}".format(s["spine"]))
    mr = s["primary_misread_risk"]
    L.append("- **Primary misread risk:** {}  _(mitigation: {})_".format(
        mr.get("risk"), mr.get("mitigation", "—")))
    L.append("")
    L.append("## Slide map  _(one row per slide — every headline is a claim, not a topic)_")
    L.append("")
    L.append("| # | Claim headline (sentence) | Role | Sources | Workbook | Chart | Review |")
    L.append("|---|---|---|---|---|---|---|")
    for r in arch["slide_map"]:
        L.append("| {} | {} | {} | {} | {} | {} | `{}` |".format(
            r["slide"], (r["claim_headline"] or "").replace("|", "\\|"), r["role"],
            ", ".join(r["source_ids"]) or "—", r.get("workbook_refs") or "—",
            r.get("chart") or "—", r["review_status"]))
    L.append("")
    # per-slide detail (assumptions / open questions / notes)
    L.append("### Per-slide evidence requirements")
    L.append("")
    for r in arch["slide_map"]:
        if r["role"] in ("section", "title") and not r["source_ids"]:
            continue
        L.append("**Slide {} — {}**".format(r["slide"], r["claim_headline"]))
        if r.get("source_headline") and r.get("headline_rewritten_from_topic"):
            L.append("  - rewritten from topic label: _{}_".format(r["source_headline"]))
        L.append("  - source IDs: {}".format(", ".join(r["source_ids"]) or "**none**"))
        if r["assumptions"]:
            L.append("  - assumptions: {}".format("; ".join(r["assumptions"])))
        if r["open_questions"]:
            L.append("  - open questions: {}".format("; ".join(q for q in r["open_questions"] if q)))
        L.append("  - speaker note: {}".format(r["note_requirement"]))
        L.append("")
    # the gate
    L.append("## The gate")
    L.append("")
    L.append("- **Every claim names a source or is flagged unsupported:** {}".format(
        "✅ yes" if g["every_claim_names_source_or_flagged_unsupported"] else "❌ NO"))
    L.append("- **Render-blockers (must-fix):** {}".format(len(g["render_blockers"])))
    for f in g["render_blockers"]:
        L.append("  - slide {}: {} — _{}_".format(f.get("slide"), f.get("headline"), f["issue"]))
    if g["findings"]:
        L.append("- **All findings:**")
        for f in g["findings"]:
            L.append("  - `{}` slide {}: {} — _{}_".format(
                f["severity"], f.get("slide"), f.get("headline"), f["issue"]))
    L.append("")
    L.append("### Decisions required before rendering")
    for d in g["decisions_required"]:
        L.append("- **{}** ({}): {} — _owner: {}_".format(
            d.get("id"), d.get("ref"), d.get("decision"), d.get("owner")))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="DA-01 deck-architecture schema + narrative-spine gate")
    ap.add_argument("--source-packet", required=True)
    ap.add_argument("--file-spec")
    ap.add_argument("--audience")
    ap.add_argument("--decision")
    ap.add_argument("--approve", action="store_true",
                    help="record human approval of the spine + slide map (clears the gate if clean)")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    try:
        packet = _load(args.source_packet)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "failed", "status_reason": "cannot read source packet: {}".format(e)}))
        return 2
    file_spec = None
    if args.file_spec and os.path.exists(args.file_spec):
        try:
            file_spec = _load(args.file_spec)
        except Exception:  # noqa: BLE001
            file_spec = None

    run_id = args.run_id or packet.get("run_id") or (_now_slug() + "_deck-architecture")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    arch = build_architecture(packet, file_spec=file_spec, audience=args.audience,
                              decision=args.decision, approved=args.approve, run_id=run_id)

    jpath = os.path.join(out_dir, "deck-architecture.json")
    mpath = os.path.join(out_dir, "deck-architecture.md")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(arch, fh, indent=2, ensure_ascii=False)
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(render_md(arch))

    print("deck-architecture -> {} (status: {})".format(out_dir, arch["status"]))
    print("  every claim names a source or is flagged unsupported: {}".format(
        arch["gate"]["every_claim_names_source_or_flagged_unsupported"]))
    print("  render-blockers: {}".format(len(arch["gate"]["render_blockers"])))
    # exit non-zero only on a hard failure; `blocked` is a valid, expected state
    return 0 if arch["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
