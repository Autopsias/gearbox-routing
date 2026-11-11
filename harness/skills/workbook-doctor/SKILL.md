---
name: workbook-doctor
description: The Office Trust Layer's Excel inspector, run before anyone trusts an inherited or AI-built workbook. It maps the workbook (sheet inventory plus data-structure landmines — merged cells, blank rows, numbers or dates as text, mixed units, unclear headers) and scans formulas for the quiet failures Excel never warns about — notably a formula consistently wrong across a whole row that a consistency check still passes — flagging hardcodes and broken or stale references and checking it recalculates. With a source packet, numbers trace to a claim and source ID, so an unsupported figure becomes a finding. It produces an assumptions table, a repair plan, and a readiness verdict. Use for "check this spreadsheet", "audit this workbook", "is this model safe to use", "review these formulas", or before a workbook goes to a board. Not for creating or formatting a workbook (that is xlsx) or for deck work (deck-architect) — it audits an existing Excel file.
---

# Workbook Doctor (the Excel trust path)

You are inspecting a workbook **before anyone builds on it or sends it onward**. The model can already make a spreadsheet look finished; it cannot tell you whether the numbers are right or where they came from. Your job is to map the structure, hunt the formula failures Excel stays silent about, and trace every number back to a source — so the file's risks are visible *before* it travels.

This skill is the M1 read path. It is **inspect-only** — it never edits the workbook. It runs in **four phases**: **WD-01** (workbook map) and **WD-02** (formula-risk scan) gather the findings; **WD-03** (assessment + verdict) turns them into an assumptions table, a repair plan, a verification memo, and a readiness verdict; **WD-04** (the finance domain hook) optionally layers the `finance:*` validators on top when the workbook is a financial model. The core (WD-01→03) is **domain-agnostic**; the finance hook is **opt-in**.

**Read these first — do not redefine anything they own:**
- `../trust-review/references/conventions.md` — the contract hub. Use these **verbatim**: `severity` (`must-fix`/`should-fix`/`polish`), `review_status` (`verified`/`needs-review`/`unsupported`/`conflicting`), `issue_lifecycle` (`open`/…), the **command I/O contract**, and the `{ok, ok-with-warnings, blocked, failed}` status enum. Findings carry the source-ID scheme `S<n>` when traced to a packet.
- `../trust-review/references/capability-inventory.md` — the proven `xlsx/scripts/recalc.py` contract and its **cwd = `xlsx/scripts/` import coupling**, and the **"recalc unverified"** graceful fallback when LibreOffice is absent.
- `../trust-review/references/sensitivity-routing-policy.md` — the workbook is opened, inspected, recalculated and hashed **locally**; a model never needs the whole file. Honour the run's travel class / sensitivity.

The deterministic engineering lives in three scripts; this SKILL.md tells you how to run them and how to read what they return.

```
skills/workbook-doctor/scripts/
  workbook_map.py    # WD-01 — sheet inventory + structural-defect flags
  formula_risk.py    # WD-02 — expanded formula-risk scan + dependency graph + recalc
  assessment.py      # WD-03 — assumptions table + repair plan + verification memo + readiness verdict
  domain_finance.py  # WD-04 — finance-model detection + finance:* dispatch plan (opt-in)
  refs.py            # shared A1 reference parser, R1C1 shape signature, roll-forward primitives
```

---

## Inputs (declared up front, per the I/O contract)
- `workbook` — path to the `.xlsx` (required).
- `source_packet` — path to a `source-packet.json` from `office-source-packet` (optional but recommended). When present, output numbers are traced to claims + `S<n>`, and numbers resting on `unsupported`/`conflicting` claims become findings.
- `run_id` — `<ISO-timestamp>_<short-slug>`. Auto-generated if absent; outputs go to `.trust/<run_id>/` next to the workbook.

---

## Phase 1 — Workbook map + data-structure review (WD-01) → `.trust/<run_id>/workbook-map.{json,md}`

Run the analyzer (inspect-only; formulas kept, `data_only=False`):

```bash
python skills/workbook-doctor/scripts/workbook_map.py <workbook.xlsx> \
    --out .trust/<run_id> [--source-packet <packet.json>] --run-id <run_id>
```

It produces a **sheet inventory** (name, hidden state, dimensions, formula density, and a purpose/type/status classification — data/raw · calc · output/summary · sources · conflicts · notes, with a stale-name heuristic) and **structural flags**, each keyed by `severity`/`review_status`/`issue_lifecycle`:

| Check | What it catches | Default severity |
|---|---|---|
| `merged_cells` | Merged ranges that break row/column parsing and hide which cell holds the value | should-fix in/near headers, else polish |
| `blank_rows_in_table` | A fully blank row inside a populated table — splits ranges, breaks fills/sorts | should-fix |
| `duplicate_rows` | Identical data rows — double-count in any aggregation | should-fix |
| `number_as_text` | Numbers stored as text — drop out of SUM/AVERAGE, sort wrong | must-fix |
| `date_as_text` | Dates stored as text — not comparable/sortable | should-fix |
| `mixed_units` | One column mixing €/%/absolute/k/M/x — values not comparable or addable | must-fix |
| `unclear_headers` | Blank, generic ("Column1"), or numeric header cells | should-fix / polish |
| `source_without_id_date` | A genuine data/raw table with no source-ID or date column (can't be traced per conventions.md) | should-fix |
| `hidden_sheet` | A hidden/very-hidden sheet — often where stale numbers hide | should-fix |

Every flag's `review_status` is `needs-review` — Phase 1 surfaces structure; a human confirms. Status is `ok` if clean, else `ok-with-warnings`.

## Phase 2 — Formula-risk scan, EXPANDED (WD-02) → `.trust/<run_id>/formula-risk.{json,md}`

```bash
python skills/workbook-doctor/scripts/formula_risk.py <workbook.xlsx> \
    --map .trust/<run_id>/workbook-map.json --out .trust/<run_id> \
    [--source-packet <packet.json>] --run-id <run_id>   # add --no-recalc to skip LibreOffice
```

**The headline failure (review-1 finding 5):** a formula that is *consistently wrong across a whole row* — e.g. `=$C$5/$B$5-1` filled across every year. Every cell is structurally identical, so a sibling-shape / consistency check **passes it**. The scanner therefore does **not** rely on shape alone — it runs a **positive roll-forward model**: in a time-series row, the references in period *N* should be period *(N-1)*'s references shifted by one column. Absolute refs that never advance are caught precisely; a correctly-filled row is left alone.

Checks (each emits ranked findings keyed by the conventions enums):

1. **`shape_consistency`** — R1C1 sibling agreement across a period row. *Reported, not trusted alone* — it passes the headline bug, which is the point.
2. **`roll_forward`** — period-over-period reference advancement. Refs that are entirely stuck → **must-fix** (the consistently-wrong case); refs that advance irregularly → should-fix (possible base-period anchor — verify).
3. **`repeated_literal_ref`** — the same absolute reference repeated across every period column (the bug's signature).
4. **`hardcode_in_calc_zone`** — a typed number sitting inside a calculated column/row where its neighbours are formulas → **must-fix** (won't recompute; can silently disagree).
5. **`hardcode_in_formula`** — a magic numeric constant baked into a formula (an undocumented assumption) → should-fix.
6. **`ref_error`** — `#REF!`/`#NAME?`/`#DIV/0!`/… tokens carried in the stored formula.
7. **`named_range` / tables** — defined names whose destination contains `#REF!` (broken named ranges).
8. **dependency graph** — same-sheet and cross-sheet precedent edges; **`dangling_reference`** (a precedent cell that is empty — a moved/deleted input); **`circular_reference`** chains.
9. **recalculation-capability validation** — invokes `xlsx/scripts/recalc.py` **with cwd = its own dir** (the import coupling) to recompute and read the live error map. If LibreOffice is unavailable the scanner returns status **`ok-with-warnings`** and flags **"recalc unverified"** — it never fails the run on a missing tool.
10. **`source_packet_link`** (when a packet is supplied) — a deliverable number tied to an `unsupported` claim → **must-fix** ("source it or drop"); a number carrying a **losing** value from a conflict → should-fix ("reconcile to the authoritative figure"). Numbers already on the authoritative value are not flagged, and input/source/raw sheets are skipped — linkage is about what the deliverable *asserts*.

Findings are ranked must-fix → should-fix → polish. Status is `ok` only if there are no findings and recalc ran clean; `ok-with-warnings` otherwise (the normal case, and always when recalc is unverified).

## Phase 3 — Assessment + verdict (WD-03) → `.trust/<run_id>/workbook-verdict.{json,md}`

Turns the WD-01 + WD-02 findings into a decision. It **consumes the two JSON artifacts** (not the workbook again), plus — optionally — the same `source-packet.json` and a `resolutions.json` (the human gate).

```bash
python skills/workbook-doctor/scripts/assessment.py \
    --map .trust/<run_id>/workbook-map.json \
    --formula-risk .trust/<run_id>/formula-risk.json \
    [--source-packet <packet.json>] [--resolutions <resolutions.json>] \
    [--travel-class Board] [--no-finance] --out .trust/<run_id> --run-id <run_id>
```

It emits four things, all keyed to `conventions.md`:

1. **Assumptions table** — every load-bearing assumption with `name · value · unit · source · owner · date · status`. Assembled from (a) source-packet claims that carry judgement (`assumption`/`estimate`/`open-question`, plus any `conflicting`/`unsupported` fact) and (b) the **undocumented constants the workbook bakes in** (WD-02 `hardcode_*` findings, tagged `origin: workbook-hardcode`, `status: unsupported`).
2. **Repair plan** — every *unresolved* finding, grouped by the `severity` enum (must-fix → should-fix → polish), each with a concrete action and its current `issue_lifecycle`.
3. **Verification memo** — an explicit **pass / fail per check**: **tie-outs**, **formula consistency**, **source traceability**, **stale data** (plus recalculation and data-structure). A check `fail`s when an unresolved **must-fix** maps to it; should-fix/polish surface as `concerns`, not a fail. Every finding type maps to exactly one memo line.
4. **Readiness verdict** — `not-ready` / `ready-with-limitations` / `ready` (conventions §1.4/§4), computed **strictly from the must-fix lifecycle**, always paired with the explicit `must_own` list a human still has to own:
   - **`not-ready`** while any must-fix is `open` / `fixed-claimed` / `still-open`. **Never `ready` while a must-fix is open** — the cardinal rule.
   - **`ready-with-limitations`** when no must-fix is open **but** ≥1 must-fix is `waived-by-human` (the waiver record — owner / decision / due date / signoff / rationale — travels with the file). A `Board`/`Regulatory` waiver additionally needs named-owner **signoff** or the verdict is held at `not-ready` (§4.3).
   - **`ready`** only when every must-fix is `verified-fixed` and no waivers are in play.

**The human gate (`--resolutions`).** Raw scan findings are `open`; a workbook with any must-fix is therefore `not-ready` by default — which is the point. A named human moves a finding to `verified-fixed` (re-checked) or `waived-by-human` (risk accepted, with the §4 record) via the resolutions file. An **invalid** waiver (missing a required field) is rejected and the finding stays blocking.

**Run status vs verdict are different axes.** The skill's `status` (`ok`/`ok-with-warnings`/`blocked`/`failed`) says whether the *assessment ran*; the `readiness` says whether the *workbook may travel*. Missing upstream inputs → `blocked` (run WD-01/WD-02 first), distinct from `failed`.

## Phase 4 — Finance domain hook (WD-04), opt-in → `domain` block in `workbook-verdict.json`

The core is domain-agnostic; this hook (`domain_finance.py`, called by `assessment.py`) only fires when the workbook looks like a **financial model**. Detection is deterministic and works off the upstream JSON + packet only: financial terms in sheet names / labels / findings, financial packet metrics, a time-series period axis, an actuals-vs-forecast pair, conflicting financial figures. Two or more independent signals (or any financial packet metric) ⇒ financial.

When matched, it recommends the `finance:*` validators to run **on top of** the generic checks — only those whose trigger signal is present, each with the trigger and the inputs to feed it:
- **`finance:variance-analysis`** — a time axis / actuals-vs-forecast pair ⇒ decompose the period movement; check the bridge ties out.
- **`finance:reconciliation`** — conflicting figures across sources ⇒ reconcile the displayed value to the authoritative source; preserve the losing values, don't blend.
- **`finance:audit-support`** — an unsupported number presented as fact ⇒ build the control/evidence trail before it goes to a board.
- **`finance:financial-statements`** — P&L / balance-sheet / cash-flow line items ⇒ check GAAP presentation + period comparison.

The CLI also leaves the hook off unless a `finance:*` skill is installed (`domain_finance.finance_skills_present()`; set `WD_FINANCE_SKILLS=1` to force it on). `--no-finance` (or `enabled=False`) makes the hook inert — **no** `finance:*` recommendations even on a financial model. The hook *recommends and dispatches*; the `finance:*` skills are invoked via the Skill tool by the session (proven live in `fixtures/_finance_hook_proof.txt`).

---

## Phase B — Builder path (AB-01, v3) → `.trust/<run_id>/{model.xlsx, build-report.{json,md}, manifest.json}`

The four phases above are **inspect-only**. The v3 builder path (`scripts/builder.py`) turns workbook-doctor into **build-it-right** — but only ever claims trustworthy when the alarm was actually lit. The Trust Layer owns the architecture + check logic; the `.xlsx` write is delegated to the `xlsx` skill engine (wrap-don't-rebuild).

It scaffolds the four-tab architecture **`Raw → Assumptions → Calc → Checks`** and writes a **live checks tab** — every result is a worksheet formula, so green/red is computed by the workbook, **never a hardcoded `PASS`**. The check categories are the frozen §7.3 controlled enum (`builder.py` asserts its categories *equal* that enum, so an ad-hoc/empty checks-tab claim is impossible):

| category | live formula asserts |
|---|---|
| `tie-outs` | total = stated control **and** = SUM(components) |
| `formula-consistency` | each parallel (roll-forward) cell = an independent re-derivation — catches the "one cell wrong across a row" trap a shape check passes |
| `error-scan` | `SUMPRODUCT(--ISERROR(…))=0` — no Excel error survives a recalc |
| `stale-date` | `(today − as_of) ≤ stale_after_days` |
| `scenario` | base/scenario toggle recomputes consistently |

**THE RECALC GATE (the point).** A freshly written `.xlsx` has formulas but **no computed values** — openpyxl cannot evaluate them. So the checks-tab verdict is read **only** from engine-computed cached values *after a verified live recalc* (LibreOffice, via `xlsx/scripts/recalc.py` run in place):

- **recalc verified** → read the engine's cached `Checks!` values; that is the verdict (`ready` when the alarm is green, `not-ready` when a live check FAILED — a real defect, never capped away).
- **recalc unavailable / failed** → apply the **`recalc-unverified`** degraded-status cap (conventions §7.1); the **checks-tab readiness *claim* is BLOCKED** (the smoke alarm was never powered), readiness is capped at `ready-with-limitations` (never plain `ready`), and **no green/red is read off the unevaluated sheet**. The builder still emits **static formula assertions** (a Python re-derivation of each check) — labelled `equivalent_to_recalc: false`, an honesty floor, **explicitly not a recalc**.

Source IDs (Raw + control) and each assumption's **owner / date / status / source_id** are carried from the source packet into the Raw/Assumptions tabs and the `build-report`. Run: `python builder.py --spec spec.json --out .trust/<run_id>/` (`--no-recalc` demonstrates the cap; `--selftest` runs the gate logic offline). Fixture: `fixtures/fixture_workbook_builder.py` (a-clean → ready, a-broken → checks tab flips FAIL on a verified recalc, b → recalc-unavailable applies the cap + blocks the claim + labelled static fallback).

## Router handoff

The travel-class router (`commands/travel-class-router.md`) classifies a file and gates it; now that the doctor produces a **verdict**, the router → `workbook-doctor` handoff is live: the router invokes WD-01 → WD-02 → WD-03 (passing the run's `--travel-class` so §4.3 signoff is enforced for Board/Regulatory), and reads back the `readiness` verdict + `must_own` list. (Router → `trust-review`, the two-context loop, lands at S06.)

---

## Acceptance criteria (binary)
- All four phases emit their artifacts to `.trust/<run_id>/` (`workbook-map`, `formula-risk`, `workbook-verdict` — each `.json`+`.md`); all JSON parses; every finding carries `severity` + `review_status` + `issue_lifecycle` from `conventions.md`.
- The structural scan flags merged cells, blank rows in tables, duplicate rows, number/date-as-text, mixed units, unclear headers, and un-provenanced data tables.
- The formula scan **detects the consistently-wrong roll-forward case** that shape consistency passes, plus hardcoded outputs and stale/broken references; it extracts a dependency graph and validates recalculation (or degrades to "recalc unverified").
- With a source packet, an unsupported number (e.g. the `+12% YoY` open-question, packet claim `C6`) is surfaced as a must-fix.
- WD-03 emits the assumptions table (`name/value/unit/source/owner/date/status`), a severity-keyed repair plan, and a pass/fail verification memo; the **readiness verdict flips when an assumption changes** (unsupported → `not-ready`; sourced → `ready`), a valid waiver yields `ready-with-limitations`, and a Board waiver without signoff stays `not-ready`. It is **never `ready` while a must-fix is open**.
- WD-04 detects a financial model and recommends the matching `finance:*` validators with triggers + inputs; a non-financial workbook is not flagged; `--no-finance` recommends nothing.

## Failure modes
- **LibreOffice absent** → recalc can't run → status `ok-with-warnings`, finding "recalc unverified"; static checks still run.
- **Workbook unreadable / corrupt** → status `failed` with the error (distinct from `blocked`).
- **A legitimately fixed base-period anchor** (e.g. everything indexed to a base year) looks like a non-advancing ref → reported as should-fix ("verify intended base-period anchor"), not silently must-fix, to keep false positives under the conventions threshold.
- **No source packet** → linkage is skipped (numbers can't be traced to claims); note the gap rather than inventing provenance.
- **Cross-sheet refs to an unknown/external sheet** → not treated as dangling (can't verify) — avoids false positives.

## Failure modes (WD-03 / WD-04)
- **Missing upstream inputs** (no `workbook-map.json` / `formula-risk.json`) → WD-03 returns `blocked` with the precondition, not `failed`.
- **No source packet** → the assumptions table is built from workbook hardcodes only and provenance can't be traced; the gap is noted in the run status, not invented.
- **Invalid waiver** (missing owner/decision/due-date/signoff/rationale) → rejected; the finding stays blocking → verdict stays `not-ready`.
- **Not a financial model** / `--no-finance` → WD-04 recommends nothing; the domain-agnostic core is unchanged.

## Fixtures
- **WD-01/02** — `fixtures/workbook_doctor/build_fixtures.py` builds one workbook per defect type (`roll_forward`, `hardcoded_output`, `stale_reference`, `mixed_unit`) plus the worked-run model (`q3_board_model.xlsx`) tied to the S03 source packet; `answer_key.json` is the ground truth; `fixtures/fixture_workbook_doctor.py` asserts every seeded defect with the right severity — **including the trap**: the consistently-wrong row is shape-consistent yet the roll-forward model flags it, while the correct contrast row is not.
- **WD-03 (the verdict gate)** — `fixtures/fixture_workbook_verdict.py` builds `verdict_flip.xlsx` (a deliverable resting on a single load-bearing assumption) and runs the WD-01→02→03 chain twice against two states of the packet: with the assumption **unsupported** the verdict is `not-ready`; once **sourced**, the *same* workbook is `ready` — **changing one assumption flips the verdict.** It also asserts a valid waiver → `ready-with-limitations`, a Board waiver without signoff → held `not-ready`, the assumptions-table columns, the pass/fail memo, the severity-keyed repair plan, and the `blocked` precondition.
- **WD-04 (the finance hook)** — `fixtures/fixture_finance_hook.py` (the deferred `finance:*` functional fixture) asserts the financial model is detected and dispatches `reconciliation` + `audit-support` with triggers+inputs, a non-financial workbook is not flagged, and `--no-finance` is inert. A live `finance:reconciliation` invocation on `CONFLICT-003` is recorded in `fixtures/_finance_hook_proof.txt` (IN-SESSION proof).
- **Committed worked run:** `.trust/2026-05-29T1740_q3-workbook/` now carries `workbook-map`, `formula-risk`, **and** `workbook-verdict` (`.json`+`.md`) — Board travel class, `not-ready` (3 must-fix), finance hook fired. All wired into `fixtures/run_fixtures.sh` → **18 passed / 0 failed, GATE PASS.**
