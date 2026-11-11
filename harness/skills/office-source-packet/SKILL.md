---
name: office-source-packet
description: The Office Trust Layer's truth layer, built before any file. Turns a messy pile of inputs (spreadsheets, exports, emails, prior decks, notes) into an auditable source packet — every source gets a stable ID and a status, claims are split into fact, assumption, estimate, interpretation, and open question, and a conflict log preserves disagreements instead of averaging them. Then it produces a file specification (a workbook tab-map or deck slide-map where every planned claim names its source) with a sensitivity and redaction review and a pre-creation human gate. Run it on the source material before building a deck or workbook. Use when the user says "build the source packet", "what can these sources support", "before I build this deck or model", "reconcile these numbers", or "where do these figures come from". Do NOT trigger for pure rendering or formatting (that is xlsx / pptx) or for auditing an already-built workbook (workbook-doctor) — this is the source-and-claims layer that runs first.
---

# Office Source Packet (the truth layer)

You are building the **truth layer that comes before the file**. AI already builds the file; it does not build trust. Your job is to make every later claim traceable to a named source, surface every disagreement instead of silently picking one, and stop at a human gate before anything is created.

**Read these first — do not redefine anything they own:**
- `../trust-review/references/conventions.md` — the contract hub. Source-ID scheme (`S<n>`), `source_status`, `review_status`, the **provenance record**, the **command I/O contract**, and the `{ok, ok-with-warnings, blocked, failed}` status enum. Use these values verbatim.
- `../trust-review/references/sensitivity-routing-policy.md` — source sensitivity classes (`public`/`internal`/`confidential`/`restricted`), redaction modes, and the human-gate / manifest discipline. The file specification's redaction pass and the per-source `sensitivity` it emits feed the run manifest the router/gate consume.

This skill has **two phases**, matching plan items SP-01 (source packet) and SP-02 (file specification). Phase 2 consumes Phase 1's output; it never re-reads raw sources.

---

## Inputs (declared up front, per the I/O contract)
- `sources` — the input files/paste (required): CSV/XLSX exports, emails, prior decks, notes, PDFs.
- `task_context` — what the artifact is for, e.g. "Q3 board deck + workbook" (required).
- `output_type` — `workbook` | `deck` | `both` (required for Phase 2).
- `audience` — e.g. `board-only` | `internal` | `public` (required for Phase 2; drives the redaction set).
- `run_id` — `<ISO-timestamp>_<short-slug>` (e.g. `2026-05-29T1615_q3-boardpack`). Auto-generate if absent; create `.trust/<run_id>/`.

---

## Phase 1 — Source Packet (SP-01) → `.trust/<run_id>/source-packet.{json,md}`

1. **Inventory every source.** In first-encounter order, assign a stable `S<n>` ID (`S1`, `S2`, … — never reused or renumbered). Per source record: `id`, `name`, `type`, `date` (or `"unknown"`), `owner` (or `"unknown"`), `source_status` (one of `current` / `superseded` / `estimate` / `raw` — **the exact enum from conventions.md §1.1**), `sensitivity` (S02 class; **default up on doubt**), `can_support`, `cannot_support`.
2. **Extract claims and split them.** Pull each discrete assertion as a claim. Give each a `class` — **fact / assumption / estimate / interpretation / open-question** (the task's epistemic split) — and a `review_status` from conventions.md §1.3 (`verified` / `needs-review` / `unsupported` / `conflicting`). The two are different axes: `class` is *what kind of statement it is*; `review_status` is *how well it traces*.
   - A presentation choice ("show 18% flat") is an **interpretation**, never a fact.
   - A plan/target/forecast number is an **estimate** or **assumption**, never a fact.
   - A derived figure (YoY, %) with **no base in the sources** is an **open-question** and `review_status: unsupported` (zero supporting entries).
   - Only a statement traced to a `current` source with a sound derivation may be `class: fact` + `review_status: verified`.
3. **Write a provenance record per claim** in the frozen shape from conventions.md §2: `{ claim, supporting: [{ source_id, locator, confidence, rationale }], conflict }`. Zero `supporting` → `unsupported`. A non-null `conflict` → `review_status: conflicting`. Hold sub-threshold links for human review rather than asserting false provenance.
4. **Build the conflict log — preserve, never blend.** For every metric/topic where two or more sources differ (or one source changes a value mid-document), emit a conflict: the competing values with their `source_id` + `source_status`, a **where-the-truth-lives** pointer (which source is authoritative and why), and a resolution of `authoritative` (one current source wins; losers preserved) or `human-decision` (no source settles it — escalate to the Phase-2 gate). **Never average, never silently choose, never delete the losing value.**
5. **List open questions** (the `open-question` claims) so known unknowns are explicit.
6. **Emit** `source-packet.json` (machine: sources, claims+provenance, conflicts, open_questions) and `source-packet.md` (the human review surface). Return a status: `ok` if clean; `ok-with-warnings` if there are unsupported claims or unresolved conflicts (the normal case); `blocked` if a required input is missing; `failed` on a read error.

## Phase 2 — File Specification (SP-02) → `.trust/<run_id>/file-spec.{json,md}`

1. **Consume only the packet.** Read `source-packet.json`. Do **not** re-read raw sources — this guarantees the spec inherits the S-IDs, statuses, and conflict log intact.
2. **Map content to structure** for `output_type`:
   - **workbook** → a *tab map*: each tab's rows/metrics, every value tagged with its claim + `S<n>` and the source status; reserve a **Sources** tab and a **Conflicts** tab reproducing the packet verbatim.
   - **deck** → a *slide map*: each slide's claim headlines, each tagged with its `S<n>` and link confidence.
3. **Carry conflicts forward, do not resolve them.** A metric under a conflict is shown with the authoritative value AND a visible note of the competing value(s); `human-decision` conflicts become gate items.
4. **Sensitivity / redaction pass (S02).** For each mapped claim read its `sensitivity`: `public`/`internal` → include (subject to `audience`); `confidential` → include only if `audience` is within scope, else flag for redaction with a stated reason; `restricted` (M&A / MNPI / personal data) → exclude by default, include only as a gate item needing named-owner sign-off. **Redaction is decided at spec time** — the spec states what is excluded and why; nothing is stripped after a file exists.
5. **State where the truth lives** per key metric (the authoritative `S<n>`), so the spec is visibly grounded.
6. **Build the human gate.** A "Decisions required" list: every `human-decision` conflict, every `restricted`/`confidential` inclusion awaiting sign-off, every unsupported claim proposed for drop, and the audience/redaction confirmation. The spec **ends here** — it is a hard stop. Return status `blocked` ("pre-creation human gate — awaiting approval"); no file-producing skill (Workbook Doctor, Deck Architect) runs until the gate clears.
7. **Emit** `file-spec.json` and `file-spec.md`.

---

## Acceptance criteria (binary)
- Every source has a unique `S<n>` ID and a `source_status` from the conventions enum.
- Every source records `can_support`, `cannot_support`, and a `sensitivity` class.
- Every claim has a `class`, a `review_status`, and a provenance record; no `fact`+`verified` unless traced to a `current` source.
- Every metric asserted with differing values appears in the conflict log; none is blended or averaged; each states where the truth lives or escalates `human-decision`.
- The file spec tags every value/headline to a claim + `S<n>`, runs the S02 sensitivity pass, and ends at a human gate (status `blocked`).
- All four JSON/MD files are emitted to `.trust/<run_id>/`; JSON parses.

## Failure modes
- Two sources disagree → log a conflict, preserve both, point to the authoritative source or escalate `human-decision`. Never blend.
- Source changes a value mid-document → intra-source conflict; later value supersedes only if the source says so, else `human-decision`.
- Date/owner unknown → record `"unknown"`; bias `source_status`/`sensitivity` toward caution.
- Authoritative-looking but derived claim with no base → `open-question` + `unsupported`; do not promote to fact.
- Sensitive material (M&A, PII) → set `sensitivity`; Phase 1 records, Phase 2 enforces.
- No conflicts found → emit an empty conflict log explicitly, so absence is a stated result.

## Fixture
`fixtures/messy_sources/` (synthetic Q3 board-pack inputs: actuals CSV, forecast export, CFO email thread, deck draft, strategy notes) → the committed worked run at `.trust/2026-05-29T1615_q3-boardpack/`. `fixtures/fixture_source_packet.py` asserts the acceptance gate (every source `S<n>`+status; conflicts visible with where-truth-lives; file-spec ends blocked at the human gate). Wired into `fixtures/run_fixtures.sh`.
