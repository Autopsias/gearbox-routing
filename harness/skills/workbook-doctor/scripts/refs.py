"""
Office Trust Layer — Workbook Doctor: shared A1 reference utilities.

Pure stdlib. Parses Excel A1 references out of a stored formula string, and
provides the two primitives WD-02 needs:

  * shape signature  — an R1C1-style canonical form of a formula. Two sibling
    cells have the SAME shape iff their relative/absolute reference pattern is
    identical. NOTE: both a correctly-filled row (=C5/B5-1, =D5/C5-1, ...) AND
    the consistently-wrong row (=$C$5/$B$5-1 repeated) produce a CONSTANT shape
    across the row. Shape consistency therefore PASSES the headline bug — which
    is exactly why a positive roll-forward model is also required.

  * resolved targets — the absolute (col,row) cells a formula points at, taken
    from the per-cell stored string (Excel adjusts relative refs at fill time
    and openpyxl reads them back per cell). This is what the roll-forward model
    compares period-over-period.

review-1 finding 5 / WD-02: "do NOT rely on sibling-shape alone."
"""
import os
import re
import string

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter as _gcl

# A1 cell reference: optional sheet, optional $col, col letters, optional $row, row digits.
_SHEET = r"(?:(?P<sheet>'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!)?"
_CELL = r"\$?[A-Za-z]{1,3}\$?[0-9]+"
CELL_RE = re.compile(_SHEET + r"(?P<cabs>\$?)(?P<col>[A-Za-z]{1,3})(?P<rabs>\$?)(?P<row>[0-9]+)")
# A defined-name token: a bare identifier NOT immediately followed by '(' (function)
# and not part of a cell ref. Used only for the named-range cross-check.
NAME_RE = re.compile(r"(?<![A-Za-z0-9_.!$])([A-Za-z_][A-Za-z0-9_.]{1,})(?![A-Za-z0-9_(\[!])")
_STRLIT_RE = re.compile(r'"[^"]*"')


def col_to_idx(col):
    """'A' -> 1, 'B' -> 2, 'AA' -> 27."""
    idx = 0
    for ch in col.upper():
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx


def idx_to_col(idx):
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = string.ascii_uppercase[rem] + out
    return out


def strip_strings(formula):
    """Remove string literals so quoted text is never mistaken for a reference."""
    return _STRLIT_RE.sub('""', formula or "")


def parse_cell_refs(formula):
    """Yield dicts for every single-cell A1 reference in the formula.

    Each: {raw, col, col_idx, row, col_abs(bool), row_abs(bool), sheet_local(bool)}.
    Range endpoints are parsed as individual cells (the range expands to its anchors).
    """
    f = strip_strings(formula)
    out = []
    for m in CELL_RE.finditer(f):
        sheet = m.group("sheet")
        if sheet:
            sheet = sheet.strip("'")
        out.append({
            "raw": m.group(0),
            "sheet": sheet,
            "col": m.group("col").upper(),
            "col_idx": col_to_idx(m.group("col")),
            "row": int(m.group("row")),
            "col_abs": m.group("cabs") == "$",
            "row_abs": m.group("rabs") == "$",
            "has_sheet": sheet is not None,
        })
    return out


def referenced_columns(formula, within=None):
    """Multiset (sorted list) of absolute column indices the formula points at.

    If `within` is a (lo, hi) inclusive column range, restrict to refs landing
    inside that band (used to ignore legitimately-fixed assumption cells that
    sit OUTSIDE the time-series block).
    """
    cols = []
    for r in parse_cell_refs(formula):
        if within is None or (within[0] <= r["col_idx"] <= within[1]):
            cols.append(r["col_idx"])
    return sorted(cols)


def referenced_cells(formula):
    """Set of (col_idx, row) tuples referenced (single cells only)."""
    return {(r["col_idx"], r["row"]) for r in parse_cell_refs(formula)}


def shape_signature(formula, anchor_row, anchor_col):
    """R1C1-style canonical form relative to the cell at (anchor_row, anchor_col).

    Relative col/row -> offset token C[n]/R[n]; absolute -> fixed token C<n>/R<n>.
    Function names lowercased. Whitespace stripped. Equal signatures == same shape.
    """
    f = strip_strings(formula or "")
    if f.startswith("="):
        f = f[1:]

    def repl(m):
        col_idx = col_to_idx(m.group("col"))
        row = int(m.group("row"))
        cabs = m.group("cabs") == "$"
        rabs = m.group("rabs") == "$"
        ctok = "C%d" % col_idx if cabs else "C[%d]" % (col_idx - anchor_col)
        rtok = "R%d" % row if rabs else "R[%d]" % (row - anchor_row)
        return rtok + ctok

    sig = CELL_RE.sub(repl, f)
    sig = re.sub(r"\s+", "", sig).lower()
    return sig


def defined_name_tokens(formula):
    """Bare identifiers that look like named ranges (not functions, not cell refs)."""
    f = strip_strings(formula or "")
    # blank out cell refs first so names don't pick up the letters of A1 refs
    f = CELL_RE.sub(" ", f)
    toks = set()
    for m in NAME_RE.finditer(f):
        tok = m.group(1)
        if tok.upper() in _EXCEL_FUNCS:
            continue
        toks.add(tok)
    return toks


# A small set of common Excel function names so the name cross-check doesn't
# treat them as defined names. Not exhaustive — only needs the ones our
# fixtures and typical models use; unknown funcs are conservatively reported.
_EXCEL_FUNCS = {
    "SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "COUNTA", "COUNTIF", "COUNTIFS",
    "SUMIF", "SUMIFS", "IF", "IFS", "IFERROR", "AND", "OR", "NOT", "VLOOKUP",
    "HLOOKUP", "XLOOKUP", "INDEX", "MATCH", "ROUND", "ROUNDUP", "ROUNDDOWN",
    "ABS", "INT", "MOD", "TODAY", "NOW", "DATE", "YEAR", "MONTH", "DAY",
    "CONCAT", "CONCATENATE", "TEXT", "LEFT", "RIGHT", "MID", "LEN", "TRIM",
    "NPV", "IRR", "PMT", "PV", "FV", "RATE", "SUMPRODUCT", "SUBTOTAL",
    "CHOOSE", "OFFSET", "INDIRECT", "ROW", "COLUMN", "ISERROR", "ISBLANK",
    "ISNUMBER", "POWER", "SQRT", "LOG", "EXP", "RANK", "STDEV", "VAR", "MEDIAN",
}


# =============================================================================
# DR-04 (S08 dogfood): robust large-workbook loading.
#
# A full in-memory `load_workbook(read_only=False)` of a real, bloated workbook
# (pivot caches / inflated styles) can allocate multiple GB and be OOM-killed
# before any verdict is produced — a HARD CRASH, not a graceful degradation.
# The 11.5 MB `T2D Fcst 2026 Ext.Costs.xlsx` dogfood reproduced this (~3.7 GB
# RSS, SIGKILL, no output).
#
# Mitigation (read_only / sampling, per the DR-04 brief — the §5 threshold is
# NOT relaxed): for a workbook above `max_full_load_mb`, load it in openpyxl
# read-only STREAMING mode and present each sheet through a thin adapter that
# mimics the minimal worksheet API the analysis helpers use
# (`.cell(row,column).value`, `.max_row`, `.max_column`, `.iter_rows()`,
# `.title`, `.sheet_state`, `.parent`, `.merged_cells.ranges`). The analysis
# logic is therefore UNCHANGED; only the cell source differs.
#
# Two things degrade in streaming mode and are reported as findings, never
# silently dropped:
#   * merged-cell ranges are not parsed by openpyxl read-only mode -> the
#     caller records a `large_workbook_streamed` / merged-not-scanned limitation
#     (so "0 merged cells" can never read as a clean result);
#   * the scanned extent is bounded by `cell_budget` (sampling) and by the
#     actual populated range, so an inflated <dimension> cannot explode the
#     analysis loops; a truncated sheet is flagged `sampled`.
# =============================================================================

DEFAULT_MAX_FULL_LOAD_MB = 8.0
DEFAULT_CELL_BUDGET = 400_000


class _GridCell:
    """Minimal stand-in for an openpyxl cell — only .value/.row/.column are
    ever read by the workbook-doctor helpers."""
    __slots__ = ("value", "row", "column")

    def __init__(self, value, row, column):
        self.value = value
        self.row = row
        self.column = column

    @property
    def coordinate(self):
        return "%s%d" % (_gcl(self.column), self.row)


class _EmptyMerged:
    """read-only mode does not parse merged ranges; expose an empty .ranges so
    the existing merged-cell loop simply finds none (the caller records that the
    check was skipped, so this is never a silent clean)."""
    ranges = ()


class _GridSheet:
    """A streamed worksheet view backed by a sparse {(row,col): value} dict."""

    def __init__(self, title, sheet_state, parent, values, max_row, max_col,
                 truncated=False):
        self.title = title
        self.sheet_state = sheet_state
        self._parent = parent
        self._values = values
        self.max_row = max_row
        self.max_column = max_col
        self.truncated = truncated
        self.merged_cells = _EmptyMerged()

    @property
    def parent(self):
        return self._parent

    def cell(self, row, column):
        return _GridCell(self._values.get((row, column)), row, column)

    def iter_rows(self, min_row=1, max_row=None, min_col=1, max_col=None,
                  values_only=False):
        max_row = self.max_row if max_row is None else max_row
        max_col = self.max_column if max_col is None else max_col
        for r in range(min_row, (max_row or 0) + 1):
            if values_only:
                yield tuple(self._values.get((r, c))
                            for c in range(min_col, (max_col or 0) + 1))
            else:
                yield tuple(_GridCell(self._values.get((r, c)), r, c)
                            for c in range(min_col, (max_col or 0) + 1))


class _GridWorkbook:
    """A streamed workbook proxy exposing the minimal workbook API the
    helpers use (sheetnames / indexing / worksheets / defined_names)."""

    def __init__(self, ro_wb, cell_budget=DEFAULT_CELL_BUDGET):
        self._ro_wb = ro_wb                 # kept alive for .defined_names
        self.defined_names = getattr(ro_wb, "defined_names", {})
        self.sheetnames = list(ro_wb.sheetnames)
        self._sheets = {}
        self.truncated_sheets = []
        for sn in self.sheetnames:
            ws = ro_wb[sn]
            values, mr, mc, trunc = _scan_ro_sheet(ws, cell_budget)
            self._sheets[sn] = _GridSheet(sn, ws.sheet_state, self, values,
                                          mr, mc, trunc)
            if trunc:
                self.truncated_sheets.append(sn)

    @property
    def worksheets(self):
        return [self._sheets[sn] for sn in self.sheetnames]

    def __getitem__(self, name):
        return self._sheets[name]

    def __iter__(self):
        return iter(self.worksheets)


def _scan_ro_sheet(ws, cell_budget):
    """Stream a read-only worksheet into a sparse {(r,c): value} dict, bounded
    by `cell_budget` and by the actual populated extent. Returns
    (values, eff_max_row, eff_max_col, truncated)."""
    values = {}
    eff_max_row = 0
    eff_max_col = 0
    scanned = 0
    truncated = False
    for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
        any_in_row = False
        for c, v in enumerate(row, start=1):
            scanned += 1
            if v is None or (isinstance(v, str) and v == ""):
                continue
            values[(r, c)] = v
            any_in_row = True
            if c > eff_max_col:
                eff_max_col = c
        if any_in_row and r > eff_max_row:
            eff_max_row = r
        if scanned >= cell_budget:
            truncated = True
            break
    return values, eff_max_row, eff_max_col, truncated


def open_workbook(path, max_full_load_mb=DEFAULT_MAX_FULL_LOAD_MB,
                  cell_budget=DEFAULT_CELL_BUDGET):
    """Open a workbook robustly. Returns (wb_like, mode, size_mb, note) where
    mode is 'full' (normal openpyxl) or 'streamed' (read-only adapter). Raises
    only on a genuinely unreadable file — never OOM-crashes on a large one.

    `wb_like` exposes the subset of the openpyxl API the workbook-doctor
    helpers use, so callers pass it straight through unchanged."""
    size_mb = os.path.getsize(path) / 1e6
    if size_mb <= max_full_load_mb:
        try:
            wb = load_workbook(path, data_only=False, read_only=False)
            return wb, "full", size_mb, None
        except MemoryError:
            pass  # fall through to streaming — never crash on memory pressure
    ro = load_workbook(path, data_only=False, read_only=True)
    note = ("workbook is %.1f MB (> %.1f MB full-load cap) — loaded in read-only "
            "STREAMING mode to avoid an out-of-memory crash; merged-cell ranges "
            "are not available in this mode and recalc is skipped" %
            (size_mb, max_full_load_mb))
    return _GridWorkbook(ro, cell_budget=cell_budget), "streamed", size_mb, note
