---
name: plan-builder
description: Build an executable multi-session plan — interview the user, compile a JSON spec, and emit a plan directory (self-contained PLAN.html dashboard + manifest.json dispatch graph + per-session prompt/context files) that auto-runs via the /plan-execute skill. Use when the user says "create a plan", "build a roadmap", "execution plan", "session plan", "improvement plan", "rollout plan", "project plan dashboard", "multi-session plan", or describes a body of work spanning multiple sessions even without the word "plan". Pass --harden to adversarially stress-test the finished plan with /plan-harden before handing it back. Not for simple to-do lists, and not for running an already-built plan (that's plan-execute).
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion]
effort: high  # plan authoring is judgment-heavy (session decomposition, model/reasoning rubric, dual-layer drafting). Pins the tier explicitly regardless of session default. Verified honored in binary 2.1.170.
---

# Plan Builder (Aurora edition)

**Ponytail does not apply to this task.** This is planning work, not a code change. Ignore the ponytail plugin's rules here (smallest change, skip what nobody asked for, a short reply): be thorough, cover the whole question, and report in full. Ponytail applies again when you write code. (Owner decision, `docs/worth-adopting/2026-10-19-ponytail-v5.md`.)

Generates a plan directory for any multi-session execution plan: a self-contained `PLAN.html` dashboard (~150–250 KB, dual-layer human + agent voice, opens directly in the browser) plus the `/plan-execute` execution bundle (`manifest.json`, `sessions/*.prompt.md`, `sessions/*.context.md`).

## When to invoke

- "I need a plan for X" / "Build a roadmap for Z" / "Organize this body of work into sessions"
- After an audit or critique produced ~15+ action items the user wants to execute
- "Make a plan to fix the repo-health findings" — read `../repo-health/references/fix-plan.md` first (run steps 1–3 for the live fix list; never retype findings from the dashboard)

If there are fewer than 6–8 actionable items, this skill is overkill — suggest a simpler list instead.

## Workflow

### 1. Interview and capture structure

Check two inputs before interviewing:
- **SPEC.md / PRD:** if the work spans more than one plan, send the owner to `/spec-builder` first. If a `SPEC.md` exists, read this plan's entry (`P<n>`) and draft items with `spec_ref` and `covers` by default (`references/interview-guide.md` → "Starting from SPEC.md or a PRD").
- **Repo profile cache:** run `python3 ~/.claude/scripts/repo-profile-cache.py get` — reuse on `HIT`, derive and `put` on `MISS`; any error is non-fatal.

Use `AskUserQuestion` for bounded choices (infographic template, postures) and free-form for content. Group into a few rounds, draft, then iterate. Cover:

- **Title, subtitle, and `narrative`** — ask *"when this plan succeeds, what happens next, and what would show, after it lands, that it paid off?"* Record in `narrative`; its after-landing measurement becomes one acceptance criterion.
- **Categories (3–7) and items** — draft both `human_summary` (1–2 plain-English sentences, eli5 rules) and `deliverable` (1 concrete outcome sentence) for every item and session; keep technical detail (`description`, `agent_instructions`, `schema`, `mockup`, `code`, `touches`) in the collapsible agent-spec layer. Put high-change decisions first and flag them with `tweak_likelihood` + `alternatives`.
- **Prior-art pass (RS-01, mandatory before batching sessions)** — bounded research per major item (Perplexity → Exa web → Exa deep → Ref, first hit wins). Record `prior_art: {decision: "adopt"|"adapt"|"build", source, note}` or honest degrade (`research_status: "skipped"` + `research_reason`, or `"unavailable"` only when unconfigured — machine-probed at build time). See `references/interview-guide.md` → "Prior-art pass (RS-01)".
- **Sessions, slicing, and parallelism (PL-03)** — slice vertically (exception: wide mechanical refactors use expand → migrate in batches → contract). Ask *"which of these could run at the same time, if they couldn't conflict?"* Freeze shared interfaces first, group only file-disjoint sessions, require path-only `touches` on every item, and record `serial_reason` on a deliberate pure chain.
- **Open decisions (fog test) and prompt hygiene** — resolve statable decisions or author an early decision session; record unstatable ones in `open_questions`; never leave unmade decisions or runnable programs (heredocs ≥10 lines) embedded in build prompts. Name the narrow scope for any stdlib-only grammar reader. Record explicit `out_of_scope` entries.
<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
<!-- canonical source: ~/.claude/model-routing.yaml (task_classes). The rubric below is steering prose, not the authority; see references/schemas.md → "Model + reasoning rubric" for the full table + the SSOT pointer. -->
- **Class, risks, touches, and checks (schema v8)** — the author says what the work is, what is risky, and how it is checked; `/plan-execute begin` picks the model and effort (`../plan-execute/references/route-at-dispatch-contract.md`):
  - **`task_class` (REQUIRED at v8)** — `mechanical|standard_build|agentic_build|deep_reasoning|linchpin`. Offer definitions from `references/model-effort-guidance.md`; let the user make the `agentic_build`/`deep_reasoning`/`linchpin` call (never infer silently from prose).
  - **Risks (`peer_triggers`)** — record risk flags; they raise the override floor.
  - **`touches` on every item** — paths, not prose (`[]` for none).
  - **Checks (`verify.gates`, `verify.locked`, `verify.blocking_rule`)** — lock earlier-slice check files in `verify.locked` (regular files only); ask for 2–3 testable conditions in `verify.blocking_rule` on any LLM/adversarial review gate.
  - **Model is an override, not a question.** Leave `model`/`reasoning` out unless stating an exception (both or neither, with `why_model`; refused below the risk floor for `peer_triggers` or `linchpin`). Keep wall-clock `effort` (`S`/`~2h`/`half day`) separate from reasoning depth.
- **Codex-shell posture** — if the plan may run under Codex and any session writes outside the repo, needs live network, or shells out to another CLI, declare `dispatch.codex_shell` (`writable_roots`, `network`, or human-gated `sandbox: "danger-full-access"`). Omit for repo-only work.
- **Verification posture** — draft `verify` blocks (`on_fail: rework` default) for checkable deliverables. Set `require_evidence: true` where a metric could move without the mechanism engaging — always on bug-fix sessions (requiring before-repro + after-pass evidence). Safety-mechanism sessions author invariant matrix tests in the same session. Propose (never silently auto-fill) an LLM review gate by `task_class`: `mechanical`/`standard_build` → `llm-review-low`, `agentic_build` → `llm-review-medium`, `deep_reasoning`/`linchpin` → `llm-review-high` (use `cross-family-review-<level>` for Claude-token sessions where applicable).
- **Plan-level acceptance criteria + closing review** — capture 3–7 checkable criteria (including the after-landing measurement from `narrative` with command + due date) and owner invariants, then scaffold a final `acceptance_review: true` session (`subagent_type: null`, `task_class: deep_reasoning`, `require_evidence: true`).
- **Autonomy & shipping postures** — reserve `requires_human_checkpoint: true` for genuine judgment/irreversible boundaries and ALWAYS attach `dispatch.checkpoint` (`reason`, `decision`, optional `options`; pass the litmus test). Opt-in `post_session` / `phase_closer` (`git`: `none|commit|commit-push|commit-push-pr`; deploy targets validated against `.claude/deploy-targets.json`; `rollback_hint` mandatory for deploys). Offer optional `notify_on_halt` / `notify_on_complete`.
- **Explainer & infographic** — draft `explainer` (2–5 step causal chain, analogy with `breaks`, ≤3 `terms`, 1-line `recap`) and pick an infographic template (`phase-journey`, `maturity-ladder`, `hub-spoke`, `before-after`, `pillars`, or `custom`).

Full interview rules, dual-layer voice, prompt depth (rawest material, real instrument, independent check), `ui-craft` for UI sessions, and `subagent_type` steering: [references/interview-guide.md](references/interview-guide.md).

### 2. Build the JSON spec

Compile answers into a JSON spec (full schema: [references/schemas.md](references/schemas.md)) and save to a temp file (e.g., `/tmp/plan-spec.json`):

- Stamp every new spec with top-level `"plan_schema_version": 9` (9 = v8 route-at-dispatch + required `owner_call` on BLOCKED `decision_brief`). Add `serial_reason` if the plan is a deliberate chain.
- Write each session's `prompt` as the 150–300 word task body only — `build_plan.py` wraps it with item scope, context bundle pointer, and the closeout contract.
- Include each session's `dispatch` block (`subagent_type`, `depends_on`, `parallel_group`, `requires_human_checkpoint` + `checkpoint`), `verify` block, and optional `post_session` block.
- Runtime precedence after a closeout: **human checkpoint ▸ verify ▸ shipping**.
- Preview destructive shipping actions before building: `python3 skills/plan-builder/scripts/build_plan.py <spec.json> --dry-run-shipping [--register-in <root>]`.

### 3. Run the build script (and optional `--harden`)

```bash
python3 /path/to/skill/scripts/build_plan.py <spec.json> [<plan-dir>] [--register-in <project-root>]
```

- Default output: `./_plans/<slug>-<YYYY-MM-DD>/`.
- Use `--rebuild` to regenerate over an existing directory. Over an **already-executed** plan, `--rebuild` is refused unless you pass `--preserve-state` (carries `data-status`, notes, `data-updated`, shipping badges, and `run_state.json`).
- `build_plan.py` validates the spec and runs ESLint `no-undef` over the assembled inline dashboard JS before writing `PLAN.html`.
- **If `--harden` was passed** (or the user asked to harden/stress-test): finish the build, then immediately invoke `/plan-harden <plan-dir>` (forward `--quick` if `--harden --quick` was passed) without asking for confirmation. `/plan-harden` backports surviving hardenings into `spec.json` and rebuilds.

Directory layout, schema version stamps, `--preserve-state`, JS guard, `--harden` rules, and dashboard architecture: [references/build-and-architecture.md](references/build-and-architecture.md).

### 4. Save, register, and report

Pass `--register-in <project-root>` to update `_plans_index.md`, amend `.gitignore` for runtime state, and print a `CLAUDE.md` pointer snippet.

When `orca status --json` reports `result.app.running: true`, open `PLAN.html` in an Orca tab in the plan repo's own workspace (`orca tab create --url file://<abs PLAN.html> --worktree path:<repo root> --json`) and say it is open.

Report back:
- Path to `PLAN.html` (and whether opened in Orca)
- The `/plan-execute <plan-dir>` command (and hardened status if `--harden` ran)
- The `CLAUDE.md` / index snippet (or offer to add it)
- Counts (sessions, items, categories), infographic template, and the first session

### 5. Running the plan

- `/plan-execute <plan-dir>` — run until checkpoint, blocker, or completion
- `/plan-execute <plan-dir> --auto` — unattended through verify + bounded rework; still halts on human checkpoints, hard blockers, and stale-deploy confirms
- `/plan-execute <plan-dir> --session sNN` — dispatch one named session
- `/plan-execute <plan-dir> --resume` — continue past a pre-dispatch `AWAITS_REVIEW` checkpoint
- `/plan-execute <plan-dir> --status` — read-only state dump

## Infographic templates

| Template | Best for |
|---|---|
| `phase-journey` | Staged rollouts (Now → Phases → Goal; safe default) |
| `maturity-ladder` | Capability-building plans (vertical stairs) |
| `hub-spoke` | Cross-cutting initiatives (goal center + workstream spokes) |
| `before-after` | Transformation plans (side-by-side current vs target) |
| `pillars` | Structural improvements (foundation + workstream pillars + roof) |
| `custom` | Bespoke raw SVG with data-binding hooks |

Details and required data shapes: [references/infographic-templates.md](references/infographic-templates.md).

## Files and references

- `scripts/build_plan.py` — validates spec, checks dashboard JS, emits plan directory (`[--register-in <root>] [--rebuild] [--preserve-state] [--dry-run-shipping]`).
- `scripts/validate_spec.py` — validates IDs, dependency graph, dispatch fields, parallel-group contract, and parallelism report.
- `scripts/parallel_shaping_fixtures.py` — plant/allow fixtures for parallelism shaping rules.
- `assets/base-template.html` & `assets/infographics/` — static Aurora HTML/CSS/JS shell and 5 SVG renderers.
- [references/interview-guide.md](references/interview-guide.md) — deep interview rules, prior-art pass, parallelism shaping, v8 fields, postures, dual-layer voice, prompt depth.
- [references/build-and-architecture.md](references/build-and-architecture.md) — directory layout, schema stamping, `--preserve-state`, JS guard, `--harden`, dashboard anatomy, operating principles.
- [references/schemas.md](references/schemas.md) — full JSON spec schema, model/reasoning/Codex rubrics, verify/shipping/checkpoint blocks, closing acceptance review.
- [references/model-effort-guidance.md](references/model-effort-guidance.md) — task-class definitions, override rules, and historical calibration.
- [references/plan-schema-history.md](references/plan-schema-history.md) — full per-version history of `plan_schema_version` (v2–v9).
- [references/infographic-templates.md](references/infographic-templates.md) — data shapes and selection guide for all 5 templates + `custom`.
- [references/examples.md](references/examples.md) — sample specs per archetype and dispatch patterns.
