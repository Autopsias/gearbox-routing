#!/usr/bin/env bash
# quiesce-check.sh — decidable OPERATIONAL QUIESCE GATE for the ~/.claude deploy target.
# Exit 0 = QUIESCED (safe to mutate). Exit 1 = NOT QUIESCED (abort / wait).
#                                      Exit 2 = COULD NOT INSPECT (harder stop).
#
# PROVENANCE: this is the S03A cutover gate, adversarially reviewed to APPROVED@r3
# (_evidence/s03a/CUTOVER-PROCEDURE.md §1.2 + CUTOVER-PROCEDURE-REVIEW-LOG.md), promoted
# from a one-shot cutover artifact into the permanent deploy path at S04. The gate logic
# is UNCHANGED. Only the four values that were hardcoded to that one cutover were lifted
# into environment inputs so `scripts/gearbox deploy` can supply them per run:
#   QUIESCE_RUNTIME_RE    the D3 runtime surface (default now also covers the routing
#                         eval output named in scripts/deploy.pathspec's [live-state])
#   QUIESCE_PLAN_DIR      the executing plan's own dir (self-exclusion E3)
#   QUIESCE_FF_FILE       file of paths the incoming fast-forward touches -> Q5
#                         (was hardcoded to CLAUDE.md + README.md)
#   QUIESCE_TOLERATE_FILE file of paths the CALLER has already classified as routine
#                         (live-state / machine churn). Without this, a `/model` switch
#                         reddens Q1 on a tree the deploy is about to harvest anyway —
#                         the exact alarm-fatigue failure the two-class split exists to
#                         prevent. The classifier, not this gate, decides what is routine.
#
# ---- original hardening notes, all still in force -------------------------------------
# [HARDENED:codex] "fails open on inspection errors": runs `set -euo pipefail` with an ERR
# trap — any failed git/stat/find/parse is an immediate NOT-QUIESCED, and every probe is
# validated non-empty and well-formed before comparison. Single-pass awk max instead of
# `sort -rn | head -1` (head closing early could SIGPIPE sort under pipefail); awk filters
# instead of grep in pipelines (grep returns 1 on no-match, aborting the *passing* case).
# [HARDENED:claude] QUIESCE_MODE:
#   narrow (default) — Q1 excludes the D3 runtime surface (foreign-writable, cannot affect
#                      the fast-forward); Q3 is a WARN.
#   strict           — Q1 covers the whole tracked surface; Q3 HARD.
# [HARDENED:codex-verify-r1] (a) RUNTIME_RE covers auto-memory AND _plans (both are 350+
#   tracked files written by every running plan); (b) two distinct non-zero codes so a
#   caller can tell "observed a violation" (1) from "could not inspect" (2).
# [HARDENED:codex-verify-r2] RUNTIME_RE also scopes the UNTRACKED surface (Q1b) in narrow
#   mode: a memory file created since the last harvest is new => untracked => invisible to
#   Q1, and reddened the gate as a stray.
#
# Self-exclusions are structural, not judgement calls:
#   E1  this session's Claude Code process tree ........ $CLAUDE_PID + descendants
#   E2  this session's transcript tree ................. projects/*/$CLAUDE_CODE_SESSION_ID*
#   E3  the executing plan's own directory ............. $QUIESCE_PLAN_DIR
set -euo pipefail

CD="${CLAUDE_DIR:-$HOME/.claude}"
PLAN_DIR="${QUIESCE_PLAN_DIR:-__no_plan_dir__}"
SID="${CLAUDE_CODE_SESSION_ID:-__none__}"
WINDOW="${QUIESCE_WINDOW:-60}"          # seconds for the churn sample
MODE="${QUIESCE_MODE:-narrow}"          # narrow | strict
# D3 runtime surface. Keep in lockstep with [live-state] in scripts/deploy.pathspec.
RUNTIME_RE="${QUIESCE_RUNTIME_RE:-^(projects/[^/]+/memory/|_plans/|evals/routing/(MISROUTES\.md|results/))}"
TOLERATE_FILE="${QUIESCE_TOLERATE_FILE:-/dev/null}"
FF_FILE="${QUIESCE_FF_FILE:-}"
FAIL=0

# exit 2 — "could not inspect", strictly harder than exit 1 "observed a violation".
die() {
  echo "  GATE ERROR: $*" >&2
  echo "VERDICT: NOT QUIESCED — the gate could not establish safety"
  exit 2
}
trap 'die "unexpected failure (line $LINENO) — inspection incomplete"' ERR

case "$MODE" in narrow|strict) : ;; *) die "QUIESCE_MODE must be narrow or strict (got: $MODE)" ;; esac
command -v git     >/dev/null 2>&1 || die "git not on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 not on PATH"
[ -d "$CD/.git" ] || die "$CD is not a git working tree"
[ -r "$TOLERATE_FILE" ] || die "QUIESCE_TOLERATE_FILE is not readable: $TOLERATE_FILE"

echo "quiesce-check @ $(date -u +%Y-%m-%dT%H:%M:%SZ)  CLAUDE_DIR=$CD"
echo "  mode=$MODE  window=${WINDOW}s"
echo "  self pid=${CLAUDE_PID:-?}  self session=$SID"

# Shared awk prelude: load the caller's already-classified-routine set.
TOL_PRELUDE='BEGIN { while ((getline l < tf) > 0) if (length(l)) tol[l]=1 }'

# --- Q1 HARD: tracked surface is clean -------------------------------------
status_out=$(git -C "$CD" status --porcelain=v1 --untracked-files=no)
if [ "$MODE" = "narrow" ]; then
  q1_lines=$(printf '%s\n' "$status_out" | awk -v tf="$TOLERATE_FILE" -v re="$RUNTIME_RE" "$TOL_PRELUDE"' NF { p=substr($0,4); if (p !~ re && !(p in tol)) print }')
  q1_skipped=$(printf '%s\n' "$status_out" | awk -v tf="$TOLERATE_FILE" -v re="$RUNTIME_RE" "$TOL_PRELUDE"' NF { p=substr($0,4); if (p ~ re || (p in tol)) print }' | awk 'NF' | wc -l | tr -d ' ')
else
  q1_lines=$(printf '%s\n' "$status_out" | awk 'NF')
  q1_skipped=0
fi
q1=$(printf '%s\n' "$q1_lines" | awk 'NF' | wc -l | tr -d ' ')
if [ "$q1" = "0" ]; then
  echo "  Q1 PASS  tracked surface clean (0 unclassified dirty, mode=$MODE)"
else
  echo "  Q1 FAIL  $q1 dirty tracked file(s) the caller has NOT classified as routine:"
  printf '%s\n' "$q1_lines" | awk 'NF' | sed 's/^/         /'
  FAIL=1
fi
if [ "$q1_skipped" != "0" ]; then
  echo "         note: $q1_skipped dirty file(s) on the D3 runtime surface or already"
  echo "               classified routine by the caller — excluded in narrow mode"
fi

# --- Q1b HARD: no untracked strays outside the plan dir / runtime surface ----
untracked_out=$(git -C "$CD" status --porcelain=v1 --untracked-files=all)
if [ "$MODE" = "narrow" ]; then
  q1b_lines=$(printf '%s\n' "$untracked_out" \
    | awk -v tf="$TOLERATE_FILE" -v p="$PLAN_DIR" -v re="$RUNTIME_RE" "$TOL_PRELUDE"' /^\?\?/ && index($0,p)==0 { q=substr($0,4); if (q !~ re && !(q in tol)) print }')
  q1b_skipped=$(printf '%s\n' "$untracked_out" \
    | awk -v tf="$TOLERATE_FILE" -v p="$PLAN_DIR" -v re="$RUNTIME_RE" "$TOL_PRELUDE"' /^\?\?/ && index($0,p)==0 { q=substr($0,4); if (q ~ re || (q in tol)) print }' \
    | awk 'NF' | wc -l | tr -d ' ')
else
  q1b_lines=$(printf '%s\n' "$untracked_out" | awk -v p="$PLAN_DIR" '/^\?\?/ && index($0,p)==0')
  q1b_skipped=0
fi
q1b=$(printf '%s\n' "$q1b_lines" | awk 'NF' | wc -l | tr -d ' ')
if [ "$q1b" = "0" ]; then
  echo "  Q1b PASS untracked strays outside the runtime surface: 0 (mode=$MODE)"
else
  echo "  Q1b FAIL $q1b untracked stray(s):"
  printf '%s\n' "$q1b_lines" | awk 'NF' | sed 's/^/         /'
  FAIL=1
fi
if [ "$q1b_skipped" != "0" ]; then
  echo "         note: $q1b_skipped NEWLY CREATED file(s) on the D3 runtime surface"
  echo "               (or already classified routine) — excluded in narrow mode"
fi

# --- Q2 HARD: tracked surface is not churning ------------------------------
# Q2 honours the SAME narrow/strict scope as Q1 — otherwise narrow mode is defeated by
# its own churn probe. Measured 2026-07-25T16:25Z: the newest mtime on the tracked
# surface WAS a foreign session's auto-memory file, so an unscoped Q2 reds on exactly
# the writes narrow mode exists to tolerate.
probe() {
  git -C "$CD" ls-files -z \
    | (cd "$CD" && xargs -0 stat -f '%m %N') \
    | awk -v re="$RUNTIME_RE" -v mode="$MODE" '
        { p = substr($0, index($0, " ") + 1)
          if (mode == "narrow" && p ~ re) next
          c++
          if ($1+0 > m+0) { m = $1; l = $0 } }
        END { if (c) print l }'
}
a=$(probe) || die "Q2 probe t0 failed — cannot inspect the tracked surface"
[ -n "$a" ] || die "Q2 probe t0 returned nothing — cannot establish stability"
case "$a" in [0-9]*\ *) : ;; *) die "Q2 probe t0 malformed: [$a]" ;; esac
sleep "$WINDOW"
b=$(probe) || die "Q2 probe t1 failed — cannot inspect the tracked surface"
[ -n "$b" ] || die "Q2 probe t1 returned nothing — cannot establish stability"
case "$b" in [0-9]*\ *) : ;; *) die "Q2 probe t1 malformed: [$b]" ;; esac
if [ "$a" = "$b" ]; then
  echo "  Q2 PASS  tracked-surface newest mtime stable over ${WINDOW}s  [$a]"
else
  echo "  Q2 FAIL  tracked surface changed during the ${WINDOW}s window"
  echo "         t0: $a"
  echo "         t1: $b"
  FAIL=1
fi

# --- Q3 foreign live sessions (WARN in narrow, HARD in strict) --------------
if [ -d "$CD/projects" ]; then
  foreign_all=$(find "$CD/projects" -name '*.jsonl' -mmin -2) || die "Q3 find failed"
else
  foreign_all=""
fi
foreign_list=$(printf '%s\n' "$foreign_all" | awk -v sid="/$SID" 'NF && index($0,sid)==0')
foreign=$(printf '%s\n' "$foreign_list" | awk 'NF' | wc -l | tr -d ' ')
pids=$(ps -eo pid=,command= | awk -v self="${CLAUDE_PID:-__none__}" '{$1=$1} $2=="claude" && NF==2 && $1!=self { printf "%s ", $1 }')
if [ "$foreign" = "0" ]; then
  echo "  Q3 PASS  no foreign session transcript writes in the last 2 min"
else
  if [ "$MODE" = "strict" ]; then verdict="FAIL"; FAIL=1; else verdict="WARN"; fi
  echo "  Q3 $verdict  $foreign foreign transcript(s) written in the last 2 min — other live session(s):"
  printf '%s\n' "$foreign_list" | awk 'NF' | sed "s|$CD/projects/|         |"
  echo "         other claude pids: ${pids:-none}"
  if [ "$MODE" = "strict" ]; then
    echo "         strict mode: zero foreign sessions required across the critical section."
  else
    echo "         NOT a blocker on its own — Q1/Q2/Q5 are the hard gate. It IS a blocker"
    echo "         if that session edits harness source (the D3 runtime surface is both"
    echo "         tracked AND foreign-writable, and is classified, not alarmed on)."
  fi
fi

# --- Q4 HARD: settings.json parses -----------------------------------------
[ -f "$CD/settings.json" ] || die "Q4 $CD/settings.json does not exist"
if python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$CD/settings.json" 2>/dev/null; then
  echo "  Q4 PASS  settings.json parses as JSON"
else
  echo "  Q4 FAIL  settings.json does NOT parse"
  FAIL=1
fi

# --- Q5 HARD: the paths the incoming fast-forward touches are unmodified -----
# Intersect Q1's dirty set with the incoming path set in awk rather than re-asking git
# with a shell-split pathspec — an incoming path containing a space would otherwise be
# silently mis-scoped. Paths the caller classified routine are excluded: deploy commits
# those BEFORE it merges, so they cannot make the fast-forward refuse.
if [ -z "$FF_FILE" ]; then
  echo "  Q5 SKIP  no QUIESCE_FF_FILE supplied — nothing known to be incoming"
elif [ ! -r "$FF_FILE" ]; then
  die "QUIESCE_FF_FILE is not readable: $FF_FILE"
else
  q5_lines=$(printf '%s\n' "$status_out" \
    | awk -v tf="$TOLERATE_FILE" -v ff="$FF_FILE" \
        'BEGIN { while ((getline l < tf) > 0) if (length(l)) tol[l]=1
                 while ((getline l < ff) > 0) if (length(l)) inc[l]=1 }
         NF { p=substr($0,4); if ((p in inc) && !(p in tol)) print }')
  q5=$(printf '%s\n' "$q5_lines" | awk 'NF' | wc -l | tr -d ' ')
  if [ "$q5" = "0" ]; then
    echo "  Q5 PASS  no incoming path is locally modified (fast-forward precondition)"
  else
    echo "  Q5 FAIL  the deploy-touched paths are dirty — a fast-forward will refuse:"
    printf '%s\n' "$q5_lines" | sed 's/^/         /'
    FAIL=1
  fi
fi

if [ "$FAIL" = "0" ]; then
  echo "VERDICT: QUIESCED"
  exit 0
fi
echo "VERDICT: NOT QUIESCED — do not mutate"
exit 1
