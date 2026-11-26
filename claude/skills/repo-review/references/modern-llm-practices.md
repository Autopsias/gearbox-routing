# Modern Frontier LLM Practices for Repo Reviews

## Contents

- [Core thesis — harness complexity must track real deficits](#core-thesis--harness-complexity-must-track-real-deficits)
- [Over-scaffolding vs. under-scaffolding](#over-scaffolding-vs-under-scaffolding)
- [Cross-model architectural checks](#cross-model-architectural-checks)
- [Provider-specific audit checks](#provider-specific-audit-checks)
- [Live research queries](#live-research-queries)

---

## Core thesis — harness complexity must track real deficits

Every rule, workflow wrapper, or guardrail in a repository encodes an assumption about what
an agent cannot do reliably on its own. Frontier coding models improve rapidly — and
scaffolding written for earlier generations frequently **raises cost, slows turns, or
degrades output quality** on newer models.

Conversely, even the strongest frontier models fail without a clean repo harness:
benchmark studies consistently show the **same model** varying 2x–3x in task resolution and
up to 5x in cost depending on harness tooling, context hygiene, and verification loops.

Audit every agentic element with one question: **Is this load-bearing today, or is it
memorializing an older model's weakness?**

---

## Over-scaffolding vs. under-scaffolding

| Symptom | Under-scaffolded repo | Over-scaffolded ("hobbled") repo |
|---|---|---|
| Context | Agent wastes turns rediscovering commands, layout, and hidden gotchas | Giant `AGENTS.md` / rules pile causes instruction decay ("curse of instructions": $0.95^{20} \approx 36\%$) |
| Procedures | Fragile/irreversible operations have no checklist or gate | Routine judgment tasks are micromanaged with rigid step-by-step rituals and example walls |
| Safety | High-risk boundaries ("don't leak secrets", "don't edit generated code") are undocumented or prose-only | Stacked `NEVER` / "stop and ask" rules across memory, rules, and skills cause unnecessary stalls |
| Orchestration | Long runs drift without checkpoints or verification | Heavy custom planner/router/subagent wrappers duplicate capabilities native harnesses already have |
| Evals | Prompt/skill/model changes ship on vibes ("looks good") | Brittle path-matching evals punish valid alternative solutions or saturate at 100% |

**Pruning rule:** When a repo upgrades models, flag old workarounds (prefills, "think step
by step" prompt rituals, exhaustive few-shot examples, rigid tool-order mandates, hardcoded
token countdowns) as **Delete / unhobble** candidates to ablate against the repo's evals.

---

## Cross-model architectural checks

Check these regardless of which provider the repository uses:

1. **Progressive disclosure over encyclopedias**: Keep root context short (map + commands +
   gotchas + boundaries). Push deep procedures into on-demand skills and references; keep
   module-specific rules next to the module.
2. **Expressive interfaces over example walls**: Prefer typed tool schemas, enums, clear CLI
   flags, and crisp error messages over 20 lines of prompt examples that over-constrain how
   the model explores.
3. **Static-first prompt & context ordering (cache discipline)**: Keep stable instructions
   and tool definitions at the front; keep dynamic inputs (timestamps, git status, logs,
   per-task state) at the end so implicit and explicit prompt caches hit across turns.
4. **Effort / thinking depth as configuration, not prose**: Frontier reasoning models expose
   explicit effort/thinking controls (`effort`, `thinking_level`, `reasoning.effort`). Flag
   prompts trying to substitute prose ("think very carefully and deeply") for proper
   effort configuration, or defaulting every trivial task to maximum thinking depth.
5. **Subagent & delegation economics**: Subagents multiply token cost and latency. Check
   whether skills or agent prompts have blanket "always delegate" instructions, whether
   fan-outs pin cheaper worker tiers rather than frontier tiers, and whether subagents
   return compact summaries instead of raw transcripts.
6. **Code ownership vs. scope discipline**: Frontier models respond strongly to ownership
   framing ("you own this codebase") by proactively refactoring and consolidating — which
   can override "keep diffs minimal" rules. Check that repo instructions clearly state
   whether drive-by cleanup is wanted or strictly out of scope, without contradicting
   themselves.
7. **Deterministic verification over model self-certification**: "Looks right to me" is not
   verification. Check that the repo exposes fast, deterministic local commands with clear
   non-zero exit codes and actionable failure output.
8. **Postmortem memory, not superstition**: Repo rules and memories should record verified
   root causes ("use X because Y") — never cargo-cult fixes ("always pass `--force`")
   copied from one session.

---

## Provider-specific audit checks

Apply only the sections matching models or harnesses the target repository actually uses or
configures. Verify fast-moving model IDs, parameters, and limits via live search when
changes depend on them.

### Anthropic / Claude (e.g., Claude 5 / Opus / Sonnet / Fable family & Claude Code)

- **Unhobbling check**: Anthropic's guidance for Claude 5 / Fable-class models explicitly
  warns that prompts and skills authored for older models are often over-constrained and
  can lower quality. Look for bloated `CLAUDE.md`, conflicting rules ("add comments" vs.
  "no comments"), and rigid procedures that can be replaced with outcome criteria.
- **Caution pile-up**: Stacked `NEVER` instructions and accumulated memory notes ("stop at
  first error", "never retry") cause frontier Claude models to over-ask for permission,
  especially in multi-agent flows. Prefer explicit exception rules ("ask before deleting
  files") and name routine autonomous steps explicitly.
- **Effort calibration**: Verify effort levels are tuned per task class (`low`/`medium`/
  `high`/`xhigh`/`max`) rather than carried over blindly from an older model generation,
  and check that `high` (not `xhigh`/`max`) is the upper default unless evals prove a gain.
- **Prompted token budgets**: Putting a textual token budget/countdown in prompts can warp
  spend or cause premature stopping; test removing textual budgets.
- **Subagent over-delegation**: Check for standing instructions that invite delegation on
  small tasks; ensure subagent prompts state cost/value criteria and pin appropriate tiers.
- **Claude Code surface hygiene**: `CLAUDE.md` should stay a thin `@AGENTS.md` pointer when
  cross-platform portability matters; check for broken `.claude/skills/` symlinks or
  redundant `.claude/commands/` shims.

### Google / Gemini (e.g., Gemini 4 Neon / Hydrogen / Pro / Flash & Antigravity)

- **Thinking-level API hygiene**: On Gemini 4 Neon-class models, reasoning depth is set via
  `thinking_level` (`LOW`, `MEDIUM`, `HIGH` default, `XHIGH`) — legacy `thinking_budget` or
  `MINIMAL` fail validation (`HTTP 400`), and legacy sampling tweaks (`temperature`,
  `top_k`, `top_p`) may be ignored while `frequency_penalty`/`presence_penalty` can error.
  Flag repos still shipping deprecated Gemini parameter shapes.
- **Default latency & cost awareness**: When a model defaults to `HIGH` thinking (such as
  Gemini 4 Neon), routine coding or routing call sites that omit `thinking_level` silently
  pay `HIGH` thinking tokens and latency. Check that call sites explicitly route routine
  tasks to `LOW`/`MEDIUM`, reserve `HIGH` for multi-file/agentic loops, and gate `XHIGH`
  behind measured misses.
- **Output-budget & compaction headroom**: Thinking tokens count toward `max_output_tokens`
  and `thought_signature`s accumulate across multi-turn tool loops. Flag low
  `max_output_tokens` caps, aggressive compaction thresholds, or custom chat loops that
  strip/mangle `thought_signature` parts.
- **Static-first prefix caching**: Implicit durable caching triggers automatically on shared
  prefixes (e.g., ≥4,096 tokens on Neon) with no explicit cache API on some models. Flag
  dynamic runtime state (user queries, timestamps, file dumps, logs) injected inside system
  instructions.
- **Structured pre-tool state & Antigravity surface**: Prefer dedicated tool declarations
  for intermediate notes over unstructured prose before tool calls. Check that root
  `GEMINI.md` does not duplicate `AGENTS.md` (Antigravity loads both cumulatively) and that
  root `AGENTS.md` stays within Antigravity's 24,000-byte per-file cap.
- **Planner/worker pairing**: Check whether heavy orchestration pairs a frontier planner
  (e.g., Neon) with a faster worker tier (e.g., Hydrogen/Flash) instead of fanning out on
  the top tier.

### OpenAI / GPT & Codex

- **Portable `AGENTS.md`**: Codex does not expand Claude-style `@path` imports inside
  `AGENTS.md`; flag `@`-imports in `AGENTS.md` as a portability bug.
- **Reasoning effort & harness parity**: Check that `reasoning.effort` is mapped deliberately
  per task class rather than assuming another vendor's scale, and note that models can behave
  very differently across harnesses (e.g., minimal shell loop vs. heavy IDE harness) —
  always evaluate on the actual production runtime.
- **Minimal tool surface for bash-strong models**: Empirical harness studies show bash-strong
  models often perform better and cheaper with lean shell + file primitives than with dozens
  of redundant wrapper tools. Flag tool bloat.

---

## Live research queries

When Step 2 triggers (the repo pins specific models/harnesses or the user asks for fresh
external benchmarks), run 1–3 focused queries via Exa (`web_search_exa`) or available search
tools, replacing `<model>` / `<harness>` with what the repo actually declares:

1. **Model prompting & API contract**:
   - Query: `official prompting guide and API rules for <model> coding agents thinking effort caching 2026`
   - Objective: `Find official provider documentation for <model>; extract current effort/thinking parameter names, deprecated flags, caching rules, and anti-patterns.`
2. **Harness & agent-readiness practices**:
   - Query: `engineering guide on repository harness design AGENTS.md skills hooks and behavioral evals for <harness> coding agents`
   - Objective: `Find primary engineering posts on agentic repo setup and harness audits for <harness>; extract concrete failure modes and verification patterns.`
3. **Eval methodology for the repo's domain**:
   - Query: `how to evaluate <domain> AI agent workflows behavioral evals graders and CI regression testing`
   - Objective: `Find practitioner guides on designing reliable graders, balanced eval sets, and CI eval gates for <domain> agents.`

Cite the URL and date of any live finding that changes a recommendation. Never let a
generic blog listicle override direct evidence from the repository itself.
