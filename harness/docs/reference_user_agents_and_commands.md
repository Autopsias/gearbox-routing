---
name: User Agents and Commands Reference
description: Directory of available user-level subagents and slash commands in ~/.claude/
type: reference
---

## User-Level Subagents (Available in ~/.claude/agents/)

| Agent | Use Case |
|-------|----------|
| `parallel-executor` | Independent work without delegation |
| `digdeep` | Root cause analysis with Five Whys |
| `database-test-fixer` | Database mock/fixture issues |
| `api-test-fixer` | API endpoint test failures |
| `unit-test-fixer` | General test fixing |
| `linting-fixer` | Lint error resolution |
| `type-error-fixer` | TypeScript/Python type errors |
| `import-error-fixer` | Import/module resolution |
| `security-scanner` | Security vulnerability checks |
| `browser-executor` | Browser automation (Chrome DevTools) |
| `playwright-browser-executor` | Playwright browser testing |
| `pr-workflow-manager` | PR operations and CI fixes |
| `ci-workflow-orchestrator` | CI/CD failure coordination |

## User-Level Slash Commands (Available in ~/.claude/commands/)

| Command | Purpose |
|---------|---------|
| `/pr [action]` | PR workflow helper |
| `/epic-dev <num>` | Automate BMAD epic development |
| `/ci_orchestrate` | Fix CI/CD failures |
| `/test_orchestrate` | Fix test failures (reads VS Code Test Explorer results) |
| `/parallelize <task>` | Split work across agents |
| `/nextsession` | Generate continuation prompt |
