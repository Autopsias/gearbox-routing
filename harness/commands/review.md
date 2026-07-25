---
description: "[ROUTING ALIAS — demoted s08] /review now routes to a canonical reviewer. Deep dual-model review → /adversarial-review. Fast diff review → /code-review. See ~/.claude/SKILL-UNIFICATION-ROUTING.md."
argument-hint: "[--deep | --fast] [free-text scope hint]"
disable-model-invocation: true
---

# /review — routing alias (demoted in s08, kept as back-compat)

<!-- ROUTING (skill-unification s08, CP-01): demoted typed front door; routes to the
     canonical reviewers /adversarial-review (deep) and /code-review (fast).
     Routing table: ~/.claude/SKILL-UNIFICATION-ROUTING.md. s09 (CP-03 prune, 2026-06-21)
     completed WITHOUT removing this file — verified against
     _plans/<your-plan>-<date>/_closeouts/s09.json, whose 12 deleted files do
     not include review.md. This alias remains live by deliberate decision, not oversight. -->

This command was collapsed in the **skill-unification** program (session s08, CP-01);
`/review` routes into the two canonical front doors below so no capability is lost.

## Route

- **Deep / adversarial / dual-model** (Claude Opus + Codex, synthesis, plan-hardening):
  invoke `Skill(skill='adversarial-review')` — i.e. `/adversarial-review "$ARGUMENTS"`.
- **Fast / diff / correctness pass** (built-in harness reviewer):
  invoke the native `/code-review` skill — i.e. `Skill(skill='code-review')`.

### Selection

1. If `$ARGUMENTS` contains `--deep` (or a plan/architecture is in context, or the ask
   is "thoroughly / adversarially review"), route to **`/adversarial-review`**.
2. If `$ARGUMENTS` contains `--fast` (or the ask is a quick diff/PR correctness pass),
   route to **`/code-review`**.
3. Otherwise default to **`/code-review`** (fast) for a working-tree diff, and tell the
   user `/adversarial-review` is available for a deeper dual-model pass.

> **Why this exists:** there used to be ~8 typeable review entry points. They collapsed
> to 2 canonical ones. `/review` is preserved as a thin back-compat alias — s09 concluded
> without removing it (see the routing comment above). Do not add review logic here — route only.
