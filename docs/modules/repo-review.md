# Repo review (`/repo-review`)

Runs a read-only, evidence-backed whole-repo review covering architecture,
ways of working, agentic surfaces (`AGENTS.md`, `CLAUDE.md`, skills, hooks,
context budgets), evals and verification loops, and fit with modern frontier
coding models. It delivers a Good / Bad / Ugly readout and a prioritized action
plan without editing the target repository.

| | |
|---|---|
| **Status** | Optional · stable — has its own tests |
| **Platform** | Any with `python3` and `git`. The inventory scanner uses the Python standard library only. |
| **Needs** | `install.sh` installs it alongside the routing skills into `<target-home>/skills/repo-review/`. Optional: a web search MCP server (Exa or Ref) for live frontier-model checks. |

## What you get

- `claude/skills/repo-review/SKILL.md` — the six-step review workflow and five-lens audit rubric.
- `claude/skills/repo-review/references/` — the review rubric and modern LLM practices guide.
- `claude/skills/repo-review/scripts/repo_inventory.py` — deterministic, read-only scanner for top-level modules, manifests, CI commands, agentic surfaces, caution density, unreferenced fixtures/evals, symlinks, and broken links.
- `claude/skills/repo-review/scripts/inventory_checks.py` and `test_repo_inventory.py` — surface helpers and unit tests.

## Install

`install.sh` installs `repo-review` automatically with the routing framework ([`INSTALL.md`](../INSTALL.md)). To copy it by hand into Claude Code, Codex, or Antigravity:

```bash
mkdir -p ~/.claude/skills
cp -R claude/skills/repo-review ~/.claude/skills/
```

## Check it works

```bash
python3 ~/.claude/skills/repo-review/scripts/repo_inventory.py . --format markdown
```

It prints a Markdown inventory of the current repo. Then type `/repo-review` in a git repo.

## Remove

```bash
rm -r ~/.claude/skills/repo-review
```

## Cautions

- On a trusted repo, `/repo-review` runs fast, read-only verification commands discovered in CI or manifests (unit tests, linters, drift guards). On an untrusted checkout it stays static (`E0`) unless you approve commands.
- It never edits files, installs packages, or mutates git state.
