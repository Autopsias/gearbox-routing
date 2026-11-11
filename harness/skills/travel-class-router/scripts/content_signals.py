#!/usr/bin/env python3
"""Local content-signal scanner for the travel-class router (F-03 / S02).

Detects the content signals that RAISE the minimum travel class, entirely with
LOCAL processing — no model, no upload (sensitivity-routing-policy.md §4). The
router combines the content-derived minimum with the user-declared class and
routes to max(declared, content). Downgrades require a logged waiver.

Signals -> minimum travel class they impose:
    formula                       -> travels
    financial-metric              -> travels
    unsupported-number            -> travels   (numeric density; candidate flag — true
                                                 'unsupported' is confirmed against the
                                                 source packet at S03, not by content alone)
    legal-regulatory              -> board-regulatory
    investor-board-client-term    -> board-regulatory
    external-recipient            -> board-regulatory
    stale-source-conflict         -> (not content-detectable; decided from the source
                                       packet — reported as 'needs-source-packet')

Pure stdlib + optional openpyxl (xlsx formulas) / markitdown (rich extraction).
Degrades gracefully: if it cannot extract content it returns scanned=false with a
warning, and the router DEFAULTS UP and asks the human rather than under-routing.

Usage:
    python content_signals.py <file> [--declared internal|travels|board-regulatory]
                                      [--internal-domain example.com]
"""
from __future__ import annotations

import json
import os
import re
import sys

CLASS_ORDER = ["internal", "travels", "board-regulatory"]


def _rank(c: str) -> int:
    return CLASS_ORDER.index(c) if c in CLASS_ORDER else 0


def _max_class(*classes: str) -> str:
    best = "internal"
    for c in classes:
        if _rank(c) > _rank(best):
            best = c
    return best


# --- signal patterns (case-insensitive) ------------------------------------
FINANCIAL = re.compile(
    r"\b(ebitda|revenue|gross margin|net margin|margin|yoy|y/y|cagr|forecast|guidance|"
    r"valuation|net income|cash flow|opex|capex|arr|mrr|gross profit|p&l|p ?and ?l|"
    r"basis points|bps)\b|[€$£]\s?\d|(?<![\w.])\d+(?:\.\d+)?\s?%",
    re.I,
)
LEGAL = re.compile(
    r"\b(pursuant to|hereby|shall not|indemnif\w*|liabilit\w*|warrant\w*|covenant\w*|"
    r"gdpr|sec filing|10-?k|10-?q|8-?k|regulation\w*|regulatory|statutory|privileged|"
    r"confidential(?:ity)?|material non-?public|mnpi|prospectus|disclosure)\b",
    re.I,
)
INVESTOR = re.compile(
    r"\b(board of directors|board pack|board meeting|the board|investor\w*|shareholder\w*|"
    r"due diligence|term sheet|letter of intent|\bloi\b|\bm&a\b|merger|acquisition|"
    r"earnings call|guidance|analyst|client deliverable|for the client)\b",
    re.I,
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)
RECIPIENT_HINT = re.compile(r"\b(to:|cc:|recipient|send to|forward to|external|distribute to)\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\s?%?")


def extract_text(path: str) -> tuple[str, list[str], int]:
    """Return (text, warnings, formula_count). Local only."""
    warnings: list[str] = []
    ext = os.path.splitext(path)[1].lower()
    formula_count = 0
    text_parts: list[str] = []

    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        v = cell.value
                        if v is None:
                            continue
                        if isinstance(v, str):
                            if v.startswith("="):
                                formula_count += 1
                            text_parts.append(v)
                        else:
                            text_parts.append(str(v))
            wb.close()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"openpyxl unavailable/failed ({exc}); trying markitdown")

    if not text_parts:
        # generic path: markitdown if present, else best-effort text decode
        try:
            import subprocess
            out = subprocess.run(["markitdown", path], capture_output=True, text=True, timeout=120)
            if out.returncode == 0 and out.stdout.strip():
                text_parts.append(out.stdout)
            else:
                warnings.append("markitdown produced no output")
        except Exception:
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
                text_parts.append(raw.decode("utf-8", errors="ignore"))
                warnings.append("fell back to raw text decode (structure-blind)")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"could not read file: {exc}")

    text = "\n".join(text_parts)
    # also count '=' lead formulas in any extracted text (e.g. csv/markdown of a sheet)
    if formula_count == 0 and text:
        formula_count = len(re.findall(r"(?:^|[\t,|])\s*=\w", text))
    return text, warnings, formula_count


def scan(path: str, declared: str = "internal", internal_domain: str | None = None) -> dict:
    text, warnings, formula_count = extract_text(path)
    scanned = bool(text.strip()) or formula_count > 0

    signals: dict[str, dict] = {}

    def add(name: str, min_class: str, evidence: str):
        if name not in signals:
            signals[name] = {"min_class": min_class, "evidence": evidence}

    if formula_count > 0:
        add("formula", "travels", f"{formula_count} formula cell(s)")
    if FINANCIAL.search(text):
        add("financial-metric", "travels", FINANCIAL.search(text).group(0))
    if LEGAL.search(text):
        add("legal-regulatory", "board-regulatory", LEGAL.search(text).group(0))
    if INVESTOR.search(text):
        add("investor-board-client-term", "board-regulatory", INVESTOR.search(text).group(0))

    # external recipient: any email whose domain != declared internal domain (or any
    # email at all when no internal domain is known) + textual recipient hints
    ext_hits = []
    for m in EMAIL.finditer(text):
        dom = m.group(1).lower()
        if internal_domain is None or dom != internal_domain.lower():
            ext_hits.append(m.group(0))
    if ext_hits:
        add("external-recipient", "board-regulatory",
            f"{len(ext_hits)} external address(es) e.g. {ext_hits[0]}")
    elif RECIPIENT_HINT.search(text) and internal_domain is None:
        add("external-recipient", "board-regulatory",
            f"recipient hint '{RECIPIENT_HINT.search(text).group(0)}' (no internal domain set to confirm)")

    # numeric density -> candidate unsupported numbers (confirmed at S03 vs source packet)
    nums = NUMBER.findall(text)
    if len(nums) >= 5:
        add("unsupported-number", "travels",
            f"{len(nums)} numeric token(s) — candidate; confirm vs source packet (S03)")

    content_min = "internal"
    for s in signals.values():
        content_min = _max_class(content_min, s["min_class"])

    effective = _max_class(declared, content_min)
    escalated = _rank(effective) > _rank(declared)

    return {
        "file": os.path.basename(path),
        "scanned": scanned,
        "declared_class": declared,
        "content_min_class": content_min,
        "effective_class": effective,
        "escalated": escalated,
        "signals": sorted(signals.keys()),
        "signal_detail": signals,
        "stale_source_conflict": "needs-source-packet",
        "warnings": warnings,
        "note": (
            "content scan is local-only; stale-source conflicts and confirmed unsupported "
            "numbers are decided against the source packet (S03), not from content alone. "
            "If scanned=false, the router defaults UP and asks the human."
        ),
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    declared = "internal"
    internal_domain = None
    for i, a in enumerate(argv):
        if a == "--declared" and i + 1 < len(argv):
            declared = argv[i + 1]
        if a == "--internal-domain" and i + 1 < len(argv):
            internal_domain = argv[i + 1]
    if not args:
        print("usage: content_signals.py <file> [--declared CLASS] [--internal-domain DOMAIN]", file=sys.stderr)
        return 2
    result = scan(args[0], declared=declared, internal_domain=internal_domain)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
