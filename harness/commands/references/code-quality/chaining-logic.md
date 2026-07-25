# Chaining Logic, Loop Modes & Ralph Loop

Auto-chain invocation, Ralph loop mode (fresh context), rule-level mode, and iterative self-correction.

---

## Ralph Loop Mode Detection (Fresh Context) — STEP 1.25

**If `--loop` is present in arguments, launch the real Ralph Loop runner script.**

This spawns fresh Claude instances per iteration via `~/.claude/scripts/ralph-loop-runner.sh`.
Each iteration gets clean context with the command file injected via `--append-system-prompt`.
This is different from `--ralph` (which is within-context self-correction).

```
IF "$ARGUMENTS" contains "--loop":

  # Extract loop parameters
  loop_max = extract_number_after("--loop", default=10)
  loop_delay = extract_number_after("--loop-delay", default=5)

  # Determine inner command flags (preserve all except --loop)
  inner_flags = "$ARGUMENTS" without "--loop" and "--loop-delay"
  IF NOT contains "--fix":
    inner_flags += " --fix"  # Loop mode implies --fix

  Output: "════════════════════════════════════════════════════════"
  Output: "RALPH LOOP MODE ACTIVATED (Code Quality)"
  Output: "════════════════════════════════════════════════════════"
  Output: "  Max iterations: {loop_max}"
  Output: "  Delay between iterations: {loop_delay}s"
  Output: "  Fresh context per iteration: YES"
  Output: "  Mode: Unattended (--fix implied)"
  Output: "  Inner flags: {inner_flags}"
  Output: "  Runner: ~/.claude/scripts/ralph-loop-runner.sh"
  Output: "════════════════════════════════════════════════════════"
  Output: ""

  # Launch the real bash runner script in background via Bash tool
  # with run_in_background=true and timeout=600000 (10 hours max)
  Run via Bash(run_in_background=true, timeout=600000):

  ```bash
  nohup bash "$HOME/.claude/scripts/ralph-loop-runner.sh" \
    --command "code-quality" \
    --args "{inner_flags} --fix-single-rule" \
    --max-iterations {loop_max} \
    --delay {loop_delay} \
    --timeout 15 \
    --model sonnet \
    --completion-regex "All.*violations.*fixed|0 violations remaining|Code Quality.*PASS" \
    > /tmp/ralph-loop-code-quality.log 2>&1 &
  echo "PID=$!"
  ```

  Output:
  - "Ralph loop started in background"
  - "PID: {pid}"
  - "Log: /tmp/ralph-loop-code-quality.log"
  - "Monitor: tail -f /tmp/ralph-loop-code-quality.log"
  - "Stop: kill {pid}"
  EXIT

ELSE:
  # Normal execution - continue to STEP 1.5
  PROCEED TO STEP 1.5
END IF
```

---

## Rule-Level Mode Detection (Phase Granularity) — STEP 1.26

**If `--fix-single-rule` is present, execute only ONE category of quality violations per iteration.**

This provides phase-level granularity for Ralph loops, resulting in:
- 20-35K tokens per iteration (vs 60-100K for all categories)
- Fresh context per rule category (prevents tunnel vision)
- Better recovery from failures

```
IF "--fix-single-rule" in "$ARGUMENTS":
  # RULE-LEVEL MODE: Execute ONLY the next category of violations

  Output: "Rule-level mode active - fixing ONE quality category..."

  # Run quality checks and detect violation categories
  ```bash
  echo "=== Analyzing Quality Violations ==="

  # Check for file size violations
  FILE_SIZE_OUT=""
  if [ -f ~/.claude/scripts/quality/check_file_sizes.py ]; then
      if command -v uv &> /dev/null; then
          FILE_SIZE_OUT=$(uv run python ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" 2>&1 || true)
      else
          FILE_SIZE_OUT=$(python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" 2>&1 || true)
      fi
  fi
  HAS_FILE_SIZE=$(echo "$FILE_SIZE_OUT" | grep -cE "BLOCKING|violation" || echo "0")

  # Check for function length violations
  FUNC_LEN_OUT=""
  if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
      FUNC_LEN_OUT=$(python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" 2>&1 || true)
  fi
  HAS_FUNC_LEN=$(echo "$FUNC_LEN_OUT" | grep -cE "BLOCKING|violation|>100 lines" || echo "0")

  # Check for complexity violations (if available)
  HAS_COMPLEXITY=0
  if [ -f ~/.claude/scripts/quality/check_complexity.py ]; then
      COMPLEX_OUT=$(python3 ~/.claude/scripts/quality/check_complexity.py --project "$PWD" 2>&1 || true)
      HAS_COMPLEXITY=$(echo "$COMPLEX_OUT" | grep -cE "complexity|violation" || echo "0")
  fi
  ```

  # Execute ONLY the first detected category (priority order: complexity -> function-length -> file-size)
  IF HAS_COMPLEXITY > 0:
    Output: "=== [RULE: COMPLEXITY] Fixing high-complexity functions ==="

    # DEFENSIVE: Update ralph_state BEFORE Task call (protects against hangs)
    Update batch state:
      - ralph_state.current_rule: "complexity"
      - ralph_state.rule_iteration: {STATE.ralph_state.rule_iteration + 1}
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Task(
      subagent_type="code-quality-analyzer",
      model="sonnet",
      description="Fix complexity violations",
      prompt="Fix high-complexity functions in the codebase.

Quality check output:
{COMPLEX_OUT | head -50}

Target: Cyclomatic complexity < 12 per function

Split complex functions, extract helper methods, simplify conditional logic.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'remaining_issues': N,
  'summary': 'Brief description'
}"
    )

    # Update ralph_state after successful completion
    Update batch state:
      - ralph_state.rules_completed: [...STATE.ralph_state.rules_completed, "complexity"]
      - ralph_state.rules_remaining: [filter "complexity" from STATE.ralph_state.rules_remaining]
      - ralph_state.current_rule: null
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Output: "RULE_COMPLETE: COMPLEXITY"
    Output: "   Next rule: function-length (if any)"
    Exit 0

  ELIF HAS_FUNC_LEN > 0:
    Output: "=== [RULE: FUNCTION-LENGTH] Fixing long functions ==="

    # Get list of files with function length violations
    VIOLATION_FILES=$(echo "$FUNC_LEN_OUT" | grep -E "BLOCKING|>100 lines" | grep -oE "[a-zA-Z0-9_/]+\.py" | sort -u | head -3)

    # DEFENSIVE: Update ralph_state BEFORE Task call
    Update batch state:
      - ralph_state.current_rule: "function-length"
      - ralph_state.rule_iteration: {STATE.ralph_state.rule_iteration + 1}
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Task(
      subagent_type="safe-refactor",
      model="sonnet",
      description="Fix function length violations",
      prompt="Refactor long functions in these files:

Files with violations:
$VIOLATION_FILES

Quality check output:
{FUNC_LEN_OUT | head -50}

Target: Functions < 100 lines

Use TEST-SAFE workflow:
1. Run existing tests, establish GREEN baseline
2. Split long functions using facade pattern
3. Verify tests pass after each change

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'remaining_issues': N,
  'new_structure': {
    'longest_function_lines': N,
    'target_threshold': 100,
    'meets_threshold': true|false
  },
  'summary': 'Brief description'
}"
    )

    # Update ralph_state after successful completion
    Update batch state:
      - ralph_state.rules_completed: [...STATE.ralph_state.rules_completed, "function-length"]
      - ralph_state.rules_remaining: [filter "function-length" from STATE.ralph_state.rules_remaining]
      - ralph_state.current_rule: null
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Output: "RULE_COMPLETE: FUNCTION-LENGTH"
    Output: "   Next rule: file-size (if any)"
    Exit 0

  ELIF HAS_FILE_SIZE > 0:
    Output: "=== [RULE: FILE-SIZE] Fixing oversized files ==="

    # Get list of files with file size violations
    VIOLATION_FILES=$(echo "$FILE_SIZE_OUT" | grep -E "BLOCKING" | grep -oE "[a-zA-Z0-9_/]+\.py" | sort -u | head -3)

    # DEFENSIVE: Update ralph_state BEFORE Task call
    Update batch state:
      - ralph_state.current_rule: "file-size"
      - ralph_state.rule_iteration: {STATE.ralph_state.rule_iteration + 1}
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Task(
      subagent_type="safe-refactor",
      model="sonnet",
      description="Fix file size violations",
      prompt="Refactor oversized files:

Files with violations:
$VIOLATION_FILES

Quality check output:
{FILE_SIZE_OUT | head -50}

Target: Production files < 500 LOC, Test files < 800 LOC

Use TEST-SAFE workflow:
1. Run existing tests, establish GREEN baseline
2. Extract modules using facade pattern
3. Verify tests pass after each change

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'remaining_issues': N,
  'new_structure': {
    'largest_file_loc': N,
    'target_threshold': 500,
    'meets_threshold': true|false
  },
  'summary': 'Brief description'
}"
    )

    # Update ralph_state after successful completion
    Update batch state:
      - ralph_state.rules_completed: [...STATE.ralph_state.rules_completed, "file-size"]
      - ralph_state.rules_remaining: [filter "file-size" from STATE.ralph_state.rules_remaining]
      - ralph_state.current_rule: null
      - updated_at: {timestamp}
    Write code-quality-batch-state.json

    Output: "RULE_COMPLETE: FILE-SIZE"
    Exit 0

  ELSE:
    # No violations detected or all categories fixed
    Output: "════════════════════════════════════════════════════════"
    Output: "ALL QUALITY CHECKS PASSING"
    Output: "════════════════════════════════════════════════════════"
    Output: "   All quality rule categories have been addressed."
    Output: "   Codebase meets quality thresholds."
    Exit 0
  END IF

ELSE:
  # ALL-RULES MODE: Original behavior (fix all categories with smart batching)
  Output: "All-rules mode active - fixing all quality violations..."
  PROCEED TO STEP 1.5
END IF
```

---

## Ralph Loop Mode (DEFAULT FALLBACK or --ralph flag) — STEP 4-RALPH

**ACTIVATION:**
- **Automatic (DEFAULT):** When single-shot agent hallucination is detected (git shows no changes)
- **Forced:** When `--ralph` flag is provided (skip single-shot, go straight to Ralph)
- **Disabled:** When `--no-ralph` flag is provided (fail on hallucination, no retry)

Ralph Loop provides **iterative self-correction**. If an agent hallucinates:
1. Agent claims to have modified file
2. Stop hook intercepts exit
3. Same prompt fed again
4. Agent sees UNCHANGED file and realizes work wasn't done
5. Agent actually performs the work

### Ralph Mode Workflow

For each file in violation list, spawn a Ralph loop instead of single-shot agent:

```
SlashCommand(command="/ralph-loop \"
Refactor {file_path} to reduce function length violations.

Target: Functions should be <100 lines

MANDATORY STEPS:
1. Read the file: Read({file_path})
2. Identify functions >100 lines
3. Split each long function using Edit/MultiEdit tools
4. Run verification: git diff --name-only | grep {file_path}
5. Check result: wc -l {file_path}

OUTPUT PROMISE:
Only output <promise>REFACTOR VERIFIED</promise> when BOTH conditions are true:
- git diff shows {file_path} was modified
- Functions in {file_path} are now <100 lines

If verification fails, continue refactoring. Do NOT output the promise until verified.
\" --max-iterations 5 --completion-promise \"REFACTOR VERIFIED\"")
```

### Ralph Mode Benefits

| Aspect | Single-Shot Agent | Ralph Loop |
|--------|-------------------|------------|
| Hallucination risk | HIGH | LOW |
| Self-correction | NONE | AUTOMATIC |
| Verification | Optional | Built-in |
| Failure handling | Manual | Iterative |

### Ralph Mode Behavior Summary

| Scenario | Behavior |
|----------|----------|
| **Default (no flag)** | Single-shot first -> Auto-Ralph on hallucination |
| **`--ralph` flag** | Skip single-shot -> Ralph for ALL files |
| **`--no-ralph` flag** | Single-shot only -> Fail on hallucination (no retry) |

**Force `--ralph` when:**
- You want maximum reliability regardless of cost
- Previous session had 50%+ hallucination rate
- Critical refactoring that MUST succeed first time

**Use `--no-ralph` when:**
- Debugging single-shot behavior
- Cost-sensitive batch processing
- You'll manually retry failed files

---

## Chain Invocation (unless --no-chain) — STEP 8

If all tests passing after refactoring:

```bash
# Check if chaining disabled
if [[ "$ARGUMENTS" != *"--no-chain"* ]]; then
    # Check depth to prevent infinite loops
    DEPTH=${SLASH_DEPTH:-0}
    if [ $DEPTH -lt 3 ]; then
        export SLASH_DEPTH=$((DEPTH + 1))
        SlashCommand(command="/commit_orchestrate --message 'refactor: reduce file sizes'")
    fi
fi
```
