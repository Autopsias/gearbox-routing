# Efficiency Rules for LLM Reviews, Gates, and Dispatched Agents

Invariants only; measurements and incident history live in `~/.claude/docs/reference_llm_review_efficiency.md`.

## Core principle

**A slow, silent, repeated step is a cost you owe the user a report on — not a wait you absorb.**

- **NEVER hand a reviewer its own generated output.** Review payloads hold SOURCE only. List captured logs, probe transcripts, and receipts by name and size without instructing a full read. Executable artifacts (`.sh`, `.py` probes) ARE source; discriminate on executable vs captured log, not "listed as evidence".
- **NEVER judge a long step hung vs slow without comparing parent and child elapsed** (`ps -o etime`). A much younger child proves a silent retry already occurred.
- **NEVER make a dispatched subagent run a multi-minute quiet command.** A stream watchdog kills an agent after ~600 s with no output while its turn stays open. Run long commands with `run_in_background` and read the Bash reply:
  - Single Agent dispatch ("You will be notified when it completes"): end your turn with no closeout and return after waking.
  - Workflow route ("is terminated when you give your final response"): stay in your turn and wait with Monitor and `until <check>; do echo waiting; sleep 30; done`.
  - Never write `sleep N` followed by a check (Claude Code blocks it; Codex untested). Otherwise run test suites in the orchestrator or as a gate and keep gaps between tool calls short.
- **Echo-per-pass loops are for Workflow members only. In the main session, NEVER arm a Monitor on a `run_in_background` command** (its completion notice is enough). Every Monitor stdout line wakes the session, so main-session until-loops must print nothing per pass (`until <check>; do sleep 30; done; <report>`).
- **Resume a stalled agent ONCE.** If resume yields little or nothing, dispatch a FRESH agent naming the existing on-disk state ("X and Y already exist; verify, do not rebuild").
- **NEVER prescribe a workaround you have not verified.** Verify any mechanism claim before handing it to an agent or the user.
- **NEVER put `&` inside a `run_in_background` command** (the wrapper exits and kills the inner job). Run the real command directly, and verify a log file exists before arming a Monitor on it.

Cost reporting itself is in `~/.claude/CLAUDE.md` ("repeated step's cost visibly compounds").
