# Investigation (`/blindspot`, `/diagnose`)

`/blindspot` reads the history and the files of an unfamiliar code area and
reports the traps before you edit it. `/diagnose` takes a hard bug from a
repeatable failure to a fix and a regression test.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any |
| **Needs** | `git` for `/blindspot`. `/diagnose --deep` also needs the `digdeep` subagent. |

## What you get

- `harness/skills/blindspot/` — a five-step scan of git history and code. Writes one HTML report (Markdown if HTML is not possible). Findings are labelled Landmine, Convention, History or Missing concept.
- `harness/skills/diagnose/` — a six-phase debug loop: reproduce, minimise, guess, instrument, fix, add a regression test. Includes `scripts/hitl-loop.template.sh`, a template for a repro script where you do some steps by hand.
- `harness/agents/digdeep.md` — a read-only root-cause subagent for `/diagnose --deep`. Its tool list names the Exa, Perplexity, Ref and Semgrep MCP servers; it works with the ones you have.

## Install

```bash
mkdir -p ~/.claude/skills ~/.claude/agents
cp -R harness/skills/blindspot harness/skills/diagnose ~/.claude/skills/
cp harness/agents/digdeep.md ~/.claude/agents/     # only for /diagnose --deep
```

## Check it works

In a git repo, type `/blindspot src/` (any folder). You get a report with
labelled findings that name files and commits.

## Remove

```bash
rm -r ~/.claude/skills/blindspot ~/.claude/skills/diagnose
rm ~/.claude/agents/digdeep.md
```

## Cautions

- `/blindspot` changes nothing in your repo; it writes one report.
- `/diagnose` edits code and adds a test in its last phases. It tells you to
  remove its debug code afterwards.
