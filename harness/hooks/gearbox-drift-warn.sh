#!/usr/bin/env bash
# gearbox-drift-warn.sh — SessionStart drift warning for the ~/.claude deploy target.
#
# HABIT-FORMATION WINDOW, NOT A PERMANENT HOOK. [HARDENED:adv-claude-7] wires the drift
# check BOTH ways at cutover: here (every session, loud, for the first two weeks) and in
# scripts/gearbox-weekly-drift.sh (permanent). DEMOTE this hook — delete the SessionStart
# entry from settings.json — after the recovery drill plus one clean week.
#
#   wired ....... 2026-07-25 (S04)
#   demote after  2026-08-08, if the drill has run and the week was clean
#
# Read-only, offline, never blocks: `gearbox drift --brief` prints nothing when there is
# nothing to say, and this wrapper always exits 0. A drift check that can break a session
# start would be removed within a day, and then there would be no drift check at all.
# The wall-clock bound is settings.json's per-hook `timeout` — not timeout(1), which is
# a homebrew coreutils binary that a minimal hook PATH may not have.
CD="${CLAUDE_DIR:-$HOME/.claude}"
[ -x "$CD/scripts/gearbox" ] || exit 0
"$CD/scripts/gearbox" drift --brief 2>&1 || true
exit 0
