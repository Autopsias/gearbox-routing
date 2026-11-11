#!/usr/bin/env python3
"""Canonical claim identity for the Office Trust Layer (v3 / FD-02, conventions.md §7.5).

A recurring refresh must distinguish *"this is the same claim, moved"* from *"this claim's
meaning changed"*. Without a stable identity, the refresh engine becomes a trust **copier**
that reuses `verified` on a claim whose source / period / unit quietly shifted.

This module is **pure standard library** (hashlib + unicodedata + re + datetime) so it runs
anywhere the harness runs, and is consumed by `office-update` (S02/S03) and verification.
It computes — and only ever computes — identity; it makes no verdict and no network call.

Identity fields (conventions.md §7.5):
    claim_id, normalized_text_hash, locator_hash, source_fingerprint,
    retrieval_timestamp, materiality_class, waiver_scope_hash, supersession_state

Key properties (proven by fixtures/fixture_claim_identity.py):
  * A claim that MOVES (same normalized text + same source fingerprint, new locator) keeps
    its `claim_id` — identity STABLE — and MAY carry a prior verdict.
  * A claim whose `source_fingerprint` (or normalized text) CHANGES gets a NEW `claim_id`
    and CANNOT inherit a prior verdict (it re-enters review at needs-review).
  * The §7.5 invalidation classes that MUST invalidate a carried verdict are computed by
    `classify_invalidation()` and surfaced by `diff()` (they feed the up-02 invalidation matrix).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Any, List, Optional, Sequence

# Materiality tiers, lowest → highest (conventions.md §2.3; "the highest governs").
MATERIALITY_ORDER = (
    "numeric", "date", "named-entity", "regulatory", "market", "assumption-as-fact", "headline",
)
SUPERSESSION_STATES = ("active", "superseded", "tombstoned")

# The §7.5 identity-change classes that MUST invalidate a carried verdict.
INVALIDATION_CLASSES = (
    "text-changed",
    "source-changed",
    "unit-or-period-changed",
    "source-stale",
    "waiver-scope-changed",
    "superseded-or-tombstoned",
    "materiality-escalated",
)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# --- text normalization -------------------------------------------------------
_WS = re.compile(r"\s+")
# A number token: optional sign, digits with optional thousands separators, optional decimal.
_NUM = re.compile(r"(?<![A-Za-z0-9])([+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?)(?![A-Za-z0-9])")


def _canon_number(tok: str) -> str:
    """Canonicalise a number's *formatting* WITHOUT changing its magnitude or unit:
    drop thousands separators, strip trailing zeros in the fraction, drop a bare trailing dot.
    '1,200.50' -> '1200.5'; '12.0' -> '12'; '3.140' -> '3.14'. A non-number is returned as-is.
    """
    t = tok.replace(",", "")
    if "." in t:
        intp, frac = t.split(".", 1)
        frac = frac.rstrip("0")
        t = intp if frac == "" else f"{intp}.{frac}"
    return t


def normalize_text(text: str) -> str:
    """Normalize claim text before hashing (conventions.md §7.5):
    Unicode-NFKC, lowercase, collapse whitespace, strip surrounding punctuation,
    normalize number formatting without changing magnitudes or units.
    """
    if text is None:
        text = ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.lower()
    s = _NUM.sub(lambda m: _canon_number(m.group(1)), s)
    s = _WS.sub(" ", s).strip()
    s = s.strip(" \t\r\n.,;:!?—-–\"'()[]")
    return s


def normalized_text_hash(text: str) -> str:
    return _sha256(normalize_text(text))


# --- locator hash -------------------------------------------------------------
def canonical_locator(locator: str, surface: str = "", fmt: str = "") -> str:
    """A locator's grammar follows its source's surface+format (conventions.md §1.6/§7.4).
    The canonical string folds in surface+format so an identical string in two grammars does
    not collide. Whitespace is collapsed; case is preserved (locators can be case-significant)."""
    loc = _WS.sub(" ", str(locator or "")).strip()
    tag = f"{(surface or '').strip().lower()}|{(fmt or '').strip().lower()}|"
    return tag + loc


def locator_hash(locator: str, surface: str = "", fmt: str = "") -> str:
    return _sha256(canonical_locator(locator, surface, fmt))


# --- source fingerprint -------------------------------------------------------
@dataclass(frozen=True)
class SourceRef:
    """One backing source for a claim. `content_hash` is the source's own content/excerpt
    hash (NOT recomputed here — supplied by the source packet); `as_of` is its source date."""
    source_id: str
    source_status: str = "current"     # conventions §1.1: current|superseded|estimate|raw
    content_hash: str = ""
    as_of: str = ""


def source_fingerprint(sources: Sequence[SourceRef]) -> str:
    """sha256 over the SORTED backing-source tuples (source_id, status, content_hash, as_of).
    Identity of *what backs the claim*. Order-independent (sorted), so reordering sources does
    not change identity; a changed/added/removed/superseded source does."""
    tuples = sorted(
        (s.source_id, s.source_status, s.content_hash, s.as_of) for s in (sources or [])
    )
    return _sha256("|".join("/".join(t) for t in tuples)) if tuples else _sha256("")


def mint_claim_id(ntext_hash: str, src_fingerprint: str) -> str:
    """claim_id = C-<first 12 hex of sha256(normalized_text_hash + ':' + source_fingerprint)>.
    Stable for the life of the claim: it is a pure function of text+source, so a MOVE (new
    locator) leaves it unchanged, and a text/source change yields a NEW id automatically."""
    return "C-" + _sha256(f"{ntext_hash}:{src_fingerprint}")[:12]


# --- the identity -------------------------------------------------------------
@dataclass
class ClaimIdentity:
    claim_id: str
    normalized_text_hash: str
    locator_hash: str
    source_fingerprint: str
    retrieval_timestamp: str
    materiality_class: str
    waiver_scope_hash: Optional[str]
    supersession_state: str
    # carried, optional context used by the invalidation classes (not part of the id):
    unit: Optional[str] = None
    period: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_identity(
    *,
    claim_text: str,
    locator: str,
    sources: Sequence[SourceRef],
    retrieval_timestamp: str,
    materiality_class: str,
    surface: str = "",
    fmt: str = "",
    waiver: Optional[dict] = None,
    supersession_state: str = "active",
    unit: Optional[str] = None,
    period: Optional[str] = None,
) -> ClaimIdentity:
    """Compute the canonical identity for a claim (conventions.md §7.5)."""
    if materiality_class not in MATERIALITY_ORDER:
        raise ValueError(f"unknown materiality_class {materiality_class!r}; expected one of {MATERIALITY_ORDER}")
    if supersession_state not in SUPERSESSION_STATES:
        raise ValueError(f"unknown supersession_state {supersession_state!r}; expected {SUPERSESSION_STATES}")
    nth = normalized_text_hash(claim_text)
    sfp = source_fingerprint(sources)
    return ClaimIdentity(
        claim_id=mint_claim_id(nth, sfp),
        normalized_text_hash=nth,
        locator_hash=locator_hash(locator, surface, fmt),
        source_fingerprint=sfp,
        retrieval_timestamp=retrieval_timestamp,
        materiality_class=materiality_class,
        waiver_scope_hash=_waiver_scope_hash(waiver),
        supersession_state=supersession_state,
        unit=unit,
        period=period,
    )


def _waiver_scope_hash(waiver: Optional[dict]) -> Optional[str]:
    """Hash the scope-defining fields of a §4 waiver (owner + decision + scope + travel class).
    None when the claim is unwaived."""
    if not waiver:
        return None
    parts = [
        str(waiver.get("owner", "")),
        str(waiver.get("decision", "")),
        str(waiver.get("scope", "")),
        str(waiver.get("travel_class", "")),
    ]
    return _sha256("|".join(parts))


# --- staleness + invalidation -------------------------------------------------
def _parse_ts(ts: str) -> Optional[_dt.datetime]:
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = _dt.datetime.strptime(ts.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def is_stale(identity: ClaimIdentity, *, now: Optional[str] = None, freshness_days: Optional[int] = None) -> bool:
    """A claim is source-stale when its retrieval_timestamp is older than the freshness window.
    Returns False when no window is supplied (staleness is policy-driven, not assumed)."""
    if freshness_days is None or now is None:
        return False
    r = _parse_ts(identity.retrieval_timestamp)
    n = _parse_ts(now)
    if r is None or n is None:
        return False
    return (n - r).days > freshness_days


def _materiality_rank(m: str) -> int:
    return MATERIALITY_ORDER.index(m) if m in MATERIALITY_ORDER else -1


def classify_invalidation(
    prev: ClaimIdentity,
    curr: ClaimIdentity,
    *,
    now: Optional[str] = None,
    freshness_days: Optional[int] = None,
) -> List[str]:
    """Return the §7.5 identity-change classes present between a prior identity `prev` and the
    current identity `curr`. ANY non-empty result means a carried verdict MUST be invalidated.
    Note: a locator move is deliberately NOT an invalidation class — moving alone is safe."""
    classes: List[str] = []
    if prev.normalized_text_hash != curr.normalized_text_hash:
        classes.append("text-changed")
    if prev.source_fingerprint != curr.source_fingerprint:
        classes.append("source-changed")
    # unit / period: only flagged when both sides carry the field and they differ.
    if (prev.unit is not None and curr.unit is not None and prev.unit != curr.unit) or \
       (prev.period is not None and curr.period is not None and prev.period != curr.period):
        classes.append("unit-or-period-changed")
    if is_stale(curr, now=now, freshness_days=freshness_days):
        classes.append("source-stale")
    if prev.waiver_scope_hash != curr.waiver_scope_hash:
        classes.append("waiver-scope-changed")
    if curr.supersession_state != "active":
        classes.append("superseded-or-tombstoned")
    if _materiality_rank(curr.materiality_class) > _materiality_rank(prev.materiality_class):
        classes.append("materiality-escalated")
    return classes


def diff(
    prev: ClaimIdentity,
    curr: ClaimIdentity,
    *,
    now: Optional[str] = None,
    freshness_days: Optional[int] = None,
) -> dict:
    """The moved-claim diff (conventions.md §7.5).

    relation:
      * 'unchanged'        — text + source + locator all identical.
      * 'moved'            — same normalized text + same source fingerprint, NEW locator
                             ⇒ identity STABLE (same claim_id).
      * 'changed-identity' — normalized text or source fingerprint changed ⇒ NEW claim_id.

    `identity_stable`     — the claim_id is carried (relation != 'changed-identity').
    `can_carry_verdict`   — True ONLY for a stable identity with NO invalidation class
                            (a pure move, or a truly unchanged claim). A new identity, or any
                            §7.5 invalidation class (stale / superseded / waiver-scope / materiality
                            escalation / unit-period), forces re-review at needs-review.
    """
    invalidation = classify_invalidation(prev, curr, now=now, freshness_days=freshness_days)
    text_or_source_changed = (
        prev.normalized_text_hash != curr.normalized_text_hash
        or prev.source_fingerprint != curr.source_fingerprint
    )
    if text_or_source_changed:
        relation = "changed-identity"
    elif prev.locator_hash != curr.locator_hash:
        relation = "moved"
    else:
        relation = "unchanged"

    identity_stable = relation != "changed-identity"
    # A changed identity literally cannot carry a verdict; for a stable identity, only a clean
    # (no-invalidation) move/unchanged may carry it.
    can_carry_verdict = identity_stable and not invalidation
    return {
        "relation": relation,
        "prev_claim_id": prev.claim_id,
        "curr_claim_id": curr.claim_id,
        "identity_stable": identity_stable,
        "can_carry_verdict": can_carry_verdict,
        "invalidation": invalidation,
    }


# --- self-test ---------------------------------------------------------------
def _selftest() -> int:
    s = [SourceRef("S1", "current", content_hash="abc", as_of="2026-03-31")]
    base: dict[str, Any] = dict(sources=s, retrieval_timestamp="2026-05-31T00:00:00Z", materiality_class="numeric")
    a = compute_identity(claim_text="Revenue grew 12% YoY in FY25", locator="Model.xlsx#Outputs!B14",
                         surface="excel", **base)
    # a MOVE: same text+source, new locator
    b = compute_identity(claim_text="Revenue grew 12.0% YoY in FY25  ", locator="Model.xlsx#Outputs!B27",
                         surface="excel", **base)
    d = diff(a, b)
    assert d["relation"] == "moved" and d["identity_stable"] and d["can_carry_verdict"], d
    assert a.claim_id == b.claim_id, (a.claim_id, b.claim_id)
    # a SOURCE change: new identity, cannot carry
    s2 = [SourceRef("S1", "superseded", content_hash="xyz", as_of="2026-03-31")]
    c = compute_identity(claim_text="Revenue grew 12% YoY in FY25", locator="Model.xlsx#Outputs!B14",
                         surface="excel", sources=s2, retrieval_timestamp="2026-05-31T00:00:00Z",
                         materiality_class="numeric")
    dc = diff(a, c)
    assert dc["relation"] == "changed-identity" and not dc["identity_stable"], dc
    assert not dc["can_carry_verdict"] and "source-changed" in dc["invalidation"], dc
    assert a.claim_id != c.claim_id
    print("claim_identity selftest: PASS")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else _selftest())
