---
description: "Mark this session as research/analysis so the compaction policy keeps the full 1M window (auto-compaction is deferred to ~817k on a 1M model instead of the 300k window). Use when you open a session to read, analyse, compare or study material rather than build. Usage: /research <what to study>."
argument-hint: "<what to study>"
---

This is a research/analysis session. `hooks/compact-policy.py` classified it `planning`
from the `/research` command itself (first word of the prompt) — there is nothing to run.
A wide window is a cost: every turn re-reads the whole context, so keep tool output lean
(read the sections you need, not whole files twice).

Proceed with the request: $ARGUMENTS
