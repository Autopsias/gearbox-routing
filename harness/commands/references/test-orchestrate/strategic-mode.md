# Strategic Mode

Strategic mode is triggered when recurring test failures are detected or explicitly requested.

## Triggering Conditions

| Condition | Mode |
|-----------|------|
| --strategic OR --research OR --force-escalate | STRATEGIC |
| TEST_FIX_COUNT >= 3 (auto-escalated) | STRATEGIC |
| Otherwise | TACTICAL (default) |

## Auto-Escalation Detection

Check git history for recurring test fix attempts:
```bash
TEST_FIX_COUNT=$(git log --oneline -20 | grep -iE "fix.*(test|spec|jest|pytest|vitest)" | wc -l | tr -d ' ')
echo "TEST_FIX_COUNT=$TEST_FIX_COUNT"
```

If TEST_FIX_COUNT >= 3:
- Report: "Detected $TEST_FIX_COUNT test fix attempts in recent history. Auto-escalating to strategic mode."
- Set STRATEGIC_MODE=true

## STEP 7: Strategic Analysis

If STRATEGIC_MODE=true:

### 7a. Launch Test Strategy Analyst

```
Task(subagent_type="test-strategy-analyst",
     model="opus",
     description="Analyze recurring test failures",
     prompt="Analyze test failures in this project using Five Whys methodology.

Git history shows $TEST_FIX_COUNT recent test fix attempts.
Current failures: [FAILURE SUMMARY]

Research:
1. Best practices for the detected failure patterns
2. Common pitfalls in pytest/vitest testing
3. Root cause analysis for recurring issues

Provide strategic recommendations for systemic fixes.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"root_causes\": [{\"issue\": \"...\", \"five_whys\": [...], \"recommendation\": \"...\"}],
  \"infrastructure_changes\": [\"...\"],
  \"prevention_mechanisms\": [\"...\"],
  \"priority\": \"P0|P1|P2\",
  \"summary\": \"Brief strategic overview\"
}
DO NOT include verbose analysis or full code examples.")
```

### 7b. After Strategy Analyst Completes

If fixes are recommended, proceed to STEP 8 (agent dispatch).

### 7c. Launch Documentation Generator (optional)

If significant insights were found:
```
Task(subagent_type="test-documentation-generator",
     model="haiku",
     description="Generate test knowledge documentation",
     prompt="Based on the strategic analysis results, generate:
1. Test failure runbook (docs/test-failure-runbook.md)
2. Test strategy summary (docs/test-strategy.md)
3. Pattern-specific knowledge (docs/test-knowledge/)

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"files_created\": [\"docs/test-failure-runbook.md\"],
  \"patterns_documented\": 3,
  \"summary\": \"Created runbook with 5 failure patterns\"
}
DO NOT include file contents in response.")
```
