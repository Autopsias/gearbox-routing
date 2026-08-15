---
description: "Dual-model adversarial review. Claude (Opus) and Codex independently review from complementary perspectives, then Claude synthesizes unified findings. If a plan is in context, automatically produces a hardened revision, then loops with Codex (read-only, capped) until it verifies the fixes actually land. Use for deep dual-model code/plan review, or when routed here via /review --deep."
argument-hint: "[optional free-text hint to narrow scope] [--background] [--synthesize]"
allowed-tools: ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "Skill", "EnterPlanMode", "ExitPlanMode", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "mcp__perplexity-ask__perplexity_ask", "mcp__exa__web_search_exa", "mcp__exa__deep_researcher_start", "mcp__exa__deep_researcher_check", "mcp__ref__ref_search_documentation", "mcp__ref__ref_read_url", "mcp__grep__searchGitHub"]
---

# Adversarial Review (Dual-Model: Claude + Codex)

Hint: "$ARGUMENTS"

You are the **review orchestrator**. Two models, two roles, strict scope boundaries: Codex attacks implementation-level exploitability, Claude (Opus) reviews architecture/intent/operations. Claude synthesizes both into unified findings. This command runs in phases. **All phases are mandatory when applicable -- do NOT stop early.**

Use `TaskCreate` to enumerate PHASE 1, 2a-2d, and 3a-3e as trackable tasks so progress is visible.

**Independence mechanism**: reviewers run in parallel, in isolated sandboxes (a fork for Claude, a background OS process for Codex). Neither can observe the other's output during the review -- isolation is stronger than ordering, and faster. Convergence only happens at synthesis (Phase 2d).

---

## Canonical reviewer role (skill-unification s08, CP-01)

This is the **canonical DEEP review front door**. The fast/diff counterpart is the
native `/code-review` skill. All other review entry points were collapsed into these two
(`~/.claude/SKILL-UNIFICATION-ROUTING.md`). Typed aliases that route here:
`/review --deep`, `/bmad-code-review`, `/bmad-review-adversarial-general`.

### Specialized hunter sub-agents (workers of this reviewer)

When the review target is code and a deeper, dimension-specific pass is warranted, this
orchestrator may **fan out to the `pr-review-toolkit` hunter sub-agents** (Phase 2a/2b,
in parallel with the Claude+Codex pass) and fold their findings into synthesis (Phase
2d). These are workers, not separate front doors — invoke via `Task(subagent_type=...)`:

| Hunter sub-agent | Dimension it owns |
|---|---|
| `pr-review-toolkit:silent-failure-hunter` | swallowed errors, inadequate error handling, unsafe fallbacks |
| `pr-review-toolkit:type-design-analyzer` | type encapsulation, invariant expression, enforcement |
| `pr-review-toolkit:comment-analyzer` | comment accuracy / rot / docstring drift |
| `pr-review-toolkit:pr-test-analyzer` | test coverage completeness for the change |

Dispatch is optional and scoped: use a hunter only when its dimension is in play, treat
each hunter's output as Claude-side findings, and de-duplicate against the Codex pass in
synthesis. Hunters never run as standalone typed commands — they reach users only
through this canonical reviewer.

---

## PHASE 1: Context Detection, Plan Capture & Route Selection

Before doing anything else, examine the current conversation and working directory to classify the review target, detect any plan, and **decide the Codex entry point**.

### 1a: Detect review target

Check in order:

1. **Plan in context** -- Active plan mode (plan file path in system messages), plan file on disk, architecture doc, or implementation plan in conversation
2. **Code changes** -- Recent edits, diffs, staged files. Run `git diff --stat HEAD` and `git diff --cached --stat` silently.
3. **Prompt/skill** -- `.md` command file, agent definition, system prompt being discussed
4. **Task/requirements** -- Epic, story, PRD, acceptance criteria
5. **Workflow/process** -- CI pipeline, deployment, automation
6. **Config** -- YAML, JSON, TOML, env files

If "$ARGUMENTS" is non-empty (excluding flags like `--background` and `--synthesize`), use it to narrow or override auto-detection.

### 1b: Plan capture (CRITICAL)

Search for a plan in this priority order:

1. **Active plan mode** -- Check system messages for a plan file path (plan mode produces a file like `~/.claude/plans/<name>.md`). If found, read the FULL content.
2. **Plan file in conversation** -- Has a plan file been discussed, written, or referenced? If so, read the FULL content.
3. **Plan-like content in conversation** -- Is there a structured implementation plan, architecture decision, or multi-step design in recent messages? If so, capture it mentally.

**Set your internal state:**
- `PLAN_DETECTED = true/false`
- `PLAN_LOCATION = "<file path>" | "conversation"` (if detected)
- `PLAN_CONTENT = <full plan text>` (if detected -- you WILL need this in Phase 3)
- `REVIEW_TIMESTAMP = <current epoch seconds>` (for unique artifact filenames)

### 1c: Select Codex entry point (MANDATORY -- decide here, execute in Phase 2c)

**CRITICAL: This decision is final. Do NOT try one entry point and then fall back to another. Pick the correct one now.**

The Codex companion CLI has two relevant subcommands:
- **`adversarial-review`** -- ONLY reviews git diffs (working tree or branch deltas). It ignores stdin, ignores piped content. It calls `resolveReviewTarget()` which reads git state. If the working tree is clean, it finds nothing useful.
- **`task`** -- accepts any arbitrary prompt via `--prompt-file`. Sends the prompt to Codex. Use this for ALL non-code-diff content.

**Decision rules (no exceptions):**

| Detected target | `CODEX_ENTRY_POINT` | Why |
|---|---|---|
| Code changes (dirty working tree OR branch delta) | `adversarial-review` | Built-in git diff reviewer |
| Plan, architecture, PRD, document, prompt, skill, requirements, config -- **anything that is NOT a git diff** | `task` | Send document content to Codex with adversarial prompt |

Set `CODEX_ENTRY_POINT = "adversarial-review"` or `CODEX_ENTRY_POINT = "task"`.

### 1d: Check for `--synthesize` flag

If "$ARGUMENTS" contains `--synthesize`, skip ALL of Phases 2a/2b/2c. Jump directly to Phase 2d (Dual-Model Synthesis). Look for the most recent artifact pair:
- `/tmp/adversarial-review-*-claude.md`
- `/tmp/adversarial-review-*-codex.md`

If both found with matching timestamps: proceed to Phase 2d.
If either is missing: error with message about which artifact is missing.

### Announce detection

> **Context detected**: [target type]. Plan: [detected at <location> / not detected].
> **Codex entry point**: `[adversarial-review | task]` -- [one-line reason]
> **Review mode**: dual (Claude + Codex independently -> synthesis -> Codex verify-loop on the hardened plan)

---

## PHASE 2a: Content Acquisition

Before either model reviews, acquire the review content.

**For code changes** (`CODEX_ENTRY_POINT = "adversarial-review"`):
- Run `git diff HEAD` silently. If staged changes exist, also run `git diff --cached`.
- Store combined output as `REVIEW_CONTENT`.

**For plans/documents** (`CODEX_ENTRY_POINT = "task"`):
- Read the plan/document file (from `PLAN_LOCATION` or target file).
- Store as `REVIEW_CONTENT`.
- Write the Codex prompt file (`/tmp/adversarial-review-input.md`) with adversarial framing:

```bash
cat > /tmp/adversarial-review-input.md << 'REVIEW_EOF'
You are performing an ADVERSARIAL REVIEW of the document below. Your job is to ATTACK this document -- challenge the approach, design choices, assumptions, and tradeoffs.

Rules:
- Find weaknesses, missing steps, edge cases, and failure modes
- Be aggressive but defensible -- every finding must have concrete evidence from the document
- Minimum 3 findings, maximum 12
- Output each finding as: **[CRITICAL|HIGH|MEDIUM|LOW] -- [Title]** followed by Evidence, Impact, and Fix
- No praise. No filler. No hedging.
- End with "#### Top 3 Improvements" listing the highest-impact fixes

Additional focus from user: $ARGUMENTS

--- DOCUMENT TO REVIEW ---
<INSERT FULL PLAN_CONTENT / DOCUMENT CONTENT HERE>
--- END DOCUMENT ---
REVIEW_EOF
```

---

## PHASE 2b: Concurrent Independent Reviews (Claude fork + Codex background)

Both reviewers are launched **in a single orchestrator message, in parallel**, and write their own sealed artifact. Independence is enforced by **isolation** (two sandboxes that cannot see each other's output during review) rather than by ordering. The reviews are genuinely independent -- each consumes the same `REVIEW_CONTENT`, each writes its own artifact, and synthesis (Phase 2d) is the first and only convergence point.

### `--background` short-circuit (unchanged legacy path)

If the user passed `--background`, **skip the parallel path in this phase**. Instead:
1. Perform the Claude review in-context (as described in the "Claude review specification" below) and write `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-claude.md` directly.
2. Launch Codex in background via `Bash(run_in_background: true)` using the invocation from Phase 2c.
3. Tell user: *"Claude review complete and sealed. Codex running in background. Run `/adversarial-review --synthesize` after Codex completes."*
4. **Skip Phase 2c, Phase 2d, and Phase 3.**

Rationale: `--background` exists so the user can leave the session. The fork-based parallel path requires the orchestrator to stick around to receive the fork's async completion notification, which doesn't serve that use case. The old in-context path still works and is the right tool here.

### Default path: launch both reviewers concurrently

In **one orchestrator message**, issue these two tool calls in parallel:

#### 1. Fork for Claude review (`Agent` without `subagent_type`)

Spawn a fork whose prompt:
- Inlines the full `REVIEW_CONTENT` so the prompt is self-contained
- Contains the literal word **ultrathink** so the fork uses extended reasoning
- Specifies the architecture/intent/operations lenses (see "Claude review specification" below)
- Mandates writing the artifact to `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-claude.md` in the exact format specified
- Ends with: *"Before reporting completion, verify the artifact exists with `ls -la` and is >=500 bytes. Reply in under 150 words with just: artifact path, size, and finding count."*

The fork inherits the orchestrator's prompt cache (so marginal cost is just the review work) and keeps its ultrathink reasoning tokens out of the orchestrator's synthesis context.

#### 2. Background Bash for Codex

Simultaneously, in the same message, launch the Codex invocation via `Bash(run_in_background: true)`, redirecting combined output to the Codex artifact.

**If `CODEX_ENTRY_POINT = "adversarial-review"` (code changes only):**
```typescript
Bash({
  command: `node "$(ls "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs | sort -V | tail -1)" adversarial-review --wait $ARGUMENTS > /tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.raw.md 2>&1`,
  description: "Codex adversarial review (parallel)",
  run_in_background: true
})
```

**If `CODEX_ENTRY_POINT = "task"` (plans, documents, any non-diff content) — USE THE SUPERVISED RUNNER:**
```typescript
Bash({
  command: `python3 "$HOME/.claude/scripts/codex_supervised.py" --prompt-file /tmp/adversarial-review-input.md --out /tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.md --effort xhigh --idle-timeout 600 --max-attempts 3 --total-deadline 5400 > /tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.status.json 2>&1`,
  description: "Codex adversarial review (parallel, supervised)",
  run_in_background: true
})
```

This is the **longest single Codex call the command makes** and therefore the one most exposed to the
mid-run transport stall (Rule 21; openai/codex #31376). The runner kills on `--idle-timeout` of zero
output growth and RESUMES the same session, so a stall costs the idle window rather than the run.
Three differences from the companion path, all of which simplify Phase 2c:

- The verdict lands in `--out` **already clean** — it is codex's last message, so there are no `[codex]`
  progress lines to strip. The JSONL event stream goes to `<out>.jsonl`.
- The status JSON is the completion signal: `"status": "completed"` means a usable artifact exists;
  `"failed"` means every attempt stalled or the deadline hit. **`"failed"` is a DEGRADED round, never a
  finding and never a verdict.**
- **No `codex_watchdog.py` polling is needed on this path** — idle detection is in-process. Keep the
  watchdog only for the `adversarial-review --wait` companion path above, which is still unsupervised
  (the companion builds the git-diff review itself; the runner cannot substitute for it).

**Do not peek at either side's intermediate output.** The fork notification arrives asynchronously; the background Bash is polled non-blockingly.

### Claude review specification (used by both fork and `--background` in-context paths)

#### Scope boundaries (strictly disjoint from Codex)

Codex owns implementation-level exploitability: race conditions, missing guards, data corruption, auth gaps, idempotency, null/timeout handling, schema drift. Claude MUST NOT duplicate these.

Claude owns architecture/intent/operations ONLY.

#### For code changes:

1. **ARCHITECTURAL FIT** -- Does this change fit system design? Coupling, boundary violations, tech debt?
2. **INTENT ALIGNMENT** -- Does the implementation match the stated goal?
3. **CROSS-DOMAIN RIPPLE** -- What outside the diff is affected? Migrations, API contracts, clients, config, docs?
4. **ASSUMPTION AUDIT** -- What does this code assume about environment, deps, data shape?
5. **OPERATIONAL READINESS** -- Deployable safely? Rollback? Monitoring?

#### For plans/documents:

1. **MISSING** -- What's absent that should be present?
2. **ASSUMPTIONS** -- What might not hold true?
3. **WEAKEST LINK** -- What's most fragile?
4. **CRITIC'S VIEW** -- What anti-patterns exist?
5. **FEASIBILITY** -- Actually buildable as described? Unstated prerequisites?
6. **COMPLETENESS** -- Covers full lifecycle?

#### MCP tool usage

Required ONLY when a finding depends on external facts (e.g., verifying an API contract, checking a library's behavior). Otherwise local-only. When used, include citation with source URL.

#### Claude review artifact format

Write to `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-claude.md`:

```markdown
# Claude Independent Review
Timestamp: {ISO 8601}
Target: {description}
Lenses: {list applied}

## Findings

### 1. [SEVERITY] -- [Title]
- Lens: [which]
- Scope: [file:lines or section/concept]
- Failure class: [architecture|intent|operations|other]
- Evidence: [concrete]
- Impact: [what goes wrong]
- Recommendation: [specific action]
- Confidence: [0.0-1.0]

### 2. ...
```

Constraints: min 2 findings, max 8. No filler. No implementation-level findings (that's Codex's job).

---

## PHASE 2c: Await both artifacts, then seal

Skip this phase entirely when `--background` was passed (handled in Phase 2b short-circuit).

### Coordination loop (await both)

The fork and the background Bash both run concurrently. Advance only when BOTH artifacts exist on disk and pass a sanity check. Coordination must be small and non-blocking:

1. **Poll Codex Bash shell once per turn** with `BashOutput` -- cheap status read; never block on it.
2. **Fork completion** arrives as an async user-role notification in a later turn. Never fabricate or predict the fork's result. If the user asks a follow-up before it lands, report status honestly ("fork still running, Codex %s" where %s is the BashOutput status).
3. Whichever finishes first, keep waiting for the other.
4. When both the fork notification has arrived AND the Codex shell has exited, proceed to seal-and-sanitize (step below).
5. **PATH-DEPENDENT LIVENESS CHECK.** Which check applies depends on how Phase 2b launched Codex:
   - **Supervised path (`CODEX_ENTRY_POINT = "task"`, the default for plans/documents):** nothing to do.
     Idle detection, the kill, and the resume all happen in-process. Read the status JSON when the shell
     exits: `"completed"` -> the `--out` artifact is your sealed Codex review, already clean.
     `"failed"` -> every attempt stalled or the deadline hit; treat it exactly like the `hung` branch
     below (degrade to Claude-only, notify in-stream). **Never read `"failed"` as a finding or a verdict.**
   - **Companion path (`CODEX_ENTRY_POINT = "adversarial-review"`, git diffs):** still unsupervised, so the
     watchdog below is REQUIRED on every poll turn.

6. **No-OUTPUT watchdog (OR-02, added 2026-07-03) -- COMPANION PATH ONLY; run it on EVERY poll turn, not just when the shell looks stuck.** The incident this closes: the Codex sidecar once sat silent for 1h43m at 0% CPU inside a plan-harden run before a human noticed, because the old loop only checked whether the shell had *exited* -- a process can be alive (not exited) yet emit zero bytes indefinitely, and that hang is invisible to a bare `BashOutput` exit-code check. Each poll turn, also run:
   ```
   python3 "$HOME/.claude/scripts/codex_watchdog.py" check /tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.raw.md --no-output-window-s 600
   ```
   It compares the raw-output file's size against the last poll (a tiny on-disk `.watchdog.json` sidecar next to the raw file tracks this across turns -- no orchestrator-side state needed) and returns `{"status": "growing"|"stalled_ok"|"hung"}`.
   - `growing` / `stalled_ok` -- keep polling normally.
   - `hung` (>=10 minutes with zero growth) -- **kill, degrade, notify, do not keep waiting**: (a) `KillShell` the Codex background shell; (b) write a synthetic `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.md` noting `Source: codex (DEGRADED -- no-output watchdog killed a hung invocation after N minutes; findings below are Claude-only)`; (c) proceed to synthesis with Claude's review only (do not block the whole command on a dead sidecar); (d) tell the user explicitly, in the stream, that Codex was killed by the watchdog and the review is Claude-only for this run.
   - Call `python3 "$HOME/.claude/scripts/codex_watchdog.py" reset <raw_path>` once, right after launching a FRESH Codex background invocation (Phase 2b and the verify-loop resume/fresh calls) -- otherwise a stale sidecar from a prior review on the same timestamp path could false-positive on turn 1.

Do NOT re-enter the coordination loop more than once per orchestrator turn. Do NOT issue redundant `BashOutput` calls within the same turn.

### Seal and sanitize Codex artifact

**Supervised path:** nothing to seal. `codex_supervised.py --out` already wrote
`/tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.md` as codex's last message, with no `[codex]`
progress lines in it. Go straight to the size sanity check.

**Companion path**, after the Codex background shell exits successfully:
- Read `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.raw.md`
- Strip `[codex]` progress lines
- Write the cleaned output to `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.md`

### Artifact existence + size sanity check

Before advancing to Phase 2d, verify:
- `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-claude.md` exists AND is >=500 bytes
- `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-codex.md` exists AND is non-empty

### Single-source fallbacks

- **Codex failed but Claude succeeded** (Codex shell exited non-zero, or sanitized artifact missing/empty): proceed to Phase 2d with Claude-only findings. Phase 2d uses the "Claude-only" header (current fallback format).
- **Fork failed but Codex succeeded** (no artifact, or fork returned without writing the file, or reported size <500 bytes): proceed to Phase 2d with Codex-only findings. Use a "Codex-only" header symmetric to the Claude-only one (replace model name and lens list in the fallback format; drop Claude sections).
- **Both failed**: error out cleanly. Tell the user which side(s) failed and suggest re-running or using `--synthesize` with prior artifacts.

---

## PHASE 2d: Dual-Model Synthesis

Read both sealed artifacts. Use extended thinking (ultrathink MANDATORY).

### Step 1: Extract findings

From both artifacts into a common mental model:
- Source (codex/claude)
- Scope (file:lines for code, section name for plans)
- Failure class (implementation-level vs architecture/intent/operations)
- Severity, title, evidence, recommendation, confidence

**Codex `adversarial-review` output**: structured JSON with severity, file, line_start, line_end, confidence, recommendation (per schema).
**Codex `task` output**: free-form markdown -- extract severity/title/evidence from the formatted output.
**Claude artifact**: direct extraction from the structured format above.

### Step 2: Match findings

Two findings are a **consensus match** if BOTH conditions are met:
- Same scope (file + overlapping line range, OR same plan section), AND
- Related failure class (not necessarily identical -- e.g., Claude flags "operational readiness" on the same code where Codex flags "rollback safety")

When in doubt, classify as unique rather than false-consensus.

### Step 3: Classify and present

- **CONSENSUS**: Matched pair. Confidence = max(both) + 0.1 (capped at 1.0). Strongest signal.
- **CODEX-UNIQUE**: Implementation-level finding only Codex caught.
- **CLAUDE-UNIQUE**: Architecture/operations finding only Claude caught.
- **CONFLICT**: Same scope, opposite conclusions. Present both without resolution.

### Step 4: Unified verdict

`NEEDS ATTENTION` if any finding has severity >= HIGH and confidence >= 0.6. Otherwise `APPROVE`.

### Output format

```
## DUAL-MODEL ADVERSARIAL REVIEW

**Target**: [what was reviewed]
**Models**: Codex + Claude (Opus)
**Artifacts**: adversarial-review-{ts}-claude.md, adversarial-review-{ts}-codex.md

### Verdict: [NEEDS ATTENTION | APPROVE]

### Consensus Findings
[Both models independently identified -- highest confidence]

**[SEVERITY] -- [Title]** (Codex + Claude, confidence: X.XX)
Scope: [location]
Codex evidence: [brief]
Claude evidence: [brief]
Recommendation: [unified]

### Codex-Unique Findings
**[SEVERITY] -- [Title]** (Codex, confidence: X.XX)
[detail]

### Claude-Unique Findings
**[SEVERITY] -- [Title]** (Claude, confidence: X.XX)
[detail]

### Conflicts
[If any -- both perspectives without resolution]

### Top 3 Improvements
1. ...
2. ...
3. ...
```

### Single-model fallback format

When only one reviewer produced a usable artifact, replace the header and skip the unavailable model's sections. Use whichever of the two headers matches:

```
### ADVERSARIAL REVIEW (Claude-only -- Codex unavailable)

**Target**: [what was reviewed]
**Depth**: STANDARD | DEEP
**Lenses applied**: [list]
```

or

```
### ADVERSARIAL REVIEW (Codex-only -- Claude review unavailable)

**Target**: [what was reviewed]
**Depth**: STANDARD | DEEP
**Lenses applied**: [implementation-level exploitability]
```

Then emit findings in the same structure:

```
#### Findings

**[CRITICAL|HIGH|MEDIUM|LOW] -- [Short title]**
Lens: [which lens]
Scope: [file:lines or section/concept]
Evidence: [concrete evidence]
Impact: [what goes wrong]
Fix: [specific action]
Confidence: [0.0-1.0]

#### Top 3 Improvements
1. ...
2. ...
3. ...
```

Rules: minimum 3 findings, maximum 12. Every CRITICAL/HIGH needs deterministic evidence. No praise. No filler.

**Quote-the-line evidence gate (applies to BOTH the dual-model and single-model formats, at synthesis time):** a HIGH or CRITICAL finding must carry the verbatim motivating line (or verbatim quoted plan/document text) plus its location (`file:line` for code, section name for plans) in its Evidence field. A HIGH/CRITICAL finding whose evidence is only a paraphrase or a general claim is DEMOTED one severity level (CRITICAL→HIGH, HIGH→MEDIUM) with the note `(demoted: no verbatim evidence)` — never reported at full severity. (Source: everyinc/compound-engineering-plugin ce-code-review quote gate, 2026-07-09.)

**>>> MANDATORY: After presenting findings (dual or Claude-only), CONTINUE to Phase 3. Do NOT stop here. <<<**

---

## PHASE 3: Automatic Plan Revision

**Gate**: This phase runs ONLY if ALL conditions are met:
- `PLAN_DETECTED = true`
- Review ran in foreground (not `--background`)
- Review produced at least one finding

If the gate is not met, STOP. Do not mention Phase 3. Do not ask about plan revision.

**If the gate IS met, proceed AUTOMATICALLY. Do not ask the user for permission. The automatic flow is the entire point of this command.** No plan was detected in a code-diff-only review (the common path) — skip straight past this phase to the Rules section below.

**When the gate IS met**, `Read ~/.claude/commands/references/adversarial-review/plan-revision-and-verify-loop.md` for the full Phase 3 procedure: 3a deep analysis, 3b producing the `[HARDENED]`-tagged revision, 3c applying it, 3d the default Codex verify loop (rounds 1-3, convergence gate, deadlock handling), and 3e the completion output block. Execute that file's steps in order; it is the complete phase body, not optional background reading.

## Rules

1. **Route decision is made in Phase 1 and is FINAL.** Do not try `adversarial-review` then fall back to `task`. Pick the right one upfront.
2. **`adversarial-review` = code diffs ONLY. `task` = everything else.** No exceptions.
3. **Phase 2a-2d ALWAYS run (unless `--synthesize` or `--background` modify the flow). Phase 3 ONLY if a plan was detected.** No exceptions.
4. **Never ask "would you like me to revise the plan?"** -- just do it. Automatic flow is the core value.
5. **Codex output is shown verbatim in the synthesis.** Do not summarize, filter, or editorialize the review output.
6. **Two models, strict scope boundaries.** Codex owns implementation-level exploitability. Claude owns architecture/intent/operations. Claude synthesizes both into unified findings. Neither does the other's job.
7. **If both models found zero issues**, note this as a confidence signal but do NOT invent changes. Add a brief annotation: `> Dual adversarial review (Codex + Claude Opus) found no material issues. Plan unchanged.`
8. **Anti-sycophancy.** Do not praise the plan during revision. Do not say "this is already well-structured." State what changed and why. Nothing else.
9. **The `[HARDENED]` tag is sacred.** Every plan change gets one with source attribution. Every tag gets a justification. This is the user's audit trail.
10. **Background mode skips synthesis and Phase 3.** Tell the user to run `--synthesize` after Codex completes.
11. **Independence enforced by isolation + artifacts.** Default path runs both reviewers in parallel in isolated sandboxes (fork for Claude, background OS process for Codex); neither can observe the other during review. Each seals its own artifact; artifacts are the proof of independence. `--background` uses the legacy in-context Claude + background Codex path for the same reason (different sandbox shape, still isolated).
12. **Consensus amplifies.** Matched findings from both models get boosted confidence (+0.1, capped at 1.0).
13. **Attribution mandatory.** Every finding in the synthesis shows source model(s) and confidence.
14. **`--background` runs Claude review immediately; only Codex and synthesis are deferred.** Use `--synthesize` to complete.
15. **Prefer unique findings over false consensus.** When matching is ambiguous, classify as unique.
16. **Verify loop is default, not optional — and rounds 1–2 always run, round 3 is earned.** After hardening a plan, the SAME Codex thread (at `--effort xhigh`) re-reviews it via `task --resume-last` until it confirms its own fixes, converges to minor-only findings after round 2 (the convergence gate — round 3 skipped), or hits the 3-round cap (deadlock). No flag enables this -- it runs whenever a plan was hardened and Codex was available (foreground, non-`--synthesize` path).
17. **Read-only every round; deadlock is honest; a blocked resume is not a round.** Verify-loop Codex calls never pass `--write` (the companion enforces a read-only sandbox without it). A `VERDICT: REVISE` at the round cap is reported as a DEADLOCK with its unresolved findings -- never silently upgraded to an approval. A missing or garbled verdict *from a genuine review* is read as REVISE. But a resume refused because a prior task is stuck (`still running` / `/codex:status`) is NOT a verdict at all -- never score it REVISE; fall back to a fresh reviewer thread per Phase 3d step 2 so the loop still gets a real second opinion.
18. **Review log is the audit trail.** Every verify round is appended to `*-REVIEW-LOG.md` (round, verdict, resolved/unresolved, actions taken). It is the "why" record beside the plan's "what".
19. **Never hardcode the plugin version in the companion path.** The codex cache dir is version-stamped (`…/openai-codex/codex/<ver>/scripts/codex-companion.mjs`) and keeps a single version, so a plugin update deletes the old path. Always resolve it at call time: `"$(ls "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs | sort -V | tail -1)"` (never re-introduce a literal version anywhere in this file). `installed_plugins.json` is the source of truth for the currently-installed version — check it (`python3 -c "import json;print(json.load(open('$HOME/.claude/plugins/installed_plugins.json'))['plugins'].get('codex@openai-codex'))"`) if the glob ever returns nothing.
21. **Long Codex calls run under the SUPERVISED runner (`scripts/codex_supervised.py`).** Rule 20's watchdog only fires if the ORCHESTRATOR is polling every turn -- and on 2026-07-26 the orchestrator's own Bash wrapper was killed, so nothing polled while two `xhigh` verify jobs hung for 28 and 20 minutes with `codex-companion status` still reporting `running`. `codex_supervised.py` closes that gap: it supervises the `--json` event stream IN-PROCESS, kills the process GROUP on `--idle-timeout` of zero growth, and RESUMES rather than restarts, bounded by `--max-attempts`/`--total-deadline`. Use it for Phase 2b (wired 2026-07-26 — that call is the longest the command makes) and every verify round. The `adversarial-review --wait` companion path for git diffs stays unsupervised because the companion builds that review itself; the watchdog still covers it. **Never respond to a stall by shrinking the prompt or lowering `--effort`** -- that degrades the review to dodge a transport bug (upstream openai/codex #31376: dead pooled connection, `stream_idle_timeout_ms` never fires), and it does not even help: the smallest prompt tried, 5 KB, hung too. Robustness comes from the supervisor; fidelity stays at `xhigh` with the full document inlined.

20. **Codex no-OUTPUT watchdog (OR-02).** The old failure mode was a live-but-silent Codex process (1h43m observed) that a plain "did the shell exit" check can't see. Phase 2c step 5 runs `codex_watchdog.py check` on every poll turn against the raw-output file's growth, not just its existence — 10 minutes of zero-byte growth is treated as hung: kill the shell, degrade to Claude-only findings, notify the user in-stream. Never silently wait past the window "just in case it's still working" — a live-but-silent process for >10 minutes at 0% CPU IS the definition of hung here.
