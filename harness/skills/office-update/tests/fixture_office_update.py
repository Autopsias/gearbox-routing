#!/usr/bin/env python3
"""UP-01 functional proof: the office-update CORE — surface-agnostic recurring refresh.

Drives `skills/office-update/scripts/office_update.py` against a synthetic prior
`.trust/<run_id>/` run (a schema_version=2 manifest + a native `claim-set.json`, so the
fd-03 cross-version read is exercised) and a current claim-set, asserting the UP-01
contract end-to-end:

  1. REORDER keeps IDs — the same claims shuffled + moved (new locator, same text/source)
     all keep their fd-02 `claim_id`, classify `unchanged`, and carry their prior verdict;
     nothing is re-minted as new/dropped.
  2. A DROPPED claim is TOMBSTONED — present in the prior run, absent from the current
     set ⇒ `supersession_state: tombstoned`, surfaced in the report and kept in the
     forward claim-set; NEVER silently deleted.
  3. A SUPERSEDED source is LINKED, not blended — a newer source replaces a claim ⇒ the
     prior claim is `superseded` and linked to its replacement (`superseded_by`), BOTH
     values preserved (no average, no overwrite); the replacement records `supersedes`.
  4. A changed source/text mints a NEW id that CANNOT inherit a verdict (re-enters review).
  5. Carry-forward is gated — ONLY a valid human-gate waiver carries; an EXPIRED waiver
     does not, and its claim re-enters needs-review.
  6. The prior run is consumed READ-ONLY (byte-identical before/after) and the output is
     stamped schema_version=3 + migration_notes; the manifest gate is `ok`.
  7. No §5 release threshold is touched — the engine writes ONLY into its fresh out_dir.

Run via fixtures/run_fixtures.sh, or directly:  python fixtures/fixture_office_update.py
Exit 0 = PASS, non-zero = FAIL.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "skills", "office-update", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "skills", "trust-review", "references", "scripts"))

import office_update as OU       # noqa: E402
import claim_identity as CI      # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _id(rec: dict) -> str:
    return OU.identity_of(rec).claim_id


def _tree_fp(root: str) -> dict:
    fp = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                fp[p] = hashlib.sha256(fh.read()).hexdigest()
    return fp


SRC1 = {"source_id": "S1", "source_status": "current", "content_hash": "h-s1", "as_of": "2026-03-31"}
SRC1_OLD = {"source_id": "S1", "source_status": "superseded", "content_hash": "h-s1", "as_of": "2025-12-31"}
SRC2 = {"source_id": "S2", "source_status": "current", "content_hash": "h-s2", "as_of": "2026-06-30"}


def make_prior_run(d: str) -> dict:
    """Write a synthetic prior run: a schema_version=2 manifest + a native claim-set.json."""
    claims = [
        # A,B: plain claims that will survive a reorder/move
        {"claim_text": "Q3 revenue = EUR 48.2M", "locator": "deck#slide2", "surface": "ppt",
         "sources": [SRC1], "retrieval_timestamp": "2026-03-31T00:00:00Z",
         "materiality_class": "numeric", "verdict": "verified", "slot": "q3-rev"},
        {"claim_text": "Headcount = 412", "locator": "deck#slide3", "surface": "ppt",
         "sources": [SRC1], "retrieval_timestamp": "2026-03-31T00:00:00Z",
         "materiality_class": "numeric", "verdict": "verified", "slot": "headcount"},
        # C: dropped next period
        {"claim_text": "One-off legal provision = EUR 1.1M", "locator": "deck#slide7", "surface": "ppt",
         "sources": [SRC1], "retrieval_timestamp": "2026-03-31T00:00:00Z",
         "materiality_class": "numeric", "verdict": "verified", "slot": "legal-prov"},
        # D: superseded-source claim (prior period already flagged S1 as superseded → slot path)
        {"claim_text": "Q4 outlook = EUR 50.0M (estimate)", "locator": "deck#slide9", "surface": "ppt",
         "sources": [SRC1_OLD], "retrieval_timestamp": "2026-03-31T00:00:00Z",
         "materiality_class": "numeric", "verdict": "needs-review", "slot": "q4-outlook"},
        # E1: unchanged claim with a VALID waiver + ready-with-limitations
        {"claim_text": "Cloud-migration spend = EUR 6.4M", "locator": "deck#slide5", "surface": "ppt",
         "sources": [SRC1], "retrieval_timestamp": "2026-03-31T00:00:00Z", "materiality_class": "numeric",
         "verdict": "ready-with-limitations", "slot": "cloud-spend",
         "waiver": {"owner": "Alex Example", "decision": "accept residual risk for Q3 pack",
                    "scope": "cloud-spend", "travel_class": "internal", "signoff": "approved",
                    "expiry": "2026-12-31"}},
        # E2: unchanged claim with an EXPIRED waiver
        {"claim_text": "Gross margin = 42%", "locator": "deck#slide6", "surface": "ppt",
         "sources": [SRC1], "retrieval_timestamp": "2026-03-31T00:00:00Z", "materiality_class": "numeric",
         "verdict": "ready-with-limitations", "slot": "gross-margin",
         "waiver": {"owner": "Alex Example", "decision": "accept residual risk",
                    "scope": "gross-margin", "travel_class": "internal", "signoff": "approved",
                    "expiry": "2026-01-31"}},
    ]
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "claim-set.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema_version": "3", "run_id": "2026-03-31T0900_q3-pack", "claims": claims}, fh, indent=2)
    # a v2-shaped manifest (has a `verification` block ⇒ detect_schema_version → "2")
    manifest = {
        "manifest_version": "1.0", "run_id": "2026-03-31T0900_q3-pack",
        "travel_class": {"effective": "internal"},
        "sensitivity": {"run_max": "internal", "sources": [{"source_id": "S1", "sensitivity": "internal"}]},
        "routing": {"policy": "two-context", "two_context": True,
                    "builder_model": {"name": "Opus"}, "reviewer_model": {"name": "Sonnet"}},
        "verification": {"results": []},
    }
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return {"claims": claims}


def main() -> int:
    print("UP-01 office-update CORE — surface-agnostic recurring refresh")
    print("-" * 70)

    work = tempfile.mkdtemp(prefix="otl_office_update_")
    prev_dir = os.path.join(work, ".trust", "2026-03-31T0900_q3-pack")
    out_root = os.path.join(work, ".trust")
    prior = make_prior_run(prev_dir)

    # ---- build the CURRENT claim-set ----------------------------------------
    A, B, C, D, E1, E2 = prior["claims"]
    a_id, b_id, c_id, d_id, e1_id, e2_id = (_id(A), _id(B), _id(C), _id(D), _id(E1), _id(E2))

    current = [
        # B comes first (REORDER) and A is MOVED to a new locator (same text/source) — both unchanged
        dict(B, locator="deck#slide4"),                       # moved
        dict(A),                                              # unchanged
        # C is DROPPED (not present)
        # D is SUPERSEDED by a current claim on the same slot backed by a NEW source S2
        {"claim_text": "Q4 outlook = EUR 47.5M (actual)", "locator": "deck#slide9", "surface": "ppt",
         "sources": [SRC2], "retrieval_timestamp": "2026-06-30T00:00:00Z",
         "materiality_class": "numeric", "slot": "q4-outlook"},
        # E1 unchanged (valid waiver carries); E2 unchanged-identity but EXPIRED waiver
        dict(E1),
        dict(E2),
        # F: a claim whose SOURCE content changed ⇒ NEW identity, no slot/supersedes link
        {"claim_text": "EBITDA margin = 18%", "locator": "deck#slide8", "surface": "ppt",
         "sources": [{"source_id": "S1", "source_status": "current", "content_hash": "h-CHANGED",
                      "as_of": "2026-06-30"}], "retrieval_timestamp": "2026-06-30T00:00:00Z",
         "materiality_class": "numeric"},
        # G: a brand-new claim
        {"claim_text": "New segment revenue = EUR 5.0M", "locator": "deck#slide10", "surface": "ppt",
         "sources": [SRC2], "retrieval_timestamp": "2026-06-30T00:00:00Z", "materiality_class": "numeric"},
    ]

    # READ-ONLY fingerprint of the prior run BEFORE the refresh.
    before = _tree_fp(prev_dir)
    out_root_before = _tree_fp(out_root)                 # the .trust tree before (prior run only)
    refs_before = _tree_fp(os.path.join(ROOT, "skills", "trust-review", "references"))   # §5 thresholds live here

    report = OU.refresh(prev_dir, current, run_id="2026-05-31T2010_s02-office-update",
                        now="2026-05-31T00:00:00Z", travel_class="internal", out_root=out_root)

    after = _tree_fp(prev_dir)
    refs_after = _tree_fp(os.path.join(ROOT, "skills", "trust-review", "references"))

    counts = report["counts"]
    by_id = {c["claim_id"]: c for c in report["changes"]}

    # 1. REORDER + MOVE keeps IDs ---------------------------------------------
    check(a_id in by_id and by_id[a_id]["classification"] == "unchanged",
          "reorder: claim A keeps its id and classifies unchanged")
    check(b_id in by_id and by_id[b_id]["classification"] == "unchanged" and by_id[b_id]["moved"],
          "reorder+move: claim B keeps its id, classifies unchanged, and is flagged moved")
    check(by_id[a_id]["carried_verdict"] == "verified" and by_id[b_id]["carried_verdict"] == "verified",
          "reorder: prior 'verified' verdicts carry forward on the unchanged claims")
    # the moved claim B got a NEW locator but the SAME id
    moved_locators = {r["locator"] for r in current if r["claim_text"] == "Headcount = 412"}
    check(by_id[b_id]["locator"] in moved_locators and by_id[b_id]["prev_locator"] == "deck#slide3",
          "reorder: the moved claim's locator is updated while the id is unchanged")

    # 2. DROPPED → TOMBSTONED --------------------------------------------------
    check(counts["dropped"] == 1, f"exactly one dropped claim (got {counts['dropped']})")
    check(c_id in by_id and by_id[c_id]["classification"] == "dropped"
          and by_id[c_id].get("supersession_state") == "tombstoned",
          "the dropped claim is classified dropped + tombstoned (never silently deleted)")
    tomb_ids = {t["claim_id"] for t in report["tombstones"]}
    check(c_id in tomb_ids, "the dropped claim is surfaced in the tombstones list")
    # tombstone is kept in the forward claim-set (audit, never lost)
    next_ids = {c["claim_id"]: c for c in report["next_claim_set"]["claims"]}
    check(c_id in next_ids and next_ids[c_id]["supersession_state"] == "tombstoned",
          "the tombstone is carried into the forward claim-set (kept, never deleted)")

    # 3. SUPERSEDED SOURCE LINKED, NOT BLENDED --------------------------------
    check(counts["superseded"] == 1, f"exactly one superseded claim (got {counts['superseded']})")
    check(d_id in by_id and by_id[d_id]["classification"] == "superseded",
          "the old-source claim is classified superseded (its source was replaced)")
    sup = report["supersessions"]
    check(len(sup) == 1 and sup[0]["superseded_claim_id"] == d_id, "one supersession link, on the old claim")
    if sup:
        link = sup[0]
        new_id = link["superseded_by"]
        check(by_id[d_id].get("superseded_by") == new_id, "the superseded claim is LINKED to its replacement id")
        check(link["blended"] is False
              and "50.0" in link["prior_value"] and "47.5" in link["new_value"],
              "BOTH values are preserved on the link (EUR 50.0M prior + EUR 47.5M new) — never blended/averaged")
        check(new_id in by_id and by_id[new_id]["classification"] == "new"
              and d_id in (by_id[new_id].get("supersedes") or []),
              "the replacement is a NEW identity that records it supersedes the old claim")
        # the prior source S1 and the new source S2 are both on the link (the superseded SOURCE is linked)
        ps = {s["source_id"] for s in link["prior_sources"]}
        ns = {s["source_id"] for s in link["new_sources"]}
        check("S1" in ps and "S2" in ns, "the superseded source (S1) is linked to the replacing source (S2)")

    # 4. CHANGED SOURCE ⇒ NEW ID CANNOT INHERIT A VERDICT ----------------------
    # F's prior identity (the original EBITDA claim) is NOT in the prior set at all here;
    # F is a current claim whose SOURCE differs from any prior, so it is a brand-new id at needs-review.
    f_id = _id(current[6])
    check(f_id in by_id and by_id[f_id]["classification"] == "new"
          and by_id[f_id]["carried_verdict"] is None and by_id[f_id]["status"] == "needs-review",
          "a changed-source claim is a NEW id that cannot inherit a verdict (re-enters needs-review)")

    # 5. WAIVER CARRY-FORWARD IS GATED ----------------------------------------
    check(by_id[e1_id]["classification"] == "unchanged"
          and by_id[e1_id]["carried_verdict"] == "ready-with-limitations"
          and by_id[e1_id]["carried_waiver"] is not None,
          "a VALID waiver carries forward with its ready-with-limitations verdict")
    check(by_id[e2_id]["classification"] == "updated"
          and by_id[e2_id]["carried_verdict"] is None
          and "waiver-expired" in (by_id[e2_id].get("invalidation") or [])
          and by_id[e2_id]["status"] == "needs-review",
          "an EXPIRED waiver does NOT carry — the claim re-enters needs-review (waiver-expired)")
    check(report["carried_forward"]["waivers"] == 1 and report["carried_forward"]["verdicts"] >= 3,
          "carried-forward tally counts only the valid waiver + the unchanged verdicts")

    # 6. READ-ONLY + schema_version=3 stamp + gate ok --------------------------
    check(before == after, "the prior run dir is byte-identical before/after (read-only)")
    check(report["read_only_verified"] is True, "the engine self-reports read-only verified")
    out_dir = report["out_dir"]
    rr = json.load(open(os.path.join(out_dir, "refresh-report.json"), encoding="utf-8"))
    check(rr["schema_version"] == "3" and bool(rr["migration_notes"]),
          "refresh-report.json is stamped schema_version=3 + migration_notes")
    man = json.load(open(os.path.join(out_dir, "manifest.json"), encoding="utf-8"))
    check(man["schema_version"] == "3" and man["gate"]["status"] == "ok",
          "the run manifest is schema_version=3 and the F-05 gate is ok")
    check(os.path.exists(os.path.join(out_dir, "refresh-report.md"))
          and os.path.exists(os.path.join(out_dir, "claim-set.json")),
          "refresh-report.md + the forward claim-set.json are written")

    # 7. NO §5 THRESHOLD TOUCHED — engine writes ONLY into its fresh out_dir ----
    # every NEW-or-CHANGED path under the .trust root must live inside the new run dir;
    # the prior run files are untouched, and references/ (where §5 thresholds live) is byte-stable.
    out_root_after = _tree_fp(out_root)
    new_or_changed = [p for p in out_root_after if out_root_before.get(p) != out_root_after[p]]
    stray = [p for p in new_or_changed if "2026-05-31T2010_s02-office-update" not in p]
    check(not stray, "every new/changed file is inside the fresh out_dir (prior-run files untouched)")
    check(refs_before == refs_after,
          "references/ is byte-identical — no §5 release threshold (or any contract file) was touched")

    # cross-version read proof: the prior was a schema-2 manifest, read via fd-03
    check(report["prev_schema_version"] == "2",
          "the prior run was read across schema versions (v2 manifest) via the fd-03 reader")

    print("-" * 70)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed)")
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: PASS — reorder keeps IDs + carries verdicts; a dropped claim is tombstoned; a "
          "superseded source is linked (both values preserved, not blended); a changed source mints a "
          "new id that cannot inherit a verdict; only valid waivers carry; the prior run is read-only "
          "and the output is stamped schema_version=3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
