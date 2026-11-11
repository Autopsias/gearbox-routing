---
name: office-update
description: The Office Trust Layer's recurring-refresh primitive. Takes last period's run plus this period's claim-set and produces a surface-agnostic CHANGE-SET — every claim classified unchanged / updated / new / dropped / superseded, dropped claims tombstoned (never silently deleted), stable claim IDs preserved across a re-order, a superseded source linked to its replacement (never blended), and ONLY valid waivers + prior verdicts carried forward on identity-stable unchanged claims. Built on the S01 claim identity (fd-02) and the read-only migration reader (fd-03); the prior run is consumed read-only. Use when the user says "refresh the board pack", "this quarter's update", "what changed since last period", or "diff this against last period's deck/model". This is the CORE diff/classify engine; extracting claims from a rendered .xlsx/.pptx/.docx/.pdf is the surface adapters (UP-03). Do NOT use to build a file from scratch (office-source-packet → xlsx/pptx) or to audit one static workbook (workbook-doctor).
---

# Office Update (the recurring-refresh primitive)

You are building the **eighth primitive**: recurring reporting. A monthly board pack, a QBR, an MBR — the highest-frequency real use of the Trust Layer, and the one thing v2 deferred. Last period you produced a trusted artifact; this period the sources moved. The danger is that a naive refresh becomes a trust **copier** — it reuses last period's `verified`/`ready` on a claim whose source, period, or unit quietly shifted.

This skill exists to make that impossible. It never re-judges trust by itself; it produces an honest **change-set** that says, for every claim, *exactly what happened to it* and *whether last period's verdict is still allowed to stand* — then hands the changed and new claims back to the verification + trust-review path.

**This is the CORE only (S02 / UP-01).** It consumes an already-extracted, *normalized* claim-set and emits the change-set. Extracting claims from a rendered office file per surface (Excel cells, PowerPoint shapes, the §1.6 doc grammar) is the **surface adapters (S03 / UP-03)**. Keep this engine surface-agnostic.

**Read these first — do not redefine anything they own:**

- `../trust-review/references/conventions.md §7.5` — the **canonical claim identity** and the **moved-claim diff**: how `claim_id` is minted, what counts as a `moved` claim vs a `changed-identity`, and the **seven invalidation classes** that force re-review. Use these verbatim.
- `../trust-review/references/conventions.md §7.6` — the **human-gate-digest invariants** and §4 waiver semantics (what makes a waiver valid).
- `../trust-review/references/conventions.md §7.7 / §7.8` — the two-phase vault-writeback record and the **schema_version lineage + backward-compat rule**. Every new output stamps `schema_version: "3"` + `migration_notes`.

**Build on, never reimplement:**

- `../trust-review/references/scripts/claim_identity.py` (**fd-02**) — computes every `claim_id` and every invalidation class. This skill asks it; it never hashes identity itself.
- `eval/compat_harness.py` (**fd-03**) — the reader. The prior run is loaded through `v3_view` so a v1/v2/v3 prior all read the same way, optional fields default safely, and the prior run is consumed **READ-ONLY**.

The engine is `scripts/office_update.py` (pure standard library).

---

## Inputs (declared up front, per the I/O contract)

- `prev_dir` — the prior period's `.trust/<run_id>/` directory (required). Read-only.
- `current_claims` — this period's **normalized claim-set** (required): a list of claim records, or a `claim-set.json` with a `claims` array. One record per claim:

  | field | meaning |
  |---|---|
  | `claim_text` *(req)* | the claim as stated this period |
  | `locator` *(req)* | where it sits, in the surface's grammar (`tab!cell` / `slide N/shape` / §1.6 doc anchor) |
  | `surface`, `fmt` | the locator-grammar tag (so the same string in two grammars cannot collide) |
  | `sources` | backing sources `[{source_id, source_status, content_hash, as_of}]` |
  | `retrieval_timestamp` | ISO-8601 as-of of this period's extraction |
  | `materiality_class` | the governing §2.3 tier |
  | `unit`, `period` | the silent-drift context (optional but recommended) |
  | `waiver` | the §4 waiver covering this claim, or absent |
  | `verdict` | last period's trust verdict, when carried on the record |
  | `slot` | a stable logical key (e.g. `q3-rev`) — lets a replacement be matched to what it supersedes |
  | `supersedes` | explicit: the prior `claim_id`(s) this claim replaces |

- `run_id` — `<ISO-timestamp>_<slug>` (auto-generated if absent); creates `.trust/<run_id>/`.
- `now`, `freshness_days`, `travel_class` — optional: drive source-staleness and the Board/Regulatory signoff requirement for waiver validity.

## What it does (the algorithm)

1. **Load the prior run through fd-03.** `load_prior_run(prev_dir)` reads `<prev>/manifest.json` via `compat_harness.v3_view` (any schema version, default-safe) and the prior claim-set (native `claim-set.json` first, then a prior `refresh-report.json`'s forward set, then an upstream `source-packet.json` as a bootstrap). A whole-dir byte-hash before/after proves the prior run was never mutated.
2. **Diff by fd-02 identity.** For every claim, `claim_identity.diff(prev, curr)` decides `unchanged` / `moved` / `changed-identity` and lists any invalidation class. A `moved` claim (same text + same source, new locator) **keeps its `claim_id`**.
3. **Classify** every claim into exactly one of:
   - **unchanged** — identity stable, no invalidation. The *only* class that carries a prior verdict.
   - **updated** — identity stable but a §7.5 invalidation class fired (source-stale, materiality-escalated, unit/period drift, waiver-scope change, or an **expired waiver**). Re-enters `needs-review`.
   - **new** — a new `claim_id` (text or source changed). Cannot inherit a verdict; starts at `needs-review`.
   - **dropped** — present last period, gone now, nothing replaces it ⇒ **tombstoned** (`supersession_state: tombstoned`). Never deleted; kept in the report and in the forward claim-set.
   - **superseded** — a newer source replaced it. The old claim is **linked** to its replacement (`superseded_by`) and the replacement records `supersedes`. **Both values are preserved — never blended or averaged.**
4. **Carry forward, gated.** A prior verdict carries **only** on an `unchanged` claim. A waiver carries **only** when it is still valid — well-formed (owner + decision), unexpired, and (Board/Regulatory) signed off. An expired waiver lapses the trust basis: the claim re-enters review.
5. **Emit the change-set** to `.trust/<run_id>/`:
   - `refresh-report.json` / `refresh-report.md` — the machine + human change-set (counts, per-claim rows, tombstones, supersession links, carried-forward tally, `migration_notes`).
   - `claim-set.json` — the **forward** claim-set (this period's claims stamped with ids + carried verdicts, plus the tombstones and superseded claims kept as inactive audit records) so next period has a native prior.
   - `manifest.json` — stamped `schema_version: "3"` + `migration_notes`; the F-05 gate must read `ok` (local-only diff, no model-run loop).

   Return `ok` if clean; `ok-with-warnings` when anything was updated / dropped / superseded (the normal case); `blocked` if the prior run is missing; `failed` if the read-only guarantee breaks.

## Hard rules (do not relax)

- **Never silently delete.** A claim that disappears is tombstoned, surfaced, and kept.
- **Never blend a supersession.** The old and new values are both preserved and linked; nothing is averaged or overwritten.
- **A new identity never inherits a verdict.** Any change to normalized text or source fingerprint mints a new `claim_id` that starts at `needs-review`.
- **Only valid waivers + unchanged-claim verdicts carry.** Everything else re-enters the verification + trust-review path.
- **The prior run is read-only.** Stamp `schema_version` + `migration_notes` onto the new output only.
- **Touch no §5 threshold.** This is a diff/classify primitive; it never reads or writes the frozen release thresholds.

## CLI

```bash
python scripts/office_update.py --prev .trust/<prev>/ --current claim-set.json \
    --out .trust [--run-id <id>] [--now ISO] [--freshness-days N] \
    [--travel-class internal|travels|board-regulatory]
python scripts/office_update.py --selftest
```

## Boundaries

- **office-source-packet / xlsx / pptx** build a file from scratch — this refreshes an existing one.
- **workbook-doctor / document-doctor** audit one static artifact — this diffs two periods.
- **The surface adapters (UP-03 / S03)** extract the normalized claim-set from a rendered office file — this CORE consumes that claim-set. Keep the two separate.
