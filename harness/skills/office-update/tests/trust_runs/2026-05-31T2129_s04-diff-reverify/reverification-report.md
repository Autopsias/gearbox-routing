# UP-02 diff-aware re-verification — worked run
Run: `2026-05-31T2129_s04-diff-reverify`  ·  Prior: `.trust/2026-05-30T0900_prev-refresh`  ·  Travel class: board-regulatory

## The refresh in numbers
| metric | value |
|---|---|
| prior total | 300 |
| current total | 300 |
| changed (re-verified) | 12 |
| carried forward (verdict + prior-run pointer) | 286 |
| carried verdicts invalidated by the matrix | 2 |
| total re-verify set (SV-07 budget scope) | 14 |

## The invalidation matrix (explicit table)
| input change-class | action |
|---|---|
| locator | carry |
| text | re-open |
| unit-period | re-open |
| waiver-owner | re-open |
| travel-class-escalation | re-open |
| source | re-source |
| source-age | re-source |

## The two carried verdicts the matrix re-opened
- `C-265f847b0ea9` — **re-open** (triggered: waiver-owner; prior verdict `ready-with-limitations` invalidated)
- `C-bf35bdccc7f5` — **re-source** (triggered: source-age; prior verdict `verified` invalidated)

The SV-07 budget covered the 14-claim re-verify set only — none of the 286 carried claims entered the Verifier. The prior run was read-only; references/ (§5) untouched.
