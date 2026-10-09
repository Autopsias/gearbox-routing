# Context compaction policy

Decides when Claude Code may compact the conversation automatically. It holds
the compaction back until a safe moment, such as the start of a turn or a pause
between batches of plan work. After a compaction, it tells the agent where it was.

| | |
|---|---|
| **Status** | Optional · experimental — tuned to one person's measurements |
| **Platform** | macOS or Linux (uses `fcntl` file locks, which Windows does not have) |
| **Needs** | `python3`. Nothing else. |

## What you get

- `harness/hooks/compact-policy.py` — the hook and its rules. Subcommands: `prompt`, `pre-compact`, `post-compact`, `session-start`, `safe-point`, `status`, `activation`.
- `harness/hooks/compact_store.py` — state: decision ledger, policy files, off switch.
- `harness/hooks/compact_classify.py` — reads the first prompt and tells planning sessions from build sessions.
- `harness/hooks/compact_turn.py` — the "compact at the start of a turn, not in the middle of a task" rule.
- `harness/hooks/compact_safepoint.py` — writes the `safe-point ok|hold` marks that some skills set.
- `harness/hooks/compact_reorient.py` — after a compaction, adds a short "where you are" note.
- `harness/hooks/compact_activation.py` — bookkeeping for `status` and `activation`.
- `harness/hooks/context_tokens.py` — reads the current context size from the session transcript.
- `harness/hooks/window_trust.py` — the 100,000-token margin below which the hook never blocks.

The other `compact_*` files and the `test_*` files in `harness/hooks/` are tests. You do not need them.

## Install

```bash
mkdir -p ~/.claude/hooks
cp harness/hooks/compact-policy.py \
   harness/hooks/compact_{store,classify,turn,safepoint,reorient,activation}.py \
   harness/hooks/context_tokens.py harness/hooks/window_trust.py ~/.claude/hooks/
```

Merge these four entries into the `"hooks"` object of `~/.claude/settings.json`:

```json
"SessionStart": [
  { "matcher": "compact", "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/compact-policy.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" session-start", "timeout": 10 } ] }
],
"UserPromptSubmit": [
  { "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/compact-policy.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" prompt", "timeout": 10 } ] }
],
"PreCompact": [
  { "matcher": "auto", "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/compact-policy.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" pre-compact", "timeout": 10 } ] }
],
"PostCompact": [
  { "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/compact-policy.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\" post-compact", "timeout": 10 } ] }
]
```

If an event key already exists in your file, add only the object inside its
brackets. Each command starts with a file check, so a missing file does no harm.

## Check it works

1. Start a new Claude Code session and send one prompt.
2. Run `python3 ~/.claude/hooks/compact-policy.py status`.

You see the session type and the last decisions. The folder
`~/.gearbox-state/compaction/policy/` now holds a file for the session.

## Remove

```bash
rm ~/.claude/hooks/compact-policy.py \
   ~/.claude/hooks/compact_{store,classify,turn,safepoint,reorient,activation}.py \
   ~/.claude/hooks/context_tokens.py ~/.claude/hooks/window_trust.py
rm -rf ~/.gearbox-state/compaction    # optional: the ledger and policy files
```

Then delete the four entries from `settings.json`. To turn it off without
removing it, set `GEARBOX_COMPACT_POLICY=off` or create the file
`~/.gearbox-state/compaction/DISABLED`.

## Cautions

- **It can block an automatic compaction.** It blocks for planning sessions,
  for sessions that set a "hold" mark, and when a turn is already running. It
  always allows a manual `/compact`, an unknown context size, and any case with
  less than 100,000 tokens left.
- It fails open: when the hook hits an error, compaction goes ahead. If one of
  its modules fails to import, it prints one line to stderr saying it is
  disabled. The operator subcommands refuse and exit with code 2 instead.
- It writes its state outside `~/.claude`, in `~/.gearbox-state/compaction/`.
- It knows some command names by heart (`/plan-execute`, `/plan-builder`,
  `/grill-me` and others). Without those skills, it classifies a session from
  the wording of the first prompt.
- The `/research` command in the [general commands](general-commands.md)
  module works only with this hook installed.
