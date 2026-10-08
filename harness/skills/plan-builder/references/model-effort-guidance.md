# Task classes, and when an override is justified (per session)

<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->

> **Schema v8 (route at dispatch).** The author no longer picks a model and effort.
> The author names the **task class**, the risks and the checks; `/plan-execute begin`
> resolves the model and effort from the class. The contract is
> `../../plan-execute/references/route-at-dispatch-contract.md` (it wins over this file).
> The sections before "Class definitions" are **history that
> explains the class defaults — the executor's input, not the author's choice.** Read it to
> understand why a class resolves where it does, or to justify an override. Do not copy a
> cell from it into a v8 session.

> **Canonical source:** `~/.claude/model-routing.yaml` is the AUTHORITY for
> task-class defaults, the escalation ladder, and per-agent pins. This document
> stays the prose playbook (model lineup, availability notes, live-vs-async
> framing) — it is not itself guard-checked, but its guidance must not
> contradict the SSOT; a divergence is a bug in THIS file, not the SSOT.
>
> **2026-09-22 (routing v27): the `Opus` token now serves Claude Opus 5.5** — $4/$20 per MTok,
> default effort `medium`, thinking always on, cyber + bio safety classifiers. Every `Opus` measurement
> in this file was taken on **Opus 5** and is kept as history. No pin moved: Anthropic reports Opus 5.5
> at `medium` ≥ Opus 5 at `high`, DeepSWE has no Opus 5.5 row yet, and our own sweep has not run on it.
> Source: `evals/routing/results/2026-09-22/ROUTING-v27-FINAL-CHANGESET.md`.

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
suspension/paywall** (2026-07) and the reactive auto-degrade to Opus 4.8 @ `xhigh`
— **both of those are SUPERSEDED, and are kept only as the record of what the
2026-07-01 review said**: the suspension note was retired as stale (see the callout
below) and the live degrade is Fable → **Opus 5 @ `high`** (SSOT
`degrade.effort_on_degrade`; `xhigh` is escalation-only on Opus since v1.20 and never a
degrade landing);
prior 06-10 revision added Fable 5 + the `client: codex` lane; **2026-07-01 MCP-verification
pass** added the official per-level effort use-cases, the `opusplan` plan/execute split +
4-question model routing, and the two-directional token economics — effort multiplies
output-rate tokens, so a stronger model at moderate effort can beat a weaker one at `max`
on both quality and cost — all re-verified against Anthropic's official effort doc / models
overview / Claude Code model-config).

> **Freshness.** Model names, defaults, and the effort ladder rev often. As of
> **2026-09-29**: **Opus 5.5** ($4/$20; the `Opus` token — Opus 5 held the slot
> from 2026-07-25 at $5/$25) is the standing default for ALL judgment work — deep-reasoning → `medium`, linchpin + hard
> agentic → `high`; **Sonnet** keeps standard build + fan-out shards (the `Sonnet` token serves
> **Sonnet 5.5** since 2026-09-28, same $2/$10; its effort levels are recalibrated, so every
> Sonnet calibration cell below is a **Sonnet 5 measurement**, SSOT v29); **Fable (5.1
> since v24) is the escalation apex only** (credit-metered; a Fable dispatch
> failure auto-degrades to Opus @ `high`). Opus's ladder STOPS at `high` (`xhigh`
> dead rung, `max` operator-elected). The **picker is ground truth**: verify the
> current lineup and its default effort at authoring time rather than trusting
> these labels verbatim.

---

## What the class defaults are (the executor's input, not the author's choice)

> **Default a standard session to `Sonnet · medium`; a HARD coding/agentic or
> integration session to `Opus · high`** (Opus 5.5 since 2026-09-22) (on our own calibration run, Opus·high
> dominates Sonnet·high on the hard-agentic distribution — Sonnet·high
> stays fine for LIGHTER integration work). Route architecture / novel /
> security-sensitive / hard-debug work to **`Opus · medium`** (dominates
> the retired Fable·low pin on both axes) and a genuine linchpin to
> **`Opus · high`**. Opus's ladder STOPS at `high`: `high`→`xhigh` is a DEAD
> RUNG (no measurable gain for materially higher cost) and `max` is weak
> (operator-elected only, marginal at best).
> **Fable (5.1) is the escalation apex, not a standing pick** — raise MODEL from
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

> **⚠ Availability (re-confirmed 2026-07-26).** Fable 5 is a **normal, available
> model** — the earlier "suspended 2026-06-12 / paywalled" note was STALE and had been
> quoted back at the operator as fact; `model-routing.yaml`'s `prices.fable` row now
> carries a standing warning never to assume it. Fable does draw **usage credits** on
> top of subscription, which is why it stays the escalation apex rather than a default
> — a cost posture, not an availability one. Re-check the SSOT before relying on this.
> Anthropic's own built-in fallback for Fable is
> **Opus**, and `/plan-execute` implements exactly that: a failed/refused Fable
> dispatch **auto-degrades to Opus @ `high`** (then Sonnet @ `high` — per-target
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
| Long-horizon agentic (many steps, self-correction, hours async) | **Opus 5.5 · high** (dominates fable + sonnet on the hard-agentic distribution, on our own calibration run) |
| Gate / verdict / adversarial-review sessions where judgment quality compounds | **Opus 5.5 · medium→high** |
| Quality-ceiling cases — the exact work was UNSOLVED in prior rounds/sessions | **Opus 5.5 · high → Fable 5.1 · low→medium** (escalation trigger (a): raise MODEL from Opus·high, then climb Fable one rung at a time) |
| Very-large-context sessions (whole-corpus reads / synthesis pushing past Opus limits) | **Fable 5.1 · medium→high** (1M ctx; escalation trigger (b) — the one shape that still reaches Fable directly) |
| Standard build | Sonnet · medium — unchanged |
| Light integration build | Sonnet · high (`xhigh` was a dead rung on Sonnet 5 — unmeasured on 5.5, so the stop at `high` is kept as policy) |
| Hard coding / agentic / integration build | **Opus 5.5 · high** (Sonnet's top rung dominated by Opus's, on our own calibration run) |
| Architecture / novel / security-sensitive / hard debug | **Opus 5.5 · medium** (dominates the retired Fable·low pin on both axes, on our own calibration run; escalate to `high` on the two named triggers) |

**Effort on Opus 5.** Strongly elastic **`low`→`medium`→`high`** on our own
calibration run — then the ladder STOPS: `high`→`xhigh` is a DEAD RUNG (no
measurable gain for materially higher cost) and `max` is weak (marginal at
best; operator-elected only, never routine). **Effort on Fable** is unchanged
in shape (elastic low→medium→high) but only exercised as the escalation apex —
the rungs are paid for by a trigger, never by default ($50/MTok output × deep
thinking is still the most expensive combo in the table).

## Codex-executed sessions (`model: gpt-5.6-*`)

<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
**Corrected 2026-07-28 (dual-harness S03, CL-01/CL-02).** There is no separate
`client: "codex"` field — that was an earlier, never-wired concept. The real
mechanism: set a session's `model` directly to a Codex token —
`gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna` — and `build_plan.py`
validates + renders it exactly like Haiku/Sonnet/Opus/Fable (no warning, a
dedicated chip color: slate "Codex," distinct from the four Claude colors).
`/plan-execute --harness codex` then dispatches that session through the Codex
CLI on the user's machine instead of the Cowork picker; the Cowork picker simply
does not apply to a Codex-pinned session.

**Codex rubric — the balanced map** (DeepSWE 2026-07-27,
`evals/routing/external-priors-gpt56-2026-07.md`; the same numbers back
`model-routing.yaml`'s `providers.openai`):

| Session kind | `model` | `reasoning` | Why |
|---|---|---|---|
| Mechanical (rename sweep, formatting, codemod, doc edits) | `gpt-5.6-luna` | `max` | $0.61, 67% DeepSWE — luna's ONLY usable rung; see the collapse warning below. |
| Standard build (CRUD, wiring, templated features, test scaffolds) | `gpt-5.6-luna` | `max` | $0.61, 67% DeepSWE — moved off terra at v1.14 (operator-elected on price: 3 points for 6.5×). `gpt-5.6-terra`/`max` ($3.96, 70%) is the escalation, not the default. |
| Integration / multi-file refactor / non-obvious debugging (agentic build) | `gpt-5.6-sol` | `xhigh` | $4.70, 71% DeepSWE — sol's proven escalation rung (matches `adversarial-review`'s own on-disk default). |
| Architecture, ambiguous tradeoffs, hard root-cause (deep reasoning) | `gpt-5.6-sol` | `xhigh` | Same cell as agentic build — the lane has no cheaper dedicated deep-reasoning rung. |
| Linchpin (one-shot irreversible, plan-foundational) | `gpt-5.6-sol` | `max` | $8.39, 73% DeepSWE — sol's ceiling (operator-approved 2026-08-13, SSOT v1.15 — see `model-routing.yaml` DECISION HISTORY); reserve for the rung that earns it. |

**⚠ Luna collapses below `max`.** `gpt-5.6-luna`'s DeepSWE accuracy falls off a
cliff as effort drops — `max` 67% → `high` 44% → `medium` 11%. Never pair
`gpt-5.6-luna` with any `reasoning` other than `max`; a "cheap" luna session run
at a lower tier isn't cheaper, it's broken.

Why the baseline shifted to Sonnet 5 (2026-07):

1. **Sonnet 5 closed the gap.** GA 2026-06-30, it lands near-Opus on coding/agentic
   at ~40% less cost and is the Claude Code default — the first Sonnet where Opus is
   genuinely optional for most build work. It absorbs most sessions that used to
   default to Opus.
2. **Escalate on kind, not reflex.** Route architecture / novel / security-sensitive
   design and hard multi-root-cause debugging to **Opus 5.5 · `medium`** (on our own
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

## Class definitions — what the author picks

The author picks one `task_class` per slice (a slice is what the code calls a session).
The class says what the work **is**. The executor turns it into a model and effort.

| Class | It means | Pick it when |
|---|---|---|
| `mechanical` | Rename, format, codemod, doc edit. No design call. | You could describe the whole change in one sentence and a diff would prove it. |
| `standard_build` | CRUD, wiring, a templated feature, a scaffold. The spec is clear and the change is bounded. | The scope is defined and the risk is low. |
| `agentic_build` | Multi-file or integration work, or debugging where the cause is not obvious. | The worker must explore, run things and decide as it goes. |
| `deep_reasoning` | Architecture, security-sensitive design, ambiguous trade-offs, hard root-cause. | The hard part is the judgment, not the typing. |
| `linchpin` | A one-shot, irreversible or plan-foundational call that the rest rests on. | A wrong answer is expensive to undo and nothing catches it later. |

## How to pick one

1. Match the slice's **dominant** activity, not its easiest sub-task. When two classes fit,
   take the higher one.
2. `mechanical` and `standard_build` follow from the description. `agentic_build` versus
   `deep_reasoning` versus `linchpin` is a real call: ask the user, do not infer it from prose.
3. Record the risks as `peer_triggers`. They are the risk flags that raise the override floor.
4. Put `touches` (paths, not prose) on every item, and author the checks: `verify` gates,
   evidence, and `verify.locked` for a check an earlier slice wrote that this one may not edit.
5. Ask whether the plan runs live or async. It sets `effort` (wall clock), which is separate
   from the model and its reasoning depth.

## When an override is justified

Leave `model` and `reasoning` out. Set them only as a **pair**, with a `why_model` that says
what the class default would get wrong here (a measured failure, a capability the default
lacks, a cost cap the user set). An override:

- is recorded as `pinned_override` in the ledger, even when it equals the class default;
- may not sit **below the risk floor**: a below-floor override is refused when the slice has
  any `peer_triggers` or is `linchpin`;
- is checked by the executor against the override's own provider ladder.

"I prefer Sonnet" is not a `why_model`. If the reason is only taste, pick the class instead.

## How the skill encodes this

- Each session carries a required `task_class`. `model` and `reasoning` are an optional pair
  that needs `why_model`.
- `reasoning` (`low|medium|high|xhigh|max`; `extra` is a synonym for `xhigh`) appears only on
  an override. The executor prints what it resolved in the `begin` receipt.
- Full field reference: `schemas.md` -> "Schema v8". Resolution, the floor and locked checks:
  the contract file named at the top.

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
