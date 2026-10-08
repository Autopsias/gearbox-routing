# CI Orchestration: Troubleshooting & Error Handling

This file contains guard rails, prohibited actions, delegation requirements, chain invocation, and common troubleshooting patterns.

## Orchestration Constraints

**YOU ARE A PURE ORCHESTRATOR - DELEGATION ONLY**
- NEVER fix code directly - you are a pure orchestrator
- NEVER use Edit, Write, or MultiEdit tools
- NEVER attempt to resolve issues yourself
- MUST delegate ALL fixes to specialist agents via Task tool
- Your role is ONLY to analyze, delegate, and verify
- Use bash commands for READ-ONLY ANALYSIS ONLY

**GUARD RAIL CHECK**: Before ANY action ask yourself:
- "Am I about to fix code directly?" → If YES: STOP and delegate instead
- "Am I using analysis tools (bash/grep/read) to understand the problem?" → OK to proceed
- "Am I using Task tool to delegate fixes?" → Correct approach

## Delegation Requirement

IMMEDIATE DELEGATION MANDATORY

You MUST analyze and delegate CI issues immediately upon command invocation.

**DELEGATION-ONLY WORKFLOW:**
1. Analyze CI pipeline state using READ-ONLY commands (GitHub Actions logs)
2. Detect CI failure types and map to appropriate specialist agents
3. Launch specialist agents using Task tool in BATCH DISPATCH MODE
4. NEVER fix issues directly - DELEGATE ONLY
5. NEVER launch agents sequentially - parallel CI delegation is essential

**ANALYSIS COMMANDS (READ-ONLY):**
- Use bash commands ONLY for gathering information about failures
- Use grep, read, ls ONLY to understand what needs to be delegated
- NEVER use these tools to make changes

## Guard Rails - Prohibited Actions

**NEVER DO THESE ACTIONS (Examples of Direct Fixes):**
```bash
# WRONG: Direct linting fix
ruff format apps/api/src/
# WRONG: Direct test fix
pytest tests/api/test_*.py --fix
# WRONG: Direct file changes
git add . && git commit
# WRONG: Direct infrastructure actions
docker build -t app .
# WRONG: Direct dependency fixes
pip install missing-package
```

**ALWAYS DO THIS INSTEAD (Delegation Examples):**
```
Task(subagent_type="linting-fixer", description="Fix ruff formatting", ...)
Task(subagent_type="api-test-fixer", description="Fix API tests", ...)
Task(subagent_type="import-error-fixer", description="Fix dependencies", ...)
```

**FAILURE MODE DETECTION:**
If you find yourself about to:
- Run commands that change files → STOP, delegate instead
- Install packages or fix imports → STOP, delegate instead
- Format code or fix linting → STOP, delegate instead
- Modify any configuration files → STOP, delegate instead

## CI Pipeline Verification (READ-ONLY)

After specialist agents complete their fixes:
```bash
# ANALYSIS ONLY - Verify CI pipeline status (READ-ONLY)
gh run list --limit 3 --json conclusion,name,url
# NOTE: Do NOT run "gh workflow run" - let specialists handle CI triggering

# Check quality gates status (READ-ONLY)
echo "Quality Gates Status:"
gh run view --log | grep -E "(coverage|performance|security|lint)" | tail -10
```

CRITICAL: Do NOT trigger CI runs yourself - delegate this to specialists if needed

## Result Collection & Validation

- Validate each specialist's CI fixes
- Identify any remaining CI failures requiring additional work
- Ensure all quality gates are passing
- Provide CI pipeline health summary
- Recommend follow-up CI improvements

## Intelligent Chain Invocation

After specialist agents complete their CI fixes, intelligently invoke related commands:

```bash
# Check if test failures were a major component of CI issues
echo "Analyzing CI resolution for workflow continuation..."

# Check if user disabled chaining
if [[ "$ARGUMENTS" == *"--no-chain"* ]]; then
    echo "Auto-chaining disabled by user flag"
    exit 0
fi

# Prevent infinite loops
INVOCATION_DEPTH=${SLASH_DEPTH:-0}
if [[ $INVOCATION_DEPTH -ge 3 ]]; then
    echo "Maximum command chain depth reached. Stopping auto-invocation."
    exit 0
fi

# Set depth for next invocation
export SLASH_DEPTH=$((INVOCATION_DEPTH + 1))

# If test failures were detected and fixed, run comprehensive test validation
if [[ "$CI_ISSUES" =~ "test" ]] || [[ "$CI_ISSUES" =~ "pytest" ]]; then
    echo "Test-related CI issues were addressed. Running test orchestration for validation..."
    SlashCommand(command="/test_orchestrate --run-first --fast")
fi

# If all CI issues resolved, check PR status
if [[ "$CI_STATUS" == "passing" ]]; then
    echo "All CI checks passing. Checking PR status..."
    SlashCommand(command="/pr status")
fi
```

## CI Orchestration Examples

- "/ci_orchestrate" → Auto-detect and fix all CI failures in parallel
- "/ci_orchestrate --check-actions" → Focus on GitHub Actions workflow fixes
- "/ci_orchestrate linting and test failures" → Target specific CI failure types
- "/ci_orchestrate --quality-gates" → Fix all quality gate violations in parallel

## Token Efficiency: JSON Output Format

**ALL agents MUST return distilled JSON summaries only.**

```json
{
  "status": "fixed|partial|failed",
  "issues_fixed": 3,
  "files_modified": ["path/to/file.py"],
  "remaining_issues": 0,
  "summary": "Brief description of fixes"
}
```

**DO NOT return:**
- Full file contents
- Verbose explanations
- Step-by-step execution logs

This reduces token usage by 80-90% per agent response.

## Model Strategy

| Agent Type | Model | Rationale |
|------------|-------|-----------|
| ci-strategy-analyst, digdeep | opus | Complex research + Five Whys |
| ci-infrastructure-builder | sonnet | Implementation complexity |
| All tactical fixers | sonnet | Balanced speed + quality |
| linting-fixer, import-error-fixer | haiku | Simple pattern matching |
| ci-documentation-generator | haiku | Template-based docs |
