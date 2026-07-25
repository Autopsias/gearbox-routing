---
name: test-orchestrate
description: "Analyzes test failures and dispatches parallel specialist agents (unit-test-fixer, api-test-fixer, database-test-fixer, e2e-test-fixer) to fix them. Use when you say 'fix tests', 'tests are failing', 'test orchestrator', or need strategic test failure analysis. For the full commit->push->PR->CI-green chain, use /ship-tail instead (which calls this internally as its test-fix step)."
argument-hint: "[test_scope] [--run-first] [--coverage] [--fast] [--strategic] [--research] [--force-escalate] [--no-chain] [--intent=<block>] [--api-only] [--database-only] [--vitest-only] [--pytest-only] [--playwright-only] [--only-category=<unit|integration|e2e|acceptance>] [--loop N] [--loop-delay S] [--fix-single-type]"
allowed-tools: ["Task", "Bash", "Grep", "Read", "LS", "Glob", "SlashCommand", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
disable-model-invocation: false
---

# Test Orchestration Command (v2.0)

Execute this test orchestration procedure for: "$ARGUMENTS"

## TASKLIST INTEGRATION (MANDATORY)

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/tasklist-patterns.md` and follow all steps.

---

## PROGRESS CHECKLIST (copy-paste to track position)

```
[ ] STEP 0    Mode detection + auto-escalation + depth protection
[ ] STEP 0.5  Ralph Loop mode detection (--loop)
[ ] STEP 0.6  Type-level mode detection (--fix-single-type)
[ ] STEP 1    Parse arguments
[ ] STEP 2    Discover cached test results
[ ] STEP 2.5  Test framework intelligence (save markers — MUST)
[ ] STEP 2.6  Discover project context (shared cache)
[ ] STEP 3    Decision logic + early exit
[ ] STEP 4    Run fresh tests (if needed)
[ ] STEP 5    Read test result files
[ ] STEP 5.5  Analysis phase
[ ] STEP 6    Enhanced failure categorization
[ ] STEP 7    Strategic mode (if triggered)
[ ] STEP 7.5-7.6  Conflict detection & test file safety
[ ] STEP 8    Parallel agent dispatch
[ ] STEP 9    Validate fixes
[ ] STEP 10   Intelligent chain invocation
[ ] STEP 11   Report summary
```

---

## ORCHESTRATOR GUARD RAILS

### PROHIBITED (NEVER do directly):
- Direct edits to test files
- Direct edits to source files
- pytest --fix or similar
- git add / git commit
- pip install / uv add
- Modifying test configuration

### ALLOWED (delegation only):
- Task(subagent_type="unit-test-fixer", ...)
- Task(subagent_type="api-test-fixer", ...)
- Task(subagent_type="database-test-fixer", ...)
- Task(subagent_type="e2e-test-fixer", ...)
- Task(subagent_type="type-error-fixer", ...)
- Task(subagent_type="import-error-fixer", ...)
- Read-only bash commands for analysis
- Grep/Glob/Read for investigation

**WHY:** Ensures expert handling by specialists, prevents conflicts, maintains audit trail.

---

## STEP 0: MODE DETECTION + AUTO-ESCALATION + DEPTH PROTECTION

### 0a. Depth Protection (prevent infinite loops)

```bash
echo "SLASH_DEPTH=${SLASH_DEPTH:-0}"
```

If SLASH_DEPTH >= 3: Report and EXIT immediately.
Otherwise: `export SLASH_DEPTH=$((${SLASH_DEPTH:-0} + 1))`

### 0b. Parse Strategic Flags

Check "$ARGUMENTS" for: `--strategic`, `--research`, `--force-escalate`
If ANY present -> STRATEGIC_MODE=true

### 0c. Auto-Escalation Detection

```bash
TEST_FIX_COUNT=$(git log --oneline -20 | grep -iE "fix.*(test|spec|jest|pytest|vitest)" | wc -l | tr -d ' ')
echo "TEST_FIX_COUNT=$TEST_FIX_COUNT"
```

If TEST_FIX_COUNT >= 3: Auto-escalate to STRATEGIC_MODE=true

### 0d. Mode Decision

| Condition | Mode |
|-----------|------|
| --strategic OR --research OR --force-escalate | STRATEGIC |
| TEST_FIX_COUNT >= 3 | STRATEGIC (auto-escalated) |
| Otherwise | TACTICAL (default) |

Report the mode: "Operating in [TACTICAL/STRATEGIC] mode."

---

## STEP 0.5: Ralph Loop Mode Detection

**If `--loop` is present:** Read `~/.claude/commands/references/test-orchestrate/troubleshooting.md` for Ralph Loop launch instructions, then EXIT.

---

## STEP 0.6: Type-Level Mode Detection (--fix-single-type)

**If `--fix-single-type` is present:** Read `~/.claude/commands/references/test-orchestrate/troubleshooting.md` for type-level mode execution, then EXIT after fixing one type.

**Otherwise:** Proceed to STEP 1 (all-types mode).

---

## STEP 1: Parse Arguments

Check "$ARGUMENTS" for these flags:
- `--run-first` = Ignore cached results, run fresh tests
- `--pytest-only` = Focus on pytest (backend) only
- `--vitest-only` = Focus on Vitest (frontend) only
- `--playwright-only` = Focus on Playwright (E2E) only
- `--coverage` = Include coverage analysis
- `--fast` = Skip slow tests
- `--no-chain` = Disable chain invocation after fixes
- `--only-category=<category>` = Target specific test category
- `--intent=<block>` = the change's INTENT (what it was meant to do, incl. deliberate
  choices), passed by the ship-tail / Cluster C. Forward it VERBATIM into every fixer-agent
  prompt's `## Change intent` heading so fixers classify deliberate-choice (`intent_touched:
  true` → ask-user) vs mistake (auto-fix). See `references/test-orchestrate/agent-dispatch-rules.md`
  and `~/.claude/commands/references/shared/intent-into-review.md`.

**Intent-into-review engagement (deterministic log).** Emit exactly one of:
```bash
if [[ "$ARGUMENTS" =~ "--intent=" ]]; then
    echo "[intent-into-review] intent_block=present source=cluster-c-fixer findings_classified=${N:-0}"
else
    echo "[intent-into-review] intent_block=absent source=cluster-c-fixer"
fi
```
A post-run `grep -c '\[intent-into-review\] intent_block=present'` over this output proves
the intent path engaged (count>0); count==0 with an intent supplied means the wiring is a no-op.

**Parse --only-category for targeted test execution:**
```bash
if [[ "$ARGUMENTS" =~ "--only-category="([a-zA-Z]+) ]]; then
    TARGET_CATEGORY="${BASH_REMATCH[1]}"
    echo "Targeting only '$TARGET_CATEGORY' tests"
fi
```

Valid categories: `unit`, `integration`, `e2e`, `acceptance`, `api`, `database`

---

## STEP 2: Discover Cached Test Results

Run these commands ONE AT A TIME:

**2a. Project info:**
```bash
echo "Project: $(basename $PWD) | Branch: $(git branch --show-current) | Root: $PWD"
```

**2b. Check if pytest results exist:**
```bash
test -f "test-results/pytest/junit.xml" && echo "PYTEST_EXISTS=yes" || echo "PYTEST_EXISTS=no"
```

**2c. If pytest results exist, get stats:**
```bash
echo "PYTEST_AGE=$(($(date +%s) - $(stat -f %m test-results/pytest/junit.xml 2>/dev/null || stat -c %Y test-results/pytest/junit.xml 2>/dev/null)))s"
```
```bash
echo "PYTEST_TESTS=$(grep -o 'tests="[0-9]*"' test-results/pytest/junit.xml | head -1 | grep -o '[0-9]*')"
```
```bash
echo "PYTEST_FAILURES=$(grep -o 'failures="[0-9]*"' test-results/pytest/junit.xml | head -1 | grep -o '[0-9]*')"
```

**2d. Check Vitest results:**
```bash
test -f "test-results/vitest/results.json" && echo "VITEST_EXISTS=yes" || echo "VITEST_EXISTS=no"
```

**2e. Check Playwright results:**
```bash
if test -f "test-results/playwright/results.json"; then
    echo "PLAYWRIGHT_EXISTS=yes"
    echo "PLAYWRIGHT_LOCATION=test-results/playwright/results.json"
elif test -f "apps/web/test-results/playwright/results.json"; then
    echo "PLAYWRIGHT_EXISTS=yes"
    echo "PLAYWRIGHT_LOCATION=apps/web/test-results/playwright/results.json"
elif test -f "apps/web/.playwright/results.json"; then
    echo "PLAYWRIGHT_EXISTS=yes"
    echo "PLAYWRIGHT_LOCATION=apps/web/.playwright/results.json"
else
    echo "PLAYWRIGHT_EXISTS=no"
fi
```

---

## STEP 2.5: Test Framework Intelligence

Detect test framework configuration:

```bash
grep -A 20 "\[tool.pytest" pyproject.toml 2>/dev/null | head -25 || echo "No pytest config in pyproject.toml"
```
```bash
grep -rh "pytest.mark\." tests/ 2>/dev/null | sed 's/.*@pytest.mark.\([a-zA-Z_]*\).*/\1/' | sort -u | head -10
```
```bash
grep -l "@pytest.mark.slow" tests/**/*.py 2>/dev/null | wc -l | xargs echo "Slow tests:"
```

**MUST: Save detected markers and configuration for agent context — skipping this silently degrades agent fix quality.**

---

## STEP 2.6: Discover Project Context (SHARED CACHE - Token Efficient)

**Token Savings**: Using shared discovery cache saves ~14K tokens (2K per agent x 7 agents).

```bash
echo "=== Loading Shared Project Context ==="

if [[ -f "$HOME/.claude/scripts/shared-discovery.sh" ]]; then
    source "$HOME/.claude/scripts/shared-discovery.sh"
    discover_project_context
else
    echo "Shared discovery not found, using inline discovery"
    PROJECT_CONTEXT=""
    [ -f "CLAUDE.md" ] && PROJECT_CONTEXT="Read CLAUDE.md for project conventions. "
    [ -d ".claude/rules" ] && PROJECT_CONTEXT+="Check .claude/rules/ for patterns. "
    PROJECT_TYPE=""
    [ -f "pyproject.toml" ] && PROJECT_TYPE="python"
    [ -f "package.json" ] && PROJECT_TYPE="${PROJECT_TYPE:+$PROJECT_TYPE+}node"
    SHARED_CONTEXT="$PROJECT_CONTEXT"
fi

echo "PROJECT_TYPE=$PROJECT_TYPE"
echo "VALIDATION_CMD=${VALIDATION_CMD:-pnpm prepush}"
echo "TEST_FRAMEWORK=${TEST_FRAMEWORK:-pytest}"
```

**CRITICAL**: Pass `$SHARED_CONTEXT` to ALL agent prompts instead of asking each agent to discover.

---

## STEP 3: Decision Logic + Early Exit

| Condition | Action |
|-----------|--------|
| `--run-first` flag present | Go to STEP 4 (run fresh tests) |
| PYTEST_EXISTS=yes AND AGE < 900s AND FAILURES > 0 | Go to STEP 5 (read results) |
| PYTEST_EXISTS=yes AND AGE < 900s AND FAILURES = 0 | **EARLY EXIT** (see below) |
| PYTEST_EXISTS=no OR AGE >= 900s | Go to STEP 4 (run fresh tests) |

### EARLY EXIT OPTIMIZATION (Token Savings: ~80%)

If ALL tests are passing from cached results: report "All tests passing", output JSON summary, go to STEP 10 or EXIT if --no-chain.

**DO NOT** run discovery, dispatch agents, run strategic analysis, or generate documentation if no failures.

---

## STEP 4: Run Fresh Tests (if needed)

**4a. Run pytest:**
```bash
mkdir -p test-results/pytest && cd apps/api && uv run pytest -v --tb=short --junitxml=../../test-results/pytest/junit.xml 2>&1 | tail -40
```

**4b. Run Vitest (if config exists):**
```bash
test -f "apps/web/vitest.config.ts" && mkdir -p test-results/vitest && cd apps/web && npx vitest run --reporter=json --outputFile=../../test-results/vitest/results.json 2>&1 | tail -25
```

**4c. Run Playwright (if config exists):**
```bash
if test -f "playwright.config.ts"; then
    mkdir -p test-results/playwright && npx playwright test --reporter=json 2>&1 | tee test-results/playwright/results.json | tail -25
elif test -f "apps/web/playwright.config.ts"; then
    mkdir -p apps/web/test-results/playwright && cd apps/web && npx playwright test --reporter=json 2>&1 | tee test-results/playwright/results.json | tail -25
fi
```

**4d. If --coverage flag present:**
```bash
mkdir -p test-results/pytest && cd apps/api && uv run pytest --cov=app --cov-report=xml:../../test-results/pytest/coverage.xml --cov-report=term-missing 2>&1 | tail -30
```

---

## STEP 5: Read Test Result Files

Use the Read tool:

**For pytest:** `Read(file_path="test-results/pytest/junit.xml")`
- Look for `<testcase>` with `<failure>` or `<error>` children
- Extract: test name, classname (file path), failure message, **full stack trace**

**For Vitest:** `Read(file_path="test-results/vitest/results.json")`
- Look for `"status": "failed"` entries

**For Playwright:** `Read(file_path="${PLAYWRIGHT_LOCATION}")`
- Look for specs where `"ok": false` or `"status": "unexpected"`

---

## STEP 5.5: ANALYSIS PHASE

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/failure-categorization.md` and follow the Analysis Phase section (test isolation, flakiness detection, coverage analysis).

---

## STEP 6: Enhanced Failure Categorization

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/failure-categorization.md` and follow all regex-based categorization rules and prioritization steps.

---

## STEP 7: STRATEGIC MODE (if triggered)

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/strategic-mode.md` and follow all steps.

If TACTICAL mode, skip to STEP 7.5.

---

## STEP 7.5-7.6: Conflict Detection & Test File Safety

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/agent-dispatch-rules.md` and follow the conflict detection and test file modification safety sections.

---

## STEP 8: PARALLEL AGENT DISPATCH

### CRITICAL: Launch ALL agents in ONE response with multiple Task calls.

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/agent-dispatch-rules.md` and follow the enhanced agent context template, dispatch examples, and model strategy.

---

## STEP 9: Validate Fixes

After agents complete:

```bash
cd apps/api && uv run pytest -v --tb=short --junitxml=../../test-results/pytest/junit.xml 2>&1 | tail -40
```

Check results:
- If ALL tests pass -> Go to STEP 10
- If SOME tests still fail -> Report remaining failures, suggest --strategic

---

## STEP 10: INTELLIGENT CHAIN INVOCATION

**Instructions:** Read `~/.claude/commands/references/test-orchestrate/troubleshooting.md` for chain invocation rules (depth check, --no-chain, chain action).

---

## STEP 11: Report Summary

Emit a `findings-contract/v1` report (see the Findings output section below and
`~/.claude/commands/references/shared/findings-contract.md`). Compute `decision`:
any unresolved blocking/error -> FAIL; else any `ask-user` -> CONCERNS; else PASS
(only once every auto-fix is `status: fixed` and `post_fix_verified: true`).

Alongside the report, include:
- Mode: TACTICAL or STRATEGIC
- Initial failure count by type
- Agents dispatched with priorities
- Strategic insights (if applicable)
- Current `decision` (PASS/CONCERNS/FAIL) and pass/fail status
- Coverage status (if --coverage)
- Chain invocation result
- Remaining issues and recommendations

---

## Quick Reference

| Command | Effect |
|---------|--------|
| `/test_orchestrate` | Use cached results if fresh (<15 min) |
| `/test_orchestrate --run-first` | Run tests fresh, ignore cache |
| `/test_orchestrate --pytest-only` | Only pytest failures |
| `/test_orchestrate --strategic` | Force strategic mode (research + analysis) |
| `/test_orchestrate --coverage` | Include coverage analysis |
| `/test_orchestrate --no-chain` | Don't auto-invoke /commit_orchestrate |

## VS Code Integration

pytest.ini must have: `addopts = --junitxml=test-results/pytest/junit.xml`

Then: Run tests in VS Code -> `/test_orchestrate` reads cached results -> Fixes applied

---

## Agent Quick Reference

| Failure Pattern | Agent | Model | JSON Output |
|-----------------|-------|-------|-------------|
| Assertions, mocks, fixtures | unit-test-fixer | sonnet | Required |
| HTTP, API contracts, endpoints | api-test-fixer | sonnet | Required |
| Database, SQL, connections | database-test-fixer | sonnet | Required |
| Selectors, timeouts, E2E | e2e-test-fixer | sonnet | Required |
| Type annotations, mypy | type-error-fixer | sonnet | Required |
| Imports, modules, paths | import-error-fixer | haiku | Required |
| Strategic analysis | test-strategy-analyst | opus | Required |
| Documentation | test-documentation-generator | haiku | Required |

## Findings output: the Uniform Findings Contract

**This orchestrator emits the shared Uniform Findings Contract.**
Read `~/.claude/commands/references/shared/findings-contract.md` — it is the single,
canonical findings envelope (action `no-op | auto-fix | ask-user` + severity
`error | warning | info` + the finding-vs-suggestion split + lifecycle fields), and it
maps to epic-dev's PASS/CONCERNS/FAIL gate. This supersedes the old flat
`fixed|partial|failed` status.

**Orchestrator report (what `/test-orchestrate` returns):** a `findings-contract/v1`
report with `source: "test-orchestrate"` and a computed `decision` (PASS only when every
auto-fix is `status: fixed` AND `post_fix_verified: true`):

```json
{
  "schema": "findings-contract/v1",
  "source": "test-orchestrate",
  "decision": "PASS|CONCERNS|FAIL",
  "post_fix_verified": true,
  "findings": [
    {"id": "r1", "severity": "error", "file": "tests/test_auth.py", "line": 42,
     "description": "Mock contract mismatch — assertion never exercises real service.",
     "action": "auto-fix", "suggestion": "Use mock_factories.py SearchResponse factory.",
     "status": "fixed", "blocking": false, "source": "agent",
     "evidence": "junit.xml testcase test_search_contract", "intent_touched": false,
     "post_fix_verified": true}
  ],
  "summary": "Fixed mock configuration and assertion order"
}
```

**Fixer agents** may still return the legacy distilled status
(`{"status": "fixed|partial|failed", "tests_fixed": N, "files_modified": [...], "summary": "..."}`);
the orchestrator runs each through the contract's **legacy adapter** (see the contract doc)
before rolling up the `decision`. A missing `action`/`status` is treated as `ask-user`+blocking,
never auto-fix. New agents SHOULD emit the canonical envelope directly.

**DO NOT return:** Full file contents, verbose explanations, step-by-step execution logs.
This reduces token usage by 80-90% per agent response.

---

EXECUTE NOW. Start with Step 0a (depth check).
