---
name: improve
description: Review the current conversation and recent session history to suggest improvements to the project's configuration — CLAUDE.md, skills, frameworks, memory, agents (NOT source code architecture; for that use improve-codebase-architecture). Does a full sweep by default. Arguments add extra weight to specific areas. Works on any repo structure.
---

# Retrospective

Review conversation + recent history, cross-reference against config files, present improvement suggestions as one numbered list with one question for the set.

**Checklist (8 top-level phases):**
0. [ ] Mode check — audit mode or default sweep (see Modes)
1. [ ] Load Learnings
2. [ ] Scope Selection
3. [ ] Phase 1 & 2 — Discovery + History Scan (background, parallel)
4. [ ] Phase 3 — Current Conversation Analysis
5. [ ] Phase 4 — Cross-Reference & Categorize
6. [ ] Phase 5 — Present Findings
7. [ ] Phase 6 — Apply Changes
8. [ ] Save Learnings

**Announce:** "Starting retrospective..."

**Arguments = targeted feedback.** Always full sweep. Args get highest priority but don't limit scope. Works mid-conversation or end-of-session.

## Modes — check before anything else

**Default:** the retrospective below. It hunts for what is MISSING.

**`/improve audit` — relevance audit.** Scores what ALREADY exists (every memory,
every CLAUDE.md rule, every skill description) against recent transcripts, and is the
only path that proposes deletions and demotions. Also triggered without the argument
when the user asks to re-validate, re-score, prune or spring-clean existing rules,
memories or skills — "are these rules still true?", "what can we delete?", "which of
these ever fire?".
`Read ~/.claude/references/improve/relevance-audit.md` and follow it INSTEAD
of Phases 1–5. Load Learnings, the Phase 5 evidence gates, **Phase 6 Apply Changes**
and Save Learnings still apply — an accepted deletion or demotion is applied through
Phase 6 like any other accepted finding, and produces the same Changes Applied summary.
Its recipes are in `apply-changes.md`; the audit-only carve-outs are in the audit
file's Step 6.

## Load Learnings

Before scope selection, read `~/.claude/improve-learnings.md` if it exists. This file tracks patterns from prior runs: acceptance rates by category, modify signals, user preferences.

- **Deprioritize** finding types consistently rejected across runs, **Boost** categories consistently accepted. Count only individually answered findings; a batch answer ('Apply all' / 'Apply none') is not evidence about one category.
- **Adapt** rule-writing style based on modify signals (e.g., if user repeatedly softens NEVER to Avoid, propose softer language for non-critical rules)
- If file doesn't exist, proceed normally — it will be created at the end of this run

**Also read the evidence ledger `~/.claude/improve-ledger.json`** — it carries gap
sightings and past rejections across runs, and Phase 5's three evidence gates cannot
run without it. Apply the 90-day expiry on read, and treat a missing, unparseable or
wrong-shaped file as an empty ledger and SAY SO in the run summary.
`Read ~/.claude/references/improve/evidence-ledger.md` for the schema, the
lifecycle rules and the failure handling — that file is the contract.

## Scope Selection

Before launching any agents, ask the user:

**Question:** "What scope should this retrospective cover?"

**Options (AskUserQuestion):**
- **Historical + current conversation** — Full scan: history from recent sessions, prior /improve audit, plus current conversation analysis
- **Current conversation only** — Analyze only this session's patterns and feedback

Store the answer as the `scope` for the rest of the skill.

## Phase 1 & 2: Discovery + History Scan (Background, Parallel)

**If scope = "Historical + current conversation":** Launch ALL agents in background simultaneously, then immediately proceed to Phase 3.

**If scope = "Current conversation only":** Launch only the Discovery Agent in background, skip History Scan and Prior-Improve Cross-Check agents entirely, then immediately proceed to Phase 3. Announce: "Launching discovery agent (current conversation scope)."

### Discovery Agent (Explore, background — always runs)

Prompt the agent to search for and catalog ALL config-like files at BOTH project and global levels:
- CLAUDE.md (project root, project .claude/, AND global ~/.claude/CLAUDE.md)
- .claude/commands/ and .claude/skills/ directories (BOTH project-level AND global ~/.claude/commands/)
- .claude/agents/ directory
- .claude/rules/ directory (project-level)
- Shared frameworks, guardrails, style guides (shared/, frameworks/, etc.)
- Memory files: this project's `~/.claude/projects/[project-path]/memory/`, AND every other `~/.claude/projects/*/memory/` directory (cross-project memory)
- Settings files: project .claude/settings.json, .claude/settings.local.json, global ~/.claude/settings.json, ~/.claude/settings.local.json
- Voice/brand files (vault/, brand/, etc.)
- Any other instruction-like .md files governing behavior

Return a "config map": list of files with purpose, organized by type AND level (project vs global).

### History Scan + Prior-Improve Cross-Check Agents (full scope only)

Full-scope-only branch (never runs in "Current conversation only" scope):
`Read ~/.claude/references/improve/full-scope-history-agents.md` for the
History Scan Agent prompt (background, cross-session feedback extraction) and the
Prior-Improve Cross-Check Agent prompt (audits whether prior `/improve` runs' accepted
changes actually landed). Launch both per that file, in parallel with Discovery.

Full scope also runs the 'Question-class check' in that file: it proposes a 'Standing default' finding for a class of question you answer the same way almost every time.

## Phase 3: Current Conversation Analysis (Foreground)

**Announce:** "Analyzing current conversation for patterns, feedback, and techniques..."

Analyze the conversation already in context for:

| Signal | What to Look For |
|--------|-----------------|
| **Corrections** | User corrected behavior, said "no", "don't", "stop", asked to redo |
| **Praise** | User confirmed approach, said "yes", "perfect", accepted without pushback |
| **Friction** | Multiple attempts, confusion, back-and-forth to get it right |
| **Capability gaps** | User did things manually, asked for something assistant couldn't do |
| **Behavioral patterns** | Tone issues, over/under-explaining, wrong assumptions |
| **Targeted feedback** | Arguments passed to /improve — HIGHEST PRIORITY |
| **Repeated workflows** | Multi-step manual processes that could become a skill |
| **Techniques discovered** | Novel approaches that worked well — new methods, clever tool usage |
| **User interaction patterns** | User prompting styles that led to better/worse results |

**Capture the quote as you go.** Record every signal with the word-for-word text that
produced it — never a paraphrase. A finding with no quote cannot be presented (Phase 5,
gate 1), so a quote not captured here is a finding lost later.

**Then score the agent's own conduct.** The table above reads the user's signals; the
conversation also shows the agent's. Read
`~/.claude/references/improve/transcript-scoring.md` and score the current
conversation against both rubrics — session waste, and artifact quality where code
was edited. Convert scores to finding candidates per that file's "From score to
finding" — a session with zero user complaints can still yield a Critical finding.

**Low-signal:** If minimal feedback in current conversation, say "No significant findings from this session" and proceed to history/config findings.

## Phase 4: Cross-Reference & Categorize

**Announce:** "Cross-referencing findings against config files..."

Wait for background agents to complete.

### Agent Failure Handling

After waiting for agents to complete, validate each result before proceeding:

**Discovery Agent (critical path):**
- If it fails or returns empty: fall back to hardcoded scan of known config paths directly in foreground:
  - `~/.claude/CLAUDE.md`, `~/.claude/commands/`, `~/.claude/skills/`, `~/.claude/agents/`
  - `.claude/settings.json`, `.claude/settings.local.json`
  - `~/.claude/projects/[project-path]/memory/`
- Announce: "Discovery agent failed — using fallback config path scan"

**History Scan / Prior-Improve agents (optional):**
- If they fail: gracefully degrade to current-conversation-only scope
- Announce: "History scan agent returned no results — skipping cross-session analysis for this run"
- Skip Phase 4b (Pattern Promotion) and Prior-Improve audit display

**Key principle:** Never silently proceed with incomplete data — always tell the user what was skipped and why.

**Stamp the full-scope run.** Only when scope is still "Historical + current conversation" here (no fallback to current-conversation scope for any reason, including a Prior-Improve agent failure) and the History Scan agent reported BOTH `TRANSCRIPT SCAN: complete` and `REVIEW-FINDINGS SCAN: complete` (an empty but successful scan counts; a failed or unreadable one does not), run `mkdir -p ~/.gearbox-state/improve && touch ~/.gearbox-state/improve/last-full-scope`. Do not touch it after a current-conversation run or after a fallback. An optional nudge hook (not part of this export) reads this path.

Then read each config file from the config map.

### 4a: Enforcement Gap Detection

For each existing rule in config files, check if the current conversation shows it being violated.

**First, for every violated rule, ask: can the thing that makes the mistake possible be
removed or restructured** — a stale file, an unpinned default, a shell option? If yes,
propose **"Remove the cause"** with the exact change (the file to delete, the default to
pin, the option to drop), and stop there for that rule: do not also propose strengthening
it or converting it to a hook. The two branches below, and the "generate the complete
implementation" hook instructions, run **only when removal is not possible** for that
rule:

- If the rule was violated once: suggest strengthening (emphasis, position, examples) — not removal
- If the rule shows a pattern of repeated violation (across sessions in full scope, OR multiple times within the current conversation in current-only scope): suggest **converting to a hook** instead — hooks are deterministic enforcement, while CLAUDE.md instructions are probabilistic (~80% compliance)

When suggesting "convert to hook" (removal not possible), **generate the complete implementation**:

- Detect the right hook event based on rule type:
  - `PreToolUse` with `Bash` matcher: for command gating rules
  - `PreToolUse` with specific tool matcher: for tool-specific rules
  - `PostToolUse`: for validation after tool execution
  - `Notification`: for reminders and announcements
- Generate the actual hook JSON config ready to paste into settings.json
- Include the shell command/script that enforces the rule
- Note: "This rule is currently advisory (~80% compliance as a CLAUDE.md instruction). As a hook, it becomes 100% deterministic."

When presenting enforcement gap findings in Phase 5, offer four options:
- "Remove the cause" — delete or restructure the thing that makes the mistake possible (the stale file, the unpinned default, the shell option), naming the exact change. Presented alone when removal is possible; the other options do not apply to that finding.
- "Strengthen rule" — rewrite with NEVER/ALWAYS emphasis, move to top of file
- "Convert to hook" — create a hook that enforces the rule deterministically. Include the generated JSON config in the finding. Follow up with a second AskUserQuestion: "Which scope should this hook be configured at?" with options: "Project (.claude/settings.json)", "Global (~/.claude/settings.json)", "Project local (.claude/settings.local.json)"
- "Both" — strengthen the rule AND add a hook as backup enforcement

### 4b: Progressive Evolution (Pattern Promotion) — full scope only

**Skip this sub-phase entirely in current-only scope** (requires cross-session history).
Full content (the promotion rules): `Read ~/.claude/references/improve/full-scope-history-agents.md`.

### 4c: Config Health & Consolidation

Run ALL sub-checks against BOTH project-level and global-level configs from the Discovery Agent config map.

#### CLAUDE.md Budget — measured every run

The global CLAUDE.md has a budget of **5,000 estimated tokens**. The estimator is
**bytes ÷ 4** — one command, no tokenizer, same arithmetic every run:

```bash
wc -c ~/.claude/CLAUDE.md      # bytes; ÷ 4 = estimated tokens; budget 5,000
```

Measure the **deployed** file — that is the one loaded into every session, not a
source copy — by running the command in THIS run. Never carry a figure forward from a
previous run, from this file, or from memory.

**Report the measured figure in every run's config-health note, whatever it says:**
`global CLAUDE.md: 13,477 bytes ≈ 3,369 est. tokens — 67% of the 5,000 budget
(wc -c ~/.claude/CLAUDE.md)`. A run that reports no figure has not measured.

- **Under budget** — no finding. Additions are proposed normally; the figure is still
  shown.
- **At or over budget** — one Maintenance-tier finding carrying the measured number,
  and the zero-sum rule below is live.

**Zero-sum rule (applies while the file is at or over budget).** Every finding whose
edit ADDS text to the global CLAUDE.md must name, in that same finding, the removal or
extraction that pays for it: the exact lines to delete, or the section to move into a
skill / `.claude/rules/` file / memory, with its own byte count. "Trim it later" is
not a payment. A finding that cannot name one is **not presented** — say so in the run
summary ("held: <finding> — CLAUDE.md over budget, no offset found"). Accepting such a
finding applies the addition and its payment together in Phase 6, never just the
addition.

Measured here too, same estimator and reported the same way: any project-level
CLAUDE.md (the same 5,000-token budget is a sane default for one), and the memory-file
count — flag if >20 files in a single project's memory directory.

#### The remaining config-health sub-checks

Memory consolidation, rule and skill extraction, skill consolidation, cross-skill
consistency, the five-direction Content Placement Audit, skill budget monitoring,
skill description quality, CLAUDE.md structural validation and cross-level analysis:
`Read ~/.claude/references/improve/config-health-checks.md` and run every
check in it. Their findings enter Phase 5 like any other.

### 4d: Categorize All Findings

| Category | When to Use | Priority |
|----------|-------------|----------|
| **Targeted** | From user's explicit /improve args | 1st |
| **Critical** | Caused errors, repeated correction, enforcement gaps | 2nd |
| **Promotion** | Recurring cross-session pattern needing stronger rule | 3rd |
| **Content Misplacement** | From Content Placement Audit — content living in wrong config layer | 4th |
| **Improvement** | Enhancement to existing rules/skills/behaviors | 5th |
| **Technique** | Novel approach that worked — document for reuse | 6th |
| **Maintenance** | Config health: bloat, contradictions, staleness, budget, descriptions | 7th |
| **Reinforcement** | Worked well — strengthen existing documentation | 8th |
| **New Skill** | Repeated pattern that could become a dedicated skill | 9th |
| **User Coaching** | Gentle suggestion for better user-AI interaction | Last |

Skip findings that are already documented AND being followed.

### Confidence Scoring

Assign a confidence level to every finding as a secondary axis:

| Level | Criteria |
|-------|----------|
| **High** | 3+ supporting signals, or recurrence across 2+ sessions, or direct user correction |
| **Medium** | 1-2 signals from current session, or pattern match without direct evidence |
| **Low** | Speculative — inferred from config structure or best practices, no direct user signal |

**Scope note:** In current-only scope, "recurrence across 2+ sessions" is unavailable. Session-based signals cap at Medium unless there's a direct user correction.

Confidence doesn't change priority order (Critical still beats Improvement regardless of confidence), but helps the user decide scrutiny level — high confidence findings can be accepted faster, low confidence ones deserve more thought.

## Phase 5: Present Findings

**Announce:** "Found N findings across M categories. Listing them most impactful first, then one question for the set."

### Evidence Gates — run on EVERY finding before it is presented

Three gates, in order. A finding that fails any one is **not shown to the user**.
Full rules, edge cases and the ledger's failure handling:
`Read ~/.claude/references/improve/evidence-ledger.md`.

1. **Verbatim quote (all findings).** Every finding presented MUST carry a
   word-for-word excerpt from a real source, cited. Three source kinds count, and which
   one applies is decided by where the finding came from:
   - **Conversation-derived** (Phases 3, 4a, 4b — signals, corrections, patterns): a
     quote from a real transcript or the current conversation, cited with its session.
   - **File-derived** (Phase 4c Config Health, Content Placement, Cross-Skill
     Consistency — findings produced by *reading config*, not by a user signal): the
     excerpt is the config text itself — the contradicting line, the stale reference,
     the misplaced section — cited as `path:line`. A pure measurement (a line count, a
     file count) cites the measured file and the number, and states the command that
     produced it.
   - **Review-ledger-derived** (the History Scan's 'Plan review findings' step, for
     'Remove the cause' candidates): the excerpt is the finding's `summary` field word
     for word, cited as `_plans/<plan>/_verify_state/<file>.findings.ndjson:<line>`
     plus its `fid`.

   A finding with no such excerpt is **DISCARDED — never softened, never presented
   with hedged wording**. "I noticed a pattern" is not evidence. This gate demands a
   citation, not a user complaint: it does not remove the Low confidence tier, it
   forces a Low-confidence finding to point at the exact text it was inferred from.
2. **Two-session corroboration (new-rule findings only).** A finding whose edit only
   ADDS text — a new rule, memory file, skill or section — needs **2 distinct sessions**
   behind it. **Record this run's sighting FIRST, then count** the distinct `session`
   values among that gap's live sightings — this run's included. ("Record" means into
   the live ledger held in memory since Load Learnings; Save Learnings writes it to
   disk at the end of the run.)
   Order matters: counting before recording would demand two PRIOR sessions and make
   the gate fire on the third run, not the second. 2 or more → present. Below that, do
   not present it: say so in the run summary, naming the gap and the count ("1st
   sighting of `<key>` — needs a second independent session"). This is a hold, not a
   rejection; it graduates on a later run. Findings that strengthen, reword, move or
   delete existing text are exempt.

   The count is only as good as the gap key. Before minting a new slug, scan the
   ledger's existing keys for one naming the same gap and reuse it — see the ledger
   reference's "Matching a finding to a gap key".
3. **Rejection check (all findings).** Compare against the ledger's `rejections`. A
   match — same target file, substantially the same edit — is re-proposed ONLY with
   materially new evidence: a quote that is neither in that rejection's
   `evidence_then` nor from a session already represented there. If it clears the bar,
   state it explicitly in the finding ("rejected <date> because <reason>; new evidence
   since: <quote>"). If it does not, drop the finding.

**Budget check (not a fourth gate — the ledger is not involved).** While the global
CLAUDE.md is at or over its 5,000-token budget, a finding that adds text to it is
presented only with its offset named, per 4c's zero-sum rule. Findings targeting any
other file are unaffected.

Count what each gate removed — and what the budget check held — and report it with the
finding count, so a quiet run is visibly a filtered run rather than an empty one.

### Rule-Writing Quality Standards

All proposed rule changes MUST:
- Start critical rules with **NEVER** or **ALWAYS**
- Lead with **WHY** so edge cases can be judged
- Use precise language: "try to" → "always", "consider" → "must"
- Include a concrete example when not self-evident
- Keep concise — one clear sentence beats a paragraph

### Presentation

**FIRST — Audit of Prior `/improve` Runs (full scope only)**

**Skip this section entirely in current-only scope.** Go straight to presenting findings.
Full scope: `Read ~/.claude/references/improve/full-scope-history-agents.md` for
the audit table format and the full-scope presentation order (it adds two leading
buckets — Drifted, Re-surfaced — before Targeted).

**THEN — List All Findings, Ask One Question (default mode; `/improve audit` is in step 5)**

1. **Print every finding as a numbered list in the chat.** Each entry keeps these fields: "[Tier | Confidence] — [Source: current conversation / past session date] — [Description and proposed change]. Evidence: "[verbatim quote, or cited config excerpt `path:line`]". File: [full path]. Proposed: [what to add/modify/remove]". Mark each entry `[batch]` or `[own question]`. Decide it from the first option the finding lists (the one 'Apply all' would apply) BEFORE you count: an enforcement-gap finding whose first option is a 'Remove the cause' that deletes is `[own question]`. List `[own question]` findings last, under the heading 'Asked one by one'.
2. **Ask ONE AskUserQuestion:** 'Apply all N (Recommended)' / 'Review one by one' / 'Apply none'. N counts the `[batch]` findings only. If N is 0, skip this question and ask each `[own question]` finding on its own. A free-text answer that names numbers ('all except 3 and 7') applies the rest of the batchable findings and treats the named ones as excluded.
3. **What each answer does.**
   - 'Apply all N': apply each batchable finding as its 'Proposed:' line says. For an enforcement-gap finding that is the first option the finding lists ('Remove the cause' when removal is possible, else 'Strengthen rule'). 'Modify' is never part of 'Apply all'. Then ask each exception finding on its own, one question each (Accept / Reject / Modify), in presentation order.
   - 'Review one by one': ask every finding, exceptions included, with Accept / Reject / Modify. If 8+ findings, after presenting 5, ask: "Continue with remaining findings, or apply what we have so far?"
   - 'Apply none': apply nothing from the whole list, exceptions included, and ask nothing more. Say in the run summary 'T findings declined as a set (B batch, E own question)'.
4. **Keeps its own question.** Write this as one short block under the list. A finding keeps its own question when its edit (a) removes, demotes or moves existing text or a file out of where it lives: a deleted file or memory, a deleted or demoted rule, a Content Misplacement move, a 'Remove the cause' that deletes; or (b) changes a hook, a permission or a deny entry in any settings file, including 'Convert to hook' and 'Both' (their scope follow-up question stays); or (c) is a 'Standing default' finding: its question names the default text and the target file, and its first option starts with 'Apply this default (Recommended)'. An exception is applied only after its own yes; 'Apply none' declines it with the set.
5. **`/improve audit` never offers 'Apply all'.** Each audit deletion, demotion and correction keeps its own question (see `relevance-audit.md`).

**Order (current-only scope; full scope's order is in the reference file above):**
1. Targeted (from /improve args)
2. Critical → Promotion → Content Misplacement → Improvement → Technique → Maintenance → Reinforcement → New Skill → User Coaching

## Phase 6: Apply Changes

**Announce:** "Applying N approved changes across M files..."

**If any target file is under a Gearbox-managed `~/.claude` (a deploy target, not source):**
apply the edit in the source worktree `~/your-private-harness` instead, then deploy
(`gearbox-deploy` or `git -C ~/.claude pull --ff-only`) — never hand-edit harness files
directly in `~/.claude`. Project-level files and `~/.claude`'s own runtime state
(`projects/*/memory/**`, `_plans/**`) are unaffected by this rule — those are written
directly where found.

1. Group approved changes by file
2. Edit existing files with approved modifications
3. Create new files if needed (new memory entries, new skill stubs)
4. Apply each approved finding by its type. Hook conversions, rule extractions,
   memory merges, skill extractions, feedback memories, Content Misplacement moves
   and Skill Description rewrites each touch a second file (a settings file, the
   MEMORY.md index, the source the text left) and each has its own sequence:
   `Read ~/.claude/references/improve/apply-changes.md` and follow the
   recipe for every approved finding whose type appears there. A plain edit to an
   existing file needs nothing from that file.
5. Present a summary table in the conversation:

    ## Changes Applied

    | # | File | Change | Category |
    |---|------|--------|----------|
    | 1 | path/to/file.md | Brief description of what changed | Category |

    N changes across M files.

    - **File**: short relative path (not full absolute)
    - **Change**: concise action (e.g. "Added rule: …", "Strengthened: X → Y", "New memory: …", "Hook added: …", "Rule extracted: …", "Memory merged: …", "Skill created: …", "Content moved: …", "Description rewritten: …")
    - **Category**: tier from Phase 4d (Critical, Promotion, Content Misplacement, Improvement, etc.)
6. Record every outcome in the evidence ledger (written in Save Learnings) — in audit
   mode, with that mode's two carve-outs (`relevance-audit.md` Step 6):
    - **Batch answers:** every finding applied by 'Apply all' is an Accepted outcome. A number named in a free-text answer is a Reject with the reason 'excluded by number, no reason given'. 'Apply none' is NOT a rejection: write no entry to `rejections`, keep the gap sightings.
    - **Accepted** (or Modified — a Modify is an acceptance, not a rejection): set that
      gap's `retired` to today. The rule now covers it.
    - **Rejected**: append to `rejections` — the edit summary naming the target file,
      today's date, every verbatim quote that supported it this run (`evidence_then`),
      and the user's stated reason ("no reason given" if they gave none, never invented).
7. Ask if user wants to commit changes

## Save Learnings

After Phase 6 completes (regardless of whether any changes were applied), write BOTH
files — the ledger first.

**1. Evidence ledger `~/.claude/improve-ledger.json`.** Write back the live ledger
loaded in Load Learnings, including this run's new sightings (gate 2), retirements and
rejections (Phase 6 step 6). **Write it even when nothing was applied and even when no
finding was presented** — the held-back sightings are the whole point, and a run that
only saves on success loses them. Schema and rules:
`~/.claude/references/improve/evidence-ledger.md`.

**Full-scope stamp:** already touched in Phase 4 after Agent Failure Handling (`~/.gearbox-state/improve/last-full-scope`); nothing to do here, and never touch it after a fallback or current-conversation run.

**2. Learnings `~/.claude/improve-learnings.md`:**

1. Read current file (or create if first run)
2. Append new entry under `## Recent Runs`:
   - Date of run
   - Acceptance rate by category (e.g., "Critical: 3/3 accepted, User Coaching: 0/2 accepted"), counting individually answered findings only
   - Batch-accepted findings as their own count (e.g., "Batch: 14 accepted"), apart from the per-category rates
   - Any "Modify" choices that reveal preferences (e.g., "user softened NEVER→SHOULD for style rules")
   - Detected patterns (e.g., "user prefers hooks over rule strengthening")
3. If file exceeds 80 lines: summarize oldest raw entries into `## Patterns` section at the top (e.g., "3 runs rejected User Coaching tier → pattern: deprioritize"), then delete those raw entries
4. Save updated file

**File structure (for first-run creation):**
```
# Improve Learnings

## Patterns (summarized from older runs)

## Recent Runs
### YYYY-MM-DD
- Acceptance: Critical 3/3, Improvement 2/4, User Coaching 0/1
- Modify signal: User changed "NEVER" to "Avoid" in a style rule
```
