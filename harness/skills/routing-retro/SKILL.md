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
confirmation) appends to `~/.claude/evals/routing/MISROUTES.md`, plus `aggregate_outcomes.py`'s
own `.last-aggregated` bookkeeping stamp (a byte-offset/mtime marker next to the ledger,
never the ledger or the SSOT itself — see step 1b). Calibration-level changes (a class
default, an agent pin, an elasticity) are handed to `/routing-update` — one apply-path, one
guard, one approval protocol.

**Read, never hardcode.** Expected classes, default pairs, and prices come from
`~/.claude/model-routing.yaml` AT RUN TIME. This skill's prose carries no tier values.

## Checklist (all 7 steps)

- [ ] **Deterministic scan** — run `retro_scan.py`, read the JSON
- [ ] **Aggregation** (step 1b) — run `aggregate_outcomes.py`, read its JSON (never the raw
  `outcomes.ndjson` ledger); surface any fired proposal and the `apex_revisit` callout
- [ ] **Judgment layer** — infer task shape, compare expected vs actual routing
- [ ] **Report** — render the one-pager (`render_report.py`) + a decision card of at most 3
  proposed changes, each with a recommended action
- [ ] **Did-it-help check** (step 3a) — before/after cohorts for the last adopted proposal
- [ ] **Compaction did-it-help** (step 3b) — per-intervention cohorts from the activation
  ledger, plus `arming_check.py` before any deferred verdict is re-judged
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
  `context_peak_tokens` + `reread_cost_usd`/`reread_cost_pct` (context carried forward),
  duration, tool/API error counts, `max_tokens` truncations, routing receipts
  (`class -> tier` lines), `/model`·`/effort` switch commands, first prompt, AI title.
- Read the JSON output, not the transcripts. Only open a raw transcript when a specific
  finding needs verbatim evidence (quote a receipt, confirm an escalation sequence).

### 1b. Aggregation (cohort-level facts + threshold proposals)

`retro_scan.py` reads TRANSCRIPTS (cost, context, receipts — the dispatch-hygiene half).
`aggregate_outcomes.py` reads the separate outcome LEDGER
(`evals/routing/outcomes.ndjson`, written by `/plan-execute` at every verify/rework
resolution) and turns it into per-cell success-rate facts and, only past minimum sample
sizes, upgrade/downgrade PROPOSALS for `/routing-update`. Run it every retro, before the
judgment layer:

```bash
python3 ~/.claude/skills/routing-retro/scripts/aggregate_outcomes.py > /tmp/aggregate.json
```

- **Read the JSON, never the raw ledger.** The script groups attempt records into SESSION
  COHORTS, excludes ungated/administrative/retired outcomes from every rate, and gates
  proposals on minimum sample size + model attestation — re-deriving any of that by eyeballing
  `outcomes.ndjson` directly reproduces exactly the bugs it exists to prevent.
- **An empty `proposals[]` is not a null result.** Read `time_to_signal` for each cell's
  current N and, when below threshold, the projected earliest date the sample size could be
  met at the observed cadence — report that in step 3 as "silence expected until ~N months",
  never as "nothing to see."
- **`apex_revisit`** — if `callout: true` (five cumulative cohorts carrying a fable-rung
  escalation), surface it as a named finding in the report: review whether those fable climbs
  actually converted (the ssot-01 revisit trigger), independent of any upgrade/downgrade
  proposal.
- **A fired proposal is evidence, not an instruction.** Carry its `proposal_id`,
  `first_attempt_pass_rate`/`escalation_rate`, and N into the judgment layer and the report —
  the apply path is always `/routing-update`, never this skill.

#### Downgrade canary protocol

A DOWNGRADE proposal never adopts from the class-default population alone — it is a
two-stage, explicit-tag experiment (grill 2026-08-13):

1. **Stage 1 — smoke.** The proposal names a `proposal_id`, the class, and the current
   (higher) rung. The operator authors the NEXT plan(s) of that class at ONE RUNG LOWER,
   with plan-builder writing `routing_experiment: {kind: "canary", proposal_id: "<id>"}`
   into each session's spec (carried through to `manifest.json` and every ledger record
   that session produces — see `plan-builder/references/schemas.md`'s `routing_experiment`
   field). Route the next 3 sessions this way, gates on. **Any failure aborts the
   experiment** — a passing 3-session smoke alone can never adopt (rule of three: at n=3 the
   95% upper bound on the failure rate is 100%).
   - A canary session added mid-flight via `add-session` must pass `--infographic-group`
     explicitly — an ambiguous group resolution silently drops the item from the progress
     denominator (see `plan-mutation-item-pillar-placement.md`).
2. **Stage 2 — adoption evidence.** `aggregate_outcomes.py` aggregates every
   `routing_experiment`-tagged cohort under its `proposal_id` as an explicit CANARY cell
   (`canaries[]` in the JSON), separate from the normal class-default cells. It reaches
   `adoption-ready` only at N≥10, first-attempt pass≥0.90, AND attempts-per-success≤1.1 —
   never on raw success rate alone. A Claude-lane cohort counts only when its first
   attempt ran through a tier agent (`effort_mechanism: tier_agent`); any other cohort
   ran at the orchestrator's effort, not the rung under test, and is reported as
   `excluded.effort_unbound` (v25). Before authoring a canary, confirm
   `agents/tier-<model>-<effort>.md` exists.
3. **Judge at the next retro.** Read `canaries[]` for the proposal's `proposal_id`:
   `smoke-failed` → report the abort and drop the proposal; `smoke-in-progress` /
   `smoke-passed-awaiting-adoption-evidence` → report progress toward N=10, no action;
   `adoption-ready` → recommend `/routing-update` to adopt (which also writes the
   `evals/routing/adoptions.ndjson` boundary the aggregator reads back for did-it-help
   comparisons on future retros).

### 2. Judgment layer

Read `~/.claude/model-routing.yaml` (`task_classes:`, `main_session:`, `effort_policy:`,
`escalation:`) and `references/judging-rubric.md`, then for each scanned session:

1. **Infer task shape** from `first_prompt` + `ai_title` (and `~/.claude/history.jsonl`
   prompts if the first prompt is a bare slash command).
2. **Expected class** per SSOT `task_classes:` → expected (model, effort) pair.
3. **Compare vs actual** model mix + receipts + switches. Flag per the rubric:
   over-modeled, under-modeled (escalation-ladder signatures: repeated failures then a
   model switch), receipt-vs-actual mismatch, cost outlier, degradation-ladder activation,
   context-bloat. The last one is a *dispatch-hygiene* flag, not a routing one — it needs
   cited evidence that the session's work DIVERGED, never a token count alone (a long hard
   single task with a big context is correct behaviour), and its remedy is a clean worker,
   never a tier/effort change.
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

### 3. Report — one rendered one-pager + a decision card, every retro, the house way

Render the retro's report as ONE self-contained HTML one-pager, not chat prose — same
contract as every other eval cycle in this house (`references/shared/decision-card-html.md`):

```bash
python3 ~/.claude/skills/routing-retro/scripts/aggregate_outcomes.py > /tmp/aggregate.json
python3 ~/.claude/skills/routing-retro/scripts/render_report.py /tmp/aggregate.json -o /tmp/routing-retro-report.html
```

- **Decision card, top of page, at most 3 proposed routing changes.** `render_report.py`
  ranks `aggregate.json`'s `proposals[]` (fired cell upgrades/downgrades + adoption-ready
  canaries) and shows the top 3; the rest are named and deferred, never silently dropped.
  Never present a 4th option or "it depends" prose in place of a pick.
- **Cell table inline** — every class/cell's N, first-attempt pass rate, escalation rate,
  and status, straight from `aggregate.json`'s `cells[]`.
- **Did-it-help check inline** — `aggregate.json`'s `did_it_help[]` (see step 3a below)
  renders as its own table: before/after N and pass rate per adopted proposal, always with
  a verdict (`helped` / `no_improvement` / `underpowered`).
- Share the rendered file path (or publish it as an Artifact if the environment supports
  it) — never a bare multi-page markdown dump in its place.

Below the one-pager, a short prose summary still applies:

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

### 3a. Did-it-help — did the last adopted change actually help?

`aggregate_outcomes.py`'s `did_it_help[]` compares, for every ADOPT recorded in
`evals/routing/adoptions.ndjson` (written by `/routing-update`'s apply step — see its
SKILL.md), the affected class's VERIFIED cohorts (gate-checked passed/exhausted) before vs
after the SSOT version that adoption produced — **by `ssot_version_ran`, never by calendar
date** [HARDENED:codex-verify-r3]: a source adoption isn't live in the ledger until
`gearbox deploy` ships it, so a record dated after the adoption but still carrying the OLD
`ssot_version_ran` belongs in "before", not "after". Canary cohorts for the same
`proposal_id` are reported separately in `canaries[]` (step 1b) — did-it-help is the
class-wide default-population comparison. Always report N on both sides; below the
aggregator's floor (`DID_IT_HELP_MIN_N`) on either side, the verdict reads "underpowered" —
report that honestly rather than picking a direction from too little data.

### 3b. Did the compaction work help? (per intervention, never "since the deploy")

The compaction changes (a PreCompact veto, a trimmed base context, a routing SSOT
bump, a three-repo diet) went live at DIFFERENT MOMENTS, and two of them usually
share one deploy. `~/.gearbox-state/compaction/activations.ndjson` is the sole authority on
when each went live — never `git log`, because a commit date is not a deploy date.

```bash
python3 ~/.claude/skills/routing-retro/scripts/retro_scan.py \
  --last 1500 --since YYYY-MM-DD --include-sdk > /tmp/retro-scan.json
python3 ~/.claude/skills/routing-retro/scripts/compaction_report.py \
  --scan /tmp/retro-scan.json \
  --plans-glob '~/.claude/_plans/*/_closeouts/*.json' -o /tmp/compaction.json
python3 ~/.claude/skills/routing-retro/scripts/render_report.py \
  --only-compaction --compaction /tmp/compaction.json -o did-it-help.html
```

- **Keep `--plans-glob` single-quoted.** The pattern must reach the script whole,
  so the shell expands neither the `*`s nor the `~`; the script expands `~` itself.
  Unquoted it becomes many argv entries; quoted WITHOUT the script expanding `~` it
  matched 0 files (vs 109) and the closeout half of the counter-metric silently
  rendered `n/a` — a measurement that reads nothing looks exactly like a clean result.
- `--include-sdk` is REQUIRED here and nowhere else: the compaction hooks fire in
  dispatched subagent sessions too, and the routing retro's default skip would hide
  most of the instrument's own denominator.
- **Exposure is per intervention.** A session is `before`, `after` or `spanning` for
  each intervention independently; `end == activation` is before, `start == activation`
  is after, and a session that straddles one belongs to NO cohort. One session is
  legitimately after for the hooks and before for base_context.
- **The denominator is sessions observed**, from `sessions.ndjson`, not compaction
  records — a successful rollout thins the ledger, so records alone cannot tell success
  from a hook that stopped firing. A scanned transcript with no heartbeat is `unknown`
  and is excluded from every verdict, with its count printed.
- **One vocabulary**: `helped` / `no_improvement` / `underpowered` / `confounded` /
  `no_baseline` / `no_exposure` / `activation_unknown`. `prediction_met` is a FLAG BESIDE the
  verdict, never one of those words: `true`/`false` only when a forecast and a
  realized number were both measurable, `null` (printed `n/a`) when no comparison
  was possible — a missing measurement is not a missed forecast.
  `underpowered` means the after side is still too small, and waiting helps;
  `no_baseline` means fewer than 6 observed sessions started before the activation —
  the before cohort closed when it went live, so waiting can never fill it;
  `no_improvement` means it answered no. `no_exposure` means the clock never started.
- **Give the next intervention a baseline.** Before you write an activation row, confirm
  at least 6 sessions with a heartbeat in `sessions.ndjson` started since the previous
  activation. All five August interventions went live with none (the heartbeat ledger
  began 28 minutes before the first), so they can never get a before/after verdict
  (retired 2026-09-22; the forward compact-turn re-measure still runs).
- **Say the attribution limit out loud.** Two interventions activated in one deploy
  have identical cohorts forever; more data cannot separate them. The report names the
  bundle rather than crediting a component, and the decision card offers the only fix:
  staggered re-activation, one intervention per deploy.
- **Known limit, unfixed by design:** neither `decisions.ndjson` nor `sessions.ndjson`
  records a working directory, so no ledger line can be attributed to a repository —
  a `repo_diet` cohort counts sessions that may never have opened the covered repos.

Before re-judging any deferred verdict, run the arming check — it says PER CRITERION
whether the evidence can exist yet, against that criterion's own intervention:

```bash
python3 ~/.claude/skills/routing-retro/scripts/arming_check.py --scan /tmp/retro-scan.json
```

It prints per-ledger reject counts, per-intervention before/after/spanning, per-criterion
ARMED, and one of `ANY ARMED` / `PARTIAL` / `BLOCKED`. `BLOCKED` is only for a genuinely
empty activation ledger; a malformed-only ledger is `activation_unknown` + `PARTIAL`.

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
