---
name: janitor-stop
description: Immediately stop the agent janitor's unattended cleanup — unload both launchd jobs (reap + prune) and remove their plists, then verify nothing is still scheduled. Use when the user says "stop the janitor", "janitor stop", "kill the janitor", "turn off the background cleanup", or wants the automated reaping/pruning halted now. Read-only checks belong to /janitor-status; this one changes the machine.
disable-model-invocation: true
allowed-tools: [Bash]
effort: low  # fixed three-command sequence plus verification; no judgment beyond reporting what happened
---

# Janitor Stop

Stop the unattended machinery NOW, then prove it is stopped. No questions
first — the user invoking this skill IS the confirmation, and speed is the
point. Ask nothing; report afterwards.

## 1. Stop

```bash
launchctl bootout gui/$UID/com.gearbox.agent-janitor.reap
launchctl bootout gui/$UID/com.gearbox.agent-janitor.prune
python3 ~/.claude/scripts/agent_janitor.py uninstall
```

`bootout` also terminates a run that is mid-flight, not just future ticks.
"No such service" / "No such process" on any line means that job was already
stopped — fine, continue; it is not an error.

## 2. Verify stopped — run the checks, don't assert from step 1's output

```bash
launchctl print gui/$UID/com.gearbox.agent-janitor.reap  2>&1 | head -1   # want: Could not find service
launchctl print gui/$UID/com.gearbox.agent-janitor.prune 2>&1 | head -1   # want: Could not find service
ls ~/Library/LaunchAgents/ | grep -i janitor                              # want: nothing
pgrep -fl "agent_janitor.py (reap|prune)"                                 # want: nothing
```

All four clean = stopped. Anything else = say exactly what survived and what
you ran; do not report success.

## 3. Report — including what this did NOT stop

State plainly:

- Unattended reap (every 30 min) and prune (every 6 h): **stopped**.
- If you opted in to the **SessionEnd hook**, it is still active — when a
  Claude session ends, the reaper still fires for that session's own
  leftovers. This is scoped and harmless, but it is not "off". To disable it
  too: remove the `SessionEnd` entry that runs `agent-janitor-sessionend.py`
  from your `~/.claude/settings.json`. Offer to do this; don't do it unasked.
- Already-deleted files are gone; stopping changes nothing retroactively.
- To restart later: `python3 ~/.claude/scripts/agent_janitor.py install`.
