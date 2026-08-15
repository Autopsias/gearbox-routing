# Phase 4.0 — Model-selection sanity lint: full rule set

Read this from plan-harden.md's §4.0 (the stamp, data-source order, and skip condition
stay inline there — this file is only the flag rules themselves). Data source: read
task-class defaults from `~/.claude/model-routing.yaml` (`task_classes:` block) when it
exists and parses; fall back to `~/.claude/skills/plan-builder/references/schemas.md`
→ "Model + reasoning rubric" only if the SSOT is missing/unparseable.

Flag each session that hits:

- **`Opus` + `max`** — `max` is a dead/weak rung on Opus (v1.11 calibration: little to no quality gain over `high`, ~2× the cost; the SSOT's own escalation invariants forbid emitting it as an automatic rung — `"NEVER emit opus + max as an automatic rung"`, operator opt-in only). **The old "dominated by Fable" premise this rule used to cite is RETIRED (v1.11) and now runs the other way**: Opus is the standing default for judgment work and DOMINATES Fable at every calibrated rung — don't recommend switching TO Fable as a cost/quality move, that direction no longer holds. → recommend dropping to `Opus`/`high` (the real ceiling — never crank past it automatically). Only escalate the MODEL to `Fable` after the documented trigger (2 failures at the same root cause) or for whole-codebase/large-context synthesis — that lands at `Fable`/`low` (Fable's escalation-apex entry rung, not a standing default for anything). 🟡
- **`Fable` at `high`/`xhigh` without an escalation justification** — Fable's default is `low` (its low-effort reasoning is the value point); `medium`→`high` are ESCALATION rungs for a problem unsolved in prior rounds or whole-codebase/large-context synthesis. **Fable is a normal, available model** (operator-confirmed 2026-07-26 — the earlier "suspended/paywalled" note was stale and had itself been quoted back at the operator as fact; never assume that note without re-checking `model-routing.yaml`'s `prices.fable` line), but it bills at Mythos-class rates ($10/$50), so a rung above `low` still needs a reason. A hot-Fable session whose `why_model` doesn't name one of the two triggers should be `Fable`/`low`. Also confirm the plan tolerates the auto-degrade **Fable→Opus @ `high`** on a dispatch failure (per-target degrade effort, not `xhigh` — Opus's `xhigh` rung is dead; it's reactive, not proactive). 🟡
- **`opusplan` kind↔model mismatch** — Anthropic's `opusplan` routes *plan mode → Opus, execution → Sonnet*; apply the same split to session KIND. Flag both directions from the session's title/deliverable signals: a **build / implementation / wiring** session (signals CRUD, wiring, scaffolding, codemod, migration mechanics, test-writing — the `standard_build` task class) assigned to **`Opus`/`Fable`** is likely over-modeled → recommend `Sonnet` (the `standard_build` default; note `agentic_build` — integration/multi-file refactor/non-obvious debugging — is its OWN class and correctly resolves to `Opus`/`high`, not Sonnet, since v1.11; don't flag that as over-modeled). A **design / architecture / adjudication / synthesis** session (signals design, architecture, decide, adjudicate, tradeoff, schema design, or "the rest of the plan rests on it") assigned to **`Sonnet`** is likely under-modeled for a plan-mode decision → recommend `Opus`/`medium` (the `deep_reasoning` default) or `Opus`/`high` (the `linchpin` default) — escalate to `Fable`/`low` only if the session's own `why_model` names the stuck/large-context trigger. Skip when `why_model` already justifies the deliberate off-split choice. 🟡
- **A hard session left at `reasoning: medium`** — a session whose title/deliverable signals architecture / security-sensitive / ambiguous-tradeoff / hard-root-cause work but sits at `medium` is under-dialed **only when it is declared `Sonnet`** (Sonnet's live ceiling is `high` — `xhigh` is a dead rung on Sonnet — so `medium` is genuinely low for that signal set). → recommend `Sonnet`/`high`, or route the MODEL up to `Opus`/`medium` (the `deep_reasoning` default — judgment work's standing tier, not Fable) for the harder cases; escalate further to `Opus`/`high` or `Fable`/`low` only if still stuck after that. **Do not flag an `Opus`/`medium` session on this signal set** — that IS the correctly-modeled `deep_reasoning` baseline, not an under-dial. 🟡
- **`reasoning` blank on a real (non-mechanical) session** — under-specified; the runner prepends no thinking directive. → recommend an explicit tier. 🟣

**Three rules added s04 (SKL-02) — read the SSOT `agents:` / `codex_peer` blocks, not just `task_classes:`:**

- **`pin-conflict`** — a session's `dispatch.subagent_type` names a **specialist agent** (a row in SSOT `agents:`, e.g. `digdeep`, `safe-refactor`, `security-scanner` — not a generic type like `null`/`general-purpose`/`Explore`/`Plan`/`fork`), AND that agent's `.md` frontmatter `model:` (or the SSOT's pinned `model:` for that agent, if the `.md` can't be read) **differs** from the session's own `model` field. Per the documented resolution order, the **per-invocation Task `model` param silently wins** at dispatch — the session's declared `model` is discarded with no error, so this is a silent divergence a plan author is unlikely to have intended. → recommend either changing the session `model` to match the pinned specialist, or dropping `subagent_type` to `null` if a fresh general-purpose agent at the session's own `model` was actually intended. 🟡 Deterministic (structured fields: `dispatch.subagent_type` + `model`) — reliable, not a prose heuristic.
- **`peer-gate-missing` (STRUCTURED, 🔴 plan-killer)** — the session carries a non-empty `peer_triggers` array in the manifest (structured declaration of `architecture_decision` / `irreversible_change` / `security_sensitive`), AND has no `verify.gates` entry named `adversarial-review` (nor `/adversarial-review` in its `prompt`/`agent_instructions`). → **🔴 plan-killer** (the ONE model-lint flag that can block — see §4.1 carve-out). Deterministic, not a heuristic: the author DECLARED the trigger, so a missing gate is a real omission. NOTE: `build_plan.py::validate_peer_triggers` already raises at BUILD time on this exact condition, so a freshly-built plan cannot reach harden while violating it — this lint is the backstop for hand-edited manifests. Quoted-evidence for the 🔴 is the `peer_triggers` value itself (`manifest.json` → session `.peer_triggers`).
- **`peer-trigger-undeclared` (keyword net, 🟡 advisory)** — the session's title/`human_summary`/`deliverable` text matches a keyword from SSOT `codex_peer.lint_keywords` for a trigger, AND `peer_triggers` is empty/absent. → recommend either declaring `peer_triggers` (which then enforces the gate structurally) or noting in prose why the trigger doesn't apply. 🟡 **Advisory-only, prose-heuristic** — false positives/negatives expected; it never blocks. This is the soft net that PROMPTS the author toward the structured field. (Structured promotion landed 2026-07-10 by operator direction, ahead of the documented "first observed peer-gate miss" auto-trigger; the keyword rule is retained as the discovery aid, not the enforcement.)
- **`specialist-exists-but-null-subagent`** — the session's `(model, reasoning)` pair **exactly matches** a pinned specialist agent's `(model, effort)` row in SSOT `agents:` (excluding the epic_* rows, which are a different dispatch surface), AND the session's title/`deliverable` contains a keyword drawn from that agent's own name (split on `-`, dropping generic suffixes like `-fixer`/`-analyzer`/`-generator`/`-manager`/`-refactor` — e.g. `safe-refactor` → "refactor"; `security-scanner` → "security"), AND `dispatch.subagent_type` is `null`/omitted. A fresh `general-purpose` agent at that model/effort does the work WITHOUT the specialist's tool scoping or system prompt. → recommend setting `subagent_type` to the matching specialist explicitly, or leave a `why_model`/dispatch note confirming a fresh general-purpose agent was the deliberate choice. 🟡 Prose-heuristic on the keyword match, deterministic on the `(model, effort)` comparison.

**One rule added s07 (DSP-04) — task_class as a ROUTING signal, the complement to
`peer-gate-missing` (that gate is about REVIEW; this one is about MODEL CHOICE):**

- **`task-class-model-mismatch`** — for every session carrying a non-empty `task_class`
  (`mechanical|standard_build|agentic_build|deep_reasoning|linchpin`), resolve what it
  SHOULD dispatch as and compare against what it DECLARES (`model`/`reasoning`).
  Sessions with `task_class` unset/absent are skipped entirely — no error, no flag
  (missing-field semantics: a session without `task_class` falls back to its explicit
  `model` field, which the OTHER model-lint rules above already cover).
  1. **Resolve the session's EFFECTIVE executor provider first** — never the raw
     `active_provider` dial. Under `active_provider: anthropic` the effective provider
     is always `anthropic`. Under `active_provider: openai` (codex-focused), the
     effective provider is `openai` ONLY if `task_class` is a member of
     `executor_policy.executor_for` AND the session is not barred
     (`task_class == linchpin`, or `peer_triggers` contains `irreversible_change`, or
     `dispatch.guards_irreversible` is set) — otherwise it FAILS CLOSED to `anthropic`
     (a policy-compliant Claude fallback, not a contradiction). Compute this exactly
     the way `run.py` dispatch does — reuse its own functions rather than
     reimplementing the parse:
     ```
     python3 -c "
     import sys
     sys.path.insert(0, '$HOME/.claude/skills/plan-execute/scripts')
     import run
     ssot = open('$HOME/.claude/model-routing.yaml').read()
     print(sorted(run._executor_for(ssot)))
     print(run._session_barred({'task_class': '<tc>', 'peer_triggers': [...], 'dispatch': {...}}))
     "
     ```
  2. **Resolve the expected `{model_id, native_effort}`** for `(task_class,
     effective_provider)` via the shared resolver — never hand-parse the SSOT tiers:
     ```
     python3 -c "
     import sys
     sys.path.insert(0, '$HOME/.claude/scripts')
     import resolve_route
     print(resolve_route.resolve('<task_class>', '<effective_provider>'))
     "
     ```
  3. **Compare.** Session `model` (case-insensitive family name — `Sonnet`/`Fable`/
     `Opus`/`Haiku`, or the Codex short names under an `openai` effective provider)
     against `resolved.model_id`. A mismatch → 🟡 (never 🔴 — model choice is a steer,
     not a hard gate; only `peer-gate-missing` blocks). Quote the resolved tuple and
     the session's declared value in the recommendation, e.g. *"session S07 declares
     `task_class: deep_reasoning` + `model: Haiku`, which resolves to `Opus`/`medium`
     under `active_provider: anthropic` — recommend `Opus`/`medium`, or correct
     `task_class` if `Haiku` was the deliberate choice and `why_model` should say so."*
     `reasoning`/`native_effort` mismatches ride the SAME flag as a secondary note
     rather than a second flag (one flag per session per rule).
  4. **Never flag a policy-compliant fallback as a contradiction.** Because step 1
     resolves the EFFECTIVE provider (not the raw dial) before step 2 resolves the
     model, a `linchpin`/`irreversible_change`/non-opted-in session that correctly
     executes on Claude under `active_provider: openai` compares against the
     `anthropic` resolution — it is never flagged for "not using Codex." Verify this
     specific case (barred session, `active_provider: openai`) when spot-checking the
     lint on a plan that touches `executor_policy`.

**Three rules added s08 (LN-01) — Codex-lane traps. All three read `providers.openai`
via the SAME calls the s07 rule above already uses (`resolve_route.resolve(...,
'openai')`, `run._session_barred`, `run._CODEX_DECLARED_RE`) — never a hand-rolled
second parse of the per-provider map. A session's `model` counts as an EXPLICIT Codex
pin exactly when `run._CODEX_DECLARED_RE` matches it (`re.search(r"gpt|codex", model,
re.I)`), the same test `run.py` itself uses:**

- **`luna-below-max`** — a session whose declared model is `gpt-5.6-luna` (the
  `cheap_fast` tier under `providers.openai`) with a declared `reasoning`/native effort
  other than `max`. Luna's quality curve COLLAPSES below its ceiling — measured DeepSWE:
  `max` 67% → `high` 44% → `medium` 11% (`providers.openai.calibration.research_ref`) —
  which is exactly why `providers.openai.effort.map.cheap_fast` and the per-provider
  `task_classes.mechanical` row both route EVERY intent to `max` for this tier; there is
  no lighter rung worth taking. A luna session below `max` is not a cost-saving choice,
  it is a quality collapse. → recommend `gpt-5.6-luna`/`max`, or move the session off
  luna entirely (`gpt-5.6-terra`/`max`, the `frontier_reasoner` tier) if the task doesn't actually fit
  `mechanical`. 🟡
- **`codex-task-class-mismatch`** — the s07 `task-class-model-mismatch` rule above,
  run with `effective_provider` forced to `openai`, for any session whose declared
  `model` is an explicit Codex pin (regardless of `active_provider` — a session can name
  a Codex model directly even while the plan's dial sits on `anthropic`). Resolve via
  `resolve_route.resolve(task_class, 'openai')` (which already layers
  `providers.openai.task_classes` — the v1.13 per-provider override — on top of the
  neutral rows; do not re-derive the map by hand) and compare against the session's
  declared `(model, reasoning)`. This is the SAME comparison machinery as s07's rule,
  applied with `openai` as the resolved provider rather than a separate code path — one
  flag per session, quoted the same way (*"session declares `task_class:
  agentic_build` + `model: gpt-5.6-terra`, which resolves to `gpt-5.6-sol`/`xhigh` under
  `openai` — recommend `gpt-5.6-sol`/`xhigh`, or correct `task_class`"*). 🟡
- **`linchpin-pinned-to-codex`** — mirrors `run.py::_codex_harness_spec`'s own gate
  exactly (dual-harness-contract.md D4c/D4d), computed via the SAME functions, never a
  reimplementation: a session for which `run._session_barred(session)` returns non-`None`
  (`task_class == linchpin`, or `peer_triggers` contains `irreversible_change`, or
  `dispatch.guards_irreversible` is set) **AND** whose declared `model` is an explicit
  Codex pin. `run.py`'s actual contract for exactly this combination, on BOTH harnesses:
  it raises `UnroutableCodexSession` and the session is marked BLOCKED — an explicit
  Codex pin on work declared unsafe for unsupervised Codex execution is the plan
  contradicting itself, and only the plan author can resolve it. (Barred **without** an
  explicit Codex pin is NOT a lint flag — that's the normal, supported path: checkpoint-
  forced under `--harness codex` per D4c, unaffected under the Claude harness.) →
  recommend removing the Codex pin (falls back to Claude — `Opus`/`high` for `linchpin`),
  or, only if the bar genuinely no longer applies, dropping the `task_class`/
  `peer_triggers`/`guards_irreversible` declaration that triggers it — never routinely,
  that bar exists precisely for irreversible work. **Where this lint's read of the
  situation and `run.py`'s actual behavior would ever disagree, `run.py` wins and the
  lint gets fixed** — this rule is a restatement of its gate, not an independent policy.
  Kept at 🟡 despite `run.py` hard-blocking the same combination: model-lint's one
  structural blocker stays `peer-gate-missing` — this flag informs the author before
  they hit the `run.py` block, it does not duplicate it as a second gate. 🟡

**Three non-model rules (they ride the same lint pass and MODEL_LINT list):**

- **`decision-debt`** *(2026-08-01, wayfinder-derived)* — an unmade decision is embedded in a build session's prompt instead of being resolved before it; the guess surfaces weeks later as a BLOCKED session or a silent deviation. Two nets, at most one flag per session:
  1. **Structured net:** `manifest.json` carries a top-level `open_questions` list (the fog register plan-builder writes). For each entry, find any session whose `prompt`/`deliverable` consumes its answer (same subject matter). If a consumer exists and no session upstream of it (via `depends_on` ancestry) is a **decision session** resolving that question — title/deliverable names the decision, or the prompt is research/prototype/grilling-shaped — flag the consumer: the plan schedules building on an answer nobody is scheduled to produce. Quote the `open_questions` entry as evidence.
  2. **Prose net:** a session `prompt` containing unresolved-decision language — "TBD", "to be decided", "decide later", "open question", "choose between", "we'll figure out" — with no upstream decision session covering it. Prose-heuristic; false positives expected and acceptable.

  → recommend one of: resolve the decision now and edit the prompt; split out an explicit decision session that `depends_on`-blocks the consumer (with a human checkpoint when it's a genuine judgment call — auto-accept critiques decisions, it doesn't make them); or move it to `open_questions` with what would sharpen it, if genuinely not statable yet. 🟡 — never blocks. A deliberately-deferred decision the author already annotated (the prompt marks it **RECON NEEDED** with the settling check, per the wargame contract) is not debt — skip it.

- **`acceptance-review-missing`** — the plan has **≥4 sessions** and **no** session carries `acceptance_review: true` in `manifest.json`. Session `verify` gates validate each part; without this session nothing validates the whole, so a plan whose every session closes `DONE` can still miss its objectives via accumulated `deviations` and quietly `DEFERRED` items. → recommend adding a closing acceptance-review session (fresh isolated agent, `depends_on` every terminal session, `task_class: deep_reasoning`, `require_evidence: true`, plan acceptance criteria verbatim in its prompt) per `~/.claude/skills/plan-builder/references/schemas.md` → "Closing acceptance review". 🟡 Deterministic (a structured manifest field + a session count), but never a 🔴 — a short or purely-exploratory plan can legitimately have nothing plan-level to validate; say so in prose rather than adding a hollow gate. Skip on plans of <4 sessions. Also flag 🟡 when a session DOES carry the marker but the review it declares is toothless: `subagent_type: "fork"` (inherits the orchestrator's context AND model — defeats the isolation the session exists for), no `require_evidence`/`checks` (the verdict can then be chat narration that leaves no artifact), or `depends_on` missing a terminal session (the review can run before the work it is meant to judge).

- **`blind-executability`** — the session's `prompt` leaves judgment calls to the executor: it describes multi-step work but states **no expected observations** (what the executor should see per move), **no fork triggers** ("if you observe X, take route B"), **no abort conditions** (when to stop and flag via closeout instead of improvising), or references an assumption it never settles and doesn't mark **RECON NEEDED** with the settling check. Grade against the wargame contract in `~/.claude/skills/plan-builder/references/schemas.md` (the `prompt` field): could a mid-tier model run this session end to end without asking a single question? → recommend adding the missing contract elements to the session prompt. Skip trivial/mechanical sessions (single obvious step, `reasoning` blank-or-low mechanical work) — the contract is for sessions where the executor can plausibly hit an unanticipated state. 🟡 Prose-heuristic; false positives expected and acceptable.

Current landscape for the recommendations (v14 SSOT — re-read `model-routing.yaml`
`task_classes:` before trusting this paragraph verbatim, it is a summary, not the
source): Sonnet is the `standard_build` default (`medium`, escalating to `high` — its
live ceiling, `xhigh` is a dead rung). **Opus is the standing default for ALL other
judgment work** — `agentic_build`/`linchpin` at `high`, `deep_reasoning` at `medium`
escalating to `high` — and DOMINATES Fable at every calibrated rung (v1.11 retired the
older "Fable dominates Opus" premise this file used to assert; never emit `Opus`+`max`
as an automatic rung regardless — dead/weak, operator opt-in only). **Fable is
escalation-apex ONLY, never a standing default for anything** — reached by escalating
the MODEL after 2 failures at the same root cause, or for whole-codebase/large-context
synthesis; it lands at `low` and can climb `low`→`medium`→`high`→`xhigh` from there.
`opusplan`-style split: CRUD/wiring/scaffolding/codemod work → `Sonnet` (`standard_build`);
integration/multi-file refactor/non-obvious debugging → `Opus`/`high` (`agentic_build`);
architecture/ambiguous-tradeoff/hard-root-cause → `Opus`/`medium` (`deep_reasoning`); the
plan's one-shot/irreversible session → `Opus`/`high` (`linchpin`). Codex lane
(`providers.openai`, s08 LN-01): `gpt-5.6-luna` never below `max` (curve collapses),
`gpt-5.6-terra` never below `max` (effort-steep), `gpt-5.6-sol` at `xhigh` for
`agentic_build`/`deep_reasoning` and `max` for `linchpin` — a barred session (`linchpin`/
`irreversible_change`/`guards_irreversible`) explicitly pinned to any of these is a
`run.py`-level BLOCK on both harnesses, not just a lint flag.
