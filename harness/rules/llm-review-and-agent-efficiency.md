# Efficiency Rules for LLM Reviews, Gates, and Dispatched Agents

**PRIORITY: HIGH** — these prevent silent, expensive waste in any step that runs
an LLM review, a verification gate, or a dispatched subagent. Invariants only;
the measurements behind each one, and the concrete fixes they imply, live in
`~/.claude/docs/reference_llm_review_efficiency.md` — read that when you are
tuning a gate, not on every session.

## Core principle

**A step that is slow, silent, and repeated is a cost you owe the user a report
on — not a wait you absorb.** The cost of these steps is almost always knowable
in one or two commands; the failure is never measuring.

- **NEVER hand a reviewer its own generated output.** A review payload is the
  SOURCE under review. Captured logs, probe transcripts, run receipts and
  evidence artifacts are output — list them by name and size so nothing is
  hidden, but never instruct a model to read them in full as new code. An
  executable artifact (a `.sh` or `.py` probe) IS source; discriminate on
  "is this executable" versus "is this a captured log", not on "is this listed
  as evidence".
- **NEVER judge a long step hung-versus-slow without comparing parent and child
  elapsed** (`ps -o etime` on both). A child much younger than its parent proves
  an earlier attempt already died and you are watching a silent retry — which
  doubles the wall time before anything is learned. No log will say so.
- **NEVER make a dispatched subagent run a multi-minute quiet command.** A
  stream watchdog kills an agent after ~600s with no OUTPUT — liveness does not
  count. This kills it in the foreground and in the background alike. Run test
  suites in the orchestrator, or as a gate; give the agent targeted files only,
  and tell it to keep gaps between tool calls short.
- **Resume a stalled agent ONCE.** If the resume produces little or nothing,
  dispatch a FRESH agent with the on-disk state named in the prompt ("X and Y
  already exist; verify, do not rebuild"). Successive resumes degrade — measured
  4 resumes to zero output, then a fresh dispatch finished the same task in
  4½ minutes.
- **NEVER prescribe a workaround you have not verified.** Advice about a
  mechanism is a claim about that mechanism, and the same rule applies as to any
  other claim: verify it before handing it to an agent or the user. Telling two
  agents to "run the tests in the foreground" is what killed them.

Cost reporting itself is a Behavior rule, not repeated here: see the
"repeated step's cost visibly compounds" bullet in `~/.claude/CLAUDE.md`.

*Why:* in one plan session, roughly six verification cycles
ran at 10–60 minutes each. One gate invocation burned its full 1800s timeout without
returning a verdict, then silently restarted. Five subagent attempts died to the
watchdog. Every one of those costs was visible in advance to anyone who measured.
