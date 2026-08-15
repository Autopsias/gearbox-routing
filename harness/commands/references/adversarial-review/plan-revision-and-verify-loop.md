# Phase 3 — Automatic Plan Revision + Codex Verify Loop

## Contents
- [PHASE 3: Automatic Plan Revision](#phase-3-automatic-plan-revision)
- [3a: Deep Analysis](#3a-deep-analysis-ultrathink-mandatory)
- [3b: Produce the Revised Plan](#3b-produce-the-revised-plan)
- [3c: Apply the Revision](#3c-apply-the-revision)
- [3d: Codex Verify Loop](#3d-codex-verify-loop-default----runs-automatically-no-flag)
- [3e: Completion Output](#3e-completion-output)

Read this ONLY when Phase 1 detected a plan (`PLAN_DETECTED = true`). Code-diff-only
reviews (no plan in context) never need this file — Phase 2d's output is the end of the
command for them. This is the full content of Phase 3, moved verbatim out of the main
command body; the gate check and entry point stay in `adversarial-review.md`.

---

## PHASE 3: Automatic Plan Revision

**Gate**: This phase runs ONLY if ALL conditions are met:
- `PLAN_DETECTED = true`
- Review ran in foreground (not `--background`)
- Review produced at least one finding

If the gate is not met, STOP. Do not mention Phase 3. Do not ask about plan revision.

**If the gate IS met, proceed AUTOMATICALLY. Do not ask the user for permission. The automatic flow is the entire point of this command.**

### 3a: Deep Analysis (UltraThink MANDATORY)

Use extended thinking to perform deep synthesis. This is where Claude (Opus) integrates BOTH models' findings into plan improvements:

1. **Map every finding to plan impact.** For each finding ask:
   - Does it invalidate a plan step? -> Mark for revision
   - Does it require a NEW step? -> Mark for insertion
   - Does it change step ordering or dependencies? -> Mark for resequencing
   - Does it reveal an unstated assumption? -> Mark for explicit assumption documentation
   - Is it about implementation details the plan doesn't cover? -> Mark as informational annotation

2. **Prioritize consensus findings.** Findings flagged by BOTH models get priority treatment in revision decisions.

3. **Classify the overall revision type:**
   - **STRUCTURAL** -- Findings change the plan's approach, architecture, or fundamental strategy. Major revision required.
   - **ADDITIVE** -- Findings require new steps, guards, or validations. Surgical insertions without restructuring.
   - **CORRECTIVE** -- Findings fix incorrect assumptions, missing edge cases, or wrong ordering. Targeted fixes.
   - **INFORMATIONAL** -- Findings are worth noting but don't change the plan. Annotate only.

4. **Trace cascading effects.** A single finding may ripple through multiple steps. Follow the full chain. Example: a missing retry mechanism affects not just the API call step but also the error handling step, the rollback step, and the testing step.

5. **Determine what NOT to change.** Not every finding warrants a plan change. If a finding is about code-level detail that the plan intentionally abstracts, note it as an implementation concern but don't restructure the plan.

### 3b: Produce the Revised Plan

Generate the hardened plan:

1. **Preserve the original plan's format, structure, and style.** Same heading levels, same section names, same conventions. The revision should feel like the same document, improved.

2. **Mark every change with `[HARDENED]` and source attribution** so the user can see what was added/modified and which model(s) drove the change:
   ```
   ### Step 3: Deploy to staging [HARDENED:consensus]
   Added rollback verification after deployment (both models flagged missing rollback safety)
   ```
   ```
   ### Step 5: Error handling [HARDENED:codex]
   Added idempotency guard for retry scenarios (Codex finding: missing idempotency)
   ```
   ```
   ### Step 7: Monitoring [HARDENED:claude]
   Added alert threshold for degraded dependency behavior (Claude finding: operational readiness gap)
   ```

3. **For each `[HARDENED]` change, include a one-line justification** referencing the specific finding that motivated it. Keep it terse.

4. **Add a "Review Integration Summary" block** at the very top of the revised plan:
   ```
   > **Review Integration Summary**
   > - Source: Dual adversarial review (Codex + Claude Opus) | <date>
   > - Consensus findings: N (highest priority)
   > - Codex-unique findings integrated: N of M
   > - Claude-unique findings integrated: N of M
   > - Revision type: STRUCTURAL | ADDITIVE | CORRECTIVE | INFORMATIONAL
   > - Key changes: [2-3 sentence summary of what was strengthened]
   ```

   When Codex was unavailable, use the single-model variant:
   ```
   > **Review Integration Summary**
   > - Source: Claude adversarial review (Opus) | <date>
   > - Findings integrated: N of M
   > - Revision type: STRUCTURAL | ADDITIVE | CORRECTIVE | INFORMATIONAL
   > - Key changes: [2-3 sentence summary]
   ```

5. **Do NOT remove content from the original plan** unless a finding explicitly invalidates it. Add, amend, reorder -- but don't delete without cause.

### 3c: Apply the Revision

**If the plan is in a file** (plan mode file or any `.md` on disk):
- Use the Edit tool to apply changes directly to the file
- After editing, briefly summarize the diff for the user

**If the plan is only in conversation context:**
- Output the full revised plan as formatted markdown
- Clearly delineate it: `---` before and after

### 3d: Codex Verify Loop (DEFAULT -- runs automatically, no flag)

**Gate** -- runs ONLY when ALL of these hold:
- `PLAN_DETECTED = true`
- Foreground default path (NOT `--background`, NOT `--synthesize` -- those defer synthesis or lack a resumable Codex thread)
- Codex produced a usable artifact in Phase 2 (skip if synthesis fell back to Claude-only)
- Phase 3c applied at least one `[HARDENED:...]` change

If the gate fails, skip silently to 3e.

**Why this exists**: a single-pass review is an echo trap -- Codex flagged issues, Claude integrated them, but Codex never confirmed the fixes actually land. This loop closes it: the SAME Codex thread re-reads the hardened plan and verifies its own prior findings.

**Read-only invariant**: every call here (`task --resume-last`, or the `task --fresh` resume-blocked fallback in step 2) is run WITHOUT `--write`. The companion forces `sandbox: "read-only"` whenever `--write` is absent (verified in `codex-companion.mjs`), so verify rounds cannot mutate the repo. NEVER add `--write` to a verify-loop call.

**Fresh-context validator rule**: a finding is verified resolved only by an independent reviewer (the Codex thread, or a fresh fork) addressing it **per-finding** — each finding gets its own RESOLVED/UNRESOLVED verdict with a reason; a blanket "all resolved" verdict is not a verification (a batched pass pattern-matches across findings). The orchestrator may NEVER self-certify a finding resolved: it synthesized the findings and is not an independent second opinion. (Source: ce-code-review per-finding validator rule.)

**Constants**: `MAX_VERIFY_ROUNDS = 3`, `round = 1`.

**Convergence gate (round 3 is earned, not automatic).** Rounds 1 and 2 always run when the verdict keeps coming back REVISE — one verify pass is not enough to trust that findings have settled. But a third round runs ONLY if round 2 is still surfacing *strong* findings (a new or still-unresolved `[HIGH]`/`[CRITICAL]` issue that is materially distinct from a round-1 finding). If round 2's remaining findings are all `[MEDIUM]`/`[LOW]` or are restatements / minor refinements of already-known items, the loop has converged: stop at round 2 and record the minor items as accepted known-debt — do NOT burn a third xhigh round for diminishing returns. This is the branch logic in step 6; the severity tags from the verify prompt drive it. A finding suppressed as relitigation (>50% evidence overlap with a primer REJECTED entry — see step 1) NEVER counts as a strong new finding for earning round 3, whatever its severity tag. Saving a round is the gate's side effect, never its goal — when unsure whether a finding is strong, run round 3.

**Review log**: append round-by-round to a sibling of the plan -- `${PLAN_FILE%.md}-REVIEW-LOG.md` (if the plan is conversation-only, use `/tmp/adversarial-review-{REVIEW_TIMESTAMP}-REVIEW-LOG.md`). Seed it with a header and the Phase 2d verdict as "Round 0".

**Loop**:

1. Write the verify prompt to `/tmp/adversarial-review-verify-{REVIEW_TIMESTAMP}.md`. **For round ≥ 2, prepend a DECISION PRIMER** (anti-relitigation, adapted from ce-doc-review R29) built from the review log — a compact block, ≤1KB:
   ```
   DECISION PRIMER (prior rounds — do not relitigate):
   APPLIED: <finding title> -> fixed at <plan section / [HARDENED:...] tag location>  (one line each)
   REJECTED: <finding title> -> <rejection reason>; evidence was: "<truncated snippet, ≤120 chars>"  (one line each)
   A new finding whose evidence substantially overlaps (>50%) a REJECTED entry above
   is relitigation: do not re-raise it as new; reference the entry instead.
   ```
   Then the verify prompt body:
   ```
   The plan below was REVISED to address the findings from your previous review.
   For EACH finding you raised, say RESOLVED or UNRESOLVED (with why). Then raise any
   NEW issue the revision introduced. Be adversarial; do not rubber-stamp.
   Tag EVERY still-open (UNRESOLVED) and NEW finding with a severity in brackets:
   [CRITICAL] | [HIGH] | [MEDIUM] | [LOW]. (The caller uses these tags to decide
   whether another verify round is warranted, so tag honestly — don't inflate or deflate.)
   End with EXACTLY one line:
     VERDICT: APPROVED   -- every prior finding resolved AND no new HIGH/CRITICAL issue
     VERDICT: REVISE     -- anything still unresolved, or a new material issue
   --- REVISED PLAN ---
   <full current PLAN_FILE content>
   --- END ---
   ```
2. Re-review the hardened plan with Codex (read-only). **Run it through the SUPERVISED runner, not a bare companion call** -- verify rounds are the longest Codex calls this command makes and are where the transport stall bites:
   ```bash
   python3 "$HOME/.claude/scripts/codex_supervised.py" \
     --prompt-file /tmp/adversarial-review-verify-{REVIEW_TIMESTAMP}.md \
     --out /tmp/adversarial-review-verify-{REVIEW_TIMESTAMP}-r{round}.md \
     --effort xhigh --idle-timeout 600 --max-attempts 3 --total-deadline 5400
   ```
   The verdict is the `--out` file (codex's last message, already free of `[codex]` progress lines); the
   JSONL event stream lands beside it at `<out>.jsonl`. The runner prints one JSON status object:
   `status: "completed"` means a usable verdict exists, `"failed"` means every attempt stalled or the
   deadline hit -- and `"failed"` is NOT a `REVISE`, it is a degraded round (see the honesty rule in step 3).

   **Why supervised, and why you must not "fix" a stall by shrinking the request** (measured 2026-07-26,
   plan-harden on profile-a-brain): two `xhigh` verify runs each did ~9-10 min of real work and then went
   silent; `codex-companion status` still said `running` while the job logs had not grown in 28 and 20
   minutes. Upstream openai/codex #31376 is the same signature (dead pooled connection in `CLOSE_WAIT`,
   `stream_idle_timeout_ms` never fires). The instinct to cut the prompt or drop to a lower effort tier
   degrades the review to dodge a transport bug AND does not work -- the smallest prompt tried (5 KB)
   hung too. The runner instead makes a stall recoverable: it kills on idle and RESUMES the same session,
   so a stall costs the idle window, not the work. Keep `xhigh` and keep the full plan inlined.

   **Prefer resuming the Phase-2 thread** so the SAME reviewer checks its own findings: the runner's
   attempt 1 is a fresh `codex exec`, so to resume Phase 2 explicitly, pass the verify prompt through
   the companion's `--resume-last` ONCE and fall back to the supervised runner (which inlines the full
   revised plan, so a fresh thread still reviews correctly) the moment it stalls or is resume-blocked.

   **Resume-blocked fallback (REQUIRED -- a blocked resume is NOT a verdict).** The companion refuses `--resume-last` while any earlier Codex task is stuck in `running`/`queued`, emitting a line like `Task <id> is still running. Use /codex:status before continuing it.` -- output that contains no `VERDICT:`. If the raw output matches `still running|/codex:status|No resumable task`, the resume did NOT run: there is no second opinion yet. Do **not** fall through to step 3 and score it `REVISE` -- that silently burns a round and is exactly the bug this fallback fixes. Instead re-review with a FRESH thread for this round:
   ```bash
   node "$(ls "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs | sort -V | tail -1)" task --fresh --prompt-file /tmp/adversarial-review-verify-{REVIEW_TIMESTAMP}.md --effort xhigh > /tmp/adversarial-review-verify-{REVIEW_TIMESTAMP}-r{round}.raw.md 2>&1
   ```
   A fresh thread cannot verify "its own" prior findings, but a genuine adversarial re-read beats a dead resume, and the verify prompt inlines the full revised plan so a fresh thread reviews correctly. Set `VERIFY_MODE = fresh-thread` and record `verifier=fresh-thread (resume blocked)` in this round's review-log entry. For later rounds keep using `--resume-last` (it now resumes the fresh thread); if it blocks again, use `--fresh` again. Only once a genuine review response comes back (resume or fresh) do you go to step 3.
3. Parse the verdict (`grep -E '^VERDICT:'`) from the genuine review response produced in step 2. If that response has no parseable `VERDICT:` line -> treat as `REVISE` (fail-closed: never fabricate an approval). A resume-guard/CLI error is handled in step 2's fallback and is never itself a `REVISE` verdict.
4. **Fix-landed check (R30) before closing the round:** for each finding Codex marked RESOLVED that you integrated in a prior round, grep `PLAN_FILE` for its `[HARDENED:...]` tag (or the edited text) and confirm the edit is actually present on disk. A claimed-applied fix that is NOT in the file is re-opened as UNRESOLVED for the next round — never logged as resolved on the reviewer's say-so alone.
5. Append a round entry to the review log: round number, verdict, Codex's RESOLVED/UNRESOLVED notes (verbatim, trimmed), the findings you then acted on, and — for round ≥ 2 — any findings suppressed as relitigation (title + which REJECTED primer entry they overlapped).
6. Branch on the verdict:
   - **APPROVED** -> set `LOOP_OUTCOME = "approved@r{round}"`. Break.
   - **REVISE** and `round == 1` -> integrate the still-open findings exactly as in 3a-3c (extend, don't restructure; tag new edits `[HARDENED:codex-verify-r{round}]`). `round += 1` (=2). Repeat from step 1. (Round 2 always runs — a single verify pass can't establish that findings have settled.)
   - **REVISE** and `round == 2` -> **CONVERGENCE GATE** (per the note above). Read the `[CRITICAL]/[HIGH]/[MEDIUM]/[LOW]` tags Codex put on this round's still-open + NEW findings:
       - **Strong** — ≥1 open finding is `[HIGH]` or `[CRITICAL]` AND is materially distinct from a round-1 finding (a genuinely new or still-unfixed substantive issue, not a restatement or minor refinement). → round 3 is warranted: integrate as above, `round += 1` (=3), repeat from step 1.
       - **Converged** — every still-open finding is `[MEDIUM]`/`[LOW]`, OR is a restatement / minor refinement of an already-logged finding ("mostly the same, low-priority, or small details"). → do NOT run round 3. Apply any trivial minor fixes inline (no Codex round); record the rest in the review log under "✓ CONVERGED — minor open items accepted (round 3 skipped; NOT a deadlock)". Set `LOOP_OUTCOME = "converged@r2 ({M} minor open)"`. Break.
       - **Unsure** whether an open finding is strong → treat it as Strong and run round 3. Fail toward rigor, never toward saving a round.
   - **REVISE** and `round == 3` (== `MAX_VERIFY_ROUNDS`) -> **DEADLOCK**. Do NOT loop further and do NOT mark the plan approved. Set `LOOP_OUTCOME = "deadlock@r{round}"`. Record the unresolved findings in the review log under "⚠️ UNRESOLVED AT DEADLOCK". A flagged deadlock beats a fake approval. Break.

**Codex error mid-loop** (a resume call exits non-zero or returns empty): stop the loop, set `LOOP_OUTCOME = "codex-error@r{round}"`, keep the hardening already applied, surface it in 3e. Do not fail the whole command.

### 3e: Completion Output

After the verify loop settles, output:

```
---
### PLAN HARDENED

**Source**: Dual adversarial review (Codex + Claude Opus) -> automatic plan revision -> Codex verify loop
**Consensus findings**: N
**Codex-unique findings integrated**: N of M
**Claude-unique findings integrated**: N of M
**Revision type**: STRUCTURAL | ADDITIVE | CORRECTIVE | INFORMATIONAL
**Verify loop**: <approved@rN | converged@rN (M minor) | deadlock@rN (M unresolved) | codex-error@rN | n/a (Codex unavailable)>
**Review log**: <path | n/a>
**Key changes**:
- [change 1]
- [change 2]
- [change 3]

<closing line, pick by LOOP_OUTCOME>
- approved:      The plan was hardened and Codex confirmed the fixes landed. Look for `[HARDENED:...]` tags for each change, its source, and justification.
- converged:     The plan was hardened; after 2 rounds Codex's findings converged to only minor `[MEDIUM]`/`[LOW]` items (listed in the review log, accepted as known-debt). Round 3 was correctly skipped — this is NOT a deadlock; the plan is ship-ready modulo the listed minor items.
- deadlock:      ⚠️ Codex did NOT converge -- <M> findings remain UNRESOLVED after <N> rounds (see review log). The plan is NOT clean; address the unresolved items before shipping.
- codex-error / n/a:  The plan was hardened from the review findings (verify loop did not complete: <reason>). Look for `[HARDENED:...]` tags.
---
```
