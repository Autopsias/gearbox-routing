# MISROUTES.md — append-only misroute ledger

Referenced by your `CLAUDE.md` routing digest's "routing receipt" line and by
`claude/evals/routing/README.md`'s re-run triggers.

**Purpose.** Routing is automatic BY CONSTRUCTION for dispatched work (agent
frontmatter pins + `verify-routing.sh`), but ADVISORY-WITH-OBSERVABILITY for
the main/interactive session (it cannot switch its own session model — see
`model-routing.yaml`'s `main_session:` block). This ledger is the
observability half of that: every time the actual tier used for a task
diverged from the `task_classes:` default (a main-session mismatch not
corrected via `/model`/`/effort`, or a dispatched task that needed an
unplanned escalation), log one row here. **Append-only — never edit or
delete a past row**, even if a later entry supersedes its lesson.

**Re-run trigger:** `claude/evals/routing/README.md` fires a full
recalibration pass when **≥2 entries land since `model-routing.yaml`'s
`last_reviewed`** — this ledger is literally the counter for that trigger, so
entries must be dated and never silently pruned.

## Format

One row per entry, oldest first:

```
### YYYY-MM-DD — <task shape, one line>
- expected class: <task_classes key> → <tier.effort>
- actual: <tier.effort used>
- cost symptom: <what the mismatch cost — $ delta, retries, wrong-depth answer, etc.>
- notes: <optional — root cause, whether corrected via /model or /effort>
```

## Entries

_(none yet)_
