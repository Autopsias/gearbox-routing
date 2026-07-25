# Model & thinking-effort guidance (per session)

<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
> **Canonical source:** `~/.claude/model-routing.yaml` is the AUTHORITY for
> task-class defaults, the escalation ladder, and per-agent pins. This document
> stays the prose playbook (model lineup, availability notes, live-vs-async
> framing) — it is not itself guard-checked, but its guidance must not
> contradict the SSOT; a divergence is a bug in THIS file, not the SSOT.

This is the reference the skill uses to recommend, for **every session**, both a
Cowork **model** and a **thinking-effort** level. It is grounded in Anthropic's
own documentation and in advanced-user practice (sources at the bottom).
Last reviewed: **2026-07-25** (Opus 5 recalibration — Claude Opus 5 GA'd
2026-07-24 at Opus 4.8's exact price ($5/$25) and our own calibration run showed
TOTAL dominance inversion: every Fable rung beaten or matched-cheaper by an
Opus-5 rung on both accuracy and cost. What moved: **deep reasoning
Fable·low → Opus·`medium`**; **linchpin Fable·low → Opus·`high`**; **hard
agentic/integration Sonnet·high → Opus·`high`** (Sonnet's top rung was
dominated); **Opus's ladder now stops at `high`** (`high`→`xhigh` DEAD RUNG —
no measurable gain for materially higher cost; `max` weak — operator-elected
only); **Fable is escalation-apex only** (reach via raise-model from Opus·high
on the two named triggers); degrade Fable→Opus lands at **`high`** (was
xhigh). Full rationale documented in our own eval run.)
Prior review: **2026-07-03** (DeepSWE recalibration, same day as the SSOT
re-alignment below — the July-2026 DeepSWE frontier chart (113 long-horizon
tasks, 91 repos, behavioral verifiers) fired the pre-registered revisit
trigger. What moved: **deep reasoning re-pins Opus·high → Fable·low**
(Fable's low rung dominated Opus's high rung on our own calibration run;
Opus·high stays the alternative for very input-heavy deep-reads); **Opus is
mildly effort-elastic but DOMINATED** — every Opus rung is beaten by a Fable
rung at equal or lower $/task, so escalate MODEL Opus→Fable, never Opus's
effort (never-Opus+max survives on dominance grounds, not "overthinking");
**Sonnet's elasticity stops at `high`** — `high`→`xhigh` is a dead rung on
hard tasks (a small accuracy gain for materially higher cost); **Fable is
effort-ELASTIC on long-horizon hard tasks** — `low` stays the default, and
escalation climbs `low`→`medium`→`high` on the two named triggers. Full
rationale documented in our own eval run.)
Same-day prior (SSOT re-alignment — effort is a **per-model default +
justified escalation, not a ceiling**: linchpin default dropped to **Fable·low**;
coding/agentic default dropped `xhigh`→**`high`**; Fable escalation reserved for
(a) a problem unsolved in prior rounds or (b) whole-codebase/large-context
synthesis. Full rationale documented in our own eval run.)
Prior review: **2026-07-01** (added **Sonnet 5** — the broad workhorse, near-Opus on
coding/agentic at ~40% less, GA 2026-06-30, first Sonnet with `xhigh`; renamed the
effort-tier label `Extra`→`xhigh` to match the builder enum; recorded **Fable 5's
suspension/paywall** (2026-07) and the reactive auto-degrade to Opus 4.8 @ `xhigh`;
prior 06-10 revision added Fable 5 + the `client: codex` lane; **2026-07-01 MCP-verification
pass** added the official per-level effort use-cases, the `opusplan` plan/execute split +
4-question model routing, and the two-directional token economics — effort multiplies
output-rate tokens, so a stronger model at moderate effort can beat a weaker one at `max`
on both quality and cost — all re-verified against Anthropic's official effort doc / models
overview / Claude Code model-config).

> **Freshness.** Model names, defaults, and the effort ladder rev often. As of
> **2026-07-25**: **Opus 5** ($5/$25 — same price as 4.8) is the standing
> default for ALL judgment work — deep-reasoning → `medium`, linchpin + hard
> agentic → `high`; **Sonnet 5** keeps standard build + fan-out shards; **Fable 5
> is the escalation apex only** (credit-metered, paywalled; a Fable dispatch
> failure auto-degrades to Opus @ `high`). Opus's ladder STOPS at `high` (`xhigh`
> dead rung, `max` operator-elected). The **picker is ground truth**: verify the
> current lineup and its default effort at authoring time rather than trusting
> these labels verbatim.

---

## TL;DR — the house default

> **Default a standard session to `Sonnet 5 · medium`; a HARD coding/agentic or
> integration session to `Opus 5 · high`** (on our own calibration run, Opus·high
> dominates Sonnet·high on the hard-agentic distribution — Sonnet·high
> stays fine for LIGHTER integration work). Route architecture / novel /
> security-sensitive / hard-debug work to **`Opus 5 · medium`** (dominates
> the retired Fable·low pin on both axes) and a genuine linchpin to
> **`Opus 5 · high`**. Opus's ladder STOPS at `high`: `high`→`xhigh` is a DEAD
> RUNG (no measurable gain for materially higher cost) and `max` is weak
> (operator-elected only, marginal at best).
> **Fable 5 is the escalation apex, not a standing pick** — raise MODEL from
> Opus·high only when the two named triggers persist: a problem unsolved in prior
> rounds, or whole-codebase/large-context synthesis (1M ctx). De-escalate to
> **`Haiku 4.5 · low`** for mechanical batch work.

> **Refinement — effort is a per-model default + justified escalation, not a
> ceiling.** Effort tokens bill at the OUTPUT rate, so a hot *standing* default is
> waste on any model. Anthropic's "start coding/agentic at `xhigh`" guidance is
> the escalation's justification, not the house default: default
> coding/agentic to **`high`**; from there escalate MODEL, not effort (Sonnet's
> `high`→`xhigh` is a dead rung on our own calibration run) —
> **async** (paste-and-walk-away) sessions absorb a bigger model's latency freely,
> **live** (watched) sessions pay it every turn. See **"Live vs async"** below.


## Claude Fable 5 — the tier above Opus (added 2026-06-10)

**What it is.** First model of the Claude 5 family, Mythos-class — sits **above
Opus 4.8** in capability. GA on 2026-06-09. 1M-token context, adaptive thinking
only (the same Low–Max effort dials apply in the Cowork picker), priced at
**2× Opus 4.8** ($10/$50 vs $5/$25 per Mtok). Built for long-horizon agentic
work; benchmark deltas vs Opus 4.8 are largest exactly there (e.g. agentic-coding
evals where performance scales strongly with the effort dial).

> **⚠ Availability (2026-07).** Fable 5 was **suspended 2026-06-12** (export-control
> order) and **paywalled from 2026-06-23** (usage credits on top of subscription); it
> returns behind a paywall, no date. So Fable now draws **usage credits** and may be
> **intermittently unavailable**. Anthropic's own built-in fallback for Fable is
> **Opus**, and `/plan-execute` implements exactly that: a failed/refused Fable
> dispatch **auto-degrades to Opus @ `high`** (then Sonnet 5 @ `high` — per-target
> degrade efforts, recalibrated on our own calibration run: BOTH Opus-5 and
> Sonnet `xhigh` are dead rungs, so a degrade never lands on either; floor at
> Sonnet).

**Lane rule — Fable is the ESCALATION APEX only; no session KIND defaults
to it.** Opus 5 (same $5/$25 as 4.8) dominates every Fable rung on our own
calibration run, on both accuracy and cost. Fable's 2× price + credit metering
+ intermittent availability + a higher refusal-classifier rate than the rest
of the lineup mean it must be justified per session — and it is NEVER a
fan-out default:

| Session shape | Pick |
|---|---|
| Long-horizon agentic (many steps, self-correction, hours async) | **Opus 5 · high** (dominates fable + sonnet on the hard-agentic distribution, on our own calibration run) |
| Gate / verdict / adversarial-review sessions where judgment quality compounds | **Opus 5 · medium→high** |
| Quality-ceiling cases — the exact work was UNSOLVED in prior rounds/sessions | **Opus 5 · high → Fable 5 · low→medium** (escalation trigger (a): raise MODEL from Opus·high, then climb Fable one rung at a time) |
| Very-large-context sessions (whole-corpus reads / synthesis pushing past Opus limits) | **Fable 5 · medium→high** (1M ctx; escalation trigger (b) — the one shape that still reaches Fable directly) |
| Standard build | Sonnet 5 · medium — unchanged |
| Light integration build | Sonnet 5 · high (`xhigh` is a dead rung on our own calibration run) |
| Hard coding / agentic / integration build | **Opus 5 · high** (Sonnet's top rung dominated by Opus's, on our own calibration run) |
| Architecture / novel / security-sensitive / hard debug | **Opus 5 · medium** (dominates the retired Fable·low pin on both axes, on our own calibration run; escalate to `high` on the two named triggers) |

**Effort on Opus 5.** Strongly elastic **`low`→`medium`→`high`** on our own
calibration run — then the ladder STOPS: `high`→`xhigh` is a DEAD RUNG (no
measurable gain for materially higher cost) and `max` is weak (marginal at
best; operator-elected only, never routine). **Effort on Fable** is unchanged
in shape (elastic low→medium→high) but only exercised as the escalation apex —
the rungs are paid for by a trigger, never by default ($50/MTok output × deep
thinking is still the most expensive combo in the table).

## Codex-executed sessions (`client: codex`)

Sessions with `client: "codex"` run in the Codex CLI on the user's machine, not
in Cowork — the Cowork picker does NOT apply. Set `model` to a descriptive label
(e.g. "GPT-5 Codex"); `thinking` maps to the Codex reasoning-effort suggestion
(Medium for mechanical verification/install work, High for retrieval/eval work).
The card renders a CODEX badge and replaces the picker hint with a
run-in-Codex-CLI instruction automatically.

Why the baseline shifted to Sonnet 5 (2026-07):

1. **Sonnet 5 closed the gap.** GA 2026-06-30, it lands near-Opus on coding/agentic
   at ~40% less cost and is the Claude Code default — the first Sonnet where Opus is
   genuinely optional for most build work. It absorbs most sessions that used to
   default to Opus.
2. **Escalate on kind, not reflex.** Route architecture / novel / security-sensitive
   design and hard multi-root-cause debugging to **Opus 5 · `medium`** (on our own
   calibration run, it dominates the retired Fable·low pin on both axes; escalate
   to `high` on the two named triggers, then raise MODEL to Fable).
3. **`high` is Sonnet's ceiling for coding/agentic — and hard agentic work
   now routes to Opus 5.** Sonnet's elasticity is real only medium→high;
   `high`→`xhigh` is a dead rung. On the hard-agentic distribution Sonnet's top
   rung is dominated by Opus's — change MODEL, don't crank.
4. **Opus 5's ladder stops at `high` — then raise MODEL.** Opus 5 is strongly
   effort-elastic low→medium→high on our own calibration run, but `high`→`xhigh`
   is a dead rung (no measurable gain for materially higher cost) and `max` weak
   (marginal at best; operator-elected only). When Opus @ `high` isn't enough,
   that is the Fable signal (escalation apex), not the crank-Opus signal. The
   earlier "Opus is dominated" rule is RETIRED — Opus 5 now dominates Fable at
   every rung.

This is a *default*, not a rule. The whole point of putting model + effort on
each card is to vary them deliberately.

---

## The two dials

Cowork (as of 2026-05-28) gives you **two independent controls** next to each
other, set BEFORE you start a session:

- **Model** — *how capable* the engine is (Fable 5 ▸ Opus 5 ▸ Sonnet 5 ▸ Haiku 4.5).
- **Thinking effort** — *how much it deliberates* before/while answering.

They are orthogonal. "Opus 4.8 · Low" (a sharp model thinking briefly) and
"Sonnet 5 · xhigh" (a lighter model thinking hard) are both valid, different
trade-offs. Each session card recommends a **pair**.

### How thinking-effort actually works (adaptive thinking)

Opus 4.8 uses **adaptive thinking** as its *only* mode: the model itself decides
*whether* and *how much* to think on each turn. **Effort is soft guidance** on
that allocation — a behavioural signal, not a hard token budget. At `High`+ it
almost always thinks; at `Low`/`Medium` it may skip thinking on simple turns.
(Manual `budget_tokens` is gone on Opus 4.7/4.8 — effort is the control.)

### Token economics — effort multiplies output-rate tokens (and it cuts both ways)

Effort isn't free depth. It scales **every** token the session emits — text,
tool calls, *and* thinking — and all of them bill at the **output** rate. So the
two dials trade against each other in *cost*, not just quality:

- **Tune effort before switching model.** Anthropic's own guidance is that
  *tuning effort is often a better lever than switching models.* Escalating a
  cheaper model's effort is usually the cost-favourable first move — Sonnet 5 at
  `high`/`xhigh` reaches near-Opus quality well below Opus's price.
- **But the inversion is real.** Because effort multiplies output-rate tokens, a
  **stronger model at *lower* effort** — one that reaches the answer with fewer
  tool calls, less preamble, and less thinking — can be **both better and
  cheaper** than a weaker model cranked to `xhigh`/`max`. Anthropic's Fable note
  makes the same point: its *lower* effort *"often exceeds `xhigh` performance on
  prior models."*
- **The honest rule.** Tune effort first; when the reasoning depth genuinely
  exceeds the cheaper model's ceiling, a stronger model at **moderate** effort
  usually beats the cheaper model at **max** on quality *and* cost. The path
  Anthropic itself recommends for a capable model is **start capable, then
  economize by lowering effort** — not start cheap and crank to max.

---

## The effort ladder (Cowork picker labels)

We display the **Cowork picker labels**. Equivalents in the API / Claude Code
are noted for cross-reference. **Effort applies to *all* the tokens a session
emits — text, tool calls, *and* thinking — and every one bills at the output
rate, so a higher tier is a real cost multiplier, not a free quality knob.**

| Cowork label | API / Code token | What it does | Reach for it when… (official use-cases) |
|---|---|---|---|
| **Low** | `low` | Minimises thinking; skips it on easy turns. Fastest, cheapest, lightest on rate limits. | **Subagents**, simple / well-defined tasks, high-volume or latency-sensitive work, plain chat: renames, formatting, simple lookups/classification, mechanical batch edits. Pair with an explicit checklist if the task has several parts. |
| **Medium** | `medium` | Moderate thinking; good results at lower cost. | **Balanced agentic work** — the cost-efficient drop-in for clearly-scoped everyday tasks (Sonnet 5 @ `medium` ≈ Sonnet 4.6 @ `high`). |
| **High** ⭐ | `high` (the model default) | Always thinks; deep reasoning. Best balance of quality and tokens. | **The default** — complex reasoning, difficult coding, and agentic work; most build, integration, analysis, and writing sessions. |
| **xhigh** (older pickers: "Extra") | `xhigh` | Always thinks *deeply* with extended exploration; meaningfully more tokens than High. | **The justified escalation for hard coding & agentic work** (Anthropic's doc calls it the coding/agentic starting point; the house standing default is `high` — effort bills at the output rate): genuinely hard long-running (30 min+) async sessions, repeated tool-calling, deep web/KB search, multi-file refactors, big migrations. Effectively a **Sonnet** lever — near-dead on Opus, and on Fable prefer `low` unless a prior round failed. Offered on **Fable 5 / Opus 4.8 / Opus 4.7 / Sonnet 5 only**. |
| **Max** | `max` | Maximum depth, no constraints. Slowest, most expensive. | **Genuinely frontier problems only**: gnarly multi-root-cause debugging, novel algorithmic design, high-stakes one-shot decisions. Anthropic's own wording — *significant cost for small gains; can overthink structured output.* **Never pair with Opus** (if `xhigh` isn't enough, that's the switch-to-a-stronger-model signal); **not offered on Haiku.** |

Rule of thumb: **`high` is the coding/agentic default and `xhigh` the
justified escalation** (genuinely hard multi-file/agentic work — where Sonnet's
elasticity converts effort to accuracy — or long async runs where the latency is
free); **Medium for bounded single-pass work; Max only for genuinely frontier /
irreversible calls** where you'd accept real extra cost and latency to be right.
Don't default to Max — on structured or less intelligence-sensitive tasks it can
*overthink* — and never pair it with Opus. And remember the per-model shape:
the dial is a live lever on Sonnet, near-dead on Opus, and inverted on Fable
(low default, high tail).

---

## Choosing the model

| Model | Use it for | Notes |
|---|---|---|
| **Sonnet 5** ⭐ | The broad workhorse and Claude Code default. Standard build, integration, multi-file refactor, non-obvious debugging, high-volume work. Near-Opus on coding/agentic. | GA 2026-06-30. First Sonnet with `xhigh`. $3/$15 per MTok (intro $2/$10 through 2026-08-31) — ~40% cheaper than Opus. Now absorbs most work that used to escalate to Opus. Pair with `medium` for defined scope, `high`/`xhigh` for integration/debugging. |
| **Opus 4.8** | The input-heavy alternative + degrade target: very read-dominated deep-reads (in-rate $5 vs Fable's $10/MTok), polished docs/slides; the auto-degrade landing zone when Fable is paywalled/refused. | Shipped 2026-05-28. $5/$25 per MTok. 1M context. Sharper judgment; markedly less likely than 4.7 to leave code flaws unflagged. Default **`high`**; mildly effort-elastic but **DOMINATED** (on our own calibration run: every Opus rung is beaten by a Fable rung at equal or lower $/task) — **never pair with `max`** (dominance, not overthinking: `max` IS Opus's peak, yet Fable·high matches at roughly the same $/task with a clear accuracy edge, and Fable·low matches at a fraction of the cost); when `high` isn't enough, that's the Fable signal, not a crank-Opus signal. |
| **Fable 5** | The linchpin tier: genuinely hard cross-cutting / irreversible work, gate/verdict sessions where judgment compounds, whole-corpus (1M-ctx) reads. | $10/$50 per MTok **+ usage credits; suspended/paywalled as of 2026-07**. **Default effort `low`** (Mythos-class — low already exceeds Opus @ `xhigh`); `high`/`xhigh` only for unsolved-prior-rounds or large-context synthesis. A failed Fable dispatch **auto-degrades to Opus 4.8 @ `xhigh`**. Reserve for a linchpin that earns the premium. |
| **Haiku 4.5** | Truly trivial, high-throughput sessions: bulk formatting, simple transforms, light classification. | Cheapest/fastest. **Rejects the `effort`/reasoning parameter entirely** — no thinking dial; keep it at Low. Rare as a whole-session choice in a plan. |

### Which model — the 4-question test + `opusplan` plan/execute

Anthropic's model-selection playbook routes by the work's *nature*, not its
apparent difficulty:

1. **Extended multi-step reasoning, deep code analysis, or nuanced judgment on
   ambiguous inputs?** → **Fable 5 · `low`** (deep-reasoning default; **Opus
   4.8 · `high`** for very input-heavy deep-reads, and as the auto-degrade target).
2. **Instruction-following, structured output, tool use, or RAG?** → **Sonnet 5**
   — the default, and roughly 70% of real work.
3. **Trivial / high-volume / latency-sensitive?** → **Haiku 4.5** (or Sonnet 5 at
   `low`).
4. **Genuinely frontier / cross-cutting / irreversible linchpin?** → **Fable 5**
   (auto-degrades to Opus 4.8 @ `xhigh` when paywalled/unavailable).

**Plan/execute is Anthropic's own pattern.** Claude Code's `opusplan` alias
*"uses `opus` during plan mode, then switches to `sonnet` for execution."* Read
that across to a multi-session plan: **design / architecture / adjudication /
synthesis sessions → Opus 4.8 (or a Fable linchpin); build / implementation /
wiring sessions → Sonnet 5.** Route each session by what it *does*, and
re-validate any Opus routing against your own evals — Sonnet 5 absorbs most build
work now. (The Fable→Opus degrade mirrors the `best` alias — *"Fable 5 where
available, otherwise the latest Opus."*)

---

## Decision framework — session archetype → recommended pair

Match the session's *dominant* activity, not the easiest sub-task in it.

| Session archetype | Model | Effort | Rationale to write in `why_model` |
|---|---|---|---|
| Architecture / design decision, irreversible trade-offs | Fable 5 (→ Opus 4.8 @ xhigh degrade; Opus·high for very input-heavy deep-reads) | **low** | On our own calibration run, Fable·low dominates Opus·high on DeepSWE; every Opus rung is dominated — never `max` on Opus, and escalate MODEL (→Fable) rather than Opus's effort. |
| Deep research / multi-source synthesis / heavy tool-calling | Sonnet 5 (Opus 4.8 if novel) | **high**→xhigh | Exploratory + agentic — `high` default, `xhigh` when genuinely hard (Sonnet's elasticity makes it a live lever). |
| Long-running async build, large multi-file refactor, codebase migration | Sonnet 5 | **high**→xhigh | 30 min+ sustained work with many steps — Sonnet 5 absorbs most of this; async runs take `xhigh` freely when the work earns it. Escalate to **Fable 5 · low** for long-horizon/monorepo/stuck agentic loops. |
| Gnarly debugging (multiple possible root causes) | Opus 4.8 | **high** | Deep reasoning to isolate the cause — Opus @ `high`; if stuck, escalate to Fable, not to `xhigh`/`max`. |
| Coding / agentic build, heavy tool-calling (async) | Sonnet 5 | **high** | `high` is the house default for coding/agentic; step up to `xhigh` for the genuinely hard tail — async makes its latency free. |
| Standard build / integration — **bounded, single-pass, not agentic** | Sonnet 5 | **medium**→high ⭐ | Defined scope; Sonnet 5 at medium is the workhorse, high when judgement rises. |
| Analysis, drafting, structured docs/slides/spreadsheets | Opus 4.8 | **high** | Quality matters; avoid Max here (overthinking risk on structured output). |
| The plan's linchpin — cross-cutting / irreversible / synthesis the rest rests on | Fable 5 (→ Opus 4.8 @ xhigh if unavailable) | **low** | Mythos-class judgment at Fable's cheapest point — low already exceeds Opus @ `xhigh`; Fable is effort-elastic on long-horizon hard work, so climb `low`→`medium`→`high` only for an exceptionally hard or context-heavy one-shot call. |
| Well-scoped refactor / routine CRUD with a clear spec | Sonnet 5 | **medium** | Mechanical enough to trade some depth for cost/speed. |
| Bulk rename / formatting / simple transforms / classification | Sonnet 5 or Haiku 4.5 | **low** | Trivial, high-volume — optimise for speed and rate limits. Haiku ignores the effort dial. |

When two archetypes fit, **default up, not down**: a session that's "mostly
mechanical but with one design call" should take the higher pair. The one
exception is the `xhigh`-vs-High choice on coding/agentic sessions — there, let
**live-vs-async** decide (see below), not difficulty alone.

### Cost / latency caveats (call these out when relevant)

- Higher effort can exhaust the output budget and run longer — fine for async
  sessions, annoying if you're watching it live.
- **Max can over-think** structured or less intelligence-sensitive tasks
  (tables, fills, format conversions). Prefer high/`xhigh` there.
- Higher effort uses rate limits faster. For a long plan, reserve `xhigh`/max for
  the sessions that earn it.

### Live vs async — the lever that actually decides `xhigh`-vs-High

The single most useful question before pairing efforts: **will the user watch the
session run, or fire it and walk away?** Over high, `xhigh` buys ~2–3×
latency and rate-limit burn for a small — often zero — quality gain; the
token-dollar difference is minor. The cost you actually pay is *time and limits*,
and that only hurts when someone's waiting.

- **Async** (paste the prompt, come back later): step up to `xhigh` freely *when
  the work is genuinely hard* — async is where `xhigh`'s latency cost disappears
  (Anthropic's "start coding/agentic at `xhigh`" applies here; the house standing
  default stays `high` per the SSOT). The right lane for hard "~1 day" sessions.
- **Live** (watching it work, iterating turn-by-turn): default most sessions to
  **high** — the model still thinks deeply, you just don't pay `xhigh`'s latency on
  every turn. Step a specific session up to `xhigh` only when it clearly needs the depth.

Ask once during the interview, let the answer set the baseline, override per
session. A plan that's all-`xhigh` "because the work is hard" usually just means
nobody asked whether it runs live.

---

## How the skill encodes this

- Each session carries a `reasoning` field (`low|medium|high|xhigh|max`; `extra`
  is an accepted synonym for `xhigh`). It renders as a colour-coded chip beside
  the model chip and the time-estimate chip.
- `why_model` should justify **both** dials in one sentence — it renders as
  "Why Opus 4.8 · xhigh effort: …".
- Each card shows a **picker hint** ("set Cowork picker → Opus 4.8 · High") and
  the session protocol reminds the user that model + effort are *picker settings
  set before pasting the prompt*, not edits to the HTML file.
- During the interview, **propose a pair for every session** using the framework
  above; let the user override. Don't leave `thinking` blank on a real plan.

---

## Sources (reviewed 2026-05-29; official docs re-verified via MCP 2026-07-01)

Official (re-verified 2026-07-01 via Ref + Exa MCP):

- Anthropic — *Effort* (Claude API docs): per-level use-cases (**low** = subagents / simple / high-volume / latency-sensitive / chat · **medium** = balanced agentic · **high** = default: complex reasoning / difficult coding / agentic · **xhigh** = the coding/agentic start, offered on Fable 5 / Opus 4.8 / Opus 4.7 / Sonnet 5 only · **max** = genuinely frontier problems, *"significant cost for small gains,"* can *overthink* structured output); effort applies to **all** tokens (text + tool calls + thinking) billed at the output rate; **Haiku excluded** from `effort`; Fable's lower effort *"often exceeds `xhigh` performance on prior models."* <https://platform.claude.com/docs/en/build-with-claude/effort>
- Anthropic — *Models overview / Choosing a model* (Claude API docs): the 4-question routing test (Opus 4.8 for extended multi-step reasoning / deep code analysis / nuanced judgment on ambiguous inputs; Sonnet 5 for instruction-following / structured output / tool use / RAG); *"tuning effort is often a better lever than switching models"*; positioning + pricing. <https://platform.claude.com/docs/en/about-claude/models/overview>
- Anthropic — *Claude Code model configuration*: the **`opusplan`** alias (*"uses `opus` during plan mode, then switches to `sonnet` for execution"*) and the **`best`** alias (*"Fable 5 where available, otherwise the latest Opus"*) — the native plan/execute-by-model and Fable→Opus fallback patterns. <https://docs.claude.com/en/docs/claude-code/model-config>
- Practitioner guides — ClaudeKit · claude-platform-playbook · MarkTechPost: effort-vs-model economics; *Sonnet 5 narrows the Opus gap and can match Opus 4.8 on some tasks at higher effort, at ~1.7× lower cost.*

Prior review (2026-05-29):
- Anthropic — *Adaptive thinking* (Claude API docs): adaptive-only on Opus 4.7/4.8, effort as soft guidance, `max_tokens` interaction. <https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking>
- Anthropic — *Claude Opus 4.8* (product page): positioning, High default, 1M context, pricing, recommended use cases. <https://www.anthropic.com/claude/opus>
- Anthropic — *Introducing Claude Opus 4.7*: origin of the `xhigh` level and "start at high/xhigh for coding/agentic". <https://www.anthropic.com/news/claude-opus-4-7>
- 9to5Mac, *Anthropic upgrades Claude with Opus 4.8* (2026-05-28): Cowork/claude.ai Effort Control launch; "Extra" = `xhigh`; High default; raised rate limits. <https://9to5mac.com/2026/05/28/anthropic-upgrades-claude-with-opus-4-8-heres-whats-new/>
- Business Standard / BeInCrypto / 9to5Mac (2026-05-28/29): Opus 4.8 benchmark deltas (SWE-Bench Pro 64.3→69.2, HLE 54.7→57.9, GDPval-AA 1753→1890), Fast Mode ~2.5× faster, "4× less likely to leave code flaws unflagged".
- Advanced-user practice — MindStudio, *Claude Code Effort Levels Explained* (2026-03): Low/Medium/High/Max task mapping; Medium as the high-volume coding default. <https://www.mindstudio.ai/blog/claude-code-effort-levels-explained/>
- Advanced-user practice — *ultrathink / thinking modes* handbook: effort↔keyword mapping and the "5+ files / security / architecture → max" decision rule. <https://github.com/ThamJiaHe/claude-code-handbook/blob/main/docs/ultrathink-thinking-modes.md>
