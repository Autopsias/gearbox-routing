# Safe refactor

A subagent that splits a large file behind a facade, one step at a time, with
the tests run after each step. A hook stops Claude when it starts a file split
without it and offers the subagent instead.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any with `bash` and `jq` |
| **Needs** | `jq`; `git` for the subagent's checkpoints |

## What you get

- `harness/agents/safe-refactor.md` — the subagent. Steps: baseline, facade, move, verify, clean up.
- `harness/agents/references/safe-refactor/` — `facade-pattern.md`, `mikado-method.md`, `troubleshooting.md`. The subagent reads them from `~/.claude/agents/references/safe-refactor/`.
- `harness/hooks/safe-refactor-advisory.sh` — a `PreToolUse` hook for Write. It blocks a new file that looks like a module split (`_legacy.*`, `internal.*`, a new `__init__.py`, `index.ts`, `index.js` or `mod.rs` next to an existing file).

## Install

```bash
mkdir -p ~/.claude/agents/references ~/.claude/hooks
cp harness/agents/safe-refactor.md ~/.claude/agents/
cp -R harness/agents/references/safe-refactor ~/.claude/agents/references/
cp harness/hooks/safe-refactor-advisory.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/safe-refactor-advisory.sh
```

Optional: merge this entry into the `"PreToolUse"` list in `~/.claude/settings.json`:

```json
{ "matcher": "Write", "hooks": [ { "type": "command", "command": "~/.claude/hooks/safe-refactor-advisory.sh" } ] }
```

## Check it works

```bash
echo '{"tool_input":{"file_path":"/tmp/x/_legacy.py"}}' | bash ~/.claude/hooks/safe-refactor-advisory.sh; echo "exit $?"
```

It prints a box with the options and `exit 2`. A normal path such as
`/tmp/x/a.py` prints nothing and `exit 0`. In Claude Code, `/agents` lists
`safe-refactor`.

## Remove

```bash
rm ~/.claude/agents/safe-refactor.md ~/.claude/hooks/safe-refactor-advisory.sh
rm -r ~/.claude/agents/references/safe-refactor
```

Delete the `settings.json` entry **before** you delete the hook file.

## Cautions

- **The hook entry has no file check.** If you delete the script and keep the
  entry, every Write shows a hook error.
- The hook blocks the write. You then choose: run the subagent, or approve the
  write.
- The `/code-quality` command and the `repo-health` skill also use this subagent.
