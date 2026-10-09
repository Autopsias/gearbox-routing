# Skill-Unification Routing Table (s08 — Collapse duplicate front doors)

**Plan:** `~/.claude/_plans/<your-plan>-<date>/` · **Session:** s08 (CP-01, CP-02)
**Applied:** 2026-06-21 · **Pre-s08 tag:** `skill-unification-pre-s08-1782081110` (af25904e, a historical marker)
**Source:** tracked in `~/your-private-harness` since 2026-09-26; `gearbox deploy` copies it to
`~/.claude`. Edit it in the source repo, never in `~/.claude`.
**Last checked:** 2026-09-26, against source `main` at 188ace40 — files, call sites, plugin
settings and the gate default. Nothing was re-tested at runtime.

**The June revert command is withdrawn.** It ran `git reset --hard` (or a scoped
`git checkout`) to the tag inside `~/.claude`. That tree is a deploy target and moves only
by fast-forward, so the command would discard every harness change since June and any
live state not yet harvested. To undo a routing change, commit the reverse change in
`~/your-private-harness` and deploy it.

---

## Why this exists

Each crowded job (code review, testing, research, diagnosis) had many typeable front
doors → choice paralysis. s08 picks ONE-or-TWO **canonical** entry points per job and
routes the rest into them. **s08 deleted nothing** (deletion was s09's job) — every demoted
entry is converted to a thin back-compat **alias / routing pointer** that still reaches
its original capability. Every demoted typed name still resolves; every programmatic
call site (`Skill(...)`, `Task(subagent_type=...)`, chain-invocation) is preserved.

**Demotion ≠ removal.** Demotion = (1) the typed front door now carries a routing
pointer to the canonical entry, AND (2) the underlying capability/worker stays callable
(directly by the canonical entry, and by any existing programmatic caller).

**s09 (CP-03 prune) ran and removed none of the entries below.** It
`git rm`'d 12 dead files: `commands/archive/` (5), `agents/archive/` (3),
`commands/morning-briefing.md` and three `bmad-bmm-*-prd` wrappers
(`~/.claude/_plans/<your-plan>-<date>/_closeouts/s09.json`).

Typed names below are command file names. Claude Code names a `commands/*.md` file after
the file and ignores its `name:` field (Claude Code skills docs, checked).

---

## CP-01 — Code review: ~8 front doors → 2 canonical

**Canonical (keepers):**
- **Deep / dual-model** → `/adversarial-review` (Claude Opus + Codex, synthesis, plan-hardening loop)
- **Fast / diff** → native `/code-review` (built-in harness skill; correctness bugs + cleanups; effort low→ultra)

A review of a whole repo, not a diff, has its own door: `/repo-health` — see
[Whole-repo review](#whole-repo-review).

| Demoted entry | Kind | Canonical target | Demotion method / where it routes |
|---|---|---|---|
| `/review` | openai-codex **plugin** command (external, not user-editable) | `/code-review` (fast) or `/adversarial-review` (deep) | User-owned alias `commands/review.md` shadows the plugin name and routes to canonical. It sets `disable-model-invocation: true`, so only the user can start it. Plugin file untouched. |
| plugin `/code-review` (`claude-plugins-official/code-review`) | **plugin** command (external) | native `/code-review` | The native built-in `code-review` skill is the canonical "fast" reviewer and owns the name. The plugin is disabled: `settings.json` sets `"code-review@claude-plugins-official": false`. Plugin file untouched. |
| `/bmad-bmm-code-review` (`commands/bmad-bmm-code-review.md`) | user-owned BMAD **overlay wrapper** | `/adversarial-review` (deep) | Deprecation/routing header added to the wrapper. **Body preserved** — `agents/epic-code-reviewer.md` and `references/epic-dev/full/phase-5-code-review.md` still invoke `Skill(skill='bmad-bmm-code-review')` (Lane-A workers); those callers keep working. |
| `/bmad-review-adversarial-general` (`commands/bmad-review-adversarial-general.md`) | user-owned BMAD overlay wrapper | `/adversarial-review` (deep) | Deprecation/routing header added. Body preserved (still loads the `_bmad` task for anyone who needs the BMAD-specific flow). |
| pr-review-toolkit hunters: `silent-failure-hunter`, `type-design-analyzer`, `comment-analyzer`, `pr-test-analyzer` | **plugin agents** (external) | sub-agents of `/adversarial-review` | Declared as dispatchable **sub-agents/workers** of the canonical deep reviewer (see `commands/adversarial-review.md` → "Specialized hunter sub-agents"). Plugin agents untouched; the canonical reviewer now knows to fan out to them. |
| `pr-review-toolkit:code-reviewer`, `pr-review-toolkit:code-simplifier` | plugin agents (external) | `/adversarial-review` (review) / `/code-review` (simplify) | Routed via the canonical reviewers; remain available as workers. |

**Gate pin — withdrawn.** In June the plan gate `code-review-gate` resolved
to the native `code-review` skill, and this section called that a stable pin. Since
410e89ea, the skill-bundled default for `code-review-gate`
(`skills/plan-execute/references/eval-gates.default.json`) is a deliberate failure: it
prints "code-review-gate has NO portable default" and exits 1. The message gives two
reasons. A skill gate that cannot be launched read as a pass, and that shipped twice.
And `/code-review` run as a gate reviewed the whole repo root, not the session's scope
(2026-08-23: 12 agents, 110M cache-read tokens, no verdict at the 1800 s wall). Bind a
review gate to `llm-review-low|medium|high`, or define `code-review-gate` in the
project's `.claude/eval-gates.json`.

---

## CP-02 — Testing / research / diagnosis: collapse to one each

### Testing (~14 → 1 canonical)

**Canonical:** `/test-orchestrate` (already the hub: dispatches the fixer family as
**workers** via `Task(subagent_type=...)`, supports `--no-chain`, `--intent`, scope flags).

| Demoted entry | Kind | Routes to | Method |
|---|---|---|---|
| `unit-test-fixer`, `api-test-fixer`, `database-test-fixer`, `e2e-test-fixer`, `type-error-fixer`, `import-error-fixer` | user agents (workers) | `/test-orchestrate` | Already workers — dispatched by `/test-orchestrate`, never typed as a front door. No change needed; documented here. |
| `/bmad-tea-testarch-atdd`, `-automate`, `-ci`, `-framework`, `-nfr`, `-test-design`, `-test-review`, `-trace` (`commands/bmad-tea-testarch-*.md`) | user-owned BMAD overlay wrappers | `/test-orchestrate` (as **presets**) | Deprecation/routing header added to each; body preserved (still loads the `_bmad` tea workflow for the preset). `/test-orchestrate` is the obvious typed entry; these are presets behind it. |
| `bmad-tea-teach-me-testing`, `bmad-qa-generate-e2e-tests`, `create-test-plan`, `coverage`, `usertestgates` | mixed | `/test-orchestrate` | Specialized modes/presets; `/test-orchestrate` is the front door. Documented; not retyped. `bmad-qa-generate-e2e-tests` is not installed at user level: it exists only as a project skill in the example-project repo (`~/DeveloperFolder/example-project/.claude/skills/`). |

### Research (5 → 1 canonical)

**Canonical (since 2026-09-26):** `~/.claude/docs/reference_mcp_tool_selection.md` — a
guide for picking research tools, not a command. For a deep-research request it names
Exa `agent_run`.

**`/deep-research`, the June choice, never shipped (checked).** No command,
skill or plugin of that name is installed at user level, in the plugin cache, or in the
`.claude` or `.agents` folder of any project under `~/DeveloperFolder`. No path named
`deep-research` was ever tracked in `~/your-private-harness` or `~/.agents`. `/research` is not
its successor: that command (58a970a7) only marks a session as research, so
the compaction policy keeps the 1M window.

| Demoted entry | Kind | Routes to | Method |
|---|---|---|---|
| `/bmad-bmm-domain-research` (`bmad-bmm-domain-research.md`) | user-owned BMAD overlay wrapper | the tool guide | Routing header points to the guide; body preserved (still loads the `_bmad` workflow). |
| `/bmad-bmm-market-research` (`bmad-bmm-market-research.md`) | user-owned BMAD overlay wrapper | the tool guide | Routing header points to the guide; body preserved (still loads the `_bmad` workflow). |
| `/bmad-bmm-technical-research` (`bmad-bmm-technical-research.md`) | user-owned BMAD overlay wrapper | the tool guide | Routing header points to the guide; body preserved (still loads the `_bmad` workflow). |
| `exa:search`, `mcp__exa__*` research tools | plugin/MCP | the tool guide | The `exa` plugin is disabled (`"exa@claude-plugins-official": false`). The `mcp__exa__*` tools come from an MCP server, not the plugin, and are still listed in sessions. |

### Diagnosis (3 → 1 canonical)

**Canonical:** `/diagnose` (the diagnosis discipline skill). A new **`--deep` mode**
folds the deep root-cause investigators. `skills/diagnose` is a tracked symlink to
`~/.agents/skills/diagnose`, which is a separate git repo; edit the skill there.
`settings.json` sets it to `user-invocable-only` (f4e56744): the user types `/diagnose`,
and a skill that hands work to it tells Claude to follow its `SKILL.md`.

| Demoted entry | Kind | Routes to | Method |
|---|---|---|---|
| `digdeep` (`agents/digdeep.md`) | user agent (Five-Whys + deep research, analysis-only) | `/diagnose --deep` | Kept as the **worker** that `/diagnose --deep` dispatches. Existing `Task(subagent_type="digdeep")` call sites in `ci-orchestrate` strategic-mode + `parallel-orchestrator` are PRESERVED — `digdeep` stays a valid worker; only the *typed front door* is unified under `/diagnose --deep`. |
| `bmad-investigate` | external/registered skill (Forensic case investigation) | `/diagnose --deep` | Forensic deep mode reached via `/diagnose --deep`. Skill untouched; routing documented. (External — NOT user-editable; alias-only.) Not installed at user level: it exists only as a project skill in the example-project repo (`~/DeveloperFolder/example-project/.claude/skills/bmad-investigate`), so `/diagnose --deep` reaches it only in a example-project session. |

---

## Whole-repo review

**Added 2026-09-26. Canonical:** `/repo-health` — the one door for reviewing a whole
repo, not a diff. Its arguments name the goals: `health`, `security`, `refactoring`,
`over-engineering`, `performance` or `all`. With no argument it asks which goals to run,
with `health` recommended; a headless run does `health` only. Every run ends with one
decision card, and a run that includes a review also writes a summary
(`.claude/health/REVIEW.md`). It routes each fix to an existing specialist. It sets
`disable-model-invocation: true`, so only the user can start it; other commands tell the
user to type it.

| Part | Kind | Role under `/repo-health` |
|---|---|---|
| `/declutter` | user skill (`skills/declutter`) | The **deep** over-engineering audit: evidence-gated, area-sharded, read-only. It costs about 150k–200k subagent tokens per area shard (about 1–1.5M for a 150k-line app). `/repo-health` never runs it: the decision card names it with its cost, and only the user can start it (`disable-model-invocation: true`). |
| `/code-quality` | user command (`commands/code-quality.md`) | File size, function length and complexity. The fix route for the `cq.*` checks: an agent follows the command file with `--fix` or `--adopt`. The `--deep` pass runs it with `--check`. |
| `improve-codebase-architecture` | vendored skill (a `skills/` symlink into `~/.agents/skills`) | The design half of the `refactoring` goal: it finds deepening opportunities. `settings.json` sets it to `user-invocable-only` (f4e56744), so `/repo-health` has an agent follow its `SKILL.md` (steps 1–2); a candidate the user picks goes to its step 3. |
| `ponytail:ponytail-audit` | ponytail plugin skill | The **quick** over-engineering scan, run first. It gives leads, not findings. If the plugin is missing, the review says so and recommends `/declutter`. |
| `security-scanner` | user agent (`agents/security-scanner.md`) | The fix route for `sec.dep-vulns`, `sec.sast` and `sec.diff-review` (`--mode=fix`). The findings come from the health check and, for the `security` goal, from a whole-repo pass of the vendored `security-review` skill. |

The `performance` goal measures first, then hunts along at most five hot paths; a
measured hot spot goes to `diagnose`, through its `SKILL.md`. A diff or PR review is a
different job (`/review`, `/code-review`, `/adversarial-review`). So is fixing one known-failing area: go straight
to `/code-quality`, `/test-orchestrate`, `/ci-orchestrate` or `security-scanner`.

**Pointers into this door.** `commands/review.md` (step 0) and `commands/code-quality.md`
stop on a whole-repo ask and tell the user to type `/repo-health`. `skills/declutter`
names `/repo-health` as its front door. A fourth pointer, the hand-kept directory doc of
agents and commands, was deleted by plan remove-the-cause-2026-09-28. The
three pointers and the three review goals landed on `main` in 79b0fafd.

---

## Preserved programmatic call sites (must keep resolving)

| Caller | Reference | Status |
|---|---|---|
| `agents/epic-code-reviewer.md` | `Skill(skill='bmad-bmm-code-review')` | PRESERVED — wrapper body intact |
| `references/epic-dev/full/phase-5-code-review.md` | "Execute the bmad-bmm-code-review workflow" | PRESERVED |
| `commands/ci-orchestrate.md` + `references/ci-orchestrate/strategic-mode.md` | `Task(subagent_type="digdeep")` / digdeep routing rows | PRESERVED — digdeep stays a worker |
| `commands/test-orchestrate.md` | `Task(subagent_type="unit-test-fixer" ...)` etc. | PRESERVED — fixers are canonical workers |

Line numbers are left out on purpose: they drift. The s08 verify script checks that each
target exists (`commands/bmad-bmm-code-review.md`, `agents/digdeep.md`); it does not read
the callers.

---

## Verify

Run the guard against the deployed tree, or against the source tree:

    bash ~/.claude/skills/.s08-verify-dangling-refs.sh
    CLAUDE_DIR=~/your-private-harness bash ~/your-private-harness/skills/.s08-verify-dangling-refs.sh

It exits 1 when this file is missing; when a canonical target or preserved wrapper or
worker is missing (7 named files, the 8 testarch presets, the 3 research presets); when a
demoted wrapper lacks its `skill-unification s08` marker; or when
`commands/adversarial-review.md` lacks its hunter section or `skills/diagnose/SKILL.md`
lacks its `--deep` mode. Its sweep over `commands/`, `agents/`, `skills/`, `_plans/*`,
`CLAUDE.md` and per-project `MEMORY.md` only counts the files that name a demoted entry;
it never fails on them.

Two limits. It checks only the files it names: it does not check that the research
guide exists. And `skills/diagnose` resolves only where `.agents` sits beside the tree (as
`~/.agents` sits beside `~/.claude` and `~/your-private-harness`); in a
`~/your-private-harness/.claude/worktrees/*` checkout its two `diagnose` checks fail.
