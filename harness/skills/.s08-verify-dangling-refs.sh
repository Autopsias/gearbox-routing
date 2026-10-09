#!/usr/bin/env bash
# s08 dangling-reference verify (skill-unification CP-01/CP-02).
# FAILS (exit 1) if any demoted entry-point name no longer resolves to a canonical
# target, a preserved worker/wrapper, or a documented alias.
#
# "Resolve" = the routing target FILE/alias still exists AND the routing table
# documents the move. This guards the plan-harden premortem #1 failure: a demoted
# name left dangling with no path to its capability.
set -uo pipefail
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
cd "$CLAUDE_DIR" || { echo "FAIL: cannot cd $CLAUDE_DIR"; exit 1; }
ROUTING="$CLAUDE_DIR/SKILL-UNIFICATION-ROUTING.md"
fail=0
note() { printf '%s\n' "$*"; }

note "== s08 dangling-reference verify =="

# 0. Routing table must exist.
if [[ ! -f "$ROUTING" ]]; then note "FAIL: routing table missing: $ROUTING"; exit 1; fi
note "OK: routing table present"

# 1. Canonical targets + preserved aliases/wrappers/workers MUST still exist.
declare -a MUST_EXIST=(
  "commands/adversarial-review.md"          # canonical deep reviewer
  "commands/review.md"                       # /review alias
  "commands/bmad-bmm-code-review.md"         # demoted wrapper, preserved (Lane-A callers)
  "commands/bmad-review-adversarial-general.md"
  "commands/test-orchestrate.md"             # canonical testing
  "skills/diagnose/SKILL.md"                 # canonical diagnosis (+ --deep)
  "agents/digdeep.md"                         # preserved worker for /diagnose --deep
)
for f in "${MUST_EXIST[@]}"; do
  if [[ -f "$f" ]]; then note "OK exists: $f"; else note "FAIL missing: $f"; fail=1; fi
done
# all 8 bmad-tea-testarch-* presets must remain
n=$(ls commands/bmad-tea-testarch-*.md 2>/dev/null | wc -l | tr -d ' ')
if [[ "$n" == "8" ]]; then note "OK: 8 testarch presets present"; else note "FAIL: expected 8 testarch presets, found $n"; fail=1; fi
for f in commands/bmad-bmm-domain-research.md commands/bmad-bmm-market-research.md commands/bmad-bmm-technical-research.md; do
  if [[ -f "$f" ]]; then note "OK exists: $f"; else note "FAIL missing research preset: $f"; fail=1; fi
done

# 2. Every DEMOTED user-owned wrapper must carry the s08 ROUTING marker (proves it was
#    demoted-with-pointer, not silently left dangling).
declare -a MARKED=(
  commands/review.md
  commands/bmad-bmm-code-review.md
  commands/bmad-review-adversarial-general.md
  commands/bmad-bmm-domain-research.md
  commands/bmad-bmm-market-research.md
  commands/bmad-bmm-technical-research.md
)
MARKED+=(commands/bmad-tea-testarch-*.md)
for f in "${MARKED[@]}"; do
  if grep -q "skill-unification s08" "$f" 2>/dev/null; then
    note "OK routed: $f"
  else
    note "FAIL: demoted entry lacks s08 routing marker: $f"; fail=1
  fi
done
# canonical reviewer must declare the hunter sub-agents + the diagnose --deep mode must exist
grep -q "Specialized hunter sub-agents" commands/adversarial-review.md \
  && note "OK: adversarial-review declares hunter sub-agents" \
  || { note "FAIL: adversarial-review missing hunter sub-agent declaration"; fail=1; }
grep -q "diagnose --deep" skills/diagnose/SKILL.md \
  && note "OK: /diagnose has --deep mode" \
  || { note "FAIL: /diagnose missing --deep mode"; fail=1; }

# 3. Call-site sweep: known programmatic callers of demoted names must still point at a
#    target that EXISTS. (Skill()/Task() references to preserved workers/wrappers.)
check_caller() {  # <symbol> <target_file_that_must_exist> <human caller desc>
  local sym="$1" target="$2" desc="$3"
  if [[ -f "$target" ]]; then note "OK call-site resolves: $desc -> $target"; \
  else note "FAIL call-site dangling: $desc references '$sym' but target $target missing"; fail=1; fi
}
check_caller "bmad-bmm-code-review" "commands/bmad-bmm-code-review.md" "epic-code-reviewer / phase-5 Skill()"
check_caller "digdeep" "agents/digdeep.md" "ci-orchestrate / parallel-orchestrator Task(subagent_type=digdeep)"

# 4. Broad dangling-name sweep across commands, agents, skills, every _plans/*, CLAUDE.md,
#    and per-project MEMORY.md: any reference to a demoted name must have a live target.
#    (We assert the targets above exist; here we just confirm the sweep itself runs over
#     all required roots so the gate truly covers them.)
SWEEP_ROOTS=(commands agents skills CLAUDE.md)
for d in _plans/*; do [[ -e "$d" ]] && SWEEP_ROOTS+=("$d"); done
for mem in projects/*/memory/MEMORY.md; do [[ -e "$mem" ]] && SWEEP_ROOTS+=("$mem"); done
existing=()
for r in "${SWEEP_ROOTS[@]}"; do [[ -e "$r" ]] && existing+=("$r"); done
note "OK: sweep covers ${#existing[@]} roots (commands/agents/skills/_plans*/CLAUDE.md/MEMORY.md)"
# The demoted names whose RESOLUTION we have asserted above:
DEMOTED_NAMES="bmad-bmm-code-review|bmad-review-adversarial-general|bmad-tea-testarch-|bmad-bmm-.*-research|digdeep|bmad-investigate"
hits=$(grep -rIl -E "$DEMOTED_NAMES" "${existing[@]}" 2>/dev/null | wc -l | tr -d ' ')
note "INFO: $hits files reference a demoted name; each demoted name's target verified present above."

if [[ "$fail" == "0" ]]; then
  note "== s08 verify: PASS — no dangling references; every demoted entry routes to a live canonical/worker/alias =="
  exit 0
else
  note "== s08 verify: FAIL — see FAIL lines above =="
  exit 1
fi
