# Phase 1 — critique: dispatch, envelope, findings

The critic **enumerates**. It never rewrites. Everything in this file exists to make that a property of
the transport rather than an instruction a model can drift from — and to make a malformed critic
response impossible to mistake for a clean one.

Read `intake-and-rubric.md` first: it owns the rubric, the calibration exemplar and the compiled score.
This file owns the dispatch, the envelope, the findings, and the pairwise-ranking format that
`council.md` imports.

## Contents
- [Step 1 — Probe the transport](#step-1--probe-the-transport)
- [Step 2 — Assemble the critic prompt](#step-2--assemble-the-critic-prompt)
- [Step 3 — Dispatch: the three rungs](#step-3--dispatch-the-three-rungs)
- [Step 4 — Validate the envelope](#step-4--validate-the-envelope)
- [Step 5 — Tool-ground the checkable findings](#step-5--tool-ground-the-checkable-findings)
- [Step 6 — Record the critique](#step-6--record-the-critique)
- [The findings contract](#the-findings-contract)
- [The pairwise-ranking envelope](#the-pairwise-ranking-envelope)
- [The fault-injection override — test only](#the-fault-injection-override--test-only)

---

## Step 1 — Probe the transport

Run this before every run's first dispatch. Its result goes in `run.json.probe`.

```
probe_rung1(runtime):
  if runtime has no shell (claude-desktop | codex-desktop):  FAIL("no-shell")
  1. command -v codex                                     absent  -> FAIL("cli-not-found")
  2. codex login status          (timeout 5s)             stdout must contain "Logged in"
                                                          else    -> FAIL("unauthenticated")
  3. record `codex --version`
  PASS
```

**When the probe passes, the lower rungs never engage.** Rung 1 is the default in every shell-capable
runtime, which includes every Claude Code run. Demotion happens only through Step 4's failure ladder,
never as a preference.

The Codex-side probe is a **different** procedure — its failure is sandbox-shaped, not auth-shaped, and
a network denial there surfaces as a misleading "Not logged in" error. It lives in
`codex-transport.md`, which overrides this file **for transport only** when running under the Codex
port. Everything else here — the envelope, the findings contract, the discard gate, the ranking
envelope — is shared and identical in both harnesses.

Record in `run.json`, always, whatever the outcome:

```json
"runtime_detected": "claude-code",
"probe": { "rung1": {"result": "pass", "reason": null,
                     "cli": "codex-cli 0.146.0", "auth": "ok"} },
"rung_used": 1,
"reviewer_independence": "cross-vendor",
"residual_bias": "<the rung's verbatim sentence — see the table below>",
"demotions": []
```

`residual_bias` is **printed verbatim in the convergence report** and is fixed per rung so it cannot
drift into a bare label:

| Rung | `reviewer_independence` | `residual_bias`, verbatim |
|---|---|---|
| 1 | `cross-vendor` | "Cross-vendor critic (`<model>`, `<version>`). Self-preference mitigated by BOTH transport independence and rubric decomposition. Residual: cross-vendor critique reduces but does not eliminate preference for text resembling the critic's own training distribution." |
| 2 | `named-human-SME` | "Human-mediated cross-vendor critic (`<vendor named by the user>`). Same mitigation as rung 1, plus an unverifiable transport — the user vouches for the vendor and the run; the independence manifest hashes the packet, not the critic's runtime." |
| 3 | `same-family` | "Same-family critic; self-preference mitigated by rubric decomposition only." |

A bare `same-family` label without rung 3's sentence is a contract violation: it implies the transport
carried mitigation it did not carry.

## Step 2 — Assemble the critic prompt

**The critic sees exactly four things:** the memo, the compiled rubric, the calibration exemplar, and
the enumerate-only directive. Lessons, preferences, the voice profile, the terms list and the
rejected-decisions log are orchestrator-side and **never** enter this prompt. Never tell the critic what
to stop flagging — triage its findings through a visible waiver instead.

**Enforcement, before dispatch:** substring-check the assembled prompt against every ACTIVE memory
entry. A hit is a hard abort. Then hash the prompt into `manifest.json`, so what was checked is what was
sent.

**Randomize the rubric item order for this pass** — ids unchanged. Judges favour items appearing first
or last; randomizing the presentation and not the identity removes the position effect without breaking
per-item comparison across runs. Record the order actually used in `critique/critic-prompt.md` (it is
part of the hashed text, so it is reproducible).

Write the assembled text to `<run-dir>/critique/critic-prompt.md`. Template:

````markdown
# Critique task — enumerate, do not rewrite

You are reviewing one memo against a fixed rubric. You are a CRITIC, not an editor.

## Rules

1. **Never rewrite.** Do not restate, redraft, or propose replacement prose for any part of the
   memo. Say what is wrong and where; the fix is someone else's job. A remediation line names the
   *kind* of fix ("state the period and the source for this figure"), never the sentence to use.
2. **Reason before you judge.** For EVERY rubric item, in the order given below, write your reasoning
   first and your verdict second. Never emit a verdict you have not already argued for.
3. **Evaluate every item.** All of them, including the ones you find satisfied. Report the count you
   evaluated; it must equal the count given.
4. **Localize every finding.** Every finding names a location precise enough to edit from — a
   section id, a paragraph number, or a quoted clause — and the rubric item id it violates.
5. **[checkable] items: run the named check** and report what you ran and what it returned. A
   citation must resolve; a number must recompute. Report the check even when it passes.
6. **Zero findings is a valid and welcome result.** Do not manufacture findings to look useful. Do
   not pad, do not rank, do not compliment.

## Output

Return exactly the JSON envelope described by the attached schema. Nothing outside it.

## The memo

<memo text>

## The rubric — <M> items, in the order you must evaluate them

<items, randomized order, each with id, tag, text, substance marker, and check spec>

## Calibration exemplar

<the selected known-bad / known-good pair and its verdicts — it calibrates how strictly you read,
never what you read for. Never import an item from it.>
````

## Step 3 — Dispatch: the three rungs

### Rung 1 — cross-vendor CLI, read-only, pinned model

The default wherever the probe passes. Verified against codex-cli 0.146.0.

```bash
codex exec \
  -s read-only \
  -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -C "$RUN_DIR" --skip-git-repo-check \
  --output-schema "$RUN_DIR/critic-schema.json" \
  -o "$RUN_DIR/critique/critic-response.json" \
  --json \
  - < "$RUN_DIR/critique/critic-prompt.md"
```

Every flag is load-bearing:

- **`-s read-only`** is what makes "the critic cannot edit" structural. The sandbox denies writes; the
  model cannot drift out of it. Never raise it, never substitute `workspace-write`.
- **`-m gpt-5.6-sol -c model_reasoning_effort=xhigh`** — both pinned explicitly on every dispatch, never
  inherited from the local Codex config, so the critic is the same critic on every machine. The lane is
  never `ultra`.
- **`--output-schema`** enforces the envelope at the transport, so Step 4's validation is the second
  layer rather than the only one.
- **`- < prompt.md`** — the prompt goes on **stdin**. It dodges the documented `codex exec` stdin hangs
  and removes the ARG_MAX ceiling on long memos.

**Long dispatches go through the existing supervisor**, which watches the JSONL event stream and
*resumes* rather than restarts on a stall. Reuse it; do not build a second supervisor.

```bash
python3 ~/.claude/scripts/codex_supervised.py \
  --prompt-file "$RUN_DIR/critique/critic-prompt.md" \
  --out        "$RUN_DIR/critique/critic-response.json" \
  --log        "$RUN_DIR/critique/critic-events.jsonl" \
  --model gpt-5.6-sol --effort xhigh \
  --cwd "$RUN_DIR" --sandbox read-only \
  --idle-timeout 180 --max-attempts 3 --total-deadline 1800
```

> **The supervised path is one layer, not two, and must say so.** As installed, `codex_supervised.py`
> accepts `--prompt-file --out --log --model --effort --cwd --sandbox --idle-timeout --max-attempts
> --total-deadline` and **has no `--output-schema`** (re-verified against the installed
> script). On that path the envelope is enforced by Step 4's post-read validation only.
>
> Two acceptable resolutions, builder's choice — doing neither is a contract violation:
> **(a)** add an `--output-schema` passthrough to the supervisor and pass it on both the initial and
> the resumed dispatch (preferred, and small); or **(b)** route only *short* dispatches through plain
> `codex exec` and record `"schema_enforced": "read-only"` in the `manifest.json` dispatch entry, so the
> weaker guarantee is visible in the run log instead of silently assumed.

### Rung 2 — the portable cross-vendor packet

For CLI-less runtimes (both Desktop apps — the **primary** runtimes) on high-stakes or
`[judgment]`-heavy memos, and reachable from any runtime by demotion.

Write `<run-dir>/cross-vendor-review-request.md` containing, in this order: the Step 2 critic prompt
verbatim, the compiled rubric, the calibration exemplar, and the claim/evidence bundle. Then tell the
user plainly:

> Paste this file into the other vendor's app, and bring its response back with
> `memo-loop improve <path> --resume <run_id> --ingest <response-file>`.

Then persist the pause and **stop**:

```json
"run_state": "awaiting-external-review",
"terminal_state": null,
"awaiting_external_review": {
  "packet_path": "<run-dir>/cross-vendor-review-request.md",
  "packet_sha256": "…",
  "requested_at": "ISO-8601", "expires_at": "<requested_at + 14 days>",
  "vendor_named_by_user": null, "ingested_at": null
}
```

`AWAITING_EXTERNAL_REVIEW` is **not terminal**. `terminal_state` stays `null` until
`run_state == "terminated"`; writing one while a packet is outstanding is exactly the falsely-satisfied
outcome the typed terminal states exist to prevent. Until the response returns the run is capped and may
not report `CLEAN` under a `cross-vendor` label.

| Trigger | Command | Result |
|---|---|---|
| Response returned | `--resume <run_id> --ingest <file>` | Validate as a Step 4 envelope. Valid → `reviewer_independence: named-human-SME`, `run_state: running`, the run continues from Step 4. Invalid → a transport error, but **retry-once does not apply** (there is no automatic retry for a human transport): demote straight to rung 3 and record it. |
| User abandons | `--resume <run_id> --cancel-external` | Demote to rung 3, record the demotion, `run_state: running`. |
| `expires_at` passes | any later invocation | Same as `--cancel-external`, `reason: "external-review-expired"`. The run never waits forever. |

Resume is safe to call repeatedly: all state is on disk, the packet is hashed, and lessons entries
dedup on `event_id`.

### Rung 3 — fresh same-family subagent, forced reason-first

Last resort, and the routine Desktop default. On the Claude side, dispatch a **fresh** subagent with an
explicit `model` override — a different model in the same family — carrying the identical Step 2 prompt.
Fresh context is not optional: it is what makes the critic independent of the drafting conversation.

Three properties the dispatch must carry, because the sandbox is no longer supplying them:

1. **Enumerate-only is now an instruction, so make it structural where you can** — the subagent is given
   no write tools. Pass an authoritative allow-list (`Read`, `Grep`, `Glob`), not a deny-list: a
   deny-list leaves any tool added in a future release enabled by default.
2. **Long chain-of-thought is forced**, per item, reasoning before verdict — rung 3's only remaining
   mitigation is rubric decomposition, and a fast same-family verdict discards even that.
3. **The result is labeled `same-family`** in `run.json`, in `manifest.json` and in the convergence
   report, with the mandated `residual_bias` sentence. Never presented as cross-model.

On the Codex side rung 3 is a fresh `codex exec` on a *different* GPT model, recorded the same way.

## Step 4 — Validate the envelope

### The schema

Write it once per run to `<run-dir>/critic-schema.json` and pass it to the transport:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schema", "rubric_items_evaluated", "independence", "item_verdicts",
               "findings", "open_questions"],
  "properties": {
    "schema": {"type": "string", "const": "memo-loop.critic.v1"},
    "rubric_items_evaluated": {
      "type": "object", "additionalProperties": false, "required": ["n", "of"],
      "properties": {"n": {"type": "integer"}, "of": {"type": "integer"}}},
    "independence": {
      "type": "object", "additionalProperties": false,
      "required": ["model_name","model_version","timestamp",
                   "memo_sha256","rubric_sha256","critic_prompt_sha256"],
      "properties": {"model_name":{"type":"string","minLength":1},
                     "model_version":{"type":"string","minLength":1},
                     "timestamp":{"type":"string","minLength":1},
                     "memo_sha256":{"type":"string","minLength":64},
                     "rubric_sha256":{"type":"string","minLength":64},
                     "critic_prompt_sha256":{"type":"string","minLength":64}}},
    "item_verdicts": {
      "type": "array",
      "items": {"type": "object", "additionalProperties": false,
        "required": ["rubric_item_id", "reasoning", "verdict", "check_spec", "check_output"],
        "properties": {"rubric_item_id": {"type": "string"},
                       "reasoning": {"type": "string", "minLength": 1},
                       "verdict": {"type": "string", "enum": ["yes", "no"]},
                       "check_spec": {"type": ["string", "null"]},
                       "check_output": {"type": ["string", "null"]}}}},
    "findings": {"type": "array", "items": {"$ref": "#/$defs/finding"}},
    "open_questions": {"type": "array", "items": {"type": "string"}}
  },
  "$defs": {
    "finding": {
      "type": "object", "additionalProperties": false,
      "required": ["title","impact","evidence_strength","location","criterion",
                   "rubric_item_id","check_type","check_spec","evidence","consequence",
                   "remediation","assumption_dependent","provenance"],
      "properties": {
        "title": {"type": "string", "minLength": 1},
        "impact": {"type": "string", "enum": ["blocking", "material", "minor"]},
        "evidence_strength": {"type": "string", "enum": ["verified", "probable"]},
        "location": {"type": "string", "minLength": 1},
        "criterion": {"type": "string"},
        "rubric_item_id": {"type": "string"},
        "check_type": {"type": "string", "enum": ["checkable", "judgment"]},
        "check_spec": {"type": ["string", "null"]},
        "evidence": {"type": "string"},
        "consequence": {"type": "string"},
        "remediation": {"type": "string"},
        "assumption_dependent": {"type": "boolean"},
        "provenance": {"type": "array", "items": {"type": "string"}}}}
  }
}
```

**Write this block verbatim — it is strict-mode-legal and was rejected before it was.** OpenAI
structured outputs run the schema in strict mode, which demands three things JSON Schema itself does
not: `additionalProperties: false` on **every** object subschema, **every** declared property listed in
`required`, and an explicit `type` beside every `const`/`enum`. An earlier revision of this block met
none of the three and `codex exec --output-schema` returned **HTTP 400 `invalid_json_schema` before the
model was ever called** — so the transport layer this step advertises could not engage at all, and each
run hand-repaired the schema differently. Genuinely optional fields are therefore `required` **with a
nullable type** (`["string", "null"]`), never omitted: `check_spec` and `check_output` are `null` on a
`[judgment]` item, and that is the shape the critic must emit. Re-verified end to end against
codex-cli 0.146.0 — a dispatch carrying `check_spec: null` returns exit 0 and a valid
envelope, and the pre-fix block still returns 400. Changing any of the three properties above
re-breaks the dispatch, silently, at the transport.

**The same rules bind every schema this skill hands to a transport, not just this one.** Council mode
derives a schema per run for the ranking, blinded-grading and chairman envelopes below; each is subject
to strict mode identically. Deriving one that violates the rules costs a dispatch and a 400 before the
model runs.

**And the three rules above are the ones that bit us, not the whole specification.** Strict mode also
constrains the root type, nesting depth, total property count, and which JSON Schema keywords are
supported at all — check the current list at
<https://developers.openai.com/api/docs/guides/structured-outputs> when deriving a schema rather than
generalising from this block. Two habits make the difference between "documented" and "enforced":

1. **Derive by editing a known-good schema**, not by writing one from scratch. The block above is
   known-good against codex-cli 0.146.0; the ranking, grading and chairman envelopes are structurally
   the same shape.
2. **A rejection is a hard stop, not a retry** — see `invalid_json_schema` under
   [What counts as a transport error](#what-counts-as-a-transport-error). That is the check that turns
   this from advice into something a run cannot ignore.

`check_result` is deliberately **absent** from the finding schema: the critic never writes it. It is
added by the orchestrator in Step 5, after an independent re-run of the check.

`item_verdicts` carries the per-item G-Eval record and is **required**, for two reasons: the compiled
score needs the critic's binary verdict for every `[judgment]` item, and `len(item_verdicts)` is what
makes `rubric_items_evaluated.n` a *checkable* number instead of a self-report. Within each item,
`reasoning` precedes `verdict` in the schema **and in generation order** — a verdict-first shape
produces post-hoc rationalisation, which is the thing reasoning-first exists to prevent.

### What counts as a transport error

A response is a **TRANSPORT ERROR** if any of these hold:

- non-zero exit, or a timeout (900s default)
- empty output, or unparseable JSON
- `schema`, `rubric_items_evaluated`, `independence`, `item_verdicts` or `findings` missing
- any `independence` field absent or empty
- `len(item_verdicts) != n`
- **`n != of`, or `of != item_count`** — `item_count` is the frozen denominator from `rubric.json`.
  Equality in both directions, not `n < of`: `of` and `n` are both the critic's own self-report, so a
  critic that graded 7 items and returned `{"n": 7, "of": 7}` would otherwise pass every check and its
  `findings: []` would read as a clean pass on a 12-item rubric — and `{"n": 13, "of": 12}` with 13
  verdicts would pass a one-sided `n < of` test too. The orchestrator holds `item_count`; it must be the
  one that says what `n` and `of` have to be.
- **the set of `rubric_item_id`s in `item_verdicts` is not exactly the frozen rubric's id set** — equal
  as *sets*, so duplicates and unknown ids both fail. A count alone is satisfiable by returning
  `item_count` verdicts that are all `R-01`, which evaluates one item and reports full coverage.
- **any `independence` hash that does not equal the orchestrator's own** — `memo_sha256` against the
  dispatched memo, `rubric_sha256` against `rubric.json`, `critic_prompt_sha256` against the text hashed
  into `manifest.json` at Step 2. Present-and-non-empty is not enough: unverified hashes make the
  provenance record self-reported, which is exactly what Step 6 claims it is not.

> **`findings: []` is a genuine clean pass ONLY when the envelope is complete and `n == of`.
> Everything else is a transport error and is NEVER read as zero findings.**

**One thing that is emphatically NOT a transport error: `invalid_json_schema`.** An HTTP 400 whose body
names `invalid_json_schema` (or any rejection of `text.format.schema`) means **our own schema is
malformed** — the model was never called, no tokens were spent, and the identical failure will repeat on
every rung. Never retry it, never demote on it, never let it walk down the ladder toward
`critic-transport-exhausted`, and never fall back to post-read validation and continue quietly:

> **Abort the run with `ABORTED`, `terminal_reason: "critic-schema-invalid"`, and report the
> rejection message verbatim.** Fixing the schema is a change to this file, not a run-time workaround.
> A run that hand-repairs the schema and carries on has silently deleted the transport layer for
> everyone who reads its log as a success.

This branch exists because it was missing. Every run in an earlier live eval hit a 400 here, and each one
independently invented a different repair — so the "second layer" this step advertises was absent on
every run and no log said so.

### The failure ladder

1. **Retry once** at the same rung — fresh dispatch, **same prompt**, so `critic_prompt_sha256` is
   unchanged and the retry is provably the same question.
2. Still failing → **demote one rung**, and append to `run.json.demotions[]`:
   ```json
   {"from": 1, "to": 2, "reason": "malformed-envelope: rubric_items_evaluated 7 of 12",
    "at": "ISO-8601", "attempts": 2}
   ```
   Rewrite `reviewer_independence` and `residual_bias` to the new rung's values, and **surface the
   demotion in the convergence report**.
3. No rung yields a well-formed envelope → **the run terminates
   `ABORTED`** with `terminal_reason: "critic-transport-exhausted"`. The abort report template
   is in `critique-report.md`.

Why this clause is the one that matters: a silent zero-findings read is indistinguishable from a genuine
clean pass. It would let the run report `CLEAN` when no critique happened at all, and
it would make every negative control unfalsifiable — a planted defect the critic never saw would score
identically to a planted defect the critic correctly cleared.

### Worked example A — a genuine clean pass

```json
{ "schema": "memo-loop.critic.v1",
  "rubric_items_evaluated": {"n": 10, "of": 10},
  "independence": {
    "model_name": "gpt-5.6-sol", "model_version": "2026-06-11",
    "timestamp": "2026-08-04T17:04:11Z",
    "memo_sha256": "9f2c…", "rubric_sha256": "41ab…", "critic_prompt_sha256": "d0e7…"},
  "item_verdicts": [
    {"rubric_item_id": "R-01",
     "reasoning": "Paragraph 1 sentence 1 states the action ('ship the rewrite') and the timeframe ('Q3') before any supporting reasoning.",
     "verdict": "yes", "check_spec": null, "check_output": null},
    {"rubric_item_id": "R-09",
     "reasoning": "Resolved src:funnel-2026Q1.csv; row 14 gives mobile 2.1% and desktop 3.4% for 2026-Q1, matching the memo's figures and the period it now names.",
     "verdict": "yes", "check_spec": "resolve src:funnel-2026Q1.csv and compare both rates",
     "check_output": "mobile=2.1% desktop=3.4% period=2026-Q1 — match"}
    /* … 8 more, one per item … */ ],
  "findings": [],
  "open_questions": [] }
```

**Read as: clean pass.** `n == of == item_count == 10`, `len(item_verdicts) == 10`, the ten
`rubric_item_id`s are exactly the frozen rubric's ten ids with no duplicate, all six independence fields
present and non-empty, all three hashes equal to the orchestrator's own, exit 0. `findings: []` is the
real result.

### Worked example B — a malformed payload

```json
{ "schema": "memo-loop.critic.v1",
  "rubric_items_evaluated": {"n": 7, "of": 10},
  "independence": {
    "model_name": "gpt-5.6-sol", "model_version": "",
    "timestamp": "2026-08-04T17:19:52Z",
    "memo_sha256": "9f2c…", "rubric_sha256": "41ab…", "critic_prompt_sha256": ""},
  "item_verdicts": [ /* 7 records */ ],
  "findings": [] }
```

**Read as: transport error, on three counts** — `n (7) < of (10)`, `model_version` empty,
`critic_prompt_sha256` empty. It is **not** a clean pass, and the difference from example A is
mechanical, not a judgment call: the same checklist above decides both, and every item on it is a
comparison against a value the orchestrator already holds.

Handling: retry once at rung 1 → if it recurs, demote to rung 2 with
`"reason": "malformed-envelope: rubric_items_evaluated 7 of 10; independence.model_version empty"` →
if no rung produces a well-formed envelope, `ABORTED / critic-transport-exhausted`.

## Step 5 — Tool-ground the checkable findings

**The orchestrator re-runs every `[checkable]` finding's check itself.** The critic ran its own check
inside its sandbox and reported it as evidence; that is the critic's justification for raising the
finding, not proof of it. The orchestrator's independent run is what decides.

| `check_result` | Meaning | What happens |
|---|---|---|
| `confirmed` | The independent run reproduces the defect the finding claims. | The finding ships in the deliverable. |
| `unconfirmed` | The independent run does **not** reproduce it — the citation resolves, the number recomputes. | **DISCARDED before it reaches the author**, and published in the deliverable's discard list so they can disagree. |
| `not-run` | The check could not be run (tool or source unavailable). | Surfaced **to the human** as an open question. Never auto-discarded, never auto-actioned. The rubric item scores `UNRESOLVED`. |

> **An unconfirmed check is a critic error, not a weaker finding.**

A discarded finding is **not** downgraded to `probable`, **not** re-tagged `judgment`, and **never
presented as a finding**. Write it to `critique/discarded.json` with the full tool output — so the
critic's error rate is measurable across runs and feeds `reflect`.

It is, however, **published in the deliverable's discard list** (`critique-report.md`), with the check
output that failed to reproduce it. That is not a contradiction: it never reaches the author as
something to fix, and it does reach them as something to *disagree with*. A silent discard would make
the orchestrator the final judge of the critic with no appeal; a published one leaves the human able to
say "your check was wrong" and act on the finding anyway.

This gate exists because a hallucinated critic finding is worse than a missed one: it sends the author
to "fix" text that was already correct, and their edit is the defect.

**Be precise about what the gate now is, because it changed.** Under the old revision loop the
protection was *mechanical*: `discarded.json` never entered the reviser's context, so a discarded
finding could not be acted on. Under a critic-only skill the author and the auditor are the same person
reading one document, so publication puts the discarded item back in front of exactly the reader the
gate was protecting. **"Never presented as a finding" is now a labelling claim, not a containment
claim** — and that is the right trade (the appeal right matters more), but it must not be described as
protection it no longer provides. What the gate still does, unchanged and valuable: it keeps the
critic's error rate **measurable** — `discarded.json` is the record `reflect` consumes — and it stops an
unconfirmed claim being presented with the authority of a confirmed one. Whether to act on a discarded
item is the author's call, made with the failing check output in front of them.

The two checks that carry most of the weight:

- **Citations.** Resolve the source, then confirm it supports *the claim it is attached to* — a source
  that exists but says something adjacent is a finding, not a pass. The per-run citation tally that
  reaches the deliverable is specified in `critique-report.md` Step 2.
- **Numbers.** Recompute from the stated inputs. A figure that recomputes is `unconfirmed` for any
  finding claiming it does not; a figure whose inputs are absent is `not-run`, not `confirmed`.

## Step 6 — Record the critique

Write, in `<run-dir>/critique/`: `critic-prompt.md`, `critic-response.json` (raw, exactly as returned),
`critic-events.jsonl`, `findings.json` (validated, post-discard), `discarded.json`. Append the dispatch
record to `manifest.json`:

```json
{ "purpose": "critique",
  "model": {"name": "gpt-5.6-sol", "version": "2026-06-11"},
  "timestamp": "2026-08-04T17:04:11Z",
  "memo_sha256": "…", "rubric_sha256": "…", "critic_prompt_sha256": "…",
  "rung": 1, "runtime": "claude-code", "reviewer_independence": "cross-vendor",
  "schema_enforced": "transport" }
```

Independence is then **provable rather than asserted**: re-hash the same inputs and the record
reproduces; change the memo and `memo_sha256` changes. A run whose `dispatches[]` is incomplete makes
its own provenance untrustworthy regardless of how clean the memo scored.

## The findings contract

Adopted **verbatim** from `~/.claude/skills/adversarial-review/codex/references/finding-contract.md` —
impact (`blocking` | `material` | `minor`), evidence strength (`verified` | `probable` | `question`),
the six-question admission test, and the record shape. Use these exact labels; never translate them to
critical/high/medium/low or to numeric scores. `question` items go to `open_questions` and are not
findings.

**Three additive fields, memo-loop-specific.** No existing field is redefined:

| Field | Values | Why |
|---|---|---|
| `rubric_item_id` | `R-07` | `criterion` is free text; this is the **machine** link to the rubric item, needed for per-item scoring and revert-on-regression. |
| `check_type` | `checkable` \| `judgment` | Mirrors the rubric tag, so Step 5's discard gate can find the findings it governs. |
| `check_result` | `confirmed` \| `unconfirmed` \| `not-run`, plus the tool output | Meaningful only when `check_type == checkable`. Written by the **orchestrator**, never by the critic. |

```json
{ "title": "The 40% ticket drop is offered as evidence that the mobile rewrite will cut tickets",
  "impact": "material", "evidence_strength": "verified",
  "location": "S3, paragraph 3, sentence beginning \"Support tickets about checkout fell 40%\"",
  "criterion": "Every load-bearing claim carries a ground that supports THAT claim",
  "rubric_item_id": "R-06", "check_type": "judgment", "check_spec": null,
  "check_result": null,   /* orchestrator-added in Step 5 — the critic never emits this key,
                             and the transport schema rejects it if it tries */
  "evidence": "The 40% figure is measured on desktop after the desktop rewrite. No mobile measurement is offered, and the memo asserts the transfer without stating why the populations are comparable.",
  "consequence": "A reader who accepts the ticket argument is accepting a desktop result as a mobile forecast; if mobile tickets do not fall, the stated payback disappears.",
  "remediation": "Either state the reason the desktop result should transfer, or drop the ticket claim from the argument.",
  "assumption_dependent": false, "provenance": [] }
```

**Every finding is localized.** `location` must be precise enough to edit from — a section id, a
paragraph number, or a quoted clause. A finding the author cannot locate is a finding they cannot act
on, so it is rejected at validation rather than shipped as a vague complaint about the whole memo.

**Mapping to trust vocabulary**, emitted in `trust-handoff.json`, never left to a reader:

| memo-loop `impact` | trust `severity` |
|---|---|
| `blocking` | `must-fix` |
| `material` | `must-fix` when it leaves a claim `unsupported` or `conflicting`; else `should-fix` |
| `minor` | `polish` |

`should-fix` is a **trust** severity that exists only downstream of that mapping. A finding emitted with
it inside memo-loop is rejected by the validator.

## The pairwise-ranking envelope

Council mode ranks anonymized drafts. It uses **this** format — `council.md` imports it by pointer and
never authors a second one. Same transport ladder, same failure ladder, same independence discipline as
the critic above; only the task differs.

**Anonymization is orchestrator-side and structural.** Drafts are relabeled `A`/`B`/`C` before any
ranking. The label map is written to the run dir and **never enters a ranker prompt** — enforced by the
same substring check as the memory guard in Step 2. Strip stance names and drafter metadata too: "the
risk-first draft" identifies its author as surely as a byline does.

**Every pair runs in both orders.** One comparison, two dispatches, `order: "AB"` and `order: "BA"`.

```json
{ "schema": "memo-loop.ranking.v1",
  "comparison_id": "CMP-03",
  "pair": {"left": "A", "right": "B"},
  "order": "AB",
  "rubric_items_evaluated": {"n": 10, "of": 10},
  "per_item": [
    {"rubric_item_id": "R-04",
     "reasoning": "A grounds both cost figures on the same monthly basis; B gives one monthly and one total, so a reader must do arithmetic to compare.",
     "winner": "A"}
    /* one record per rubric item, reasoning before winner */ ],
  "verdict": "A",
  "independence": {
    "ranker_model_name": "gpt-5.6-sol", "ranker_model_version": "2026-06-11",
    "timestamp": "ISO-8601",
    "rubric_sha256": "…", "ranker_prompt_sha256": "…",
    "draft_sha256": {"A": "…", "B": "…"} } }
```

The same transport-error rules apply: a missing envelope, a missing independence field, or
`n < of` is a transport error and goes through retry-once → demote → abort. A ranking that "came back
empty" is never read as a tie. The JSON Schema derived from this payload for `--output-schema` obeys
the strict-mode rules stated under [Step 4's schema](#the-schema) — they are not optional here either.

**Reconciliation — never resolved by picking one ordering.** Swapping candidate order flips verdicts by
10–15 points of win rate, so a verdict that survives only one order is a position artifact:

| `AB` verdict | `BA` verdict | Reconciled |
|---|---|---|
| A | A | **A** |
| B | B | **B** |
| tie | tie | **tie** |
| A | B | **undecided** — the verdict is a position artifact |
| B | A | **undecided** — same |
| a draft | tie | **undecided** — position-dependent |

`undecided` is passed to the chairman **as a tie**. Write both orders' envelopes plus the reconciled
result to `<run-dir>/council/rankings/`; a reconciliation recorded without both orders on disk is
invalid.

**The blinded absolute score — a producer, not an assertion.** Pairwise rankings cannot establish "the
merge is at least as good as the best input": they are relative, and the merge was never in the
tournament. So score **every input draft and the merge** absolutely, blinded, against the frozen
compiled rubric, and write `<run-dir>/council/blinded-scores.json`:

```json
{ "schema": "memo-loop.blinded-score.v1",
  "rubric_sha256": "…", "item_count": 10,
  "grader": {"model_name": "…", "model_version": "…", "timestamp": "ISO-8601",
             "grader_prompt_sha256": "…"},
  "drafts": [
    {"label": "A", "draft_sha256": "…", "pass": 6,
     "per_item": [{"rubric_item_id": "R-01", "verdict": "PASS"}]},
    {"label": "MERGE", "draft_sha256": "…", "pass": 8, "per_item": [ ]} ] }
```

The grader sees labels only — never the stance, never which one is the merge. Scoring is the compiled
score from `intake-and-rubric.md`, unchanged: integer cross-product comparison, full item set, frozen
denominator.

## The fault-injection override — test only

The eval must be able to break the transport **after** the probe passes — that exact boundary is where
the retry → demote → abort ladder lives, and it is otherwise unreachable: revoking auth fails the
*probe* instead (a different boundary), and repinning the model means editing the deployed skill rather
than invoking it normally.

**The switch.** One environment variable, set per invocation, never persisted anywhere:

```
MEMO_LOOP_FAULT_INJECT=critic-dispatch:<mode>[:rungs=<list>]

  mode  = once | persistent
  rungs = comma-separated rung numbers, or `all`. Default: 1.
```

**Semantics.** The fault is applied at the moment of dispatch — after the probe returned PASS, after the
prompt was assembled, hashed and memory-checked. The dispatch is not executed; the transport returns a
synthetic non-zero exit with the body `FAULT_INJECTED: <spec>`, which flows into Step 4's transport-error
classification exactly like a real failure. Nothing else in the ladder is special-cased.

Rung 2 performs no model dispatch, so there the fault fails that rung's one mechanical action — writing
the portable packet — and is classified as a transport error at rung 2. That is what keeps `rungs=all`
able to reach exhaustion.

| Mode | Behaviour | What it proves |
|---|---|---|
| `once` | Fails **the first eligible dispatch only**, then disarms for the rest of the run. | The same-rung **retry succeeds**: the failure is recorded, no demotion, no abort. A recovered fault is a PASS, not a miss. |
| `persistent` | Fails **every** attempt at every rung in `rungs`, for the whole run. | With `rungs=1`: the retry also fails → a recorded demotion to rung 2. With `rungs=all`: every rung exhausts → `ABORTED / critic-transport-exhausted`. |

Both modes are needed. A one-shot fault alone would simply be recovered by the ladder's retry, so an
assertion that only accepts demotion would fail against correct behaviour; a persistent fault alone
could never show that the retry works.

**It records itself.** `run.json` carries the key on **every** run — `null` when the variable is unset:

```json
"fault_injection": {
  "spec": "critic-dispatch:persistent:rungs=all",
  "mode": "persistent", "rungs": [1, 2, 3],
  "armed_at": "2026-08-04T18:02:00Z",
  "fired": [ {"round": 1, "rung": 1, "attempt": 1, "at": "…"},
             {"round": 1, "rung": 1, "attempt": 2, "at": "…"},
             {"round": 1, "rung": 2, "attempt": 1, "at": "…"}
             /* … rung 2 attempt 2, then rung 3 attempts 1 and 2 … */ ],
  "disarmed_at": null }
```

**It cannot be left on silently.** Five mechanisms, all cheap:

1. **A startup banner**, printed before any work, on every mode including `status`:
   `FAULT INJECTION ACTIVE: critic-dispatch:persistent:rungs=all — this run is a test run.`
2. **`test_run: true`** in `run.json`, and the same banner at the top of the convergence report.
3. **The before/after pair is never archived** to the calibration archive, whatever the terminal state
   — a fault-injected run must not become a known-good exemplar.
4. **Lessons are not appended** to the resolved lessons file. They are written to the run dir's
   `lessons-entries.jsonl` for audit and `run.json` records
   `"lessons_suppressed": "fault-injection active"`. Synthetic transport demotions must not inflate the
   occurrence counts that gate promotion.
5. **`status` lists fault-injected runs separately**, so an exported variable is visible on the next
   invocation rather than discovered later.

**An unparseable spec is a hard ABORT**, never a silent no-op — `MEMO_LOOP_FAULT_INJECT=1` aborts,
naming the accepted grammar. A typo'd fault spec that quietly did nothing would make the transport
eval vacuous while it reported PASS, which is the same class of defect as the silent zero-findings read
this whole file exists to prevent.

`critic-dispatch` is the only injection point that exists. There is no fault switch for the probe, the
revision, the scorer or the write path; those boundaries are reachable without one.
