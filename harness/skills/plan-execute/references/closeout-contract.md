# Closeout Contract

Every subagent dispatched by `/plan-execute` ends its final message with exactly
one closeout block. This is the **only** channel through which a subagent
influences plan state — the subagent never edits `PLAN.html`, `manifest.json`,
or `run_state.json`.

## Contents

- [The block](#the-block)
- [Fields](#fields) (worked examples: [Evidence](#evidence--worked-example-a-require_evidence-session) · [Degradation](#degradation--worked-example-requested-fable-ran-opus) · [Learnings](#learnings--worked-example-a-non-obvious-root-cause-worth-keeping) · [Deviations](#deviations--worked-example-an-edge-case-forced-a-conservative-choice))
- [Semantic rules the orchestrator enforces (H2)](#semantic-rules-the-orchestrator-enforces-h2)
- [Extraction rules the orchestrator enforces (P4)](#extraction-rules-the-orchestrator-enforces-p4)
- [Worked examples](#worked-examples)

## The block

```
<plan-execute-closeout>
{
  "session": "s01",
  "result": "DONE",
  "items_completed": ["eng-01"],
  "items_blocked": [],
  "notes": {"eng-01": "Implemented; tests green", "s01": "Spike landed cleanly"},
  "dispatch_next": true,
  "human_checkpoint_reason": null
}
</plan-execute-closeout>
```

## Fields

| Field | Type | Meaning |
|---|---|---|
| `session` | string | The dispatched session id. MUST match — a mismatch is a semantic error. |
| `result` | `"DONE"` \| `"PARTIAL"` \| `"BLOCKED"` | Session outcome. |
| `items_completed` | string[] | Item ids (⊆ session scope) now finished. |
| `items_blocked` | string[] | Item ids (⊆ session scope) that can't be completed. Explain each in `notes`. |
| `notes` | object | Map of id → one-line outcome. Keys may be item ids and/or the session id. Values are HTML-escaped and capped at 500 chars by the orchestrator. |
| `dispatch_next` | bool | Hint only. Normally `true`; `false` if you want to pause. |
| `human_checkpoint_reason` | string \| null | If non-null (and `result` ≠ BLOCKED), the session is set to `AWAITS_REVIEW` and the loop halts for human review. **Set it only when a human must decide something specific, and make the string carry that decision in plain language** — what happened, what the options are, what you recommend (e.g. `"Migration touched 3 tables the plan didn't list — proceed with the extra tables, or roll back and re-scope?"`). Never a bare `"please review"`; if you cannot name a decision, leave it null — the session's verify gates already check the work. |
| `evidence` | string[] | **Vista ③ — only when the session sets `require_evidence`.** Paths to artifacts that PROVE the work engaged: an eval/metrics JSON, the saved output of `grep -c <log-event>` (count > 0), a screenshot, a command transcript. At `verify-finalize` every path must exist and be non-empty or `DONE` is refused (reworks like a failed gate). Absolute, or relative to the project root the orchestrator runs from. Ignored by the closeout digest, so adding it never trips state-drift. |
| `degraded_from` | object \| null | **Reactive model degradation (added 2026-07).** Present ONLY when the orchestrator substituted a lower model after the requested one failed at dispatch (model unavailable/unentitled — e.g. Fable suspended/paywalled). Shape: `{"requested": "<token>", "ran": "<token>", "reasoning": "xhigh"}` — records "requested Fable, ran Opus @ xhigh" for the audit trail + cost transparency. Set by the orchestrator, not the subagent. Ignored by the closeout digest, so it never trips state-drift. |
| `learnings` | object[] | **OPTIONAL — learning loop (added 2026-07-09).** Include ONLY when the session solved a non-obvious problem: a surprising root cause, an approach that had to be reverted, a constraint discovered the hard way. Each entry: `{"summary": "<one line>", "grounding": "<file:line or PR #NNN>"}`. Routine sessions omit the field entirely — routine work must not generate noise. Persisted verbatim, ignored by the closeout digest (like `evidence`), and harvested by the orchestrator's learning-capture pass at plan completion/halt (SKILL.md § "Learning capture"). |
| `deviations` | string[] | **OPTIONAL (added 2026-07-10).** One line per edge case that forced the session off the plan — the conservative option it picked, and why. Absent is the normal case; `apply` tolerates missing/older closeouts with no `deviations` key identically to today. When present, `apply` appends the joined text to the session's `notes` entry, so it's visible in the session card's notes on `PLAN.html` (not just in `_closeouts/<sid>.json`) without any change to the closeout schema's required fields. Ignored by the closeout digest (like `evidence`/`degraded_from`), so adding it never trips state-drift. This is how plan-vs-reality drift feeds forward into later sessions instead of evaporating. |

### Evidence — worked example (a `require_evidence` session)

```
<plan-execute-closeout>
{"session":"s05","result":"DONE","items_completed":["rg-05"],"items_blocked":[],"notes":{"rg-05":"reserve-J lever flipped; recall@10 0.40→0.61 on the 81-Q holdout"},"dispatch_next":true,"human_checkpoint_reason":null,"evidence":["docs/operations/rg-05-reserve-j-eval.json","_evidence/s05/grep-reserve-j-engaged.txt"]}
</plan-execute-closeout>
```
Here `grep-reserve-j-engaged.txt` holds the `grep -c '\[reserve-j\] engaged'` output (a non-zero count) — the literal "mechanism engaged" proof the project's CLAUDE.md demands. If either file is missing or empty, `verify-finalize` returns `rework` (gate `evidence`) instead of `DONE`.

### Degradation — worked example (requested Fable, ran Opus)

The orchestrator, not the subagent, adds `degraded_from` when a dispatched model
is refused for access/entitlement (e.g. Fable is paywalled/suspended) and it
re-dispatches on the fallback (Fable→Opus 4.8 → Sonnet 5, always at reasoning
`xhigh`, floor at Sonnet). The substitution is announced in the stream AND recorded:

```
<plan-execute-closeout>
{"session":"s02","result":"DONE","items_completed":["core-01"],"items_blocked":[],"notes":{"core-01":"library + CLI landed; tests green"},"dispatch_next":true,"human_checkpoint_reason":null,"degraded_from":{"requested":"fable","ran":"opus","reasoning":"xhigh"}}
</plan-execute-closeout>
```
This replaces the silent-inherit anti-pattern: the run's cost + capability are
legible ("requested Fable, ran Opus @ xhigh"), never a mystery inherited model.

### Learnings — worked example (a non-obvious root cause worth keeping)

```
<plan-execute-closeout>
{"session":"s06","result":"DONE","items_completed":["api-02"],"items_blocked":[],"notes":{"api-02":"Webhook retries fixed; PR #430"},"dispatch_next":true,"human_checkpoint_reason":null,"learnings":[{"summary":"Provider re-sends webhooks with a NEW delivery id on retry, so idempotency must key on event id, not delivery id","grounding":"src/webhooks/dedupe.py:41"}]}
</plan-execute-closeout>
```
A session that just implemented what the plan said, with no surprises, includes
no `learnings` field at all.

### Deviations — worked example (an edge case forced a conservative choice)

```
<plan-execute-closeout>
{"session":"s07","result":"DONE","items_completed":["mig-03"],"items_blocked":[],"notes":{"mig-03":"Backfill script landed"},"dispatch_next":true,"human_checkpoint_reason":null,"deviations":["Spec assumed the legacy table had a unique index on user_id; it didn't — added a dedupe pass before the backfill instead of the planned direct copy"]}
</plan-execute-closeout>
```
`apply` appends the deviation text to `s07`'s note, so it shows up on the session
card in `PLAN.html`, not only in `_closeouts/s07.json`. A session with no
deviations omits the field entirely — same as `learnings`.

## Semantic rules the orchestrator enforces (H2)

- `items_completed ⊆ session.items` and `items_blocked ⊆ session.items` — no hallucinated ids.
- `items_completed ∩ items_blocked = ∅` — an item can't be both.
- For `result == "DONE"`: `items_completed ∪ items_blocked == session.items` — full coverage. If you finished only some, use `PARTIAL`.

Any violation sets the session to `BLOCKED`, sets the halt flag, and stops the
loop. Schema validation alone won't catch a hallucinated id or a partial-coverage
DONE — that's why semantic verification exists.

## Extraction rules the orchestrator enforces (P4)

- The block must be the **last** non-whitespace content of your message. Trailing prose after `</plan-execute-closeout>` is rejected.
- If you show an *example* closeout earlier, wrap it in a ``` code fence — fenced blocks are ignored by the extractor, so they won't be mistaken for the real thing.
- If two real (unfenced) blocks appear, the LAST one wins.
- Do NOT wrap your real closeout in a code fence.

## Worked examples

### DONE — single item

```
<plan-execute-closeout>
{"session":"s01","result":"DONE","items_completed":["eng-01"],"items_blocked":[],"notes":{"eng-01":"Auth spike works; PR #412"},"dispatch_next":true,"human_checkpoint_reason":null}
</plan-execute-closeout>
```

### PARTIAL — finished one of two, needs another pass

```
<plan-execute-closeout>
{"session":"s02","result":"PARTIAL","items_completed":["eng-02"],"items_blocked":[],"notes":{"eng-02":"Flow built","s02":"eng-03 not started — ran out of scope"},"dispatch_next":true,"human_checkpoint_reason":null}
</plan-execute-closeout>
```
The orchestrator flips `eng-02` to DONE and leaves `s02` as `PARTIAL`; the loop
re-dispatches `s02` next time to finish the rest.

### BLOCKED — can't proceed

```
<plan-execute-closeout>
{"session":"s03","result":"BLOCKED","items_completed":[],"items_blocked":["eng-03"],"notes":{"eng-03":"Migration needs a prod DB credential I don't have"},"dispatch_next":false,"human_checkpoint_reason":null}
</plan-execute-closeout>
```

### Requesting a human checkpoint

```
<plan-execute-closeout>
{"session":"s04","result":"DONE","items_completed":["docs-01"],"items_blocked":[],"notes":{"docs-01":"Docs drafted"},"dispatch_next":false,"human_checkpoint_reason":"Please review the public-facing wording before the next session ships it"}
</plan-execute-closeout>
```
The session is marked DONE for its items, then set to `AWAITS_REVIEW`; the loop
halts and the user continues with `--resume`.
