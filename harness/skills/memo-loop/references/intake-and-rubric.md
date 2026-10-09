# Phase 0 — intake and the rubric compiler

Everything that happens before the first critic dispatch. It runs identically in `improve` and
`create` (in `create` it runs on the brief and the validated evidence, before a word is drafted, so the
writer writes against the criteria it will be graded on).

This file is the **home of the compiled-rubric score definition**. `critique.md` and
`critique-report.md` point here for it rather than restating it.

## Contents
- [Step 1 — Assert, resolve, create the run dir](#step-1--assert-resolve-create-the-run-dir)
- [Step 2 — Freeze the anchor](#step-2--freeze-the-anchor)
- [Step 3 — Map the claims](#step-3--map-the-claims)
- [Step 4 — Name the decision and the audience](#step-4--name-the-decision-and-the-audience)
- [Step 5 — Clarifying questions, cap 5](#step-5--clarifying-questions-cap-5)
- [Step 6 — Compile the rubric](#step-6--compile-the-rubric)
- [Step 7 — Attach the calibration exemplar](#step-7--attach-the-calibration-exemplar)
- [Step 8 — Score the anchor and freeze the denominator](#step-8--score-the-anchor-and-freeze-the-denominator)
- [The compiled score](#the-compiled-score)
- [Worked example — a short strategy memo to its rubric](#worked-example--a-short-strategy-memo-to-its-rubric)

---

## Step 1 — Assert, resolve, create the run dir

1. **Resolve four targets**, not one: the run-state dir, the lessons file, the calibration dir and the
   promotion dir. Each to an **absolute, symlink-resolved** path.

   **The backend ladder** — first rung that resolves wins, and the winner is recorded in
   `run.json.backends.resolved_by`. This is the ladder every other file means by "the backend ladder".
   Each rung resolves **all four targets explicitly**; they are never mixed across rungs, because a
   `home_dir` that is sometimes a file and sometimes a directory is how `learned/` ends up in three
   different places on three machines:

   | Rung | `resolved_by` | `home_dir` (a DIRECTORY) | `lessons_file` | `calibration_dir` | `promotion_dir` | When |
   |---|---|---|---|---|---|---|
   | 1 | `env` | `$MEMO_LOOP_HOME` | `<home_dir>/lessons.jsonl` | `<home_dir>/calibration` | `<home_dir>/learned` | the env var is set — also the test-backend override, which sets `test_backend: true` |
   | 2 | `vault` | `<vault>/<workspace-folder>/_skill_memory/memo-loop/` | `<vault>/<workspace-folder>/_skill_memory/memo-loop.md` | `<home_dir>/calibration` | `<home_dir>/learned` | a vault is connected |
   | 3 | `global` | `~/.claude/memo-loop` | `<home_dir>/lessons.jsonl` | `<home_dir>/calibration` | `<home_dir>/learned` | **the global default** |
   | 4 | `sidecar` | `<run-dir>/_memo-loop-home` | `<home_dir>/lessons.jsonl` | `<home_dir>/calibration` | `<home_dir>/learned` | absolute fallback: the run's own state dir, **announced out loud** as degraded |

   **Rung 2 is the one that needs care, which is why it is spelled out.** The vault's `memo-loop.md` is
   the human-readable lessons file the vault convention expects, and it sits *beside* — never
   inside — the sibling `memo-loop/` directory holding the machine state. `promotion_dir` is therefore
   `…/_skill_memory/memo-loop/learned`: never `…/_skill_memory/learned`, which collides with other
   skills, and never `…/memo-loop.md/learned`, which is not a path.
2. **Assert none is under `~/.claude/skills/memo-loop/` or `~/.codex/skills/memo-loop/`.** A violation
   is a hard ABORT naming the offending target and the rung that produced it. This is one `realpath`
   plus two prefix comparisons and it runs on every invocation of every mode. Reads from the skill
   directory are permitted — only writes break the deploy.
3. **Create** `<memo-dir>/.memo-loop/<ISO-timestamp>_<short-slug>/` and open `run.json` with
   `run_id`, `mode`, `schema_version`, `runtime_detected`, and `backends` (all four resolved paths plus
   `resolved_by`, and `test_backend` if an override is in force).

If the resolved backend is the run-dir sidecar (the last rung), say so out loud — a degraded backend is
never silent.

## Step 2 — Freeze the anchor

Copy the draft to `original.md` **before touching anything**, and record **two** hashes in `run.json`:
`anchor_sha256` (the frozen copy) and `source_sha256` (the file at the path the user gave you). Open the
source **read-only** and never write to it.

At the end of the run, **re-hash both**. Re-hashing only the copy would prove we preserved our own
snapshot while saying nothing about the user's file — which is the thing the promise is actually about.
A mismatch on either is a hard abort: `ABORTED` with `terminal_reason: "input-integrity-failed"`, naming
which hash moved. That pair of checks is cheap, and it is the whole proof that nothing was edited.

In `create` mode the anchor is machine-written: record `anchor_origin: "generated"`. The critique that
follows treats it exactly as it treats a human draft.

## Step 3 — Map the claims

One mechanical pass over the frozen text. It is orchestrator-side and is never shown to the critic.

**Claim→section map** → `dependency-map.json`. Built here; consumed by `critique.md` to locate
findings and by `trust-handoff.json` to carry per-claim `review_status` into pre-send review.

```json
{ "sections": [{"id":"S3","heading":"Cost","span":[120,180],"sha256":"…"}],
  "claims":   [{"id":"C7","text":"…","section":"S3","sources":["src:report.pdf#p4"],
                "depends_on":["C2"],"locator":"para:14"}] }
```

Edges come from three cheap signals: headings → sections, sentence-level claim extraction → claims, and
explicit citation or cross-reference markers → `sources` and `depends_on`. **This is a one-hop map, not
an inferred DAG** — DAG inference is unsolved, and claiming more than the signals support would attach
findings to the wrong text. A claim with no detected edges is recorded as belonging to its own section
and nothing more; the map never asserts an edge it did not observe.

## Step 4 — Name the decision and the audience

Read the memo (or the brief) and write down two sentences before anything else:

- **The decision this memo supports** — the specific choice a reader is being asked to make, in the
  reader's terms. "Whether to sign the Northfield lease now or hold the option to Q1" — not "about the
  Northfield office".
- **The audience and its prior context** — who reads it, what they already know, and what they will do
  with it.

Both go in `run.json.intake`. Everything downstream depends on them: an item that does not bear on this
decision for this audience does not belong in the rubric.

If the memo does not make the decision identifiable, that is the first clarifying question — and it is
also a finding, because a memo whose decision cannot be named has already failed.

## Step 5 — Clarifying questions, cap 5

**At most five, asked one at a time, each with a pre-filled recommended answer.** Fewer is better;
asking zero is a valid intake when the memo answers everything.

Format every question so the user can accept it with one word:

> **Q2 — Who is the primary reader?** I'd assume the exec team, since the memo addresses "we" and
> asks for a lease decision. **Recommended: exec team.** Correct, or someone else?

**Only ask what changes a rubric item or a hard constraint, and only what the text cannot answer.**
Before asking, try to answer it from the memo; a question the draft already answers spends the cap and
teaches the author nothing. The five slots are normally spent on:

| Slot | What it resolves | Why it cannot be inferred |
|---|---|---|
| The decision | Step 4, when the draft leaves it implicit | A memo can describe a situation without naming the choice |
| The audience | Which prior context the memo may assume | Nothing in the text names the reader |
| Why now | The significance item's deadline, cost or risk | A deadline outside the memo is invisible to it |
| Load-bearing claims | Which claims the argument cannot survive losing | Prominence in the text is not importance |
| Hard constraints | Length ceiling, mandated sections, house format, passages that must not be touched | These are facts about the world, not the draft |

Record every question, its recommended answer and the answer given in `run.json.intake.questions`.
Answers are tagged `scope: "this-run"` by default — a constraint given for this memo becomes a standing
preference only through `reflect`, never by accident.

## Step 6 — Compile the rubric

A fresh rubric per memo. **Never a fixed template** — a template is what the writer learns to satisfy
instead of the reader.

**Size: 10–15 items** (floor 8, ceiling 15; the measured band for instance rubrics is ~11–12).
Composition, where the contract leaves the split open, defaults to:

- **5–9 backbone items** drawn from `rubric-backbone.md` — the axes the memo's genre actually needs, not
  all of them. The backbone supplies coverage.
- **at least 4 memo-specific items** written against **this memo's actual claims and its decision**,
  each carrying `claim_refs` into `dependency-map.json`. These supply the teeth: they are the items a
  generic well-formatted memo cannot pass.

**Every item, without exception:**

1. **Binary.** One yes/no verdict, no scale. Decomposed yes/no checklists raise agreement sharply and
   cut variance across evaluator models.
2. **Positively phrased.** "The memo names at least one condition under which the recommendation would
   not hold" — not "the memo does not present the recommendation as unconditional". Judges resist
   penalizing output for bad behaviours more than they resist crediting it for good ones.
3. **Mid-length and specific.** Very short and very long items are the most exploitable.
4. **Anchored to a substance marker** — a specific number, a named risk, a falsifiable claim, a resolved
   source. **The test: could a memo pass this item by adding a header, a bullet list, or a hedging
   sentence? If yes, it is not an item yet.** Optimizing against a rubric that fails this test teaches
   length-padding and header cosmetics, which is the documented reward-hacking failure.
5. **Tagged `[checkable]` or `[judgment]`.** `[checkable]` means a tool can settle it — a citation
   resolves, a number recomputes, a named element is present at a locator. **A `[checkable]` item must
   name its check in the `check` field; an item whose check is "read it and decide" is a `[judgment]`
   item wearing the wrong tag**, and mis-tagging it corrupts the discard gate in `critique.md`.
6. **Given a stable id** `R-01 … R-NN`. Findings link to items by id, and per-item deltas are compared
   by id.

Write both `rubric.json` and its human-readable mirror `rubric.md`:

```json
{ "rubric_id": "…", "compiled_at": "ISO-8601", "memo_sha256": "…", "item_count": 13,
  "items": [
    { "id": "R-02", "tag": "checkable", "source": "bluf",
      "text": "The opening paragraph names at least one specific quantity or date tied to the recommendation.",
      "substance_marker": "a numeral, date, or currency amount in paragraph 1",
      "check": "token-scan paragraph 1 for a numeral/date/currency token; confirm the same figure recurs in the body",
      "claim_refs": [] } ] }
```

**Item order is randomized per critic pass**, ids unchanged — judges favour items appearing first or
last. Randomize the presentation, never the identity.

**The rubric is never recompiled mid-run.** `item_count` freezes here, so the anchor score and every
item verdict in the deliverable share one denominator the reader can check.

## Step 7 — Attach the calibration exemplar

Every critic dispatch carries one known-good / known-bad pair. A fixed exemplar cuts per-run standard
error by roughly 44% and cross-judge variation by roughly 72% — it is the cheapest variance reduction
available.

**Term discipline:** in this skill *anchor* means exactly one thing — the frozen `original.md` and the
scores taken against it (`anchor_score`, `anchor_origin`). The known-good/known-bad pair is always the
**exemplar**, never "the anchor".

Selection order, recorded in `run.json` as `exemplar_source`:

1. the nearest archived pair for the **same** memo slug (`<calibration_dir>/INDEX.json`) → `same-slug`
2. else the highest-scoring archived pair for **any** slug → `any-slug`
3. else the **shipped bootstrap** at `calibration-exemplar.md` → `bootstrap`

Copy the selected pair to `<run-dir>/calibration-exemplar.md` so the dispatched prompt is reproducible
from the run dir alone. Only runs that **completed a critique** (`CLEAN` or `FINDINGS_RAISED`) are ever
archived as exemplars — an `ABORTED` run criticised nothing and would poison the critic's calibration.
A run with fault injection active is never archived either.

**The exemplar is a variance reducer, not an authority.** A run on the bootstrap pair is fully valid and
carries no cap. Never let exemplar content become a rubric item: the rubric grades this memo, the
exemplar only calibrates how strictly.

## Step 8 — Score the anchor and freeze the denominator

Score `original.md` against the full compiled rubric and write `anchor-scores.json`. This is
`run.json.anchor_score` — the **only** score the run produces, because the memo is never edited. Record
`item_count` as frozen. Report it in the deliverable with its grader block: a score whose scorer is
unknown is an assertion, not a measurement.

## The compiled score

Two conforming implementations must produce the same number on the same inputs. The score is what tells
the author how far the memo is from the bar this rubric sets, and it is the number the critique report
leads with.

| Item verdict | When | Contributes |
|---|---|---|
| `PASS` | satisfied — `[checkable]`: the orchestrator's own tool check confirms it; `[judgment]`: an **independent** binary verdict is yes | 1 |
| `FAIL` | not satisfied | 0 |
| `UNRESOLVED` | a `[checkable]` item whose tool could **not run** (`check_result: not-run`) | 0 |

**"Independent" is a requirement on the scorer, not a description of it.** A `[judgment]` verdict comes
from a context that did not produce the text being scored. Since this skill never writes a memo, the
critic's own `item_verdicts` (`critique.md`) are independent of the **author** by construction on an
`improve` run — and in `create` mode, where the skill did generate the text, the anchor is scored by a
**fresh grading dispatch** on the same transport ladder, never by a context that drafted it.
`anchor-scores.json` records the grader either way. A model is up to 50% more likely to wrongly pass its
own failed output even against a fully objective binary rubric, which is why the provenance is recorded
rather than assumed.

```
compiled_score    = pass_count / item_count            # an exact rational, never a float
per_item_delta(N) = rank(N) − rank(N−1)                # rank: PASS=1, FAIL=0, UNRESOLVED=0
compare A vs B    = (pass_A × count_B)  vs  (pass_B × count_A)      # integer cross-product
```

Fixed properties, all load-bearing:

- **Unweighted** — every item counts 1. A weight source would be a second judgment call inside the
  thing being judged.
- **Integer comparison only.** There is therefore no float ordering and no tie tolerance to specify; a
  tie is exact equality of the cross-products.
- **The full item set.** Every item is scored, including the ones no finding touched — a partial score
  is not comparable to anything and tells the reader nothing about coverage.
- **`UNRESOLVED` scores 0** — an item nobody could verify is not satisfied. `anchor-scores.json` carries
  `unresolved_count` separately, so a run full of unverifiable items is visible to the reader rather
  than quietly deflated into a low score they would misread as a bad memo.

`critique-report.md` owns what is *done* with the score (it is reported, with its grader, in the
deliverable); this file owns what the score *is*.

---

## Worked example — a short strategy memo to its rubric

### The draft (`original.md`, 118 words, one paragraph, 5 sections' worth of claims in prose)

> **Pause the Northfield office lease until Q1.** We should pause the Northfield lease signature until Q1 2027.
> Headcount in the region grew 8% last year against a 25% plan, so the 40-desk floor we optioned is
> sized for a team we do not have. Holding the option costs €6k a month; signing costs €31k a month for
> 36 months. Remote retention in the region has held at 91%, so the office is not solving an attrition
> problem. The main argument against pausing is that the landlord may not re-offer the floor — we think
> that risk is real but small, since two comparable floors in the same building have sat empty since
> March.

**Step 4.** Decision: *whether to sign the Northfield lease now or hold the option until Q1 2027.*
Audience: *the exec team, who know the headcount plan and not the lease terms.*

**Step 5.** Two questions asked of a cap of five — "why now / is there a signature deadline?"
(recommended: *the option expires and that is the forcing event*) and "which claims are load-bearing?"
(recommended: *the two cost figures and the headcount gap*). The other three slots went unused because
the draft answers them.

### The compiled rubric — 13 items, 8 backbone + 5 memo-specific

| id | tag | source | item | check / substance marker |
|---|---|---|---|---|
| R-01 | checkable | bluf | The memo states its recommendation in the first paragraph, before any supporting reasoning. | Locate a recommendation clause naming the action and the timeframe in paragraph 1. |
| R-02 | checkable | bluf | The opening paragraph names at least one specific quantity or date tied to the recommendation. | Token-scan paragraph 1; confirm the figure recurs in the body. |
| R-03 | judgment | minto | The memo has one governing thought that every subsequent claim supports. | — |
| R-04 | checkable | toulmin | Every load-bearing claim is followed by a stated ground — a number, a named fact, or a source. | For each claim in `dependency-map.json`, assert ≥1 ground in the same or adjacent sentence. |
| R-05 | checkable | toulmin | The memo names at least one condition under which its recommendation would not hold. | Locate a conditional clause attached to the recommendation ("we would sign if…", "unless…"). |
| R-06 | checkable | amazon | The memo names a specific objection a skeptical reader would raise, and answers it directly. | Locate the objection sentence and its answer. |
| R-07 | judgment | paul-elder | The memo considers at least one alternative to the recommended option and states why it was not chosen. | — |
| R-08 | checkable | paul-elder | The memo states why this decision matters now — a named deadline, recurring cost, or risk. | Locate a date or per-period cost attached to the timing. |
| R-09 | checkable | **memo-specific** (C4, C5) | The cost of holding the option and the cost of signing are stated on the same time basis, so a reader can compare them without arithmetic. | Both figures per-month **or** both totalled; if a total appears, recompute it (€31k × 36 = €1.116m). |
| R-10 | checkable | **memo-specific** (C2) | The 8%-actual-versus-25%-plan headcount figure names both its period and its source. | Sentence carries a period **and** a system of record; resolve the source. |
| R-11 | checkable | **memo-specific** (C6) | The 91% regional retention figure names the population and the period it is measured over. | Locate population + period in the same sentence; resolve the source. |
| R-12 | judgment | **memo-specific** (C8) | The memo's stated confidence in the landlord-risk claim is matched by evidence for *that* claim, not for an adjacent one. | — |
| R-13 | checkable | **memo-specific** (decision) | The memo states what would trigger revisiting the pause before Q1 2027. | Locate a named trigger event or a review date. |

### Anchor score

`PASS`: R-01, R-02, R-03, R-06, R-08, R-09 → **6 / 13**.

The seven failures are the memo's real gaps, and each one is repairable in a sentence: no sources behind
the two headline figures (R-04, R-10, R-11), no rebuttal condition (R-05), no alternative considered —
a smaller floor or a sublease (R-07), a warrant gap where evidence about *floor supply* is offered as
evidence about *landlord behaviour* (R-12), and no trigger for revisiting (R-13).

### Three items that were drafted and rejected

Kept here because rejecting them is the part that is easy to get wrong.

| Rejected draft | Why | Replaced by |
|---|---|---|
| "The memo is well-structured." | Not binary, no substance marker, and satisfiable by adding headers. | R-03, which names the governing thought. |
| "The memo does not use vague quantifiers." | Negatively phrased — judges resist penalizing. | Folded into R-10/R-11, which *require* the period and the source. |
| "The memo includes a risks section." | A header satisfies it. Format, not substance. | R-05 and R-12, which require a stated condition and a matched warrant. |
