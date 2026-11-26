# /repo-review — Scoring & Audit Rubric

## Contents

- [Rating scale and evidence tiers](#rating-scale-and-evidence-tiers)
- [Lens 1 — Repo architecture & ways of working](#lens-1--repo-architecture--ways-of-working)
- [Lens 2 — Agentic elements & harness design](#lens-2--agentic-elements--harness-design)
- [Lens 3 — Evals, verification & proof map](#lens-3--evals-verification--proof-map)
- [Lens 4 — Modern frontier LLM fit](#lens-4--modern-frontier-llm-fit)
- [Lens 5 — Canonical task dry-run](#lens-5--canonical-task-dry-run)
- [Anti-noise filter and Good / Bad / Ugly bar](#anti-noise-filter-and-good--bad--ugly-bar)

---

## Rating scale and evidence tiers

Rate each lens independently using both a **quality rating** and an **evidence tier**.
Separate **Repository** (what the checkout and its contracts provide) from **Runner**
(local tools, credentials, CI environment) so a missing local secret is never misdiagnosed
as a broken repository.

### Quality ratings

| Rating | Meaning |
|---|---|
| `Strong` | Explicit, verified, enforced where it matters, low rot, usable without tribal knowledge. |
| `Good` | Solid foundations with minor gaps, light drift, or a few manual steps. |
| `Partial` | Exists in pieces, relies on conventions or guesswork, or enforces critical rules only in prose. |
| `Weak` | Absent, broken, misleading, or actively working against humans and coding agents. |
| `N/A` | Genuinely does not apply to this repo's shape (explain why in one phrase). |

### Evidence tiers (caps confidence)

| Tier | What you actually did | What you may claim |
|---|---|---|
| `E0` | Static inspection only (inventory + file reads). | Structure, rot, static inconsistencies. Cannot claim commands or evals actually pass. |
| `E1` | One read-only check/command exercised (e.g., `--collect-only`, linter, doc test). | That specific entry point runs in this environment. |
| `E2` | Observed both a real passing check and an actionable failure/guard behavior (or verified CI run history). | End-to-end verification loop genuinely catches regressions. |
| `E3` | Repeated / multi-trial runs or historical eval trend logs inspected. | Statistical stability and regression-rate claims. |

Always print the achieved `E`-tier on the scorecard. On a **trusted** repo the user asked
you to review, do not settle for `E0` when fast (≤2–3 min) read-only checks exist — run
them to reach `E1`–`E2`. Reserve `E0` for untrusted third-party checkouts or environments
missing the required runtime.

### Blind-spot bar (no lazy skips)

Before finalizing the report, audit every item you plan to place under **Genuine blockers /
Blind spots**:

| Allowed in Blind spots | NOT allowed (investigate before reporting) |
|---|---|
| Out-of-repo private sources, manifests, or internal services not in this checkout | Unread files or top-level directories inside the repo (read or fan out read-only subagents) |
| Checks requiring missing credentials, paid APIs, GPUs, or physical hardware | Fast unit tests, CI fast lanes, linters, doc/drift guards finishing in ≤2–3 min |
| Destructive or prod-mutating commands (deploys, migrations, live writes) | `--collect-only` / syntax / dry-run checks on slow suites |
| Slow E2E suites (>5 min) *after* running their fast subset or `--collect-only` | Approximate metrics you can tighten deterministically (e.g., running `repo_inventory.py`) |

---

## Lens 1 — Repo architecture & ways of working

Check whether a skilled newcomer (human or agent) can understand, change, and ship safely:

1. **Purpose & boundaries**: Is it clear within 60 seconds what the repo owns, what is
   generated/vendored/read-only, and where core logic lives?
2. **Single source of truth vs. doc mirrors**: Does documentation explain *why* and link to
   the authoritative code/config, or does it copy schemas, UI descriptions, or command lists
   that silently drift whenever code changes? (If a code edit forces a prose edit to keep a
   description true, that doc is a mirror.)
3. **Onboarding & lifecycle**: One ordinary command surface shared by humans, agents, and CI
   (`bootstrap` → `boot`/`doctor` where relevant → `verify` → `teardown`), rather than
   parallel `agent-*` shadow scripts or hand-crafted `.env` rituals.
4. **Dependency & supply-chain hygiene**: Pinned lockfiles, single declaration of tool/runtime
   versions reused by CI (not hardcoded in two places), no committed secrets or junk artifacts.
5. **Change & review discipline**: Meaningful CI gates, clear `CONTRIBUTING.md` / ADRs for
   irreversible decisions, clean separation of generated artifacts from hand-authored source.
6. **Trust boundaries**: Explicit handling of external input, secrets, hooks, and publication
   gates (especially in public repos or repos with automated agents).

---

## Lens 2 — Agentic elements & harness design

Evaluate the agent-facing layer (`AGENTS.md`, `CLAUDE.md`, rules, skills, commands, hooks,
subagents, MCP configs) separately from the language toolchain:

### Context-file hygiene & cross-platform portability

- **Canonical entry point**: Root `AGENTS.md` is a real file (not a symlink). If Claude Code
  is supported, `CLAUDE.md` is a thin `@AGENTS.md` pointer (regular file, not duplicated
  rules). Flag root `GEMINI.md` coexisting with `AGENTS.md` (Antigravity loads both
  cumulatively, doubling context). Flag `@path` imports inside `AGENTS.md` (non-portable
  across Codex and other harnesses).
- **Auto-loaded context budget**: Check total auto-loaded bytes/lines/tokens before the first
  user turn. Defaults unless the repo declares its own override:
  - Root `AGENTS.md` / `CLAUDE.md`: aim ≤ 150–200 lines and ≤ 24,000 bytes (hard per-file
    cap on several harnesses).
  - Combined auto-loaded total: warn > ~4,000 estimated tokens; flag strongly > ~8,000.
- **Four-question line test** for always-loaded rules:
  1. *Checkable?* Could you tell if an agent broke it? ("Use `Decimal` for money" vs. "write clean code".)
  2. *Actionable pair?* Does every "don't" name the matching "do"?
  3. *Earned?* Did a real mistake or non-obvious invariant put it there, rather than boilerplate?
  4. *Right layer?* Belongs always-loaded, or should it live in a module `AGENTS.md`, skill,
     or `docs/` file loaded on demand?

### Skills, hooks & progressive disclosure

- **Skills as playbooks, not context dumps**: Each skill has clear trigger phrases ("what +
  when"), non-overlapping boundaries with sibling skills, concise `SKILL.md` (<300 lines
  target; <500 ceiling), and heavy tables/rubrics pushed to `references/`.
- **Discoverability**: Broken symlinks, stale command shims duplicating skills, or orphaned
  docs unlinked from `AGENTS.md` / `README.md` are **rot**.
- **Enforcement ladder (Prose → Sensor → Gate)**:
  - *Prose* (`AGENTS.md`, prompts) is advisory context, never a guarantee.
  - *Sensors* (linters, type checkers, doc-drift tests, `verify` scripts) detect violations
    after the edit.
  - *Gates* (pre-commit/pre-push hooks, tool permission denials, CI branch protection, typed
    schemas) prevent expensive/irreversible mistakes before they land.
  - Flag every high-cost rule ("never touch generated files", "never commit secrets",
    "never leak private identifiers") that exists *only* as prose.

### Rot & accuracy audit

- Verify paths, commands, and links named in `AGENTS.md` / `SKILL.md` against the actual
  tree (using `repo_inventory.py` output plus targeted spot-checks of factual claims like
  tool versions, scripts, and env vars). Every broken path or stale command is **rot**
  ("Fix now").

---

## Lens 3 — Evals, verification & proof map

Distinguish **code tests** ("does the software work?") from **agent/harness evals** ("does
the agent work the way this repo expects when prompts, skills, tools, or models change?").

### When evals are appropriate (the dogfooding precedent)

- Do not penalize a brand-new or tiny non-agentic repo for lacking an elaborate LLM eval
  framework if its code tests + linters already cover its risk.
- **Do** flag missing evals when the repo ships prompts, skills, agent workflows, routing
  policies, tool schemas, or relies heavily on autonomous coding agents.

### What good evals look like

1. **Behavioral guideposts alongside E2E checks**: Fast, deterministic behavioral assertions
   (tool choice, refusal/ask-first boundaries, running validators, avoiding forbidden files)
   tell you *why* a change broke; coarse E2E benchmarks alone hide root causes.
2. **Capability vs. regression separation**:
   - *Regression suite*: near-100% pass rate, protects against backsliding on prompt/model
     updates.
   - *Capability suite*: starts below ceiling to measure headroom. A suite pinned at 100% is
     saturated (provides no improvement signal); a task at 0% across repeated trials usually
     indicates an ambiguous spec or broken grader.
3. **Balanced problem sets (positive + negative cases)**: Test both when a behavior/skill/tool
   *should* trigger and when it *should not*. One-sided evals create over-triggering.
4. **Outcome grading over brittle path matching**: Grade resulting state and invariants rather
   than a rigid tool-call sequence, unless the sequence itself is a policy requirement.
5. **Trial isolation & anti-cheating**: Clean state per trial (no leaked git history, cached
   files, or shared temp state). Any self-optimizing loop must be anchored to a held-out
   check the agent cannot edit.
6. **Non-determinism handling**: Single runs are signals, not settled results. Look for
   multi-run stability (≥3 trials or batch pass-rate tracking) rather than noisy single-shot
   PR gates.
7. **Transcript review & error analysis**: Are failures inspectable (traces/transcripts saved)
   and turned into new regression cases after real incidents?
8. **Config-change gating**: Do edits to `AGENTS.md`, skills, hooks, or model routing trigger
   validation/eval checks in CI, or can agent behavior regress silently?

### The Proof Map

Check whether the repo maps each real change class it owns (docs, pure logic, UI, API,
infra/hooks, agent config) to its exact verification command — without inventing fake lanes
or forcing full slow suites on doc-only edits.

---

## Lens 4 — Modern frontier LLM fit

Judge whether the repo's prompts, skills, and harness are calibrated for current frontier
reasoning and coding models (see the modern LLM practices reference loaded in Step 2)
rather than fighting them with legacy scaffolding.

Key questions:
- Is the harness **over-scaffolded** (freezing old model weaknesses into rigid rituals,
  caution pile-ups, or redundant orchestration)?
- Is the harness **under-scaffolded** (missing fast local verification, clear boundaries, or
  repo-specific gotchas)?
- Are **effort / thinking controls**, **prefix caching**, **ownership vs. scope**, and
  **subagent delegation costs** handled cleanly?

---

## Lens 5 — Canonical task dry-run

Pick the most recently modified non-trivial source file reported by `repo_inventory.py`
(skip lockfiles, changelogs, and generated snapshots). Without modifying anything, trace:

1. Which harness/context files would load automatically, and which you had to hunt for.
2. Which exact command you would run to verify a small bug fix + test in that file.
3. Every point where a fresh agent would have to guess, ask unnecessarily, or risk tripping
   an undocumented constraint.
4. One concrete harness change that would remove the biggest point of friction.

---

## Anti-noise filter and Good / Bad / Ugly bar

### Discard before reporting

- Stylistic wording preferences that do not change agent behavior or human clarity.
- Generic recommendations not anchored to a concrete artifact in this repo.
- Hypothetical risks without a concrete failure mechanism (`input/action → bad outcome`).
- Intentional tradeoffs already explained in an ADR or repo doc (unless evidence shows drift).

### Placing findings in Good / Bad / Ugly

- **The Good**: Genuine strengths — clean SSOTs, fail-closed gates, well-scoped skills,
  strong deterministic tests/evals, crisp boundaries. Protect these from accidental erosion.
- **The Bad**: Real friction or gaps — stale links/commands (rot), missing behavioral evals,
  bloated auto-loaded context, over-prescriptive legacy prompts, unclear proof commands.
- **The Ugly**: Structural traps that silently mislead or fail open — prose rules pretending
  to be security gates, evals that pass by accident or cannot fail, contradictory rules
  across surfaces, runaway subagent/token traps, or unguarded publication/mutation paths.
