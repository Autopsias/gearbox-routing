#!/usr/bin/env bash
# gearbox-drift-warn.sh — SessionStart drift warning for the ~/.claude deploy target.
#
# HABIT-FORMATION WINDOW, NOT A PERMANENT HOOK. [HARDENED:adv-claude-7] wires the drift
# check BOTH ways at cutover: here (every session, loud, for the first two weeks) and in
# scripts/gearbox-weekly-drift.sh (permanent).
#
#   wired ....... 2026-07-25 (S04)
#   expires ..... 2026-08-08 — SELF-ENFORCED below, no diary entry required
#
# The original design said "DEMOTE this hook after the drill plus one clean week", which
# made the end of the window a thing a human had to remember. Nobody remembers; the hook
# then runs forever, and a noisy permanent hook gets deleted in irritation along with the
# weekly check. So the expiry is enforced here instead: past GEARBOX_DRIFT_HOOK_EXPIRY the
# wrapper prints a one-line removal instruction and stops running the check. The permanent
# coverage is the weekly cron, which is unaffected.
#
# To extend the window (e.g. the drill has not run yet), move the date. To end it early,
# delete the SessionStart entry from settings.json — this file may then be removed.
#
# Read-only, offline, never blocks: `gearbox drift --brief` prints nothing when there is
# nothing to say, and this wrapper always exits 0. A drift check that can break a session
# start would be removed within a day, and then there would be no drift check at all.
# The wall-clock bound is settings.json's per-hook `timeout` — not timeout(1), which is
# a homebrew coreutils binary that a minimal hook PATH may not have.
CD="${CLAUDE_DIR:-$HOME/.claude}"
EXPIRY="${GEARBOX_DRIFT_HOOK_EXPIRY:-2026-08-08}"

# date(1) comparison, lexicographic on ISO-8601 — no GNU-only flags, no coreutils dep.
TODAY="$(date -u +%F)"
if [ "$TODAY" \> "$EXPIRY" ]; then
  echo "gearbox: SessionStart drift hook expired ${EXPIRY} (habit-formation window over)."
  echo "         Remove the SessionStart entry for hooks/gearbox-drift-warn.sh from"
  echo "         settings.json; the weekly cron check continues to cover drift."
  exit 0
fi

[ -x "$CD/scripts/gearbox" ] || exit 0
"$CD/scripts/gearbox" drift --brief 2>&1 || true
exit 0
