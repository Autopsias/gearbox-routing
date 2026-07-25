---
name: routing-retro
description: >-
  Retrospective over recent session transcripts + the misroute ledger to judge whether model/effort routing is actually working — over-modeled, under-modeled, cost outliers, receipt mismatches. Use when the user says "routing retro", "is our model selection working", "analyze model/effort fit", "review routing performance", "are we over/under-modeled", "how much are sessions costing", or after ~1 week / ~30 sessions on a new routing mapping. READ-ONLY analysis - it proposes; the only write it ever offers is an operator-confirmed append to MISROUTES.md. Calibration changes route to /routing-update — this skill never edits the SSOT.
allowed-tools: [Read, Bash, Glob, Grep, Edit, AskUserQuestion]
effort: medium  # judgment over pre-digested per-session stats (the deterministic scanner does the heavy lifting); findings are proposals reviewed by the operator, not applied state.
---

# /routing-retro — is the routing table working in practice?

Manual, nudged retrospective. The routing system is automatic-by-construction for
dispatched work and advisory-with-observability for the main session — this skill is the
**observability half exercised deliberately**: scan recent sessions, compare what SHOULD
have run (SSOT `task_classes:`) against what DID run (transcript model mix + receipts),
and turn mismatches into ledger entries and calibration proposals.

**Read-only posture.** This skill writes NOTHING except (after explicit per-entry operator
confirmation) appends to `~/.claude/evals/routing/MISROUTES.md`. Calibration-level changes
(a class default, an agent pin, an elasticity) are handed to `/routing-update` — one
apply-path, one guard, one approval protocol.

**Read, never hardcode.** Expected classes, default pairs, and prices come from
`~/.claude/model-routing.yaml` AT RUN TIME. This skill's prose carries no tier values.

## Checklist (all 5 steps)

- [ ] **Deterministic scan** — run `retro_scan.py`, read the JSON
- [ ] **Judgment layer** — infer task shape, compare expected vs actual routing
- [ ] **Report** — ranked findings, each with a recommended action
- [ ] **Did-it-stick check** — verify prior changes are still in place
- [ ] **Close** — state the next evidence-tied cadence

## Flow

### 1. Deterministic scan (cheap, no raw-JSONL reading by the model)

```bash
python3 ~/.claude/skills/routing-retro/scripts/retro_scan.py \
  --last 20 --exclude-session <CURRENT-SESSION-ID> > /tmp/retro-scan.json
```

- **Always pass the current session's id** to `--exclude-session` (it's in your transcript
  path); the scanner also drops `entrypoint: sdk*` (headless) sessions itself.
- Useful variants: `--project <substring>` to scope to one project; `--since YYYY-MM-DD`;
  `--last N`.
- Output per session: model mix %, token breakdown, **cost against SSOT `prices:`**,
  duration, tool/API error counts, `max_tokens` truncations, routing receipts
  (`class -> tier` lines), `/model`·`/effort` switch commands, first prompt, AI title.
- Read the JSON output, not the transcripts. Only open a raw transcript when a specific
  finding needs verbatim evidence (quote a receipt, confirm an escalation sequence).

### 2. Judgment layer

Read `~/.claude/model-routing.yaml` (`task_classes:`, `main_session:`, `effort_policy:`,
`escalation:`) and `references/judging-rubric.md`, then for each scanned session:

1. **Infer task shape** from `first_prompt` + `ai_title` (and `~/.claude/history.jsonl`
   prompts if the first prompt is a bare slash command).
2. **Expected class** per SSOT `task_classes:` → expected (model, effort) pair.
3. **Compare vs actual** model mix + receipts + switches. Flag per the rubric:
   over-modeled, under-modeled (escalation-ladder signatures: repeated failures then a
   model switch), receipt-vs-actual mismatch, cost outlier, degradation-ladder activation.
4. **Prescriptive-prompt friction (fable-pinned agents only).** Anthropic's Fable-5
   prompting guide warns that skills/agent prompts written for prior models are often too
   prescriptive for Fable and can DEGRADE output. For sessions that dispatched a
   fable-pinned agent (per SSOT `agents:` — currently digdeep, ci-strategy-analyst), check
   the transcript for the agent fighting its instructions: skipping mandated procedure
   steps, restating why a step doesn't apply, or output quality complaints. If seen,
   propose trimming that agent's .md prescription via `/routing-update` — never edit here.

**Effort blind spot — handle explicitly.** Transcripts do NOT record effort. Report effort
as *inferred* (settings.json `effortLevel`, `CLAUDE_CODE_EFFORT_LEVEL` env, `/effort`
switches seen in transcripts, receipts) and label it "inferred" in every finding — never
claim effort as measured. (`verify-routing.sh` check (g) shows current env overrides.)

### 3. Report — biggest wins first, every finding gets a recommended action

Short report, ranked by impact (lead with a recommendation, never neutral):

- **Misroute instances** → propose appending to `evals/routing/MISROUTES.md` in its
  documented schema (`### YYYY-MM-DD — <task shape>` / expected class / actual / cost
  symptom / notes). **Confirm each append with the operator individually**, stating the
  consequence: *the ledger is append-only, and ≥2 entries since the SSOT's `last_reviewed`
  arms the runbook's full-calibration-eval re-run trigger.* Only append what the operator
  confirms. (This append is a harness-source edit — make it in the source worktree
  `~/your-private-harness`, then deploy; never hand-edit the ledger directly in `~/.claude`.)
- **Calibration-level proposals** (change a class default, an agent pin, an elasticity,
  a price row) → recommend running **`/routing-update`** with this retro report as input
  evidence. Never edit the SSOT or any consumer from here.
- **Trigger check**: after any appends, count MISROUTES entries dated since the SSOT's
  `last_reviewed:` — if ≥2, surface the runbook's full-eval re-run recommendation
  (`evals/routing/README.md` §Re-run triggers) explicitly.
- When the findings boil down to a short decision-shaped set (accept/reject a misroute
  append, run `/routing-update` or not), the report may instead be shipped as a
  decision-card one-pager per `~/.claude/commands/references/shared/decision-card-html.md`.

### 4. "Did it stick" check

If a prior `/routing-update` or retro applied changes (check the SSOT `DECISION HISTORY`
dates and `evals/routing/results/*/ROUTING-*-CHANGESET.md`), verify they're still in place:

- Guard-covered surfaces: `bash ~/.claude/scripts/verify-routing.sh --full` (read-only) —
  green means SSOT/agents/digest/stamps still agree.
- NOT guard-blocking: `settings.json` `model`/`effortLevel` vs `main_session:` (the guard
  only warns) and *behavioral* adherence (are receipts actually being emitted? are fan-outs
  pinning non-Fable models per `fanout_policy:`?) — check both here, from the scan data.

### 5. Close

End with an evidence-tied next cadence, not a calendar default: **"re-run /routing-retro
after ~30 new sessions on this mapping, or immediately after any MISROUTES entry or
`/routing-update` apply"** — adjust the session count to how fast sessions actually
accumulate in the scan window you just measured.
