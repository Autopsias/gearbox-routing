# Phase 4.0 — Model-selection sanity lint: full rule set

Read this from plan-harden.md's §4.0 (the stamp, data-source order, and skip condition
stay inline there — this file is only the flag rules themselves). Data source: read
task-class defaults from `~/.claude/model-routing.yaml` (`task_classes:` block) when it
exists and parses; fall back to `~/.claude/skills/plan-builder/references/schemas.md`
→ "Model + reasoning rubric" only if the SSOT is missing/unparseable.

## Contents
- [Schema v8 — route-at-dispatch gate](#schema-v8--route-at-dispatch-gate)
- [Base flags](#base-flags)
- [Three rules added s04 (SKL-02)](#three-rules-added-s04-skl-02)
- [One rule added s07 (DSP-04) — task_class as a routing signal](#one-rule-added-s07-dsp-04--task_class-as-a-routing-signal)
- [Three rules added s08 (LN-01) — Codex-lane traps](#three-rules-added-s08-ln-01--codex-lane-traps)
- [Three non-model rules](#three-non-model-rules)
- [One rule added s07 (ESC-04) — `max_rework` sizing vs the escalation ladder](#one-rule-added-s07-esc-04--max_rework-sizing-vs-the-escalation-ladder)


## Schema v8 — route-at-dispatch gate

Contract: `skills/plan-execute/references/route-at-dispatch-contract.md` (sections 2 and 4).
**From `plan_schema_version` 8 the author no longer picks the model — the executor resolves
it from `task_class` at dispatch.** Read the version from `manifest.json`'s
`plan_schema_version` (the manifest governs, not the spec). A plan below 8 keeps every rule
in this file exactly as written; the rest of this section applies to v8 only.

A v8 session carries an **override** when its `model` or `reasoning` is non-empty (the
builder requires them as a pair). Without an override the session's model is the executor's
job, and **every rule below that compares a declared `model`/`reasoning` with the rubric is
skipped for it** — there is nothing declared to grade. With an override those rules run
unchanged, and the five new rules below run too.

- **`task-class-missing`** — a v8 session whose `task_class` is absent, empty, or not a key of
  the SSOT `task_classes:` block. The builder refuses this, so it catches a hand-edited
  manifest. 🔴 Deterministic (structured field); quote the session's `task_class` value.
- **`override-without-reason`** — a v8 session with an override and no non-blank `why_model`
  (whitespace-only counts as missing; also: `why_model` set with no override, since it is then a reason for nothing). 🔴
  Deterministic; quote the `model`/`reasoning`/`why_model` values.
- **`override-incomplete`** — a v8 session that sets exactly one of `model` and `reasoning`.
  The builder requires the pair, so this catches a hand-edited manifest. 🔴 Deterministic;
  quote both values.
- **`override-below-floor`** — a v8 override that sits below the floor, AND the session has
  non-empty `peer_triggers` or `task_class == linchpin`. 🔴 Deterministic. **Below the floor =
  the override's cell ranks LOWER than the class default's cell on the provider's escalation
  ladder** (operator decision 2026-09-30). Rank orders tier first, by the provider's `models:`
  declaration (ascending; the `model_ladder` walks the same order), then effort, by `low <
  medium < high < xhigh < max` (no dial lowest). So every opus cell outranks every sonnet
  cell, and a same-model, lower effort is below. It is NOT "the default is reachable by
  `escalate()` steps": for `deep_reasoning` on anthropic (default `opus@medium`) the walk from
  `sonnet@high` goes `opus@high → opus@xhigh → fable@…`, never visiting `opus@medium`, yet
  `sonnet@high` is below. Compute it with the shared resolver
  (`resolve_route.below_floor(task_class, provider, cell)`, which both this lint and the
  builder call), never a hand-written ladder, on the ladder of the **override's own
  provider** (a Codex-family model such as `gpt-*`/`codex-*` uses `openai`; a Claude family
  name uses `anthropic`; under the GLM tree `zai`). **Cross-provider = the override's provider
  differs from the tree's provider** — the provider this plan dispatches on by default, read
  the way `run.py` reads it: `PLAN_EXECUTE_ROUTING_PROVIDER`, else `zai` under the GLM tree,
  else the routing file's `active_provider`. Check order: (1) a cross-provider override (for
  example a Codex model on the Claude tree) skips before any default is resolved (contract §4,
  recorded `pinned_override`); (2) a cell that cannot be ranked — its model is not on that
  provider's ladder, its effort is not a known level, or the provider has no escalation
  ladder — skips; (3) otherwise compare ranks, strictly lower is below. The check:
  ```
  python3 -c "
  import sys
  sys.path.insert(0, '$HOME/.claude/scripts')
  import resolve_route as r
  if '<provider>' != '<tree provider>':  # (1) cross-provider: skip the floor check
      print('ok (cross-provider)'); raise SystemExit
  cell = {'model_id': '<override model_id>', 'native_effort': '<override effort>'}
  # (2) unrankable skips and (3) rank compare both live in the helper; an unknown effort
  # returns False instead of raising.
  print('below floor' if r.below_floor('<task_class>', '<provider>', cell) else 'ok')
  "
  ```
  An override equal to the default is not below. An override below the floor on a session
  with neither `peer_triggers` nor `linchpin` is allowed — no flag.
- **`locked-check-suggested`** — a v8 session that `depends_on` a session whose deliverable is
  a test file (any of its items' `touches`, or its `deliverable` text, names a path matching
  `test_*.py`, `*_test.py`, or a `tests/` folder) and declares no `verify.locked`. The
  dependent can then edit the test it is graded by. 🟡 Advisory; recommend listing the test
  paths in `verify.locked`.

`peer-gate-missing`, `peer-trigger-undeclared` and `acceptance-review-missing` need no
change for v8.

## Base flags

**v8: every flag in this section applies only to a session that declares a model override (see the v8 gate above). Below v8, unchanged.**

Flag each session that hits:

- **`Opus` + `max`** — `max` is a dead/weak rung on Opus (v1.11 calibration: little to no quality gain over `high`, ~2× the cost; the SSOT's own escalation invariants forbid emitting it as an automatic rung — `"NEVER emit opus + max as an automatic rung"`, operator opt-in only). **The old "dominated by Fable" premise this rule used to cite is RETIRED (v1.11) and now runs the other way**: Opus is the standing default for judgment work and DOMINATES Fable at every calibrated rung — don't recommend switching TO Fable as a cost/quality move, that direction no longer holds. → recommend dropping to `Opus`/`high` (the real ceiling — never crank past it automatically). Only escalate the MODEL to `Fable` after the documented trigger (2 failures at the same root cause) or for whole-codebase/large-context synthesis — that lands at `Fable`/`low` (Fable's escalation-apex entry rung, not a standing default for anything). 🟡
- **`Fable` at `high`/`xhigh` without an escalation justification** — Fable's default is `low` (its low-effort reasoning is the value point); `medium`→`high` are ESCALATION rungs for a problem unsolved in prior rounds or whole-codebase/large-context synthesis. **Fable is a normal, available model** (operator-confirmed 2026-07-26 — the earlier "suspended/paywalled" note was stale and had itself been quoted back at the operator as fact; never assume that note without re-checking `model-routing.yaml`'s `prices.fable` line), but it bills at Mythos-class rates ($10/$50), so a rung above `low` still needs a reason. A hot-Fable session whose `why_model` doesn't name one of the two triggers should be `Fable`/`low`. Also confirm the plan tolerates the auto-degrade **Fable→Opus @ `high`** on a dispatch failure (per-target degrade effort, not `xhigh` — Opus's `xhigh` rung is dead; it's reactive, not proactive). 🟡
- **`opusplan` kind↔model mismatch** — Anthropic's `opusplan` routes *plan mode → Opus, execution → Sonnet*; apply the same split to session KIND. Flag both directions from the session's title/deliverable signals: a **build / implementation / wiring** session (signals CRUD, wiring, scaffolding, codemod, migration mechanics, test-writing — the `standard_build` task class) assigned to **`Opus`/`Fable`** is likely over-modeled → recommend `Sonnet` (the `standard_build` default; note `agentic_build` — integration/multi-file refactor/non-obvious debugging — is its OWN class and correctly resolves to `Opus`/`high`, not Sonnet, since v1.11; don't flag that as over-modeled). A **design / architecture / adjudication / synthesis** session (signals design, architecture, decide, adjudicate, tradeoff, schema design, or "the rest of the plan rests on it") assigned to **`Sonnet`** is likely under-modeled for a plan-mode decision → recommend `Opus`/`medium` (the `deep_reasoning` default) or `Opus`/`high` (the `linchpin` default) — escalate to `Fable`/`low` only if the session's own `why_model` names the stuck/large-context trigger. Skip when `why_model` already justifies the deliberate off-split choice. 🟡
- **A hard session left at `reasoning: medium`** — a session whose title/deliverable signals architecture / security-sensitive / ambiguous-tradeoff / hard-root-cause work but sits at `medium` is under-dialed **only when it is declared `Sonnet`** (Sonnet's live ceiling is `high` — `xhigh` is a dead rung on Sonnet — so `medium` is genuinely low for that signal set). → recommend `Sonnet`/`high`, or route the MODEL up to `Opus`/`medium` (the `deep_reasoning` default — judgment work's standing tier, not Fable) for the harder cases; escalate further to `Opus`/`high` or `Fable`/`low` only if still stuck after that. **Do not flag an `Opus`/`medium` session on this signal set** — that IS the correctly-modeled `deep_reasoning` baseline, not an under-dial. 🟡
- **`reasoning` blank on a real (non-mechanical) session** — under-specified; the runner prepends no thinking directive. → recommend an explicit tier. 🟣

## Three rules added s04 (SKL-02)

**v8: `pin-conflict` and `specialist-exists-but-null-subagent` compare a declared `model` and apply only to a session with a model override; `peer-gate-missing` and `peer-trigger-undeclared` apply to every v8 session unchanged.**

Read the SSOT `agents:` / `codex_peer` blocks, not just `task_classes:`:

- **`pin-conflict`** — a session's `dispatch.subagent_type` names a **specialist agent** (a row in SSOT `agents:`, e.g. `digdeep`, `safe-refactor`, `security-scanner` — not a generic type like `null`/`general-purpose`/`Explore`/`Plan`/`fork`), AND that agent's `.md` frontmatter `model:` (or the SSOT's pinned `model:` for that agent, if the `.md` can't be read) **differs** from the session's own `model` field. Per the documented resolution order, the **per-invocation Task `model` param silently wins** at dispatch — the session's declared `model` is discarded with no error, so this is a silent divergence a plan author is unlikely to have intended. → recommend either changing the session `model` to match the pinned specialist, or dropping `subagent_type` to `null` if a fresh general-purpose agent at the session's own `model` was actually intended. 🟡 Deterministic (structured fields: `dispatch.subagent_type` + `model`) — reliable, not a prose heuristic.
- **`peer-gate-missing` (STRUCTURED, 🔴 plan-killer)** — the session carries a non-empty `peer_triggers` array in the manifest (structured declaration of `architecture_decision` / `irreversible_change` / `security_sensitive`), AND has no `verify.gates` entry named `adversarial-review` (nor `/adversarial-review` in its `prompt`/`agent_instructions`). → **🔴 plan-killer** (the ONE model-lint flag that can block — see §4.1 carve-out). Deterministic, not a heuristic: the author DECLARED the trigger, so a missing gate is a real omission. NOTE: `build_plan.py::validate_peer_triggers` already raises at BUILD time on this exact condition, so a freshly-built plan cannot reach harden while violating it — this lint is the backstop for hand-edited manifests. Quoted-evidence for the 🔴 is the `peer_triggers` value itself (`manifest.json` → session `.peer_triggers`).
- **`peer-trigger-undeclared` (keyword net, 🟡 advisory)** — the session's title/`human_summary`/`deliverable` text matches a keyword from SSOT `codex_peer.lint_keywords` for a trigger, AND `peer_triggers` is empty/absent. → recommend either declaring `peer_triggers` (which then enforces the gate structurally) or noting in prose why the trigger doesn't apply. 🟡 **Advisory-only, prose-heuristic** — false positives/negatives expected; it never blocks. This is the soft net that PROMPTS the author toward the structured field. (Structured promotion landed 2026-07-10 by operator direction, ahead of the documented "first observed peer-gate miss" auto-trigger; the keyword rule is retained as the discovery aid, not the enforcement.)
- **`specialist-exists-but-null-subagent`** — the session's `(model, reasoning)` pair **exactly matches** a pinned specialist agent's `(model, effort)` row in SSOT `agents:` (excluding the epic_* rows, which are a different dispatch surface), AND the session's title/`deliverable` contains a keyword drawn from that agent's own name (split on `-`, dropping generic suffixes like `-fixer`/`-analyzer`/`-generator`/`-manager`/`-refactor` — e.g. `safe-refactor` → "refactor"; `security-scanner` → "security"), AND `dispatch.subagent_type` is `null`/omitted. A fresh `general-purpose` agent at that model/effort does the work WITHOUT the specialist's tool scoping or system prompt. → recommend setting `subagent_type` to the matching specialist explicitly, or leave a `why_model`/dispatch note confirming a fresh general-purpose agent was the deliberate choice. 🟡 Prose-heuristic on the keyword match, deterministic on the `(model, effort)` comparison.

## One rule added s07 (DSP-04) — task_class as a routing signal

The complement to `peer-gate-missing` (that gate is about REVIEW; this one is about MODEL CHOICE):

- **`task-class-model-mismatch`** — **(v8: only for a session that declares a model override; a v8 session with no override has no declared model to compare. Below v8, unchanged.)** For every session carrying a non-empty `task_class`
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

## Three rules added s08 (LN-01) — Codex-lane traps

**All three read `providers.openai`
via the SAME calls the s07 rule above already uses (`resolve_route.resolve(...,
'openai')`, `run._session_barred`, `run._CODEX_DECLARED_RE`) — never a hand-rolled
second parse of the per-provider map. A session's `model` counts as an EXPLICIT Codex
pin exactly when `run._CODEX_DECLARED_RE` matches it (`re.search(r"gpt|codex", model,
re.I)`), the same test `run.py` itself uses:**

- **`luna-below-max`** — **(v8: only when the session declares a model override.)** A session whose declared model is `gpt-5.6-luna` (the
  `cheap_fast` tier under `providers.openai`) with a declared `reasoning`/native effort
  other than `max`. Luna's quality curve COLLAPSES below its ceiling — measured DeepSWE:
  `max` 67% → `high` 44% → `medium` 11% (`providers.openai.calibration.research_ref`) —
  which is exactly why `providers.openai.effort.map.cheap_fast` and the per-provider
  `task_classes.mechanical` row both route EVERY intent to `max` for this tier; there is
  no lighter rung worth taking. A luna session below `max` is not a cost-saving choice,
  it is a quality collapse. → recommend `gpt-5.6-luna`/`max`, or move the session off
  luna entirely (`gpt-5.6-terra`/`max`, the `frontier_reasoner` tier) if the task doesn't actually fit
  `mechanical`. 🟡
- **`codex-task-class-mismatch`** — **(v8: only when the session declares a model override.)** The s07 `task-class-model-mismatch` rule above,
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
- **`linchpin-pinned-to-codex`** — **(v8: only when the session declares a model override; an unpinned v8 linchpin session is resolved by the executor.)** Mirrors `run.py::_codex_harness_spec`'s own gate
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

## Two rules added v1.21 — zai-lane traps (GLM tree)

**Both fire only when the plan will run under the GLM substrate — detect it the
SAME way `run.py` does (`run._load_routing()` returning `zai`: the
`PLAN_EXECUTE_ROUTING_PROVIDER` env, or `CLAUDE_CONFIG_DIR` ending
`.claude-glm`), never by guessing from model names. When hardening a plan that
will run on the Claude mainline, skip both silently:**

- **`zai-fable-dead-apex`** — **(v8: only when the session declares a model override.)** A session whose declared `model` is `Fable`,
  under the zai provider. The zai profile has NO apex tier — nothing above the
  `opus` token exists on that lane — and `run.py`'s zai clamp remaps fable to
  `opus@max` at resolution, so the pin silently means something other than what
  the manifest says. Worse, a BARE fable alias under the GLM env resolves to
  the SONNET slot (glm-5.3-flash — probed 2026-08-30, see
  `providers.zai.calibration.research_ref`): without the clamp this "apex" pin
  would be a silent DOWNGRADE. → recommend `Opus` + `max` (the zai ceiling,
  reached without a fable pin), noting the clamp already makes the session run
  there. 🟡
- **`zai-haiku-rough-tier`** — **(v8: only when the session declares a model override.)** A session whose declared `model` is `Haiku`,
  under the zai provider. The GLM env maps the haiku alias to glm-4.5-air — a
  previous-generation model in the measured rough zone (DeepSWE v1.1:
  glm-5.2[max] 44%; `providers.zai` declares no cheap tier at all for this
  reason) — while the zai workhorse token (sonnet → glm-5.3-flash @ max) is
  ~19 points better at pocket-change cost. The zai clamp already remaps the
  dispatch to `sonnet@<its tier>`; the flag is about the MANIFEST lying. →
  recommend `Sonnet` + `low` for true mechanical work (flash @ low), or
  `Sonnet` + anything for normal work (flash @ max). 🟡

Under zai, NO flag fires for a `Sonnet`/`Opus` session pinned below `max` (the
anthropic-lane "never opus+max" base rule does NOT apply — `providers.zai` is
effort-steep and the clamp raises the rung automatically); and the s07
`task-class-model-mismatch` rule resolves with `effective_provider` forced to
`zai` exactly as it does for `openai` (step 1's provider resolution already
carries the override — never re-derive it by hand).

## Three non-model rules

They ride the same lint pass and the same `MODEL_LINT` list:

- **`decision-debt`** *(2026-08-01, wayfinder-derived)* — an unmade decision is embedded in a build session's prompt instead of being resolved before it; the guess surfaces weeks later as a BLOCKED session or a silent deviation. Two nets, at most one flag per session:
  1. **Structured net:** `manifest.json` carries a top-level `open_questions` list (the fog register plan-builder writes). For each entry, find any session whose `prompt`/`deliverable` consumes its answer (same subject matter). If a consumer exists and no session upstream of it (via `depends_on` ancestry) is a **decision session** resolving that question — title/deliverable names the decision, or the prompt is research/prototype/grilling-shaped — flag the consumer: the plan schedules building on an answer nobody is scheduled to produce. Quote the `open_questions` entry as evidence.
  2. **Prose net:** a session `prompt` containing unresolved-decision language — "TBD", "to be decided", "decide later", "open question", "choose between", "we'll figure out" — with no upstream decision session covering it. Prose-heuristic; false positives expected and acceptable.

  → recommend one of: resolve the decision now and edit the prompt; split out an explicit decision session that `depends_on`-blocks the consumer (with a human checkpoint when it's a genuine judgment call — auto-accept critiques decisions, it doesn't make them); or move it to `open_questions` with what would sharpen it, if genuinely not statable yet. 🟡 — never blocks. A deliberately-deferred decision the author already annotated (the prompt marks it **RECON NEEDED** with the settling check, per the wargame contract) is not debt — skip it.

- **`acceptance-review-missing`** — the plan has **≥4 sessions** and **no** session carries `acceptance_review: true` in `manifest.json`. Session `verify` gates validate each part; without this session nothing validates the whole, so a plan whose every session closes `DONE` can still miss its objectives via accumulated `deviations` and quietly `DEFERRED` items. → recommend adding a closing acceptance-review session (fresh isolated agent, `depends_on` every terminal session, `task_class: deep_reasoning`, `require_evidence: true`, plan acceptance criteria verbatim in its prompt) per `~/.claude/skills/plan-builder/references/schemas.md` → "Closing acceptance review". 🟡 Deterministic (a structured manifest field + a session count), but never a 🔴 — a short or purely-exploratory plan can legitimately have nothing plan-level to validate; say so in prose rather than adding a hollow gate. Skip on plans of <4 sessions. Also flag 🟡 when a session DOES carry the marker but the review it declares is toothless: `subagent_type: "fork"` (inherits the orchestrator's context AND model — defeats the isolation the session exists for), no `require_evidence`/`checks` (the verdict can then be chat narration that leaves no artifact), or `depends_on` missing a terminal session (the review can run before the work it is meant to judge).

- **`blind-executability`** — the session's `prompt` leaves judgment calls to the executor: it describes multi-step work but states **no expected observations** (what the executor should see per move), **no fork triggers** ("if you observe X, take route B"), **no abort conditions** (when to stop and flag via closeout instead of improvising), or references an assumption it never settles and doesn't mark **RECON NEEDED** with the settling check. Grade against the wargame contract in `~/.claude/skills/plan-builder/references/schemas.md` (the `prompt` field): could a mid-tier model run this session end to end without asking a single question? → recommend adding the missing contract elements to the session prompt. Skip trivial/mechanical sessions (single obvious step, `reasoning` blank-or-low mechanical work) — the contract is for sessions where the executor can plausibly hit an unanticipated state. 🟡 Prose-heuristic; false positives expected and acceptable.

## One rule added s07 (ESC-04) — `max_rework` sizing vs the escalation ladder

**DERIVED, never a hardcoded number** [HARDENED:codex-verify-r1 — an earlier attempt at this rule
no-oped; HARDENED:codex-verify-r2 — the fixed `< 3` threshold this replaced was wrong for
`opus@medium`, which needs `opus@high` first and only reaches `fable@xhigh` at rework 5]:

- **`max-rework-cannot-reach-apex`** — for every session declaring BOTH `model` and
  `reasoning` (a session with no `reasoning` never escalates at all — see the `escalation`
  field's own carve-out — so this rule does not apply to it), resolve its starting rung and
  walk the SAME ladder `/plan-execute`'s `escalation.py` walks, via `resolve_route`, never a
  hardcoded ladder list:
  ```
  python3 -c "
  import sys
  sys.path.insert(0, '$HOME/.claude/scripts')
  import resolve_route as rr
  provider = '<effective_provider>'   # resolve per the task-class-model-mismatch rule above
  cur = {'model_id': '<session model, lowercased/normalized>', 'native_effort': '<session reasoning>'}
  rungs = 1   # the mandatory same-rung retry (rework 1) always precedes the first climb
  while cur != rr.EXHAUSTED:
      cur = rr.escalate('<task_class>', provider, current=cur)
      rungs += 1 if cur != rr.EXHAUSTED else 0
  print('rungs_to_apex =', rungs)
  "
  ```
  `rungs_to_apex(start_cell)` is that count — the number of REWORK ATTEMPTS from the
  session's own starting cell until the ladder reaches the apex rung (e.g. `fable@xhigh` on
  the Claude lane or `gpt-5.6-sol@max` on the Codex lane): one for the mandatory same-rung
  retry (`stuck_protocol` needs two same-signature failures before the first climb) plus one
  per `escalate()` call after that. Verified against the live SSOT (2026-08-15):
  `opus@high` → 4, `opus@medium` → 5, `sonnet@high` → 5 — these are the same worked examples
  `skills/plan-builder/references/schemas.md`'s `max_rework` sizing guidance states, computed
  the same way. Flag the session when its declared `max_rework` is:
  - **`< 2`** — no escalation is possible at all (the stuck protocol needs two same-rung
    attempts before the first climb; `max_rework` of 0 or 1 never reaches a second attempt),
    even though `escalation` is not opted out. → recommend `max_rework: 2` as the floor, or
    set `escalation: false` explicitly if the session is a deliberate single-cell calibration
    run (see the `escalation` field's own documented use case).
  - **`< rungs_to_apex(start_cell)`** — `max_rework` bounds the rework loop before the ladder
    reaches its apex, so a real, persistent failure parks the session `BLOCKED` at an
    intermediate rung rather than ever trying the apex model. **Since 2026-08-27 that is the
    intended default, not a defect** — apex-sized budgets of 5–6 were measured buying 11
    rework rounds across 11 sessions on one plan (see plan-builder `schemas.md`'s
    `max_rework` sizing guidance), and a parked session costs less than the rounds. → REPORT
    the gap with the computed `rungs_to_apex(start_cell)` number, and recommend raising to it
    ONLY for a `linchpin` session or one the author expects to defeat its authored tier;
    otherwise state that the early exhaustion is the default posture and no change is needed.
  **CHECK THE RECOMMENDATION AGAINST THE SCHEMA CEILING** *(2026-08-21, revised the same day
  when the ceiling was raised)*. `build_plan.py` validates `max_rework` against
  `plan_limits.MAX_REWORK_CEILING`, which is **6** — the length of the longest ladder any legal
  starting rung has. So the computed `rungs_to_apex` is now buildable for every cell, including
  `sonnet@medium` (the `standard_build` DEFAULT), which needs 6 and was unauthorable under the
  old cap of 5. Recommend the computed number directly. Read the ceiling from `plan_limits`
  rather than repeating a literal here — if the SSOT ever grows a longer ladder, the constant
  and its pinning test move first. Never emit a number above the ceiling: the builder rejects
  it and the plan fails to build.

  Both conditions name the exact numbers computed (session's current `max_rework`, the floor
  of 2, and `rungs_to_apex`) rather than a generic "raise max_rework" — the whole point of
  deriving this per-session is that the right number differs by starting cell (see the worked
  examples in `skills/plan-builder/references/schemas.md`'s `max_rework` sizing guidance:
  `opus@high` → 4, `opus@medium` → 5, `sonnet@high` → 5, `sonnet@medium` → 6). 🟡 Deterministic (resolver-computed),
  never 🔴 — an author may legitimately want a session capped short of the apex.

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
