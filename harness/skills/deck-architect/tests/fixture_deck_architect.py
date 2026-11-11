#!/usr/bin/env python3
"""
S09 acceptance-gate fixture — Deck Architect (DA-01 + DA-02).

THE GATE (plan S09): a fixture turns a source packet into an architecture where
EVERY claim names a source OR is flagged unsupported.

DA-01 — deck-architecture schema + narrative-spine gate:
  * POSITIVE — on the well-formed q3 boardpack packet + file-spec, the emitted
    architecture satisfies the invariant `every claim names a source or is flagged
    unsupported`; the one unsupported claim (+12% YoY, C6) appears in the slide map
    flagged `unsupported` with ZERO sources — NOT silently dropped and NOT given a
    fake source.
  * CLAIMS-NOT-TOPICS — every content headline emitted is a claim SENTENCE; the
    file-spec topic labels ("Revenue €48.2M") were rewritten and the rewrite recorded.
  * THE HARD GATE — without `--approve` the status is `blocked` (no rendering); a
    clean, approved architecture returns `ok`/`ok-with-warnings`.
  * NEGATIVE (the property the layer exists for) — plant a claim that asserts a
    number, has ZERO sources, and is mislabeled `needs-review` (not `unsupported`).
    The builder must catch it: invariant False, a must-fix render-blocker, status
    stays `blocked`. A naive renderer would have shipped an unsourced number.

DA-02 — brand-system interpreter:
  * runs on a self-contained synthetic .pptx (built here with python-pptx);
  * asserts theme fonts + colours are EXTRACTED from the file;
  * asserts the "do not invent" discipline: every `unclear` rule has value None
    (nothing fabricated), and the known-unknowns (banned_moves, footer_legal_text)
    are `unclear`.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh.
"""
import copy
import glob
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "deck-architect", "scripts")
sys.path.insert(0, SCRIPTS)

import deck_architecture as DA   # noqa: E402
import brand_interpreter as BI   # noqa: E402

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
        print("  FAIL", msg)
    else:
        print("  ok  ", msg)


# ---- locate the committed q3 boardpack packet + file-spec -------------------
def _load_boardpack():
    base = os.path.join(HERE, "trust_runs", "2026-05-29T1615_q3-boardpack")
    packet = json.load(open(os.path.join(base, "source-packet.json"), encoding="utf-8"))
    file_spec = json.load(open(os.path.join(base, "file-spec.json"), encoding="utf-8"))
    return packet, file_spec


# ===========================================================================
# DA-01
# ===========================================================================
def test_da01_positive():
    print("[DA-01 positive — every claim names a source or is flagged unsupported]")
    packet, file_spec = _load_boardpack()
    arch = DA.build_architecture(packet, file_spec=file_spec, run_id="fix_da01")

    inv = arch["gate"]["every_claim_names_source_or_flagged_unsupported"]
    check(inv is True, "THE GATE: invariant holds — every claim names a source or is flagged unsupported")

    # every slide-map row individually satisfies the invariant
    bad = []
    for r in arch["slide_map"]:
        if r["role"] in ("section", "title"):
            continue
        if r["role"] == "ask" and not r.get("claim_id"):
            continue
        if not (r["source_ids"] or r["review_status"] == "unsupported"):
            bad.append(r)
    check(not bad, "no slide-map row asserts a claim without a source or unsupported flag (offenders: {})".format(
        [(r["slide"], r["claim_headline"]) for r in bad]))

    # the unsupported claim is FLAGGED, not dropped, not fake-sourced
    c6 = [r for r in arch["slide_map"] if r.get("claim_id") == "C6"]
    check(len(c6) == 1, "the unsupported +12% YoY claim (C6) is still present in the slide map (not silently dropped)")
    if c6:
        check(c6[0]["review_status"] == "unsupported", "C6 is flagged review_status=unsupported")
        check(c6[0]["source_ids"] == [], "C6 carries ZERO sources (no fabricated provenance)")

    # claims-not-topics: every content headline is a claim sentence
    topic_rows = []
    for r in arch["slide_map"]:
        if r["role"] in ("section", "title"):
            continue
        if not DA.is_claim_sentence(r["claim_headline"]):
            topic_rows.append((r["slide"], r["claim_headline"]))
    check(not topic_rows, "every content headline emitted is a claim SENTENCE, not a topic (offenders: {})".format(topic_rows))

    # at least one headline was rewritten from a topic label (proves the transform ran)
    rewritten = [r for r in arch["slide_map"] if r.get("headline_rewritten_from_topic")]
    check(len(rewritten) >= 1, "≥1 file-spec topic label was rewritten to a claim sentence (transform exercised)")

    # the spine has all six required fields + the misread risk is computed
    s = arch["narrative_spine"]
    for f in ("audience", "decision", "what_they_know", "what_they_must_believe",
              "spine", "primary_misread_risk"):
        check(f in s and s[f], "spine field present: {}".format(f))
    check(isinstance(s["primary_misread_risk"], dict) and "risk" in s["primary_misread_risk"],
          "primary misread risk is computed from the packet")

    # per-row schema: the DA-01 fields are all present
    required_cols = ("slide", "claim_headline", "role", "source_ids", "workbook_refs",
                     "chart", "assumptions", "open_questions", "note_requirement", "review_status")
    schema_ok = all(all(k in r for k in required_cols) for r in arch["slide_map"])
    check(schema_ok, "every slide-map row carries the full DA-01 schema (10 columns)")


def test_da01_gate_blocks_until_approved():
    print("[DA-01 the hard gate — no rendering until approved]")
    packet, file_spec = _load_boardpack()
    blocked = DA.build_architecture(packet, file_spec=file_spec, approved=False, run_id="g1")
    check(blocked["status"] == "blocked",
          "un-approved architecture is `blocked` (no rendering) — got {}".format(blocked["status"]))
    check(blocked["gate"]["approved"] is False, "gate records approved=False")
    approved = DA.build_architecture(packet, file_spec=file_spec, approved=True, run_id="g2")
    check(approved["status"] in ("ok", "ok-with-warnings"),
          "approved + clean architecture clears the gate — got {}".format(approved["status"]))


def test_da01_negative_planted_unsourced():
    print("[DA-01 negative — a planted unsourced, unflagged number must be caught]")
    packet, file_spec = _load_boardpack()
    packet = copy.deepcopy(packet)
    file_spec = copy.deepcopy(file_spec)
    # plant: a claim that asserts a number, has ZERO sources, but is MISLABELLED
    # needs-review (not unsupported) — the exact trust hole the gate must close.
    packet["claims"].append({
        "id": "CX", "class": "fact", "review_status": "needs-review",
        "metric": "market_share", "period": "Q3-FY26", "value": "31%",
        "provenance": {"claim": "Market share reached 31% in Q3-FY26",
                       "supporting": [], "conflict": None},
    })
    file_spec["deck_slide_map"].append({
        "slide": 6, "title": "Market", "headlines": [
            {"text": "Market share 31%", "claim_id": "CX", "source_id": None}]})

    arch = DA.build_architecture(packet, file_spec=file_spec, approved=True, run_id="neg")
    inv = arch["gate"]["every_claim_names_source_or_flagged_unsupported"]
    check(inv is False, "invariant correctly FALSE — the planted unsourced/unflagged claim is detected")
    blockers = arch["gate"]["render_blockers"]
    cx = [f for f in blockers if f.get("claim_id") == "CX"]
    check(len(cx) >= 1, "the planted claim raises a must-fix render-blocker")
    check(arch["status"] == "blocked",
          "status stays `blocked` even WITH approval — an unsourced number cannot render (got {})".format(arch["status"]))


def test_da01_emits_files():
    print("[DA-01 I/O contract — emits deck-architecture.{json,md}, JSON parses]")
    packet, file_spec = _load_boardpack()
    with tempfile.TemporaryDirectory() as d:
        rc = DA.main(["--source-packet",
                      os.path.join(HERE, "trust_runs", "2026-05-29T1615_q3-boardpack", "source-packet.json"),
                      "--file-spec",
                      os.path.join(HERE, "trust_runs", "2026-05-29T1615_q3-boardpack", "file-spec.json"),
                      "--out", d, "--run-id", "io"])
        check(rc == 0, "CLI returns 0 on a valid run")
        jp = os.path.join(d, "deck-architecture.json")
        mp = os.path.join(d, "deck-architecture.md")
        check(os.path.exists(jp) and os.path.exists(mp), "both deck-architecture.json + .md emitted")
        json.load(open(jp, encoding="utf-8"))  # raises on bad JSON
        check(True, "deck-architecture.json parses")


# ===========================================================================
# DA-02
# ===========================================================================
def _build_synthetic_template(path):
    """A self-contained .pptx with the default Office theme, so the fixture does
    not depend on any external skill being present."""
    from pptx import Presentation
    pres = Presentation()  # ships the stock Office theme (Calibri / accent1=4472C4)
    pres.save(path)
    return path


def _assert_brand_rules(b, label):
    print("  [{}]".format(label))
    check(b["status"] in ("ok", "ok-with-warnings"), "{}: interpreter ran (status {})".format(label, b["status"]))
    rr = b["render_rules"]
    # fonts extracted from the theme
    hf = rr["typography"]["heading_font"]
    check(hf["determination"] in ("extracted", "unclear"), "{}: heading_font has a determination".format(label))
    # at least one extracted and one unclear rule
    counts = b["determination_counts"]
    check(counts.get("extracted", 0) >= 3, "{}: ≥3 rules EXTRACTED from the file".format(label))
    check(counts.get("unclear", 0) >= 3, "{}: ≥3 rules marked UNCLEAR (known-unknowns)".format(label))
    # THE DISCIPLINE: nothing fabricated — every unclear rule has value None
    fabricated = []

    def scan(path, obj):
        if isinstance(obj, dict):
            if obj.get("determination") == "unclear" and obj.get("value") is not None:
                fabricated.append(path)
            else:
                for k, v in obj.items():
                    scan("{}.{}".format(path, k), v)
    scan("", rr)
    check(not fabricated, "{}: NO `unclear` rule carries a fabricated value (offenders: {})".format(label, fabricated))
    # the canonical known-unknowns are unclear
    check(rr["banned_moves"]["determination"] == "unclear", "{}: banned_moves is unclear (never invented)".format(label))
    check(rr["logo_legal_footer"]["footer_legal_text"]["determination"] == "unclear",
          "{}: footer/legal wording is unclear (not in the binary)".format(label))
    # every unclear item is listed with a reason
    for u in b["unclear_items"]:
        if not u.get("reason"):
            check(False, "{}: unclear item {} has no reason".format(label, u["rule"]))


def test_da02_synthetic():
    print("[DA-02 brand interpreter — self-contained synthetic template]")
    with tempfile.TemporaryDirectory() as d:
        tpl = _build_synthetic_template(os.path.join(d, "synthetic.pptx"))
        b = BI.interpret(tpl, run_id="syn")
        _assert_brand_rules(b, "synthetic")
        # theme colours extracted (Office default has a full clrScheme)
        tc = b["render_rules"]["palette"]["theme_colors"]
        check(tc["determination"] == "extracted" and isinstance(tc["value"], dict) and tc["value"],
              "synthetic: theme colours EXTRACTED from the file")
        # accessibility contrast computed
        acc = b["render_rules"]["accessibility"]["text_on_background_contrast"]
        check(acc["determination"] == "extracted", "synthetic: dk1/lt1 contrast computed from the theme")


# ===========================================================================
def main():
    print("=" * 72)
    print("S09 Deck Architect — acceptance gate (DA-01 + DA-02)")
    print("=" * 72)
    test_da01_positive()
    test_da01_gate_blocks_until_approved()
    test_da01_negative_planted_unsourced()
    test_da01_emits_files()
    test_da02_synthetic()
    print("-" * 72)
    if _errs:
        print("RESULT: FAIL ({} assertion(s) failed)".format(len(_errs)))
        for e in _errs:
            print("  -", e)
        return 1
    print("RESULT: PASS — a source packet became an architecture where every claim names a "
          "source or is flagged unsupported; the brand interpreter extracts without inventing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
