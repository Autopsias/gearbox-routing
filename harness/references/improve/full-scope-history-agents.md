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
   - For each file, extracts ONLY owner prompts: records with `type "user"` (Claude Code never writes `human`) whose message content is a string or a `text` block. Tool results share the `user` type (their content is a `tool_result` block): skip them, and skip assistant responses and tool calls
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

3. Score each sampled session against the two rubrics in
   `~/.claude/references/improve/transcript-scoring.md` — session waste, and
   artifact quality where the session shows code edits. The evidence is on the AGENT
   side of the transcript: extract it cheaply with grep (repeated identical commands,
   error-then-retry runs, tool-call density), never by reading a transcript in full.
   Return one label per rubric per session, each with the verbatim moment that drove
   it, cited with its session.

4. **Plan review findings** (full scope only). Find what the plan reviews flagged in the last 30 days and group it by cause. Read `<target repo>/_plans/*/_verify_state/*findings.ndjson` with one short script that writes a temp file. Rules:
   - **Window:** use the date suffix of the plan directory name (`YYYY-MM-DD`), never file mtime: the files are git-tracked and a checkout resets mtime. Plans still running in a worktree (`<repo>/.plan-worktrees/<plan>` exists, or a suffixed one such as the land worktree `<repo>/.plan-worktrees/<plan>__land-<id>`) are not counted; say so in the output.
   - **Valid row:** a line counts only when it parses to a dict with `kind == "finding"`, `fid` a non-empty string, `attempt` an int (not a bool) and `summary` a non-empty string. Reject every other finding-shaped or unparseable line, and report the number of rejected lines and the files they came from. Never skip silently.
   - **One row per finding:** per (plan, `fid`) keep the row with the highest `attempt`; on equal attempts keep the later line in the file.
   - **Keep every `status` and every `new_in`.** A fixed or noted finding was still a real occurrence. `new_in == "outside"` means outside the CURRENT rework attempt's change (`skills/plan-execute/scripts/llm_review_ledger.py`), not outside the plan, so dropping it loses real defects.
   - Extract plan, file, line, severity and summary.
   - **Group by cause**, not by wording or severity. Write every finding's class to a file (`plan<TAB>fid<TAB>class`) so the grouping can be recounted.
   - **A cause seen in 3 or more distinct plans** becomes a finding candidate of type 'Remove the cause' (`commands/improve.md` section 4a; `transcript-scoring.md` already defines it, do not copy it). It names: the exact automatic check that would catch it (a lint rule, a test, a guard); three verbatim summaries from three DIFFERENT plans, each cited as its ledger `path:line` and `fid` (the review-ledger source kind of gate 1), as the gate 1 quote; and the plan count. A cause in 2 plans is not proposed.
   - **Covered is not proposed.** Report a class as covered only when the check that prevents it exists AND is invoked: cite the check's `file:line` and the gate, hook or test run that calls it. A grep hit in a comment, a fixture or an unused function is not coverage.
   - **Open memory candidates:** also list memory notes in the target project (`~/.claude/projects/<project>/memory/`) that carry an open `Remove-the-cause candidate (open)` line (written by plan-execute's learning capture). Present each as a 'Remove the cause' finding. Its gate 1 quote is the memory note's candidate line, cited as `path:line` (file-derived).
   - Write the extraction as one script and run it against a small fixture tree first (a cause in 2 plans and one in 3, a duplicate `fid`, a tie, a null `fid`, a string `attempt`, a truncated line, a plan older than 30 days) before the real run.

5. **Completion lines.** End your report with one line per scan, exactly: `TRANSCRIPT SCAN: complete` or `TRANSCRIPT SCAN: failed (<why>)`, and `REVIEW-FINDINGS SCAN: complete` or `REVIEW-FINDINGS SCAN: failed (<why>)`. An empty but successful scan is `complete`. A scan that failed or could not read its files is `failed`. The caller touches `~/.gearbox-state/improve/last-full-scope` only when BOTH say `complete` (see `commands/improve.md`, Phase 4).

Return categorized findings with source citations. Concise summaries, not raw data.

## Prior-Improve Cross-Check Agent (general-purpose, background — full scope only)

Launch this as a 3rd background agent in parallel with Discovery and History Scan. Its job: audit what prior `/improve` runs recommended and whether their accepted changes actually landed.

For each session file identified by History Scan, check if `/improve` was invoked in that session (`grep -l "/improve"` on the .jsonl). For every session where it was:

1. **Extract the "Changes Applied" summary table** (the final markdown table that `/improve` prints in Phase 6). Or if no table is present, parse the AskUserQuestion responses, in this order of shapes. 'Apply all N': every `[batch]` finding in the numbered list printed in the assistant message just before the question was accepted, except numbers named as excluded; each `[own question]` finding takes the answer of its own question (never read as accepted from the batch answer). 'Apply none': none accepted. Free-text numbers ('all except 3 and 7') are resolved against that printed list. 'Review one by one': per-finding Accept/Reject/Modify. Capture: recommendation text, target file, decision.

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

Return only the report above. Cite session dates and target files.

## Question-class check — full scope only

Counts the questions agents asked you and proposes a standing default for a class you answer the same way almost every time.

1. **Run it.** `python3 ~/.claude/scripts/improve_ask_classes.py --days 30`. That file exists there only after `gearbox deploy`; if it is missing, run the same file from `~/your-private-harness/scripts/`. (`--days` is the real flag: a bare `30` is rejected.)
2. **Read the evidence ledger first.** For a class with an adopted default (gap `standing-default-<class-slug>` with `retired` set) or a rejected one (a rejection whose edit starts 'Standing default for <class-slug>'), re-run with `--after <that date>`. The count restarts at adoption or rejection: an adopted default that agents ignore shows up again as an 'Ignored default' (see `evidence-ledger.md`, 'Matching a finding to a gap key'), and a rejected one needs a fresh pass of the bar.
3. **Each class with status PASS** becomes one finding of type 'Standing default' (tier Improvement, confidence High). It names the class, its count and sessions, three verbatim member questions (the gate 1 quote), the proposed default text and its target file. Find the target file by grepping a verbatim member question across tracked files (`commands/`, `skills/`, and the repo of the project directory where it was asked). The project directory only says where it was asked, not who owns it. A class with no match, or a match in another repo, is reported and not edited from here. The default text names the class, the default answer, and the sentence 'state the default taken in one line'.
4. **Answer check, mandatory before any finding.** Run `--members <slug>` and read EVERY member's first option. State in one sentence the one answer they all express. If the first options contain opposite decisions (for example 'Retry' and 'Stop'), split the class ONLY by a condition visible before the answer is known (a header, a named repo or plan, a phrase in the question text). Each part then gets its own slug `<class-slug>--<condition-slug>`, its own count against the bar and a default text that states its condition. If no such condition separates the opposite answers, report 'answers split, no condition' and propose no default; never propose two defaults for one question. A class's 95% first-option rate is agreement with the agent's own recommendation, not proof of one answer. The finding lists the number of members per answer group.
5. **Model-read classes.** A class that only a model read (from `--candidates`) must list all its members and pass the same per-member excluded check by hand. Never propose a default for a class with status NEAR, NO or EXCLUDED, and never lower the bar to make one pass.
6. **Gates.** Gate 2 is satisfied by the class's own 3 or more sessions; record a sighting for gap `standing-default-<class-slug>` as gate 2 does. Gate 3 applies as for any finding: record a rejected default with the edit 'Standing default for <class-slug> in <target file>'.
7. **Own question.** A 'Standing default' finding keeps its own question (see 'Keeps its own question' in `commands/improve.md`). The question names the default text and the target file; its first option starts with 'Apply this default (Recommended)' so the module counts it as class 1b. The module never lists class 1b as eligible.

Known limit: nothing re-confirms an adopted default later; the since-adoption count is the only check.

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
