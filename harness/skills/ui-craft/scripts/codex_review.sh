#!/usr/bin/env bash
# The second blind reviewer: Codex reads the frames read-only and answers a brief.
#
#   bash codex_review.sh <brief.md> <answer.md> <frame.png>...
#
# The brief is the same prompt the Sonnet reviewer gets (REVIEW step 4, or the structural
# brief in references/direction.md). The model comes from ~/.codex/config.toml; the effort is
# set here, because a run outside the user's Codex session ignores the user config.
# It prints nothing for 1 to 5 minutes: run it in the background and read <answer.md> after.
# Exit 0 = answer written, 2 = not run (no codex, no brief, a missing frame, or an empty answer).
set -euo pipefail

[ $# -ge 3 ] || { echo "usage: codex_review.sh <brief.md> <answer.md> <frame.png>..." >&2; exit 2; }
command -v codex >/dev/null || { echo "codex CLI not found: skip the second reviewer and say so" >&2; exit 2; }
brief=$1 answer=$2
shift 2
[ -s "$brief" ] || { echo "no brief: $brief" >&2; exit 2; }
for f in "$@"; do [ -f "$f" ] || { echo "no frame: $f" >&2; exit 2; }; done

# The prompt goes before --image: the flag takes several files, so a prompt after it is read
# as a file name and Codex stops with "No prompt provided via stdin".
codex exec -s read-only --skip-git-repo-check -c model_reasoning_effort=high \
  -o "$answer" "$(cat "$brief")" --image "$@" < /dev/null > "$answer.log" 2>&1 \
  || { echo "codex failed: see $answer.log" >&2; exit 2; }
[ -s "$answer" ] || { echo "codex wrote no answer: see $answer.log" >&2; exit 2; }
echo "codex answer: $answer"
