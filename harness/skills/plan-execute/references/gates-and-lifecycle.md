# Plan Execute — Verify Sub-Loop, Escalation, Checkpoints & Lifecycle

Deep procedures for verify gates, upward escalation, structural DONE-gate, checkpoints (`AWAITS_REVIEW`), `redispatch`, mid-run mutations, REPLAN, clear-halt rules, locking, notifications, and crash recovery.

## Contents

- [Verify sub-loop and review gate rules](#verify-sub-loop-and-review-gate-rules)
- [Upward escalation (ESC-02 / ESC-03)](#upward-escalation-esc-02--esc-03)
- [Structural DONE-gate (PS-01) and eval deliverable (PS-02)](#structural-done-gate-ps-01-and-eval-deliverable-ps-02)
- [Checkpoints and the two flavors of `AWAITS_REVIEW`](#checkpoints-and-the-two-flavors-of-awaits_review)
- [Re-running a finished session (`redispatch`)](#re-running-a-finished-session-redispatch)
- [Changing the plan mid-run & `verify_skill`](#changing-the-plan-mid-run--verify_skill)
- [The REPLAN gate (`plan_impact`)](#the-replan-gate-plan_impact)
- [Halt guards, `clear-halt` rules, and `depends_on_policy`](#halt-guards-clear-halt-rules-and-depends_on_policy)
- [Locking, halt notifications, helper guarantees, and turn-end guard](#locking-halt-notifications-helper-guarantees-and-turn-end-guard)

---

## Verify sub-loop and review gate rules

Full gate contract: `verify-gates.md`. When `apply` reports `verify_pending: true`, the session stays `DOING`. Run BEFORE shipping:

1. Announce the gates (name-first, e.g. `s03 verify gates: pytest-fast, eval-smoke-baseline (on_fail: rework, max 2)`).
2. Run `PYBP verify-begin <dir> --session sNN` → returns directive:
   - `already-verified` → go to `verify-finalize`. `noop` → continue.
   - `run-argv` `{gate}` → `PYBP verify-run <dir> --session sNN --gate <gate>` (background + `Monitor` if timeout > 540 s).
   - `invoke-skill` `{gate, skill, args}` → invoke `Skill`, write final message to temp file, then `PYBP verify-record <dir> --session sNN --gate <gate> --status done|failed [--result-file F]`.
   - `failed` `{reason: state-drift}` → manifest changed under live verify state; halted. Delete `_verify_state/<sid>.json` to re-verify.
   - **Fix rounds run review gates first:** on a rework pass `verify-begin` orders review gates before test gates; run them in the order handed to you.
3. Loop until `passed`, `rework`, `indeterminate`, or `halted`:
   - `passed` → `PYBP verify-finalize <dir> --session sNN` flips `DOING`→`DONE` (or `AWAITS_REVIEW` if closeout also asked for a human checkpoint). If `require_evidence: true` and any declared evidence path is missing/empty, `verify-finalize` itself returns `rework`/`halted` with `gate: "evidence"`.
   - `rework` → session is `PARTIAL`. Re-dispatch via `PYBP begin <dir> --sessions sNN` with `feedback_file` appended under `## Verification feedback`. (`apply` refuses `undispatched-rework`; `verify-begin`/`verify-run` refuse `stale-closeout`.) Fix a finding inline only when narrow and `begin` names no higher model, then run `begin` + `record-receipt --inline-fix '<reason>'` (required, else `inline-fix-unlabelled`).
   - `indeterminate` → gate exited `indeterminate_exit` (timeout or no parseable verdict; nothing was reviewed). Gate stays `pending` and `rework_count` is unchanged. Do NOT re-dispatch the session; fix the cause in `feedback_file`, then `PYBP verify-begin <dir> --session sNN --resume` (`--resume` is enforced — bare `verify-begin` exits 2 with `requires-resume`). Repeats still arm the stuck protocol.
   - `halted` → `on_fail: halt` or rework exhausted; session `BLOCKED`, plan halted. Run one learning-capture pass (`learning-capture.md`), surface `reason` + `feedback_file` verbatim (if stopped mid-ladder, `reason` names reached vs untried rungs), and STOP.

### Skill-kind review gate rules

- **Intent into a review gate (Lane B):** for `code-review-gate` / `llm-review-*`, prepend the session's `prompt.md` `## Work` text wrapped in `===BEGIN UNTRUSTED INTENT … ===END UNTRUSTED INTENT===`, requiring classification `intent_touched`/`ask-user` vs `auto-fix` and `[intent-into-review] intent_block=present source=lane-b-verify …`. Confirm with `grep -c '\[intent-into-review\] intent_block=present'` (count > 0). Intent can only downgrade to `ask-user`, never clear a blocking finding. Full pattern: `~/.claude/references/shared/intent-into-review.md` and `verify-gates.md` § "Intent into the review gate".
- **Never hand a review gate to `/code-review` or `/adversarial-review` BARE:** pin the scope in `<args>` yourself (worktree path, exact file list from `git diff HEAD --stat` + untracked new files, base commit, instruction to LIST captured evidence rather than read it).
- **2-pass close rule:** after a review skill has run TWICE on one session and each blocking finding is closed with (a) reproduction before fix, (b) test that fails on revert, and (c) known-positive control, record `--status done` with a verdict file naming closed findings and residual. Never run a 3rd pass by default — a 3rd pass is a decision card carrying elapsed cost.

## Upward escalation (ESC-02 / ESC-03)

Active on plans stamped `plan_schema_version >= 6`. When the SAME root-cause signature fails twice, the stuck protocol arms and the next rework climbs ONE rung up the SSOT ladder:
- **Claude ladder:** `sonnet@high → opus@high → opus@xhigh`, then stops (SSOT v39 withdrew automatic fable rungs; a fable-authored session still climbs to `fable@xhigh`).
- **Codex ladder (ESC-03):** `gpt-5.6-luna@max → gpt-5.6-terra@max → gpt-6.2-sol@xhigh → gpt-6.2-sol@max`, then stops (never crosses to Claude).
- A **different** error is progress (takes no rung, holds the rung reached). Non-tier `dispatch.subagent_type` (Claude lane) or `model` without `reasoning` never climb (logged on stderr).
- `begin` computes the rung and returns `escalated_from` `{authored, ran, attempt, rung, generation}` plus `model_ran` / `reasoning_ran` / `model_ran_source`. Copy all four into the closeout handed to `apply`.
- **Announcement (exact name-first one-liner):** `"rework attempt <N>: escalating <session title> (sNN) to <model> - <reasoning> per SSOT ladder"`.
- **Clean brief & 5-minute cache rule:** escalated attempts get original prompt + one bounded feedback file (never prior transcript). Same-model reworks also take a fresh agent once >5 minutes have passed since the builder's last turn (prompt cache TTL is 5 min; a resume inside 5 min repeats the worktree path because cwd does not persist).
- **Refusal:** on an observed dispatch error or `CODEX-DISPATCH-FAILED`, run `PYBP record-refusal <dir> --session sNN --model <M> --reasoning <R> --reason "<what you saw>"` (using the dispatched pair — `codex_model`/`codex_effort` on Codex). Falls back to previous rung, costs no rework budget, never re-proposed.

## Structural DONE-gate (PS-01) and eval deliverable (PS-02)

- **PS-01 (`scripts/structural_gate.py`):** `apply` and `verify-finalize` re-read `PLAN.html` from disk and refuse to grant a claimed status unless every touched item/session `data-status` actually changed AND the inline repaint script passes ESLint `no-undef`. Mismatch downgrades to `BLOCKED` + halt. Best-effort secondary visual check (`scripts/render_verify.py`) checks the layout-audit banner when headless Chrome is available. `containment` checks section/category/strip/header/infographic/parallel-group invariants (warnings here, refusal on mutation). Every `PYBP` JSON payload carries `plan_url` (`file://…/PLAN.html`) — announce it at every checkpoint and completion.
- **PS-02 (eval-bearing sessions):** a session whose deliverable is an eval, benchmark, or audit (gate ids like `eval-smoke-*`, or `human_summary`/`deliverable` describing an evaluation, benchmark, or audit) must ship exactly two things: one rendered one-pager (PNG/PDF/Artifact, data inline) and a decision card of at most three options — never narration alone or a bare multi-page HTML dump. If the session's closeout does not obviously carry both, ask for them before treating the session as done.

## Checkpoints and the two flavors of `AWAITS_REVIEW`

| Flavor | How it got there | Clear it with |
|---|---|---|
| **Pre-dispatch** | Manifest `requires_human_checkpoint`; session has **not run**. Asks *should this start?* | `--resume` (with `--answer-file`) |
| **Post-session** | Session closed `DONE`, parked by `human_checkpoint_reason`. Asks *do you accept this result?* | `PYBP ack-checkpoint <dir> --session sNN` |

`plan`/`status` tell them apart from `_closeouts/<sid>.json` (`result: DONE`), default to pre-dispatch when absent, exclude post-session parks from `--resume`, and carry `ack_required: [sNN]` on every action.

### Presenting a pre-dispatch checkpoint

Present as a **decision, not a toll booth**, and STOP:
- Render `checkpoint_brief` (`reason`, `decision`, `options`) with a short paragraph of background from `human_summary`/`deliverable`, plus what `--resume` does after they answer. (If `null` on a legacy manifest, derive background and note it predates briefs; if no real decision exists, recommend rebuilding as a `verify` gate.)
- When the decision asks the owner to read a file, render it, open it in an Orca tab (when `orca status --json` reports `result.app.running: true`), and put its key points in the card — never list "read the file" as the owner's action.
- **Recording the owner's answer:** write any reply (including "proceed") verbatim to `<plan-dir>/_decisions/<sid>.md` with your file-writing tool (never shell echo), then run `PYBP plan <dir> --resume --answer-file <that file>`. Use `--no-answer` only if the owner explicitly declined to answer. Bare `--resume` refuses.

### Approving a post-session checkpoint

```bash
PYBP ack-checkpoint <dir> --session sNN [--note "..."]
```
Flips to `DONE`, logs `checkpoint_approved`, re-reads `PLAN.html`. Idempotent; refuses on `TODO`/`DOING`/`PARTIAL` or pre-dispatch parks. Then run `plan` (without `--resume`).

## Re-running a finished session (`redispatch`)

```bash
PYBP redispatch <dir> --session sNN --reason "what changed" [--allow-stale-dependents]
```
- Resets the session (and exclusively-owned items) to `TODO`, moves `_closeouts`/`_verify_state`/`_shipping_state` records to `<sid>.rN.<ext>`, logs `redispatch`.
- `--reason` is required. Refuses on non-finished sessions (resolve `AWAITS_REVIEW` with `ack-checkpoint` first).
- **Cascade is default:** resets transitive `DONE`/`AWAITS_REVIEW` dependents (`DOING`, `WONTFIX`, `DEFERRED` are untouched and listed in `dependents_untouched`). `--allow-stale-dependents` skips cascade and logs `stale_dependents_accepted`.

## Changing the plan mid-run & `verify_skill`

Never hand-edit `PLAN.html` or `manifest.json`. Use:
```bash
PYBP add-session    <dir> --id s21 --title "…" --depends-on s20 --task-class agentic_build --gates pytest-brain --new-item '{...}'
PYBP amend-session  <dir> --session s21 --depends-on s19 --prompt "…" [--model M --reasoning R]
PYBP retire-session <dir> --session s21 --reason "…" [--cascade | --drop-dependency]
```
Note: `amend-session --model`/`--reasoning` resets that session's escalation ladder (refused rungs cleared, generation bumped). Full rules: `plan-mutation.md`.

**`verify_skill` closeout field:** when the repo has `.claude/skills/verify/SKILL.md`, sessions touching code run it and add `verify_skill: {"result": "pass|fail|not_run", "reason": "…", "evidence": "…"}`. Shape-checked only (`reason` non-empty for `not_run`/`fail`); missing never refuses and `fail` does not block.

## The REPLAN gate (`plan_impact`)

On `plan_schema_version >= 3`, a closeout carrying `plan_impact: {"invalidates": ["s07","s08"], "reason": "…"}`:
1. Validates ids against the manifest (unknown id or self-reference → `BLOCKED` + halt schema error).
2. Keeps the reporting session's result, halts the plan with `kind: "replan"`, and adds notes to affected cards.
3. `plan` returns `action: "replan"` with a brief (3 options: `amend` / `retire` / `proceed` + empty `recommendation` slot). Present verbatim with your recommendation filled in, then STOP.
4. Apply the pick via `amend-session`/`retire-session`/`redispatch` (allowed through a `replan` halt), then record:
   ```bash
   PYBP resolve-replan <dir> --session sNN --decision amend|retire|proceed --reason "…"
   ```
   `amend`/`retire` are refused until `_changelog.ndjson` shows a mutation touching the invalidated sessions.
5. **Precedence:** `human_checkpoint_reason` parks first (`ack-checkpoint` raises REPLAN next); `verify` runs first (`verify-finalize` raises REPLAN, reported as `replan_deferred`). Plans below v3 ignore `plan_impact`.

## Halt guards, `clear-halt` rules, and `depends_on_policy`

- **Halt guard:** `begin`, `ack-checkpoint`, `redispatch`, and the 3 mutation commands refuse while halted (exception: mutations and `redispatch` pass through `halt.kind == "replan"`).
- **`clear-halt` rules:**
  - **1st clear:** `PYBP clear-halt <dir>` (or `--clear-halt`).
  - **2nd clear of the same session's halt:** REFUSED without `--cost-report '<elapsed, passes, what changed, the cut>'`. Post that line to the owner first.
  - **3rd clear of a review halt:** takes ONLY `--exit accept|split|escalate` (`verify-gates.md` § "The same stop rules hold for every session review gate").
  - **Owner words for a halted session:** write reply verbatim to `<plan-dir>/_decisions/<sid>.halt-<n>.md` (n = next clear number) and pass `--answer-file <that file>` (add `--decision` when stuck). Refuses with no halt, plan-wide/replan halt, or DONE/WONTFIX session; never replaces `--decision` or `--exit`.
- **`depends_on_policy`:** `"all"` (default, hard AND: every dep `DONE`/`WONTFIX`/`DEFERRED`) vs `"completed_or_terminal"` (dispatches over completed subset once every dep is terminal, `BLOCKED` included — still surfaces `action: blocked` first so dispatch waits for `--resume`).

## Locking, halt notifications, helper guarantees, and turn-end guard

- **Filesystem locking (P5):** `.lock` pidfile requires a local POSIX filesystem. `begin` detects cloud-sync/networked mounts (iCloud, Google Drive, OneDrive, Dropbox, NFS/SMB) and refuses. Move to local disk, or override at your own risk with `--unsafe-lock` (logs `lock_fs_warning`).
- **Halt notification (P8, opt-in):** if manifest has `notify_on_halt.command`, halt runs it once synchronously (`shell=False`, ~10 s timeout, env `PLAN_DIR`/`PLAN_TITLE`/`HALT_SESSION`/`HALT_REASON`); failures log `notify_failed` and never block halt.
- **Helper guarantees:** atomic temp+rename writes bounded by `<!-- ARTICLE:<id>:BEGIN -->`/`:END` anchors; write-ahead `_closeouts/<sid>.json` before HTML edits; H2 semantic verification (scope, double-counting, coverage); P4 closeout extraction (last unfenced block, trailing content, matching session id).
- **Turn-end guard (Stop hook):** `hooks/turnend-guard.py` prevents ending a turn while a plan dispatched by this session has a `DOING` session with no closeout and no running agent/workflow/background command (blocks at most 3 consecutive times per finding).
