#!/usr/bin/env bash
# Self-check for dispatch-audit.sh. Every case is paired: the hook must CLASSIFY the
# drift it is meant to catch (plant), and must NOT cry drift on a fully-pinned dispatch
# (allow control). Plus the invariant that matters most: it exits 0 with no stdout,
# even on garbage input. An observer that can block a dispatch is a liability, and
# one that prints an "allow" decision bypasses the user's permission rules.
#
# Run: bash hooks/test_dispatch_audit.sh
set -uo pipefail

HOOK="$(cd "$(dirname "$0")" && pwd)/dispatch-audit.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0

run() {  # run <payload> ; sets OUT (stdout) and LOG (last log line)
  export CLAUDE_DISPATCH_AUDIT_LOG="$TMP/audit.ndjson"
  OUT="$(printf '%s' "$1" | python3 "$HOOK" 2>/dev/null)"; RC=$?
  LOG="$(tail -n 1 "$CLAUDE_DISPATCH_AUDIT_LOG" 2>/dev/null || true)"
}

check() {  # check <name> <condition-result> <detail>
  if [ "$2" = "0" ]; then echo "PASS $1"; else echo "FAIL $1: $3"; fails=$((fails+1)); fi
}

# --- silent-pass invariant, including on garbage --------------------------------
for payload in \
  '{"tool_input":{"model":"opus","subagent_type":"tier-opus-high"}}' \
  'not json at all' \
  '{"tool_input":"a string, not an object"}' \
  '' ; do
  run "$payload"
  [ "$RC" -eq 0 ] && [ -z "$OUT" ]
  check "exits 0 with no decision (payload: ${payload:0:34})" $? "rc=$RC stdout: $OUT"
done

# --- drift classification: plant each class, then the allow control --------------
run '{"tool_input":{"description":"d"}}'
echo "$LOG" | grep -q '"drift": "inherits_both"'
check "PLANT model+subagent_type both unset -> inherits_both" $? "got: $LOG"

# REGRESSION (found by running the hook in production): a tier-agent dispatch
# legitimately omits `model` because the definition's frontmatter pins it. The first
# draft labelled that `inherits_model`, crying drift on the CORRECT pattern.
run '{"tool_input":{"subagent_type":"tier-opus-high","description":"d"}}'
echo "$LOG" | grep -q '"drift": "definition_governs"'
check "ALLOW CONTROL named agent without model -> definition_governs (not drift)" $? "got: $LOG"
echo "$LOG" | grep -q '"drift": "inherits_model"'
[ $? -ne 0 ]
check "REGRESSION named-agent dispatch is never labelled inherits_model" $? "got: $LOG"

run '{"tool_input":{"model":"opus","description":"d"}}'
echo "$LOG" | grep -q '"drift": "inherits_effort"'
check "PLANT no definition to carry effort -> inherits_effort" $? "got: $LOG"

run '{"tool_input":{"model":"opus","subagent_type":"tier-opus-high","description":"d"}}'
echo "$LOG" | grep -q '"drift": "pinned"'
check "ALLOW CONTROL fully pinned -> pinned (no false drift)" $? "got: $LOG"

# --- RT-02(b): unpinned_generic flag + its one stderr line -----------------------
# PLANT — no model, no subagent_type at all: the exact ad-hoc fan-out a hook cannot
# pin (Claude Code silently ignores a hook's attempt to change model/subagent_type).
run '{"tool_input":{"description":"d"}}'
echo "$LOG" | grep -q '"unpinned_generic": true'
check "PLANT no model + no subagent_type -> unpinned_generic: true" $? "got: $LOG"

# PLANT — the built-in generic agent names carry no model/effort frontmatter either.
for generic in general-purpose Explore Plan; do
  run "{\"tool_input\":{\"subagent_type\":\"$generic\",\"description\":\"d\"}}"
  echo "$LOG" | grep -q '"unpinned_generic": true'
  check "PLANT subagent_type=$generic + no model -> unpinned_generic: true" $? "got: $LOG"
done

# ALLOW CONTROL — a tier-* agent (its OWN frontmatter pins model+effort) never flags.
run '{"tool_input":{"subagent_type":"tier-opus-high","description":"d"}}'
echo "$LOG" | grep -q '"unpinned_generic"'
[ $? -ne 0 ]
check "ALLOW CONTROL tier-* agent without model -> no unpinned_generic flag" $? "got: $LOG"

# ALLOW CONTROL — an explicit model pin never flags, generic subagent_type or not.
run '{"tool_input":{"model":"sonnet","description":"d"}}'
echo "$LOG" | grep -q '"unpinned_generic"'
[ $? -ne 0 ]
check "ALLOW CONTROL explicit model pin -> no unpinned_generic flag" $? "got: $LOG"

# The stderr line names it, once, only on the unpinned-generic case.
export CLAUDE_DISPATCH_AUDIT_LOG="$TMP/audit.ndjson"
ERR="$(printf '%s' '{"tool_input":{"description":"d"}}' | python3 "$HOOK" 2>&1 1>/dev/null)"
echo "$ERR" | grep -q 'dispatch-audit: unpinned generic dispatch'
check "unpinned generic dispatch prints one stderr line naming it" $? "got: $ERR"
ERR="$(printf '%s' '{"tool_input":{"model":"sonnet"}}' | python3 "$HOOK" 2>&1 1>/dev/null)"
[ -z "$ERR" ]
check "ALLOW CONTROL a pinned dispatch prints nothing to stderr" $? "got: $ERR"

# --- the record carries what an audit needs -------------------------------------
run '{"session_id":"sess-1","cwd":"/tmp/x","effort":"high","tool_input":{"model":"sonnet","subagent_type":"tier-sonnet-medium","description":"S07 build"}}'
for field in '"session_id": "sess-1"' '"requested_model": "sonnet"' '"subagent_type": "tier-sonnet-medium"' '"session_effort": "high"' '"description": "S07 build"' '"ts": "20'; do
  echo "$LOG" | grep -q "$field"
  check "record carries $field" $? "got: $LOG"
done

# REGRESSION (found in production): `effort` arrives as an OBJECT, not a string. The
# first draft logged the raw dict, which no downstream grep could match. Both shapes
# must normalize to a plain level string, and an unexpected shape must not emit a dict.
run '{"effort":{"level":"xhigh"},"tool_input":{"model":"opus","subagent_type":"tier-opus-high"}}'
echo "$LOG" | grep -q '"session_effort": "xhigh"'
check "PLANT effort as an object {level:...} normalizes to a plain string" $? "got: $LOG"
echo "$LOG" | grep -q '"session_effort": {'
[ $? -ne 0 ]
check "REGRESSION session_effort is never logged as a raw object" $? "got: $LOG"

run '{"effort":["not","a","level"],"tool_input":{"model":"opus","subagent_type":"tier-opus-high"}}'
echo "$LOG" | grep -qE '"session_effort": (null|")'
check "ALLOW CONTROL an unexpected effort shape degrades to null, not a crash" $? "got: $LOG"

# The default log path must be OUTSIDE the versioned trees: the first draft defaulted
# into ~/.claude/evals/routing/, and the log then aborted the next `gearbox deploy` as
# an unclassified path. An observer must never be able to block a deploy.
grep -q 'DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".gearbox-state"' "$HOOK"
check "default log path is outside ~/.claude and ~/Gearbox (deploy-gate safety)" $? \
  "default path must live under the runtime root, not a versioned tree"

# --- append-only: a second dispatch must not clobber the first -------------------
before="$(wc -l < "$TMP/audit.ndjson")"
run '{"tool_input":{"model":"haiku","subagent_type":"tier-haiku"}}'
after="$(wc -l < "$TMP/audit.ndjson")"
[ "$after" -eq "$((before + 1))" ]
check "append-only (line count $before -> $after)" $? "expected exactly one new line"

# --- an unwritable log must not break the dispatch -------------------------------
export CLAUDE_DISPATCH_AUDIT_LOG="/dev/null/cannot/exist/audit.ndjson"
OUT="$(printf '%s' '{"tool_input":{"model":"opus"}}' | python3 "$HOOK" 2>/dev/null)"; RC=$?
[ "$RC" -eq 0 ] && [ -z "$OUT" ]
check "unwritable log still exits 0 with no decision" $? "rc=$RC stdout: $OUT"

grep -qE '(^|[|;&]|[[:space:]])python3 +-c' "$HOOK"
[ $? -ne 0 ]
check "hook is pure python (no nested python3 -c quoting to break)" $? \
  "an embedded python3 -c string is what a stray apostrophe broke before"

echo
echo "$fails failure(s)"
[ "$fails" -eq 0 ]
