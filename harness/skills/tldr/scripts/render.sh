#!/usr/bin/env bash
# Render tldr outputs with tools already on the machine.
#   render.sh pdf  in.html out.pdf            one A4 page, no header/footer
#   render.sh png  in.html out.png [WxH]      screenshot, default 1200x1600
#   render.sh docx in.md   out.docx           pandoc
# Chrome headless prints allocator / task_policy noise to stderr and still
# writes the file (observed). Judge by the output file.
set -euo pipefail

CHROME="${TLDR_CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mode="${1:-}"; in="${2:-}"; out="${3:-}"
[ -n "$mode" ] && [ -n "$in" ] && [ -n "$out" ] || { sed -n '2,5p' "$0"; exit 2; }
[ -f "$in" ] || { echo "render.sh: no such input: $in" >&2; exit 2; }

need_chrome() {
  [ -x "$CHROME" ] && return
  echo "render.sh: Chrome not found at $CHROME" >&2
  echo "  fix: brew install --cask google-chrome, or set TLDR_CHROME to a Chromium binary" >&2
  exit 3
}

abs() { python3 -c 'import os,sys;print(os.path.abspath(sys.argv[1]))' "$1"; }

case "$mode" in
  pdf)
    need_chrome
    "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
      --virtual-time-budget=2000 --print-to-pdf="$(abs "$out")" \
      "file://$(abs "$in")" 2>/dev/null
    ;;
  png)
    need_chrome
    size="${4:-1200x1600}"; w="${size%x*}"; h="${size#*x}"
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
      --virtual-time-budget=2000 --window-size="$w,$h" \
      --screenshot="$(abs "$out")" "file://$(abs "$in")" 2>/dev/null
    ;;
  docx)
    command -v pandoc >/dev/null || { echo "render.sh: pandoc missing (brew install pandoc)" >&2; exit 3; }
    pandoc "$in" -o "$out"
    ;;
  *) echo "render.sh: unknown mode '$mode' (pdf|png|docx)" >&2; exit 2 ;;
esac

[ -s "$out" ] || { echo "render.sh: $out was not written" >&2; exit 1; }
echo "$out $(wc -c <"$out" | tr -d ' ') bytes"
