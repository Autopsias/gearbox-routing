# Self-eval (E-checks) — trust-review

Adopted 2026-06-05 per the estate's skill eval + memory contract (in the owner's
vault: `90 System/_skill_eval_memory_contract.md`; the rules below travel with
this file so the skill stays portable). Binary pass/fail only — never scores.

**Why only ONE check — the lightest treatment in the estate.** Trust-review
is already the strongest self-checker there is: the two-context loop, the
verify-prior pass, the SAFE stop rule, and the manifest gate ARE its evals.
Stacking a parallel E-check layer on top would duplicate machinery that is
better than the layer itself. The approved catalog (2026-06-04,
"trust-review" block) therefore adds exactly one thing — the property the
loop *claims* but nothing verifies after the fact: **that independence is
actually provable from the written manifest**. A round whose record is
missing a model name, a timestamp, or one of the three hashes is a round
whose independence cannot be re-derived — the whole "provable" claim
quietly degrades to "asserted". Change the catalog first if this needs
changing.

## The check

```
- id: E1
  check: "The manifest's rounds[] is non-empty and EVERY round record is complete:
          model.name + model.version non-empty, an ISO-8601 timestamp, and all three
          independence hashes present and non-empty (reviewer_prompt_sha256,
          input_artifact_sha256, provenance_record_sha256). A missing manifest.json
          is itself a FAIL."
  how: script
  on_fail: repair
```

## Grading protocol (contract §2.3)

```bash
python3 scripts/eval_checks.py --run-dir .trust/<run_id>
```

Prints one PASS/FAIL line per round with field-level evidence, then the
summary. Exit 0 = pass. Run it after the loop concludes (converged or
capped) — the verdict is already written; this verifies the provenance OF
the verdict.

The loop (contract §2.4): FAIL → repair the manifest defect (usually a
round writer dropped a field) → re-run. **Max 2 repair rounds**, then
ACTION REQUIRED naming the round and missing fields. A review whose
independence record is incomplete is reported `not-ready` provenance-wise
regardless of how clean the artifact reviewed.

## 🧪 report block (contract §2.5)

```markdown
## 🧪 Self-eval — E-checks (1/1 passed, 0 repair rounds)

- E1 PASS — 3/3 rounds carry model+timestamp+3 hashes
```

## Memory focus (contract §3) — and the independence guard

Memory file: `$TRUST_MEMORY_DIR/trust-review.md` when set, else
`_skill_memory/trust-review.md` in the workspace folder of the current vault when it exists
(read at Phase 0; otherwise skip silently). Append on event
only — the catalog's focus is **reviewer finding types the owner deemed
noise** ("stylistic must-fixes on internal-class artifacts", "locator-format
pedantry on .md sidecars"), so the hostile reviewer stays hostile but stops
crying wolf.

**The guard that matters: memory NEVER reaches the reviewer context.** The
reviewer's independence is the product — feeding it a list of "things to
stop flagging" is exactly the contamination the two-context split exists to
prevent. Memory entries are applied on the BUILDER/orchestrator side only:
when triaging the reviewer's findings, a known-noise type can be contested
through the normal §4 waiver path or annotated in the human-gate digest —
visibly, with the memory note as rationale, for the owner to confirm. The
reviewer keeps flagging; the triage stops re-litigating. Two recurrences →
graduate per contract §3.4 (T2 row proposing a severity-default or
checklist change via /skill-creator).
