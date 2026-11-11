# Self-eval (E-checks) — trust-run

Adopted 2026-06-05 per the estate's skill eval + memory contract (in the owner's
vault: `90 System/_skill_eval_memory_contract.md`; the rules below travel with
this file so the skill stays portable). Binary pass/fail only — never scores.
Light treatment per the APPROVED catalog (2026-06-04, orchestrators block).
Change the catalog first if a check needs changing.

**Why these four.** trust-run's own Step 0a names the worst failure for a
trust tool: **a fabricated step** — claiming a pipeline stage ran when it
left no artefact. And its Step 7 makes one promise above all: ONE verdict,
taken from the strictest file and the strictest active cap, never an
average, never softer than a component. The four checks verify exactly
that: the artefact trail, the single honest verdict, the actionable
fix-list, and the reviewer-independence cap. Each delegated skill already
self-checks its own artefact (their `references/_evals.md`); this layer
only verifies the orchestration record. All script-type, run inline via
`scripts/eval_checks.py` (§2.3).

## The checks

```
- id: E1
  check: "Every pipeline step the summary claims ran left its artefact in the run
          dir: router json per Step 1, refresh-report per Step 2 (when claimed),
          source-packet per Step 3, ≥1 inspector output per Step 4 (claim-audit /
          claim-set / workbook-verdict), evidence-map per Step 5, review manifest
          with rounds per Step 6, and the human-gate digest. A claimed step with
          no artefact is a fabricated step — the worst failure for a trust tool."
  how: script
  on_fail: repair

- id: E2
  check: "Exactly ONE headline verdict, from the enum (ready /
          ready-with-limitations / blocked / not-ready, with the
          not-ready-for-forwarding qualifier allowed) — and never SOFTER than
          the strictest per-file verdict found in the run dir's component
          artefacts. A pack verdict that averages away a weak document is the
          lie Step 7 exists to prevent."
  how: script
  on_fail: action_required

- id: E3
  check: "One single prioritised fix-list in the summary: must-fix items come
          first, and every must-fix names its file and locator — actionable
          without opening six reports."
  how: script
  on_fail: repair

- id: E4
  check: "Reviewer independence is recorded and the cap honored: the summary
          carries reviewer_independence; on a Board/Regulatory run a same-family
          review without a recorded named-owner signoff caps the verdict at
          blocked — never plain ready, never silently ready-with-limitations."
  how: script
  on_fail: repair
```

## Grading protocol (contract §2.3)

```bash
python3 scripts/eval_checks.py --run-dir .trust/<run_id>
```

Prints one PASS/FAIL line per check with step/file-level evidence. Exit 0 =
all pass. Run after Step 7 writes `trust-run-summary.json`, before the
verdict is reported to the user.

The loop (contract §2.4): FAIL → repair the record defect → re-run the FULL
set. **Max 2 repair rounds**, then ACTION REQUIRED. **E2 is the exception**
— a verdict already softer than its strictest component is not a record
defect, it is a wrong verdict: re-derive it from the components, restate it
to the user, never patch the summary to match.

## 🧪 report block (contract §2.5)

```markdown
## 🧪 Self-eval — E-checks (4/4 passed, 0 repair rounds)

- E1 PASS — 6/6 claimed steps left artefacts (refresh not claimed)
- E2 PASS — one verdict "blocked"; strictest component is blocked — consistent
- E3 PASS — 5-item fix-list, 2 must-fix first, all name file+locator
- E4 PASS — reviewer_independence=cross-vendor recorded
```

## Memory focus (contract §3)

Memory file: `$TRUST_MEMORY_DIR/trust-run.md` when set, else
`_skill_memory/trust-run.md` in the workspace folder of the current vault when it exists
(read at Phase 0; otherwise skip silently). Append on event only —
orchestration decisions the owner corrects: a step he tells the run to skip or
add for a pack type, an intake assumption he reverses, a cap he routinely
signs off (candidate for a standing waiver discussion). Record pack type +
direction — it tunes the next run's intake proposals; the pipeline order,
the gates, and the §7.1 cap ordering never move via memory. Two
recurrences → graduate per contract §3.4.
