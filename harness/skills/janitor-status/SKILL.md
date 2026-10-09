---
name: janitor-status
description: Health readout for the agent janitor (the background reaper/pruner installed with the agent janitor). Reports whether both launchd jobs are alive, what was killed/deleted lately, and flags issues worth fixing — a stale heartbeat, failed runs, refused blast-radius caps, growing report-only counts. Use when the user says "janitor status", "is the janitor running", "what has the janitor done", "what did the janitor kill/delete", or asks whether the background cleanup is healthy. Not for stopping it (that's /janitor-stop) and not for general disk usage questions.
allowed-tools: [Bash, Read]
effort: low  # mechanical: run three commands, classify their output against a fixed issue table
disable-model-invocation: true
---

# Janitor Status

## Read this first: what the janitor does to your machine

- **It runs only on macOS.** launchd schedules it, and it reads macOS `ps`
  and `lsof` output. It is not built or tested for Linux or Windows.
- **It kills processes and deletes files without asking.** Once installed, it
  kills orphaned agent processes every 30 minutes. It also deletes old temp
  files and old Codex session logs, at most once a day. Nothing prompts you
  first. Deleted files are gone.
- **Nothing runs until you install it.** `python3 ~/.claude/scripts/agent_janitor.py install`
  loads both launchd jobs. A dry run touches nothing:
  `agent_janitor.py reap --dry-run` and `agent_janitor.py prune --dry-run`.
- **To turn it off:** run `/janitor-stop`, or
  `python3 ~/.claude/scripts/agent_janitor.py uninstall`.
- **The session-end hook is opt-in.** `hooks/agent-janitor-sessionend.py` runs
  `reap --apply` for the ending session only when your `settings.json` wires
  it. To opt in, add this under `"hooks"` in `~/.claude/settings.json`:

```json
"SessionEnd": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "~/.claude/hooks/agent-janitor-sessionend.py",
        "timeout": 30,
        "async": true
      }
    ]
  }
]
```

Answer three questions, in this order, and lead with the verdict: **is it
running · what has it done lately · anything to fix**. Run the real commands —
never report from memory or from this file's examples.

## 1. Is it running?

```bash
python3 ~/.claude/scripts/agent_janitor.py status
launchctl print gui/$UID/com.gearbox.agent-janitor.reap  2>&1 | grep -E "state|runs =|last exit code" || echo "REAP JOB NOT LOADED"
launchctl print gui/$UID/com.gearbox.agent-janitor.prune 2>&1 | grep -E "state|runs =|last exit code" || echo "PRUNE JOB NOT LOADED"
```

The `status` output's `heartbeat:` line is the primary signal — it was added
precisely because a wedged janitor and a clean machine otherwise look
identical. Report its verdict verbatim (`FRESH` / `STALE` / `NEVER RUN`).

## 2. What has it done lately?

```bash
# action events only — ' INFO (killed|pruned) ' anchors the EVENT word;
# a looser pattern also matches every heartbeat's killed=0 field (probed)
grep -E ' INFO (killed|pruned) ' ~/.claude/logs/agent-janitor.log ~/.claude/logs/agent-janitor.log.old 2>/dev/null | tail -20
tail -30 ~/.claude/logs/agent-janitor.log
tail -20 ~/.claude/logs/agent-janitor-launchd.log 2>/dev/null
```

Summarize in plain language: N processes killed / N paths deleted / N bytes
since when — or "nothing killed or deleted since <date>", which on a clean
machine is the expected answer, not a problem. `heartbeat` and
`prune-heartbeat` lines carry per-class counts; `killed` and `pruned` lines
are the individual actions.

## 3. Anything to fix?

Check each row; report only the rows that fire, with the matching next step.
If none fire, say so in one line.

| signal | meaning | next step |
|---|---|---|
| `heartbeat: STALE` or `NEVER RUN` | janitor stopped running (or never started) | `launchctl print gui/$UID/com.gearbox.agent-janitor.reap`; reinstall via `agent_janitor.py install` if unloaded |
| launchd `last exit code` ≠ 0 | a scheduled run failed | read the tail of `agent-janitor-launchd.log` |
| either job NOT LOADED | schedule half-installed | `python3 ~/.claude/scripts/agent_janitor.py install` (owner-run) |
| `refused-max-kills` / `refused-max-delete` | blast-radius cap tripped — deliberately refused to act | inspect what grew; the cap firing repeatedly means real debris is accumulating uncollected |
| `bare` or `unknown` counts growing across heartbeats | detector is declining work it cannot prove safe | dry-run and eyeball the report-only tables: `agent_janitor.py reap --dry-run` |
| `WARN survivor` / `pid-recycled` | a kill did not complete cleanly | usually self-heals next tick; recurring = investigate the named pid |
| `ERROR session-scope-unresolved` | SessionEnd hook refused an ambiguous session id | fine once; recurring = check the scratchpad layout it names |
| `prune-delete-failed` | a path could not be deleted | check permissions on the named path |
| deployed hash ≠ source hash (`module hash:` in status vs `shasum -a 256 <your harness source>/scripts/agent_janitor.py`) | source moved ahead of deploy | `gearbox deploy` when intended; mid-plan divergence can be deliberate — check recent commits before "fixing" |

## Report shape

BLUF, ≤10 lines: one verdict line (RUNNING / STOPPED / DEGRADED + why), the
lately-done summary, then only the fired issue rows. Offer /janitor-stop only
if something is actually wrong.
