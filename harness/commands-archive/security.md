---
description: "Runs security scans (Semgrep, Trivy, gitleaks), bootstraps security tooling, auto-fixes vulnerabilities, and generates CI security workflows. Use when you say 'security audit', 'scan for vulnerabilities', 'setup security', or before commits."
argument-hint: "[audit|setup|fix|ci] [--semgrep] [--deps] [--secrets] [--strict] [--mcp] [--yes]"
allowed-tools: ["Task", "Bash", "Read", "Glob", "Grep", "AskUserQuestion", "SlashCommand", "TaskCreate", "TaskUpdate", "TaskList"]
---

# /security — Unified Security Command

You are the security orchestrator. Parse the user's arguments and route to the appropriate mode.

## Loop Guard

```bash
SLASH_DEPTH=${SLASH_DEPTH:-0}
if [ "$SLASH_DEPTH" -ge 3 ]; then
    echo "ABORT: /security invocation depth >= 3. Breaking potential loop."
    exit 0
fi
export SLASH_DEPTH=$((SLASH_DEPTH + 1))
```

Check the `SLASH_DEPTH` environment variable. If >= 3, print a warning and STOP. Do not invoke any agents or further slash commands.

## Argument Parsing

Parse `$ARGUMENTS` for:

**Mode** (first positional arg, default: `audit`):
- `audit` — Run scanners, report findings
- `setup` — Bootstrap security tooling for the project
- `fix` — Scan and auto-fix vulnerabilities
- `ci` — Generate CI security workflow only

**Flags** (can combine):
- `--semgrep` — Limit audit to semgrep only
- `--deps` — Limit audit to dependency scan only
- `--secrets` — Limit audit to gitleaks only
- `--strict` — Fail on any finding (not just secrets)
- `--mcp` — Use Semgrep MCP instead of CLI
- `--yes` — Skip setup confirmation prompt

## Mode: audit (default)

Fast path — run the global security scanner directly, no agent overhead:

1. Check `~/bin/security-scan.sh` exists and is executable
2. Run it with scope flags if provided:
   ```bash
   ~/bin/security-scan.sh
   ```
3. Capture the exit code:
   - **Exit 0**: Print "All security scans passed. No findings." and STOP.
   - **Exit 1**: Findings detected. Delegate to `security-scanner` agent for detailed analysis:
     ```
     Task(subagent_type="security-scanner", prompt="Analyze and fix the security findings in this project. Mode: --mode=fix")
     ```
   - **Exit 2**: Missing tools or errors. Report what's missing and suggest `brew install`.

If `--semgrep` flag: only run the semgrep section (modify script call or run inline).
If `--deps` flag: only run trivy section.
If `--secrets` flag: only run gitleaks section.

## Mode: setup

Delegate entirely to the `security-setup` agent:

```
Task(
    subagent_type="security-setup",
    prompt="Bootstrap security tooling for this project. Detect project type, generate configs from ~/.config/security-templates/, and wire everything together. Flags: [passthrough --yes, --ci-only if present]"
)
```

Pass through these flags: `--yes`, `--ci-only` (derived from `/security ci`).

## Mode: fix

Delegate to `security-scanner` agent in fix mode:

```
Task(
    subagent_type="security-scanner",
    prompt="Scan this project for security vulnerabilities and fix them. --mode=fix [--mcp if present]"
)
```

Pass through: `--mcp`, `--strict`.

## Mode: ci

Shortcut for setup with `--ci-only`:

```
Task(
    subagent_type="security-setup",
    prompt="Generate only the CI security workflow for this project. --ci-only --yes"
)
```

## Error Handling

- If `~/bin/security-scan.sh` doesn't exist, inform the user and suggest:
  ```
  The global security scanner is not installed at ~/bin/security-scan.sh.
  This is part of the reusable security toolkit. Check your setup.
  ```
- If an agent fails, report the error and suggest running with `--mcp` as fallback.
- Never retry the same agent invocation — if it fails, report and stop.

## Examples

```
/security                  # Quick audit with all scanners
/security audit --semgrep  # Semgrep-only audit
/security setup            # Full project security bootstrap
/security setup --yes      # Bootstrap without confirmation
/security fix              # Scan and auto-fix vulnerabilities
/security fix --mcp        # Fix using Semgrep MCP (no CLI needed)
/security ci               # Generate CI workflow only
```
