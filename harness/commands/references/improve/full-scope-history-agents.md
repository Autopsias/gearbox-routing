# Full-scope history agents + cross-session mechanics

Read this ONLY when scope = "Historical + current conversation" (the full-sweep path).
When scope = "Current conversation only", none of this applies — only the Discovery
Agent runs, and Phase 4b / the Prior-Improve audit / cross-session confidence rules are
skipped entirely per the main command file.

## History Scan Agent (general-purpose, background — full scope only)

Prompt the agent to:

1. Write a bash script that:
   - Lists .jsonl session files from `~/.claude/projects/[project-path]/`
   - Sorts by modification date, takes 5 most recent (excluding current session)
   - For each file, extracts ONLY lines containing user messages (type "human") — skip assistant responses and tool calls
   - From user messages, filters for feedback signals:
     - Corrections: "no", "don't", "stop", "not that", "wrong", "actually", "instead"
     - Praise: "yes", "perfect", "exactly", "great", "love", "nice"
     - Explicit feedback: "improve", "better", "should", "could you", "I wish", "next time"
     - Frustration: repeated requests, "again", "I already said"
   - Saves extracted messages with session date to a temp file
   - No cap on signals — let all relevant feedback through

2. Read temp file and organize findings:
   - Tag each with: session date, brief context quote
   - Group by type: corrections, praise, friction, capability gaps
   - Note recurring patterns across sessions (same feedback 2+ times = promotion candidate)

Return categorized findings with source citations. Concise summaries, not raw data.

## Prior-Improve Cross-Check Agent (general-purpose, background — full scope only)

Launch this as a 3rd background agent in parallel with Discovery and History Scan. Its job: audit what prior `/improve` runs recommended and whether their accepted changes actually landed.

For each session file identified by History Scan, check if `/improve` was invoked in that session (`grep -l "/improve"` on the .jsonl). For every session where it was:

1. **Extract the "Changes Applied" summary table** (the final markdown table that `/improve` prints in Phase 6). Or if no table is present, parse the AskUserQuestion responses for Accept/Reject/Modify decisions on each recommendation. Capture: recommendation text, target file, decision.

2. **For each Accepted recommendation**, verify the proposed change actually landed:
   - Read the target file mentioned in the recommendation.
   - Grep for the key phrase / rule text that was supposed to be added.
   - Mark as **Verified Implemented** (key text present), **Drifted** (file exists but text missing or modified), or **Missing** (target file doesn't exist).

3. **For each Skipped / Rejected recommendation**, flag for re-surfacing:
   - Original date + session ID
   - What was recommended and why declined (if captured from user's notes)
   - Whether the underlying friction has recurred since (cross-reference with current History Scan signals)

Return a structured report:
- **Prior `/improve` runs:** N (list dates)
- **Verified implemented:** X (no re-action needed — surface to user for confidence/audit trail)
- **Accepted but drifted/missing:** Y (needs re-application)
- **Previously skipped but still signaling:** Z (re-surface as current-run findings)

Keep total output under 400 words. Cite session dates and target files.

## Phase 4b: Progressive Evolution (Pattern Promotion) — full scope only

When history scan shows the same feedback across 2+ sessions, suggest PROMOTING:
- Memory file → CLAUDE.md rule
- Buried rule → top of CLAUDE.md with NEVER/ALWAYS emphasis
- Implicit pattern → explicit documented rule with examples
- Soft guideline → hard rule with enforcement language

## Phase 5 presentation: Audit of Prior `/improve` Runs — full scope only

Before presenting any new findings, surface the Prior-Improve Cross-Check report as an audit trail:

```
## Audit of Prior /improve Runs

| Date | Recommendations | Implemented | Drifted | Skipped |
|------|----------------|-------------|---------|---------|
| 2026-04-11 | 6 | 6 ✅ | 0 | 0 |
| 2026-04-09 | 5 | 3 ✅ | 1 ⚠️ | 1 (re-surfaced below) |
```

This gives the user confidence (verified-implemented), highlights drift (needs re-application), and re-surfaces previously-skipped items as new findings to reconsider. NEVER silently drop prior findings — always show the verification.

Presentation order in full scope adds two leading buckets before Targeted:
1. Drifted items (prior accept didn't land — needs re-application)
2. Re-surfaced previously-skipped items (with note: "previously skipped on [date]")
3. Targeted (from /improve args)
4. Critical → Promotion → Content Misplacement → Improvement → Technique → Maintenance → Reinforcement → New Skill → User Coaching
