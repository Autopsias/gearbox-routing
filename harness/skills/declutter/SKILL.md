---
name: declutter
description: >
  Deep, evidence-gated overengineering audit of a whole repository: deterministic
  dead-code recon, then area-sharded read-only auditors that classify every finding
  SAFE-DELETE / SIMPLIFY / OPERATOR-GATED / WIRING-BUG with caller-proof evidence,
  lead-verified, delivered as a one-pager artifact plus a ≤3-option decision card —
  and optionally handed to /plan-builder as an executable cleanup plan (--plan).
  Use when the user types "/declutter", "deep overengineering audit", "find
  everything we can delete, with evidence", "simplify this project without losing
  functionality", or "systematic dead-code audit". NOT the cheap one-shot scan
  (that is ponytail-audit), not size/complexity metrics (/code-quality), not diff
  review (/code-review), not interface deepening (improve-codebase-architecture).
  The deep tier of /repo-health's over-engineering review: /repo-health, the front door
  for whole-repo reviews, runs the quick scan and names this audit when it is worth the cost.
disable-model-invocation: true
---

# Declutter — evidence-gated overengineering audit

Audits a whole repository for code that costs more than it earns: dead code,
flag graveyards, hand-maintained mirrors of framework features, duplicate
pipelines, single-implementation abstractions. Every finding carries evidence a
reviewer can check. The audit itself **applies nothing** — its outputs are a
report, a decision card, and (with `--plan`) an executable plan whose sessions
do the deleting under gates.

**Cost warning:** the fan-out phase dispatches one subagent per repo area
and each one reads its whole area, so a large app is expensive. Scale the shard count down for small repos; do not skip the
verification phase to save tokens — unverified findings are the failure mode.

## Invariants (hold for the whole run)

- **Read-only.** No file edits, no deletions, no flag flips — in any phase.
  `--plan` produces a plan; humans and gated plan sessions do the applying.
- **Four classes, defined once** in [references/shard-prompt.md](references/shard-prompt.md):
  SAFE-DELETE (unreachable/uncalled/duplicate — deleting it cannot change
  behavior), SIMPLIFY (live but collapsible, behavior preserved), OPERATOR-GATED
  (parked by a recorded decision, or deletion needs owner knowledge — never
  auto-deletable), WIRING-BUG (code whose wiring contradicts its intent or its
  documentation).
- **Evidence contract.** A finding without `file:line` plus caller-proof (the
  grep that shows who calls it, or that nothing does) does not go in the report.
- **Fan-out pins Sonnet.** Shard auditors run `model: sonnet`. Never fan out on
  the orchestrator's model when it is Fable.
- **Deliverable shape is fixed:** one rendered one-pager with the data inline,
  plus a decision card of at most 3 options with a marked recommendation. Never
  a narration wall.

## Checklist (copy into the run)

- [ ] 0. Recon: deterministic tooling + project-context brief
- [ ] 1. Fan-out: area shards, read-only, taxonomy + evidence contract
- [ ] 2. Verify: lead re-checks headline claims; false-positive ledger
- [ ] 3. Deliver: one-pager + decision card + memory record
- [ ] 4. `--plan` only: rulings interview → /plan-builder handoff

## Phase 0 — Recon (deterministic, cheap, before any agent)

Run the recipes in [references/recon-toolkit.md](references/recon-toolkit.md):

1. **Size map** — LOC per top-level package, app vs tests vs scripts. This
   drives shard partitioning and puts every later number in proportion.
2. **Dead-code tooling** for the repo's language (vulture for Python, knip or
   ts-prune for JS/TS — full matrix in the toolkit). Tool output is *candidate
   input* for shards, never reported directly.
3. **Unreferenced-module scan** — modules no import statement anywhere reaches.
4. **Flag cross-check** — inventory feature flags; for each: default value,
   runtime/prod config value, and whether any non-config code reads it. Flags
   read by nothing and flags OFF everywhere mark the flag-graveyard candidates.
5. **`/code-quality --check`** (or the project's equivalent) if available —
   its size/complexity violations feed the shards as context, not as findings.
6. **Project-context brief** (one paragraph + bullet list, reused verbatim in
   every shard prompt): read the project's CLAUDE.md and memory for parked
   programs, standing rules, operator rulings, recently-fixed subsystems, and
   in-flight work other sessions own. This brief is what prevents the two
   classic false positives: flagging operator-parked code as trash, and
   flagging just-fixed code as dead.

## Phase 1 — Fan-out (area shards)

Partition the repo into shards of roughly 15–25k LOC along top-level package
boundaries, capped at ~10 shards. Two shards are always separate regardless of
size: **config/flags** and **test infrastructure** (suites, fixtures, CI
wiring — not per-test correctness). Small repos may need only 2–3 shards.

Dispatch all shards in one message as background agents, each `model: sonnet`,
each with the prompt template from
[references/shard-prompt.md](references/shard-prompt.md) filled with: its
scope paths, the project-context brief, the recon candidates that fall in its
scope, and the output format. Shards are read-only and must re-verify every
recon candidate before classifying it — the recon heuristics over-trigger by
design.

## Phase 2 — Verify (the lead's own pass)

Findings drive deletions later; a wrong headline claim poisons the whole run.
Before writing the report:

- Re-verify every finding that (a) drives a headline number, (b) touches
  auth/security surface, or (c) contradicts project memory — by running the
  grep/registration check yourself, not by trusting the shard.
- Keep a **false-positive ledger**: every candidate a shard or your own pass
  cleared goes in the report ("verified NOT dead: …"). It is what makes the
  next audit — and the cleanup plan's re-verification steps — cheaper.
- If you wrote an ad-hoc check, probe it with a known positive before trusting
  its all-clear.
- Cross-shard sanity: two shards independently finding the same duplicate is
  confirmation; one shard claiming a module another shard relies on is a
  conflict to resolve now.

## Phase 3 — Deliver

1. **One-pager artifact** (data inline): headline totals by class, per-area
   table, wiring defects with evidence, structural findings, root-cause list
   ("why it happened" — recurring: removal triggers written but never fired,
   closed programs never pruned, hand-maintained framework mirrors, mechanical
   rule-obeying splits), and the false-positive ledger.
2. **Decision card, ≤3 options,** recommendation marked, plus "if you do
   nothing". Typical shape: (A) mechanical purge only, (B) purge + structural
   simplification as a plan, (C) file the report.
3. **Memory record** (project memory, `type: project`): the verified
   inventories, the rulings still open, artifact URL. The cleanup plan's
   sessions will cite it.

## Phase 4 — `--plan` (only when the operator picks a build option)

1. **Rulings interview first.** Every OPERATOR-GATED block needs a ruling.
   Ask the cheap ones now via one AskUserQuestion batch (with a recommended
   option each); the laborious ones (e.g. "did these 40 migration scripts all
   run?") become research-session → human-checkpoint pairs inside the plan.
2. Hand off to `/plan-builder` with this session shape: purge sessions first
   (re-verify before every deletion; skip-and-record when a new caller
   appeared), then structural sessions, then adjudication, then a closing
   acceptance review. Every session: deterministic verify gates (tests,
   lint/type gate, the project's offline eval gate where retrieval/scoring is
   touched), `commit-push` shipping, and parity evidence
   (settings dumps, route-table diffs, collection counts) wherever a value
   could silently change. Deploys stay outside the plan unless the operator
   says otherwise.

## Boundaries and siblings

| Intent | Skill |
|---|---|
| One review of the whole repo: health, security, refactoring, over-engineering, performance | `/repo-health` — the front door; its over-engineering review runs `ponytail-audit` as the quick scan and names this skill for the deep audit |
| Cheap one-shot bloat scan, no evidence, no fan-out | `ponytail-audit` — run it first when unsure the deep audit is warranted |
| File size / function length / complexity metrics + refactor dispatch | `/code-quality` — feeds Phase 0 |
| Review a diff or PR for bugs | `/code-review` — reviews the cleanup diffs this skill causes |
| Clean up just-changed code | `/simplify` |
| Deepen module interfaces / seams | `improve-codebase-architecture` |

This skill never edits, never deletes, never flips flags, and never dispatches
its own fixes — the plan it emits does, under gates the operator approved.
