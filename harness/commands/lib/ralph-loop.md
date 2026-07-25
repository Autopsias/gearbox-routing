# Ralph Loop Pattern Library

Provides the fresh-context loop pattern for unattended/overnight command execution.

## Why This Library Exists

Traditional Claude sessions accumulate context over time, leading to:
1. Context window exhaustion on long-running tasks
2. Decreased accuracy as context fills up
3. No clean "checkpoint and restart" mechanism

The Ralph Loop pattern solves this by:
- Spawning FRESH Claude instances per iteration
- Each iteration gets a full 200K context window
- Completion signals allow early termination on success
- Blocking signals halt the loop for human intervention

---

## Architecture

The Ralph Loop is powered by a **real executable bash script** at `~/.claude/scripts/ralph-loop-runner.sh`.

### How It Works

```
User types: /epic-dev 7 --loop 10

Claude reads epic-dev.md -> detects --loop
  |
  v
Claude runs via Bash(run_in_background=true):
  nohup bash ~/.claude/scripts/ralph-loop-runner.sh \
    --command epic-dev \
    --args "7 --yolo --phase-single --force-model" \
    --max-iterations 10 \
    --timeout 15 \
    --model opus \
    --completion-regex "EPIC.*COMPLETE|..." \
    > /tmp/ralph-loop-epic-dev.log 2>&1 &
  |
  v
Claude returns immediately with:
  - PID and log file path
  - Monitor command: tail -f /tmp/ralph-loop-epic-dev.log
  - Kill command: kill <PID>
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `nohup` + background | Loop survives if Claude Code session ends |
| `--append-system-prompt` | Injects command markdown as context (replaces slash commands in `-p` mode) |
| `--output-format json` | Enables reliable signal parsing |
| `--permission-mode bypassPermissions` | Required for unattended execution |
| Real `timeout` command | Per-iteration timeout protection |
| Real `grep -E` | Reliable completion/blocking signal detection |

### Why Not Slash Commands?

**Critical**: `claude -p` (non-interactive mode) **cannot execute slash commands/skills**.
Skills are interactive-only. The spawned Claude instance receives literal text, not a skill invocation.

**Solution**: The runner script uses `--append-system-prompt "$(cat command-file.md)"` to inject
the full command markdown as system context, then sends a natural language prompt telling Claude
to execute the workflow described in its system prompt.

---

## Runner Script

**Location**: `~/.claude/scripts/ralph-loop-runner.sh`

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--command` | string | required | Command name (e.g., `epic-dev`, `ci-orchestrate`) |
| `--args` | string | `""` | Arguments for the inner command |
| `--max-iterations` | integer | 10 | Maximum number of iterations |
| `--delay` | integer | 5 | Seconds to wait between iterations |
| `--timeout` | integer | 15 | Minutes per iteration before timeout |
| `--model` | string | `opus` | Claude model to use |
| `--completion-regex` | regex | `""` | Regex pattern for success detection |
| `--blocking-regex` | regex | see below | Regex pattern for halt detection |

### Default Blocking Signals

```regex
HALT|BLOCKED|Cannot proceed|Manual intervention|STATUS UPDATE FAILED
```

### Command File Resolution

The script resolves command files from `~/.claude/commands/`:
1. Try exact name: `~/.claude/commands/{command}.md`
2. Try hyphen variant: `~/.claude/commands/{command with - instead of _}.md`

### Core Loop

For each iteration, the script:
1. Builds a natural language prompt describing the workflow
2. Spawns `claude -p` with `--append-system-prompt "$(cat command-file.md)"`
3. Applies `timeout` protection (exit code 124 on timeout)
4. Checks output for completion regex -> exit 0
5. Checks output for blocking regex -> exit 1
6. Handles timeout/errors -> continue to next iteration
7. Sleeps `--delay` seconds before next iteration

---

## Standard Signals

### Completion Signals (per command)

| Command | Completion Regex |
|---------|------------------|
| epic-dev | `EPIC.*COMPLETE\|All stories in Epic.*complete\|Epic.*finished` |
| epic-dev-full | `EPIC.*COMPLETE\|All stories in Epic.*complete\|Epic.*finished` |
| code-quality | `All.*violations.*fixed\|0 violations remaining\|Code Quality.*PASS` |
| test-orchestrate | `All tests passing\|PYTEST_FAILURES=0.*VITEST_FAILURES=0\|0 failures` |
| ci-orchestrate | `All CI checks passing\|CI_STATUS.*passing\|CI pipeline.*PASS` |

### Blocking Signals (universal)

```regex
HALT|BLOCKED|Cannot proceed|Manual intervention|STATUS UPDATE FAILED
```

---

## Usage in Commands

Commands implement the `--loop` modifier by launching the runner script:

```markdown
## STEP 1.5: Ralph Loop Mode Detection

**If `--loop` is present in arguments, launch the real Ralph Loop runner script.**

IF "$ARGUMENTS" contains "--loop":

  Extract loop_max and loop_delay from arguments.
  Output the Ralph Loop activation banner.

  Run via Bash(run_in_background=true, timeout=600000):

  ```bash
  nohup bash "$HOME/.claude/scripts/ralph-loop-runner.sh" \
    --command "{command-name}" \
    --args "{args} {inner-flags}" \
    --max-iterations {loop_max} \
    --delay {loop_delay} \
    --timeout {timeout_minutes} \
    --model {model} \
    --completion-regex "{completion-regex}" \
    > /tmp/ralph-loop-{command-name}.log 2>&1 &
  echo "PID=$!"
  ```

  Output PID, log path, monitor command, kill command.
  EXIT

ELSE:
  PROCEED TO normal execution
END IF
```

---

## Command-Specific Configurations

| Command | Timeout | Model | Inner Flags | Completion Regex |
|---------|---------|-------|-------------|-----------------|
| epic-dev | 15min | (omitted - per-phase) | `--yolo --phase-single --force-model` | `EPIC.*COMPLETE\|All stories.*complete\|Epic.*finished` |
| epic-dev-full | 20min | (omitted - per-phase) | `--yolo --phase-single --force-model` | `EPIC.*COMPLETE\|All stories.*complete\|Epic.*finished` |
| ci-orchestrate | 10min (20 strategic) | sonnet (opus strategic) | `--fix-single-category` | `All CI checks passing\|CI_STATUS.*passing\|CI pipeline.*PASS` |
| test-orchestrate | 12min | sonnet | `--fix-single-type` | `All tests passing\|PYTEST_FAILURES=0.*VITEST_FAILURES=0\|0 failures` |
| code-quality | 15min | sonnet | `--fix --fix-single-rule` | `All.*violations.*fixed\|0 violations remaining\|Code Quality.*PASS` |

**Model strategy**: When `--model` is omitted from the runner, `claude -p` uses its default model.
The command file injected via `--append-system-prompt` contains its own per-phase model selection
table (e.g., opus for create/review, sonnet for dev, haiku for simple tasks). This is preferred
for commands like `epic-dev` and `epic-dev-full` that use different models per phase.

---

## Workflow Granularity

Ralph loops operate at **phase level** for optimal token efficiency.

### Why Phase-Level Matters

| Aspect | Story-Level | Phase-Level |
|--------|-------------|-------------|
| **Token Cost** | 150-200K per iteration | 20-50K per iteration |
| **Iterations per 200K** | 1 story | 4-6 phases |
| **Context Accumulation** | High (phases share context) | None (fresh per phase) |
| **Recovery from Failures** | Tunnel vision by end | Fresh perspective each phase |

### Phase Granularity Flags

| Command | Flag | Effect |
|---------|------|--------|
| epic-dev | `--phase-single` | One phase per iteration |
| epic-dev-full | `--phase-single` | One phase per iteration |
| ci-orchestrate | `--fix-single-category` | One CI failure category per iteration |
| test-orchestrate | `--fix-single-type` | One test failure type per iteration |
| code-quality | `--fix-single-rule` | One quality rule per iteration |

---

## Monitoring and Control

### Monitor a Running Loop

```bash
# Watch log output in real-time
tail -f /tmp/ralph-loop-epic-dev.log

# Check runner status
cat /tmp/ralph-loop-epic-dev/runner.log

# View specific iteration output
cat /tmp/ralph-loop-epic-dev/iter-3.log
```

### Stop a Running Loop

```bash
# Graceful: kill the runner (children will finish current iteration)
kill <PID>

# Force: kill runner and all children
kill -9 <PID>
```

### Check if Loop is Still Running

```bash
ps aux | grep ralph-loop-runner
```

---

## Troubleshooting

### Loop Exits Immediately

**Cause**: Command file not found.
**Fix**: Verify `~/.claude/commands/{command}.md` exists. Check hyphen/underscore variants.

### Timeout Occurring Too Soon

**Cause**: Iteration timeout too short for the work being done.
**Fix**: Increase `--timeout` parameter. Recommended minimums: 10min for tactical, 15min for opus.

### Completion Signal Not Detected

**Cause**: Output doesn't match the regex pattern.
**Fix**: Check actual Claude output matches the `--completion-regex`. Use `grep -E` to test:
```bash
echo "output text" | grep -qE "your|regex|pattern"
```

### Process Hangs After nohup

**Cause**: Shell didn't background properly.
**Fix**: Ensure the `&` is at the end and `echo "PID=$!"` captures the PID.

### State Loss After Interrupt

**Fix**: Commands write state before blocking operations. Resume with `--resume` flag.

---

## Attribution

The Ralph Loop pattern is inspired by [snarktank/ralph](https://github.com/snarktank/ralph) (2.5k stars).

The key insight from Ralph is that **fresh context per iteration** prevents:
1. Context window exhaustion
2. Accumulated confusion from prior failed attempts
3. Agent "tunnel vision" from seeing too much prior work

By spawning a fresh Claude instance per iteration, each attempt gets:
- Full 200K context window
- Clean slate (no prior mistakes to confuse)
- Fresh perspective on the problem
