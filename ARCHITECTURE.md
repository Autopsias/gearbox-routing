# Gearbox architecture

Gearbox (distributed as **`gearbox-routing`**) is a provider-agnostic model +
reasoning-effort routing framework for coding agents. It answers one question per
task: *which model, driven how hard?* — and keeps the answer in one versioned,
guard-checked policy file instead of scattered per-tool defaults.

This document fixes the three abstractions every other file builds on:

1. the **capability-tier vocabulary** (provider-neutral),
2. the **provider-profile schema** (where all provider-specific behaviour lives),
3. the **escalation / degrade schema** the vendored resolver consumes.

---

## 1. Capability tiers (provider-neutral)

Three tiers. Task classes point at tiers, never at model ids.

| Tier | What it buys | Typical use |
|---|---|---|
| `cheap_fast` | Cheapest competent model; no reasoning dial assumed | renames, formatting, codemods, doc edits |
| `workhorse` | Mid-price generalist; reliable tool use; elastic effort | CRUD, wiring, templated features, multi-file refactors |
| `frontier_reasoner` | The provider's strongest reasoning model; priced accordingly | architecture, ambiguous tradeoffs, hard root-cause, one-shot irreversible calls |

### Task classes → tiers

The five task classes are the stable public vocabulary (they survive provider swaps
and model releases):

```yaml
task_classes:
  mechanical:     { tier: cheap_fast,        intent: light }
  standard_build: { tier: workhorse,         intent: standard }
  agentic_build:  { tier: frontier_reasoner, intent: thorough }
  deep_reasoning: { tier: frontier_reasoner, intent: standard }
  linchpin:       { tier: frontier_reasoner, intent: thorough }
```

`intent` is defined next — it is **not** an effort level.

---

## 2. Effort is provider-specific behaviour, not a portable dial

**There is no cross-provider effort equivalence, and this repo will never ship an
"equivalent tier" table.** The controls differ in *kind*, not just in scale:

- **Anthropic `effort`** governs overall model *behaviour*: thinking depth, but also
  agentic persistence (tool-call count, willingness to keep working), and
  instruction-adherence tradeoffs. Turning it down changes how lazy the agent is,
  not just how long it thinks.
- **OpenAI `reasoning_effort`** and **Gemini `thinking_level` / thinking budget**
  primarily bound *thinking depth* before responding. They do not carry the same
  agentic-persistence semantics.
- Level names, defaults, and observed behaviour **drift release-to-release** on all
  three. Any mapping asserted from memory is wrong by construction; per-provider
  maps are filled in by *researched* calibration sessions and re-verified on
  provider version bumps.

Therefore the provider-neutral layer carries only an abstract **intent** label —
how much depth the *task* deserves — and each provider profile owns the translation
to its native control:

| Intent | Meaning (task-shaped, provider-blind) |
|---|---|
| `light` | Rote transformation; correctness is cheap; no deliberation expected |
| `standard` | Normal build/reasoning depth for the tier |
| `thorough` | Hard, long-horizon, or high-stakes; depth is worth paying for |

A provider profile is free to map two intents to the same native level (e.g. a
frontier model whose lowest effort already out-reasons everything else maps
`standard` **and** `thorough` to its minimum), or to ignore the dial entirely for a
model that rejects it. That asymmetry is the point: the mapping is calibration
data, not vocabulary.

---

## 3. Provider profiles

All provider-specific facts live under `providers:`; `active_provider:` selects
exactly one. `task_classes:` never changes when you switch providers.

```yaml
active_provider: anthropic        # the only line a provider swap edits

providers:
  <name>:
    calibration:                  # REQUIRED — when these values were measured
      status: researched | unresearched  # `unresearched` is a HARD validation error if this
                                  #   is the active_provider (never run active with no dial data)
      date: <ISO date>            #   validation WARNS (not fails) past a staleness horizon;
      provider_version: <str|null> #   MUST change whenever any effort `map` value changes
      research_ref: <str|null>    #   the calibration session/eval that filled the maps

    models:                       # tier -> concrete model id
      cheap_fast:        <model-id>
      workhorse:         <model-id>   # tier->model ALIASING is legal: two tiers may resolve
      frontier_reasoner: <model-id>   #   to the same id (provider with <3 distinct models)

    effort:
      control: <native dial name>     # e.g. effort | reasoning_effort | thinking_level
      map:                            # intent -> native level, PER TIER
        cheap_fast:        { light: <lvl>, standard: <lvl>, thorough: <lvl> }
        workhorse:         { light: <lvl>, standard: <lvl>, thorough: <lvl> }
        frontier_reasoner: { light: <lvl>, standard: <lvl>, thorough: <lvl> }
      # a tier whose model rejects the dial maps every intent to null (omit the param)

    escalation:                   # OR the literal string "none" (explicit opt-out)
      trigger: <prose>            # HUMAN/CALLER contract only (e.g. "2 failures at the same
                                  #   root cause"). The resolver does NOT evaluate this — the
                                  #   CALLER decides a rung failed and passes a typed signal.
      effort_ladder:              # ordered native rungs per tier; ladder may stop
        <tier>: [<lvl>, <lvl>]    #   early where a rung is known-dead for that model
      model_ladder: [cheap_fast, workhorse, frontier_reasoner]
      invariants: [<prose>]       # operator-facing notes only — NOT resolver logic (any
                                  #   machine invariant is a schema rule below, not prose here)

    degrade:                      # OR "none" — reactive fallback
      signals: [entitlement, unavailable]  # which typed signals walk the ladder. `refusal` is NOT
                                  #   here by default: a refused task surfaces to the operator
                                  #   (rerouting a refusal to a weaker model is refusal-shopping).
                                  #   Add `refusal` explicitly to opt in per profile. (Key is
                                  #   named `signals:`, not `on:` — a bare `on:` is coerced to
                                  #   the boolean `True` by YAML 1.1 parsers; see resolve_route.py.)
      ladder: { <tier>: <tier> }  # e.g. frontier_reasoner: workhorse
      floor: <tier>               # provider-wide absolute floor; a task_class MAY pin a
                                  #   higher per-class floor (linchpin must not drop as far
                                  #   as mechanical) via task_classes.<class>.min_tier
      effort_on_degrade:          # PER TARGET tier -> native level (compensation is
        <tier>: <lvl>             #   provider-specific; no universal rule)
```

### Resolver contract (normative — the schema all profiles satisfy)

The resolver is **stateless**. It never detects failures and never persists ladder
position; the caller owns both. One call = one decision:

- **Input:** `(task_class, active_provider, current: {tier, effort} | null, signal:
  none | failure | refusal | entitlement | unavailable)`.
- **Output:** `{model_id, native_effort}`, or `exhausted` when no rung remains.
- **Baseline (`signal: none`, `current: null`):** resolve `task_class → {tier, intent}`,
  then `intent → native_effort` via the tier's map, falling back to `standard` when the
  named intent is absent; alias a null effort to "omit the dial".
- **Escalation walk (`signal: failure`):** exhaust the current tier's `effort_ladder`
  first; when its rungs are spent, advance `model_ladder` by one rung and enter the new
  tier's `effort_ladder` **at its first rung** (not its intent-map level). Skip any
  `model_ladder` rung whose resolved `model_id` equals the current one (alias no-op).
- **Degrade walk (`signal` ∈ profile's `degrade.signals`):** follow `degrade.ladder`,
  applying `effort_on_degrade` at the target, never below `floor`.
- **Scope of a decision is per-task by default.** Every new `task_class` resolution
  starts from the baseline map; an escalation/degrade rung does not persist across tasks
  unless a profile adds an explicit stickiness field (none defined yet — YAGNI until a
  profile needs it).
- **`floor` is absolute** and overrides any ladder step, escalation or degrade.

### Schema rules

- **Model ids are verify-before-use examples.** Shipped profiles carry ids that were
  correct at the profile's calibration date; adopters MUST re-verify ids, pricing,
  and dial semantics against provider docs before trusting them.
- **Escalation/degrade shapes are per-provider calibration data.** Which rungs are
  dead, which model dominates which, how effort bills — all of that is measured for
  one provider at one point in time and lives only inside that provider's profile.
  Nothing in the neutral layer assumes another provider shares the shape.
- **Opt-out is explicit.** A profile that wants no automatic escalation or degrade
  says `escalation: none` / `degrade: none`; a missing block is a validation error,
  not a default.
- **Unresearched profiles ship with `map: null` placeholders** plus a comment naming
  the research session that must fill them. A null map means "do not set the dial";
  it is never silently substituted with another provider's values.
- **`standard` is the mandatory intent in every non-null effort map; the resolver
  falls back to it** when a task class names an intent a tier's map omits. `light`
  and `thorough` are optional per tier.
- **`degrade.floor` is absolute.** It overrides any escalation `model_ladder` or
  degrade `ladder` step — the resolver never returns a tier below the floor for
  judgement work, whichever ladder is running.
- **`cost_policy`: the escalation trigger is evidence-gated, never cost-blind.**
  `escalation.trigger` names a bounded, observable condition (e.g. "2 failures
  at the same root cause") — it is a quality gate, not a standing invitation to
  reach for `frontier_reasoner` because a task merely *feels* hard. A cheaper
  tier that fails fast is a cheaper diagnostic than defaulting high; raising
  effort/tier is conditioned on demonstrated failure, never on task-feel alone.
  This has no dedicated schema field (nothing here changes the resolver
  contract) — it names a principle the `escalation:` block already encodes
  structurally; see `docs/METHODOLOGY.md` §3 for the worked rationale.

### The vendored resolver

`claude/scripts/resolve_route.py` (built in a later session) is the portable path:
it reads `(task_class, active_provider)` from the policy file and resolves
`{model_id, native_effort}`, then — **on a failure/refusal signal supplied by its
caller** (the resolver detects nothing itself; `escalation.trigger` prose is a
human/consumer contract, not resolver logic) — walks the profile's escalation ladder
on reported failure and the degrade ladder on refusal, for **any** provider whose
profile supplies the blocks above. A consumer embedded in a specific harness (e.g. Claude
Code's `plan-execute/run.py`) may keep a mirror of its own ladder for its one
consumer, but that mirror MUST be guard-verified against this policy file (byte- or
contract-checked, the same shape as the existing drift guard) so it cannot silently
diverge; Gearbox's resolver is the reference implementation the schema is tested
against.

---

## 4. Anthropic profile — worked example (verify-before-use)

Illustrates the schema with the shapes measured in the source calibration
(2026-07); every value here is example data, not vocabulary:

```yaml
providers:
  anthropic:
    calibration:
      status: researched
      date: 2026-07
      provider_version: null
      research_ref: source-deployment eval sweeps (2026-07)
    models:                        # EXAMPLES — re-verify ids/pricing before use
      cheap_fast:        claude-haiku-<version>
      workhorse:         claude-sonnet-<version>
      frontier_reasoner: claude-<frontier>-<version>
    effort:
      control: effort
      map:
        cheap_fast:        { light: null, standard: null, thorough: null }   # rejects the dial
        workhorse:         { light: low, standard: medium, thorough: high }
        frontier_reasoner: { light: low, standard: low,    thorough: low }   # lowest effort already
                                       # out-reasons the workhorse ceiling; higher rungs are
                                       # escalation-only, never a standing default
    escalation:
      trigger: "2 failures at the same root cause"
      effort_ladder:
        workhorse:         [medium, high]        # stops at high — the next rung measured dead
        frontier_reasoner: [low, medium, high]
      model_ladder: [cheap_fast, workhorse, frontier_reasoner]
      invariants:
        - "never auto-drop judgement work below workhorse"
    degrade:
      signals: [entitlement, unavailable]
      ladder: { frontier_reasoner: workhorse }
      floor: workhorse
      effort_on_degrade: { workhorse: high }
```

The dead-rung, dominance, and effort-elasticity findings that produced these
numbers are Anthropic-specific measurements from the source deployment's eval
sweeps. They demonstrate *why* the schema keys exist; they are not claims about
any other provider.

---

## 5. What deliberately does NOT exist

- **No cross-provider effort equivalence table.** See §2.
- **No provider-neutral escalation shape.** Only the *schema* is neutral.
- **No fourth tier.** `agentic_build` shares a tier with a neighbouring class and
  distinguishes itself by `intent`; a separate tier would freeze one provider's
  price ladder into the vocabulary. Which tier it shares is CALIBRATION, not
  vocabulary — a frontier refresh that inverts the tiers' cost-per-solved-task
  ordering moves that class to another tier without any of the five class names
  changing (docs/METHODOLOGY.md §2b). That is the neutral layer working as
  designed.
- **No baked-in model ids in the neutral layer.** Ids live only in profiles, as
  dated, verify-before-use examples.

---

## 6. Open questions (deferred by design — dual-model review 2026-07-05)

Raised by the adversarial review, accepted as deferred rather than fixed now, so a
later session can reopen with a real driving case:

- **Split `thorough` into two neutral axes (reasoning depth vs agentic
  persistence)?** Deferred (YAGNI). §2 keeps intent task-shaped and provider-owned;
  revisit when a second provider profile can't express its tradeoff with one intent.
- **A capability-role axis (multimodal / long-context / low-latency), not just the
  three price/reasoning tiers?** Out of scope for a coding-task router; a v2 concern
  if Gearbox ever routes non-coding work.
- **Operational task-class definitions (inclusion/exclusion/tie-break table).** Lives
  in the consuming digest (the CLAUDE.md routing block a later session ports), not in
  this schema doc — noted here as a downstream requirement so it isn't lost.
