# Shared grilling shell: interview opening + adversarial-review closing

Canonical text shared by `/grill-me` and `/grill-with-docs` — both skills run the same
interview discipline and the same mandatory post-interview adversarial-review step;
`/grill-with-docs` additionally does CONTEXT.md/ADR side effects during the interview
(its own file covers those). Read this when you need the exact wording either skill
uses; each skill's own SKILL.md states only its delta on top of this shell.

## Interview opening (shared)

Interview me relentlessly about every aspect of this plan until we reach a shared
understanding. Walk down each branch of the design tree, resolving dependencies
between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead.

## After the session — adversarial review (default, shared)

Reaching shared understanding is Act 1, and it's still single-model — you proposed, I
agreed. A plan only one model has looked at is an echo chamber; it can't grade its own
work. Before treating the design as ready, hand it to a different model (Codex):

1. **Consolidate** the agreed plan/design into a single artifact:
   - If a plan-mode plan file exists, that's the artifact.
   - Otherwise write the consolidated plan to `~/.codex/plans/grill-<short-slug>-<date>.md` and tell me the path.
   - If the interview produced no concrete plan worth reviewing, skip the rest and say so.
2. **Invoke `/adversarial-review`** on that artifact:
   `Use $adversarial-review with args: Review the plan at <path>. Apply hardenings inline.`
   It runs a dual-model review (Claude + Codex) and then loops with Codex (read-only,
   capped) until it verifies the fixes actually land — all by default.
3. **Report back**: the hardened plan path, the verify-loop outcome, and any findings
   left unresolved at deadlock.

**Exception:** skip this Act 2 only if the invocation args contain
`DEFER_ADVERSARIAL_REVIEW: true` (a caller such as /plan-harden runs the dual-model
review itself downstream), OR `/adversarial-review` is not available in this
environment (then say so and stop — do not fabricate a review).
