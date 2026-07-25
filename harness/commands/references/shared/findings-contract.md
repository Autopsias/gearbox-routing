# Uniform Findings Contract (shared)

**This is the single, canonical way every quality step reports what it found.** The
ship-tail (Cluster C: `/test-orchestrate`, `/commit-orchestrate`, `/ci-orchestrate`)
and their fixer agents all emit findings in this one envelope so their results compose
and the lanes can consume them. It supersedes the ~5 flat `fixed|partial|failed`
vocabularies the orchestrators used to emit.

## Contents

- [The model in one breath](#the-model-in-one-breath)
- [JSON shape](#json-shape)
- [Action-assignment rules (how a tool decides the `action`)](#action-assignment-rules-how-a-tool-decides-the-action)
- [Gate-decision mapping (to epic-dev's PASS / CONCERNS / FAIL)](#gate-decision-mapping-to-epic-devs-pass-concerns-fail)
- [Lane-A scope decision (AUTHORITATIVE)](#lane-a-scope-decision-authoritative)
- [Adapters (the only places a non-canonical shape is allowed)](#adapters-the-only-places-a-non-canonical-shape-is-allowed)
- [Worked example (validates against the shape)](#worked-example-validates-against-the-shape)
- [How orchestrators reference this](#how-orchestrators-reference-this)


> **Status of this contract** — authoritative for Cluster C and for any Lane-A
> conductor-level emission (see [Lane-A scope](#lane-a-scope-decision-authoritative)).
> Do NOT introduce a second findings vocabulary. If you need a field this envelope
> lacks, add it here, do not fork the shape.

---

## The model in one breath

Each quality step returns a **report** (one run) containing a list of **findings**.
A finding states the **finding** (authoritative: the defect and why it is wrong) and a
**suggestion** (a *hypothesis* for the fixer, not a mandate). Every finding carries an
**action** — exactly one of `no-op | auto-fix | ask-user` — plus a **severity**
(`error | warning | info`) and lifecycle fields. The report rolls up to one of
epic-dev's gate decisions: **PASS / CONCERNS / FAIL**.

The three actions, in plain terms:

| action | meaning | who acts |
|--------|---------|----------|
| `no-op` | nothing to do — informational, already correct, or out of scope | nobody |
| `auto-fix` | objectively wrong AND the fixer may correct it without asking | a fixer agent |
| `ask-user` | the call is not the tool's to make — surface it and wait | the human |

**When in doubt, `ask-user`.** Silence and `auto-fix` are not the safe defaults; asking is.

---

## JSON shape

A report:

```json
{
  "schema": "findings-contract/v1",
  "source": "test-orchestrate|commit-orchestrate|ci-orchestrate|<agent-name>|user",
  "decision": "PASS|CONCERNS|FAIL",
  "post_fix_verified": false,
  "findings": [ { /* finding, see below */ } ],
  "summary": "one line"
}
```

A finding:

```json
{
  "id": "r1",
  "severity": "error|warning|info",
  "file": "path/to/file.py",
  "line": 0,
  "description": "the FINDING — authoritative: the defect and why it is wrong",
  "action": "no-op|auto-fix|ask-user",
  "suggestion": "a hypothesis for the fixer, NOT a mandate",
  "status": "open|fixed|escalated",
  "blocking": false,
  "source": "agent|user",
  "evidence": "log line, failing assertion, scanner rule id — what proves the finding",
  "intent_touched": false,
  "post_fix_verified": false
}
```

### Field rules (read these — they are the contract, not decoration)

- **`action` is REQUIRED.** A **missing `action` is INVALID** everywhere except the one
  [legacy adapter](#legacy-adapter-the-only-place-a-missing-action-is-tolerated). Never
  default a missing action to `auto-fix` — a legacy emitter with no action could smuggle
  a blocking or intent-touching case into silent auto-fix. Missing action => the consumer
  MUST treat the finding as `ask-user` and `blocking: true` until a human resolves it.
- **`severity`** — `error` (objectively broken: test fails, type error, security hole,
  reliability bug), `warning` (should fix: perf, style-with-teeth, coverage gap), `info`
  (FYI, no action implied).
- **finding vs `suggestion`** — the `description` is the **finding** and is authoritative;
  the `suggestion` is the fixer's starting hypothesis. A fixer may reach the correct fix a
  different way than the suggestion proposes. Never encode a blast-radius-heavy global
  mutation as the only path in `suggestion`; prefer the narrowest local fix.
- **`status`** — `open` (not yet acted on), `fixed` (auto-fix applied and re-verified),
  `escalated` (handed to the human via ask-user).
- **`blocking`** — `true` means this finding alone forces a FAIL until resolved. An
  unresolved `error` is `blocking: true`. `blocking` is NEVER auto-cleared by applying a
  fix; it clears only when `status` becomes `fixed` AND `post_fix_verified` is `true`.
- **`source`** — `agent` (the tool found it) or `user` (a human-supplied finding, e.g.
  from an intent block or a manual review note). `user`-source findings are never silently
  overridden by an agent.
- **`evidence`** — the concrete artifact that proves the finding (a failing assertion, a
  ruff/semgrep rule id, a CI log line). A finding with no evidence is `info` at most.
- **`intent_touched`** — `true` when the fix would alter, re-add, or undo something the
  change's **intent block** names as deliberate. Consumed by the
  [intent-precedence rule](#intent-precedence-rule-lives-here). In s02 this field exists
  and is consumed; **s03 populates it** from the intent block. s02 does not depend on s03 —
  this rule reads the field; s03 merely fills it — so there is no cycle. The **intent block**
  this field reads, and how each review path (Lane B verify-gate, Cluster C ship-tail) feeds
  it in (BEGIN/END-wrapped untrusted data + the engagement log line), are defined in the
  shared [intent-into-review](intent-into-review.md) pattern.
- **`post_fix_verified`** — `true` only after the tool re-ran the relevant check and the
  finding's defect is gone. **A report MAY NOT carry `decision: PASS` unless every
  `auto-fix` finding has `status: fixed` AND `post_fix_verified: true`.** No re-verify =>
  not PASS, regardless of how good the metrics look.

---

## Action-assignment rules (how a tool decides the `action`)

Apply in order; first match wins:

1. **Intent precedence (highest).** If `intent_touched: true` — i.e. the fix would re-add
   or undo code the intent block names as deliberately removed/changed — the action is
   **`ask-user`**, *regardless of objective severity*. A correct-looking security or
   reliability fix that re-adds deliberately-deleted code still downgrades to `ask-user`.
   (See [Intent-precedence rule](#intent-precedence-rule-lives-here).)
2. **Ambiguous or intent-shaped => `ask-user`.** If the finding touches what the change was
   *meant* to do, expresses a design/taste/architecture preference, or the tool is not sure
   the fix is objectively correct — **`ask-user`**. When in doubt, ask.
3. **Objective correctness / security / reliability => `auto-fix`.** A failing test, a real
   type error, an injection hole, a missing-await race — the fixer may correct it
   autonomously, **even if the smallest correct fix re-adds a little previously-deleted
   code** (UNLESS rule 1 fired: that re-add is named in the intent as deliberate).
4. **Nothing to do => `no-op`.** Already correct, informational, or out of this tool's scope.

> Cross-cutting blast radius is an `ask-user` signal, not an `auto-fix` one: if the only
> correct fix touches shared/global/cross-package code (a global guard, a schema, a
> golden/parity fixture, another package's module), set `action: ask-user` and say so in
> the `description`. This mirrors Lane A's `auto_fixable=false` rule.

### Intent-precedence rule (lives here)

This rule is **part of this contract**, not s03. It reads `intent_touched` (which s03
populates) and re-classifies:

```
if finding.intent_touched and finding.action == "auto-fix":
    finding.action = "ask-user"
    finding.status = "escalated"
    finding.evidence += " [downgraded: fix re-adds/undoes intent-named code]"
```

Because the rule only *reads* `intent_touched`, s02 ships it inert-but-correct (the field
defaults `false`, so nothing downgrades) and s03 turns it live by populating the field. No
dependency cycle.

---

## Gate-decision mapping (to epic-dev's PASS / CONCERNS / FAIL)

A report's `decision` is computed from its findings — **deterministically**, in this order:

| Condition over the report's findings | `decision` |
|--------------------------------------|------------|
| ANY finding unresolved with `blocking: true` OR (`severity: error` AND `status: open`) | **FAIL** |
| ELSE ANY finding with `action: ask-user` (and not already FAIL) | **CONCERNS** |
| ELSE every finding is `no-op`, or `auto-fix` with `status: fixed` AND `post_fix_verified: true` | **PASS** |

In words:

- **all no-op / auto-fix-only (all fixed + re-verified) => PASS.** PASS is unreachable
  until `post_fix_verified` is `true` for every auto-fix.
- **any ask-user => CONCERNS.** CONCERNS is "accept-with-flag": a human decides. It is NOT a
  red gate; it surfaces a deliberate-choice / judgment call for review.
- **any unresolved blocking/error => FAIL.** **FAIL is blocking and is NOT auto-fix** — a
  FAIL never silently marks the work done; it stops the tail.

`WAIVED` is epic-dev's human-override of a CONCERNS/FAIL gate. It is **not** a value this
contract's automation produces — only a human may waive. Tools emit PASS/CONCERNS/FAIL;
a human may subsequently record WAIVED out-of-band (per epic-dev's waiver handling).

This is exactly epic-dev's existing gate vocabulary
(`references/epic-dev/full/phase-8-quality-gate.md`,
`references/epic-dev/end-tests.md`), so a Cluster-C report and a Lane-A gate decision are
the same three words and compose without translation.

---

## Lane-A scope decision (AUTHORITATIVE)

**Question (raised by s02, required-resolved before s07):** does this one contract also
span Lane A's `epic-*` agents, or is it Cluster-C-only?

**Decision: the contract is the SINGLE canonical findings shape for BOTH lanes.** There is
exactly one findings vocabulary across the system. BUT the BMAD-orbit `epic-*` *sub-agents*
(`epic-code-reviewer`, `epic-quality-gate`, ...) are **not rewritten in s02** — they are the
user's own, edited only by their owner / by the sessions that own them (the conductor-level
emission lands in **s07**, which emits this contract directly). To guarantee Lane A never
runs two *live undocumented* vocabularies in the meantime, s02 ships an explicit, documented
**[Lane-A adapter](#lane-a-adapter)** that maps the existing `epic-code-reviewer` output
shape onto this envelope. That keeps the count of *live, undocumented* contracts at zero:
the epic-reviewer shape is now a documented projection of this one, not a rival.

Net: one contract, one documented adapter per legacy shape. No two live undocumented
contracts anywhere.

---

## Adapters (the only places a non-canonical shape is allowed)

### Legacy adapter (the only place a missing `action` is tolerated)

The pre-contract fixer agents emit a flat status object:

```json
{ "status": "fixed|partial|failed|conflict", "issues_fixed": 0, "files_modified": [], "summary": "..." }
```

This shape has **no `action` field**. The legacy adapter — and ONLY the legacy adapter —
maps it onto a finding, and it does so **conservatively** (never to silent auto-fix):

| legacy `status` | adapter output |
|-----------------|----------------|
| `fixed`   | `{action: auto-fix, status: fixed, severity: warning, blocking: false, post_fix_verified: false}` — note: still needs a re-verify pass before PASS |
| `partial` | `{action: ask-user, status: escalated, severity: error, blocking: true}` — incomplete => a human decides |
| `failed`  | `{action: ask-user, status: escalated, severity: error, blocking: true}` — FAIL is blocking, never auto-fix |
| `conflict`| `{action: ask-user, status: escalated, severity: error, blocking: true}` |
| *(missing)* | `{action: ask-user, status: escalated, severity: error, blocking: true}` — **missing never becomes auto-fix** |

Rule: **a missing `action` outside this adapter is INVALID** and the consumer rejects the
report. Inside the adapter, missing => `ask-user`/blocking (the safe direction). The adapter
is a migration shim; new emitters MUST emit the canonical envelope directly.

### Lane-A adapter

Lane A's `epic-code-reviewer` emits (today):

```json
{
  "total_issues": 0,
  "high_issues":   [{"id":"H1","description":"...","file":"...","line":0,"suggestion":"..."}],
  "medium_issues": [{"id":"M1","description":"...","file":"...","line":0,"suggestion":"..."}],
  "low_issues":    [{"id":"L1","description":"...","file":"...","line":0,"suggestion":"..."}],
  "auto_fixable": true
}
```

It already has the contract's DNA (finding-vs-suggestion split; an `auto_fixable`
cross-cutting rule). Map it onto the envelope:

| epic-reviewer field | envelope mapping |
|---------------------|------------------|
| `high_issues[]`   | `severity: error` |
| `medium_issues[]` | `severity: warning` |
| `low_issues[]`    | `severity: info` |
| `description`, `file`, `line`, `id`, `suggestion` | copied through 1:1 |
| `auto_fixable == true`  AND `severity == error/warning` | `action: auto-fix` |
| `auto_fixable == false` (cross-cutting/blast-radius) | `action: ask-user`, `blocking: (severity==error)` |
| `severity == info` | `action: no-op` (unless a human flags it) |
| any issue whose fix is `intent_touched` | **`action: ask-user`** (intent precedence overrides the above) |

Roll up via the [gate mapping](#gate-decision-mapping-to-epic-devs-pass-concerns-fail):
any `error` open => FAIL; else any `ask-user` => CONCERNS; else PASS once re-verified. This
matches epic-dev's existing PASS/CONCERNS/FAIL gate exactly. **This adapter is the migration
step** that keeps Lane A on one documented vocabulary; the eventual rewrite of the
`epic-*` agents themselves is owned by their owner / s07, not s02.

---

## Worked example (validates against the shape)

```json
{
  "schema": "findings-contract/v1",
  "source": "test-orchestrate",
  "decision": "CONCERNS",
  "post_fix_verified": true,
  "findings": [
    {
      "id": "r1",
      "severity": "error",
      "file": "apps/api/tests/test_auth.py",
      "line": 42,
      "description": "Mock returns dict but real service returns SearchResponse — assertion never exercises the real contract.",
      "action": "auto-fix",
      "suggestion": "Use tests/fixtures/mock_factories.py SearchResponse factory.",
      "status": "fixed",
      "blocking": false,
      "source": "agent",
      "evidence": "AssertionError: 'results' not found; junit.xml testcase test_search_contract",
      "intent_touched": false,
      "post_fix_verified": true
    },
    {
      "id": "r2",
      "severity": "warning",
      "file": "apps/api/app/services/search/ranking.py",
      "line": 88,
      "description": "Reverting reverse=True would re-add the ranking inversion the change deliberately removed.",
      "action": "ask-user",
      "suggestion": "Confirm the new ordering is intended before 'fixing' the sort direction.",
      "status": "escalated",
      "blocking": false,
      "source": "agent",
      "evidence": "intent block lists 'inverted ranking removed on purpose'",
      "intent_touched": true,
      "post_fix_verified": false
    }
  ],
  "summary": "1 auto-fixed (mock contract), 1 escalated to user (intent-touching sort direction)."
}
```

`decision: CONCERNS` because r1 is fixed+verified (would be PASS alone) but r2 is
`ask-user` (intent precedence downgraded an otherwise-auto-fixable change). No `error` is
left `open`, so it is not FAIL.

---

## How orchestrators reference this

Each ops orchestrator's output section points here and emits a `findings-contract/v1`
report (its fixer agents may still emit the legacy flat status, which the orchestrator runs
through the [legacy adapter](#legacy-adapter-the-only-place-a-missing-action-is-tolerated)
before rolling up). The orchestrator's own report MUST be the canonical envelope with a
`decision`. See:

- `references/test-orchestrate/failure-categorization.md`
- `references/commit-orchestrate/json-output-format.md`
- `references/ci-orchestrate/agent-routing.md`

and the orchestrator command files (`test-orchestrate.md`, `commit-orchestrate.md`,
`ci-orchestrate.md`).
