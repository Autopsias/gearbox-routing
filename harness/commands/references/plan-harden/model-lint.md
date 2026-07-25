# Phase 4.0 — Model-selection sanity lint: full rule set

Read this from plan-harden.md's §4.0 (the stamp, data-source order, and skip condition
stay inline there — this file is only the flag rules themselves). Data source: read
task-class defaults from `~/.claude/model-routing.yaml` (`task_classes:` block) when it
exists and parses; fall back to `~/.claude/skills/plan-builder/references/schemas.md`
→ "Model + reasoning rubric" only if the SSOT is missing/unparseable.

Flag each session that hits:

- **`Opus` + `max`** — the rubric forbids it on DOMINANCE grounds (on our own calibration run, `max` IS Opus's peak, but every Opus rung is beaten by a Fable rung at equal or lower $/task; switch model, don't crank). → recommend `Fable`/`low` (deep-reasoning/linchpin default), or `Opus`/`high` for very input-heavy deep-reads. 🟡
- **`Fable` at `high`/`xhigh` without an escalation justification** — Fable's default is `low` (its low-effort reasoning is the value point); `medium`→`high` are ESCALATION rungs for a problem unsolved in prior rounds or whole-codebase/large-context synthesis, and Fable draws usage credits (suspended/paywalled 2026-07). A hot-Fable session whose `why_model` doesn't name one of the two triggers should be `Fable`/`low`. Also confirm the plan tolerates the auto-degrade **Fable→Opus 4.8 @ `xhigh`** on a dispatch failure (it's reactive, not proactive). 🟡
- **`opusplan` kind↔model mismatch** — Anthropic's `opusplan` routes *plan mode → Opus, execution → Sonnet*; apply the same split to session KIND. Flag both directions from the session's title/deliverable signals: a **build / implementation / wiring** session (signals CRUD, wiring, scaffolding, codemod, migration mechanics, test-writing) assigned to **`Opus`/`Fable`** is likely over-modeled → recommend `Sonnet` (the execution default). A **design / architecture / adjudication / synthesis** session (signals design, architecture, decide, adjudicate, tradeoff, schema design, or "the rest of the plan rests on it") assigned to **`Sonnet`** is likely under-modeled for a plan-mode decision → recommend `Fable`/`low` (the deep-reasoning/linchpin default; `Opus`/`high` for very input-heavy deep-reads). Skip when `why_model` already justifies the deliberate off-split choice. 🟡
- **A hard session left at `reasoning: medium`** — a session whose title/deliverable signals architecture / security-sensitive / ambiguous-tradeoff / hard-root-cause work but sits at `medium` is under-dialed. → recommend `high` (Sonnet's live ceiling — `xhigh` is a dead rung on Sonnet, per our own calibration run); for novel/security/architecture route the MODEL up instead (`Fable`/`low`). 🟡
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
     `task_class: deep_reasoning` + `model: Haiku`, which resolves to `Fable`/`low`
     under `active_provider: anthropic` — recommend `Fable`/`low`, or correct
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

**One non-model rule (rides the same lint pass and MODEL_LINT list):**

- **`blind-executability`** — the session's `prompt` leaves judgment calls to the executor: it describes multi-step work but states **no expected observations** (what the executor should see per move), **no fork triggers** ("if you observe X, take route B"), **no abort conditions** (when to stop and flag via closeout instead of improvising), or references an assumption it never settles and doesn't mark **RECON NEEDED** with the settling check. Grade against the wargame contract in `~/.claude/skills/plan-builder/references/schemas.md` (the `prompt` field): could a mid-tier model run this session end to end without asking a single question? → recommend adding the missing contract elements to the session prompt. Skip trivial/mechanical sessions (single obvious step, `reasoning` blank-or-low mechanical work) — the contract is for sessions where the executor can plausibly hit an unanticipated state. 🟡 Prose-heuristic; false positives expected and acceptable.

Current landscape for the recommendations: Sonnet 5 workhorse ·
`high` coding/agentic default (Sonnet `xhigh` = dead rung) · Opus-never-`max` (dominated, not
overthinking) · Fable·`low` for deep-reasoning AND linchpin (paywall covered by the degrade
ladder) · `opusplan`-style split (design/architecture/adjudication → Fable·low, build/implementation → Sonnet).
