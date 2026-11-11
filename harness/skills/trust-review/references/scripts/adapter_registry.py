#!/usr/bin/env python3
"""
adapter_registry.py — F2-04 (v2 Foundation)
============================================
Capability-by-operation adapter registry for the Office Trust Layer.

Why this exists (review findings #2 / #3): the v2 surfaces depend on backends that
**vary by machine and by session** — an external web search/fetch provider (e.g. Exa)
and an internal corpus (Obsidian, Smart Connections, a plain Markdown folder named by
TRUST_CORPUS_DIR, repo docs; an owner's extra tools join through provider packs). The plan must NOT assume any of these is present or callable, and
must NEVER hard-code a tool name. This module maps the *operations* the harness needs
(`conventions.md` §2.2, `capability-inventory.md`) onto whatever tools are actually
connected **at run time**, binding each operation to the FIRST available provider in that
operation's ordered fallback chain.

Contract anchors (this module redefines nothing — see `conventions.md` §6):
  - Operations + ordered fallback chains + graceful-degradation behaviour are the contract
    in `references/capability-inventory.md` (F2-02). This module is the runtime binder for
    that contract.
  - Source types / provider-as-metadata: `conventions.md` §2.2. `provider` is metadata only;
    which provider serves a `source_type` is decided here, at runtime, never in the schema.
  - The resolved bindings are written into the run manifest (`sensitivity-routing-policy.md`
    §9, manifest key `adapter_bindings`) so every run states what backed it.

Design properties:
  - **Pure capability mapping.** No live MCP calls here; the registry is given the list of
    available MCP tool names (probed from the environment by the caller) plus the set of
    available local capabilities, and resolves deterministically. This makes it testable with
    an injected tool list (`fixtures/fixture_adapter_registry.py` injects full/partial/empty).
  - **`resolve(operation) -> Adapter | None`.** `None` drives the *documented* degradation
    (`capability-inventory.md`), never a crash. Operations with no local terminal
    (`external_search`, `external_fetch`, `pdf_read`) can resolve to `None`; operations with a
    builtin terminal (the internal chain, `docx/html/md` readers, `docx_comment_write`) never do.
  - **Additive / no behavioural code.** Nothing in v1 imports this; it is new v2 infrastructure.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Provider kinds
# ---------------------------------------------------------------------------
#   "mcp"     — a connected MCP tool; available iff a probed tool name matches its
#               token patterns. The bound tool's name+version is recorded.
#   "local"   — a local capability (a python library, a CLI binary, a configured vault
#               path, an on-disk skill). Available iff its capability id is in `local_caps`.
#   "builtin" — an always-on terminal fallback that needs nothing external (stdlib parsing,
#               the provided source packet, session memory, an appendix+sidecar). Always
#               available, so any chain that ends in a builtin can never resolve to None.
KIND_MCP = "mcp"
KIND_LOCAL = "local"
KIND_BUILTIN = "builtin"

# A token-group matcher: provider matches a tool name iff, for EVERY group, at least one
# alternative substring appears in the (lower-cased) tool name. Tool names may be either the
# short form (`web_search_exa`) or the fully-qualified MCP form (`mcp__<server>__web_search_exa`);
# substring matching handles both, and is deliberately conservative so an internal tool can
# never bind an external operation.
TokenGroups = Tuple[Tuple[str, ...], ...]


@dataclass(frozen=True)
class Provider:
    """One candidate provider in an operation's ordered fallback chain."""
    id: str
    kind: str                       # KIND_MCP | KIND_LOCAL | KIND_BUILTIN
    match: TokenGroups = ()         # token groups for KIND_MCP; cap-id is `id` for KIND_LOCAL
    note: str = ""                  # what this provider does / why it is in the chain
    preferred: bool = False         # a CO-EQUAL first-choice rung, not a fallback. Only one provider
                                    # can sit at index 0, but a capability can legitimately ship in
                                    # several runtime flavours (e.g. an MCP tool for a client with
                                    # no shell and a CLI for shell runtimes). Resolving to a `preferred`
                                    # rung is NOT a degradation and must not lower stated coverage.


@dataclass(frozen=True)
class Operation:
    """An operation the harness needs, with its ordered fallback chain + degradation note."""
    name: str
    purpose: str
    providers: Tuple[Provider, ...]
    degradation: str                # the documented behaviour when this op resolves to None / a fallback
    has_terminal: bool              # True iff the chain ends in an always-on builtin (=> never None)


@dataclass
class Adapter:
    """The bound provider for an operation in a given environment."""
    operation: str
    provider_id: str
    kind: str
    tool: Optional[str] = None      # the matched MCP tool name (KIND_MCP only)
    version: str = "unknown"
    degraded: bool = False          # True if a non-preferred / capability-reducing provider was used
    note: str = ""

    def binding(self) -> str:
        """The manifest binding string: 'tool@version' for an MCP tool, else 'fallback:<id>'."""
        if self.kind == KIND_MCP and self.tool:
            return f"{self.tool}@{self.version}"
        return f"fallback:{self.provider_id}"


# ---------------------------------------------------------------------------
# The operation table — mirrors references/capability-inventory.md (F2-02).
# Order within `providers` IS the ordered fallback chain. NEVER hard-code a single tool:
# every MCP provider is a pattern over candidate tool names discovered at runtime.
# ---------------------------------------------------------------------------

# Source-verification operations (conventions.md §2.2)
_EXTERNAL_SEARCH = Operation(
    name="external_search",
    purpose="Find public web sources that address a claim",
    providers=(
        Provider("exa_web_search", KIND_MCP, (("exa",), ("search",)),
                 "an Exa-style web-search op, e.g. web_search_exa"),
        Provider("web_search_generic", KIND_MCP,
                 (("web_search", "websearch", "search_web", "brave_search", "tavily",
                   "serpapi", "serp_", "google_search", "bing_search", "duckduckgo", "you_search"),),
                 "any other registered web-search tool"),
    ),
    degradation="No external corroboration; eligible claims recorded not-checked-budget / not-found; "
                "a verdict is NEVER fabricated; verification proceeds internal-only.",
    has_terminal=False,
)

_EXTERNAL_FETCH = Operation(
    name="external_fetch",
    purpose="Retrieve the full text of a specific URL for quote/figure checking",
    providers=(
        Provider("exa_fetch", KIND_MCP, (("exa",), ("fetch", "crawl")),
                 "an Exa-style crawl/fetch op, e.g. web_fetch_exa / crawling_exa"),
        Provider("fetch_generic", KIND_MCP,
                 (("web_fetch", "fetch_url", "url_fetch", "fetch_page", "http_get",
                   "crawl", "scrape", "readability", "read_url"),),
                 "a generic fetch tool"),
    ),
    degradation="Fetch-grade evidence unavailable; fall back to search snippets, lower the link "
                "confidence, and NEVER assert a fetched quote it could not retrieve.",
    has_terminal=False,
)

_INTERNAL_SEARCH = Operation(
    name="internal_search",
    purpose="Lexical search of the user's own corpus",
    providers=(
        Provider("obsidian", KIND_MCP, (("obsidian",), ("search", "query", "find", "tasks", "running_todo")),
                 "an Obsidian search/query op"),
        Provider("smart_connections", KIND_MCP,
                 (("smart-connections", "smart_connections", "smartconnections"),),
                 "Smart Connections"),
        Provider("local_vault_rg", KIND_LOCAL, note="ripgrep/markdown over a configured local vault path"),
        Provider("repo_docs", KIND_LOCAL, note="repo / project docs on disk"),
        Provider("source_packet", KIND_BUILTIN, note="the provided source packet (always available in a run)"),
        Provider("session_memory", KIND_BUILTIN, note="session memory — the last-resort terminal"),
    ),
    degradation="Each backend tried in order; on all-absent, search the provided source packet, then "
                "session memory; if still nothing, not-found — never a crash.",
    has_terminal=True,
)

_INTERNAL_SEMANTIC = Operation(
    name="internal_semantic_search",
    purpose="Semantic / embedding search of the user's corpus",
    providers=(
        Provider("smart_connections", KIND_MCP,
                 (("smart-connections", "smart_connections", "smartconnections"),),
                 "Smart Connections (semantic)"),
        Provider("obsidian_semantic", KIND_MCP,
                 (("obsidian",), ("semantic", "embedding", "neighbor", "neighborhood", "related", "connect")),
                 "an Obsidian semantic op"),
        Provider("lexical_fallback", KIND_BUILTIN,
                 note="degrade to lexical internal_search; reduced recall recorded in the manifest"),
    ),
    degradation="Degrade to lexical internal_search; record reduced recall in the manifest; "
                "NEVER claim semantic coverage it did not run.",
    has_terminal=True,
)

_INTERNAL_READ = Operation(
    name="internal_read",
    purpose="Read a specific internal note/record by path/id to resolve a locator",
    providers=(
        Provider("obsidian_read", KIND_MCP, (("obsidian",), ("read", "get", "retrieve", "note", "file")),
                 "an Obsidian read op"),
        Provider("local_vault_read", KIND_LOCAL, note="a local-vault file read"),
        Provider("packet_excerpt", KIND_BUILTIN, note="the approved source-packet excerpt — terminal"),
    ),
    degradation="If the note is unreachable, the internal source stays needs-review with its locator "
                "recorded — never invented.",
    has_terminal=True,
)

# Document reader / writer operations (one capability per format, conventions.md §1.6)
_DOCX_READ = Operation(
    name="docx_read",
    purpose="Extract text + headings + tables + comments from .docx",
    providers=(
        Provider("python_docx", KIND_LOCAL, note="python-docx"),
        Provider("markitdown", KIND_LOCAL, note="markitdown"),
        Provider("raw_xml_strip", KIND_BUILTIN, note="stdlib unzip + document.xml strip — terminal"),
    ),
    degradation="On total failure the doc is blocked (unreadable) — never a silent empty map.",
    has_terminal=True,
)

_PDF_READ = Operation(
    name="pdf_read",
    purpose="Extract text/tables/figures AND report extraction quality (coverage, image-only pages, "
            "table confidence, OCR availability)",
    providers=(
        Provider("pdf_skill", KIND_LOCAL, note="the pdf skill"),
        Provider("markitdown", KIND_LOCAL, note="markitdown (text)"),
        Provider("ocr", KIND_LOCAL, note="an OCR engine when present"),
    ),
    degradation="Below-threshold coverage + no OCR => blocked / not-ready + a human-review item "
                "(DOC-PBW-01); a partial extract is NEVER presented as complete.",
    has_terminal=False,
)

_HTML_READ = Operation(
    name="html_read",
    purpose="Extract visible-text / semantic-DOM / raw, scoping hidden + agent-only blocks separately",
    providers=(
        Provider("dom_reader", KIND_LOCAL, note="a DOM-aware reader"),
        Provider("markitdown", KIND_LOCAL, note="markitdown"),
        Provider("raw_tag_strip", KIND_BUILTIN, note="raw tag strip — terminal"),
    ),
    degradation="Record which mode ran; hidden / agent-only DOM (e.g. <details>) counted separately "
                "(DOC-PBW-05), never silently merged.",
    has_terminal=True,
)

_MD_READ = Operation(
    name="md_read",
    purpose="Parse Markdown headings / tables / links / citations",
    providers=(
        Provider("md_parser", KIND_LOCAL, note="a markdown parser"),
        Provider("plain_text", KIND_BUILTIN, note="plain-text read — terminal, reduced structure fidelity"),
    ),
    degradation="Plain-text fallback preserves content at reduced structure fidelity (recorded) — "
                "never a crash.",
    has_terminal=True,
)

_DOCX_COMMENT_WRITE = Operation(
    name="docx_comment_write",
    purpose="Write anchored Word comments that round-trip through Word / LibreOffice",
    providers=(
        Provider("python_docx_comments", KIND_LOCAL,
                 note="the python-docx comment API where supported (dd-08 spike proves round-trip)"),
        Provider("appendix_sidecar", KIND_BUILTIN,
                 note="appendix + a .trust/<run>/ sidecar — terminal, so no evidence is lost"),
    ),
    degradation="If anchored comments don't round-trip, fall back to appendix + sidecar so NO evidence "
                "is lost.",
    has_terminal=True,
)

# v3 operations (conventions.md §7; office-trust-layer-v3-review-disposition.md).
# Registered now so the contract precedes the implementation (same rule as the v2 doc
# readers); each is DEFERRED-FUNCTIONAL in capability-inventory.md until the session that
# first depends on it proves it live (vault_write: av-02/S09; external_reviewer: pt-02/S08).
_VAULT_WRITE = Operation(
    name="vault_write",
    purpose="Two-phase write-back of a derived-provenance note to the user's corpus "
            "(quarantine -> promoted only after human approval, conventions.md §7.7)",
    providers=(
        Provider("obsidian_write", KIND_MCP,
                 (("obsidian",), ("create", "write", "append", "log", "init_vault", "from_template")),
                 "an Obsidian create/write/append op"),
        Provider("local_vault_write", KIND_LOCAL, note="a local-vault file write under a configured vault path"),
        Provider("trust_quarantine", KIND_BUILTIN,
                 note=".trust/<run>/ quarantine sidecar — terminal; the derived note stays "
                      "quarantined and is NEVER auto-promoted (the av-02 two-phase safety property)"),
    ),
    degradation="No vault backend => the derived note stays in the .trust quarantine sidecar and is "
                "NEVER auto-promoted into the corpus; never a silent corpus write, never a fabricated "
                "promotion.",
    has_terminal=True,
)

_EXTERNAL_REVIEWER = Operation(
    name="external_reviewer",
    purpose="Provide an independent reviewer whose independence level is gated by travel class "
            "(conventions.md §7.2): cross-vendor > named-human-SME > same-family",
    providers=(
        Provider("cross_vendor_model", KIND_LOCAL,
                 note="a cross-vendor (different-vendor) reviewer model reachable via the Agent "
                      "tool — the strongest automated independence"),
        Provider("same_family_agent", KIND_BUILTIN,
                 note="the Agent same-family model-split (always available) — triggers the "
                      "same-family-reviewer cap (§7.1/§7.2); Board/Regulatory => blocked unless "
                      "a named-owner signoff is recorded"),
        Provider("named_human_sme", KIND_BUILTIN,
                 note="a named human SME signoff record — the human-gate terminal"),
    ),
    degradation="No cross-vendor reviewer => degrade to the same-family Agent split and record the "
                "same-family-reviewer cap; on Board/Regulatory this caps at blocked unless a named-owner "
                "signoff is recorded (§7.2); independence is never overstated.",
    has_terminal=True,
)

OPERATIONS: Dict[str, Operation] = {
    op.name: op
    for op in (
        _EXTERNAL_SEARCH, _EXTERNAL_FETCH,
        _INTERNAL_SEARCH, _INTERNAL_SEMANTIC, _INTERNAL_READ,
        _DOCX_READ, _PDF_READ, _HTML_READ, _MD_READ, _DOCX_COMMENT_WRITE,
        _VAULT_WRITE, _EXTERNAL_REVIEWER,
    )
}

# Operations that have NO always-on terminal — these can legitimately resolve to None,
# which drives the documented degradation rather than a crash.
NO_TERMINAL_OPERATIONS = tuple(name for name, op in OPERATIONS.items() if not op.has_terminal)

# Provider packs (contract + loader: pack_loader.py). State lives here so callers read it off the registry.
PACKS_LOADED: List[str] = []
PACKS_FAILED: List[Tuple[str, str]] = []
PACK_HOOKS: Dict[str, object] = {"parsers": {}, "labels": [], "theme": {}}
_PACK_MODULES: list = []


def load_packs() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pack_loader
    pack_loader.load(sys.modules[__name__])


def _pack_local_caps() -> List[str]:
    import pack_loader
    return pack_loader.local_caps(sys.modules[__name__])


def _corpus_caps() -> List[str]:
    """Capabilities switched on by TRUST_CORPUS_DIR naming a readable folder (see local_corpus.py)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import local_corpus
    except ImportError:
        return []
    if not (local_corpus.corpus_dir() or local_corpus.configured_error()):   # bad config still binds, so it fails loudly
        return []
    return ["local_vault_rg", "local_vault_read"] + (["local_vault_write"] if local_corpus.can_write() else [])


def _tool_matches(tool_name: str, groups: TokenGroups) -> bool:
    """True iff, for every group, at least one alternative substring is in `tool_name`."""
    tl = tool_name.lower()
    return all(any(alt in tl for alt in group) for group in groups)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
class AdapterRegistry:
    """
    Binds each operation to the first available provider in its fallback chain, given the
    environment's available MCP tool names and local capabilities.

    Construct with explicit lists to test deterministically (the fixture injects full / partial
    / empty), or call `AdapterRegistry.probe()` to build one from the live environment.
    """

    def __init__(
        self,
        available_tools: Optional[Iterable[str]] = None,
        local_caps: Optional[Iterable[str]] = None,
        preferred_caps: Optional[Iterable[str]] = None,
    ) -> None:
        # MCP tool names present in this environment (probed by the caller / injected in tests).
        self.available_tools: List[str] = sorted(set(available_tools or []))
        # Local capability ids present (python_docx, markitdown, pdf_skill, ocr, dom_reader,
        # md_parser, rg/local-vault, repo_docs, python_docx_comments). Builtins are always on
        # and are NOT listed here.
        self.local_caps: set = set(local_caps or [])
        # Local caps the adopter chose on purpose (TRUST_CORPUS_DIR): bound as preferred, not degraded.
        self.preferred_caps: set = set(preferred_caps or [])
        self._cache: Dict[str, Optional[Adapter]] = {}

    # -- discovery ---------------------------------------------------------
    @classmethod
    def probe(cls, available_tools: Optional[Iterable[str]] = None,
              local_caps: Optional[Iterable[str]] = None) -> "AdapterRegistry":
        """
        Build a registry from the live environment.

        `available_tools` — the MCP tool names the harness can see this run. The *mechanism*
        for enumerating live MCP tools is environment-specific and supplied by the caller; when
        omitted, a best-effort probe reads `OTL_AVAILABLE_TOOLS` (comma/space-separated) so the
        registry is still runnable headless. It NEVER asserts a tool it did not observe.
        `local_caps` — when omitted, best-effort local detection runs (import/binary checks).
        """
        if available_tools is None:
            available_tools = _probe_env_tools()
        if local_caps is None:
            local_caps = detect_local_caps()
        return cls(available_tools=available_tools, local_caps=local_caps, preferred_caps=_corpus_caps())

    # -- resolution --------------------------------------------------------
    def _provider_available(self, prov: Provider) -> Optional[str]:
        """Return the bound tool name (for MCP) / provider id (local/builtin) if available, else None."""
        if prov.kind == KIND_BUILTIN:
            return prov.id
        if prov.kind == KIND_LOCAL:
            return prov.id if prov.id in self.local_caps else None
        if prov.kind == KIND_MCP:
            for tool in self.available_tools:
                if _tool_matches(tool, prov.match):
                    return tool
            return None
        return None

    def resolve(self, operation: str) -> Optional[Adapter]:
        """
        Bind `operation` to the first available provider in its fallback chain.

        Returns an `Adapter`, or `None` when no provider is available (only possible for
        NO_TERMINAL_OPERATIONS). `None` is the signal for the operation's documented degradation;
        it is never an error.
        """
        if operation not in OPERATIONS:
            raise KeyError(f"unknown operation {operation!r}; known: {sorted(OPERATIONS)}")
        if operation in self._cache:
            return self._cache[operation]

        op = OPERATIONS[operation]
        adapter: Optional[Adapter] = None
        for idx, prov in enumerate(op.providers):
            bound = self._provider_available(prov)
            if bound is None:
                continue
            adapter = Adapter(
                operation=operation,
                provider_id=prov.id,
                kind=prov.kind,
                tool=bound if prov.kind == KIND_MCP else None,
                version="unknown",
                # degraded iff we fell past a higher-priority provider, or this is a
                # capability-reducing terminal (a non-mcp fallback below the preferred mcp op).
                # A `preferred` rung is a co-equal first choice, never a degradation - see
                # Provider.preferred.
                degraded=(idx > 0 and not prov.preferred and prov.id not in self.preferred_caps),
                note=prov.note,
            )
            break
        self._cache[operation] = adapter
        return adapter

    def resolve_all(self) -> Dict[str, Optional[Adapter]]:
        """Resolve every known operation → Adapter|None."""
        return {name: self.resolve(name) for name in OPERATIONS}

    # -- manifest ----------------------------------------------------------
    def manifest_record(self) -> Dict[str, object]:
        """
        The `adapter_bindings` block to merge into `.trust/<run_id>/manifest.json`
        (sensitivity-routing-policy.md §9). Every operation states what backed it:
        an MCP `tool@version`, a `fallback:<id>`, or `unbound` (its degradation applies).
        """
        bindings: Dict[str, str] = {}
        degraded_ops: List[str] = []
        for name in OPERATIONS:
            adapter = self.resolve(name)
            if adapter is None:
                bindings[name] = "unbound"
                degraded_ops.append(name)
            else:
                bindings[name] = adapter.binding()
                # "degraded" = a non-preferred provider was used (fell past a higher one in the
                # chain). A primary provider — MCP or local — is not degraded just for being local.
                if adapter.degraded:
                    degraded_ops.append(name)
        return {
            "adapter_bindings": {
                "_doc": (
                    "F2-04 adapter registry — which provider backed each operation this run. "
                    "provider is metadata only (conventions.md §2.2); resolution is runtime + "
                    "degrades gracefully (capability-inventory.md). 'tool@version' = a live MCP "
                    "tool; 'fallback:<id>' = a local/builtin fallback; 'unbound' = no provider, "
                    "the operation's documented degradation applies (NEVER a fabricated verdict)."
                ),
                "probed_tool_count": len(self.available_tools),
                "local_caps": sorted(self.local_caps),
                "bindings": bindings,
                "degraded_operations": degraded_ops,
            }
        }


# ---------------------------------------------------------------------------
# PT-02 (S08) — reviewer independence resolution (conventions §7.2).
# The `external_reviewer` operation (registered above, DEFERRED-FUNCTIONAL since S01)
# becomes FUNCTIONAL here: resolving it binds the reviewer's INDEPENDENCE level, which
# trust-review (loop_controller) then gates by travel class. The mapping is kept next to
# the operation definition so "the reviewer is registry-resolved" is literal — the loop
# never hard-codes a level, it asks the registry which provider backed the review.
# provider_id -> §7.2 independence level (verbatim enum tokens):
REVIEWER_PROVIDER_INDEPENDENCE = {
    "cross_vendor_model": "cross-vendor",     # a different-vendor reviewer model
    "named_human_sme": "named-human-SME",     # a named human SME signoff record
    "same_family_agent": "same-family",       # the always-on Agent same-family split
}


def resolve_reviewer(registry: "AdapterRegistry") -> Tuple[Optional[Adapter], str, str]:
    """Resolve the `external_reviewer` operation and return (adapter, provider_id, level).

    The chain is cross_vendor_model (local) -> same_family_agent (builtin terminal) ->
    named_human_sme (builtin); `external_reviewer` has an always-on terminal so this never
    returns None. The level is the §7.2 enum token for the bound provider — the loop gates
    that level by travel class, never overstating independence."""
    ad = registry.resolve("external_reviewer")
    pid = ad.provider_id if ad else "same_family_agent"
    level = REVIEWER_PROVIDER_INDEPENDENCE.get(pid, "same-family")
    return ad, pid, level


# ---------------------------------------------------------------------------
# Best-effort live probing (used only when the caller does not inject lists).
# Kept tiny + side-effect-free; the fixture never relies on these.
# ---------------------------------------------------------------------------
def _probe_env_tools() -> List[str]:
    """Read available MCP tool names from OTL_AVAILABLE_TOOLS (comma/space separated)."""
    raw = os.environ.get("OTL_AVAILABLE_TOOLS", "")
    return [t for t in raw.replace(",", " ").split() if t]


def detect_local_caps() -> List[str]:
    """Best-effort detection of local capabilities by import/availability. Conservative."""
    caps: List[str] = []
    import importlib.util as _ilu
    import shutil as _shutil

    def _has_module(name: str) -> bool:
        try:
            return _ilu.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    if _has_module("docx"):
        caps += ["python_docx", "python_docx_comments"]
    if _shutil.which("markitdown") or _has_module("markitdown"):
        caps.append("markitdown")
    if _has_module("markdown") or _has_module("markdown_it") or _has_module("mistune"):
        caps.append("md_parser")
    if _has_module("bs4") or _has_module("lxml") or _has_module("html5lib"):
        caps.append("dom_reader")
    if _has_module("pypdf") or _has_module("pdfplumber") or _has_module("fitz") or _has_module("pdfminer"):
        caps.append("pdf_skill")
    if _shutil.which("tesseract") or _has_module("pytesseract"):
        caps.append("ocr")
    caps += _corpus_caps() + _pack_local_caps()
    return caps


# ---------------------------------------------------------------------------
# CLI: print the resolution + manifest record for an injected/probed environment.
# ---------------------------------------------------------------------------
def _main(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Office Trust Layer adapter registry (F2-04).")
    ap.add_argument("--tools", default=None,
                    help="comma/space-separated MCP tool names (default: probe OTL_AVAILABLE_TOOLS)")
    ap.add_argument("--local-caps", default=None,
                    help="comma/space-separated local capability ids (default: best-effort detect)")
    ap.add_argument("--manifest", action="store_true", help="print only the manifest adapter_bindings block")
    ap.add_argument("--resolve", action="store_true",
                    help="print each operation's bound provider, degraded flag and the packs loaded")
    ap.add_argument("--json", action="store_true", help="with --resolve: machine-readable output")
    args = ap.parse_args(argv)

    tools = None if args.tools is None else [t for t in args.tools.replace(",", " ").split() if t]
    caps = None if args.local_caps is None else [c for c in args.local_caps.replace(",", " ").split() if c]
    reg = AdapterRegistry.probe(available_tools=tools, local_caps=caps)

    if args.resolve:
        rows = {n: (None if a is None else {"provider": a.provider_id, "degraded": a.degraded})
                for n, a in reg.resolve_all().items()}
        info = {"bindings": rows, "packs_loaded": PACKS_LOADED,
                "packs_failed": [{"path": f, "error": e} for f, e in PACKS_FAILED]}
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for n, r in rows.items():
                print(f"{n:26s} {'unbound' if r is None else r['provider']}"
                      f"{' (degraded)' if r and r['degraded'] else ''}")
            print("packs loaded:", ", ".join(os.path.basename(x) for x in PACKS_LOADED) or "none")
            for f, e in PACKS_FAILED:
                print("pack FAILED:", os.path.basename(f), e)
        return 0

    if args.manifest:
        print(json.dumps(reg.manifest_record(), indent=2))
        return 0

    out = {
        "available_tools": reg.available_tools,
        "local_caps": sorted(reg.local_caps),
        "resolution": {
            name: (None if ad is None else {
                "provider_id": ad.provider_id, "kind": ad.kind,
                "tool": ad.tool, "binding": ad.binding(), "degraded": ad.degraded,
            })
            for name, ad in reg.resolve_all().items()
        },
        "manifest": reg.manifest_record(),
    }
    print(json.dumps(out, indent=2))
    return 0


load_packs()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
