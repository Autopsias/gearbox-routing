---
name: memo-loop
description: "Critique a decision memo against a rubric compiled for that memo, or draft one from scratch. Use for \"critique my memo\", \"what's wrong with this memo\", \"review this draft\", \"poke holes in this\", \"make this argument stronger\", \"create a memo about X\", \"write me a decision memo\". Returns a CRITIQUE, never a rewritten memo \u2014 you do the rewriting. The `reflect` mode is human-gated and runs ONLY when explicitly asked for. NOT for pre-send verification \u2014 \"is this board-ready\", \"prep this board pack\", \"run the trust review\", \"is this safe to send\" belong to a separate pre-send review step."
---

# memo-loop — measured critique for decision memos

Anything can make a memo *look* finished. This finds what is actually wrong with one, with evidence:
every run compiles a rubric for **this** memo, scores it, hands it to a critic that **cannot edit it**,
re-runs every checkable finding itself and throws out the ones that do not reproduce, then ends in a
**typed** terminal state that distinguishes "nothing material found" from "the critic never ran".

> **It returns a critique, not a rewritten memo.** You rewrite — or hand the findings to a rewriter of
> your choosing. memo-loop used to revise automatically; that loop was measured against single-pass
> rewrites twice and lost both times, so it was descoped. Everything the eval showed
> working is still here.

**Files are the only state.** Every pass reads disk and runs in a fresh context; nothing is carried in
conversation history. That is what makes the fresh-context critic provable rather than asserted.

## Modes

| Mode | Invocation | What it does | Writes |
|---|---|---|---|
| `improve` | `memo-loop improve <path>` | Critique an existing draft: intake → rubric → anchor score → independent critique → discard the unconfirmed → **critique deliverable**. Your memo is never edited. | run dir, lessons entries |
| `create` | `memo-loop create <topic>` | Evidence gate → draft (council by default, `--no-council` for one writer) → hands the merged draft to the **critique deliverable**, so a generated memo is criticised before you see it. | run dir, lessons entries |
| `reflect` | `memo-loop reflect` | The outer loop: batched, human-gated promotion of repeated lessons into standing rules. **Explicit invocation only.** | promotion overlay, `reflect-state.json` |
| `status` | `memo-loop status` | Read-only. Resolved backends, recent runs and their terminal states, pending promotions, graduation candidates, runs parked on an external reviewer. | nothing |

`status` mutates nothing. `reflect` is the only mode that writes standing rules, and it never runs
because a phrase sounded like it — the user asks for it by name.

Every `improve` / `create` run appends lessons cheaply and without judgement; **everything editorial
happens in `reflect`, in batch, in front of the human.** Observe cheaply, promote rarely.

## Non-negotiables

These are hard constraints, not preferences. Each is enforced by a mechanism, not an instruction.

1. **The critic is independent and cannot edit.** It runs in a fresh context on a different model
   family where the transport allows, and it **enumerates** — it never rewrites. On the CLI that is a
   read-only sandbox; in a tool-capable runtime it is an authoritative allow-list. When true
   cross-vendor critique is unavailable, the run says so in the critique report in the mandated
   words and **never** presents a same-family critic as cross-model.
2. **A malformed critic response is NEVER read as zero findings.** `findings: []` is a clean pass only
   inside a complete envelope whose `rubric_items_evaluated` shows `n == of == item_count` and whose
   independence hashes match the ones the orchestrator itself computed. Anything else is a
   transport error: retry once at the same rung, then demote a rung and record it, and try again there.
   Only when **no** rung returns a well-formed envelope does the run terminate `ABORTED`. A silent zero-findings read is the single failure this skill exists to prevent.
   **The one exception is `invalid_json_schema`** — the transport rejecting *our own* schema before the
   model ran. It is not a transport error, it will repeat identically on every rung, and it is never
   retried, demoted or quietly downgraded to post-read validation: abort with
   `terminal_reason: "critic-schema-invalid"` and report the rejection verbatim.
3. **This skill never edits a memo you gave it.** Scope, stated exactly, because `create` does write
   prose: the rule binds **any text supplied as input** — in `improve`, the file at the path you named.
   It is frozen at intake and, when the run ends, **both the run copy and the file at your path must
   re-hash to their intake hashes**. The orchestrator opens your file read-only; hashing only the copy
   would prove we preserved our own snapshot, not your memo. There is no revision, no diff budget and no
   containment check, because there is nothing to contain — the deliverable is a critique.
   In `create` the skill authors council drafts and a chairman merge; those are **generated artifacts,
   never an input memo**. Once the merge is frozen as `original.md` it becomes input and this rule binds
   it too — from that moment nothing rewrites it, including the phase that produced it.
4. **One critique pass.** Intake → rubric → anchor score → critique → discard gate → deliverable. No
   rounds, no convergence, no re-critique of a draft the skill wrote. If the author rewrites and wants
   a fresh opinion, that is a **new run** on the new text, with its own frozen anchor and its own
   rubric — which is what makes the second opinion independent rather than anchored on the first.
5. **Never write inside the installed skill directory**, in either harness. Before any write, all four
   targets (run state, lessons, calibration, promotions) resolve to absolute symlink-resolved paths and
   are asserted outside `~/.claude/skills/memo-loop/` and `~/.codex/skills/memo-loop/`. A violation is a
   hard abort. Reads from the skill directory are fine; writes are what break the deploy.
6. **The critic sees four things and nothing else:** the memo, the compiled rubric, the calibration
   exemplar, and the enumerate-only directive. Lessons, preferences, the voice profile, the
   rejected-decisions log **and every promoted rule** are orchestrator-side only. Never tell the critic
   what to stop flagging — triage its findings through a visible waiver that names the memory entry as
   its rationale, where the human can see it and disagree.
7. **Nothing becomes a standing rule without an explicit affirmative.** A lesson seen once is recorded
   and never promoted. A candidate must clear 2+ occurrences (counted as distinct runs), survive a 3×
   sample intersection, pass a held-out eval gate — or carry a deliberately stamped `--ungated` override
   when the calibration archive is too thin to run one — and then be **named** by the human in the
   decision card. "No objection", silence, and an ambiguous reply all promote **nothing**. Every promotion runs
   backup → schema-validate → atomic replace → dated changelog → re-read-and-validate, with
   rollback-on-malformed; rules are length-capped and decay to `retired/` rather than being deleted.
8. **No silent auto-accept.** A run ends `CLEAN` (zero blocking/material findings **and** every
   checkable item actually verified), `CLEAN_UNVERIFIED` (zero findings but some check could not run —
   ships with a banner naming the count, because a run that verified nothing must never look like a run
   that verified everything), `FINDINGS_RAISED` (at least one — the normal outcome, and not a failure),
   or `ABORTED` (no rung returned a well-formed envelope, or the transport rejected our own schema —
   parked for the human, never presented as a clean pass). A run waiting on an external reviewer is in
   a non-terminal state and carries no terminal state at all. **`CLEAN` never means the memo is good**;
   it means this rubric and this critic found nothing material.

## Phases

Run them in order. Each phase's contract lives in one reference file — read it when you reach the
phase, not before.

| Phase | What happens | Contract |
|---|---|---|
| 0 · Intake + rubric | Assert the write targets, resolve backends, create the run dir, freeze the original as the permanent anchor (hashing both it and your source file), build the claim→section map, ask at most 5 clarifying questions, compile the rubric, score the anchor. | `references/intake-and-rubric.md` |
| 1 · Critique | Probe the transport, dispatch the critic against the rubric, validate the envelope, run tool checks on `[checkable]` items and discard the unconfirmed ones before they reach the author. | `references/critique.md` |
| 2 · Deliverable | Assemble the critique: rubric, anchor score, located findings by impact, the published discard list, `[judgment]` items as a decision list, citation status, independence and residual bias. Nothing is edited. | `references/critique-report.md` |
| 3 · Handoff + lessons | Write the critique report with its terminal state and the residual-bias line verbatim, emit `trust-handoff.json`, archive the critique exemplar (never on `ABORTED`), append this run's lessons. | `references/critique-report.md`, then `references/reflect.md` §2 for the lessons append |
| `create` only | Perspective discovery → outline, evidence gate before drafting, then 2-3 anonymized council drafters ranked pairwise (position-swapped) and merged by a chairman — or one writer under `--no-council`. A second evidence gate re-validates after the merge. The merge is then **criticised**, not accepted. | `references/council.md` |
| `reflect` only | Batched promotion: 3× sampling and intersection, the held-out eval gate, the human decision card, the atomic overlay write. | `references/reflect.md` |

Supporting references, read only when the phase calls for them:
`references/run-state.md` (every run-dir file and its template — `run.json` first),
`references/rubric-backbone.md` (the candidate-item pool) and
`references/calibration-exemplar.md` (the shipped bootstrap exemplar).

`status` has no contract file of its own: it only reads what the other phases wrote — `run.json` per run
dir, plus the pending promotions, ungated rules and graduation candidates in `reflect-state.json`
(`references/reflect.md` §7) and the fault-injected runs flagged in `references/critique.md`.

## Checklist

Copy this into the run and tick it — the steering degrades across a multi-phase run otherwise.

```
[ ] Startup assertion passed: no write target under either skill tree
[ ] Backends resolved and recorded, including which rung answered
[ ] original.md frozen + hashed; at the end, BOTH it and the user's source file re-hash to intake
[ ] Clarifying questions asked one at a time, cap 5, each pre-filled
[ ] Rubric compiled: 10-15 binary items, item_count frozen, anchor scored by a non-authoring grader
[ ] Transport probed, envelope validated, n == of == item_count, independence hashes matched
[ ] Every [checkable] finding re-run by the orchestrator; unconfirmed ones discarded AND published
[ ] Citations checked once: present / verified / flagged, with a reason on every flag
[ ] Deliverable assembled: findings by impact, discard list, [judgment] decision list, residual bias
[ ] Terminal state typed and reported, with the residual-bias line verbatim
[ ] No input memo edited, no rewrite suggested, no patch offered (create: drafts are generated
    artifacts, and nothing rewrites the merge once frozen)
[ ] Lessons appended; critique exemplar archived (never on ABORTED, never under fault injection)
[ ] create only: outline scored before drafting; evidence gate A passed before drafters saw sources
[ ] create only: drafting_path recorded; evidence gate B run on the merge; merge then criticised
```

## Boundary with pre-send review

> **memo-loop criticises and drafts. A separate pre-send review step verifies before sending.**

memo-loop never re-implements travel-class routing, egress sanitisation, source-packet construction, or
office-format inspection — those belong to a pre-send review step and stay there. A board-bound memo
finishes its improvement here and then hands off: memo-loop writes `trust-handoff.json`, so the memo
enters pre-send review without translation.

When a request could be read either way, the deciding question is **what the memo needs**: making the
argument better is memo-loop; deciding whether a finished argument may be sent is pre-send review.
