# CI Orchestration: Strategic Mode

This file contains the full strategic mode workflow including research, infrastructure building, and documentation generation phases.

## Mode Triggers

- `--strategic` flag: Full research + infrastructure + docs
- `--research` flag: Research best practices only
- `--docs` flag: Generate runbook/strategy docs only
- `--force-escalate` flag: Force strategic mode regardless of history
- Auto-detect phrases: "comprehensive", "strategic", "root cause", "analyze", "review"
- Auto-escalate: After 3+ failures on same branch (checks git history)

## Auto-Escalation Check

Analyze git history for recurring CI fix attempts:
```bash
# Count recent "fix CI" commits on current branch
BRANCH=$(git branch --show-current)
CI_FIX_COUNT=$(git log --oneline -20 | grep -iE "fix.*(ci|test|lint|type)" | wc -l | tr -d ' ')

echo "CI fix commits in last 20: $CI_FIX_COUNT"

# Auto-escalate if 3+ CI fix attempts detected
if [[ $CI_FIX_COUNT -ge 3 ]]; then
    echo "Detected $CI_FIX_COUNT CI fix attempts. AUTO-ESCALATING to strategic mode..."
    echo "   Breaking the fix-push-fail cycle requires root cause analysis."
    STRATEGIC_MODE=true
fi
```

## Strategic Phase 1: Research & Analysis (PARALLEL)

Launch research agents simultaneously:

```
### NEXT_ACTIONS (PARALLEL) ###
Execute these simultaneously:
1. Task(subagent_type="ci-strategy-analyst", description="Research CI best practices", prompt="...")
2. Task(subagent_type="digdeep", description="Root cause analysis", prompt="...")

After ALL complete: Synthesize findings before proceeding
###
```

### Agent Prompts

**For ci-strategy-analyst (model="opus"):**
```
Task(subagent_type="ci-strategy-analyst",
     model="opus",
     description="Research CI best practices",
     prompt="Analyze CI/CD patterns for this project. The user is experiencing recurring CI failures.

Context: \"$ARGUMENTS\"

Your tasks:
1. Research best practices for: Python/FastAPI + React/TypeScript + GitHub Actions + pytest-xdist
2. Analyze git history for recurring \"fix CI\" patterns
3. Apply Five Whys to top 3 failure patterns
4. Produce prioritized, actionable recommendations

Focus on SYSTEMIC issues, not symptoms. Think hard about root causes.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"root_causes\": [{\"issue\": \"...\", \"five_whys\": [...], \"fix\": \"...\"}],
  \"best_practices\": [\"...\"],
  \"infrastructure_recommendations\": [\"...\"],
  \"priority\": \"P0|P1|P2\",
  \"summary\": \"Brief strategic overview\"
}
DO NOT include verbose analysis.")
```

**For digdeep (model="opus"):**
```
Task(subagent_type="digdeep",
     model="opus",
     description="Root cause analysis",
     prompt="Perform Five Whys root cause analysis on the CI failures.

Context: \"$ARGUMENTS\"

Analyze:
1. What are the recurring CI failure patterns?
2. Why do these failures keep happening despite fixes?
3. What systemic issues allow these failures to recur?
4. What structural changes would prevent them?

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"failure_patterns\": [\"...\"],
  \"five_whys_analysis\": [{\"why1\": \"...\", \"why2\": \"...\", \"root_cause\": \"...\"}],
  \"structural_fixes\": [\"...\"],
  \"prevention_strategy\": \"...\",
  \"summary\": \"Brief root cause overview\"
}
DO NOT include verbose analysis or full file contents.")
```

## Strategic Phase 2: Infrastructure (if --strategic, not --research)

After research completes, launch infrastructure builder:

```
Task(subagent_type="ci-infrastructure-builder",
     model="sonnet",
     description="Create CI infrastructure",
     prompt="Based on the strategic analysis findings, create necessary CI infrastructure:

1. Create reusable GitHub Actions if cleanup/isolation needed
2. Update pytest.ini/pyproject.toml for reliability (timeouts, reruns)
3. Update CI workflow files if needed
4. Add any beneficial plugins/dependencies

Only create infrastructure that addresses identified root causes.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"files_created\": [\"...\"],
  \"files_modified\": [\"...\"],
  \"dependencies_added\": [\"...\"],
  \"summary\": \"Brief infrastructure changes\"
}
DO NOT include full file contents.")
```

## Strategic Phase 3: Documentation (if --strategic or --docs)

Generate documentation for team reference:

```
Task(subagent_type="ci-documentation-generator",
     model="haiku",
     description="Generate CI docs",
     prompt="Create/update CI documentation based on analysis and infrastructure changes:

1. Update docs/ci-failure-runbook.md with new failure patterns
2. Update docs/ci-strategy.md with strategic improvements
3. Store learnings in docs/ci-knowledge/ for future reference

Document what was found, what was fixed, and how to prevent recurrence.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  \"files_created\": [\"...\"],
  \"files_updated\": [\"...\"],
  \"patterns_documented\": 3,
  \"summary\": \"Brief documentation changes\"
}
DO NOT include file contents.")
```

## Phase Flow Control

- IF RESEARCH_ONLY is true: Stop after Phase 1 (research only, no fixes)
- IF DOCS_ONLY is true: Skip to documentation generation only
- OTHERWISE: Continue to TACTICAL STEPS (delegate immediately section)

## Strategic Agent Quick Reference

| Agent | Model | Phase | Purpose |
|-------|-------|-------|---------|
| ci-strategy-analyst | opus | 1 (Research) | CI best practices research |
| digdeep | opus | 1 (Research) | Five Whys root cause analysis |
| ci-infrastructure-builder | sonnet | 2 (Infra) | CI config improvements |
| ci-documentation-generator | haiku | 3 (Docs) | Runbook/strategy docs |
