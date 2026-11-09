#!/usr/bin/env bash
# Put the pinned Stageshow at vendor/stageshow when it is missing (a copy that
# does not ship it, for example the public export: its artwork is not MIT).
# Downloads upstream at the pinned commit and refuses a tree whose git hash is
# not the pinned one. No-op when the folder exists.
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
V="$SKILL/vendor/stageshow"
[ -d "$V" ] && { echo "$V"; exit 0; }
read -r repo pin path tree < <(python3 -c 'import json,sys; e=json.load(open(sys.argv[1]))[0]; print(e["repo"], e["pin"], e["path"], e["tree"])' "$SKILL/vendor.json")
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git -C "$tmp" init -q
git -C "$tmp" fetch -q --depth 1 "https://github.com/$repo.git" "$pin"
got="$(git -C "$tmp" rev-parse "FETCH_HEAD:$path")"
[ "$got" = "$tree" ] || { echo "fetch_vendor: $repo@$pin:$path has tree $got, pinned $tree; refusing" >&2; exit 1; }
mkdir -p "$V.part" && git -C "$tmp" archive FETCH_HEAD "$path" | tar -x -C "$V.part" --strip-components=1
mv "$V.part" "$V"
echo "$V"
