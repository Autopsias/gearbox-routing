#!/usr/bin/env bash
# verify-assignments.sh — drift guard for the /epic-dev model+effort tuning.
#
# DERIVES four on-disk sources and compares each to the single-source-of-truth manifest
# (~/.claude/epic-dev-assignments.yaml). Exits non-zero (loud) on ANY drift. The manifest is
# authoritative; if a source disagrees, fix the source — do NOT loosen this check.
#
# Sources checked:
#   1. agent frontmatter      ~/.claude/agents/epic-*.md           (model:/effort:)
#   2. phase primary dispatch full/phase-N-*.md                    (first subagent_type + model=)
#
# NOTE: the Ralph runner's orchestrator checks (#3 ORCH_EFFORT, #4 get_status_model) were
# retired together with the bash runner. The in-session `--auto` loop has no per-phase
# orchestrator-model script — the orchestrator IS the session. The real levers are the
# per-phase SUBAGENT model/effort (#1/#2 below), which stay fully drift-guarded.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
MANIFEST="${MANIFEST:-$CLAUDE_DIR/epic-dev-assignments.yaml}"
AGENTS_DIR="$CLAUDE_DIR/agents"
PHASE_DIR="$CLAUDE_DIR/commands/references/epic-dev/full"

for p in "$MANIFEST" "$AGENTS_DIR" "$PHASE_DIR"; do
  [[ -e "$p" ]] || { echo "FATAL: missing required path: $p" >&2; exit 2; }
done

MANIFEST="$MANIFEST" AGENTS_DIR="$AGENTS_DIR" PHASE_DIR="$PHASE_DIR" \
python3 - <<'PY'
import os, re, sys, glob

MANIFEST = os.environ["MANIFEST"]
AGENTS_DIR = os.environ["AGENTS_DIR"]
PHASE_DIR = os.environ["PHASE_DIR"]

failures = []
checks = 0

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

# ---- 1. parse the manifest (single source of truth) -------------------------
man = read(MANIFEST)

# phases:  N: { agent: X, model: Y, effort: Z }
phase_rx = re.compile(r'^\s*([1-8]):\s*\{\s*agent:\s*([\w-]+),\s*model:\s*(\w+),\s*effort:\s*(\w+)\s*\}', re.M)
phases = {}  # n -> (agent, model, effort)
for m in phase_rx.finditer(man):
    phases[int(m.group(1))] = (m.group(2), m.group(3), m.group(4))
if len(phases) != 8:
    print(f"FATAL: manifest must define phases 1-8, found {sorted(phases)}", file=sys.stderr); sys.exit(2)

gf = re.search(r'gate_fix:\s*\{\s*agent:\s*([\w-]+),\s*model:\s*(\w+),\s*effort:\s*(\w+)\s*\}', man)
gate_fix = (gf.group(1), gf.group(2), gf.group(3)) if gf else None

# ---- helpers to derive on-disk sources --------------------------------------
def agent_frontmatter(agent):
    p = os.path.join(AGENTS_DIR, agent + ".md")
    if not os.path.exists(p):
        return (None, None)
    t = read(p)
    fm = t.split("---", 2)
    block = fm[1] if len(fm) >= 3 else t
    mo = re.search(r'^model:\s*(\w+)', block, re.M)
    eo = re.search(r'^effort:\s*(\w+)', block, re.M)
    return (mo.group(1) if mo else None, eo.group(1) if eo else None)

def phase_primary(n):
    files = glob.glob(os.path.join(PHASE_DIR, f"phase-{n}-*.md"))
    if not files:
        return (None, None)
    t = read(files[0])
    m = re.search(r'subagent_type="(epic-[\w-]+)"', t)
    if not m:
        return (None, None)
    after = t[m.end():]
    mm = re.search(r'model="(\w+)"', after)
    return (m.group(1), mm.group(1) if mm else None)

def check(desc, expected, actual):
    global checks
    checks += 1
    ok = expected == actual
    print(f"  [{'OK ' if ok else 'DRIFT'}] {desc}: manifest={expected!r} disk={actual!r}")
    if not ok:
        failures.append(f"{desc}: manifest={expected!r} disk={actual!r}")

# ---- 2. agent frontmatter ↔ manifest ----------------------------------------
print("== agent frontmatter (model/effort) ==")
seen = set()
for n in range(1, 9):
    agent, model, effort = phases[n]
    if agent in seen:  # an agent can serve >1 phase; check once
        continue
    seen.add(agent)
    dm, de = agent_frontmatter(agent)
    check(f"agent {agent} model", model, dm)
    check(f"agent {agent} effort", effort, de)
if gate_fix:
    a, model, effort = gate_fix
    dm, de = agent_frontmatter(a)
    check(f"agent {a} model", model, dm)
    check(f"agent {a} effort", effort, de)

# ---- 3. phase primary dispatch model ↔ manifest -----------------------------
print("== phase primary dispatch (subagent_type + model=) ==")
for n in range(1, 9):
    agent, model, _ = phases[n]
    da, dm = phase_primary(n)
    check(f"phase {n} agent", agent, da)
    check(f"phase {n} dispatch model", model, dm)

# ---- verdict ----------------------------------------------------------------
print(f"\n{checks} checks run.")
if failures:
    print(f"\nDRIFT DETECTED ({len(failures)}):")
    for f in failures:
        print("  - " + f)
    print("\nFAIL: on-disk sources disagree with the manifest. Fix the source (or the manifest if it is wrong).")
    sys.exit(1)
print("\nPASS: both on-disk sources (agent frontmatter + phase dispatch) agree with the manifest.")
PY
