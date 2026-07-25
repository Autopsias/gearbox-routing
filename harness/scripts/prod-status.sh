#!/usr/bin/env bash
# prod-status.sh — sanctioned read-only production diagnostics lane.
#
# PURPOSE
#   The ONE auditable, read-only path for inspecting your production host.
#   Authored to end a loop in which sessions repeatedly attempted
#   ad-hoc prod SSH reads and credential-store probes that the safety classifier
#   (correctly) denied — including two attempts to widen their own permissions.
#
# CONTRACT (do not weaken)
#   - READ-ONLY. Every remote command only observes state. No writes, no
#     restarts, no pulls, no config edits, no package installs.
#   - FIXED COMMAND SURFACE. This script takes NO arguments. There is no code
#     path that lets a caller change which commands run on the host. The
#     settings.json allowlist pins the exact invocation `bash <this path>`.
#   - NO CREDENTIAL READS. It never cats env files, dumps environment variables,
#     reads secrets/keys, or enumerates credential stores.
#   - BatchMode ssh: never prompts for a password; fails closed if key auth is
#     unavailable.
#
# KNOWN HANG CLASS (found 2026-07-16, docs/operations/prod-health-readout-20260716.md):
#   Tailscale SSH enforces a periodic re-authentication "check" at the
#   tailscaled layer (not OpenSSH). When the check is due, the FIRST
#   connection of a session prints "Tailscale SSH requires an additional
#   check. To authenticate, visit: <url>" to stderr and blocks waiting for
#   that URL to be opened in a browser. This is NOT a password/host-key
#   prompt, so `BatchMode=yes` and `StrictHostKeyChecking=yes` do nothing to
#   it — it hung this script for 10+ minutes with zero stdout in a headless
#   dispatch context where nobody could click the link. Root-cause fix lives
#   on the Tailscale ACL side (check period / device posture), which is out
#   of this script's control; the mitigation here is bounding every remote
#   command with `timeout` + keepalives so a stuck re-auth FAILS FAST with a
#   clear message instead of hanging silently.
#
# If you need a NEW diagnostic, add a fixed read-only command below and re-pin
# the content hash in ~/.claude/settings.json — never parameterise this script.
#
# CANONICAL COPY: this file is mirrored at
# infra/scripts/prod-status.sh in the example-project repo (this path is outside any
# repo, so it cannot be committed directly). Edit the repo copy first, `cp` it
# here, re-run shasum, and re-pin prod-status-pinned.sh's PINNED_SHA256 —
# never edit only the home copy, or the change is invisible to `git log` and
# will look reverted to the next reader of the repo.

set -euo pipefail

readonly PROD_HOST="${PROD_HOST:?set PROD_HOST, e.g. ubuntu@your-host}"
readonly HEALTH_URL="http://127.0.0.1:8000/health"
readonly DISK_WARN_PCT=85
# Backups run nightly at 03:00; 2 days = one missed run plus slack before it warns.
readonly BACKUP_STALE_DAYS=2
# LOOP-01 (2026-07-17): the durable re-readout trigger window starts at this
# plan's completion, NOT 2026-07-17 literally — bump this the day the plan
# closes out so the count excludes this plan's own verification traffic
# (r01/r04 spot queries). See docs/operations/trace-readout-playbook.md.
readonly REREADOUT_WINDOW_START="2026-07-17"
readonly REREADOUT_CANDIDATE_THRESHOLD=30
readonly REREADOUT_ACTIVE_DAYS_THRESHOLD=5
# BatchMode=yes -> never interactive-prompt; fail closed. Bounded connect time.
# ServerAlive* -> detect a dead/stuck connection (e.g. wedged Tailscale
# re-auth) and drop it instead of hanging forever.
readonly SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o StrictHostKeyChecking=yes
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=3
)
# Hard ceiling per remote command: 5s connect + 3x5s keepalive grace + slack.
readonly CMD_TIMEOUT=25

section() { printf '\n===== %s =====\n' "$1"; }
heartbeat() { printf '[%s] %s...\n' "$(date -u +%H:%M:%S)" "$1"; }

run_remote() {
  # $1 = step label, $2 = remote command
  heartbeat "$1"
  if timeout "$CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$PROD_HOST" "$2"; then
    return 0
  fi
  local rc=$?
  if [[ $rc -eq 124 ]]; then
    echo "[TIMEOUT after ${CMD_TIMEOUT}s] $1 — host unreachable or stuck (check Tailscale SSH re-auth: tailscale.com/s/ssh-check)" >&2
  else
    echo "[unreachable] $1 failed (exit $rc)" >&2
  fi
  return 0
}

section "prod-status.sh — read-only diagnostics for ${PROD_HOST}"
printf 'run-at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

section "1. Host uptime / load"
run_remote "uptime" 'uptime'

section "2. Container status (docker compose ps)"
run_remote "docker compose ps" 'cd /opt/${APP_NAME:?set APP_NAME} && docker compose -f ${COMPOSE_FILE:-docker-compose.yml} ps'

section "3. Disk usage (df -h)"
df_out="$(timeout "$CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$PROD_HOST" 'df -h' 2>&1)" && df_rc=0 || df_rc=$?
if [[ $df_rc -eq 0 ]]; then
  printf '%s\n' "$df_out"
  root_pct="$(printf '%s\n' "$df_out" | awk '$NF=="/" {gsub("%","",$5); print $5}')"
  if [[ -n "${root_pct:-}" && "$root_pct" =~ ^[0-9]+$ && "$root_pct" -ge "$DISK_WARN_PCT" ]]; then
    printf '\n*** WARNING: root disk at %s%% (>= %s%% threshold) — reclaim before it fills. ***\n' "$root_pct" "$DISK_WARN_PCT"
  fi
elif [[ $df_rc -eq 124 ]]; then
  echo "[TIMEOUT after ${CMD_TIMEOUT}s] df -h — host unreachable or stuck (check Tailscale SSH re-auth: tailscale.com/s/ssh-check)" >&2
else
  echo "[unreachable] df -h failed (exit $df_rc)" >&2
fi

section "4. API liveness (health endpoint, on-host loopback)"
run_remote "health check" "curl -s --max-time 10 ${HEALTH_URL}"

section "5. Phoenix backup freshness (S3)"
# A 4-day Phoenix-backup outage (Jul 12-16 2026) was invisible because nothing
# watched it -- the .backup cron silently stopped converging and no channel
# reported it. This box has no CloudWatch/SNS/webhook reach (instance role
# lacks the perms), so this read-only lane is the surface a human actually
# looks at. Warn if the newest phoenix-*.db in S3 is older than BACKUP_STALE_DAYS.
# The durable push alert needs an SNS topic + IAM policy (AWS_setup briefing);
# this is the local backstop, not a replacement for it.
bk_out="$(timeout "$CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
  'aws s3 ls s3://example-project-backups/phoenix/ 2>&1 | grep -E "phoenix-[0-9]{4}-[0-9]{2}-[0-9]{2}\.db" | sort | tail -3' 2>&1)" \
  && bk_rc=0 || bk_rc=$?
if [[ $bk_rc -eq 0 && -n "${bk_out:-}" ]]; then
  printf '%s\n' "$bk_out"
  # Newest backup date = last date-stamped filename in the sorted listing.
  latest_date="$(printf '%s\n' "$bk_out" | grep -oE 'phoenix-[0-9]{4}-[0-9]{2}-[0-9]{2}\.db' | tail -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')"
  if [[ -n "${latest_date:-}" ]]; then
    latest_epoch="$(date -j -f %Y-%m-%d "$latest_date" +%s 2>/dev/null || date -d "$latest_date" +%s 2>/dev/null || echo 0)"
    now_epoch="$(date +%s)"
    age_days=$(( (now_epoch - latest_epoch) / 86400 ))
    if [[ "$latest_epoch" -eq 0 ]]; then
      printf '\n*** WARNING: could not parse latest backup date (%s) — check manually. ***\n' "$latest_date"
    elif [[ "$age_days" -ge "$BACKUP_STALE_DAYS" ]]; then
      printf '\n*** WARNING: newest Phoenix backup is %s (%s days old, >= %s) — backups may have stopped. Check /var/log/phoenix-s3-backup.log on the box. ***\n' "$latest_date" "$age_days" "$BACKUP_STALE_DAYS"
    else
      printf '\n(newest Phoenix backup %s, %s days old — ok)\n' "$latest_date" "$age_days"
    fi
  fi
elif [[ $bk_rc -eq 124 ]]; then
  echo "[TIMEOUT after ${CMD_TIMEOUT}s] S3 backup listing — host unreachable or stuck" >&2
else
  echo "[unreachable] could not list s3://example-project-backups/phoenix/ (exit $bk_rc)" >&2
fi

section "6. Re-readout trigger (LOOP-01)"
# Cheap APPROXIMATION only: query-shaped root spans since REREADOUT_WINDOW_START
# minus eval-tagged, read read-only off the host SQLite file (mode=ro, NOT
# immutable=1 -- immutable skips WAL locking on an actively-written DB and can
# return corrupt results per sqlite.org/wal.html; mode=ro is the documented-safe
# flag here). This counter only PROMPTS a re-readout -- it never decides. At
# trigger time run the full funnel in docs/operations/trace-readout-playbook.md,
# which strips testing/eval traffic properly; this line does not.
# 2026-07-22: span names carry the HTTP METHOD prefix ("POST /api/mcp/query"),
# so the original bare-path IN(...) list matched zero rows on every run since
# LOOP-01 was armed -- a structural false negative that could never fire, the
# same silently-clear-monitor class as the HYG-04 cron. Verified against live
# Phoenix: bare paths -> 0 rows ever; POST-prefixed -> 12,306 /api/mcp/query
# root spans since 2026-04-15. Keep the METHOD prefix on any name added here.
loop_sql="SELECT COUNT(*), COUNT(DISTINCT date(start_time)) FROM spans WHERE parent_id IS NULL AND name IN ('POST /api/mcp/query','POST /api/mcp/search','POST /api/query','POST /api/mcp/retrieve_document','POST /api/mcp/episode') AND start_time >= '${REREADOUT_WINDOW_START}' AND (JSON_EXTRACT(attributes,'\$.example-project.trace_type') IS NULL OR JSON_EXTRACT(attributes,'\$.example-project.trace_type') != 'eval');"
loop_out="$(timeout "$CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
  "sqlite3 'file:/mnt/phoenix-data/phoenix.db?mode=ro' \"${loop_sql}\"" 2>&1)" && loop_rc=0 || loop_rc=$?
if [[ $loop_rc -eq 0 && "$loop_out" =~ ^([0-9]+)\|([0-9]+)$ ]]; then
  n_candidates="${BASH_REMATCH[1]}"
  n_days="${BASH_REMATCH[2]}"
  printf 'candidate organic episodes since %s (approx, incl. any untagged testing): %s, across %s active days\n' \
    "$REREADOUT_WINDOW_START" "$n_candidates" "$n_days"
  if [[ "$n_candidates" -ge "$REREADOUT_CANDIDATE_THRESHOLD" && "$n_days" -ge "$REREADOUT_ACTIVE_DAYS_THRESHOLD" ]]; then
    printf '\n*** TRIGGER: >=%s candidates AND >=%s active days -- run docs/operations/trace-readout-playbook.md. ***\n' \
      "$REREADOUT_CANDIDATE_THRESHOLD" "$REREADOUT_ACTIVE_DAYS_THRESHOLD"
  fi
else
  # Do NOT swallow the cause: a bare "(count unavailable)" is indistinguishable
  # from a healthy zero and is how a dead monitor stays invisible.
  printf '(count unavailable) LOOP-01 query failed (exit %s): %s\n' \
    "$loop_rc" "${loop_out:-<no output>}" >&2
fi

section "7. Community staleness (last rebuild vs. 14-day threshold)"
# HYG-04 (2026-07-22): the weekly community-rebuild cron silently 400'd for
# 6 consecutive Sundays (2026-06-14..07-19, root cause: a vault key-file
# format change) and nobody noticed until a manual audit. Read the max
# updated_at across Community nodes directly from FalkorDB (no credentials
# needed -- prod FalkorDB has no auth) so this can never rot unseen again.
comm_out="$(timeout "$CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$PROD_HOST" \
  "docker exec example-project-falkordb redis-cli GRAPH.QUERY example-project \"MATCH (c:Community) RETURN max(c.updated_at) AS last_build, count(c) AS n\"" 2>&1)" \
  && comm_rc=0 || comm_rc=$?
if [[ $comm_rc -eq 0 ]]; then
  last_build_raw="$(printf '%s\n' "$comm_out" | sed -n '3p' | tr -d '\r')"
  n_communities="$(printf '%s\n' "$comm_out" | sed -n '4p' | tr -d '\r')"
  if [[ -n "${last_build_raw:-}" && "$last_build_raw" != "null" ]]; then
    last_build_date="${last_build_raw:0:10}"
    last_build_epoch="$(date -j -f %Y-%m-%d "$last_build_date" +%s 2>/dev/null || date -d "$last_build_date" +%s 2>/dev/null || echo 0)"
    now_epoch="$(date +%s)"
    if [[ "$last_build_epoch" -eq 0 ]]; then
      printf 'community last-build date unparseable (%s) -- check manually\n' "$last_build_raw"
    else
      age_days=$(( (now_epoch - last_build_epoch) / 86400 ))
      printf 'communities: %s, last updated_at: %s (%s days ago)\n' "${n_communities:-?}" "$last_build_raw" "$age_days"
      if [[ "$age_days" -ge 14 ]]; then
        printf '\n*** WARNING: community summaries are %s days stale (>= 14) -- rebuild cron may be broken or disarmed (see HYG-04 / com-01/com-02). ***\n' "$age_days"
      fi
    fi
  else
    echo "(no Community nodes found -- graph may never have been built)"
  fi
elif [[ $comm_rc -eq 124 ]]; then
  echo "[TIMEOUT after ${CMD_TIMEOUT}s] community staleness query -- host unreachable or stuck" >&2
else
  echo "[unreachable] community staleness query failed (exit $comm_rc)" >&2
fi

section "done"
