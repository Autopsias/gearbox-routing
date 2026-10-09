# Engineering skills (`/tdd`, `/improve-codebase-architecture`, `/setup-matt-pocock-skills`)

Three skills for day-to-day engineering, adapted from Matt Pocock's public
skills. `/tdd` works one failing test at a time (red, green, refactor).
`/improve-codebase-architecture` proposes refactors that turn many shallow
modules into fewer deep ones. `/setup-matt-pocock-skills` writes the per-repo
settings that these skills read.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any. `gh` (GitHub) or `glab` (GitLab) for the setup skill's issue-tracker step. |
| **Needs** | `/improve-codebase-architecture` links to two templates in [grilling](grilling.md) (`grill-with-docs`). |

## What you get

- `harness/skills/tdd/` — the loop, plus notes on interfaces, mocks, deep modules and refactoring.
- `harness/skills/improve-codebase-architecture/` — the process and its glossary (`DEEPENING.md`, `INTERFACE-DESIGN.md`, `LANGUAGE.md`).
- `harness/skills/setup-matt-pocock-skills/` — writes `docs/agents/` and an `## Agent skills` block in your repo's `AGENTS.md` or `CLAUDE.md`.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/tdd harness/skills/improve-codebase-architecture harness/skills/setup-matt-pocock-skills ~/.claude/skills/
```

## Check it works

Say "build <a small feature> with TDD". Claude asks which behaviours to test
before it writes code, then writes one failing test.

## Remove

```bash
rm -r ~/.claude/skills/tdd ~/.claude/skills/improve-codebase-architecture ~/.claude/skills/setup-matt-pocock-skills
```

## Cautions

- `/improve-codebase-architecture` can write `CONTEXT.md` and offer ADR files
  in your repo. It does not edit source code.
- `/setup-matt-pocock-skills` writes files in your repo after you confirm the
  drafts. Its issue-tracker and label sections serve skills (`to-issues`,
  `triage` and others) that are not in this repo.
