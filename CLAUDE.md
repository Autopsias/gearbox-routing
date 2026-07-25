> **EXPORT — do not edit this repo as a source.** Gearbox is the genericized,
> scrubbed export of a private source repo (`<your-org>/<your-private-harness>`).
> **The edit surface is that source-repo clone** (`~/your-private-harness`): edits
> there deploy to `~/.claude` and separately export to this repo. Edits made
> here are lost at the next sync. Change the source, then re-run the sync
> pipeline (`docs/HARNESS.md` §The three tiers).

<!-- BEGIN ROUTING -->
## Task routing — classify before you start

Classify the task, set the tier. Authority: `claude/model-routing.yaml`
(v2.3.0, active_provider=anthropic).

| Class | Cues | Resolved tier |
|---|---|---|
| mechanical | rename/format · codemod · doc edit | claude-haiku-4-5 |
| standard_build | CRUD · wiring · templated feature · scaffold | claude-sonnet-5 · medium |
| agentic_build | multi-file · integration · non-obvious debug | claude-sonnet-5 · high |
| deep_reasoning | architecture · security · ambiguous · hard root-cause | claude-opus-4-8 · high |
| linchpin | one-shot irreversible · plan-foundational call | claude-opus-4-8 · high |

- **Effort = default + escalation, not a ceiling** — never a floor on judgement work.
- **Fan-out pins an explicit tier** — never the frontier tier across N agents (see `fanout_policy`).
- **Escalate on evidence:** after 2 failures at one root cause, raise effort → advisor → tier → second-model peer.
- **Main session advisory:** `main_session.advisory_default_tier`/`_effort` — recommend `/model`/`/effort` on mismatch; cannot self-switch.
- **Routing receipt:** after delegate/escalate, log a one-liner (class → resolved tier) to `claude/evals/routing/MISROUTES.md` on any mismatch.
<!-- END ROUTING -->

## Commit messages here carry no trailers

**This repo is a repo-local exception to the global commit convention: no
`Claude-Session:` trailer, no session URL, no `Co-Authored-By:` trailer, on any
commit.** It is public — a session URL is a private-tier identifier and a
co-author line discloses which model wrote the change, and a commit message is
published irreversibly. Write subject + body (what changed and why) and nothing
else; provenance here is the `harness/SYNCED-FROM` stamp.

Enforced structurally, both directions: `.githooks/commit-msg` refuses to create
such a commit, `.githooks/pre-push` refuses to push one. Enable both once per
clone with `git config core.hooksPath .githooks` — an unwired clone has no gate,
and **CI cannot substitute**: it runs structural checks only and never scans for
confidential identifiers. See `GENERICIZATION.md`.
