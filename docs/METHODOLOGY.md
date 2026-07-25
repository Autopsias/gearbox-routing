# Methodology — calibrating Gearbox for your own setup

This is the reusable method. It replaces a private decision-history (internal
eval sweeps, one operator's model preferences) with a repeatable process you can
run against *your* models, *your* providers, and *your* task mix.

Read [`ARCHITECTURE.md`](../ARCHITECTURE.md) first — it fixes the vocabulary
(tiers, intent, provider profiles) this document assumes.

---

## 1. Classify a task into a tier

Every task_class in `claude/model-routing.yaml` names a `{ tier, effort }` pair.
When you meet a task that doesn't obviously match an existing row, classify it
by asking, in order:

1. **Is correctness cheap to verify and the transformation rote?** (rename,
   reformat, codemod, doc edit, mechanical dependency bump) → `mechanical` /
   `cheap_fast`.
2. **Is this a templated build with a clear spec — CRUD, wiring, scaffolding,
   a well-understood feature shape?** → `standard_build` / `workhorse`.
3. **Does it span multiple files, involve non-obvious integration, or require
   genuine debugging (not "apply the known fix")?** → `agentic_build`, at
   `thorough` intent. WHICH TIER this class resolves to is a calibration
   result, not part of the definition: it can sit on `workhorse` (same tier as
   #2, more depth) or on `frontier_reasoner`, and a frontier refresh can flip
   the answer without anything about the class changing (§2b). The shipped
   example puts it on `workhorse`; that is an illustration, not a finding.
   Measure it on your own lineup (§2) rather than assuming either answer.
4. **Is the judgment call itself hard** — architecture, an ambiguous tradeoff,
   a security-sensitive design, a root cause that resisted two prior
   attempts? → `deep_reasoning` / `frontier_reasoner`.
5. **Is this call one-shot and load-bearing** — a decision the rest of a plan
   or a migration rests on, not reversible by re-running it? → `linchpin` /
   `frontier_reasoner` at `thorough` intent.

Tie-break rule: when a task matches two classes, pick the **higher** one and
move on — the cost delta of over-classifying one task is small; the cost of
a wrong low-tier call compounding through a plan is not. Don't relitigate the
same tie-break twice; if it recurs, that's a sign the task_class boundary
itself needs a row, not more judgment calls at dispatch time (see §4).

## 2. Pick a `(tier, effort)` default for YOUR models

The three provider profiles shipped in `claude/model-routing.yaml`
(`anthropic`, `openai`, `gemini`) are **dated, verify-before-use examples**,
not settings to trust as-is. To calibrate your own:

1. **Confirm your `models:` map is current.** Tier → model id drifts every
   release. Re-verify against the provider's own docs, not memory (see
   `docs/PROVIDERS.md` for the per-provider staleness cadence).
2. **Confirm the `effort:` map matches how the dial actually behaves today.**
   The three native controls (Anthropic `effort`, OpenAI `reasoning_effort`,
   Gemini `thinking_level`) are different in *kind*, not just naming — see
   `ARCHITECTURE.md` §2. Never assume a provider's low/medium/high maps onto
   another provider's, or onto a prior model generation's.
3. **Find dead rungs and dominance before you rely on the ladder.** Run the
   full grid — every tier × every effort rung — over a task set drawn from
   *your* real work, and record accuracy (or pass rate) and cost per solved
   task for each cell. Two things fall out of that grid, and neither can be
   read off a vendor's docs.

4. **Write the finding into the profile, not just into your head.** Every
   `providers.<name>.calibration` block carries `status`, `date`,
   `provider_version`, and `research_ref` — fill all four so the next person
   (or the next `/routing-update`) knows what was checked and when.

### 2a. Ladder saturation ("dead rungs")

Walk one tier's row from the cheapest rung upward. Typically quality climbs,
then flattens while cost keeps climbing. The rung where it flattens is your
**saturation point**, and every rung above it is a **dead rung**: strictly more
expensive, not measurably better.

This matters because providers routinely recommend their *top* rung for
agentic coding. The recommended rung and the measured last-useful rung are
different numbers, and only one of them was measured on your workload.
Truncate `escalation.effort_ladder` at your saturation point, so that "still
failing at the last useful rung" escalates by **model_ladder** rather than by
buying more of something you've shown doesn't help. Keep any rung above it
reachable only by explicit operator election.

*Hypothetically:* if a tier's `high` and `xhigh` rungs score the same while
`xhigh` costs half again as much, `xhigh` is dead — truncate at `high`. The
shape is what transfers; the letters and the percentages will not.

### 2b. Tier dominance (and inversions)

Now read the grid **across** tiers. A **dominance** relation is one cell
beating another on *both* axes at once — higher quality *and* lower cost per
solved task. The intuition it breaks is "the expensive tier is for hard
problems": a hot workhorse burns output-rate tokens on retries and can end up
costing more per *outcome* than a calm frontier model that gets it right the
first time. Effort multiplies output-billed tokens, so cranking effort is
never free, and a stronger model at a *lower* rung is a real option.

The reason to re-run this after every frontier release is that dominance
**inverts**. A new generation landing at the previous generation's price can
flip which tier owns a whole task class in one step — nothing about the task
changed, the provider did. If your `agentic_build` row has not been re-measured
since the last frontier release, assume it is pointing at the wrong tier.

*Hypothetically:* a refresh could make the frontier tier's middle rung beat the
workhorse tier's top rung on both axes, which would move `agentic_build` up a
tier and move the standing main-session default up one rung with it. The
shipped `claude/model-routing.yaml` deliberately does **not** encode that
outcome — it is an illustration, and a real calibration would likely move at
least two of its rows.

**A stale-calibration smell worth naming:** if two different effort *intents*
(`light`, `standard`) resolve to the same native level in a tier's `effort.map`,
the map was probably written against an older generation that wasn't elastic
across the range. Re-measure before trusting it.

Both findings come from your own eval sweep or a retrospective (§3) — never
from a provider's marketing copy, and never from someone else's published grid,
which is stale the moment their provider ships.

## 3. Treat effort as escalation, not a ceiling

The default `(tier, effort)` a task_class resolves to is a **floor for normal
work**, not a cap you're stuck under when things go wrong. The escalation
ladder in `claude/model-routing.yaml`'s `escalation:` block (and each
provider's `escalation.effort_ladder` / `escalation.model_ladder`) exists
precisely so a stuck task can climb:

- **Raise effort one rung first** — cheaper than raising tier, and the more
  common fix (the model had the right approach but didn't push far enough).
- **Then raise tier one rung** — only after the current tier's effort ladder
  is exhausted.
- **Then pull in a second-model peer** (`codex_peer` block) — for
  architecture decisions, irreversible changes, security-sensitive work, or a
  task that's stuck even after escalating.

The trigger is explicit and bounded: **2 failures at the same root cause** —
not "this feels hard," which invites over-modeling from the first attempt.
Never invert this into using effort/tier as a ceiling ("this is only a
`mechanical` task, so it can never go past `cheap_fast`") — a `mechanical`
task that's failed twice for the same reason has stopped being mechanical and
earns the escalation walk like anything else.

**Dev-cost is a reason to try cheap first, never a reason to skip the ladder.**
A workhorse attempt is cheap to try and fail fast; reaching straight for
`frontier_reasoner` because a task is *plausibly* hard — without a failed
cheaper attempt on record — spends compute the evidence hasn't earned yet.
The inverse failure mode is just as real: don't let cost-consciousness turn
into under-modeling once the 2-failure bar is actually met — at that point
the ladder exists precisely so you climb it, not stall on it. The rule is
symmetric: escalate on evidence, never on task-feel in either direction.

`degrade:` is the separate, reactive-only counterpart: it fires on
`entitlement`/`unavailable` signals (a tier got refused for access reasons),
never on ordinary failure, and it never drops below the provider's `floor` —
see `ARCHITECTURE.md` §3 for the full contract.

## 4. Run the retro → update loop

Calibration is not a one-time setup step. Two skills close the loop:

- **`/routing-retro`** — read-only. Mines recent session transcripts and the
  misroute ledger (`claude/evals/routing/MISROUTES.md`) for signal: sessions
  that were over-modeled (a `cheap_fast` job would have done), under-modeled
  (a `frontier_reasoner` job landed on `workhorse` and needed a retry),
  cost outliers, or receipt mismatches (what a session *claims* it ran at vs.
  what routing said it should). It never edits the SSOT — its only write is an
  operator-confirmed append to `MISROUTES.md`.
- **`/routing-update`** — the write path. Takes a model-landscape change (new
  model, price change, deprecation, a `/routing-retro` finding worth acting
  on) and regenerates every affected surface — the provider's `models:` /
  `effort.map` / `prices:` block, the semver bump, and the `CHANGELOG.md`
  entry — through one operator-approved changeset. It never asserts a model
  fact from memory: it hard-stops if no research tool (a research MCP —
  Exa, Ref, or Perplexity; see `docs/INTEGRATION.md` "Dependencies &
  companions") is available to verify against current provider docs.

Cadence: run `/routing-retro` after roughly a week or ~30 sessions on a new
calibration, or whenever a task class "feels" wrong in the moment (surface it
immediately rather than waiting for the next scheduled retro). Route every
accepted finding through `/routing-update` so the version bump and changelog
entry land together — never hand-edit `model-routing.yaml`'s provider blocks
outside that loop, or the calibration history stops being trustworthy.

## 5. Add a brand-new provider

Adding a provider never touches `task_classes:` — that vocabulary is
provider-neutral by construction (`ARCHITECTURE.md` §1). Adding one means
filling in a new `providers.<name>` block and nothing else:

1. **Research the model lineup.** Using a connected research MCP (never from
   memory), find the provider's current lineup and assign one model id per
   tier: `cheap_fast`, `workhorse`, `frontier_reasoner`. If the provider ships
   fewer than three distinct capability points, tier-aliasing is legal — two
   tiers may resolve to the same model id (`ARCHITECTURE.md` §3).
2. **Research the effort/thinking dial.** Find the provider's native control
   name (`effort:.control`) and its value set. Determine what the dial
   actually governs — pure reasoning-depth ceiling, or something that also
   shapes agentic behavior/spend the way Anthropic's does — and write that
   distinction into the profile's comments so nobody assumes portability
   later.
3. **Fill `effort.map` per tier.** Map `light` / `standard` / `thorough` to
   native levels for each tier. `standard` is mandatory in every non-null map
   (the resolver falls back to it); `light` and `thorough` are optional. A
   tier whose model rejects the dial gets `{ light: null, standard: null,
   thorough: null }` — never a guessed value.
4. **Research and fill prices.** One `in`/`out` $/MTok cell per tier under
   `prices.<name>`, each stamped `as_of` and a `note` marking it
   EXAMPLE-verify-pricing, plus a `source:` URL.
5. **Fill escalation and degrade, or opt out explicitly.** Either supply
   `escalation.effort_ladder` + `escalation.model_ladder` (with any known dead
   rungs noted) and a `degrade.signals` / `ladder` / `floor` /
   `effort_on_degrade`, or write the literal string `escalation: none` /
   `degrade: none`. A missing block is a validation error — there is no
   silent default.
6. **Set `calibration:` honestly.** New profiles ship `status: unresearched`
   with `map: null` placeholders until step 1-4 are actually done. Flipping to
   `researched` without doing the research is the one thing the schema can't
   catch for you — the guard only checks that `unresearched` is never the
   *active* provider.
7. **Switch to it.** Set `active_provider: <name>` — see `docs/PROVIDERS.md`
   for the install-time and post-install switch paths — and run the drift
   guard (`claude/scripts/verify-routing.sh`) to confirm the new profile
   resolves every tier the task classes reference.

No code changes are required to add a provider: the resolver
(`claude/scripts/resolve_route.py`) and every consumer read the profile
generically. If you find yourself editing resolver code to support a new
provider, that's a sign the profile is missing a field the schema already
has room for — check `ARCHITECTURE.md` §3 again before adding one.
