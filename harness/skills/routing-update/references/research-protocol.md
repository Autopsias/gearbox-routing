# /routing-update research protocol — verify the landscape, never assert from memory

The house rule (user CLAUDE.md "Behavior" §1) applies with full force here: **every
capability, price, or availability claim that lands in the SSOT must be verified via a
tool at update time** — the SSOT's own `prices:` block says "VERIFIED via the /claude-api
skill — NOT from memory" and every future diff keeps that bar.

## Lane 1 — `/claude-api` skill (mandatory, first)

Invoke the `claude-api` skill. It is the authoritative source for:

- current model IDs + aliases (incl. what `claude` binary aliases like `best`/`opusplan` resolve to)
- per-model pricing ($/MTok in/out, intro pricing windows, batch/caching multipliers)
- deprecations + retirement dates
- effort/reasoning parameter availability per model (which models accept the dial, which
  tiers exist, adaptive-thinking semantics)

Every `prices:` diff and every "model X exists / is deprecated / accepts effort" claim in
the changeset cites this lane. If the skill's cached reference looks stale for the model in
question, chase the live doc via `mcp__ref` before writing the diff.

## Lane 2 — external priors (parallel with lane 1)

For a NEW model/tier where the SSOT needs quality-vs-cost placement, not just prices:

- `mcp__exa__web_search_exa` (or `deep_researcher_start` for a genuinely new family) —
  benchmarks, practitioner reports, head-to-head evals.
- `mcp__perplexity-ask__perplexity_ask` — cross-check the same questions; disagreement
  between lanes is signal to keep digging, not to average.
- `mcp__ref__ref_search_documentation` / `ref_read_url` — Anthropic's effort doc, models
  overview, Claude Code model-config deltas.

Write findings to `evals/routing/external-priors-<topic>-<YYYY-MM>.md` (precedent:
`external-priors-vulcanbench-2026-07.md`) so the changeset can cite a stable artifact
instead of a chat transcript. External priors inform *placement hypotheses*; they do NOT
substitute for the calibration eval when the runbook's re-run triggers demand one — say so
in the changeset's Confidence section.

## Binary-bump path (replaces lanes 1–2)

Trigger: the `claude` binary version changed (effort honoring is binary-version-dependent
and unobservable post-hoc — it has flipped inert→live between adjacent versions before).

1. Run `~/.claude/evals/routing/harness/preflight.sh --live` (the effort-canary; costs real
   trials). Its per-(model,effort) verdicts are the research output.
2. If the dial is inert anywhere it matters: that's the changeset content — document which
   pairs are inert on this binary (`harness/canary-status-<version>.json`), and propose
   whatever the SSOT should say about it. Do not silently keep effort pins the binary ignores.
3. No web research needed unless the bump ALSO shipped new models.

## What "done researching" means

You can fill in the changeset's per-diff evidence brackets with a concrete citation for
every value that changes — a `/claude-api` verification, a preflight verdict file, an
external-priors artifact, or a dated operator decision. A diff you can't cite isn't ready
to propose.

## Radar — dated watch items

Model-landscape changes that don't yet warrant a changeset diff, but that the next
research pass (Lane 1 or Lane 2, per trigger) should deliberately re-evaluate rather
than rediscover from scratch. Each entry: what it is, why it's not a diff yet, and what
would flip it into one. Remove an entry once it's been evaluated (converted to a diff,
or explicitly declined with a dated note in a changeset's Confidence section).

- **Advisor tool beta** (`advisor-tool-2026-03-01`, fetched from platform.claude.com
  docs 2026-07-10) — executor+advisor model pairing inside ONE Messages request.
  Advisor tokens are billed at the advisor model's own rate via `usage.iterations[]`
  (not the executor's rate) — a genuinely different cost shape than a separate advisor
  dispatch. NOT available on Bedrock/GCP/Vertex/Foundry — Anthropic-API-only. Relevant
  to any API-side project this harness's routing touches (e.g. the health-advisor
  pipeline), NOT to Claude Code sessions themselves (Claude Code doesn't call the raw
  Messages API this way). Watch for: GA / non-beta header, Bedrock/GCP parity, or a
  first API-side project in this harness's scope that could use it — any of those is
  reason to open a changeset diff.
- **GPT-5.6 ultra measured cost** (added 2026-07-10, v1.5) — only MODELED
  estimates of ultra's cost multiplier exist (~2–4×, techsy.io; 4 parallel
  subagents by default per openai.com). The SSOT bans ultra-by-default on the
  Codex lane partly on this uncertainty. Flip to a diff if a measured figure,
  a real-world blowup report, or an OpenAI-published multiplier lands.
- **ChatGPT-plan quota shape** (added 2026-07-10, codex-lane spot-check) — is
  the rolling 5-hour window a SHARED pool across gpt-5.6 tiers, or a per-model
  allowance? Secondary sources report distinct msgs/5h per tier (implying
  per-model), but no OpenAI-owned page confirming it was loadable. The SSOT's
  `codex_peer.lane.degradation.quota_exhausted` rung assumes a shared pool
  (degrade to NO-CODEX, not to another tier). Flip to a diff if OpenAI
  publishes the quota shape, or if an in-session `/status` observation shows
  per-model counters draining independently.
- **gpt-5.3-codex / gpt-5.5-codex manifest discrepancy** (added 2026-07-10,
  v1.5) — absent from the live codex-rs models.json despite blog claims of
  Feb-2027 support. Legacy *-codex shutdown is 2026-07-23. Re-check on the
  next manifest pull; matters only if the lane's gpt-5.5 degradation rung
  ever disappears.
- **Managed agents (multi-agent sessions) beta** (`managed-agents-2026-04-01`, fetched
  from platform.claude.com docs 2026-07-10) — hosted coordinator/roster sessions, cap
  20 roster agents, 25 concurrent threads, delegation depth 1 (no sub-delegation from a
  roster agent). Relevant only if `/plan-execute` or similar dispatch sessions ever move
  from local subagent dispatch to hosted managed-agent infra — not applicable to the
  current local-dispatch shape. Watch for: GA, depth-1 limit lifted, or an explicit
  proposal to move plan-execute dispatch to hosted infra — any of those is reason to
  open a changeset diff.
