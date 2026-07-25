# /routing-update research protocol — verify the landscape, never assert from memory

The house rule applies with full force here: **every capability, price, or
availability claim that lands in the SSOT must be verified via a tool at update
time** — the SSOT's own `prices:` and `models:` blocks are marked "EXAMPLE — re-verify
before use" precisely because model facts drift and this skill is the mechanism that
re-grounds them. Never fill in a diff from training-data recall of "what that model
probably costs" — providers change pricing, aliases, and dial semantics between
releases, and a stale assumption silently poisons every future dispatch that reads the
SSOT.

## Prerequisite gate

Before any research lane below runs, confirm at least ONE of these is connected:

- **Exa** (`web_search_exa`, `deep_researcher_start` / `deep_researcher_check`)
- **Ref** (`ref_search_documentation`, `ref_read_url`)
- **Perplexity** (its ask/search tool)

If none is connected, do not proceed past the snapshot step of SKILL.md — stop and
ask the operator to supply the researched numbers themselves, with a source. A
changeset built on an unverified guess is worse than no changeset.

## Lane 1 — the active provider's own documentation (mandatory, first)

Go directly to the provider's official docs/pricing pages (via whichever connected
tool can reach them — Ref for structured doc search, Exa/Perplexity for a web
search landing on the official page). This is the authoritative source for:

- current model IDs + aliases (what a runtime's model-selection shortcuts resolve to)
- per-model pricing ($/MTok in/out, intro pricing windows, batch/caching multipliers)
- deprecations + retirement dates
- effort/reasoning-dial availability per model (which models accept the provider's
  native control — `effort` / `reasoning_effort` / `thinking_level` / etc. — and
  which tiers exist)

Every `providers.<name>.models:` diff, every `providers.<name>.effort.map:` diff, and
every `prices.<name>:` diff in the changeset cites this lane. If a cached/summarized
reference looks stale for the model in question, chase the live doc URL before
writing the diff.

**This lane is provider-agnostic by construction** — apply it to whichever provider
the change is about (Anthropic, OpenAI, Gemini, or any other profile the SSOT
carries), not only the currently-`active_provider:`. Updating a dormant provider
profile is a legitimate use of this skill; it just carries less urgency than updating
the active one.

## Lane 2 — external priors (parallel with lane 1)

For a NEW model/tier where the SSOT needs quality-vs-cost placement, not just prices:

- A web-search tool (Exa or Perplexity) — benchmarks, practitioner reports,
  head-to-head evals against the provider's existing tiers.
- `ref_search_documentation` / `ref_read_url` — the provider's own effort/reasoning
  doc, model-overview page, and any agent-runtime model-config deltas.

Write findings to a durable artifact (e.g.
`claude/evals/routing/external-priors-<topic>-<YYYY-MM>.md`, once that directory
exists in your deployment) so the changeset can cite a stable file instead of a chat
transcript. External priors inform *placement hypotheses*; they do NOT substitute for
a calibration eval when one is warranted — say so in the changeset's Confidence
section.

## Runtime-bump path (replaces lanes 1–2)

Trigger: the coding-agent runtime/binary version changed (effort-dial honoring is
runtime-version-dependent and unobservable post-hoc — it can flip inert→live or
live→inert between adjacent versions).

1. Run your deployment's effort-canary probe (a small live-trial harness that submits
   the same task at each effort level and checks whether behavior actually differs)
   against the ACTIVE provider's models. Its per-(model, effort) verdicts are the
   research output.
2. If the dial is inert anywhere it matters: that's the changeset content — document
   which pairs are inert on this runtime version, and propose whatever the SSOT
   should say about it (e.g. mapping that intent to `null` for that tier). Do not
   silently keep effort pins the runtime ignores.
3. No web research needed unless the bump ALSO shipped new models for the active
   provider — if so, fall through to lanes 1-2 for those specific models.

## What "done researching" means

You can fill in the changeset's per-diff evidence brackets with a concrete citation
for every value that changes — a documentation/pricing verification, a canary-probe
result file, an external-priors artifact, or a dated operator decision. A diff you
can't cite isn't ready to propose.
