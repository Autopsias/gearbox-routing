#!/usr/bin/env python3
"""S03 fixture — messy sources -> source packet + file spec.

Proves the office-source-packet skill's acceptance gate against the committed
worked run. Validates the truth-layer invariants from references/conventions.md
and references/sensitivity-routing-policy.md. Exit 0 = PASS.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
RUN = os.path.join(HERE, "trust_runs", "2026-05-29T1615_q3-boardpack")
SRC_DIR = os.path.join(HERE, "messy_sources")

SOURCE_STATUS = {"current", "superseded", "estimate", "raw"}
REVIEW_STATUS = {"verified", "needs-review", "unsupported", "conflicting"}
SENSITIVITY = {"public", "internal", "confidential", "restricted"}
STATUS = {"ok", "ok-with-warnings", "blocked", "failed"}

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def main():
    # Inputs exist (messy sources)
    inputs = [f for f in os.listdir(SRC_DIR) if not f.startswith(".")] if os.path.isdir(SRC_DIR) else []
    check(len(inputs) >= 5, f"expected >=5 messy source inputs, found {len(inputs)}")

    pkt = json.load(open(os.path.join(RUN, "source-packet.json")))
    spec = json.load(open(os.path.join(RUN, "file-spec.json")))

    # --- Source packet (SP-01) ---
    check(pkt.get("status") in STATUS, "packet status not in enum")
    ids = [s["id"] for s in pkt["sources"]]
    check(all(s.get("id") and s.get("source_status") for s in pkt["sources"]),
          "every source must have id + source_status")
    check(all(s["source_status"] in SOURCE_STATUS for s in pkt["sources"]),
          "source_status must be from the conventions enum")
    check(all(s.get("sensitivity") in SENSITIVITY for s in pkt["sources"]),
          "every source must carry a sensitivity class")
    check(all(s.get("can_support") and s.get("cannot_support") for s in pkt["sources"]),
          "every source must record can_support + cannot_support")
    check(len(set(ids)) == len(ids), "source ids must be unique")
    check(all(i and i[0] == "S" and i[1:].isdigit() for i in ids), "source ids must be S<n>")

    # claims: class split + review_status + provenance
    for c in pkt["claims"]:
        check(c.get("class") in {"fact", "assumption", "estimate", "interpretation", "open-question"},
              f"claim {c.get('id')} bad class")
        check(c.get("review_status") in REVIEW_STATUS, f"claim {c.get('id')} bad review_status")
        prov = c.get("provenance", {})
        check("supporting" in prov and "conflict" in prov, f"claim {c.get('id')} missing provenance shape")
        # no fact+verified unless traced to a current source
        if c["class"] == "fact" and c["review_status"] == "verified":
            cur = {s["id"] for s in pkt["sources"] if s["source_status"] == "current"}
            check(any(sp["source_id"] in cur for sp in prov["supporting"]),
                  f"claim {c['id']} fact+verified not traced to a current source")
        # zero supporting => unsupported
        if not prov.get("supporting"):
            check(c["review_status"] == "unsupported", f"claim {c['id']} zero-support must be unsupported")

    # at least one unsupported open-question demonstrated
    check(any(c["review_status"] == "unsupported" for c in pkt["claims"]),
          "fixture should demonstrate an unsupported claim")

    # --- Conflicts visible, preserved, truth located ---
    check(len(pkt["conflicts"]) >= 1, "conflicts must be visible")
    for k in pkt["conflicts"]:
        check(len(k.get("competing", [])) >= 2, f"{k.get('id')} must list >=2 competing values")
        check(bool(k.get("where_truth_lives")), f"{k.get('id')} must state where the truth lives")
        check(k.get("resolution") in {"authoritative", "human-decision"}, f"{k.get('id')} bad resolution")
    check(any(k["resolution"] == "human-decision" for k in pkt["conflicts"]),
          "at least one conflict should escalate human-decision")

    # --- File spec (SP-02): traceability, sensitivity, human gate ---
    check(spec.get("status") == "blocked", "file-spec must end blocked at the human gate")
    check(spec.get("human_gate", {}).get("status") == "blocked", "human_gate must be blocked")
    check(len(spec.get("decisions_required", [])) >= 1, "human gate must list decisions")
    # restricted claims must be excluded/gated in the sensitivity review
    restricted = {c["id"] for c in pkt["claims"] if c.get("sensitivity") == "restricted"}
    reviewed = {r["claim_id"] for r in spec.get("sensitivity_review", [])}
    check(restricted <= reviewed, f"restricted claims not all handled: {restricted - reviewed}")
    # deck headlines reference source ids (or are explicitly excluded)
    for sl in spec.get("deck_slide_map", []):
        for h in sl.get("headlines", []):
            check(("source_id" in h) or (h.get("status", "").startswith("EXCLUDE")),
                  f"slide {sl['slide']} headline not traced or excluded: {h.get('text')}")

    if fails:
        print("FAIL — source-packet fixture")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — source-packet fixture (messy sources -> packet -> file spec; gate blocked)")
    sys.exit(0)


if __name__ == "__main__":
    main()
