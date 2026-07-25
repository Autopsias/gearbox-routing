#!/usr/bin/env bash
# install.sh — one-command Gearbox installer (REL-01).
#
# Copies the routing framework (SSOT + guard + resolver + renderer + skills +
# fixtures + evals) into a TARGET home, sets --provider, splices the CLAUDE.md
# routing digest, and runs the final all-artifact guard (verify-routing.sh).
# Safe to re-run: running it twice with the same flags produces no diff and no
# duplicate ROUTING block.
#
# SAFETY (non-negotiable, per adversarial review 2026-07-05):
#   - The default target is a FRESH directory, never the operator's real
#     ~/.claude. Touching the live ~/.claude requires BOTH --claude-home
#     "$HOME/.claude" AND --i-understand-this-mutates-live-claude.
#   - --provider anthropic (or any shipped example provider) installs EXAMPLE
#     profiles (see model-routing.yaml's own "EXAMPLE" markers) — installing
#     one as if it were live policy requires --accept-example-profile, unless
#     you point --profile at your own researched profile file instead.
#   - Any existing file this script would overwrite is backed up first
#     (<file>.bak-<timestamp>), never silently clobbered. A target
#     model-routing.yaml whose version >= the package's, or that looks like
#     real (non-example) production policy, is refused unless --force.
#
# Usage:
#   ./install.sh [--claude-home DIR] [--provider NAME] [--accept-example-profile]
#                [--profile FILE] [--force] [--i-understand-this-mutates-live-claude]
#                [--uninstall] [-h|--help]
#
# Exit codes: 0 = installed clean, guard PASS. 1 = guard FAIL (installed but
# drift found). 2 = refused before installing anything (bad flags, missing
# opt-in, version/force guard tripped, tooling failure).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d%H%M%S)"

PROVIDER="anthropic"
CLAUDE_HOME=""
ACCEPT_EXAMPLE=0
PROFILE_FILE=""
FORCE=0
UNDERSTAND_LIVE=0
UNINSTALL=0

usage() {
  cat <<'EOF'
usage: install.sh [--claude-home DIR] [--provider NAME] [--accept-example-profile]
                   [--profile FILE] [--force] [--i-understand-this-mutates-live-claude]
                   [--uninstall] [-h|--help]

  --claude-home DIR                     install target (default: ./gearbox-install-<timestamp>
                                         under the current directory — NEVER ~/.claude unless
                                         you pass it explicitly + the opt-in flag below)
  --provider NAME                       active_provider to set (default: anthropic).
                                         Must name a provider already in model-routing.yaml,
                                         or be paired with --profile supplying your own.
  --accept-example-profile              required when installing a shipped EXAMPLE provider
                                         profile (anthropic/openai/gemini) as if it were live
                                         policy. Omit this by supplying --profile instead.
  --profile FILE                        use FILE as the installed model-routing.yaml instead
                                         of this repo's copy (your own researched profile) —
                                         satisfies the accept-example-profile requirement.
  --force                               allow overwriting a target model-routing.yaml whose
                                         version >= this package's, or that looks like
                                         non-example (production) policy
  --i-understand-this-mutates-live-claude
                                         required IN ADDITION to --claude-home "$HOME/.claude"
                                         before this script will touch the real live home
  --uninstall                           restore the most recent .bak-* backups under
                                         --claude-home and strip the ROUTING block; does not
                                         delete files that were newly created (never destroys
                                         work install.sh didn't itself create a backup for)
  -h, --help                            this message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --claude-home) CLAUDE_HOME="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --accept-example-profile) ACCEPT_EXAMPLE=1; shift ;;
    --profile) PROFILE_FILE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --i-understand-this-mutates-live-claude) UNDERSTAND_LIVE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FATAL: unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$CLAUDE_HOME" ]]; then
  CLAUDE_HOME="$(pwd)/gearbox-install-${TIMESTAMP}"
fi
# Resolve to an absolute path (target may not exist yet).
CLAUDE_HOME="$(cd "$(dirname "$CLAUDE_HOME")" 2>/dev/null && pwd)/$(basename "$CLAUDE_HOME")" || {
  echo "FATAL: --claude-home parent directory does not exist: $CLAUDE_HOME" >&2; exit 2;
}

REAL_HOME_CLAUDE="$(cd "$HOME" && pwd)/.claude"
if [[ "$CLAUDE_HOME" == "$REAL_HOME_CLAUDE" && $UNDERSTAND_LIVE -ne 1 ]]; then
  echo "FATAL: --claude-home resolves to your real $HOME/.claude." >&2
  echo "This installer refuses to touch the live Claude home without an explicit opt-in." >&2
  echo "Re-run with --i-understand-this-mutates-live-claude if that is really what you want." >&2
  exit 2
fi

echo "install.sh — Gearbox installer"
echo "  repo:        $REPO_DIR"
echo "  target home: $CLAUDE_HOME"
echo "  provider:    $PROVIDER"
echo

# ---------------------------------------------------------------------------
# Uninstall path
# ---------------------------------------------------------------------------
if [[ $UNINSTALL -eq 1 ]]; then
  if [[ ! -d "$CLAUDE_HOME" ]]; then
    echo "FATAL: nothing to uninstall — $CLAUDE_HOME does not exist" >&2
    exit 2
  fi
  echo "== uninstall: restoring most-recent .bak-* files under $CLAUDE_HOME =="
  claude_md="$CLAUDE_HOME/CLAUDE.md"
  claude_md_had_backup=0
  restored=0
  while IFS= read -r -d '' bak; do
    orig="${bak%.bak-*}"
    cp -p "$bak" "$orig"
    echo "  restored: $orig (from $(basename "$bak"))"
    [[ "$orig" == "$claude_md" ]] && claude_md_had_backup=1
    restored=$((restored + 1))
  done < <(find "$CLAUDE_HOME" -name '*.bak-*' -print0 | sort -z -t- -k99 -r)
  echo "  restored $restored file(s) from backup."
  echo
  # Only strip the ROUTING block from CLAUDE.md if it had NO backup — a
  # restored backup already reflects the correct pre-install content (which
  # may legitimately contain its own routing block from before this tool ever
  # touched it) and must be left exactly as restored, never re-mutated. A
  # CLAUDE.md with no backup means install.sh created it fresh (--install
  # bootstrap onto a missing/markerless file), so stripping is the correct
  # undo for THAT file only.
  if [[ $claude_md_had_backup -eq 1 ]]; then
    echo "== CLAUDE.md had a pre-existing backup — restored as-is, not stripping =="
  elif [[ -f "$claude_md" ]]; then
    echo "== stripping ROUTING block from $CLAUDE_HOME/CLAUDE.md (no backup existed — this file was created by install.sh) =="
    python3 - "$claude_md" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    text = f.read()
new = re.sub(r"[ \t]*<!-- BEGIN ROUTING -->.*?<!-- END ROUTING -->\n?", "", text, flags=re.S)
if new != text:
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"  stripped ROUTING block from {path}")
else:
    print(f"  no ROUTING block found in {path} — nothing to strip")
PY
  else
    echo "  no CLAUDE.md at $claude_md — nothing to strip"
  fi
  echo
  echo "Uninstall complete. Newly-created files (ones with no .bak-* backup) were left in place —"
  echo "remove $CLAUDE_HOME yourself if you want a full clean sweep."
  exit 0
fi

# ---------------------------------------------------------------------------
# --provider validity + example-profile opt-in
# ---------------------------------------------------------------------------
SSOT_SRC="$REPO_DIR/claude/model-routing.yaml"
if [[ -n "$PROFILE_FILE" ]]; then
  [[ -f "$PROFILE_FILE" ]] || { echo "FATAL: --profile file not found: $PROFILE_FILE" >&2; exit 2; }
  SSOT_SRC="$PROFILE_FILE"
else
  if [[ $ACCEPT_EXAMPLE -ne 1 ]]; then
    echo "FATAL: installing this repo's shipped model-routing.yaml uses EXAMPLE provider" >&2
    echo "profiles (anthropic/openai/gemini) — verify-before-use data, not researched live" >&2
    echo "policy (see model-routing.yaml's own EXAMPLE markers)." >&2
    echo "Pass --accept-example-profile to install it anyway, or --profile FILE to supply" >&2
    echo "your own researched profile instead." >&2
    exit 2
  fi
fi

known_providers="$(python3 - "$SSOT_SRC" <<'PY'
import re, sys
with open(sys.argv[1], encoding="utf-8") as f:
    text = f.read()
p_start = text.find("\nproviders:\n")
p_end = text.find("\nprices:\n", p_start) if p_start != -1 else -1
block = text[p_start: p_end if p_end != -1 else len(text)] if p_start != -1 else ""
names = re.findall(r"^  ([\w-]+):\s*$", block, re.M)
print(" ".join(names))
PY
)"
if ! grep -qE "^  ${PROVIDER}:\s*\$" "$SSOT_SRC" || ! echo "$known_providers" | grep -qw "$PROVIDER"; then
  echo "FATAL: provider '$PROVIDER' has no providers.$PROVIDER: block in $SSOT_SRC" >&2
  echo "Known providers: $known_providers" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Version/force guard on an EXISTING target model-routing.yaml
# ---------------------------------------------------------------------------
target_ssot="$CLAUDE_HOME/claude/model-routing.yaml"
if [[ -f "$target_ssot" && $FORCE -ne 1 ]]; then
  new_version="$(grep -oE '^version:\s*"?[0-9]+\.[0-9]+\.[0-9]+"?' "$SSOT_SRC" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
  old_version="$(grep -oE '^version:\s*"?[0-9]+\.[0-9]+\.[0-9]+"?' "$target_ssot" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "")"
  is_example="$(grep -c 'EXAMPLE' "$target_ssot" || true)"
  if [[ -z "$old_version" ]]; then
    echo "FATAL: existing target model-routing.yaml has no parseable version: — refusing to" >&2
    echo "overwrite what may be hand-edited production policy without --force." >&2
    exit 2
  fi
  ver_ge() { [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" == "$1" ]]; }
  if ver_ge "$old_version" "$new_version" && [[ "$old_version" != "$new_version" || "$is_example" -eq 0 ]]; then
    echo "FATAL: existing target model-routing.yaml (version $old_version) is >= this package's" >&2
    echo "($new_version) and does not look like the shipped example (no EXAMPLE markers found) —" >&2
    echo "refusing to overwrite what looks like real production routing policy." >&2
    echo "Pass --force if you really want to overwrite it (it will still be backed up first)." >&2
    exit 2
  fi
fi

# ---------------------------------------------------------------------------
# Copy list — preserves structure under $CLAUDE_HOME
# model-routing.yaml is handled SEPARATELY below (not in this list): its
# content depends on --provider, and diffing/backing-up must compare against
# the FINAL post-provider-swap content, not the raw pre-swap source — otherwise
# a same-flags re-run would always see a diff (source has active_provider:
# anthropic; the previously-installed file has whatever --provider set it to)
# and spuriously back up + rewrite on every single run, breaking idempotency.
# ---------------------------------------------------------------------------
declare -a COPY_PAIRS=(
  "claude/model-routing.digest.md:claude/model-routing.digest.md"
  "claude/skills/routing-update:claude/skills/routing-update"
  "claude/skills/routing-retro:claude/skills/routing-retro"
  "claude/scripts/verify-routing.sh:claude/scripts/verify-routing.sh"
  "claude/scripts/render-routing-digest.py:claude/scripts/render-routing-digest.py"
  "claude/scripts/resolve_route.py:claude/scripts/resolve_route.py"
  "claude/evals/routing:claude/evals/routing"
  "claude/fixtures/routing-guard:claude/fixtures/routing-guard"
  "claude/fixtures/route-resolver:claude/fixtures/route-resolver"
)

backups=()
copies=()

backup_if_differs() {
  local dst="$1"
  local src="$2"
  if [[ -e "$dst" ]]; then
    if [[ -d "$dst" ]]; then
      return 0   # directories are merged file-by-file by cp -R below; per-file backup happens there
    fi
    if ! cmp -s "$src" "$dst"; then
      local bak="${dst}.bak-${TIMESTAMP}"
      cp -p "$dst" "$bak"
      backups+=("$dst -> $(basename "$bak")")
    fi
  fi
}

copy_tree_with_backup() {
  # Copies file-by-file so each differing pre-existing file gets its own backup,
  # rather than one backup of a whole directory (which would bury which files
  # actually changed).
  local src_root="$1" dst_root="$2"
  mkdir -p "$dst_root"
  while IFS= read -r -d '' f; do
    local rel="${f#"$src_root"/}"
    local dst="$dst_root/$rel"
    mkdir -p "$(dirname "$dst")"
    backup_if_differs "$dst" "$f"
    cp -p "$f" "$dst"
    copies+=("$rel")
  done < <(find "$src_root" -type f -print0)
}

# Stage the final model-routing.yaml content (source + --provider swap applied)
# BEFORE any diff/backup decision, so idempotency is judged against the true
# post-install content, not the raw pre-swap source.
staged_ssot="$(mktemp)"
python3 - "$SSOT_SRC" "$PROVIDER" "$staged_ssot" <<'PY'
import re, sys
src_path, provider, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src_path, encoding="utf-8") as f:
    text = f.read()
new_text, n = re.subn(r"^active_provider:\s*\S+(\s*(?:#.*)?)$", f"active_provider: {provider}" + r"\1", text, count=1, flags=re.M)
if n != 1:
    print(f"FATAL: could not find/replace a single top-level active_provider: line in {src_path}", file=sys.stderr)
    sys.exit(2)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(new_text)
PY
STAGE_RC=$?
if [[ $STAGE_RC -ne 0 ]]; then
  rm -f "$staged_ssot"
  exit 2
fi

echo "== copying framework files into $CLAUDE_HOME =="
mkdir -p "$(dirname "$target_ssot")"
backup_if_differs "$target_ssot" "$staged_ssot"
cp -p "$staged_ssot" "$target_ssot"
rm -f "$staged_ssot"
copies+=("claude/model-routing.yaml")
echo "  active_provider set to '$PROVIDER' in $target_ssot"

for pair in "${COPY_PAIRS[@]}"; do
  src_rel="${pair%%:*}"
  dst_rel="${pair##*:}"
  src="$REPO_DIR/$src_rel"
  dst="$CLAUDE_HOME/$dst_rel"
  if [[ ! -e "$src" ]]; then
    echo "FATAL: expected source path missing: $src" >&2
    exit 2
  fi
  if [[ -d "$src" ]]; then
    copy_tree_with_backup "$src" "$dst"
  else
    mkdir -p "$(dirname "$dst")"
    backup_if_differs "$dst" "$src"
    cp -p "$src" "$dst"
    copies+=("$dst_rel")
  fi
done
echo "  copied ${#copies[@]} file(s)."
if [[ ${#backups[@]} -gt 0 ]]; then
  echo "  backed up ${#backups[@]} pre-existing differing file(s):"
  for b in "${backups[@]}"; do echo "    - $b"; done
else
  echo "  no pre-existing differing files — nothing backed up."
fi
echo

# ---------------------------------------------------------------------------
# chmod +x the ported scripts
# ---------------------------------------------------------------------------
chmod +x "$CLAUDE_HOME/claude/scripts/verify-routing.sh"
chmod +x "$CLAUDE_HOME/claude/scripts/render-routing-digest.py"
chmod +x "$CLAUDE_HOME/claude/scripts/resolve_route.py"
echo "== chmod +x applied to ported scripts =="
echo

# ---------------------------------------------------------------------------
# Wire CLAUDE.md: render + splice via render-routing-digest.py --install
# (marker-state handling — absent file / zero markers / one pair / corrupt —
# is entirely owned by render-routing-digest.py's own safety invariants; see
# its module docstring. --install covers "create if missing" and "bootstrap
# on zero markers"; an existing single pair is replaced in place; duplicate/
# malformed markers make it refuse with a clear message, never blind-append.)
# ---------------------------------------------------------------------------
target_claude_md="$CLAUDE_HOME/CLAUDE.md"
# Snapshot pre-render content (if any) so we can back it up ONLY if the render
# actually changes it (render-routing-digest.py does its own atomic write; we
# back up before calling it since it may overwrite in place).
pre_render_snapshot=""
if [[ -f "$target_claude_md" ]]; then
  pre_render_snapshot="$(mktemp)"
  cp -p "$target_claude_md" "$pre_render_snapshot"
fi

echo "== rendering + splicing CLAUDE.md routing digest =="
set +e
python3 "$CLAUDE_HOME/claude/scripts/render-routing-digest.py" \
  --repo-dir "$CLAUDE_HOME" \
  --claude-home "$CLAUDE_HOME" \
  --yaml "$target_ssot" \
  --source "$CLAUDE_HOME/claude/model-routing.digest.md" \
  --target "$target_claude_md" \
  --install
RENDER_RC=$?
set -e
if [[ $RENDER_RC -ne 0 ]]; then
  echo "FATAL: render-routing-digest.py refused to write CLAUDE.md (exit $RENDER_RC) — see message above." >&2
  [[ -n "$pre_render_snapshot" ]] && rm -f "$pre_render_snapshot"
  exit 2
fi
if [[ -n "$pre_render_snapshot" ]] && ! cmp -s "$pre_render_snapshot" "$target_claude_md"; then
  bak="${target_claude_md}.bak-${TIMESTAMP}"
  cp -p "$pre_render_snapshot" "$bak"
  backups+=("$target_claude_md -> $(basename "$bak")")
  echo "  backed up pre-existing CLAUDE.md -> $(basename "$bak")"
fi
[[ -n "$pre_render_snapshot" ]] && rm -f "$pre_render_snapshot"
echo

# ---------------------------------------------------------------------------
# Run the guard as the final all-artifact check
# ---------------------------------------------------------------------------
echo "== running verify-routing.sh --full over the installed tree =="
set +e
CLAUDE_HOME="$CLAUDE_HOME" \
SSOT="$target_ssot" \
AGENTS_DIR="$CLAUDE_HOME/claude/agents" \
bash "$CLAUDE_HOME/claude/scripts/verify-routing.sh" --full
GUARD_RC=$?
set -e
echo

echo "==================================================================="
echo "install.sh summary"
echo "  target home:     $CLAUDE_HOME"
echo "  active_provider:  $PROVIDER"
echo "  files copied:     ${#copies[@]}"
echo "  files backed up:  ${#backups[@]}"
if [[ ${#backups[@]} -gt 0 ]]; then
  for b in "${backups[@]}"; do echo "    - $b"; done
fi
if [[ $GUARD_RC -eq 0 ]]; then
  echo "  guard:            PASS"
else
  echo "  guard:            FAIL (exit $GUARD_RC) — see verify-routing.sh output above"
fi
echo "==================================================================="
exit "$GUARD_RC"
