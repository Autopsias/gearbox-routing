---
name: nlm-skill
description: "Expert guide for the NotebookLM CLI (`nlm`) and MCP server - interfaces for Google NotebookLM. Use this skill when users want to interact with NotebookLM programmatically, including: creating/managing notebooks, adding sources (URLs, YouTube, text, Google Drive), generating content (podcasts, reports, quizzes, flashcards, mind maps, slides, infographics, videos, data tables), conducting research, chatting with sources, or automating NotebookLM workflows. Triggers on mentions of \"nlm\", \"notebooklm\", \"notebook lm\", \"podcast generation\", \"audio overview\", or any NotebookLM-related automation task."
version: "0.3.19"
---

# NotebookLM CLI & MCP Expert

This skill provides comprehensive guidance for using NotebookLM via both the `nlm` CLI and MCP tools.

If `nlm --version` reports a newer version than this doc's (0.3.19), prefer `nlm --ai` output over this file for exact flags.

## Tool Detection (CRITICAL - Read First!)

**ALWAYS check which tools are available before proceeding:**

1. **Check for MCP tools**: Look for tools starting with `mcp__notebooklm-mcp__*` or `mcp_notebooklm_*`
2. **If BOTH MCP tools AND CLI are available**: **ASK the user** which they prefer to use before proceeding
3. **If only MCP tools are available**: Use them directly (refer to tool docstrings for parameters)
4. **If only CLI is available**: Use `nlm` CLI commands via Bash

**Decision Logic:**
```
has_mcp_tools = check_available_tools()  # Look for mcp__notebooklm-mcp__* or mcp_notebooklm_*
has_cli = check_bash_available()  # Can run nlm commands

if has_mcp_tools and has_cli:
    # ASK USER: "I can use either MCP tools or the nlm CLI. Which do you prefer?"
    user_preference = ask_user()
else if has_mcp_tools:
    # Use MCP tools directly
    mcp__notebooklm-mcp__notebook_list()
else:
    # Use CLI via Bash
    bash("nlm notebook list")
```

This skill documents BOTH approaches. Choose the appropriate one based on tool availability and **user preference**.

## Quick Reference

**Run `nlm --ai` to get comprehensive AI-optimized documentation** - this provides a complete view of all CLI capabilities.

```bash
nlm --help              # List all commands
nlm <command> --help    # Help for specific command
nlm --ai                # Full AI-optimized documentation (RECOMMENDED)
nlm --version           # Check installed version
```

## Critical Rules (Read First!)

1. Always authenticate first: Run `nlm login` before any operations
2. Sessions expire in ~20 minutes: Re-run `nlm login` if commands start failing
3. **⚠️ ALWAYS ASK USER BEFORE DELETE**: Before executing ANY delete command, ask the user for explicit confirmation. Deletions are **irreversible**. Show what will be deleted and warn about permanent data loss.
4. **`--confirm` is REQUIRED**: All generation and delete commands need `--confirm` or `-y` (CLI) or `confirm=True` (MCP)
5. Research requires `--notebook-id`: The flag is mandatory, not positional
6. Capture IDs from output: Create/start commands return IDs needed for subsequent operations
7. Use aliases: Simplify long UUIDs with `nlm alias set <name> <uuid>`
8. Check aliases before creating: Run `nlm alias list` before creating a new alias to avoid conflicts with existing names.
9. **DO NOT launch REPL**: Never use `nlm chat start` - it opens an interactive REPL that AI tools cannot control. Use `nlm notebook query` for one-shot Q&A instead.
10. Choose output format wisely: Default output (no flags) is compact and token-efficient—use it for status checks. Use `--quiet` to capture IDs for piping. Only use `--json` when you need to parse specific fields programmatically.
11. Use `--help` when unsure: Run `nlm <command> --help` to see available options and flags for any command.

## Workflow Decision Tree

Use this to determine the right sequence of commands:

```
User wants to...
│
├─► Work with NotebookLM for the first time
│   └─► nlm login → nlm notebook create "Title"
│
├─► Add content to a notebook
│   ├─► From a URL/webpage → nlm source add <nb-id> --url "https://..."
│   ├─► From YouTube → nlm source add <nb-id> --url "https://youtube.com/..."
│   ├─► From pasted text → nlm source add <nb-id> --text "content" --title "Title"
│   ├─► From Google Drive → nlm source add <nb-id> --drive <doc-id> --type doc
│   └─► Discover new sources → nlm research start "query" --notebook-id <nb-id>
│
├─► Generate content from sources
│   ├─► Podcast/Audio → nlm audio create <nb-id> --confirm
│   ├─► Written summary → nlm report create <nb-id> --confirm
│   ├─► Study materials → nlm quiz/flashcards create <nb-id> --confirm
│   ├─► Visual content → nlm mindmap/slides/infographic create <nb-id> --confirm
│   ├─► Video → nlm video create <nb-id> --confirm
│   └─► Extract data → nlm data-table create <nb-id> "description" --confirm
│
├─► Ask questions about sources
│   └─► nlm notebook query <nb-id> "question"
│       (Use --conversation-id for follow-ups)
│       ⚠️ Do NOT use `nlm chat start` - it's a REPL for humans only
│
├─► Check generation status
│   └─► nlm studio status <nb-id>
│
└─► Manage/cleanup
    ├─► List notebooks → nlm notebook list
    ├─► List sources → nlm source list <nb-id>
    ├─► Delete source → nlm source delete <source-id> --confirm
    └─► Delete notebook → nlm notebook delete <nb-id> --confirm
```

## Command Categories

### 1. Authentication

#### MCP Authentication

If using MCP tools and encountering authentication errors:

```bash
# Run the CLI authentication (works for both CLI and MCP)
nlm login

# Then reload tokens in MCP
mcp__notebooklm-mcp__refresh_auth()
```

Or manually save cookies via MCP (fallback):
```python
# Extract cookies from Chrome DevTools and save
mcp__notebooklm-mcp__save_auth_tokens(cookies="<cookie_header>")
```

#### CLI Authentication

```bash
nlm login                           # Launch browser, extract cookies (primary method)
nlm login --check                   # Validate current session
nlm login --profile work            # Use named profile for multiple accounts
nlm login --provider openclaw --cdp-url http://127.0.0.1:18800  # External CDP provider
nlm login switch <profile>          # Switch the default profile
nlm login profile list              # List all profiles with email addresses
nlm login profile delete <name>     # Delete a profile
nlm login profile rename <old> <new> # Rename a profile
```

**Multi-Profile Support**: Each profile gets its own isolated browser session (supports Chrome, Arc, Brave, Edge, Chromium, and more), so you can be logged into multiple Google accounts simultaneously.

**Session lifetime**: ~20 minutes. Re-authenticate when commands fail with auth errors.

**Switching MCP Accounts**: The MCP server always uses the active default profile. If you need to switch which Google account the MCP server is communicating with, you MUST use the CLI: run `nlm login switch <name>`. Your next MCP tool call will instantly use the new account.

**Note**: Both MCP and CLI share the same authentication backend, so authenticating with one works for both.

### 2-11. Notebook / Source / Research / Generation / Studio / Download / Export / Sharing / Note / Chat / Alias / Config / Skill Management

Full detail (MCP tool names + CLI command patterns per category): `Read ~/.claude/skills/nlm-skill/references/mcp-and-cli-usage.md`.


**Verb-first aliases**: `nlm update skill`, `nlm list skills`, `nlm install skill`

## Output Formats

Most list commands support multiple formats:

| Flag | Description |
|------|-------------|
| (none) | Rich table (human-readable) |
| `--json` | JSON output (for parsing) |
| `--quiet` | IDs only (for piping) |
| `--title` | "ID: Title" format |
| `--url` | "ID: URL" format (sources only) |
| `--full` | All columns/details |

## Common Patterns

### Pattern 1: Research → Podcast Pipeline

- [ ] `nlm notebook create "AI Research 2026"` — capture ID
- [ ] `nlm alias set ai <notebook-id>`
- [ ] `nlm research start "agentic AI trends" --notebook-id ai --mode deep`
- [ ] `nlm research status ai --max-wait 300` — wait up to 5 min
- [ ] `nlm research import ai <task-id>` — import all sources
- [ ] `nlm audio create ai --format deep_dive --confirm`
- [ ] `nlm studio status ai` — check generation progress

### Pattern 2: Quick Content Ingestion

- [ ] `nlm source add <id> --url "https://example1.com"`
- [ ] `nlm source add <id> --url "https://example2.com"`
- [ ] `nlm source add <id> --text "My notes..." --title "Notes"`
- [ ] `nlm source list <id>`

### Pattern 3: Study Materials Generation

- [ ] `nlm report create <id> --format "Study Guide" --confirm`
- [ ] `nlm quiz create <id> --count 10 --difficulty 3 --focus "Exam prep" --confirm`
- [ ] `nlm flashcards create <id> --difficulty medium --focus "Core terms" --confirm`

### Pattern 4: Drive Document Workflow

- [ ] `nlm source add <id> --drive 1KQH3eW0hMBp7WK... --type slides`
- [ ] ... time passes, document is edited ...
- [ ] `nlm source stale <id>` — check freshness
- [ ] `nlm source sync <id> --confirm` — sync if stale

## Error Recovery

| Error | Cause | Solution |
|-------|-------|----------|
| "Cookies have expired" | Session timeout | `nlm login` |
| "authentication may have expired" | Session timeout | `nlm login` |
| "Notebook not found" | Invalid ID | `nlm notebook list` |
| "Source not found" | Invalid ID | `nlm source list <nb-id>` |
| "Rate limit exceeded" | Too many calls | Wait 30s, retry |
| "Research already in progress" | Pending research | Use `--force` or import first |
| Browser doesn't launch | Port conflict | Close browser, retry |

## Rate Limiting

Wait between operations to avoid rate limits:
- Source operations: 2 seconds
- Content generation: 5 seconds
- Research operations: 2 seconds
- Query operations: 2 seconds

## Advanced Reference

For detailed information, see:
- **[references/command_reference.md](references/command_reference.md)**: Complete command signatures
- **[references/troubleshooting.md](references/troubleshooting.md)**: Detailed error handling
- **[references/workflows.md](references/workflows.md)**: End-to-end task sequences
