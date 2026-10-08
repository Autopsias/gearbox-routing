# Pure-orchestrator invariant

Shared constraint text for commands that dispatch ALL fixes/implementation to subagents
and must never touch code directly themselves. Read this when you need the full
canonical wording; the calling command inlines only its own guard-rail check line plus
this pointer.

## Canonical constraints

- NEVER fix code / implement directly — you are a pure orchestrator.
- NEVER use Edit, Write, or MultiEdit tools on target/implementation files.
- MUST delegate ALL fixes/implementation work to subagents via the Task tool.
- Use Read/Bash/Grep for READ-ONLY analysis only — never to mutate code.

**GUARD RAIL CHECK**: Before ANY action, ask: "Am I about to do work directly?" → if YES,
STOP and delegate via Task instead.

## Documented exceptions (per-command, keep inline in the calling command)

Some orchestrators carry ONE narrow, explicitly-scoped exception (e.g. epic-dev's
full-workflow.md permits direct `Edit` on `sprint-status.yaml` / the story file's
`Status` field only, because that's orchestration bookkeeping, not implementation work).
Any such exception must stay stated in the calling command itself — this shared file is
the invariant, not the place to enumerate every command's carve-outs.
