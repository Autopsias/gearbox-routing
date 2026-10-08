# Failure Modes & Recovery

Every failure halts the loop loudly rather than corrupting state silently. The
halt flag lives in `run_state.json`; a `HALT_NOTICE.txt` is written at the plan
root, and an event lands in `run.ndjson`.

## Closeout failures (from `apply`)

| `failure` | Cause | What `apply` does | Recovery |
|---|---|---|---|
| `missing` | No `<plan-execute-closeout>` block found (or only fenced examples) | session → BLOCKED, halt set, note "closeout missing" | Inspect the subagent's output. Re-dispatch the session: `/plan-execute <dir> --session sNN` after `--clear-halt`. |
| `json_error` | Block found but JSON won't parse, or trailing text after it | session → BLOCKED, halt set | Same — the subagent emitted a malformed block; re-dispatch. |
| `schema_error` | JSON parses but wrong shape (missing field, bad `result`, non-string ids) | session → BLOCKED, halt set, violations listed | Re-dispatch; if it recurs, the prompt may be mis-teaching the format. |
| `semantic_error` | Hallucinated item id, completed∩blocked overlap, DONE without full coverage, or a malformed/unknown-id `plan_impact` on a v3+ plan | session → BLOCKED, halt set, violation named | Investigate whether the subagent actually did the work; re-dispatch or hand-correct. A `plan_impact` naming a session that is in no manifest is a subagent error, never something to quietly drop. |

When a batch member fails, **finish applying the other members' closeouts first**
(so their work is recorded) — then stop. The halt prevents the *next* batch, not
the consumption of in-flight siblings.

## Result-driven outcomes (valid closeout)

| `result` | Effect |
|---|---|
| `DONE` | items_completed → DONE, session → DONE. Loop continues. |
| `PARTIAL` | items_completed → DONE, session → PARTIAL. Loop re-dispatches the session next pass to finish remaining items. |
| `BLOCKED` | items_blocked → BLOCKED, session → BLOCKED, halt set. Loop stops. |
| `plan_impact` set (schema v3+) | Session keeps its own result; the PLAN is halted with `kind: "replan"` and a decision brief (invalidated sessions · reason · amend/retire/proceed). Deferred behind a pending verify block or an unacked human checkpoint — reported as `replan_deferred`, never dropped. On schema v5+ record YOUR judgement first — `run.py recommend-replan <dir> --session sNN --recommendation "…"` — it lands in `HALT_NOTICE.txt` beside the options, and `resolve-replan` refuses without it. Then answer with `run.py resolve-replan <dir> --session sNN --decision … --reason "…"` AFTER applying the change through `amend-session`/`retire-session`/`redispatch`. |
| `human_checkpoint_reason` non-null | session → AWAITS_REVIEW, loop halts. This is the **post-session** flavor (the session already closed): approve it with `run.py ack-checkpoint <dir> --session sNN`. NOT `--resume`, which is for a pre-dispatch gate and would re-run finished work — the loop now refuses to re-dispatch it and surfaces `ack_required` instead. |

## Structural / environmental failures

| Symptom | Source | Recovery |
|---|---|---|
| `manifest/HTML mismatch` | A session in manifest.json has no matching `<!-- ARTICLE:id -->` anchor in PLAN.html (stale manifest or hand-edited HTML) | Rebuild: `/plan-builder --rebuild --preserve-state <slug>`. |
| `expected exactly 1 '<!-- ARTICLE:id:BEGIN -->'` | Anchor count wrong — duplicated/deleted block | The HTML was hand-edited or a prior write corrupted it. Restore from git or rebuild. |
| `block 'id' missing data-status / pill / notes-content` | Anchor preflight: the article structure was damaged | Same as above. |
| `refusing to … this plan is HALTED — REPLAN …` | A `plan_impact` closeout parked the plan (`halt.kind == "replan"`) and you ran `plan`/`begin` | Not a failure — a decision. Read the brief (`run.py plan` prints it as JSON and as presentable text in `replan_text`; `status` carries `replan_pending`), record your recommendation with `recommend-replan`, apply the pick, then `resolve-replan`. The mutation commands and `redispatch` are allowed through this halt on purpose. |
| `plan_schema_version is None/1` | A v1 (Cowork-era) plan | Rebuild via `/plan-builder --rebuild <spec.json>`, or view read-only in a browser. v1 plans can't auto-execute. |
| lock contention | Another `/plan-execute` is in flight (or a stale `.lock`) | If stale (>1h or dead pid), the helper overwrites it automatically. Otherwise wait, or remove `<dir>/.lock` manually. |
| `refusing to acquire the plan lock … is on <FS class>` | The plan dir resolves onto a networked/sync FS (iCloud, Google Drive / OneDrive / Dropbox via Finder, NFS/SMB) where the pidfile lock is unreliable (P5) | Move the plan to local disk, or re-run `begin` with `--unsafe-lock` to override (logs a `lock_fs_warning` event and proceeds at your own risk). |

## Halt notification (opt-in, P8)

When a halt is set, if `manifest.json` carries `notify_on_halt.command` the halt
path runs it once — `shell=False`, ~10s timeout, env `PLAN_DIR` / `PLAN_TITLE` /
`HALT_SESSION` / `HALT_REASON`. It is best-effort and never blocks the halt:

| ndjson event | Meaning |
|---|---|
| `notify_sent` | Command ran and exited 0. |
| `notify_failed` | Command was missing/unparseable, timed out, raised, or exited non-zero (`returncode`/`stderr`/`error` captured). The halt is still set — only the notification failed. |

No `notify_on_halt` in the manifest → no command runs and neither event is logged.

## Crash recovery (write-ahead log)

The pipeline persists `_closeouts/<sid>.json` (verified) **before** editing
PLAN.html, and marks it `replayed: true` after.

- **Session in `DOING`, no `_closeouts/<sid>.json`:** the subagent crashed or never returned. Re-dispatch with `--session sNN`.
- **`_closeouts/<sid>.json` exists, `replayed: false`, PLAN.html not yet DONE:** a prior run captured the closeout but died before/during the HTML edit. Re-running `apply` for that session is idempotent and completes the mutation. (In practice: re-run the loop; the helper's mutations are safe to repeat.)
- **`replayed: true`:** already applied; nothing to do.

## Shipping failures (post-session actions)

Shipping runs AFTER a closeout is applied (and after any human checkpoint). Every
failure halts; shipping resumes at the failed step — it never re-runs the session
and never rolls back the already-recorded closeout.

| `reason` / `action` | Cause | Recovery |
|---|---|---|
| `failed` (`failed_step` named) | A commit/push/PR/gate/deploy sub-step failed; `stderr_excerpt` is recorded **redacted** | Fix the root cause, `--clear-halt`, re-run. `ship-begin` resumes at the failed step; finished steps are skipped (no duplicate commit/deploy). |
| `skipped` `deploy-target-missing` | The `deploy` target vanished from `.claude/deploy-targets.json` between build and run | Restore the registry entry (or remove the `deploy` from the session) and re-run. |
| `skipped` `skip-if-partial` | `skip_if_partial: true` and the closeout `result` was not `DONE` | Intentional — finish the session (`PARTIAL` → `DONE`) to ship. |
| `failed` `state-drift` | The manifest or closeout digest changed since the shipping state was written (e.g. plan rebuilt) | Decide whether the prior shipping is still valid. If so, delete `_shipping_state/<sid>.json` to re-ship from scratch; otherwise reconcile manually. The helper refuses to skip-as-already-shipped against a stale digest. |
| `confirm-required` `deploy-auth-stale` | An intervening session changed what this deploy ships, so the pre-authorization was downgraded to surface-and-confirm | The `rollback_hint` for the prior deploy is surfaced. Confirm the deploy is still correct, then re-run with `--resume` / `--confirm-stale`. |
| `failed` `adapter-contract-drift` | A skill flag the adapter references no longer exists in the skill's current interface (a plan authored week-1, run week-20 against an evolved skill) | Update `scripts/shipping_adapter.py` (or the skill) so the flag matches, then re-run. |
| `failed` `state-corrupt` | `_shipping_state/<sid>.json` is unreadable and has no usable `.bak` | Inspect / delete the corrupt file and re-run (shipping re-plans from the closeout). |

Shipping lock events (`shipping_lock_acquired` / `shipping_lock_released`,
resource-scoped: `git:`/`push:`/`deploy:`/`gate:`) bracket each session's
shipping. Orphaned locks from a dead run are reclaimed automatically (stale pid).

**CI / smoke without a live orchestrator:** `ship-simulate <dir> --session sNN`
runs the whole pipeline producing real `post_session_*` events + state but
auto-succeeds every step (no real skill invocation / command). Use it to verify
the machinery (events, badge, idempotency) deterministically.

## Land failures (contract §4/§5, s08)

Only for a **plan-level isolated** plan (`plan_schema_version >= 7` or
`begin --isolate` — see SKILL.md "Plan-level git isolation"). `land`/`land-resume`
walk the same ordered protocol every time and PARK loudly on any of these; nothing
is ever auto-resolved or force-pushed. Full authority:
`references/plan-isolation-contract.md` §4/§5.

| `kind` | Cause | What happened | Recovery |
|---|---|---|---|
| `sync-conflict` | Merging `<remote>/<default>` into the plan branch (step 1) conflicts | Nothing pushed; the merge is left in the PLAN worktree with the conflict intact — `merge --abort` is deliberately NOT run here | `cd` into the plan worktree the brief names, resolve by hand (read the WHOLE `git status`, not just the `U` lines — a modify/delete conflict stages sibling deletions with no conflict marker), commit, then `run.py land-resume <dir>`. |
| `merge-conflict` | The plan branch merges cleanly into `<remote>/<default>` in step 1 but conflicts merging INTO the detached land worktree (step 3) | Nothing pushed; the land worktree is left with `MERGE_HEAD` set and the conflict intact | Same shape: `cd` into the land worktree the brief names, resolve, `git merge --abort` only if you mean to give up (the brief prints the exact command), then `land-resume`. |
| `merge-in-progress` | A prior `land` was killed (crash, SIGKILL, OOM) mid-merge, leaving `MERGE_HEAD` set with no commit | The next `land` NAMES it rather than silently concluding a merge nobody watched | `git merge --abort` in the land worktree (the brief's own command), then `land-resume`. Nothing was pushed — a kill here cannot corrupt the operator's checkout (§4.1's lease + a worktree confine the mess to one detached tree). |
| `land-locked` | Another `land`/`begin` holds this repo's `git:<root>` lease | No git operation ran yet | **Same host, same or another process:** wait for it to finish, or confirm it is dead and remove the printed lock file. **Foreign-host lease (LCK-02):** the message names the OTHER machine's hostname, plan dir and expiry — a lease taken there is never reclaimed from here (this host cannot see that process). Clear it on that machine, or remove the lock file only once you know that run is over. |
| `default-renamed` / `remote-ambiguous` | §4.0's one-time default-branch resolution no longer matches the repo (someone renamed `main`, or `origin` was removed/replaced and no single remote is unambiguous) | Parks before touching anything | Follow the brief's exact `git remote rename`/re-check command, then `land-resume`. This is a decision (which ref is now "the" default), never a guess. |
| `push-rejected` / `resync-exhausted` | The push was refused, or 3 bounded re-syncs (§4.5) still lost the race to a competing lander | **Nothing was cleaned up.** The plan branch is KEPT, local and remote, and the land worktree is kept with the merge intact — the work exists only there. | `cd` into the land worktree, `git fetch <remote>`, then `run.py land-resume <dir>` — it re-syncs, re-gates, re-asks for the ack, and retries. Never delete the plan branch here: §2.3 deletes it only AFTER a successful push. |
| `gate-empty-surface` | The re-gate's `<expected>..HEAD` diff is empty — nothing to review | This plan has no work outside its own record (or a resume raced a push that already succeeded) | Not usually an error: check `files_examined` and confirm the plan really has code to land. If a resume shows this right after a genuinely successful push, re-run `land` once more — `already_landed` (checked against git, not the record) short-circuits straight to `landed`. |
| `gate-indeterminate` | An `argv`-kind re-gate exited its declared `indeterminate_exit` (timeout, no parseable verdict) | Neither a pass nor a fail — nothing was reviewed, and `rework_count` is unchanged | Raise that gate's timeout from a measurement, narrow its scope, or run it by hand; then `land-resume`. |
| `gate-on-box-verification` | A re-gate exited its `indeterminate_exit` **printing `VERIFIER: on_box_human`** — a POLICY refusal, not a timeout. Under the Codex harness with no `verification.claude_verifier_under_codex_harness` opt-in, running the on-box `claude -p` reviewer would send a Codex-built tree to Anthropic, so no model may read it at all | Nothing was reviewed and nothing was pushed. Told apart from `gate-indeterminate` on purpose: this one is decided BEFORE the tree is read and is identical on every retry, so **every remedy in the row above is inert here** — re-running reproduces it exactly | Review the merged tree yourself (`cd` into the land worktree the brief names, `git diff <expected>..HEAD`), then record what you found: `run.py land-verify <dir> --decision verified-on-box --note '<what you checked>'` and `land` again, or `--decision blocked --note '<why>'`. This is the LAND-scope twin of `ack-checkpoint --session sNN --decision verified-on-box\|blocked`. `verified-on-box` is recorded as its OWN gate outcome — never as a pass — so the digest your `land-ack` binds to says a human cleared that gate, and a moved candidate discards it and asks again. |
| `gate-on-box-blocked` | You answered `land-verify --decision blocked` | Nothing was pushed and nothing is claimed about the tree. No work is deleted: the plan branch, its worktree and the land worktree are all kept | Fix what you found on the plan branch, then `land` again. A new plan head is a new candidate, so you are asked afresh rather than held to the old answer. |
| a **stale ack** (no distinct `kind` — `land-awaits-review` recurs with `invalidated_prior_ack` true) | §5.1d re-compares `{plan_head, main_head, gate_digest}` against the recorded ack immediately before the merge AND again immediately before the push; a live move on ANY of the three (a peer session pushed to the plan branch, the default branch advanced, a re-gate produced a different digest) clears the ack rather than pushing "since we're already here" | The plan returns to `awaiting_review` with a FRESH candidate; the invalidated ack is carried forward as `prior` so the brief can show the human the diff against what they approved before | Read the new candidate, `run.py land-ack <dir> --note '<why>'` again, then `land`. An ack is bound to an exact triple — it is never silently reused across a moved target. |
| `land-worktree-preserved` | `step_finish`'s own teardown of the LAND (merge) worktree refused — untracked/ignored-but-created content (§12.4), most often a gate's own git-ignored artefact | **The land itself already SUCCEEDED** — the merge is on the target and the final record is published. Only the teardown parked. | `git status --porcelain --ignored=matching` in the land worktree (plain `git status` will not show an ignored file), save or discard what it finds, then `land-resume` — the push is idempotent and will not repeat. |
| a hand-deleted **locked** land worktree | The operator (or a script) `rm -rf`s a preserved land worktree directly instead of going through git | `git worktree` still has an administrative entry pointing at nothing; re-running `land` used to re-walk the WHOLE protocol against it (measured: `land-worktree-preserved` → `gate-empty-surface` forever, because a fresh re-gate against an already-landed candidate has nothing left to review) | Nothing to do by hand — `land`/`land-resume` now asks git directly "is the merge on the target?" (`already_landed`, never "does the worktree directory still exist") and short-circuits straight to `landed` when it is. Never `git worktree prune` it yourself first; there is nothing that needs pruning here that a normal `land-resume` doesn't already handle. |

## Cleanup and abandon failures (§15, s09)

**Cleanup is `land`'s own LAST step**, not a separate command: once the push and
the final record both succeed, `land` unlocks, removes and prunes the PLAN
worktree (`.plan-worktrees/<slug>/` — distinct from the LAND worktree the table
above already covers) and deletes the plan branch, local and remote. See
`references/plan-isolation-contract.md` §15 and SKILL.md "Plan-level git
isolation" for the full lifecycle.

| Symptom | Cause | Recovery |
|---|---|---|
| `landed` result carries `cleanup.worktree: "preserved"` | The plan worktree itself holds uncommitted/untracked/ignored-but-created content at cleanup time (rare through the normal loop — the sync step already refuses to START a land against a dirty plan worktree, §4.6 — but a park-then-resume, or content written by something outside this loop, can still reach here) | **The land already succeeded** — nothing was lost, nothing was force-removed, and the plan branch is kept exactly like `land-worktree-preserved` above. Clean the worktree, then call `land` again — cleanup retries and is idempotent; a plan already fully cleaned is a cheap no-op. |
| `cleanup.branch_remote: "not-pushed"` | The plan branch was never pushed to the remote in the first place — normal: `land` never pushes `plan/<slug>` anywhere, only the merge commit goes to the default branch | Not a failure. Tolerated silently by design (the session brief's own instruction), reported so the record is honest rather than silently skipped. |
| `cleanup.branch_remote: "no-remote"` | This repo has no remote at all (`ctx["remote"]` is None — same condition `land` itself reports as "LANDED LOCALLY ONLY") | Not a failure. Tolerated silently, same reasoning. |
| `cleanup.branch_remote: {"kept": "<reason>"}` | The remote plan branch was NOT deleted (LND-11): its tip has commits not on the landed commit, moved before the leased push, or `ls-remote`/fetch failed | Nothing is lost. Read the reason. A transient failure is retried by `run.py finish` (leftovers target `remote-branch`). A tip with extra commits is real work: review it before you delete it by hand. |
| `cleanup.branch_remote: "already-gone"` | Someone deleted the remote branch between the check and the leased push | Not a failure. The branch is gone, which is what cleanup wanted. |
| `retire-plan` reports `status: "preserved"` | The plan's worktree holds content a removal would lose (§12.4) — the operator is abandoning an ACTIVE plan, so this is the common case, not the exception | The path is printed in the result. Save or discard the content, then re-run `retire-plan` — it is idempotent. The plan is marked retired either way (that half never waits on a clean worktree); only the worktree removal is conditional. |
| `retire-plan` on a plan that was never isolated | No `_plan_worktree.json` for this plan (a schema < 7 plan, or one that never crossed `--isolate`) | `status: "noop"` — nothing to retire at the git level. Not an error. |

## Clearing a halt

After investigating and fixing the underlying cause:

```
python ~/.claude/skills/plan-execute/scripts/run.py clear-halt <dir>
```

(or `/plan-execute <dir> --clear-halt`). Then resume the loop normally, or
re-dispatch the specific session.

## What never auto-retries

Semantic failures and `result: BLOCKED` are **decisions**, not transient errors.
They never auto-retry — a human decides whether the work was actually done and
whether to re-dispatch. (Transient Task-tool errors — network, timeout, rate
limit — are the only retry candidates, and that backoff is a v1.5 addition.)
