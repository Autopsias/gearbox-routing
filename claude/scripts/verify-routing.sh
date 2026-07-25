#!/usr/bin/env bash
# verify-routing.sh — provider-aware drift guard for claude/model-routing.yaml (the
# tier/effort/provider-profile SSOT for this repo).
#
# Ported from the source deployment's ~/.claude/scripts/verify-routing.sh (S05, this
# port). DROPPED on port: the epic-dev-assignments.yaml cross-check and all epic-*
# agent special-casing (the epic-dev suite is not part of Gearbox — see
# GENERICIZATION.md "Drops"), the run.py _FALLBACK_LADDER/_REASONING_DIRECTIVE ast
# lockstep (that was a plan-execute-internal consumer, out of scope here), and the
# pre-commit/commit-msg hook surface-list convergence check (this repo's leak-defence
# hooks live under .githooks/, a different mechanism than the source's ~/.claude/.git/
# hooks — re-add a convergence check there if/when this repo grows its own routing-
# affecting commit hooks).
#
# ADDED on port (provider-aware checks — the reason this guard needs re-porting at
# all): every task_classes.<class>.tier resolves under EVERY providers.<name>.models
# map (not just the active one — a provider you might swap TO must already be
# complete, not just the one you're on today); active_provider: is a valid key under
# providers:; version: is present AND semver-shaped (MAJOR.MINOR.PATCH, not the
# source deployment's bare integer — see docs/VERSIONING.md); an as_of staleness WARN
# when a provider's calibration.date is older than a threshold.
#
# KEPT from the source: agent-frontmatter-vs-SSOT check (a) (tier now, not model —
# GUARD PARSE CONTRACT in model-routing.yaml requires this), haiku-style
# no-effort-key invariant (b) generalized to ANY tier whose active-provider effort
# map is all-null, and the CLAUDE.md digest re-render check (e).
#
# ADDED (S07, final integration): check (f) — resolve_route.py exists, imports
# cleanly, and baseline-resolves EVERY task_classes row for EVERY declared
# provider (not just active_provider). This is what actually ENFORCES the
# SSOT's own `consumers:` table stamp (`claude/scripts/resolve_route.py: {stamp:
# code-authoritative}`) — until this check existed, that stamp was aspirational
# prose with nothing verifying it. Runs in both --core and --full.
#
# POSIX/python3-only (no PyYAML — regex parsing), CLAUDE_HOME env override, loud
# actionable failures. Fails CLOSED on both a POLICY VIOLATION (drift found) and a
# TOOLING CRASH (missing dependency, malformed SSOT, python3 traceback) — a broken
# guard must never silently become a bypass.
#
# Modes:
#   --core   checks (a)+(b)+(tier-completeness)+(active-provider)+(version) — everything
#            except the CLAUDE.md digest re-render check (e), which needs a real
#            CLAUDE.md target to diff against.
#   --full   adds (e) CLAUDE.md digest re-render check.
#   (bare)   uses DEFAULT_MODE below.
#   --strict turns "unknown agent (no SSOT row)" from a WARN into a FAILURE. Drift on
#            a KNOWN agent (SSOT row exists, frontmatter disagrees) ALWAYS fails,
#            strict or not.
set -euo pipefail

CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSOT="${SSOT:-$REPO_DIR/claude/model-routing.yaml}"
# Gearbox ships no agents/ of its own (it's a framework repo, not a live agent
# deployment) — default AGENTS_DIR under the REPO, not $CLAUDE_HOME/agents. Pointing
# this at $CLAUDE_HOME/agents would silently check Gearbox's example SSOT against a
# DIFFERENT, unrelated live deployment's frontmatter and always show noise. Adopters
# who vendor this SSOT as their real routing policy should set AGENTS_DIR explicitly
# to wherever THEIR agents live.
AGENTS_DIR="${AGENTS_DIR:-$REPO_DIR/claude/agents}"
DIGEST_RENDERER="$REPO_DIR/claude/scripts/render-routing-digest.py"
STALE_DAYS="${ROUTING_STALE_DAYS:-180}"   # as_of staleness WARN threshold (days)

DEFAULT_MODE="core"
MODE="$DEFAULT_MODE"
STRICT=0

for arg in "$@"; do
  case "$arg" in
    --core) MODE="core" ;;
    --full) MODE="full" ;;
    --strict) STRICT=1 ;;
    -h|--help)
      echo "usage: verify-routing.sh [--core|--full] [--strict]"
      echo "  env: CLAUDE_HOME (default ~/.claude), SSOT (default <repo>/claude/model-routing.yaml),"
      echo "       AGENTS_DIR (default <repo>/claude/agents), ROUTING_STALE_DAYS (default 180)"
      exit 0
      ;;
    *)
      echo "FATAL: unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

for p in "$SSOT"; do
  [[ -e "$p" ]] || { echo "FATAL: missing required path: $p" >&2; exit 2; }
done

echo "verify-routing.sh — mode=$MODE strict=$STRICT claude_home=$CLAUDE_HOME ssot=$SSOT"
echo

OVERALL_FAIL=0
OVERALL_CODE=0
_note_fail() {
  local rc=$1
  OVERALL_FAIL=1
  if [[ $rc -eq 2 ]]; then
    OVERALL_CODE=2
  elif [[ $OVERALL_CODE -ne 2 ]]; then
    OVERALL_CODE=1
  fi
}

# ---- (a)(b) + provider-aware checks (python3, regex parsing) --------------
echo "== (a) agent frontmatter vs SSOT · (b) no-effort-dial invariant · (tier<->provider) · (version) =="
set +e
SSOT="$SSOT" AGENTS_DIR="$AGENTS_DIR" MODE="$MODE" STRICT="$STRICT" STALE_DAYS="$STALE_DAYS" \
python3 - <<'PY'
import datetime as dt
import glob, os, re, sys

SSOT = os.environ["SSOT"]
AGENTS_DIR = os.environ["AGENTS_DIR"]
MODE = os.environ["MODE"]
STRICT = os.environ["STRICT"] == "1"
STALE_DAYS = int(os.environ["STALE_DAYS"])

failures = []
unknown_agent_warnings = []
checks = 0


def check(desc, ok, detail=""):
    global checks
    checks += 1
    print(f"  [{'OK ' if ok else 'DRIFT'}] {desc}" + (f" — {detail}" if not ok else ""))
    if not ok:
        failures.append(f"{desc} — {detail}")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def find_next_top_level_key(text, start):
    """Index of the next top-level (column-0, non-comment) `key:` line after `start`,
    or len(text) if none. Robust to comment/blank-line growth inside the preceding
    block — a fixed-offset window or a literal-next-key anchor silently mis-slices
    when the block grows or a row is renamed (carried over from the source guard's
    re-harden fixes #3/#4)."""
    for m in re.finditer(r"^(\S)", text[start:], re.M):
        idx = start + m.start()
        nl = text.find("\n", idx)
        line = text[idx: nl if nl != -1 else len(text)]
        if line.startswith("#"):
            continue
        return idx
    return len(text)


ssot_text = read(SSOT)

# ---- version: must be present AND semver-shaped (MAJOR.MINOR.PATCH) -------
# This SSOT ships MIGRATED to semver (docs/VERSIONING.md) — the source deployment's
# bare-integer `^version:\s*(\d+)$` shape is REJECTED here, not just widened, so a
# regressed integer stamp fails loudly instead of silently re-validating.
m = re.search(r'^version:\s*"?(\d+\.\d+\.\d+)"?\s*$', ssot_text, re.M)
bare_int = re.search(r"^version:\s*(\d+)\s*$", ssot_text, re.M)
if not m and bare_int:
    print(f"FATAL: SSOT `version:` is a bare integer ({bare_int.group(1)!r}) — this repo "
          f"migrated to semver (docs/VERSIONING.md); a bare-int stamp is the OLD pre-migration "
          f"shape and must be fixed, not re-widened for.", file=sys.stderr)
    sys.exit(2)
if not m:
    print("FATAL: SSOT has no top-level `version:` (expected semver MAJOR.MINOR.PATCH)", file=sys.stderr)
    sys.exit(2)
SSOT_VERSION = m.group(1)
check("SSOT version is present and semver-shaped", True, f"version={SSOT_VERSION}")

# ---- active_provider: must name a real providers.<name> key ----------------
apm = re.search(r"^active_provider:\s*(\S+)\s*(?:#.*)?$", ssot_text, re.M)
if not apm:
    print("FATAL: SSOT has no top-level `active_provider:`", file=sys.stderr)
    sys.exit(2)
ACTIVE_PROVIDER = apm.group(1)

# ---- providers: block -------------------------------------------------------
p_start = ssot_text.find("\nproviders:\n")
if p_start == -1:
    print("FATAL: SSOT has no `providers:` block", file=sys.stderr)
    sys.exit(2)
p_end = ssot_text.find("\nprices:\n", p_start)
if p_end == -1:
    p_end = len(ssot_text)
providers_block = ssot_text[p_start:p_end]

# provider names are 2-space-indented top-level keys under `providers:`
provider_names = re.findall(r"^  ([\w-]+):\s*$", providers_block, re.M)
if not provider_names:
    print("FATAL: parsed zero provider names from SSOT `providers:` block — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)

check(f"active_provider '{ACTIVE_PROVIDER}' is a valid providers: key", ACTIVE_PROVIDER in provider_names,
      f"known providers={sorted(provider_names)}")

# Slice each provider's own sub-block (from its header to the next same-indent header,
# or end of providers: block).
provider_blocks = {}
starts = [(pn, m.start()) for pn in provider_names for m in [re.search(rf"^  {re.escape(pn)}:\s*$", providers_block, re.M)]]
starts.sort(key=lambda t: t[1])
for i, (pn, s) in enumerate(starts):
    e = starts[i + 1][1] if i + 1 < len(starts) else len(providers_block)
    provider_blocks[pn] = providers_block[s:e]

# ---- per-provider models: tier->model-id + effort.map + calibration.date --
provider_tiers = {}       # name -> set(tier)
provider_models = {}      # name -> {tier: model_id}
provider_effort_map = {}  # name -> {tier: {intent: native_level_or_None}}
provider_calib_date = {}  # name -> ISO date str or None
provider_status = {}      # name -> calibration.status


def _slice_same_indent_block(block, key):
    """Slice `block` from `key:` up to (not including) the next line at the SAME
    indent as `key:` itself. Provider sub-blocks are indented (4 spaces), so the
    column-0-anchored find_next_top_level_key doesn't apply here directly."""
    key_start = block.find(key + ":")
    if key_start == -1:
        return None
    line_start = block.rfind("\n", 0, key_start) + 1
    indent = key_start - line_start
    nxt = re.search(rf"^\s{{0,{indent}}}\S", block[key_start + len(key) + 1:], re.M)
    end = key_start + len(key) + 1 + (nxt.start() if nxt else len(block) - key_start - len(key) - 1)
    return block[key_start:end]


for pn, block in provider_blocks.items():
    models_slice = _slice_same_indent_block(block, "models")
    if models_slice is None:
        check(f"provider '{pn}' has a models: block", False, "no `models:` key found")
        provider_tiers[pn] = set()
        provider_models[pn] = {}
    else:
        provider_models[pn] = dict(re.findall(r"^\s+([\w-]+):\s*(\S+)", models_slice, re.M))
        provider_tiers[pn] = set(provider_models[pn])

    effort_slice = _slice_same_indent_block(block, "effort") or ""
    map_slice = _slice_same_indent_block(effort_slice, "map") or ""
    tier_effort_map = {}
    for tier_name in provider_tiers[pn]:
        row = re.search(rf"^\s*{re.escape(tier_name)}:\s*\{{([^}}]*)\}}", map_slice, re.M)
        if row:
            pairs = dict(re.findall(r"(\w+):\s*(null|\w+)", row.group(1)))
            tier_effort_map[tier_name] = {k: (None if v == "null" else v) for k, v in pairs.items()}
    provider_effort_map[pn] = tier_effort_map

    dm = re.search(r"calibration:.*?date:\s*(\S+)", block, re.S)
    provider_calib_date[pn] = dm.group(1) if dm else None
    sm = re.search(r"calibration:.*?status:\s*(\w+)", block, re.S)
    provider_status[pn] = sm.group(1) if sm else None

# ---- task_classes: block ----------------------------------------------------
tc_start = ssot_text.find("\ntask_classes:\n")
if tc_start == -1:
    print("FATAL: SSOT has no `task_classes:` block", file=sys.stderr)
    sys.exit(2)
tc_end = find_next_top_level_key(ssot_text, tc_start + len("\ntask_classes:\n"))
tc_block = ssot_text[tc_start:tc_end]
# GUARD PARSE CONTRACT: rows are `{ tier: <tier>, effort: <intent> }` — NOT `model:`.
tc_rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+),\s*effort:\s*(\w+)\s*\}", re.M)
task_classes = {mo.group(1): (mo.group(2), mo.group(3)) for mo in tc_rx.finditer(tc_block)}
if not task_classes:
    print("FATAL: parsed zero task_classes rows (expected `{ tier:, effort: }` shape per "
          "the GUARD PARSE CONTRACT header in model-routing.yaml) — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)

all_tiers_used = {tier for tier, _effort in task_classes.values()}

# ---- ADDED: every task_class tier resolves in EVERY provider's models map -----
print("-- tier <-> provider-map completeness (every task_class tier, every provider) --")
for pn in sorted(provider_tiers):
    missing = all_tiers_used - provider_tiers[pn]
    check(f"provider '{pn}' models: covers every task_classes tier", not missing,
          f"missing tier(s) {sorted(missing)} — providers.{pn}.models needs these keys before this "
          f"provider could ever become active_provider")

# ---- ADDED: as_of / calibration staleness WARN (never fails the build) ------
print("-- calibration staleness (WARN-only; never fails the build) --")
today = dt.date.today()
for pn in sorted(provider_calib_date):
    raw = provider_calib_date[pn]
    if raw in (None, "null"):
        if provider_status.get(pn) == "researched":
            print(f"  WARN: provider '{pn}' calibration.status=researched but calibration.date is null/missing")
        else:
            print(f"  [OK ] provider '{pn}' calibration.date absent (status={provider_status.get(pn)!r}, expected for an unresearched profile)")
        continue
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        print(f"  WARN: provider '{pn}' calibration.date {raw!r} is not ISO-8601 — cannot check staleness")
        continue
    age = (today - d).days
    if age > STALE_DAYS:
        print(f"  WARN: provider '{pn}' calibration.date={raw} is {age}d old (> {STALE_DAYS}d threshold) — "
              f"re-verify model ids/pricing/effort map before trusting this profile (see docs/VERSIONING.md)")
    else:
        print(f"  [OK ] provider '{pn}' calibration.date={raw} ({age}d old, within {STALE_DAYS}d threshold)")

# ---- ADDED: active_provider must not be `unresearched` ----------------------
if ACTIVE_PROVIDER in provider_status:
    check(f"active_provider '{ACTIVE_PROVIDER}' calibration.status != unresearched",
          provider_status[ACTIVE_PROVIDER] != "unresearched",
          f"status={provider_status[ACTIVE_PROVIDER]!r} — never dispatch against an unresearched active profile "
          f"(ARCHITECTURE.md §3)")

# ============================================================================
# (a) + (b): agent frontmatter vs SSOT, no-effort-dial invariant (generalized)
# ============================================================================
# `agents:` in this SSOT is an ILLUSTRATIVE SNAPSHOT (see the block's own header
# comment) using the same {tier:, effort:} shape as task_classes. `effort: unset`
# means "this tier's active-provider effort map is all-null (rejects the dial) —
# expect NO effort: key on disk", generalizing the source guard's haiku-only check.
a_start = ssot_text.find("\nagents:\n")
a_end = ssot_text.find("\nconsumers:\n", a_start if a_start != -1 else 0)
if a_start == -1 or a_end == -1:
    print("FATAL: could not locate `agents:` .. `consumers:` block in SSOT", file=sys.stderr)
    sys.exit(2)
agents_block = ssot_text[a_start:a_end]
agent_rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+),\s*effort:\s*(\w+)\s*\}", re.M)
ssot_agents = {mo.group(1): (mo.group(2), mo.group(3)) for mo in agent_rx.finditer(agents_block)}
if not ssot_agents:
    print("FATAL: parsed zero agent rows from SSOT `agents:` block — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)

# PROVIDER-AWARE RESOLUTION (the point of GUARD-01): an agent's on-disk frontmatter
# carries the ACTIVE PROVIDER's NATIVE model id + native effort level (e.g. Anthropic
# `model: sonnet` / `effort: medium`) — never the abstract tier/intent vocabulary.
# Resolve each SSOT agent row {tier, effort} through providers.<active_provider> the
# same way the (future, s09) resolver does before comparing to disk, instead of
# comparing the abstract token directly. `effort: unset` still means "this tier's
# active-provider effort map is all-null — expect NO effort: key on disk", the
# generalization of the source guard's haiku-only invariant to any all-null tier.
active_models = provider_models.get(ACTIVE_PROVIDER, {})
active_effort_map = provider_effort_map.get(ACTIVE_PROVIDER, {})


def resolve_native(tier, intent):
    """(model_id, native_effort_or_None, no_effort_dial: bool) for tier+intent under
    active_provider. `intent == "unset"` short-circuits to the no-effort-dial case."""
    model_id = active_models.get(tier)
    tier_map = active_effort_map.get(tier, {})
    no_dial = bool(tier_map) and all(v is None for v in tier_map.values())
    if intent == "unset" or no_dial:
        return model_id, None, True
    native = tier_map.get(intent, tier_map.get("standard"))  # falls back to `standard` per ARCHITECTURE.md §3
    return model_id, native, False


def agent_frontmatter(name):
    p = os.path.join(AGENTS_DIR, name + ".md")
    if not os.path.exists(p):
        return (None, None)
    t = read(p)
    parts = t.split("---", 2)
    block = parts[1] if len(parts) >= 3 else t
    mo = re.search(r"^model:\s*(\S+)", block, re.M)
    eo = re.search(r"^effort:\s*(\w+)", block, re.M)
    return (mo.group(1) if mo else None, eo.group(1) if eo else None)


print(f"-- (a)/(b) per-agent (resolved against active_provider='{ACTIVE_PROVIDER}') --")
if not os.path.isdir(AGENTS_DIR):
    print(f"  [SKIP] {AGENTS_DIR} not found — agent-frontmatter checks skipped "
          f"(expected for a repo checkout with no local agents/ directory; set AGENTS_DIR "
          f"to point at your deployment's agents/ to exercise this check)")
elif not any(os.path.exists(os.path.join(AGENTS_DIR, n + ".md")) for n in ssot_agents):
    print(f"  [SKIP] none of the SSOT `agents:` snapshot rows have a matching .md under {AGENTS_DIR} — "
          f"that block is an ILLUSTRATIVE SNAPSHOT (see its header comment); regenerate it from your "
          f"own agents/ before expecting this check to compare anything")
else:
    for name, (tier, effort) in sorted(ssot_agents.items()):
        agent_path = os.path.join(AGENTS_DIR, name + ".md")
        if not os.path.exists(agent_path):
            continue  # illustrative-snapshot row with no corresponding agent in THIS deployment — not drift
        disk_model, disk_effort = agent_frontmatter(name)
        want_model, want_effort, no_dial = resolve_native(tier, effort)

        if effort != "unset" and no_dial:
            check(f"SSOT invariant: '{name}' pinned to no-effort-dial tier '{tier}' but effort={effort!r}",
                  False, f"providers.{ACTIVE_PROVIDER}.effort.map.{tier} is all-null — expected effort: unset")

        check(f"agent '{name}' model (tier '{tier}' resolved for {ACTIVE_PROVIDER})",
              want_model is not None and disk_model == want_model,
              f"resolved={want_model!r} disk={disk_model!r}")
        if no_dial:
            check(f"agent '{name}' (no-effort-dial tier) carries no effort: key on disk",
                  disk_effort is None, f"found effort: {disk_effort!r} in {name}.md frontmatter")
        else:
            check(f"agent '{name}' effort (intent '{effort}' resolved for {ACTIVE_PROVIDER})",
                  disk_effort == want_effort, f"resolved={want_effort!r} disk={disk_effort!r}")

    # ---- unknown-agent scan (disk agents with no SSOT row) --------------------
    print("-- unknown-agent scan --")
    disk_agent_files = sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md")))
    for fp in disk_agent_files:
        name = os.path.splitext(os.path.basename(fp))[0]
        if name not in ssot_agents:
            msg = f"unknown agent '{name}' (no SSOT row) — {fp}"
            unknown_agent_warnings.append(msg)
            print(f"  [{'FAIL(strict)' if STRICT else 'WARN'}] {msg}")

print()
print(f"{checks} structured checks run; {len(unknown_agent_warnings)} unknown-agent warning(s).")

if STRICT and unknown_agent_warnings:
    failures.extend(f"[--strict] {w}" for w in unknown_agent_warnings)

if failures:
    print(f"\nFAIL ({len(failures)} issue(s)):", file=sys.stderr)
    for f in failures:
        print("  - " + f, file=sys.stderr)
    sys.exit(1)

print("PASS: (a)/(b)/(tier<->provider)/(version)/(active_provider) clean.")
sys.exit(0)
PY
PY_RC=$?
set -e
[[ $PY_RC -eq 0 ]] || _note_fail "$PY_RC"
echo

# ---- (f) resolve_route.py exists, imports, and resolves every task_class --
# ENFORCES the SSOT's own `consumers:` table stamp: resolve_route.py is
# marked `code-authoritative` there, but until this check existed nothing
# actually verified that stamp — a consumer could rot silently. Runs in
# BOTH --core and --full (it's a cheap structural check, no CLAUDE_HOME
# needed): import the vendored resolver directly (never re-implement its
# parsing here) and baseline-resolve() every task_classes row for
# active_provider, for EVERY declared provider (not just active_provider) —
# mirrors the tier<->provider-map completeness check above: a provider you
# might swap TO must already resolve cleanly, not just the one you're on.
echo "== (f) resolve_route.py exists/imports/resolves every task_class per provider =="
set +e
SSOT="$SSOT" REPO_DIR="$REPO_DIR" python3 - <<'PY'
import os, sys

SSOT = os.environ["SSOT"]
REPO_DIR = os.environ["REPO_DIR"]
RESOLVER_PATH = os.path.join(REPO_DIR, "claude", "scripts", "resolve_route.py")

if not os.path.isfile(RESOLVER_PATH):
    print(f"  [DRIFT] resolve_route.py missing at {RESOLVER_PATH} — SSOT `consumers:` "
          f"stamps it code-authoritative; the file must exist", file=sys.stderr)
    sys.exit(1)

import importlib.util
spec = importlib.util.spec_from_file_location("resolve_route", RESOLVER_PATH)
if spec is None or spec.loader is None:
    print(f"  [DRIFT] could not build an import spec for {RESOLVER_PATH}", file=sys.stderr)
    sys.exit(1)
try:
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"  [DRIFT] resolve_route.py failed to import: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
print("  [OK ] resolve_route.py imports cleanly")

if not hasattr(mod, "resolve"):
    print("  [DRIFT] resolve_route.py has no `resolve()` entry point", file=sys.stderr)
    sys.exit(1)

# Re-parse task_classes + provider names the same lightweight way the rest of
# this script already does, rather than importing this script's own parsing —
# resolve_route.py's _parse_task_classes/_parse_provider_block are private
# (underscore-prefixed); calling the public resolve() end-to-end is the
# contract this check exists to enforce, not a reason to reach into internals.
import re
with open(SSOT, encoding="utf-8") as f:
    ssot_text = f.read()
tc_rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+),\s*effort:\s*(\w+)\s*\}", re.M)
tc_start = ssot_text.find("\ntask_classes:\n")
tc_end = ssot_text.find("\nmain_session:\n", tc_start)
task_classes = [mo.group(1) for mo in tc_rx.finditer(ssot_text[tc_start:tc_end if tc_end != -1 else len(ssot_text)])]
if not task_classes:
    print("  [DRIFT] parsed zero task_classes names for the resolver check", file=sys.stderr)
    sys.exit(1)

p_start = ssot_text.find("\nproviders:\n")
p_end = ssot_text.find("\nprices:\n", p_start)
providers_block = ssot_text[p_start: p_end if p_end != -1 else len(ssot_text)]
provider_names = re.findall(r"^  ([\w-]+):\s*$", providers_block, re.M)
if not provider_names:
    print("  [DRIFT] parsed zero provider names for the resolver check", file=sys.stderr)
    sys.exit(1)

failed = False
for provider in sorted(provider_names):
    for tc in sorted(task_classes):
        try:
            result = mod.resolve(task_class=tc, active_provider=provider, current=None, signal="none", ssot_path=SSOT)
        except Exception as e:
            print(f"  [DRIFT] resolve('{tc}', '{provider}') raised {type(e).__name__}: {e}", file=sys.stderr)
            failed = True
            continue
        if result == "exhausted" or not isinstance(result, dict) or not result.get("model_id"):
            print(f"  [DRIFT] resolve('{tc}', '{provider}') returned {result!r} — baseline resolve must "
                  f"always produce a real model_id", file=sys.stderr)
            failed = True
    print(f"  [OK ] provider '{provider}': every task_class resolves via resolve_route.resolve()")

sys.exit(1 if failed else 0)
PY
RESOLVER_RC=$?
set -e
[[ $RESOLVER_RC -eq 0 ]] || _note_fail "$RESOLVER_RC"
echo

# ---- (e) CLAUDE.md digest re-render diff clean — --full mode only ---------
if [[ "$MODE" == "full" ]]; then
  echo "== (e) CLAUDE.md digest re-render diff (ssot=$SSOT) =="
  set +e
  python3 "$DIGEST_RENDERER" --claude-home "$CLAUDE_HOME" --repo-dir "$REPO_DIR" --yaml "$SSOT" --check
  DIGEST_RC=$?
  set -e
  if [[ $DIGEST_RC -ne 0 ]]; then
    echo "  FAIL: digest re-render check exited $DIGEST_RC (see render-routing-digest.py output above)" >&2
    _note_fail "$DIGEST_RC"
  else
    echo "  OK: digest matches SSOT re-render"
  fi
  echo
fi

if [[ $OVERALL_FAIL -ne 0 ]]; then
  echo "==================================================================="
  echo "FAIL: verify-routing.sh detected drift or a tooling failure (mode=$MODE, strict=$STRICT)." >&2
  echo "Fix the on-disk source (or the SSOT if it is wrong) — do not loosen this guard." >&2
  echo "===================================================================" >&2
  exit "$OVERALL_CODE"
fi

echo "==================================================================="
echo "PASS: verify-routing.sh clean (mode=$MODE, strict=$STRICT)."
echo "==================================================================="
exit 0
