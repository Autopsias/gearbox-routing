#!/usr/bin/env bash
# sanitize_runner_log.sh — redact a real self-hosted GitHub Actions Worker_*.log
# into a checked-in fixture, AND verify a fixture is clean.
#
# Used by ST-03 (ship-tail CI loop, runner-log path). The runner-log parser in
# ship-tail consumes ONLY sanitized fixtures in tests; this script is the single
# point of contact for the redaction rules so the rules live as executable code,
# not prose that drifts.
#
# Usage:
#   sanitize_runner_log.sh redact  <raw_worker.log> <out_fixture.log>
#   sanitize_runner_log.sh verify  <fixture.log>            # exit 1 if any leak
#
# REDACTION RULES (each MUST have a matching verify check below):
#   R1 absolute home paths     /Users/<name>/...  /home/<name>/...  -> /Users/RUNNER_HOME/...
#   R2 runner IDs              <slug>-runner-<N>  bin.<ver>          -> RUNNER_<N>
#   R3 tokens / secrets        ghs_/ghp_/ghu_/ github_pat_ / bearer <jwt> / x-access-token
#   R4 real repo identity      $REAL_REPO and owner ids           -> acme-org/example-repo
#   R5 owner / actor ids       repository_owner / actor numeric ids -> redacted
#
# The fixture intentionally KEEPS: the verdict line, jobDisplayName, head_sha,
# run_id, jobId, and timestamps — those are what the parser correlates on. SHAs
# are synthetic-but-stable in the fixture (see SYNTHETIC_SHA), not real.

set -euo pipefail

# --- denylist of UNRELATED real repos that must never appear in a fixture -----
# (the fixture's own identity is rewritten to the synthetic repo by R4; anything
#  here is a real repo from the operator's machine that would be a leak)
UNRELATED_REPOS=("YourOrg/your-private-repo" "your-private-repo-name")  # ponytail: populate with YOUR OWN private repo names

SYNTHETIC_REPO="acme-org/example-repo"

redact() {
  local raw="$1" out="$2"
  [[ -f "$raw" ]] || { echo "redact: raw log not found: $raw" >&2; exit 2; }
  sed -E \
    -e 's#/Users/[^/"'"'"' ]+#/Users/RUNNER_HOME#g' \
    -e 's#/home/[^/"'"'"' ]+#/Users/RUNNER_HOME#g' \
    -e 's#[A-Za-z0-9_.-]+-runner-([0-9]+)#RUNNER_\1#g' \
    -e 's#bin\.[0-9]+\.[0-9]+\.[0-9]+#bin.RUNNER_VER#g' \
    -e 's#gh[pousr]_[A-Za-z0-9]{20,}#REDACTED_TOKEN#g' \
    -e 's#github_pat_[A-Za-z0-9_]{20,}#REDACTED_TOKEN#g' \
    -e 's#(Bearer|bearer) [A-Za-z0-9._-]{20,}#\1 REDACTED_TOKEN#g' \
    -e 's#x-access-token:[A-Za-z0-9._-]+#x-access-token:REDACTED_TOKEN#g' \
    -e "s#${REAL_REPO:?set REAL_REPO to the repo identity to redact, e.g. YourOrg/your-private-repo}#${SYNTHETIC_REPO}#g" \
    -e 's#git://github.com/[^"]+\.git#git://github.com/'"${SYNTHETIC_REPO}"'.git#g' \
    "$raw" \
  | sed -E \
    -e '/"k": "(repository_owner|actor|triggering_actor)"/{n; s#"v": "[^"]+"#"v": "acme-org"#;}' \
    -e '/"k": "(repository_owner_id|actor_id|repository_id)"/{n; s#"v": "[0-9]+"#"v": "REDACTED_ID"#;}' \
    > "$out"
  echo "redacted -> $out ($(wc -l < "$out" | tr -d ' ') lines)"
  verify "$out"
}

verify() {
  local f="$1" rc=0
  [[ -f "$f" ]] || { echo "verify: fixture not found: $f" >&2; exit 2; }

  # R1: no real absolute home paths (only the sanitized RUNNER_HOME token allowed)
  if grep -qE '/Users/[a-z][a-z0-9_-]+/|/home/[a-z][a-z0-9_-]+/' "$f" \
     && grep -qvE '/Users/RUNNER_HOME' <<<"$(grep -oE '/Users/[^/"]+|/home/[^/"]+' "$f" | sort -u)"; then
    # Re-check precisely: any /Users/<x> where <x> != RUNNER_HOME is a leak.
    if grep -oE '/(Users|home)/[A-Za-z0-9_.-]+' "$f" | sort -u | grep -qvE '^/Users/RUNNER_HOME$'; then
      echo "LEAK R1: real home path present"; grep -oE '/(Users|home)/[A-Za-z0-9_.-]+' "$f" | sort -u | grep -vE '^/Users/RUNNER_HOME$' | head; rc=1
    fi
  fi

  # R2: no runner IDs of the form <slug>-runner-N (only RUNNER_N allowed)
  if grep -qE '[A-Za-z0-9_.-]+-runner-[0-9]+' "$f"; then
    echo "LEAK R2: runner ID present"; grep -oE '[A-Za-z0-9_.-]+-runner-[0-9]+' "$f" | sort -u | head; rc=1
  fi

  # R3: no real tokens / secrets
  if grep -qE 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(Bearer|bearer) [A-Za-z0-9._-]{20,}|x-access-token:[A-Za-z0-9._-]{10,}' "$f"; then
    echo "LEAK R3: token/secret present"; rc=1
  fi

  # R4: no unrelated real repo names
  for r in "${UNRELATED_REPOS[@]}"; do
    if grep -qF "$r" "$f"; then echo "LEAK R4: unrelated/real repo '$r' present"; rc=1; fi
  done

  # R5: no real owner/actor numeric ids (key + value may be on adjacent lines)
  if grep -A1 -E '"k": "(repository_owner_id|actor_id|repository_id)"' "$f" | grep -qE '"v": "[0-9]+"'; then
    echo "LEAK R5: numeric owner id present"; grep -A1 -E '"k": "(repository_owner_id|actor_id|repository_id)"' "$f" | grep -E '"v": "[0-9]+"' | head; rc=1
  fi

  if [[ $rc -eq 0 ]]; then echo "verify OK: $f is sanitized"; else echo "verify FAILED: $f"; fi
  return $rc
}

cmd="${1:-}"; shift || true
case "$cmd" in
  redact) redact "$@" ;;
  verify) verify "$@" ;;
  *) echo "usage: $0 {redact <raw> <out> | verify <fixture>}" >&2; exit 2 ;;
esac
