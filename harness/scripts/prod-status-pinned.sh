#!/usr/bin/env bash
# prod-status-pinned.sh — content-hash-enforcing entry point for prod-status.sh.
#
# This wrapper is the ONLY allowlisted invocation of the prod-diagnostics lane
# (settings.json pins `bash <this path>`). It refuses to run prod-status.sh
# unless its sha256 matches the hash pinned below, so a later edit to the
# script CANNOT silently expand the allowlisted prod command surface — the
# exact hazard the SH-02 owner gate named. Re-pinning the hash is a
# human-gated change: edit PINNED_SHA256 here together with the script change,
# with the operator's explicit approval (see CLAUDE.md NEVER rule on self-widening
# permission surfaces).
set -euo pipefail

readonly SCRIPT="~/.claude/scripts/prod-status.sh"
readonly PINNED_SHA256="REPLACE_ME_recompute_for_your_own_scrubbed_prod-status.sh"

actual="$(shasum -a 256 "$SCRIPT" | awk '{print $1}')"
if [[ "$actual" != "$PINNED_SHA256" ]]; then
  echo "REFUSED: ${SCRIPT} sha256 ${actual} does not match pinned ${PINNED_SHA256}." >&2
  echo "The script changed since the owner-approved pin. Re-pin requires explicit human approval." >&2
  exit 1
fi
exec bash "$SCRIPT"
