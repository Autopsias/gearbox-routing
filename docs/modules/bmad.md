# BMAD method and epic builds (`/bmad-*`, `/epic-dev`)

Slash commands for the [BMAD method](https://github.com/bmad-code-org/BMAD-METHOD)
(product brief, architecture, stories, test design and more), and `/epic-dev`,
which builds a BMAD epic story by story with a subagent per phase.

| | |
|---|---|
| **Status** | Optional · stable, but **does nothing without BMAD installed in your project** |
| **Platform** | Any with `git` |
| **Needs** | A `_bmad/` folder in each project: every command loads its files from there. `/epic-dev` also needs the test and CI commands from [test and CI](test-and-ci.md) (`/ship-tail`, `/ci-orchestrate`). The epic conductor needs the [plan pipeline](plan-pipeline.md). |

## What you get

- `harness/commands/bmad-*.md` — 52 thin launchers:
  - agents (`bmad-agent-*`, 11): analyst, architect, developer, product manager, QA, scrum master, tech writer, UX designer and others;
  - planning and build workflows (`bmad-bmm-*`, about 25): product brief, architecture, UX design, epics and stories, story creation, dev story, code review, sprint planning, retrospective, research, diagrams;
  - test architecture (`bmad-tea-*`, 9): ATDD, test design, test review, traceability, NFR, CI, framework;
  - core tasks: `bmad-help`, `bmad-brainstorming`, `bmad-party-mode`, `bmad-shard-doc`, `bmad-index-docs`, editorial reviews.
- `harness/commands/epic-dev.md` and `harness/references/epic-dev/` — the epic build command and its phase files.
- `harness/agents/epic-*.md` — 9 phase subagents: story creator, story validator, ATDD writer, implementer, code reviewer, test expander, test reviewer, quality gate, test fixer.
- `harness/skills/epic-dev-conductor/` — runs `/epic-dev` across epics under a build policy in your project's `CLAUDE.md`.

## Install

```bash
mkdir -p ~/.claude/commands ~/.claude/references ~/.claude/agents ~/.claude/skills
cp harness/commands/bmad-*.md harness/commands/epic-dev.md ~/.claude/commands/
cp -R harness/references/epic-dev ~/.claude/references/
cp harness/agents/epic-*.md ~/.claude/agents/
cp -R harness/skills/epic-dev-conductor ~/.claude/skills/
```

Then install BMAD in each project where you use these commands. The epic
reference files name `npx bmad-method install`; check the BMAD project's own
install guide first.

## Check it works

In a project that has `_bmad/`, type `/bmad-help`. It tells you the next step.
Type `/epic-dev` with no epic number: it prints its usage line.

## Remove

```bash
rm ~/.claude/commands/bmad-*.md ~/.claude/commands/epic-dev.md ~/.claude/agents/epic-*.md
rm -r ~/.claude/references/epic-dev ~/.claude/skills/epic-dev-conductor
```

## Cautions

- **52 commands is a lot of context.** Each one adds its name and description
  to every session. Install them only if you use BMAD.
- `/epic-dev --auto` turns off the confirmation prompts and runs many
  subagents. It commits and pushes after each story.
- `/epic-dev --uat` uses the user-testing commands, which do not work as
  shipped (see [test and CI](test-and-ci.md)).
