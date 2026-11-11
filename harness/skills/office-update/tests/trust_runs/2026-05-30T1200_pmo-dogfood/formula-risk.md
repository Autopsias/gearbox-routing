# Formula-Risk Scan

- **Run:** `pmo-dogfood`
- **Workbook:** `pmo_dogfood.xlsx`
- **Source packet:** `source-packet.json`
- **Recalc:** **UNVERIFIED** — recalc unverified — Traceback (most recent call last):
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

- **Status:** `ok-with-warnings` — 9 formula risk(s) found; RECALC UNVERIFIED (recalc unverified — Traceback (most recent call last):
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
).

## Ranked formula risks (9)

| ID | Severity | Check | Sheet | Where | Risk |
|---|---|---|---|---|---|
| FR-001 | should-fix | hardcode_in_formula | DDs review | F3 | Magic constant 21 embedded in a formula |
| FR-002 | should-fix | hardcode_in_formula | DDs review | F4 | Magic constant 21 embedded in a formula |
| FR-003 | should-fix | hardcode_in_formula | DDs review | F5 | Magic constant 21 embedded in a formula |
| FR-004 | should-fix | hardcode_in_formula | DDs review | F7 | Magic constant 21 embedded in a formula |
| FR-005 | should-fix | hardcode_in_formula | DDs review | F10 | Magic constant 21 embedded in a formula |
| FR-006 | should-fix | hardcode_in_formula | DDs review | F11 | Magic constant 21 embedded in a formula |
| FR-007 | should-fix | hardcode_in_formula | DDs review | F12 | Magic constant 21 embedded in a formula |
| FR-008 | should-fix | hardcode_in_formula | DDs review | F13 | Magic constant 21 embedded in a formula |
| FR-009 | should-fix | hardcode_in_formula | DDs review | F14 | Magic constant 21 embedded in a formula |

## Dependency graph

- 9 precedent edges across 18 cells; 0 dangling precedent(s); 0 cycle(s).

> The **roll-forward** check is a positive model of period-over-period reference advancement: it catches a formula that is *consistently wrong across a row* — the case a sibling-shape/consistency check passes (review-1 finding 5).
