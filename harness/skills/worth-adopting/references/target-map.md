# Where a harness keeps things

Read this file at step 4 of `SKILL.md`, before the target search.

Where a harness keeps things (gearbox paths; other repos have their equivalents):

| What | Where |
|---|---|
| Skills and commands | `skills/*/SKILL.md`, `commands/*.md` (the `description:` line is the index) |
| Hooks and what fires them | `hooks/`, the `hooks` block of `settings.json` |
| Agents and model routing | `agents/`, `model-routing.yaml` |
| Standing rules and their reasons | `CLAUDE.md`, `rules/`, `docs/reference_rules_rationale.md` |
| Decisions on record | `docs/adr/`, `_plans_index.md`, `docs/worth-adopting/LEDGER.md` |
| Pains on record | `~/.claude/projects/*/memory/MEMORY.md`, `improve-learnings.md`, `evals/routing/MISROUTES.md` |

A proposal about an app (run it, test it, deploy it) is searched in each active app repo in
this same step, in `.claude/skills`, `.claude/commands`, `.claude/rules`, the tests and the CI
files (2026-11-17: a search of skill names alone said no repo could run its app; example-project
had `/uat` and `/eval` as commands).
