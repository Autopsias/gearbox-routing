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

Optional: the `/improve` nudge. `harness/hooks/improve-nudge.py` tells Claude
to offer `/improve` at three moments: at your third pushback prompt in a
session, at session start when the last run is old, and once after a session
pushes, merges or deploys. At that last moment it writes a short digest of the
session (`harness/scripts/improve_digest.py`) and asks Claude to have a Sonnet
subagent read it with `references/improve/triage-prompt.md`, then show the
hints. It never runs `/improve` by itself. State goes to
`~/.gearbox-state/improve/`; set `IMPROVE_NUDGE=off` to silence it.

```bash optional
mkdir -p ~/.claude/hooks ~/.claude/scripts
cp harness/hooks/improve-nudge.py ~/.claude/hooks/
cp harness/scripts/improve_digest.py ~/.claude/scripts/
```

```json optional
{
  "hooks": {
    "SessionStart": [
      {"matcher": "startup", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/improve-nudge.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" session-start", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/improve-nudge.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" prompt", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/improve-nudge.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" finished", "timeout": 10}]}
    ]
  }
}
```

`./install-module general-commands --with-optional` adds the nudge and its
three entries; without `--with-optional` it is left out.

## Check it works

Type `/wait-what` after any answer: you get a short plain-language version and
no tool runs. In a repo with no pull request, `/pr status` prints
`No PR for current branch`. For the nudge: `python3 -m pytest harness/hooks/test_improve_nudge.py`.

## Remove

```bash
rm ~/.claude/commands/{review,research,wait-what,nextsession,improve,pr}.md
rm -r ~/.claude/references/improve
rm ~/.claude/scripts/improve_ask_classes.py
rm ~/.claude/agents/pr-workflow-manager.md
rm -f ~/.claude/hooks/improve-nudge.py ~/.claude/scripts/improve_digest.py   # the nudge, if installed
```

`./install-module --remove general-commands` also takes the nudge's three
entries out of `settings.json`.

## Archived

`harness/commands-archive/` holds `/parallel` and `/user-testing`. The
subagents they call were retired, so the original setup archived them too.
Do not install them.
