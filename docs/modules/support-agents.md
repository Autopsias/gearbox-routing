# Support agents

Specialist subagents. The test, CI, quality and repo-health commands send work
to them, and you can also ask for one by name ("use the linting-fixer agent").

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any |
| **Needs** | Some agents name MCP servers in their tool list (see the table). Without the server, the agent still loads, but cannot use those tools. |

## The agents

All files are in `harness/agents/`. Each file sets its own model and effort.

| Agent | What it does | Extra tools |
|---|---|---|
| `unit-test-fixer` | Fixes pytest and unittest failures | — |
| `api-test-fixer` | Fixes API endpoint test failures | — |
| `database-test-fixer` | Fixes database mock, fixture and SQL test failures | — |
| `e2e-test-fixer` | Fixes Playwright end-to-end failures | — |
| `type-error-fixer` | Fixes mypy type errors | — |
| `import-error-fixer` | Fixes import and module errors | — |
| `linting-fixer` | Fixes ruff, black and isort issues | — |
| `code-quality-analyzer` | Splits files and functions that are too large | — |
| `safe-refactor` | Splits a file behind a facade, with tests after each step | — |
| `ci-infrastructure-builder` | Builds GitHub Actions workflows and test configuration | — |
| `ci-strategy-analyst` | Root-cause analysis of repeated CI failures | Web search |
| `digdeep` | Root-cause investigation; reads only, never runs or edits code | Exa, Perplexity, Ref, Semgrep MCP servers |
| `security-scanner` | Scans for vulnerabilities (bandit, semgrep, ESLint) and fixes them | Semgrep MCP server |
| `pr-workflow-manager` | Branches, pull requests, status checks and merges through `gh` | — |
| `uat-script-generator` | Writes user acceptance test scripts for a deployed system | — |
| `aws-cost-calculator` | AWS cost scenarios from current prices | Web search; Exa, Perplexity, Ref |
| `aws-service-researcher` | Researches AWS services and architectures | Web search; Exa, Perplexity, Ref, GitHub code search |
| `general-purpose` | Replaces Claude Code's built-in `general-purpose` agent with one that has a fixed effort (medium) | All tools |

Some agents read notes from `harness/agents/references/<agent>/`.

## Install

Copy only the agents you need. For example, the fixers that the test and CI commands use:

```bash
mkdir -p ~/.claude/agents/references
cp harness/agents/{unit-test-fixer,api-test-fixer,database-test-fixer,e2e-test-fixer,type-error-fixer,import-error-fixer,linting-fixer,security-scanner,ci-infrastructure-builder,ci-strategy-analyst,digdeep,pr-workflow-manager}.md ~/.claude/agents/
cp -R harness/agents/references/{unit-test-fixer,database-test-fixer,pr-workflow-manager} ~/.claude/agents/references/
```

## Check it works

Type `/agents` in Claude Code. The agents you copied are in the list.

## Remove

Delete each agent file and its `references/<agent>/` folder.

## Cautions

- The fixers edit your code. `pr-workflow-manager` pushes and merges.
- **`general-purpose.md` changes every untyped subagent on your machine.**
  Install it only if you want that.
- `digdeep` and the AWS agents can spend money on paid search tools.
