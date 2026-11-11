#!/usr/bin/env python3
"""S13 regression — the slide-map binding guard in deck_architecture.py.

The S13 dogfood found that a plausible deck_slide_map row shape
(`{slide, role, claim_id, headline}`) was silently read as an all-`section`
deck: every claim dropped, the gate passed VACUOUSLY (0 render-blockers), and an
unsupported claim slid through. Two fixes were added:
  1. _normalize_skeleton — accept that row shape (and `claim_ids`) and bind it.
  2. a binding guard — if the packet has claims but the slide map bound NONE of
     them, raise a must-fix; never a vacuous clean pass.

This fixture locks both in."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "skills", "deck-architect", "scripts"))
import deck_architecture as DA  # noqa: E402

_errs = []
def check(cond, label):
    print(("  ok  " if cond else " FAIL ") + label)
    if not cond:
        _errs.append(label)

def _packet():
    def prov(c, sup):
        return {"claim": c, "supporting": sup, "conflict": None}
    return {"run_id": "fx", "skill": "office-source-packet", "claims": [
        {"id": "C1", "metric": "rev", "value": "EUR 48.2M", "class": "fact",
         "review_status": "verified",
         "provenance": prov("Q3 revenue = EUR 48.2M",
                            [{"source_id": "S1", "locator": "x#rev", "confidence": 1.0, "rationale": "actual"}])},
        {"id": "C2", "metric": "yoy", "value": "+12%", "class": "interpretation",
         "review_status": "unsupported",
         "provenance": prov("Revenue grew +12% YoY", [])},  # zero source — must be flagged
    ], "sources": [{"id": "S1", "source_status": "current"}], "conflicts": [], "open_questions": []}

print("[binding-guard] alternative row shape {slide,role,claim_id,headline} binds claims")
alt_spec = {"deck_slide_map": [
    {"slide": 1, "role": "title", "claim_id": None, "headline": "Q3 Board Review"},
    {"slide": 2, "role": "claim", "claim_id": "C1", "headline": None},
    {"slide": 3, "role": "claim", "claim_id": "C2", "headline": None},
]}
arch = DA.build_architecture(_packet(), file_spec=alt_spec, run_id="fx")
bound = {r.get("claim_id") for r in arch["slide_map"] if r.get("claim_id")}
check(bound == {"C1", "C2"}, "alt-shape binds BOTH claims (not silently read as sections): bound=%s" % sorted(bound))
blockers = arch["gate"]["render_blockers"]
check(any("unsupported" in (b.get("issue", "") + b.get("review_status", "")) or b.get("review_status") == "unsupported"
          for b in blockers),
      "the unsupported C2 is caught as a render-blocker (not vacuously clean): %d blocker(s)" % len(blockers))

print("[binding-guard] a structural-only slide map with claims present FAILS the guard")
empty_spec = {"deck_slide_map": [
    {"slide": 1, "title": "Title only"},
    {"slide": 2, "title": "Section only"},
]}
arch2 = DA.build_architecture(_packet(), file_spec=empty_spec, run_id="fx")
bound2 = {r.get("claim_id") for r in arch2["slide_map"] if r.get("claim_id")}
guard_fired = any("bound 0 of" in f.get("issue", "") for f in arch2["gate"]["render_blockers"])
check(not bound2, "no claims bound by a structural-only map (bound=%s)" % sorted(bound2))
check(guard_fired, "the binding guard raises a must-fix instead of passing vacuously")
check(arch2["gate"]["every_claim_names_source_or_flagged_unsupported"] is False,
      "a vacuous map is NOT reported as 'every claim sourced/flagged' (the dangerous false pass is blocked)")
check(arch2["status"] == "blocked", "status is blocked (got %s)" % arch2["status"])

print("-" * 60)
if _errs:
    print("FAIL deck binding-guard: %d check(s) failed" % len(_errs))
    sys.exit(1)
print("PASS deck binding-guard: the alt slide-map shape binds; a map that binds no claims "
      "cannot pass vacuously — the unsupported claim is never hidden by a misformatted spec.")
sys.exit(0)
