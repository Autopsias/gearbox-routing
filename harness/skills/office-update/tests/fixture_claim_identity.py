#!/usr/bin/env python3
"""FD-02 functional proof: the canonical claim identity + moved-claim diff (conventions.md §7.5).

Asserts, deterministically (pure stdlib, no I/O):
  1. A claim that MOVES (same normalized text + same source fingerprint, NEW locator) keeps its
     claim_id — identity STABLE — and CAN carry a prior verdict.
  2. Text-formatting differences that don't change meaning (12% vs 12.0%, whitespace, case,
     thousands separators) normalize to the SAME identity (no spurious new id).
  3. A claim whose SOURCE FINGERPRINT changes gets a NEW claim_id and CANNOT inherit a verdict.
  4. A claim whose TEXT changes gets a NEW claim_id and CANNOT inherit a verdict.
  5. Each §7.5 invalidation class is detected and forces re-review even when the identity is
     stable: source-stale, waiver-scope-changed, superseded-or-tombstoned, materiality-escalated,
     unit-or-period-changed.
  6. A locator move ALONE is NOT an invalidation class (moving is safe).

Run via fixtures/run_fixtures.sh, or directly:  python fixtures/fixture_claim_identity.py
Exit 0 = PASS, non-zero = FAIL.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "skills", "trust-review", "references", "scripts"))

import claim_identity as CI  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _ident(text, locator, sources, *, mat="numeric", ts="2026-05-31T00:00:00Z",
           waiver=None, supersession="active", unit=None, period=None, surface="excel"):
    return CI.compute_identity(claim_text=text, locator=locator, sources=sources,
                               retrieval_timestamp=ts, materiality_class=mat, surface=surface,
                               waiver=waiver, supersession_state=supersession, unit=unit, period=period)


def main() -> int:
    print("FD-02 canonical claim identity + moved-claim diff (conventions.md §7.5)")
    print("-" * 70)

    S = [CI.SourceRef("S1", "current", content_hash="h-abc", as_of="2026-03-31")]

    # 1. + 2. MOVE with cosmetic text difference ⇒ stable id, carries verdict
    a = _ident("Revenue grew 12% YoY in FY25", "Model.xlsx#Outputs!B14", S)
    moved = _ident("revenue grew 12.0%  YoY in FY25.", "Model.xlsx#Outputs!B27", S)
    d = CI.diff(a, moved)
    check(a.claim_id == moved.claim_id, "a MOVED claim keeps its claim_id (identity stable)")
    check(d["relation"] == "moved" and d["identity_stable"] and d["can_carry_verdict"],
          "the moved-claim diff is relation=moved, identity_stable, can_carry_verdict")
    check(d["invalidation"] == [], "a pure move carries NO invalidation class (locator move is safe)")
    check(a.normalized_text_hash == moved.normalized_text_hash,
          "cosmetic text differences (case/%/whitespace/trailing-zero) normalize to the same hash")

    # 2b. fields are all present + correct shape
    for f in ("claim_id", "normalized_text_hash", "locator_hash", "source_fingerprint",
              "retrieval_timestamp", "materiality_class", "waiver_scope_hash", "supersession_state"):
        check(hasattr(a, f), f"claim identity carries field '{f}'")
    check(a.claim_id.startswith("C-") and len(a.claim_id) == 14, "claim_id is C-<12 hex>")

    # 3. SOURCE fingerprint change ⇒ new identity, cannot inherit
    S2 = [CI.SourceRef("S1", "superseded", content_hash="h-xyz", as_of="2026-03-31")]
    src_changed = _ident("Revenue grew 12% YoY in FY25", "Model.xlsx#Outputs!B14", S2)
    dc = CI.diff(a, src_changed)
    check(a.claim_id != src_changed.claim_id, "a SOURCE-fingerprint change yields a NEW claim_id")
    check(dc["relation"] == "changed-identity" and not dc["identity_stable"]
          and not dc["can_carry_verdict"] and "source-changed" in dc["invalidation"],
          "a source change is changed-identity, cannot carry a verdict, flags source-changed")

    # 4. TEXT change ⇒ new identity, cannot inherit
    txt_changed = _ident("Revenue grew 9% YoY in FY25", "Model.xlsx#Outputs!B14", S)
    dt = CI.diff(a, txt_changed)
    check(a.claim_id != txt_changed.claim_id and dt["relation"] == "changed-identity"
          and not dt["can_carry_verdict"] and "text-changed" in dt["invalidation"],
          "a meaning-changing text edit (12%→9%) yields a NEW id, flags text-changed, cannot carry")

    # 5. invalidation classes that force re-review even with a STABLE identity
    # 5a. source-stale (retrieval older than the freshness window)
    old = _ident("Revenue grew 12% YoY in FY25", "Model.xlsx#Outputs!B14", S, ts="2026-01-01T00:00:00Z")
    ds = CI.diff(old, old, now="2026-05-31T00:00:00Z", freshness_days=30)
    check("source-stale" in ds["invalidation"] and not ds["can_carry_verdict"],
          "source-stale (beyond the freshness window) invalidates a carried verdict")
    # without a window, staleness is NOT assumed
    ds0 = CI.diff(old, old)
    check("source-stale" not in ds0["invalidation"] and ds0["can_carry_verdict"],
          "no freshness window ⇒ staleness is not assumed (policy-driven), verdict carries")

    # 5b. waiver-scope-changed
    w1 = _ident("X", "L1", S, mat="headline", waiver={"owner": "CFO", "decision": "accept", "scope": "fy25", "travel_class": "board-regulatory"})
    w2 = _ident("X", "L1", S, mat="headline", waiver={"owner": "CFO", "decision": "accept", "scope": "fy26", "travel_class": "board-regulatory"})
    dw = CI.diff(w1, w2)
    check("waiver-scope-changed" in dw["invalidation"] and not dw["can_carry_verdict"],
          "a changed waiver scope invalidates a carried verdict")

    # 5c. superseded-or-tombstoned
    act = _ident("Y", "L2", S, mat="numeric")
    tomb = _ident("Y", "L2", S, mat="numeric", supersession="tombstoned")
    dtomb = CI.diff(act, tomb)
    check("superseded-or-tombstoned" in dtomb["invalidation"] and not dtomb["can_carry_verdict"],
          "a tombstoned/superseded claim invalidates a carried verdict")

    # 5d. materiality-escalated (numeric → headline)
    lo = _ident("Z", "L3", S, mat="numeric")
    hi = _ident("Z", "L3", S, mat="headline")
    dm = CI.diff(lo, hi)
    check("materiality-escalated" in dm["invalidation"] and not dm["can_carry_verdict"],
          "a materiality escalation (numeric→headline) invalidates a carried verdict")
    # de-escalation does NOT invalidate
    dde = CI.diff(hi, lo)
    check("materiality-escalated" not in dde["invalidation"],
          "a materiality DE-escalation is not an invalidation class")

    # 5e. unit-or-period-changed (same surface text, unit shifts) — the silent-drift case
    u1 = _ident("margin 6", "L4", S, unit="%", period="Q1")
    u2 = _ident("margin 6", "L4", S, unit="pp", period="Q1")
    du = CI.diff(u1, u2)
    check("unit-or-period-changed" in du["invalidation"] and not du["can_carry_verdict"],
          "a unit/period shift (% → pp) invalidates a carried verdict even when text looks similar")

    # 6. every declared invalidation class is a known constant
    for cls in ("text-changed", "source-changed", "unit-or-period-changed", "source-stale",
                "waiver-scope-changed", "superseded-or-tombstoned", "materiality-escalated"):
        check(cls in CI.INVALIDATION_CLASSES, f"'{cls}' is a declared §7.5 invalidation class")

    print("-" * 70)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed)")
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: PASS — a moved claim keeps its identity and may carry its verdict; a text/source "
          "change mints a NEW identity that cannot inherit a verdict; and every §7.5 invalidation "
          "class forces re-review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
