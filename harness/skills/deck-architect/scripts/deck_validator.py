#!/usr/bin/env python3
"""
Office Trust Layer — Deck Architect · DA-04: brand + accuracy validator (the deck variant of Trust Review).

The deck arm of the hostile review (review-1 finding 11): a deck needs BOTH brand AND
accuracy validation, because a slide can be perfectly on-brand and still lie, or perfectly
accurate and still drift off-brand into something that reads as rushed and untrustworthy.

This is NOT a second review engine — it is the M1 Trust Review loop run with a deck-specialised
ENUMERATOR. It produces the exact `review_contract` edit-list shape the loop already ingests
(`{id, location, type, severity, evidence, suggested_repair, human_review, status}`), so it
drops straight into `loop_controller.run_loop(reviewer=deck_reviewer(...), surface="ppt")`.
Severities are VERBATIM from `references/conventions.md §1.2`.

It runs nine deck-specific checks against the truth layer (architecture + evidence map +
render report + brand rules) — never against its own guess of what is true:

  1. CLAIM SPECIFICITY    — every headline is a claim SENTENCE, not a topic/fragment.
  2. SOURCE ATTRIBUTION   — every claim names a source or is flagged; unsupported == must-fix.
  3. NUMBERS MATCH        — the slide number agrees with the workbook (no conflict; not held-only).
  4. CHARTS TRACEABLE     — a chart's numbers trace to a source/cell (untraceable == must-fix).
  5. ASSUMPTIONS LABELLED — a load-bearing assumption is labelled as such, not stated as fact.
  6. BRAND / TEMPLATE     — fonts/colours/logo/footer compliance; render deviations surfaced.
  7. HIERARCHY / READABILITY — density + contrast (overcrowded / low-contrast slides).
  8. LEGAL / SENSITIVITY  — a sensitivity-flagged slide is surfaced EXPLICITLY for a human.
  9. NARRATIVE LOGIC      — a headline its evidence does not support (claim ≠ what's sourced).

Each maps to a Pretty-But-Wrong class (TR-04) so the M2 benchmark can score severity accuracy
against the same key the M1 reviewer uses.

I/O (`references/conventions.md §3`):
  in : deck-architecture.json (DA-01) · evidence-map.json (EM-02/04) · render-report.json (DA-03)
       · brand-rules.json (DA-02)        [any subset; more inputs => more checks fire]
  out: <out>/deck-validation.json  (the edit list + summary)  ·  <out>/deck-validation.md
       and, run inside the loop, the standard review-result.json the loop writes.

Status: `ok` / `ok-with-warnings` (findings to read) / `failed`.
"""
import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deck_architecture as DA  # noqa: E402  (claims-not-topics test)

_TR = os.path.normpath(os.path.join(HERE, "..", "..", "trust-review", "scripts"))
sys.path.insert(0, _TR)
try:
    import review_contract as RC  # noqa: E402
except Exception:  # noqa: BLE001
    RC = None

SEVERITY = ("must-fix", "should-fix", "polish")


def _now_slug():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H%M")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# The deck-specialised enumeration
# ===========================================================================
def enumerate_deck_findings(architecture=None, evidence_map=None, render_report=None,
                            brand_rules=None):
    """Return a list of edit-list dicts (the review_contract shape) for a deck. Pure,
    deterministic, no I/O — so it hashes stably inside the loop and the fixture can drive it."""
    edits = []
    n = {"i": 0}

    def add(location, type_, severity, evidence, repair, human=False, pbw=None):
        assert severity in SEVERITY
        n["i"] += 1
        e = {
            "id": "DV-%02d" % n["i"],
            "location": location,
            "type": type_,
            "severity": severity,
            "evidence": evidence,
            "suggested_repair": repair,
            "human_review": bool(human),
            "status": "open",
        }
        if pbw:
            e["pbw"] = pbw      # the Pretty-But-Wrong class this maps to (TR-04)
        edits.append(e)

    arch = architecture or {}
    slide_map = arch.get("slide_map", [])
    ev_by_slide, ev_by_id = _index_evidence(evidence_map)

    # ---- 1, 2, 3, 5, 9: per-claim checks (architecture + evidence map) --------
    for row in slide_map:
        sidx = row.get("slide")
        role = row.get("role")
        if role in ("title", "section"):
            continue
        loc = "slide %s / title" % sidx
        headline = row.get("claim_headline") or ""
        rec = ev_by_id.get(row.get("claim_id")) or _first(ev_by_slide.get(sidx))

        # 1. CLAIM SPECIFICITY — headline must be a claim sentence
        if headline and not DA.is_claim_sentence(headline):
            add(loc, "vague-headline", "should-fix",
                "headline '%s' is a topic/fragment, not a claim sentence" % headline,
                "rewrite as a full claim the body can support (claims-not-topics)",
                pbw="PBW-10")

        # 2. SOURCE ATTRIBUTION
        rs = (rec or {}).get("review_status") or row.get("review_status")
        if rs == "unsupported":
            add(loc, "unsupported-claim", "must-fix",
                "claim '%s' has no above-threshold source in the evidence map" % headline,
                "source it (link a current source / workbook cell) or drop the slide",
                human=True, pbw="PBW-01")
        elif rs is None and not row.get("source_ids") and role not in ("ask",):
            add(loc, "unattributed-claim", "must-fix",
                "claim '%s' names no source and is not flagged" % headline,
                "attach a source id or flag it unsupported in the architecture",
                human=True, pbw="PBW-01")

        # 3. NUMBERS MATCH — conflict, or held-candidate-only support
        if rec is not None:
            if rec.get("review_status") == "conflicting":
                cf = rec.get("conflict") or {}
                add(loc, "number-mismatch", "must-fix",
                    "slide number conflicts with the workbook: %s" % (cf.get("note") or "sources disagree"),
                    "show the authoritative value + a visible reconciliation note",
                    human=True, pbw="PBW-01")
            elif not rec.get("supporting") and rec.get("held_for_approval"):
                add(loc, "held-link-only", "should-fix",
                    "the only workbook match(es) for '%s' are HELD below threshold (%d candidate(s))"
                    % (headline, len(rec.get("held_for_approval", []))),
                    "a human must confirm or reject the held link before this number is trusted",
                    human=True, pbw="PBW-01")

        # 5. ASSUMPTIONS LABELLED — a load-bearing assumption presented as fact
        assumptions = (rec or {}).get("assumptions") or row.get("assumptions") or []
        if assumptions and rs not in ("unsupported", "conflicting"):
            labelled = any(w in headline.lower() for w in ("assume", "estimate", "forecast", "projected"))
            if not labelled:
                add(loc, "assumption-as-fact", "should-fix",
                    "slide rests on assumption(s) (%s) but the headline states it as fact"
                    % "; ".join(assumptions)[:120],
                    "label the estimate/assumption on the slide and in the speaker note",
                    pbw="PBW-06")

        # 9. NARRATIVE LOGIC — headline claim differs from what the evidence backs
        if rec is not None and rec.get("claim") and headline:
            if _claim_drift(headline, rec.get("claim")):
                add(loc, "headline-evidence-mismatch", "should-fix",
                    "headline '%s' does not match the evidenced claim '%s'" % (headline, rec.get("claim")),
                    "align the headline to the claim the evidence actually supports",
                    pbw="PBW-10")

    # ---- 4. CHARTS TRACEABLE (render report is authoritative) -----------------
    for sl in (render_report or {}).get("slides", []):
        ch = sl.get("chart")
        if ch and not ch.get("traceable"):
            add("slide %s / chart" % sl.get("slide"), "untraceable-chart", "must-fix",
                "chart '%s' numbers cannot be traced: %s" % (ch.get("declared"), ch.get("detail")),
                "trace the chart to a workbook cell / source, or remove it",
                human=False, pbw="PBW-03")

    # ---- 6. BRAND / TEMPLATE — render deviations + brand-rule gaps -------------
    for d in (render_report or {}).get("deviations", []):
        if d.get("area") in ("chart", "evidence", "claim"):
            continue   # those are covered by the accuracy checks above
        sev = d.get("severity") if d.get("severity") in SEVERITY else "should-fix"
        add("slide %s / brand" % d.get("slide", "—"), "brand-drift", sev,
            "brand rule '%s' (%s): %s" % (d.get("rule"), d.get("determination"), d.get("detail")),
            "apply the house value via the pptx skill, or have the brand owner confirm it",
            pbw="PBW-08")

    # ---- 7. HIERARCHY / READABILITY — density + contrast from brand rules ------
    rules = (brand_rules or {}).get("rules", brand_rules or {})
    dens = (rules.get("density") or {}).get("density_band") if isinstance(rules.get("density"), dict) else None
    band = dens.get("value") if isinstance(dens, dict) else dens
    if band in ("dense", "high", "overcrowded"):
        add("deck / layout", "overcrowded", "polish",
            "template density band is '%s' — risk of overcrowded slides" % band,
            "cut to one idea per slide; raise type size", pbw="PBW-09")
    acc = rules.get("accessibility") if isinstance(rules, dict) else None
    if isinstance(acc, dict):
        for k, v in acc.items():
            vv = v.get("value") if isinstance(v, dict) else v
            if isinstance(vv, (int, float)) and vv < 4.5:   # WCAG AA body text
                add("deck / palette", "low-contrast", "polish",
                    "contrast %.2f on %s is below WCAG AA (4.5)" % (vv, k),
                    "darken text / lighten background to clear 4.5:1", pbw="PBW-09")

    # ---- 8. LEGAL / SENSITIVITY — surfaced EXPLICITLY -------------------------
    for row in slide_map:
        sens = row.get("sensitivity")
        if sens and str(sens).lower() not in ("none", "public", "false"):
            add("slide %s" % row.get("slide"), "sensitivity-flag", "must-fix",
                "slide carries a sensitivity flag (%s) — confirm travel class / redaction before it ships" % sens,
                "confirm the audience travel class allows this content; redact or gate if not",
                human=True, pbw="PBW-01")

    return edits


# ---------------------------------------------------------------------------
def _index_evidence(evidence_map):
    by_slide, by_id = {}, {}
    for r in (evidence_map or {}).get("records", []):
        by_slide.setdefault(r.get("slide"), []).append(r)
        if r.get("claim_id"):
            by_id[r["claim_id"]] = r
    return by_slide, by_id


def _first(seq):
    return seq[0] if seq else None


def _claim_drift(headline, evidenced_claim):
    """A coarse, deterministic drift test: the headline and the evidenced claim should
    share their key tokens. If a number in one is absent in the other, flag it."""
    import re
    hn = set(re.findall(r"\d[\d,.]*%?", headline))
    en = set(re.findall(r"\d[\d,.]*%?", evidenced_claim))
    if hn and en and not (hn & en):
        return True      # both cite numbers, none shared -> drift
    return False


# ===========================================================================
# The reviewer closure for the Trust Review loop (the deck variant)
# ===========================================================================
def deck_reviewer(architecture=None, evidence_map=None, render_report=None, brand_rules=None):
    """Return a reviewer fn compatible with loop_controller.run_loop:
        reviewer(state, provenance, round_no) -> [edit dicts]
    The enumeration is deterministic and independent of the builder's reasoning — it reads
    only the truth-layer artifacts (the loop re-scans them each round)."""
    snapshot = {"architecture": architecture, "evidence_map": evidence_map,
                "render_report": render_report, "brand_rules": brand_rules}

    def _reviewer(state, provenance, round_no):
        src = state if isinstance(state, dict) and state.get("architecture") else snapshot
        return enumerate_deck_findings(
            architecture=src.get("architecture"), evidence_map=src.get("evidence_map"),
            render_report=src.get("render_report"), brand_rules=src.get("brand_rules"))
    return _reviewer


def validate_deck(architecture=None, evidence_map=None, render_report=None,
                  brand_rules=None, run_id=None):
    """Standalone deck validation (outside the loop): the edit list + a summary + the
    schema-validity check against review_contract."""
    edits = enumerate_deck_findings(architecture, evidence_map, render_report, brand_rules)
    ok, problems = (RC.validate_edit_list(
        [{k: e.get(k) for k in RC.EDIT_FIELDS} for e in edits]) if RC else (True, []))
    by_sev = {s: sum(1 for e in edits if e["severity"] == s) for s in SEVERITY}
    legal = [e for e in edits if e["type"] == "sensitivity-flag"]
    out = {
        "run_id": run_id,
        "skill": "deck-architect",
        "phase": "deck-validation",
        "surface": "ppt",
        "status": "ok" if not edits else "ok-with-warnings",
        "status_reason": "%d finding(s): %d must-fix, %d should-fix, %d polish%s"
        % (len(edits), by_sev["must-fix"], by_sev["should-fix"], by_sev["polish"],
           "; %d legal/sensitivity surfaced" % len(legal) if legal else ""),
        "edit_list": edits,
        "edit_list_valid": ok,
        "edit_list_problems": problems,
        "by_severity": by_sev,
        "legal_sensitivity": legal,
        "checks": ["claim-specificity", "source-attribution", "numbers-match", "charts-traceable",
                   "assumptions-labelled", "brand-template", "hierarchy-readability",
                   "legal-sensitivity", "narrative-logic"],
        "assertion": ("The deck variant of the hostile Trust Review: brand AND accuracy. Every "
                      "finding is the review_contract edit-list shape the M1 loop ingests, keyed to "
                      "conventions severities and a Pretty-But-Wrong class. Legal/sensitivity issues "
                      "are surfaced explicitly for a human."),
    }
    return out


def render_md(v):
    L = ["# Deck Validation (DA-04) — %s" % (v.get("run_id") or "")]
    L += ["", "> **Status: `%s`** — %s" % (v["status"], v["status_reason"]), ""]
    L += ["> %s" % v["assertion"], ""]
    L += ["- **Edit list valid against review_contract:** %s" % ("✅ yes" if v["edit_list_valid"] else "❌ NO"),
          "- **By severity:** %d must-fix · %d should-fix · %d polish"
          % (v["by_severity"]["must-fix"], v["by_severity"]["should-fix"], v["by_severity"]["polish"]),
          "- **Legal / sensitivity surfaced:** %d" % len(v["legal_sensitivity"]), ""]
    L += ["| ID | Location | Type | Sev | Human? | Evidence |", "|---|---|---|---|---|---|"]
    for e in v["edit_list"]:
        L.append("| %s | %s | %s | `%s` | %s | %s |" % (
            e["id"], e["location"], e["type"], e["severity"],
            "yes" if e["human_review"] else "—", (e["evidence"] or "")[:60].replace("|", "\\|")))
    L.append("")
    return "\n".join(L)


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="DA-04 deck brand+accuracy validator (Trust Review deck variant)")
    ap.add_argument("--architecture")
    ap.add_argument("--evidence-map")
    ap.add_argument("--render-report")
    ap.add_argument("--brand-rules")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    arch = _load(args.architecture) if args.architecture and os.path.exists(args.architecture) else None
    ev = _load(args.evidence_map) if args.evidence_map and os.path.exists(args.evidence_map) else None
    rr = _load(args.render_report) if args.render_report and os.path.exists(args.render_report) else None
    br = _load(args.brand_rules) if args.brand_rules and os.path.exists(args.brand_rules) else None
    if not any([arch, ev, rr, br]):
        print(json.dumps({"status": "blocked",
                          "status_reason": "no deck artifacts to validate (need at least one of "
                                           "--architecture/--evidence-map/--render-report/--brand-rules)"}))
        return 0

    run_id = args.run_id or (arch or {}).get("run_id") or (ev or {}).get("run_id") or (_now_slug() + "_deck-validation")
    out_dir = args.out or os.path.join(".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    v = validate_deck(arch, ev, rr, br, run_id=run_id)
    with open(os.path.join(out_dir, "deck-validation.json"), "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "deck-validation.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(v))

    print("deck-validation -> %s (status: %s)" % (out_dir, v["status"]))
    print("  %s" % v["status_reason"])
    print("  edit list valid against review_contract: %s" % v["edit_list_valid"])
    return 0 if v["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
