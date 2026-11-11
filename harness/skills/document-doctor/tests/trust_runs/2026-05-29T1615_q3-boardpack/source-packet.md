# Source Packet — 2026-05-29T1615_q3-boardpack

**Skill:** office-source-packet (Phase 1) · **Status:** `ok-with-warnings` (1 unsupported claim, 3 conflicts incl. 1 human-decision)
**Task:** Q3 FY26 board deck + supporting workbook · **Inputs:** `fixtures/messy_sources/` (5 sources)

> Truth substrate for the run. Every figure traces to an `S<n>`. Conflicts are preserved, never blended.
> Enums per `references/conventions.md` (`source_status`, `review_status`); sensitivity per `references/sensitivity-routing-policy.md`.

## Source inventory

| ID | Name | Type | Date | Owner | `source_status` | Sensitivity | Can / **Cannot** support |
|---|---|---|---|---|---|---|---|
| S1 | q3_actuals.csv | CSV export | end Q3 | FP&A close pack | `current` | internal | Q3 actuals / **not** Q4, YoY, strategy |
| S2 | q3_forecast_v2.csv | forecast export | mid Q3 | FP&A | `estimate` | internal | Q4 outlook; planning assumptions / **not** Q3 actuals (superseded by S1) |
| S3 | cfo_email_thread.md | email (informal) | close week | CFO | `current` | restricted | CFO directives; confirms €48.2M; Lighthouse board-only / **not** authoritative figures |
| S4 | board_deck_draft_v3.md | draft deliverable | close week | unattributed | `current` | restricted | prior framing / **not** any figure as fact (unsourced, placeholders) |
| S5 | strategy_offsite_notes.md | rough notes | unknown | strategy/CEO | `estimate` | restricted | strategic intent/assumptions / **not** any actual; partly sensitive |

## Claims — class split + traceability

| Claim | Class | review_status | Value | Source(s) |
|---|---|---|---|---|
| C1 Q3 revenue | fact | conflicting | €48.2M | S1 (conflict S2,S3) |
| C2 Q3 EBITDA margin | fact | conflicting | 18.5% | S1 (conflict S3,S2) |
| C3 EBITDA board display | interpretation | needs-review | 18% flat | S3 |
| C4 Q3 headcount | fact | conflicting | 1,195 | S1 (conflict S2,S4) |
| C5 Q4 revenue | estimate | needs-review | ≈€52M | S2 |
| C6 +12% YoY | open-question | **unsupported** | +12% | — (no base) |
| C7 Project Lighthouse | interpretation | needs-review | ~€15M | S5 (restricted) |
| C8 FY27 headcount | assumption | needs-review | ~1,250 | S5 |
| C9 Cloud spend | fact | **verified** | €2.1M | S1 |
| C10 EMEA reorg | open-question | needs-review | possible | S5 (restricted) |

Provenance records (claim → supporting[{source_id, locator, confidence, rationale}] → conflict) are in `source-packet.json`.

## Conflict log — preserved, never blended

- **CONFLICT-001 Q3 revenue** — S1 €48.2M (`current`) ← **truth** · S2 €47.5M (`estimate`, superseded) · S3 €47.5M prelim→corrected to €48.2M. **authoritative.**
- **CONFLICT-002 Q3 EBITDA margin** — S1 18.5% (`current`) ← **truth** · S3 18% board display · S2 17%. **human-decision** (present 18.5% or 18%+footnote → Phase-2 gate).
- **CONFLICT-003 Q3 headcount** — S1 1,195 (`current`) ← **truth** · S2/S4 1,180. **authoritative** (correct the draft).

## Open questions
- **C6** +12% YoY — no prior-year base in any source. Source or drop.
- **CONFLICT-002** EBITDA presentation — human decision.
- **C10** EMEA reorg — status unknown, personnel-sensitive.

## Where the truth lives
Q3 actuals → **S1**. Q4 revenue → **S2** (estimate). Strategy/M&A → **S5** (restricted; board-only per S3).
