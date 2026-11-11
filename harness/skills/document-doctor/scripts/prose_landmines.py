#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — Document Doctor · DD-02 prose landmine detectors
#
# The prose analogue of workbook-doctor's formula-risk scan. Runs over a
# document map (document-map.json from DD-01) — or a raw document, which it maps
# first — and flags the quiet prose failures a reader can't see:
#
#   undated-number          a material figure with no date anywhere near it
#   stale-date              an "as of / as at / updated" date past a staleness window
#   table-as-image          a table or chart shipped as a picture (untraceable)
#   broken-cross-ref        "see Table 4 / Section 3 / Appendix B" with no referent
#   numeric-token-candidate a material number in prose — a claim to source/verify
#   unattributed-quote      a quotation with no nearby attribution / citation
#
# Every finding carries (conventions.md, verbatim): surface=doc, the record's
# `format`, a §1.6 `locator`, a `severity` (must-fix/should-fix/polish), a
# `review_status`, `issue_lifecycle=open`, a `type`, and concrete `evidence`.
# These map onto the §1.7 doc Pretty-But-Wrong taxonomy; nothing is house- or
# client-specific. Output obeys the §3 I/O contract:
#   <out>/prose-landmines.{json,md}
#
#   python prose_landmines.py <map.json|document-file> --out .trust/<run_id> \
#       [--run-id <id>] [--today YYYY-MM-DD] [--stale-days 365] [--format auto]
# =============================================================================
import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import document_map as dm  # noqa: E402

SURFACE = "doc"
SCHEMA_VERSION = "dd-landmines/1.0"

# --- recognisers -------------------------------------------------------------
MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
          r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE_RX = re.compile(
    r"(?:\b(?:19|20)\d{2}\b"                       # 1999 / 2026
    r"|\b\d{4}-\d{2}-\d{2}\b"                      # ISO
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"        # 31/12/2025
    r"|\bF?Y'?\d{2,4}\b"                           # FY25 / FY2025
    r"|\bQ[1-4]\s?'?\d{2,4}\b"                     # Q3 2025
    r"|\b" + MONTHS + r"\.?\s+\d{1,2}?,?\s*(?:19|20)\d{2}\b)", re.I)
ASOF_RX = re.compile(
    r"\b(?:as\s+of|as\s+at|as\s+per|updated(?:\s+on)?|effective(?:\s+as\s+of)?|"
    r"current\s+as\s+of|data\s+as\s+of|valid\s+(?:as\s+of|until))\b[:\s]*?(" + DATE_RX.pattern + ")",
    re.I)
# a "material" number: percent, currency, magnitude word, ratio, or 3+ digit run
NUM_RX = re.compile(
    r"(?:[€$£]\s?\d[\d,.\s]*"                      # €1,200
    r"|\d[\d,.\s]*\s?%"                            # 12.5 %
    r"|\b\d[\d,.]*\s?(?:k|m|bn|mn|million|billion|thousand|x)\b"   # 4.2bn, 3x
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"           # 1,234,567
    r"|\b\d+\.\d+\b)", re.I)
QUOTE_RX = re.compile(r"[“”\"]([^“”\"]{25,})[“”\"]")
ATTRIB_RX = re.compile(
    r"\b(?:said|says|according to|per\s+[A-Z]|stated|wrote|noted|reported|"
    r"\(\s*(?:19|20)\d{2}\s*\)|\[[0-9]+\]|source\s*:|cited|quoted in)\b", re.I)
XREF_RX = re.compile(
    r"\b(?:see\s+)?(table|figure|fig\.?|section|appendix|exhibit|chart|"
    r"schedule|annex)\s+([A-Z]?-?\d+(?:\.\d+)?|[IVXLC]+|[A-Z])\b", re.I)


def _is_dateish_only(numtext):
    """A bare year/date should not also be flagged as an undated number."""
    return bool(DATE_RX.fullmatch(numtext.strip()))


# --- the detectors -----------------------------------------------------------


def scan_paragraphs(doc, today, stale_days):
    findings = []
    fmt = doc.get("format")
    # build referent indices for cross-ref checking
    have_tables = len(doc.get("tables", []))
    have_figs = doc.get("figures", []) or []
    section_paths = [s.get("heading_path", "") for s in doc.get("sections", [])]
    section_blob = " | ".join(section_paths).lower()
    fig_count = len(have_figs)

    for p in doc.get("paragraphs", []):
        text = p.get("text", "") or ""
        loc = p.get("locator")
        low = text.lower()

        # ---- stale 'as of' date ------------------------------------------
        for m in ASOF_RX.finditer(text):
            d = _parse_date(m.group(1))
            if d:
                age = (today - d).days
                if age > stale_days:
                    sev = "must-fix" if age > stale_days * 2 else "should-fix"
                    findings.append(_f(fmt, "stale-date", sev, loc,
                                       "‘%s’ is %d days old (> %d-day window) — "
                                       "may no longer hold" % (m.group(0).strip(), age, stale_days),
                                       review_status="needs-review"))

        # ---- undated number ----------------------------------------------
        nums = [n.group(0).strip() for n in NUM_RX.finditer(text)
                if not _is_dateish_only(n.group(0))]
        has_date = bool(DATE_RX.search(text))
        if nums and not has_date:
            findings.append(_f(fmt, "undated-number", "should-fix", loc,
                               "material figure(s) %s with no date in the same passage"
                               % ", ".join(_uniq(nums)[:4]),
                               review_status="needs-review"))

        # ---- numeric-token-in-prose candidate (claim to source/verify) ---
        for n in _uniq(nums)[:6]:
            findings.append(_f(fmt, "numeric-token-candidate", "polish", loc,
                               "numeric claim ‘%s’ in prose — candidate for sourcing/verification" % n,
                               review_status="needs-review"))

        # ---- unattributed quote ------------------------------------------
        for q in QUOTE_RX.finditer(text):
            window = text[max(0, q.start() - 60): q.end() + 60]
            if not ATTRIB_RX.search(window):
                findings.append(_f(fmt, "unattributed-quote", "should-fix", loc,
                                   "quotation with no nearby attribution/citation: “%s…”"
                                   % q.group(1)[:60], review_status="unsupported"))

        # ---- broken cross-reference --------------------------------------
        for x in XREF_RX.finditer(text):
            kind = x.group(1).lower()
            ref = x.group(2)
            missing = False
            if kind in ("table",):
                missing = _intish(ref) and (_to_int(ref) is None or _to_int(ref) > have_tables or have_tables == 0)
            elif kind in ("figure", "fig", "fig.", "chart", "exhibit"):
                missing = _intish(ref) and (_to_int(ref) is None or _to_int(ref) > fig_count or fig_count == 0)
            elif kind in ("section", "appendix", "annex", "schedule"):
                missing = (ref.lower() not in section_blob) and (kind in ("appendix", "annex", "schedule"))
            if missing:
                findings.append(_f(fmt, "broken-cross-ref", "should-fix", loc,
                                   "‘%s %s’ has no matching %s in the document map"
                                   % (kind, ref, kind), review_status="needs-review"))
    return findings


def scan_figures(doc):
    """table-as-image: a table/chart shipped as a picture (untraceable numbers)."""
    findings = []
    fmt = doc.get("format")
    for fig in doc.get("figures", []):
        alt = (fig.get("alt") or "").lower()
        ref = (fig.get("ref") or "").lower()
        kind = fig.get("kind")
        is_tableish = any(w in (alt + " " + ref) for w in
                          ("table", "chart", "graph", "exhibit", "figure", "plot"))
        # a PDF image-only page or an SVG/image captioned as a table is the classic case
        if is_tableish or (fmt == "pdf" and kind == "image"):
            findings.append(_f(fmt, "table-as-image", "should-fix", fig.get("locator"),
                               "a %s appears to be a table/chart rendered as an image — its "
                               "numbers cannot be traced or re-checked" % (kind or "figure"),
                               review_status="needs-review"))
    # PDF scan with no text already blocks in DD-07; reinforce table-as-image signal
    q = doc.get("pdf_quality") or {}
    if q.get("image_only_pages"):
        findings.append(_f(fmt, "table-as-image", "must-fix", "page:1",
                           "%s image-only page(s): any tables/figures on them are pictures, "
                           "not extractable content" % q.get("image_only_pages"),
                           review_status="needs-review"))
    return findings


# --- small utilities ---------------------------------------------------------


def _f(fmt, type_, severity, locator, evidence, review_status="needs-review"):
    return {"surface": SURFACE, "format": fmt, "type": type_, "severity": severity,
            "review_status": review_status, "issue_lifecycle": "open",
            "locator": locator, "evidence": evidence}


def _uniq(seq):
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _intish(s):
    return bool(re.fullmatch(r"\d+", s.strip()))


def _to_int(s):
    try:
        return int(s)
    except Exception:
        return None


def _parse_date(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return _dt.date(*_dt.datetime.strptime(s, fmt).timetuple()[:3])
        except Exception:
            pass
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        try:
            return _dt.date(int(m.group(0)), 12, 31)  # year-only ⇒ end of year
        except Exception:
            return None
    return None


def load_doc(path, fmt, max_text, coverage_threshold):
    """Accept a document-map.json or a raw document (mapped on the fly)."""
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if "format" in doc and "paragraphs" in doc:
            return doc
    real_fmt = dm.detect_format(path, fmt)
    return dm.build_map(path, real_fmt, max_text, coverage_threshold)


def write_md(report, out_dir):
    L = ["# Prose landmines — `%s` (format: **%s**)" %
         (os.path.basename(report["input"]), report["format"]),
         "", "- **status:** `%s`  ·  **findings:** %d" %
         (report["status"], len(report["findings"])), ""]
    by_type = {}
    for f in report["findings"]:
        by_type.setdefault(f["type"], []).append(f)
    for t, items in sorted(by_type.items()):
        L.append("## %s (%d)" % (t, len(items)))
        for f in items[:50]:
            L.append("- `%s` **%s** — %s _[%s]_" %
                     (f["severity"], f["review_status"], f["evidence"], f["locator"]))
        L.append("")
    with open(os.path.join(out_dir, "prose-landmines.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Document Doctor — DD-02 prose landmines")
    ap.add_argument("input", help="document-map.json OR a .docx/.pdf/.md/.html file")
    ap.add_argument("--out", help=".trust/<run_id> output dir")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--format", default="auto")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: system date)")
    ap.add_argument("--stale-days", type=int, default=365)
    ap.add_argument("--coverage-threshold", type=float, default=0.10)
    ap.add_argument("--max-text", type=int, default=4000)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        print(json.dumps({"status": "failed", "error": "no such file: %s" % args.input}))
        return 2
    today = (_dt.datetime.strptime(args.today, "%Y-%m-%d").date()
             if args.today else _dt.date.today())

    doc = load_doc(args.input, args.format, args.max_text, args.coverage_threshold)
    findings = scan_paragraphs(doc, today, args.stale_days) + scan_figures(doc)

    status = "ok-with-warnings" if findings else "ok"
    report = {"schema": SCHEMA_VERSION, "surface": SURFACE, "format": doc.get("format"),
              "generated": dm._now_iso(), "input": os.path.abspath(args.input),
              "today": today.isoformat(), "stale_days": args.stale_days,
              "status": status,
              "summary": {t: sum(1 for f in findings if f["type"] == t)
                          for t in sorted({f["type"] for f in findings})},
              "findings": findings}

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_landmines")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                       ".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)
    report["run_id"] = run_id
    with open(os.path.join(out_dir, "prose-landmines.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    write_md(report, out_dir)

    if not args.quiet:
        print(json.dumps({"status": status, "format": doc.get("format"),
                          "summary": report["summary"], "out": out_dir}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
