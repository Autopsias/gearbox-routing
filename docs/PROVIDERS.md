# Providers — choosing and switching

Gearbox ships three worked provider profiles — `anthropic`, `openai`,
`gemini` — inside `claude/model-routing.yaml`'s `providers:` block. Exactly
one is active at a time (`active_provider:`). This doc covers picking one at
install time, switching later, and what "effort" means on each.

For the schema every profile satisfies (models/effort/escalation/degrade
shape), see [`ARCHITECTURE.md`](../ARCHITECTURE.md) §3. For *how to research
and fill in* a provider (including a brand-new one), see
[`docs/METHODOLOGY.md`](METHODOLOGY.md) §2 and §5.

## Choosing at install time

```bash
./install.sh --provider anthropic     # default
./install.sh --provider openai
./install.sh --provider gemini
```

`--provider` sets `active_provider:` in the installed copy of
`claude/model-routing.yaml` and re-renders the routing digest so
`CLAUDE.md`'s `BEGIN/END ROUTING` block shows resolved model names for that
provider, not abstract tier labels. Omit the flag to install with the
`anthropic` default.

## Switching later

`active_provider:` is the **only line a provider swap edits** — that
invariant is load-bearing throughout the schema (`task_classes:` never
changes when you switch). To switch:

1. Edit `active_provider:` in `claude/model-routing.yaml`.
2. Confirm the target profile's `calibration.status` is `researched`, not
   `unresearched` — an unresearched profile as the *active* one is a hard
   validation error (see below), not a soft warning.
3. Re-run the drift guard: `claude/scripts/verify-routing.sh`. It checks that
   every `task_classes[*].tier` resolves under the new
   `providers[active_provider].models`, and that the version/consumers are in
   sync.
4. Re-render the digest so `CLAUDE.md` reflects the new provider's resolved
   model names (`claude/scripts/render-routing-digest.py`, or re-run
   `install.sh` against the already-installed target — it's idempotent).

There is no partial-switch state: either the guard is green on the new
provider, or you haven't actually switched.

## Per-provider effort semantics

**There is no cross-provider effort equivalence** — see `ARCHITECTURE.md` §2
for why this is a permanent design decision, not a gap to fill later. Each
profile's `effort.map` is that provider's own policy for translating the
neutral `light` / `standard` / `thorough` intent into its native dial:

| Provider | Native control | What it actually governs |
|---|---|---|
| `anthropic` | `effort` | ALL token spend (text + tool calls + thinking) **and** visibly shapes agentic behavior — how many tool calls, how much preamble, how tightly the model scopes to what was asked. Turning it down changes how the agent behaves, not just how long it thinks. |
| `openai` | `reasoning_effort` (Responses API: `reasoning.effort`; Chat Completions: `reasoning_effort`) | Internal reasoning-token depth before the visible response. Not documented to carry Anthropic's agentic-behavior-shaping property. |
| `gemini` | `thinking_level` | A maximum-depth ceiling on internal reasoning before responding. A bound, not a spend-shaping dial. Mutually exclusive with the legacy `thinking_budget` param (setting both is a 400 error). |

Each provider's `effort.map` in `claude/model-routing.yaml` is the actual
intent → native-level table, per tier, with a `source:` doc link and an
inline note on any tier whose model rejects the dial entirely (mapped to
`null` for every intent — omit the param rather than guess a value). Read
that block directly rather than trusting a summary here to stay current.

## Staleness cadence for the shipped example profiles

The three shipped profiles (`anthropic` / `openai` / `gemini`) are dated
examples, correct as of their `calibration.date`. **Re-verify each example
provider's model ids, prices, and effort-dial semantics against that
provider's current docs on the earlier of: that provider's next model
release, or at least quarterly.** When you do:

1. Confirm/update `models:`, `effort.map`, and `prices.<name>` against
   first-party docs (never memory — see `docs/METHODOLOGY.md` §2).
2. Bump `calibration.date` (and `provider_version` if the provider names
   one) to the day you checked.
3. Run the drift guard — it warns (not fails) once `calibration.date` crosses
   the staleness horizon, and CI fails the build on a stale `as_of` past that
   point, so a re-verify that isn't stamped will surface on its own.
4. Land the update through `/routing-update` so the version bump and
   CHANGELOG entry travel with it (`docs/METHODOLOGY.md` §4).

**Owner:** whoever runs `/routing-update` for this deployment. If that's
unowned in your setup, assign it before you rely on the shipped profiles past
one quarter — an unowned staleness cadence is how "verify-before-use example"
quietly becomes "trusted stale data."

## Provider not in the shipped set?

Adding a new one is a data-only change — no code, no schema change. Follow
`docs/METHODOLOGY.md` §5 end to end.
