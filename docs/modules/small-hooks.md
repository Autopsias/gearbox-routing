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
| `harness/hooks/agent-elapsed-nag.py` | When a subagent runs for 30 minutes or more, it adds a reminder to Claude's context: report the elapsed time and a cheaper option. It also tracks background `Bash` commands. No hook event marks the end of such a command, so a third entry (`Stop`, below) reads the list of running tasks that Claude Code sends at each stop. It nags once for each kind of background task in a session, and forgets a task after 24 hours. | **Yes**, if you run long subagents. Not wired in `harness/settings.json`; the entries are below. |
| `harness/hooks/routing-cadence-check.py` | At session start, says when the routing outcome log has grown by about 30 records since the last analysis. It also prints one line when your last whole-history review of how you use Claude Code is more than 30 days old. It reads the date from `~/.dyno/last-whole-history-review`, or else from `~/.claude/reflection-notes.md`. `harness/scripts/dyno_paths.py` holds the stamp path; without it the hook uses the same default. | Only with the routing eval files of the original setup (`~/.claude/evals/routing/`), which this repo does not ship. Without them it prints nothing. Its `settings.json` entry checks that the file exists. |
| `harness/hooks/audio-hooks.sh` | Plays a sound when a task ends or when Claude needs input. | Not as shipped: it needs sound files and a preferences file that this repo does not ship. |
| `harness/hooks/skill-eval-hook.ts` | Adds a "check your skills first" reminder to prompts that contain words such as "implement" or "build". | Only if you use the [BMAD](bmad.md) workflow; the reminder names BMAD commands. Needs `npx tsx`. |
| `harness/hooks/gearbox-drift-warn.sh` | Ran the original author's deploy check. | **No.** It expired on 2026-08-08 and now only prints "remove me". |
| `harness/hooks/skill-usage-log.py` | `PreToolUse` on `Skill`: writes one line per skill call to `~/.gearbox-state/skills/usage.ndjson`, so you can measure which skills run and which never trigger. | If you want to prune or tune skills from data. Its entry is in `harness/settings.json`. Test: `bash harness/hooks/test_skill_usage_log.sh`. |
| `harness/hooks/instructions-inventory.py` | `InstructionsLoaded`: writes one line per loaded instruction file (`CLAUDE.md`, rules) to `instructions.ndjson`, so you can see what each project really loads. | If you want to trim your instruction files. Its entry is in `harness/settings.json`. Test: `python3 -m pytest harness/hooks/test_instructions_inventory.py`. |

## Install the agent nag

```bash
mkdir -p ~/.claude/hooks
cp harness/hooks/agent-elapsed-nag.py ~/.claude/hooks/
```

`./install-module small-hooks` adds this to `~/.claude/settings.json` for you. By hand: add each entry to the matching list under `"hooks"`, and keep your own entries.

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/agent-elapsed-nag.py\"; [ -f \"$p\" ] || exit 0; python3 \"$p\" pre", "timeout": 15}]}
    ],
    "SubagentStop": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/agent-elapsed-nag.py\"; [ -f \"$p\" ] || exit 0; python3 \"$p\" stop", "timeout": 5}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/turnend-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10, "statusMessage": "Turn-end guard: open plan work?"}, {"type": "command", "command": "p=\"$HOME/.claude/hooks/agent-elapsed-nag.py\"; [ -f \"$p\" ] || exit 0; python3 \"$p\" stopbg", "timeout": 5}]}
    ]
  }
}
```

The `PreToolUse` entry matches every tool (`*`) on purpose: the hook must see
every tool call to notice the elapsed time.

## Check it works

```bash
python3 harness/hooks/test_agent_elapsed_nag.py
```

It prints `agent-elapsed-nag self-check: all assertions passed`.

In use: after a subagent runs for 30 minutes, the next tool call carries a
`[cost] The oldest open dispatch has been running N minutes` line.

## Remove

Run `./install-module --remove small-hooks` to take the hook's entries out of `settings.json`. Then delete the file:

```bash
rm ~/.claude/hooks/agent-elapsed-nag.py
rm -rf ~/.gearbox-state/agent-nag
```

## Cautions

- The `Stop` entry above holds two hooks, `turnend-guard.py` and `agent-elapsed-nag.py`, because the harness wires them as one entry. The installer adds it once, whichever of the two modules you install first. `./install-module --remove` for either module removes the whole entry, so run `--remove` for one and re-add the other by hand if you keep it. Each command skips itself when its file is missing.
- The nag cannot match a start to a stop for each agent. On any `SubagentStop`
  it removes the oldest open dispatch, so it can remind you too often.
- The nag message refers to a rule in the original author's `CLAUDE.md`. In
  your setup it is only a reminder.
