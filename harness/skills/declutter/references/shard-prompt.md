# Shard auditor prompt template + lead verification

## Contents
- [The four classes (single source of truth)](#the-four-classes-single-source-of-truth)
- [Prompt template](#prompt-template)
- [Filling the placeholders](#filling-the-placeholders)
- [Lead verification checklist (Phase 2)](#lead-verification-checklist-phase-2)

## The four classes (single source of truth)

- **SAFE-DELETE** — unreachable, uncalled, or a byte-duplicate of the live
  copy. Deleting it cannot change behavior. Requires caller-proof: the grep
  that shows nothing reaches it (per the reachability rules in
  [recon-toolkit.md](recon-toolkit.md)).
- **SIMPLIFY** — live code that collapses to something smaller with identical
  behavior: copy-pasted blocks, single-implementation abstractions,
  hand-maintained mirrors of framework features, dead parameters.
- **OPERATOR-GATED** — parked by a recorded decision (flag OFF by ruling,
  program closed with levers kept), or deletion needs owner knowledge (did
  this migration run?), or rare-but-critical shape (incident tooling,
  year-end jobs). The audit sizes it; only an operator ruling unlocks it.
- **WIRING-BUG** — code whose wiring contradicts its intent or documentation:
  an ungated debug route, a docstring claiming prod wiring that does not
  exist, config whose documented state contradicts its deployed state.

## Prompt template

```text
You are a READ-ONLY overengineering auditor for the repo at {REPO_ROOT}.
Do NOT edit any file.

SCOPE: {SHARD_PATHS} ({SHARD_LOC} LOC)

PROJECT CONTEXT (prevents false positives — read first):
{PROJECT_CONTEXT_BRIEF}

RECON CANDIDATES in your scope (verify each — the heuristics over-trigger):
{RECON_CANDIDATES}

MISSION: find where this area is overengineered — simpler with zero loss of
functionality or performance. Hunt:
1. Dead/unwired code. Verify with grep across app code, scripts, AND tests —
   a module referenced only by its own tests is still dead.
2. Flag graveyards: code behind flags OFF in both defaults and production
   config. Distinguish parked-by-ruling (OPERATOR-GATED) from
   never-referenced (SAFE-DELETE).
3. Single-implementation abstractions: interfaces/factories/strategies with
   one concrete member, wrappers that only delegate.
4. Duplicated logic: parallel implementations of the same job.
5. Hand-maintained registries or metadata that a framework generates.
6. Wiring that contradicts documentation or intent (WIRING-BUG).

CLASSES: SAFE-DELETE / SIMPLIFY / OPERATOR-GATED / WIRING-BUG
{CLASS_DEFINITIONS}

OUTPUT (final message, raw markdown, no preamble): max 15 findings ranked by
LOC impact, each:
- `F<n> | <class> | ~<LOC> LOC | <paths>`
- One sentence what it is; one sentence evidence (file:line + the grep that
  proves who calls it or that nothing does); one sentence simplification.
Then: candidates you VERIFIED AS NOT DEAD (with the caller that saved them).
End with: total SAFE-DELETE LOC and total OPERATOR-GATED LOC.
```

## Filling the placeholders

- `{PROJECT_CONTEXT_BRIEF}` — from Phase 0: parked programs and their rulings,
  standing rules, recently-fixed subsystems ("X was just fixed — it is LIVE,
  do not flag"), in-flight work owned by other sessions ("do not touch Y").
  This brief is the main defense against the two classic false positives:
  operator-parked code flagged as trash, and just-fixed code flagged as dead.
- `{RECON_CANDIDATES}` — only the recon hits inside this shard's scope.
- `{CLASS_DEFINITIONS}` — paste the four class definitions above verbatim.
- Dispatch with `model: sonnet`. All shards in one message, in background.

## Lead verification checklist (Phase 2)

- [ ] Every headline-number finding re-verified by running the grep or
      registration check yourself.
- [ ] Every auth/security-adjacent finding re-verified (route registration,
      middleware wiring) — these become P0 items downstream.
- [ ] Every finding that contradicts project memory re-verified; where memory
      is stale, note the correction for the memory record.
- [ ] Cross-shard conflicts resolved (shard A deletes what shard B relies on).
- [ ] False-positive ledger assembled from every shard's "verified NOT dead"
      section plus your own clears.
- [ ] Any ad-hoc check you wrote was probed with a known positive before its
      all-clear was trusted.
