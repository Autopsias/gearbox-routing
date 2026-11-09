#!/usr/bin/env bash
# Print the path of a work copy of the vendored Stageshow with its npm packages
# installed, building it once per pin. Upstream's own workflow writes examples/
# and node_modules/ into its folder; doing that in vendor/ breaks the pin.
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
tree="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["tree"][:12])' "$SKILL/vendor.json")"
[ -n "$tree" ] || { echo "stage: no pin in $SKILL/vendor.json" >&2; exit 2; }
W="${UI_CRAFT_HOME:-$HOME/.cache/ui-craft}/stageshow-$tree"
if [ ! -f "$W/.ready" ]; then
  mkdir -p "$(dirname "$W")"
  tmp="$(mktemp -d "${W}.build.XXXXXX")"   # build aside, then move: never a half-built $W
  cp -R "$SKILL/vendor/stageshow/." "$tmp/"
  # --ignore-scripts: no package runs code at install time.
  (cd "$tmp" && npm ci --ignore-scripts --no-audit --no-fund --loglevel=error) >&2
  touch "$tmp/.ready"
  [ -e "$W" ] && mv "$W" "${W}.stale.$$"     # an earlier failed build; kept, not deleted
  mv "$tmp" "$W"
fi
echo "$W"
