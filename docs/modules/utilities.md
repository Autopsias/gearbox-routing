# Utility scripts

Three stand-alone scripts for your Claude Code setup. `memory_budget.py` and `mcp-health.py` only report. `render-codex-instructions.py` writes a file unless you pass `--check`.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any with `python3` |
| **Needs** | Nothing |

## What you get

| File | What it reports |
|---|---|
| `harness/scripts/memory_budget.py` | For each project's auto-memory: the size of `MEMORY.md`, the note count, stale notes, and notes that nothing links to. Options: `--root` (default `~/.claude/projects`), `--age-days`. |
| `harness/scripts/mcp-health.py` (with `mcp-health.md`) | Counts 401, 403, quota and expired-token errors per MCP server in your recent session transcripts. Options: `--days`, `--json`. |
| `harness/scripts/render-codex-instructions.py` | Writes `codex/global-instructions.md` (under `--repo`) from a `CLAUDE.md` plus `rules/*.md`. Text between `<!-- claude-only -->` markers stays out. It does not create `AGENTS.md`: you link `~/.codex/AGENTS.md` to the output yourself. `--check` only reports a stale render and writes nothing. |

## Install

```bash
mkdir -p ~/.claude/scripts
cp harness/scripts/memory_budget.py harness/scripts/mcp-health.py harness/scripts/mcp-health.md ~/.claude/scripts/
```

`render-codex-instructions.py` expects the folder layout of the original
setup. Read its `--help` before you use it.

## Check it works

```bash
python3 ~/.claude/scripts/memory_budget.py
python3 ~/.claude/scripts/mcp-health.py --days 3
```

## Remove

```bash
rm ~/.claude/scripts/{memory_budget.py,mcp-health.py,mcp-health.md}
```

## Cautions

`mcp-health.py` reads your session transcripts, and its output shows short
snippets of them. Do not paste its output where others can see it.
