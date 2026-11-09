#!/usr/bin/env bash
# Self-check for skill-usage-log.py. Mirrors test_dispatch_audit.sh: plant a real
# invocation and check the record, plant garbage and check the always-allow
# invariant, and pin the two safety properties (log outside versioned trees,
# pure python). KNOWN-POSITIVE first: every "clean" grep below runs against a
# line the plant case proved was written.
#
# Run: bash hooks/test_skill_usage_log.sh
set -uo pipefail

HOOK="$(cd "$(dirname "$0")" && pwd)/skill-usage-log.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0

run() {  # run <payload> ; sets OUT (stdout) and LOG (last log line)
  export CLAUDE_SKILL_USAGE_LOG="$TMP/usage.ndjson"
  OUT="$(printf '%s' "$1" | python3 "$HOOK" 2>/dev/null)"
  LOG="$(tail -n 1 "$CLAUDE_SKILL_USAGE_LOG" 2>/dev/null || true)"
}

check() {  # check <name> <condition-result> <detail>
  if [ "$2" = "0" ]; then echo "PASS $1"; else echo "FAIL $1: $3"; fails=$((fails+1)); fi
}

# --- always-allow invariant, including on garbage -------------------------------
for payload in \
  '{"tool_input":{"skill":"plan-execute"}}' \
  'not json at all' \
  '{"tool_input":"a string, not an object"}' \
  '' ; do
  run "$payload"
  echo "$OUT" | grep -qE '"permissionDecision":[[:space:]]*"allow"'
  check "always allows (payload: ${payload:0:34})" $? "did not emit an allow decision: $OUT"
done

# --- KNOWN POSITIVE: a real invocation writes a full record ----------------------
run '{"session_id":"sess-1","cwd":"/tmp/x","tool_input":{"skill":"code-review","args":"--fix high"}}'
for field in '"skill": "code-review"' '"args": "--fix high"' '"session_id": "sess-1"' '"cwd": "/tmp/x"' '"ts": "20'; do
  echo "$LOG" | grep -q "$field"
  check "record carries $field" $? "got: $LOG"
done

# --- no skill name -> no record (a malformed call is not usage data) -------------
before="$(wc -l < "$TMP/usage.ndjson")"
run '{"tool_input":{"args":"orphan args, no skill"}}'
after="$(wc -l < "$TMP/usage.ndjson")"
[ "$after" -eq "$before" ]
check "skill-less payload writes no record" $? "line count $before -> $after"

# --- args are truncated, absent args tolerated -----------------------------------
long="$(printf 'a%.0s' $(seq 1 300))"
run "{\"tool_input\":{\"skill\":\"s\",\"args\":\"$long\"}}"
python3 -c "import json,sys; r=json.loads(sys.argv[1]); sys.exit(0 if len(r['args'])==120 else 1)" "$LOG"
check "args truncated to 120 chars" $? "got: $LOG"
run '{"tool_input":{"skill":"no-args-skill"}}'
echo "$LOG" | grep -q '"args": ""'
check "absent args degrade to empty string" $? "got: $LOG"

# --- append-only: a second invocation must not clobber the first -----------------
before="$(wc -l < "$TMP/usage.ndjson")"
run '{"tool_input":{"skill":"another-skill"}}'
after="$(wc -l < "$TMP/usage.ndjson")"
[ "$after" -eq "$((before + 1))" ]
check "append-only (line count $before -> $after)" $? "expected exactly one new line"

# --- an unwritable log must not break the invocation -----------------------------
export CLAUDE_SKILL_USAGE_LOG="/dev/null/cannot/exist/usage.ndjson"
OUT="$(printf '%s' '{"tool_input":{"skill":"s"}}' | python3 "$HOOK" 2>/dev/null)"
echo "$OUT" | grep -qE '"permissionDecision":[[:space:]]*"allow"'
check "unwritable log still allows the invocation" $? "got: $OUT"

# The default log path must be OUTSIDE the versioned trees: an observer that can
# abort `gearbox deploy` as an unclassified path is exactly what this must not be.
grep -q 'DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".gearbox-state"' "$HOOK"
check "default log path is outside ~/.claude and ~/Gearbox (deploy-gate safety)" $? \
  "default path must live under the runtime root, not a versioned tree"

grep -qE '(^|[|;&]|[[:space:]])python3 +-c' "$HOOK"
[ $? -ne 0 ]
check "hook is pure python (no nested python3 -c quoting to break)" $? \
  "an embedded python3 -c string is what a stray apostrophe broke before"

echo
echo "$fails failure(s)"
[ "$fails" -eq 0 ]
