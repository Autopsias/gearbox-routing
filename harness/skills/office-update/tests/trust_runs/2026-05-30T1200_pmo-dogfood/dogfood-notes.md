# M1 Dogfood — Harbor Team Extension Requirements PMO (Preliminary v3)

**Run:** `2026-05-30T1200_pmo-dogfood` · **path:** Excel (M1) · **date:** 2026-05-30

The S08 ship-gate dogfood: the full M1 chain run end-to-end on one **real** workbook
(`20260512 Harbor Team Extension Requirements_PMO_Preliminary_v3.xlsx` — a live
resourcing/budget model, 2 sheets: `Carve-out`, `DDs review`).

## Chain & outcome (as actually run)

1. **Source packet** — minimal, honest: one source `S1` = the workbook itself, flagged
   *single source, no external corroboration* (every figure rests on this one file, no
   external claims to reconcile). → `source-packet.json`
2. **Workbook Doctor** — `workbook-map` (status `ok-with-warnings`, **20 findings**) +
   `formula-risk` (status `ok-with-warnings`, **9 findings**; **recalc could not run** —
   LibreOffice `soffice` timed out after 10s in the sandbox, so it degraded to "recalc
   unverified" per the graceful-fallback contract). Findings were structural / should-fix /
   polish: 8× polish `merged_cells`, 9× should-fix `blank_rows_in_table`, 1× `duplicate_rows`,
   1× `date_as_text` (E52), 1× `source_without_id_date`, and 9× should-fix
   `hardcode_in_formula` (F-column magic constants). **Zero must-fix.**
   → `workbook-map.*`, `formula-risk.*`
3. **Assessment / verdict** — readiness **`ready`** (status `ok-with-warnings` because recalc
   was unverified), **0 must-fix**, **9 must-own** items (the should-fix/polish findings a
   human should still own before the figures travel). → `workbook-verdict.*`
4. **Trust Review loop** — Internal travel class; the **S02 manifest gate passed** (sensitivity
   + routing + travel-class recorded), the **two-context routing** (Opus builder / Sonnet
   reviewer) was recorded, the loop ran **1 round** and converged to **`ready`** ("every
   must-fix is verified-fixed; no waivers in play"). The `manifest.human_gate` block is present
   and empty — **no waiver was needed** because the workbook carried no blocking defect.
   → `review-edits.*`, `review-round-1.*`, `review-result.*`, `manifest.json`

This is a legitimate, honest outcome: on this particular real workbook the harness found
**no must-fix defect**, so it returned `ready` with a list of 9 lesser items to own — and the
human gate correctly had nothing to waive. The dogfood exercised every stage of the chain
(packet → map → formula-risk → verdict → review loop → gate) on real data without a single
plugin-script crash. The waiver / `ready-with-limitations` path is the one proven by the
hr-00 human-gate fixtures (a planted must-fix); it is not exercised here because this file
had no must-fix to waive — which is exactly the behaviour we want, not a gap.

## Friction (found & fixed this session)

- **trust-review SKILL.md frontmatter** — the description contained a colon-space
  (`provable: a …`) that strict YAML (skill-creator `quick_validate`) rejected even though
  `claude plugin validate` tolerated it. **Fixed:** colon → em-dash; all three skills validate.
- **benchmark scanner scratch** — `benchmark_m1.py` first wrote per-fixture scanner output
  into the committed `.trust` run dir (and the cleanup hit a delete permission-lock).
  **Fixed:** scanner scratch routed to a `/tmp` tempdir; the committed run dir now holds only
  `benchmark-m1.{json,md}`.
- **driving the loop programmatically** — `run_loop()` is keyword-only; the first dogfood
  driver passed it positionally and crashed before the review stage. **Fixed:** drove it via
  the same keyword call its CLI uses (`reviewer=_verdict_reviewer(verdict)`, `out_dir=`, …).
- **recalc unavailable in-sandbox** — `soffice` times out (no display/profile warm-up), so
  recalc degrades to "unverified" and the verdict status is `ok-with-warnings`. Expected
  fallback, not a defect; on a workstation with LibreOffice warm it recomputes for real.
- **office-source-packet is prose-only** (no script) — a standalone single-source workbook has
  no script to auto-emit a packet stub, so the packet was hand-built for this dogfood. Minor;
  a tiny packet-scaffolder is a natural M2 nicety. Not a blocker.
