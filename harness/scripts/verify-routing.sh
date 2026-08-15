#!/usr/bin/env bash
# verify-routing.sh — generalized drift guard for ~/.claude/model-routing.yaml (the
# model/effort/degradation SSOT). Modeled on verify-assignments.sh's style and extended
# to cover every consumer surface: agent pins, the haiku-has-no-effort invariant,
# prose-consumer version stamps, the run.py degradation/reasoning lockstep, the CLAUDE.md
# digest render, and (as a sub-check) verify-assignments.sh itself.
#
# POSIX/python3-only (no PyYAML — regex + `ast` parsing), CLAUDE_DIR env override, loud
# actionable failures. Fails CLOSED on both a POLICY VIOLATION (drift found) and a
# TOOLING CRASH (missing dependency, malformed SSOT, python3 traceback) — a broken guard
# must never silently become a bypass. See ~/.claude/githooks/pre-commit (wired by
# scripts/install-hooks.sh via core.hooksPath) for the
# ROUTING_GUARD_ALLOW_FIX / ROUTING_GUARD_ALLOW_REVERT recovery escapes (never --no-verify).
#
# Modes:
#   --core   checks (a)+(b)+(d)+(e)+(f)+(g)  — everything except prose-consumer stamps (c),
#            which don't exist until s04 lands them. Green from s02.
#   --full   adds (c) prose-consumer stamp check. Red until s04 publishes the stamps —
#            that is EXPECTED today, not a bug.
#   (bare)   uses DEFAULT_MODE below. s02 sets this to "core"; s04 flips the literal to
#            "full" as its ONE-LINE edit point — the pre-commit hook always invokes bare
#            mode, so that single edit silently upgrades every future commit to full
#            enforcement with zero hook changes.
#   --strict turns "unknown agent (no SSOT row)" from a WARN into a FAILURE. Orthogonal
#            to --core/--full. Drift on a KNOWN agent (SSOT row exists, frontmatter
#            disagrees) ALWAYS fails, strict or not.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SSOT="${SSOT:-$CLAUDE_DIR/model-routing.yaml}"
AGENTS_DIR="$CLAUDE_DIR/agents"
DIGEST_RENDERER="$CLAUDE_DIR/scripts/render-routing-digest.py"
VERIFY_ASSIGNMENTS="$CLAUDE_DIR/scripts/verify-assignments.sh"
RUN_PY="$CLAUDE_DIR/skills/plan-execute/scripts/run.py"
DIGEST_VARIANT="${ROUTING_DIGEST_VARIANT:-full}"   # s04: flipped v0->full — the full advisory digest is now published in CLAUDE.md (every in-scope stamp landed; full-guard-green confirmed first)

DEFAULT_MODE="full"   # s04: flipped core->full — every in-scope stamp now exists; the bootstrap window closes here.
MODE="$DEFAULT_MODE"
STRICT=0

for arg in "$@"; do
  case "$arg" in
    --core) MODE="core" ;;
    --full) MODE="full" ;;
    --strict) STRICT=1 ;;
    -h|--help)
      echo "usage: verify-routing.sh [--core|--full] [--strict]"
      exit 0
      ;;
    *)
      echo "FATAL: unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

# s04: unknown-agent enforcement closes here. Full mode (and therefore the DEFAULT/
# bare mode, hence the pre-commit path, once DEFAULT_MODE="full" above) now treats an
# agent with no SSOT row as a FAILURE, not a WARN — WARN-by-default was the s02-s03
# BOOTSTRAPPING affordance only, while agent portfolio audits were still landing.
# --core keeps the old opt-in --strict behavior (still useful for a fixture-scoped
# probe that isn't ready to fail-closed on unknown agents yet).
if [[ "$MODE" == "full" ]]; then
  STRICT=1
fi

for p in "$SSOT" "$AGENTS_DIR" "$DIGEST_RENDERER" "$VERIFY_ASSIGNMENTS" "$RUN_PY"; do
  [[ -e "$p" ]] || { echo "FATAL: missing required path: $p" >&2; exit 2; }
done

echo "verify-routing.sh — mode=$MODE strict=$STRICT claude_dir=$CLAUDE_DIR ssot=$SSOT"
echo

# ---- (g) runtime bypass visibility — report BEFORE anything else --------------------
echo "== (g) active runtime overrides =="
if [[ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]]; then
  echo "  WARNING: CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_CODE_SUBAGENT_MODEL} is set — dispatched agents route HERE regardless of frontmatter/SSOT pins."
else
  echo "  CLAUDE_CODE_SUBAGENT_MODEL: not set"
fi
if [[ -n "${CLAUDE_CODE_EFFORT_LEVEL:-}" ]]; then
  echo "  WARNING: CLAUDE_CODE_EFFORT_LEVEL=${CLAUDE_CODE_EFFORT_LEVEL} is set — dispatched effort routes HERE regardless of frontmatter/SSOT pins."
else
  echo "  CLAUDE_CODE_EFFORT_LEVEL: not set"
fi
if [[ -n "${ROUTING_DIGEST_VARIANT:-}" ]]; then
  echo "  WARNING: ROUTING_DIGEST_VARIANT=${ROUTING_DIGEST_VARIANT} is set — check (e) renders/validates the '$DIGEST_VARIANT' variant instead of the implicit default; an unexpected override can overflow the digest budget and lock ALL commits until unset (s02 re-harden fix #12)."
else
  # s04: message now reflects the ACTUAL default ($DIGEST_VARIANT), not a hardcoded
  # "v0" literal — that literal went stale the moment s04 flipped the implicit
  # default to "full" and would have misreported every commit's diagnostic banner.
  echo "  ROUTING_DIGEST_VARIANT: not set (using default $DIGEST_VARIANT)"
fi
echo "  (files can be green while the runtime routes elsewhere — this is informational, not a pass/fail gate)"
echo

OVERALL_FAIL=0

# ---- (a)(b)(d) + unknown-agent + epic composed-cross-check (python3, ast + regex) ----
echo "== (a) agent frontmatter vs SSOT · (b) haiku-has-no-effort · (d) run.py lockstep · unknown-agent scan =="
set +e
CLAUDE_DIR="$CLAUDE_DIR" SSOT="$SSOT" AGENTS_DIR="$AGENTS_DIR" RUN_PY="$RUN_PY" MODE="$MODE" STRICT="$STRICT" \
python3 - <<'PY'
import ast, glob, os, re, subprocess, sys

CLAUDE_DIR = os.environ["CLAUDE_DIR"]
SSOT = os.environ["SSOT"]
AGENTS_DIR = os.environ["AGENTS_DIR"]
RUN_PY = os.environ["RUN_PY"]
MODE = os.environ["MODE"]
STRICT = os.environ["STRICT"] == "1"

failures = []
unknown_agent_warnings = []
frontmatter_warnings = []   # strict-YAML hygiene; warn-only (see the scan for why)
epic_crosscheck_warnings = []


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def find_next_top_level_key(text, start):
    """Index of the next top-level (column-0, non-comment) `key:` line after `start`,
    or len(text) if none. Used instead of a fixed-offset window or a literal-next-key
    anchor (both of which silently mis-slice when the preceding block grows a comment
    or a row is renamed/removed — s02 re-harden fixes #3/#4)."""
    for m in re.finditer(r"^(\S)", text[start:], re.M):
        idx = start + m.start()
        nl = text.find("\n", idx)
        line = text[idx: nl if nl != -1 else len(text)]
        if line.startswith("#"):
            continue
        return idx
    return len(text)


ssot_text = read(SSOT)

# ---- version -------------------------------------------------------------
m = re.search(r"^version:\s*(\d+)\s*$", ssot_text, re.M)
if not m:
    print("FATAL: SSOT has no top-level `version:`", file=sys.stderr)
    sys.exit(2)
SSOT_VERSION = int(m.group(1))

# ---- agents: block (scoped — task_classes: uses the same {model:,effort:} shape) ----
a_start = ssot_text.find("\nagents:\n")
a_end = ssot_text.find("\nconsumers:\n", a_start if a_start != -1 else 0)
if a_start == -1 or a_end == -1:
    print("FATAL: could not locate `agents:` .. `consumers:` block in SSOT", file=sys.stderr)
    sys.exit(2)
agents_block = ssot_text[a_start:a_end]

agent_rx = re.compile(r"^\s*([\w-]+):\s*\{\s*model:\s*(\w+),\s*effort:\s*(\w+)\s*\}", re.M)
ssot_agents = {}  # name -> (model, effort)  effort == "unset" means "no effort: key expected on disk"
for mo in agent_rx.finditer(agents_block):
    ssot_agents[mo.group(1)] = (mo.group(2), mo.group(3))

if not ssot_agents:
    print("FATAL: parsed zero agent rows from SSOT `agents:` block — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)

# ---- consumers: block (path -> stamp type) -------------------------------
c_start = ssot_text.find("\nconsumers:\n")
consumers_block = ssot_text[c_start:] if c_start != -1 else ""
consumer_rx = re.compile(r"^\s*(\S+):\s*\{\s*stamp:\s*([\w-]+)\s*\}", re.M)
consumers = {}
for mo in consumer_rx.finditer(consumers_block):
    consumers[mo.group(1)] = mo.group(2)


def resolve_consumer_path(raw):
    if raw.startswith("~/.claude"):
        return raw.replace("~/.claude", CLAUDE_DIR, 1)
    return os.path.expanduser(raw)


# ---- providers.<name>.degrade — the ONLY machine-readable degradation source ----
# (s04, DSP-02.) run.py's _FALLBACK_LADDER/_DEGRADE_EFFORT are PROVIDER-SCOPED and
# model-keyed; the SSOT's degrade blocks are tier-keyed. Parse every provider's
# `models:` (tier→model) + `degrade:` (`ladder:`/`effort_on_degrade:` single-line
# flow maps) and translate tiers→models for the check-(d) comparison. The legacy
# flat `degradation:` block is prose-only pending its s03 retirement — while it
# still exists we cross-check it against the translated anthropic walk below.
# {tier, effort-INTENT} row shape — used for BOTH the neutral top-level
# `task_classes:` block (below) and the v1.13 optional per-provider override inside
# each provider profile (parsed in the loop that follows). ONE regex on purpose: two
# copies of this grammar is exactly how an override silently stops being parsed.
tc_row_rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+)\s*,\s*effort:\s*(\w+)\s*\}", re.M)

prov_start = ssot_text.find("\nproviders:\n")
if prov_start == -1:
    print("FATAL: SSOT has no `providers:` block", file=sys.stderr)
    sys.exit(2)
prov_end = find_next_top_level_key(ssot_text, prov_start + len("\nproviders:\n"))
providers_ssot_block = ssot_text[prov_start:prov_end]
prov_hdr_rx = re.compile(r"^  ([\w-]+):\s*(?:#.*)?$", re.M)
prov_headers = list(prov_hdr_rx.finditer(providers_ssot_block))
if not prov_headers:
    print("FATAL: parsed zero provider profiles from SSOT `providers:` — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)
ssot_provider_models = {}    # provider -> {tier: model}
ssot_ladders = {}            # provider -> {model: model}   (tier-translated)
ssot_degrade_efforts = {}    # provider -> {model: effort}  (tier-translated)
ssot_provider_pbody = {}     # provider -> raw block text (s03: reused by GUARD-01 + reasoning-tier scan)
ssot_provider_effort_map = {}  # provider -> {tier: {intent: native_or_None}}
ssot_provider_calibration_status = {}  # provider -> calibration.status
ssot_provider_task_classes = {}   # provider -> {class: (tier, intent)} — v1.13 per-provider override, {} when absent
ssot_provider_ceiling = {}        # provider -> {tier: probed native ceiling} — v1.13, {} when the profile declines to declare one
ssot_provider_degrade_effort_tier = {}  # provider -> {tier: effort} (tier-keyed, unlike ssot_degrade_efforts)
ssot_provider_effort_ladder = {}  # provider -> {tier: [rung, ...]}
ssot_provider_ladder_entry = {}   # provider -> {tier: entry rung} — v1.15 escalation.model_ladder_entry, {} when absent
ssot_provider_ladder_tier = {}    # provider -> {tier: tier} degrade ladder, UNtranslated (ssot_ladders is model-keyed)
for i, hdr in enumerate(prov_headers):
    pname = hdr.group(1)
    pbody_end = prov_headers[i + 1].start() if i + 1 < len(prov_headers) else len(providers_ssot_block)
    pbody = providers_ssot_block[hdr.end():pbody_end]
    ssot_provider_pbody[pname] = pbody
    cal_m = re.search(r"^\s*status:\s*(\w+)", pbody, re.M)
    ssot_provider_calibration_status[pname] = cal_m.group(1) if cal_m else None
    mm = re.search(r"^    models:[^\n]*\n((?:      [^\n]*\n)+)", pbody, re.M)
    if not mm:
        print(f"FATAL: providers.{pname} has no parseable `models:` block", file=sys.stderr)
        sys.exit(2)
    models = dict(re.findall(r"^\s*([\w-]+):\s*([\w.\-]+)", mm.group(1), re.M))
    # effort.map — one `{ intent: level, ... }` flow-map row per tier, used by
    # GUARD-01 provider completeness (s03) below. TIER-FILTERED (s12): `effort:` now
    # also carries the sibling flow map `native_effort_ceiling:`, which has the same
    # `key: { ... }` shape and would otherwise register as a bogus tier.
    em_block = re.search(r"^    effort:[^\n]*\n((?:      [^\n]*\n)+)", pbody, re.M)
    tier_effort_map = {}
    ceiling = {}
    if em_block:
        for tmo in re.finditer(r"^\s*([\w-]+):\s*\{([^}]*)\}", em_block.group(1), re.M):
            key = tmo.group(1)
            cells = dict(re.findall(r"([\w-]+)\s*:\s*(null|[\w-]+)", tmo.group(2)))
            if key == "native_effort_ceiling":
                ceiling = cells
            elif key in models:
                tier_effort_map[key] = {k: (None if v == "null" else v) for k, v in cells.items()}
    ssot_provider_effort_map[pname] = tier_effort_map
    ssot_provider_ceiling[pname] = ceiling
    # v1.13 OPTIONAL per-provider task-class map (indent 4, same {tier, effort} row
    # shape as the neutral block). Absent => that provider uses the neutral rows.
    tcm = re.search(r"^    task_classes:[^\n]*\n((?:      [^\n]*\n)+)", pbody, re.M)
    ssot_provider_task_classes[pname] = (
        {mo.group(1): (mo.group(2), mo.group(3)) for mo in tc_row_rx.finditer(tcm.group(1))} if tcm else {}
    )
    if tcm and not ssot_provider_task_classes[pname]:
        print(f"FATAL: providers.{pname} declares `task_classes:` but zero {{tier, effort}} rows parsed "
              f"— an unparseable override would silently fall back to the neutral rows", file=sys.stderr)
        sys.exit(2)
    # escalation.effort_ladder — a rung emitted here is a real route, so the ceiling
    # check below has to see it too (not just effort.map / effort_on_degrade).
    elm = re.search(r"^      effort_ladder:[^\n]*\n((?:        [^\n]*\n)+)", pbody, re.M)
    ssot_provider_effort_ladder[pname] = (
        {mo.group(1): [x.strip() for x in mo.group(2).split(",") if x.strip()]
         for mo in re.finditer(r"^\s*([\w-]+):\s*\[([^\]]*)\]", elm.group(1), re.M)} if elm else {}
    )
    # escalation.model_ladder_entry (v1.15, OPTIONAL) — the effort rung the model
    # ladder ENTERS a tier on. Same argument as effort_ladder above: a rung named
    # here is a real dispatched route, so the ceiling check has to see it too.
    mle = re.search(r"^      model_ladder_entry:\s*\{([^}]*)\}", pbody, re.M)
    ssot_provider_ladder_entry[pname] = (
        dict(re.findall(r"([\w-]+)\s*:\s*([\w-]+)", mle.group(1))) if mle else {}
    )
    # Scope the map search to the `degrade:` sub-block itself (adversarial-review
    # 2026-07-10, Codex MEDIUM): searching the whole provider body would keep
    # finding the child maps even with the `degrade:` header typo'd away, letting
    # check (d) report green on an SSOT the resolver would reject.
    dm = re.search(r"^    degrade:[^\n]*\n((?:(?: {5,}[^\n]*)?\n)*)", pbody, re.M)
    if not dm:
        print(f"FATAL: providers.{pname} has no `degrade:` block", file=sys.stderr)
        sys.exit(2)
    dbody = dm.group(1)
    lm = re.search(r"^\s*ladder:\s*\{([^}]*)\}", dbody, re.M)
    em = re.search(r"^\s*effort_on_degrade:\s*\{([^}]*)\}", dbody, re.M)
    if not lm or not em:
        print(f"FATAL: providers.{pname}.degrade missing `ladder:`/`effort_on_degrade:` flow map", file=sys.stderr)
        sys.exit(2)
    tier_ladder = dict(re.findall(r"([\w-]+)\s*:\s*([\w-]+)", lm.group(1)))
    tier_eff = dict(re.findall(r"([\w-]+)\s*:\s*([\w-]+)", em.group(1)))
    missing = [t for t in set(tier_ladder) | set(tier_ladder.values()) | set(tier_eff) if t not in models]
    if missing:
        print(f"FATAL: providers.{pname}.degrade names tier(s) {missing} not in its models: block", file=sys.stderr)
        sys.exit(2)
    ssot_provider_models[pname] = models
    ssot_ladders[pname] = {models[k]: models[v] for k, v in tier_ladder.items()}
    ssot_degrade_efforts[pname] = {models[k]: v for k, v in tier_eff.items()}
    ssot_provider_degrade_effort_tier[pname] = tier_eff
    ssot_provider_ladder_tier[pname] = tier_ladder

# Reasoning-tier lockstep only concerns the ANTHROPIC dial (Claude thinking
# directives); other providers' native efforts (e.g. openai `max`) never map to
# a _REASONING_DIRECTIVE key.
ssot_degrade_effort = ssot_degrade_efforts.get("anthropic", {})

# ---- active_provider: — the single execution-family dial ------------------
am = re.search(r"^active_provider:\s*(\S+)\s*$", ssot_text, re.M)
if not am:
    print("FATAL: SSOT has no top-level `active_provider:`", file=sys.stderr)
    sys.exit(2)
ACTIVE_PROVIDER = am.group(1)
if ACTIVE_PROVIDER not in ssot_provider_models:
    print(f"FATAL: active_provider={ACTIVE_PROVIDER!r} has no providers.{ACTIVE_PROVIDER} profile", file=sys.stderr)
    sys.exit(2)

# ---- task_classes: {tier, effort-intent} rows (s03: renamed from task_classes_v2,
# legacy flat {model,effort} block retired). Slice to the NEXT top-level key rather
# than anchoring on the literal `linchpin:` row (s02 re-harden fix #4) — a rename/
# removal of that row previously returned -1 and blind-sliced 2000 bytes, swallowing
# unrelated blocks (main_session/escalation/etc).
tc_start = ssot_text.find("\ntask_classes:\n")
if tc_start == -1:
    print("FATAL: SSOT has no `task_classes:` block", file=sys.stderr)
    sys.exit(2)
tc_end = find_next_top_level_key(ssot_text, tc_start + len("\ntask_classes:\n"))
tc_block = ssot_text[tc_start:tc_end]
ssot_task_classes = {mo.group(1): (mo.group(2), mo.group(3)) for mo in tc_row_rx.finditer(tc_block)}
if not ssot_task_classes:
    print("FATAL: parsed zero {tier, effort} rows from SSOT `task_classes:` block — parser or SSOT is broken", file=sys.stderr)
    sys.exit(2)

# tier_tokens: CONCRETE Claude reasoning-dial values that run.py's
# _REASONING_DIRECTIVE must cover. NOTE (s03): task_classes rows now carry
# ABSTRACT INTENT tokens (light/standard/thorough), not dial values — scraping
# `effort:` out of task_classes (the pre-s03 approach) would feed the wrong
# vocabulary into this check and false-fail on every commit. Concrete tokens
# come from: providers.anthropic's effort.map cells (intent->native, already
# resolved), its escalation effort ladders, agent frontmatter efforts, and the
# degrade effort_on_degrade values.
_CONCRETE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
tier_tokens = set()
_anthropic_pbody = ssot_provider_pbody.get("anthropic", "")
tier_tokens.update(w for w in re.findall(r"\b(\w+)\b", _anthropic_pbody) if w in _CONCRETE_EFFORTS)
tier_tokens.update(ssot_degrade_effort.values())
for _model, effort in ssot_agents.values():
    if effort != "unset":
        tier_tokens.add(effort)

# ============================================================================
# (a) + (b): agent frontmatter vs SSOT, haiku-has-no-effort invariant
# ============================================================================


def agent_frontmatter(name):
    p = os.path.join(AGENTS_DIR, name + ".md")
    if not os.path.exists(p):
        return (None, None)
    t = read(p)
    parts = t.split("---", 2)
    block = parts[1] if len(parts) >= 3 else t
    mo = re.search(r"^model:\s*(\w+)", block, re.M)
    eo = re.search(r"^effort:\s*(\w+)", block, re.M)
    return (mo.group(1) if mo else None, eo.group(1) if eo else None)


# ---- frontmatter VALIDITY (added 2026-07-26) -------------------------------
# agent_frontmatter() above greps `model:`/`effort:` line-wise. That is fine for drift
# detection but BLIND to whether the block is loadable YAML at all: a description
# containing an unquoted ": " (e.g. `description: dispatch tier: opus at high effort`)
# invalidates the entire frontmatter, so Claude Code silently does not register the
# agent — while every grepped line is still present and this guard stays green. That
# happened to the tier-* agents on their first draft. A green gate over an artifact the
# runtime cannot load is the exact defect class this repo keeps re-finding, so validity
# is now checked for EVERY agent file, not only the ones with SSOT rows.
def frontmatter_error(path):
    """Return a human-readable reason the frontmatter is unloadable, or None if OK."""
    t = read(path)
    if not t.startswith("---"):
        return "no leading '---' frontmatter block"
    parts = t.split("---", 2)
    if len(parts) < 3:
        return "frontmatter block is not terminated by a second '---'"
    block = parts[1]
    try:
        import yaml
    except ImportError:
        # No PyYAML on this box: detect the specific failure mode rather than skip
        # the check entirely — a skipped check is a green lie.
        for raw in block.strip().splitlines():
            line = raw.rstrip()
            if not line or line.lstrip().startswith(("#", "-")) or ":" not in line:
                continue
            key, _, val = line.partition(":")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                continue
            if ": " in val:
                return (f"unquoted value for {key.strip()!r} contains ': ' "
                        "(invalid YAML — quote it or use an em dash)")
        return None
    try:
        d = yaml.safe_load(block)
    except Exception as e:
        return f"YAML parse error: {str(e).splitlines()[0]}"
    if not isinstance(d, dict):
        return f"frontmatter parsed to {type(d).__name__}, not a mapping"
    for required in ("name", "description"):
        if not d.get(required):
            return f"missing or empty required key {required!r}"
    if d.get("name") != os.path.splitext(os.path.basename(path))[0]:
        return f"frontmatter name {d.get('name')!r} != filename"
    return None


checks = 0


def check(desc, ok, detail):
    global checks
    checks += 1
    print(f"  [{'OK ' if ok else 'DRIFT'}] {desc}" + (f" — {detail}" if not ok else ""))
    if not ok:
        failures.append(f"{desc} — {detail}")


# ============================================================================
# GUARD-01 (s03, ported from Gearbox) — provider completeness: every
# task_classes tier must resolve under providers[active_provider].models, and
# every effort-intent it uses must be a key in that tier's effort.map cell.
# Also refuses to activate a provider whose calibration is only lane_scoped
# (transcribed facts, never calibrated as an execution default — v1.8
# adversarial-review finding on the openai profile).
# ============================================================================
print(f"-- GUARD-01 provider completeness (active_provider={ACTIVE_PROVIDER!r}) --")
_active_models = ssot_provider_models.get(ACTIVE_PROVIDER, {})
_active_effort_map = ssot_provider_effort_map.get(ACTIVE_PROVIDER, {})
_active_calibration = ssot_provider_calibration_status.get(ACTIVE_PROVIDER)
check(f"providers.{ACTIVE_PROVIDER}.calibration.status is not lane_scoped",
      _active_calibration != "lane_scoped",
      f"status={_active_calibration!r} — lane_scoped facts are transcribed, not calibrated as an execution default; run /routing-update before activating this provider")
# s12: iterate the EFFECTIVE map — the neutral rows with the active provider's own
# v1.13 `task_classes:` override layered on, exactly as resolve_route._load does.
# A provider that declares no override (providers.anthropic) yields the neutral rows
# unchanged, so this loop's output is byte-identical to the pre-v1.13 guard.
_effective_task_classes = {**ssot_task_classes, **ssot_provider_task_classes.get(ACTIVE_PROVIDER, {})}
for tc_name, (tier, intent) in sorted(_effective_task_classes.items()):
    check(f"task_classes.{tc_name} tier '{tier}' resolves under providers.{ACTIVE_PROVIDER}.models",
          tier in _active_models, f"providers.{ACTIVE_PROVIDER}.models has no '{tier}' entry")
    tier_map = _active_effort_map.get(tier, {})
    check(f"task_classes.{tc_name} intent '{intent}' present in providers.{ACTIVE_PROVIDER}.effort.map.{tier}",
          intent in tier_map, f"providers.{ACTIVE_PROVIDER}.effort.map.{tier} has no '{intent}' key (has {sorted(tier_map)})")
print()

# ============================================================================
# GUARD-02 (s12, v1.13) — PER-PROVIDER TASK-CLASS MAPS + PROBED EFFORT CEILINGS.
# Runs over EVERY declared provider, not just the active one: an inactive profile's
# map is what the operator reads when deciding whether to flip the dial, so a wrong
# row there must fail NOW, not at flip time. GUARD-01 above deliberately inspects the
# active provider only, which is why these checks live separately rather than being
# folded into it.
#   (1) a per-provider row may only re-point a class the NEUTRAL block declares —
#       the class vocabulary is provider-neutral. A name absent there would look
#       encoded and silently resolve through the neutral row forever.
#   (2) its tier must exist in that provider's models:, and its intent must be a key
#       in that tier's effort.map cell — the GUARD-01 contract, applied per provider.
#   (3) every NATIVE effort this profile can emit must be within the model's PROBED
#       ceiling (`effort.native_effort_ceiling`, optional). Emission sites are all
#       three: effort.map cells, degrade.effort_on_degrade landings, and
#       escalation.effort_ladder rungs. MEASURED FACT this exists for: gpt-5.5
#       returns API 400 "Invalid value: 'max'" (2026-07-28, codex-cli 0.145.0) while
#       the gpt-5.6 family accepts max — so `max` on workhorse is a dispatch-time
#       crash, and before v1.13 nothing checked for it.
# ============================================================================
print("-- GUARD-02 per-provider task-class maps + probed effort ceilings (v1.13) --")
_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4, "ultra": 5}
for pname in sorted(ssot_provider_models):
    _pmodels = ssot_provider_models[pname]
    _pmap = ssot_provider_effort_map.get(pname, {})
    # (0) NO TIER ALIASING (v1.15) — two tiers of ONE provider may never resolve to the
    # same model. Probed failure modes this forbids structurally rather than by
    # convention: this file flattens tier-keyed ladders into MODEL-keyed dicts (see
    # ssot_ladders / ssot_degrade_efforts above), so an alias yields a literal
    # {model: model} degrade SELF-LOOP that check (d) would then force run.py to
    # encode, AND collapses effort_on_degrade onto one key (last-wins, silently
    # discarding the other tier's calibrated effort). resolve_route.tier_of() is
    # additionally first-match, so every session on the aliased model reads as the
    # LOWER tier and its first degrade hits the floor rule and returns exhausted.
    _by_model = {}
    for _tier, _mid in _pmodels.items():
        _by_model.setdefault(_mid, []).append(_tier)
    for _mid, _tiers in sorted(_by_model.items()):
        check(f"providers.{pname}.models: '{_mid}' is declared by exactly one tier",
              len(_tiers) == 1,
              f"tiers {sorted(_tiers)} all resolve to '{_mid}' — tier aliasing breaks the model-keyed "
              "ladder flattening (self-loop), effort_on_degrade (last-wins) and tier_of() (first-match); "
              "remove the tier instead of pointing two at one model")
    # (0a) DEGRADE LADDER TERMINATES (v1.15) — walk `degrade.ladder` from EVERY tier
    # with a visited set; a tier seen twice is a cycle, i.e. a consumer that follows
    # this ladder to exhaustion never stops. The rescue edge out of the bottom tier is
    # INCLUDED in the walk on purpose: resolve_route's downward-order check exempts
    # index 0 (that exemption is exactly what lets a rescue edge through), and
    # `_degrade_walk`/`_fallback_for` only survive a cycle because each happens to take
    # exactly ONE step today. The escalation/rework loops this SSOT is growing walk
    # ladders to exhaustion, so termination has to be structural here rather than an
    # accident of the current consumers. Fix a hit by making the ladder a DAG (give it
    # a sink tier), never by capping the walk in one consumer.
    _lad = ssot_provider_ladder_tier.get(pname, {})
    _cycle = None
    for _start in sorted(_pmodels):
        _seen, _cur = [], _start
        while _cur in _lad and _cycle is None:
            if _cur in _seen:
                _cycle = _seen[_seen.index(_cur):] + [_cur]
                break
            _seen.append(_cur)
            _cur = _lad[_cur]
        if _cycle:
            break
    check(f"providers.{pname}.degrade.ladder terminates from every tier (no cycle)",
          _cycle is None,
          f"cycle {' -> '.join(_cycle or [])} — a ladder-walking consumer spins forever here; "
          "make the ladder acyclic (one sink tier), do not cap the walk in the consumer")
    # (0b) model_ladder_entry (v1.15): each key must be a declared tier, and its value
    # must be a RUNG OF THAT TIER'S OWN effort_ladder — otherwise the climb enters on a
    # rung it cannot then continue from.
    _entry = ssot_provider_ladder_entry.get(pname, {})
    for _tier, _rung in sorted(_entry.items()):
        check(f"providers.{pname}.escalation.model_ladder_entry names a declared tier '{_tier}'",
              _tier in _pmodels, f"providers.{pname}.models has no '{_tier}' entry (has {sorted(_pmodels)})")
        _rungs = ssot_provider_effort_ladder.get(pname, {}).get(_tier, [])
        check(f"providers.{pname}.escalation.model_ladder_entry.{_tier} = '{_rung}' is a rung of that tier's effort_ladder",
              _rung in _rungs,
              f"effort_ladder.{_tier}={_rungs!r} — an entry rung outside the ladder leaves the climb "
              "with nowhere to continue from")
    for tc_name, (tier, intent) in sorted(ssot_provider_task_classes.get(pname, {}).items()):
        check(f"providers.{pname}.task_classes.{tc_name} is a class the neutral block declares",
              tc_name in ssot_task_classes,
              f"no '{tc_name}' row in the top-level task_classes: block (known: {sorted(ssot_task_classes)}) "
              "— the class vocabulary is provider-NEUTRAL; this override would never fire")
        check(f"providers.{pname}.task_classes.{tc_name} tier '{tier}' resolves under providers.{pname}.models",
              tier in _pmodels, f"providers.{pname}.models has no '{tier}' entry (has {sorted(_pmodels)})")
        _cell = _pmap.get(tier, {})
        check(f"providers.{pname}.task_classes.{tc_name} intent '{intent}' present in providers.{pname}.effort.map.{tier}",
              intent in _cell, f"providers.{pname}.effort.map.{tier} has no '{intent}' key (has {sorted(_cell)})")
    # (3) ceiling — only for a profile that declares one.
    _ceil = ssot_provider_ceiling.get(pname, {})
    for tier, limit in sorted(_ceil.items()):
        if tier not in _pmodels:
            check(f"providers.{pname}.effort.native_effort_ceiling names a declared tier '{tier}'",
                  False, f"providers.{pname}.models has no '{tier}' entry (has {sorted(_pmodels)})")
            continue
        emissions = [(f"effort.map.{tier}.{k}", v) for k, v in sorted((_pmap.get(tier) or {}).items())]
        _dg = ssot_provider_degrade_effort_tier.get(pname, {}).get(tier)
        if _dg:
            emissions.append((f"degrade.effort_on_degrade.{tier}", _dg))
        emissions += [(f"escalation.effort_ladder.{tier}[{i}]", r)
                      for i, r in enumerate(ssot_provider_effort_ladder.get(pname, {}).get(tier, []))]
        _ent = ssot_provider_ladder_entry.get(pname, {}).get(tier)
        if _ent:
            emissions.append((f"escalation.model_ladder_entry.{tier}", _ent))
        for site, native in emissions:
            if native is None:
                continue
            check(f"providers.{pname}.{site} = '{native}' is within {_pmodels[tier]}'s probed ceiling '{limit}'",
                  _EFFORT_RANK.get(native, 99) <= _EFFORT_RANK.get(limit, -1),
                  f"{_pmodels[tier]} rejects efforts above '{limit}' (probed) — this route would fail at dispatch")

# ---- codex_peer.lane / providers.openai single-source-of-truth cross-check ---
# v1.8 moved codex_peer.lane's model/degrade facts to providers.openai (single
# Codex truth prose note, model-routing.yaml ~line 353); this guards that the
# lane block never re-introduces its own competing `model:`/ladder fact,
# which would recreate the two-silently-driftable-copies problem v1.8 closed.
_cp_start = ssot_text.find("\ncodex_peer:\n")
if _cp_start != -1:
    _cp_end = find_next_top_level_key(ssot_text, _cp_start + len("\ncodex_peer:\n"))
    _lane_m = re.search(r"^  lane:[^\n]*\n((?:    [^\n]*\n)+)", ssot_text[_cp_start:_cp_end], re.M)
    _lane_block = _lane_m.group(1) if _lane_m else ""
    check("codex_peer.lane carries no competing model:/ladder: fact (single Codex truth stays in providers.openai)",
          not re.search(r"^\s*(model|ladder|degrade|prices):\s*\S", _lane_block, re.M),
          "codex_peer.lane re-introduced a model/ladder/degrade/prices key — move it back to providers.openai, this SSOT allows exactly one copy")
print()


print("-- (a)/(b) per-agent --")
for name, (model, effort) in sorted(ssot_agents.items()):
    is_epic = name.startswith("epic-")
    disk_model, disk_effort = agent_frontmatter(name)

    # (b) haiku invariant applies to EVERY row, epic or not, and to the SSOT itself.
    if model == "haiku" and effort != "unset":
        check(f"SSOT invariant: haiku agent '{name}' has effort pinned", False,
              f"SSOT says effort={effort!r} but haiku rejects the reasoning dial")
    if model == "haiku" and disk_effort is not None:
        check(f"haiku agent '{name}' carries no effort: key on disk", False,
              f"found effort: {disk_effort!r} in {name}.md frontmatter")
    elif model == "haiku":
        check(f"haiku agent '{name}' carries no effort: key on disk", True, "")

    if is_epic:
        # epic_* rows are OWNED by epic-dev-assignments.yaml; cross-checked warn-only
        # below, and their frontmatter is already fully drift-guarded by
        # verify-assignments.sh (sub-check f). Skip the hard (a) check here to avoid
        # double-failing the same drift under two different messages.
        continue

    if disk_model is None and disk_effort is None and not os.path.exists(os.path.join(AGENTS_DIR, name + ".md")):
        check(f"agent '{name}' file exists", False, f"no {name}.md under {AGENTS_DIR}")
        continue

    check(f"agent '{name}' model", disk_model == model, f"SSOT={model!r} disk={disk_model!r}")
    if effort == "unset":
        check(f"agent '{name}' effort (expect no key)", disk_effort is None, f"SSOT=unset disk effort={disk_effort!r}")
    else:
        check(f"agent '{name}' effort", disk_effort == effort, f"SSOT={effort!r} disk={disk_effort!r}")

# ---- frontmatter hygiene scan (EVERY agent file — see frontmatter_error) ----
# WARN-ONLY, deliberately. MEASURED 2026-07-26: `epic-test-fixer` has failed
# yaml.safe_load for a long time (its description carries an unquoted "(Death-A): must"),
# and Claude Code registers it anyway — so its frontmatter parser is MORE PERMISSIVE than
# PyYAML, and a strict-YAML failure here does NOT mean the runtime rejects the agent.
# Hard-failing would block unrelated commits over a non-bug, so this reports hygiene:
# strict-YAML-invalid frontmatter is fragile (any standard tool that reads it breaks, as
# this guard's own helper did) and worth fixing, but it is not proof of breakage.
# Do NOT promote this to a failure without first MEASURING what Claude Code actually
# rejects — that measurement does not exist yet.
print("-- agent frontmatter hygiene (warn-only; Claude Code's parser is more permissive than PyYAML) --")
disk_agent_files = sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md")))
for fp in disk_agent_files:
    name = os.path.splitext(os.path.basename(fp))[0]
    err = frontmatter_error(fp)
    if err:
        msg = f"agent '{name}' frontmatter is not strict-YAML — {err} ({fp})"
        frontmatter_warnings.append(msg)
        print(f"  [WARN] {msg}")
if not frontmatter_warnings:
    print("  [OK ] every agent frontmatter parses as strict YAML")

# ---- unknown-agent scan (disk agents with no SSOT row) --------------------
print("-- unknown-agent scan --")
for fp in disk_agent_files:
    name = os.path.splitext(os.path.basename(fp))[0]
    if name not in ssot_agents:
        msg = f"unknown agent '{name}' (no SSOT row) — {fp}"
        unknown_agent_warnings.append(msg)
        print(f"  [{'FAIL(strict)' if STRICT else 'WARN'}] {msg}")

# ---- epic_* composed-cross-check against epic-dev-assignments.yaml (warn-only) ----
print("-- epic_* composed-cross-check (warn-only; epic-dev-assignments.yaml is authoritative) --")
epic_manifest = os.path.join(CLAUDE_DIR, "epic-dev-assignments.yaml")
if os.path.exists(epic_manifest):
    man = read(epic_manifest)
    phase_rx = re.compile(r"^\s*(?:[1-8]|gate_fix):\s*\{\s*agent:\s*([\w-]+),\s*model:\s*(\w+),\s*effort:\s*(\w+)\s*\}", re.M)
    manifest_agents = {}
    for mo in phase_rx.finditer(man):
        manifest_agents[mo.group(1)] = (mo.group(2), mo.group(3))
    for name, (model, effort) in sorted(ssot_agents.items()):
        if not name.startswith("epic-"):
            continue
        if name not in manifest_agents:
            w = f"epic agent '{name}' has an SSOT row but no epic-dev-assignments.yaml entry"
            epic_crosscheck_warnings.append(w)
            print(f"  [WARN] {w}")
            continue
        m_model, m_effort = manifest_agents[name]
        if (m_model, m_effort) != (model, effort):
            w = f"epic agent '{name}': SSOT=({model},{effort}) vs epic-dev-assignments.yaml=({m_model},{m_effort})"
            epic_crosscheck_warnings.append(w)
            print(f"  [WARN] {w}")
        else:
            print(f"  [OK ] epic agent '{name}' matches epic-dev-assignments.yaml")
else:
    print(f"  [WARN] {epic_manifest} not found — skipping composed-cross-check")

# ---- surface-list convergence (s02 re-harden fix #5) -----------------------
# Three hand-maintained lists must agree on which paths are "routing-affecting":
# SSOT `consumers:`, the pre-commit hook's ROUTING_PREFIXES, and the commit-msg
# hook's ROUTING_AFFECTING. Before this fix commit-msg silently OMITTED two SSOT
# consumers (commands/plan-harden.md, skills/plan-builder/references/schemas.md) —
# the pin-change token gate never fired on them. This derives nothing (the hooks stay
# hand-authored bash regexes for now) but FAILS loudly the moment a consumer is added
# to the SSOT without also being reachable by both hook regexes, closing the drift
# path for good rather than just patching today's two known gaps.
print("-- surface-list convergence (SSOT consumers vs pre-commit/commit-msg hook regexes) --")
# s02/SRC-03 fail-closed fix: this block used to SKIP (fail OPEN) whenever the hooks
# were absent, so a FRESH CLONE — which by construction has no installed hooks, because
# git cannot version .git/hooks — reported the guard green while running no guard at
# all. The hooks now live tracked under githooks/ and are wired by scripts/install-hooks.sh
# via core.hooksPath; an unwired repo root is a REAL failure, not a skip.
#
# The hooks dir is resolved the way git itself resolves it (core.hooksPath if set, else
# .git/hooks), so this check always validates the files git will ACTUALLY run rather than
# a stale copy left behind in .git/hooks.
#
# The one honest skip that remains: a fixture CLAUDE_DIR (fixtures/routing-guard/broken-tree
# and friends) is a SUBTREE of a repo, not a repo root — it has no hooks of its own to
# enforce, and forcing it to fail here would mask the violations those fixtures seed.
def _git_out(*args):
    try:
        p = subprocess.run(["git", "-C", CLAUDE_DIR, *args],
                           capture_output=True, text=True)
    except OSError:
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


_toplevel = _git_out("rev-parse", "--show-toplevel")
_is_repo_root = bool(_toplevel) and os.path.realpath(_toplevel) == os.path.realpath(CLAUDE_DIR)
_hooks_path_cfg = _git_out("config", "--get", "core.hooksPath")
if _hooks_path_cfg:
    _hooks_dir = _hooks_path_cfg if os.path.isabs(_hooks_path_cfg) else os.path.join(CLAUDE_DIR, _hooks_path_cfg)
else:
    _hooks_dir = os.path.join(CLAUDE_DIR, ".git", "hooks")
_pre_commit_hook = os.path.join(_hooks_dir, "pre-commit")
_commit_msg_hook = os.path.join(_hooks_dir, "commit-msg")

if not _is_repo_root:
    print(f"  [SKIP] {CLAUDE_DIR} is not a git repo root (fixture CLAUDE_DIR override) — no hooks of its own to enforce")
else:
    check("core.hooksPath is set (guard hooks are tracked in githooks/, which git only runs via core.hooksPath)",
          bool(_hooks_path_cfg),
          "run scripts/install-hooks.sh — an unwired clone runs NO gitleaks gate and NO routing drift guard")
    for _label, _hook in (("pre-commit", _pre_commit_hook), ("commit-msg", _commit_msg_hook)):
        check(f"guard hook installed+executable: {_label} ({_hook})",
              os.path.isfile(_hook) and os.access(_hook, os.X_OK),
              "run scripts/install-hooks.sh — this guard is not wired into this clone")

if os.path.isfile(_pre_commit_hook) and os.path.isfile(_commit_msg_hook):
    def _extract_hook_regex(hook_path, var_name):
        t = read(hook_path)
        mo = re.search(rf"{var_name}='([^']*)'", t)
        if not mo:
            print(f"FATAL: could not find {var_name}='...' literal in {hook_path}", file=sys.stderr)
            sys.exit(2)
        return re.compile(mo.group(1))

    _routing_prefixes_rx = _extract_hook_regex(_pre_commit_hook, "ROUTING_PREFIXES")
    _routing_affecting_rx = _extract_hook_regex(_commit_msg_hook, "ROUTING_AFFECTING")
    for raw_path in sorted(consumers):
        rel = raw_path[len("~/.claude/"):] if raw_path.startswith("~/.claude/") else raw_path
        check(f"consumer '{rel}' reachable by pre-commit ROUTING_PREFIXES", bool(_routing_prefixes_rx.match(rel)),
              f"pattern={_routing_prefixes_rx.pattern!r} — add it or the hook silently skips this consumer's partial-stage race guard")
        check(f"consumer '{rel}' reachable by commit-msg ROUTING_AFFECTING", bool(_routing_affecting_rx.match(rel)),
              f"pattern={_routing_affecting_rx.pattern!r} — add it or the pin-change approval-token gate never fires for this consumer")
elif not _is_repo_root:
    print(f"  [SKIP] no hooks under {_hooks_dir} — fixture CLAUDE_DIR override, nothing to converge against")

# ============================================================================
# (d) run.py _FALLBACK_LADDER / _REASONING_DIRECTIVE lockstep — ast, no import/exec
# ============================================================================
print()
print("-- (d) run.py lockstep (ast.parse + literal_eval; never imports/executes run.py) --")
run_py_src = read(RUN_PY)
try:
    tree = ast.parse(run_py_src, filename=RUN_PY)
except SyntaxError as e:
    print(f"FATAL: could not ast.parse {RUN_PY}: {e}", file=sys.stderr)
    sys.exit(2)

found = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        tgt = node.targets[0].id
        if tgt in ("_FALLBACK_LADDER", "_REASONING_DIRECTIVE", "_DEGRADE_EFFORT"):
            try:
                found[tgt] = ast.literal_eval(node.value)
            except Exception as e:
                check(f"ast literal_eval {tgt}", False, f"could not statically evaluate: {e}")

# s04 (DSP-02): both run.py dicts are PROVIDER-SCOPED ({provider: {model: ...}})
# and compare against the tier-keyed providers.<name>.degrade blocks translated
# through providers.<name>.models — the ONLY machine-readable degradation source.
if "_FALLBACK_LADDER" not in found:
    check("run.py defines _FALLBACK_LADDER", False, "assignment not found via ast walk")
else:
    check("_FALLBACK_LADDER == providers.<name>.degrade.ladder (tier→model translated)",
          found["_FALLBACK_LADDER"] == ssot_ladders,
          f"run.py={found['_FALLBACK_LADDER']!r} SSOT={ssot_ladders!r}")

if "_DEGRADE_EFFORT" not in found:
    check("run.py defines _DEGRADE_EFFORT", False, "assignment not found via ast walk")
else:
    check("_DEGRADE_EFFORT == providers.<name>.degrade.effort_on_degrade (tier→model translated)",
          found["_DEGRADE_EFFORT"] == ssot_degrade_efforts,
          f"run.py={found['_DEGRADE_EFFORT']!r} SSOT={ssot_degrade_efforts!r}")

# Interim cross-check while the legacy flat `degradation:` block still exists
# (s03 retires it): its model-keyed ladder must equal the translated anthropic
# walk, or the prose copy has silently drifted from the machine-readable truth.
_flat_start = ssot_text.find("\ndegradation:\n")
if _flat_start != -1:
    _flat_block = ssot_text[_flat_start:find_next_top_level_key(ssot_text, _flat_start + len("\ndegradation:\n"))]
    _flat_ladder = dict(re.findall(r"^\s{4}(\w+):\s*(\w+)\s*(?:#.*)?$", _flat_block.split("floor:")[0], re.M))
    check("legacy flat degradation.ladder still matches providers.anthropic.degrade",
          _flat_ladder == ssot_ladders.get("anthropic"),
          f"flat={_flat_ladder!r} providers.anthropic={ssot_ladders.get('anthropic')!r}")

if "_REASONING_DIRECTIVE" not in found:
    check("run.py defines _REASONING_DIRECTIVE", False, "assignment not found via ast walk")
else:
    directive_keys = set(found["_REASONING_DIRECTIVE"].keys())
    missing_tiers = tier_tokens - directive_keys
    check("every SSOT reasoning tier is a _REASONING_DIRECTIVE key", not missing_tiers,
          f"SSOT tiers={sorted(tier_tokens)} missing from run.py={sorted(missing_tiers)}")

# ============================================================================
# (c) prose-consumer version stamps — FULL MODE ONLY (stamps land in s04)
# ============================================================================
if MODE == "full":
    print()
    print("-- (c) prose-consumer stamps (<!-- routing-ssot: vN -->) — full mode only --")
    stamp_rx = re.compile(r"<!--\s*routing-ssot:\s*v(\d+)\s*-->")
    for raw_path, stamp_type in sorted(consumers.items()):
        if stamp_type not in ("lint-reads", "prose-rationale"):
            continue
        p = resolve_consumer_path(raw_path)
        if not os.path.exists(p):
            check(f"stamp present in {raw_path}", False, f"file not found: {p}")
            continue
        t = read(p)
        sm = stamp_rx.search(t)
        if not sm:
            check(f"stamp present in {raw_path}", False, "no `<!-- routing-ssot: vN -->` marker found")
            continue
        found_v = int(sm.group(1))
        check(f"stamp version in {raw_path}", found_v == SSOT_VERSION,
              f"stamp=v{found_v} SSOT=v{SSOT_VERSION}")

print()
print(f"{checks} structured checks run; {len(frontmatter_warnings)} frontmatter-hygiene warning(s); "
      f"{len(unknown_agent_warnings)} unknown-agent warning(s); "
      f"{len(epic_crosscheck_warnings)} epic cross-check warning(s).")

if STRICT and unknown_agent_warnings:
    failures.extend(f"[--strict] {w}" for w in unknown_agent_warnings)

if failures:
    print(f"\nFAIL ({len(failures)} issue(s)):", file=sys.stderr)
    for f in failures:
        print("  - " + f, file=sys.stderr)
    sys.exit(1)

print("PASS: (a)/(b)/(d)" + ("/(c)" if MODE == "full" else "") + " clean.")
sys.exit(0)
PY
PY_RC=$?
set -e
# _note_fail preserves the drift(1)-vs-tooling-crash(2) distinction the internal
# sub-checks already maintain, instead of folding every non-zero RC to 1 (s02
# re-harden fix #11). Fail-closed is unaffected either way (both are non-zero); this
# only restores the signal for a future caller branching on 1 vs 2.
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
[[ $PY_RC -eq 0 ]] || _note_fail "$PY_RC"
echo

# ---- (e) CLAUDE.md digest re-render diff clean ---------------------------
echo "== (e) CLAUDE.md digest re-render diff (variant=$DIGEST_VARIANT, ssot=$SSOT) =="
set +e
python3 "$DIGEST_RENDERER" --variant "$DIGEST_VARIANT" --claude-dir "$CLAUDE_DIR" --yaml "$SSOT" --check
DIGEST_RC=$?
set -e
if [[ $DIGEST_RC -ne 0 ]]; then
  echo "  FAIL: digest re-render check exited $DIGEST_RC (see render-routing-digest.py output above)" >&2
  _note_fail "$DIGEST_RC"
else
  echo "  OK: digest matches SSOT re-render"
fi
echo

# ---- (h) settings.json ↔ main_session advisory alignment (WARN-ONLY) ------
# settings.json is USER-OWNED and may be intentionally overridden per session/period,
# so drift here surfaces loudly but never blocks a commit (contrast every other check).
# Full mode only — mirrors the stamp check's mode gating. Skips quietly when the file
# is absent (fixture CLAUDE_DIR overrides don't carry a settings.json).
if [[ "$MODE" == "full" ]]; then
  echo "== (h) settings.json advisory alignment (warn-only; settings.json is user-owned) =="
  SETTINGS_JSON="$CLAUDE_DIR/settings.json"
  if [[ -f "$SETTINGS_JSON" ]]; then
    set +e
    SSOT="$SSOT" SETTINGS_JSON="$SETTINGS_JSON" python3 - <<'PY'
import json, os, re, sys

ssot = open(os.environ["SSOT"], encoding="utf-8").read()
ms_start = ssot.find("\nmain_session:\n")
if ms_start == -1:
    print("  WARN: SSOT has no `main_session:` block — cannot check settings alignment")
    sys.exit(0)
tail = ssot[ms_start + len("\nmain_session:\n"):]
nxt = re.search(r"^[^\s#]", tail, re.M)
block = tail[: nxt.start() if nxt else len(tail)]
# s03: main_session converted to TIER FORM (advisory_default_tier/
# advisory_default_effort) — resolve through providers.anthropic (main-session
# is Claude-only; it cannot switch execution family) to get the concrete
# family name / effort level settings.json actually carries, instead of
# comparing the abstract tier/intent tokens directly (which would never match
# a concrete `model`/`effortLevel` and false-warn on every run).
tier_m = re.search(r"advisory_default_tier:\s*([\w-]+)", block)
intent_m = re.search(r"advisory_default_effort:\s*(\w+)", block)

anthropic_m = re.search(r"\n  anthropic:\n((?:.*\n)*?)(?=\n  \w+:\n|\Z)", ssot)
anthropic_block = anthropic_m.group(1) if anthropic_m else ""
models_m = re.search(r"^    models:[^\n]*\n((?:      [^\n]*\n)+)", anthropic_block, re.M)
provider_models = dict(re.findall(r"^\s*([\w-]+):\s*([\w.\-]+)", models_m.group(1), re.M)) if models_m else {}
effort_map = {}
em_m = re.search(r"^    effort:[^\n]*\n((?:      [^\n]*\n)+)", anthropic_block, re.M)
if em_m:
    for tmo in re.finditer(r"^\s*([\w-]+):\s*\{([^}]*)\}", em_m.group(1), re.M):
        cells = dict(re.findall(r"([\w-]+)\s*:\s*(null|[\w-]+)", tmo.group(2)))
        effort_map[tmo.group(1)] = {k: (None if v == "null" else v) for k, v in cells.items()}

fam = provider_models.get(tier_m.group(1)) if tier_m else None
want = effort_map.get(tier_m.group(1), {}).get(intent_m.group(1)) if (tier_m and intent_m) else None
if tier_m and fam is None:
    print(f"  WARN: main_session.advisory_default_tier={tier_m.group(1)!r} has no providers.anthropic.models entry — cannot resolve")
if tier_m and intent_m and fam is not None and want is None and intent_m.group(1) not in effort_map.get(tier_m.group(1), {}):
    print(f"  WARN: main_session.advisory_default_effort={intent_m.group(1)!r} has no providers.anthropic.effort.map.{tier_m.group(1)} entry — cannot resolve")

try:
    settings = json.load(open(os.environ["SETTINGS_JSON"], encoding="utf-8"))
except Exception as e:
    print(f"  WARN: could not parse settings.json: {e}")
    sys.exit(0)

warned = False
model = (settings.get("model") or "").lower()
if fam:
    if not model:
        print(f"  WARN: settings.json has no `model` — SSOT main_session.advisory_default_tier resolves to '{fam}'")
        warned = True
    elif fam not in model:
        print(f"  WARN: settings.json model={settings.get('model')!r} is not the SSOT main_session advisory family '{fam}' (tier={tier_m.group(1)})")
        warned = True
    else:
        print(f"  [OK ] settings.json model ({settings.get('model')}) matches advisory_default_tier-resolved family '{fam}'")
if want:
    have = settings.get("effortLevel")
    if have is None:
        print(f"  WARN: settings.json has no `effortLevel` — SSOT main_session.advisory_default_effort resolves to '{want}'")
        warned = True
    elif have != want:
        print(f"  WARN: settings.json effortLevel={have!r} != SSOT main_session advisory effort '{want}' (v1.1 D6/D7, resolved from intent={intent_m.group(1)!r})")
        warned = True
    else:
        print(f"  [OK ] settings.json effortLevel ({have}) matches advisory_default_effort-resolved '{want}'")
if warned:
    print("  (warn-only: settings.json is user-owned — align it via /routing-update step 7, or accept the override knowingly)")
PY
    set -e
  else
    echo "  [SKIP] $SETTINGS_JSON not found (expected for a fixture CLAUDE_DIR override)"
  fi
  echo
fi

# ---- (f) verify-assignments.sh sub-check ----------------------------------
echo "== (f) verify-assignments.sh sub-check =="
set +e
CLAUDE_DIR="$CLAUDE_DIR" bash "$VERIFY_ASSIGNMENTS"
ASSIGN_RC=$?
set -e
if [[ $ASSIGN_RC -ne 0 ]]; then
  echo "  FAIL: verify-assignments.sh exited $ASSIGN_RC" >&2
  _note_fail "$ASSIGN_RC"
fi
echo

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
