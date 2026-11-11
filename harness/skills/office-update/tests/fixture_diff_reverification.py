#!/usr/bin/env python3
"""S04/UP-02 — diff-aware re-verification + invalidation matrix (codex v3 finding #2).

Proves the verification layer makes a recurring refresh CHEAP and HONEST:

  * VERIFY only the changed/new set — never the whole document.
  * CARRY a prior §2 verdict forward for an identity-stable, unchanged claim, with a POINTER
    to the prior run.
  * but RUN every carried verdict through the EXPLICIT invalidation matrix
    (verification.INVALIDATION_MATRIX / decide_carry): text / locator / source / unit-period /
    source-age / waiver-owner / travel-class-escalation -> carry | re-open | re-source — so a
    claim whose source aged or whose waiver owner changed is RE-OPENED, never silently kept
    'verified'.
  * APPLY the SV-07 budget to the CHANGED SET ONLY (the Verifier runs over the re-verify set,
    not the 300-claim document).

The headline acceptance fixture (the up-02 card): a 300-claim doc where 12 claims changed
=> EXACTLY 12 re-verified; 1 carried verdict invalidated on SOURCE-AGE; 1 on
WAIVER-OWNER-CHANGE. Built ON the S02 office-update CORE (the change-set) + the S01 fd-02
identity. references/ (the FROZEN §5 thresholds) is byte-identical — relaxes no threshold.

Pure stdlib + deterministic (injected backend). Exit 0 = PASS.
"""
from __future__ import annotations

import copy
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # plugin root: office-trust-layer/
sys.path.insert(0, os.path.join(ROOT, "skills", "trust-review", "references", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "skills", "office-update", "scripts"))

import adapter_registry as AR        # noqa: E402
import verification as V             # noqa: E402
import verification_wiring as VW     # noqa: E402
import claim_identity as CI          # noqa: E402
import office_update as OU           # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _reg(tools):
    return AR.AdapterRegistry(available_tools=tools, local_caps=[])


def _sha_files(paths):
    out = {}
    for p in paths:
        with open(p, "rb") as fh:
            out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


# -----------------------------------------------------------------------------
# the 300-claim document: 288 unchanged (carry) · 12 changed (re-verify)
# of the 288, two will be tripped by the matrix at re-verification time:
#   - claim STALE  (retrieval_timestamp old + a freshness policy applied here) -> re-source
#   - claim WAIVER (its verdict was granted under a DIFFERENT waiver owner)     -> re-open
# -----------------------------------------------------------------------------
RECENT = "2026-05-01T00:00:00Z"     # within a 180-day freshness window @ 2026-05-31
STALE = "2025-01-01T00:00:00Z"      # > 180 days old
NOW = "2026-05-31T00:00:00Z"
FRESHNESS_DAYS = 180

STALE_SLOT = 50        # which unchanged claim carries the stale source
WAIVER_SLOT = 120      # which unchanged claim's verdict rests on a waiver

WAIVER_OWNER_NOW = {"owner": "cfo-2026", "decision": "accept-limitation",
                    "scope": "fy25-revenue", "travel_class": "board-regulatory",
                    "signoff": "approved"}  # valid (named-owner signoff) so it CARRIES at the office-update layer


def _src(i, *, as_of="2026-03-31"):
    return [{"source_id": "S1", "source_status": "current",
             "content_hash": "h%d" % i, "as_of": as_of}]


def _base_claim(i, *, retr=RECENT, unit="EUR", waiver=None, verdict="verified"):
    rec = {
        "claim_text": "Metric %d equals %d basis points in FY25" % (i, i * 7),
        "locator": "Memo.docx#para%d" % i,
        "sources": _src(i),
        "retrieval_timestamp": retr,
        "materiality_class": "numeric",
        "unit": unit,
        "verdict": verdict,            # the PRIOR §2 verdict (office-update reads this to carry)
        "verifiability": "external",   # for the Verifier
        "severity": "should-fix",
    }
    if waiver is not None:
        rec["waiver"] = waiver
    return rec


def build_prior_and_current():
    """288 unchanged + 12 changed (unit/period shift => identity-stable 'updated')."""
    prior, current = [], []
    for i in range(300):
        if i < 12:
            # CHANGED: identity-stable claim whose UNIT shifts (a §7.5 unit-or-period change).
            # Same text + same source => same claim_id, but office-update classifies it 'updated'
            # and drops the carried verdict -> it must be re-verified.
            prior.append(_base_claim(i, unit="EUR"))
            current.append(_base_claim(i, unit="USD"))
        else:
            # UNCHANGED: identical identity inputs => carries the prior verdict.
            retr = STALE if i == STALE_SLOT else RECENT
            if i == WAIVER_SLOT:
                # the verdict rested on a waiver; the CURRENT claim-set carries owner cfo-2026
                # (so office-update sees no scope change and carries it) — but the prior RUN
                # granted the verdict under a DIFFERENT owner (modelled via prior_waiver_scope).
                rec_p = _base_claim(i, waiver=dict(WAIVER_OWNER_NOW), verdict="ready-with-limitations")
                rec_c = _base_claim(i, waiver=dict(WAIVER_OWNER_NOW), verdict="ready-with-limitations")
                prior.append(rec_p)
                current.append(rec_c)
            else:
                prior.append(_base_claim(i, retr=retr))
                current.append(_base_claim(i, retr=retr))
    return prior, current


def main() -> int:
    print("S04 diff-aware re-verification — UP-02 invalidation matrix (codex v3 finding #2)")
    print("=" * 78)

    conv = [os.path.join(ROOT, "skills", "trust-review", "references", "conventions.md"),
            os.path.join(ROOT, "skills", "trust-review", "references", "conventions.json")]
    conv_before = _sha_files(conv)

    # ---- the explicit matrix is a real table with exactly the seven rows -----------
    print("[the invalidation matrix is an explicit table]")
    check(set(V.INVALIDATION_MATRIX) == {
        "text", "locator", "source", "unit-period", "source-age",
        "waiver-owner", "travel-class-escalation"},
        "the matrix has EXACTLY the seven input change-classes")
    check(V.INVALIDATION_MATRIX["locator"] == V.ACTION_CARRY,
          "locator (a pure move) -> CARRY (identity stable; verdict holds)")
    check(V.INVALIDATION_MATRIX["text"] == V.ACTION_REOPEN
          and V.INVALIDATION_MATRIX["unit-period"] == V.ACTION_REOPEN
          and V.INVALIDATION_MATRIX["waiver-owner"] == V.ACTION_REOPEN
          and V.INVALIDATION_MATRIX["travel-class-escalation"] == V.ACTION_REOPEN,
          "text / unit-period / waiver-owner / travel-class-escalation -> RE-OPEN")
    check(V.INVALIDATION_MATRIX["source"] == V.ACTION_RESOURCE
          and V.INVALIDATION_MATRIX["source-age"] == V.ACTION_RESOURCE,
          "source / source-age -> RE-SOURCE (go back to the source)")
    check(V.matrix_action("any-unknown-class") == V.ACTION_REOPEN,
          "an unknown change-class FAILS SAFE to re-open — never a silent carry")

    # ---- build the 300-claim doc + the office-update change-set --------------------
    print("[the 300-claim document + the office-update CHANGE-SET (S02 CORE)]")
    prior, current = build_prior_and_current()
    prior_snapshot = copy.deepcopy(prior)
    # office-update does NOT presume a freshness policy => now/freshness_days NOT passed here;
    # the unchanged claims (including the soon-to-be-stale one) carry their prior verdict.
    change_set = OU.classify_changes(prior, current, travel_class="board-regulatory")
    counts = change_set["counts"]
    check(counts["updated"] == 12, "office-update classifies EXACTLY 12 claims 'updated' (the changed set) — got %d" % counts["updated"])
    check(counts["unchanged"] == 288, "the other 288 are 'unchanged' (carry-candidates) — got %d" % counts["unchanged"])
    check(counts["new"] == 0 and counts["dropped"] == 0 and counts["superseded"] == 0,
          "no spurious new / dropped / superseded (identity stable across the refresh)")

    # the two carry-candidates the matrix must trip — find their claim_ids
    stale_id = OU.identity_of(current[STALE_SLOT]).claim_id
    waiver_id = OU.identity_of(current[WAIVER_SLOT]).claim_id
    # the prior RUN granted the waiver claim's verdict under a DIFFERENT owner:
    prior_waiver_scope = {waiver_id: "sha-of-the-prior-owner-cfo-2024-scope"}

    # ---- run the diff-aware re-verification stage ----------------------------------
    print("[re-verify ONLY the changed/new set; carry the rest; matrix re-opens the unsafe carries]")
    reg = _reg(["mcp__x__web_search_exa"])
    prior_run_ref = ".trust/2026-05-30T0900_prev-refresh"
    manifest = {"travel_class": {"effective": "board-regulatory"}}
    manifest = VW.run_reverification_stage(
        manifest, change_set=change_set, current_claims=current,
        prior_run_ref=prior_run_ref, registry=reg,
        travel_class="board-regulatory",
        now=NOW, freshness_days=FRESHNESS_DAYS,
        prior_waiver_scope=prior_waiver_scope,
        search_fn=lambda q: "",  # injected backend (deterministic); board-regulatory auto-runs external
    )
    rv = manifest["reverification"]
    c = rv["counts"]

    check(c["changed"] == 12, "the change-set changed count is 12 — got %d" % c["changed"])
    check(c["reverified_from_change"] == 12,
          "EXACTLY 12 re-verified from the changed set (12 changed -> 12 re-verified) — got %d"
          % c["reverified_from_change"])
    check(c["reverified_from_invalidation"] == 2,
          "2 carried verdicts were invalidated by the matrix and re-queued — got %d"
          % c["reverified_from_invalidation"])
    check(c["carried"] == 286, "286 verdicts carried forward (288 unchanged - 2 invalidated) — got %d" % c["carried"])
    check(c["reverified_total"] == 14, "re-verify set = 12 changed + 2 invalidated = 14 — got %d" % c["reverified_total"])

    # the two specific invalidations the card calls out
    inval = {x["claim_id"]: x for x in rv["invalidated"]}
    check(len(rv["invalidated"]) == 2, "EXACTLY two carried verdicts invalidated — got %d" % len(rv["invalidated"]))
    stale = inval.get(stale_id)
    check(stale is not None and stale["action"] == V.ACTION_RESOURCE
          and any(t["change_class"] == "source-age" for t in stale["triggered"]),
          "1 carried verdict invalidated on SOURCE-AGE -> re-source")
    waiv = inval.get(waiver_id)
    check(waiv is not None and waiv["action"] == V.ACTION_REOPEN
          and any(t["change_class"] == "waiver-owner" for t in waiv["triggered"]),
          "1 carried verdict invalidated on WAIVER-OWNER-CHANGE -> re-open")

    # carried verdicts each keep a POINTER to the prior run; the invalidated two are NOT carried
    carried_ids = {x["claim_id"] for x in rv["carried"]}
    check(stale_id not in carried_ids and waiver_id not in carried_ids,
          "the two invalidated claims are NOT in the carried set (verdict not reused)")
    check(all(x["prior_run"] == prior_run_ref and x["verdict"] for x in rv["carried"]),
          "every carried verdict keeps a pointer to the prior run + its verdict value")

    # ---- the SV-07 budget covered the CHANGED SET ONLY -----------------------------
    print("[the SV-07 budget applies to the changed set ONLY — never the 300-claim doc]")
    verif = rv["verification"]
    checked = verif["claims_checked"]
    check(checked == 14, "the Verifier checked the 14-claim re-verify set, not 300 (claims_checked=%d)" % checked)
    distinct_verified = {r["claim"] for r in verif["results"]}
    check(len(distinct_verified) == 14, "results cover the 14-claim re-verify set only (%d distinct claims)" % len(distinct_verified))
    cap = V.DEFAULT_CAPS["board-regulatory"]
    check(checked <= cap, "within the §2.3 board-regulatory cap (%d <= %d)" % (checked, cap))
    verified_texts = {r["claim"] for r in verif["results"]}
    carried_texts = {current[i]["claim_text"] for i in range(12, 300)
                     if i not in (STALE_SLOT, WAIVER_SLOT)}
    check(verified_texts.isdisjoint(carried_texts),
          "NOT ONE of the 286 carried claims entered the Verifier (budget spent on the changed set only)")

    # ---- discipline: prior run read-only · references/ byte-identical --------------
    print("[discipline — prior records read-only · references/ (§5) byte-identical]")
    check(prior == prior_snapshot, "the prior claim-set was consumed READ-ONLY (unmutated)")
    check(_sha_files(conv) == conv_before,
          "conventions.md + conventions.json byte-identical — no §5 threshold relaxed, no §5 change")
    # the matrix table is echoed into the manifest for the audit trail
    check(rv["matrix"] == dict(V.INVALIDATION_MATRIX) and rv["prior_run"] == prior_run_ref,
          "the manifest reverification block records the matrix table + the prior-run pointer")

    print("-" * 78)
    if FAILURES:
        print("RESULT: FAIL — %d check(s) failed:" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: PASS — diff-aware re-verification verifies only the changed/new set, carries "
          "identity-stable verdicts forward through the explicit invalidation matrix (with a "
          "prior-run pointer), re-opens on source-age + waiver-owner, and spends the SV-07 budget "
          "on the changed set only; references/ (§5) untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
