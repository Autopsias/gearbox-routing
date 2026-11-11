# Workbook Map — data-structure review

- **Run:** `pmo-dogfood`
- **Workbook:** `pmo_dogfood.xlsx`
- **Source packet:** `/sessions/sweet-eloquent-newton/mnt/Downloads/office-trust-layer/office-trust-layer/.trust/2026-05-30T1200_pmo-dogfood/source-packet.json`
- **Status:** `ok-with-warnings` — 20 structural flag(s): 0 must-fix, 12 should-fix, 8 polish

## Sheet inventory

| Sheet | State | Rows×Cols | Formulas | Type | Status | Note |
|---|---|---|---|---|---|---|
| Carve-out | visible | 66×13 | 0 (0%) | data/raw | current | literals only, no formulas — looks like an input/export table |
| DDs review | visible | 17×12 | 9 (8%) | mixed | current | mix of literals and formulas |

## Structural flags (20)

| ID | Severity | Check | Sheet | Where | Issue |
|---|---|---|---|---|---|
| WM-001 | polish | merged_cells | Carve-out | M13:M19 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-002 | polish | merged_cells | Carve-out | M21:M24 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-003 | polish | merged_cells | Carve-out | M28:M37 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-004 | polish | merged_cells | Carve-out | M40:M43 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-005 | polish | merged_cells | Carve-out | M4:M10 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-006 | polish | merged_cells | Carve-out | M50:M54 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-007 | polish | merged_cells | Carve-out | M56:M61 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-008 | polish | merged_cells | Carve-out | M63:M65 | Merged cells break row/column parsing and hide which cell actually holds the value. |
| WM-009 | should-fix | blank_rows_in_table | Carve-out | row 11 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-010 | should-fix | blank_rows_in_table | Carve-out | row 19 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-011 | should-fix | blank_rows_in_table | Carve-out | row 25 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-012 | should-fix | blank_rows_in_table | Carve-out | row 37 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-013 | should-fix | blank_rows_in_table | Carve-out | row 44 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-014 | should-fix | blank_rows_in_table | Carve-out | row 48 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-015 | should-fix | blank_rows_in_table | Carve-out | row 54 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-016 | should-fix | blank_rows_in_table | Carve-out | row 61 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-017 | should-fix | blank_rows_in_table | Carve-out | row 65 | Fully blank row inside a populated table — splits the range and breaks fills/sorts. |
| WM-018 | should-fix | duplicate_rows | Carve-out | row 66 (== row 62) | Duplicate data row — double-counts in any aggregation over this table. |
| WM-019 | should-fix | date_as_text | Carve-out | E52 | Date(s) stored as text — not comparable/sortable as dates. |
| WM-020 | should-fix | source_without_id_date | Carve-out | header row 2 | Raw/data sheet has no source ID or date/period column — values can't be traced to a source per conventions.md. |

> Inspect-only. Review status of every flag is `needs-review` (a human confirms). Formula-level risk is scanned separately by WD-02 (`formula-risk.json`).
