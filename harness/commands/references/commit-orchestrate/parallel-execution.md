## PARALLEL EXECUTION GUARANTEE

ABSOLUTE REQUIREMENT: This command MUST maintain parallel execution in ALL modes.

- All quality fixes run in parallel across domains
- Staging and commit verification run efficiently
- FAILURE: Sequential quality fixes (one domain after another)
- FAILURE: Waiting for one quality check before starting another

**COMMIT QUALITY ADVANTAGE:**
- Parallel quality checks minimize commit delay
- Domain-specific expertise for faster issue resolution
- Comprehensive pre-commit validation across all domains
- Automated staging and commit workflow

## EXECUTION REQUIREMENT

IMMEDIATE EXECUTION MANDATORY

You MUST execute this commit orchestration procedure immediately upon command invocation.

Do not describe what you will do. DO IT NOW.

**REQUIRED ACTIONS:**
1. Analyze git repository state and staged changes
2. Detect quality issues and map to specialist agents
3. Launch quality agents using Task tool in BATCH DISPATCH MODE
4. Execute automated staging and commit workflow
5. NEVER launch agents sequentially - parallel quality fixes are essential

**COMMIT ORCHESTRATION EXAMPLES:**
- "/commit_orchestrate" -> Auto-stage, quality fix, and commit all changes
- "/commit_orchestrate 'feat: add new feature' --quality-first" -> Run quality checks before staging
- "/commit_orchestrate --stage-all --push-after" -> Full workflow with remote push
- "/commit_orchestrate 'fix: resolve issues' --skip-hooks" -> Commit with hook bypass

**PRE-COMMIT HOOK INTEGRATION:**
If pre-commit hooks fail after quality fixes:
- Automatically retry commit ONCE to include hook modifications
- If hooks fail again, report specific hook failures for manual intervention
- Never bypass hooks unless explicitly requested with --skip-hooks
