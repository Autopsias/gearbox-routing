#!/usr/bin/env python3
"""
internal_corpus_adapter.py — SV-04 (v2 Source Verification II)
==============================================================
The **internal-corpus verification adapter** — the concrete backend that checks a
claim against the user's **own** corpus (an Obsidian vault, Smart Connections, a
project-memory / knowledge graph, a configured local vault, repo/project docs), and
returns the SV-01 result shape (`{verdict, sources[], note}`). It implements
`verification.VerificationAdapter`, exactly like the SV-02 external-web adapter, so the
pipeline folds an internal find into the **same** §2 provenance record.

Two things make this NOT a copy of SV-02:

1. **It walks a registry-resolved FALLBACK CHAIN, never a single tool** (review #3 —
   "internal chain beyond session memory"). The chain (conventions §2.2;
   capability-inventory `internal_*`) is:

       Obsidian → Smart Connections → project memory (pack-provided) →
       configured local vault (rg/markdown) → repo/project docs →
       provided source packet → session memory

   Each lane is a callable the harness binds to whatever the F2-04 registry resolved
   (`internal_search` / `internal_semantic_search` / `internal_read`); tests inject
   replays. A lane that is **absent, errors, or returns nothing falls through to the
   next** — it NEVER crashes and NEVER fabricates a citation. The two always-on builtin
   terminals (`source_packet`, then `session_memory`) guarantee the chain always
   terminates: a run with no live backend still searches the provided packet, then
   reports `not-found` — identical to v1's closed world.

2. **Internal evidence is sensitive by default** (review #9 / sensitivity-routing §2.1).
   A retrieved note never leaves the machine, but it DOES enter the model's context, the
   logs, and the manifest — so it is governed like any other `internal`+ source, not
   waved through because it is "local". This adapter therefore stores each internal
   source **minimally**: the note **path / record-id** (`locator`), an **excerpt hash**
   (sha256 of the retrieved text — proves *what* was seen without retaining it), and the
   **minimum snippet** the stance step needs (never the whole note). Only that minimum
   snippet is what would reach a reviewer; the pipeline admits the source as `internal`
   (default-up) so it inherits the same redaction + model-routing as any other internal
   source.

Verdict rules (identical contract to SV-02, conventions §2.2):
  * a corroborating internal note  ⇒ `corroborates` (the source-quality grade is applied
    by the pipeline, internal grading by link confidence — a strong match asserts a
    supporting link + verified; a weak one is HELD as a candidate, never auto-promoted);
  * a **disagreeing** internal note ⇒ `contradicts` ⇒ the pipeline OPENS a §2 conflict
    (both preserved, never blended);
  * nothing addressed it ⇒ `not-found`.

Stdlib only. Pure + deterministic: a given replayed backend response always yields the
same parsed sources, the same stance, and the same verdict, so the fixture can assert it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verification as V          # noqa: E402  (VerificationAdapter / Source / Result)
import external_web_adapter as EWA  # noqa: E402  (reuse the identical deterministic stance)
import pack_hooks as H  # noqa: E402  (provider-pack parser and chain-label hooks)
from pack_hooks import chain_with_pack  # noqa: E402,F401  (re-exported)


# ---------------------------------------------------------------------------
# Source types this adapter can produce (conventions.md §2.2)
# ---------------------------------------------------------------------------
TYPE_VAULT = "internal-vault"      # a note/document from the user's corpus (Obsidian, local vault, repo docs)
TYPE_MEMORY = "internal-memory"    # a fact from a project-memory / knowledge-graph backend

# AV-02 (conventions §7.7): a DERIVED-provenance note the Trust Layer wrote back to the
# corpus is AUDIT METADATA, not a primary fact, until a named human PROMOTES it out of
# quarantine. The verifier must NOT cite a quarantined derived note as corroboration —
# otherwise next quarter's refresh would cite the Trust Layer's own artifact as evidence
# (the av-02 poisoning failure). We mark such a source's authority `derived-provenance`
# and exclude it from the corroborating/contradicting set (it stays listed as audit
# context only). Detection is by frontmatter markers the write-back stamps.
DERIVED_PROVENANCE_AUTHORITY = "derived-provenance"
_QUAR_AUTHORITY_RE = re.compile(r"source_authority\s*:\s*derived-provenance", re.I)
_QUAR_PHASE_RE = re.compile(r"\bphase\s*:\s*quarantine\b", re.I)
_QUAR_PROMOTED_RE = re.compile(r"\bphase\s*:\s*promoted\b", re.I)


def _is_quarantined_derived(row: dict) -> bool:
    """True iff a search row is a DERIVED-provenance note still in quarantine (not
    promoted) ⇒ audit metadata, never a primary fact (§7.7 / AV-02). Detected from
    explicit fields (source_authority/phase) OR the note's own frontmatter text
    carried in the snippet/content/title/path."""
    if not isinstance(row, dict):
        return False
    # explicit structured fields (a backend that surfaces frontmatter)
    fm = row.get("frontmatter") if isinstance(row.get("frontmatter"), dict) else {}
    sa = (row.get("source_authority") or fm.get("source_authority") or "").lower()
    phase = (row.get("phase") or fm.get("phase") or "").lower()
    if sa == DERIVED_PROVENANCE_AUTHORITY:
        return phase != "promoted"
    # text scan of the surfaced content (frontmatter often rides in the snippet)
    blob = " ".join(str(row.get(k, "")) for k in
                    ("snippet", "excerpt", "text", "content", "title", "locator",
                     "path", "note_path", "file"))
    if _QUAR_AUTHORITY_RE.search(blob):
        return not _QUAR_PROMOTED_RE.search(blob)
    return False

# How much of a retrieved note may be carried as the minimum snippet that reaches a
# reviewer (§2.1 least-exposure). The FULL retrieved text is hashed into excerpt_hash and
# then discarded — never copied into the run.
DEFAULT_EXCERPT_CHARS = 240


def excerpt_hash(text: str) -> str:
    """sha256 of the retrieved text — proves WHAT was seen without retaining it (§2.1)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Response normalisation — built around the REAL shapes confirmed live in-session.
#
#   Obsidian search (mcp__obsidian-connector__obsidian_search, profile=hybrid):
#       {"result": "[{\"path\":…,\"title\":…,\"score\":0.33,\"snippet\":…}, …]"}
#       (a JSON STRING wrapped in a single-key {"result": …} envelope; on failure the
#        string is an error object {"ok":false,…} or bare text "Vault not found.")
#
#   Memory backend (generic): {"results":[{"id":…,"title":…,"snippet":…,"score":0.98}]}.
#       A backend with its own row keys registers a parser for TYPE_MEMORY in a provider
#       pack (`PARSERS`); the adapter calls it first and falls back to the generic shape.
#
#   Smart Connections lookup (…__lookup): a list / {results:[…]} of semantic hits when a
#       vault path is configured; an error string otherwise (present-but-unconfigured).
#
# The parser tolerates: the {"result": <json-string>} MCP envelope, an already-parsed
# dict/list, a bare JSON string, and any error/`ok:false`/"not found" payload (⇒ []).
# ---------------------------------------------------------------------------
def _unwrap(raw):
    """Peel the MCP `{"result": <payload>}` envelope and parse a JSON string if present.
    Returns a python object (dict/list) or a non-JSON string, or None. Never raises."""
    seen = 0
    while seen < 4:
        seen += 1
        if isinstance(raw, dict) and set(raw.keys()) == {"result"}:
            raw = raw["result"]
            continue
        if isinstance(raw, str):
            s = raw.strip()
            if s[:1] in "[{":
                try:
                    raw = json.loads(s)
                    continue
                except (ValueError, TypeError):
                    return s
            return s
        break
    return raw


def _is_error_payload(obj) -> bool:
    """True for a backend error object the chain should treat as 'this lane failed'."""
    if isinstance(obj, str):
        s = obj.lower()
        return (not s) or ("not found" in s) or ("error" in s and "{" not in s) or ("failed to parse" in s)
    if isinstance(obj, dict):
        if obj.get("ok") is False or "error" in obj:
            return True
    return False


def classify_lane(raw, *, kind: str = TYPE_VAULT) -> tuple:
    """Type one lane's outcome ONCE: `(rows, status, note)`, status in clean|partial|failed.

    TOTAL: any odd shape or exception yields `failed`, never a raise or a silent clean.
    failed = unparseable, non-dict/list, no results key, a results container that is not a
    list, a non-empty list with no usable row, or an `error` payload (valid rows that still
    parse are kept); partial = a truthy `incomplete` or non-empty `errors` (rows kept).
    A bare empty list is `failed` too: it cannot be told apart from a truncated reply."""
    try:
        return _classify_lane(raw, kind)
    except Exception as exc:  # noqa: BLE001 — a lane must never raise out of the search path
        return [], "failed", "unclassifiable reply: %s" % exc.__class__.__name__


def _classify_lane(raw, kind: str) -> tuple:
    obj = _unwrap(raw)
    if obj is None or isinstance(obj, str):
        return [], "failed", "unparseable or error reply"
    status, note = "clean", ""
    if isinstance(obj, dict):
        rows = next((obj[k] for k in ("results", "matches", "hits") if k in obj), None)
        if _is_error_payload(obj):
            status, note = "failed", "error payload"
            rows = rows if isinstance(rows, (list, tuple)) else []
        elif not isinstance(rows, (list, tuple)):
            return [], "failed", "no results key" if rows is None and not any(
                k in obj for k in ("results", "matches", "hits")) else "results is not a list"
        elif obj.get("incomplete") or obj.get("errors"):
            status, note = "partial", "incomplete: %s" % str(obj.get("errors", ""))[:120]
    elif isinstance(obj, (list, tuple)) and obj:
        rows = list(obj)
    else:
        return [], "failed", "empty or non-result reply"
    out = _rows_from(rows, kind)
    if rows and not out and status != "failed":   # non-empty reply, nothing usable in it
        return [], "failed", "no usable rows"
    return out, status, note


def parse_internal_search(raw, *, kind: str = TYPE_VAULT) -> List[dict]:
    """Normalise an internal-search response into `[{locator,title,snippet,score}]`
    (rows only; lane health comes from `classify_lane`)."""
    return classify_lane(raw, kind=kind)[0]


def _rows_from(rows, kind: str) -> List[dict]:
    """Shape raw result records: TYPE_VAULT (`path`, `snippet`) or TYPE_MEMORY (`id`,
    `snippet`/`text`, `score`); a pack-registered parser for the kind runs first."""
    out: List[dict] = []
    pack_parse = H.pack_parser(kind)
    for r in rows:
        if not isinstance(r, dict):
            continue
        if kind == TYPE_MEMORY:
            locator, title, snippet, score = H.memory_fields(r, pack_parse)
        else:  # vault / local / repo
            locator = (r.get("path") or r.get("note_path") or r.get("file")
                       or r.get("locator") or r.get("id") or "")
            title = r.get("title") or os.path.basename(str(locator))
            snippet = (r.get("snippet") or r.get("excerpt") or r.get("text")
                       or r.get("content") or "")
            score = r.get("score", r.get("relevance_score", 0.0))
        if not (locator or snippet):
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        # AV-02: flag a derived-provenance note still in quarantine so the adapter can
        # exclude it from corroboration (audit metadata, not a primary fact).
        derived = _is_quarantined_derived(r) or _is_quarantined_derived(
            {"snippet": snippet, "title": title, "locator": locator})
        out.append({"locator": str(locator), "title": str(title),
                    "snippet": str(snippet), "score": score,
                    "derived_provenance": bool(derived)})
    return out


def _doc_locator(note_path: str, snippet: str) -> str:
    """The §1.6 internal locator: note-path (+ a heading hint if the snippet carries one).
    Kept to the BASENAME-friendly path; the full vault path stays local per §5.2 — callers
    render the basename in any shared view."""
    return note_path or ""


def _minimal_excerpt(snippet: str, max_chars: int) -> str:
    """The minimum snippet that reaches a reviewer (§2.1). The backend already returns a
    short highlight; we hard-cap it and never expand to the whole note."""
    s = re.sub(r"\s+", " ", (snippet or "").strip())
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"


@dataclass
class Lane:
    """One link in the internal fallback chain: a provider label, the source TYPE it
    yields, and the callable the harness bound to the registry-resolved op (or None)."""
    provider: str            # metadata label, e.g. "obsidian" / "smart_connections" / a pack-added label
    kind: str                # TYPE_VAULT | TYPE_MEMORY
    search_fn: Optional[Callable[[str], object]]


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------
class InternalCorpusAdapter(V.VerificationAdapter):
    """Walks the internal fallback chain; the FIRST lane that returns usable sources
    answers. Absent/erroring lanes fall through; the builtin packet/session terminals
    guarantee termination. Emits `internal-vault` / `internal-memory` sources minimally
    (path + excerpt hash + minimum snippet), per §2.1."""

    kind = TYPE_VAULT  # the adapter can also emit internal-memory; per-source `type` is authoritative

    def __init__(self, *, lanes: Sequence[Lane], packet_claims: Optional[Sequence[dict]] = None,
                 read_fn: Optional[Callable[[str], object]] = None, read_provider: str = "",
                 max_results: int = 6, excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
                 retrieval_date: str = ""):
        # only lanes that actually have a callable are live; the order IS the chain.
        self.lanes: List[Lane] = [ln for ln in lanes if ln.search_fn is not None]
        self.all_lane_labels = [ln.provider for ln in lanes]
        self.packet_claims = list(packet_claims or [])
        self.read_fn = read_fn
        self.read_provider = read_provider
        self.max_results = max_results
        self.excerpt_chars = excerpt_chars
        self.retrieval_date = retrieval_date
        self.lanes_tried: List[str] = []   # provider labels actually called this run (manifest/debug)
        self.failed_lanes: dict = {}       # provider -> "partial" | "failed" for the last verify()

    # -- the chain ---------------------------------------------------------
    def verify(self, claim: str, sensitivity: str, context: dict) -> V.VerificationResult:
        self.failed_lanes = {}
        res = self._verify(claim, sensitivity, context)
        if self.failed_lanes:   # one warning, every verdict type: a damaged search is never a clean one
            res.note += " | PARTIAL SEARCH: %s, so this is NOT a clean search" % ", ".join(
                "%s %s" % (k, "FAILED" if v == "failed" else "partial") for k, v in self.failed_lanes.items())
        return res

    def _search_lanes(self, query: str, retrieval_date: str):
        """First lane with rows answers; a lane that errors or returns nothing falls through."""
        for lane in self.lanes:
            self.lanes_tried.append(lane.provider)
            try:
                raw = lane.search_fn(query)
            except Exception:  # noqa: BLE001 — a backend that errors is a FALL-THROUGH, never a crash
                self.failed_lanes[lane.provider] = "failed"
                continue
            rows, status, _note = classify_lane(raw, kind=lane.kind)
            if status != "clean":
                self.failed_lanes[lane.provider] = status
            if not rows:
                continue  # absent / empty / error payload ⇒ next lane
            return self._rows_to_sources(rows, lane, retrieval_date), lane.provider
        return [], ""

    def _verify(self, claim: str, sensitivity: str, context: dict) -> V.VerificationResult:
        context = context or {}
        query = context.get("query") or claim
        retrieval_date = context.get("retrieval_date") or self.retrieval_date or EWA._today()
        expected_numbers = context.get("expected_numbers")
        key_terms = context.get("key_terms") or EWA._key_terms(claim)
        self.lanes_tried = []

        sources, answering_lane = self._search_lanes(query, retrieval_date)

        # builtin terminal: search the PROVIDED source packet (always available in a run)
        if not sources and self.packet_claims:
            self.lanes_tried.append("source_packet")
            rows = self._search_packet(query, key_terms)
            if rows:
                sources = self._rows_to_sources(rows, Lane("source_packet", TYPE_VAULT, None),
                                                retrieval_date)
                answering_lane = "source_packet"

        if not sources:
            # session-memory terminal: nothing in any backend or the packet ⇒ not-found
            self.lanes_tried.append("session_memory")
            tried = ", ".join(self.lanes_tried) or "none"
            return V.VerificationResult(
                claim=claim, verdict="not-found", sources=[],
                note="internal verification found no addressing note (chain tried: %s); claim "
                     "stays needs-review — no source fabricated" % tried)

        # AV-02 (§7.7): split off any quarantined derived-provenance note. It is AUDIT
        # METADATA, NOT a primary fact — it can never corroborate/contradict a claim
        # until a named human promotes it out of quarantine. (A promoted derived note
        # is admitted as authority `internal` upstream, so it is NOT in this bucket.)
        audit_metadata = [s for s in sources if s.authority == DERIVED_PROVENANCE_AUTHORITY]
        primary = [s for s in sources if s.authority != DERIVED_PROVENANCE_AUTHORITY]
        if audit_metadata and not primary:
            return V.VerificationResult(
                claim=claim, verdict="not-found", sources=[],
                note="internal search matched only a QUARANTINED derived-provenance note "
                     "(%d) — audit metadata, NOT a primary fact; not cited until a named "
                     "human promotes it (§7.7/AV-02). Claim stays needs-review."
                     % len(audit_metadata))
        sources = primary

        # optional internal_read confirmation of the top source (raises confidence,
        # never asserts an excerpt it could not read) — degrade silently if unavailable.
        if self.read_fn and sources:
            self._maybe_confirm_by_read(claim, sources[0], expected_numbers, key_terms)

        # stance per source (identical deterministic logic to SV-02)
        corroborating, contradicting = [], []
        for src in sources:
            st = EWA.stance(claim, src.snippet, expected_numbers=expected_numbers, key_terms=key_terms)
            if st == "contradicts":
                contradicting.append(src)
            elif st == "corroborates":
                corroborating.append(src)

        # a disagreeing internal note opens a conflict (preserved, never blended)
        if contradicting:
            best = max(contradicting, key=lambda s: s.confidence)
            return V.VerificationResult(
                claim=claim, verdict="contradicts", sources=[best],
                note="internal %s source (%s) disagrees with the claim — conflict opened, not blended"
                     % (best.type, best.provider or answering_lane))
        if corroborating:
            return V.VerificationResult(
                claim=claim, verdict="corroborates", sources=corroborating,
                note="%d internal source(s) from %s addressed the claim; link-confidence grading "
                     "applied by the pipeline (§2.1 internal-as-sensitive: minimal excerpt + hash only)"
                     % (len(corroborating), answering_lane))
        return V.VerificationResult(
            claim=claim, verdict="not-found", sources=[],
            note="internal search returned notes but none corroborated or contradicted the claim "
                 "(answering lane: %s)" % answering_lane)

    # -- helpers -----------------------------------------------------------
    def _rows_to_sources(self, rows: List[dict], lane: Lane, retrieval_date: str
                         ) -> List[V.VerificationSource]:
        out: List[V.VerificationSource] = []
        for r in rows[: self.max_results]:
            full = r.get("snippet", "") or ""
            locator = _doc_locator(r.get("locator", ""), full)
            # §2.1: store the path + an excerpt HASH (of the full retrieved text) + the
            # MINIMUM snippet — never the whole note.
            # AV-02: a quarantined derived-provenance note is admitted ONLY as audit
            # metadata — authority `derived-provenance`, never `internal` (primary).
            authority = (DERIVED_PROVENANCE_AUTHORITY if r.get("derived_provenance")
                         else "internal")
            src = V.VerificationSource(
                type=lane.kind, url=locator,
                snippet=_minimal_excerpt(full, self.excerpt_chars),
                confidence=max(0.0, min(1.0, float(r.get("score", 0.0)))),
                retrieval_date=retrieval_date, provider=lane.provider,
                authority=authority, excerpt_hash=excerpt_hash(full))
            out.append(src)
        return out

    def _search_packet(self, query: str, key_terms: Sequence[str]) -> List[dict]:
        """Builtin terminal: lexical scan of the PROVIDED source packet's claims/snippets.
        This is the always-available fallback that makes the chain terminate (= v1's
        closed world) when no live backend resolves."""
        terms = [t.lower() for t in (key_terms or []) if t]
        rows = []
        for c in self.packet_claims:
            text = " ".join(str(c.get(k, "")) for k in ("claim", "snippet", "text", "locator"))
            low = text.lower()
            hits = sum(1 for t in terms if t in low)
            if hits:
                rows.append({"locator": c.get("source_id") or c.get("locator") or "packet",
                             "title": c.get("source_id", "packet"),
                             "snippet": text, "score": min(1.0, 0.3 + 0.1 * hits)})
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows

    def _maybe_confirm_by_read(self, claim: str, src: V.VerificationSource,
                               expected_numbers, key_terms) -> None:
        try:
            raw = self.read_fn(src.url)
        except Exception:  # noqa: BLE001 — read unavailable ⇒ snippet-only, never assert a read it couldn't do
            return
        obj = _unwrap(raw)
        if obj is None or _is_error_payload(obj):
            return
        text = obj if isinstance(obj, str) else json.dumps(obj)
        if EWA.stance(claim, text, expected_numbers=expected_numbers, key_terms=key_terms) == "corroborates":
            # record the confirmation in the minimal snippet; the full read text is hashed,
            # not stored (§2.1) — and we bump confidence modestly, never above a fetched-fact ceiling.
            src.snippet = (src.snippet + " [read-confirmed]").strip()
            src.excerpt_hash = excerpt_hash(text)
            src.confidence = max(src.confidence, 0.7)


if __name__ == "__main__":  # tiny self-check against the live shapes observed in-session
    # Obsidian (vault) real shape, wrapped in the MCP {"result": <json-string>} envelope.
    obs = {"result": json.dumps([
        {"path": "90 System/_operating_guide.md", "title": "_operating_guide",
         "score": 0.31, "snippet": "Asymmetric costs justify asymmetric defaults: default to not writing."},
    ])}
    rows = parse_internal_search(obs, kind=TYPE_VAULT)
    print("obsidian rows:", rows)
    # Generic memory-backend shape.
    mem = {"result": json.dumps({"results": [
        {"id": "m-001", "title": "Renewal clause check",
         "snippet": "the uncertain term is verified against the renewal clause", "score": 0.98},
    ], "total_count": 1})}
    print("memory rows:", parse_internal_search(mem, kind=TYPE_MEMORY))
    # error payloads degrade to []
    print("obsidian error ->", parse_internal_search({"result": "Vault not found.\n"}, kind=TYPE_VAULT))
    print("sc error ->", parse_internal_search("Error: Vault path not specified.", kind=TYPE_VAULT))
