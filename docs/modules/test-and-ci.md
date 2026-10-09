# Test and CI commands (`/test-orchestrate`, `/ci-orchestrate`, `/commit-orchestrate`, `/ship-tail`, `/coverage`, `/create-test-plan`)

Commands that sort test or CI failures by kind and send fixer subagents in
parallel, commit with checks, and take a branch through push, pull request and
green CI. `/ship-tail` chains them: fix tests, commit, push and open a pull
request, then fix CI until it is green. It never merges.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any `git` repo. Written for Python (pytest, ruff, mypy); covers vitest and Playwright too. |
| **Needs** | The fixer subagents in [support agents](support-agents.md). `gh` (signed in) for CI and pull requests. `/ship-tail` also needs `/pr` from [general commands](general-commands.md). |

## What you get

| Command | Files | What it does |
|---|---|---|
| `/test-orchestrate` | `commands/test-orchestrate.md`, `references/test-orchestrate/` | Groups failing tests and sends a fixer per group |
| `/ci-orchestrate` | `commands/ci-orchestrate.md`, `references/ci-orchestrate/` | The same for a red CI run; `--strategic` does a root-cause analysis |
| `/commit-orchestrate` | `commands/commit-orchestrate.md`, `references/commit-orchestrate/` | Runs ruff, mypy and pytest in parallel, fixes what it can, writes the commit; `--push-after` pushes |
| `/ship-tail` | `commands/ship-tail.md`, `references/ship-tail/` | The whole chain, up to 3 CI fix cycles; `--dry-run` prints the plan |
| `/coverage` | `commands/coverage.md`, `references/coverage/` | Finds coverage gaps, ranks them by risk, writes tests |
| `/create-test-plan` | `commands/create-test-plan.md`, `references/create-test-plan/` | Writes a test plan for an epic, story or feature |

All paths are under `harness/`. Several commands also read
`harness/references/shared/` and `harness/references/lib/`.

## Install

```bash
mkdir -p ~/.claude/commands ~/.claude/references
cd harness
cp commands/{test-orchestrate,ci-orchestrate,commit-orchestrate,ship-tail,coverage,create-test-plan}.md ~/.claude/commands/
cp -R references/{test-orchestrate,ci-orchestrate,commit-orchestrate,ship-tail,coverage,create-test-plan,shared,lib} ~/.claude/references/
cd ..
```

Then install the [support agents](support-agents.md).

## Check it works

Type `/ship-tail --dry-run` on a clean branch. It prints the stages it would
run and runs none of them.

## Remove

```bash
rm ~/.claude/commands/{test-orchestrate,ci-orchestrate,commit-orchestrate,ship-tail,coverage,create-test-plan}.md
rm -r ~/.claude/references/{test-orchestrate,ci-orchestrate,commit-orchestrate,ship-tail,coverage,create-test-plan}
```

Keep `references/shared` and `references/lib` if another module uses them.

## Cautions

- **They edit code, commit and push.** `/test-orchestrate` chains to
  `/commit-orchestrate` unless you pass `--no-chain`. `/ship-tail` pushes and
  opens a pull request.
- `--loop` (in `/test-orchestrate`, `/ci-orchestrate` and `/code-quality`)
  calls a script that is not in this repo. Only `--loop` fails.
- The `--strategic` modes call three subagents that are not in this repo
  (`test-strategy-analyst`, `test-documentation-generator`,
  `ci-documentation-generator`). Only those modes fail.
- `/commit-orchestrate` has a `--skip-hooks` flag. Do not use it to skip your
  repo's checks.

## Not working as shipped: `/user-testing` and `/usertestgates`

`harness/commands/user-testing.md` and `usertestgates.md` say in their headers
that they are project-specific. `/user-testing` needs six subagents, and
`/usertestgates` needs a `~/.claude/lib/testgates_discovery.py` script. None of
them are in this repo. Do not install these two.
