# Plan Execute — Land, Finish, Acceptance Review & Isolation

Deep procedures for closing a plan: `land`, `land-repair`, `finish`, closing `acceptance_review: true` reporting, worktree-isolated parallel groups, and plan-level git isolation (including the deploy seam).

## Contents

- [Closing acceptance review (`complete`)](#closing-acceptance-review-complete)
- [The `land` protocol](#the-land-protocol)
- [`land-repair` and non-repairable parks](#land-repair-and-non-repairable-parks)
- [Long gates (>540 s timeout)](#long-gates-540-s-timeout)
- [The `finish` protocol](#the-finish-protocol)
- [Worktree-isolated parallel groups](#worktree-isolated-parallel-groups)
- [Plan-level git isolation and the deploy seam](#example-isolation-plan-and-the-deploy-seam)

---

## Closing acceptance review (`complete`)

A `complete` carrying `scope: session` answers only for the one named session while others are still open; once every session is terminal, `plan --session sNN` returns the whole-plan `land`/`finish`/`complete` action.

When any session's manifest entry carries `acceptance_review: true`:
1. Read its `_closeouts/<sid>.json` plus the evidence artifact it names, and lead your report with ITS verdict rather than a bare "plan complete".
2. Report each criterion's verdict (`ACHIEVED`, `GAP`, or `NEVER CHECKED`, with the evidence cited) and the path to its one-page report.
3. When the plan has a `spec_ref`, check the write-back itself: `SPEC.md` `## Results` must hold at least one line for each covered requirement id (the section only grows; the newest line decides, contract §8). If any line is missing, report it and do **not** report the plan as validated.
4. If the acceptance-review session ended `DEFERRED`/`WONTFIX`/`BLOCKED`, report the plan as complete but **not validated**.
5. When no session carries the marker, report completion as before and note that no plan-level acceptance review was authored (`../../plan-builder/references/schemas.md` § "Closing acceptance review").

## The `land` protocol

Every session is terminal and the plan runs on its own branch (schema v7+ or `begin --isolate`). Authority: `plan-isolation-contract.md` §4/§5 (carry-over/reservation/push-on-ack/lease renewal: §17; two-phase land: §18).

**Forbidden git commands:** nothing in this loop may run `git branch -f`, `git fetch --update-head-ok`, or `git update-ref refs/heads/<default>` (they corrupt the operator's checkout).

Run `PYBP land <dir>`. It merges the default branch into the plan branch, merges the plan into a detached land worktree, commits the plan record, and re-runs the plan's union of verify gates on the exact tree about to be pushed.

### Outcomes

- **`land-awaits-review`** — the last human gate before pushing to the default branch. Present `brief` verbatim (also in `LAND_NOTICE.txt`). The operator approves with:
  ```bash
  PYBP land-ack <dir> --note '<why>'
  ```
  `land-ack` checks the approved result still holds and pushes it in the same call:
  - `landed` — pushed and cleaned up (tears down plan worktree + local/remote branch). Run `PYBP plan <dir>` next → returns `finish`.
  - `land-awaits-review` with `moved` — anything other than runtime output (harvests) moved on main; ack is kept on record.
  - `land-locked` — another plan holds the lease; ack stays recorded; re-run `land`.
  - `land-running` — another process of this plan is running land; nothing recorded; re-run once it ends.
- **`land-reserved` and `land-locked`** — **NOT a STOP** (even under `--auto`). Another plan holds the repo lease or waits on its owner (`land-reserved` carries `reserved_by`, `reason`, `expires_at`, `retry_after_seconds`). Wait with `Monitor` (an `until` loop printing a line each pass, never `sleep N` then a check) until `expires_at` or the reservation file is gone, then re-run `land`, at most 3 times; after the 3rd, surface the brief and STOP.
- **`invoke-skill`** (skill-kind re-gate) — run that skill, judge its output, and record with `PYBP land-record <dir> --gate <id> --status passed|failed`, which continues the land in the same call (re-run `land` only if the answer says so).
  - Before invoking a review skill, write its brief by the **land review stop rules** (`verify-gates.md` § "Land review stop rules"): agreed `Blocking rule:` line, delta-only scope after a passed round, simplify-or-patch decision card after 2 failed rounds on one mechanism.
  - If `stale_cwd_pids` is non-empty (processes holding a deleted land worktree — Codex fails with "failed to load configuration"), stop only the ones your own earlier review started, never another session's, then invoke.
- **`kind: gate-on-box-verification`** — policy refusal (Codex-built plan, no Claude-verifier opt-in). Surface the brief and STOP; only a human answers with `PYBP land-verify <dir> --decision verified-on-box|blocked --note '<why>'` (continues the land in the same call). Never re-run `land` hoping it clears.
- **`kind: land-environment`** — comes before any gate in a repo with `requirements-ci.txt`: `python3` on PATH is not a venv with pytest. Run the two commands its brief names.
- **Any other `land-parked`** (exit 1) — conflict, red/undecided gate, rejected push. Surface the brief and STOP; nothing was pushed or cleaned up.

## `land-repair` and non-repairable parks

Before issuing `land-repair`, `land` re-runs each red gate once, whole, on the same tree; a gate that passes on re-run is flaky and gets no repair.

**`land-repair`** (exit 1): every red gate is an `at_land` argv gate and passes on the base — this plan broke it. At most 3 rounds (`finish-contract.md`, "The `land-repair` directive"). Do 4 steps in order:
1. Run `PYBP add-session <dir>` from `add_session` (`--id`, `--title`, `--task-class`, `--gates` comma-joined, `--prompt`, optional `--infographic-group`, and each `new_items` entry as `--new-item '<json>'`) — OR if the directive carries `amend_session` (replaced an unstarted round), run `PYBP amend-session <dir> --session <sid> --prompt <prompt>` instead. If this fails, surface the error and STOP.
2. Fast-forward the plan branch by running `start.command`. If this fails, STOP before dispatch and surface `start.command` (`plan`/`begin` refuse with `blocked`, `reason: repair-start-pending` until the plan tree holds the candidate; if the refusal says the candidate no longer exists, run `PYBP land <dir>` for a new directive; if it says `REPAIR NOT NEEDED`, run the `retire-session` command it names).
3. Dispatch the new session like any other (`plan`, then `begin`).
4. Once terminal, re-run `PYBP land <dir>`.

### Non-repairable parks (surface brief and STOP)

- `gate-flaky` — red gate passed on re-run, or a held check is unresolved (fix/quarantine the named test if in a plan-changed file, or re-run `land`; a gate naming no test/file holds until the plan changes anything, or for a review gate until `_verify_state/land*.accepted.json` changes).
- `gate-inherited` — red on the base with the same failures.
- `gate-base-differs` — red on the base with different failures (compare both excerpts in the brief, fix the right side, re-run `land`).
- `repair-gate-unresolvable` — pull the registry into the root named in the brief, then re-run `land`.
- `repair-session-invalid`, `repair-exhausted` (3 rounds ran), `repair-same-failures`.
- `repair-not-needed` — green, but repair session is still `TODO` (run the `retire-session` command in the brief, then `land`).
- `repair-in-progress` — green, but repair session is still open (wait for it, then `land`).

## Long gates (>540 s timeout)

Whenever any resolved gate's `timeout` exceeds 540 s — or for `PYBP finish <dir>` (its CI wait can run 540 s) — run `verify-run`, `land`, `land-ack`, `land-record`, `land-verify`, or `finish` with `run_in_background: true` and wait on it with `Monitor` until it exits. Never run it in the foreground under the Bash tool's 600 s cap.

## The `finish` protocol

Authority: `finish-contract.md`. Every plan ends here before `complete`.

Run `PYBP finish <dir>` with `run_in_background: true` and wait with `Monitor` (add `--no-wait` to check CI once instead of waiting up to 540 s). `finish` saves `steps_done_at` once its git steps (record, leftovers, checkout) finish; a later `finish` with `steps_done_at` and no `finished_at` skips them and goes straight to CI.

- **`finish-parked`** (exit 1) — show `brief` verbatim and STOP:
  - Preflight, record push, `record-special-file` (remove the FIFO/socket/device file from the plan folder), or busy lease before git steps: nothing past the park was done.
  - `ci-save-busy` (top-level `park_reason`): git steps ran, only saving the CI result hit a busy lease. Once the named lease holder finishes, run `PYBP finish <dir> --no-wait`.
  - `finish-running` (top-level `park_reason`): another finish run of this plan holds its lock (read/wrote/pushed nothing). Wait for it to end, then run `PYBP finish <dir> --no-wait`.
- **`finish-superseded`** (exit 0) — another finish run or CI poll wrote state first; do NOT report this run's CI verdict. Follow the note's three cases:
  - Has `finished_at`: run `PYBP finish <dir> --no-wait` once to read the saved result.
  - Has `steps_done_at` and no `finished_at`: another run is polling CI; wait for it, or if dead re-run `PYBP finish <dir>`.
  - Has neither (plan was reopened): run `PYBP plan <dir>` and follow it.
- **`finished`** — report in plain words: record (committed/pushed), leftovers (unpushed commits, plan worktree/branch), checkout (fast-forwarded, or blocked + command), and CI verdict (`pending` + `PYBP finish <dir> --no-wait`, `unknown` + reason, `descendant`, `inherited`). If `finish_mode` is `report-only`, say nothing was pushed and give `PYBP finish <dir> --apply`. Then run `PYBP plan <dir>` → returns `complete`.

## Worktree-isolated parallel groups

Authority: `parallel-group-contract.md` (v2, amended; wins on disagreement). This is members of ONE group sharing ONE plan branch (distinct from plan-level isolation below).

- **`begin <members>`** pins ONE base ref for the group, creates one worktree per member at `<repo>/.plan-worktrees/<group>/<sid>` on branch `plan/<group>/<sid>` (serially staggered, explicit start-point, `git merge-base` verified).
- **Members never commit;** `apply` commits each member's worktree onto its own branch serially as each closeout is accepted (avoids concurrent `.git/index.lock` races).
- **Per-member verify gates** run inside that member's worktree (`verify.gate_cwd`).
- **NEVER pass the Agent/Task tool's own `isolation: "worktree"` parameter** — barred by name (S02 measured it silently destroying agent output).
- **`begin <integration-session>`** checks all members are `DONE` and checks containment (did a member write to the shared tree?), then merges every member branch producer-first in manifest order. On conflict: aborts merge, marks integration `BLOCKED`, halts plan, prints conflicting files + branches + `git reset --hard <base>`. Never resolve such a merge yourself.
- Integration gates are a superset of the union of member gates and run on the merged tree.
- **Cleanup is explicit and last:** `PYBP worktree-cleanup <dir> --group <g>` after integration passes and commits (preserves any dirty/untracked/unmerged worktree). Read-only view: `PYBP worktree-status <dir> --group <g>`.
- **Containment is advisory** (`EnterWorktree(path=…)` is refused from subagents); integration detects violations. Both Claude and Codex harnesses honour isolation. Executable proof: `bash fixtures/worktree-parallel/run.sh`.

## Plan-level git isolation and the deploy seam

Authority: `plan-isolation-contract.md` and `plan-lifecycle.md` (`plans-status`, `plans-sweep`, `retire-plan`).

- **v7+ activation:** plans stamped `plan_schema_version >= 7` (`plan_version_gate.ISOLATION_MIN_SCHEMA`) automatically get their own branch + locked worktree at `begin` and reach the default branch via `land`. Override per run with `begin --isolate` or `begin --no-isolate` (`--no-isolate` wins; logged as `plan_isolation_gate`).
- **Concurrent isolated plans:** two isolated plans may run in one repo with no `--concurrent` flag (they share no working tree); REG-02 still fires when any active neighbour is non-isolated.
- **The deploy seam:** `/plan-execute` runs from the DEPLOYED tree (`~/.claude/skills/plan-execute/scripts/run.py`), while a plan editing `skills/plan-execute/**` writes to the SOURCE repo. **Never run `gearbox deploy` between the first and last session of a plan that edits `skills/plan-execute/**`** — swapping the deployed tree mid-plan breaks resume invariants for every subsequent session.
