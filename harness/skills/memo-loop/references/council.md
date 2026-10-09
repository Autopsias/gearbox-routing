# `create` mode — evidence gate, outline, council, single-writer fallback

This file owns **all** of `create` mode, both drafting paths: the **council path** (the default) and the **single-writer path** (`--no-council`, and the automatic degradation
target when council cannot be dispatched). It introduces **no second finding shape and no second
ranking format** — the pairwise-ranking envelope, the blinded-absolute-score record, and the finding
contract are all imported by pointer from `critique.md`, which already specifies them for this file to
use. Anything that looks like a ranking or finding schema below is that same format, referenced, not
restated.

`[pack]` marks a source cited from an unpublished design note.

**The chairman's merged draft is a starting point, not the answer.** It is criticised exactly like a
human-authored draft — same critic, same rubric, same terminal states — via `critique.md` and
`critique-report.md`. `create` hands you a draft **and** the critique of it; it does not hand you a
finished memo, and it never rewrites its own output.

## Contents
- [Where this fits](#where-this-fits)
- [Step 1 — Perspective discovery → outline (STORM-style)](#step-1--perspective-discovery--outline-storm-style)
- [Step 2 — Evidence gate A: validate before drafting](#step-2--evidence-gate-a-validate-before-drafting)
- [Step 3 — Degradation check](#step-3--degradation-check)
- [Council path (default)](#council-path-default)
  - [Step 4 — Divergent drafts, capped at 3](#step-4--divergent-drafts-capped-at-3)
  - [Step 5 — Anonymized pairwise ranking](#step-5--anonymized-pairwise-ranking)
  - [Step 6 — Chairman merge](#step-6--chairman-merge)
- [Single-writer path (`--no-council` / degradation target)](#single-writer-path---no-council--degradation-target)
- [Step 7 — Evidence gate B: re-validate after merge](#step-7--evidence-gate-b-re-validate-after-merge)
- [Step 8 — Hand off into the critique](#step-8--hand-off-into-the-critique)
- [Rejected — 5+ drafts or extra debate rounds](#rejected--5-drafts-or-extra-debate-rounds)
- [Cost note](#cost-note)
- [When NOT to use council](#when-not-to-use-council)
- [Artifact layout](#artifact-layout)
- [Worked trace](#worked-trace)

---

## Where this fits

`create` shares Phase 0's rubric compiler (`intake-and-rubric.md`) with `improve`, but Phase 0 straddles
this file's drafting steps rather than running as one uninterrupted block, because Phase 0's step 2
(freeze the anchor) needs a draft to freeze and `create` has none yet at intake time. The split:

| Phase 0 step | When, in `create` |
|---|---|
| Step 1 — assert, resolve, create run dir | Immediately, before anything else. |
| Step 4 — name the decision and audience | Against the **brief**, not a draft. |
| Step 5 — clarifying questions, cap 5 | Against the **brief**. |
| Step 6 — compile the rubric | Against the **brief and whatever evidence came with it** — memo-specific items are grounded in the brief's stated claims and open questions, since there is no prose yet. **Not against Step 1's perspectives:** Step 1 reads the compiled rubric, so deriving the rubric from Step 1 would make the dependency circular and neither could run first. |
| Step 2 — freeze the anchor | **Deferred to this file's Step 8** — the anchor is whichever draft this file produces (chairman merge, or the single writer's draft), not the brief. |
| Step 3 — map the claims | Deferred alongside Step 2: the claim→section map is built once the merged draft is frozen as `original.md`. `anchor_origin: "generated"` is recorded there. |
| Step 7 — attach the calibration exemplar | Runs normally, once Step 1 (critique) of `improve` dispatches. |
| Step 8 — score the anchor | Deferred alongside Step 2, run on the frozen merge/draft. |

So: rubric compiled first (against the brief) → this file's Steps 1–8 → the output becomes
`original.md` and Phase 0 steps 2/3/8 close out on it → Phase 1 critique dispatches exactly as
in `improve`.

---

## Step 1 — Perspective discovery → outline (STORM-style)

**The outline is the first converged artifact, and it is graded before a word of prose exists** — the
lesson `[pack]` behind this step is Stanford STORM: refinement that happens before drafting outperforms
refinement bolted onto drafting after the fact (STORM/Co-STORM).

1. **Derive 2–3 perspectives from the brief and the already-compiled rubric.** Default names — **the
   same three names Step 4 uses as drafter stances**, so the outline and the divergent drafts share one
   vocabulary: `risk-first`, `opportunity-first`, `stakeholder-first`. (The contract's illustrative
   synonym for the third is "customer-first" — use whichever reads more naturally for the brief's
   audience; the identity, not the label, is what matters.)
2. **Simulate a short Q&A per perspective.** Each perspective asks the questions a reader with that lens
   would ask of the brief ("what is the downside if this is wrong", "what is the upside if it works",
   "who bears the cost and who gets the benefit"); answer each from the brief and whatever evidence is
   already at hand, and **note every source the answer leans on** — this is where the evidence pool
   Step 2 validates comes from. A question the brief cannot answer becomes either a Phase-0 clarifying
   question (if it changes a rubric item) or an open gap the outline names explicitly.
3. **Converge the three transcripts into one outline** — one shared outline, not three. Divergence in
   the council path comes from **stance in the prose**, not from structure; STORM's lesson is that
   structure converges before drafting, which is exactly what makes it safe for three differently-angled
   drafters to write against the same skeleton without homogenizing. Write it to
   `<run-dir>/council/outline.md`: one entry per section, each carrying its claim(s), the source(s) it
   leans on, and which rubric item(s) it is meant to satisfy.
4. **Score the outline against the compiled rubric, restricted to outline-decidable items.** An item is
   outline-decidable when its `substance_marker` names something an outline bullet can already state — a
   number, a named source, a named condition, a section's presence — which in practice is every
   `[checkable]` item (the rubric compiler requires exactly this anchoring, `intake-and-rubric.md` Step
   6.4). `[judgment]` items (prose flow, warrant quality, tone) cannot be decided pre-prose and are
   deferred to the first real score, in Step 8. Write `<run-dir>/council/outline-score.json`, same shape
   as `anchor-scores.json` (`intake-and-rubric.md` → "The compiled score"), with a fourth verdict this file adds
   **only here**: `DEFERRED` for anything not outline-decidable, scored 0 and excluded from the
   denominator used to judge the outline (never from the denominator used later to score prose).
5. **Re-plan, don't just proceed, on a failed outline item.** A `[checkable]` item that fails at outline
   grain — a claim with no source, a section missing entirely — is fixed in the outline before drafting
   begins, not carried forward as a known defect. This is cheap: there is no prose to re-derive yet.

## Step 2 — Evidence gate A: validate before drafting

**Every source the outline leans on is validated before any drafter sees it.** This is MemoPop's
two-gate design `[pack]`: "gate the evidence before the writer
sees it — URL validation pre-drafting, re-validation post-enrichment." Gate A is the first gate; Step 7
is the second.

**Reuse the exact citation check `critique.md` Step 5 already specifies** — *"Resolve the source, then
confirm it supports the claim it is attached to"* — applied here to the outline's source list instead
of a critic's findings. Same three-way result, same asymmetry:

| `check_result` | Meaning | What happens |
|---|---|---|
| `confirmed` | The source resolves and supports the exact claim in the outline. | Enters the evidence pool handed to drafters. |
| `unconfirmed` | Resolves but does not support the claim, or does not resolve. | **Discarded before any drafter sees it.** The outline claim it backed is flagged `unsourced` — re-source it or drop the claim; either is a Step-1 fix, not a drafting-time one. |
| `not-run` | The check could not run (tool or source unavailable). | Surfaced to the human as an open question. Never auto-discarded, never auto-actioned. |

Write `<run-dir>/council/evidence-gate.json`:

```json
{ "schema": "memo-loop.evidence-gate.v1", "gate": "pre-draft",
  "checked_at": "ISO-8601",
  "sources": [
    {"source": "src:funnel-2026Q1.csv#row14", "claim_id": "outline:S3.C2",
     "check_result": "confirmed", "evidence": "row 14 gives mobile 2.1%, matches the outline claim"},
    {"source": "src:blogpost.example.com/q1-notes", "claim_id": "outline:S4.C1",
     "check_result": "unconfirmed", "evidence": "resolves but discusses Q2, not the Q1 figure claimed"}
  ],
  "discarded": ["outline:S4.C1"], "not_run": [] }
```

**A memo drafted on an unvalidated source is a defect the critique loop cannot repair** — the critic
grades prose, and by the time a fabricated-source finding surfaces in the critique the drafters have already
built three arguments on it. This gate is what makes that class of defect structurally unreachable
instead of merely likely to be caught.

## Step 3 — Degradation check

Council needs to dispatch 3 fresh-context drafters and run the anonymized ranking exchange. Before
committing to it:

- If the runtime cannot fan out to independent fresh contexts, or `--no-council` was passed, go straight
  to the [single-writer path](#single-writer-path---no-council--degradation-target).
- Otherwise proceed to Step 4. If a **drafter dispatch itself** fails mid-council, see the retry rule
  there — council degrades to single-writer only if it cannot end up with at least 2 drafts.

**Never a silent substitution.** Whichever path runs, record it in `run.json.drafting_path` — either
`"council"` or `"single-writer (council unavailable: <reason>)"`, set once, next to `anchor_origin` —
and state it in the convergence report. The same honesty rule that governs the critic transport ladder
in `critique.md` §4 governs this choice.

---

## Council path (default)

### Step 4 — Divergent drafts, capped at 3

1. **Each drafter runs in a fresh context** — a fresh subagent dispatch (Claude Code) or a fresh `codex
   exec` invocation (Codex port), with **no shared context between drafters** and no visibility into
   what any other drafter produces. This is the same "fresh context is not optional" property
   `critique.md` states for rung 3, applied to writers instead of critics.
2. **Each drafter sees exactly:** the brief, the validated evidence pool (post-Gate-A), the converged
   outline from Step 1, the compiled rubric, and one stance directive. Nothing else — no lessons, no
   voice profile, no other drafter's existence.
3. **Named stances, capped at 3:** `risk-first`, `opportunity-first`, `stakeholder-first` (or the
   brief-appropriate synonym chosen in Step 1 — the same three names, so stance and outline-perspective
   line up). A run may use 2 stances instead of 3 (the item's own contract: "two to three divergent
   drafts") — never fewer than 2, since ranking needs a pair to compare.
4. **One dispatch per drafter, writing against the shared outline, not a blank page.** Each draft is
   written to `<run-dir>/council/drafts/<stance>.md` under its real stance name — anonymization is
   applied next, orchestrator-side, never by the drafter itself.
5. **Drafters never see each other's drafts, and this composes forward.** If any future iteration adds a
   feedback round to this step, the exchange is **critiques only, never peer drafts** — seeing peer
   drafts homogenizes output and erases the divergence the whole path exists to produce
   (LLM Review, arXiv 2601.08003). Violating this is a contract
   violation, not a tuning choice. v1 council runs each drafter single-shot; this rule exists so a later
   iteration doesn't reintroduce the failure mode LLM Review measured.
6. **Retry once per failed drafter dispatch.** If fewer than 2 drafts survive after retry, degrade to
   the single-writer path per Step 3, recording the reason (e.g. `"2 of 3 drafter dispatches failed"`).
7. **Every drafter gets the SAME word ceiling** — the brief's own length constraint, or the outline's
   section count × a stated per-section budget when the brief names none. State it in each stance
   directive, and record it once in `run.json.council.word_ceiling`. Length is not quality: a longer
   semantically-equal draft attracts 15–30 points of preference inflation from any judge, and a
   tournament that lets one drafter write twice as much is measuring verbosity, not argument. Capping
   before grading is cheaper and more reliable than trying to discount it afterwards.

### Step 5 — Anonymized pairwise ranking

**This step imports `critique.md`'s pairwise-ranking envelope wholesale — see
["The pairwise-ranking envelope"](critique.md#the-pairwise-ranking-envelope) for the schema, the
position-swap rule, the reconciliation table, and the blinded-absolute-score record.** Nothing here
redefines any of that; this section is only the council-specific orchestration around it.

1. **Anonymize orchestrator-side, before any ranking.** Copy each drafter's file to
   `<run-dir>/council/drafts/A.md` / `B.md` / `C.md` in an order not derived from stance name (e.g.
   shuffled). Write `<run-dir>/council/label-map.json` mapping label → stance → source path, and **never
   let that file, the stance names, or any drafter metadata enter a ranker prompt** — the same substring
   check `critique.md` Step 2 uses to guard the memory boundary applies here to guard authorship.
2. **Every pair, both orders.** 2 drafts → 1 pair → 2 dispatches. 3 drafts → 3 pairs (AB, AC, BC) → 6
   dispatches. Each dispatch produces one `memo-loop.ranking.v1` envelope (critique.md's schema),
   written to `<run-dir>/council/rankings/<pair>-<order>.json`.
3. **Reconcile every pair** using critique.md's table (`AB`=`BA` → that verdict; disagreement → the pair
   is `undecided` and is passed to the chairman as a tie). Write
   `<run-dir>/council/rankings/reconciled.json`.
4. **Score every draft absolutely, blinded, plus nothing yet for the merge** (the merge does not exist
   at this point) — `<run-dir>/council/blinded-scores.json`, critique.md's schema, `drafts` array
   containing `A`/`B`/`C` only. The merge's own blinded score is added in Step 6. **Record each draft's
   `word_count` on its entry** — additive, alongside `draft_sha256`. It is the audit trail for Step 4's
   ceiling: a draft materially over it, or a pairwise winner that is also the longest in every pair it
   won, is visible in the run log rather than inferred later.
5. **Same transport-error discipline as the critic.** A missing envelope, a missing independence field,
   or `n < of` on any ranking dispatch is a transport error: retry once, then demote the ranker per
   `critique.md`'s rung ladder, then — if no rung yields a well-formed envelope for a given pair —
   record that pair `undecided` with `reason: "ranker-transport-exhausted"` rather than guessing a
   winner. A council run does not abort the whole memo over one unreachable ranker; it degrades that
   one comparison to a tie and says so.

### Step 6 — Chairman merge

1. **A separate context, never one of the drafters.** The chairman is dispatched fresh, same
   independence discipline as a critic dispatch (fresh context or cross-vendor where the transport
   allows) — it did not write any of the three drafts and carries no stake in which one wins.
2. **The chairman is blind to stance and authorship, exactly like the rankers.** It receives the brief,
   the compiled rubric, the outline, the three **labeled** drafts (`A`/`B`/`C`, never de-anonymized),
   the reconciled pairwise verdicts, and the blinded absolute scores. The label map is not revealed to
   it. Merging on rubric-grounded merit rather than "this is the risk-first one" keeps the last step of
   the pipeline as bias-free as the ranking step that fed it.
3. **Graft, don't pick.** Give the chairman the **per-item winner breakdown**, aggregated across both
   orders of every pair the item appeared in (already present in each ranking envelope's `per_item[]`) —
   e.g. *"A won R-01–R-04 and R-09; B won R-05, R-06, R-11; C won R-07, R-08, R-10."* Instruct it
   explicitly: start from the highest blinded-score draft as the base, and graft in the specific
   sections or passages from the other drafts where they won specific rubric items — this is guidance
   for where to look, not a mechanical splice instruction; the chairman still exercises synthesis
   judgment on how the grafted material reads as one voice.
4. **One synthesized draft**, written to `<run-dir>/council/merge.md`.
5. **Score the merge, blinded, against the same rubric** and append it to `blinded-scores.json` under
   `label: "MERGE"` (critique.md's schema — the `drafts[]` array now holds `A`, `B`, `C`, and `MERGE`).
   This is the honesty check pairwise ranking cannot provide on its own: the merge was never in the
   tournament, so only an absolute blinded score can show whether it actually beats its best input
   rather than merely reading as a plausible synthesis.
6. **Record the merge as a manifest entry**, same shape as a critique round (`critique.md` Step 6 /
   `run-state.md`'s `manifest.json`), tagged `"purpose": "chairman-merge"` — independence is provable, not
   asserted, the same way it is for every critic dispatch.

---

## Single-writer path (`--no-council` / degradation target)

Retained in full — this is not a stub. It is what runs when `--no-council` is passed, and it is where
council degrades when it cannot be dispatched.

1. **Re-plan at each section boundary**, rather than committing to the whole outline up front — measured
   +4.73 with interleaved re-planning vs +0.55 without `[pack]` (IS-Writer).
   The outline from Step 1 above still applies (single-writer skips council orchestration, not the
   outline-first discipline); re-planning happens as each section is actually written.
2. **One writer, fresh context, writing against the outline and the compiled rubric.** No stance
   directive — a single writer has no peer to diverge from.
3. **The draft becomes `<run-dir>/original.md`** directly (no `council/merge.md` intermediate) — Phase
   0's deferred steps 2/3/8 close out on it exactly as in the council path.
4. **No ranking, no chairman, no `council/` subdirectory** — this path's cost is one drafting dispatch,
   not the council's ten-plus.

---

## Step 7 — Evidence gate B: re-validate after merge

MemoPop's second gate `[pack]`: *"re-validation post-enrichment."* The chairman's merge (or the single
writer's draft) is exactly where new claim–source combinations get created — grafting a passage from
draft B next to a citation carried from draft A is the point where a citation and the claim beside it
can drift apart without either draft alone being wrong.

**This is not new machinery.** It is the same two-part citation check `critique.md` Step 5 and
`critique-report.md` Step 2 run everywhere else, invoked once here — explicitly, on `council/merge.md`
(or the single writer's draft) **before** it is copied to `original.md` and frozen. A citation that
fails at merge time is a defect the drafting path introduced, not one the critic should have to
discover; catching it here keeps the critique focused on the argument rather than on a merge artifact.

Write the result into the same `<run-dir>/council/evidence-gate.json`, second entry, `"gate":
"post-merge"`, same schema as Step 2.

## Step 8 — Hand off into the critique

1. Copy `council/merge.md` (or the single writer's draft) to `<run-dir>/original.md` and freeze it.
2. Run Phase 0's deferred steps: record `anchor_origin: "generated"`, hash the anchor, and score it —
   this is `run.json.anchor_score`, computed on the drafting path's output. **In `create` the anchor
   score must come from a fresh grading dispatch**, never from the context that drafted or merged the
   text: this is the one mode where the skill wrote the memo, so it is the one mode where self-grading
   is possible and must be structurally prevented.
3. Proceed to Phase 1 (`critique.md`) exactly as `improve` would, and then to the deliverable. The merge
   faces the same critic, the same rubric, the same terminal states. **No `create` run reports `CLEAN`
   without a real critique having completed** — a generated draft nobody criticised is exactly the
   "looks finished" failure this skill exists to prevent.

---

## Rejected — 5+ drafts or extra debate rounds

More council seats and more debate rounds showed no consistent gains `[pack]` (ICLR 2025 multi-agent
debate analysis), and a 9-judge panel can carry the effective independent voting power of ~2 judges
because shared training data correlates their mistakes `[pack]` ("Nine Judges, Two Effective Votes",
2026, https://arxiv.org/pdf/2605.29800). Marginal budget goes to **critic diversity** (the transport ladder in
`critique.md`), never seat count. The cap is 3 drafters, not a knob to raise.

## Cost note

Council costs **roughly 3–5× the tokens of a single-draft create** `[pack]` (the design note's own
Option-B estimate). Concretely: 2–3 drafting dispatches, up to 6
ranking dispatches (3 pairs × 2 orders), 1 chairman dispatch, plus the blinded-score pass on every draft
and the merge — against 1 drafting dispatch for the single-writer path. Ranking dispatches are cheap
relative to drafting ones (a comparison, not a full draft), which is why the blended multiplier lands at
3–5× rather than the raw dispatch-count ratio.

## When NOT to use council

- **Short memos.** A one-page update has little room for genuinely divergent stances to add anything a
  single fresh-context writer plus the critique loop would not also reach — pay the 3–5× only where
  there is enough substance for stances to actually disagree.
- **Tight deadlines.** Council adds real wall-clock latency (up to 9 sequential-capable dispatches before
  drafting is even done) on top of the critique loop that follows it.
- **Well-specified updates.** A memo whose content is already substantially determined — a status update,
  a routine renewal, a small edit to a known-good memo — has no open question for stances to diverge on;
  `improve` (or the single-writer path) is the right tool.

`--no-council` exists precisely for these cases. It is not a lesser mode; it is the mode most `create`
runs should probably use, and council is for the memos where getting the argument right is worth 3–5×
the cost of writing it once.

## Artifact layout

Everything this file writes lives under `<run-dir>/council/`, alongside the shared run-dir files listed
in `run-state.md`:

```
<run-dir>/
  council/
    outline.md               # Step 1 — the converged outline
    outline-score.json        # Step 1 — outline-decidable rubric items only
    evidence-gate.json        # Step 2 + Step 7 — pre-draft and post-merge entries
    drafts/
      <stance>.md              # real stance names, pre-anonymization
      A.md  B.md  C.md          # anonymized copies, ranking- and merge-facing
    label-map.json             # label -> stance -> source path. NEVER enters a ranker/chairman prompt.
    rankings/
      AB-AB.json  AB-BA.json  AC-AB.json  AC-BA.json  BC-AB.json  BC-BA.json
      reconciled.json
    blinded-scores.json        # A, B, C, then MERGE appended in Step 6
    merge.md                   # Step 6 — the chairman's synthesized draft
  original.md                  # Step 8 — council/merge.md (or the single writer's draft), frozen
```

Single-writer runs write none of `council/` — only `original.md`, directly.

## Worked trace

**Brief:** "Should we consolidate the two EU warehouses into one, or keep both running through peak
season." 3-stance council, v1 defaults.

1. **Outline** (`council/outline.md`): 5 sections — cost delta, peak-season throughput risk, headcount
   impact, the fallback if consolidation slips, the trigger for revisiting. Sourced from two internal
   reports and one vendor SLA doc.
2. **Outline score** (`council/outline-score.json`): of the rubric's 11 items, 2 are `[judgment]` and
   score `DEFERRED` (excluded from the outline denominator); of the 9 outline-decidable items, **9 PASS,
   0 FAIL**. No re-plan needed.
3. **Gate A**: 3 sources checked, 3 `confirmed`. Evidence pool handed to drafters unchanged.
4. **Drafts**: `risk-first.md` leads with the peak-season throughput risk; `opportunity-first.md` leads
   with the cost delta; `stakeholder-first.md` leads with headcount impact. All three cover all 5
   outline sections — divergence is in emphasis and framing, not structure.
5. **Anonymize**: `label-map.json` maps `A -> risk-first`, `B -> opportunity-first`,
   `C -> stakeholder-first` (order shuffled, not alphabetical-by-stance).
6. **Ranking**: 3 pairs × 2 orders = 6 dispatches. `AB`: both orders say `A`. `AC`: `AC` says `C`, `CA`
   says `A` → **undecided**, passed as a tie. `BC`: both orders say `B`.
7. **Blinded scores**: A = 8/11, B = 9/11, C = 7/11.
8. **Chairman**: base = B (highest blinded score); grafts A's peak-season-risk section (A won that
   item outright) and C's headcount-impact framing (the undecided AC pair means C is not dismissed).
   Writes `council/merge.md`.
9. **Merge blinded score**: 10/11 — higher than any single input, the honest check Step 6 point 5 exists
   to provide.
10. **Gate B**: re-checks the same 3 sources against the merge's claims — all still `confirmed`; the
    graft did not separate any citation from its claim.
11. **Hand-off**: `merge.md` → `original.md`, `anchor_origin: "generated"`, `anchor_score = 10/11` from
    a fresh grading dispatch. The critique dispatches next, exactly as in `improve`, and the run ends
    with a critique deliverable — not with the merge presented as finished.
