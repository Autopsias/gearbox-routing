# Code quality (`/code-quality`, `/declutter`)

`/code-quality` finds files over 500 lines, functions over 100 lines and code
with a complexity over 12, and gives the fixes to refactor subagents. Its
checkers can also run in CI as a ratchet that lets debt only go down.
`/declutter` audits a whole repo for dead code and needless layers, and changes nothing.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any. Python repos get the most checks (`ruff` rule C901 for complexity). |
| **Needs** | `python3`, `ruff`. The subagents `code-quality-analyzer`, `safe-refactor`, `linting-fixer` and `type-error-fixer`. `/declutter` uses `vulture` (Python) or `knip` / `ts-prune` (JavaScript, TypeScript) through `uv` or `npx`. |

## What you get

- `harness/commands/code-quality.md` and `harness/references/code-quality/` — the command (`--check`, `--fix`, `--adopt`, `--focus=…`) and its procedures.
- `harness/scripts/quality/` — the three checkers (`check_file_sizes.py`, `check_function_lengths.py`, `check_complexity.py`), their shared code, a CI workflow template (`quality-ratchet.yml`) and `vendor_quality.py`, which copies the checkers and the workflow into another repo.
- `harness/agents/code-quality-analyzer.md`, `safe-refactor.md`, `linting-fixer.md`, `type-error-fixer.md` and `harness/agents/references/safe-refactor/` — the fixer subagents.
- `harness/skills/declutter/` — the audit skill.

## Install

```bash
mkdir -p ~/.claude/commands ~/.claude/references ~/.claude/scripts ~/.claude/agents/references ~/.claude/skills
cp harness/commands/code-quality.md ~/.claude/commands/
cp -R harness/references/code-quality ~/.claude/references/
cp -R harness/scripts/quality ~/.claude/scripts/
cp harness/agents/{code-quality-analyzer,safe-refactor,linting-fixer,type-error-fixer}.md ~/.claude/agents/
cp -R harness/agents/references/safe-refactor ~/.claude/agents/references/
cp -R harness/skills/declutter ~/.claude/skills/
```

The [safe refactor](safe-refactor.md) card has an optional hook for the same subagent.

## Check it works

```bash
python3 ~/.claude/scripts/quality/check_file_sizes.py --help
```

Then type `/code-quality` in a Python repo. The default is `--check`: analysis only.

## Remove

```bash
rm ~/.claude/commands/code-quality.md
rm -r ~/.claude/references/code-quality ~/.claude/scripts/quality ~/.claude/skills/declutter
rm ~/.claude/agents/{code-quality-analyzer,safe-refactor,linting-fixer,type-error-fixer}.md
rm -r ~/.claude/agents/references/safe-refactor
```

## Cautions

- `--fix` starts subagents that edit and split your files. Run it on a clean branch.
- `--generate-baseline` accepts the current debt as the starting point. Do not
  run it while someone else edits the tree.
- `/declutter` starts one subagent per area of the repo. On a large repo that is expensive.
