# repo-review

Whole-repo review skill for architecture, ways of working, agentic surfaces
(`AGENTS.md`, skills, hooks, MCP), evals/verification loops, and modern frontier LLM fit.
Delivers a candid **Good / Bad / Ugly** readout and prioritized action plan without editing
the target repository.

## Contents

- `SKILL.md` — 6-step read-only review workflow.
- `references/review-rubric.md` — scoring definitions, evidence tiers (`E0`–`E3`), ways-of-working
  checks, agentic-harness checks, and eval-quality rubric.
- `references/modern-llm-practices.md` — frontier coding-model fit guide (unhobbling,
  effort/thinking-level discipline, prefix-cache friendliness, delegation economics, and
  live-research query templates).
- `scripts/repo_inventory.py` — stdlib-only Python scanner that deterministically inventories
  manifests, agentic files, byte/line/token budgets, symlinks, broken relative links, CI,
  and eval/test surfaces without executing target-repo code.
- `scripts/test_repo_inventory.py` — unit tests for `repo_inventory.py`.

## Usage

`install.sh` installs this skill into `<home>/skills/repo-review/` across Claude Code,
OpenAI Codex CLI, and Google Antigravity.

Run the deterministic inventory standalone against any repository:

```bash
python3 claude/skills/repo-review/scripts/repo_inventory.py /path/to/repo --format json
python3 claude/skills/repo-review/scripts/repo_inventory.py /path/to/repo --format markdown
```
