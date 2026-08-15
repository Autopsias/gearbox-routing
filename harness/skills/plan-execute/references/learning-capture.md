# Learning capture (closes the loop into project memory)

<!-- Adopted 2026-07-09 from the everyinc/compound-engineering-plugin gap review
     (ce-compound / ce-compound-refresh / ce-debug mechanisms, adapted to the
     project-memory conventions plan-harden Fork A already reads). -->

Loaded on demand — read this at the END of a run: on `complete`, and on a **terminal
halt** (`blocked`/`halted`, including a verify gate that exhausted `max_rework` or a
failed shipping step you are handing back to the operator).

plan-harden's Fork A reads project memory when hardening the NEXT plan — this pass is
the write side of that loop. It is orchestrator work (you), never a subagent's. Run it
ONCE per run.

1. **Gather candidates.** `learnings` arrays across `_closeouts/*.json`, plus
   the run's own history: verify reworks and their `feedback_file`s, halt
   causes, `degraded_from` substitutions, `run.ndjson` failure events.
2. **Generalizability gate.** Skip silently anything mechanical or one-off.
   Capture only lessons that would change how a future plan is *built* or
   *run*: a session-sizing or model-tier mistake, a repo landmine, an approach
   that had to be reverted, a gate that always fails for the same reason.
   Zero qualifying learnings ⇒ write nothing — no noise.
3. **Grounding rules.** Every code-behavior claim quotes `file:line`. Cite PR
   numbers over bare commit SHAs (rebase/squash rewrites SHAs). Phrase
   unmerged fixes as pending, not landed.
4. **Overlap check BEFORE writing.** Search the project memory directory
   (`~/.claude/projects/<cwd-slug>/memory/` — the same store plan-harden Fork A
   reads). An existing memory already covering the problem → **update it**
   rather than create a duplicate (two docs describing the same problem will
   inevitably drift apart). Moderate overlap → create new and name the
   consolidation candidate inside it. Then update the `MEMORY.md` index line
   per the memory conventions.

## Memory maintenance (when capture touches existing memories)

Five outcomes, in preference order: **Keep** (still accurate — no write) ·
**Update** (solution right, references drifted — fix in place) ·
**Consolidate** (2+ overlapping-but-correct memories — merge into one
canonical, delete the subsumed) · **Replace** (misleading, with a known better
successor — write the successor, delete the old) · **Delete** (problem gone,
no successor). Delete, don't archive — git history is the archive. Age alone
is never a stale signal; contradiction with current reality is a strong
Replace signal. If you find yourself rewriting a memory's solution, that is
Replace, not Update.
