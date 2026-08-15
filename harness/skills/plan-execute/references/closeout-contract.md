# Closeout Contract

Every subagent dispatched by `/plan-execute` ends its final message with exactly
one closeout block. This is the **only** channel through which a subagent
influences plan state — the subagent never edits `PLAN.html`, `manifest.json`,
or `run_state.json`.

## Contents

- [The block](#the-block)
- [Fields](#fields) (worked examples: [Evidence](#evidence--worked-example-a-require_evidence-session) · [Degradation](#degradation--worked-example-requested-fable-ran-opus) · [Learnings](#learnings--worked-example-a-non-obvious-root-cause-worth-keeping) · [Deviations](#deviations--worked-example-an-edge-case-forced-a-conservative-choice) · [REPLAN](#replan--worked-example-a-discovery-that-invalidates-future-sessions) · [Decision brief](#decision_brief--worked-example-a-blocked-session-that-actually-reports))
- [The stuck protocol — the rung between "retry" and "raise the model"](#the-stuck-protocol--the-rung-between-retry-and-raise-the-model)
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
| `escalated_from` | object \| null | **UPWARD escalation (ESC-02, added 2026-08-13, manifests stamped `plan_schema_version` >= 6).** The mirror of `degraded_from`, for a substitution that goes UP. Present ONLY when a session's gate kept failing at the SAME root cause and `begin` therefore dispatched it one rung up the SSOT's ladder. Shape: `{"authored": {"model", "reasoning"}, "ran": {"model", "reasoning"}, "attempt": <int>, "rung": <int>, "generation": <int>}` — e.g. authored sonnet@high, ran fable@medium at rung 2. `attempt` is the DISPATCH attempt (1 = the first one) and `rung` is the ladder position; they are different numbers — the climb does not arm until attempt 3, and a refused rung or a held rung breaks the correspondence entirely. The orchestrator copies it verbatim from the `begin` payload; the subagent never writes it. **Codex-backed members carry it too (ESC-03, 2026-08-14)**, with both halves naming that lane's own cells — e.g. authored `gpt-5.6-luna@max`, ran `gpt-5.6-terra@max` — never a Claude model; the climb never crosses providers. Paired with `model_ran`/`reasoning_ran` and `model_ran_source` (`requested` | `attested` | `unknown`) — `requested` means "this is what we ASKED Task for" and is NOT a claim about what actually served the request, which nothing in this loop can currently prove. `generation` bumps on `redispatch` and on an `amend-session --model/--reasoning`, so records from before a deliberate re-run are not pooled with records from after it. `model_ran`, `reasoning_ran` and `model_ran_source` are SEPARATE top-level keys the orchestrator copies from the same `begin` member — the ledger reads all four as one record, so carry them together or not at all. Ignored by the closeout digest, so adding it never trips state-drift. |
| `learnings` | object[] | **OPTIONAL — learning loop (added 2026-07-09).** Include ONLY when the session solved a non-obvious problem: a surprising root cause, an approach that had to be reverted, a constraint discovered the hard way. Each entry: `{"summary": "<one line>", "grounding": "<file:line or PR #NNN>"}`. Routine sessions omit the field entirely — routine work must not generate noise. Persisted verbatim, ignored by the closeout digest (like `evidence`), and harvested by the orchestrator's learning-capture pass at plan completion/halt (SKILL.md § "Learning capture"). |
| `plan_impact` | object \| null | **OPTIONAL — the REPLAN gate (added 2026-08-12, schema v3+).** Set it when this session learned something that invalidates work the plan has NOT done yet. Shape: `{"invalidates": ["s07", "s08"], "reason": "<one line: what you learned and why it breaks them>"}`. Every id must name a session that exists in the manifest and must not be this session — a bad id is a **closeout schema error** (session BLOCKED + halt), never a silent drop. On a `plan_schema_version >= 3` plan, `apply` parks the whole plan in a **REPLAN checkpoint**: the loop stops and the operator is handed a brief naming the invalidated sessions with three options (amend / retire / proceed). Plans stamped below v3 ignore the field entirely. Ignored by the closeout digest, so adding it never trips state-drift. See [REPLAN](#replan--worked-example-a-discovery-that-invalidates-future-sessions). |
| `decision_brief` | object | **REQUIRED on `result: "BLOCKED"` (added 2026-08-12, schema v5+); recommended whenever `human_checkpoint_reason` is set.** Shape: `{"attempts": ["…"], "findings": [{"source": "…", "takeaway": "…"}], "options": ["…"] (1-3), "recommendation": "…"}`. A BLOCKED closeout without it is a **closeout schema error** — session BLOCKED + halt, reworks like any malformed closeout. `findings` carries what the [stuck protocol](#the-stuck-protocol--the-rung-between-retry-and-raise-the-model)'s research pass found, with sources; a pass that found nothing says so as a finding rather than omitting the key. More than three options is refused — that is a menu, not a decision. Rendered into `HALT_NOTICE.txt` and the `apply` output, so the operator reads the options and the recommendation without opening `_closeouts/<sid>.json`. Enforced only on plans stamped `plan_schema_version >= 5` (`closeout_pipeline.DECISION_BRIEF_MIN_SCHEMA`) — in-flight plans keep the old contract. Ignored by the closeout digest, so adding it never trips state-drift. |
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

### REPLAN — worked example (a discovery that invalidates future sessions)

```
<plan-execute-closeout>
{"session":"s04","result":"DONE","items_completed":["ing-04"],"items_blocked":[],"notes":{"ing-04":"Connector landed"},"dispatch_next":true,"human_checkpoint_reason":null,"plan_impact":{"invalidates":["s07","s08"],"reason":"the vendor retired the bulk-export API s07/s08 were written against; only per-record polling remains"}}
</plan-execute-closeout>
```

`s04` still closes DONE — a REPLAN reports a fact about the PLAN, it is not a
failure of the session that found it. What changes is that the loop stops: the
plan is halted with `kind: "replan"`, the invalidated session cards each gain a
"flagged by s04's plan_impact" note, and `plan` returns

```
{"action":"replan","replan":[{"decision":"s04 learned something that invalidates s07, s08. Amend those sessions, retire them, or proceed unchanged?", "options":[…amend…retire…proceed…], "recommendation":null, "recommendation_required":true}], "replan_needs_recommendation":["s04"]}
```

**The brief owes a recommendation, and that is enforced (schema v5+).** The
module never invents one — it cannot judge which sessions a specific discovery
invalidated — so the judgement is yours, and you supply it as a recorded input
BEFORE presenting the decision:

```
run.py recommend-replan <plan-dir> --session s04 --recommendation 'amend — s07/s08 are 80% reusable against the per-record endpoint, retiring them throws that away'
```

That writes the recommendation onto the park record and re-renders
`HALT_NOTICE.txt`, so the operator reads it beside the three options without
opening any JSON. **`resolve-replan` REFUSES while the slot is empty**
(`replan.RECOMMENDATION_MIN_SCHEMA = 5`; plans stamped below it, and parks
raised before this existed, are unaffected). A recommendation cannot be supplied
in the same command as the decision on purpose — advice that arrives with the
answer advised nobody.

The operator then picks, and the chosen change is applied through
`amend-session` / `retire-session` / `redispatch` — never by hand. The decision is
recorded with `run.py resolve-replan --session s04 --decision <pick> --reason '…'`,
which appends one line to the plan's rendered change log and releases the halt.
`amend` and `retire` are **refused** until the change log shows the amendment
actually landed, so the decision cannot be a rubber stamp.

**Deviations vs plan_impact.** `deviations` records what THIS session did
differently. `plan_impact` records what a FUTURE session can no longer do. Use
both when both are true.

**Precedence.** A closeout may carry `human_checkpoint_reason` AND `plan_impact`.
The human checkpoint parks first; the REPLAN park lands the moment that
checkpoint is acked. On a session with a `verify` block, the REPLAN park waits
for the gates to pass — for the same reason the checkpoint does.

**When NOT to use it.** Your own session's output being wrong is `redispatch`,
not `plan_impact`. Work you noticed that the plan simply never covered is a note
(or an `add-session`), not an invalidation. Reserve it for "a session that has
not run yet is now written against something that is no longer true."

### `decision_brief` — worked example (a BLOCKED session that actually reports)

```
<plan-execute-closeout>
{"session":"s03","result":"BLOCKED","items_completed":[],"items_blocked":["eng-03"],"notes":{"eng-03":"Migration needs a prod DB credential I don't have"},"dispatch_next":false,"human_checkpoint_reason":null,"decision_brief":{"attempts":["Ran the migration against the local replica — schema matches, so the script is not the problem","Tried the read-only app role in 1Password — it cannot CREATE INDEX","Checked whether the index could be built online without elevated rights"],"findings":[{"source":"PostgreSQL 16 docs, CREATE INDEX CONCURRENTLY","takeaway":"CONCURRENTLY still needs table ownership, so a read-only role cannot do this at all"},{"source":"internal runbook docs/ops/db-access.md","takeaway":"prod DDL is deliberately gated behind the on-call DBA, not a role we can be granted"}],"options":["Ask the on-call DBA to run the migration in the next window — slowest, but the documented path","Ship the feature behind a flag reading the un-indexed table — works now, degrades at ~50k rows","Move the index to the nightly maintenance job — no human in the loop, but lands a day later"],"recommendation":"Option 1: the runbook makes DBA-gated DDL a deliberate control, and options 2 and 3 both route around it"}}
</plan-execute-closeout>
```

Without the `decision_brief`, that same closeout is REFUSED on a v5 plan — the
note alone ("needs a credential I don't have") is a symptom, not a report.
`apply` renders the brief into `HALT_NOTICE.txt` under the halt reason, so
`--status` shows the operator the three options and the recommendation before
they open anything.

## The stuck protocol — the rung between "retry" and "raise the model"

**The rule.** Two consecutive failed attempts with the SAME root-cause signature
— same error class, same message locus, ignoring paths, line numbers and
whitespace — stop the retry loop. A *different* error next time is progress and
does not trigger anything. The same one twice means the model of the problem is
wrong, and a third blind retry cannot fix that.

**It is a mechanism, not a reminder.** `verify._gate_failed` reduces every gate
failure to a normalised signature (`stuck_protocol.signature`) and keeps it, with
a consecutive counter, under `run_state.json`'s `stuck` key — mutable runtime
state, never a digest input, so it can never trip `state-drift`. On the second
same-signature failure the research pass is appended to
`_verify_state/<sid>.feedback.md` (which the re-dispatch reads) and a
`stuck_protocol_armed` event lands in `run.ndjson`. That is why "two different
errors do not trigger it" is a runnable test rather than a claim about prose.

**The pass.** One time-boxed research pass, 10 minutes, hard stop, against
sources OUTSIDE the repository — tiered Perplexity → Exa → Ref, first available
wins, degrading gracefully to the built-in web search when no MCP is configured.
Then exactly one of: apply the fix, citing what the research changed about the
diagnosis; or close `BLOCKED` carrying the `decision_brief` above, whose
`findings` are that pass's sources.

**Where it sits on the escalation ladder.** The standing ladder is *retry →
raise reasoning → advisor → raise model → Codex*. The research pass is the rung
between **retry** and **raise model**: a second identical failure is evidence
about the problem, not about the model, and paying for a bigger model to repeat a
wrong diagnosis is the expensive version of retrying blind. Escalate the model
only after the research pass has run and either failed to explain the failure or
named a fix too large for this session.

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

A BLOCKED closeout on a `plan_schema_version >= 5` plan MUST carry a
`decision_brief`; see the [worked example](#decision_brief--worked-example-a-blocked-session-that-actually-reports)
above. On an older plan the brief is optional and the bare form below still
validates:

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
