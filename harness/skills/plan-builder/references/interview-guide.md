# Plan Builder — Interview & Authoring Guide

Deep interview procedures, authoring rules, and posture guidance for `/plan-builder`. Read the relevant section during step 1 (interview) and step 2 (spec compilation) of `SKILL.md`.

## Contents

- [Starting from SPEC.md or a PRD](#starting-from-specmd-or-a-prd)
- [Repo profile cache](#repo-profile-cache)
- [Narrative and Explainer](#narrative-and-explainer)
- [Prior-art pass (RS-01)](#prior-art-pass-rs-01)
- [Sessions, vertical slicing, and parallelism (PL-03)](#sessions-vertical-slicing-and-parallelism-pl-03)
- [Open decisions, embedded programs, and grammar scopes](#open-decisions-embedded-programs-and-grammar-scopes)
- [Schema v8 — class, risks, touches, checks, and overrides](#schema-v8--class-risks-touches-checks-and-overrides)
- [Codex-shell posture](#codex-shell-posture)
- [Verification posture and LLM review levels](#verification-posture-and-llm-review-levels)
- [Plan-level acceptance criteria and closing review](#plan-level-acceptance-criteria-and-closing-review)
- [Autonomy posture and decision briefs](#autonomy-posture-and-decision-briefs)
- [Shipping posture](#shipping-posture)
- [Dual-layer authoring](#dual-layer-authoring)
- [Prompt depth and subagent_type steering](#prompt-depth-and-subagent_type-steering)

---

## Starting from SPEC.md or a PRD

*(2026-11-20)* If the work is larger than one plan, stop and send the owner to `/spec-builder` first; do not stretch one plan over it.

If a `SPEC.md` exists for this work, read the entry of this plan (its plan line, `P<n>`) and the requirements it covers, and draft the items and their `covers` from them. Write `spec_ref` (`path` and `plan`) and each item's `covers` into the plan spec by default; if you draft from a `SPEC.md` without `spec_ref`, state why in one line of the spec. Reason: `spec_ref` is optional and warn-only, so without this default the coverage warning and the write-back never run. If the owner points to an existing PRD in a repository, read its requirements the same way and keep their ids as the `covers` ids.

## Repo profile cache

Before deriving any repo grounding for the interview (stack, layout, conventions — e.g. to draft `touches`, session prompts, or `why_model`), run:

```bash
python3 ~/.claude/scripts/repo-profile-cache.py get
```

- On `HIT`: reuse the cached profile instead of re-reading manifests.
- On `MISS`: derive as usual and `put` the derived profile (five keys: `stack`, `dependencies`, `topology`, `conventions`, `vocabulary`) so the next run reuses it.
- On `NO-CACHE`, script missing, or any error: derive as usual — the cache is never a correctness dependency. *(Added 2026-07-09, source: everyinc/compound-engineering-plugin gap review.)*

## Narrative and Explainer

### Narrative — the goal beyond the goal

*(Optional field, always asked; 2026-10-18, Rieseberg-derived.)* Ask: *"when this plan succeeds, what happens next, and what would show, after it lands, that it paid off?"*

Record the answer in `narrative` (1–2 sentences: WHY this plan exists and what it should change once done); it renders inside the Plan Achievement section. Measured: 3 of 52 plans filled it, so plans recorded what they build and never what for — one built part (the cross-session findings digest) was removed 2026-10-13 when a later re-measure showed it changed nothing, and no plan step could have caught that. The after-landing measurement from this answer becomes one of the plan-level acceptance criteria.

### Explainer — "The plan, explained"

*(2026-08-24, eli5-derived.)* Once the sessions exist, draft the `explainer` field and offer it on every plan. First read the eli5 skill's rules (`../../eli5/SKILL.md` → "Rules for the page") and apply them to ALL human-layer text you draft (`narrative`, `human_summary`, and `explainer`): pictures carry the explanation, show the causal chain, keep and define real names, say only what is true.

Draft from the sessions you just decomposed:
1. A **causal chain** of 2–5 steps, each mapping to one or more session ids, each with a one-sentence `caption` (what this step changes) and an `unlocks` sentence (what becomes possible when it is done — the causal link to the next step).
2. **One analogy** with the place it breaks — never without `breaks`, the build refuses a half-analogy.
3. At most ~3 `terms` the reader cannot follow the chain without.
4. A one-line `recap`.

Draft it yourself and let the user refine — they will never volunteer it. The rendered section's live half (step colors, "You are here", the "Where we are" paragraph) repaints from session `data-status` on every page load, so it follows `/plan-execute` with no runtime writes. Field reference: `schemas.md` → `explainer`.

## Prior-art pass (RS-01)

*(2026-08-12, RS-01.)* Once items exist but BEFORE batching them into sessions, run one bounded research pass per major item: does a proven solution already cover this scope?

Use the same tiered detection order plan-harden's Fork B uses (`~/.claude/references/plan-harden/fork-prompts.md` → "Fork B — external research"): **Perplexity → Exa (web) → Exa (deep) → Ref**, first hit wins, no redundant tier calls. This is a decision input, not a literature review — one question per item, capped findings, don't research the plan's whole domain.

Record the outcome on the item as `prior_art: {decision: "adopt"|"adapt"|"build", source, note}`:
- `adopt` — an existing tool covers it as-is.
- `adapt` — covers most of it; note what is still custom.
- `build` — nothing credible covers it, or nothing fits. **A `build` call made despite a credible alternative surfacing requires `note` to say why.**

Degrade honestly instead of guessing or silently skipping — never leave the field absent. Two honest degrades, and **the builder tells them apart for itself**:
- `research_status: "skipped"` + `research_reason` — when you chose not to run the pass.
- `research_status: "unavailable"` — when the tooling was genuinely not there. Do not reach for `"unavailable"` to save a step: at build time `validate_spec` runs its own configuration probe and **refuses the spec** if it can see research capability configured here, naming what it found (on a stock install, built-in `WebSearch`/`WebFetch` alone make that verdict *available*). The probe reads configuration only (MCP server names + permission deny-lists); it proves "configured and permitted", not "reachable".

Either way, the probe's record is stamped into the built plan as `research_env`, so the skip is recorded by the system and not only asserted by you. `build_plan.py` enforces that every item carries one shape or the other; see `schemas.md` → "Prior-art decision".

## Sessions, vertical slicing, and parallelism (PL-03)

### Vertical slicing vs expand–migrate–contract

*(2026-08-01, from mattpocock/skills to-tickets.)* **Slice sessions vertically**: each session cuts a narrow but complete path through every layer it touches and leaves something demoable or checkable behind — that is what gives its `verify` block teeth — not one layer at a time ("all the schemas", then "all the endpoints").

Exception: a wide mechanical refactor (rename a shared symbol, retype a column) whose blast radius spans the codebase. Sequence it **expand → migrate in batches → contract**, each batch its own session, so every session lands green.

### Parallelism — ask it as a question, not an afterthought

*(2026-08-12, PL-03.)* Once the sessions exist, ask the user plainly: **"which of these could run at the same time, if they couldn't conflict?"** Then design for the answer:

- **Freeze the shared interface first.** If two sessions both need a schema, an API shape, a config format, or a file layout, put an early **sequential** session that decides and freezes it, and make the consumers `depends_on` it. Contract first, fan-out second.
- **Split by files, not by topic.** Sessions belong in one `parallel_group` when their **written paths are disjoint**. Overlapping writes are refused on a shared tree (contract M5) and are a merge cost even under isolation.
- **Name the integration point.** Group members are barred from committing (M1) — shipping is a whole-group act. For a **worktree-isolated** group `build_plan.py` writes the integration session for you (merge producer-first, re-run the union of the members' gates, commit); for a shared-tree group, say which later session absorbs the group's work.
- **Ask for `touches` on every write-heavy item** — a comma-separated list of **paths**, not prose (e.g., `"skills/plan-builder/scripts/build_plan.py, skills/plan-builder/references/schemas.md"`). This is the input the conflict matrix computes from at build and dispatch time: an item with no `touches` makes its session ineligible for a group (refused, contract M2a), and prose is conservatively treated as conflicting with everything.
- **A plan that ends up a pure chain must say why.** `build_plan.py` warns when every dependency layer is one session wide, and names the file-disjoint pairs that could have been grouped. If the chain is real (one hand on one interface, an ordered migration, a spike whose result reshapes the next session), record it in top-level `serial_reason` and the warning goes quiet. Never silence it by inventing a wrong group.

Full field reference: `schemas.md` → "Parallel groups"; frozen contract: `../../plan-execute/references/parallel-group-contract.md`.

## Open decisions, embedded programs, and grammar scopes

### Open decisions — the fog test

*(2026-08-01, wayfinder-derived.)* For every session, ask whether its prompt hinges on a decision nobody has made yet. Test whether the question **can be stated precisely now — not whether it can be answered now**:
- **Statable** → resolve it in the interview, or author an early **decision session** (research / throwaway-prototype / grilling shaped, sized to answer the question and nothing more) that `depends_on`-blocks every session consuming the answer. Give genuine judgment calls a human checkpoint — auto-accept is for critiquing decisions already made, never for making them.
- **Not statable yet** → record it in top-level `open_questions` with what would sharpen it, instead of pre-slicing the fog into guessed sessions.

**Never leave an unmade decision embedded in a build session's prompt** — plan-harden's `decision-debt` lint flags exactly that.

### Never leave a program embedded in a build session's prompt

*(2026-08-21, measured.)* A prompt may carry an illustrative excerpt (a signature, a config shape, one command). It must not carry a program the session is expected to RUN. A review pass reads prose and cannot execute code: measured on the context-budget plan, a 139-line arming snippet sat at 8, then 8, then 6 open review findings across three rounds (`ast.parse` accepted it and it still raised `NameError` on its first real input) and all six closed the moment it moved into a script with tests. Author it the other way round: one session BUILDS `<script>.py` with its tests, and every later session RUNS it. `build_plan.py` warns on any heredoc of 10+ lines in a session prompt (threshold and evidence in `scripts/plan_limits.py`).

### Narrow scope for stdlib-only grammar readers

*(2026-11-21, two plans.)* A session that builds a stdlib-only reader of a grammar (YAML, shell) names its narrow scope in the prompt: the top-level shapes it must read, and that every other shape is accepted unread. A prompt that says "refuse every shape the full parser refuses" does not converge across review rounds (memory note `heuristic-shell-counter-review-does-not-converge`).

### Out of scope

Ask *"what is this plan consciously NOT doing?"* and record it in top-level `out_of_scope`. Entries never graduate into sessions (redrawing the goal means a fresh plan), and the closing acceptance review checks that the boundary held.

## Schema v8 — class, risks, touches, checks, and overrides

The author says what the work is, what is risky, and how it is checked; `/plan-execute begin` picks the model and effort at dispatch (`../../plan-execute/references/route-at-dispatch-contract.md`; the human word for a session is **slice**, the code keeps `session`).

Ask per slice:
- **`task_class` (REQUIRED at v8; a v8 session without it fails the build)** — one of `mechanical|standard_build|agentic_build|deep_reasoning|linchpin`. Offer the class definitions (`model-effort-guidance.md` → "Class definitions"). `mechanical` and `standard_build` map from the work described; `agentic_build` vs `deep_reasoning` vs `linchpin` is a real judgment call the user makes, not you. **Do NOT infer it silently from prose** — infer only where the mapping is deterministic (e.g. the user already said "mechanical rename sweep"). Pre-v8 specs keep the old rule: `task_class` optional, `model` required.
- **Risks (`peer_triggers`)** — *"what could go badly wrong here?"* Record as `peer_triggers` risk flags; they raise the override floor.
- **`touches` on every item** — paths, not prose (`[]` for none). Required on every item at v8.
- **Verify gates and checks** — author `verify.gates`, checks, and evidence contracts. For a check an EARLIER slice wrote that this slice must not edit, list its repo-relative path in `verify.locked` (regular files only; the executor hashes them at `begin` and fails the attempt if the worker changes one). When a session carries an `llm-review-*`, `cross-family-review-*`, or `adversarial-review` gate, also ask for its **`verify.blocking_rule`**: two or three numbered conditions, each a failure a test could show (`build_plan.py` warns when it is missing).
- **Model is an override, not a question.** Leave `model`/`reasoning` out. Set them only as a stated exception, both or neither, with a `why_model` saying what the class default would get wrong here. An override may not sit below the risk floor: it is refused when the slice has any `peer_triggers` or is `linchpin`. Do not offer a model choice in the interview.
- Every session still gets a wall-clock `effort` (`S`/`~2h`/`half day`), separate from reasoning depth.

## Codex-shell posture

Ask whenever the plan might be run from Codex: *"does any session write outside the repo, need live network, or shell out to another agent/CLI?"*

A `codex exec` dispatch runs `--sandbox workspace-write`, which is measured to deny all three. Without a `dispatch.codex_shell` declaration, a `--harness codex` run dispatches the session and fails it after paying for it:
- Artifacts outside the repo → `writable_roots: ["~/.gearbox-state"]`
- Live API / price / clone work → `network: true`
- Session that itself invokes `codex exec` / `claude -p` / a vendor CLI → `sandbox: "danger-full-access"`, which is legal ONLY on a session a human already gates (`guards_irreversible`, `requires_human_checkpoint`, `linchpin`) because it hands that session an unsandboxed shell.

Omit the block entirely for repo-only work — absent is the default behaviour. Field reference: `schemas.md` → `codex_shell`; contract: `../../plan-execute/references/dual-harness-contract.md` § 4.5.

## Verification posture and LLM review levels

Ask *"how do we KNOW each session is really done?"* For any session whose `deliverable` is checkable, draft a `verify` block (`on_fail: rework` default, bounded by `max_rework`, or `on_fail: halt`). Gates resolve in the project's `.claude/eval-gates.json` merged over skill-bundled defaults (`eval-smoke-baseline`, `code-review-gate`) — an unknown gate is a build-time error. The skill steers; it never silently auto-fills `verify`.

### Evidence & invariant rules

- **Mechanism engagement (`require_evidence: true`):** set where a metric could move without the mechanism engaging (a flag flip, a scoring tweak, a fix whose log event must fire). `DONE` is refused until every declared `evidence` path exists and is non-empty (may stand alone without `gates`).
- **Bug-fix sessions:** a session whose deliverable is a bug fix always sets `require_evidence: true`, and its prompt tells the session to name two evidence paths: the failing reproduction captured BEFORE the edit, and the passing run after it (measured: 0 of 11 fix-like sessions across 51 plans carried any evidence requirement).
- **Safety mechanisms:** a session that builds a safety mechanism (a gate, a fail-closed check, a state machine, an allow/deny rule) writes its invariant tests in that same session — a property or matrix test across every input combination, named in the prompt and listed in `verify.gates`. Name each invariant in the session's prompt.
- **Owner invariants for land review:** ask the owner for the plan's goal and its invariants during the interview, and write them, numbered, into the acceptance-review session's prompt (`schemas.md` § "Closing acceptance review") — the land review's blocking rule cites that list word for word (`../../plan-execute/references/verify-gates.md` § "Land review stop rules"). Measured (`cloudflare-security-vendor`): without this, land review found missing cases one per round for five rounds.

### LLM review level by task class (s10 RV-02)

Propose an LLM review level per ship-ready session from its `task_class`:
- `mechanical` / `standard_build` → `llm-review-low`
- `agentic_build` → `llm-review-medium`
- `deep_reasoning` / `linchpin` → `llm-review-high`

**Propose, never silently auto-fill:** say which level you would add and why (the task class), and let the author confirm, drop it, or move a level. For a session whose `model` is a Claude token, propose `cross-family-review-<level>` at that same level instead of `llm-review-<level>` (Codex reviewing Claude-built code), subject to the plan-builds-its-own-gate, restricted-repo, and no-working-Codex exceptions (`schemas.md`). Only on sessions that actually ship code — a research/spike/decision session gets none. It stacks with `adversarial-review` where `peer_triggers` are declared and is distinct from `code-review-gate` (deterministic test/lint gate). Full table + rationale: `schemas.md` → "LLM review level by task class".

## Plan-level acceptance criteria and closing review

Ask *"when the whole plan is finished, how do we know it achieved what it set out to?"* Get **3–7 checkable statements** (each true or false about the finished system). **One of them names the after-landing measurement from `narrative`** — the command that produces the number and the calendar date it is due *(2026-10-18)*.

Then scaffold a **closing acceptance-review session** (`acceptance_review: true`):
- Fresh isolated agent (`subagent_type: null`), depending on every other terminal session, `task_class: deep_reasoning`, `require_evidence: true`, with the criteria verbatim in its prompt.
- Verifies each criterion against the repo/system itself (not closeout claims), reads every `_closeouts/*.json` for accumulated deviations/blocked/deferred work, and returns per-criterion `ACHIEVED`, `GAP`, or `NEVER CHECKED` with evidence.
- When `spec.json` has `spec_ref`, appends one line per requirement id of that plan line to `## Results` of `SPEC.md`.
- Parks the plan for a human decision unless every result is `ACHIEVED` (one exception: the after-landing measurement criterion may stay `NEVER CHECKED` until its due date and is reported with that date).

`build_plan.py` warns on a ≥4-session plan without one, and `plan-harden` flags it. Full contract + authoring template: `schemas.md` → "Closing acceptance review".

## Autonomy posture and decision briefs

Ask *"where MUST a human look, vs. where can a gate decide?"* Make a checkpoint `requires_human_checkpoint: true` only for genuine human-judgment / irreversible boundaries (prod deploy sign-off, public-facing wording, one-shot adjudication). Everywhere else, prefer an automated `verify` gate — that lets the operator run `/plan-execute --auto` and walk away (`--auto` never overrides a human checkpoint).

**Every human gate must ship its decision brief.** `requires_human_checkpoint: true` requires a companion `dispatch.checkpoint` object:
- `reason` — why a human must look (what is irreversible or judgment-laden, in plain language).
- `decision` — the specific question the human answers when the plan parks.
- `options` (optional) — 2–4 concrete answers.

`build_plan.py` refuses to build a gate without it. **Litmus test:** if you and the user cannot name what would make the human answer anything other than "proceed", the gate has no decision — do not create it. Use a `verify` gate for anything checkable, or `checkpoint_policy: "notify-and-continue"` for a pure FYI.

Order at runtime after a closeout: **human checkpoint ▸ verify ▸ shipping**.

## Shipping posture

Ask *"should any session commit, push, open a PR, or deploy when it finishes?"* Default to nothing (opt-in). If yes, draft a `post_session` block (or a phase-level `phase_closer` on top-level `phases[]`, inherited via `"phase": "pN"` and overridable per key).

- **Four `git` values:** `none` / `commit` / `commit-push` / `commit-push-pr`.
- **Steer by session kind:** ship-ready (`kind: ship-ready`) → `git: commit-push`; spike/research (`kind: spike`) → `git: commit` or `none`. The skill steers; it never auto-fills shipping.
- **Project-detection rule:** a `deploy` target is legal only if it resolves in `.claude/deploy-targets.json`, and `pre_deploy_gate` only if it resolves in `.claude/eval-gates.json` (or skill-bundled `eval-smoke-baseline`). `build_plan.py` validates this and runs the adapter capability-probe before writing `manifest.json`. See `../../plan-execute/references/shipping-registries.md`.
- **`rollback_hint` is mandatory** for any deploy-bearing session.
- **Preview destructive surface:** `python build_plan.py <spec.json> --dry-run-shipping [--register-in <root>]` prints every commit/push/PR/deploy/gate action and writes nothing.

Also offer optional top-level `notify_on_halt: {"command": "<script>"}` and `notify_on_complete: {"command": "<script>"}` for unattended runs (`schemas.md`).

## Dual-layer authoring

For each item and each session, draft two texts:
- `human_summary` — 1–2 sentences in plain English (hallway voice, no acronyms, no file paths, no dev steps). Most prominent text on the card.
- `deliverable` — 1 sentence describing the concrete outcome ("When done, X exists / Y is verifiable / Z is true"), rendered as a green-tinted callout.

The user almost never volunteers these — draft both and let them refine. Write both under the eli5 rules (`../../eli5/SKILL.md` → "Rules for the page"): plain words, real names kept and defined, no mechanism cut that the reader needs to follow the cause.

Technical detail (`description`, `agent_instructions`, `schema`, `mockup`, `code`, `touches`) goes into the collapsible **agent-spec** layer (always in the DOM).

**Lead with what's likely to change.** Put decisions a reviewer is most likely to want changed first (data-model choices, type interfaces, user-facing behavior) and bury mechanical work at the bottom. Flag each judgment call with `tweak_likelihood` + `alternatives` (`schemas.md`) so it surfaces in Decision hotspots.

When to add `schema`, `mockup`, or `code`:
- `schema` — data shape, API contract, or DB schema.
- `mockup` — UX surface or visual layout (inline SVG best; ASCII for terminal/tables). A session that builds or changes a UI names `ui-craft` (BUILD, then REVIEW) in its prompt, and its REVIEW frames are the session's evidence.
- `code` — small code excerpt clarifying expected shape (signature, config snippet, usage example).
- Leave empty blocks out.

## Prompt depth and `subagent_type` steering

### Depth of a session prompt

*(2026-10-18, Rieseberg-derived.)* For a `deep_reasoning` or `agentic_build` session, the drafted prompt names three things:
1. **The rawest material** the session starts from (logs, transcripts, data, the running system — not a summary or status page).
2. **The instrument a top professional in that field would use** (a profiler, a database query, a statistical test, the real operation run end to end — never a text-shape proxy such as a status line, file size, anchor count, or plausible reading of code).
3. **The check other than the agent itself** that shows the result is right (the session's `verify` gate or `require_evidence`, named in the prompt).

### `subagent_type` steering

Default to a **fresh agent**: leave `subagent_type` `null` (a fresh `general-purpose`) or name a fresh typed specialist (`Explore`, `Plan`, `code-reviewer`, `epic-implementer`). A fresh agent gets a clean context and honors the session's model. Only use the literal `"fork"` on pre-v8 plans when a session genuinely needs the orchestrator's live conversation context (a fork ignores per-session `model`, and v8 refuses `"fork"`).

Do not design sessions around ultracode/Workflow dispatch, and do not add workflow flags to the spec or manifest. Transport decisions belong to `/plan-execute` and are invisible to the plan.
