# Phase 2/3 — the critique deliverable, terminal states, and the handoff

**memo-loop does not rewrite memos. It tells you, precisely and with evidence, what is wrong with one.**
The author rewrites — or hands the findings to a single-pass rewriter of their choosing.

`intake-and-rubric.md` owns the compiled rubric and the anchor score; `critique.md` owns the critic, the
transport ladder and the findings. This file owns what is *done* with them: the deliverable, the typed
terminal state, the handoff to pre-send review, and the lessons append.

> **Why this file replaced a revision loop — read once, then never re-litigate it.**
> An earlier version of this skill ran an automated multi-round revise-and-converge loop. Blind
> comparisons against single-pass rewrites of the same memo, on the same frozen rubric, showed it
> scoring lower even at matched or greater compute, so the loop was descoped. What the eval showed *working* — the rubric, the cross-vendor
> critic, the discard gate, the typed states, the human gate — is all still here.

## Contents
- [Step 1 — Assemble the critique deliverable](#step-1--assemble-the-critique-deliverable)
- [Step 2 — Citations: verified once, reported plainly](#step-2--citations-verified-once-reported-plainly)
- [Terminal states](#terminal-states)
- [The critique report](#the-critique-report)
- [The abort report](#the-abort-report)
- [Phase 3 — handoff, archive, lessons](#phase-3--handoff-archive-lessons)
- [Dry-run trace — a 10-line memo to a deliverable](#dry-run-trace--a-10-line-memo-to-a-deliverable)

---

## Step 1 — Assemble the critique deliverable

Everything below already exists by the time you get here. This step **arranges** it; it invents nothing
and edits nothing.

| Part | Source | Rule |
|---|---|---|
| The compiled rubric | `intake-and-rubric.md` Step 6 | Verbatim, with `rubric_sha256` and `item_count`. The reader must be able to see what the memo was judged against. |
| The anchor score | `intake-and-rubric.md` Step 8 | The frozen original's score, with its grader provenance block. This is the **only** score in the deliverable — there is no second draft to compare it to. |
| Located findings | `critique.md` Step 4 + 5 | Each with impact, evidence, consequence, remediation, location precise enough to edit from, and `check_result` for `[checkable]` items. Ordered `blocking` → `material` → `minor`. |
| The discard list | `critique.md` Step 5 | Findings whose tool check did **not** confirm, with the check output that failed to reproduce them. **Published, never silently dropped** — a reader who disagrees with a discard must be able to see it and say so. |
| `[judgment]` items as decisions | the rubric's `[judgment]` items | A short list, one line each: what the item asks, what the critic concluded, and what a fix would have to establish. These are for the human, not for a machine. |
| Independence + residual bias | `critique.md` Step 3 | The rung used, verbatim `residual_bias` sentence, and every demotion with its reason. Never presented as cross-model when it was not. |

**The deliverable never contains a rewritten memo, a suggested rewrite, or a patch.** The critic is
enumerate-only by construction (`SKILL.md` non-negotiable 1) and this file does not undo that at the
last step. `remediation` on a finding states *what must become true*, not the sentence to paste.

## Step 2 — Citations: verified once, reported plainly

Every citation in the memo is checked **once**, in this run, by the same two-part test `critique.md`
Step 5 uses: the source resolves, **and** it supports the claim it is attached to. A source that
resolves but has drifted to saying something adjacent is a flag, not a pass.

```json
"citations": {"present": 3, "verified": 2,
              "flagged": [{"citation": "src:funnel-2026Q1.csv#row14", "claim": "C2",
                           "reason": "resolves, but reports 2026-Q1 while the claim says 'over the last year'"}]}
```

An empty `flagged` list is a result; a **missing** `citations` block is a defect. `verified` counts
checks that actually ran, so `present > verified + len(flagged)` is impossible — a check that could not
run goes to `flagged` with `reason: "not-run: <why>"` and its rubric item scores `UNRESOLVED`.

There is no *carried* citation and no re-verification across rounds: there is one pass, so there is one
check. A memo with no citation markers at all reports `{"present": 0, ...}` — **zero is a result, not a
skipped check**, and the deliverable says so out loud rather than omitting the block.

## Terminal states

| State | Trigger | What ships |
|---|---|---|
| `CLEAN` | The critique completed, raised **zero** `blocking`/`material` findings, **and `unresolved_count == 0`**. | The deliverable, saying plainly that nothing material was found. Open `minor` findings still ship in the decision list. |
| `CLEAN_UNVERIFIED` | Zero `blocking`/`material` findings, but **`unresolved_count > 0`** — one or more `[checkable]` items had a tool that could not run. | The deliverable, with a **mandatory banner**: `N of M checkable items could not be verified — this is not a clean bill of health.` |
| `FINDINGS_RAISED` | The critique completed and raised at least one `blocking`/`material` finding. | The deliverable with every finding. **This is the normal outcome and it is not a failure.** |
| `ABORTED` | **Any run that did not complete**, with an enumerated `terminal_reason`. Not only critic failures — see the list below. | The [abort report](#the-abort-report), **parked for the human**. Never presented as a clean pass. |

`terminal_reason` is `null` for `CLEAN`, `CLEAN_UNVERIFIED` and `FINDINGS_RAISED`, and non-null exactly
for `ABORTED`:

| `terminal_reason` | When |
|---|---|
| `critic-transport-exhausted` | No rung returned a well-formed envelope. |
| `critic-schema-invalid` | The transport rejected **our own** schema (`critique.md` Step 4). Never retried, never demoted. |
| `input-integrity-failed` | The frozen copy or the user's source file did not re-hash to its intake hash. Names which one moved. |
| `write-boundary-violated` | A write target resolved inside an installed skill tree (`SKILL.md` non-negotiable 5). |
| `anchor-score-failed` | The anchor could not be scored, so there is no denominator and no deliverable. |
| `deliverable-write-failed` | The critique completed but its report or `trust-handoff.json` could not be written. The findings exist in `critique/findings.json`; the run still does not claim success. |

**`ABORTED` is the terminal class for every incomplete run, not a critic-specific one.** The earlier
version enumerated only the two transport reasons, which left a write-boundary violation or a failed
anchor score with no state to land in — and an undocumented state is exactly the silent "done" the typed
states exist to prevent. Any new failure mode gets a reason added here rather than a fourth state.

**Why `CLEAN_UNVERIFIED` exists at all.** This skill takes great care to make a failed *critic dispatch*
unmistakable — `findings_summary` is `null` on `ABORTED`, never `0`. It had no equivalent for a failed
*check layer*: a run where every source was unreachable produces zero findings and would otherwise be
byte-indistinguishable from a run where ten checks passed. That is the same confusion one layer down,
and the prose disclaimer "`CLEAN` does not mean the memo is good" does not cover "nothing was actually
checked". An `UNRESOLVED` item is a `[checkable]` item whose tool could not run
(`intake-and-rubric.md`); it never becomes a finding, so nothing else would have surfaced it.

There is no fourth "done" path and no silent auto-accept. A run waiting on an external reviewer is in
the non-terminal `awaiting-external-review` state and carries `terminal_state: null` (see `critique.md`,
rung 2).

**`CLEAN` does not mean the memo is good, and must never be reported as if it did.** It means this
rubric, checked by this critic, found nothing material. The memo has not been rewritten, verified for
sending, or endorsed. pre-send review is what decides whether it may leave the building.

## The critique report

`<run-dir>/critique-report.md`, the human-facing output. Fixed skeleton — the marked lines are verbatim,
not paraphrasable.

```markdown
# memo-loop — <memo name>
**Terminal state: FINDINGS_RAISED** · 1 critique pass · run `<run_id>`

<!-- banner lines, only when they apply -->
TEST BACKEND IN USE: <path>
FAULT INJECTION ACTIVE: <spec> — this run is a test run.
DEGRADED BACKEND: lessons home resolved to the run-dir sidecar.

## What this is
A critique, not a rewrite. Nothing in your memo was changed — `original.md` is byte-identical to the
file you gave me. Below is what a rubric compiled for THIS memo, and an independent critic that cannot
edit it, found. The rewriting is yours.

## Score
anchor **4/10** against the 10-item rubric compiled for this memo (`rubric_sha256 41ab…`).
**1 item unverified** (`unresolved_count` — a `[checkable]` item whose tool could not run).
Graded by <grader model/version>, which did not write the memo.

## Who criticised it
Rung 1 · cross-vendor · gpt-5.6-sol · schema enforced at the transport
<the rung's residual_bias sentence, verbatim>
<any demotions, with their reasons and the rungs tried>

## Findings — 1 blocking, 3 material, 2 minor
Ordered by impact. Each carries where it is, what is wrong, why it matters, and what a fix must establish.

### BLOCKING — The approval request conflicts with the stated condition
- **Where:** S7, ¶1, "approve serving notice"
- **Evidence:** S5 makes the recommendation conditional on the 15 Feb connector check; S7 asks for
  unconditional approval now.
- **Consequence:** The reader could approve an action the memo's own condition does not yet support.
- **A fix must establish:** which of the two is intended, and make the other agree.
- **Rubric item:** R-12 · `[judgment]`

### MATERIAL — The 34% deflection rate uses the wrong denominator
- … same shape, plus for `[checkable]` items:
- **Check run by the orchestrator:** `8,400 / 33,100 = 25.4%` — the memo's 34% divides by 24,700
  (contacts that reached an agent), not by arriving contacts. **`check_result: confirmed`.**

## Rejected critic claims — NO ACTION REQUIRED
The critic raised these; the orchestrator's own check did **not** reproduce them. They are published so
you can disagree with a rejection, not so you can act on them. **The default is to do nothing here.**
1. **R-03** — critic claimed the S4 figure carries no number; the registered check found one at S4 ¶2.
   `check_result: unconfirmed` → dropped before it could reach you as a finding.

## Decisions left to you
Short list, never prose. Open `minor` findings, and every `UNRESOLVED` `[checkable]` item with the
reason its check could not run — an item nobody could verify is a decision for you, not a pass.
1. **R-08 (minor)** — no named objection is answered. S4 names the payments-migration risk but does not
   answer the reader who says "then ship in Q4". A fix has to state why waiting costs more.

## Citations
3 present, 2 verified, 1 flagged — `src:funnel-2026Q1.csv#row14` resolves but reports 2026-Q1 while the
claim says "over the last year".

## Handoff
`trust-handoff.json` written. Critique exemplar archived to `<calibration_dir>/<slug>/<run_id>/`.
Board-bound? Rewrite first, then pre-send review decides whether it may be sent.
```

## The abort report

Same skeleton, different obligations. The acceptance review consumes exactly these fields, so a run that
validly aborts must still emit all of them.

```markdown
# memo-loop — pricing-2026.md
**Terminal state: ABORTED** · 0 critique passes completed · run `2026-08-04T1802_pricing-2026`

## Why it stopped
**`terminal_reason: "critic-transport-exhausted"`** — no rung returned a well-formed critic envelope.
| rung | attempts | outcome |
|---|---|---|
| 1 · cross-vendor CLI (gpt-5.6-sol) | 2 (initial + retry) | non-zero exit, `FAULT_INJECTED: critic-dispatch:persistent:rungs=all` |
| 2 · portable packet | 2 | dispatch failed before the packet could be written |
| 3 · same-family subagent | 2 | dispatch failed |
No critique was performed. **This is not a clean pass**, and it must never be read as one.

## Score
| field | value |
|---|---|
| anchor score | **4/10** (the frozen original, scored at intake) |
| findings | **N/A — no critique completed** |

## Who criticised it
Nobody. `reviewer_independence` records the last rung attempted with its `residual_bias` sentence, and
`demotions[]` carries all three transitions with timestamps and reasons.

## What ships
Nothing was edited — this skill never edits. The memo is untouched and the run is **parked**, not
finished.

## What to do next
Re-run once the transport is available. If `FAULT INJECTION ACTIVE` appears in the banner, the failure
was injected deliberately: unset `MEMO_LOOP_FAULT_INJECT` and re-run.
```

The corresponding `run.json`:

```json
"run_state": "terminated",
"terminal_state": "ABORTED",
"terminal_reason": "critic-transport-exhausted",
"anchor_score": {"pass": 4, "of": 10},
"findings_summary": {"blocking": null, "material": null, "minor": null, "discarded": null},
"critique": {"completed": false, "reason": "aborted before any rung returned an envelope"}
```

`findings_summary` values are **`null`, never `0`** on an abort: zero findings is a *result* a completed
critique produces, and writing `0` here would make a failed dispatch indistinguishable from a clean memo
— which is the single failure this skill exists to prevent.

## Phase 3 — handoff, archive, lessons

In this order:

1. **`trust-handoff.json`** — the finding set mapped to trust severities, per-claim `review_status`,
   `readiness`, `reviewer_independence`, waivers, and a timestamped `run_id`. The impact →
   severity mapping is in `critique.md` and is applied here, never left to the reader. `readiness` is
   always **`critiqued-not-remediated`**. The old `improved-not-verified` was retired: it claimed an
   improvement that never happened, and a trust consumer could read it as "someone worked on this".
2. **Archive the run** to `<calibration_dir>/<slug>/<run_id>/` — the memo, the compiled rubric, the
   findings, `discarded.json`, the waiver records, the anchor score and the terminal state. Archive on
   `CLEAN`, `CLEAN_UNVERIFIED` and `FINDINGS_RAISED`; **never on `ABORTED`**, and never when fault
   injection was active.

   **An archived run is RAW EVIDENCE, not a known-good exemplar, and the two must not be conflated.**
   It records what this critic said — including its false positives and whatever it missed. Promoting
   that to "known-good" would let the critic calibrate on its own errors and let `reflect`'s eval gate
   validate rules against them. So the archive carries `adjudicated: false` by default, and **only an
   adjudicated entry may be selected as a calibration exemplar** (`intake-and-rubric.md` Step 7) or
   replayed by the eval gate. Adjudication is a human marking which findings were right — there is no
   automatic path to it, deliberately.
3. **Append this run's lessons entries.** The schema and everything downstream belong to `reflect.md`.

> **What the descope changed here, stated rather than left to be discovered.** The archive used to hold
> **before/after pairs**, and `reflect.md`'s held-out eval gate scored promotions against them. A
> critic-only skill produces no "after". The eval gate therefore cannot run on `improve` output: it
> records `blocked_reason: "no-after-draft-critic-only"` and promotion falls to the existing `--ungated`
> path, which stamps `ungated: true` on the rule and its changelog line so it stays findable and
> revocable. **The permanent human gate is unchanged and is now the whole control** — nothing is
> promoted without an explicit affirmative naming the rule.

## Dry-run trace — a 10-line memo to a deliverable

```
original.md — 73 words, 5 sections, frozen and hashed at intake. Never rewritten.
intake      — rubric compiled: 10 items (7 backbone + 3 memo-specific), item_count frozen
              anchor scored 4/10 by a fresh grading dispatch (not the drafting context)
critique    — rung 1, exit 0, envelope valid: n == of == item_count == 10,
              independence hashes equal the orchestrator's own
              6 findings raised: 1 blocking, 3 material, 2 minor
discard     — R-03's registered check did NOT reproduce the claimed defect -> discarded,
              published in the deliverable with the check output
citations   — 3 present, 2 verified, 1 flagged (source drifted period)
terminal    — FINDINGS_RAISED (>= 1 material). terminal_reason null.
ships       — critique-report.md + trust-handoff.json. original.md byte-identical to input.
```

**What this trace proves, and what it deliberately does not.** It proves the critic ran independently,
the envelope was enforced at the transport, an unconfirmed finding was dropped before it could waste the
author's time, and the memo was not touched. It does **not** prove the memo got better — this skill no
longer makes that claim, and the honest reason is that when it did make it, the claim did not survive
measurement.

**AUTHOR UTILITY IS UNVALIDATED, and that is the open question this design rests on.** The eval that
authorised the descope compared *finished memos*: loop-revised vs single-pass-rewritten. It never
compared **an author working with this deliverable against an author working without it**. So the
evidence says the old revision loop was worse than a single rewrite; it does **not** say a critique
packet makes a human's rewrite better. The failure mode to watch for is a skill that converts failed
automation into an expensive diagnostic packet which offloads synthesis and rewriting onto the author
while claiming rigour. Closing it needs a held-out utility eval — critique-assisted rewrite vs
unassisted, scoring rubric improvement, newly-introduced defects and finding-acceptance rate. Until that
runs, no document here should claim the deliverable helps.
