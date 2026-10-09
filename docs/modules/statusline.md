# Status line

Shows your rate-limit use (5 hours and 7 days), the context use, the
prompt-cache state and the model, in two rows at the bottom of Claude Code.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | `bash` on macOS or Linux |
| **Needs** | `jq`. Rate limits show only on Pro and Max plans. |

## What you get

- `harness/scripts/statusline.sh` — reads the JSON that Claude Code sends to a status line command. No network use.

## Install

```bash
mkdir -p ~/.claude/scripts
cp harness/scripts/statusline.sh ~/.claude/scripts/
```

Set this top-level key in `~/.claude/settings.json`. It replaces any status
line you have now:

```json
"statusLine": { "type": "command", "command": "bash ~/.claude/scripts/statusline.sh" }
```

## Check it works

Start a new session. Row 1 looks like `5h:N% (2h26m) 7d:N% (6d7h) | Ctx:N% | Cache:N% warm`.
Row 2 shows the session, the model and its effort, and the folder.

## Remove

Delete the `statusLine` key, then `rm ~/.claude/scripts/statusline.sh`.

## Cautions

None. It only prints.
