#!/usr/bin/env python3
"""
external_web_adapter.py — SV-02 (v2 Source Verification I)
==========================================================
The **external-web verification adapter** — the concrete backend that turns an
externally-checkable claim into a web query, runs whatever `external_search` /
`external_fetch` operations the F2-04 registry resolved at runtime (Exa is a
*candidate*, **never a hard-coded tool name**), and returns the SV-01 result shape
(`{verdict, sources[], note}`).

It implements `verification.VerificationAdapter`. The harness wires `search_fn` /
`fetch_fn` to the resolved MCP tool (the agent makes the actual call); a fixture injects
*replayed* responses. The adapter itself is pure + deterministic: a given raw response
always yields the same parsed sources, the same stance, and the same verdict.

Hardened per the adversarial review:
  * **#7 source quality.** The adapter never decides "verified" — it returns the sources;
    the §2.4 grading (`verification.grade_corroboration`) is applied by the pipeline, so
    a plausible-but-weak or stale page can corroborate but can NEVER clear a `must-fix`.
    Authority is classified per source (`verification.authority_of`); `retrieval_date` is
    stamped on every source.
  * **#8 egress.** The query reaching `search_fn` is the SANITISED query the pipeline
    built via SV-03 — the adapter never re-derives it from raw claim text.
  * **Fallback never fabricates.** If the external op is unresolved or `search_fn`
    errors/returns nothing, the adapter returns `not-found` with an "external verification
    unavailable" note — never an invented citation (`capability-inventory.md`
    external_search/external_fetch degradation).

It parses the **real Exa MCP response shape confirmed live in-session** (a text block of
`Title:` / `URL:` / `Published:` / `Author:` / `Highlights:` records separated by `---`
for search; a `# title\nURL: …\n<markdown>` block for fetch) AND, defensively, the
canonical structured forms (a list/`{results:[…]}` of dicts) — so it survives an MCP
wrapper that reshapes its output.

Stdlib only.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verification as V  # noqa: E402  (VerificationAdapter / Source / Result / authority_of)


# ---------------------------------------------------------------------------
# Response parsers — built around the REAL Exa shape observed live in-session,
# tolerant of the canonical structured forms.
# ---------------------------------------------------------------------------
def parse_exa_search(raw) -> List[dict]:
    """Normalise an Exa web-search response into `[{title,url,snippet,published,author}]`.

    Accepts (a) the text-rendered block observed live (records separated by '---', each
    with Title:/URL:/Published:/Author:/Highlights:), (b) a list of dicts, or (c) a dict
    with a `results` list. Unknown shapes ⇒ []."""
    if raw is None:
        return []
    if isinstance(raw, dict) and "results" in raw:
        raw = raw["results"]
    if isinstance(raw, (list, tuple)):
        out = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            snippet = r.get("text") or r.get("snippet") or ""
            hl = r.get("highlights")
            if not snippet and hl:
                snippet = " ".join(hl) if isinstance(hl, (list, tuple)) else str(hl)
            out.append({
                "title": r.get("title") or "", "url": r.get("url") or r.get("id") or "",
                "snippet": snippet, "published": r.get("publishedDate") or r.get("published") or "",
                "author": r.get("author") or "",
            })
        return out
    if isinstance(raw, str):
        return _parse_exa_text_block(raw)
    return []


def _parse_exa_text_block(text: str) -> List[dict]:
    records, cur, field, buf = [], {}, None, []

    def flush_field():
        if field is not None and cur is not None:
            cur[field] = (cur.get(field, "") + "\n".join(buf)).strip()

    def flush_record():
        nonlocal cur
        if cur:
            records.append(cur)
        cur = {}

    cur = {}
    for line in text.splitlines():
        if line.strip() == "---":
            flush_field()
            field = None
            buf = []
            flush_record()
            continue
        m = re.match(r"^(Title|URL|Published|Author|Highlights|Text)\s*:\s*(.*)$", line)
        if m:
            flush_field()
            field = m.group(1).lower()
            buf = [m.group(2)]
        else:
            if field is not None:
                buf.append(line)
    flush_field()
    flush_record()

    out = []
    for r in records:
        if not (r.get("url") or r.get("title")):
            continue
        snippet = r.get("highlights") or r.get("text") or ""
        out.append({
            "title": r.get("title", ""), "url": r.get("url", ""),
            "snippet": snippet, "published": r.get("published", ""),
            "author": r.get("author", ""),
        })
    return out


def parse_exa_fetch(raw) -> List[dict]:
    """Normalise an Exa fetch/crawl response into `[{url,text}]`. Accepts the
    `# title\\nURL: …\\n<markdown>` block observed live, a list of dicts, or a dict."""
    if raw is None:
        return []
    if isinstance(raw, dict) and "results" in raw:
        raw = raw["results"]
    if isinstance(raw, (list, tuple)):
        return [{"url": r.get("url") or r.get("id") or "", "text": r.get("text") or r.get("content") or ""}
                for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [{"url": raw.get("url", ""), "text": raw.get("text") or raw.get("content") or ""}]
    if isinstance(raw, str):
        m = re.search(r"^URL:\s*(\S+)", raw, re.MULTILINE)
        return [{"url": m.group(1) if m else "", "text": raw}]
    return []


# ---------------------------------------------------------------------------
# Deterministic stance detection (number / percentage / entity)
# ---------------------------------------------------------------------------
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# a number possibly written with spaced thousands ("299 792 458") or grouped ("299,792,458")
_NUM_RE = re.compile(r"\d[\d  .,]*\d|\d")


def _norm_num(tok: str) -> str:
    return re.sub(r"[^\d]", "", tok)


def _numbers(text: str) -> set:
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        n = _norm_num(m.group(0))
        if n:
            out.add(n)
    return out


def _pcts(text: str) -> set:
    return {m.group(1).replace(",", ".") for m in _PCT_RE.finditer(text or "")}


def stance(claim_text: str, snippet: str, *, expected_numbers: Optional[Sequence[str]] = None,
           key_terms: Optional[Sequence[str]] = None) -> str:
    """Deterministic stance of a source `snippet` toward a `claim`.

    Returns 'corroborates' | 'contradicts' | 'neutral'. Percentage and plain-number
    "slots" are compared independently; an entity/term-only claim corroborates when the
    term appears. Conservative: a plain-number mismatch is NOT auto-contradiction (too
    noisy) — only a percentage slot with a different value contradicts, plus an explicit
    `expected_numbers` mismatch when the snippet clearly states a same-kind figure."""
    claim_pcts = _pcts(claim_text)
    snip_pcts = _pcts(snippet)
    exp_nums = {_norm_num(str(n)) for n in (expected_numbers or [])} | _numbers(claim_text)
    snip_nums = _numbers(snippet)

    # percentage slot
    if claim_pcts and snip_pcts:
        if claim_pcts & snip_pcts:
            return "corroborates"
        return "contradicts"
    # explicit expected number(s)
    if expected_numbers:
        exp = {_norm_num(str(n)) for n in expected_numbers}
        if exp & snip_nums:
            return "corroborates"
        # if the snippet states a same-length figure that differs, treat as contradiction
        same_len_diff = any(len(sn) == len(next(iter(exp))) for sn in snip_nums) if exp else False
        if snip_nums and same_len_diff and not (exp & snip_nums):
            return "contradicts"
    # plain numbers in the claim
    if exp_nums and (exp_nums & snip_nums):
        return "corroborates"
    # entity / key-term match
    for term in (key_terms or []):
        if term and term.lower() in (snippet or "").lower():
            return "corroborates"
    return "neutral"


def _today() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------
class ExternalWebAdapter(V.VerificationAdapter):
    kind = "external-web"

    def __init__(self, *, search_fn: Callable[[str], object],
                 fetch_fn: Optional[Callable[[Sequence[str]], object]] = None,
                 provider_label: str = "web", search_binding: str = "",
                 fetch_binding: Optional[str] = None,
                 source_quality: Optional[dict] = None,
                 max_results: int = 6):
        self.search_fn = search_fn
        self.fetch_fn = fetch_fn
        self.provider_label = provider_label
        self.search_binding = search_binding
        self.fetch_binding = fetch_binding
        self.primary_domains = (source_quality or {}).get("primary_domains", [])
        self.max_results = max_results

    def verify(self, claim: str, sensitivity: str, context: dict) -> V.VerificationResult:
        context = context or {}
        query = context.get("query") or ""
        retrieval_date = context.get("retrieval_date") or _today()
        if not query:
            # the pipeline already gates egress; an empty query here means skip, not fabricate
            return V.VerificationResult(claim=claim, verdict="not-found",
                                        note="no egress-safe query was produced (egress skipped/blocked)")

        # 1. run the resolved external_search op — degrade, never fabricate, on error.
        try:
            raw = self.search_fn(query)
        except Exception as exc:  # noqa: BLE001
            return V.VerificationResult(
                claim=claim, verdict="not-found",
                note="external verification unavailable: external_search errored (%s); claim "
                     "stays needs-review — no citation fabricated" % type(exc).__name__)
        parsed = parse_exa_search(raw)
        if not parsed:
            return V.VerificationResult(
                claim=claim, verdict="not-found",
                note="external verification returned no usable sources; claim stays needs-review")

        # 2. build sources + stance
        expected_numbers = context.get("expected_numbers")
        key_terms = context.get("key_terms") or _key_terms(claim)
        corroborating: List[V.VerificationSource] = []
        contradicting: List[V.VerificationSource] = []
        for r in parsed[: self.max_results]:
            url = r.get("url", "")
            snippet = (r.get("snippet") or "").strip()
            if not url:
                continue
            src = V.VerificationSource(
                type="external-web", url=url, snippet=snippet[:500],
                retrieval_date=retrieval_date, provider=self.provider_label,
                authority=V.authority_of(url, self.primary_domains))
            st = stance(claim, snippet, expected_numbers=expected_numbers, key_terms=key_terms)
            if st == "contradicts":
                contradicting.append(src)
            elif st == "corroborates":
                corroborating.append(src)

        # 3. optional fetch-grade confirmation of the TOP corroborating source (raises
        #    confidence). Absent/failed fetch ⇒ snippet-only, never assert a fetched quote.
        if self.fetch_fn and corroborating:
            self._maybe_confirm_by_fetch(claim, corroborating[0], expected_numbers, key_terms)

        # 4. decide the verdict. A CREDIBLE (>= secondary) dated contradiction opens a
        #    conflict (preserved, never blended, §2.2/§2.4); low-authority contradictions
        #    are noted but not conflict-opening.
        credible_contra = [s for s in contradicting
                           if V.AUTHORITY_SCORE.get(s.authority, 0) >= 1 and s.retrieval_date]
        if credible_contra:
            best = max(credible_contra, key=lambda s: V.AUTHORITY_SCORE.get(s.authority, 0))
            return V.VerificationResult(
                claim=claim, verdict="contradicts", sources=[best],
                note="external %s source disagrees with the claim (%s) — conflict opened, not blended"
                     % (best.authority, _host(best.url)))
        if corroborating:
            return V.VerificationResult(
                claim=claim, verdict="corroborates", sources=corroborating,
                note="%d external source(s) addressed the claim; source-quality grading applied "
                     "by the pipeline (§2.4)" % len(corroborating))
        # nothing addressed it (or only neutral / low-authority-contradiction)
        note = "external search returned sources but none corroborated or credibly contradicted the claim"
        if contradicting:
            note = ("external search found only low-authority disagreement — recorded, not "
                    "conflict-opening; claim stays needs-review")
        return V.VerificationResult(claim=claim, verdict="not-found", sources=[], note=note)

    # -- fetch confirmation -------------------------------------------------
    def _maybe_confirm_by_fetch(self, claim: str, src: V.VerificationSource,
                                expected_numbers, key_terms) -> None:
        try:
            raw = self.fetch_fn([src.url])
        except Exception:  # noqa: BLE001
            return  # fetch unavailable ⇒ snippet-only; never assert a fetched quote
        pages = parse_exa_fetch(raw)
        if not pages:
            return
        full = pages[0].get("text", "")
        if stance(claim, full, expected_numbers=expected_numbers, key_terms=key_terms) == "corroborates":
            src.snippet = (src.snippet + " [fetch-confirmed]").strip()
            # a fetch confirmation is recorded in the snippet; authority/grading unchanged.


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "", re.IGNORECASE)
    return (m.group(1) if m else url or "").lower()


_STOP = {"the", "a", "an", "of", "in", "on", "to", "is", "are", "was", "were", "for",
         "and", "or", "by", "with", "at", "as", "that", "this", "it", "be", "per",
         "from", "grew", "was", "will", "has", "had", "have", "than", "into"}


def _key_terms(claim: str) -> List[str]:
    """Salient multi-char alpha terms from a claim (for entity/term corroboration)."""
    toks = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", claim or "")
    return [t for t in toks if t.lower() not in _STOP][:6]


if __name__ == "__main__":  # tiny self-check against the live shape observed in-session
    sample = (
        "Title: - metre - BIPM\n"
        "URL: https://www.bipm.org/en/si-base-units/metre\n"
        "Published: N/A\n"
        "Author: N/A\n"
        "Highlights: the speed of light in vacuum c to be 299 792 458 when expressed in m s-1.\n"
        "---\n"
    )
    parsed = parse_exa_search(sample)
    print("parsed:", parsed)
    print("stance:", stance("The speed of light is 299792458 m/s", parsed[0]["snippet"],
                            expected_numbers=["299792458"]))
