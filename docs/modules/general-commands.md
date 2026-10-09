# General commands

Small slash commands. Install only the rows you want.

| | |
|---|---|
| **Status** | Optional · see the table |
| **Platform** | Any |
| **Needs** | See the table |

## The commands

| Command | What it does | Needs | Cautions |
|---|---|---|---|
| `/review` | Sends `--deep` to `/adversarial-review`, and everything else to Claude Code's built-in `/code-review`. | [Adversarial review](adversarial-review.md) for `--deep`. | Only you can start it. |
| `/research <topic>` | Marks the session as research, so that automatic compaction waits much longer. | The [compaction policy](compaction.md) hook. Without it the command changes nothing. | A large context costs more on each turn. |
| `/wait-what` | Asks Claude to explain its last reply again in plain words. Uses no tools. | Nothing | — |
| `/nextsession [focus]` | Writes a self-contained prompt to continue the work in a new session. | Nothing | — |
| `/improve [focus]` | Looks back over your sessions and proposes missing rules, skills and memory; `/improve audit` scores what exists and proposes deletions. | `harness/references/improve/`, `harness/scripts/improve_ask_classes.py` | It edits `CLAUDE.md`, skills, memory and settings after you accept a finding. Some steps assume the original author's deploy tool; skip them. |
| `/pr [status\|sync\|create\|merge]` | `/pr` alone stages, commits and pushes. Other forms show the PR status, merge the base branch in, or hand off to a subagent. | `git`, `gh` (signed in), `jq`; the `pr-workflow-manager` subagent for `create` and `merge`. | **`/pr` alone runs `git add -A`, commits and pushes without asking.** |

## Install

```bash
mkdir -p ~/.claude/commands ~/.claude/references ~/.claude/agents ~/.claude/scripts
cp harness/commands/{review,research,wait-what,nextsession,pr}.md ~/.claude/commands/
cp harness/commands/improve.md ~/.claude/commands/
cp -R harness/references/improve ~/.claude/references/
cp harness/scripts/improve_ask_classes.py ~/.claude/scripts/
cp harness/agents/pr-workflow-manager.md ~/.claude/agents/      # for /pr create and /pr merge
```

## Check it works

Type `/wait-what` after any answer: you get a short plain-language version and
no tool runs. In a repo with no pull request, `/pr status` prints
`No PR for current branch`.

## Remove

```bash
rm ~/.claude/commands/{review,research,wait-what,nextsession,improve,pr}.md
rm -r ~/.claude/references/improve
rm ~/.claude/scripts/improve_ask_classes.py
rm ~/.claude/agents/pr-workflow-manager.md
```

## Archived

`harness/commands-archive/` holds `/parallel` and `/user-testing`. The
subagents they call were retired, so the original setup archived them too.
Do not install them.
