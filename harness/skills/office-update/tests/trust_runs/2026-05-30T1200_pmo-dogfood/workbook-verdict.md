# Workbook Verdict — assessment & readiness

- **Run:** `pmo-dogfood`
- **Workbook:** `pmo_dogfood.xlsx`
- **Travel class:** Internal
- **Run status:** `ok-with-warnings` — assessed; recalc unverified (recalc unverified — Traceback (most recent call last):
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 184, in <module>
    main()
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 179, in main
    result = recalc(filename, timeout)
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 76, in recalc
    if not setup_libreoffice_macro():
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 55, in setup_libreoffice_macro
    subprocess.run(
  File "/usr/lib/python3.10/subprocess.py", line 505, in run
    stdout, stderr = process.communicate(input, timeout=timeout)
  File "/usr/lib/python3.10/subprocess.py", line 1154, in communicate
    stdout, stderr = self._communicate(input, endtime, timeout)
  File "/usr/lib/python3.10/subprocess.py", line 2022, in _communicate
    self._check_timeout(endtime, orig_timeout, stdout, stderr)
  File "/usr/lib/python3.10/subprocess.py", line 1198, in _check_timeout
    raise TimeoutExpired(
subprocess.TimeoutExpired: Command '['soffice', '--headless', '--terminate_after_init']' timed out after 10 seconds
)

## ⟶ Readiness: **READY**

> no must-fix findings; structural/should-fix items (if any) are limitations to own, not blockers.

## What a human must still own (9)

- **[assumption]** Hardcoded value at DDs review!F3 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F4 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F5 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F7 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F10 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F11 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F12 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F13 = '21' [unsupported] — source (in-cell, no source), owner unknown
- **[assumption]** Hardcoded value at DDs review!F14 = '21' [unsupported] — source (in-cell, no source), owner unknown

## Assumptions (9)

| Name | Value | Unit | Source | Owner | Date | Status | Origin |
|---|---|---|---|---|---|---|---|
| Hardcoded value at DDs review!F3 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F4 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F5 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F7 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F10 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F11 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F12 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F13 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |
| Hardcoded value at DDs review!F14 | 21 | number/text | (in-cell, no source) | unknown | — | unsupported | workbook-hardcode |

## Verification memo

| Check | Result | Detail |
|---|---|---|
| Tie-outs (numbers reconcile / recompute to the right value) | PASS | clean. |
| Formula consistency (references behave across the model) | PASS | no must-fix; 9 should-fix/polish to own. |
| Source traceability (every number traces to a current source) | PASS | no must-fix; 1 should-fix/polish to own. |
| Stale data (no broken / moved / hidden / superseded inputs) | PASS | clean. |
| Recalculation (the workbook recomputes clean) | UNVERIFIED | recalc not run — recalc unverified — Traceback (most recent call last):
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 184, in <module>
    main()
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 179, in main
    result = recalc(filename, timeout)
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 76, in recalc
    if not setup_libreoffice_macro():
  File "/sessions/sweet-eloquent-newton/mnt/.claude/skills/xlsx/scripts/recalc.py", line 55, in setup_libreoffice_macro
    subprocess.run(
  File "/usr/lib/python3.10/subprocess.py", line 505, in run
    stdout, stderr = process.communicate(input, timeout=timeout)
  File "/usr/lib/python3.10/subprocess.py", line 1154, in communicate
    stdout, stderr = self._communicate(input, endtime, timeout)
  File "/usr/lib/python3.10/subprocess.py", line 2022, in _communicate
    self._check_timeout(endtime, orig_timeout, stdout, stderr)
  File "/usr/lib/python3.10/subprocess.py", line 1198, in _check_timeout
    raise TimeoutExpired(
subprocess.TimeoutExpired: Command '['soffice', '--headless', '--terminate_after_init']' timed out after 10 seconds
 |
| Data structure (no parsing landmines) | PASS | no must-fix; 19 should-fix/polish to own. |

## Repair plan (29 unresolved)

| Severity | ID | Check | Where | Action |
|---|---|---|---|---|
| should-fix | FR-001 | hardcode_in_formula | DDs review!F3 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-002 | hardcode_in_formula | DDs review!F4 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-003 | hardcode_in_formula | DDs review!F5 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-004 | hardcode_in_formula | DDs review!F7 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-005 | hardcode_in_formula | DDs review!F10 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-006 | hardcode_in_formula | DDs review!F11 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-007 | hardcode_in_formula | DDs review!F12 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-008 | hardcode_in_formula | DDs review!F13 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | FR-009 | hardcode_in_formula | DDs review!F14 | Move the magic constant to a labelled, sourced assumption cell and reference it. |
| should-fix | WM-009 | blank_rows_in_table | Carve-out!row 11 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-010 | blank_rows_in_table | Carve-out!row 19 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-011 | blank_rows_in_table | Carve-out!row 25 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-012 | blank_rows_in_table | Carve-out!row 37 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-013 | blank_rows_in_table | Carve-out!row 44 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-014 | blank_rows_in_table | Carve-out!row 48 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-015 | blank_rows_in_table | Carve-out!row 54 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-016 | blank_rows_in_table | Carve-out!row 61 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-017 | blank_rows_in_table | Carve-out!row 65 | Remove the blank row inside the table so ranges/fills/sorts are intact. |
| should-fix | WM-018 | duplicate_rows | Carve-out!row 66 (== row 62) | Remove the duplicate data row so aggregations don't double-count. |
| should-fix | WM-019 | date_as_text | Carve-out!E52 | Convert the text to a real date so it compares and sorts. |
| should-fix | WM-020 | source_without_id_date | Carve-out!header row 2 | Add a source-ID and date/period column so rows trace to a source. |
| polish | WM-001 | merged_cells | Carve-out!M13:M19 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-002 | merged_cells | Carve-out!M21:M24 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-003 | merged_cells | Carve-out!M28:M37 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-004 | merged_cells | Carve-out!M40:M43 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-005 | merged_cells | Carve-out!M4:M10 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-006 | merged_cells | Carve-out!M50:M54 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-007 | merged_cells | Carve-out!M56:M61 | Unmerge the range; keep the value in a single addressable cell. |
| polish | WM-008 | merged_cells | Carve-out!M63:M65 | Unmerge the range; keep the value in a single addressable cell. |

## Domain hook (WD-04)

_Not detected as a financial model — generic checks only (the core is domain-agnostic)._

> Readiness is computed strictly from the must-fix lifecycle (conventions.md §1.4/§4). It is never `ready` while a must-fix is open; a waiver caps it at `ready-with-limitations`.
