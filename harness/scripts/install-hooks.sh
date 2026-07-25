#!/usr/bin/env bash
# install-hooks.sh — wire the tracked githooks/ into THIS clone.
#
# Git cannot version .git/hooks, so the routing/secret guards would be absent in
# every fresh clone of the private source repo. They live as tracked files under
# githooks/; this script points git at them via core.hooksPath. Run it once after
# cloning (and it is safe to re-run — idempotent).
#
# verify-routing.sh FAILS when core.hooksPath is unset or a guard hook is missing,
# so a clone that skips this step cannot report green.
set -euo pipefail

ROOT="$(git -C "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" rev-parse --show-toplevel)"
HOOKS_REL="githooks"
HOOKS_DIR="$ROOT/$HOOKS_REL"

for h in pre-commit commit-msg; do
  [[ -f "$HOOKS_DIR/$h" ]] || { echo "FATAL: missing $HOOKS_DIR/$h" >&2; exit 1; }
  chmod +x "$HOOKS_DIR/$h"
done

git -C "$ROOT" config core.hooksPath "$HOOKS_REL"

# Assert what git will actually run — a config write that does not resolve to an
# executable hook is the exact failure this script exists to prevent.
resolved="$(git -C "$ROOT" config --get core.hooksPath)"
[[ "$resolved" == "$HOOKS_REL" ]] || { echo "FATAL: core.hooksPath is '$resolved', expected '$HOOKS_REL'" >&2; exit 1; }
for h in pre-commit commit-msg; do
  [[ -x "$HOOKS_DIR/$h" ]] || { echo "FATAL: $HOOKS_DIR/$h is not executable" >&2; exit 1; }
done

echo "hooks installed: core.hooksPath=$resolved -> $HOOKS_DIR"
echo "  pre-commit  (gitleaks secret gate + routing drift guard)"
echo "  commit-msg  (routing-pin approval-token gate)"
