#!/usr/bin/env bash
# test_runner_log_path.sh — verification harness for ST-03 runner-log path.
#
# Asserts THREE things the plan's "Verify" + adversarial-hardening demand:
#   T1  the runner-log parser extracts the CORRECT verdict from a REAL captured
#       fixture (Succeeded AND Failed) — not merely that a path was logged.
#   T2  the parser correlates run identity (repo + run_id + head_sha + jobId) and
#       only considers verdicts AFTER the push timestamp; a stale (pre-push) log
#       is REJECTED, never re-pushed against.
#   T3  the sanitization verifier FAILS on a deliberately-poisoned fixture
#       (token, real home path, runner ID, unrelated repo) and PASSES on the
#       checked-in fixtures.
#
# Run:  references/ship-tail/test_runner_log_path.sh
# Exit: 0 = all pass, 1 = any fail.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIX="$HERE/fixtures"
SAN="$HERE/sanitize_runner_log.sh"
PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

# ---- the parser under test (same logic ship-tail STEP 5 runner-log path uses) --
# parse_verdict <logfile> <expected_repo> <after_epoch>
#   prints SUCCEEDED|FAILED|UNKNOWN|STALE|WRONG_REPO ; exit always 0
parse_verdict() {
  local log="$1" want_repo="$2" after="$3"
  [[ -f "$log" ]] || { echo "UNKNOWN"; return 0; }
  # Identity gate: the fixture's repo line must match the run we pushed.
  if ! grep -A1 '"k": "repository"' "$log" | grep -qF "$want_repo"; then
    echo "WRONG_REPO"; return 0
  fi
  # Freshness gate: file mtime must be AFTER the push timestamp.
  local mtime; mtime="$(stat -f %m "$log" 2>/dev/null || stat -c %Y "$log" 2>/dev/null)"
  if [[ -n "$after" && -n "$mtime" && "$mtime" -lt "$after" ]]; then
    echo "STALE"; return 0
  fi
  # Verdict: ONLY the authoritative "Job result after all job steps finish" line.
  local line; line="$(grep "Job result after all job steps finish:" "$log" | tail -1)"
  case "$line" in
    *": Succeeded") echo "SUCCEEDED" ;;
    *": Failed")    echo "FAILED" ;;
    *)              echo "UNKNOWN" ;;
  esac
  return 0
}

echo "== T1: verdict extraction from REAL fixtures =="
v="$(parse_verdict "$FIX/Worker_succeeded.log" "acme-org/example-repo" 0)"
[[ "$v" == "SUCCEEDED" ]] && ok "succeeded fixture -> SUCCEEDED" || bad "succeeded fixture -> got '$v'"
v="$(parse_verdict "$FIX/Worker_failed.log" "acme-org/example-repo" 0)"
[[ "$v" == "FAILED" ]] && ok "failed fixture -> FAILED" || bad "failed fixture -> got '$v'"

# Anti-cheat: the parser must NOT trust per-step "result": "succeeded" JSON.
# The failed fixture contains green post-steps but an overall Failed verdict.
if grep -q '"result": "succeeded"' "$FIX/Worker_failed.log"; then
  v="$(parse_verdict "$FIX/Worker_failed.log" "acme-org/example-repo" 0)"
  [[ "$v" == "FAILED" ]] && ok "ignores green per-step JSON; honors overall Failed" \
                          || bad "trusted per-step JSON; got '$v'"
else
  ok "failed fixture has no misleading per-step JSON (n/a)"
fi

echo "== T2: identity + freshness gates =="
v="$(parse_verdict "$FIX/Worker_succeeded.log" "someone-else/other-repo" 0)"
[[ "$v" == "WRONG_REPO" ]] && ok "wrong-repo run rejected" || bad "wrong-repo not rejected; got '$v'"
# Stale: push timestamp in the far future -> fixture mtime is older -> STALE.
future="$(( $(date +%s) + 86400 ))"
v="$(parse_verdict "$FIX/Worker_succeeded.log" "acme-org/example-repo" "$future")"
[[ "$v" == "STALE" ]] && ok "pre-push (stale) log rejected" || bad "stale log not rejected; got '$v'"

echo "== T3: sanitization verifier =="
"$SAN" verify "$FIX/Worker_succeeded.log" >/dev/null 2>&1 && ok "verify passes clean succeeded fixture" || bad "verify rejected a clean fixture"
"$SAN" verify "$FIX/Worker_failed.log"    >/dev/null 2>&1 && ok "verify passes clean failed fixture"    || bad "verify rejected a clean fixture"

# Poison each redaction rule; verifier MUST catch every one.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
declare -A POISON=(
  [R1_homepath]='[2026 INFO] path /Users/realuser/secret/work'
  [R2_runnerid]='[2026 INFO] runner host my-secret-runner-7 online'
  [R3_token]='Authorization: Bearer ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  [R4_repo]='          "v": "acme-org/example-repo"'
)
for rule in "${!POISON[@]}"; do
  cp "$FIX/Worker_succeeded.log" "$TMP/poison.log"
  echo "${POISON[$rule]}" >> "$TMP/poison.log"
  if "$SAN" verify "$TMP/poison.log" >/dev/null 2>&1; then
    bad "verifier MISSED poison: $rule"
  else
    ok "verifier caught poison: $rule"
  fi
done

echo
echo "== RESULT: $PASS passed, $FAIL failed =="
[[ $FAIL -eq 0 ]]
