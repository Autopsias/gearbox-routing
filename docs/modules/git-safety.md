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
- `harness/rules/git-safety.md` — rule text: no `git stash` without a clean-up path, no delete of a file you did not create without confirmation (a delete made reversible first, with a saved patch or a move to a kept folder, needs only a normal yes), read `origin/main` before you answer about the code, fast-forward your checkout after a merge, and never hide the output of `git commit` (a hook can block the commit while the rest of the command chain goes on).

## Install

```bash
mkdir -p ~/.claude/hooks ~/.claude/rules
cp harness/hooks/git-tree-guard.py ~/.claude/hooks/
cp harness/rules/git-safety.md ~/.claude/rules/
mkdir -p ~/.claude/docs && cp harness/docs/reference_git_safety_playbook.md ~/.claude/docs/   # optional: the worked patterns the rule points to
```

`./install-module git-safety` adds this to `~/.claude/settings.json` for you. By hand: add each entry to the matching list under `"hooks"`, and keep your own entries.

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/git-tree-guard.py\"; [ -f \"$p\" ] || exit 0; f(){ echo \"git-tree-guard: $p failed ($1) - the guard itself failed, not the command. Refusing this Bash call; the same command can be tried again.\" >&2; exit 2; }; in=$(cat) || f read; t=$(printf '%s' \"$in\" | LC_ALL=C tr -d '\\042\\047\\134') || f strip; [ -z \"$in\" ] || [ -n \"$t\" ] || f strip; case \"$t\" in *[gG][iI][tT]*) ;; *) case \"$in\" in *'\\u'*) ;; *) exit 0;; esac;; esac; printf '%s' \"$in\" | python3 \"$p\"; s=$?; [ $s -eq 0 ] && exit 0; [ $s -eq 2 ] && exit 2; f \"exit $s\"", "timeout": 10, "onFailure": "block"}]}
    ]
  }
}
```

## Check it works

In a repo with an uncommitted change, ask Claude to run `git reset --hard`.
Claude Code refuses it with a `BLOCKED … would discard N tracked modification(s)`
message. In a clean repo the command is not blocked.

## Remove

```bash
rm ~/.claude/hooks/git-tree-guard.py ~/.claude/rules/git-safety.md
```

Run `./install-module --remove git-safety` to take the `PreToolUse` entry out of `settings.json`.

## Cautions

- It reads the command text. A command built from variables, an alias or a
  script gets past it. It is a seat belt, not a security boundary.
- To allow one reset on purpose, start the command with
  `GEARBOX_ALLOW_DIRTY_RESET=1`.
- The entry above fails closed. A shell wrapper skips Python when the command
  has no `git` in its text. When the guard crashes, times out or python3 is
  missing, `"onFailure": "block"` refuses the command with one line that says the
  guard failed, and you can try the same command again. A missing guard file
  still allows the command. If python3 is gone, every command with `git` in its
  text stays refused until you fix python3 or remove the entry from
  `settings.json` by hand.
