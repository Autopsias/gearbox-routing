# NotebookLM (`/nlm-skill`)

A guide that lets Claude drive Google NotebookLM through the `nlm` command
line or the NotebookLM MCP server: notebooks, sources, audio overviews,
reports and quizzes.

| | |
|---|---|
| **Status** | Optional · stable (written for `nlm` 0.3.19) |
| **Platform** | Any, with a browser for the sign-in |
| **Needs** | The `nlm` command line or the `notebooklm-mcp` MCP server, and a Google account with NotebookLM |

## What you get

- `harness/skills/nlm-skill/SKILL.md` — picks the CLI or the MCP server, and a quick reference.
- `harness/skills/nlm-skill/references/` — workflows, CLI and MCP usage, troubleshooting, the full command list.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/nlm-skill ~/.claude/skills/
```

Install the `nlm` tool or the MCP server yourself; this repo does not ship them.
The skill's commands and flags follow `nlm` 0.3.19. Run `nlm --help` to check
them against the version you install.

## Check it works

Run `nlm --version`, then type `/nlm-skill list my notebooks`. The first use
asks you to sign in (`nlm login` opens a browser).

## Remove

```bash
rm -r ~/.claude/skills/nlm-skill
```

## Cautions

- What you add to a notebook goes to Google.
- The skill's own notes say a NotebookLM sign-in lasts about 20 minutes; run
  `nlm login` again when commands start failing. That is the upstream service's
  behaviour, not something this repo can check.
- Only you can start it.
