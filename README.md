# Gearbox

**The right gear for every task.**

> **EXPORT — do not edit this repo as a source.** This repo is the public,
> genericized export of a private source repo (`<your-org>/<your-private-harness>`).
> **The edit surface is that source-repo clone** (`~/your-private-harness`): edits
> there deploy to `~/.claude` (`git pull --ff-only`) and separately export to
> this repo — never the reverse. Edits made directly here are overwritten by
> the next sync. See [`docs/HARNESS.md`](docs/HARNESS.md) §The three tiers.

Gearbox is a provider-agnostic **model + reasoning-effort routing policy**
for coding agents — one versioned file (`claude/model-routing.yaml`) that
says, per task class, *which capability tier of model to use and how hard to
drive it*, plus the guard scripts and skills that keep every consumer
surface (CLAUDE.md, agents, CI) in sync with that one file.

It answers two questions for every task, separately:

1. **Which model?** — a capability tier (`cheap_fast` / `workhorse` /
   `frontier_reasoner`), not a hardcoded model id.
2. **How hard should it think?** — an effort intent (`light` / `standard` /
   `thorough`), translated into whatever native dial your provider actually
   exposes (Anthropic `effort`, OpenAI `reasoning_effort`, Gemini
   `thinking_level` — three different *kinds* of control, not just different
   names for the same thing; see [`ARCHITECTURE.md`](ARCHITECTURE.md) §2).

Swapping provider is a one-line edit (`active_provider:`). The task
vocabulary never changes; only the tier→model and effort→dial translation
does.

## Requirements

- **A coding agent/harness that honours model + effort routing** — Claude
  Code, or anything that reads the rendered CLAUDE.md digest and acts on it.
- **Required for `/routing-update` to self-calibrate:** one research-capable
  MCP provider (Exa, Ref, or Perplexity) — it's how the skill verifies model
  ids/prices/effort semantics against live docs instead of guessing from
  memory.
- **Optional:** the `openai-codex` plugin, only if you want the codex peer
  lane for adversarial second-model review.

Gearbox does **not** require ponytail / code-review / frontend-design /
security-guidance or any domain MCP — those are orthogonal to routing, don't
install them on Gearbox's account.

## The provider table (illustrative — verify before use)

> **The tiers, model ids, and prices below are dated, illustrative
> examples**, not settings to trust as-is. Re-verify against each provider's
> current docs and rebase onto your own models before relying on them — see
> [`docs/PROVIDERS.md`](docs/PROVIDERS.md) for the staleness cadence and
> [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) §2 for how to redo the
> calibration yourself.

| Provider | Example models (cheap_fast / workhorse / frontier_reasoner) | Native effort control |
|---|---|---|
| `anthropic` | claude-haiku-4-5 / claude-sonnet-5 / claude-opus-4-8 | `effort` (low…max) — shapes token spend *and* agentic behavior |
| `openai` | gpt-5.4-mini / gpt-5.4 / gpt-5.5 | `reasoning_effort` — bounds internal reasoning depth |
| `gemini` | gemini-3.1-flash-lite / gemini-3.5-flash / gemini-3.1-pro-preview | `thinking_level` — a maximum-depth ceiling |

There is **no cross-provider effort equivalence** — each profile's
`effort.map` is that provider's own translation, calibrated independently.
See `docs/PROVIDERS.md` for the full per-provider breakdown.

## Quickstart

```bash
git clone https://github.com/Autopsias/gearbox-routing.git
cd gearbox-routing
git config core.hooksPath .githooks   # required if you will ever push — see below
./install.sh --claude-home /path/to/a/fresh/dir --accept-example-profile --provider anthropic
```

> **If you intend to push to a fork, the `core.hooksPath` line is not
> optional.** Git does not wire repo-local hooks on clone. Those hooks are the
> only gate that scans for confidential identifiers before a push — CI runs
> structural checks only and cannot do it, because the denylist itself is
> private. See [`GENERICIZATION.md`](GENERICIZATION.md) §"Why CI cannot scan for
> identifiers".

> **The default install target is a fresh directory — never your real
> `~/.claude`.** Run it against a throwaway dir first. Pointing it at your
> live `~/.claude` requires *both* `--claude-home "$HOME/.claude"` **and**
> `--i-understand-this-mutates-live-claude` as an explicit, separate opt-in
> — see `install.sh --help`.

`--provider` picks which shipped example profile (`anthropic` / `openai` /
`gemini`) becomes `active_provider:`. `--accept-example-profile` is required
because the shipped profiles are verify-before-use examples, not your
researched policy — pass `--profile <your-file>` instead once you have one.

Where each piece lands under `--claude-home`:

```
claude/model-routing.yaml        the SSOT policy file (--provider swaps active_provider:)
claude/model-routing.digest.md   rendered summary spliced into CLAUDE.md's ROUTING block
claude/scripts/                  verify-routing.sh (drift guard), resolve_route.py (resolver), renderer
claude/skills/routing-update/    model-landscape change -> researched changeset -> all surfaces regenerated
claude/skills/routing-retro/     read-only retrospective — is routing actually working?
claude/evals/routing/            eval runbook + MISROUTES.md ledger template
claude/fixtures/                 guard + resolver test fixtures
CLAUDE.md                        gets a <!-- BEGIN/END ROUTING --> block installed or updated in place
```

The install is idempotent (re-running with the same flags is a no-op diff)
and ends by running `claude/scripts/verify-routing.sh --full` as a final
guard — a non-zero exit means the install left drift, not a clean pass.

## The `/routing-update` + `/routing-retro` loop

- **`/routing-update`** — run when a provider ships a new model, changes
  pricing, or changes effort-dial semantics. Researches the change against
  live docs (needs a research MCP — see Requirements), proposes an
  operator-approved changeset, and regenerates every consumer surface
  (digest, CHANGELOG, version bump) through one commit. See
  `docs/METHODOLOGY.md` §4.
- **`/routing-retro`** — read-only. Run periodically (roughly weekly, or
  every ~30 sessions) to judge whether the current routing is actually
  working: over/under-modeled tasks, cost outliers, receipt mismatches
  against `MISROUTES.md`. It never edits the policy file itself — findings
  route back through `/routing-update`.

## Further reading

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — tier vocabulary, provider-profile
  schema, resolver contract.
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — how to classify a task,
  calibrate a provider profile for your own models, and add a new provider.
- [`docs/VERSIONING.md`](docs/VERSIONING.md) — semver rules for the policy
  file and CHANGELOG conventions.
- [`docs/PROVIDERS.md`](docs/PROVIDERS.md) — choosing/switching providers,
  per-provider effort semantics, staleness cadence for the example profiles.
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — how a consumer (orchestrator,
  lint step, CI check) binds to the policy file and resolver.
- [`CHANGELOG.md`](CHANGELOG.md) — policy + framework changes.
- [`GENERICIZATION.md`](GENERICIZATION.md) — scrub rules applied to
  everything ported into this public repo.

## Status

Pre-release. See [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md) for the
current gate status before this is published.
