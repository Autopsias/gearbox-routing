# File Specification — 2026-05-29T1615_q3-boardpack

**Skill:** office-source-packet (Phase 2) · **Status:** `blocked` (pre-creation human gate)
**Consumes:** `source-packet.json` (only — no raw source re-read) · **Output:** workbook + deck · **Audience:** board-only

> Every figure traces to a claim + `S<n>`. Conflicts carried forward, not resolved. Sensitive material handled per S02. Ends at a hard-stop human gate — no file is created until approval.

**Where the truth lives:** Q3 actuals → S1 · Q4 revenue → S2 (estimate) · strategy/M&A → S5 (restricted).

## Deck slide map (board-only)
- **S1 Title** — Q3 FY26 Board Review
- **S2 Financial Highlights** — Revenue **€48.2M** (C1/S1) · EBITDA margin **⚠ GATE D-1**: 18.5% actual (C2) vs 18% board display (C3), CONFLICT-002 · Headcount **1,195** (C4/S1, corrects 1,180) · ~~+12% YoY~~ (C6) **EXCLUDE pending source (D-2)**
- **S3 Commentary** — cloud-migration spend **€2.1M** (C9/S1) · Q4 **≈€52M** (C5/S2, label forecast)
- **S4 Strategic** — **Project Lighthouse ~€15M** (C7/S5) **RESTRICTED — board-only, sign-off (D-4)**
- **S5 Asks** — FY27 opex budget · Greenlight Lighthouse (C7) **RESTRICTED — sign-off (D-4)**

## Workbook tab map
| Tab | Basis | Contents |
|---|---|---|
| Q3 Actuals | S1 | Revenue €48.2M (C1), EBITDA 18.5% (C2), Headcount 1,195 (C4), Cloud €2.1M (C9), Gross margin 61% |
| Forecast vs Actual | S1 vs S2 | Revenue +€0.7M (CONFLICT-001), EBITDA +1.5pp (CONFLICT-002), Headcount +15 (CONFLICT-003) — variances shown, **not** blended |
| Q4 Outlook | S2 | Q4 €52M (C5) — labelled forecast/stretch |
| Sources | packet | S1..S5 + source_status, verbatim |
| Conflicts | packet | CONFLICT-001..003, verbatim |

*Project Lighthouse and the EMEA reorg do **not** appear in the workbook.*

## Sensitivity / redaction review (S02)
| Claim | Topic | Tier | Decision |
|---|---|---|---|
| C7 | Project Lighthouse (M&A) | restricted | Exclude by default; board-only deck w/ owner sign-off; never in workbook/wider version |
| C10 | EMEA reorg | restricted | Exclude from deck and workbook |
| C6 | +12% YoY | internal | Exclude pending sourced base |

## Decisions required (human gate)
| ID | Ref | Decision | Owner |
|---|---|---|---|
| D-1 | CONFLICT-002 | EBITDA margin: 18.5% actual or 18% flat + footnote? | CFO |
| D-2 | C6 | Source +12% YoY or drop | FP&A |
| D-3 | CONFLICT-003 | Confirm 1,195 replaces 1,180 | FP&A |
| D-4 | C7 | Sign-off Lighthouse in board-only deck; confirm exclusion elsewhere | CFO / Deal sponsor |
| D-5 | C10 | Confirm exclusion of EMEA reorg | CFO / HR |
| D-6 | audience | Confirm board-only (public version also redacts C7 + slide 5) | CFO |

> **🚧 HUMAN GATE — `blocked`, awaiting approval.** No workbook or deck is created until a human approves this spec and records the decisions above. Workbook Doctor / Deck Architect do not run until the gate clears.
