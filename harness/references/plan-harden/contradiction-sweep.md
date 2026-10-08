# Phase 4.0c — the contradiction sweep: five deterministic checks

Read this from plan-harden.md's §4.0c (the mandate stays inline there; this file is the five
sweeps themselves). No model call. Run after EVERY batch of hardenings, including the ones
applied between verify rounds — not once at the end.

For each hardening applied in this run, the edit claims to REPLACE something. Assert that what
it replaced is gone:

0. **Run the builder's own sweeps first — they are free and deterministic.** A `build_plan.py`
   rebuild (or any `plan_mutate` mutation) now prints three advisories over the plan's own text:
   a session too wide to review, PROSE naming a session OR ITEM id the plan does not have, and a program
   embedded in a session prompt. The middle one is this sweep's class caught mechanically —
   a split or retired session whose old id survives in a sibling's text. Read them before
   spending a model round on the same question. Definitions + evidence:
   `skills/plan-builder/references/schemas.md` -> "Build-time advisories on a plan's own text".

1. **Deleted-phrase sweep.** For every hardening whose fix removed or superseded a rule, grep
   the WHOLE artifact for that rule's distinctive phrasing. A surviving occurrence outside the
   new text (a deliberate negation like *"there is no X"* is fine — allow-list those explicitly)
   is a finding, at the severity of the original hardening.
2. **Item-vs-prompt sweep.** For a plan-builder plan, assert no `items[].deliverable` /
   `human_summary` / `why` contradicts the session prompt that consumes it. The common shape is
   a prompt made branch-conditional while its item still demands the work unconditionally.
3. **Field-vs-prose sweep.** Assert every `verify.checks[].assert`, `deliverable` and
   `dispatch.checkpoint` is satisfiable on EVERY branch the prompt permits.
4. **Schema-vs-writer sweep.** Where a record shape is declared in one place and written in
   another, assert the field lists match exactly.

Record the sweep's result in the Phase 4 summary as `sweep ✓ N classes clean` or list the
survivors. **A sweep that finds nothing must be probed with a known positive** before you trust
it — a sweep pointed at the wrong string returns "clean" and "I did not look" identically.

*Why:* measured across three hardening passes on one plan (2026-08-21), *a fix applied in one
place while the text it replaces survives elsewhere* caused the MAJORITY of findings in every
single round — including two contradictions the hardening patches themselves introduced, and
the round-3 fix that forced an entire second pass to exist. An ad-hoc version of this sweep
caught 9 findings that neither reviewer model found.
