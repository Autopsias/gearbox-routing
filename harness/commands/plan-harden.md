---
description: "Pre-flight harden plan before exit. Runs parallel enrichment (project memory + external research + edge-cases), invokes /grill-with-docs and /adversarial-review with enrichment context loaded, then a Klein-style premortem, model + parallelization lints, and severity-tagged synthesis. Use when in plan mode and want maximum critique surface, or with --quick to just chain the existing two skills."
argument-hint: "[--quick] [--interactive-grill] [--plan-file PATH] [--from-phase N] [--no-memory] [--no-research] [--no-edge-cases] [--no-blindspot]"
allowed-tools: ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Skill", "Agent", "Workflow", "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
effort: high  # cascades to nested skills as adaptive-thinking guidance (cost-only tradeoff — neither nested review skill pins its own effort, so `high` only deepens them); history + unverified A/B protocol in ## Changelog. Delete this line to inherit the session level.
---

# /plan-harden — meta-orchestrator for plan mode

You are a **pre-flight hardening orchestrator**. The user has a plan (in plan mode or in a file) and wants maximum critique surface before they ExitPlanMode and ship it. Your job is to compose existing skills (`/grill-with-docs`, `/adversarial-review`, `bmad-review-edge-case-hunter`) into a single chained workflow with three layers added: (a) parallel enrichment from project memory + external research + edge-cases, (b) a Klein-style premortem, (c) model + parallelization lints and a severity-tagged synthesis that mutates the plan file once.

> **Scope of generalizability**: this command works for any project where this user's Claude Code setup is configured. The cwd-slug memory pattern is a Claude Code internal convention this user's machine follows; portability across machines, users, or non-Claude agents is not claimed.

**You orchestrate; you do not do the review work yourself**, with two exceptions explicitly inlined: the Klein premortem (Phase 3) and the inline edge-case prompt (Phase 0 fork C — always on the Workflow path; on the Agent-fork fallback path only when BMAD is absent).

**Terminology:** a *finding* is raw input from a fork/reviewer/premortem; a *hardening* is a finding actually applied to `PLAN_FILE` (tagged `[HARDENED:...]`); a *flag* (§4.0/§4.0b only) is a lint result — a MODEL_LINT rubric violation or a PARALLEL_LINT blocker — never promoted past 🟡/🟠.

Args: "$ARGUMENTS"

---

## SETUP — Argument parsing, plan resolution, environment detection

Use `TaskCreate` to enumerate phases as tasks so progress is visible. Mark each `in_progress` at start, `completed` at end.

### S.1: Parse `$ARGUMENTS`

Set these flags (all default false unless flag present):

| Flag | Effect |
|---|---|
| `--quick` | Skip Phase 0 + Phase 3. |
| `--interactive-grill` | Phase 1: run `/grill-with-docs` interactively (pause for the user's answers). **Auto-accept is the DEFAULT** (operator standing order): without this flag, grilling auto-accepts its own recommended answers. `--auto-grill` is still accepted as a no-op for back-compat. Composes with `--quick`. |
| `--plan-file PATH` | Override plan auto-detection. |
| `--from-phase N` | Resume from phase N (0/1/2/3/4). **Loads the prior findings ledger** — see S.1b. |
| `--no-memory` | Force-skip Fork A. |
| `--no-research` | Force-skip Fork B. |
| `--no-edge-cases` | Force-skip Fork C. |
| `--no-blindspot` | Force-skip Fork D. |

### S.1b: Load the prior findings ledger (`--from-phase`, or any re-run)  *(2026-08-21)*

If a `*-REVIEW-LOG.md` sits beside `PLAN_FILE`, READ IT and build `RESOLVED_LEDGER` — the
list of findings prior runs already settled. Pass it verbatim into every reviewer prompt
this run creates (Phase 2's args, and each verify round), under the heading:

```
ALREADY RESOLVED BY A PRIOR PASS — DO NOT RE-RAISE unless you can show the applied fix is
itself WRONG (quote it and say why):
  - <one line per settled finding>
```

*Why:* without it a second pass re-derives findings the first pass closed and burns the
round. Measured: the ledger had to be hand-written into the review args twice
across two passes of one plan. The review log IS the ledger — do not maintain a second one.

### S.2: Plan file resolution (in order, stop at first match)

1. If `--plan-file PATH` was supplied → use it. Verify the file exists; if not, error and stop.
2. Else if a plan-mode system message in this conversation contains a plan file path → use that path. Verify file exists; if not, warn and fall through.
3. Else → `AskUserQuestion`: `"No plan file detected. Provide --plan-file PATH or paste the plan text and I'll write it to a tmp file (e.g. /tmp/plan-harden-<ts>.md)."` Wait for user response.
4. If after step 3 there is still no plan file (user declined and no in-conversation text to materialize) → refuse to run with message `"/plan-harden requires a plan file (Phase 4 mutates the file). Re-invoke with --plan-file PATH."`

Set `PLAN_FILE = <resolved path>`. Read it once and remember its content for Phase 1 context-passing.

### S.3: Detect available enrichment sources (orchestrator-side, BEFORE forks)

Pass detection results to forks as explicit args — do NOT make forks self-introspect.

**Memory file detection**:
```bash
# Determine the canonical cwd slug
PROJECT_SLUG=$(pwd | sed 's|/|-|g')   # path-encoding fallback
# Note: leading dash is intentional ("/Users/foo/bar" → "-Users-foo-bar")
MEMORY_PATH="$HOME/.claude/projects/${PROJECT_SLUG}/memory/MEMORY.md"
if [ -f "$MEMORY_PATH" ]; then
  echo "MEMORY_AVAILABLE=true (path: $MEMORY_PATH)"
else
  echo "MEMORY_AVAILABLE=false (looked at: $MEMORY_PATH)"
fi
```

**Caveat**: cwd-slug is lossy (paths `/Users/foo/bar-baz/proj` and `/Users/foo/bar/baz/proj` encode identically). Worktrees may show double-dash artifacts (`-...--claude-worktrees-...`); if encountered AND the file does not exist at that exact path, mark `MEMORY_AVAILABLE=false` rather than risk reading the wrong project's memory.

**Research MCP detection**: scan your own available-tools system message for these tool names; if present, the corresponding tier is available:
- Tier 1: `mcp__perplexity-ask__perplexity_ask`
- Tier 2: `mcp__exa__web_search_exa` or `mcp__exa__agent_run`
- Tier 3: `mcp__ref__ref_search_documentation`

Build `RESEARCH_TIERS_AVAILABLE = ["perplexity"|"exa"|"ref"|...]`. If empty, Fork B will skip.

**Repo-profile cache probe** (optional, never blocking): if `~/.claude/scripts/repo-profile-cache.py` exists, run `python3 ~/.claude/scripts/repo-profile-cache.py get` — on `HIT`, hold the profile JSON (line 2 of the output) for fork dispatch (see the pre-dispatch section in `references/plan-harden/fork-prompts.md`). Any other status word, a missing script, or an error → proceed exactly as if the probe never ran.

**Edge-case skill detection**: scan available-skills system message for `bmad-review-edge-case-hunter`. Set `BMAD_EDGE_CASE_AVAILABLE=true|false`.

**Blindspot detection + scan-target derivation**: scan available-skills system message for `blindspot`. Set `BLINDSPOT_AVAILABLE=true|false`. If available, derive `BLINDSPOT_TARGETS` — the concrete code areas (dirs/files/modules) the plan under hardening touches — from the plan's session cards / manifest (`sessions[].touches` or equivalent) or, for a freeform plan file, from explicit file paths named in its "Recommended Approach" / "Touches" sections. If the plan touches no code at all (pure docs/process/research plan — no file paths, no dirs, no modules named), set `BLINDSPOT_TARGETS=[]` and record the skip reason `"plan touches no code — skipping blindspot enrichment"`.

**Early model-lint pass (plan-builder plans only, advisory)** *(2026-08-03)*: on a plan-builder plan, run §4.0's model-lint NOW as well — deterministic and near-free, and a 🔴-class hit (`peer-gate-missing`) discovered only at Phase 4 arrives AFTER the ~50-200k-token Phase 2 spend and forces a re-run; discovered here, it is one grilling branch. Hold as `EARLY_MODEL_LINT`, passed into the Phase 1 args as a fifth enrichment source. The §4.0 pass remains AUTHORITATIVE (Phases 1-2 mutate sessions); never skip it because this pass ran. **On a schema-v8 plan (`manifest.json` `plan_schema_version` ≥ 8) the lint checks the class and risk declarations — `task_class` valid, every override has a `why_model`, no override below the risk floor — not the model pick (§4.0).**

Apply user overrides:
- `--no-memory` → `MEMORY_AVAILABLE=false`
- `--no-research` → `RESEARCH_TIERS_AVAILABLE=[]`
- `--no-edge-cases` → set `EDGE_CASE_MODE=skip` (else `bmad` or `inline`)
- `--no-blindspot` → `BLINDSPOT_AVAILABLE=false` (forces skip regardless of targets)

### S.4: Print detected configuration to user

```
/plan-harden starting
  plan file:        <PLAN_FILE>
  mode:             <full | --quick>
  from phase:       <0 | --from-phase N>
  grill mode:       <auto-accept (default) | interactive (--interactive-grill)>
  Phase 0 forks:
    A memory:       <enabled @ path | skipped (reason)>
    B research:     <enabled tiers=[...] | skipped (reason)>
    C edge-cases:   <bmad | inline | skipped (reason)>
    D blindspot:    <enabled targets=[...] | skipped (reason)>
  est. token budget: ~80k beyond baseline grill+adv (+~6k if Fork D runs)
```

If `--from-phase N` was supplied AND N > 0, jump to that phase. Otherwise start Phase 0.

---

## PHASE 0 — Pre-grill enrichment (Workflow-primary; Agent forks as fallback)

**Safe point.** From the main conversation (never inside a fork or a Workflow agent), run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state ok --phase phase-0 || true` — SETUP is done, and the only state it carries is the handful of scalars §S.4 just printed to the user, so a compaction here loses nothing. It marks a boundary, so it runs whether or not the phase below is skipped; the same holds for every safe point in this file. **This and §4.3 are the ONLY two `--state ok` points in this command**, because from Phase 0's forks onward every phase consumes `ENRICHMENT_FINDINGS` (and Phase 4 consumes `PREMORTEM`), which live in context and on no disk — so every boundary between them is a `hold`, and an `ok` there would permit exactly the compaction that destroys the next phase's input. This `ok`'s window therefore ends at the fork-dispatch hold below: it covers only the SETUP boundary behind it, never the fan-out. The `[ -f … ] && … || true` guard on this and every safe-point line below is the same fail-open pattern the settings.json hook entries use: the hook ships at deploy time, so on a tree where it is absent the line is a silent no-op (exit 0, no output), never an error the operator has to interpret. **`--session "$CLAUDE_CODE_SESSION_ID"` is mandatory on every safe-point line in this file and must not be dropped.** `safe-point` never guesses a session id for a write: it refuses whenever a worker marker is set, and `CLAUDE_CODE_CHILD_SESSION`/`CLAUDE_CODE_FORK_SUBAGENT` both read `1` in the MAIN conversation's own Bash subprocess too (measured, re-confirmed) — so the bare form refuses HERE, prints `Nothing written.` to stderr, and exits 0. Measured with a known-positive control: the bare call left the policy file byte-identical while the `--session` call wrote `safe_point`. Every safe point in this command was therefore inert until this flag was added. The variable resolves in a main conversation, and an empty one falls back to the same refusal, so the explicit form is never worse than the bare one.

**Skip this entire phase if `--quick` was passed. Note in the final summary: `enrichment ⊘ (--quick)`.**

**Workflow seam rule (shared with /plan-execute):** a Workflow is bounded analysis that returns a structured report; all human interaction (S.2 plan resolution, Phase 1 grilling, Phase 4 decisions) stays in the main conversation — workflows cannot pause for input. Phase 0 contains no human-input point, so it is a clean fit.

**Fork-dispatch hold.** Immediately before dispatching the forks — on EITHER path below — run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-0 || true`. The forks return `ENRICHMENT_FINDINGS` into context and onto no disk, and `compact-policy.py`'s verdict answers `allow (safe_point_ok)` on a stored `ok` BEFORE it ever reaches the planning-session branch — so leaving Phase 0's `ok` standing through the fan-out permits an auto-compaction in exactly the window where the findings land. This is the hold the run carries until §4.3 releases it (under `--quick` the forks never run, so the hold starts at Phase 1 instead).

### Primary path — ONE Workflow call (schema-forced forks)

If the Workflow tool is in this session's tool list (it can be disabled via settings), run all enabled forks as ONE Workflow call — this paragraph is the documented opt-in for that invocation. Construction:

- Define the shared fork result schema ONCE as a JSON Schema `S`: `{status: enum[ok,skipped,error], source: enum[memory,research,edge-cases,blindspot], skip_reason: string|null, findings: array(max 5) of {text: string, confidence: number, evidence_ref: string}, errors: array of string}` plus optional `tier_used` and `via` properties (Fork B/C extras) so all four forks validate against the same shape.
- Pass the S.3 detection results + the full plan content in via the Workflow `args` (the script must not self-introspect, same rule as the forks).
- The script builds one thunk per ENABLED fork — `() => agent(FORK_X_PROMPT, {label: "fork-A|B|C|D", schema: S, agentType: "Explore"})` (omit `model`: inherit) — runs `await parallel(thunks)`, synthesizes `{"status":"skipped","skip_reason":...}` entries for forks S.3 disabled, and returns the combined object directly as `ENRICHMENT_FINDINGS`.
- The fork prompts are the ones in `references/plan-harden/fork-prompts.md` (shared with the fallback path), minus their JSON-shape blocks — the schema enforces the shape, validation retries happen at the tool layer, and there is nothing to regex-parse. A fork whose schema validation still fails after retries (or whose agent dies) returns `null` — record it as `{"status":"error","errors":["fork failed"]}`.
- **Fork C in the Workflow path MUST use the inline edge-case prompt variant** (no Skill call — Skill-tool availability inside Workflow agents is undocumented). The bmad-via-Skill variant stays available on the Agent-fork fallback path only.
- **Fork D also invokes a Skill (`/blindspot`)** — same constraint as Fork C's bmad variant: Skill-tool availability inside Workflow agents is undocumented, so if Fork D is enabled and the Workflow path is used, run Fork D as a parallel Agent fork alongside the Workflow call (not inside the Workflow script) and merge its result into `ENRICHMENT_FINDINGS` once both complete. Note this hybrid in the Phase 0 status line: `blindspot: agent-fork (Workflow path can't Skill-call)`.

### Fallback path — parallel Agent forks

If the Workflow tool is NOT available: spawn the enabled forks as parallel `Explore` Agent forks in a SINGLE orchestrator message, each receiving the plan content + S.3 detection results as explicit args. Forks auto-notify on completion — do not poll. If one fork is still running long after the others have finished (rule of thumb ~2-3 min), proceed without it and record `{"status": "error", "errors": ["timeout"]}` for that source.

Full fork prompt templates (each with its embedded JSON output schema) + fork-output-handling for both dispatch paths: `Read ~/.claude/references/plan-harden/fork-prompts.md`.

---

## PHASE 1 — Grill with enrichment

**Safe point — `hold`, not `ok`.** Run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-1 || true` before starting (skipped phase or not): §1.2 passes `ENRICHMENT_FINDINGS` VERBATIM out of context into the grill's `args` and nothing wrote them to disk, so a compaction in this window destroys Phase 1's input.

**Skip this phase only if `--from-phase` was supplied with N>1.**

The plan file may already have been mutated by hand or by prior runs since Phase 0 read it — do NOT re-read it here. The grilling skill will read whatever's on disk when it runs.

### 1.1: Print one-line status (user-facing visibility)

Print to user (not the full findings — args carry those):

```
Phase 1: invoking /grill-with-docs (mode=<interactive|auto-accept>) with <N> enrichment findings loaded (memory: <m>, research: <r>, edge-cases: <e>, blindspot: <b>, early-model-lint: <l | ->). Plan: <PLAN_FILE>.
```

### 1.2: Invoke the grilling skill with enrichment in args

**Hold (refresh).** Re-run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-1 || true` here — every safe point expires after 30 minutes and the grill reads many sources for longer than that. The grill should not write its own safe points while nested under this command (`skills/grill-with-docs/SKILL.md` carries that rule, and its between-section `--state ok --phase <section>` rewrites both `current_phase` and `safe_point_phase`, which would read as `ok` at its first section boundary) — but that rule lives in ANOTHER repository this command does not ship, so it is belt-and-braces, never the control. The control is the caller's own re-assert after the grill returns, below.

Pass the enrichment findings VERBATIM via the Skill `args` field. The Skill tool surfaces args in the executing skill's conversation context as an `ARGUMENTS:` line, which the executing Claude reads and uses to ground questions. (Behavior empirically verified 2026-05; the probe command used has since been removed.)

```
Skill(
  skill="grill-with-docs",
  args="""
PRE-GRILL ENRICHMENT FINDINGS (from /plan-harden Phase 0):

[Memory] (<status>)
  - <finding 1 text> [confidence X.X, source: <evidence_ref>]
  - <finding 2 text> [...]
  (or, if skipped: "skipped — <skip_reason>")

[Research] (<status>, tier=<tier_used>)
  - <finding 1 text> [confidence X.X, source: <evidence_ref>]

[Edge cases] (<status>, via=<bmad|inline>)
  - <finding 1 text> [confidence X.X, evidence: <plan section>]

[Territory blindspot] (<status>)
  - <finding 1 text> [confidence X.X, source: <evidence_ref>]
  (or, if skipped: "skipped — <skip_reason>")

[Model lint] (advisory, early pass — authoritative re-run happens at Phase 4)
  - <EARLY_MODEL_LINT flag: session, issue, recommendation, severity>
  (omit this source entirely when EARLY_MODEL_LINT is empty or the plan is not a plan-builder plan)

Use these to ground your grilling questions. Reference specific findings where they expose contradictions, unstated assumptions, or known prior failures. The plan to grill is at <PLAN_FILE>.

DEFER_ADVERSARIAL_REVIEW: true   # /plan-harden runs the full dual-model adversarial review (Claude + Codex, with the Codex verify loop) itself in Phase 2. Do NOT invoke /adversarial-review yourself at the end of grilling — it would double-run.
"""
)
```

**Unless `--interactive-grill` was passed** (auto-accept is the default), append the following block to the `args` string above (immediately before the closing `"""`):

```
AUTO-ACCEPT MODE (default; caller did not pass --interactive-grill):
For each grilling question you would normally ask the user, instead:
  1. Form the question and your recommended answer per your normal procedure.
  2. DO NOT pause for user input.
  3. Record the Q+A pair in a session log appended to the plan file under a
     `## Grill auto-accept log` section (create the section if absent; append
     entries as `- Q: <question> / A (auto-accepted): <recommended answer>`).
  4. Treat the recommended answer as accepted and proceed to the next branch.
  5. Continue until you would otherwise have decided shared understanding is
     reached (per your normal stop condition). Then return.
Apply CONTEXT.md / ADR / plan-file edits per your normal rules using the
auto-accepted answers. Do NOT ask the user to confirm those edits either.
```

Keep the args block under ~3KB. If aggregated findings exceed that, summarize per-source to fit (preserve evidence_refs and key numbers; trim prose).

Allow the grilling to run until either:
- (interactive mode only, `--interactive-grill`) the user signals "done" / "no more questions" / "ship it" / equivalent, OR
- The skill itself decides shared understanding has been reached (per its existing logic).
- In auto-accept mode (the default), the skill stops on its own shared-understanding signal only (the user-signal bullet does not apply — there is no user in the loop).

**Hold (re-assert, on return).** The moment the grill returns — before reading its output, before anything else — run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-1 || true` again. This re-assert exists precisely because the callee's cooperation cannot be relied on: the DIRECTORY `skills/grill-with-docs` is a tracked symlink (git mode 120000, target `../../.agents/skills/grill-with-docs`) into `~/.agents` — nothing under it is tracked in this repository, and `ls -l` on a file inside it follows the link and reports a regular file, which is the target file in `~/.agents`, not one this repo ships — so on any other checkout the grill may still write `--state ok --phase <section>` at its section boundaries and clear the hold above. The safe-point store is last-writer-wins with no writer identity, so the caller who owns the window overwrites whatever the callee wrote, on every return, rather than trusting a rule in the callee's file. (`ENRICHMENT_FINDINGS` is still only in context — §3.1 and §4.1 read it later.) Apply this same on-return re-assert after EVERY nested Skill invocation in this command that could write a safe point; the other such call site is §2.2.

The grilling skill may itself mutate the plan file (or `CONTEXT.md` / `docs/adr/`). That's fine — Phase 2 reads the post-grill plan, not the pre-grill plan.


---

## PHASE 2 — Adversarial review (dual-model)

**Safe point — `hold`, not `ok`.** Run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-2 || true` before starting (skipped phase or not): the grill's edits are on disk, but `ENRICHMENT_FINDINGS` — which §3.1 still reads and §4.1 still reports — is not.

**Skip if `--from-phase` > 2.**

Re-read `PLAN_FILE` here (it may have been mutated during grilling).

### 2.0: Phase 2 is NON-NEGOTIABLE — read before invoking  `[HARDENED:wave1-contract]`

**You may NOT inline Phase 2 work.** Phase 2 ≡ the actual `/adversarial-review` Skill invocation. Inline self-review is NOT a substitute (single-model + no ULTRATHINK + no Codex ≠ dual-model adversarial review). The two specific runner rationalizations that have caused this bug in the past, and why each is wrong:

1. **"Plan mode is read-only, so I can't run `/adversarial-review`."** — FALSE. `/adversarial-review` only mutates the plan file via Edit (verified: `~/.claude/commands/adversarial-review.md` Phase 3c). Plan mode explicitly permits editing the plan file — this is what Phase 4 of `/plan-harden` itself requires. If `/adversarial-review` were illegal in plan mode, so would Phase 4 be, and SETUP would have refused. SETUP did not refuse, so plan mode is not the constraint.

2. **"Phase 2 is too expensive (~50K-200K tokens), I'll abbreviate inline."** — OUT OF CONTRACT. Cost management is `--quick`'s job. If the user wanted a cheaper Phase 2 they would have passed `--quick`. Unilaterally substituting inline reasoning for the actual dual-model skill invents a flag the user did not pass.

If you find yourself reasoning "this is plan mode" OR "the budget is tight" as a reason to inline Phase 2: **stop**. Invoke the Skill. The only legal Phase-2-skip paths are: `--from-phase N` with N>2, OR `/adversarial-review` itself returning a hard error. Runner discretion is not on the list.

### 2.0b: MEASURE THE REVIEW SURFACE FIRST, AND SCOPE ACCORDINGLY  *(2026-08-21)*

Before invoking, measure the artifact the reviewers will read (`spec.json` for a plan-builder
plan, else `PLAN_FILE`). **A whole-document review loop does not converge above roughly
60 KB** — each round re-reads everything and samples a different part of it, so findings per
round stop falling and the 3-round cap is reached with the document no closer to closed.

| Measured size | Mode |
|---|---|
| < 60 KB | `whole-document` — the classic path below, unchanged. |
| ≥ 60 KB | `targeted` — §2.1b. Say so in the Phase 2 status line: `scope=targeted (spec.json is N KB)`. |

Hold the choice as `REVIEW_MODE`. This is a scope decision, never a depth or effort decision:
`--quick` is the only thing that reduces rigour, and targeted mode reviews MORE, not less.

**Evidence (2026-08-21, a 107 KB spec).** Whole-document new findings per round ran
19 → 10 → 1 → 1 across pass 1 and 12 → 6 → 13 → 9 across pass 2 — no convergence, and pass 2's
first round found a CRITICAL inside the fix pass 1 had ended on. Codex, asked the convergence
question directly: *"Another whole-document round would more likely surface another fresh
sample than close this document. It is too large for a whole-document review loop to converge
reliably without a targeted patch pass."* A targeted pass on the same plan then returned 25
findings in one parallel round against the whole-document loop's 9, and the session it was
aimed at went from 7 findings to 1 on re-review.

### 2.1: Invoke

**Hold (refresh).** Re-run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-2 || true` here — the review plus its Codex verify rounds run well past the safe point's 30-minute expiry, and this reads the whole plan plus both reviewers' findings.

```
Skill(skill="adversarial-review", args="Review the plan at <PLAN_FILE>. Apply hardenings inline.")
```

Append `RESOLVED_LEDGER` (S.1b) to those args whenever it is non-empty.

This nested invocation relies on `/plan-harden`'s frontmatter declaring `Agent` in `allowed-tools` (which it does). The Skill tool executes within the main conversation, so `/adversarial-review`'s Phase 2b fork-spawning will run with this command's tool permissions, not the nested skill's frontmatter.

### 2.1b: `targeted` mode — one unit at a time against a fixed contracts block

Selected by §2.0b on a large review surface, and ALSO whenever a whole-document verify round
answers its `## Convergence` question with "different latent defects / another round would not
close this document" (`scope-too-wide`). One unit = ONE SESSION plus the items it declares,
reviewed against a ≤15 KB verbatim block of the cross-unit contracts and `RESOLVED_LEDGER`,
several units in parallel. **Full procedure, prompt contract and the measured evidence:
`Read ~/.claude/references/plan-harden/targeted-review.md`.**

### 2.2: Wait for completion

`/adversarial-review` is itself an orchestrator. It will:
- Spawn a Claude fork via Agent
- Launch background Codex via `Bash(run_in_background: true)`
- Wait for both, synthesize, and (Phase 3 of `/adversarial-review`) auto-edit `PLAN_FILE` with `[HARDENED:...]` tags
- Then run a **default Codex verify loop** (`task --resume-last`, read-only, `--effort xhigh`) that re-checks the hardened plan and writes a `*-REVIEW-LOG.md` audit trail beside `PLAN_FILE`. Rounds 1–2 always run; a 3rd runs ONLY if round 2 keeps surfacing strong (new `[HIGH]`/`[CRITICAL]`) findings — the **convergence gate** stops at round 2 when only minor items remain (`converged@r2`, NOT a deadlock). If Codex still has strong unresolved findings at the 3-round cap it ends in a flagged **DEADLOCK** (it does NOT fake an approval).

Do NOT kill it if it runs long — killing mid-write would corrupt the `/tmp/` artifacts. The verify loop adds up to 3 foreground Codex rounds, so let it run up to 15min, then warn user but still proceed.

**Hold (re-assert, on return).** When `/adversarial-review` returns — success OR hard error — run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-2 || true` before anything else, exactly as §1.2 does after the grill: the nested skill runs in this session and may have written its own safe point into the last-writer-wins store, and the caller who owns the window re-asserts it on every return instead of relying on the callee's behaviour. (The measured fact behind that rule: the directory `skills/grill-with-docs` is a tracked symlink, git mode 120000, into `~/.agents`, with nothing under it tracked in this repository — so `ls -l` on a file inside it reports a regular file only because it follows the link, and neither callee's safe-point behaviour is code this command ships.)

If `/adversarial-review` returns a hard error or both reviewers fail (no `[HARDENED:...]` tags applied):
- Mark Phase 2 as ⊘ in the summary
- Do NOT halt — proceed to Phase 3 (premortem) and Phase 4 (synthesis)
- Note the failure in the synthesis: `"Phase 2 ⊘ failed: <reason from /adversarial-review error>"`

**Auditability requirement** `[HARDENED:wave1-contract]`: the Phase 4 summary's `adversarial <✓ N hardenings | ⊘ failed: <reason>>` slot — specifically the `⊘` branch — is reserved for HARD ERRORS from `/adversarial-review` itself (timeout, both reviewers crashed, plan file vanished, network failure on Codex bash). It is NOT a slot for "I decided to inline." If a runner completes a run without invoking the Skill, the summary block MUST explicitly write `adversarial ⊘ CONTRACT VIOLATION: runner inlined Phase 2 (see §2.0)`. This makes the violation visible in audit rather than masked as a normal `⊘ failed`.


---

## PHASE 3 — Klein premortem

**Safe point — `hold`, not `ok`.** Run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-3 || true` before starting (skipped phase or not): Phase 2's hardenings are applied to the plan file, but §3.1 reads `ENRICHMENT_FINDINGS.blindspot.findings` out of context.

**Skip if `--quick` was passed OR `--from-phase` > 3. Note in summary: `premortem ⊘ (--quick)`.**

This is acknowledged inline reasoning, not orchestration delegation.

**Overlap allowance** *(2026-08-03)*: §3.1's independent hypothesis consumes only the plan and Fork D's findings, and Phase 2's Codex verify rounds leave the orchestrator idle for minutes — you MAY form the hypothesis during those waits. Klein independence holds as long as it is committed BEFORE reading Phase 2's findings; the §3.2 reconciliation then runs after Phase 2 lands and may weigh its findings the same way it weighs Fork D's.

### 3.1: Self-prompt

First, reason through this prompt yourself — independently, before seeing the empirical findings below — using extended thinking where it materially helps:

> Ship date + 6 months. The plan at `<PLAN_FILE>` was implemented and shipped. Today, it broke. Walk back from the failure: what was the most likely root cause? Be specific — name the concrete failure mode, the surface that broke, the user impact. Do NOT name "general complexity" or "scope creep alone". The honest most-likely failure has a concrete shape.

Form your own hypothesis first. Then, after forming it, weigh these empirical territory findings for the code this plan touches and reconcile your hypothesis against them — a documented reverted attempt or mid-flight migration in the touched code is stronger evidence than a hypothesized one:

> Territory blindspot findings (empirical, not imagined — weigh these first if any name a landmine directly on the plan's path):
> <ENRICHMENT_FINDINGS.blindspot.findings, or "none — Fork D skipped/empty: <skip_reason>">

If a Fork D finding directly names a landmine on the plan's execution path (a prior reverted attempt at the same change, a mid-flight migration the plan doesn't account for, a flag the plan doesn't check), prefer it over your purely hypothesized failure mode — it is a stronger prior. Revise your hypothesis accordingly before producing the final output below.

### 3.2: Output

Produce a one-paragraph (3-5 sentences) failure hypothesis, plus a single classification tag from this list (pick the BEST fit):

- `assumption_drift` — a load-bearing assumption from the plan stopped being true
- `missing_dependency` — an unstated prerequisite tripped deployment
- `scope_creep` — the team built more than the plan and the new pieces broke
- `production_constraint` — prod environment differed from plan's mental model (perf, scale, network, IAM, etc.)
- `vendor_change` — a third-party API/library changed under the plan
- `team_handoff` — the person who shipped wasn't the person who designed (knowledge loss)
- `monitoring_blind_spot` — broke silently because no signal was emitted for the failure mode
- `rollback_failure` — known-bad change but no clean rollback path

Hold the result as `PREMORTEM = {paragraph: "...", class: "<tag>"}`.

---

## PHASE 4 — Synthesis (idempotent)

**Safe point — `hold`, not `ok`.** Run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --phase phase-4 || true` before starting: §3.2 holds `PREMORTEM` in context and this is the phase that aggregates it, so an `ok` here would permit exactly the compaction that destroys Phase 4's input. The release comes at §4.3, once §4.2 has written the summary to disk.

This phase ALWAYS runs (regardless of skipped phases). Aggregates all phase outputs into a single summary section appended/replaced in `PLAN_FILE`.

### 4.0: Model-selection sanity lint (lightweight, always-on)

<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
<!-- canonical source: ~/.claude/model-routing.yaml. This §4.0 lint reads that file's
     `task_classes:` (defaults), `codex_peer.lint_keywords` (the codex-trigger-no-gate
     keyword list), `active_provider:` + `executor_policy:` (s07 `task-class-model-mismatch`
     effective-provider resolution — reused via resolve_route.py + run.py's
     `_executor_for`/`_session_barred`, never hand-reparsed) as its data source.
     verify-routing.sh --full check (c) enforces this stamp's version stays in lockstep
     with the SSOT. -->

A cheap deterministic scan for **plan-builder plans** — flags model/reasoning rubric
violations in the plan being hardened. **Data source order:** read task-class defaults
from `~/.claude/model-routing.yaml` (`task_classes:` block) when the file exists and
parses; fall back to `~/.claude/skills/plan-builder/references/schemas.md` → "Model +
reasoning rubric" only if the SSOT is missing/unparseable (note which source was used
in the Phase 4 summary — `model-lint source=ssot|schemas-fallback`). No model call, no
external dispatch. **Skip only if this is not a plan-builder plan** (no sibling
`manifest.json`/`spec.json` and no session cards carrying `model`/`reasoning`) — note
`model-lint ⊘ (not a plan-builder plan)`.

Read the sessions from `manifest.json` (`sessions[].model` / `.reasoning` /
`.dispatch.subagent_type`) when present, else parse the plan's session cards.

**Schema v8 (`plan_schema_version` ≥ 8, route-at-dispatch):** the executor picks the
model from `task_class`, so the lint stops grading the author's model pick. It checks the
class and risk declarations instead: `task-class-missing` 🔴, `override-without-reason` 🔴,
`override-incomplete` 🔴, `override-below-floor` 🔴 (session has `peer_triggers` or is `linchpin`), and
`locked-check-suggested` 🟡. Every rule that compares a declared `model` with the rubric
runs on a v8 session only when it declares an override. Below v8 nothing changes. Detail:
`model-lint.md` → "Schema v8 — route-at-dispatch gate".

**Full rule set (all flags — Opus+max, Fable escalation, opusplan mismatch, hard session
at medium, blank reasoning; the s04/SKL-02 structural rules pin-conflict /
codex-trigger-no-gate / specialist-exists-but-null-subagent; the s07
`task-class-model-mismatch` routing rule; the s08/LN-01 Codex-lane rules; and the three
non-model rules acceptance-review-missing / decision-debt / blind-executability — each
with severity tag and rationale): `Read
~/.claude/references/plan-harden/model-lint.md`.** Apply every flag in that
file; hold the result as `MODEL_LINT = [{session, issue, recommendation, severity}, …]`
(empty list if clean). **Non-blocking by design, with exceptions** — every flag is
🟡 Polish / 🟣 Known-debt EXCEPT the v8 structural flags (`task-class-missing`, `override-without-reason`, `override-incomplete`, `override-below-floor`, all 🔴 and deterministic) and `peer-gate-missing` (a session carrying a non-empty
structured `peer_triggers` array with no `adversarial-review` gate), which is a 🔴
plan-killer: deterministic (a declared field, not a heuristic) and self-evidenced (the
`peer_triggers` value), satisfying the §4.1 🔴 gate.

If an `EARLY_MODEL_LINT` pass ran at S.3, this pass supersedes it — re-run against the
CURRENT manifest (Phases 1-2 mutate sessions), and note in the summary any early flag
that grilling/hardening already resolved.

### 4.0b: Parallelization-opportunity lint (plan-builder plans, advisory)

*(Added 2026-08-03.)* A structural scan for safe concurrency in the plan's session DAG —
runs HERE, after the last plan mutation, because Phases 1-2 change the DAG itself (a
hardening pass can add cross-session dependencies, invalidating any grouping computed
earlier). Near-free (structured-field checks + one prose pass). **Skip only if this is
not a plan-builder plan** — note `parallel-lint ⊘ (not a plan-builder plan)`.

**Full rule set (isolation eligibility; write-conflict matrix; the deterministic half
delegated to `parallel_contract.check()` — file-write-conflict / merge-cost /
member-shipping-declared / integration-session-gap; the judgment half —
gate-mutates-global-state / tree-scoped-gate / checkpoint-member /
data-dependency-in-prose; semantic-ordering suppressor; session-split decomposition
patterns; verdict + decision-card contract): `Read
~/.claude/references/plan-harden/parallelization-lint.md`.** Hold the result as
`PARALLEL_LINT = {verdict: linear-optimal|opportunities, blockers, warnings, options}`.
Three invariants: **never auto-apply** `parallel_group` OR `dispatch.isolation` (the lint
recommends with evidence; the operator elects — a wrong grouping corrupts a shared-tree
run, and a silently isolated group also conscripts an integration session the operator
never agreed to); a `data-dependency-in-prose` hit is a 🟡 correctness finding in its own
right regardless of verdict; and on `opportunities` the ≤3-option decision card goes in
the §4.3 output, with the operator's election (including "keep linear") recorded in the
summary block so no later run silently re-opens it.

*(Re-derived 2026-08-12, S08/PL-04.)* Since the executor honours
`dispatch.isolation: "worktree"`, a shared write between two ISOLATION-ELIGIBLE sessions
is a quantified merge cost rather than a blocker, and tree-scoped gates no longer block
(they run inside the member's worktree). Both revert to their old blocking form for a
pair that is not isolation-eligible — a pre-v3 manifest, a lockfile touch, or a missing
integration session. The reference file's rule ledger is the authority on which is which.

### 4.0c: Contradiction sweep (deterministic, MANDATORY after every mutation)  *(2026-08-21)*

**The single highest-yield check in this command, and it needs no model.** After EVERY batch of
hardenings — including those applied between verify rounds — assert that what each fix claims
to have replaced is actually gone: a deleted-phrase sweep, an item-vs-prompt sweep, a
field-vs-prose sweep on every branch the prompt permits, and a schema-vs-writer sweep. Record
`sweep ✓ N classes clean` (or the survivors) in the Phase 4 summary, and probe any all-clear
with a known positive before trusting it — a sweep pointed at the wrong string reports "clean"
and "I did not look" identically. **The five sweeps and the measured rationale:
`Read ~/.claude/references/plan-harden/contradiction-sweep.md`.**

### 4.0d: Reviewability lint — is any session too wide to converge?  *(2026-08-21)*

`build_plan.py` already WARNS when a session writes past its measured p90 of 6 files, and
explains why: *"One LLM review pass samples a surface that size at 15-31% recall and falls
further as it grows, so rework rounds will keep finding NEW things in untouched code."* Nothing
in this command acted on that warning. Now it does — as a RECOMMENDATION with evidence and a
decision card, never an auto-apply, exactly as §4.0b treats `parallel_group`.

**NO STATIC SIZE PROXY PREDICTED CONVERGENCE — both were falsified on the plan that produced
this rule** (2026-08-21, corrected the same day after the first version of this section asserted
one of them from an unverified number):

| session | brief | distinct files | items | rounds | outcome |
|---|---|---|---|---|---|
| s08 | 8,850 | 1 | 1 | 2 → 4 → **0** | APPROVE |
| s09 | 7,231 | 2 | 1 | 8 → 8 → 6 → 3 | code, not size |
| s07 | **19,171** | **7** | 1 | 7 → 1 → — → 2 | **converged** |
| s02 | 21,501 | 5 | **4** | 8 → 5 → 2 → **4** | **never converged** |

s07 is the longest-but-one AND the widest-but-one, and it converged. s02 is NARROWER than s07
and never did. So neither brief length nor file count is the discriminator, and a lint keyed on
either would have flagged the healthy session. (The "12 files" originally read off s02 was a
counting bug in `build_plan._declared_writes`, which did not dedupe paths across a session's
items; fixed the same day. Do not rebuild this rule on that number.)

What actually singles out s02 here is that it is the only MULTI-ITEM session (4 items) and the
only one with BRANCH CONDITIONALITY (two mutually exclusive bundles, doubling every rule a
reviewer must hold at once). That is a plausible mechanism, but it is **n = 1** — offer it as a
hypothesis in the decision card, never as a threshold.

Three signals, and only the last one is evidence:
1. **Observed non-convergence** (the trigger). Findings did not FALL across two consecutive
   targeted rounds on that unit. Available only after §2.1b ran. This is the ONLY signal here
   with no measured counter-example, so it decides.
2. **Embedded code** — findings concentrating on a snippet in the prompt. Rule 13's territory:
   the remedy is to MOVE the code to whoever owns its tests, NOT to split the session. Measured:
   that move closed six findings at once where three review rounds had closed none.
3. **Static hints** (weak, each falsified above) — a brief past `build_plan.py`'s 6,400-char p90,
   a `touches` span past its 6-file p90, more than one item, or branch conditionality. Worth a
   look, never worth a flag on their own. Say "hint" in the output, not "finding".

**Never split automatically.** Splitting changes the DAG, `depends_on`, gate placement and
possibly a checkpoint's position — that is the operator's call. Emit a ≤3-option decision card:
split at a named item boundary (name it from the session's own item list); extract the
untestable part to its proper owner (what actually resolved the measured case — the code moved
to the session that owns its test suite, and six findings closed at once); or accept, with the
session's human checkpoint as the standing control. Record the election in the summary so a
later run does not silently re-open it.

### 4.1: Build the summary block

Severity classification rule:
- 🔴 **Plan-killer** — finding is severity HIGH or CRITICAL with confidence ≥ 0.7, OR is a consensus finding (per `/adversarial-review` synthesis), OR is a finding left UNRESOLVED when the Codex verify loop hit DEADLOCK (Codex never confirmed its own fix landed — treat as unverified, fail-closed)
- 🟡 **Polish** — severity MEDIUM, OR HIGH with confidence < 0.7
- 🟣 **Known-debt** — severity LOW, OR a finding the user explicitly accepted during grilling

**Quoted-evidence requirement for 🔴** (quote-the-line gate): a 🔴 plan-killer classification must carry quoted evidence — the verbatim motivating line or verbatim source quote plus its location (`file:line`, plan section, or review-log round). A finding that otherwise qualifies as 🔴 but has no verbatim evidence is reported as 🟡 with the note `(downgraded: no quoted evidence produced)`. DEADLOCK-unresolved findings satisfy this via their verbatim entry in `*-REVIEW-LOG.md`.

A verify-loop **DEADLOCK** forces `recommend_exit_now = no` regardless of other counts: the plan carries findings a second model could not confirm fixed. Read the `*-REVIEW-LOG.md` for the unresolved set. A **`converged@r2`** outcome is NOT a deadlock — it means round 2 settled to only minor `[MEDIUM]`/`[LOW]` items (logged under "✓ CONVERGED"); classify those as 🟡 Polish / 🟣 Known-debt, and do NOT force `recommend_exit_now = no` on their account.

Pull findings from:
- `ENRICHMENT_FINDINGS` (Phase 0)
- The `[HARDENED:...]` annotations now in `PLAN_FILE` (Phase 2 output, including any `[HARDENED:codex-verify-rN]` tags from the verify loop)
- The `*-REVIEW-LOG.md` written by Phase 2's verify loop — read its final round; any "UNRESOLVED AT DEADLOCK" entries are 🔴 plan-killers
- The grilling exchange (Phase 1) — extract any explicit "let's add X to the plan" decisions
- `PREMORTEM` (Phase 3) — including any Fork D landmine it weighed in on
- `MODEL_LINT` (Phase 4.0) — fold each flag into 🟡 Polish or 🟣 Known-debt per its severity, EXCEPT `peer-gate-missing` and the four v8 structural flags (`task-class-missing`, `override-without-reason`, `override-incomplete`, `override-below-floor`) which are 🔴 (the one structured, self-evidenced model-lint flag allowed to block — see §4.0)
- `PARALLEL_LINT` (Phase 4.0b) — the verdict goes in the Phases-run line; any `data-dependency-in-prose` hit lands under 🟡 Polish; the operator's grouping election (or "keep linear") is recorded verbatim

Compose the block:

```markdown
## /plan-harden Summary

**Run metadata**: timestamp <ISO8601>, version v1.0.0, args `<original $ARGUMENTS>`
**Phases run**: enrichment <✓ N hits | ⊘ skipped> (memory: <n>, research: <tier>, edge-cases: <n>, blindspot: <n | ⊘ reason>), grill <✓ ~N exchanges (mode=interactive|auto-accept) | ⊘>, reviewability <✓ N sessions flagged (elected: <choice>) | clean>, adversarial <✓ N hardenings, verify=<approved@rN | converged@rN (M minor) | deadlock@rN (M unresolved) | codex-error | n/a>, log=<path> | ⊘ failed: <reason>>, premortem <✓ | ⊘>, model-lint <✓ N flags | clean | ⊘ (not a plan-builder plan)>, parallel-lint <linear-optimal | opportunities (elected: <choice>) | ⊘ (not a plan-builder plan)>
**Token cost**: ~<N>k total (coarse self-estimate)

When auto-accept mode ran (the default), the `N exchanges` count for the grill slot comes from counting `- Q:` lines in the freshly-written `## Grill auto-accept log` section of `PLAN_FILE`.

**🔴 Plan-killers** (must address before exit):
- <finding 1 — short> — source: <phase>
- ...

**🟡 Polish** (worth fixing):
- ...

**🟣 Known-debt** (flag, accept):
- ...

**Premortem (6mo failure prediction)**: <PREMORTEM.paragraph> [class: <PREMORTEM.class>]

**Hand-off envelope**:
```
plan-harden:
  hardenings_applied: <n>
  blockers_remaining: <n>
  premortem_class: <tag>
  recommend_exit_now: <yes|no>
```
```

`recommend_exit_now` = `yes` ONLY if blockers_remaining == 0. Otherwise `no`.

### 4.2: Apply to plan file (idempotent)

**ORDERING — READ THIS FIRST, IT IS A REAL BUG IF YOU GET IT WRONG**  *(2026-08-21)*. On a
plan-builder plan, `PLAN_FILE` is `PLAN.html`, which is GENERATED from `spec.json`. So the
rebuild that §4.2b REQUIRES destroys anything §4.2 writes into it. The order is fixed:

1. Write the summary to a DURABLE sibling file — `PLAN-HARDEN-SUMMARY.md` — which no rebuild
   touches. This is the copy of record.
2. Do §4.2b's backport and the LAST rebuild.
3. ONLY THEN insert the summary into `PLAN.html`, between the idempotent anchors
   `<!-- PLAN-HARDEN-SUMMARY:BEGIN -->` / `<!-- PLAN-HARDEN-SUMMARY:END -->` placed
   immediately before `</main>`, stripping any prior block first.
4. RENDER the result and confirm the section is in the live DOM. A byte count is not proof —
   see Rule 12.

Any later `--rebuild` wipes step 3 again; that is expected, and step 1 is why it does not
matter. On a non-plan-builder plan (a plain `.md`) steps 2–4 do not apply and the summary
goes straight into `PLAN_FILE`.

Read `PLAN_FILE` once.

**Idempotency rule**: search for an existing `## /plan-harden Summary` heading.
- If found → REPLACE the existing section (everything from that heading until the next `## ` heading or EOF) with the new block. Use `Edit` with the full old section as `old_string` and new block as `new_string`.
- If NOT found → APPEND the new block at the EOF (preceded by a blank line).

**Preserve prior `[HARDENED:...]` tags from prior `/adversarial-review` runs.** Do NOT strip them when rewriting the summary section. The summary section never contains `[HARDENED:...]` tags itself — those live inline in the plan body.

If the plan file structure changed unexpectedly during the run (e.g. heading depth shifted) such that idempotent replacement is ambiguous: append a fresh summary AND prepend `> WARNING: plan structure changed during /plan-harden run; previous summary may still be present above` to the new section.

### 4.2b: Backport hardenings to the rebuild source (plan-builder plans)

If `PLAN_FILE` lives in a **plan-builder plan directory** (a sibling `spec.json` + `manifest.json` + `sessions/*.prompt.md` exist), then `PLAN.html` and `sessions/*.prompt.md` are GENERATED artifacts — `build_plan.py --rebuild` regenerates the prompts from `spec.json` and will **silently wipe any hardenings** applied only to those files. So whenever this run applied hardenings into `sessions/*.prompt.md` (or into the dashboard's session bodies), you MUST also backport them into `spec.json`:

1. For each edited session, set `spec.json` session `prompt` = the current prompt-file body between `## Work` and the first of `## Verification gates` / `## Post-session actions` / `## Closeout` (stripped).
2. Write `spec.json` back (preserve JSON shape: `json.dump(..., indent=2, ensure_ascii=False)` + trailing newline).
3. **Verify the round-trip:** rebuild to a TEMP dir (`build_plan.py spec.json /tmp/<x>`) and diff
   the regenerated prompt bodies against the live ones. They must match **after normalising the
   plan-directory path** — `build_plan.py` stamps the output directory into a header line
   (*"The plan dashboard is at the sibling `PLAN.html` in this directory: `<dir>/`"*), so a
   temp-dir build ALWAYS differs there and a literal byte-for-byte demand can never be met
   [2026-08-21]. Normalise that one line, then require byte equality on everything else. Never
   overwrite the live plan dir during verification.

Report the backport + round-trip result in the completion output. (Originating: 2026-06-21 — hardenings landed only in PLAN.html + prompt files; the user caught that a rebuild would wipe them. See `reference_in_place_revision_no_contradictory_layers.md`.)

### 4.3: Final user-facing output

**Safe point — release.** Run `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state ok --phase phase-4 || true` now: §4.2 wrote the summary to `PLAN-HARDEN-SUMMARY.md` and the plan file, nothing this run built lives only in context any more, and this clears the hold the run has carried since Phase 0's fork dispatch (since Phase 1, under `--quick`).

Print to user:

```
✓ /plan-harden complete

Summary section written to: <PLAN_FILE>
Plan-killers: <n> (recommend_exit_now: <yes|no>)
Parallelization: <linear-optimal | N opportunities — decision card below | ⊘>
Total token cost: ~<N>k (coarse self-estimate)

Next steps:
- If recommend_exit_now=yes: review the summary, then ExitPlanMode.
- If recommend_exit_now=no: address the 🔴 items, then re-run /plan-harden
  (idempotent — will replace the summary, not duplicate it).
```

On `opportunities`, follow this block with the §4.0b decision card; `linear-optimal` gets its one line and NO card.

---

## --quick mode

Behavior: skip Phase 0 + Phase 3. Phase 1 runs WITHOUT the enrichment-context print (just invokes `/grill-with-docs` directly). Phase 2 runs as normal. Phase 4 runs and notes the skipped phases as ⊘.

This is a thin wrapper around the user's existing manual chain (`/grill-with-docs` → `/adversarial-review`), eliminating only the ExitPlanMode-and-reenter friction. Token cost should be ≤ baseline × 1.2.

---

## Failure modes & recovery

Full failure-mode table (Workflow/Agent-fork errors, `/adversarial-review` errors or
long-runs, Ctrl-C mid-run, deleted plan file, structure drift), the `--from-phase N`
resumption preconditions, and the rough token-budget numbers per phase: `Read
~/.claude/references/plan-harden/failure-modes.md`.

---

## Rules

1. **Orchestrator-first.** Never do review work that an existing skill can do. Two exceptions ONLY: Phase 3 premortem (inline reasoning by design) and Phase 0 Fork C inline edge-case prompt (always on the Workflow path; on the fallback path only when BMAD absent). **Specifically forbidden** `[HARDENED:wave1-contract]`: substituting inline reasoning for Phase 2 `/adversarial-review` on grounds of (a) "plan mode is read-only" — false, see §2.0; (b) "context/token budget" — `--quick` is for that, see §2.0; (c) "I can do an abbreviated review faster" — no, dual-model + ULTRATHINK is the value prop, not incidental. The runner's job is to invoke; the skill's job is to review.
2. **Detect, don't introspect from forks.** All capability detection happens in S.3 by the orchestrator. Forks (and the Workflow script, via `args`) receive detection results explicitly.
3. **Forks emit structured JSON, period.** Schema-forced on the Workflow path; parsed-or-error on the fallback path. No silent corruption.
4. **Phase 4 is idempotent.** Re-running `/plan-harden` REPLACES the summary section, never appends a second one. Prior `[HARDENED:...]` tags survive untouched.
5. **--quick is the friction-reduction wrapper.** Same flow as the user's manual chain, just chained.
6. **Anti-sycophancy.** Do not praise the plan. State what changed and why.
7. **Single-file mutation.** Only `PLAN_FILE` is ever edited by `/plan-harden`. Skills called via Skill may mutate other files (e.g. `CONTEXT.md`, ADRs) — that is their concern, not yours.
8. **Honest skip notices.** If a fork was skipped, say WHY in the summary (path, missing MCP, etc.) — not just "skipped".
9. **Auto-accept is auditable.** In auto-accept mode (the default), all auto-accepted Q+A pairs MUST land in a `## Grill auto-accept log` section of the plan file so the user can review post-hoc what the grilling skill decided on their behalf. The Phase 4 summary's `mode=auto-accept` tag is the entry point to that log.
10. **Plan-file required.** This command refuses to run without a resolvable plan file (Phase 4 must mutate something).
11. **Severity rule for the summary block** is in Phase 4.1. Apply consistently.
12. **Never report an artifact correct from a structural proxy** *(2026-08-21)*. A byte count, a
    tag count, an exit code or an anchor count is not evidence the reader sees what you think.
    Render `PLAN.html` and confirm the summary is in the live DOM before saying it landed. And
    when a count comes back ZERO, establish whether the zero is CORRECT before reporting it —
    a check pointed at the wrong string returns "clean" and "I declined to look" identically.
13. **Embedded code is EXECUTED, never merely reviewed** *(2026-08-21)*. When a session prompt
    carries a code snippet, a hardening pass that only reads it is not finished. **Better: move
    it out.** `build_plan.py` now warns on any heredoc of 10+ lines in a session prompt, and the
    standing plan-builder rule is that one session BUILDS a script with tests and later sessions
    RUN it — a prompt that names a tested script has nothing for this rule to execute. Run it against
    one KNOWN POSITIVE (it must produce the affirmative result) and at least one negative per
    branch (each must fail for ITS OWN reason), then re-extract the snippet FROM the authoring
    source and run it again, so the code a builder receives is the code that was tested.
    *Measured:* a snippet that passed `ast.parse` shipped with a `NameError` on its first real
    line; executing it against seven fixtures then found two further defects that four rounds
    of two-model review had not — both introduced by fixes applied minutes earlier.
14. **Never state a number you have not re-measured at report time** *(2026-08-21)*. Counts
    drift across rounds; a wrong number inside a correct report is the hardest error to catch
    because everything around it is right. Re-run the command that produces each figure, or
    label it unverified.

---

## Changelog

- **2026-08-21** — Scope, sweep and execution, all from one measured session (three hardening
  passes over a 107 KB plan; evidence in that plan's `PLAN-REVIEW-LOG.md`). (1) NEW §2.0b: the
  review surface is MEASURED and, above ~60 KB, Phase 2 runs in `targeted` mode — a
  whole-document verify loop does not converge on a large artifact, it resamples it
  (19→10→1→1 then 12→6→13→9 across two passes, with pass 2's first round finding a CRITICAL
  inside the fix pass 1 ended on). (2) NEW §2.1b: targeted mode reviews ONE session at a time
  against a ≤15 KB verbatim contracts block — 25 findings in one parallel round against the
  whole-document loop's 9, and the target session converged 7→1. (3) NEW §4.0c: a deterministic
  contradiction sweep after EVERY mutation — *a fix applied in one place while the replaced
  text survives elsewhere* was the majority defect in every round of all three passes, and an
  ad-hoc sweep caught 9 findings neither model did. (4) NEW S.1b: `--from-phase` loads the
  prior `*-REVIEW-LOG.md` as a findings ledger so a second pass stops re-litigating a settled
  first. (5) §4.2 ordering fixed: the summary is written to a durable `PLAN-HARDEN-SUMMARY.md`
  and inserted into `PLAN.html` only AFTER the last rebuild — §4.2b's required rebuild was
  destroying it. (6) §4.2b's round-trip check now normalises the plan-directory path, which the
  builder stamps into a header line, so the check can actually pass. (7) Rules 12–14:
  render-verify instead of trusting structural proxies, EXECUTE embedded code against a known
  positive and per-branch negatives, and re-measure every number at report time. Companion
  edits: the verify prompt now demands a `## Convergence` answer and the loop branches on it
  (new `scope-too-wide` outcome handing off to §2.1b); the review log records `verifier=` mode
  every round; the model-lint checks `max_rework` against the schema's ceiling, which was
  RAISED from 5 to 6 later the same day (`plan_limits.MAX_REWORK_CEILING`) because the longest
  ladder — `sonnet@medium`, the `standard_build` default — needs 6, so the computed number is
  now buildable for every cell instead of being clamped to one that cannot reach the apex.

- **2026-08-03** — Ordering pass + parallelization lint, from the agent-janitor run's evidence. (1) NEW §4.0b `PARALLEL_LINT` (reference: `references/plan-harden/parallelization-lint.md`): deterministic write-conflict/gate/commit blockers + decision-card contract, advisory-only, placed AFTER the last plan mutation because hardening changes the DAG (observed: grilling added a cross-session data dependency). (2) §4.0 model-lint now ALSO runs early at S.3 as advisory grill enrichment — a 🔴 discovered only at Phase 4 arrives after the dominant Phase-2 spend; the Phase-4 pass stays authoritative. (3) Phase 3 premortem may overlap Phase 2's verify-loop waits (hypothesis committed before Phase 2 findings are read; reconciliation may then weigh them). Macro phase order confirmed correct against the same run: grilling before the dual-model pass let Phase 2 attack the improved plan — and catch a CRITICAL that Phase 1 itself introduced, which is the layering working, not an ordering defect.
- **2026-07-09** — Phase 4.1: 🔴 plan-killer now requires quoted verbatim evidence (else downgraded to 🟡 with note). Companion edits in `/adversarial-review`: decision primer + relitigation suppression (R29) and fix-landed check (R30) in the Codex verify loop, quote-the-line gate at synthesis, fresh-context per-finding validator rule. Source: everyinc/compound-engineering-plugin gap review — ce-doc-review R29/R30 + ce-code-review quote gate.
- **2026-07-01** — `effort` bumped `medium` → `high` for a deeper Phase-3 premortem + Phase-4 synthesis. Skill/command frontmatter effort is documented-honored and verified in binary 2.1.170 (overrides session effort; `CLAUDE_CODE_EFFORT_LEVEL` env var still wins).
- **Cascade-to-nested-Skills A/B protocol** (`~/.claude/fixtures/plan-harden-corpus/ab-fork-inheritance-protocol.md`) remains **empirically unverified** — the `claude /usage` measurement scaffolding it depends on has been structurally dead since 2026-06-10 (no headless token output), so the A/B cannot complete.
