#!/usr/bin/env python3
"""
Office Trust Layer — Evidence Map · EM-01: evidence-map schema + speaker-note contract.

The honest evidence layer for a deck. It is the DECK INSTANCE of the frozen
provenance record (`references/conventions.md §2`): one record per slide claim,
tying the claim to the source(s) that back it — with confidence and conflict made
explicit, and with sub-threshold links HELD as candidates for a human rather than
asserted as provenance (review-1 finding 9: a single tidy cell-per-claim invites
false provenance).

A record is the §2 shape — `{claim, supporting[], conflict}` — extended with the
deck-instance fields the plan's EM-01 schema names:

  slide · claim · tab(s) · cells/named outputs · source IDs · calculation ·
  assumptions · date range · owner · review status · confidence · candidate links ·
  match rationale.

`supporting[]` and `candidates[]` both use the §2 link shape
  {source_id, locator, confidence, rationale}
The ONLY difference is the confidence cut: a link at/above `LINK_THRESHOLD` is
`supporting` (asserted provenance); a link below it is a `candidate` (held for human
approval — never silently promoted to provenance). A claim with ZERO supporting
links is `unsupported` — a `must-fix` once it travels (§2 + §1.4) — even if it has
candidates. "Some cell somewhere holds this number" is NOT a source.

### 2.1 speaker-note / evidence template (per claim) — the fixed, machine-checkable shape
  Claim:        <the claim, plain English>
  Source IDs:   <S1, S3 …  or  "none (unsupported)">
  Calculation:  <the formula/derivation, or "direct from source">
  Assumptions:  <load-bearing assumptions, with source/owner/status>
  Needs review: <what a human must still confirm, or "none">

Enums (`review_status`, `severity`) are VERBATIM from `references/conventions.md`.
Pure stdlib so it imports anywhere; the builder (EM-02) and gap review (EM-03)
both import this module as the single source of truth for the shape.
"""
import re

# ---- conventions enums (verbatim from references/conventions.md) -------------
REVIEW_STATUS = {"verified", "needs-review", "unsupported", "conflicting"}
SEVERITY = {"must-fix", "should-fix", "polish"}
SOURCE_STATUS = {"current", "estimate", "superseded", "unknown", "draft"}

# The confidence cut between an asserted link and a held candidate. A link the
# cross-app linker (EM-02) emits below this is NOT asserted as provenance; it is
# surfaced for a human to confirm. Tunable; the default is deliberately strict.
LINK_THRESHOLD = 0.6

# The five fixed lines of the §2.1 evidence speaker note, in order.
NOTE_FIELDS = ("Claim", "Source IDs", "Calculation", "Assumptions", "Needs review")

# The deck-instance record fields (the plan EM-01 schema). The first three
# (claim/supporting/conflict) ARE the §2 provenance record verbatim; the rest are
# the deck-instance extensions.
RECORD_FIELDS = (
    "slide", "claim", "supporting", "candidates", "conflict", "review_status",
    "tabs", "cells", "source_ids", "calculation", "assumptions", "date_range",
    "owner", "confidence",
)
RECORD_REQUIRED = ("slide", "claim", "supporting", "candidates", "conflict", "review_status")
LINK_FIELDS = ("source_id", "locator", "confidence", "rationale")


# ---------------------------------------------------------------------------
def link(source_id, locator, confidence, rationale):
    """A single provenance link — the frozen §2 shape. `confidence` is the LINK
    confidence (does this source actually back this claim), not the source's own
    reliability. Clamped to [0.0, 1.0]."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return {
        "source_id": source_id,
        "locator": locator,
        "confidence": round(conf, 3),
        "rationale": rationale or "",
    }


def derive_review_status(supporting, conflict, declared=None):
    """Derive the claim's review_status from its links + conflict, honouring a
    declared status from the upstream packet where it is stricter.

      * a non-null conflict      -> `conflicting`   (§2: a conflict sets conflicting)
      * zero supporting links    -> `unsupported`   (§2: zero supporting == unsupported)
      * an upstream `verified`   -> `verified`      (kept only if it has support and no conflict)
      * otherwise                -> `needs-review`

    A declared `unsupported`/`conflicting` from upstream is never softened.
    """
    if declared in ("unsupported", "conflicting"):
        # upstream already flagged it; only a conflict can override unsupported
        if conflict:
            return "conflicting"
        return declared
    if conflict:
        return "conflicting"
    if not supporting:
        return "unsupported"
    if declared == "verified":
        return "verified"
    return "needs-review"


def evidence_record(slide, claim, supporting=None, candidates=None, conflict=None,
                    declared_status=None, tabs=None, cells=None, calculation=None,
                    assumptions=None, date_range=None, owner=None, source_ids=None):
    """Construct a normalized deck-instance evidence record. `supporting` and
    `candidates` are lists of `link(...)` dicts. The review_status and the headline
    `confidence` (the max supporting confidence, 0.0 if unsupported) are DERIVED so
    they can never silently disagree with the links."""
    supporting = list(supporting or [])
    candidates = list(candidates or [])
    review_status = derive_review_status(supporting, conflict, declared_status)

    # the claim's headline confidence is the strongest ASSERTED link (0.0 if none).
    confidence = round(max([s["confidence"] for s in supporting], default=0.0), 3)

    # tabs / cells / source_ids are convenience projections of the links, so a
    # reader (and the gap review) can scan them without re-parsing locators.
    src_ids = source_ids if source_ids is not None else _unique(
        [s["source_id"] for s in supporting if s.get("source_id")])
    rec = {
        "slide": slide,
        "claim": claim,
        "supporting": supporting,
        "candidates": candidates,
        "conflict": conflict,
        "review_status": review_status,
        # tabs/cells project the ASSERTED (supporting) links only — a held candidate
        # is not yet backing evidence, so it must not appear as a backing cell.
        "tabs": tabs if tabs is not None else _unique(_tabs_from_links(supporting)),
        "cells": cells if cells is not None else _cells_from_links(supporting),
        "source_ids": src_ids,
        "calculation": calculation or "direct from source",
        "assumptions": list(assumptions or []),
        "date_range": date_range,
        "owner": owner,
        "confidence": confidence,
    }
    return rec


# ---------------------------------------------------------------------------
_CELL_RE = re.compile(r"!\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?")


def _tabs_from_links(links):
    out = []
    for ln in links:
        loc = ln.get("locator") or ""
        if "!" in loc:
            out.append(loc.split("!", 1)[0].split("#")[-1])
    return out


def _cells_from_links(links):
    out = []
    for ln in links:
        loc = ln.get("locator") or ""
        m = _CELL_RE.search(loc)
        if m:
            out.append(loc[: m.start()].split("#")[-1] + m.group(0))
    return out


def _unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
def validate_record(rec):
    """Return a list of problems (empty == valid). Enforces the §2 invariants so a
    malformed record can't smuggle false provenance past the gate."""
    problems = []
    for f in RECORD_REQUIRED:
        if f not in rec:
            problems.append("missing required field %r" % f)
    rs = rec.get("review_status")
    if rs not in REVIEW_STATUS:
        problems.append("review_status %r not in %s" % (rs, sorted(REVIEW_STATUS)))

    for bucket in ("supporting", "candidates"):
        for i, ln in enumerate(rec.get(bucket, []) or []):
            for lf in ("source_id", "locator", "confidence", "rationale"):
                if lf not in ln:
                    problems.append("%s[%d] missing %r" % (bucket, i, lf))
            c = ln.get("confidence")
            if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
                problems.append("%s[%d] confidence %r not in [0,1]" % (bucket, i, c))

    # the cardinal §2 invariants — the whole reason this layer exists:
    supporting = rec.get("supporting", []) or []
    conflict = rec.get("conflict")
    # (a) a supporting (ASSERTED) link must be at/above threshold — a sub-threshold
    #     link belongs in candidates, never in supporting.
    for i, ln in enumerate(supporting):
        if float(ln.get("confidence", 0)) < LINK_THRESHOLD:
            problems.append(
                "supporting[%d] confidence %.2f < LINK_THRESHOLD %.2f — a sub-threshold "
                "link must be a candidate (held for human approval), not asserted provenance"
                % (i, float(ln.get("confidence", 0)), LINK_THRESHOLD))
    # (b) zero supporting MUST read as unsupported (or conflicting). Never anything
    #     that implies the claim is sourced.
    if not supporting and rs not in ("unsupported", "conflicting"):
        problems.append("zero supporting links but review_status=%r (must be 'unsupported' "
                        "or 'conflicting' — a claim with no source is unsupported)" % rs)
    # (c) a non-null conflict must be reflected in the status.
    if conflict and rs != "conflicting":
        problems.append("conflict present but review_status=%r (must be 'conflicting')" % rs)
    return problems


# ---------------------------------------------------------------------------
def speaker_note(rec, packet_sources=None):
    """Render the §2.1 evidence speaker note for a record — the fixed five-line
    template that travels in the slide's speaker notes so the proof survives the
    forward. `packet_sources` (optional {id: source dict}) lets the note annotate
    each source with its status/owner."""
    packet_sources = packet_sources or {}
    src_ids = rec.get("source_ids") or []
    if src_ids:
        annotated = []
        for sid in src_ids:
            s = packet_sources.get(sid, {})
            st = s.get("source_status")
            annotated.append("%s%s" % (sid, " (%s)" % st if st else ""))
        src_line = ", ".join(annotated)
    else:
        src_line = "none (unsupported — must source or drop before it travels)"

    assumptions = rec.get("assumptions") or []
    assum_line = "; ".join(assumptions) if assumptions else "none stated"

    # what a human must still confirm
    needs = []
    if rec["review_status"] == "unsupported":
        needs.append("claim has NO confirmed source — source it or drop it")
    if rec["review_status"] == "conflicting":
        cf = rec.get("conflict") or {}
        needs.append("sources disagree: %s" % (cf.get("note") or "reconcile before it travels"))
    if rec.get("candidates"):
        needs.append("%d candidate link(s) held for approval — confirm or reject" % len(rec["candidates"]))
    if rec["review_status"] == "needs-review":
        needs.append("confirm the source link and the period match")
    needs_line = "; ".join(needs) if needs else "none"

    lines = [
        "Claim:        %s" % rec["claim"],
        "Source IDs:   %s" % src_line,
        "Calculation:  %s" % (rec.get("calculation") or "direct from source"),
        "Assumptions:  %s" % assum_line,
        "Needs review: %s" % needs_line,
    ]
    return "\n".join(lines)


def note_is_wellformed(text):
    """A speaker note conforms if it carries all five §2.1 lines in order."""
    if not text:
        return False
    idx = [text.find(f + ":") for f in NOTE_FIELDS]
    return all(i >= 0 for i in idx) and idx == sorted(idx)


if __name__ == "__main__":  # tiny self-check
    r = evidence_record(
        7, "Revenue is ahead of plan",
        supporting=[link("S3", "Model.xlsx#Outputs!B14", 0.9, "exact label + period match")],
        candidates=[link("S7", "Plan.xlsx#Plan!C12:C24", 0.4, "candidate; period ambiguous")],
        conflict={"with": ["S7"], "note": "S3 actuals vs S7 plan mix"},
        calculation="Outputs!B14 = (actual-plan)/plan",
        assumptions=["FY25 figures are current (S3, status=current)"],
        date_range="FY25", owner="FP&A")
    probs = validate_record(r)
    print("review_status:", r["review_status"], "| confidence:", r["confidence"])
    print("valid:", not probs, probs)
    print("---- note ----")
    print(speaker_note(r))
    print("well-formed:", note_is_wellformed(speaker_note(r)))
