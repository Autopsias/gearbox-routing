---
description: "Pre-flight harden plan before exit. Runs parallel enrichment (project memory + external research + edge-cases), invokes /grill-with-docs and /adversarial-review with enrichment context loaded, then a Klein-style premortem and severity-tagged synthesis. Use when in plan mode and want maximum critique surface, or with --quick to just chain the existing two skills."
argument-hint: "[--quick] [--interactive-grill] [--plan-file PATH] [--from-phase N] [--no-memory] [--no-research] [--no-edge-cases] [--no-blindspot]"
allowed-tools: ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Skill", "Agent", "Workflow", "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
effort: high  # cascades to nested skills as adaptive-thinking guidance (cost-only tradeoff); see ## Changelog for history. Delete this line to inherit the session level.
---

# /plan-harden — meta-orchestrator for plan mode

You are a **pre-flight hardening orchestrator**. The user has a plan (in plan mode or in a file) and wants maximum critique surface before they ExitPlanMode and ship it. Your job is to compose existing skills (`/grill-with-docs`, `/adversarial-review`, `bmad-review-edge-case-hunter`) into a single chained workflow with three layers added: (a) parallel enrichment from project memory + external research + edge-cases, (b) a Klein-style premortem, (c) a severity-tagged synthesis that mutates the plan file once.

> **Scope of generalizability**: this command works for any project where this user's Claude Code setup is configured. The cwd-slug memory pattern is a Claude Code internal convention this user's machine follows; portability across machines, users, or non-Claude agents is not claimed.

**You orchestrate; you do not do the review work yourself**, with two exceptions explicitly inlined: the Klein premortem (Phase 3) and the inline edge-case prompt (Phase 0 fork C — always on the Workflow path; on the Agent-fork fallback path only when BMAD is absent).

**Terminology:** a *finding* is raw input from a fork/reviewer/premortem; a *hardening* is a finding actually applied to `PLAN_FILE` (tagged `[HARDENED:...]`); a *flag* (§4.0 only) is a MODEL_LINT rubric violation, never promoted past 🟡/🟠.

**Frontmatter configuration**: `effort: high` (delete the line to inherit the session level). Neither `/grill-with-docs` nor `/adversarial-review` pins its own `effort`; effort is soft adaptive-thinking guidance, so IF the frontmatter cascades to them, `high` only deepens the nested reviews (more cost/latency, never broken correctness) — acceptable for a tool whose whole purpose is maximum critique surface. See `## Changelog` for the dated history of this setting and its unverified A/B protocol.

Args: "$ARGUMENTS"

---

## SETUP — Argument parsing, plan resolution, environment detection

Use `TaskCreate` to enumerate phases as tasks so progress is visible. Mark each `in_progress` at start, `completed` at end.

### S.1: Parse `$ARGUMENTS`

Set these flags (all default false unless flag present):

| Flag | Effect |
|---|---|
| `--quick` | Skip Phase 0 + Phase 3. |
| `--interactive-grill` | Phase 1: run `/grill-with-docs` interactively (pause for the user's answers). **Auto-accept is the DEFAULT** (operator standing order, 2026-07-03): without this flag, grilling auto-accepts its own recommended answers. `--auto-grill` is still accepted as a no-op for back-compat. Composes with `--quick`. |
| `--plan-file PATH` | Override plan auto-detection. |
| `--from-phase N` | Resume from phase N (0/1/2/3/4). |
| `--no-memory` | Force-skip Fork A. |
| `--no-research` | Force-skip Fork B. |
| `--no-edge-cases` | Force-skip Fork C. |
| `--no-blindspot` | Force-skip Fork D. |

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
- Tier 2: `mcp__exa__web_search_exa` or `mcp__exa__deep_researcher_start`
- Tier 3: `mcp__ref__ref_search_documentation`

Build `RESEARCH_TIERS_AVAILABLE = ["perplexity"|"exa"|"ref"|...]`. If empty, Fork B will skip.

**Repo-profile cache probe** (optional, never blocking): if `~/.claude/scripts/repo-profile-cache.py` exists, run `python3 ~/.claude/scripts/repo-profile-cache.py get` — on `HIT`, hold the profile JSON (line 2 of the output) for fork dispatch (see the pre-dispatch section in `references/plan-harden/fork-prompts.md`). Any other status word, a missing script, or an error → proceed exactly as if the probe never ran.

**Edge-case skill detection**: scan available-skills system message for `bmad-review-edge-case-hunter`. Set `BMAD_EDGE_CASE_AVAILABLE=true|false`.

**Blindspot detection + scan-target derivation**: scan available-skills system message for `blindspot`. Set `BLINDSPOT_AVAILABLE=true|false`. If available, derive `BLINDSPOT_TARGETS` — the concrete code areas (dirs/files/modules) the plan under hardening touches — from the plan's session cards / manifest (`sessions[].touches` or equivalent) or, for a freeform plan file, from explicit file paths named in its "Recommended Approach" / "Touches" sections. If the plan touches no code at all (pure docs/process/research plan — no file paths, no dirs, no modules named), set `BLINDSPOT_TARGETS=[]` and record the skip reason `"plan touches no code — skipping blindspot enrichment"`.

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

**Skip this entire phase if `--quick` was passed. Note in the final summary: `enrichment ⊘ (--quick)`.**

**Workflow seam rule (shared with /plan-execute):** a Workflow is bounded analysis that returns a structured report; all human interaction (S.2 plan resolution, Phase 1 grilling, Phase 4 decisions) stays in the main conversation — workflows cannot pause for input. Phase 0 contains no human-input point, so it is a clean fit.

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

Full fork prompt templates (each with its embedded JSON output schema) + fork-output-handling for both dispatch paths: `Read ~/.claude/commands/references/plan-harden/fork-prompts.md`.

---

## PHASE 1 — Grill with enrichment

**Skip this phase only if `--from-phase` was supplied with N>1.**

The plan file may already have been mutated by hand or by prior runs since Phase 0 read it — do NOT re-read it here. The grilling skill will read whatever's on disk when it runs.

### 1.1: Print one-line status (user-facing visibility)

Print to user (not the full findings — args carry those):

```
Phase 1: invoking /grill-with-docs (mode=<interactive|auto-accept>) with <N> enrichment findings loaded (memory: <m>, research: <r>, edge-cases: <e>, blindspot: <b>). Plan: <PLAN_FILE>.
```

### 1.2: Invoke the grilling skill with enrichment in args

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

The grilling skill may itself mutate the plan file (or `CONTEXT.md` / `docs/adr/`). That's fine — Phase 2 reads the post-grill plan, not the pre-grill plan.


---

## PHASE 2 — Adversarial review (dual-model)

**Skip if `--from-phase` > 2.**

Re-read `PLAN_FILE` here (it may have been mutated during grilling).

### 2.0: Phase 2 is NON-NEGOTIABLE — read before invoking  `[HARDENED:wave1-contract]`

**You may NOT inline Phase 2 work.** Phase 2 ≡ the actual `/adversarial-review` Skill invocation. Inline self-review is NOT a substitute (single-model + no ULTRATHINK + no Codex ≠ dual-model adversarial review). The two specific runner rationalizations that have caused this bug in the past, and why each is wrong:

1. **"Plan mode is read-only, so I can't run `/adversarial-review`."** — FALSE. `/adversarial-review` only mutates the plan file via Edit (verified: `~/.claude/commands/adversarial-review.md` Phase 3c). Plan mode explicitly permits editing the plan file — this is what Phase 4 of `/plan-harden` itself requires. If `/adversarial-review` were illegal in plan mode, so would Phase 4 be, and SETUP would have refused. SETUP did not refuse, so plan mode is not the constraint.

2. **"Phase 2 is too expensive (~50K-200K tokens), I'll abbreviate inline."** — OUT OF CONTRACT. Cost management is `--quick`'s job. If the user wanted a cheaper Phase 2 they would have passed `--quick`. Unilaterally substituting inline reasoning for the actual dual-model skill invents a flag the user did not pass.

If you find yourself reasoning "this is plan mode" OR "the budget is tight" as a reason to inline Phase 2: **stop**. Invoke the Skill. The only legal Phase-2-skip paths are: `--from-phase N` with N>2, OR `/adversarial-review` itself returning a hard error. Runner discretion is not on the list.

### 2.1: Invoke

```
Skill(skill="adversarial-review", args="Review the plan at <PLAN_FILE>. Apply hardenings inline.")
```

This nested invocation relies on `/plan-harden`'s frontmatter declaring `Agent` in `allowed-tools` (which it does). The Skill tool executes within the main conversation, so `/adversarial-review`'s Phase 2b fork-spawning will run with this command's tool permissions, not the nested skill's frontmatter.

### 2.2: Wait for completion

`/adversarial-review` is itself an orchestrator. It will:
- Spawn a Claude fork via Agent
- Launch background Codex via `Bash(run_in_background: true)`
- Wait for both, synthesize, and (Phase 3 of `/adversarial-review`) auto-edit `PLAN_FILE` with `[HARDENED:...]` tags
- Then run a **default Codex verify loop** (`task --resume-last`, read-only, `--effort xhigh`) that re-checks the hardened plan and writes a `*-REVIEW-LOG.md` audit trail beside `PLAN_FILE`. Rounds 1–2 always run; a 3rd runs ONLY if round 2 keeps surfacing strong (new `[HIGH]`/`[CRITICAL]`) findings — the **convergence gate** stops at round 2 when only minor items remain (`converged@r2`, NOT a deadlock). If Codex still has strong unresolved findings at the 3-round cap it ends in a flagged **DEADLOCK** (it does NOT fake an approval).

Do NOT kill it if it runs long — killing mid-write would corrupt the `/tmp/` artifacts. The verify loop adds up to 3 foreground Codex rounds, so let it run up to 15min, then warn user but still proceed.

If `/adversarial-review` returns a hard error or both reviewers fail (no `[HARDENED:...]` tags applied):
- Mark Phase 2 as ⊘ in the summary
- Do NOT halt — proceed to Phase 3 (premortem) and Phase 4 (synthesis)
- Note the failure in the synthesis: `"Phase 2 ⊘ failed: <reason from /adversarial-review error>"`

**Auditability requirement** `[HARDENED:wave1-contract]`: the Phase 4 summary's `adversarial <✓ N hardenings | ⊘ failed: <reason>>` slot — specifically the `⊘` branch — is reserved for HARD ERRORS from `/adversarial-review` itself (timeout, both reviewers crashed, plan file vanished, network failure on Codex bash). It is NOT a slot for "I decided to inline." If a runner completes a run without invoking the Skill, the summary block MUST explicitly write `adversarial ⊘ CONTRACT VIOLATION: runner inlined Phase 2 (see §2.0)`. This makes the violation visible in audit rather than masked as a normal `⊘ failed`.


---

## PHASE 3 — Klein premortem

**Skip if `--quick` was passed OR `--from-phase` > 3. Note in summary: `premortem ⊘ (--quick)`.**

This is acknowledged inline reasoning, not orchestration delegation.

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

**Full rule set (all 10 flags — Opus+max, Fable escalation, opusplan mismatch, hard
session at medium, blank reasoning, the 3 s04/SKL-02 structural rules
pin-conflict / codex-trigger-no-gate / specialist-exists-but-null-subagent, the s07
`task-class-model-mismatch` rule (task_class as a ROUTING signal — resolves each
session's task_class through the active provider's effective executor and flags a
declared `model` that contradicts it), plus the non-model blind-executability rule
(wargame contract, prose-heuristic over session prompts) — with severity tags and
rationale for each): `Read
~/.claude/commands/references/plan-harden/model-lint.md`.** Apply every flag in that
file; hold the result as `MODEL_LINT = [{session, issue, recommendation, severity}, …]`
(empty list if clean). **Non-blocking by design, with ONE exception** — every flag is
🟡 Polish / 🟣 Known-debt EXCEPT `peer-gate-missing` (a session carrying a non-empty
structured `peer_triggers` array with no `adversarial-review` gate), which is a 🔴
plan-killer. That flag alone is deterministic (a declared field, not a heuristic) and
supplies its own quoted evidence (the `peer_triggers` value), satisfying the §4.1 🔴
gate. All other model-lint flags remain non-blocking.

### 4.1: Build the summary block

Severity classification rule:
- 🔴 **Plan-killer** — finding is severity HIGH or CRITICAL with confidence ≥ 0.7, OR is a consensus finding (per `/adversarial-review` synthesis), OR is a finding left UNRESOLVED when the Codex verify loop hit DEADLOCK (Codex never confirmed its own fix landed — treat as unverified, fail-closed)
- 🟡 **Polish** — severity MEDIUM, OR HIGH with confidence < 0.7
- 🟣 **Known-debt** — severity LOW, OR a finding the user explicitly accepted during grilling

**Quoted-evidence requirement for 🔴** (quote-the-line gate, 2026-07-09): a 🔴 plan-killer classification must carry quoted evidence — the verbatim motivating line or verbatim source quote plus its location (`file:line`, plan section, or review-log round). A finding that otherwise qualifies as 🔴 but has no verbatim evidence is reported as 🟡 with the note `(downgraded: no quoted evidence produced)`. DEADLOCK-unresolved findings satisfy this via their verbatim entry in `*-REVIEW-LOG.md`.

A verify-loop **DEADLOCK** forces `recommend_exit_now = no` regardless of other counts: the plan carries findings a second model could not confirm fixed. Read the `*-REVIEW-LOG.md` for the unresolved set. A **`converged@r2`** outcome is NOT a deadlock — it means round 2 settled to only minor `[MEDIUM]`/`[LOW]` items (logged under "✓ CONVERGED"); classify those as 🟡 Polish / 🟣 Known-debt, and do NOT force `recommend_exit_now = no` on their account.

Pull findings from:
- `ENRICHMENT_FINDINGS` (Phase 0)
- The `[HARDENED:...]` annotations now in `PLAN_FILE` (Phase 2 output, including any `[HARDENED:codex-verify-rN]` tags from the verify loop)
- The `*-REVIEW-LOG.md` written by Phase 2's verify loop — read its final round; any "UNRESOLVED AT DEADLOCK" entries are 🔴 plan-killers
- The grilling exchange (Phase 1) — extract any explicit "let's add X to the plan" decisions
- `PREMORTEM` (Phase 3) — including any Fork D landmine it weighed in on
- `MODEL_LINT` (Phase 4.0) — fold each flag into 🟡 Polish or 🟣 Known-debt per its severity, EXCEPT `peer-gate-missing` which is 🔴 (the one structured, self-evidenced model-lint flag allowed to block — see §4.0)

Compose the block:

```markdown
## /plan-harden Summary

**Run metadata**: timestamp <ISO8601>, version v1.0.0, args `<original $ARGUMENTS>`
**Phases run**: enrichment <✓ N hits | ⊘ skipped> (memory: <n>, research: <tier>, edge-cases: <n>, blindspot: <n | ⊘ reason>), grill <✓ ~N exchanges (mode=interactive|auto-accept) | ⊘>, adversarial <✓ N hardenings, verify=<approved@rN | converged@rN (M minor) | deadlock@rN (M unresolved) | codex-error | n/a>, log=<path> | ⊘ failed: <reason>>, premortem <✓ | ⊘>, model-lint <✓ N flags | clean | ⊘ (not a plan-builder plan)>
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
3. **Verify the round-trip:** rebuild to a TEMP dir (`build_plan.py spec.json /tmp/<x>`) and diff the regenerated prompt bodies against the live ones — they must match byte-for-byte. Never overwrite the live plan dir during verification.

Report the backport + round-trip result in the completion output. (Originating: 2026-06-21 — hardenings landed only in PLAN.html + prompt files; the user caught that a rebuild would wipe them. See `reference_in_place_revision_no_contradictory_layers.md`.)

### 4.3: Final user-facing output

Print to user:

```
✓ /plan-harden complete

Summary section written to: <PLAN_FILE>
Plan-killers: <n> (recommend_exit_now: <yes|no>)
Total token cost: ~<N>k (coarse self-estimate)

Next steps:
- If recommend_exit_now=yes: review the summary, then ExitPlanMode.
- If recommend_exit_now=no: address the 🔴 items, then re-run /plan-harden
  (idempotent — will replace the summary, not duplicate it).
```

---

## --quick mode

Argument: `/plan-harden --quick`

Behavior: skip Phase 0 + Phase 3. Phase 1 runs WITHOUT the enrichment-context print (just invokes `/grill-with-docs` directly). Phase 2 runs as normal. Phase 4 runs and notes the skipped phases as ⊘.

This is a thin wrapper around the user's existing manual chain (`/grill-with-docs` → `/adversarial-review`), eliminating only the ExitPlanMode-and-reenter friction. Token cost should be ≤ baseline × 1.2.

---

## Failure modes & recovery

Full failure-mode table (Workflow/Agent-fork errors, `/adversarial-review` errors or
long-runs, Ctrl-C mid-run, deleted plan file, structure drift), the `--from-phase N`
resumption preconditions, and the rough token-budget numbers per phase: `Read
~/.claude/commands/references/plan-harden/failure-modes.md`.

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

---

## Changelog

- **2026-07-09** — Phase 4.1: 🔴 plan-killer now requires quoted verbatim evidence (else downgraded to 🟡 with note). Companion edits in `/adversarial-review`: decision primer + relitigation suppression (R29) and fix-landed check (R30) in the Codex verify loop, quote-the-line gate at synthesis, fresh-context per-finding validator rule. Source: everyinc/compound-engineering-plugin gap review — ce-doc-review R29/R30 + ce-code-review quote gate.
- **2026-07-01** — `effort` bumped `medium` → `high` for a deeper Phase-3 premortem + Phase-4 synthesis. Skill/command frontmatter effort is documented-honored and verified in binary 2.1.170 (overrides session effort; `CLAUDE_CODE_EFFORT_LEVEL` env var still wins).
- **Cascade-to-nested-Skills A/B protocol** (`~/.claude/fixtures/plan-harden-corpus/ab-fork-inheritance-protocol.md`) remains **empirically unverified** — the `claude /usage` measurement scaffolding it depends on has been structurally dead since 2026-06-10 (no headless token output), so the A/B cannot complete.
