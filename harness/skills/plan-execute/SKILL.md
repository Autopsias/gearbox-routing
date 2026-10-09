---
name: plan-execute
description: The plan already exists — now run it. Use to execute, run, or resume a multi-session plan built by plan-builder (a `_plans/<slug>-<date>/` folder with a PLAN.html dashboard), dispatching each ready session to a subagent until a human-review checkpoint, a blocker, or completion. Trigger on any intent to carry out a finished plan — "run/execute the plan", "kick off the plan in <dir>", "run plan-execute on …", "go work through it automatically", "dispatch/execute the next session", or "continue/resume it" after an AWAITS_REVIEW checkpoint or blocker. Do NOT use to create, build, design, or draft a plan/roadmap/dashboard (that's plan-builder), nor for unrelated run/execute jobs (data ingestion, a morning briefing, resuming a chat).
allowed-tools: [Skill, Task, Workflow, Read, Write, Bash, TaskCreate, TaskUpdate, TaskList, Glob, Grep]
effort: medium  # orchestration is mechanical (the deterministic helper + dispatched subagents do the heavy work; per-session depth comes from the manifest's reasoning directives). Deliberately lower than the session default — delete to inherit. Verified honored in binary 2.1.170.
---

# Plan Execute

Orchestrates execution of a plan built by `/plan-builder`. **You (the main
conversation) are the orchestrator.** You own every write to `PLAN.html`.
Subagents you dispatch do project work and return a structured closeout — they
never touch plan files.

The deterministic plumbing lives in `scripts/run.py` (a CLI facade). You call
its subcommands between Task dispatches; it parses closeouts, verifies them,
persists a write-ahead record, and rewrites `PLAN.html` atomically.

## Invocation forms

- `/plan-execute <plan-dir>` — run the loop from the next ready session until a checkpoint/blocker/done.
- `/plan-execute <plan-dir> --auto` — autonomous mode: self-drive to completion, halting ONLY on genuine human checkpoints, hard blockers, and stale-deploy confirms. See "Autonomous mode" below. Human gates stay sacrosanct.
- `/plan-execute <plan-dir> --session sNN` — dispatch exactly one named session.
- `/plan-execute <plan-dir> --resume` — continue past a **pre-dispatch** `AWAITS_REVIEW` gate (dispatching that session for the first time), or a blocked/halted state the human has cleared. It does NOT ack a session that already finished — that is `ack-checkpoint`.
- `PYBP ack-checkpoint <plan-dir> --session sNN` — approve a **post-session** checkpoint: a session that closed `DONE` and parked for review. Flips it to `DONE`, logs the approval, idempotent.
- `PYBP redispatch <plan-dir> --session sNN --reason "..."` — deliberately re-run a finished session because new facts invalidated its output. Cascades to stale dependents by default.
- `PYBP resolve-replan <plan-dir> --session sNN --decision amend|retire|proceed --reason "..."` — answer a **REPLAN** park raised by a closeout's `plan_impact`. `amend`/`retire` are refused until the change log shows the amendment actually landed. See "The REPLAN gate".
- `PYBP add-session <plan-dir> --id sNN --title "..." [...]` — add a session (and any new items) to a plan that is already running. Writes every surface in one transaction. See "Changing the plan mid-run".
- `PYBP amend-session <plan-dir> --session sNN [--depends-on … --prompt … --model … --reasoning …]` — change what a **TODO** session will be dispatched with. `--model`/`--reasoning` also RESET that session's escalation ladder (refused rungs cleared, generation bumped): the climb is measured from the authored cell, so moving it starts a new cohort.
- `PYBP record-refusal <plan-dir> --session sNN --model <M> --reasoning <R> --reason "..."` — report an OBSERVED refusal of one escalated rung (a real dispatch error, or a `CODEX-DISPATCH-FAILED` wrapper signal). The session falls back to the PREVIOUS rung — never below the authored one — the refusal costs no rework budget, and that rung is never proposed again this session. Nothing infers a refusal; this command is the only way one is recorded.
- `PYBP retire-session <plan-dir> --session sNN --reason "..."` — close a session `WONTFIX`. Refuses while live dependents exist unless you cascade or drop the dependency.
- `PYBP retire-plan <plan-dir> [--reason "..."]` — abandon a whole **plan-level isolated** plan without landing it: keeps its branch (local and remote) unconditionally, drops its worktree only when clean. Not the session-level `retire-session` above. See "Plan-level git isolation" below.
- `/plan-execute <plan-dir> --status` — read-only: print current state, no dispatch. Surfaces any `HALT_NOTICE.txt` banner first.
- `/plan-execute --status --all` — one-line status for every `_plans/*/` under the current directory.
- `/plan-execute <plan-dir> --clear-halt` — clear the halt flag (and remove `HALT_NOTICE.txt`) after the operator has fixed the cause. Then re-run or `--resume`. **The SECOND clear of the same session's halt is REFUSED without `--cost-report '<elapsed, passes, what changed, the cut>'`** — the tool counts, because a memory rule saying "once" did not hold (measured: 9 halts, 10 clears, one session). Post that line to the owner, then pass it.
- `/plan-execute <plan-dir> --unsafe-lock` — proceed even when the plan dir sits on a networked/sync filesystem (see "Filesystem locking" below). Off by default.
- `/plan-execute` (bare, no `<plan-dir>`) — resolves `_plans_index.md` by searching upward from cwd and defaults to its `**CANONICAL**`-tagged row, or the highest `created YYYY-MM-DD` row if none is tagged. Prints which plan it defaulted to on stderr. (QW-02, 2026-07-03.)

`<plan-dir>` is the directory plan-builder created (`_plans/<slug>-<date>/`),
or a path to its `PLAN.html`. `scripts/run.py` accepts either, and if the
literal path doesn't resolve from cwd (e.g. an earlier `cd` shifted cwd
relative to it), searches upward before erroring.

## Filesystem locking (P5)

The `.lock` pidfile (like `flock`) is reliable only on a **local POSIX
filesystem**. On a networked or cloud-sync filesystem (iCloud Drive, Google
Drive / OneDrive / Dropbox via Finder, NFS/SMB) two runs can each believe they
hold the lock and race `PLAN.html` into corruption. `begin` detects the common
cases (path-prefix + mount-type probe) and **refuses** rather than risk it. Move
the plan to local disk, or pass `--unsafe-lock` to override at your own risk (the
override logs a `lock_fs_warning` event to `run.ndjson`).

## Halt notification (P8, opt-in)

If the plan's manifest carries `notify_on_halt.command` (set in the plan-builder
spec — see plan-builder `references/schemas.md`), the halt path runs that command
once, synchronously, best-effort: `shell=False`, ~10s timeout, with env vars
`PLAN_DIR`, `PLAN_TITLE`, `HALT_SESSION`, `HALT_REASON`. Any failure is swallowed
and logged as `notify_failed` — notification never blocks or crashes the halt. It
is opt-in, logged, and accompanied by the visible `HALT_NOTICE.txt`. Point
`command` at any project script — e.g. one that posts to Slack, sends a desktop
notification, or pings a webhook.

`PYBP` below means: `python ~/.claude/skills/plan-execute/scripts/run.py`.

## The dispatch loop

Repeat until `plan` reports `complete`, `land`, `checkpoint`, `replan`, `blocked`, or `halted`:

1. **Check state.** Run `PYBP plan <dir>` (add `--resume` if the user asked to resume; add `--session sNN` to target one). It prints a JSON `action`:
   - `halted` — a prior run set the halt flag. STOP. Surface `halt.reason` + `halt.by_session` to the user. They clear it with `/plan-execute <dir> --clear-halt` after fixing, or pass `--resume`.
   - `complete` — every session is DONE/terminal. (A `complete` carrying `scope: session` answers only for the one session you named while others are still open; once every session is terminal, `plan --session sNN` returns the whole-plan `land`/`finish`/`complete` answer instead.) Run the learning-capture pass (see "Learning capture" below), then report the outcome; STOP. **If any session's manifest entry carries `acceptance_review: true`, report ITS verdict rather than a bare "plan complete"** — read that session's `_closeouts/<sid>.json` plus the evidence artifact it names, and lead with the per-criterion result (which acceptance criteria came back ACHIEVED, which came back GAP, with the evidence each cited) and the path to its one-page report. That session is the plan's validation half: the per-session verify gates checked whether each part finished; it checked whether the plan's objectives were actually met. If the plan carries the marker but that session ended `DEFERRED`/`WONTFIX`/`BLOCKED`, the plan is complete but **not validated** — say exactly that instead of reporting success. When no session carries the marker (or the plan predates the field), report completion as before and note that no plan-level acceptance review was authored — see plan-builder `references/schemas.md` § "Closing acceptance review".
   - `land` — every session is terminal **and this plan runs on its own branch** (plan isolation, `plan_schema_version >= 7` or `begin --isolate`). It does not just stop; it LANDS. Run `PYBP land <dir>`. That merges the default branch into the plan branch, merges the plan into a detached land worktree, commits the plan record, re-runs the plan's union of verify gates on the exact tree it is about to push, and then **STOPS at `land-awaits-review`** — the last human gate before an irreversible write to the shared default branch. Present the `brief` verbatim (it is also written to `LAND_NOTICE.txt` and printed above the JSON as text); the operator approves with `PYBP land-ack <dir> --note '<why>'` and you then re-run `PYBP land <dir>` to push. Every other outcome is `land-parked` (a conflict, a red or undecided gate, a rejected push) and exits 1 — surface the brief and STOP; nothing was pushed and nothing was cleaned up. **`land-repair`** (exit 1) means the re-gate is red, every red gate is an `at_land` argv gate, and each passes on the base: this plan broke it, so it gets a repair session instead of a park, at most 3 rounds (`references/finish-contract.md`, "The `land-repair` directive"). Do four steps in order: (1) run `PYBP add-session <dir>` from `add_session` — `--id <sid> --title <title> --task-class <task_class> --gates <gates, comma-joined> --prompt <prompt>`, plus `--infographic-group <infographic_group>` when it is not null, and each `new_items` entry as `--new-item '<json>'` — but when the directive carries `amend_session` (land replaced a round whose session never started and is already in the plan), run `PYBP amend-session <dir> --session <sid> --prompt <prompt>` from it instead; (2) fast-forward the plan branch by running `start.command`; (3) dispatch the new session like any other (`plan`, then `begin`); (4) once it is terminal, re-run `PYBP land <dir>`. If step 1 fails, nothing has moved: surface the error and STOP. If step 2 fails, STOP before dispatch and surface `start.command`; the added session stays pending until the operator runs it, and `plan` and `begin` refuse it (`blocked`, `reason: repair-start-pending`) until the plan tree holds the candidate — if the refusal says the candidate no longer exists, run `PYBP land <dir>` for a new directive; if it says land went green without the session (`REPAIR NOT NEEDED`), run the `retire-session` command it names, because land holds a lock that blocks it from retiring the session itself. Before any of this, land re-runs each red gate once, whole, on the same tree; a gate that then passes is flaky and gets no repair. A red that a repair cannot help parks instead — `gate-flaky` (every red gate passed on its re-run, or the gates are green but a held check is unresolved; fix or quarantine the named test, or re-run `land` — but when the test lives in a file the plan changed, only fix or quarantine, and land refuses until the plan changes that file; a gate whose output names no test or file is held until the plan changes anything), `gate-inherited` (red on the base too), `repair-gate-unresolvable` (pull the registry into the root the brief names, then re-run `land`), `repair-session-invalid`, `repair-exhausted` (3 rounds ran), `repair-same-failures`, `repair-not-needed` (the re-gate is green but the repair session is still TODO: run the `retire-session` command the brief names, then re-run `land`) or `repair-in-progress` (the re-gate is green but the repair session is still open: wait for it to finish, then re-run `land`) — surface the brief and STOP. **Long gates, for verify gates and for land alike:** when any resolved gate's `timeout` exceeds 540 s, run that step (`PYBP verify-run …`, or `PYBP land <dir>`, which runs every gate synchronously) with `run_in_background: true` and wait on it with `Monitor` until it exits — never in the foreground, under the Bash tool's 600 s cap. Three sub-cases: `land-locked` means another plan holds the repo lease, so retry later; if the re-gate resolves a **skill-kind** gate, `land` returns `invoke-skill` — run that skill, judge its output, and record the verdict with `PYBP land-record <dir> --gate <id> --status passed|failed`, then re-run `land`; and `kind: gate-on-box-verification` means a re-gate REFUSED on policy (Codex-built plan, no Claude-verifier opt-in) so no model may read the merged tree at all — surface the brief and STOP, because only a human may answer it with `PYBP land-verify <dir> --decision verified-on-box|blocked --note '<why>'`. Never re-run `land` hoping that one clears: unlike `gate-indeterminate` it is decided before the tree is read and is identical on every retry. Only `landed` is success — and a `landed` result also carries `cleanup`: a successful land's LAST step tears down the plan's own worktree and branch (local and remote), see "Plan-level git isolation" below. After `landed`, run `PYBP plan <dir>` again: it returns `finish`, which owns bringing the checkout to the landed commit. The authority for every rule here is `references/plan-isolation-contract.md` §4/§5; nothing in this loop may reach for `git branch -f`, `git fetch --update-head-ok` or `git update-ref refs/heads/<default>` — all three are forbidden by name because two of them succeed and corrupt the operator's checkout.
   - `finish` — the plan is done (and landed, when isolated) but its record, leftovers and CI have not been reported. Every plan ends here, before `complete`. Run `PYBP finish <dir>` with `run_in_background: true` and wait on it with `Monitor` until it exits, never as one blocking call (the CI wait can run 540 s, and a run stopped during it resumes at the CI check on the next `finish`, not at its git steps); add `--no-wait` to look at CI once instead of waiting up to 540 s. On `finish-parked`, show the `brief` verbatim and STOP. A park at preflight, at the record push, on `record-special-file` or on a busy lease before the git steps means nothing past the park was done, and the operator's fix is in the brief. A `ci-save-busy` park (top-level `park_reason`, never a preflight reason: the lease was busy only when the CI result was saved) comes after the git steps ran: the record may be pushed and the checkout fast-forwarded, only the CI result was not saved. Its brief names the lease holder; once that holder has released the lease, run `PYBP finish <dir> --no-wait`, which skips the git steps and goes straight to CI. A `finish-running` park (top-level `park_reason`) means another finish run of this plan holds its run lock: this run read, wrote and pushed nothing; wait for that run to end, then run `PYBP finish <dir> --no-wait`. On `finish-superseded` (CLI exit 0), another finish run (a newer generation) or another CI poll wrote the state first and this run wrote nothing: show its note and do NOT report this run's CI verdict as the plan's result. The note names one of three cases, read from the saved state. It has `finished_at` (the note says a newer run saved its result first): run `PYBP finish <dir> --no-wait` once to read it. It has `steps_done_at` and no `finished_at` (the note says a newer run has not saved its CI result yet): another finish run did the git steps and is polling CI; wait for the finish run you started, and if none is running it died, so run `PYBP finish <dir>`. It has neither (the note says the plan was reopened): `begin` reopened the plan, or a newer finish run stopped before its git steps; run `PYBP plan <dir>` and follow it, and a re-run of `finish` is safe. A run superseded at its start (another run of this plan changed `finished_at`, `steps_done_at` or `generation` after this run read them) resets nothing and runs no git step; a run superseded at its steps checkpoint skips the CI poll. A `record-special-file` park means a FIFO, socket or device file sits in the plan folder; the operator removes it and re-runs. `finish` saves `steps_done_at` once its git steps (record, leftovers, checkout) are done; a later `finish` with `steps_done_at` and no `finished_at` skips them and goes straight to CI. On `finished`, report in plain words: the record (what was committed and pushed), the leftovers (unpushed commits, the plan worktree and branch), the checkout (fast-forwarded, or blocked and the command to run), and CI's verdict. Say `pending` with the re-run command (`PYBP finish <dir> --no-wait`), say `unknown` with its reason, and say plainly when CI tested a later commit (`descendant`) or the base had the same failures (`inherited`). A `finish_mode` of `report-only` (a plan finished before arming existed) pushed nothing: say so and give `PYBP finish <dir> --apply` to do it for real. There is no `finish-pending` action. Only then run `PYBP plan <dir>` again, which now reports `complete`. Authority: `references/finish-contract.md`.
   - `checkpoint` — the next session needs human review. Run `PYBP checkpoint <dir> --session sNN` to flip it to `AWAITS_REVIEW`, then **present the gate as a decision, not a toll booth**, and STOP. The action (and the `checkpoint` command's output, as `checkpoint_brief`) carries the author's decision brief — `reason` (why this gate exists), `decision` (the specific question being asked), `options` (the concrete answers, when authored). Render it in plain language: one short paragraph of background (what the plan just finished, what this session will do — from the session's `human_summary`/`deliverable` if the reader needs it), then the reason, then the decision as a direct question with its options and what `--resume` does after they answer. Never reduce the gate to "sNN awaits review — run `--resume`". If the brief is `null` (a legacy pre-brief manifest), derive the background yourself from the session's `human_summary` + `deliverable` + any `post_session` actions, state the decision as best the manifest supports, and note the plan predates decision briefs — and if even that yields no real decision (nothing irreversible, nothing judgment-laden, the only sane answer is "proceed"), say so explicitly and recommend the user rebuild the gate as a `verify` gate or `checkpoint_policy: "notify-and-continue"`. **The owner's reply is the answer.** Any reply, including "proceed", is the answer: write it verbatim to `<plan dir>/_decisions/<sid>.md` with your file-writing tool (never a shell echo), then run `PYBP plan <dir> --resume --answer-file <that file>`. Use `--no-answer` only when the owner explicitly declined to answer. A bare `--resume` past this checkpoint refuses. The answer is recorded with the plan and put at the top of the resumed session's prompt. This covers only the pre-dispatch checkpoint on `plan --resume`: `ack-checkpoint`, `ship-begin --resume` and a by-name `begin --sessions` record nothing.
   - `replan` — a finished session's closeout carried `plan_impact`: it learned something that invalidates work the plan has NOT done yet, so the plan is parked. Present each `replan[]` brief as a decision — the reason, the invalidated sessions (with their titles and current statuses), and the three options — **with your own recommendation filled into the empty `recommendation` slot**, then STOP. After the user picks, apply it with `amend-session`/`retire-session`/`redispatch` and record it with `resolve-replan`. See "The REPLAN gate" below.
   - `blocked` — a session is BLOCKED or has unmet deps. Surface it; STOP. With `reason: repair-start-pending`, read `message`: it holds the command to run.
   - `verify-pending` — a session closed DONE but is still `DOING` because its verify block has not run. Not a blocker: run `PYBP verify-begin <dir> --session sNN`, `verify-run` per gate, then `verify-finalize`, and re-run `plan`.
   - `dispatch` — proceed to step 2 with the listed `batch`.

   Two cross-cutting keys ride on EVERY action: `blocked_items` (items left BLOCKED inside a
   PARTIAL session — visible without wedging the loop) and **`ack_required`** (sessions
   parked at a POST-SESSION checkpoint). A session in `ack_required` has already finished —
   present its result and approve it with `PYBP ack-checkpoint <dir> --session sNN`. Never
   re-dispatch it, and never reach for `--resume` to clear it: `--resume` is the answer to a
   *pre-dispatch* gate only. See "The two flavors of `AWAITS_REVIEW`" below.

2. **Begin the batch.** Run `PYBP begin <dir> --sessions <id...>` with every id in `batch` (add `--resume` when you got here from `plan --resume` on a HALTED plan — `begin` guards the halt flag too, and refuses without it). This acquires the lock, anchor-preflights, flips each session to `DOING`, and returns each session's `backend` (`claude` or `codex` — resolved at dispatch time against the SSOT's `active_provider` + the session's model), `prompt_text`, `model_arg`, `reasoning` tier, and `fallback_model` / `fallback_reasoning` (the reactive-degradation target to use if the requested model is refused at dispatch — see step 3). A Codex-declared session whose route can't be constructed makes `begin` exit 1 with the session marked `BLOCKED` + the plan halted (nothing is dispatched — surface the `unroutable` reasons and STOP; it never silently falls back to Claude). **executor_policy (s06):** `begin` enforces the SSOT's `executor_policy` fail-closed — under `active_provider: openai` only a session whose manifest `task_class` is opted into `executor_for` rides the dial to Codex (others dispatch Claude, with an `executor_policy_note`); linchpin/irreversible sessions NEVER auto-dispatch to Codex (dial-driven → Claude; an explicit Codex pin on barred work → BLOCKED); and the `data_sensitivity_guard` egress check refuses any Codex dispatch from a working tree that trips its CONTENT scan (one `gitleaks dir` pass — the same scanner the commit hook runs) or its `.env*` FILENAME rule, realpath + symlink-resolved — the refusal happens BEFORE any codex process exists and names the offending path. The two rules scan DIFFERENT surfaces on purpose: `.env*` over the whole tree, git-ignored files included (a `.env` is usually git-ignored and trips no content rule at all); the content scan over the REVIEWED surface only — git-tracked plus untracked-not-ignored, minus `__pycache__`/`*.pyc`, with a non-git tree scanned whole. Git-ignored build output is out of scope because it is not the repo: full-tree, this check took 6 min 5 s and returned 826 findings on a real 16 GB plan repo, every one of them a build artifact; scoped it is ~3 s (measured). Two SSOT keys clear it, both requiring an `expiry`: `content_scan_allowlist` for one reviewed file, `egress_opt_ins` for the whole repo. No `gitleaks` on PATH fails CLOSED — it refuses and names the missing dependency; there is no fallback scanner. A scan whose scanned-byte count overshoots the surface it selected also fails CLOSED (`gitleaks dir` takes ONE path and silently falls back to the CWD tree for extra args). Filename SUBSTRINGS (`corpus`/`creds`/`credential`/`secret`) were DROPPED 2026-07-28: they blocked a real repo over a file named `migrate_corpus.py` while missing a live key in `config.py`. Each member also carries `executor_family`/`verifier_family`/`verifier_mode` — the PROVIDER-SYMMETRIC verification stamp derived from the ACTUAL executor (the non-executing family verifies; `on_box_human` means the tree is restricted, so verification is human/on-box at the checkpoint — record VERIFIED-ON-BOX or BLOCKED, and NEVER start a Codex process to verify it) — the same `on_box_human`/`degraded_from: cross_family[_unavailable]` disposition backs the `cross-family-review-low|medium|high` verify gates (`references/verify-gates.md` → "Cross-family review"). **Effort enforcement — `subagent_type` is the mechanism, not the prompt (corrected 2026-07-26).** `begin` resolves each member's `(model, reasoning)` pair to a **tier agent** — `tier-<model>-<effort>`, e.g. `tier-opus-high` — whose frontmatter carries real `model:` + `effort:` fields, and returns it as `subagent_type`. Each member also carries `effort_enforced` (bool) and `effort_mechanism`:
   - `tier_agent` — the tier is genuinely bound by the resolved agent's `effort:` frontmatter. Announce the tier as authoritative.
   - `agent_definition` — the manifest named its own `subagent_type`; that agent's frontmatter governs, and `begin` can't see it. Announce the manifest's declared tier, not a claim about what bound it.
   - `prompt_directive_advisory` — **nothing binds the effort.** No tier agent covers this pair (e.g. sonnet `xhigh` and every `max`, which are deliberately unmapped — dead/operator-elected rungs; opus `xhigh` is mapped since v1.20, and opus `low` since v25 as an experiment tier for the downgrade canary), so `begin` names `session-effort-worker` — the one agent with NO `effort:` key (SSOT v26) — and the subagent **inherits the orchestrator's session effort**; `prompt_text` gets the old advisory prose instead. `begin` prints a WARNING. Announce it honestly — *"S05 declares max; no tier agent, so it inherits the session effort"* — never as an enforced tier. **fable's xhigh is the one named exception** (ESC-01): `tier-fable-xhigh` exists because fable is the SSOT's escalation apex reached from opus-high, so `(fable, xhigh)` DOES bind via `tier_agent` like any other mapped pair — only `(fable, max)` still falls through to this advisory path. That fable-xhigh binding is source work only until the live probe in a deployed session confirms `subagent_type: tier-fable-xhigh` actually resolves and dispatches (PROVEN BY s09, not assumed here).

   Why: measured against the Claude Code 2.1.220 docs, prompt text is **not** an effort control. Only `ultrathink` is a recognized keyword, and even that "adds an in-context instruction. The effort level sent to the API is unchanged"; `think`, `think hard` and `think more` "are passed through as ordinary prompt text and are not recognized as keywords." An agent with no `effort:` frontmatter inherits the session level. Since SSOT v26 `agents/general-purpose.md` replaces the built-in and pins effort medium, so an UNTYPED dispatch no longer inherits — which is why the unbound path names `session-effort-worker` instead of leaving `subagent_type` null. Full precedence (`CLAUDE_CODE_EFFORT_LEVEL` > frontmatter > session > model default) and the two other corrections: `model-routing.yaml` § "WHAT ACTUALLY BINDS AN EFFORT LEVEL". (Open a TaskCreate per batch for visibility if you like.)

3. **Dispatch — ONE assistant turn. Workflow by default for a batch of two or more; N parallel `Task` calls for a one-member batch and as the fallback whenever the Workflow path is excluded (see below).** First, from the MAIN CONVERSATION — never inside a dispatched worker or a Workflow agent, whose session id is not yours — mark yourself unsafe to compact for the length of the batch: `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --plan-dir <dir> --phase batch-<n> || true`, where `<n>` is this loop iteration counting from 1 — since compact-policy v2 this hold is a REAL brake: an orchestrator's automatic compaction is deferred while a live in-phase `hold` stands and released at the between-batches `ok` (step 7), so compaction lands at the seam instead of mid-batch; every fail-open (expired hold, no headroom, at-ceiling) still applies, so the worst case is a mid-batch compaction exactly as before v2. **`--session "$CLAUDE_CODE_SESSION_ID"` is mandatory on both safe-point lines (here and step 7) and must not be dropped:** `safe-point` never guesses a session id for a write, and it refuses whenever a worker marker is set — `CLAUDE_CODE_CHILD_SESSION`/`CLAUDE_CODE_FORK_SUBAGENT` both read `1` in the MAIN conversation's own Bash subprocess too (measured, re-confirmed), so the bare form refuses HERE, prints `Nothing written.` to stderr and exits 0. Measured with a known-positive control: the bare call left the policy file byte-identical while the `--session` call wrote `safe_point`. Without the flag this hold is not a brake at all — it is a no-op that reads like a success. Then, for each batch member on the `Task` path, emit a `Task` tool call:
   - `subagent_type` = the member's `subagent_type`. This is normally a resolved **tier agent** (`tier-opus-high`, `tier-sonnet-medium`, …) — pass it, since it is what binds the session's effort (see step 2). On the unbound path it is `session-effort-worker` (the per-session `model` IS honored; the effort **inherits** the orchestrator's session level). When it is `null` — only when that agent's file is missing on disk — **omit `subagent_type`**: a fresh `general-purpose` agent, which since SSOT v26 runs at effort medium. When it is the literal `"fork"`, pass `subagent_type: "fork"` — a fork inherits the orchestrator's full context **and always runs the orchestrator's model** (the per-session `model_arg` is ignored by the tool — `build_plan.py` warns at build time if a session pairs `fork` with a model). Reserve forks for the rare session that genuinely needs the orchestrator's live conversation context; sessions normally communicate through the project filesystem + closeouts, not context. (Earlier docs called `null` "a fork" — that was wrong: omitting `subagent_type` yields a fresh agent, not a fork.)
   - `prompt` = the member's `prompt_text` verbatim. On the `prompt_directive_advisory` path it carries an advisory thinking line (`max` = `Ultrathink…`, `high` = `Think hard…`, `medium` = `Think about…`; nothing for `low`) — prose that expresses intent, NOT an effort control. On the `tier_agent` path there is deliberately no such line: the resolved agent's `effort:` frontmatter does the work. Either way, don't add or strip thinking instructions yourself.
   - `description` = `member.dispatch_description` verbatim when it is non-null; otherwise use the ordinary concise session label. This opaque description marker is emitted only for an enabled shadow-eligible plan session with deterministic argv verify gates. It is deliberately outside `prompt_text`, so telemetry cannot alter the primary subagent instructions or result. Do not copy it into the prompt, embellish it, or reuse it on a retry.
   - `model` = the member's `model_arg` when it is non-null (this is the manifest's per-session model — e.g. `sonnet`, `opus`, `haiku`, `fable` — already normalized to the tool's accepted token). **Honor it** — do NOT omit it, or the subagent silently inherits the orchestrator's model and the plan's model directive is lost. **When `model_arg` IS `null` and `subagent_type` is GENERIC — omitted, `general-purpose`, `Explore`, or `Plan` (never a `tier-*` agent, whose own frontmatter already pins a model, and never the literal `"fork"`, which always runs the orchestrator's model by design) — omitting `model` is ILLEGAL (RT-02, measured: 46 of 196 fan-out dispatches ran unpinned this way).** A `PreToolUse` hook cannot fix this after the fact — Claude Code silently ignores a hook's attempt to change an Agent/Task call's `model`/`subagent_type` — so resolve it HERE, at construction: pass the SSOT's `fanout_policy.default_model` (`~/.claude/model-routing.yaml` — `sonnet` today; use `mechanical_shard_model` instead for a `mechanical` task_class shard) as `model`. Never override an explicitly pinned `model_arg`, a `tier-*` agent, or a fork — this substitution fires ONLY on the null+generic combination.
   - **Announce the tier — and refer by name.** When you emit the Task call, name the session by its `title` with the id riding inside, plus the model + reasoning effort — e.g. *“Dispatching ‘Migrate corpus schema’ (s03) on Opus · reasoning: high”* — using the member's `title`, `model_arg` and `reasoning`. A wall of bare sNN ids is illegible; names read at a glance. Use the same name-first form everywhere the user reads a session reference (checkpoints, verify announcements, completion reports), never a bare id. Makes the per-session tier visible live, not just in retrospective audit. *(2026-08-01, wayfinder-derived.)*
   - <!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
   **Codex-backed members (`backend: "codex"`) — two-layer wrapper dispatch.** The Task tool only accepts Claude tokens, so a Codex session NEVER rides Task's `model` param. For these members `begin` already did the work: `prompt_text` is a short WRAPPER prompt (not the session prompt — that stays in `prompt_file`, which the embedded command reads on stdin) instructing a fresh Claude agent to run the member's `codex_cmd` in the FOREGROUND (`codex exec --ignore-user-config --ignore-rules --sandbox workspace-write -c project_doc_max_bytes=262144 -m <codex_model> -c model_reasoning_effort=<codex_effort> -o <last-message file>`) and relay Codex's final message verbatim — it ends with the closeout block, and extraction takes the LAST block. Dispatch exactly like any other member (`model` = `model_arg`, the fixed Claude wrapper model; `prompt` = `prompt_text`; omit `subagent_type` when null) and announce with the Codex tier, name-first like any other member — e.g. *“Dispatching ‘Price-sync spike’ (s05) via Codex wrapper — gpt-5.6-sol · reasoning_effort: high”* — from `title`/`codex_model`/`codex_effort`. **Degradation:** a wrapper reply of `CODEX-DISPATCH-FAILED: <signal> — …` (entitlement | unavailable | cli_version_rejected | quota_exhausted) instead of a closeout means the Codex model was refused at dispatch: re-dispatch ONCE using the member's ready-made `fallback_prompt_text` (embeds `fallback_model` @ `fallback_reasoning` — the provider-scoped chain sol→terra@max→NO-CODEX, plus the luna→terra@max rescue; nothing degrades onto luna) and record `degraded_from` in the closeout you hand to `apply`. `quota_exhausted`, or a `null` `fallback_model`/`fallback_prompt_text`, means NO-CODEX: surface the failure and STOP — never re-run the session on a Claude model instead. A `CODEX-NO-CLOSEOUT` or `CODEX-TIMEOUT` reply is NOT a dispatch failure — apply it as-is (the missing-closeout path blocks the session for review). Never edit the `-m`/`--sandbox` flags yourself and never strip the wrapper to run the session inline. **Verify-gate rework for a codex member:** `begin` already appends the live `_verify_state/<sid>.feedback.md` to the prompt file `codex_cmd` reads (once, under `--- BEGIN CURRENT VERIFICATION FEEDBACK ---`; an archived `r<N>` feedback is never carried), so re-dispatch the returned `codex_cmd` unchanged — do NOT pipe the feedback file in yourself (that delivers it twice), and do NOT append it to the wrapper prompt (the wrapper ignores it). Note: the manifest's five reasoning tiers map one-to-one to native Codex effort for codex members (`xhigh` → sol@xhigh, `max` → sol@max — the old clamp of `xhigh`/`max` onto the `thorough` cell was removed at CL-03); every model in the 5.6-only lane reaches `max`, so no tier clamps any more, and `ultra` is never emitted. **Unroutable recovery:** when `begin` blocks a codex session as unroutable, fix the manifest model (or the SSOT), run `--clear-halt`, and re-run — the next successful `begin` overwrites the BLOCKED status.
   - <!-- fallback-ladder-source: ~/.claude/model-routing.yaml providers.<name>.degrade (provider-scoped; see the plan-builder routing-ssot stamp) -->
   **Reactive model degradation.** If a dispatched `Task` fails or is refused for **model access/entitlement** — the requested model is unavailable or unentitled (an entitlement or availability refusal on the requested model — Fable is a normal, available model as of 2026-07-26, so do NOT assume it is the one that failed) — do NOT silent-inherit and do NOT skip the session. Immediately **re-dispatch the SAME session** on the member's `fallback_model` at reasoning `fallback_reasoning` (per-target: **both Opus and Sonnet land at `high`** — `xhigh` is never a DEGRADE landing per the SSOT's own calibration — a dead rung for sonnet, and for opus an escalation-only rung (v1.20) that degradation must not enter). **PRECEDENCE — an ESCALATED member is not degraded.** When the member carries `on_dispatch_refusal` (ESC-02 raised its rung above the authored cell), its `fallback_model` is `null` by construction and that field names the only correct response: run `record-refusal` and re-run `plan`. Degrading instead would drop the session to or below the tier its author chose — which the floor invariant forbids — and, because no refusal was recorded, the same refused rung is re-proposed on every rework until `max_rework` burns out on a model that will never run.

   **This path only fires on a REAL dispatch error, and often there won't be one.** Per the docs, a subagent `model` override that is blocked or unresolvable "falls back to the inherited or default model rather than failing the request" — so an entitlement/allowlist refusal can produce **no error at all**, and the session silently runs on the orchestrator's model. There is nothing to catch. Detection for that case is observational: the `PreToolUse(Agent)` dispatch-audit hook records the requested model + `subagent_type` of every dispatch to `~/.claude/evals/routing/dispatch-audit.ndjson`, and session-level availability failover is handled natively by the `fallbackModel` chain in `settings.json`. See `model-routing.yaml` § degradation "HOW THE LADDER IS (AND IS NOT) TRIGGERED". The Claude ladder is **Fable → Opus (5.5 since 2026-09-22) → Sonnet (5.5 since 2026-09-28), floor at Sonnet** — never auto-drop judgement work to Haiku, and never emit Opus @ `max`. **Announce** the substitution in the stream — e.g. *“S02 requested Fable — unavailable; degraded to Opus · high”* — and **record** it: in the closeout you hand to `apply`, add `degraded_from: {"requested":"<token>","ran":"<fallback>","reasoning":"high"}` so the audit trail + cost transparency show “requested Fable, ran Opus @ high” instead of a silently-inherited mystery model. `begin` already gives you `fallback_model` / `fallback_reasoning` per member, so the fallback is ready without recomputing. (A `null` `fallback_model` — the requested model was Sonnet/Haiku/unrecognized — means there is no lower judgement-safe tier: surface the dispatch failure and STOP rather than degrade below the floor.)
   - **GLM tree (`active_provider: zai` in the begin payload) — same Task calls, translated cells (v1.21).** When `begin` reports `active_provider: zai` (the `~/.claude-glm` tree, or `PLAN_EXECUTE_ROUTING_PROVIDER=zai`), dispatch EXACTLY as above — but read the member fields with the translation in mind: `model_arg`/`reasoning` are the ZAI cell (`begin` clamped the manifest pair: `Sonnet`→`sonnet`@max-or-low, `Opus`→`opus`@max, `Haiku`/`Fable` pins collapsed onto `sonnet`/`opus`), `subagent_type` names a zai tier agent (`tier-sonnet-low`, `tier-sonnet-max`, `tier-opus-max`), and degradation KEEPS `max` (`opus→sonnet@max` — GLM is effort-steep; a below-max flash re-dispatch is the rough zone, not a saving). Under the GLM env the tokens ARE glm-5.3-flash / glm-5.3 — announce with the wire name when you know the run is GLM, e.g. *“Dispatching ‘Build govrun’ (s01) on glm-5.3 · reasoning: max”*. The zai ladder has NO apex: escalation tops out at `opus@max`, and a `null` fallback on `sonnet` means surface-and-stop, exactly as above.
   - **Sandbox (H8):** the subagent works on project code only. It must NOT edit anything under `<plan-dir>/`. The closeout block is its only channel to plan state. Do not grant it write access to the plan directory.
   - **Isolated members (`isolation: "worktree"`) — dispatch them EXACTLY like any other member.** `begin` already created each one's git worktree and prepended the instruction to `prompt_text`; the member also carries `worktree` and `worktree_branch` for your announcement. **NEVER pass the Agent/Task tool's own `isolation: "worktree"` parameter** — it is barred by name (S02 measured it silently destroying an agent's on-disk output), and doing so would put the member in a *different* worktree from the one the orchestrator merges. See "Worktree-isolated parallel groups" below.
   - **Findings digest — a session starts knowing what earlier sessions of THIS plan already got flagged for.** `begin` prepends a short digest to every member's dispatched prompt (`prompt_text` for a Claude member; the prompt file a Codex member reads on stdin) built from every OTHER session's `_verify_state/<sid>.findings.ndjson` — medium severity or worse, still open (not accepted), newest first, capped at 12 findings. A bad ledger never blocks a dispatch (an unreadable ledger is skipped, not raised). The one refusal is on the Codex lane: the digest and the rework feedback file come from the plan directory, outside the tree the egress guard scans, so `codex_command._refuse_restricted_text` runs the same gitleaks pass over that text and refuses the dispatch on a hit (fail-closed without gitleaks; the repo's `egress_opt_ins` entry clears it). A plan with no prior findings dispatches byte for byte as before. Built once, by `findings_digest.digest(plan_dir, session_id)`, and read the same way by both harnesses.
   When you are on the Task path and the batch has >1 member, put all the Task calls in a SINGLE message so they fan out concurrently. Your turn stays open until all return.

   **Workflow dispatch — THE DEFAULT for a batch ≥ 2 (`backend:"claude"` members ONLY; a codex member always uses the plain Task wrapper path, since `agent()`'s model param cannot take a GPT id and a codex failure surfaces as a `CODEX-DISPATCH-FAILED` message, not an `agent()` error).** If the Workflow tool is available in this session (it can be disabled via settings) and no member's prompt requires invoking skills mid-session, dispatch the batch as ONE Workflow call rather than N Task calls; the parallel Task calls above are the fallback for every other case. **Why this is the default and not an option (th-02):** a Task dispatch returns each worker's ENTIRE output into your context, so an orchestrator running a long plan accumulates every worker's prose and compacts mid-run — losing the plan state it alone owns. The schema-forced Workflow return keeps that output out of your context altogether. The script runs `parallel()` over the members, one `agent()` each: `agent(prompt_text, {label: <id>, model: <model_arg, or — when it is null AND agentType is GENERIC (omitted / `general-purpose` / `Explore` / `Plan`) — the SSOT's `fanout_policy.default_model` (RT-02; never omit `model` on that combination, and never override an explicitly pinned `model_arg`, a `tier-*` agentType, or `"fork"`)>, agentType: <subagent_type — omit when null>, schema: <closeout schema>})`, where the schema mirrors `references/closeout-contract.md` with `session` pinned `const` to the member's id and `result` an enum of `[DONE, PARTIAL, BLOCKED]`. The schema-forced return eliminates the missing/malformed-closeout failure classes at source and keeps large subagent outputs out of your context. On return, for each member wrap its validated JSON in the `<plan-execute-closeout>` envelope yourself, write that as the entire temp-file content, and continue at step 4 unchanged — `apply` remains the single validator and the write-ahead contract is preserved. If a member comes back with no validated result (agent error, budget exhaustion), write NO closeout file for it: the session stays `DOING` and the normal crash-recovery path covers it (re-run `--session sNN`, using a plain Task dispatch). Never synthesize a closeout the agent did not return. On Workflow unavailability or a hard workflow error, fall back to the parallel Task calls above. **Reactive model degradation applies here too:** if a member's `agent()` errors on model access/entitlement (e.g. Fable unavailable), re-run that member's `agent()` on its `fallback_model` at `fallback_reasoning` (`high` for both Opus and Sonnet — never `xhigh` — a dead rung for sonnet, and for opus an escalation-only rung (v1.20), so no degrade lands there), and set `degraded_from` in the closeout you envelope for `apply` — the same Fable → Opus → Sonnet ladder as the Task path.

   **Workflow seam rule (shared with /plan-harden):** a Workflow is bounded dispatch/analysis that returns a structured report. Human gates — checkpoints, `--resume`, shipping confirms — stay in the main conversation (workflows cannot pause for input; checkpoints are resolved in step 1, before any dispatch). Workflow agents never run `run.py` (`PYBP begin/apply/release/ship-*`): all helper invocations, lock handling, and closeout applies stay in the main conversation. The H8 sandbox binds workflow agents identically.

4. **Apply each closeout.** For each returned subagent output:
   - Write the subagent's full final message to a temp file (e.g. `/tmp/closeout-sNN.txt`). If the message is long, you may write only its tail starting at the last unfenced `<plan-execute-closeout>` occurrence (extraction takes the LAST block anyway); if you see no closeout block at all, write the last ~2,000 characters so `apply` records the missing-closeout diagnostics.
   - **Record the dispatch receipt (TEL-01), AFTER the Task call returns and BEFORE `apply`.** Run `PYBP record-receipt <dir> --session sNN --agent-id <id> --transcript <path> --backend <claude|codex>`. `--session` and `--agent-id` are required (the command rejects an empty id); `--transcript` and `--backend` (default `claude`) are optional flags, but pass `--transcript` **whenever a transcript or log exists for this dispatch**. Per backend: a Claude Agent-tool member passes the Task tool's returned agent id and its `subagents/agent-<agentId>.jsonl`; a `claude -p --output-format json` member passes that result file (the one proven `modelUsage` served-model source); a codex member passes `--backend codex` with any stable label for `--agent-id` (for example the member's session id) when no Agent id exists. The `codex_cmd` that `begin` builds runs `codex exec` WITHOUT `--json` (it writes only the `-o` last-message file), so a standard codex dispatch has no usage log: call `record-receipt` with no `--transcript` (or skip it): either way usage lands as `unavailable` and `model_ran_source` is `requested`. Pass a `codex exec --json` stdout log (usage only, no served-model field) only for a codex run you started with `--json` yourself. This is not optional bookkeeping: a background Agent-tool completion carries no usage telemetry of its own, so without this call the outcome ledger has no way to tell "we asked for Fable and it ran" from "we asked for Fable and silently got something else" — a missing or stale receipt lands `model_ran_source: "requested"` (the requested cell, never `unknown`), and only a fresh receipt with an attested served model gives `"attested"`. Best-effort and non-blocking. **Call it whenever a transcript exists.** Where the transcript lives: an Agent-tool member writes `~/.claude/projects/<project>/<parent-session-id>/subagents/agent-<agentId>.jsonl` (the Agent result's `agentId` matches the filename); a Workflow member writes `.../subagents/workflows/<wf_id>/agent-<agentId>.jsonl` (its ids are in that folder's `journal.jsonl`); a `claude -p` member is its result JSON; a codex member has a `--json` stdout log only if you started it with `--json`; the standard `codex_cmd` does not, so there is none. This transcript is also where the attempt's usage comes from (see "Route at dispatch"). Only when no transcript exists at all may you skip the call — usage lands `unavailable` (never 0) and `model_ran_source` is `requested`; `apply` is not refused.
   - Run `PYBP apply <dir> --session sNN --output-file /tmp/closeout-sNN.txt`.
   - This extracts the `<plan-execute-closeout>` block, validates it (schema + semantic), persists `_closeouts/sNN.json` (write-ahead), then atomically rewrites the affected `<article>` blocks in `PLAN.html`.
   - If it prints `"applied": false`, the closeout was missing/malformed/invalid: the session is now `BLOCKED` and the halt flag is set. Surface the `reason`. Finish applying any sibling closeouts in the batch (so their work isn't lost), then STOP.
   - `"failure": "missing_dispatch_receipt"` **cannot occur on this harness** and needs no handling here. It is the Codex-orchestrator check (contract § 3.7): under `--harness codex` a session whose `codex_dispatch` event has no `-o` file on disk is refused, because a Codex orchestrator that did the session's work inline rather than dispatching it produces a valid closeout while silently running the whole plan on its own model. Claude-harness sessions log no such event — including a Codex-backed member, whose wrapper relays the `-o` file's contents through this conversation instead of handing `apply` a path.

5. **Verify each session (if declared).** If a session's `apply` output reports `verify_pending: true`, the session is still `DOING` — a self-reported `DONE` does NOT count until its gates pass. Run the verify sub-loop for it (see "Verification gates" below) BEFORE shipping. On all-pass the session becomes `DONE` (or `AWAITS_REVIEW` if it also asked for a human checkpoint); on a gate failure it re-dispatches (bounded rework) or halts. When `apply` reports `verify_pending: false` (no verify block), skip to shipping unchanged.

6. **Ship each session (if declared).** If a session's `apply` output carries a non-null `post_session`, run the shipping sub-loop for it (see "Shipping actions" below) BEFORE releasing the lock and looping. A human checkpoint always outranks shipping — `ship-begin` defers automatically when one is pending, and shipping fires only on the explicit `--resume` past it. **Verify outranks shipping too:** never ship a session whose gates haven't passed — a session left `DOING`/`PARTIAL`/`BLOCKED` by verify is not shippable.

7. **Release + loop.** After all batch closeouts (and any verify + shipping) are applied and before the next `begin`, mark the between-batches moment safe to compact — `[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state ok --plan-dir <dir> --phase batch-<n> || true`, from the MAIN CONVERSATION with the same `<n>` you held on — then run `PYBP release <dir>` and go back to step 1.

## Route at dispatch (plan schema v8)

The single statement of record is `references/route-at-dispatch-contract.md`. Read it before you rely on any rule below; where this text disagrees, the contract wins. A plan below schema 8 dispatches exactly as before.

- **Who picks the model.** On a v8 plan the author says what the work is (`task_class`) and the executor picks the model. `begin` resolves an unpinned session from the routing file for the active provider, freezes that **class default** for the escalation generation, and prints the pick in its receipt. The receipt also prints `effort_mechanism`: `agent_definition`, `tier_agent`, `prompt_directive_advisory` (no agent binds the effort, so the cell runs at YOUR effort) or `codex_cli`.
- **An authored `model`/`reasoning` pair on v8 is an override.** Set both or neither. It needs `why_model` and lands in the ledger as `routing_provenance: pinned_override`. A below-floor override is refused for a session with `peer_triggers` or `task_class: linchpin`.
- **Locked checks.** A path in `verify.locked` is hashed at `begin`. `verify-begin` re-hashes it, and the protected test-configuration set (pytest config, `conftest.py`, `sitecustomize`, `*.pth` and the rest the contract names), before any gate. A change fails the attempt as `LOCKED_CHECK_EDITED`.
- **Usage.** Each attempt records worker token counts and cost in the outcome ledger, from the transcript passed to `record-receipt`. With no transcript the counts are `null`, never `0`. A codex attempt dispatched by the standard `codex_cmd` writes its `--json` event stream to `<last_message_file>.events.jsonl`; pass it to `record-receipt --transcript` so the attempt carries usage (`model_ran_source` stays `requested`: no served id in the stream). Cost per dispatch path: `claude -p` records the reported cost; an Agent-tool or Workflow dispatch now records a real cost (`cost_source` `model_prices`), because `model-routing.yaml` v31 carries `cache_write_5m` / `cache_write_1h` rates and cache-creation tokens bill at the 5m rate, or at the 1h rate for the share the transcript splits out as `ephemeral_1h_input_tokens`; a codex attempt records token usage from its `--json` log but `cost_usd` stays null (the stream names no served model and `model_prices` has no GPT rows).
- **Rollback.** `begin --no-route-at-dispatch`, or `PLAN_EXECUTE_ROUTE_AT_DISPATCH=0`, turns this off. An unpinned v8 session is then refused; it never inherits your model. Use the switch, never a revert.
- **GLM tree.** A v8 plan must not run under `~/.claude-glm` until that tree's copy of plan-execute has `SUPPORTED_MAX_SCHEMA` of 8 or more. `gearbox deploy` does not update it.

## Worktree-isolated parallel groups

**The rules live in `references/parallel-group-contract.md` (contract v2, amended). This section is only how the executor carries them out — where the two disagree, the contract wins.**

A group whose members declare `dispatch.isolation: "worktree"` runs each member in its own git working copy. **You do not drive any of this by hand** — it is structural, inside `begin` and `apply`, precisely so no step can be skipped by a model that read the docs quickly:

- **`begin <members>`** pins ONE base ref for the group (the repo `HEAD` at group start, recorded with the pre-existing dirty state as the baseline) and creates one worktree per member at `<repo>/.plan-worktrees/<group>/<sid>` on branch `plan/<group>/<sid>`. Creation is serial with an explicit stagger, each branch gets an EXPLICIT start-point, and the result is verified with `git merge-base` — `git worktree add` bases on local `HEAD` when you don't say otherwise, which S02 measured picking up an unpushed commit. A commit landing on the shared branch mid-flight does not move the pin.
- **Members never commit, and neither do you on their behalf.** `apply` commits each member's worktree onto that member's own branch, one session at a time, as its closeout is accepted. One serial committer is the whole answer to the concurrent-`.git/index.lock` hazard the contract's capability ledger leaves OPEN.
- **Per-member verify gates run inside that member's worktree** (`verify.gate_cwd`), so a gate that reads the working tree sees its own member's edits and not its peers' half-finished ones.
- **`begin <integration-session>` performs the merge before the agent starts.** It refuses if any member is not `DONE`, checks containment (did a member write into the shared tree?), then merges every member branch producer-first in manifest document order. A conflict ABORTS the merge, marks the integration session `BLOCKED`, halts the plan, and prints the conflicting files plus every (intact) branch name and a `git reset --hard <base>` recovery line. **Never resolve such a merge yourself** — surface it and stop.
- **A clean merge is not a working tree.** The contract forces the integration session's gates to be a superset of the union of its members', and those gates run on the MERGED tree. The failure this catches — one member renames a symbol, another adds a caller of the old name, in different files — merges without a word from git.
- **Cleanup is explicit and last:** `PYBP worktree-cleanup <dir> --group <g>` after the integration session's gates pass and its commit lands. It PRESERVES (and exits 1 on) any worktree still holding uncommitted, untracked, or locally-created ignored content, and never `git branch -D`s a branch carrying unmerged commits. `PYBP worktree-status <dir> --group <g>` is the read-only view.
- **Containment is ADVISORY.** The member is told its worktree in its prompt; nothing in the harness confines it there (`EnterWorktree(path=…)` is refused from a dispatched subagent — measured, do not re-attempt on the strength of its docs). The integration merge DETECTS a member that wrote into the shared tree and refuses rather than assuming obedience.
- **Both harnesses honour isolation.** Claude members receive the worktree preamble in their prompt. Codex members receive a `dispatch_cmd` beginning `cd <member-worktree> &&`; the effective prompt and stamped `--output-last-message` receipt live under the ignored `<member-worktree>/.plan-worktrees/codex/` directory so `workspace-write` is rooted at the checkout. Integration remains serial on the merged shared tree for either harness.

Executable proof, re-runnable: `bash fixtures/worktree-parallel/run.sh` — three concurrent members off a pinned base, a producer-first merge, the full gate set on the merged tree, and both planted failures (same-line conflict; clean-merge-but-broken-tree). Last line is `FIXTURE_RESULT=pass|fail`.

## Plan-level git isolation — the whole-plan lifecycle

**Different from the section above.** "Worktree-isolated parallel groups" is
members of ONE session sharing ONE plan branch. This is the WHOLE PLAN running on
its own branch, in its own worktree, from `begin` to a landed (or abandoned) end.
Full authority: `references/plan-isolation-contract.md`. Read it — and
`references/failure-modes.md`'s "Land failures" / "Cleanup and abandon failures"
sections — before touching any of this by hand.

**The lifecycle, one line per stage:**

1. **`begin`** — on a plan at `plan_schema_version >= 7` (or an explicit
   `--isolate`), creates ONE branch (`plan/<slug>`) and ONE **locked** worktree
   (`.plan-worktrees/<slug>/`) off a pinned base. Every session in the plan
   dispatches inside that worktree from here on.
2. **Sessions** — the normal dispatch loop (above), unchanged, just running
   inside the plan's own worktree/branch instead of the shared checkout.
3. **`land`** — once every session is terminal, the dispatch loop's `land`
   action (documented above in step 1 of "The dispatch loop") runs the whole
   protocol: sync, merge, re-gate, a human ack, then push.
4. **Cleanup — `land`'s own LAST step, not a separate command.** Once the push
   and the plan's final record both succeed, `land` unlocks, removes and prunes
   the plan worktree, then deletes the plan branch — local AND remote. A
   `landed` result carries a `cleanup` object (`worktree`, `branch_local`,
   `branch_remote`) so you can see exactly what happened rather than assume it.
   A branch is deleted only at a tip proven to be on the landed commit; any
   doubt keeps it as `{"kept": "<reason>"}`, and `finish` retries a kept
   remote branch once per finish run that reaches its leftovers step. A retry that finds the branch `not-pushed` is recorded as `already-gone`. If it is still kept after a finished apply-mode `finish`,
   `land` and the `finish` brief give the exact `git push
   --force-with-lease=refs/heads/<branch>:<tip> <remote> :refs/heads/<branch>`
   to run yourself, not another retry. It names the `git fetch` and the
   `git merge-base --is-ancestor` check to run first. If the tip cannot be read
   it gives `git ls-remote` as the first step, and if the branch is already gone
   it says so and gives no command.
   A dirty plan worktree PRESERVES rather than loses content and is reported on
   the SAME `landed` result (the land already succeeded — this is never a
   park); clean it and call `land` again, cleanup retries and is idempotent.
5. **Abandon, instead of landing — `PYBP retire-plan <dir> [--reason "..."]`.**
   The operator ends the plan without landing it. Marks the plan retired
   unconditionally, removes the worktree ONLY when clean (a dirty one is
   preserved and its path reported, never forced), and ALWAYS keeps the
   branch, local and remote — an unlanded branch is the only copy of work
   nobody merged, and deleting it is a decision only an operator makes,
   naming the branch, never a lifecycle transition this command makes on its
   own.

**Registry commands** (repo-wide, no `<plan-dir>` — they scan `_plans/` under
cwd):

- `PYBP plans-status [--json]` — every plan's lifecycle (`active` /
  `stale-lock` / `done` / `unknown`), joined against its `plan/*` branches and
  git worktrees, each classified `active` / `leftover` / `unknown` / `conflict`.
  After a landed-and-cleaned plan, or an abandoned plan whose worktree came out
  clean, that plan shows no `plan/*` branch or worktree row at all — there is
  nothing left to classify.
- `PYBP plans-sweep [--apply] [--json]` — reports (and, with `--apply`, removes)
  dead `.lock` files plus LEFTOVER `plan/*` branches/worktrees a DONE plan left
  behind. `unknown`/`conflict` are always left alone — never guessed at.

**The version gate.** See "Preconditions" below for exactly which schema version
turns this on and how `--isolate`/`--no-isolate` override it.

**The deploy seam — read this before editing `plan-execute`'s own source.**
`/plan-execute` runs from the DEPLOYED tree (`~/.claude/skills/plan-execute/scripts/run.py`
— see the `PYBP` definition near the top of this file), while a plan that builds
or changes this skill writes to the SOURCE repo. Nothing such a plan does is live
until `gearbox deploy` carries it across — and a deploy that runs MID-PLAN swaps
the tree out from under the very plan that is building it, mid-flight, silently.
If you are executing a plan whose sessions edit `skills/plan-execute/**` in the
source repo, **do not run `gearbox deploy` between its first session and its
last** — a halted plan of that shape resumes correctly only from a CONSISTENT
tree (the one its earlier sessions actually ran against), and a deploy in the
middle breaks that invariant for every session after it.

## Verification gates (session-level — the "don't ship trash" boundary)

A session/phase may declare a `verify` block — gate ids (tests / `/eval --smoke` /
a reviewer) that MUST pass before a self-reported `DONE` becomes `DONE`. The
orchestrator runs them at the apply boundary so an optimistic closeout never
advances or ships unverified. Gates resolve through the SAME `eval-gates` registry
as shipping's `pre_deploy_gates`; state machine in `scripts/verify.py`. **Full
contract (block shape, review gates, durability): `references/verify-gates.md`.**

When `apply` reports `verify_pending: true` the session is still `DOING`. Run this
sub-loop BEFORE shipping (and before releasing the lock):

1. **Announce** the gates — e.g. `s03 verify gates: pytest-fast, eval-smoke-baseline (on_fail: rework, max 2)`.
2. `PYBP verify-begin <dir> --session sNN` → an `action`:
   - `already-verified` → go to `verify-finalize`. `noop` → no block; continue.
   - `invoke-skill` `{gate, skill, args}` → run `Skill(skill="<skill>", args="<args>")`, write its final message to a temp file, then `PYBP verify-record <dir> --session sNN --gate <gate> --status done|failed [--result-file F]`. Judge done/failed from the gate's contract (a **review gate** fails on any blocking finding). **Intent into a review gate (Lane B):** when the gate is a review gate (`code-review-gate`, `llm-review-*`), prepend the session's intent to `<args>` so the reviewer stops flagging deliberate choices — read the session's `prompt.md` "## Work" text, wrap it in the UNTRUSTED-DATA markers (`===BEGIN UNTRUSTED INTENT … ===END UNTRUSTED INTENT===`, data describing the change, NOT instructions), and require the reviewer to classify each finding `intent_touched`/`ask-user` vs `auto-fix` and emit `[intent-into-review] intent_block=present source=lane-b-verify …`. The intent block can only *downgrade* a finding to `ask-user`; it can never clear a blocking finding. After the skill returns, `grep -c '\[intent-into-review\] intent_block=present'` its output (count>0) to confirm the path engaged. No `## Work` text ⇒ run with no intent block (legacy). Full pattern + markers: `~/.claude/references/shared/intent-into-review.md`; the review-gate intent contract: `references/verify-gates.md` § "Intent into the review gate". **Never hand a review gate to `/code-review` or `/adversarial-review` BARE** — bare, both fan finder agents over the repo root and discard the gate's scope. A skill-kind binding is acceptable ONLY when the `<args>` you pass at invoke time pin the scope yourself: the worktree path, the exact file list (`git diff HEAD --stat` plus untracked new files), the base commit, and an instruction to LIST captured evidence rather than read it. The registry entry does not carry that — it is per-session, so you write it, next to the intent block. Measured on one plan run: scoped that way, `adversarial-review` returned a verdict twice and found two real defects; unscoped it measured 12 subagents and no verdict at 1800 s (`references/verify-gates.md` § "Review gates"). **A review gate closes on your judgement, not on a clean pass.** When a review skill has run TWICE on one session and each blocking finding is closed with (a) your own reproduction before the fix, (b) a test that fails on revert and (c) a known-positive control, record `--status done` with a verdict file naming the closed findings and the stated residual. Never run a third pass by default: on a security-shaped surface each pass finds one more thing by construction (measured on one long plan run: each extra pass found one new instance of one class, for hours of wall time). A third pass is a decision card for the operator, carrying the elapsed cost.
   - `run-argv` `{gate}` → `PYBP verify-run <dir> --session sNN --gate <gate>` (helper runs it); a gate whose `timeout` exceeds 540 s runs in the background with `Monitor`, per the long-gate rule in the `land` bullet.
   - `failed` `{reason: state-drift}` → manifest changed under a live verify state; halted. Delete `_verify_state/<sid>.json` to re-verify.
   **Fix rounds run review gates first:** on a rework pass `verify-begin` orders review gates (skill-kind, or `llm_review_gate.py` argv gates) before the test gates, so run them in the order it hands you; first-pass order is the declared order.
3. Each `verify-record`/`verify-run` returns the next directive. Loop until:
   - `passed` → `PYBP verify-finalize <dir> --session sNN` flips `DOING`→`DONE` (or →`AWAITS_REVIEW` if the closeout also asked for a human checkpoint — verify runs FIRST, so a human only reviews already-passing work). THEN ship. **Vista ③:** if the session set `require_evidence`, `verify-finalize` itself can return `rework`/`halted` with `gate: "evidence"` when a declared `evidence` path is missing or empty — handle those exactly like a gate `rework`/`halted` below. A `done` result means gates AND evidence both cleared.
   - `rework` `{gate, attempt, max_rework, feedback_file}` → session is `PARTIAL`; **re-dispatch it** — that means `begin --sessions sNN` again, then dispatch what it returns — appending `feedback_file`'s contents under a "## Verification feedback" heading. Bounded by `max_rework`. **The mechanism holds you to this:** `apply` refuses a rework closeout whose dispatch did not pass through `begin` (`failure: undispatched-rework`), and `verify-begin`/`verify-run` refuse to re-run gates on the closeout that already failed (`action: stale-closeout`, exit 2) — one closeout, one verdict. Never fix the findings inline and re-run the gate: a rework that no `begin` dispatched ran at the authored cell no matter what the ladder had bought (measured: 11 armed records, 0 climbs). **UPWARD ESCALATION (ESC-02, plans stamped `plan_schema_version` ≥ 6).** When the SAME root-cause signature fails twice the stuck protocol arms, and the NEXT rework runs ONE RUNG UP the SSOT's ladder — `sonnet@high → opus@high → opus@xhigh → fable@medium → fable@high → fable@xhigh` (opus@xhigh is the v1.20 escalation-only rung), then it stops. **A codex-backed session climbs too (ESC-03), on its OWN provider's ladder** — `gpt-5.6-luna@max → gpt-5.6-terra@max → gpt-5.6-sol@xhigh → gpt-5.6-sol@max`, then it stops; the climb never crosses into a Claude model, and only the `-m` and `-c model_reasoning_effort=` flags of the re-derived `codex exec` command change. Each further same-signature failure takes one more rung; a DIFFERENT error is progress, so it takes no rung — and it does not give one back either, the session HOLDS the rung it reached. Sessions that never climb: one pinned to a non-tier `dispatch.subagent_type` (Claude lane only — on the Codex lane that field is a Claude agent definition that never applied), and one declaring `model` without `reasoning` (no rung to climb from) — each is said out loud on stderr rather than announced as a climb that then does not happen. You do NOT choose the rung: `begin` computes it and returns the escalated `model_arg`/`reasoning`/`subagent_type` plus `escalated_from` `{authored, ran, attempt, rung, generation}`; dispatch exactly what `begin` hands you and copy `escalated_from` — **and the member's `model_ran` / `reasoning_ran` / `model_ran_source`** — into the closeout you give `apply` (the upward mirror of `degraded_from`). All four travel together; the routing outcome ledger reads them as one record, and a missing `model_ran_source` there is indistinguishable from an attested one. The rework payload's advisory `escalation_next` tells you what to ANNOUNCE — **name-first,
one line, exact form**: `"rework attempt <N>: escalating <session title> (sNN) to <model> -
<reasoning> per SSOT ladder"` (e.g. *"rework attempt 3: escalating tighten the S16 gate
(s03) to Fable - medium per SSOT ladder"*) — never a bare "escalating" with no session
identity, and never buried mid-paragraph. **Clean brief:** an escalated attempt's prompt is the ORIGINAL session prompt plus this one bounded feedback file — never a prior attempt's transcript. **If the escalated dispatch is REFUSED** (a real dispatch error you observed, or a `CODEX-DISPATCH-FAILED` wrapper reply), report it with `PYBP record-refusal <dir> --session sNN --model <M> --reasoning <R> --reason "<what you saw>"` — passing the pair `begin` DISPATCHED, which on a codex member is its `codex_model`/`codex_effort`, not its manifest token; the session then re-dispatches at the PREVIOUS rung, the refusal is not charged against `max_rework`, and that rung is never proposed again this session. Never infer a refusal from anything else. `model_ran`/`reasoning_ran` carry `model_ran_source: "requested"` — what was ASKED for, not proof of what served it.
   - `indeterminate` `{gate, feedback_file, hint, rework_count, max_rework}` → the gate exited its declared `indeterminate_exit` (see `references/verify-gates.md`): it could not DECIDE — a review gate that timed out, or returned no parseable verdict. **Neither a pass nor a failure: nothing was reviewed.** The gate stays `pending` and `rework_count` is UNCHANGED — charging the agent's budget for the harness's own timeout is what once exhausted `max_rework` with not one finding ever raised (measured). Do NOT re-dispatch the session; fix the cause named in `feedback_file` — raise that gate's timeout FROM A MEASUREMENT, narrow the session's `review_scope` to its real footprint, or run the reviewer by hand — then `PYBP verify-begin <dir> --session sNN --resume` re-runs just that gate. **`--resume` is now ENFORCED (RT-04):** calling `verify-begin` again on this same open state WITHOUT `--resume` is refused (`action: "requires-resume"`, exit 2) rather than silently re-running the gate blind — pass the flag once you've actually addressed the cause. A repeat at the same gate and level still arms the stuck protocol, so a reviewer that never answers escalates rather than looping silently.
   - `halted` `{gate, reason}` → `on_fail: halt` or rework exhausted; session `BLOCKED`, plan halted. Surface `reason` + `feedback_file`; STOP. When the budget ran out MID-LADDER, `reason` names the rung the climb reached and the rungs above it that were never tried — relay that verbatim, it is the difference between "we tried everything" and "we stopped one rung short".

**Precedence after a closeout:** human checkpoint (at apply) ▸ **verify** ▸ shipping. Verify runs only for a claimed-complete `DONE` closeout. Read-only: `PYBP verify-status`; CI smoke: `PYBP verify-simulate` (auto-passes all gates). Crash mid-verify (`DOING` + closeout + pending `_verify_state/`) → re-run `verify-begin … --resume`.

### PS-01 — structural DONE-gate + every message carries the dashboard URL

"You didn't update the plan HTML" was the single most-repeated correction across three weeks of sessions (30+ messages, escalating to profanity) — and it regressed TWICE after being "fixed" with prompt/instruction guidance alone. It is now structural, not prose: `apply` and `verify-finalize` both re-read PLAN.html from disk (never trust the in-memory string a mutation call just wrote) and refuse to let a session stand at its claimed status unless the `data-status` attribute of every item/session the closeout touched ACTUALLY changed to match, AND the dashboard's inline status-repaint script still parses (`no-undef`, same ESLint guard `plan-builder` runs before writing PLAN.html the first time). A mismatch downgrades the session to `BLOCKED` + halt with the concrete mismatch named — never a silent DONE. This PRIMARY gate is browser-free and always enforced (`scripts/structural_gate.py`). A SECONDARY, best-effort visual check (`scripts/render_verify.py`) headless-renders PLAN.html and reads the in-page layout-audit banner; when no headless Chrome is available on the host it reports `unavailable — structural gate passed, visual unconfirmed` rather than silently granting DONE or hard-stranding the session. That gate closes the *status* half. The *membership* half is `containment` (same module), carried on every gate result: item `data-cat` vs its enclosing `<section data-cat>`, the `session-strip` chip set, the `N sessions · M items` blurb, the infographic const array the progress bar counts through, session document order (what "Up next" scans), header totals, manifest⇄article membership, and `parallel_group` depends_on symmetry. It is WARNINGS here and a REFUSAL on the mutation path — see "Changing the plan mid-run". Every `PYBP` command's JSON output (status, plan, apply, checkpoint, verify-*, ship-*) now carries an absolute `plan_url` (`file://…/PLAN.html`) — announce it at every checkpoint and completion so the dashboard's location is never something you have to hunt for or re-derive.

### PS-02 — eval-deliverable contract (eval-bearing sessions)

A session whose deliverable IS an eval/assessment result (gate ids like `eval-smoke-*`, or a `human_summary`/`deliverable` describing an evaluation, benchmark, or audit) must NOT close with narration alone or a bare multi-page HTML dump — mirrors the global CLAUDE.md Behavior rule. It ships exactly two things: one rendered one-pager (PNG/PDF/Artifact, data inline) and a decision card of at most three options. If the session's closeout doesn't obviously carry both, ask for them before treating the session as done — the angriest eval sessions in this project's own history were exactly the narration-wall and the maze-of-pages shapes, never the one-page-plus-bounded-choice shape.

## Shipping actions (post-session version-control + deploy)

A plan may declare a per-session (or phase) `post_session` block: commit / push /
open-PR / pre-deploy gates / deploy. Plan-declared shipping is **pre-authorized** —
you do NOT re-ask. The state machine is `scripts/shipping.py`; you invoke the actual
skills, exactly like you dispatch Tasks. A human checkpoint and a verify gate BOTH
outrank shipping.

**When `apply` returns a non-null `post_session`, read
[references/shipping-actions.md](references/shipping-actions.md)** — the `ship-begin`
→ `ship-record`/`ship-run` → `ship-finalize` sub-loop, every action verb, the
announce-before-you-ship rule, registries, safety guarantees, `run.ndjson` events, and
shipping recovery (`state-drift`, `deploy-auth-stale`).

## What the helper guarantees (so you don't have to)

- **Atomic, idempotent HTML writes.** Each `<article>` is bounded by `<!-- ARTICLE:<id>:BEGIN -->` / `:END` anchors; `apply` rewrites the whole block in one temp+rename. Re-running `apply` for a session already replayed is safe (the closeout record is marked `replayed`).
- **Write-ahead durability.** The verified closeout JSON lands in `_closeouts/<sid>.json` *before* any HTML edit. If an edit pass dies mid-way, the record survives and a re-run replays it.
- **Semantic verification (H2).** `apply` rejects closeouts claiming items outside the session's scope, double-counting (completed ∩ blocked), or `result=DONE` without full coverage. These set BLOCKED + halt rather than corrupting state.
- **Closeout extraction hardening (P4).** Only the LAST closeout block is taken; a block inside a ``` markdown fence is ignored; the block must be the message's last content; the `session` field must match.

## Autonomous mode (`--auto`) — stop babysitting, keep the human gates

`--auto` changes how YOU self-drive between halts; it does **not** change the dispatch
decision. It removes the *incidental* pauses (advisory `dispatch_next: false` hints,
verify `rework` re-dispatch, the gap between batches) and **never** the deliberate
ones — `requires_human_checkpoint`, a closeout's `human_checkpoint_reason`,
a `plan_impact` **REPLAN** park, `BLOCKED`/`halted`, and `deploy-auth-stale` all still
halt exactly as they do without it. The human boundary is sacrosanct by construction: autonomy is granted per-gate by
the plan AUTHOR choosing a verify gate over a human checkpoint.

**When the user passes `--auto`, or a gate/deploy will hold the loop open long enough
that idle-blocking matters, read
[references/autonomous-mode.md](references/autonomous-mode.md)** — the full
override/never-override lists, per-gate notify-and-continue (OR-03), and the three
endurance patterns (background+`Monitor`, `ScheduleWakeup` soak waits, cloud Routines).

## Status semantics

| Status | Meaning |
|---|---|
| `TODO` | Not yet dispatched |
| `DOING` | Subagent in-flight, OR verify gates running. Set by `begin`; cleared by `apply` — UNLESS a `verify` block keeps it `DOING` until the gates pass (then `verify-finalize` clears it). |
| `DONE` | Fully complete |
| `PARTIAL` | Some items done; session needs another dispatch to finish |
| `AWAITS_REVIEW` | Human checkpoint — halts the loop. **Pre-dispatch** gate (session never ran) → `--resume`; **post-session** park (session closed DONE) → `ack-checkpoint`. See below. |
| `BLOCKED` | Halts the loop; needs investigation |
| `DEFERRED` / `WONTFIX` | Terminal, skipped by the loop |

### The two flavors of `AWAITS_REVIEW` — and which command clears each

| Flavor | How it got there | Clear it with |
|---|---|---|
| **Pre-dispatch** | The manifest's `requires_human_checkpoint` gate; `checkpoint` parked a session that has **not run**. The gate asks *should this start?* | `--resume` — dispatches it for the first time |
| **Post-session** | The session **ran and closed `DONE`**, then `apply`/`verify-finalize` parked it on its closeout's `human_checkpoint_reason`. The gate asks *do you accept this result?* | `PYBP ack-checkpoint <dir> --session sNN` |

`plan`/`status` tell them apart from **recorded state** — a persisted
`_closeouts/<sid>.json` carrying `result: DONE` — never from prose, and default to
**pre-dispatch** when no closeout is on disk (never silently ack work whose record is
missing). A post-session park is excluded from the ready set even under `--resume`, so
`--resume` can no longer re-dispatch finished work; `plan` returns `action: checkpoint`
naming the ack command instead, and every action carries `ack_required: [sNN]` so a
pending ack cannot go unnoticed.

```
PYBP ack-checkpoint <dir> --session sNN [--note "..."]
```

Flips the session to `DONE`, logs `checkpoint_approved` to `run.ndjson`, and re-reads
PLAN.html to confirm the flip landed. **Idempotent** — a repeat on an already-`DONE`
session is a no-op success. It **refuses** on `TODO`/`DOING`/`PARTIAL` (that is the
dispatch loop's job) and on a pre-dispatch park (that is `--resume`'s job); it is not a
way to mark unfinished work complete. Then run `plan` (no `--resume`) to continue.

### Re-running a finished session — `redispatch`

When new facts invalidate a completed session's output, re-run it deliberately instead of
hand-editing state:

```
PYBP redispatch <dir> --session sNN --reason "what changed" [--allow-stale-dependents]
```

Resets the session to `TODO` (with its exclusively-owned items), **archives** its
`_closeouts` / `_verify_state` / `_shipping_state` records under `<sid>.rN.<ext>` — moved,
not copied, so a stale "already verified" record cannot let the re-run skip its own gates —
and logs a `redispatch` event carrying the reason.

`--reason` is **required**: re-running throws away a recorded result, and the plan's
history is the only place the *why* survives. It refuses on any session that is not
finished (`DONE`/`WONTFIX`/`DEFERRED`/`BLOCKED`) — a session parked at `AWAITS_REVIEW` is
resolved with `ack-checkpoint` first.

**Cascade is the default.** A session is redispatched precisely *because* its output no
longer holds, so every dependent that consumed it (transitively) is stale by construction;
leaving them `DONE` would let `--resume` skip them and complete the plan on stale work.
Dependents at `DONE`/`AWAITS_REVIEW` are reset too. `DOING` is never yanked out from under
an in-flight dispatch, and `WONTFIX`/`DEFERRED` are operator decisions, not stale results —
both are reported under `dependents_untouched` instead. `--allow-stale-dependents` keeps
the dependents as they are and records that choice as a `stale_dependents_accepted` event,
so accepting stale work is auditable rather than silent.

### Changing the plan mid-run — `add-session` / `amend-session` / `retire-session`

A plan that discovers new work mid-flight used to have exactly one path: hand-editing
`PLAN.html` and `manifest.json`. On the COS Ingestion II run six sessions were added that
way and nearly every pass introduced a different defect — articles appended outside their
section, invented `data-cat` values, a stale nav strip, stale header counts — and **every
one passed the structural gate**. Use these commands instead; never hand-edit a plan.

```
PYBP add-session    <dir> --id s21 --title "…" --depends-on s20 --model Opus \
                          --reasoning high --task-class agentic_build --gates pytest-brain \
                          --new-item '{"id": "ext-07", "category": "extraction",
                                       "title": "Backfill",
                                       "prior_art": {"decision": "build",
                                                     "source": "what you checked"}}'
PYBP amend-session  <dir> --session s21 --depends-on s19 --prompt "…" --model Opus
PYBP retire-session <dir> --session s21 --reason "…" [--cascade | --drop-dependency]
```

A new item is passed as **JSON**, not `id|category|title`: every plan the builder stamps
today requires `prior_art` (decision + source) or `research_status` on every item, and the
pipe form has nowhere to put them — so it fails validation every time (measured).
The pipe form still works on plans built before that gate.

`--task-class` is **optional** — it never blocks the add. Omit it and `add-session` prints a
stderr warning naming the consequence: this session's outcomes are excluded from routing
proposals until amended, because `task_class` is how a routing retro groups sessions by kind
of work.

**One renderer.** These commands own no HTML assembler. They mutate `spec.json` and re-run
*plan-builder's own* renderer, carrying recorded state forward with
`article_block.carry_over_state` — so article containment, the nav strip, header counts,
"Session N of M" steps and the category sections are correct by construction. A second
assembler is exactly how the hand edits diverged. `add-session` refuses an item whose
category matches no section, refuses to take an item another session owns, and refuses any
dependency cycle (`amend-session` can create one; it is refused the same way).

**One transaction.** A mutation writes six files. `temp+rename` is atomic for *one* file,
so the write is journalled into `_mutation/<txn>/`: staging first, then a single atomic
rename of `journal.json` as the commit point, then the individual renames. A crash before
the commit point leaves the plan untouched; a crash after it leaves a journal that the
**next `run.py` command of any kind replays** — rolling forward to one complete generation,
never a mix. Replay is idempotent.

**`amend-session` is TODO-only.** A `DOING`/`DONE`/terminal session was dispatched against
the old text, so amending it would rewrite history rather than change the future. Use
`redispatch` to re-run it, or `retire-session` to drop it.

**`retire-session` has a required dependent policy.** `WONTFIX` is a member of the
executor's `DONE_STATES`, so retiring a producer *satisfies* its consumers' dependencies —
they would dispatch without the input they were written to consume. So retirement refuses
while any live (non-terminal) dependent exists, direct **or** transitive, and names both
sets. Two escapes, both recorded: `--cascade` retires the whole live sub-tree, and
`--drop-dependency` keeps the dependents, removing the retired session from their
`depends_on` **and** appending a warning to their prompts in the same transaction.

**One containment gate.** "Correct by construction" is a claim about the renderer, and a
claim is not a check — so every mutation runs `structural_gate.containment_report` over the
generation it has COMPUTED, *before* the commit point, and refuses on any problem the plan
did not already have (pre-existing ones ride out as warnings, because a defect this mutation
did not cause must not strand the plan). It checks the six surfaces the 2026-08-02 hand
edits broke — item `data-cat` vs its enclosing `<section data-cat>`, the `session-strip`
chip set, the `N sessions · M items` blurb, the `const WORKSTREAMS`/`PILLARS`/… array the
progress bar counts through, session document order (what the "Up next" panel actually
scans), and the header totals — plus manifest⇄article membership both ways and
`parallel_group` depends_on symmetry (asymmetric deps make peers ready at different times,
so the batch silently degrades to sequential dispatch). Same report rides along as WARNINGS
on every `apply` / `verify-finalize`, where it is deliberately non-blocking.

**Refusals you will meet.** The halt flag; a live **dispatch lock** (a batch is in flight —
restructuring would clobber its status writes); **shipping mid-flight** (`_shipping_state`
binds to the manifest digest, so mutating now would make that run refuse as `state-drift`
and halt the plan); **recorded state mid-flight** — an unsettled `_verify_state/<sid>.json`
(no outcome yet, or `rework` awaiting the re-dispatch) or a persisted-but-unreplayed
`_closeouts/<sid>.json`, for the same digest reason; and **`builder_drift`** — if re-rendering with today's plan-builder
would change the page even with *no* mutation applied, the command prints the diff and
requires `--allow-builder-drift`, so a builder-version upgrade can never ride in
unannounced. A plan with no `spec.json` is refused outright: there is no second renderer.

Every mutation appends one line to `_changelog.ndjson` (`{at, op, session, summary,
sessions_touched, items_touched}`) **and one dated row to the rendered "Plan changes" section
at the bottom of `PLAN.html`** — so the plan stays a self-contained record of why it changed
instead of pointing at a sibling file. `redispatch` and a REPLAN resolution write there too.
Plans built before the section exists have no anchors for it; every writer is a tolerant
no-op there, and the record still lands in `_changelog.ndjson`.
A new item is also placed in a Plan Achievement group (defaulting to the group named after
its category, `--infographic-group` to override); when nothing matches, the containment gate
REFUSES the mutation and names the flag to re-run with, rather than shipping a progress bar
that counts a short denominator (the 2026-08-02 bar read 92% · 12-of-13 against a real
70% · 19-of-27).

Settled verify state (`passed`/`halted`) is **migrated forward onto the new manifest digest
in the same transaction, `rework_count` preserved** — otherwise the operator's only way past
the resulting `state-drift` refusal is to delete the file, which hands a session that had
already exhausted `max_rework` a silent fresh budget.

### The REPLAN gate — a session discovers the plan is wrong

A closeout may carry `plan_impact: {"invalidates": ["s07","s08"], "reason": "…"}` — "what I
learned means work the plan has NOT done yet no longer holds." Before this existed the
discovery went into a notes field and the plan carried on.

What happens on a `plan_schema_version >= 3` plan:

1. The ids are **validated against the manifest**. A session that is in no manifest, or the
   reporting session naming itself, is a closeout **schema error** — BLOCKED + halt, never a
   silent drop.
2. The session keeps its own result (a REPLAN is a fact about the PLAN, not a failure of the
   session that found it), and the plan is **halted with `kind: "replan"`**. The reporting
   card and every invalidated card get a note.
3. `plan` returns `action: "replan"` with a brief: the reason, the invalidated sessions with
   their titles and statuses, exactly three options (**amend / retire / proceed**), and an
   empty `recommendation` slot. **Present it verbatim with your recommendation filled in.**
4. Apply the chosen change with `amend-session` / `retire-session` / `redispatch` — those are
   let through a replan halt, because they are its cure. Then record the decision:
   `PYBP resolve-replan <dir> --session sNN --decision amend|retire|proceed --reason "…"`.
   It appends one change-log row and clears the halt.

`amend` and `retire` are **refused** while the change log shows nothing touching the
invalidated sessions since the park — the decision cannot be a rubber stamp. `proceed` needs
no mutation; it is the honest "they still hold" answer, and it is recorded.

**Precedence.** A closeout carrying BOTH `human_checkpoint_reason` and `plan_impact` parks on
the human checkpoint FIRST; `ack-checkpoint` then raises the REPLAN. A session with a
`verify` block parks at `verify-finalize`, after its gates pass. Deferred, never dropped —
`apply` reports it as `replan_deferred`.

Plans stamped below schema v3 ignore the field entirely: no validation, no park.

### Halt is guarded on every door

`begin`, `ack-checkpoint`, `redispatch` and the three mutation commands all refuse while
the halt flag is set — not just `plan`. (Until 2026-08-12 only `plan` checked it, so the loop's own next step could
dispatch and mutate `TODO`→`DOING` straight through a halt.) Clear it with `--clear-halt`
after fixing the cause, or pass `--resume` to `begin` for the same deliberate override
`plan --resume` already is.

The ONE flavored exception is a **REPLAN** halt (`halt.kind == "replan"`): the mutation
commands and `redispatch` pass through it, because restructuring the plan is exactly the
answer that gate is asking for. `plan` and `begin` still refuse — advancing is what a REPLAN
park stops — and `resolve-replan` clears it once every park is answered.

### Terminally-failed dependencies — `depends_on_policy`

A session's `dispatch.depends_on_policy` (written by plan-builder into every manifest)
decides what happens when an upstream dep **terminally fails**:

- `"all"` (default) — hard AND: every dep must be `DONE`/`WONTFIX`/`DEFERRED`.
- `"completed_or_terminal"` — dispatches over the **completed subset** once every dep has
  stopped moving, `BLOCKED` included. This is what stops a capstone/acceptance session from
  being stranded forever by one upstream that closed `BLOCKED`; such a session's prompt is
  expected to say how it degrades.

The plan-level stop-the-world rule is unchanged: a `BLOCKED` session still surfaces
`action: blocked` first, so the capstone dispatches on the operator's `--resume` — never
silently past a failure nobody looked at.

## Failure handling

| Symptom | What `apply` does | What you do |
|---|---|---|
| No closeout block | session → BLOCKED + halt; `failure: missing` | surface tail of subagent output; STOP |
| Malformed JSON / trailing text | session → BLOCKED + halt; `failure: json_error` | surface diagnostics; STOP |
| Schema / semantic violation | session → BLOCKED + halt; `failure: schema_error`/`semantic_error` | surface the named violation; STOP |
| `result: BLOCKED` | session → BLOCKED + halt | surface the subagent's reason; STOP |
| `result: PARTIAL` | completed items → DONE, session → PARTIAL (loop will re-dispatch it next time) | continue or stop per user |
| `human_checkpoint_reason` set | session → AWAITS_REVIEW (after verify, if any) | present the reason as a plain-language decision (the contract requires the subagent to state what's being decided — surface it verbatim plus any context the user needs); approve with `ack-checkpoint` — NOT `--resume`, which would re-run finished work |
| `plan_impact` set (schema v3+) | items/session applied as normal, then the plan → **halted, `kind: replan`** with a decision brief | present the brief with your recommendation; apply the pick via `amend-session`/`retire-session`/`redispatch`, then `resolve-replan` |
| verify gate failed, rework budget remains | session → PARTIAL; `feedback_file` written | re-dispatch with the feedback appended (automatic in `--auto`); bounded by `max_rework` |
| verify gate failed, `on_fail: halt` or rework exhausted | session → BLOCKED + halt | surface the gate + `feedback_file`; STOP |

Semantic/blocked failures **never** auto-retry. Re-dispatch only after the
human investigates (and clears halt if needed). The ONE bounded exception is a
**verify rework** loop (`on_fail: rework`, capped by `max_rework`): a failed
verification gate re-dispatches the same session with the gate output attached —
this is the intended "make the tests pass" loop, not an open-ended retry.

## Transport-error auto-retry (OR-01, added)

A **second, separate** bounded exception from verify-rework: a `Task` dispatch that
fails with a **transport-layer** error (connection refused, 529/overloaded, timeout,
a fresh connection dropped with zero output) is NOT the same failure class as a
semantic refusal or a bad closeout — the model never got to run. Auto-retries up to
3x with full-jitter exponential backoff (capped ~90s total) UNLESS the session already
crossed its commit boundary (PLAN.html applied, a `post_session` git commit landed, an
MCP write fired) — past that point a connection drop is ambiguous about whether the
side effect landed, so it always surfaces instead of retrying.

Full classifier taxonomy (retryable/ambiguous/semantic), the `transport.decide()`
contract, the commit-boundary gate mechanics, and subagent-death checkpoint
preservation: `Read ~/.claude/skills/plan-execute/references/transport-retry.md`. The
canonical classifier logic lives in `scripts/transport.py`'s module docstring; this
skill and `epic-dev-conductor` both call the same module — one source of truth for
transport-error handling across both orchestration loops.

## Crash recovery

**Turn-end guard (Stop hook, added).** `hooks/turnend-guard.py` refuses to let
the orchestrator end its turn while a plan THIS session dispatched still has a session
`DOING` with no `_closeouts/<sid>.json` and no dispatched agent, workflow or background
command is still running (it reads the transcript's `<task-notification>` records). It
blocks at most 3 consecutive times per finding, then lets the turn end. It is the
structural form of "before ending your turn, do that work now"; the wiring is a `Stop`
entry in `settings.json`.


If a session is stuck in `DOING` with no `_closeouts/<sid>.json`, the subagent
crashed or never returned — re-run `/plan-execute <dir> --session sNN`. If a
`_closeouts/<sid>.json` exists with `replayed: false`, a prior run captured the
closeout but died before finishing the HTML edit — `apply` is safe to re-run and
will complete the mutation.

**Verify recovery.** A session stuck in `DOING` WITH a replayed `_closeouts/<sid>.json`
AND a `_verify_state/<sid>.json` whose `outcome` is not `passed` is a verify that
was interrupted — re-run `PYBP verify-begin <dir> --session sNN --resume` to
resume the gates (passed gates are not re-run). **`--resume` is REQUIRED here
(RT-04):** calling `verify-begin` on this same open state without it is refused
(`action: "requires-resume"`, exit 2) instead of silently re-advancing — the flag
is the deliberate acknowledgment that this call is a resume, not an accidental
re-invocation. This refusal fires ONLY on re-entering existing open state; a
brand-new verify pass and the documented post-rework-redispatch re-entry (a
FRESH closeout was just applied) both need no flag. A `state-drift` refusal means
the manifest changed since the verify state was written (e.g. a rebuild); decide
whether the prior verification still holds, and delete `_verify_state/<sid>.json`
to re-verify from scratch.

**Shipping recovery.** If `_shipping_state/<sid>.json` shows a `failed` step, the
plan halted mid-ship. Fix the root cause (the failed step's `stderr_excerpt` is
recorded, redacted), `--clear-halt`, then re-run — `ship-begin` resumes at the
failed step; finished steps are skipped, so no duplicate commit/deploy fires. Two
shipping-specific halt reasons need operator judgement, not a blind re-run:

- **`state-drift`** — the manifest or closeout changed since the shipping state was
  written (e.g. the plan was rebuilt). The helper refuses rather than skip a
  now-stale `deploy: done`. Decide whether the prior shipping is still valid; if
  so, delete `_shipping_state/<sid>.json` to re-ship from scratch.
- **`deploy-auth-stale` (later-invalidation)** — an intervening session changed
  what this deploy ships. The `rollback_hint` for the prior deploy is surfaced.
  Confirm the deploy is still correct, then re-run with `--resume`/`--confirm-stale`.
  Rollback execution itself stays manual.

## Learning capture (closes the loop into project memory)

Run ONE learning-capture pass when the loop ends — on `complete`, and on a **terminal
halt** (`blocked`/`halted`, including rework exhaustion or a failed shipping step). It
is orchestrator work (you), never a subagent's, and it is the write side of the loop
plan-harden's Fork A reads when hardening the NEXT plan.

**At that point, read
[references/learning-capture.md](references/learning-capture.md)** — the four-step pass
(gather → generalizability gate → grounding rules → overlap check) and the five memory
maintenance outcomes. Zero qualifying learnings ⇒ write nothing.

## Preconditions

- The plan must be schema-version ≥ 2 (`plan_schema_version` in manifest.json; also stamped as `<meta name="plan-schema-version">` in PLAN.html). v1 (Cowork-era) plans are refused — rebuild via `/plan-builder --rebuild`. **plan-builder stamps 7 since 2026-08-23** (`build_plan.PLAN_SCHEMA_VERSION`); v2 plans run exactly as before, and the version-gated features (`plan_impact` / the REPLAN gate at v3+; the mandatory BLOCKED `decision_brief` at v5+; **UPWARD ESCALATION of a repeatedly-failing session at v6+**, `escalation.ESCALATION_MIN_SCHEMA`; **PLAN-LEVEL GIT ISOLATION at v7+** — own branch, own locked worktree, and the `land` protocol instead of a commit in your checkout, `plan_version_gate.ISOLATION_MIN_SCHEMA`) simply do not apply to them. That is deliberate: a plan built before a feature must never gain a new refusal mode retroactively.
- **Plan-level git isolation is gated at v7 (`plan_version_gate.ISOLATION_MIN_SCHEMA`) and is LIVE for plans built since 2026-08-23** — the builder stamps 7, so every NEW plan gets its own branch and its own locked worktree at `begin` and reaches the default branch through `land`. Every plan already on disk is below the gate and runs in the shared checkout exactly as before (re-measure at the bump: the count of manifests scanned matters as much as the crossing count, because a sweep that read nothing also reports 0). `begin --isolate` forces it on for one run and `begin --no-isolate` forces it off, `--no-isolate` winning; both are logged to `run.ndjson` as `plan_isolation_gate` with the manifest's actual version. **Two isolated plans may run in one repo with no `--concurrent`** — they share no working tree, so REG-02's refusal has nothing to refuse; it still fires, and still needs the override, when any active neighbour is below the gate or was begun `--no-isolate`. The activation receipts are `_evidence/s10/two-plan-e2e.json` and `_evidence/s10/canary-real-plan.txt`. See "Plan-level git isolation" above for the full lifecycle, and `references/plan-isolation-contract.md` for the contract.
- Run from inside the project (so relative `<plan-dir>` resolves). The helper also accepts an absolute path.
- Do NOT invoke `/plan-execute` from inside a dispatched subagent. It is a main-conversation orchestrator only.

## References

- `references/closeout-contract.md` — the closeout JSON shape with worked examples.
- `references/plan-isolation-contract.md` — the single statement of record for PLAN-LEVEL git isolation: begin's branch+worktree, the land protocol (§4/§5), and the cleanup/abandon lifecycle (§15) `retire-plan` implements. Read before touching any of "Plan-level git isolation" above by hand.
- `references/route-at-dispatch-contract.md` — the single statement of record for schema v8: who picks the model and effort, overrides and the floor, `verify.locked`, per-attempt usage, and the off switch. Read before touching any of "Route at dispatch" above.
- `references/failure-modes.md` — every failure state, what it looks like, and recovery. Its "Land failures" and "Cleanup and abandon failures" sections cover this same lifecycle.
- `references/shipping-registries.md` — deploy-target + eval-gate registry shapes (skill-kind vs argv-kind) and why they're project-local. The SAME `eval-gates.json` registry powers session `verify` gates.
- `references/verify-gates.md` — the session verification-gate contract: the `verify` block, the verify sub-loop directives, review gates, and the rework/halt semantics.
- `references/autonomous-execution-primitives.md` — verified (2026-01) contracts for the Claude Code loop primitives this skill builds on (fork vs fresh agent, Workflow seam, ScheduleWakeup, background tasks) with the gotchas that shaped the design.
- `references/parallel-group-contract.md` — **the single statement of record for `parallel_group`**: which isolation mechanism is legal (orchestrator-managed `git worktree add`) and which is barred by name (the Agent tool's own `isolation: "worktree"`, measured destroying agent output), the member bans (no commit/push, no dependency/lockfile change, no overlapping writes, `touches` mandatory), the declared integration session, and the group lifecycle. Enforced by `scripts/parallel_contract.py` at BOTH build time and dispatch time. Read before touching parallel dispatch, and before believing any other doc about worktree isolation. The MECHANISM that carries out §3 rules 5-7 and §4 is `scripts/worktree.py` (wired into `run.py` `cmd_begin`/`cmd_apply` and `verify.gate_cwd`); its executable proof is `fixtures/worktree-parallel/run.sh`.
- `references/shipping-actions.md` — the `post_session` shipping sub-loop: every action verb, registries, safety guarantees, events, and shipping recovery. Read when `apply` returns a non-null `post_session`.
- `references/autonomous-mode.md` — `--auto` semantics (what it skips, what always halts), per-gate notify-and-continue, and the endurance patterns. Read when `--auto` is passed or a long gate/deploy is in play.
- `references/learning-capture.md` — the end-of-run pass into project memory, and the memory-maintenance outcomes. Read on `complete` or a terminal halt.
- `references/dual-harness-contract.md` — the ADR settling how ONE plan directory runs from Claude Code **or** Codex CLI: the `--harness codex` flag, the Codex-side shell dispatch loop (no Task tool), SSOT tier translation with a fidelity receipt, which safety gates hold inside the Codex harness, and the single-source skill layout. Read before touching Codex-side dispatch; every claim in it cites a probe.
