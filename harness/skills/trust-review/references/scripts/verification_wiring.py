#!/usr/bin/env python3
"""
verification_wiring.py — SV-06 (v2 Source Verification II)
==========================================================
**Auto-by-travel-class wiring** of the verification pass into the run, at the
**source-packet stage**, with an idempotent **re-check in trust-review**.

The contract (sensitivity-routing-policy.md §5.1, the routing table; conventions.md §2.3
budgets) decides *whether* verification runs, as a function of the travel class:

    | travel class      | external (web)        | internal (vault/memory) |
    |-------------------|-----------------------|-------------------------|
    | Internal          | opt-in (off default)  | opt-in (off default)    |
    | Travels           | auto                  | auto                    |
    | Board/Regulatory  | auto (egress-guarded) | auto                    |

This module is the small, deterministic policy + driver that turns that table into a run:

  * `verification_policy(travel_class, opt_in_*)` → which passes run (pure policy).
  * `run_verification_stage(manifest, …)` → runs the SV-01 `Verifier` with those passes,
    resolving backends THROUGH the F2-04 registry (never a hard-coded tool), and **merges a
    `verification` block into the run manifest**: the resolved backends (`adapter_bindings`),
    the egressed queries (`egress`), the per-claim verdicts + sources, and the budget /
    fallback / timeout notes. It is **idempotent** — it stamps the block with a fingerprint
    of (travel_class · claim set · bindings · passes · caps); a **re-check in trust-review**
    with the same inputs is a no-op that returns the existing block rather than re-running
    the backends or duplicating the record.

It writes NOTHING to disk and calls NO model/network itself; the harness persists the
returned manifest under `.trust/<run_id>/manifest.json`. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Callable, Dict, List, Optional, Sequence, TypedDict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verification as V  # noqa: E402
import claim_identity as CI  # FD-02 — canonical identity (read straight from the claim records)  # noqa: E402

STAGE_SOURCE_PACKET = "source-packet"
STAGE_TRUST_REVIEW = "trust-review"
STAGE_REFRESH = "refresh"

# Travel-class stringency order (sensitivity-routing-policy §5.1): a verdict earned at a lower
# class must be re-earned when the document escalates to a higher one.
_TRAVEL_RANK = {"internal": 0, "travels": 1, "board-regulatory": 2}


class VerificationPolicy(TypedDict):
    external: bool
    internal: bool
    reason: str


def verification_policy(travel_class: str, *, opt_in_external: bool = False,
                        opt_in_internal: bool = False) -> VerificationPolicy:
    """Decide which verification passes run for this travel class (policy §5.1).

    Internal travel class: BOTH passes are opt-in (off by default) — a human turns them on.
    Travels + Board/Regulatory: BOTH passes are automatic. (The external pass is still
    subject to the SV-03 egress guard inside the pipeline; Board/Regulatory restricted text
    that can't egress is recorded not-checked-budget, while the internal pass — no egress —
    still runs.) Returns `{external, internal, reason}`."""
    tc = (travel_class or "internal").lower()
    if tc == "internal":
        return {"external": bool(opt_in_external), "internal": bool(opt_in_internal),
                "reason": "Internal travel class: verification is opt-in (off by default)"}
    if tc in ("travels", "board-regulatory"):
        return {"external": True, "internal": True,
                "reason": "%s travel class: external + internal verification run automatically "
                          "(external egress-guarded)" % tc}
    # unknown ⇒ default UP to the Travels behaviour (never silently skip on doubt)
    return {"external": True, "internal": True,
            "reason": "unknown travel class %r ⇒ defaulted up to automatic verification" % tc}


def _fingerprint(travel_class: str, claims: Sequence[dict], bindings: dict,
                 passes: dict, caps: dict) -> str:
    """A stable hash of the inputs that determine the verification result, so a re-check
    with the same inputs is recognised as already-done (idempotent)."""
    claim_keys = sorted(V._dedupe_key(c.get("claim", "")) for c in claims)
    payload = json.dumps({
        "travel_class": (travel_class or "").lower(),
        "claims": claim_keys,
        "bindings": bindings.get("bindings", bindings) if isinstance(bindings, dict) else {},
        "passes": passes, "caps": caps,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_verification_stage(manifest: dict, *, claims: List[dict], packet: "V.SourcePacket",
                           registry, travel_class: Optional[str] = None,
                           stage: str = STAGE_SOURCE_PACKET,
                           opt_in_external: bool = False, opt_in_internal: bool = False,
                           caps: Optional[dict] = None, banned_tokens: Sequence[str] = (),
                           primary_domains: Sequence[str] = (), egress_waiver: Optional[dict] = None,
                           search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None,
                           internal_lane_fns: Optional[Dict[str, Callable]] = None,
                           internal_read_fn: Optional[Callable] = None,
                           per_call_timeout_s: Optional[float] = None,
                           per_run_timeout_s: Optional[float] = None,
                           retrieval_date: str = "", provider_label: Optional[str] = None) -> dict:
    """Run (or re-confirm) the verification pass for this run and merge a `verification`
    block into `manifest`. Returns the (mutated) manifest.

    Travel class is read from `manifest['travel_class']['effective']` unless `travel_class`
    is passed. The pass selection comes from `verification_policy`. The merge is idempotent:
    if `manifest['verification']['_fingerprint']` already matches the inputs, the existing
    block is kept and the backends are NOT re-run (the re-check in trust-review)."""
    tc = (travel_class
          or (manifest.get("travel_class", {}) or {}).get("effective")
          or (manifest.get("travel_class", {}) or {}).get("value")
          or "internal")
    policy = verification_policy(tc, opt_in_external=opt_in_external, opt_in_internal=opt_in_internal)
    caps = caps or {}
    bindings = registry.manifest_record()["adapter_bindings"] if registry else {}
    passes = {"external": policy["external"], "internal": policy["internal"]}
    fp = _fingerprint(tc, claims, bindings, passes, dict(V.DEFAULT_CAPS, **caps))

    existing = manifest.get("verification")
    if isinstance(existing, dict) and existing.get("_fingerprint") == fp:
        # idempotent re-check (e.g. trust-review re-confirming the source-packet-stage run):
        # do NOT re-run backends, do NOT duplicate the block — record that we re-confirmed.
        stages = existing.setdefault("_stages", [])
        if stage not in stages:
            stages.append(stage)
        existing["_recheck_noop"] = True
        return manifest

    # neither pass runs (Internal, no opt-in) ⇒ a stated, recorded skip — NOT a failure.
    if not (policy["external"] or policy["internal"]):
        manifest["verification"] = {
            "_doc": "SV-06 wiring: no verification pass enabled for this travel class.",
            "_fingerprint": fp, "_stages": [stage], "_recheck_noop": False,
            "travel_class": tc, "policy": policy, "passes": passes,
            "results": [], "not_checked_budget": [], "must_own": [],
            "promoted_to_verified": [],
            "note": "verification opt-in and not enabled (Internal travel class); v1 closed-world behaviour",
        }
        manifest.setdefault("egress", [])
        manifest["adapter_bindings"] = bindings
        return manifest

    verifier = V.Verifier(
        claims=claims, packet=packet, registry=registry, travel_class=tc,
        caps=caps, banned_tokens=banned_tokens, primary_domains=primary_domains,
        egress_waiver=egress_waiver, search_fn=search_fn, fetch_fn=fetch_fn,
        retrieval_date=retrieval_date, provider_label=provider_label,
        run_external=policy["external"], run_internal=policy["internal"],
        internal_lane_fns=internal_lane_fns, internal_read_fn=internal_read_fn,
        per_call_timeout_s=per_call_timeout_s, per_run_timeout_s=per_run_timeout_s)
    block = verifier.run()

    verif = block["verification"]
    verif["_fingerprint"] = fp
    verif["_stages"] = [stage]
    verif["_recheck_noop"] = False
    verif["policy"] = policy
    manifest["verification"] = verif
    manifest["egress"] = block["egress"]
    manifest["adapter_bindings"] = block["adapter_bindings"]
    return manifest


# =============================================================================
# UP-02 — diff-aware re-verification stage (codex v3 finding #2)
# =============================================================================
def _travel_rank(tc: Optional[str]) -> int:
    return _TRAVEL_RANK.get((tc or "internal").lower(), 0)


def _record_identity(rec: dict) -> "CI.ClaimIdentity":
    """Compute the fd-02 identity for one claim record. Field names are accepted flexibly so the
    same record can feed both the office-update claim-set (`claim_text`/`materiality_class`/…) and
    the Verifier (`claim`/`materiality`). A record may carry a pre-minted `claim_id`; identity is
    still computed from text+source so a move/change is detected, not trusted blindly."""
    srcs = []
    for s in (rec.get("sources") or []):
        srcs.append(CI.SourceRef(
            source_id=str(s.get("source_id", "")),
            source_status=str(s.get("source_status", "current")),
            content_hash=str(s.get("content_hash", "")),
            as_of=str(s.get("as_of", "")),
        ))
    return CI.compute_identity(
        claim_text=rec.get("claim_text") or rec.get("claim") or "",
        locator=rec.get("locator", ""),
        sources=srcs,
        retrieval_timestamp=rec.get("retrieval_timestamp", ""),
        materiality_class=rec.get("materiality_class", "numeric"),
        surface=rec.get("surface", ""), fmt=rec.get("fmt", ""),
        waiver=rec.get("waiver"),
        supersession_state=rec.get("supersession_state", "active"),
        unit=rec.get("unit"), period=rec.get("period"),
    )


def _verifier_claim(rec: dict) -> dict:
    """Project a claim record into the shape the SV-01 `Verifier` reads (`claim`, `materiality`,
    `severity`, `verifiability`, `supporting`/`conflict`/`review_status`). Additive — never drops
    a field the record already carries."""
    c = dict(rec)
    c.setdefault("claim", rec.get("claim_text", ""))
    if "materiality" not in c:
        mc = rec.get("materiality_class")
        c["materiality"] = [mc] if mc else []
    c.setdefault("severity", rec.get("severity", "should-fix"))
    c.setdefault("supporting", rec.get("supporting", []))
    c.setdefault("conflict", rec.get("conflict"))
    c.setdefault("review_status", rec.get("review_status", "needs-review"))
    return c


def run_reverification_stage(
    manifest: dict, *, change_set: dict, current_claims: List[dict],
    prior_run_ref: str, registry,
    travel_class: Optional[str] = None, prior_travel_class: Optional[str] = None,
    now: str = "", freshness_days: Optional[int] = None,
    prior_waiver_scope: Optional[Dict[str, str]] = None,
    packet: Optional["V.SourcePacket"] = None,
    caps: Optional[dict] = None, banned_tokens: Sequence[str] = (),
    primary_domains: Sequence[str] = (), egress_waiver: Optional[dict] = None,
    search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None,
    internal_lane_fns: Optional[Dict[str, Callable]] = None,
    internal_read_fn: Optional[Callable] = None,
    opt_in_external: bool = False, opt_in_internal: bool = False,
    per_call_timeout_s: Optional[float] = None, per_run_timeout_s: Optional[float] = None,
    retrieval_date: str = "", provider_label: Optional[str] = None,
) -> dict:
    """Diff-aware re-verification (up-02). Consume an office-update CHANGE-SET + the fd-02 identity
    and merge a `reverification` block into `manifest`.

    The contract:
      * VERIFY only the changed/new set — every `updated`/`new` change-set entry. (`dropped` and
        `superseded` claims are handled upstream by office-update and never re-verified here.)
      * CARRY a prior §2 verdict forward for an identity-stable, `unchanged` claim — but ONLY after
        the explicit invalidation matrix (`verification.decide_carry`) clears it; the carry record
        keeps a POINTER to the prior run.
      * INVALIDATE + RE-OPEN (or RE-SOURCE) any carried verdict the matrix trips — text / locator /
        source / unit-period / source-age / waiver-owner / travel-class-escalation — and add that
        claim to the verify set.
      * Apply the SV-07 budget to the CHANGED SET ONLY: the Verifier runs over the re-verify set
        (changed/new ∪ invalidated-carry) — never the whole document — so the per-class cap +
        per-call/per-run timeouts account for just the work the refresh actually requires.

    Writes nothing to disk; the harness persists the returned manifest. Stdlib only."""
    tc = (travel_class
          or (manifest.get("travel_class", {}) or {}).get("effective")
          or (manifest.get("travel_class", {}) or {}).get("value")
          or "internal")
    travel_escalated = (prior_travel_class is not None
                        and _travel_rank(tc) > _travel_rank(prior_travel_class))
    prior_waiver_scope = prior_waiver_scope or {}
    policy = verification_policy(tc, opt_in_external=opt_in_external, opt_in_internal=opt_in_internal)

    # index the current claim records by fd-02 claim_id (+ identity + verifier projection)
    by_id: Dict[str, dict] = {}
    ident_by_id: Dict[str, "CI.ClaimIdentity"] = {}
    for rec in current_claims:
        ident = _record_identity(rec)
        cid = rec.get("claim_id") or ident.claim_id
        if cid not in by_id:                      # first writer wins (matches office-update)
            by_id[cid] = rec
            ident_by_id[cid] = ident

    carried: List[dict] = []
    invalidated: List[dict] = []
    reverify_ids: List[str] = []                  # ordered, de-duped
    seen_reverify = set()
    changed_ids: List[str] = []                   # the office-update changed/new set
    decisions: List[dict] = []

    def _queue(cid: str) -> None:
        if cid not in seen_reverify:
            seen_reverify.add(cid)
            reverify_ids.append(cid)

    for ch in change_set.get("changes", []):
        cls = ch.get("classification")
        cid = ch.get("claim_id", "")
        if cls in ("updated", "new"):
            changed_ids.append(cid)
            _queue(cid)
        elif cls == "unchanged":
            prior_verdict = ch.get("carried_verdict")
            if prior_verdict is None:
                continue                          # nothing to carry (no prior verdict on record)
            dec = V.decide_carry(
                ch, prior_verdict=prior_verdict, prior_run_ref=prior_run_ref,
                curr_identity=ident_by_id.get(cid), prior_waiver_scope_hash=prior_waiver_scope.get(cid),
                now=now, freshness_days=freshness_days, travel_escalated=travel_escalated)
            decisions.append(dec.to_dict())
            if dec.action == V.ACTION_CARRY:
                carried.append({"claim_id": cid, "verdict": dec.carried_verdict,
                                "prior_run": dec.prior_run, "triggered": dec.triggered})
            else:
                invalidated.append({"claim_id": cid, "action": dec.action,
                                    "triggered": dec.triggered,
                                    "invalidated_verdict": prior_verdict})
                _queue(cid)
        # dropped / superseded: recorded by office-update; not re-verified here.

    # build the changed claim set for the Verifier (SV-07 budget applies to THIS set only)
    reverify_claims = [_verifier_claim(by_id[cid]) for cid in reverify_ids if cid in by_id]
    missing = [cid for cid in reverify_ids if cid not in by_id]

    if reverify_claims and (policy["external"] or policy["internal"]):
        verifier = V.Verifier(
            claims=reverify_claims, packet=packet or V.SourcePacket(), registry=registry,
            travel_class=tc, caps=caps, banned_tokens=banned_tokens, primary_domains=primary_domains,
            egress_waiver=egress_waiver, search_fn=search_fn, fetch_fn=fetch_fn,
            retrieval_date=retrieval_date, provider_label=provider_label,
            run_external=policy["external"], run_internal=policy["internal"],
            internal_lane_fns=internal_lane_fns, internal_read_fn=internal_read_fn,
            per_call_timeout_s=per_call_timeout_s, per_run_timeout_s=per_run_timeout_s)
        block = verifier.run()
        verif = block["verification"]
        egress = block["egress"]
        bindings = block["adapter_bindings"]
    else:
        # nothing changed (or verification not enabled) — a stated, recorded no-op, never a failure.
        verif = {"_doc": "up-02 re-verification: no changed/new claims to verify "
                         "(or verification not enabled for this travel class).",
                 "travel_class": tc, "passes": {"external": policy["external"], "internal": policy["internal"]},
                 "results": [], "not_checked_budget": [], "must_own": [], "promoted_to_verified": [],
                 "claims_checked": 0, "budget_used": 0}
        egress = []
        bindings = registry.manifest_record()["adapter_bindings"] if registry else {}

    manifest["reverification"] = {
        "_doc": "UP-02 diff-aware re-verification (codex v3 finding #2): verify only the changed/new "
                "set, carry identity-stable unchanged verdicts forward THROUGH the explicit "
                "invalidation matrix (with a prior-run pointer), re-open/re-source on any trigger; "
                "the SV-07 budget covers the changed set only.",
        "prior_run": prior_run_ref,
        "travel_class": tc, "prior_travel_class": prior_travel_class,
        "travel_class_escalated": travel_escalated,
        "policy": policy,
        "matrix": dict(V.INVALIDATION_MATRIX),
        "counts": {
            "prior_total": change_set.get("prior_total"),
            "current_total": change_set.get("current_total"),
            "changed": len(changed_ids),
            "reverified_from_change": len(changed_ids),
            "reverified_from_invalidation": len(invalidated),
            "reverified_total": len(reverify_ids),
            "carried": len(carried),
        },
        "changed_claim_ids": changed_ids,
        "carried": carried,
        "invalidated": invalidated,
        "decisions": decisions,
        "missing_records": missing,
        "verification": verif,
        "_stage": STAGE_REFRESH,
    }
    manifest.setdefault("egress", [])
    if egress:
        manifest["egress"] = egress
    manifest["adapter_bindings"] = bindings
    return manifest


if __name__ == "__main__":  # tiny self-check: the policy table + idempotency
    print("policy internal:", verification_policy("internal"))
    print("policy travels :", verification_policy("travels"))
    print("policy board   :", verification_policy("board-regulatory"))
