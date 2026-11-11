#!/usr/bin/env python3
"""office-update CORE — surface-agnostic recurring-refresh engine (v3 / UP-01, S02).

The eighth primitive the article names and v2 deferred: take **last period's run**
plus **this period's claim-set** and produce a correct *change-set* — every claim
classified `unchanged | updated | new | dropped | superseded`, dropped claims
**tombstoned** (never silently deleted), stable claim IDs preserved across a pure
re-order, a superseded source **linked (not blended)** to its replacement, and ONLY
valid human-gate waivers + prior verdicts carried forward on identity-stable
*unchanged* claims.

Built on the S01 foundation, wrap-don't-rebuild:
  * **fd-02** `references/scripts/claim_identity.py` — the canonical claim identity
    and the moved-claim diff. This module computes NO identity of its own; it asks
    claim_identity for every `claim_id` and every §7.5 invalidation class.
  * **fd-03** `references/scripts/compat_harness.py` — the backward-compat reader (ships
    with the plugin). The prior run is
    loaded **through `compat_harness.v3_view`** so a v1/v2/v3 prior run all read the
    same way, optional fields default safely, and the prior run is consumed READ-ONLY
    (proven by a whole-tree byte-hash before/after).

This is the CORE only — it consumes an *already-extracted, normalized* claim-set
(the boundary). Pulling claims out of a rendered .xlsx/.pptx/.docx/.pdf is the job of
the **surface adapters (S03 / UP-03)**; the CORE is deliberately surface-agnostic and
pure standard library so it runs anywhere the harness runs.

Normalized claim record (one dict per claim; the adapter's output, the CORE's input):

    {
      "claim_text": "Revenue grew 12% YoY in FY25",   # required
      "locator":    "Model.xlsx#Outputs!B14",          # required (surface grammar)
      "surface":    "excel",  "fmt": "xlsx",            # locator grammar tag (optional)
      "sources":    [{"source_id":"S1","source_status":"current",
                      "content_hash":"…","as_of":"2026-03-31"}],
      "retrieval_timestamp": "2026-05-31T00:00:00Z",
      "materiality_class":   "numeric",                # §2.3 tier
      "unit": null, "period": null,                    # silent-drift context (optional)
      "waiver":  {…} | null,                           # §4 / §7.6 waiver (optional)
      "verdict": "verified" | "ready" | "ready-with-limitations"
                  | "needs-review" | null,             # the prior trust verdict
      "slot":    "Q3-revenue",                         # stable logical key (optional)
      "supersedes": ["C-…"]                            # explicit supersession (optional)
    }

A claim-set file is `{ "schema_version": "3", "run_id": …, "claims": [ … ] }` and is
persisted as `claim-set.json` so the *next* refresh has a native prior (tombstones and
superseded claims are kept in it — never lost).

CLI:
    python office_update.py --prev .trust/<prev>/ --current claim-set.json \
        [--out .trust] [--run-id <id>] [--now ISO] [--freshness-days N] \
        [--travel-class internal|travels|board-regulatory]
    python office_update.py --selftest
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

# --- wire in the S01 foundation modules (pure stdlib, no third-party) ---------
_HERE = os.path.dirname(os.path.abspath(__file__))            # skills/office-update/scripts
_ROOT = os.path.dirname(_HERE)  # skills/office-update
sys.path.insert(0, os.path.join(os.path.dirname(_ROOT), "trust-review", "references", "scripts"))
# compat_harness (the fd-03 reader) ships under references/scripts/ so the
# DISTRIBUTED skill is self-contained — eval/ is the dev harness and is NOT packaged.

import claim_identity as CI       # noqa: E402  (fd-02)
import compat_harness as CH       # noqa: E402  (fd-03 reader — references/scripts)
import manifest_gate as MG        # noqa: E402  (F-05 gate, stdlib)

SCHEMA_VERSION = "3"
CLASSES = ("unchanged", "updated", "new", "dropped", "superseded")


# =============================================================================
# identity helpers
# =============================================================================
def _source_refs(rec: dict) -> List[CI.SourceRef]:
    out = []
    for s in (rec.get("sources") or []):
        out.append(CI.SourceRef(
            source_id=str(s.get("source_id", "")),
            source_status=str(s.get("source_status", "current")),
            content_hash=str(s.get("content_hash", "")),
            as_of=str(s.get("as_of", "")),
        ))
    return out


def identity_of(rec: dict) -> CI.ClaimIdentity:
    """Compute the §7.5 canonical identity for a normalized claim record via fd-02.

    A `supersession_state` carried on the record (active|superseded|tombstoned) is
    honoured so a prior claim-set's tombstones/superseded claims keep their state.
    """
    return CI.compute_identity(
        claim_text=rec.get("claim_text", ""),
        locator=rec.get("locator", ""),
        sources=_source_refs(rec),
        retrieval_timestamp=str(rec.get("retrieval_timestamp", "")),
        materiality_class=str(rec.get("materiality_class", "numeric")),
        surface=str(rec.get("surface", "")),
        fmt=str(rec.get("fmt", "")),
        waiver=rec.get("waiver"),
        supersession_state=str(rec.get("supersession_state", "active")),
        unit=rec.get("unit"),
        period=rec.get("period"),
    )


def _index_by_id(records: Sequence[dict]) -> "Tuple[Dict[str, dict], Dict[str, CI.ClaimIdentity]]":
    """Index records by claim_id (first writer wins on a collision) + their identities.

    A record may carry an explicit `claim_id`; if so we trust it for the lookup key but
    STILL recompute the identity from text+source so a tampered id cannot smuggle a
    verdict across a real change.
    """
    by_rec: Dict[str, dict] = {}
    by_id: Dict[str, CI.ClaimIdentity] = {}
    for rec in records:
        ident = identity_of(rec)
        cid = ident.claim_id
        if cid not in by_rec:
            by_rec[cid] = rec
            by_id[cid] = ident
    return by_rec, by_id


# =============================================================================
# waiver validity (§4 / §7.6) — only VALID waivers carry forward
# =============================================================================
def _parse_date(s: str) -> Optional[_dt.date]:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def waiver_valid(waiver: Optional[dict], *, now: Optional[str] = None,
                 travel_class: str = "internal") -> bool:
    """A §4 waiver is carry-forward-valid when it is well-formed, unexpired, and (for
    Board/Regulatory) carries a named-owner signoff.

      * well-formed  — has an `owner` and a `decision`.
      * unexpired    — no `expiry`, or `expiry` >= `now` (date compare; a staleness-policy
                       string that is not a date is treated as non-expiring here — the
                       freshness window is handled separately by source-stale).
      * signed off   — Board/Regulatory require `signoff == 'approved'` (named-owner
                       signoff, §4.3); Internal/Travels do not.
    """
    if not waiver:
        return False
    if not (str(waiver.get("owner", "")).strip() and str(waiver.get("decision", "")).strip()):
        return False
    exp = _parse_date(waiver.get("expiry", ""))
    if exp is not None:
        ref = _parse_date(now) or _dt.date.today()
        if exp < ref:
            return False
    if str(travel_class).lower() in ("board", "regulatory", "board-regulatory"):
        if str(waiver.get("signoff", "")).strip().lower() != "approved":
            return False
    return True


# =============================================================================
# supersession pairing (a superseded source is LINKED, never blended)
# =============================================================================
def _detect_supersessions(
    prev_by_rec: Dict[str, dict],
    curr_by_rec: Dict[str, dict],
) -> Dict[str, str]:
    """Pair a prior claim_id that LEFT the set with the current claim_id that replaces it.

    A supersession (distinct from a plain drop) is declared two ways:

      1. **Explicit** — a current claim carries `supersedes: ["C-prev", …]`.
      2. **Slot-based** — a prior claim and a current claim share the same stable `slot`
         but mint DIFFERENT claim_ids, AND the prior claim's backing source went
         `superseded` (its `source_status`), i.e. a newer source replaced the old one.

    Returns {prev_claim_id -> superseding_curr_claim_id}. Only prior ids that are NOT
    themselves still present in the current set (by id) are eligible — an id that is in
    both is `unchanged`/`updated`, not superseded.
    """
    pairs: Dict[str, str] = {}

    # 1. explicit supersedes on a current record
    for curr_id, rec in curr_by_rec.items():
        for prev_id in (rec.get("supersedes") or []):
            if prev_id in prev_by_rec and prev_id not in curr_by_rec:
                pairs[prev_id] = curr_id

    # 2. slot-based: prior slot reappears with a new identity off a superseded source
    curr_slots: Dict[str, str] = {}
    for curr_id, rec in curr_by_rec.items():
        slot = rec.get("slot")
        if slot:
            curr_slots.setdefault(str(slot), curr_id)
    for prev_id, rec in prev_by_rec.items():
        if prev_id in curr_by_rec or prev_id in pairs:
            continue
        slot = rec.get("slot")
        if not slot:
            continue
        curr_id = curr_slots.get(str(slot))
        if not curr_id or curr_id == prev_id:
            continue
        prior_superseded = any(
            str(s.get("source_status", "")).lower() == "superseded"
            for s in (rec.get("sources") or [])
        )
        curr_declares = bool(curr_by_rec[curr_id].get("supersedes"))
        if prior_superseded or curr_declares:
            pairs[prev_id] = curr_id
    return pairs


def _value_excerpt(rec: dict) -> str:
    """A short human-facing value for the change-set (the claim text is the value here)."""
    return str(rec.get("value", rec.get("claim_text", "")))[:240]


def _source_summary(rec: dict) -> List[dict]:
    return [
        {"source_id": s.get("source_id"), "source_status": s.get("source_status", "current"),
         "as_of": s.get("as_of", "")}
        for s in (rec.get("sources") or [])
    ]


# =============================================================================
# the change-set — the pure, testable heart (no IO)
# =============================================================================
def classify_changes(
    prior_records: Sequence[dict],
    current_records: Sequence[dict],
    *,
    now: Optional[str] = None,
    freshness_days: Optional[int] = None,
    travel_class: str = "internal",
) -> dict:
    """Diff a prior claim-set against the current one by fd-02 identity and classify
    every claim. Pure: no file IO. Returns the change-set described in the module docstring.
    """
    prev_by_rec, prev_by_id = _index_by_id(prior_records)
    curr_by_rec, curr_by_id = _index_by_id(current_records)

    superseded_map = _detect_supersessions(prev_by_rec, curr_by_rec)   # prev_id -> curr_id
    reverse_super = {}
    for p, c in superseded_map.items():
        reverse_super.setdefault(c, []).append(p)

    changes: List[dict] = []
    tombstones: List[dict] = []
    supersessions: List[dict] = []
    counts = {k: 0 for k in CLASSES}
    carried_verdicts = 0
    carried_waivers = 0

    # ---- claims present in the PRIOR set --------------------------------------
    for cid, prec in prev_by_rec.items():
        pident = prev_by_id[cid]
        if cid in curr_by_rec:
            # identity stable (moved or unchanged) — never a new id.
            cident = curr_by_id[cid]
            crec = curr_by_rec[cid]
            d = CI.diff(pident, cident, now=now, freshness_days=freshness_days)
            prior_waiver = prec.get("waiver")
            wvalid = waiver_valid(prior_waiver, now=now, travel_class=travel_class)
            # waiver carries only when still valid AND its scope did not change.
            scope_changed = "waiver-scope-changed" in d["invalidation"]
            carried_waiver = prior_waiver if (wvalid and not scope_changed) else None

            if d["can_carry_verdict"]:
                # a pure move or a truly-unchanged claim. BUT a prior verdict that rested
                # on a waiver only carries if that waiver is still valid (an expired
                # limitation lapses the trust basis even though identity is stable).
                if prior_waiver and not wvalid:
                    classification = "updated"
                    carried_verdict = None
                    invalidation = sorted(set(d["invalidation"]) | {"waiver-expired"})
                    status = "needs-review"
                else:
                    classification = "unchanged"
                    carried_verdict = prec.get("verdict")
                    invalidation = d["invalidation"]
                    status = carried_verdict or "needs-review"
                    if carried_verdict:
                        carried_verdicts += 1
                    if carried_waiver:
                        carried_waivers += 1
            else:
                # stable identity but a §7.5 invalidation class — re-enter review.
                classification = "updated"
                carried_verdict = None
                invalidation = d["invalidation"]
                status = "needs-review"
                if carried_waiver:
                    carried_waivers += 1

            counts[classification] += 1
            changes.append({
                "claim_id": cid,
                "classification": classification,
                "relation": d["relation"],            # unchanged | moved | changed-identity
                "moved": d["relation"] == "moved",
                "identity_stable": d["identity_stable"],
                "can_carry_verdict": d["can_carry_verdict"],
                "invalidation": invalidation,
                "carried_verdict": carried_verdict,
                "carried_waiver": carried_waiver,
                "status": status,
                "prev_locator": prec.get("locator"),
                "locator": crec.get("locator"),
                "claim_text": crec.get("claim_text"),
            })
        elif cid in superseded_map:
            # a newer source replaced this claim — LINK, do not blend.
            curr_id = superseded_map[cid]
            crec = curr_by_rec[curr_id]
            counts["superseded"] += 1
            supersessions.append({
                "superseded_claim_id": cid,
                "superseded_by": curr_id,
                "prior_value": _value_excerpt(prec),
                "prior_sources": _source_summary(prec),
                "new_value": _value_excerpt(crec),
                "new_sources": _source_summary(crec),
                "blended": False,
                "note": "superseded source linked to its replacement; both values preserved, never blended or averaged",
            })
            changes.append({
                "claim_id": cid,
                "classification": "superseded",
                "relation": "superseded",
                "supersession_state": "superseded",
                "superseded_by": curr_id,
                "carried_verdict": None,            # a superseded claim never carries a verdict
                "carried_waiver": None,
                "status": "superseded",
                "locator": prec.get("locator"),
                "claim_text": prec.get("claim_text"),
            })
        else:
            # gone, and nothing replaces it — TOMBSTONE (never silently deleted).
            counts["dropped"] += 1
            tomb = {
                "claim_id": cid,
                "claim_text": prec.get("claim_text"),
                "last_locator": prec.get("locator"),
                "last_sources": _source_summary(prec),
                "supersession_state": "tombstoned",
                "reason": "present in prior run, absent from current claim-set",
            }
            tombstones.append(tomb)
            changes.append({
                "claim_id": cid,
                "classification": "dropped",
                "relation": "dropped",
                "supersession_state": "tombstoned",
                "carried_verdict": None,
                "carried_waiver": None,
                "status": "tombstoned",
                "locator": prec.get("locator"),
                "claim_text": prec.get("claim_text"),
            })

    # ---- claims ONLY in the CURRENT set (new identities) ----------------------
    for cid, crec in curr_by_rec.items():
        if cid in prev_by_rec:
            continue
        counts["new"] += 1
        supersedes = sorted(reverse_super.get(cid, []))
        changes.append({
            "claim_id": cid,
            "classification": "new",
            "relation": "changed-identity" if supersedes else "new",
            "supersedes": supersedes,                 # the prior ids this one replaces, if any
            "carried_verdict": None,                  # a new identity cannot inherit a verdict
            "carried_waiver": None,
            "status": "needs-review",                 # every new claim re-enters review
            "locator": crec.get("locator"),
            "claim_text": crec.get("claim_text"),
        })

    changes.sort(key=lambda c: (CLASSES.index(c["classification"]) if c["classification"] in CLASSES else 9,
                                c["claim_id"]))
    return {
        "counts": counts,
        "prior_total": len(prev_by_rec),
        "current_total": len(curr_by_rec),
        "carried_forward": {"verdicts": carried_verdicts, "waivers": carried_waivers},
        "changes": changes,
        "tombstones": tombstones,
        "supersessions": supersessions,
    }


# =============================================================================
# the next claim-set (persisted so the NEXT refresh has a native prior)
# =============================================================================
def build_next_claim_set(
    current_records: Sequence[dict],
    change_set: dict,
    run_id: str,
) -> dict:
    """The forward claim-set: every CURRENT claim stamped with its id + carried verdict,
    PLUS the tombstones and superseded claims (kept, never lost). This is what the next
    period's refresh loads as its prior."""
    by_id = {c["claim_id"]: c for c in change_set["changes"]}
    claims = []
    _, curr_by_id = _index_by_id(current_records)
    seen = set()
    for rec in current_records:
        ident = identity_of(rec)
        cid = ident.claim_id
        if cid in seen:
            continue
        seen.add(cid)
        ch = by_id.get(cid, {})
        out = dict(rec)
        out["claim_id"] = cid
        out["supersession_state"] = "active"
        out["verdict"] = ch.get("carried_verdict") if ch.get("classification") == "unchanged" else (
            rec.get("verdict") if ch.get("classification") == "new" and rec.get("verdict") else "needs-review")
        if ch.get("carried_waiver") is not None:
            out["waiver"] = ch.get("carried_waiver")
        if ch.get("supersedes"):
            out["supersedes"] = ch["supersedes"]
        claims.append(out)
    # carry tombstones + superseded forward as inactive records (audit, never deleted)
    for t in change_set["tombstones"]:
        claims.append({
            "claim_id": t["claim_id"], "claim_text": t.get("claim_text"),
            "locator": t.get("last_locator"), "supersession_state": "tombstoned",
            "verdict": None, "sources": [], "materiality_class": "numeric",
        })
    for s in change_set["supersessions"]:
        claims.append({
            "claim_id": s["superseded_claim_id"], "claim_text": s.get("prior_value"),
            "supersession_state": "superseded", "superseded_by": s["superseded_by"],
            "verdict": None, "sources": [], "materiality_class": "numeric",
        })
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id, "claims": claims}


# =============================================================================
# loading a prior run THROUGH the fd-03 reader (read-only)
# =============================================================================
def _sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _tree_fingerprint(root: str) -> Dict[str, str]:
    fp: Dict[str, str] = {}
    if not os.path.isdir(root):
        return fp
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                fp[p] = _sha256_file(p)
            except OSError:
                fp[p] = "unreadable"
    return fp


def _records_from_claim_set(obj: dict) -> List[dict]:
    return list(obj.get("claims") or [])


def _records_from_source_packet(obj: dict) -> List[dict]:
    """Bootstrap: read normalized claims from an upstream office-source-packet (§2 shape).

    Tolerant of both the flat shape (`claim`/`supporting` at top level) and the real
    nested shape (under a `provenance` block, with `metric`/`period`/`value`/`id`).
    Surface-agnostic and minimal — proper per-surface extraction is the S03 adapters."""
    out = []
    for c in (obj.get("claims") or []):
        prov = c.get("provenance") or {}
        text = c.get("claim") or prov.get("claim") or ""
        supporting = c.get("supporting") or prov.get("supporting") or []
        first = supporting[0] if supporting else {}
        out.append({
            "claim_text": text,
            "locator": first.get("locator", ""),
            "sources": [{"source_id": s.get("source_id"), "source_status": "current",
                         "content_hash": "", "as_of": ""} for s in supporting],
            "retrieval_timestamp": obj.get("produced_at", ""),
            "materiality_class": "numeric",
            "unit": None,
            "period": c.get("period"),
            "value": c.get("value"),
            "slot": c.get("id") or c.get("metric"),
            "verdict": "verified" if c.get("review_status") == "verified" else "needs-review",
        })
    return out


def load_prior_run(prev_dir: str) -> "Tuple[List[dict], dict, Optional[str], str]":
    """Load a prior `.trust/<run_id>/` run **through the fd-03 reader**.

    Returns `(records, manifest_view, prev_run_id, prev_schema_version)`.

    The manifest is read through `compat_harness.v3_view` so a v1/v2/v3 prior all read
    the same way and optional fields default safely. Claims are read from a native
    `claim-set.json` first, then a prior `refresh-report.json`'s forward set, then an
    upstream `source-packet.json` (bootstrap). NOTHING in `prev_dir` is written.
    """
    manifest_view: dict = {}
    prev_run_id: Optional[str] = None
    prev_schema = "1"
    man_path = os.path.join(prev_dir, "manifest.json")
    if os.path.exists(man_path):
        with open(man_path, encoding="utf-8") as fh:
            m = json.load(fh)
        manifest_view = CH.v3_view(m)                 # fd-03: read-only, default-safe view
        prev_run_id = m.get("run_id") or manifest_view.get("run_id")
        prev_schema = manifest_view.get("schema_version", CH.detect_schema_version(m))

    records: List[dict] = []
    for name, reader in (("claim-set.json", _records_from_claim_set),
                         ("refresh-report.json", lambda o: _records_from_claim_set(o.get("next_claim_set", {}))),
                         ("source-packet.json", _records_from_source_packet)):
        p = os.path.join(prev_dir, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                obj = json.load(fh)
            records = reader(obj)
            if prev_run_id is None:
                prev_run_id = obj.get("run_id")
            if records:
                break
    return records, manifest_view, prev_run_id, prev_schema


# =============================================================================
# the top-level refresh (loads prior, classifies, emits the report)
# =============================================================================
def refresh(
    prev_dir: str,
    current_claims: Sequence[dict],
    *,
    run_id: Optional[str] = None,
    now: Optional[str] = None,
    freshness_days: Optional[int] = None,
    travel_class: str = "internal",
    out_root: Optional[str] = None,
) -> dict:
    """Run a recurring refresh: load the prior run (read-only, via fd-03), diff by fd-02
    identity, classify, and emit the change-set. If `out_root` is given, write
    `refresh-report.{json,md}`, `claim-set.json`, and a schema_version=3 `manifest.json`
    into `out_root/<run_id>/`."""
    now = now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = run_id or (_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H%M") + "_office-update")

    # READ-ONLY proof: fingerprint the prior run dir before and after.
    before = _tree_fingerprint(prev_dir)
    prior_records, prev_view, prev_run_id, prev_schema = load_prior_run(prev_dir)
    after = _tree_fingerprint(prev_dir)
    read_only_verified = (before == after)

    cs = classify_changes(prior_records, current_claims, now=now,
                          freshness_days=freshness_days, travel_class=travel_class)
    next_cs = build_next_claim_set(current_claims, cs, run_id)

    anomalies = cs["counts"]["dropped"] + cs["counts"]["superseded"] + cs["counts"]["updated"]
    if not prior_records and not os.path.exists(os.path.join(prev_dir, "manifest.json")):
        status = "blocked"
        status_reason = f"prior run not found / unreadable at {prev_dir}"
    elif not read_only_verified:
        status = "failed"
        status_reason = "prior run dir mutated during read (read-only guarantee broken)"
    else:
        status = "ok-with-warnings" if anomalies else "ok"
        status_reason = (
            f"{cs['counts']['unchanged']} unchanged, {cs['counts']['updated']} updated, "
            f"{cs['counts']['new']} new, {cs['counts']['dropped']} dropped (tombstoned), "
            f"{cs['counts']['superseded']} superseded (linked)."
        )

    migration_notes = (
        f"office-update consumed prior run {prev_run_id or '(unknown)'} "
        f"(schema_version {prev_schema}) READ-ONLY via the fd-03 reader (whole-dir byte-hash "
        f"unchanged before/after). Diffed by fd-02 claim identity. Carried forward "
        f"{cs['carried_forward']['verdicts']} prior verdict(s) + {cs['carried_forward']['waivers']} "
        f"valid waiver(s) on identity-stable UNCHANGED claims only; {cs['counts']['updated']} "
        f"identity-stable-but-invalidated claim(s) re-enter needs-review; "
        f"{cs['counts']['dropped']} dropped claim(s) tombstoned (never deleted); "
        f"{cs['counts']['superseded']} superseded source(s) linked, not blended. "
        f"New output stamped schema_version=3."
    )

    report = {
        "run_id": run_id,
        "skill": "office-update",
        "phase": "refresh",
        "schema_version": SCHEMA_VERSION,
        "produced_at": now,
        "prev_run_id": prev_run_id,
        "prev_schema_version": prev_schema,
        "prev_dir": prev_dir,
        "consumes": [prev_dir, "current claim-set"],
        "status": status,
        "status_reason": status_reason,
        "read_only_verified": read_only_verified,
        "travel_class": travel_class,
        "counts": cs["counts"],
        "prior_total": cs["prior_total"],
        "current_total": cs["current_total"],
        "carried_forward": cs["carried_forward"],
        "changes": cs["changes"],
        "tombstones": cs["tombstones"],
        "supersessions": cs["supersessions"],
        "migration_notes": migration_notes,
        "next_claim_set": next_cs,
    }

    if out_root:
        out_dir = os.path.join(out_root, run_id)
        os.makedirs(out_dir, exist_ok=True)
        _write_json(os.path.join(out_dir, "refresh-report.json"), report)
        _write_text(os.path.join(out_dir, "refresh-report.md"), render_md(report))
        _write_json(os.path.join(out_dir, "claim-set.json"), next_cs)
        manifest = _build_manifest(report, out_dir)
        _write_json(os.path.join(out_dir, "manifest.json"), manifest)
        report["out_dir"] = out_dir
    return report


def _build_manifest(report: dict, out_dir: str) -> dict:
    """A schema_version=3 manifest for the refresh run (§7.8): stamps schema_version +
    migration_notes, records sensitivity + routing so the F-05 gate passes (local-only
    diff — no model-run loop)."""
    art = os.path.join(out_dir, "refresh-report.json")
    art_hash = _sha256_file(art) if os.path.exists(art) else "0" * 64
    manifest = {
        "manifest_version": "1.0",
        "schema_version": SCHEMA_VERSION,
        "run_id": report["run_id"],
        "created": report["produced_at"],
        "artifact": {"path": "refresh-report.json", "sha256": art_hash, "kind": "other"},
        "travel_class": {"effective": report["travel_class"], "value": report["travel_class"],
                         "source": "declared"},
        "sensitivity": {"run_max": "internal", "sources": [{"source_id": "PRIOR", "sensitivity": "internal"}]},
        "routing": {"policy": "local-only (claim-identity diff; no model-run loop)",
                    "local_only": True, "two_context": False, "reviewer_independence": "same-model"},
        "degraded_status": [],
        "migration_notes": report["migration_notes"],
    }
    manifest["gate"] = {k: v for k, v in MG.gate(manifest).items()
                        if k in ("sensitivity_recorded", "routing_recorded", "status", "missing", "checked_at")}
    return manifest


# =============================================================================
# the human change-set surface (refresh-report.md)
# =============================================================================
def render_md(report: dict) -> str:
    c = report["counts"]
    L = [
        f"# Refresh change-set — {report['run_id']}",
        "",
        f"**office-update** · schema_version **{report['schema_version']}** · status **{report['status']}** · "
        f"prior run `{report.get('prev_run_id')}` (schema {report.get('prev_schema_version')}) · "
        f"read-only **{report['read_only_verified']}**",
        "",
        f"> {report['status_reason']}",
        "",
        "## Change-set",
        "",
        "| class | count |",
        "|---|---|",
        f"| unchanged | {c['unchanged']} |",
        f"| updated | {c['updated']} |",
        f"| new | {c['new']} |",
        f"| dropped (tombstoned) | {c['dropped']} |",
        f"| superseded (linked) | {c['superseded']} |",
        "",
        f"Prior claims: **{report['prior_total']}** → current claims: **{report['current_total']}**. "
        f"Carried forward: **{report['carried_forward']['verdicts']}** verdict(s), "
        f"**{report['carried_forward']['waivers']}** valid waiver(s) — identity-stable *unchanged* claims only.",
        "",
        "## Per-claim",
        "",
        "| claim_id | class | relation | carried verdict | invalidation | locator |",
        "|---|---|---|---|---|---|",
    ]
    for ch in report["changes"]:
        L.append(
            f"| `{ch['claim_id']}` | {ch['classification']} | {ch.get('relation','')} | "
            f"{ch.get('carried_verdict') or '—'} | {', '.join(ch.get('invalidation') or []) or '—'} | "
            f"`{(ch.get('locator') or '')[:48]}` |"
        )
    if report["tombstones"]:
        L += ["", "## Tombstones (dropped — preserved, never deleted)", ""]
        for t in report["tombstones"]:
            L.append(f"- `{t['claim_id']}` — {t.get('claim_text','')} "
                     f"(last seen `{t.get('last_locator','')}`) → **tombstoned**")
    if report["supersessions"]:
        L += ["", "## Supersession links (linked, NOT blended)", ""]
        for s in report["supersessions"]:
            L.append(
                f"- `{s['superseded_claim_id']}` → superseded by `{s['superseded_by']}`. "
                f"Prior value: _{s['prior_value']}_ · New value: _{s['new_value']}_ "
                f"— both preserved, never averaged."
            )
    L += ["", "## Migration notes", "", report["migration_notes"], ""]
    return "\n".join(L)


# =============================================================================
# IO helpers
# =============================================================================
def _write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


# =============================================================================
# self-test (a quick, dependency-free smoke of the core invariants)
# =============================================================================
def _selftest() -> int:
    base_src = [{"source_id": "S1", "source_status": "current", "content_hash": "h1", "as_of": "2026-03-31"}]
    prior = [
        {"claim_text": "Revenue grew 12% YoY", "locator": "M.xlsx#Out!B14", "surface": "excel",
         "sources": base_src, "retrieval_timestamp": "2026-02-28T00:00:00Z",
         "materiality_class": "numeric", "verdict": "verified", "slot": "rev"},
        {"claim_text": "Headcount 412", "locator": "M.xlsx#Out!B20", "surface": "excel",
         "sources": base_src, "retrieval_timestamp": "2026-02-28T00:00:00Z",
         "materiality_class": "numeric", "verdict": "verified"},
    ]
    # current: same two claims but REORDERED + the first one MOVED (new locator); identical text/source
    current = [
        {"claim_text": "Headcount 412", "locator": "M.xlsx#Out!B20", "surface": "excel",
         "sources": base_src, "retrieval_timestamp": "2026-02-28T00:00:00Z", "materiality_class": "numeric"},
        {"claim_text": "Revenue grew 12.0% YoY", "locator": "M.xlsx#Out!B30", "surface": "excel",
         "sources": base_src, "retrieval_timestamp": "2026-02-28T00:00:00Z", "materiality_class": "numeric",
         "slot": "rev"},
    ]
    cs = classify_changes(prior, current)
    assert cs["counts"]["unchanged"] == 2, cs["counts"]
    assert cs["counts"]["new"] == 0 and cs["counts"]["dropped"] == 0, cs["counts"]
    # stable ids preserved across reorder + move
    pids = {identity_of(p).claim_id for p in prior}
    cids = {identity_of(c).claim_id for c in current}
    assert pids == cids, (pids, cids)
    assert cs["carried_forward"]["verdicts"] == 2, cs["carried_forward"]
    print("office_update selftest: PASS")
    return 0


# =============================================================================
# CLI
# =============================================================================
def _parse_args(argv: List[str]) -> dict:
    args: dict = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--selftest":
            args["selftest"] = True
            i += 1
        elif a.startswith("--"):
            key = a[2:]
            val = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else True
            args[key] = val
            i += 2 if val is not True else 1
        else:
            i += 1
    return args


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    if args.get("selftest"):
        return _selftest()
    prev = args.get("prev")
    cur = args.get("current")
    if not prev or not cur:
        print("usage: office_update.py --prev .trust/<prev>/ --current claim-set.json "
              "[--out .trust] [--run-id ID] [--now ISO] [--freshness-days N] "
              "[--travel-class internal|travels|board-regulatory]\n"
              "   or: office_update.py --selftest", file=sys.stderr)
        return 2
    with open(cur, encoding="utf-8") as fh:
        obj = json.load(fh)
    current_claims = obj.get("claims") if isinstance(obj, dict) else obj
    fd = args.get("freshness-days")
    report = refresh(
        prev, current_claims or [],
        run_id=args.get("run-id") if isinstance(args.get("run-id"), str) else None,
        now=args.get("now") if isinstance(args.get("now"), str) else None,
        freshness_days=int(fd) if isinstance(fd, str) else None,
        travel_class=args.get("travel-class") if isinstance(args.get("travel-class"), str) else "internal",
        out_root=args.get("out") if isinstance(args.get("out"), str) else None,
    )
    print(json.dumps({k: report[k] for k in
                      ("run_id", "status", "status_reason", "counts", "carried_forward",
                       "read_only_verified", "prev_run_id", "schema_version")}, indent=2, ensure_ascii=False))
    if report.get("out_dir"):
        print(f"\nwrote: {report['out_dir']}/refresh-report.{{json,md}} + claim-set.json + manifest.json")
    print(f"OFFICE-UPDATE: {report['status'].upper()}  "
          f"(unchanged={report['counts']['unchanged']} updated={report['counts']['updated']} "
          f"new={report['counts']['new']} dropped={report['counts']['dropped']} "
          f"superseded={report['counts']['superseded']}; read-only={report['read_only_verified']})")
    return 0 if report["status"] in ("ok", "ok-with-warnings") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
