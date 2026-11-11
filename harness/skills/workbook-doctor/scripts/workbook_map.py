#!/usr/bin/env python3
"""
Office Trust Layer — Workbook Doctor · WD-01: workbook map + data-structure review.

INSPECT ONLY. Reads a workbook with openpyxl (formulas kept, data_only=False),
classifies every sheet, and flags structural landmines that break the trail or
mislead a reader:

  merged_cells · blank_rows_in_table · duplicate_rows · number_as_text ·
  date_as_text · mixed_units · unclear_headers · source_without_id_date

Emits, per the conventions.md I/O contract:
  <out>/workbook-map.json   (machine — findings keyed by severity/review_status/issue_lifecycle)
  <out>/workbook-map.md     (human memo)

Enums (severity, review_status, issue_lifecycle, status) come from
references/conventions.md — used verbatim, never redefined here.

Usage:
  python workbook_map.py <workbook.xlsx> [--out DIR] [--source-packet PACKET.json] [--run-id ID]
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refs as _refs  # noqa: E402

# ---- value typing helpers ----------------------------------------------------
_NUM_TEXT_RE = re.compile(r"^[\s]*[-+(]?\s*[€$£¥]?\s*\d{1,3}(?:[ ,. ]\d{3})*(?:[.,]\d+)?\s*[%)]?\s*$")
_PLAIN_NUM_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
_DATE_TEXT_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}"                       # 2026-03-31, 31/03/2026
    r"|(?:Q[1-4])[ \-/]?(?:FY)?\d{2,4}"                        # Q3 FY26, Q3-2026
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ \-]?\d{2,4}"  # Mar-26
    r")\s*$",
    re.IGNORECASE,
)
_UNIT_PATTERNS = [
    ("percent", re.compile(r"%\s*$")),
    ("eur", re.compile(r"^\s*€")),
    ("usd", re.compile(r"^\s*\$")),
    ("gbp", re.compile(r"^\s*£")),
    ("thousands", re.compile(r"\d\s*[kK]\s*$")),
    ("millions", re.compile(r"\d\s*(?:[mM]|mn|M€|€M)\s*$")),
    ("billions", re.compile(r"\d\s*(?:bn|[bB])\s*$")),
    ("multiple", re.compile(r"\d\s*[xX]\s*$")),
    ("bps", re.compile(r"\d\s*bps\s*$", re.IGNORECASE)),
]
_GENERIC_HEADER_RE = re.compile(r"^(?:column|col|field|unnamed|var|untitled)\s*\d*$|^column\d+$", re.IGNORECASE)


def is_formula(v):
    return isinstance(v, str) and v.startswith("=")


def looks_number_text(v):
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s or is_formula(s):
        return False
    core = s.replace("€", "").replace("$", "").replace("£", "").replace("%", "")
    core = core.replace(" ", "").replace(" ", "").replace(",", "")
    if _PLAIN_NUM_RE.match(core):
        # a bare unit-less number stored as text, OR currency/percent text
        return True
    return bool(_NUM_TEXT_RE.match(s))


def looks_date_text(v):
    return isinstance(v, str) and not is_formula(v) and bool(_DATE_TEXT_RE.match(v.strip()))


def unit_signature(v):
    """Return a coarse unit tag for a value, or None if no explicit unit marker."""
    if isinstance(v, (int, float)):
        return "number"
    if not isinstance(v, str) or is_formula(v):
        return None
    s = v.strip()
    for tag, pat in _UNIT_PATTERNS:
        if pat.search(s):
            return tag
    if looks_number_text(s):
        return "number-text"
    return None


# ---- core inspection ---------------------------------------------------------
def cell_kind(cell):
    v = cell.value
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return "blank"
    if is_formula(v):
        return "formula"
    if isinstance(v, (_dt.datetime, _dt.date)):
        return "date"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "numeric"
    return "text"


def classify_sheet(name, counts, formula_density, table_region):
    n = name.lower()
    if re.search(r"source", n):
        typ, status, why = "sources", "current", "named a sources sheet"
    elif re.search(r"conflict", n):
        typ, status, why = "conflicts", "current", "named a conflicts sheet"
    elif re.search(r"note|readme|doc|assumption", n):
        typ, status, why = "notes/assumptions", "current", "named a notes/assumptions sheet"
    elif formula_density >= 0.30:
        typ, status, why = "calc", "current", "high formula density (%.0f%%)" % (formula_density * 100)
    elif counts["formula"] == 0 and (counts["numeric"] + counts["text"] + counts["date"]) > 0:
        typ, status, why = "data/raw", "current", "literals only, no formulas — looks like an input/export table"
    else:
        typ, status, why = "mixed", "current", "mix of literals and formulas"
    # stale-name heuristic
    if re.search(r"\b(old|bak|backup|copy|prev|previous|v\d|draft|superseded|2019|2020|2021|2022|2023)\b", n):
        status = "superseded?"
        why += "; name suggests a stale/superseded copy"
    return {"type": typ, "status": status, "rationale": why}


def detect_table_region(ws, max_row, max_col):
    """Return (header_row, first_data_row, last_row) of the dominant top table, best-effort."""
    # header = first row with >=2 non-blank cells
    header_row = None
    for r in range(1, min(max_row, 30) + 1):
        nonblank = sum(1 for c in range(1, max_col + 1)
                       if cell_kind(ws.cell(row=r, column=c)) != "blank")
        if nonblank >= 2:
            header_row = r
            break
    if header_row is None:
        return None
    return header_row


def analyze_sheet(ws, findings, add):
    name = ws.title
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    counts = {"total": 0, "formula": 0, "numeric": 0, "text": 0, "date": 0, "blank": 0, "bool": 0}

    grid = {}  # (r,c) -> (kind, value)
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            k = cell_kind(cell)
            counts[k] = counts.get(k, 0) + 1
            counts["total"] += 1
            grid[(r, c)] = (k, cell.value)

    nonblank_value_cells = counts["total"] - counts["blank"]
    formula_density = (counts["formula"] / nonblank_value_cells) if nonblank_value_cells else 0.0

    header_row = detect_table_region(ws, max_row, max_col)
    cls = classify_sheet(name, counts, formula_density, header_row)

    # --- merged cells ---
    for mr in sorted(ws.merged_cells.ranges, key=str):
        sev = "should-fix" if (header_row and mr.min_row <= header_row + 1) else "polish"
        add("merged_cells", name, str(mr), sev,
            "Merged cells break row/column parsing and hide which cell actually holds the value.")

    # --- header quality ---
    if header_row:
        blanks, generics, numerics = [], [], []
        # determine populated column span on the header row
        cols = [c for c in range(1, max_col + 1)
                if grid.get((header_row, c), ("blank",))[0] != "blank"]
        if cols:
            lo, hi = min(cols), max(cols)
            for c in range(lo, hi + 1):
                k, v = grid.get((header_row, c), ("blank", None))
                loc = "%s%d" % (get_column_letter(c), header_row)
                if k == "blank":
                    blanks.append(loc)
                elif k in ("numeric", "date"):
                    numerics.append(loc)
                elif isinstance(v, str) and _GENERIC_HEADER_RE.match(v.strip()):
                    generics.append(loc)
            if blanks:
                add("unclear_headers", name, ",".join(blanks[:20]), "should-fix",
                    "Blank header cell(s) inside the table's header row — columns are unlabelled.")
            if generics:
                add("unclear_headers", name, ",".join(generics[:20]), "should-fix",
                    "Generic placeholder header(s) (Column1/Unnamed/…) — meaning is unclear.")
            if numerics:
                add("unclear_headers", name, ",".join(numerics[:20]), "polish",
                    "Numeric/date value used as a header label — ambiguous header.")

    # --- blank rows inside the table + duplicate rows + per-column unit/text scans ---
    if header_row:
        first_data = header_row + 1
        # find last populated row (within this sheet)
        last_row = max((r for (r, c) in grid if grid[(r, c)][0] != "blank"), default=header_row)

        def row_is_blank(r):
            return all(grid.get((r, c), ("blank",))[0] == "blank" for c in range(1, max_col + 1))

        def row_tuple(r):
            return tuple(
                (grid.get((r, c), ("blank", None))[1]) for c in range(1, max_col + 1)
            )

        # blank rows between data rows
        for r in range(first_data, last_row):
            if row_is_blank(r) and any(not row_is_blank(rr) for rr in range(r + 1, last_row + 1)):
                add("blank_rows_in_table", name, "row %d" % r, "should-fix",
                    "Fully blank row inside a populated table — splits the range and breaks fills/sorts.")

        # duplicate data rows
        seen = {}
        for r in range(first_data, last_row + 1):
            if row_is_blank(r):
                continue
            key = row_tuple(r)
            if all(v is None or (isinstance(v, str) and v.strip() == "") for v in key):
                continue
            if key in seen:
                add("duplicate_rows", name, "row %d (== row %d)" % (r, seen[key]), "should-fix",
                    "Duplicate data row — double-counts in any aggregation over this table.")
            else:
                seen[key] = r

        # number/date as text + mixed units, scanned per column over data rows
        ncols = max_col
        for c in range(1, ncols + 1):
            num_text_locs, date_text_locs = [], []
            unit_tags = {}
            for r in range(first_data, last_row + 1):
                k, v = grid.get((r, c), ("blank", None))
                if k == "blank":
                    continue
                loc = "%s%d" % (get_column_letter(c), r)
                if k == "text" and looks_number_text(v):
                    num_text_locs.append(loc)
                if k == "text" and looks_date_text(v):
                    date_text_locs.append(loc)
                tag = unit_signature(v)
                if tag:
                    unit_tags.setdefault(tag, []).append(loc)
            if num_text_locs:
                add("number_as_text", name, ",".join(num_text_locs[:20]), "must-fix",
                    "Number(s) stored as text — silently drop out of SUM/AVERAGE and sort wrong.")
            if date_text_locs:
                add("date_as_text", name, ",".join(date_text_locs[:20]), "should-fix",
                    "Date(s) stored as text — not comparable/sortable as dates.")
            # mixed units: >1 distinct *unit* class in one column (ignore pure 'number' vs nothing)
            unit_classes = set(unit_tags.keys())
            meaningful = unit_classes - {"number"}
            if len(unit_classes) > 1 and meaningful:
                # only flag genuine unit conflicts, not 'number' + 'number-text' alone unless mixed w/ a unit
                if not (unit_classes <= {"number", "number-text"}):
                    locs = []
                    for t in sorted(unit_classes):
                        locs.append("%s:%s" % (t, unit_tags[t][0]))
                    add("mixed_units", name, "col %s {%s}" % (get_column_letter(c), "; ".join(locs)),
                        "must-fix",
                        "Mixed units in one column (%s) — values are not comparable/addable." %
                        ", ".join(sorted(unit_classes)))

    # --- source data lacking ID / date (raw/data sheets need provenance) ---
    # Only for genuine record tables (>=4 data rows, >=3 columns) — a short KPI
    # list isn't "source data" and shouldn't be flagged.
    _data_rows = max(0, (max_row - header_row)) if header_row else 0
    if cls["type"] in ("data/raw",) and header_row and _data_rows >= 4 and max_col >= 3:
        header_texts = []
        for c in range(1, max_col + 1):
            k, v = grid.get((header_row, c), ("blank", None))
            if isinstance(v, str):
                header_texts.append(v.strip().lower())
        has_id = any(re.search(r"\b(source|src|id|ref|provenance)\b", h) for h in header_texts)
        has_date = any(re.search(r"\b(date|period|asof|as[_ ]of|month|quarter|fy)\b", h) for h in header_texts)
        if not (has_id and has_date):
            missing = []
            if not has_id:
                missing.append("source ID")
            if not has_date:
                missing.append("date/period")
            add("source_without_id_date", name, "header row %d" % header_row, "should-fix",
                "Raw/data sheet has no %s column — values can't be traced to a source per conventions.md."
                % " or ".join(missing))

    return {
        "name": name,
        "index": ws.parent.sheetnames.index(name),
        "state": ws.sheet_state,
        "dimensions": {
            "rows": max_row,
            "cols": max_col,
            "used_range": "%s%d:%s%d" % (get_column_letter(1), 1, get_column_letter(max(max_col, 1)), max(max_row, 1)),
        },
        "cell_counts": counts,
        "formula_density": round(formula_density, 3),
        "header_row": header_row,
        "classification": cls,
    }


def run(workbook_path, out_dir, source_packet=None, run_id=None,
        max_full_load_mb=_refs.DEFAULT_MAX_FULL_LOAD_MB,
        cell_budget=_refs.DEFAULT_CELL_BUDGET):
    findings = []
    _seq = {"n": 0}

    def add(check, sheet, locator, severity, message, detail=None):
        _seq["n"] += 1
        findings.append({
            "id": "WM-%03d" % _seq["n"],
            "check": check,
            "sheet": sheet,
            "locator": locator,
            "severity": severity,             # conventions §1.2
            "review_status": "needs-review",  # conventions §1.3 (structure flags await human read)
            "issue_lifecycle": "open",        # conventions §1.5
            "message": message,
            "detail": detail,
        })

    try:
        wb, load_mode, size_mb, load_note = _refs.open_workbook(
            workbook_path, max_full_load_mb=max_full_load_mb, cell_budget=cell_budget)
    except Exception as e:
        return _emit(out_dir, {
            "run_id": run_id, "skill": "workbook-doctor", "phase": "workbook-map",
            "status": "failed", "status_reason": "could not open workbook: %s" % e,
            "inputs": {"workbook": workbook_path, "source_packet": source_packet},
            "sheets": [], "findings": [], "summary": {},
        })

    # DR-04: a workbook too large for a full in-memory load is mapped in read-only
    # streaming mode (never OOM-crashed). Record the degraded checks as a finding
    # so a streamed map can never read as a clean full audit.
    if load_mode == "streamed":
        add("large_workbook_streamed", "(workbook)", os.path.basename(workbook_path),
            "should-fix",
            "Workbook is %.1f MB — too large for a full in-memory load, so it was mapped in "
            "read-only STREAMING mode. Merged-cell detection is UNAVAILABLE in this mode "
            "(the ranges are not parsed — read as 'not scanned', NOT 'none'); recalc is "
            "skipped. Pivot caches typically inflate size — split/trim the file, or re-run on "
            "a higher-memory machine, for a full structural map." % size_mb,
            detail={"load_mode": load_mode, "size_mb": round(size_mb, 1),
                    "degraded_checks": ["merged_cells", "recalc"],
                    "sampled_sheets": getattr(wb, "truncated_sheets", [])})
        for sn in getattr(wb, "truncated_sheets", []):
            add("sheet_sampled", sn, sn, "should-fix",
                "Sheet exceeded the streaming cell budget — only the leading cells were scanned "
                "(sampled); structural flags on this sheet are partial.")

    sheets = [analyze_sheet(wb[sn], findings, add) for sn in wb.sheetnames]

    # optional: note hidden sheets (often where stale numbers hide)
    for s in sheets:
        if s["state"] != "visible":
            add("hidden_sheet", s["name"], s["name"], "should-fix",
                "Sheet is %s — hidden sheets often hold stale or superseded numbers a reader won't see." % s["state"])

    sev_counts = {"must-fix": 0, "should-fix": 0, "polish": 0}
    for f in findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1

    status = "ok" if not findings else "ok-with-warnings"
    reason = ("clean — no structural flags" if not findings
              else "%d structural flag(s): %d must-fix, %d should-fix, %d polish"
              % (len(findings), sev_counts["must-fix"], sev_counts["should-fix"], sev_counts["polish"]))

    doc = {
        "run_id": run_id,
        "skill": "workbook-doctor",
        "phase": "workbook-map",
        "status": status,
        "status_reason": reason,
        "load_mode": load_mode,
        "size_mb": round(size_mb, 1),
        "inputs": {"workbook": os.path.basename(workbook_path), "source_packet": source_packet},
        "sheets": sheets,
        "findings": findings,
        "summary": {"sheet_count": len(sheets), "finding_count": len(findings),
                    "by_severity": sev_counts,
                    "degraded": (["merged_cells", "recalc"] if load_mode == "streamed" else [])},
    }
    return _emit(out_dir, doc)


def _emit(out_dir, doc):
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, "workbook-map.json")
    mpath = os.path.join(out_dir, "workbook-map.md")
    with open(jpath, "w") as fh:
        json.dump(doc, fh, indent=2, default=str)
    with open(mpath, "w") as fh:
        fh.write(_markdown(doc))
    return doc


def _markdown(doc):
    L = []
    L.append("# Workbook Map — data-structure review")
    L.append("")
    L.append("- **Run:** `%s`" % doc.get("run_id"))
    L.append("- **Workbook:** `%s`" % doc["inputs"]["workbook"])
    if doc["inputs"].get("source_packet"):
        L.append("- **Source packet:** `%s`" % doc["inputs"]["source_packet"])
    L.append("- **Status:** `%s` — %s" % (doc["status"], doc["status_reason"]))
    L.append("")
    L.append("## Sheet inventory")
    L.append("")
    L.append("| Sheet | State | Rows×Cols | Formulas | Type | Status | Note |")
    L.append("|---|---|---|---|---|---|---|")
    for s in doc["sheets"]:
        L.append("| %s | %s | %d×%d | %d (%.0f%%) | %s | %s | %s |" % (
            s["name"], s["state"], s["dimensions"]["rows"], s["dimensions"]["cols"],
            s["cell_counts"]["formula"], s["formula_density"] * 100,
            s["classification"]["type"], s["classification"]["status"],
            s["classification"]["rationale"]))
    L.append("")
    L.append("## Structural flags (%d)" % len(doc["findings"]))
    L.append("")
    if not doc["findings"]:
        L.append("_None — no structural landmines detected._")
    else:
        L.append("| ID | Severity | Check | Sheet | Where | Issue |")
        L.append("|---|---|---|---|---|---|")
        for f in doc["findings"]:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                f["id"], f["severity"], f["check"], f["sheet"], f["locator"], f["message"]))
    L.append("")
    L.append("> Inspect-only. Review status of every flag is `needs-review` (a human confirms). "
             "Formula-level risk is scanned separately by WD-02 (`formula-risk.json`).")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--out", default=None, help="output dir (default .trust/<run_id>/ next to workbook)")
    ap.add_argument("--source-packet", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-full-load-mb", type=float, default=_refs.DEFAULT_MAX_FULL_LOAD_MB,
                    help="workbooks larger than this are mapped in read-only streaming mode "
                         "(avoids an OOM crash on bloated files); default %(default)s")
    ap.add_argument("--scan-cell-budget", type=int, default=_refs.DEFAULT_CELL_BUDGET,
                    help="max cells scanned per sheet in streaming mode (sampling cap)")
    args = ap.parse_args()

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_workbook")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.workbook)), ".trust", run_id)
    doc = run(args.workbook, out_dir, source_packet=args.source_packet, run_id=run_id,
              max_full_load_mb=args.max_full_load_mb, cell_budget=args.scan_cell_budget)
    print(json.dumps({"status": doc["status"], "findings": len(doc["findings"]),
                      "out": out_dir}, indent=2))
    sys.exit(0 if doc["status"] in ("ok", "ok-with-warnings") else 1)


if __name__ == "__main__":
    main()
