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

# `|| true` on every step here is load-bearing: under `set -euo pipefail` a
# nonzero doc_check --strict exit, or a grep that matches nothing, would kill
# the whole weekly job before it logs. This check must raise a notification
# on a real failure but never stop the run — same posture as the harness-drift
# block above, just a second, independent gate.
doc_out="$(python3 "$CD/scripts/doc_check.py" --repo-root "$CD" --strict 2>&1)" && doc_rc=0 || doc_rc=$?
doc_failed="$(printf '%s\n' "$doc_out" | head -1 | grep -oE 'failed=[0-9]+' | cut -d= -f2 || true)"
printf '[%s] doc_check: failed=%s rc=%s\n' "$ts" "${doc_failed:-?}" "$doc_rc" >> "$LOG"
if [ "$doc_rc" != "0" ]; then
  printf '%s\n' "$doc_out" | sed 's/^/    /' >> "$LOG"
  osascript -e "display notification \"${doc_failed:-some} doc_check claim(s) failed\" with title \"gearbox: doc_check drift\" subtitle \"run: python3 ~/.claude/scripts/doc_check.py --strict\"" 2>/dev/null || true
fi
