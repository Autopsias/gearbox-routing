---
name: plan-builder
description: Build an executable multi-session plan — interview the user, compile a JSON spec, and emit a plan directory (self-contained PLAN.html dashboard + manifest.json dispatch graph + per-session prompt/context files) that auto-runs via the /plan-execute skill. Use when the user says "create a plan", "build a roadmap", "execution plan", "session plan", "improvement plan", "rollout plan", "project plan dashboard", "multi-session plan", or describes a body of work spanning multiple sessions even without the word "plan". Pass --harden to adversarially stress-test the finished plan with /plan-harden before handing it back. Not for simple to-do lists, and not for running an already-built plan (that's plan-execute).
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion]
effort: high  # plan authoring is judgment-heavy (session decomposition, model/reasoning rubric, dual-layer drafting). Pins the tier explicitly regardless of session default. Verified honored in binary 2.1.170.
---

# Plan Builder (Aurora edition)

Generates a plan directory for any multi-session execution plan: a self-contained PLAN.html dashboard (~150-250 KB, opens directly in the browser, no external dependencies beyond Google Fonts) plus the /plan-execute execution bundle (manifest.json dispatch graph, per-session prompt + context files).

The Aurora edition gives every plan a **dual-layer voice**: the parts a human reads to follow the plan, and the parts an AI agent reads to execute it. Both live in the same document — the human layer prominent in serif type, the agent layer one click away inside a `<details>` block but always present in the DOM.

## What the output gives the user

1. **Editorial page header** — serif title, glass-blur sticky banner, last-updated stamp.
2. **Sticky session navigation strip** — 1 chip per session, color-coded by status, click to jump.
3. **Operating Manual** at the very top — collapsed for humans, fully in DOM for any Claude that opens the file (this is the load-bearing rule sheet).
4. **Plan Achievement infographic** — visual story of what the plan achieves (one of 5 structured SVG templates or `custom`).
5. **Up Next callout** — quiet terminal-style prompt, auto-derives the next non-DONE session.
6. **Twin donut rings** — items + sessions, status-segmented, TODO arc at reduced opacity so 0/N looks intentional.
7. **Status filter chips + category filter chips** — both filter the cards below.
8. **Categories status grid** — one row per category with colored accent + segmented progress bar + ratio.
9. **Session arc timeline** — N SVG segments, status-colored, model dot below each, click jumps.
10. **Session Plan section** — every session as a card with: step indicator ("Session 3 of 18"), title, status pill, model chip, time-effort + reasoning chips, plain-English human summary, deliverable callout, items in scope, dispatch row (subagent type / dependencies / parallel group / checkpoint), why-this-model rationale, action bar showing the `/plan-execute … --session sNN` command, collapsible agent spec pointing to the per-session prompt + context bundle, notes.
11. **Category sections** — one per category with item cards in the same dual-layer pattern.
12. **Floating progress pill** — bottom-right; appears once you scroll past the dashboard; shows overall % done + quick link to the next session.
13. **Light + dark theme** — auto via `prefers-color-scheme` + manual cycle button.

## When to invoke

- "I need a plan for X" / "Build a plan to do Y" / "Create a roadmap for Z"
- "Help me organize this body of work into sessions"
- After running an analysis/audit that produced a long list of action items
- After a `/design:design-critique` or similar produced ~15+ items the user wants to execute

If the user has fewer than 6-8 actionable items, this skill is overkill — suggest a simpler list instead.

## Workflow

### 1. Capture the plan structure

Before deriving any repo grounding for the interview (stack, layout, conventions — e.g. to draft `touches`, session prompts, or `why_model`), run `python3 ~/.claude/scripts/repo-profile-cache.py get`: on `HIT` reuse the cached profile instead of re-reading manifests; on `MISS` derive as usual and `put` the derived profile (five keys: `stack`/`dependencies`/`topology`/`conventions`/`vocabulary`) so the next run reuses it; on `NO-CACHE`, script missing, or any error, derive as usual — the cache is never a correctness dependency. *(Added 2026-07-09, source: everyinc/compound-engineering-plugin gap review.)*

Interview the user. Get enough to produce a useful first draft; don't drill on every field. Ask about:

- **Title** — one line, e.g., "Q4 Product Launch Plan"
- **Subtitle / one-line goal** — what this plan achieves
- **Narrative** (optional) — 1-2 sentence explanation of WHY this plan exists; renders inside the Plan Achievement section
- **Categories** — 3-7 logical groupings of work (e.g., "Quick Wins", "Defence on Truth", "Skill Updates")
- **Items** — the actual action items
- **Prior-art pass (per item, before drafting sessions)** *(2026-08-12, RS-01)* — once items exist but BEFORE batching them into sessions, run one bounded research pass per major item: does a proven solution already cover this scope? Use the SAME tiered detection order plan-harden's Fork B uses (`~/.claude/commands/references/plan-harden/fork-prompts.md` → "Fork B — external research"): Perplexity → Exa (web) → Exa (deep) → Ref, first hit wins, no redundant tier calls. This is a decision input, not a literature review — one question per item, capped findings, don't research the plan's whole domain. Record the outcome on the item as `prior_art: {decision: "adopt"|"adapt"|"build", source, note}` — `adopt` (an existing tool covers it as-is), `adapt` (covers most of it, note what's still custom), or `build` (nothing credible covers it, or nothing fits — **a `build` call made despite a credible alternative surfacing requires `note` to say why**). Degrade honestly instead of guessing or silently skipping — never leave the field simply absent. Two honest degrades, and **the builder tells them apart for itself**: `research_status: "skipped"` + a `research_reason` when YOU chose not to run the pass, or `research_status: "unavailable"` when the tooling was genuinely not there. Don't reach for `"unavailable"` to save a step: at build time `validate_spec` runs its own configuration probe and **refuses the spec** if it can see research capability configured here, naming what it found — and on a stock install the built-in `WebSearch`/`WebFetch` alone make that verdict *available*. The probe reads configuration only (MCP server names + permission deny-lists); it proves "configured and permitted", not "reachable". Either way the probe's record is stamped into the built plan as `research_env`, so the skip is recorded by the system and not only asserted by you. `build_plan.py` enforces that every item carries one shape or the other; see `references/schemas.md` → "Prior-art decision" for the full field reference and the validation rule.
- **Sessions** — how items will be batched into execution sessions, plus each session's `dispatch` block (subagent type, dependencies, parallel grouping, human checkpoints) so `/plan-execute` can run it. **Slice sessions vertically**: each session cuts a narrow but complete path through every layer it touches and leaves something demoable or checkable behind — that's what gives its `verify` block teeth — not one layer at a time ("all the schemas", then "all the endpoints"). The exception is a wide mechanical refactor (rename a shared symbol, retype a column) whose blast radius spans the codebase: sequence it **expand → migrate in batches → contract**, each batch its own session, so every session lands green. *(2026-08-01, from mattpocock/skills to-tickets.)*
- **Parallelism — ask it as a question, not as an afterthought** *(2026-08-12, PL-03)* — once the sessions exist, ask the user plainly: **"which of these could run at the same time, if they couldn't conflict?"** Then design for the answer instead of discovering it later:
  - **Freeze the shared interface first.** If two sessions both need a schema, an API shape, a config format or a file layout, put an early **sequential** session that decides and freezes it, and make the consumers `depends_on` it. Contract first, fan-out second — a group whose members are each still inventing the interface produces three incompatible halves that merge cleanly and don't work.
  - **Split by files, not by topic.** Sessions belong in one `parallel_group` when their **written paths are disjoint**. Overlapping writes are refused on a shared tree (contract M5) and are a merge cost even under isolation.
  - **Name the integration point.** Group members are barred from committing (M1) — shipping is a whole-group act. For a **worktree-isolated** group `build_plan.py` writes the integration session for you (merge producer-first, re-run the union of the members' gates, commit); for a shared-tree group, say which later session absorbs the group's work.
  - **Ask for `touches` on every write-heavy item** — a comma-separated list of **paths**, not prose ("`skills/plan-builder/scripts/build_plan.py, skills/plan-builder/references/schemas.md`"). This is the input the conflict matrix is computed from at build AND dispatch time: an item with no `touches` makes its session ineligible for a group (refused, contract M2a), and a `touches` written as prose is conservatively treated as conflicting with everything.
  - **A plan that ends up a pure chain must say why.** `build_plan.py` warns when every dependency layer is one session wide, and names the file-disjoint pairs that could have been grouped. If the chain is real (one hand on one interface, an ordered migration, a spike whose result reshapes the next session), record it in top-level **`serial_reason`** and the warning goes quiet. Don't silence it by inventing a group — a wrong group is worse than an honest chain.

  Full field reference — `dispatch.isolation`, `dispatch.integrates_group`, `serial_reason`, and what is refused where: `references/schemas.md` → "Parallel groups"; the frozen contract itself: `../plan-execute/references/parallel-group-contract.md`.
- **Open decisions — apply the fog test before decomposing** *(2026-08-01, wayfinder-derived)* — for every session, ask: does its prompt hinge on a decision nobody has made yet? The test for each such question is whether it **can be stated precisely now — not whether it can be answered now**. Statable → either resolve it in the interview, or author an explicit early **decision session** (research / throwaway-prototype / grilling shaped, sized to answer the question and nothing more) that `depends_on`-blocks every session consuming the answer; when the decision is a genuine judgment call, give that session a human checkpoint — auto-accept is for critiquing decisions already made, never for making them. Not statable yet → record it in top-level `open_questions` with what would sharpen it, instead of pre-slicing the fog into guessed sessions. **Never leave an unmade decision embedded in a build session's prompt** — that's a guess dressed as a task, surfacing weeks later as a BLOCKED session or a silent deviation; plan-harden's `decision-debt` lint flags exactly this.
- **Out of scope** — ask "what is this plan consciously NOT doing?" and record it in top-level `out_of_scope`. Entries never graduate into sessions (redrawing the goal means a fresh plan), and the closing acceptance review checks the boundary held. Scope creep hides precisely in what a plan doesn't mention.
<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
<!-- canonical source: ~/.claude/model-routing.yaml (task_classes). The rubric below is steering prose, not the authority; see references/schemas.md → "Model + reasoning rubric" for the full table + the SSOT pointer. -->
- **Model + reasoning tier per session** — pick `model` (`Haiku`/`Sonnet`/`Opus`/`Fable` — or a Codex token `gpt-5.6-sol`/`gpt-5.6-terra`/`gpt-5.6-luna` for a session that should dispatch under `/plan-execute --harness codex`), `reasoning` effort (`low`/`medium`/`high`/`xhigh`/`max`), and a time `effort` for each session. Steering principle (as of 2026-07-25): mechanical work → Haiku/low; standard build → Sonnet/medium; light integration → Sonnet/high; hard multi-file/agentic/non-obvious-debug → **Opus/high**; architecture/security-sensitive/hard-root-cause → **Opus/medium**; linchpin → **Opus/high** (Opus 5 dominates Fable at every rung on our own calibration run, at half the $/MTok — Fable is the escalation apex only; Opus's ladder STOPS at `high`: `xhigh` dead rung, `max` operator-elected). This is Anthropic's own `opusplan` pattern (design → Opus, execution → Sonnet). Keep `effort` (wall-clock) separate from `reasoning` (depth); don't default everything to Sonnet/medium. **On the Codex lane specifically** (2026-07-28, DeepSWE-backed): mechanical AND standard build → `gpt-5.6-luna`/`max` ($0.61, 67% — luna collapses to 44%/11% below `max`, never pair it with anything else; standard build moved off terra at v1.14 on price, with `gpt-5.6-terra`/`max` ($3.96, 70%) as its escalation); agentic/deep-reasoning → `gpt-5.6-sol`/`xhigh` ($4.70, 71%); linchpin → `gpt-5.6-sol`/`max` ($8.39, 73%). **Full rubric with the benchmark evidence and the escalation ladder: `references/schemas.md` → "Model + reasoning rubric" / "Codex rubric"** (that file is the authority — read it before drafting `why_model`, don't rely on memory of this summary).
- **Task class (optional, s07 EXE-01/DSP-04)** — ask which SSOT `task_class`
  (`mechanical|standard_build|agentic_build|deep_reasoning|linchpin`) each session
  belongs to, OFFERING the rubric classes above (mechanical/standard_build map
  fairly mechanically from the model+reasoning answer just given; agentic_build vs.
  deep_reasoning vs. linchpin is a real judgment call the user should make, not you).
  **Do NOT silently infer `task_class` from `why_model` prose** — infer only where the
  mapping is deterministic (e.g. the user already said "mechanical rename sweep" in
  plain language), otherwise leave it absent and note in the plan that it's unset.
  Absent is a legitimate answer, not a gap to fill by guessing: an unset `task_class`
  falls back to the session's explicit `model` field everywhere it's consumed
  (`/plan-execute`'s `executor_policy`, plan-harden's `task-class-model-mismatch`
  lint) — never an error. Carried into `manifest.json` as `task_class` (schemas.md).
- **Codex-shell posture (ask whenever the plan might be run from Codex)** — "does any session write outside the repo, need live network, or shell out to another agent/CLI?" A `codex exec` dispatch runs `--sandbox workspace-write`, which is MEASURED to deny all three, so such a session needs a `dispatch.codex_shell` declaration — without it a `--harness codex` run dispatches it and fails it *after* paying for it. Steer: artifacts outside the repo → `writable_roots: ["~/.dyno"]`; live API / price / clone work → `network: true`; a session that itself invokes `codex exec` / `claude -p` / a vendor CLI → `sandbox: "danger-full-access"`, which is legal ONLY on a session a human already gates (`guards_irreversible`, `requires_human_checkpoint`, `linchpin`) because it hands that session an **unsandboxed** shell. Omit the block entirely for repo-only work — absent is today's behaviour, byte for byte. Field reference: `references/schemas.md` → `codex_shell`; contract: `../plan-execute/references/dual-harness-contract.md` § 4.5.
- **Verification posture** — ask "how do we KNOW each session is really done?" For any session whose `deliverable` is checkable, draft a `verify` block: gates that must pass before the session counts as `DONE` (tests for code, `/eval --smoke` for search/scoring changes, `code-review-gate` for ship-ready work). Default `on_fail: rework` so a failed gate re-dispatches the session with the failure attached (bounded by `max_rework`). This is the single highest-leverage move for "don't ship trash" and for unattended runs — without it, `DONE` is just the subagent's say-so. Gates resolve in the project's `.claude/eval-gates.json` (or the skill-bundled defaults). For a session where a metric could move without the mechanism actually engaging (a flag flip, a scoring tweak, a "fix" whose log event must fire), also set `require_evidence: true` — `DONE` is then refused until the closeout carries `evidence` paths that exist and are non-empty (it may stand alone without `gates`). See "Verification & autonomy" below + `references/schemas.md`.
  **Then propose an LLM review level per ship-ready session, from its `task_class`** (s10 RV-02): `mechanical`/`standard_build` → `llm-review-low`; `agentic_build` → `llm-review-medium`; `deep_reasoning`/`linchpin` → `llm-review-high`. These are argv gates that run a headless `/code-review` over the session's working diff in a fresh context — small diffs reviewed as they land, which is where review actually catches bugs. **Propose, never silently auto-fill:** say which level you'd add and why (the task class), and let the author confirm, drop it, or move a level. Only on sessions that actually SHIP code — a research/spike/decision session gets none. It **stacks with** `adversarial-review` where `peer_triggers` are declared (different reviewers, different question) and is **distinct from `code-review-gate`**, which is the deterministic test/lint gate. Full table + rationale: `references/schemas.md` → "LLM review level by task class".
- **Plan-level acceptance criteria + the closing review** — ask "when the whole plan is finished, how do we know it *achieved* what it set out to?" Get **3–7 checkable statements** (each one either true or false about the finished system — "auth works" is not checkable; "every API route rejects an unauthenticated request, proven by a test" is). Then scaffold a **closing acceptance-review session** that validates them: a fresh isolated agent (`subagent_type: null`), depending on every other terminal session, `task_class: deep_reasoning`, `require_evidence: true`, with the criteria verbatim in its prompt — it verifies each criterion against the repo/system itself (not against the closeouts' claims), reads every `_closeouts/*.json` for accumulated `deviations`/blocked/deferred work, and returns per-criterion ACHIEVED/GAP with evidence. This is the plan's validation half: session `verify` gates check the parts, and until this session existed nothing checked the whole — a plan where every session closed `DONE` can still miss its objectives through accumulated deviations and quiet descoping. `build_plan.py` warns on a ≥4-session plan without one, and plan-harden flags it. **Full contract + the authoring template (including why the checkpoint is verdict-conditional): `references/schemas.md` → "Closing acceptance review"** — read it before drafting the session.
- **Autonomy posture** — ask "where MUST a human look, vs. where can a gate decide?" Make a checkpoint `requires_human_checkpoint: true` only for genuine human-judgment / irreversible boundaries (prod deploy sign-off, public-facing wording, one-shot adjudication). Everywhere else, prefer an automated `verify` gate — that's what lets the operator run `/plan-execute --auto` and walk away. The more verification you author, the fewer human halts the plan needs.
  **Every human gate must ship its decision brief.** `requires_human_checkpoint: true` requires a companion `dispatch.checkpoint` object — `reason` (why a human must look: what is irreversible or judgment-laden here, in plain language a reader with no context can follow) and `decision` (the specific question the human answers when the plan parks), optionally `options` (the 2-4 concrete answers). `build_plan.py` refuses to build a gate without it. **The litmus test: if you and the user cannot name what would make the human answer anything other than "proceed", the gate has no decision — don't create it.** Use a `verify` gate for anything checkable, or `checkpoint_policy: "notify-and-continue"` for a pure FYI. Draft the brief yourself from the session's deliverable and let the user refine — never park a plan on a gate whose only content is "review this".
- **Shipping posture** — ask whether sessions should end with version-control / deploy actions: "should any session commit, push, open a PR, or deploy when it finishes?" If yes, draft a `post_session` block (or a phase-level `phase_closer`). Default to nothing (opt-in). Steer by session kind — ship-ready → `commit-push`; spike/research → `commit` or none. Deploy fields require a project `.claude/deploy-targets.json` entry. See "Shipping actions" below + `references/schemas.md`.
- **Completion ping (optional)** — for plans meant to run unattended, offer a top-level `notify_on_complete: {"command": "<script>"}` (symmetric with `notify_on_halt`) so the operator gets pinged when the run finishes. See `references/schemas.md`.
- **Infographic template** — pick one of: `phase-journey`, `maturity-ladder`, `hub-spoke`, `before-after`, `pillars`, or `custom`. See `references/infographic-templates.md`.

Use AskUserQuestion for choices (template selection, infographic type) and free-form for content. Don't ask 30 questions — group into a few covering the key decisions, then iterate.

#### Dual-layer authoring — the highest-leverage move

For each item and each session, draft **two** pieces of text:

- `human_summary` — one or two sentences in plain English, the voice you'd use explaining the work to a peer in the hallway. No acronyms, no file paths, no dev steps. This is the most prominent text on the card. Aim for "what does this mean for the business / for the user" rather than "what files get touched."
- `deliverable` — one sentence describing the concrete outcome. "When done, X exists / Y is verifiable / Z is true." Rendered as a green-tinted callout. Makes progress legible at a glance.

The user almost never volunteers these. Draft both from the title + description, show the draft, let them refine.

The technical details (`description`, `agent_instructions`, `schema`, `mockup`, `code`, `touches`) go into the **agent-spec** layer — a collapsible details block that's still in the DOM, so a Claude opening the file sees everything, but the human view stays clean.

**Lead with what's likely to change.** When drafting session prompts and ordering the plan structure, put the decisions a reviewer is most likely to want changed first — data-model choices, type interfaces, user-facing behavior — and bury mechanical work (renames, boilerplate, wiring) at the bottom, since attention is scarcest at the top. Flag each such judgment call with `tweak_likelihood` + `alternatives` (see `references/schemas.md`) so it surfaces in the plan's Decision hotspots section instead of disappearing into execution order.

When to add a `schema`, `mockup`, or `code` block:

- **`schema`** — when an item involves a data shape, API contract, or DB schema. JSON, SQL DDL, GraphQL, TS interface — anything an executing Claude will want to match exactly.
- **`mockup`** — when there's a UX surface or visual layout. Inline SVG is best; ASCII works for terminal output / data tables.
- **`code`** — when a small code excerpt clarifies the expected shape (a function signature, a config snippet, a usage example).

If none of those add clarity, leave them out — empty blocks aren't helpful.

### 2. Build the JSON spec

Compile the user's answers into a JSON spec. See `references/schemas.md` for the full schema. Save to a temp file (e.g., `/tmp/plan-spec.json`) so the build script can read it.

For sessions, the `prompt` field is the **task body** the dispatched subagent receives. Help the user write good prompts — specific instructions, pre-session decisions called out. Keep them 150-300 words. `build_plan.py` writes each prompt to `sessions/<id>.prompt.md`, wrapping it with the item scope, a pointer to the per-session context bundle, and the closeout contract — the user only writes the task body, never closeout instructions.

Add a `dispatch` block to each session: `subagent_type` (which agent type runs it, or `null` for a fork), `depends_on` (session IDs that must finish first), `parallel_group` (sessions that can run concurrently), and `requires_human_checkpoint` (halt for review before this session — requires the `checkpoint` decision brief; see "Autonomy posture" above). See `references/schemas.md` and `references/examples.md` for the dispatch patterns.

Stamp every new spec with a top-level `"plan_schema_version": 6` — that is the opt-in that turns on the per-item prior-art requirement and the parallel-group contract at build time. And when the plan is a deliberate chain, add top-level `serial_reason`.

Optionally add a top-level `notify_on_halt: {"command": "<string>"}` — an opt-in hook `/plan-execute` runs (best-effort, `shell=False`, ~10s timeout, with `PLAN_DIR`/`PLAN_TITLE`/`HALT_SESSION`/`HALT_REASON` env vars) when it halts the plan. Point it at any project script — e.g. one that posts to Slack, sends a desktop notification, or pings a webhook. See `references/schemas.md`.

#### Verification & autonomy — gate what "done" means

The plan only runs unattended safely if "done" is *checked*, not *claimed*. Two
first-class, opt-in levers, both resolved into the manifest at build time:

- **`verify` block (per session or phase).** An ordered list of gate ids that must
  pass before a self-reported `DONE` becomes `DONE`. `/plan-execute` runs them at
  the apply boundary, BEFORE shipping; a failure re-dispatches the session with the
  gate output attached (`on_fail: rework`, bounded by `max_rework`) or halts
  (`on_fail: halt`). Gates resolve through the project's `.claude/eval-gates.json`
  merged over the skill-bundled defaults (`eval-smoke-baseline`, `code-review-gate`)
  — an unknown gate is a **build-time error**. Steer by deliverable: code → a test
  gate; search/scoring/traversal → `/eval --smoke`; ship-ready → add
  `code-review-gate` (the "vetted before finishing" loop). The skill *steers*; it
  never silently auto-fills verify.
- **Autonomy = verify gates vs. human checkpoints.** A session's post-work pause is
  either a `verify` gate (an automated check the loop clears on pass) or a human
  checkpoint (`requires_human_checkpoint` / a closeout `human_checkpoint_reason` —
  the loop always stops). Reserve human checkpoints for genuine judgment /
  irreversible boundaries; make everything else a verify gate. Every human
  checkpoint carries its mandatory `checkpoint` decision brief (reason /
  decision / options — build-time enforced; see "Autonomy posture"), so the
  operator who hits the gate weeks later knows exactly what they are deciding
  and why. That is exactly what
  lets the operator run `/plan-execute --auto` and walk away: `--auto` self-drives
  through verify + bounded rework but NEVER overrides a human checkpoint.

- **Closing acceptance review (`acceptance_review: true`).** The third lever, and the
  only one that is plan-level. Both levers above are per-session: they answer "did THIS
  session finish?" Neither answers "did the PLAN achieve its objectives?" — so author a
  final session, isolated from the orchestrator, that checks the plan's acceptance
  criteria against reality and returns per-criterion ACHIEVED/GAP. It parks the plan for
  a human decision **only when it finds a gap**. Contract + template:
  `references/schemas.md` → "Closing acceptance review".

Order at runtime after a closeout: **human checkpoint ▸ verify ▸ shipping** — so a
human only reviews work that already passed its gates, and nothing ships unverified.
See `../plan-execute/references/verify-gates.md` and `references/schemas.md`.

#### Shipping actions — declare version-control + deploy posture

Shipping discipline deserves the same first-class treatment as model selection. Give each session (or a phase) a `post_session` / `phase_closer` block so `/plan-execute` commits, pushes, opens a PR, runs gates, and deploys automatically at the right boundary — instead of leaving "did we commit / deploy?" to operator memory (dangerous in example-project, where EC2 can be deployed-but-uncommitted and silently reverted by a sibling `/deploy-update`).

- **Four `git` values:** `none` / `commit` / `commit-push` / `commit-push-pr`. **Four-value enum, opt-in by default.**
- **Phase defaults + per-session override:** put a `phase_closer` on a top-level `phases[]` entry, set `"phase": "pN"` on sessions to inherit, override per key with a session `post_session`.
- **Steer the default by session KIND, not blanket:** ship-ready sessions → `git: commit-push` (per `feedback_push_frequently_avoid_peer_drift.md` — batched local commits get erased by siblings resetting against remote). Spike / research sessions (plan-builder's canonical "Auth migration spike") → `git: commit` or `none`; pushing throwaway commits pollutes shared history and trips CI. Set `kind: ship-ready | spike` to make the intent explicit. **The skill steers; it never auto-fills shipping.**
- **Project-detection rule (deploy fields):** a `deploy` target is only legal if it resolves in the project's `.claude/deploy-targets.json`, and a `pre_deploy_gate` only if it resolves in `.claude/eval-gates.json` (or the skill-bundled `eval-smoke-baseline` default). `build_plan.py` validates this **before writing manifest.json** — an unknown target/gate is a build error. It also runs the adapter capability-probe so a `git` value whose underlying skill flag drifted (e.g. `/commit-orchestrate` renames `--push-after`) fails loud at build time. See `../plan-execute/references/shipping-registries.md`.
- **`rollback_hint` is mandatory** for any deploy-bearing session.
- **Preview the destructive surface before building:** `python build_plan.py <spec.json> --dry-run-shipping [--register-in <root>]` prints every commit/push/PR/deploy/gate action declared across all sessions — and writes nothing. Use it so the operator sees the destructive surface at authoring time, not at execution.

See `references/schemas.md` → "Shipping block" + "phase_closer" for the full field reference and the ADR-027 lineage.

### 3. Run the build script

```bash
python /path/to/skill/scripts/build_plan.py <spec.json> [<plan-dir>] [--register-in <project-root>]
```

The script reads the spec, validates it (IDs, dependency graph, dispatch fields), **validates the rendered dashboard's inline JavaScript** (see below), then emits a **plan directory** (not a single file):

```
<plan-dir>/
├── PLAN.html              # canonical dashboard + per-item/session state
├── spec.json              # authoring input (kept for rebuilds)
├── manifest.json          # immutable dispatch graph
├── run_state.json         # mutable runtime state (halt, last batch)
├── _closeouts/            # write-ahead log written during execution
├── sessions/
│   ├── s01.prompt.md      # subagent invocation prompt
│   ├── s01.context.md     # per-session agent-spec bundle
│   └── ...
├── run.ndjson             # event log (appended during execution)
└── _changelog.ndjson      # mid-run plan amendments (rendered as "Plan changes")
```

Plans are stamped `plan_schema_version: 6` (`build_plan.PLAN_SCHEMA_VERSION`).
`/plan-execute` accepts `>= 2`; the v3-only features — `plan_impact` closeouts +
the REPLAN checkpoint, and the rendered **Plan changes** section at the bottom of
the page — simply do not apply to a plan built before the bump, so an older plan
never gains a new refusal mode retroactively. v4 (2026-08-12) adds the mandatory
prior-art decision per item (`prior_art`, or `research_status: "skipped"` /
`"unavailable"` — the latter machine-checked against a build-time configuration
probe; enforced by `validate_spec`, see "Prior-art decision" in `references/schemas.md`);
existing specs need a one-time update to add it before their next build. v5
(2026-08-12) makes a `decision_brief` mandatory on a BLOCKED closeout — enforced
at runtime off the built MANIFEST's stamp, so it needs nothing from the spec. v6
(2026-08-13) turns on **upward escalation**: a session whose verify gate keeps
failing at the SAME root cause is re-dispatched one rung UP the SSOT's ladder
(`sonnet@high → opus@high → fable@medium → fable@high → fable@xhigh`) instead of
on the same rung forever. Also runtime-only, off the manifest's stamp; a session
opts out with `"escalation": false`. The same version accepts the optional
`routing_experiment: {kind, proposal_id}` session tag, which the builder validates
and carries into the manifest for the routing outcome ledger to read back.

A spec that stamps its own top-level `"plan_schema_version": 5`-or-higher also opts in to
build-time enforcement of the **frozen parallel-group contract** (no member
ships, no member touches a lockfile, every member item declares `touches`, no two
members write the same path, one integration session per isolated group). A spec
without the stamp gets the same findings as warnings, so the mutation engine
keeps working on plans written before the contract existed. See
`references/schemas.md` → "Parallel groups".

If `<plan-dir>` is omitted it defaults to `./_plans/<slug>-<today>/`. A legacy `*.html` output path still works (PLAN.html lands beside the other files in the parent dir). Use `--rebuild` to regenerate over an existing directory.

**Rebuilding a plan that has already run.** Per-session/item status lives ONLY in PLAN.html's `data-status` attributes — `/plan-execute`'s dispatcher and its structural DONE-gate both read them there — and a rebuild re-renders every article at `TODO`. So `--rebuild` over an executed plan is **refused** unless you pass `--preserve-state`, which carries the recorded state forward: each article's status, its accumulated closeout notes, its `data-updated` stamp and its shipping badge, plus `run_state.json`. Sessions or items the rebuild **adds** start at `TODO`; ones it **removes** stay gone. To deliberately restart a plan from scratch, delete `PLAN.html` first. (Before 2026-07-28 a rebuild silently reset every status — destroying dispatch state, not just badges. The carry-over lives in `../plan-execute/scripts/article_block.py` next to the writers whose fields it must preserve.)

**Dashboard JS guard.** The dashboard's status nav (session strip, donut rings, counters, progress pill) is repainted on every page load by the inline `<script>` reading each `<article>`'s `data-status`. A single runtime `ReferenceError` in a render function aborts that repaint mid-run and **silently freezes the nav** at its authored fallback statuses — the page still loads and *looks* valid (this is exactly how a `isOpus is not defined` typo shipped on 2026-06-06). `node --check` cannot catch it (runtime error, not syntax), so `build_plan.py` runs ESLint `no-undef` over the assembled inline script (`scripts/dashboard-eslint.config.mjs`) **before writing PLAN.html** and aborts the build on any undefined identifier. The check degrades gracefully (warn + skip) if Node/ESLint aren't installed. If it fails with a legitimate *new* browser or injected-data global, add it to the config's allowlist; otherwise it's a real bug in `assets/base-template.html` or `assets/infographics/*.js`.

### 3b. Optionally harden the plan (`--harden`)

If the user passed **`--harden`** (or asked to "harden", "stress-test", or "pressure-test"
the plan), the build hands straight off to the hardener — **automatically, with no user
prompt**. Because `/plan-harden` is a slash command and this skill stays a **pure builder
that does NOT hold `SlashCommand`** (the same permission-minimal rule the epic-dev conductor
follows — never widen a startup-loaded skill's grant), the chain runs at the **orchestrator
level**: finish the build, then your immediate next action for this request is to invoke

```
/plan-harden <plan-dir>
```

`/plan-harden` runs its adversarial pre-flight (parallel enrichment → `/grill-with-docs` +
`/adversarial-review` → Klein premortem → severity-tagged synthesis), applies the surviving
hardenings into the session prompts, **backports them into `spec.json`** and rebuilds (its
§4.2b step) so a later `--rebuild` can't silently wipe them. The plan is then red-teamed
before its first session ever runs.

- **Opt-in by design.** Hardening is heavy (the Codex verify loop dominates, ~50–200k
  tokens). Only chain it when `--harden` is present — never automatically on every build.
- **`--harden --quick`** forwards `--quick` to `/plan-harden` (skips enrichment + premortem,
  keeps the adversarial verify loop) for a lighter pass.
- **Automatic, never asked.** Run the chain straight through; do not stop to confirm the
  hand-off. (Genuine human checkpoints still live *inside* `/plan-harden` — e.g. when it
  surfaces a plan-killer for sign-off — and those still stop, by design.)
- After `/plan-harden` returns, continue to step 4 and report the plan as **hardened**
  (revision type + any plan-killers addressed, from `/plan-harden`'s output).

**End-to-end flow this unlocks:** `/plan-builder … --harden` → (auto) `/plan-harden` →
`/plan-execute <plan-dir>` — build, red-team, and run as one intent.

### 4. Save and register the plan

A plan only matters if Claude can find it three months later. Layers:

**Layer 1 — Default location.** `_plans/<slug>-<YYYY-MM-DD>/` under the project root (the script's default when no output path is given).

**Layer 2 — Project index + .gitignore.** Pass `--register-in <project-root>`: the script updates `<project-root>/_plans_index.md` with a `/plan-execute` run command, and amends `.gitignore` so runtime state (`.lock`, `_closeouts/`, `run.ndjson`, `run_state.json`, `HALT_NOTICE.txt`) stays local while `PLAN.html`, `manifest.json`, and `sessions/*` are committed.

```bash
python build_plan.py spec.json --register-in /path/to/project
```

**Layer 3 — Pointer in CLAUDE.md.** `--register-in` prints a one-line snippet (including the `/plan-execute <dir>` command) to paste into the project's `CLAUDE.md`.

After building, respond to the user with:
- The path to `PLAN.html` (open in a browser to view)
- The `/plan-execute <plan-dir>` command to run it
- The CLAUDE.md / index snippet (or offer to add it yourself)
- Counts: sessions, items, categories; the infographic template; the first session

### 5. Running the plan

The plan auto-executes via the **`/plan-execute`** skill — no copy-paste. The user runs:

- `/plan-execute <plan-dir>` — run the loop until a checkpoint, blocker, or completion
- `/plan-execute <plan-dir> --auto` — run unattended to completion, halting only on human checkpoints, hard blockers, and stale-deploy confirms (verify gates clear themselves)
- `/plan-execute <plan-dir> --session sNN` — dispatch one named session
- `/plan-execute <plan-dir> --resume` — continue past an `AWAITS_REVIEW` human checkpoint
- `/plan-execute <plan-dir> --status` — read-only state dump

`/plan-execute` reads `manifest.json`, finds the next ready session(s), dispatches a subagent per session via the Task tool (running parallel-group members concurrently), parses each subagent's `<plan-execute-closeout>` block, and atomically rewrites the affected `<article>` blocks in `PLAN.html`. See the `plan-execute` skill for the full contract.

## Operating principles

### The dual-layer principle

The output is two documents stitched into one:
- A **human-readable plan** — what the work means, what each session achieves, where you are.
- An **agent-runnable spec** — full prompts, schemas, mockups, code excerpts, edit instructions.

The human layer is prominent (serif type, callouts, generous whitespace). The agent layer lives inside `<details class="agent-spec">` blocks — visually quiet but always in the DOM, so any Claude that opens the file sees everything regardless of `open` state. This is what lets the file serve both audiences without compromising either.

When drafting cards, write the `human_summary` and `deliverable` as if your reader has never seen the source spec. If the human layer can't stand on its own, the plan won't survive a quarter on the shelf.

### Why the Operating Manual matters

The HTML embeds an Operating Manual at the top of `<main>` describing how the plan runs (`/plan-execute`), the runner-owned mutation surface (the `<!-- ARTICLE:<id> -->`-bounded blocks), what auto-recomputes (donuts, arc, categories, counters, Up Next, floating pill), and the 8 status semantics. The build script handles it; don't remove or restructure.

### Why comment anchors matter

Every `<article>` is wrapped in `<!-- ARTICLE:<id>:BEGIN -->` / `:END -->` comment anchors placed *between* elements (HTML5 forbids comments inside start tags). These give the orchestrator a unique, collision-proof target for an atomic whole-block replacement — even when many TODO items share the same date and status. Don't move anchors inside the `<article>` start tag or remove them; `/plan-execute` depends on exactly one BEGIN/END pair per id.

### Dashboard review hooks + layout self-audit

Every session/item `<article>` carries a stable `data-session-id` / `data-item-id`,
so `PLAN.html` is a first-class in-browser annotation target: a human can open it,
click the **exact** card, and `/plan-execute` receives element-anchored review
feedback instead of a re-typed chat round-trip. `PLAN.html` also ships a tiny
isolated `<script data-layout-audit>` (+ a hidden `#layout-audit-banner`) that, once
fonts + layout settle, flags real horizontal overflow / clipped text and raises a
banner — catching the visual breakage a static lint can't see. Both are automatic;
neither needs a spec field. The build script handles them; don't remove them.

### Why state is split

`PLAN.html` is canonical for per-item/session status (`data-status`, `data-updated`, notes). `manifest.json` is the **immutable** dispatch graph — regenerated only on rebuild. `run_state.json` holds **mutable** runtime state (halt flag, last batch) and is gitignored. `_closeouts/<sid>.json` is a write-ahead log written before any HTML edit so a mid-edit crash is recoverable. Keep these roles separate — **NEVER put mutable runtime state in manifest.json.**

### Why subagents never edit the plan

Dispatched subagents do project work and return a `<plan-execute-closeout>` JSON block — the only channel through which they influence plan state. The orchestrator (main conversation) does every write to PLAN.html. This prevents a crashed subagent from corrupting state and keeps mutation idempotent.

### Be helpful with prompt + dispatch drafting

When interviewing, the user might say "S03 covers items DT-01 and DT-02; just bundle them." Don't insist on full prompt text — offer to draft it from the item descriptions. Same for `human_summary`, `deliverable`, `why_model`, the `dispatch` block (suggest sensible `subagent_type`, `depends_on`, and parallel grouping from the work's shape), and the `verify` block (suggest gates from the deliverable — tests, `/eval --smoke`, a review gate). Show the draft, let them refine.

**`subagent_type` steering.** Default to a **fresh agent** — leave `subagent_type` `null` (a fresh `general-purpose`) or name a fresh typed specialist (`Explore` for read-only investigation, `Plan` for design, `code-reviewer`/`epic-implementer` for those roles). A fresh agent gets a clean context and **honors the session's `model`** — right for almost every session. Only use the literal `"fork"` when a session genuinely needs the orchestrator's *live conversation context*; a fork **ignores the per-session `model`** (it runs the orchestrator's), so never pair `"fork"` with a model you care about — `build_plan.py` warns if you do.

Do not design sessions around ultracode/Workflow dispatch, and do not add workflow flags to the spec or manifest. Plan sessions are dispatched by `/plan-execute` (default: the Task tool; it may internally route a parallel batch — or a session's own implement→verify→review sub-pipeline — through one Workflow call; that is its transport decision, invisible to the plan). The plan's load-bearing properties — human checkpoints (`requires_human_checkpoint` → `AWAITS_REVIEW` → `--resume`) and cross-session resume — live outside what a Workflow can host (no mid-run human input, same-session-only resume).

## Plan-achievement infographic templates

Five reusable SVG templates ship with the skill, plus `custom` for bespoke visuals. Pick one based on the plan's archetype:

- **`phase-journey`** — horizontal flow: Now → Phase 1 → Phase 2 → … → Goal. Best for *staged rollouts* (the safe default).
- **`maturity-ladder`** — vertical stairs: each step = a capability level. Best for *capability-building plans*.
- **`hub-spoke`** — center node = goal, spokes = workstreams. Best for *cross-cutting initiatives*.
- **`before-after`** — two cards side-by-side with current-state and target-state bullets. Best for *transformation plans*.
- **`pillars`** — roof = goal, pillars = workstreams (each fills with progress), foundation = current capability. Best for *structural improvements*.
- **`custom`** — user (or Claude) provides raw SVG markup with simple data-binding hooks. Best when none of the structured templates capture the narrative without distortion.

See `references/infographic-templates.md` for the data each template needs and when to recommend it. The build script handles SVG generation for the 5 structured templates — the spec just needs the right shape per template. For `custom`, the SVG itself comes from the spec and the build wires data binding.

## Files and references

- `scripts/build_plan.py` — builds the plan directory from a JSON spec. Run as `python build_plan.py <spec.json> [<plan-dir>] [--register-in <root>] [--rebuild] [--preserve-state]`.
- `scripts/validate_spec.py` — validates a spec before build; surfaces missing fields, invalid IDs, dispatch-graph errors (cycles, dangling deps, parallel-group mismatches), parallel-group contract violations, and a parallelism report (layer width, serialization ratio, file-disjoint pairs).
- `scripts/parallel_shaping_fixtures.py` — the four plant/allow fixtures for the parallelism shaping rules (pure-chain warning, `serial_reason`, auto-emitted integration session, M1 refusal). `test_schema_hardening.py` asserts them; `main()` prints them as an evidence transcript.
- `assets/base-template.html` — the static HTML structure (CSS, JS, head, footer) with marker placeholders that the build script fills, including the `<!-- ARTICLE:<id> -->` anchors and the 8-status CSS/JS.
- `assets/infographics/` — SVG renderers for the 5 templates (loaded by build_plan.py).
- `references/infographic-templates.md` — descriptions of each template, the data it needs, and when to recommend it.
- `references/schemas.md` — full JSON schema for the plan spec, including the dual-layer fields and the `dispatch` block.
- `references/examples.md` — sample specs for different plan archetypes, including dispatch patterns.

To run a built plan, see the **`plan-execute`** skill.

## Output expectations

Final output: a plan directory (`PLAN.html` usually under 250 KB plus manifest.json + sessions/*). PLAN.html opens in any modern browser; Google Fonts loaded over the network but the page works offline with system font fallback. `/plan-execute` reads the directory to auto-run the plan.

If anything is unclear during interview, ask. The user's domain knowledge about their own plan beats your guesses — but don't ask them to author the prompt-block body if a draft from item descriptions would do; offer first. Same for `human_summary`, `deliverable`, `why_model`, and the `dispatch` block.
