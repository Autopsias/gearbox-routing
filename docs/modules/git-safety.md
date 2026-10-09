# Git safety guard

Stops Claude Code's Bash tool from running a command that throws away
uncommitted work (`git reset --hard`, `git checkout .`, `git restore .`,
`git clean -f`). A rule file also tells agents how to handle stashes and deletes.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any with `git` and `python3` |
| **Needs** | Nothing |

## What you get

- `harness/hooks/git-tree-guard.py` — a `PreToolUse` hook for Bash. It blocks only when `git status` shows changed tracked files.
- `harness/rules/git-safety.md` — rule text: no `git stash` without a clean-up path, no delete of a file you did not create without confirmation.

## Install

```bash
mkdir -p ~/.claude/hooks ~/.claude/rules
cp harness/hooks/git-tree-guard.py ~/.claude/hooks/
cp harness/rules/git-safety.md ~/.claude/rules/
```

Merge this entry into the `"PreToolUse"` list in `~/.claude/settings.json`:

```json
{ "matcher": "Bash", "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/git-tree-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10 } ] }
```

## Check it works

In a repo with an uncommitted change, ask Claude to run `git reset --hard`.
Claude Code refuses it with a `BLOCKED … would discard N tracked modification(s)`
message. In a clean repo the command is not blocked.

## Remove

```bash
rm ~/.claude/hooks/git-tree-guard.py ~/.claude/rules/git-safety.md
```

Then delete the `PreToolUse` entry.

## Cautions

- It reads the command text. A command built from variables, an alias or a
  script gets past it. It is a seat belt, not a security boundary.
- To allow one reset on purpose, start the command with
  `GEARBOX_ALLOW_DIRTY_RESET=1`.
- `git-safety.md` names a playbook file that is not in this repo. Only the
  name is missing; the rule still works.
