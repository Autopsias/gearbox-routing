#!/usr/bin/env python3
"""UP-03 NEGATIVE fixtures — per-surface refresh adapters (S03, codex v3 finding #1).

Finding #1 (HIGH): `office-update` was scoped cross-format but tested as a happy path.
This battery is the teeth. Each check encodes a guarantee that MUST hold across the
rendered surfaces, and is written so that **if the guard regresses, the assertion fails
and the build goes red**:

  1. reorder-keeps-id ................ EXCEL  — a pure reorder/move keeps stable claim IDs
                                       and carries prior verdicts (no spurious new/dropped).
  2. stale-waiver-no-carry .......... PPT    — an EXPIRED waiver must NOT carry its verdict;
                                       the claim re-enters needs-review (waiver-expired).
  3. superseded-source-invalidates-verdict  DOC/docx — a superseded source invalidates the
                                       carried verdict; old+new are LINKED, never blended.
  4. dropped-claim-tombstoned ....... DOC/md — a dropped claim is tombstoned + kept, never
                                       silently deleted.
  5. obsolete-locator-rejected ...... DOC/html — a carried evidence/speaker-note locator that
                                       no longer resolves is re-pointed or invalidated; NO
                                       surviving note may cite an obsolete locator.
  6. layout-drift-detected .......... PPT + EXCEL — a layout/brand swap between periods is
                                       surfaced as a finding (and a no-change control does
                                       NOT false-positive).
  7. pdf-sidecar-immutable .......... DOC/pdf — a PDF is never mutated; evidence is sidecar-
                                       only and the verdict is capped (sidecar-only-pdf /
                                       not-ready-for-forwarding).

Plus the cross-cutting guards every Trust-Layer change must keep: the prior run is consumed
READ-ONLY and `references/` (the frozen §5 thresholds) is byte-identical before/after.

Run via fixtures/run_fixtures.sh, or directly:  python fixtures/fixture_surface_adapters.py
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

import office_update as OU        # noqa: E402  (the S02 CORE)
import surface_adapters as SA     # noqa: E402  (the S03 adapters under test)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _id(rec: dict) -> str:
    return OU.identity_of(rec).claim_id


def _tree_fp(root: str) -> dict:
    fp = {}
    if not os.path.isdir(root):
        return fp
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                fp[p] = hashlib.sha256(fh.read()).hexdigest()
    return fp


def src(sid="S1", status="current", h="h1", as_of="2026-03-31"):
    return {"source_id": sid, "source_status": status, "content_hash": h, "as_of": as_of}


# =============================================================================
# 1. reorder-keeps-id  (EXCEL / xlsx)
# =============================================================================
def t_reorder_keeps_id() -> None:
    print("\n[1] reorder-keeps-id  (EXCEL / xlsx)")
    prev = {"surface": "excel", "fmt": "xlsx", "claims": [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "Outputs!B14", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "rev"},
        {"claim_text": "Headcount = 412", "locator": "Outputs!B20", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "hc"},
    ]}
    # current: order swapped AND the first claim MOVED to a new cell (same text/source)
    cur = {"surface": "excel", "fmt": "xlsx", "claims": [
        {"claim_text": "Headcount = 412", "locator": "Outputs!B20", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z"},
        {"claim_text": "Revenue = EUR 48.2M", "locator": "Outputs!B30", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "slot": "rev"},
    ]}
    pr, cr = SA.extract_claims(prev), SA.extract_claims(cur)
    rev_id = _id(prev["claims"][0]) if False else _id(pr[1])  # via adapter records
    rev_id = _id([r for r in pr if "Revenue" in r["claim_text"]][0])
    cs = OU.classify_changes(pr, cr)
    by_id = {c["claim_id"]: c for c in cs["changes"]}
    pids = {_id(r) for r in pr}
    cids = {_id(r) for r in cr}
    check(pids == cids, "reorder/move: the claim-id SET is identical across the reorder")
    check(cs["counts"]["new"] == 0 and cs["counts"]["dropped"] == 0,
          "reorder/move mints NO new and drops NO claim (regression guard)")
    check(cs["counts"]["unchanged"] == 2, "both claims classify unchanged")
    check(by_id[rev_id]["moved"] and by_id[rev_id]["locator"] == "Outputs!B30"
          and by_id[rev_id]["prev_locator"] == "Outputs!B14",
          "the moved claim keeps its id; its locator is updated B14 -> B30")
    check(by_id[rev_id]["carried_verdict"] == "verified",
          "the prior 'verified' verdict carries on the moved-but-unchanged claim")


# =============================================================================
# 2. stale-waiver-no-carry  (PPT / pptx)
# =============================================================================
def t_stale_waiver_no_carry() -> None:
    print("\n[2] stale-waiver-no-carry  (PPT / pptx)")
    expired = {"owner": "Alex Example", "decision": "accept residual risk",
               "scope": "gross-margin", "travel_class": "internal", "signoff": "approved",
               "expiry": "2026-01-31"}
    prev = {"surface": "ppt", "fmt": "pptx", "claims": [
        {"claim_text": "Gross margin = 42%", "locator": "slide 6/chart", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "materiality_class": "numeric",
         "verdict": "ready-with-limitations", "slot": "gm", "waiver": expired},
    ]}
    cur = {"surface": "ppt", "fmt": "pptx", "claims": [
        {"claim_text": "Gross margin = 42%", "locator": "slide 6/chart", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "materiality_class": "numeric",
         "verdict": "ready-with-limitations", "slot": "gm", "waiver": expired},
    ]}
    pr, cr = SA.extract_claims(prev), SA.extract_claims(cur)
    cid = _id(pr[0])
    cs = OU.classify_changes(pr, cr, now="2026-05-31T00:00:00Z", travel_class="internal")
    ch = {c["claim_id"]: c for c in cs["changes"]}[cid]
    check(ch["carried_verdict"] is None,
          "an EXPIRED waiver does NOT carry its verdict (the cardinal regression)")
    check(ch["classification"] == "updated" and ch["status"] == "needs-review",
          "the claim re-enters needs-review")
    check("waiver-expired" in (ch.get("invalidation") or []),
          "the change-set records 'waiver-expired' as the invalidation cause")
    check(cs["carried_forward"]["waivers"] == 0,
          "zero waivers are carried forward (the expired one is not counted)")


# =============================================================================
# 3. superseded-source-invalidates-verdict  (DOC / docx)
# =============================================================================
def t_superseded_invalidates() -> None:
    print("\n[3] superseded-source-invalidates-verdict  (DOC / docx)")
    prev = {"surface": "doc", "fmt": "docx", "claims": [
        {"claim_text": "Q4 outlook = EUR 50.0M (estimate)", "locator": "para:9",
         "sources": [src("S1", "current", "h-s1", "2026-03-31")],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "q4"},
    ]}
    d_id = _id(SA.extract_claims(prev)[0])
    cur = {"surface": "doc", "fmt": "docx", "claims": [
        {"claim_text": "Q4 outlook = EUR 47.5M (actual)", "locator": "para:9",
         "sources": [src("S2", "current", "h-s2", "2026-06-30")],
         "retrieval_timestamp": "2026-06-30T00:00:00Z", "slot": "q4", "supersedes": [d_id]},
    ]}
    pr, cr = SA.extract_claims(prev), SA.extract_claims(cur)
    cs = OU.classify_changes(pr, cr)
    by_id = {c["claim_id"]: c for c in cs["changes"]}
    new_id = _id(cr[0])
    check(by_id[d_id]["classification"] == "superseded" and by_id[d_id]["carried_verdict"] is None,
          "the old-source claim is superseded and CANNOT carry its prior verdict")
    check(cs["counts"]["superseded"] == 1 and len(cs["supersessions"]) == 1,
          "exactly one supersession link is emitted")
    link = cs["supersessions"][0]
    check(link["blended"] is False and "50.0" in link["prior_value"] and "47.5" in link["new_value"],
          "old + new values are LINKED and both preserved — never blended/averaged")
    check("S1" in {s["source_id"] for s in link["prior_sources"]}
          and "S2" in {s["source_id"] for s in link["new_sources"]},
          "the superseded source (S1) is linked to the replacing source (S2)")
    check(by_id[new_id]["classification"] == "new" and d_id in (by_id[new_id].get("supersedes") or []),
          "the replacement is a NEW identity recording what it supersedes (re-enters review)")


# =============================================================================
# 4. dropped-claim-tombstoned  (DOC / md)
# =============================================================================
def t_dropped_tombstoned() -> None:
    print("\n[4] dropped-claim-tombstoned  (DOC / md)")
    prev = {"surface": "doc", "fmt": "md", "claims": [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "para:2", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "rev"},
        {"claim_text": "One-off legal provision = EUR 1.1M", "locator": "para:7", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "legal"},
    ]}
    cur = {"surface": "doc", "fmt": "md", "claims": [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "para:2", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "slot": "rev"},
    ]}
    pr, cr = SA.extract_claims(prev), SA.extract_claims(cur)
    dropped_id = _id(pr[1])
    cs = OU.classify_changes(pr, cr)
    by_id = {c["claim_id"]: c for c in cs["changes"]}
    check(cs["counts"]["dropped"] == 1, "exactly one dropped claim")
    check(by_id[dropped_id]["classification"] == "dropped"
          and by_id[dropped_id].get("supersession_state") == "tombstoned",
          "the dropped claim is TOMBSTONED, not silently deleted")
    check(dropped_id in {t["claim_id"] for t in cs["tombstones"]},
          "the tombstone is surfaced in the report")
    nxt = OU.build_next_claim_set(cr, cs, "test-run")
    keep = {c["claim_id"]: c for c in nxt["claims"]}
    check(dropped_id in keep and keep[dropped_id]["supersession_state"] == "tombstoned",
          "the tombstone is carried into the forward claim-set (kept for audit, never lost)")


# =============================================================================
# 5. obsolete-locator-rejected  (DOC / html)
# =============================================================================
def t_obsolete_locator_rejected() -> None:
    print("\n[5] obsolete-locator-rejected  (DOC / html)")
    prev = {"surface": "doc", "fmt": "html", "claims": [
        # P1 will MOVE (note must be re-pointed to the new, resolving locator)
        {"claim_text": "Revenue = EUR 48.2M", "locator": "para:3", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "rev",
         "evidence_note": {"locator": "para:3", "text": "Claim: Revenue ... Source IDs: S1"}},
        # P2 will be DROPPED (its note points at para:7, which no longer exists)
        {"claim_text": "Legal provision = EUR 1.1M", "locator": "para:7", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "legal",
         "evidence_note": {"locator": "para:7", "text": "Claim: Legal ... Source IDs: S1"}},
    ]}
    cur = {"surface": "doc", "fmt": "html", "claims": [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "para:5", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "slot": "rev"},  # moved 3 -> 5
        # P2 absent; para:7 no longer in the artifact
    ]}
    pr, cr = SA.extract_claims(prev), SA.extract_claims(cur)
    p1_id, p2_id = _id(pr[0]), _id(pr[1])
    cs = OU.classify_changes(pr, cr)
    prior_by_id = {_id(r): r for r in pr}
    cur_index = SA.locator_index(cur)
    rp = SA.repoint_evidence(cs, prior_by_id, cur_index)
    rep = {r["claim_id"]: r for r in rp["repointed"]}
    inv = {r["claim_id"]: r for r in rp["invalidated"]}
    check(p1_id in rep and rep[p1_id]["to"] == "para:5" and rep[p1_id]["from"] == "para:3",
          "the moved claim's note is RE-POINTED para:3 -> para:5 (the resolving locator)")
    check(p2_id in inv and inv[p2_id]["review_status"] == "unsupported",
          "the dropped claim's note is INVALIDATED (obsolete pointer dropped, marked unsupported)")
    check("para:7" not in {r["to"] for r in rp["repointed"]},
          "the obsolete locator para:7 is never re-pointed TO (it does not resolve)")
    check(rp["dangling"] == [] and rp["all_resolve"] is True,
          "post-condition: NO surviving evidence note cites an obsolete/unresolved locator")


# =============================================================================
# 6. layout-drift-detected  (PPT brand + EXCEL layout, with a no-drift control)
# =============================================================================
def t_layout_drift_detected() -> None:
    print("\n[6] layout-drift-detected  (PPT brand + EXCEL layout)")
    ppt_prev = {"surface": "ppt", "fmt": "pptx",
                "layout": {"theme": "BrandDark", "fonts": ["PingL"], "master": "Brand-Master",
                           "slide_count": 8}}
    ppt_cur = {"surface": "ppt", "fmt": "pptx",
               "layout": {"theme": "BrandLight", "fonts": ["PingL"], "master": "Brand-Master",
                          "slide_count": 8}}
    sp, sc = SA.layout_signature(ppt_prev), SA.layout_signature(ppt_cur)
    d = SA.detect_layout_drift(sp, sc, surface="ppt", fmt="pptx")
    check(d is not None and d["type"] == "brand-drift" and "theme" in d["changed"],
          "a brand/theme swap between periods is DETECTED and typed brand-drift")
    check(d and d["severity"] in ("should-fix", "must-fix") and d["status"] == "open",
          "the drift is surfaced as a review_contract finding (not silently passed)")
    # no-drift control — identical signatures must NOT raise (no false positive)
    check(SA.detect_layout_drift(sp, sp, surface="ppt", fmt="pptx") is None,
          "an unchanged layout/brand signature raises NO drift (no false positive)")
    # EXCEL structural drift
    xl_prev = {"surface": "excel", "fmt": "xlsx",
               "layout": {"sheet_names": ["Outputs", "Model"], "sheet_count": 2}}
    xl_cur = {"surface": "excel", "fmt": "xlsx",
              "layout": {"sheet_names": ["Outputs", "Model", "Hidden"], "sheet_count": 3}}
    dx = SA.detect_layout_drift(SA.layout_signature(xl_prev), SA.layout_signature(xl_cur),
                                surface="excel", fmt="xlsx")
    check(dx is not None and dx["type"] == "layout-drift" and "sheet_names" in dx["changed"],
          "a workbook structure change is DETECTED and typed layout-drift")


# =============================================================================
# 7. pdf-sidecar-immutable  (DOC / pdf)  + end-to-end read-only / no-§5-touch
# =============================================================================
def make_prior_run(d: str, claims: list, layout: dict | None = None) -> None:
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "claim-set.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema_version": "3", "run_id": "prior", "claims": claims}, fh, indent=2)
    manifest = {  # a v2-shaped manifest (has `verification`) ⇒ cross-version read via fd-03
        "manifest_version": "1.0", "run_id": "prior",
        "travel_class": {"effective": "internal"},
        "sensitivity": {"run_max": "internal", "sources": [{"source_id": "S1", "sensitivity": "internal"}]},
        "routing": {"policy": "two-context", "two_context": True},
        "verification": {"results": []},
    }
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    if layout is not None:
        with open(os.path.join(d, "surface-refresh.json"), "w", encoding="utf-8") as fh:
            json.dump({"layout_signature": layout, "current_claims": claims}, fh, indent=2)


def t_pdf_sidecar_immutable() -> None:
    print("\n[7] pdf-sidecar-immutable  (DOC / pdf) + read-only / no-§5-touch")
    caps = SA.surface_caps("doc", "pdf")
    check(len(caps) == 1 and caps[0]["cap"] == "sidecar-only-pdf"
          and caps[0]["capped_at"] == "ready-with-limitations"
          and caps[0]["forwarding"] == "not-ready-for-forwarding",
          "a PDF forces the sidecar-only-pdf cap (ready-with-limitations / not-ready-for-forwarding)")
    check("pdf" in SA.IMMUTABLE_FORMATS, "pdf is registered immutable (never rewritten)")

    work = tempfile.mkdtemp(prefix="otl_surface_pdf_")
    prev_dir = os.path.join(work, ".trust", "prior")
    out_root = os.path.join(work, ".trust")
    prior_claims = [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "page:2", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "verdict": "verified", "slot": "rev",
         "evidence_note": {"locator": "page:2", "text": "Claim: Revenue ... Source IDs: S1"}},
    ]
    make_prior_run(prev_dir, prior_claims, layout={"surface": "doc", "fmt": "pdf",
                                                   "section_count": 4})
    cur = {"surface": "doc", "fmt": "pdf", "layout": {"section_count": 4}, "claims": [
        {"claim_text": "Revenue = EUR 48.2M", "locator": "page:2", "sources": [src()],
         "retrieval_timestamp": "2026-03-31T00:00:00Z", "slot": "rev"}]}

    refs = os.path.join(ROOT, "skills", "trust-review", "references")
    before_prev = _tree_fp(prev_dir)
    refs_before = _tree_fp(refs)

    res = SA.refresh_surface(prev_dir, cur, run_id="2026-05-31T2230_s03-pdf",
                             now="2026-05-31T00:00:00Z", travel_class="internal", out_root=out_root)

    after_prev = _tree_fp(prev_dir)
    refs_after = _tree_fp(refs)
    adapter = res["adapter"]

    check(adapter["immutable_source"] is True
          and "speaker-notes-sidecar" in adapter["preserves"],
          "the pdf refresh is immutable-source / sidecar-only (the file is never rewritten)")
    check(any(c["cap"] == "sidecar-only-pdf" for c in adapter["degraded_status"]),
          "the sidecar-only-pdf cap is recorded on the surface-refresh")
    out_dir = adapter["out_dir"]
    man = json.load(open(os.path.join(out_dir, "manifest.json"), encoding="utf-8"))
    check(any(c["cap"] == "sidecar-only-pdf" for c in man.get("degraded_status", [])),
          "the cap is written into THIS run's manifest (§7.1)")
    # no source .pdf was ever created/written by the adapter (sidecar only)
    produced = [p for p in _tree_fp(out_root) if p.lower().endswith(".pdf")]
    check(not produced, "the adapter writes NO .pdf — evidence is sidecar only")
    check(before_prev == after_prev and res["core"]["read_only_verified"] is True,
          "the prior run is consumed READ-ONLY (byte-identical before/after)")
    check(refs_before == refs_after,
          "references/ is byte-identical — no §5 release threshold was touched")


def main() -> int:
    print("UP-03 per-surface refresh adapters — NEGATIVE fixture battery")
    print("=" * 70)
    t_reorder_keeps_id()
    t_stale_waiver_no_carry()
    t_superseded_invalidates()
    t_dropped_tombstoned()
    t_obsolete_locator_rejected()
    t_layout_drift_detected()
    t_pdf_sidecar_immutable()
    print("=" * 70)
    if FAILURES:
        print("RESULT: FAIL (%d check(s) failed)" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: PASS — every UP-03 guard holds across xlsx / pptx / docx / md / html / "
          "pdf-sidecar: reorder keeps IDs, a stale waiver cannot carry, a superseded source "
          "invalidates the verdict (linked not blended), a dropped claim is tombstoned, an "
          "obsolete evidence locator is re-pointed or invalidated, layout/brand drift is "
          "detected, and a PDF stays an immutable sidecar — prior run read-only, no §5 "
          "threshold touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
