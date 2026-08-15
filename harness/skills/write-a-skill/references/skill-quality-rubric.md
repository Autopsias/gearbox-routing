# Skill Quality Rubric — permanent standard for creating and updating skills

## Contents
- [Provenance](#provenance)
- [How to apply](#how-to-apply)
- [Area 1 — TRIGGER](#area-1--trigger)
- [Area 2 — STRUCTURE](#area-2--structure-disclosure)
- [Area 3 — STEERING](#area-3--steering)
- [Area 4 — PRUNING](#area-4--pruning)
- [Post-edit gates](#post-edit-gates-updates-to-existing-skills)

## Provenance

Distilled from the 2026-07-05 skill-quality audit (plan: `_plans/<your-plan>-<date>/`,
full scoring machinery in its `rubric.md`). Encodes Anthropic's official skill-authoring
best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
(retrieved 2026-07-05). Key official numbers: SKILL.md body under 500 lines; reference files
over 100 lines need a TOC; descriptions state what + when, third person; one term per concept.
G5 added 2026-08-12 from "Lessons from building Claude Code: How we use skills"
(claude.com blog, 2026-06-03).

## How to apply

For every NEW skill and every UPDATE to an existing skill/command under ~/.claude, walk the
17 criteria below before finishing. Each scores 0/1/2; nothing ships at 0, target 2.
Hard separation rule: a TRIGGER problem is never fixed by a file split, and a STRUCTURE
problem is never fixed by rewording the description — those are two separate changes.

## Area 1 — TRIGGER

- **T1. Invocation mode is deliberate.** Decide user-invoked vs model-invoked explicitly.
  Destructive, expensive, or purely user-initiated workflows get `disable-model-invocation: true`
  (canary-test the flag first if the harness binary changed — bug anthropics/claude-code#43875
  once hid flagged skills entirely; verified absent on 2.1.201). Auto-triggering skills must
  earn their always-loaded description tokens.
- **T2. Description is a context pointer.** States what the skill does AND when to reach for
  it, in third person, containing concrete trigger phrases the user actually types. No
  "Helps with X".
- **T3. No over-triggering.** Keywords narrow enough not to fire on unrelated requests;
  check sibling skill descriptions for collisions before finalizing.
- **T4. Naming compliance.** `name` matches `^[a-z0-9-]+$`, ≤64 chars, no reserved words
  ("claude", "anthropic").

## Area 2 — STRUCTURE (disclosure)

- **S1. Size discipline.** SKILL.md under 300 lines by default; 300–500 only when content is
  provably always-needed; over 500 must split (official ceiling).
- **S2. Steps vs reference separated.** Always-needed workflow inline; API references,
  schemas, exhaustive tables, edge-case detail (>30-line reference-style blocks) pushed to
  `references/*.md`.
- **S3. References one level deep, TOC over 100 lines.** No reference-to-reference chains;
  every reference file >100 lines opens with `## Contents`.
- **S4. Fix-category discipline.** Structure fixes are file reorganizations, never
  description rewrites (and vice versa).

## Area 3 — STEERING

- **G1. Consistent terminology.** One term per concept through the whole file — no
  endpoint/route/URL drift.
- **G2. Calibrated leading words.** MUST/ALWAYS/NEVER only for true hard constraints;
  soft preferences use lighter language. All-caps everywhere dilutes the real constraints.
- **G3. No steering leakage.** If a step must be done blind (guess-then-check, independent
  review), isolate it — the next step's answer must not be visible in the same context.
- **G4. Checklists for 4+ step workflows.** Complex sequential workflows carry a copy-paste
  checklist so steering doesn't degrade over a long task.
- **G5. Failure knowledge captured.** A skill that wraps a tool, workflow, or external
  surface with observed failure modes carries a gotchas section built from what ACTUALLY
  went wrong (dated, specific), updated as new failures appear — per Anthropic this is
  "the highest-signal content in any skill". Score the mechanism, not the count: a young
  skill with no observed failures yet scores 2 by omitting the section, never by
  inventing hypotheticals.

## Area 4 — PRUNING

- **P1. Single source of truth.** No content duplicated across skills/commands; one owns it,
  others link. If a description cited by CLAUDE.md prose-routing tables or
  SKILL-UNIFICATION-ROUTING.md changes, update those surfaces in the SAME commit.
- **P2. No sediment.** No time-sensitive claims, dated workarounds, or references to
  superseded flows presented as current.
- **P3. Deletion test.** Every paragraph must change Claude's behavior if deleted; pure
  scene-setting prose is cut.
- **P4. Stamp lockstep.** Files carrying version stamps (e.g. `<!-- routing-ssot: vN -->`)
  stay in sync with their SSOT; if a stamped file is touched, run
  `~/.claude/scripts/verify-routing.sh --full`.

## Post-edit gates (updates to existing skills)

- Full coherence re-read after editing any high-risk/orchestrator skill.
- `bash ~/.claude/skills/.s08-verify-dangling-refs.sh` must pass after any batch of edits
  that moves or deletes files.
