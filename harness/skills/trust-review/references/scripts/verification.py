#!/usr/bin/env python3
"""
verification.py — SV-01 (v2 Source Verification I)
==================================================
The **verification module + schema + result→source pipeline** for the Office Trust
Layer. v1 checks a claim only against the sources uploaded with the file; v2 may
additionally check a claim against sources **outside the packet** (the open web via
SV-02's Exa adapter; the user's own corpus via S06) and **folds the result back into
the same §2 provenance record** — a verification result is just another source.

This module owns the parts that must be *generic and deterministic* (review findings
#2/#3/#7):

  1. **The result schema** — `VerificationSource` + `VerificationResult` — the single
     adapter return shape for EVERY backend the registry resolves
     (`{verdict, sources[], note}`, `conventions.md` §2.2). `provider` is metadata only.
  2. **The adapter protocol** — `VerificationAdapter.verify(claim, sensitivity, context)
     -> VerificationResult`. Backends are resolved **via the F2-04 adapter registry**
     (`resolve("external_search")` / `resolve("external_fetch")`), **never by a
     hard-coded tool name**. An unresolved external op yields a `NullExternalAdapter`
     that returns `not-found` + an "external verification unavailable" note — **never a
     fabricated citation**.
  3. **The §2.4 source-quality policy** — `authority_of()` + `grade_corroboration()`:
     authority order (official/primary/secondary/low), `retrieval_date` required,
     **reject low-authority for a `must-fix`**, **high-confidence = ≥2 independent OR 1
     primary/official**, stale/contradictory preserved as conflicts.
  4. **The §2.3 materiality + per-travel-class budget** — which claims are eligible
     (highest materiality tier governs), bounded by a per-travel-class cap, near-
     identical claims de-duped, and every eligible-but-skipped claim recorded
     `not-checked-budget` — never silently dropped.
  5. **The pipeline** — `Verifier`: for each eligible claim it applies the egress gate
     (SV-03), calls the routed adapter, **appends found sources as new `S<n>`**, updates
     `supporting[]` / `conflict` + `review_status`, **opens a conflict on `contradicts`
     (both preserved, never blended)**, and **never auto-promotes a claim to `verified`
     without a sound match AND the source-quality bar**. Emits a manifest block.

The concrete external (Exa) adapter is `external_web_adapter.py` (SV-02); the egress
sanitizer is `egress_sanitizer.py` (SV-03). This module imports neither at module load
(the Exa adapter is resolved lazily) so it stays a clean, importable contract layer.

Stdlib only. Deterministic: an injected backend (or a replayed response) yields the
same pipeline output every time, so the fixture can assert it.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import egress_sanitizer as ES  # SV-03 — same references/scripts dir  # noqa: E402
import claim_identity as CI  # FD-02 — canonical claim identity + §7.5 invalidation classes  # noqa: E402

# ---------------------------------------------------------------------------
# Conventions enums (VERBATIM, conventions.md §1.3 / §2.2). Defined locally so this
# references/ module never depends on a skill package (skills import references, not
# the reverse).
# ---------------------------------------------------------------------------
REVIEW_STATUS = {"verified", "needs-review", "unsupported", "conflicting"}
VERDICTS = {"corroborates", "contradicts", "not-found", "not-checked-budget"}
SOURCE_TYPES = {"external-web", "internal-vault", "internal-memory"}
SEVERITY = {"must-fix", "should-fix", "polish"}

# The link-confidence cut between an ASSERTED provenance link and a HELD candidate
# (conventions.md §2 / evidence-map EM-01). A corroboration below this is recorded as a
# candidate (held for human approval), never asserted as provenance.
LINK_THRESHOLD = 0.6


def link(source_id: str, locator: str, confidence: float, rationale: str) -> dict:
    """A single §2 provenance link. `confidence` is the LINK confidence (does this source
    back this claim), clamped to [0,1]."""
    try:
        conf = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        conf = 0.0
    return {"source_id": source_id, "locator": locator,
            "confidence": round(conf, 3), "rationale": rationale or ""}


def derive_review_status(supporting: list, conflict, declared=None) -> str:
    """Derive a claim's review_status from its links + conflict (conventions.md §1.3/§2).
    A declared `unsupported`/`conflicting` is never softened; a conflict always wins."""
    if declared in ("unsupported", "conflicting"):
        return "conflicting" if conflict else declared
    if conflict:
        return "conflicting"
    if not supporting:
        return "unsupported"
    if declared == "verified":
        return "verified"
    return "needs-review"


# ---------------------------------------------------------------------------
# §2.3 materiality tiers (highest governs eligibility priority)
# ---------------------------------------------------------------------------
MATERIALITY_PRIORITY = {
    "headline": 7, "regulatory": 6, "market": 5, "numeric": 4,
    "named-entity": 3, "date": 2, "assumption-as-fact": 1,
}

# Per-travel-class verification budget (conventions.md §2.3). The CONTRACT fixes the
# ordering (internal < travels < board-regulatory) and the EXISTENCE of a cap; the
# numbers are tuneable per run and recorded in the manifest.
DEFAULT_CAPS = {"internal": 0, "travels": 8, "board-regulatory": 20}
# Board/Regulatory: the headline + executive-summary set is ALWAYS in scope.
HEADLINE_ALWAYS_IN_SCOPE = ("headline",)


def materiality_score(materiality) -> int:
    """The highest-governing tier's priority for a claim (a claim may sit in several)."""
    tiers = materiality if isinstance(materiality, (list, tuple, set)) else [materiality]
    return max((MATERIALITY_PRIORITY.get(t, 0) for t in tiers), default=0)


def _dedupe_key(claim_text: str) -> str:
    """A normalised token key so near-identical claims verify once (§2.3)."""
    toks = re.findall(r"[a-z0-9]+", (claim_text or "").lower())
    return " ".join(sorted(set(toks)))


# ---------------------------------------------------------------------------
# §2.4 external source-quality policy
# ---------------------------------------------------------------------------
# Authority order (conventions.md §2.4). Tunable, domain-keyed, deliberately generic —
# no house client. The entity's OWN domain (an official primary source for a claim about
# it) is supplied at runtime via `primary_domains`, never hard-coded.
AUTHORITY_SCORE = {"official": 3, "primary": 2, "secondary": 1, "low": 0}

OFFICIAL_PATTERNS = (
    ".gov", ".gov.", ".mil", ".int", "europa.eu", "ec.europa.eu", "ecb.europa.eu",
    "esma.europa.eu", "sec.gov", "federalreserve.gov", "bankofengland.co.uk",
    "bis.org", "imf.org", "worldbank.org", "oecd.org", "un.org", "who.int",
    "bipm.org", "iso.org", "ieee.org", "nist.gov", "eur-lex.europa.eu",
)
PRIMARY_NEWS = (
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "economist.com", "nytimes.com", "washingtonpost.com", "theguardian.com",
    "bbc.co.uk", "bbc.com", "cnbc.com", "afp.com", "dowjones.com",
)
SECONDARY_REPUTABLE = (
    "wikipedia.org", "investopedia.com", "forbes.com", "businessinsider.com",
    "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com",
    "statista.com", "spglobal.com", "moodys.com", "fitchratings.com",
)
# Content farms / aggregators / user-generated — weak corroboration only; can NEVER
# clear a must-fix (conventions.md §2.4).
LOW_PATTERNS = (
    "medium.com", "blogspot.", "wordpress.com", "quora.com", "reddit.com",
    "answers.com", "ehow.com", "wikihow.com", ".blogspot.", "tumblr.com",
    "facebook.com", "twitter.com", "x.com", "pinterest.com", "scribd.com",
)


def _domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "", re.IGNORECASE)
    host = (m.group(1) if m else (url or "")).lower()
    return host[4:] if host.startswith("www.") else host


def authority_of(url: str, primary_domains: Sequence[str] = ()) -> str:
    """Classify a URL's authority tier (conventions.md §2.4). Unknown ⇒ `low` (the
    conservative default: an unrecognised page is weak corroboration, never enough to
    clear a must-fix). The entity's own domain (passed in `primary_domains`) ⇒ official."""
    host = _domain(url)
    if not host:
        return "low"
    for pd in primary_domains or ():
        if pd and pd.lower() in host:
            return "official"
    if any(p in host for p in OFFICIAL_PATTERNS):
        return "official"
    if any(host == d or host.endswith("." + d) for d in PRIMARY_NEWS):
        return "primary"
    if any(p in host for p in LOW_PATTERNS):
        return "low"
    if any(host == d or host.endswith("." + d) for d in SECONDARY_REPUTABLE):
        return "secondary"
    return "low"


def grade_corroboration(sources: List["VerificationSource"], severity: str,
                        *, primary_domains: Sequence[str] = ()) -> dict:
    """Apply §2.4 to a set of CORROBORATING sources. Returns
    `{admit_as_verified, confidence, note, qualifying_source_ids}`.

    Rules enforced:
      * retrieval_date REQUIRED — an undated source is treated stale and does not count
        toward high confidence (it can still be weak corroboration).
      * REJECT low-authority for a `must-fix` — content-farm/aggregator/unknown sources
        are excluded from clearing a must-fix claim.
      * HIGH-CONFIDENCE (verified-eligible) = ≥2 independent (distinct-domain) sources at
        ≥ secondary authority, OR 1 primary/official source. A lone secondary ⇒
        needs-review, not verified.
    """
    is_must_fix = (severity == "must-fix")
    dated, undated = [], []
    for s in sources:
        (dated if s.retrieval_date else undated).append(s)

    # for a must-fix, low-authority sources cannot count at all
    def eligible(s: "VerificationSource") -> bool:
        if is_must_fix and s.authority == "low":
            return False
        return True

    counting = [s for s in dated if eligible(s)]
    distinct_domains = {_domain(s.url) for s in counting if AUTHORITY_SCORE.get(s.authority, 0) >= 1}
    has_primary_or_official = any(AUTHORITY_SCORE.get(s.authority, 0) >= 2 for s in counting)
    two_independent = len(distinct_domains) >= 2

    admit = bool(has_primary_or_official or two_independent)
    # confidence: scaled by best authority + breadth; bounded so a single secondary
    # never reaches the LINK_THRESHOLD on its own.
    best = max((AUTHORITY_SCORE.get(s.authority, 0) for s in counting), default=0)
    if admit:
        confidence = 0.9 if has_primary_or_official and best == 3 else (
            0.78 if has_primary_or_official else 0.66)
    else:
        # weak / held: a lone secondary or low-authority-only ⇒ < threshold (candidate)
        confidence = 0.45 if best >= 1 else 0.3

    notes = []
    if undated:
        notes.append("%d source(s) undated ⇒ treated stale, not counted toward high confidence" % len(undated))
    if is_must_fix and any(s.authority == "low" for s in sources):
        notes.append("low-authority source(s) rejected for a must-fix claim")
    if admit and has_primary_or_official:
        notes.append("cleared by 1 primary/official source")
    elif admit and two_independent:
        notes.append("cleared by ≥2 independent sources")
    else:
        notes.append("insufficient authority for high confidence ⇒ held needs-review (candidate)")
    return {
        "admit_as_verified": admit,
        "confidence": round(confidence, 3),
        "note": "; ".join(notes),
        "qualifying_source_ids": [s.source_id for s in counting if s.source_id],
    }


# ---------------------------------------------------------------------------
# Schema — the single adapter return shape (conventions.md §2.2)
# ---------------------------------------------------------------------------
@dataclass
class VerificationSource:
    """A source a verification found. `provider` is metadata only (conventions.md §2.2);
    the source TYPE (external-web / internal-vault / internal-memory) is part of the
    contract, the provider that answered is not."""
    type: str                              # one of SOURCE_TYPES
    url: str = ""                          # external-web: the URL; internal: note-path/id
    snippet: str = ""
    confidence: float = 0.0                # link confidence (set by grading / linker)
    retrieval_date: str = ""               # as-of of the fetch (REQUIRED for high conf)
    provider: str = ""                     # metadata: which backend answered (e.g. "exa")
    authority: str = "low"                 # §2.4 tier (external-web); "internal" for internal sources
    source_id: str = ""                    # assigned when admitted to the packet as S<n>
    excerpt_hash: str = ""                 # §2.1 — sha256 of the retrieved text (internal-as-sensitive);
                                           # proves WHAT was seen without retaining the whole note

    def to_dict(self) -> dict:
        return {
            "type": self.type, "provider": self.provider, "url": self.url,
            "snippet": self.snippet, "confidence": round(float(self.confidence), 3),
            "retrieval_date": self.retrieval_date, "authority": self.authority,
            "source_id": self.source_id, "excerpt_hash": self.excerpt_hash,
        }


@dataclass
class VerificationResult:
    """The adapter return: a verdict + the sources it rests on + a human note."""
    claim: str
    verdict: str                            # one of VERDICTS
    sources: List[VerificationSource] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"claim": self.claim, "verdict": self.verdict,
                "sources": [s.to_dict() for s in self.sources], "note": self.note}


# ---------------------------------------------------------------------------
# Adapter protocol + the never-fabricate null adapter
# ---------------------------------------------------------------------------
class VerificationAdapter:
    """The protocol every backend implements. `kind` is the source_type it produces."""
    kind: str = "external-web"

    def verify(self, claim: str, sensitivity: str, context: dict) -> VerificationResult:
        raise NotImplementedError


class NullExternalAdapter(VerificationAdapter):
    """Used when the external op did NOT resolve (registry returned None) or was skipped.
    Returns `not-found` with a stated note — NEVER a fabricated citation."""
    kind = "external-web"

    def __init__(self, reason: str = "external verification unavailable (no provider resolved)"):
        self.reason = reason

    def verify(self, claim: str, sensitivity: str, context: dict) -> VerificationResult:
        return VerificationResult(claim=claim, verdict="not-found", sources=[], note=self.reason)


def resolve_external_adapter(registry, *, search_fn: Optional[Callable] = None,
                             fetch_fn: Optional[Callable] = None,
                             provider_label: Optional[str] = None,
                             source_quality: Optional[dict] = None) -> VerificationAdapter:
    """Resolve the external-web adapter THROUGH the F2-04 registry — never by tool name.

    If `external_search` resolves, build the Exa-style `ExternalWebAdapter` (SV-02) wired
    to the injected `search_fn`/`fetch_fn` (the harness binds these to the resolved MCP
    tool; tests inject replays). If it does NOT resolve, return a `NullExternalAdapter`
    so the pipeline degrades to `needs-review` with a stated note — never a crash, never
    a fabricated verdict (`capability-inventory.md` external_search degradation)."""
    search = registry.resolve("external_search") if registry else None
    if search is None:
        return NullExternalAdapter("external verification unavailable: no external_search "
                                   "provider resolved by the registry")
    if search_fn is None:
        return NullExternalAdapter("external verification unavailable: external_search op "
                                   "resolved (%s) but no callable was wired this run" % search.binding())
    # lazy import so this contract module doesn't hard-depend on the adapter at load time
    import external_web_adapter as EWA  # noqa: E402
    fetch = registry.resolve("external_fetch") if registry else None
    label = provider_label or _provider_label_from(search)
    return EWA.ExternalWebAdapter(
        search_fn=search_fn, fetch_fn=(fetch_fn if fetch is not None else None),
        provider_label=label, search_binding=search.binding(),
        fetch_binding=(fetch.binding() if fetch is not None else None),
        source_quality=source_quality or {},
    )


def _provider_label_from(adapter) -> str:
    """Derive a human provider label from the bound op (metadata only). e.g. an Exa tool
    ⇒ 'exa'; a generic web-search tool ⇒ its short token. Never part of the schema."""
    pid = getattr(adapter, "provider_id", "") or ""
    if "exa" in pid or "exa" in (getattr(adapter, "tool", "") or ""):
        return "exa"
    return pid.split("_")[0] if pid else "web"


# The internal fallback chain (capability-inventory `internal_*`; conventions §2.2). Order
# IS the chain; each entry is (provider-label, source-type). The two builtin terminals
# (source_packet, session_memory) are handled by the adapter itself, so this lists only the
# providers the harness binds a callable for. NEVER a hard-coded single tool — the registry
# decides which of these is actually present this run.
INTERNAL_CHAIN = (
    ("obsidian", "internal-vault"),
    ("smart_connections", "internal-vault"),
    ("local_vault_rg", "internal-vault"),
    ("repo_docs", "internal-vault"),
)


def resolve_internal_adapter(registry, *, lane_fns: Optional[Dict[str, Callable]] = None,
                             read_fn: Optional[Callable] = None, read_provider: str = "",
                             packet_claims: Optional[Sequence[dict]] = None,
                             retrieval_date: str = "", max_results: int = 6
                             ) -> VerificationAdapter:
    """Resolve the internal-corpus adapter THROUGH the F2-04 registry — never by tool name.

    Unlike the external adapter, the internal chain has an always-on builtin TERMINAL
    (`internal_search` resolves the `source_packet`/`session_memory` builtin), so this
    NEVER returns a null/None: it returns an `InternalCorpusAdapter` whose live lanes are
    whatever the harness bound a callable for (`lane_fns`, keyed by the chain's provider
    labels), walked in chain order, terminating at the provided source packet then session
    memory. A lane the registry did not resolve, or for which no callable was wired, is
    simply absent — the adapter falls through it, never crashes, never fabricates.

    `lane_fns` — {provider_label: search_callable}; the harness binds the resolved MCP/local
    op to its callable, tests inject replays. `read_fn` — an optional `internal_read`
    confirmation. `packet_claims` — the run's source packet (the builtin terminal target)."""
    import internal_corpus_adapter as ICA  # noqa: E402 — lazy, so this stays a clean contract layer
    lane_fns = lane_fns or {}
    # confirm the registry actually resolves the internal ops (drives the manifest binding +
    # the documented degradation); resolution never throws for these (they have a terminal).
    _ = registry.resolve("internal_search") if registry else None
    lanes = [ICA.Lane(provider=label, kind=kind, search_fn=lane_fns.get(label))
             for (label, kind) in ICA.chain_with_pack(INTERNAL_CHAIN)]
    return ICA.InternalCorpusAdapter(
        lanes=lanes, packet_claims=packet_claims, read_fn=read_fn,
        read_provider=read_provider, max_results=max_results, retrieval_date=retrieval_date)


def grade_internal(sources: List["VerificationSource"]) -> dict:
    """Grade a set of CORROBORATING **internal** sources (§2.1/§2.2). Internal sources have
    no external §2.4 authority tier, so the grade rests on the LINK confidence the backend
    returned (a relevance/semantic score). Returns the same shape as `grade_corroboration`.

    A strong internal match (≥ LINK_THRESHOLD) is verified-eligible — the user's own corpus
    is a real source; a weak match is HELD as a candidate, never auto-promoted. An undated
    source still counts (internal notes are retrieved as-of now), but a zero-confidence row
    can never assert provenance."""
    best = max((float(s.confidence) for s in sources), default=0.0)
    admit = best >= LINK_THRESHOLD
    qualifying = [s.source_id for s in sources if s.source_id and float(s.confidence) >= LINK_THRESHOLD]
    if admit:
        note = "internal corroboration cleared the link-confidence bar (best=%.2f ≥ %.2f)" % (best, LINK_THRESHOLD)
    else:
        note = ("internal corroboration below the link-confidence bar (best=%.2f < %.2f) ⇒ held "
                "needs-review (candidate)" % (best, LINK_THRESHOLD))
    return {"admit_as_verified": admit, "confidence": round(min(best, 0.85), 3),
            "note": note, "qualifying_source_ids": qualifying}


# ---------------------------------------------------------------------------
# The pipeline: result → source, provenance + conflict, budget accounting
# ---------------------------------------------------------------------------
class SourcePacket:
    """Minimal packet wrapper that assigns stable, never-reused S<n> ids (§1.1)."""

    def __init__(self, sources: Optional[List[dict]] = None):
        self.sources: List[dict] = list(sources or [])
        mx = 0
        for s in self.sources:
            m = re.match(r"S(\d+)$", str(s.get("source_id", "")))
            if m:
                mx = max(mx, int(m.group(1)))
        self._next = mx + 1

    def add_source(self, *, type: str, locator: str, provider: str, sensitivity: str,
                   source_status: str, retrieval_date: str = "", authority: str = "") -> str:
        sid = "S%d" % self._next
        self._next += 1
        self.sources.append({
            "source_id": sid, "type": type, "locator": locator, "provider": provider,
            "sensitivity": sensitivity, "source_status": source_status,
            "retrieval_date": retrieval_date, "authority": authority,
        })
        return sid


# Sentinel returned by a pass when a per-call timeout fired (SV-07): distinct from a
# not-found result, so the run records it as not-checked-budget, never a fabricated verdict.
_TIMEOUT = object()


@dataclass
class ClaimVerification:
    """The per-claim verification log entry written to the manifest."""
    claim: str
    verdict: str
    materiality: list
    severity: str
    admitted_source_ids: List[str] = field(default_factory=list)
    new_review_status: str = ""
    confidence: float = 0.0
    note: str = ""
    egress: Optional[dict] = None
    promoted_to_verified: bool = False


class Verifier:
    """Runs the verification pipeline over a list of claim records, folding each result
    back into the SAME §2 provenance record (claim.supporting[] / claim.conflict) and the
    source packet, bounded by the §2.3 budget and gated by the SV-03 egress guard."""

    def __init__(self, *, claims: List[dict], packet: SourcePacket, registry,
                 travel_class: str = "travels", run_sensitivity: str = "internal",
                 caps: Optional[dict] = None, banned_tokens: Sequence[str] = (),
                 primary_domains: Sequence[str] = (), egress_waiver: Optional[dict] = None,
                 search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None,
                 retrieval_date: str = "", provider_label: Optional[str] = None,
                 run_external: bool = True, run_internal: bool = False,
                 internal_lane_fns: Optional[Dict[str, Callable]] = None,
                 internal_read_fn: Optional[Callable] = None,
                 per_call_timeout_s: Optional[float] = None,
                 per_run_timeout_s: Optional[float] = None):
        self.claims = claims
        self.packet = packet
        self.registry = registry
        self.travel_class = (travel_class or "travels").lower()
        self.run_sensitivity = run_sensitivity
        self.caps = dict(DEFAULT_CAPS, **(caps or {}))
        self.sanitizer = ES.EgressSanitizer(banned_tokens=banned_tokens)
        self.primary_domains = list(primary_domains or [])
        self.egress_waiver = egress_waiver
        self.retrieval_date = retrieval_date
        self.search_fn = search_fn
        self.fetch_fn = fetch_fn
        self.provider_label = provider_label
        # SV-04/SV-06: the internal pass (no egress) is opt-in; SV-06 turns it on by travel class.
        self.run_external = run_external
        self.run_internal = run_internal
        self.internal_lane_fns = dict(internal_lane_fns or {})
        self.internal_read_fn = internal_read_fn
        # SV-07: per-call + per-run timeouts (None ⇒ unbounded; recorded in the manifest).
        self.per_call_timeout_s = per_call_timeout_s
        self.per_run_timeout_s = per_run_timeout_s
        self.results: List[ClaimVerification] = []
        self.egress_records: List[dict] = []
        self._adapter: Optional[VerificationAdapter] = None
        self._internal_adapter: Optional[VerificationAdapter] = None
        self._claims_checked: int = 0
        self._timeouts: List[str] = []

    # -- eligibility / budget ---------------------------------------------
    def _budget(self) -> int:
        return int(self.caps.get(self.travel_class, 0))

    def eligible_order(self) -> List[int]:
        """Indices of verifiable claims, de-duped, highest-materiality first. A claim is
        eligible for the EXTERNAL pass if its verifiability is external/None, and for the
        INTERNAL pass if internal/None; only claims matching an ENABLED pass are in scope
        (so an internal-only claim is checked once the internal pass is on — SV-04 fix).
        Near-identical claims verify once (§2.3 dedupe). Board/Regulatory keeps the headline
        set always in scope (enforced in run())."""
        seen, scored = set(), []
        for i, c in enumerate(self.claims):
            v = c.get("verifiability")
            external_ok = self.run_external and v in ("external", "external-checkable", None)
            internal_ok = self.run_internal and v in ("internal", "internal-checkable", None)
            if not (external_ok or internal_ok):
                continue
            key = _dedupe_key(c.get("claim", ""))
            if key in seen:
                continue
            seen.add(key)
            scored.append((materiality_score(c.get("materiality")), -i, i))
        scored.sort(reverse=True)
        return [i for _, _, i in scored]

    def _adapter_for(self) -> VerificationAdapter:
        if self._adapter is None:
            self._adapter = resolve_external_adapter(
                self.registry, search_fn=self.search_fn, fetch_fn=self.fetch_fn,
                provider_label=self.provider_label,
                source_quality={"primary_domains": self.primary_domains},
            )
        return self._adapter

    def _internal_adapter_for(self) -> VerificationAdapter:
        if self._internal_adapter is None:
            self._internal_adapter = resolve_internal_adapter(
                self.registry, lane_fns=self.internal_lane_fns, read_fn=self.internal_read_fn,
                packet_claims=self.packet.sources, retrieval_date=self.retrieval_date)
        return self._internal_adapter

    @staticmethod
    def _guarded_verify(adapter, claim_text, sens, ctx, timeout_s):
        """Call adapter.verify under a per-call wall-clock timeout (SV-07). Returns
        (status, result) where status ∈ {ok, timeout, err}. A timeout NEVER blocks the run —
        the slow lane is abandoned (daemon thread) and the claim is recorded not-checked."""
        if not timeout_s:
            try:
                return ("ok", adapter.verify(claim_text, sens, ctx))
            except Exception:  # noqa: BLE001
                return ("err", None)
        import threading
        box: dict = {}

        def _run():
            try:
                box["v"] = ("ok", adapter.verify(claim_text, sens, ctx))
            except Exception:  # noqa: BLE001
                box["v"] = ("err", None)
        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(timeout_s)
        if th.is_alive():
            return ("timeout", None)
        return box.get("v", ("err", None))

    # -- run ---------------------------------------------------------------
    def run(self) -> dict:
        import time
        budget = self._budget()
        order = self.eligible_order()
        used = 0
        t0 = time.monotonic()

        for idx in order:
            claim = self.claims[idx]
            tiers = claim.get("materiality") or []
            tiers = tiers if isinstance(tiers, (list, tuple)) else [tiers]
            is_headline = any(t in HEADLINE_ALWAYS_IN_SCOPE for t in tiers)
            in_budget = used < budget
            board_headline = (self.travel_class == "board-regulatory" and is_headline)

            if not (in_budget or board_headline):
                self._record_not_checked(claim, "verification budget (cap=%d) reached for "
                                         "travel-class %s" % (budget, self.travel_class))
                continue

            # SV-07 per-run timeout: stop checking, record the rest not-checked-budget (never drop).
            if self.per_run_timeout_s and (time.monotonic() - t0) > self.per_run_timeout_s:
                self._record_not_checked(claim, "per-run verification timeout (%.2fs) reached"
                                         % self.per_run_timeout_s, timeout=True)
                continue

            sens = (claim.get("sensitivity") or self.run_sensitivity or "internal").lower()
            ran_any = False

            # ---- EXTERNAL pass (egress-gated) -------------------------------------
            if self.run_external:
                gate = ES.egress_gate(sens, self.travel_class, self.egress_waiver)
                if not gate["egress_allowed"]:
                    # restricted-Board with no signed waiver ⇒ skipped-egress (not failed).
                    # Internal verification (no egress) still runs regardless (policy §5.1).
                    self._record_not_checked(claim, gate["reason"], egress_skipped=True, sens=sens)
                else:
                    result = self._verify_one(claim, sens, gate["mode"])
                    if result is _TIMEOUT:
                        self._record_not_checked(claim, "per-call timeout (%.2fs) on the external "
                                                 "backend" % self.per_call_timeout_s, timeout=True)
                    else:
                        self._fold(claim, result, sens)
                        ran_any = True

            # ---- INTERNAL pass (no egress; §2.1 least-exposure) -------------------
            if self.run_internal:
                iresult = self._verify_one_internal(claim, sens)
                if iresult is _TIMEOUT:
                    self._record_not_checked(claim, "per-call timeout (%.2fs) on the internal "
                                             "backend" % self.per_call_timeout_s, timeout=True)
                else:
                    self._fold(claim, iresult, sens, internal=True)
                    ran_any = True

            if ran_any:
                used += 1
                self._claims_checked += 1

        return self.manifest_block()

    # -- one claim ---------------------------------------------------------
    def _verify_one(self, claim: dict, sens: str, mode: str) -> VerificationResult:
        entities = list(claim.get("entities") or [])
        san = self.sanitizer.sanitize(claim.get("claim", ""), sensitivity=sens,
                                      entities=entities, mode=mode)
        rec = ES.egress_record(san, sensitivity=sens, mode=mode, egress_allowed=True)
        self.egress_records.append(rec)
        if san.blocked or not san.egressed_query:
            return VerificationResult(claim=claim.get("claim", ""), verdict="not-found",
                                      note="egress sanitiser blocked the query: " + (san.block_reason or "empty"))
        ctx = {
            "query": san.egressed_query, "severity": claim.get("severity", "should-fix"),
            "materiality": claim.get("materiality"), "primary_domains": self.primary_domains,
            "retrieval_date": self.retrieval_date, "expected": claim.get("expected"),
            "expected_numbers": claim.get("expected_numbers"),
        }
        status, result = self._guarded_verify(self._adapter_for(), claim.get("claim", ""),
                                              sens, ctx, self.per_call_timeout_s)
        if status == "timeout":
            return _TIMEOUT
        if status == "err" or result is None:
            return VerificationResult(claim=claim.get("claim", ""), verdict="not-found",
                                      note="external verification unavailable: backend raised; "
                                           "claim stays needs-review — no citation fabricated")
        return result

    # -- one claim, INTERNAL (no egress; §2.1 least-exposure) --------------
    def _verify_one_internal(self, claim: dict, sens: str):
        """Run the internal corpus pass. NO egress gate (the internal chain never touches the
        web), but the result IS governed by least-exposure: the adapter stores only path +
        excerpt hash + the minimum snippet, and `_admit` classifies the source `internal`
        (default-up), so it inherits the same redaction + model-routing as any internal
        source. Returns a VerificationResult or the _TIMEOUT sentinel."""
        ctx = {
            "query": claim.get("claim", ""),  # no sanitiser — nothing leaves the machine
            "severity": claim.get("severity", "should-fix"),
            "materiality": claim.get("materiality"),
            "retrieval_date": self.retrieval_date,
            "expected": claim.get("expected"), "expected_numbers": claim.get("expected_numbers"),
            "key_terms": claim.get("key_terms"),
        }
        status, result = self._guarded_verify(self._internal_adapter_for(), claim.get("claim", ""),
                                              sens, ctx, self.per_call_timeout_s)
        if status == "timeout":
            return _TIMEOUT
        if status == "err" or result is None:
            return VerificationResult(claim=claim.get("claim", ""), verdict="not-found",
                                      note="internal verification unavailable: backend raised; "
                                           "claim stays needs-review — no source fabricated")
        return result

    # -- fold result into the §2 record + packet --------------------------
    def _fold(self, claim: dict, result: VerificationResult, sens: str, *, internal: bool = False) -> None:
        severity = claim.get("severity", "should-fix")
        claim.setdefault("supporting", [])
        # an internal pass carries no egress record; an external pass does.
        egress = (None if internal else (self.egress_records[-1] if self.egress_records else None))
        cv = ClaimVerification(claim=result.claim, verdict=result.verdict,
                               materiality=claim.get("materiality") or [], severity=severity,
                               note=result.note, egress=egress)

        if result.verdict == "contradicts":
            sid = self._admit(result.sources[0], sens) if result.sources else None
            note = result.note or "external source disagrees with the claim"
            # CONFLICT, never blended: preserve the existing supporting + record the disagreement
            existing = (claim.get("conflict") or {}).get("with", []) if claim.get("conflict") else []
            with_ids = list(dict.fromkeys(existing + ([sid] if sid else [])))
            claim["conflict"] = {"with": with_ids, "note": note}
            claim["review_status"] = derive_review_status(claim["supporting"], claim["conflict"],
                                                          claim.get("review_status"))
            cv.admitted_source_ids = [sid] if sid else []
            cv.new_review_status = claim["review_status"]
            cv.note = note

        elif result.verdict == "corroborates":
            grade = (grade_internal(result.sources) if internal
                     else grade_corroboration(result.sources, severity, primary_domains=self.primary_domains))
            kind_label = "internal" if internal else "external"
            admitted = [self._admit(s, sens) for s in result.sources]
            cv.admitted_source_ids = [a for a in admitted if a]
            cv.confidence = grade["confidence"]
            if grade["admit_as_verified"] and grade["confidence"] >= LINK_THRESHOLD:
                # sound match + source-quality bar cleared ⇒ assert provenance + verified
                for s in result.sources:
                    if s.source_id:
                        claim["supporting"].append(
                            link(s.source_id, s.url, grade["confidence"],
                                 "%s %s corroboration (%s)" % (kind_label, s.authority, s.provider or kind_label)))
                claim["review_status"] = derive_review_status(
                    claim["supporting"], claim.get("conflict"), "verified")
                cv.promoted_to_verified = (claim["review_status"] == "verified")
            else:
                # NO auto-promotion: held as candidate, claim stays needs-review
                claim.setdefault("candidates", [])
                for s in result.sources:
                    if s.source_id:
                        claim["candidates"].append(
                            link(s.source_id, s.url, min(grade["confidence"], LINK_THRESHOLD - 0.01),
                                 "weak/insufficient external corroboration — held for human approval"))
                if claim.get("review_status") not in ("conflicting", "verified"):
                    claim["review_status"] = "needs-review"
            cv.new_review_status = claim["review_status"]
            cv.note = grade["note"]

        else:  # not-found
            cv.new_review_status = claim.get("review_status", "needs-review")
            # a not-found never changes a claim's status; it is recorded, not silent

        self.results.append(cv)

    def _admit(self, s: VerificationSource, run_sens: str) -> str:
        """Admit a found source to the packet as a new S<n> (§2.2). External-web sources
        are `public` (already on the open web) and `current` unless undated (⇒ unknown)."""
        sid = self.packet.add_source(
            type=s.type, locator=s.url, provider=s.provider,
            sensitivity="public" if s.type == "external-web" else "internal",
            source_status="current" if s.retrieval_date else "unknown",
            retrieval_date=s.retrieval_date, authority=s.authority)
        s.source_id = sid
        return sid

    def _record_not_checked(self, claim: dict, reason: str, *, egress_skipped: bool = False,
                            sens: str = "", timeout: bool = False) -> None:
        cv = ClaimVerification(
            claim=claim.get("claim", ""), verdict="not-checked-budget",
            materiality=claim.get("materiality") or [], severity=claim.get("severity", "should-fix"),
            note=reason)
        if timeout:
            self._timeouts.append(claim.get("claim", ""))
        if egress_skipped:
            # record the raw-claim hash so the SKIP is auditable, without egressing anything
            raw = claim.get("claim", "")
            cv.egress = {
                "raw_claim_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "egressed_query": "", "sensitivity": sens, "mode": ES.MODE_SKIPPED,
                "egress_allowed": False, "blocked": False,
                "diff": "skipped-egress — nothing left the machine", "transform": {},
            }
            self.egress_records.append(cv.egress)
        self.results.append(cv)

    # -- manifest ----------------------------------------------------------
    def _is_material(self, materiality, severity) -> bool:
        """A claim a skipped check must be OWNED for (SV-07 → must_own): a headline/exec
        assertion, or a must-fix-severity material claim. Eligible-but-skipped is never a
        silent omission — these surface as must_own so the readiness layer carries them."""
        tiers = materiality if isinstance(materiality, (list, tuple, set)) else [materiality]
        return bool(any(t in HEADLINE_ALWAYS_IN_SCOPE for t in tiers)
                    or severity == "must-fix"
                    or materiality_score(tiers) >= MATERIALITY_PRIORITY["regulatory"])

    def manifest_block(self) -> dict:
        adapter_bindings = self.registry.manifest_record()["adapter_bindings"] if self.registry else {}
        not_checked = [r for r in self.results if r.verdict == "not-checked-budget"]
        # SV-07: an eligible-but-skipped MATERIAL claim is surfaced in must_own (owned by a
        # human in the readiness layer), never silently dropped.
        must_own = [{"claim": r.claim, "reason": r.note,
                     "materiality": r.materiality, "severity": r.severity}
                    for r in not_checked if self._is_material(r.materiality, r.severity)]
        return {
            "verification": {
                "_doc": "SV-01 pipeline output (conventions.md §2.2/§2.3/§2.4; internal §2.1). Each "
                        "result folded into the same §2 provenance record; provider is metadata only.",
                "travel_class": self.travel_class,
                "passes": {"external": bool(self.run_external), "internal": bool(self.run_internal)},
                "caps": self.caps,
                "timeouts": {"per_call_s": self.per_call_timeout_s, "per_run_s": self.per_run_timeout_s,
                             "timed_out_claims": list(self._timeouts)},
                "claims_checked": self._claims_checked,
                "budget_used": self._claims_checked,
                "results": [self._cv_dict(r) for r in self.results],
                "not_checked_budget": [r.claim for r in not_checked],
                "must_own": must_own,
                "promoted_to_verified": [r.claim for r in self.results if r.promoted_to_verified],
            },
            "egress": self.egress_records,
            "adapter_bindings": adapter_bindings,
        }

    @staticmethod
    def _cv_dict(r: ClaimVerification) -> dict:
        return {
            "claim": r.claim, "verdict": r.verdict, "materiality": r.materiality,
            "severity": r.severity, "admitted_source_ids": r.admitted_source_ids,
            "new_review_status": r.new_review_status, "confidence": r.confidence,
            "note": r.note, "promoted_to_verified": r.promoted_to_verified,
            "egress": r.egress,
        }


# =============================================================================
# UP-02 — diff-aware re-verification: the explicit invalidation matrix
# =============================================================================
# A recurring refresh (office-update / S02·S03) tells us *which* claims changed. The cheap,
# honest move is to re-verify ONLY the changed/new set and CARRY a prior §2 verdict forward
# for an identity-stable, unchanged claim. The danger (codex v3 finding #2): carrying a
# verdict forward without a gate is exactly how a refresh reuses 'verified' on a claim whose
# *meaning moved* — its source aged, its unit/period shifted, its waiver owner changed, or the
# whole document escalated to a higher travel class. The matrix below is that gate: an
# EXPLICIT table mapping every input change-class to one of three actions, consulted before
# any prior verdict is kept.
#
#     input change-class        →  action     why
#     ------------------------     ---------   ------------------------------------------------
#     locator                      carry       a pure MOVE — identity is stable (fd-02), the
#                                              verdict still holds; evidence is re-pointed (up-03)
#     text                         re-open     the claim's meaning changed — re-judge the new
#                                              text against its sources
#     unit-period                  re-open     magnitude/period semantics shifted — re-judge
#     waiver-owner                 re-open     the §4 waiver authority changed — the human
#                                              decision the verdict rested on must be re-confirmed
#     travel-class-escalation      re-open     higher stakes — a verdict earned under a laxer
#                                              regime (e.g. Internal, external pass off) must be
#                                              re-earned under the stricter passes
#     source                       re-source   the backing source itself changed — you cannot
#                                              re-judge against a cached source; go back to it
#     source-age                   re-source   the source is stale — re-acquire a CURRENT source
#
# `carry` keeps the prior verdict (+ a pointer to the prior run). `re-open` and `re-source`
# BOTH invalidate the carried verdict and put the claim back in the verify set; `re-source` is
# the stronger signal that the *source*, not just the judgement, must be re-acquired. Severity
# orders them carry < re-open < re-source, and the MOST SEVERE triggered action governs.

ACTION_CARRY = "carry"
ACTION_REOPEN = "re-open"
ACTION_RESOURCE = "re-source"

# The authoritative table — the seven input change-classes mapped to exactly one action each.
INVALIDATION_MATRIX: Dict[str, str] = {
    "locator": ACTION_CARRY,
    "text": ACTION_REOPEN,
    "unit-period": ACTION_REOPEN,
    "waiver-owner": ACTION_REOPEN,
    "travel-class-escalation": ACTION_REOPEN,
    "source": ACTION_RESOURCE,
    "source-age": ACTION_RESOURCE,
}

_ACTION_SEVERITY = {ACTION_CARRY: 0, ACTION_REOPEN: 1, ACTION_RESOURCE: 2}

# Map the fd-02 §7.5 invalidation-class names onto the matrix's change-class keys. A
# per-claim materiality escalation rides the same row as a run-level travel-class escalation
# (both are "the stakes went up; re-earn the verdict"). A superseded/tombstoned claim never
# carries (it is handled upstream by office-update) — if one ever reaches the gate it FAILS
# SAFE to re-open rather than silently carrying.
_CI_CLASS_TO_MATRIX = {
    "text-changed": "text",
    "source-changed": "source",
    "unit-or-period-changed": "unit-period",
    "source-stale": "source-age",
    "waiver-scope-changed": "waiver-owner",
    "materiality-escalated": "travel-class-escalation",
    "superseded-or-tombstoned": "text",   # fail-safe: invalidate (re-open), never carry
}


def matrix_action(change_class: str) -> str:
    """The action for one input change-class. An UNKNOWN class fails SAFE to `re-open`
    (never silently carry a verdict on a change we do not recognise)."""
    return INVALIDATION_MATRIX.get(change_class, ACTION_REOPEN)


def _most_severe(actions: Sequence[str]) -> str:
    """The governing action when several change-classes fire at once (carry < re-open < re-source)."""
    best = ACTION_CARRY
    for a in actions:
        if _ACTION_SEVERITY.get(a, 1) > _ACTION_SEVERITY.get(best, 0):
            best = a
    return best


@dataclass
class ReverifyDecision:
    """The matrix verdict for ONE identity-stable carry-candidate.

    `action`         — carry | re-open | re-source (the governing, most-severe triggered action).
    `triggered`      — every fired change-class with its own action (auditable; empty ⇒ clean carry).
    `carried_verdict`— the prior verdict, kept ONLY when `action == carry`, else None.
    `prior_run`      — the pointer to the run that produced the carried verdict (kept on carry).
    `reverify`       — True when the claim must go back into the verify set (action != carry).
    """
    claim_id: str
    action: str
    triggered: List[dict]
    carried_verdict: Optional[str]
    prior_run: Optional[str]
    reverify: bool

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id, "action": self.action, "triggered": self.triggered,
            "carried_verdict": self.carried_verdict, "prior_run": self.prior_run,
            "reverify": self.reverify,
        }


def decide_carry(
    change: dict,
    *,
    prior_verdict: Optional[str],
    prior_run_ref: Optional[str] = None,
    curr_identity: Optional["CI.ClaimIdentity"] = None,
    prior_waiver_scope_hash: Optional[str] = None,
    now: Optional[str] = None,
    freshness_days: Optional[int] = None,
    travel_escalated: bool = False,
) -> ReverifyDecision:
    """Apply the explicit invalidation matrix to ONE identity-stable carry-candidate.

    `change` is a single office-update change-set entry (`classify_changes()['changes'][i]`):
    its `invalidation` list (fd-02 §7.5 classes), `relation` (`moved`/`unchanged`) and
    `claim_id` are read. The matrix ADDS the two run-level gates office-update does not assume:
      * `source-age`  — applied here via fd-02 `is_stale(curr_identity, now, freshness_days)`
                        (office-update does not presume a freshness policy);
      * `waiver-owner`— the waiver the prior VERDICT was granted under (`prior_waiver_scope_hash`)
                        vs the current claim's waiver scope (caught even when the current
                        claim-set→claim-set diff alone would not — the verdict's own context);
      * `travel-class-escalation` — `travel_escalated` (run-level, set by the driver).
    Returns a `ReverifyDecision`. A clean carry keeps the prior verdict + prior-run pointer; any
    re-open / re-source invalidates it and flags the claim for re-verification."""
    triggered: List[dict] = []

    def fire(change_class: str) -> None:
        triggered.append({"change_class": change_class, "action": matrix_action(change_class)})

    # (1) per-claim §7.5 invalidation classes already computed by the identity diff / office-update
    for ci_class in (change.get("invalidation") or []):
        fire(_CI_CLASS_TO_MATRIX.get(ci_class, ci_class))

    # (2) a pure locator MOVE — recorded as a `locator` input whose action is `carry` (never invalidates)
    if change.get("relation") == "moved":
        fire("locator")

    # (3) source-age — the freshness gate up-02 applies at re-verification time
    if curr_identity is not None and CI.is_stale(curr_identity, now=now, freshness_days=freshness_days):
        fire("source-age")

    # (4) waiver-owner — the verdict was granted under a §4 waiver whose scope/owner has changed
    if (prior_waiver_scope_hash is not None and curr_identity is not None
            and curr_identity.waiver_scope_hash != prior_waiver_scope_hash):
        fire("waiver-owner")

    # (5) travel-class escalation — run-level (the whole doc moved to a stricter class)
    if travel_escalated:
        fire("travel-class-escalation")

    action = _most_severe([t["action"] for t in triggered]) if triggered else ACTION_CARRY
    carried = action == ACTION_CARRY
    return ReverifyDecision(
        claim_id=change.get("claim_id", ""),
        action=action,
        triggered=triggered,
        carried_verdict=prior_verdict if carried else None,
        prior_run=prior_run_ref if carried else None,
        reverify=not carried,
    )


def _selftest_matrix() -> int:
    """Tiny self-check of the matrix + decide_carry (stdlib-only; deterministic)."""
    assert set(INVALIDATION_MATRIX) == {
        "locator", "text", "unit-period", "waiver-owner",
        "travel-class-escalation", "source", "source-age"}, "matrix must have the seven rows"
    assert matrix_action("locator") == ACTION_CARRY
    assert matrix_action("source") == ACTION_RESOURCE and matrix_action("source-age") == ACTION_RESOURCE
    assert matrix_action("unknown-class") == ACTION_REOPEN, "unknown ⇒ fail-safe re-open"
    assert _most_severe([ACTION_CARRY, ACTION_REOPEN, ACTION_RESOURCE]) == ACTION_RESOURCE

    # a truly-unchanged claim with no triggers ⇒ carry, prior verdict + pointer kept
    d = decide_carry({"claim_id": "C-1", "relation": "unchanged", "invalidation": []},
                     prior_verdict="verified", prior_run_ref=".trust/prev")
    assert d.action == ACTION_CARRY and d.carried_verdict == "verified" and d.prior_run == ".trust/prev"
    assert not d.reverify

    # a pure move ⇒ still carry (locator row), pointer kept
    dm = decide_carry({"claim_id": "C-1", "relation": "moved", "invalidation": []},
                      prior_verdict="verified", prior_run_ref=".trust/prev")
    assert dm.action == ACTION_CARRY and not dm.reverify
    assert any(t["change_class"] == "locator" for t in dm.triggered)

    # source-age ⇒ re-source, verdict invalidated
    s = [CI.SourceRef("S1", "current", content_hash="abc", as_of="2025-01-01")]
    ident_old = CI.compute_identity(claim_text="x grew 5%", locator="A!1", sources=s,
                                    retrieval_timestamp="2025-01-01T00:00:00Z", materiality_class="numeric")
    da = decide_carry({"claim_id": ident_old.claim_id, "relation": "unchanged", "invalidation": []},
                      prior_verdict="verified", prior_run_ref=".trust/prev", curr_identity=ident_old,
                      now="2026-05-31T00:00:00Z", freshness_days=180)
    assert da.action == ACTION_RESOURCE and da.reverify and da.carried_verdict is None
    assert any(t["change_class"] == "source-age" for t in da.triggered)

    # waiver-owner change ⇒ re-open
    ident_w = CI.compute_identity(claim_text="y is 3", locator="B!2", sources=s,
                                  retrieval_timestamp="2026-05-30T00:00:00Z", materiality_class="numeric",
                                  waiver={"owner": "new-cfo", "decision": "accept", "scope": "fy25",
                                          "travel_class": "board-regulatory"})
    dw = decide_carry({"claim_id": ident_w.claim_id, "relation": "unchanged", "invalidation": []},
                      prior_verdict="ready-with-limitations", prior_run_ref=".trust/prev",
                      curr_identity=ident_w, prior_waiver_scope_hash="deadbeef-old-owner")
    assert dw.action == ACTION_REOPEN and dw.reverify
    assert any(t["change_class"] == "waiver-owner" for t in dw.triggered)

    # travel-class escalation ⇒ re-open
    dt = decide_carry({"claim_id": "C-9", "relation": "unchanged", "invalidation": []},
                      prior_verdict="verified", prior_run_ref=".trust/prev", travel_escalated=True)
    assert dt.action == ACTION_REOPEN and dt.reverify
    print("verification invalidation-matrix selftest: PASS")
    return 0


if __name__ == "__main__":  # tiny self-check
    import json
    import sys as _sys
    if "--selftest" in _sys.argv:
        raise SystemExit(_selftest_matrix())
    print(json.dumps({"authority_examples": {
        "bipm.org": authority_of("https://www.bipm.org/en/si"),
        "reuters.com": authority_of("https://reuters.com/x"),
        "medium.com": authority_of("https://medium.com/@x/y"),
        "unknown.io": authority_of("https://unknown.io/p"),
    }}, indent=2))
