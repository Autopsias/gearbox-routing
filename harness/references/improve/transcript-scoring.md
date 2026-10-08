# Transcript scoring — agent-side waste and artifact quality

Adapted 2026-08-28 from `skill-doctor` in `warpdotdev/common-skills` (MIT). The
never-fired-skill check is NOT here — that is `relevance-audit.md` Step 3 (failed
triggers); route those findings there, never duplicate them.

The main command's Phase 3 signal table and the History Scan agent both read USER
messages: corrections, praise, friction. This file adds the other side — what the
AGENT did. A session can contain zero user complaints and still show waste worth a
config change: rework cycles, redundant reads, serial calls, flailing. Wherever this
file is invoked, score each session against the two rubrics, then convert per "From
score to finding".

## Rubric 1 — session waste

Judge the full cost of reaching the result against what a competent engineer with the
same tools would have needed. One label per session:

| Label | Means |
|---|---|
| clean | direct path: nothing re-read or re-run, independent steps batched, no rework |
| minor slips | one or two: a duplicated read, an early retry — no knock-on cost |
| repeated waste | waste recurred, or a rework round an earlier check would have prevented |
| dominated by waste | same defect reworked across cycles, repeated user correction, extended flailing |

Sources of waste to look for (common ones, not an exhaustive checklist):

- **Rework**: work redone because it was wrong the first time — a failure a local
  check would have caught, edits to the wrong file, a misread requirement reverted.
- **Cost to the human**: repeated correction or steering. A question asked up front is
  cheap; the same question asked after building the wrong thing is not.
- **Redundant gathering**: re-reading, re-running or re-searching something already
  found; reading a file end to end when a targeted search answers it.
- **Routine-step overhead**: a roundabout path through a standard, repeated part of
  the job. Weight it beyond the one run — the same overhead recurs every session
  until a skill or rule fixes the pattern.
- **Serial calls**: independent reads or searches run one per turn instead of batched.
- **Flailing**: retrying a failing approach unchanged, or guessing when reading the
  code or docs would have settled it.
- **Late verification**: checks deferred until after "done", or re-run redundantly.
- **Slow navigation**: many calls to find a file or fact that one pointer, in a file
  the agent already reads, would have given.
- **Costly tool call**: a call whose output cost far more tokens or time than it gave.
- **Missing information**: the agent guessed because it could not reach a log, a
  service or a state that a read-only path would have shown.
- **Check not run**: a mistake that a check in the repo would have caught, but nothing
  ran the check, the check was broken, or the repo has no such check.

## Rubric 2 — artifact quality (code sessions only)

Apply only where the session shows code edits; otherwise skip it for that session.
The verdict is binary: would a careful senior reviewer merge the change as-is
(**approve**), or insist on a fix before merging (**block**)? One real defect is
enough to block. Judge the artifact against the target repo's own conventions:
correctness including edge cases and races; unneeded complexity; smells (magic
values, copy-paste, swallowed errors, symptom patches); tests that would actually
fail on a broken implementation; diff hygiene (debug prints, scratch files left in).
A defect the user had to point out counts against the artifact even when it was
fixed afterwards.

## From score to finding

A scored session is evidence, not a finding. Convert with this bar:

1. Only **repeated waste** or worse, and **block**, generate finding candidates.
2. For each candidate, name the moment: the tool call, edit, or exchange where the
   waste or defect happened — quoted verbatim, cited with its session. That is the
   excerpt Phase 5 gate 1 demands.
3. Name the owning config surface (skill, rule, hook, memory) and the one reusable
   rule it should state. Then apply the test: **would a competent agent with the
   current instructions still fail this way?** If the instruction already required
   the right behavior, that is model variance, not a gap — no finding. If the real
   fix is code or infrastructure (a pointer, a check, a cheaper tool, read access),
   propose it as **"Remove the cause"** (`commands/improve.md` §4a) with the exact
   change; never turn it into a rule or a memory note.
4. If a skill exists that covers the wasted step and never fired, that is a failed
   trigger — route it per `relevance-audit.md` Step 3, not as a new rule.
5. Survivors enter Phase 4d categorization and the Phase 5 gates like any other
   finding. A new-rule candidate still needs gate 2's two sessions.
