#!/usr/bin/env bash
# gearbox-weekly-drift.sh — the PERMANENT half of the drift guard.
#
# The SessionStart hook (hooks/gearbox-drift-warn.sh) is a two-week habit-formation
# affordance and gets demoted. This does not: harness source edited directly in the
# deploy target must surface even in a week with no interactive sessions.
#
# Same shape and posture as scripts/loop-trigger-weekly-check.sh: read-only, local
# notification + append-only log, never writes to the repo, never pages anyone.
#
# Install (operator action — this script does not touch your crontab):
#     crontab -e
#     0 9 * * 1  /bin/bash "$HOME/.claude/scripts/gearbox-weekly-drift.sh"
set -euo pipefail

CD="${CLAUDE_DIR:-$HOME/.claude}"
LOG="$CD/scripts/gearbox-weekly-drift.log"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

out="$("$CD/scripts/gearbox" drift 2>&1)" && rc=0 || rc=$?

if [ "$rc" != "0" ]; then
  n="$(printf '%s\n' "$out" | awk '/^HARNESS-CODE/ { print $NF }' | head -1)"
  printf '[%s] DRIFT rc=%s — %s harness-source file(s) edited in %s\n' "$ts" "$rc" "${n:-?}" "$CD" >> "$LOG"
  printf '%s\n' "$out" | sed 's/^/    /' >> "$LOG"
  osascript -e "display notification \"${n:-some} harness file(s) edited directly in ~/.claude\" with title \"gearbox: harness drift\" subtitle \"run: ~/.claude/scripts/gearbox harvest\"" 2>/dev/null || true
else
  printf '[%s] ok — no harness drift\n' "$ts" >> "$LOG"
fi
