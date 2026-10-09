# Small hooks

Single-purpose hooks. Only one of them is useful to most people as shipped.

| | |
|---|---|
| **Status** | Optional · see the table |
| **Platform** | Any with `python3` (the audio hook needs a sound player) |
| **Needs** | See the table |

## The hooks

| File | What it does | Use it? |
|---|---|---|
| `harness/hooks/agent-elapsed-nag.py` | When a subagent runs for 30 minutes or more, it adds a reminder to Claude's context: report the elapsed time and a cheaper option. | **Yes**, if you run long subagents. Not wired in `harness/settings.json`; the entries are below. |
| `harness/hooks/routing-cadence-check.py` | At session start, says when the routing outcome log has grown by about 30 records since the last analysis. | Only with the routing eval files of the original setup (`~/.claude/evals/routing/`), which this repo does not ship. Without them it prints nothing. Its `settings.json` entry checks that the file exists. |
| `harness/hooks/audio-hooks.sh` | Plays a sound when a task ends or when Claude needs input. | Not as shipped: it needs sound files and a preferences file that this repo does not ship. |
| `harness/hooks/skill-eval-hook.ts` | Adds a "check your skills first" reminder to prompts that contain words such as "implement" or "build". | Only if you use the [BMAD](bmad.md) workflow; the reminder names BMAD commands. Needs `npx tsx`. |
| `harness/hooks/gearbox-drift-warn.sh` | Ran the original author's deploy check. | **No.** It expired on 2026-08-08 and now only prints "remove me". |

## Install the agent nag

```bash
mkdir -p ~/.claude/hooks
cp harness/hooks/agent-elapsed-nag.py ~/.claude/hooks/
```

Merge into the `"hooks"` object of `~/.claude/settings.json` (from the hook's
own docstring; add the `PreToolUse` object to your existing list):

```json
"PreToolUse": [
  { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/agent-elapsed-nag.py pre", "timeout": 5 } ] }
],
"SubagentStop": [
  { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/agent-elapsed-nag.py stop", "timeout": 5 } ] }
]
```

The `PreToolUse` entry has no matcher on purpose: the hook must see every tool
call to notice the elapsed time.

## Check it works

```bash
python3 harness/hooks/test_agent_elapsed_nag.py
```

It prints `agent-elapsed-nag self-check: all assertions passed`.

In use: after a subagent runs for 30 minutes, the next tool call carries a
`[cost] The oldest open dispatch has been running N minutes` line.

## Remove

Delete the hook's `settings.json` entries first, then the file:

```bash
rm ~/.claude/hooks/agent-elapsed-nag.py
rm -rf ~/.gearbox-state/agent-nag
```

## Cautions

- The nag cannot match a start to a stop for each agent. On any `SubagentStop`
  it removes the oldest open dispatch, so it can remind you too often.
- The nag message refers to a rule in the original author's `CLAUDE.md`. In
  your setup it is only a reminder.
