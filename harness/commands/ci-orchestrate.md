---
description: "Analyzes CI/CD pipeline failures, categorizes issues (lint, type, test, build), and dispatches parallel specialist agents to fix them. Use when you say 'fix CI', 'CI is failing', 'pipeline broken', 'fix GitHub Actions'."
argument-hint: "[issue] [--fix-all] [--strategic] [--research] [--docs] [--force-escalate] [--check-actions] [--quality-gates] [--performance] [--intent=<block>] [--only-stage=<stage>] [--loop N] [--loop-delay S] [--fix-single-category]"
allowed-tools: ["Task", "Bash", "Grep", "Read", "LS", "Glob", "SlashCommand", "WebSearch", "WebFetch", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

## TASKLIST INTEGRATION (MANDATORY)

Use TaskList tools to track CI category fixes and provide visibility into orchestration progress.

**Workflow:** After mode detection, check `TaskList()` for existing state. After category detection, create priority-ordered tasks (P0: imports/types, P1: linting/tests, P2: security) with `blockedBy` dependencies. When dispatching agents, create agent-level tasks and update status on completion.

**Ralph Loop Bridge:** When using `--loop`, persist task state to `.claude/state/task-bridge.json` before exiting and restore on next session start.

**Progress Summary:** Always include a TaskList summary table in output showing task ID, subject, and status.

---

## TWO-MODE ORCHESTRATION

### Mode 1: TACTICAL (Default)
- Fix immediate CI failures fast
- Delegate to specialist fixers
- Parallel execution for speed

### Mode 2: STRATEGIC (Flag-triggered or Auto-escalated)

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/strategic-mode.md` and follow all steps.

**Triggers:** `--strategic`, `--research`, `--docs`, `--force-escalate` flags, or auto-detect phrases ("comprehensive", "strategic", "root cause"), or auto-escalate after 3+ CI fix commits on branch.

### Mode 3: TARGETED STAGE EXECUTION (--only-stage)

Skip earlier CI stages when debugging a specific stage failure. Detects CI platform (GitHub Actions/GitLab/Azure), reads workflow file, and triggers targeted run via `workflow_dispatch` with `skip_to_stage` input. Once fixed, remove flag to run full pipeline.

---

## CRITICAL ORCHESTRATION CONSTRAINTS

Canonical wording: `Read ~/.claude/commands/references/shared/pure-orchestrator-invariant.md`
(shared across the pure-orchestrator commands — same invariant, no per-command drift).
No exceptions apply to this command.

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/troubleshooting.md` for full guard rails, prohibited actions, and delegation requirements.

You must now execute the following CI/CD orchestration procedure for: "$ARGUMENTS"

## STEP 0: MODE DETECTION & AUTO-ESCALATION

**STEP 0.1: Parse Mode Flags**
```bash
STRATEGIC_MODE=false
RESEARCH_ONLY=false
DOCS_ONLY=false
TARGET_STAGE="all"

if [[ "$ARGUMENTS" =~ "--strategic" ]] || [[ "$ARGUMENTS" =~ "--force-escalate" ]]; then
    STRATEGIC_MODE=true
fi
if [[ "$ARGUMENTS" =~ "--research" ]]; then
    RESEARCH_ONLY=true
    STRATEGIC_MODE=true
fi
if [[ "$ARGUMENTS" =~ "--docs" ]]; then
    DOCS_ONLY=true
fi
if [[ "$ARGUMENTS" =~ "--only-stage="([a-z]+) ]]; then
    TARGET_STAGE="${BASH_REMATCH[1]}"
fi
if [[ "$ARGUMENTS" =~ (comprehensive|strategic|root.cause|analyze|review|recurring|systemic) ]]; then
    STRATEGIC_MODE=true
fi
```

**STEP 0.1.5: Execute Targeted Stage (if --only-stage specified)**
If `TARGET_STAGE != "all"`: detect CI platform from workflow files, check for `skip_to_stage` input support, trigger via `gh workflow run` with `-f skip_to_stage="$TARGET_STAGE"`, then exit.

**STEP 0.1.6: Ralph Loop Mode Detection**

If `--loop` is present, launch the Ralph Loop runner script in background:

```
IF "$ARGUMENTS" contains "--loop":
  loop_max = extract_number_after("--loop", default=10)
  loop_delay = extract_number_after("--loop-delay", default=5)
  inner_flags = "$ARGUMENTS" without "--loop" and "--loop-delay"

  IF "{inner_flags}" contains "--strategic":
    timeout_minutes = 20, model = "opus"
  ELSE:
    timeout_minutes = 10, model = "sonnet"

  Run via Bash(run_in_background=true, timeout=600000):
  ```bash
  nohup bash "$HOME/.claude/scripts/ralph-loop-runner.sh" \
    --command "ci-orchestrate" \
    --args "{inner_flags} --fix-single-category" \
    --max-iterations {loop_max} \
    --delay {loop_delay} \
    --timeout {timeout_minutes} \
    --model {model} \
    --completion-regex "All CI checks passing|CI_STATUS.*passing|CI pipeline.*PASS" \
    > /tmp/ralph-loop-ci-orchestrate.log 2>&1 &
  echo "PID=$!"
  ```

  Output PID, log path, monitor/stop commands. EXIT.
ELSE:
  PROCEED TO STEP 0.2
END IF
```

**STEP 0.2: Check for Auto-Escalation**

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/strategic-mode.md` for auto-escalation check and all strategic phases.

**STEP 0.3: Execute Strategic Mode (if triggered)**

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/strategic-mode.md` and follow all strategic phases (Research, Infrastructure, Documentation).

---

## STEP 0.4: Category-Level Mode Detection

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/category-detection.md` and follow all steps for `--fix-single-category` mode or proceed to all-categories mode.

---

## DELEGATE IMMEDIATELY: CI Pipeline Analysis & Specialist Dispatch

**STEP 1: Parse Arguments**
Extract CI issue description, `--check-actions`, `--fix-all`, `--quality-gates`, `--performance` flags.
Also parse `--intent=<block>` = the change's INTENT (what it was meant to do, incl. deliberate
choices), passed by the ship-tail CI-loop on red. Forward it VERBATIM into every CI-specialist
agent prompt's `## Change intent` heading so fixers classify deliberate-choice (`intent_touched:
true` → ask-user) vs mistake (auto-fix). See `references/ci-orchestrate/agent-routing.md` and
`~/.claude/commands/references/shared/intent-into-review.md`.

**Intent-into-review engagement (deterministic log).** Emit exactly one of:
```bash
if [[ "$ARGUMENTS" =~ "--intent=" ]]; then
    echo "[intent-into-review] intent_block=present source=cluster-c-fixer findings_classified=${N:-0}"
else
    echo "[intent-into-review] intent_block=absent source=cluster-c-fixer"
fi
```
A post-run `grep -c '\[intent-into-review\] intent_block=present'` proves the path engaged (count>0).

**STEP 2: CI Failure Analysis**
Use read-only diagnostic tools: check GitHub Actions status, examine recent CI results, identify failing gates, categorize failures.

**STEP 3: Discover Project Context (SHARED CACHE)**

```bash
if [[ -f "$HOME/.claude/scripts/shared-discovery.sh" ]]; then
    source "$HOME/.claude/scripts/shared-discovery.sh"
    discover_project_context
else
    PROJECT_CONTEXT=""
    [ -f "CLAUDE.md" ] && PROJECT_CONTEXT="Read CLAUDE.md for project conventions. "
    [ -d ".claude/rules" ] && PROJECT_CONTEXT+="Check .claude/rules/ for patterns. "
    PROJECT_TYPE=""
    [ -f "pyproject.toml" ] && PROJECT_TYPE="python"
    [ -f "package.json" ] && PROJECT_TYPE="${PROJECT_TYPE:+$PROJECT_TYPE+}node"
    SHARED_CONTEXT="$PROJECT_CONTEXT"
fi
```

Pass `$SHARED_CONTEXT` to ALL agent prompts instead of each agent discovering independently.

**STEP 4-5: Failure Detection, Agent Mapping & Parallel Dispatch**

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/agent-routing.md` and follow all steps for failure type detection, agent mapping, work package analysis, parallel dispatch, conflict avoidance, and refactoring safety gate.

CRITICAL: Launch multiple Task agents simultaneously in a SINGLE response. NEVER execute Task calls sequentially.

**STEP 6-7: Verification & Result Collection**

**Instructions:** Read `~/.claude/commands/references/ci-orchestrate/troubleshooting.md` for CI pipeline verification, result validation, and chain invocation steps.

---

## Agent Quick Reference

| Failure Type | Agent | Model |
|--------------|-------|-------|
| Strategic research | ci-strategy-analyst | opus |
| Root cause analysis | digdeep | opus |
| Infrastructure | ci-infrastructure-builder | sonnet |
| Documentation | ci-documentation-generator | haiku |
| Linting/formatting | linting-fixer | haiku |
| Type errors | type-error-fixer | sonnet |
| Import errors | import-error-fixer | haiku |
| Unit tests | unit-test-fixer | sonnet |
| API tests | api-test-fixer | sonnet |
| Database tests | database-test-fixer | sonnet |
| E2E tests | e2e-test-fixer | sonnet |
| Security | security-scanner | sonnet |

## Findings output: the Uniform Findings Contract

**This orchestrator emits the shared Uniform Findings Contract.**
`Read ~/.claude/commands/references/shared/findings-contract.md` — the single canonical
findings envelope (action `no-op | auto-fix | ask-user` + severity `error | warning | info`
+ the finding-vs-suggestion split + lifecycle fields), mapped to epic-dev's
PASS/CONCERNS/FAIL. This supersedes the old flat `fixed|partial|failed` status.

`/ci-orchestrate` returns a `findings-contract/v1` report with `source: "ci-orchestrate"`
and a computed `decision` (PASS only when every auto-fix is `status: fixed` AND
`post_fix_verified: true` — i.e. CI was re-run green):

```json
{
  "schema": "findings-contract/v1",
  "source": "ci-orchestrate",
  "decision": "PASS|CONCERNS|FAIL",
  "post_fix_verified": true,
  "findings": [
    {"id": "r1", "severity": "error", "file": "apps/api/app/x.py", "line": 0,
     "description": "mypy: incompatible return type", "action": "auto-fix",
     "suggestion": "annotate return as Optional[str]", "status": "fixed",
     "blocking": false, "source": "agent", "evidence": "ci log: error: ... [return-value]",
     "intent_touched": false, "post_fix_verified": true}
  ],
  "summary": "Fixed 1 type error; CI re-run green"
}
```

Fixer agents MAY still return the legacy distilled status (`status`, `issues_fixed`,
`files_modified`, `summary` — including the `conflict` variant); the orchestrator runs each
through the contract's **legacy adapter** before rolling up. A missing `action`/`status` is
treated as `ask-user`+blocking, NEVER auto-fix. New agents SHOULD emit the canonical
envelope. This reduces token usage by 80-90%.

---

EXECUTE NOW. Start with STEP 0 (mode detection).
