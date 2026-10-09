# Agent janitor (macOS)

Cleans up after Claude Code and Codex sessions. Every 30 minutes it kills
helper processes that a dead session left behind. Once a day it deletes old
temp folders and old Codex session logs.

| | |
|---|---|
| **Status** | Optional · experimental |
| **Platform** | **macOS only** (uses `launchd`, and the macOS forms of `ps` and `lsof`) |
| **Needs** | `/usr/bin/python3`. Codex is optional. |

## What you get

- `harness/scripts/agent_janitor.py` — the tool. Subcommands: `reap`, `prune`, `status`, `install`, `uninstall`.
- `harness/scripts/janitor_paths.py` — path safety checks that the tool imports. Required.
- `harness/scripts/launchd/` — the two `launchd` job templates. `install` fills in your home path and loads them.
- `harness/hooks/agent-janitor-sessionend.py` — optional `SessionEnd` hook that cleans up the ending session at once.
- `harness/skills/janitor-status/` — `/janitor-status`: a health report.
- `harness/skills/janitor-stop/` — `/janitor-stop`: stops both jobs at once.

## Install

Look at what it would do before you load the jobs:

```bash
mkdir -p ~/.claude/scripts ~/.claude/hooks ~/.claude/skills
cp harness/scripts/agent_janitor.py harness/scripts/janitor_paths.py ~/.claude/scripts/
cp -R harness/scripts/launchd ~/.claude/scripts/
cp harness/hooks/agent-janitor-sessionend.py ~/.claude/hooks/
cp -R harness/skills/janitor-status harness/skills/janitor-stop ~/.claude/skills/
python3 ~/.claude/scripts/agent_janitor.py reap --dry-run
python3 ~/.claude/scripts/agent_janitor.py prune --dry-run
python3 ~/.claude/scripts/agent_janitor.py install
```

Optional — the session-end hook. Merge this into the `"hooks"` object of
`~/.claude/settings.json`:

```json
"SessionEnd": [
  { "hooks": [ { "type": "command", "command": "~/.claude/hooks/agent-janitor-sessionend.py", "timeout": 30, "async": true } ] }
]
```

## Check it works

Run `/janitor-status` in Claude Code, or:

```bash
python3 ~/.claude/scripts/agent_janitor.py status
```

Look for a recent `heartbeat:` line.

## Remove

Run `/janitor-stop`, or `python3 ~/.claude/scripts/agent_janitor.py uninstall`. Then:

```bash
rm ~/.claude/scripts/agent_janitor.py ~/.claude/scripts/janitor_paths.py ~/.claude/hooks/agent-janitor-sessionend.py
rm -r ~/.claude/scripts/launchd ~/.claude/skills/janitor-status ~/.claude/skills/janitor-stop
```

Delete the `SessionEnd` entry if you added it.

## Cautions

- **After `install`, it kills processes and deletes files without asking.**
  - It kills only processes that belong to a session that is dead for more
    than 10 minutes. If a run would kill more than 20, it refuses the whole run
    (exit 3) and kills nothing. Other matches are reported, not killed.
  - Once a day it deletes: session scratch folders older than 14 days under
    `/private/tmp/claude-<uid>/`, plugin temp folders older than 7 days, Codex
    session logs older than 60 days, and `/private/tmp/claude-*-cwd` marker
    files older than 7 days. If a run would delete more than 20 GB or 500
    paths, it refuses the whole run and deletes nothing.
  - A protect list stops it from deleting your projects, settings, skills,
    hooks, history and credentials. It refuses to delete in a scratch folder
    that another user owns or can write.
- Logs: `~/.claude/logs/agent-janitor.log`.
- The `/janitor-status` text mentions `gearbox deploy`, a tool of the original
  author. Ignore those lines.
