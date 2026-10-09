# Reference docs, global instructions and key bindings

Background files from the original setup. Read them; copy one only if it fits.

| | |
|---|---|
| **Status** | Optional · reference only |
| **Platform** | Any |
| **Needs** | Nothing |

## The files

| File | What it is | Copy it? |
|---|---|---|
| `harness/docs/reference_mcp_tool_selection.md` | When to use which research MCP server (Perplexity, Exa, Ref, GitHub code search, Semgrep, Chrome DevTools), and when a CLI is better | Yes, if you use those servers. Three BMAD research commands point at it. |
| `harness/docs/reference_llm_review_efficiency.md` | The measurements behind the rule `harness/rules/llm-review-and-agent-efficiency.md` | With the plan pipeline only |
| `harness/docs/reference_git_safety_playbook.md` | The worked patterns behind the [git safety](git-safety.md) rule | With the git safety module |
| `harness/docs/reference_rules_rationale.md` | Why each rule in `harness/CLAUDE.md` exists, with the incident behind it | Read it if you copy a rule |
| `harness/SKILL-UNIFICATION-ROUTING.md` | Which command or skill is the single front door for each job (review, research, tests), and which old names point to it | Read it; `/adversarial-review`, `/review` and `/diagnose` refer to it |
| `harness/docs/gemini-claude-code-setup.md` | How to run Claude Code against a Gemini model through a proxy (December 2025) | Only for that experiment |
| `harness/rules/llm-review-and-agent-efficiency.md` | Rules that keep reviews, gates and subagents from wasting time and tokens | Yes, if you run long reviews or many subagents |
| `harness/CLAUDE.md` | The original author's global instructions | **Never copy it over your own.** Paste single rules into your `~/.claude/CLAUDE.md` if you want them. |
| `harness/keybindings.json` | Three key bindings: Shift+Enter for a new line, Ctrl+Enter to send, Alt+P for the model picker | Only if you have no `keybindings.json` |

## Install

```bash
mkdir -p ~/.claude/docs ~/.claude/rules
cp harness/docs/reference_mcp_tool_selection.md ~/.claude/docs/
cp harness/rules/llm-review-and-agent-efficiency.md ~/.claude/rules/
[ -e ~/.claude/keybindings.json ] || cp harness/keybindings.json ~/.claude/
```

## Remove

```bash
rm ~/.claude/docs/reference_mcp_tool_selection.md ~/.claude/rules/llm-review-and-agent-efficiency.md
```

## Cautions

- `harness/CLAUDE.md` describes the original author's deploy flow.
- The Gemini guide installs an npm package and asks for a Google API key.
  Check the package before you install it.
