# Phase 2.1b — `targeted` review mode: one unit at a time

Read this from plan-harden.md's §2.1b (the selection rule and the size threshold stay inline
there; this file is the procedure). Selected by §2.0b when the review surface is large, and
ALSO whenever a whole-document verify round answers `## Convergence` with "different latent
defects / another round would not close this document" (`scope-too-wide`).

1. **Pick the units.** For a plan-builder plan, a unit is ONE SESSION plus the items it
   declares. Review the sessions the hardening actually touched, plus any session named in an
   unresolved finding — not all of them.
2. **Extract the contracts block ONCE, verbatim.** The cross-unit invariants every unit must
   obey (shared file schemas, vocabularies, precedence rules, branch definitions), quoted from
   whichever unit is their authority. Aim for ≤ 15 KB. This is what lets a scoped reviewer
   catch a unit contradicting something it cannot see.
3. **Build one prompt per unit**: the unit's fields + its items + the contracts block +
   `RESOLVED_LEDGER`. Nothing else. Ask for exactly these defect shapes — they are what a
   scoped reviewer is uniquely good at:
   - two statements inside the unit a builder cannot both satisfy;
   - a `deliverable` / `verify.checks[].assert` / `dispatch.checkpoint` requiring something the
     unit's own prompt forbids, or demanding unconditionally what the prompt makes conditional;
   - the unit contradicting a contract, or depending on something no unit builds;
   - an ITEM field contradicting its SESSION prompt;
   - a code snippet that does not do what its own surrounding prose says.
   Require a VERBATIM quote per finding — two when it is a contradiction — and discard
   paraphrase-only findings.
4. **Run the units in parallel** through `codex_supervised.py` (one background call each, no
   nested `&`). Wall-clock is one unit, not the sum.
5. **Apply, then sweep (§4.0c), then re-review the same units once.** Falling findings per unit
   is the convergence signal; a unit still climbing is telling you its findings are about
   embedded code, which review does not close — see Rule 13.

## When a unit will not converge no matter how you review it

Two sessions in the measured run never settled, for two different reasons, and the remedy
differed each time — neither was "review it again":

- **s02 — many concerns at once.** Rounds ran 8 → 5 → 2 → 4: the count stopped falling and
  turned back up, which is the signature of a reviewer sampling a different slice each round
  rather than closing the same one. Beware the tempting explanations — BOTH were falsified in
  the same run. s02 is 21,501 chars, but s07 at 19,171 converged (7 → 1). s02 writes 5 distinct
  files; s07 writes 7 and converged. What is actually unusual about s02 is that it is the only
  MULTI-ITEM session (4) and the only one with two mutually exclusive branches. That is n = 1 —
  a hypothesis for the decision card, not a threshold.
- **s09 — embedded code.** 8 → 8 → 6 across three rounds, every finding about the same 6.6 KB
  Python program living inside its markdown prompt. Deleting the program and rehoming it as a
  tested script owned by another session closed ALL SIX at once, and what remained was ordinary
  prose.

Feed both into §4.0d's reviewability lint. The empirical signal — findings failing to fall
across two consecutive rounds on the same unit — outranks any static threshold.

## Why this mode exists (measured 2026-08-21, a 107 KB spec)

Whole-document new findings per round ran 19 → 10 → 1 → 1 across one pass and 12 → 6 → 13 → 9
across the next — no convergence, and the second pass's first round found a CRITICAL inside
the fix the first pass had ended on. A targeted pass on the same plan returned **25 findings
in one parallel round against the whole-document loop's 9**, every one an internal
contradiction four whole-document rounds had read past. On re-review the session it was aimed
at went **7 findings → 1**.

Two sessions did NOT converge, and the reason is the useful part: their remaining findings
were about EMBEDDED CODE. Scope-limiting fixes a reviewer's attention; it does not make a
prose reviewer good at auditing a snippet. That half is closed by plan-harden Rule 13 —
execute the code against a known positive and per-branch negatives — not by another round.
