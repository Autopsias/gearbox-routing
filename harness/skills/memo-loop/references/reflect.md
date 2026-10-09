# Lessons and `reflect` — the outer loop

This file owns two things that must never be confused:

| | **The Phase 3 append** | **`reflect`** |
|---|---|---|
| When | end of **every** `improve` / `create` run | only when the human asks for it by name |
| Cost | one file append, no judgement | a batched, sampled, eval-gated, human-gated cycle |
| Writes | `<lessons_file>` (+ the run's audit copy) | `reflect-state.json`, `<promotion_dir>` |
| Editorial? | **never** — it records what happened | yes, and that is why it is gated |

## Contents
- [1 · The lessons store](#1--the-lessons-store)
- [2 · What a run appends (the Phase 3 write)](#2--what-a-run-appends-the-phase-3-write)
- [3 · The inclusion bar](#3--the-inclusion-bar)
- [4 · The memory model — three objects](#4--the-memory-model--three-objects)
- [5 · `reflect` — the procedure](#5--reflect--the-procedure)
- [6 · The write sequence](#6--the-write-sequence)
- [7 · The overlay, decay, and graduation](#7--the-overlay-decay-and-graduation)
- [8 · How `reflect` ends](#8--how-reflect-ends)
- [Checklist](#checklist)

**Observe cheaply, promote rarely.** The append is a receipt, not an opinion. Everything that decides
whether a lesson becomes a standing rule happens in `reflect`, in batch, in front of the human.

`reflect` is **explicit invocation only**. It is never inferred from phrasing, never chained onto the end
of an `improve` run, and it never writes a rule that the human has not named out loud.

---

## 1 · The lessons store

`<lessons_file>` — resolved through the backend ladder at startup (see `intake-and-rubric.md` Phase 0;
the four targets are `home_dir`, `lessons_file`, `calibration_dir`, `promotion_dir`, and **none of them
may resolve under an installed skill tree**). Nothing here is written until that assertion has passed.

**Format: append-only JSONL, one object per line, one line per observation.** JSONL because an append
under `O_APPEND` of a line shorter than `PIPE_BUF` is atomic — concurrent runs need no lock file. Open
with `O_APPEND`, write one `\n`-terminated line per observation, never rewrite an existing line, never
truncate.

Under lessons-home rung 2 the vault's `memo-loop.md` is a **rendered human-readable mirror**,
regenerated from the JSONL after each append. It is never the source of truth and is never parsed back.

### The entry

```json
{ "event_id":     "L-<sha256[:12] of run_id + observation_key>",
  "run_id":       "2026-08-04T1612_pricing-2026",
  "mode":         "improve",
  "observed_at":  "2026-08-04T16:41:09Z",
  "kind":         "critic-caught | author-override | rubric-item-gamed | rubric-item-never-fired |
                   transport-demotion | terminal-state | preference-declared | finding-rejected",
  "observation_key": "bluf-missing-in-first-para",
  "detail":       "<= 300 chars, human-readable, never compared",
  "scope":        "this-run | standing",
  "rubric_item_id": "R-07" }
```

`kind` is a **closed enum** — exactly the eight values above. A new observation gets an existing kind or
it is not recorded; an open enum makes occurrence counting meaningless within two months.

| Key | Rule |
|---|---|
| `observation_key` | **The only field occurrence counting compares.** Normalized: lowercased, non-alphanumerics collapsed to `-`, trimmed. Model-free and run-free — no dates, no paths, no model names, no memo titles. If two runs hitting the same problem would not produce the same string, the key is wrong. |
| `event_id` | The dedup key. A resumed run (`AWAITING_EXTERNAL_REVIEW`, see `critique.md` rung 2) re-appending the same observation is idempotent: **readers keep the first occurrence of each `event_id`** and drop later duplicates. |
| `detail` | Free text, ≤ 300 chars, for the human reading the decision card. Never compared, never counted. |
| `scope` | `this-run` is **the default**. `standing` requires 2+ decision events or an explicit declaration (§4). |
| `rubric_item_id` | The item this attaches to, or `null`. |

**Occurrence count = the number of DISTINCT `run_id`s carrying an `observation_key`.** Counting rows
would let one run vote twice — a single run that produced four `critic-caught` entries with the same key
is one occurrence, not four.

### The run's audit copy

The same lines are also written to `<run-dir>/lessons-entries.jsonl` — exactly what this run appended,
kept with the run so the append is auditable after the shared store has moved on. It is a copy, not a
second source: `reflect` reads only `<lessons_file>`.

---

## 2 · What a run appends (the Phase 3 write)

Emit one entry per observation, from **what the run's own files already record** — `findings.json`,
`discarded.json`, `anchor-scores.json`, `run.json`. No new analysis, no LLM pass, no judgement. If producing an
entry needs a model call, it does not belong in this phase.

| Observation | `kind` | `observation_key` from | Emit when |
|---|---|---|---|
| A finding that **survived the discard gate and shipped** in the deliverable | `finding-raised` | the finding's normalized title | once per distinct shipped finding |
| A finding **rejected** at triage (waived, or discarded as tool-unconfirmed) | `finding-rejected` | the finding's normalized title | once per rejection; `detail` carries the reason |
| A rubric item that **passed at anchor** | `rubric-item-passed` | `passed:<rubric_item_id>` | once per run, per item. Only meaningful in aggregate across many runs — a single run's passes say nothing |
| A critic transport **demotion** | `transport-demotion` | `demotion:rung<N>-to-rung<M>:<normalized reason>` | once per demotion, from `run.json.demotions` |
| An explicit preference the human stated during intake or triage | `preference-declared` | the normalized preference | once each; `scope: "standing"` only if declared as standing |
| The run's outcome | `terminal-state` | `terminal:<terminal_state>` (+ `:<terminal_reason>` when non-null) | **exactly once per run** |

**Three kinds were RETIRED by the 2026-08-04 descope, and why — so nobody re-adds them without the
observer they need.** `critic-caught` recorded "a finding the *revision* accepted"; there is no
revision. `author-override` recorded "the author kept the draft as-is"; **no phase of a critic-only run
observes what the author did afterwards**, so it could only ever have been guessed. `rubric-item-gamed`
keyed on `<rubric_item_id>` across runs — but every run compiles its own rubric, so the ids are not
comparable between runs and the key fails §1's own test ("if two runs hitting the same problem would not
produce the same string, the key is wrong").

**`finding-raised` exists to stop a one-way ratchet, and that is not a nicety.** With `critic-caught`
retired, `finding-rejected` would have been the only finding-shaped observation left — so `reflect`
would compile its proposals from a corpus made purely of *suppression* events, and the only rules it
could ever learn would be rules that flag **less**. A promotion engine whose evidence base can only
argue one direction is worse than no promotion engine. `finding-raised` restores the other direction
from something a critic-only run can actually observe: what survived the tool-grounded discard gate.

### The single `terminal-state` entry carries the run summary

The per-run facts that are not observations — mode, memo type, which transport answered, what the
critique found — belong to the one row that exists once per run. They ride as an
additive `run_summary` object; occurrence counting and dedup ignore it entirely.

```json
{ "event_id": "L-9f21c4a80b37", "run_id": "2026-08-04T1612_pricing-2026",
  "mode": "improve", "observed_at": "2026-08-04T16:41:09Z",
  "kind": "terminal-state", "observation_key": "terminal:findings-raised",
  "detail": "1 blocking, 3 material, 2 minor; R-08 unresolved.", "scope": "this-run", "rubric_item_id": null,
  "run_summary": { "memo_type": "decision-memo",
                   "rung_used": 1, "reviewer_independence": "cross-vendor",
                   "exemplar_source": "bootstrap",
                   "anchor_score": "4/10",
                   "findings": {"blocking": 1, "material": 3, "minor": 2, "discarded": 1} } }
```

`findings` is the orchestrator's own post-discard tally, not the critic's self-report — the discard gate
runs first, so a critic that raised ten findings of which six did not reproduce is recorded as four.

### Suppression

A run with `test_run: true` or `fault_injection` active sets `run.json.lessons_suppressed` and appends
**nothing**. Synthetic runs must not move real occurrence counts. An `ABORTED` run appends nothing
either: no critique completed, so there is no observation to make — and recording "the transport failed"
as a lesson about memos would poison the corpus with facts about infrastructure.

---

## 3 · The inclusion bar

> **Would a future run behave meaningfully differently knowing this?**

If no, do not record it. Applied at the append, so `reflect` reads signal instead of a transcript.

Fails the bar, every time: how long the run took, which sections happened to change, restatements of the
memo's own content, "the critic was thorough", anything true of every run.

---

## 4 · The memory model — three objects

Durable memory is **exactly three objects**, kept beside the lessons file in `<home_dir>/`:

| Object | File | Content |
|---|---|---|
| Voice profile | `voice-profile.author.json` | The author's accumulated feature rates across the frozen originals of past runs. **DORMANT since the 2026-08-04 descope** — its only consumer was the reviser, and there is no reviser. Retained because it is cheap, additive and non-authoritative; it steers nothing today, and a future rewriter-facing feature is the only thing that would wake it. Said plainly rather than left looking live. |
| Terms list | `terms.json` | Preferred terms and rejected terms, each with the `run_id` that established it. Consumed by the **triage** of critic findings — a finding that objects to a term the author has already established as preferred is waived visibly, naming this entry as the rationale. |
| Rejected-decisions log | `rejected-decisions.jsonl` | `{decision, reason, run_id, date}` — **stored at equal fidelity to accepted rules.** |

**"Why we said no" is first-class.** Every system stores what it accepted; almost none store what it
rejected and why. The rejected log is what stops `reflect` re-proposing, cycle after cycle, a rule the
human has already turned down — and it is what makes an override legible instead of looking like noise.

### Extraction rule — learn from decisions, not drafts

Durable preferences extract **ONLY** from reviewed decision events: an accepted finding, a rejected
finding, an explicit preference statement, an override of a promoted rule. **Never** from raw draft
text. A phrase appearing in a draft is not a preference; a human choosing to keep it after it was
challenged is.

Every extracted item is tagged `scope`:

- `this-run` — a constraint for this memo. **The default.**
- `standing` — applies to future memos. Requires **2+ decision events OR an explicit declaration.**

A one-off instruction therefore cannot become a standing preference by accident.

### HARD RULE — the memory-independence guard

> **No lesson, preference, voice profile, rejected decision, promoted rule or noise list is EVER
> injected into the critic's context.**

The critic prompt is assembled from exactly four things: the memo, the compiled rubric, the calibration
exemplar, and the enumerate-only directive. Nothing else. The check is in `critique.md` — the assembled
prompt is substring-checked against every ACTIVE memory entry before dispatch, and a hit is a hard abort.

**A known-noise finding is handled builder-side, by a visible waiver, with the memory note as its
rationale.** The critic keeps flagging it; the triage stops re-litigating it, in writing, where the
human can see the waiver and disagree. Telling the critic what to stop flagging is exactly the
contamination the two-context split exists to prevent: it silently re-opens every blind spot the rubric
was built to cover, and nothing in the output would show it happened.

This applies to promoted rules too. An overlay rule may change the rubric or the triage —
**never** the critic prompt.

---

## 5 · `reflect` — the procedure

Run these in order. Any step may end the cycle; nothing later runs if it does.

### Step 0 · Preconditions

1. **Explicit invocation.** The human asked for `reflect` by name. If it was inferred from phrasing,
   stop and ask.
2. **Startup assertion.** Resolve all four targets absolute and symlink-resolved; assert none is under
   `~/.claude/skills/memo-loop/` or `~/.codex/skills/memo-loop/`. A violation is a hard ABORT naming the
   offending target and the rung that produced it.
3. **Frontier-tier check.** The sampling and diagnosis passes (steps 3–4) must be dispatched at the
   frontier tier — the top model/effort pair in `~/.claude/model-routing.yaml` for `deep_reasoning`.
   Small models **completely fail** at diagnose-and-rewrite: they produce plausible rules that do not
   survive the eval gate, and a cycle that promotes them is worse than one that promotes nothing. If the
   frontier tier cannot be assured, stop at `REFLECT_TIER_UNMET`: report the candidates as **pending**,
   promote nothing. Record the pair actually used in `reflect-state.json.reflect_tier` as the literal
   `"<model>/<effort>"` string the routing file uses (e.g. `"opus/high"`) — free-form, so a routing
   change does not invalidate old cycles.

### Step 1 · Read the unprocessed entries

Read the whole `<lessons_file>`, dropping duplicate `event_id`s (first wins).

- **Unprocessed** = entries whose `observed_at` is **after** `reflect-state.json.last_reflect_at`. On the
  first ever cycle, everything is unprocessed. This timestamp is the processed marker — there is no
  per-line mutation, because the store is append-only.
- **Candidate keys** = `observation_key`s with **at least one unprocessed entry**. A key with nothing new
  is not re-argued this cycle.
- **Occurrence counts are computed over the ENTIRE file**, not just the unprocessed slice — a key seen
  once last cycle and once this cycle is 2 occurrences, and a threshold that reset each cycle would never
  be reached.

If there are no candidate keys: `NO_CANDIDATES`, update `last_reflect_at`, stop.

### Step 2 · Thresholds and precedence

| Occurrences (distinct `run_id`s) | Disposition |
|---|---|
| 1 | **Recorded only. Never promoted, never proposed.** |
| 2+ | A pattern — eligible as a proposal |
| 3+ | Strong — eligible to become a hard rule |

**Violations of existing rules outrank new-rule proposals. Strengthen before you add.** Before any
candidate becomes a proposal, check it against the rules already in `<promotion_dir>/rules.md`: if an
existing rule covers it and kept being violated, the proposal is to **strengthen or clarify that rule**,
and it is ranked above every new-rule proposal in the decision card. Adding a second rule next to a rule
that is already being ignored is how a rules file becomes 5,000 characters of noise.

Then drop any candidate whose substance already appears in `rejected-decisions.jsonl` unless its
occurrence count has risen since the rejection — and if it is re-proposed, the card shows the earlier
rejection and its reason.

### Step 3 · Sample 3× and intersect

**First, count usable calibration exemplars — before spending any sampling passes.** A **usable
exemplar** is a `<calibration_dir>/<slug>/<run_id>/` directory holding the archived memo, `rubric.json`,
`findings.json`, `anchor-scores.json` and `meta.json`, whose `meta.json` records a completed critique
(`CLEAN` or `FINDINGS_RAISED`, never `ABORTED`), and which is not the source of any candidate in this
cycle. If the count is **< 2** and `--ungated` was not
passed, **skip the sampling entirely** and go straight to Step 4's blocked path: three fresh-context
passes per candidate are the most expensive thing `reflect` does, and spending them on proposals that
cannot be gated buys nothing.

Otherwise, for each surviving candidate:

1. Generate the proposed change **three times, in three independent fresh contexts** — no pass sees
   another's output. Each pass sees the candidate key, its occurrence count, the matching `detail` lines,
   and the current overlay; nothing else.
2. A **fourth** pass lists what is common to **at least 2 of the 3**.
3. **Only the intersection survives.** Anything appearing in one sample only is situation-specific
   overfitting and is discarded — this is the documented anti-overfitting safeguard for recursive prompt
   self-modification, and skipping it is how a loop that rewrites its own rules drifts.
4. If the intersection is empty, the candidate stays **pending** and is re-offered next cycle.

Record `samples: 3`, `intersection_size` per proposal.

### Step 4 · Eval gate

> **What the 2026-08-04 descope did to this gate, stated rather than left to be discovered.** The gate
> was designed to replay a proposal against held-out **before/after pairs** and check the *after* score
> did not fall. A critic-only skill produces no "after" — it never edits a memo. **The score-replay form
> of this gate can no longer run on `improve` output**, and pretending otherwise would be worse than
> saying so.
>
> What replaces it is narrower and honest: replay each surviving proposal against **2–3 held-out
> archived critiques**, and check the proposal would not have caused the orchestrator to **discard a
> finding that the tool check had confirmed**, or to waive one without a named rationale. That is a real
> non-regression test of the thing a promoted rule can actually damage — the triage — and it needs no
> rewritten memo. Record `delta: null` and `gate_form: "triage-non-regression"`.
>
> If fewer than 2 usable exemplars exist, the gate is **BLOCKED** exactly as before, and promotion falls
> to the `--ungated` path with its stamp. **The permanent human gate is unchanged and now carries the
> weight**: nothing is promoted without an explicit affirmative naming the rule.

- Replay each proposal against the held-out critiques, chosen via `INDEX.json` and excluded from
  anything the proposal was derived from. The archived exemplar must therefore include
  `discarded.json` and the run's waiver records, or the replay has nothing to check.
- **Adopt only if the rule would have suppressed, downgraded or waived NO finding that shipped** —
  neither a `[checkable]` finding the orchestrator's own tool run **confirmed**, nor a `[judgment]`
  finding that survived triage. **Visibility is irrelevant to this test.** An earlier draft of this gate
  said "discarded or *silently* waived", which was a null set by construction: non-negotiable 6 already
  requires every waiver to name its rationale, so a rule that suppressed confirmed findings through
  perfectly visible, properly-named waivers passed the gate with zero regressions recorded. That is the
  single highest-value rule an over-eager cycle would write, and the gate was blind to exactly it.
- Record `regressions` as an integer (`0` is a pass; the bar is non-regression, not improvement),
  `gate_form: "triage-non-regression"`, and `coverage` — how many held-out findings were replayed, split
  `[checkable]` / `[judgment]`. A `0` over a coverage of 1 is not the same evidence as a `0` over 40,
  and the decision card must show which it is.

> **What this gate does NOT establish, stated because the obvious reading is too generous.** It replays
> a rule against *recorded* critiques; it cannot tell you whether the rule would have made a *future*
> critique better or worse, and it cannot evaluate a rule whose effect is on the rubric compiler rather
> than on triage. For `[judgment]` findings it checks only that the rule would not have suppressed one
> that shipped — a weaker claim than the retired score-replay made, because there is no second draft to
> score. **A rule that changes how strictly the critic reads is outside this gate entirely.** For those,
> the human gate is the only control, and the decision card must say so on the line where that rule
> appears.

Record the result on the proposal. When the gate **ran**:
`"eval_gate": {"ran": true, "gate_form": "triage-non-regression", "delta": null, "regressions": 0, "ungated": false, "blocked_reason": null}`.

**Empty or thin archive.** With **fewer than 2** usable pairs the gate cannot run. Then:

> `PROMOTION_BLOCKED: insufficient calibration corpus (n=<k>, need 2)`

and every proposal records
`"eval_gate": {"ran": false, "gate_form": null, "delta": null, "regressions": null, "ungated": false, "blocked_reason": "insufficient-calibration-corpus"}`.
All six keys are always present. **`delta` is now ALWAYS `null`** — the score-replay form that produced
a numeric delta was retired with the revision loop (§Step 4); the gate reports `regressions` instead, an
integer count. A gate that did not run is not a gate
that found no regression, and conflating them is how an unmeasured rule later looks measured.

**A blocked cycle STILL shows the decision card** (Step 5), with every proposal marked
`eval gate: BLOCKED`, and still ends at Step 6 — because the human is the one who decides whether to
re-run with `--ungated`, and hiding the card would take that decision away. What a blocked cycle cannot
do is promote: even a reply naming proposals writes nothing while the block stands. The proposals are
listed as **pending** and re-offered at the next `reflect` once the archive fills.

An unmeasured self-written rule is precisely what degrades a self-improving loop, so blocking is the
default.

**Override:** the human may pass `--ungated`. An ungated promotion stamps `ungated: true` on the rule's
frontmatter **and** on its changelog line — identifiable, auditable and revocable forever after. `status`
lists ungated rules separately. The override exists, requires a deliberate act, and is never silent.

### Step 5 · The decision card

**At most 3 proposals.** One line each. Never a wall of prose.

**Ranking, fully ordered** — so which three appear is never a judgement call:

1. Rule-strengthening proposals before new-rule proposals.
2. Within each group, higher occurrence count first.
3. Ties broken by the **earlier** `first_seen_cycle` — the candidate that has been waiting longest wins,
   so nothing starves at the bottom of the card cycle after cycle.
4. Still tied: alphabetical by `observation_key`. Arbitrary, but deterministic — two `reflect` runs over
   the same store must produce the same card.

Everything below the cut stays `status: "pending"` and is re-offered next cycle; the card's header states
how many were held back, so a growing backlog is visible rather than silent.

```
memo-loop reflect · cycle 7 · 41 new entries · 6 candidates · 3 proposals

1. STRENGTHEN R-04 — "state the cost of NOT deciding, in the same units as the cost of deciding"
   seen 4 runs · rule R-9a3f exists and was violated in 3 of them · eval delta +0.02

2. NEW RULE — "every named risk gets an owner or an explicit 'unowned' label"
   seen 3 runs · eval delta 0.00 (no regression)

3. NEW RUBRIC AXIS — "the recommendation is falsifiable: it names what would change it"
   seen 2 runs · eval delta +0.04 · previously rejected 2026-06-12 ("too abstract"), occurrences since: 2

Reply with the numbers to accept (e.g. "1,3"), or "none".
```

When the eval gate was blocked, each line reads `eval gate: BLOCKED (calibration n=<k>, need 2)` instead
of a delta, and the card's closing line says so: *all proposals blocked — re-run with `--ungated` to
promote anyway, or leave them pending until the archive fills.*

### Step 6 · The gate — an explicit affirmative, and nothing less

> **Nothing is written to the overlay, the rubric backbone or any skill file without a reply that names
> the proposals to accept.**

- A reply naming numbers or proposal ids → **only those** are promoted.
- `"none"`, a rejection, a question, an unrelated message, an ambiguous reply, or **no reply at all** →
  **nothing is promoted.** Not-objecting is not accepting. Silence is not consent. "Looks good" without a
  number is not a selection — ask **once** for the numbers, then stop. The re-ask restates the block
  status if the cycle is blocked, and asks for nothing but the numbers: *"Which numbers do you want
  promoted? Reply with the numbers, or 'none'."*
- The re-ask writes nothing. Proposals stay `status: "pending"` throughout it; there is no transient
  state, because an interrupted cycle that left a half-decided status behind would be indistinguishable
  from a decided one. If the second reply names numbers, promote those; anything else ends the cycle at
  `NOTHING_PROMOTED`, and the ambiguous reply is recorded in the proposal's `rejected_reason` only when
  the human actually rejected — never when they simply did not answer.
- Every proposal not accepted is written to `reflect-state.json` as `status: "pending"` (re-offerable) or
  `status: "rejected"` with `rejected_reason` — and a rejection also appends to
  `rejected-decisions.jsonl`, at equal fidelity to an accepted rule.

If nothing was accepted: `NOTHING_PROMOTED`, update `last_reflect_at`, stop. This is a normal, healthy
outcome and is reported as such — most cycles should end here.

---

## 6 · The write sequence

Only for proposals the human named. In this exact order, per promotion:

**1 · Backup.** Copy the four mutable artifacts — `rules.md`, `rubric-backbone.additions.md`,
`tactics.md`, `CHANGELOG.md` — plus `retired/` from `<promotion_dir>` into
`<home_dir>/.backups/learned/<ISO-timestamp>/`. Retain 10 snapshots, prune the oldest beyond that.

`.backups/` is a **sibling** of `learned/`, never a child. A backup directory nested inside the tree it
backs up puts the destination inside its own source traversal: each promotion copies the previous
promotion's backups and the tree grows multiplicatively until the copy fails — before validation and the
atomic replace have run. The sibling layout keeps it a plain, non-recursive snapshot of a fixed file set.

**2 · Schema-validate the proposed content BEFORE it replaces anything.** Required frontmatter per rule:

```yaml
rule_id: R-9a3f
promoted_at: 2026-08-04T17:20:11Z
occurrences: 3
eval_gate: {ran: true, gate_form: triage-non-regression, delta: null, regressions: 0, ungated: false, blocked_reason: null}
approved_by: <approver>
```

An `--ungated` promotion carries
`eval_gate: {ran: false, gate_form: null, delta: null, regressions: null, ungated: true, blocked_reason: insufficient-calibration-corpus}`
— which is exactly what makes it findable and revocable later.

Form checks, all hard:

| Check | Limit |
|---|---|
| Each rule / axis / tactic | **one imperative line, ≤ 200 chars** |
| `rules.md` whole file | **≤ 5,000 chars** |
| `rubric-backbone.additions.md`, `tactics.md` whole file | **≤ 5,000 chars each** |
| Frontmatter | all five keys present, correct types |

The caps are the point, not bureaucracy: unconstrained self-written instructions bloat past 5,000
characters and **measurably degrade** the system that wrote them. A promotion that would breach a cap
does not get a bigger cap — it forces a retirement or a merge first, and the card says so.

A validation failure **aborts here**. Nothing has been written yet.

**3 · Write atomically.** Write `rules.md.tmp` **in the same directory**, `fsync`, then `os.replace()`
onto `rules.md`. Same-directory rename is atomic; a crash can never leave a half-written rules file that
the next startup would load. Same for each file touched.

**4 · Changelog line**, appended to `<promotion_dir>/CHANGELOG.md` — one dated line per change, including
every rubric-backbone addition:

```
<ISO> | <rule_id> | +promoted | occurrences=<n> | eval_delta=<d> | ungated=<bool> | approved_by=<name> | run=<run_id>
```

**5 · Rollback-on-malformed.** **Re-read the file from disk exactly as the skill will load it at
startup** and re-run the step-2 validation against what was actually written. Never trust the write;
verify the artifact as its consumer receives it. On any failure: restore from step 1's backup, append

```
<ISO> | <rule_id> | ROLLED-BACK | <reason>
```

and report `PROMOTION_FAILED` naming the reason.

Then update `reflect-state.json` (`cycle += 1`, `last_reflect_at`, proposal statuses, new rule records)
with the same `tmp` + `fsync` + `os.replace()` discipline, and report `PROMOTED` with the changelog lines.

---

## 7 · The overlay, decay, and graduation

```
<home_dir>/
  lessons.jsonl                     # the append-only store
  reflect-state.json                # cycle counter, proposals, rules
  voice-profile.author.json  terms.json  rejected-decisions.jsonl
  calibration/
    INDEX.json
    <memo-slug>/<run_id>/{memo.md, rubric.json, findings.json, anchor-scores.json, meta.json}
  learned/                          # <promotion_dir> — loaded at startup
    rules.md  rubric-backbone.additions.md  tactics.md  CHANGELOG.md
    retired/
  .backups/learned/<ISO-timestamp>/ # SIBLING of learned/
```

The skill **loads the overlay at startup and merges it AFTER its shipped defaults** — a promoted rule can
add or strengthen; the shipped contract always loads first and a promoted rule can never weaken a
non-negotiable.

**Decay, not delete.** A rule whose `last_fired_at` is more than **90 days** old moves to
`learned/retired/` with a changelog line, and can be restored by name. Nothing is ever silently dropped —
a rule that vanished without a trace is indistinguishable from a rule that was never promoted, and the
next cycle would re-propose it.

**Graduation, so the overlay does not become shadow harness code.** A rule that survives **3 `reflect`
cycles** and is still firing becomes a `graduation_candidate`. `status` lists it; the human hand-promotes
it into repo source through the normal authoring path (edit the skill source → commit →
reinstall the harness), where it passes the skill-quality rubric like any other skill content. The overlay is
where rules **earn** their way into source, not a permanent parallel universe.

### `reflect-state.json`

```json
{ "cycle": 7,
  "last_reflect_at": "2026-08-04T17:20:11Z",
  "reflect_tier": "opus/high",
  "proposals": [
    { "proposal_id": "P-0c1e", "observation_key": "cost-of-not-deciding-absent",
      "status": "pending|promoted|rejected", "first_seen_cycle": 5, "occurrences": 3,
      "samples": 3, "intersection_size": 2,
      "eval_gate": {"ran": true, "gate_form": "triage-non-regression", "delta": null, "regressions": 0, "ungated": false, "blocked_reason": null},
      "rejected_reason": null } ],
  "rules": [
    { "rule_id": "R-9a3f", "promoted_at_cycle": 5, "cycles_survived": 3,
      "last_fired_at": "2026-08-02T09:14:00Z", "fire_count": 11, "ungated": false,
      "graduation_candidate": true } ] }
```

`cycles_survived` drives graduation; `last_fired_at` drives the 90-day decay; `status: "pending"` is what
makes a blocked proposal re-offerable instead of lost.

### Seeding

On the first invocation of any **writing** mode — `improve`, `create`, `reflect` — after the startup
assertion passes, create the structure empty and idempotently — `home_dir`, `calibration/` with `INDEX.json` as `{"runs": []}`, `learned/` with the four
files empty, `learned/retired/`, `.backups/learned/`, and `lessons.jsonl` as a zero-byte file. Creating a
directory is not writing a rule; an empty `rules.md` is a valid overlay that promotes nothing.

**`status` never seeds.** It is read-only and mutates nothing — that is its whole contract
(`SKILL.md`, Modes). A missing structure renders as an empty view ("no runs yet, no promotions
pending"), never as a reason to create one. Seeding from `status` would make a command advertised for
inspection change what a later `reflect` and a later backend resolution observe.

---

## 8 · How `reflect` ends

| Outcome | Meaning |
|---|---|
| `NO_CANDIDATES` | Nothing new since the last cycle. `last_reflect_at` advanced. |
| `NOTHING_PROMOTED` | Proposals were shown; none was named. The normal outcome. |
| `PROMOTION_BLOCKED` | Fewer than 2 calibration pairs. Proposals pending, re-offered next cycle. |
| `REFLECT_TIER_UNMET` | Could not run at the frontier tier. Proposals pending. Nothing promoted. |
| `PROMOTED` | The named proposals were written, validated on re-read, and changelogged. |
| `PROMOTION_FAILED` | A write failed validation on re-read; the backup was restored and the reason changelogged. |

---

## Checklist

```
[ ] Invoked explicitly by name — not inferred, not chained onto a run
[ ] Startup assertion passed: no target under either skill tree
[ ] Frontier tier confirmed for the sampling passes, or stopped at REFLECT_TIER_UNMET
[ ] Duplicate event_ids dropped (first wins); occurrences counted as DISTINCT run_ids
[ ] Candidates limited to keys with at least one entry after last_reflect_at
[ ] 1-occurrence keys recorded only — never proposed
[ ] Existing-rule violations ranked above every new-rule proposal
[ ] Previously rejected candidates dropped, or re-shown with their rejection and reason
[ ] Usable calibration pairs counted BEFORE sampling; <2 short-circuits to the blocked path
[ ] 3 independent samples; only the >=2-of-3 intersection kept
[ ] Eval gate ran on 2-3 held-out pairs, or PROMOTION_BLOCKED (or --ungated, stamped)
[ ] eval_gate recorded with all six keys; delta always null; regressions is an integer or null
[ ] Decision card: at most 3 proposals, one line each, ranked by the fully-ordered rule
[ ] Held-back proposals counted in the card header, left pending — never silently dropped
[ ] An explicit affirmative naming the proposals — silence and "no objection" promoted NOTHING
[ ] Per promotion: backup -> validate -> atomic replace -> changelog -> re-read and validate again
[ ] Caps enforced: <=200 chars per rule, <=5,000 chars per overlay file
[ ] Rejections appended to rejected-decisions.jsonl with their reasons
[ ] Decayed rules moved to retired/, never deleted; graduation candidates surfaced
[ ] No lesson, preference or noise list entered any critic prompt
```
