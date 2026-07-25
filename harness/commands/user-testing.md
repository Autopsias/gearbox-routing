---
description: "Runs the BMAD UI/browser testing workflow against a deployed epic: spawns discovery, chrome-browser-executor and reporter subagents and writes a structured session report. Use when you say 'run user testing', 'user-test epic N', '/user_testing', or want UI walkthrough testing of a deployed epic. Not for unit/API test fixing (use /test-orchestrate) or coverage analysis (/coverage)."
argument-hint: "[epic_target] [options]"
allowed-tools: ["Task", "Read", "Write", "Bash", "Grep", "Glob", "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

# ⚠️ PROJECT-SPECIFIC COMMAND - Requires BMAD testing infrastructure (ui-test-discovery, chrome-browser-executor, bmad-reporter agents and workspace/testing/sessions/ layout)

# /user_testing Command

**Invocation mode:** intended for explicit `/user_testing` invocation with an epic target — not for open-ended conversational triggering.

Main UI/browser testing command for executing Epic testing workflows using Claude-native subagent orchestration with structured BMAD reporting. This command is for UI testing ONLY.

## Command Usage

```bash
/user_testing [epic_target] [options]
```

### Parameters

- `epic_target` - Target for testing (epic-3.3, story-3.2, custom document path)
- `--mode [automated|interactive|hybrid]` - Testing execution mode (default: hybrid)
- `--cleanup [session_id]` - Clean up specific session
- `--cleanup-older-than [days]` - Remove sessions older than specified days
- `--archive [session_id]` - Archive session to permanent storage
- `--list-sessions` - List all active sessions with status
- `--include-size` - Include session sizes in listing
- `--resume [session_id]` - Resume interrupted session from last checkpoint

### Examples

```bash
# Clean up old sessions
/user_testing --cleanup-older-than 7

# List all active sessions with sizes
/user_testing --list-sessions --include-size

# Resume interrupted session
/user_testing --resume epic-3.3_hybrid_20250829_143000_abc123
```

## CRITICAL: UI/Browser Testing Only

This command executes UI/browser testing EXCLUSIVELY. When invoked:
- ALWAYS use chrome-browser-executor for Phase 3 test execution
- Focus on browser-based user interface testing

## Command Implementation

You are the main testing orchestrator for the BMAD testing framework. You coordinate the execution of all testing agents using Task tool orchestration with **markdown-based communication** for seamless agent coordination and improved accessibility.

> For full execution workflow (Phases 0-4) and task tool orchestration, `Read ~/.claude/commands/references/user-testing/execution-workflow.md`

> For session management, framework improvements, and performance details, `Read ~/.claude/commands/references/user-testing/session-management.md`

> For command output examples (success/error), `Read ~/.claude/commands/references/user-testing/command-output.md`

---

> For tasklist integration details, `Read ~/.claude/commands/references/user-testing/tasklist-integration.md`