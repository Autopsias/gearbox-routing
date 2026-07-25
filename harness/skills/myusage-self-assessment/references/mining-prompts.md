# Mining-agent prompt templates (Phase 2)

Fan-out makes the assessment reproducible and keeps any single agent from having
to read the whole ~1 GB corpus. Dispatch **one miner per substantial project**
plus **one cross-project miner**. Every miner reads the digest JSON (and, if it
needs to drill into a specific cluster, the per-session array or a handful of
named raw transcripts) — never the full corpus.

All miners must return findings in the **same taxonomy** so Phase 3 can merge them
mechanically. A miner that invents its own categories breaks clustering.

## Signal taxonomy (verbatim — give this to every miner)

- **recurring jobs** — the same task done by hand repeatedly across sessions
- **boilerplate** — repeated multi-step sequences ripe for a bundled script
- **friction** — corrections, frustration, re-explanations; quote the user verbatim
- **infra failures** — dead MCPs, auth/credential errors, broken hooks, API drops
- **workflow health** — Read:Edit ratio, edits-without-prior-Read, model fit
- **interrupts / denials** — where the human halts, or the classifier blocks
- **waste** — automation firing on no-ops, re-derived scripts, redundant runs

## Per-project miner prompt

```
You are a session-transcript miner for project <PROJECT_DIR>.

Inputs (read these, NOT the raw 1 GB corpus):
  - Digest: /tmp/self_assessment_digest.json  (aggregate + per-project metrics)
  - Per-session: /tmp/self_assessment_sessions.json  (one record per session;
    filter to project_dir == "<PROJECT_DIR>")
  - When a cluster needs proof, you MAY open up to ~5 named raw transcripts at
    ~/.claude/projects/<PROJECT_DIR>/<session_id>.jsonl to pull exact quotes.

Task: find every recurring signal for this project. For EACH finding output:
  - taxonomy label (one of: recurring jobs, boilerplate, friction, infra
    failures, workflow health, interrupts/denials, waste)
  - one-line description of the pattern
  - recurrence: how many sessions / how many times (with the denominator)
  - representative session-id citations (2-5 real session_ids)
  - for friction: 1-2 verbatim user quotes with their session_id
  - your read on whether an installed skill/command already covers this

Bar: surface only patterns that RECUR. A one-off is context, not a finding.
Do not propose solutions — that is the clustering phase's job. Report signals.

Return a compact markdown list grouped by taxonomy label.
```

## Cross-project miner prompt

```
You are the cross-project miner. Read /tmp/self_assessment_digest.json and
/tmp/self_assessment_sessions.json in full, plus ~/.claude/history.jsonl-derived
tables inside the digest (history.by_project, history.commands, history.babysit,
history.top_prompts).

Task: find signals that only appear ACROSS projects or in the typed-prompt
stream — the things a single-project miner cannot see:
  - babysitting/polling load (proceed / continue / yes / check / retry counts)
  - commands installed but never invoked (dead trigger budget)
  - the same job recurring in multiple projects (→ user-level, not per-project)
  - automation fleets: what share of ALL sessions is auto-spawned vs interactive
  - error families that span projects (schema fails, API drops, auth errors)
  - model-mix / routing anomalies

Same output contract as the per-project miners (taxonomy label, recurrence with
denominator, session-id or history citations, verbatim quotes for friction).
Explicitly flag any installed skill/command with ZERO invocations — that is dead
trigger-budget, a candidate for pruning.
```

## Dispatch notes

- Run miners at the strongest available tier (Fable, else Opus xhigh). Mining is
  judgement work — cheap tiers miss the "recurs AND matters" line.
- Keep each miner's tool budget bounded (digest + a few named transcripts). A
  miner that tries to grep the whole corpus will stall; the digest exists so it
  doesn't have to.
- If a project is tiny (a handful of sessions), fold it into the cross-project
  miner rather than spending a whole subagent on it.
- Collect all miner outputs into one place before clustering; do not cluster
  incrementally as each returns (you will over-weight whoever finished first).
