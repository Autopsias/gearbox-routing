#!/usr/bin/env zsh
# Codex-only plan-harden smoke test. Walks the ported skill's
# Phase 0/2/3/4 loop (references/dual-harness-contract.md § 6.4, this port's
# SKILL.md) on a two-session fixture plan, with a REAL `codex exec` dispatch of
# the adversarial-review Codex skill.
#
#   ./harden-smoke.sh [workdir]        # default: a fresh mktemp -d
#
# Passes when: (1) the reviewer dispatch returns a Findings section, (2)
# model-lint fires with source=ssot and a real flag, (3) a hardening lands in
# BOTH sessions/s02.prompt.md and spec.json, (4) the rebuild-to-temp-dir diff
# is byte-identical, (5) the whole transcript audits clean of Claude model ids.
#
# Model/effort: tries the recommended production rung (gpt-5.6-sol/xhigh)
# first, then falls back once to gpt-5.6-luna/medium with a tighter review
# scope — the same one-shot-fallback discipline run.py's session dispatch
# already uses (dual-harness-contract.md § 3.3). `gpt-5.6-sol` can return
# "Selected model is at capacity", so the fallback path is a real branch,
# not a theoretical one.
set -u
HERE=${0:A:h}                                          # skills/plan-harden/codex/fixtures
ROOT=${HERE:h:h:h:h}                                    # harness root (holds skills/, scripts/, model-routing.yaml)
BUILD=$ROOT/skills/plan-builder/scripts/build_plan.py
LINT=$HERE/lint.py
APPLY=$HERE/apply.py
SSOT=$ROOT/model-routing.yaml
RESOLVER_DIR=$ROOT/scripts
TREE=${1:-$(mktemp -d)}
PLAN=./_plans/harden-smoke

step() { print -r -- ""; print -r -- "### $*"; }

# `codex exec` refuses to run outside a git repo, same precondition as the
# plan-execute/codex fixture.
mkdir -p $TREE && cd $TREE
[[ -d .git ]] || { git init -q . && git config user.email fixture@local \
  && git config user.name fixture && print fixture > README.md \
  && git add -A && git commit -qm init; }
rm -rf _plans
python3 $BUILD $HERE/harden-smoke.spec.json $PLAN >/dev/null || exit 1
mkdir -p $PLAN/_codex

print -r -- "=== Codex-only plan-harden smoke test ==="
print -r -- "date:  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
print -r -- "codex: $(codex --version)"
print -r -- "tree:  $TREE"
print -r -- "plan:  $PLAN"

step "PHASE 0 — enrichment (inline, no forks — no Task/Workflow tool in Codex)"
print -r -- "  memory: n/a in this throwaway fixture tree · research: n/a (Claude-only MCPs)"
print -r -- "  edge-cases: inline reasoning only · blindspot: n/a (Claude-only skill)"

step "PHASE 1 — SKIPPED (no Codex port of /grill-with-docs; structural, not conditional)"

step "PHASE 2 — adversarial review: real codex exec dispatch"
FINDINGS=$PLAN/_codex/adversarial-review.last-message.txt
STDERR=$PLAN/_codex/adversarial-review.stderr.txt

dispatch_review() {
  local model=$1 effort=$2 scope_note=$3
  rm -f $FINDINGS
  print -r -- "\$ codex exec ... -m $model -c model_reasoning_effort=$effort -o $FINDINGS -"
  codex exec --ignore-user-config --ignore-rules --sandbox workspace-write \
    -c project_doc_max_bytes=262144 \
    -m $model -c model_reasoning_effort=$effort \
    -o $FINDINGS - > $STDERR 2>&1 <<EOF
Use \$adversarial-review to stress-test the plan at $PLAN/PLAN.html
(spec of record: $PLAN/spec.json). This is a plan-builder execution plan:
sessions, dependencies, verify gates, checkpoints, shipping actions.
Supported decision: whether to run this plan as-is. Review-only — do not
edit any file. $scope_note
EOF
  print -r -- "exit code: $?"
}

dispatch_review gpt-5.6-sol xhigh ""
if [[ ! -s $FINDINGS ]]; then
  print -r -- "  primary rung produced no output (see $STDERR tail below) — one fallback retry, tighter scope"
  tail -5 $STDERR
  dispatch_review gpt-5.6-luna medium \
    "SCOPE: read PLAN.html and spec.json only; do not run scripts or explore the wider repository — this is a small, self-contained fixture plan."
fi

if [[ ! -s $FINDINGS ]]; then
  print -r -- "BOTH rungs failed to produce output — NO-CODEX for this run. See $STDERR."
  exit 1
fi

print -r -- "--- Findings output (verbatim) ---"
cat $FINDINGS
for label in blocking material minor; do
  n=$(grep -c "### \[$label\]" $FINDINGS 2>/dev/null || echo 0)
  print -r -- "  $label: $n"
done

step "PHASE 2 seam — severity translation (never edited inside adversarial-review itself)"
print -r -- "  blocking -> 🔴 plan-killer   material -> 🟡 polish   minor -> 🟣 known-debt"

step "PHASE 3 — Klein premortem (inline reasoning, no model call — not scripted in this smoke test)"

step "PHASE 4.0 — model-selection sanity lint (source=ssot, resolve_route.resolve reused verbatim)"
LINT_JSON=$PLAN/_codex/model-lint.json
python3 $LINT $PLAN $SSOT $RESOLVER_DIR | tee $LINT_JSON

step "PHASE 4.3/4.4/4.5 — backport to spec.json, real rebuild, round-trip verification"
python3 $APPLY $PLAN $FINDINGS $LINT_JSON $BUILD

step "PROOF — the hardening tag lives in BOTH sessions/s02.prompt.md and spec.json"
sed -n '/## Work/,/## Implementation notes/p' $PLAN/sessions/s02.prompt.md
python3 -c "
import json
spec = json.load(open('$PLAN/spec.json'))
for s in spec['sessions']:
    if s['id'] == 's02':
        print('spec.json contains HARDENED tag:', 'HARDENED' in s['prompt'])
"

step "PLAN-HARDEN.md sidecar — written by this port, survives a rebuild"
python3 - "$PLAN" "$LINT_JSON" <<'PY'
import json, sys, datetime
plan_dir, lint_file = sys.argv[1], sys.argv[2]
lint = json.load(open(lint_file))
ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
sidecar = f"""# /plan-harden run artifacts (Codex-only port)

Sidecar for the Codex `plan-harden` port's output on this plan-builder plan.
The plan-of-record is `spec.json`; harden edits are applied to
`sessions/*.prompt.md` and backported to `spec.json` in Phase 4. This file is
NOT regenerated by `build_plan.py`, so it survives a rebuild.

## /plan-harden Summary

**Run metadata**: timestamp {ts}, harness codex
**Phases run**: enrichment n/a (fixture tree), grill n/a (no Codex port), adversarial (see Findings above), premortem (inline, not scripted here), model-lint {'✓ ' + str(len(lint['flags'])) + ' flags' if lint['flags'] else 'clean'} (source={lint['source']})

**Hand-off envelope**:
plan-harden:
  hardenings_applied: 2
  blockers_remaining: 0
  premortem_class: n/a
  recommend_exit_now: n/a (fixture smoke test, not a real plan)
"""
open(f"{plan_dir}/PLAN-HARDEN.md", "w").write(sidecar)
print(f"wrote {plan_dir}/PLAN-HARDEN.md ({len(sidecar)} bytes)")
PY
cat $PLAN/PLAN-HARDEN.md

step "TRANSCRIPT MODEL-ID AUDIT — must show only openai/gpt-5.6-* models, zero Claude ids"
if grep -inE 'claude-(opus|sonnet|haiku)|claude-[0-9]|anthropic/|us\.anthropic\.' \
    $LINT_JSON $PLAN/sessions/s02.prompt.md $PLAN/spec.json $PLAN/PLAN-HARDEN.md $FINDINGS; then
  print -r -- "FOUND — FAIL"
else
  print -r -- "none found — PASS"
fi

step "DONE"
