# Finish contract — FROZEN 2026-10-01 (v1, rework 2)

**`finish` is the last step of every plan. It makes sure the plan's whole record
is on the default branch, reports anything left behind, brings the operator's
checkout up to date when that is provably safe, and reports CI's verdict.** It
never repairs code. Repair happens before the push, in `land` (see "The
`land-repair` directive"). Sessions s02–s07 of `_plans/finish-every-plan-2026-10-01/`
build it; where code and this page disagree, this page wins until it is amended
by `plan-isolation-contract.md` §14. The contract changes it forces are
revisions R1–R5 in that file (v2).

## Operator decisions (2026-10-01) — recorded, not open

1. **Repair before the push.** `land` runs every gate the registry flags
   `at_land: true` on the merged tree. A red repairable gate yields a repair
   session on the plan branch, at most 3 rounds, then a park. After the push,
   `finish` only watches CI and reports.
2. **Fast-forward only when provably safe.** `finish` fast-forwards the worktree
   that owns the default branch only when every dirty path there is this plan's
   own record and byte-identical to origin's copy. Otherwise it changes nothing
   and prints the command. Falsifying measurement: after the
   model-rating-and-gateways land, `git status` in the primary checkout showed
   `LAND_NOTICE.txt`, `_worktrees/g1.json` and `_plans_index.md` modified.
3. **Every future plan, every repo.** A repo with `.github/workflows/*.yml` and
   no `at_land` gate gets one plain warning line in the land review brief.
4. **Plans built before this change get `finish` too** (decided the same day).

## Decisions taken in s01

- **(a) `LAND_NOTICE.txt` and `_plans/<slug>/_worktrees/` become local runtime
  files, not reordered writes.** Reordering cannot fix `_worktrees/*.json`: the
  group cleanup writes it after the final-record push, and §8.c2 fixes that
  order. The fix has three parts, so no repo depends on a `.gitignore` edit
  someone forgot to commit:
  1. `plan_record` gets one constant, `RUNTIME_LOCAL = ("LAND_NOTICE.txt", "_worktrees/")`.
     `record_plan` never adds, changes or deletes those paths, whatever the
     target tree's `.gitignore` says. Its copy is `rmtree` + `copytree`, which
     would stage a delete, so after the copy and in the same pass as
     `_drop_ignored` it puts each one back to the target worktree's `HEAD`:
     `git checkout HEAD -- <path>` when `HEAD` tracks it, removed when it does
     not. An untracked runtime file never enters a record. One already tracked
     in the target commit keeps that commit's bytes: never deleted, never
     overwritten with local bytes. This covers the land, final and finish
     records, which all go through `record_plan`. **Test:** a base commit that
     tracks `_plans/<slug>/LAND_NOTICE.txt` and `_worktrees/g1.json`; live copies
     with one modified, one deleted, and an untracked `_worktrees/g2.json` added.
     Assert `git diff --name-status <base> <record>` names none of the three,
     and each tracked blob equals the base's.
  2. Both lines join plan-builder's `GITIGNORE_LINES`, so `git status` stays quiet.
  3. **`finish` commits the ignore lines.** Its record push may append to
     origin's `.gitignore` exactly the `GITIGNORE_LINES` entries missing there,
     under plan-builder's comment header, and nothing else. So the first finish
     in any repo carries them, and no person has to.

  **Copies older plans already committed stay tracked** (20 in this repo).
  Finish never untracks or deletes them. If one is modified in the owner, it
  blocks the fast-forward like any other differing path.
- **(b) Repairable = a gate flagged `at_land: true`, `kind: "argv"`, outcome
  `fail`, that passes on the base sha.** Nothing else. LLM reviews and
  cross-family reviews are also `kind: "argv"`, so "every deterministic argv
  gate" would need a classifier, and a hand-kept list of reviewer names decays.
  `land-repair` fires only when EVERY non-passing gate is repairable. A
  `skipped` (timeout), `blocked`, on-box or skill result anywhere keeps today's
  park. A gate already red on the base parks as `gate-inherited`.
- **(c) Finish state lives at `$GIT_COMMON_DIR/plan-state/<slug>/finish.json`**,
  beside `land.json` (via `plan_worktree.plan_state_dir`). Never under
  `_plans/`: a state file in the tracked record would make finish dirty the
  checkout it is cleaning.

## When `plan` returns `finish`

After the last session is terminal (non-isolated plan) or after `land` reports
`landed` (isolated plan), and before `complete`. `plan` returns `finish` while
`finish.json` lacks `finished_at`, then `complete`. A non-git target skips it.

**Returning `finish` writes nothing.** `plan`, `registry_plans` and every status
tool only resolve the action. Only `run.py finish` performs a step.

**Mode.** `run.py begin` writes `finish.json {"armed_at": …}` on any run after
this ships. An armed plan finishes in `apply` mode. A plan with no
`finish.json` (it completed before this shipped) finishes in `report-only` mode:
every step computes and reports what it would do, and nothing is pushed,
restored, retried or fast-forwarded. It records
`finish.json {"mode": "report-only", "reported_at": …, "finished_at": …}`, so
`plan` moves on to `complete`. `run.py finish <dir> --apply` treats a
`finished_at` whose `mode` is `report-only` as not finished: it runs preflight
and steps 1–4 in `apply` mode and overwrites `mode` and `finished_at`. This
keeps decision 4 without a burst of unasked pushes across old plans.

## Preflight — before any write

1. **Remote.** Resolve it the way `land_state` does: `origin`, else the sole
   remote. Several remotes and no `origin`: park `remote-ambiguous`. No remote:
   steps 1 and 3 report `no-remote`, CI is skipped, finish still finishes.
2. **Fetch and pin.** Fetch `<remote>/<default>` once and pin its sha as
   `origin_sha`. Pin `ci_sha`: isolated, `land.json`'s `landed_sha`;
   non-isolated, local `<default>`'s head. Pin `base_sha`: isolated,
   `land.json`'s `landed_base`; non-isolated, `None`. All three go into
   `finish.json`. Nothing later reads a moving ref.
   `landed_base` is new: `land_push.step_push` writes `st["expected"]` as
   `landed_base` in the same update as `landed_sha`, so it is the origin tip
   the pushed merge was built on, after any re-sync. Finish never reads
   `expected` itself: `publish_final_record` overwrites it later
   (land_push.py:250). It never uses `landed_sha^1` either: after a recorded
   conflict resolution or a record commit, that is a plan-side commit with no
   CI run. A `land.json` without `landed_base` (landed before this shipped)
   gives `base_sha = None`.
3. **Ancestry.** Isolated: `landed_sha` (and `final_record_sha` when set) must
   be ancestors of `origin_sha`, else park `not-ancestor` (origin was rewritten).
   Non-isolated: local `<default>` must be an ancestor of `origin_sha`. If it is
   ahead or diverged, park `local-ahead` and push nothing. The brief prints
   `git -C <owner> push <remote> <default>`, then `run.py finish <dir>`.
   Building a record commit on origin's tip there would split the two histories.
4. **Lease.** Take one fresh `git:<root>` lease. **Lock-ownership rule:**
   finish holds exactly this one lease from here through step 3 and releases
   it in its own `finally`. No callee may release it. Step 2 therefore never
   calls `plan_teardown.step_cleanup` or `retire_plan`: both call
   `release_all_ship_locks` (plan_teardown.py:146, :228). Finish checks
   `ship_locks.our_lease` right before the record push and right before the
   first Checkout restore. A lost lease parks the record `lease-lost`, or
   blocks Checkout with `why: lease-lost`; nothing more is written.

## The four steps, in order

1. **Record.** In the checkout holding `<dir>` (the one the orchestrator
   writes, §8.a), find every path under `_plans/<slug>/`, minus `.gitignore`d
   and `RUNTIME_LOCAL` paths, whose bytes differ from `origin_sha` or are absent
   there. Add this plan's one row in `_plans_index.md` (the line linking
   `_plans/<slug>/`) and any missing ignore lines (decision a). Compare with
   `origin_sha`, not local `HEAD`. Commit from a detached temporary worktree at
   `origin_sha` with the `publish_final_record` pattern: record pathspec only,
   compare-and-swap push against `origin_sha`, re-sync bounded at 3. The index
   change is applied to origin's copy and may only add, replace or remove lines
   containing `](_plans/<slug>/`. Any other staged path or line parks
   `outside-record` and pushes nothing. After a push, `target_sha` is the new
   commit; otherwise it is `origin_sha`.
2. **Leftovers.** Report unpushed commits on local `<default>`, and this plan's
   worktree and branch if they still exist. Retry a `preserved` cleanup once,
   never with force, through `plan_teardown.teardown_plan_tree(plan_dir, ctx, st)`.
   That new function is `step_cleanup`'s body minus its two releases
   (`release_all_ship_locks`, `rsi.release_lock`); `step_cleanup` becomes that
   call followed by the releases. A second refusal is reported with the path
   and the reason.
3. **Checkout.** Find the owner with `land_brief.owner_of_default` (`None`
   means every worktree is detached: report `no-owner`). Before ANY restore,
   all of these must hold, checked in this order:
   a. **Owner is behind.** The owner is on `<default>` and
      `git merge-base --is-ancestor HEAD <target_sha>` exits 0. If not, the
      owner is ahead or diverged: `blocked`, `why: owner-not-behind`.
   b. **Dirty paths are this plan's, unstaged and byte-identical.** List them
      with `git status --porcelain` (ignored files excluded). An untracked
      `RUNTIME_LOCAL` path of this plan is not a blocker and is left in place.
      Every other path must have no staged change (index column ` ` or `?`,
      else `why: staged`), must be under `_plans/<slug>/`, be
      `_plans_index.md`, or be `.gitignore`, AND its working-tree bytes must
      equal `git show <target_sha>:<path>`.
   c. **The lease is still ours** (else `why: lease-lost`).
   If all hold, restore exactly those paths (`git checkout -- <path>` for
   tracked; delete an untracked one) and run `git merge --ff-only <target_sha>`.
   If HEAD already equals `target_sha` and nothing needed restoring, the
   status is `up-to-date`. Otherwise change nothing and print the §4.3
   command, naming each blocking path and the plan that owns it.
   **If `merge --ff-only` still fails after a restore** (the owner changed
   between the check and the merge): status `ff-failed`, git's message in the
   brief, no retry, no reset. Nothing is lost, because every restored path held
   bytes equal to `target_sha`'s copy. The brief prints
   `git -C <owner> merge --ff-only <target_sha>` and, per restored path,
   `git -C <owner> restore --source <target_sha> --worktree -- <path>`.
   **Concurrent plans block this by design**: another plan's `_plans/<other>/`
   directory or index row is dirt this plan does not own, and the local
   `_plans_index.md` carries their rows. Expect `blocked` whenever another plan
   is in flight. Comparing only this plan's row is a possible follow-up, and an
   operator decision.
4. **CI.** `finish_ci.ci_verdict(root, ci_sha, branch=<default>, timeout_s=…,
   base_sha=<pinned base_sha>)`. Report only. `red` names the failing
   workflows and checks, and says whether the base had them too. `pending`
   prints the re-run command.

**Re-running.** `run.py finish` is idempotent. On a plan whose `finished_at`
was written in `apply` mode, with `ci.verdict` of `pending` or `unknown`, it
re-polls the SAME pinned `ci_sha`, stores the new `ci` with `checked_at`, and
leaves steps 1–3 alone. A `report-only` `finished_at` is re-run the same way,
unless `--apply` is passed (see Mode).

## What finish may touch — and may never touch

| May | May never |
|---|---|
| Push one commit confined to `_plans/<slug>/`, its own index row and missing ignore lines | Stage any other path, another plan's row, or any other `.gitignore` line |
| Restore byte-identical paths in the owner, then `merge --ff-only <target_sha>` | `git branch -f`, `fetch --update-head-ok`, `update-ref refs/heads/<default>`, `reset`, `stash`, `--force` |
| Retry a preserved teardown once, without force, via `teardown_plan_tree` | Delete a branch §15.1 keeps, or a worktree holding uncommitted files |
| Write `plan-state/<slug>/finish.json`; append `plan_finish*` events to `run.ndjson` | Change any other tracked file; edit code; re-run or cancel a workflow; untrack an old committed runtime file |

A preflight or record-push failure parks finish before its git steps
(`finish-parked`, the plan stays at `finish`). One park comes later: a busy
lease when the CI result is saved parks with `ci-save-busy` (the top-level
`park_reason`, never `preflight.park_reason`) AFTER the git steps ran (the
record may be pushed, the checkout fast-forwarded); only the CI result is
unsaved. The brief names the lease holder and gives `run.py finish <dir>
--no-wait` to run once it has released the lease; that re-run skips the git
steps and goes straight to CI. A blocked
fast-forward, a second teardown refusal and any CI verdict are reports; finish
still records `finished_at`.

## Frozen interfaces

**`run.py finish <dir> [--no-wait] [--apply]`** — idempotent. `--no-wait` is
`timeout_s=0`. The default `timeout_s` is 540, under the 600 s Bash tool cap.

```json
{"action": "finished", "plan": "<slug>", "mode": "apply", "default_branch": "main", "park_reason": null,
 "preflight": {"remote": "origin", "origin_sha": "77aa…", "ci_sha": "9f1c…", "base_sha": "5e6f…", "park_reason": null},
 "record": {"status": "pushed", "park_reason": null, "sha": "a0b1…", "paths": ["_plans/<slug>/PLAN.html", "_plans_index.md"], "outside_record": []},
 "leftovers": {"unpushed_default": 0, "plan_worktree": "absent", "plan_branch": "absent", "plan_branch_remote": "deleted", "retried": []},
 "checkout": {"owner": "/repo", "status": "fast-forwarded", "target_sha": "a0b1…", "restored": ["_plans/<slug>/PLAN.html"], "blocking": [], "command": null},
 "ci": {"verdict": "green", "match": "exact", "run_id": 123, "run_sha": "9f1c…", "failed_checks": [], "followed_runs": [], "inherited": null, "reason": null},
 "brief": "…plain-language summary…"}
```

Allowed values (no others):

| Field | Values |
|---|---|
| `action` | `finished`, `finish-parked` (CLI exit 1), `finish-skipped` (non-git target), `finish-superseded` (CLI exit 0: another finish run, a newer generation, or another CI poll wrote the state first; this run wrote nothing and its CI verdict is not the plan's result. The note follows the saved state. `finished_at`: re-run `finish <dir> --no-wait` once to read it. `steps_done_at` and no `finished_at`: another run is polling CI; wait for the run you started, and if none is running it died, so re-run `finish <dir>`. Neither: the plan was reopened or a newer run stopped before its git steps; run `plan <dir>` and follow it, a re-run of `finish` is safe) |
| `mode` | `apply`, `report-only` |
| `park_reason` (top level) | `null`, `ci-save-busy` (the git steps ran; the lease was busy when the CI result was saved, so it was not), `finish-running` (another finish run of this plan holds its run lock; this run read, wrote and pushed nothing: wait for it, then re-run `finish <dir> --no-wait`) |
| `preflight.park_reason` | `null`, `remote-ambiguous`, `fetch-failed`, `not-ancestor`, `local-ahead`, `lease-busy` (before the git steps: nothing was done) |
| `record.status` | `pushed`, `already-recorded`, `would-push`, `no-remote`, `skipped`, `parked` |
| `record.park_reason` | `null`, `outside-record`, `record-special-file`, `resync-exhausted`, `push-failed`, `lease-lost` |
| `leftovers.plan_worktree`, `leftovers.plan_branch` | `absent`, `removed`, `preserved`, `not-isolated` |
| `leftovers.plan_branch_remote` | `null` (not isolated, or no cleanup recorded), else land's `cleanup.branch_remote` after any retry: `deleted`, `already-gone`, `not-pushed`, `no-remote`, `worktree-preserved`, or `{"kept": "<reason>"}` (see 2026-10-03 below) |
| `leftovers.retried` | list of `{"target": "worktree" or "branch" or "remote-branch", "path": str, "result": "removed" or "refused", "reason": str or null}`; a `remote-branch` path is `<remote>/<branch>` |
| `checkout.status` | `fast-forwarded`, `up-to-date`, `blocked`, `ff-failed`, `no-owner`, `would-fast-forward`, `no-remote`, `skipped` |
| `checkout.blocking` | list of `{"path": str or null, "why": "outside-record" or "differs-from-origin" or "other-plan" or "staged" or "owner-not-behind" or "lease-lost", "plan": slug or null}`; `path` is `null` only for `owner-not-behind` and `lease-lost` |
| `finish.json` keys | `armed_at`, `mode`, `origin_sha`, `ci_sha`, `base_sha`, `target_sha`, `steps_done_at`, `steps`, `generation`, `reported_at` (report-only), `finished_at`, `ci`, `checked_at` |
| `ci` | `null` (no remote, or finish parked), else the `ci_verdict` dict |

`skipped` means an earlier step parked. `would-*` values appear only in
`report-only` mode.

**`finish_ci.ci_verdict(root, sha, *, branch, timeout_s, gh="gh", base_sha=None)`**
— the docstring in `scripts/finish_ci.py` is normative. It aggregates every
workflow run for the sha: one green run never hides a red sibling.

```json
{"verdict": "red", "match": "exact", "run_id": 456, "run_sha": "9f1c…",
 "failed_checks": [{"workflow": "ci", "job": "verify", "step": "make check", "tests": ["test_x.py::test_y"]}],
 "followed_runs": [{"run_id": 455, "workflow": "ci", "head_sha": "9f1c…", "status": "completed", "conclusion": "cancelled"},
                   {"run_id": 456, "workflow": "ci", "head_sha": "9f1c…", "status": "completed", "conclusion": "failure"},
                   {"run_id": 457, "workflow": "lint", "head_sha": "9f1c…", "status": "completed", "conclusion": "success"}],
 "inherited": false, "reason": null}
```

**Registry flag `at_land`** — boolean on a gate entry in `.claude/eval-gates.json`,
merged over the skill defaults (project wins). Absent means false.
**Discovery fails CLOSED.** It parses the bundled default and the project
file itself, never through `shipping._load_registry`, which swallows
`JSONDecodeError` and `OSError` (shipping.py:82, :88). A file that exists but
cannot be read or parsed, is not a JSON object, or has a non-boolean
`at_land` parks land `at-land-registry-unreadable`, naming the file and the
error. It never silently drops an `at_land` gate. An absent project file
means no project `at_land` gates. **Discovery reads the candidate merged tree's registry**
(`<land_path>/.claude/eval-gates.json`), not the plan directory's, so a gate
that arrived on origin while the plan ran is still found. An `at_land` gate is
resolved from that same entry; a plan-declared gate keeps today's resolution.
Each flagged gate joins the re-gate set and enters the gate digest. Argv
abbreviated:

```json
"ci-check": {"kind": "argv", "argv": ["bash", "-c", "…HOME=$H CI=true GITHUB_ACTIONS=true make check"], "cwd": ".", "timeout": 3000, "at_land": true}
```

- A candidate `at_land` entry replacing a plan-declared copy: see Revision notes (s05).

**Registry field `land_covered_by`** (2026-10-04, operator decision) — a gate id on
a plan-declared gate entry. When that id is an `at_land` gate of the same land,
land does not run the plan-declared gate: the `at_land` gate (the full suite)
already runs the same tests on the same merged tree, so the land would run them
twice. The covered gate still runs in every session's verify step. Only an
`at_land` gate can cover, and an `at_land` gate is never dropped, so a cover
cannot remove the check that does the covering. An id that is not an `at_land`
gate of this land covers nothing. The candidate tree is the plan's own code,
so the covering gate's entry must be identical in the trusted outer registry:
a plan that changes any field of the covering gate loses the cover,
and the covered gate runs. Land records the covered gates in
`land.json` as `covered: {gate: covering gate}`.

```json
"land-tests": {"kind": "argv", "argv": ["…"], "land_covered_by": "ci-check"}
```

**The `land-repair` directive** — returned by `land` instead of `land-parked`
when (b) holds for every non-passing gate. Before returning it, `land` runs
each failing gate once more on a detached worktree at the candidate's base
(`expected`). A review gate (an argv gate running `llm_review_gate.py`) skips that
base run (2026-10-04): land hands it the base as its review base, so its findings
are about the plan's diff, and on the base that diff is empty. The result is cached in `land.json["base_gate_cache"]` under the
key `<base sha>:<gate id>:<definition digest>`. The digest is sha256 over that
one gate's resolved definition, with the fields `land_gate._gate_set_digest`
uses (kind, argv, cwd, skill, indeterminate_exit, timeout, env_allowlist), so a
changed gate is a cache miss. A gate that fails there parks
`gate-inherited`. `land.json` counts rounds. Round 3 failing, or two rounds in a
row with the same failing set, parks with the failing tests named.

**Re-run before blame (LND-12).** Before the base run, `land` re-runs each red
gate once, whole, on the candidate tree. A review gate (one that declares
`PLAN_EXECUTE_REVIEW_BASE`) is not re-run: re-reviewing an unchanged tree
answers nothing new, so it stays red. The result is kept in
`land.json["rerun"]` under the candidate, so replaying the same candidate does
not run it again. A gate that passes on its re-run is flaky and leaves the
repair; one that fails again goes on to the base run as before.

The repair parks, one row each:

| Park | When | The way out |
|---|---|---|
| `gate-flaky` | Every red gate passed on its re-run, or the gates are green while a held check is unresolved. Only failures in files the plan changed are held (a test id, or a file a collection error names), plus a whole gate whose failure named neither; a repair round's failure that went green with nothing changed goes through the same hold | Fix or quarantine the named test on the plan branch, or run `land` again. A held check (in a file the plan changed, or a whole gate): fix or quarantine only |
| `repair-not-needed` | The re-gate is green, but the repair session land directed is still TODO | Run the `retire-session` command the brief names, then run `land` |
| `repair-in-progress` | The re-gate is green, but the repair session land directed is still open (DOING, AWAITS_REVIEW, ...) | Wait for that session to finish, then run `land` |
| `gate-inherited` | A gate red twice is red on the base too | Fix the default branch, then run `land` |
| `repair-gate-unresolvable` | The repair session's gate does not resolve in `add-session`'s registry | Pull the registry into the named root, then run `land` |
| `repair-session-invalid` | `add-session`'s dry run refused the session | Fix the plan, then run `land` |
| `repair-exhausted` | Red after round 3 | A person fixes the named tests |
| `repair-same-failures` | The same failing set two rounds in a row | A person fixes the named tests |

**Before emitting it, `land` checks the repair session against the registry
`add-session` will use.** That is `plan_mutate.project_root_for(plan_dir)`'s
`.claude/eval-gates.json`, loaded by `load_project_registries`, with the same
validation `add-session` runs, as a dry run that writes nothing. It is not the
candidate tree's registry that discovery read. When they differ (an `at_land`
gate that reached origin while the plan ran), the check fails. `land` then
parks `repair-gate-unresolvable`, naming the gate and saying "pull the
registry into `<root>`, then re-run land". No directive is emitted.

The orchestrator then, in order: (1) runs `run.py add-session` from
`add_session`, passing each `new_items` entry as `--new-item '<json>'`;
(2) fast-forwards the plan branch to the candidate with `start.command`;
(3) dispatches the session; (4) re-runs `land`. If step 1 fails, nothing has
moved: surface the error and STOP. If step 2 fails after step 1 (the plan
worktree is dirty, or the move is not a fast-forward), STOP before dispatch and
surface `start.command`. The added session stays pending until the operator
runs it. As a backstop, the repair prompt opens with
`git merge-base --is-ancestor <candidate.head> HEAD`; if that fails, the
session closes `BLOCKED` instead of repairing the wrong tree. The land worktree
is kept until the next `land` rebuilds it, so the candidate stays reachable.

**Long gates.** `land` runs argv gates synchronously (land_gate.py:117,
default timeout 1200 s), and a repair round adds a base run. When any
resolved gate's `timeout` exceeds 540 s, the orchestrator runs
`PYBP land <dir>` with `run_in_background: true` and waits on it with
`Monitor` until it exits (autonomous-mode.md's long-gate rule). It never runs
it in the foreground under the 600 s Bash cap. The land brief lists each
gate's wall time per round, base runs included (`gates[].wall_s`), so a
repeated 3000 s gate shows up as a cost.

```json
{"action": "land-repair", "round": 1, "max_rounds": 3,
 "candidate": {"expected": "77aa…", "merge_sha": "c3d4…", "head": "c3d4…", "plan_head": "e5f6…"},
 "failing": [{"gate": "ci-check", "outcome": "fail", "base_outcome": "pass",
              "tests": ["test_x.py::test_y"], "excerpt": "test_x.py::test_y FAILED …",
              "log": "<plan-state>/<slug>/land/ci-check.log"}],
 "start": {"plan_tree": "/repo/.plan-worktrees/<slug>",
           "command": "git -C /repo/.plan-worktrees/<slug> merge --ff-only c3d4…"},
 "add_session": {"sid": "lr1", "title": "Repair ci-check at land (round 1)",
   "task_class": "agentic_build", "gates": ["ci-check"], "depends_on": [],
   "new_items": [{"id": "land-repair-1", "category": "<category key>",
                  "title": "Repair ci-check at land (round 1)",
                  "research_status": "skipped", "research_reason": "repair of a red land gate"}],
   "infographic_group": "<group name or null>",
   "prompt": "Fix the named failures at the root cause. Never weaken, skip or delete a test. …"}}
```

Field rules. `candidate.head` is the land worktree's `HEAD` (it descends
`merge_sha` when a conflict resolution was recorded); `start` fast-forwards to
it, so the repair starts from the merged tree, not the bare plan branch.
`add_session.gates` holds only the failing gates: the next `land` re-gates the
whole union anyway. `sid` is the first free `lr<n>`; the item id is
`land-repair-<round>`. `category` and `infographic_group` are copied from the
first item of the session that last went `DONE`; `infographic_group` is `null`
when the plan has no infographic groups. A gate that does not resolve for the
repair session parks `repair-gate-unresolvable` (above). A round whose session
never started (`start_pending` still names it, it is TODO or not yet added,
and the plan tree still lacks its candidate) is replaced on the next candidate, not counted: same round, same `sid`. When
that session is already in the plan, the directive adds
`"amend_session": {"sid": "lr1", "prompt": "…"}` and the orchestrator runs
`amend-session --prompt` instead of `add-session`; the session keeps its gates.

## Revision notes

- **R6 (2026-10-01, s03, clarification; shape unchanged).** This repo's CI
  cancels a run when a newer push of a different sha arrives (`verify.yml`
  `concurrency`, `cancel-in-progress`). When every exact run of a workflow is
  cancelled and no newer same-sha run exists, that workflow falls back to its
  nearest-descendant run (`match: "descendant"`). With no descendant either,
  the verdict is `unknown`, reason `no-run-for-sha`.
- **R6a (2026-10-01, s03 rework, clarification; shape unchanged).** "Workflow"
  means the run's `workflowDatabaseId` (two workflow files can share a name);
  `failed_checks[].workflow` still carries the name. The `gh auth status`
  check is scoped to the repo remote's host (`--hostname`). `inherited` is
  `None` when a failed run here has no job data AND no baseline workflow
  passed; a baseline pass still gives `False`.
  With `timeout_s > 0`, a look that finds no run keeps polling for a grace
  of at most 90 s (never past the timeout), since a push's run can register
  late; after it the verdict is `unknown`, reason `no-run-for-sha`, not `timeout`.
- **Revision (2026-10-01, s05 rework, clarification; shape unchanged).** A
  candidate `at_land` entry replaces a plan-declared copy of the same gate.
  `expect` is honoured at land. The land brief names each `at_land` gate the
  plan added, changed or removed.
- **2026-10-02 (operator option B).** The record commit must be one commit on the
  pinned parent whose tree equals the tree finish staged and checked, blob by blob.
  Any change by a commit hook parks. A failed hook parks `push-failed` and is never
  retried.
- **s06 (2026-10-02, clarification; shape unchanged).** The repair parks are
  named: `repair-exhausted` (a red land after round 3), `repair-same-failures`
  (the same failing gates and tests two rounds in a row), and
  `repair-session-invalid` (add-session's dry run refused the repair session for
  a reason other than its gates). Re-running `land` on the same candidate before
  any repair returns the same round's directive; it does not count a new round.
- **2026-10-03 (finish-plan-follow-ups s02, LND-11; additive shape change).**
  The remote plan branch is deleted only when its tip is on the landed commit.
  `plan_teardown._delete_remote_branch` reads the tip with `ls-remote`, fetches
  it, checks `merge-base --is-ancestor <tip> <landed_sha>`, and pushes the
  delete with `--force-with-lease=refs/heads/<branch>:<tip>`. The lease is that
  exact tip, never a re-read tracking ref. `cleanup.branch_remote` gains two
  values. `already-gone`: the leased push was refused and a second `ls-remote`
  found the branch absent. `{"kept": "<reason>"}`: the branch stays, because
  `ls-remote` failed (any exit but 2, which still means `not-pushed`), the
  fetch failed, the tip is not an ancestor, the ancestry check could not run,
  there is no `landed_sha`, the tip moved before the push, or the push failed.
  The local delete is leased the same way (`update-ref -d <ref> <tested sha>`).
  `land`'s already-landed answer names a kept remote branch in `message`.
  Finish step 2 reports it as `leftovers.plan_branch_remote` and retries the
  remote delete alone (target `remote-branch`), once and under finish's lease,
  even when the worktree and the local branch are already gone.
- **2026-10-03 (finish-plan-follow-ups s03, LND-12 and LND-13; additive shape change).**
  New park `gate-flaky` (table under "The `land-repair` directive"). Every
  red-then-green test id is kept in `land.json["red_then_green"]` across
  candidates, and any later review brief of the plan prints them on one line.
  Only a failure in a file the plan's diff touches (a test id, or a file a
  collection error names) is held, in `land.json["plan_touched_flakes"]`, keyed
  by plan; a failure that names neither holds the whole gate, and a diff git
  cannot read holds every name. While one is held, `land` parks `gate-flaky`
  even with every gate green, and the brief offers no re-run. The current
  clearing rule: a known flake (seen red, then green, on one tree) clears only
  when the plan's own patch for its file changes, judged by content
  (`git diff --no-renames <base> <plan head> -- <file>` at the failure versus
  now, line numbers ignored), so main editing the same file neither hides nor
  fakes a repair; a whole-gate hold clears when that patch changes for any file.
  A failure never seen to pass on its own tree ends once its tree changes. The re-run is recorded per candidate with its
  failing test ids, and only saves a second re-run: a gate red again on the
  same candidate stays red and goes to the base check. Each repair round's wall times gain
  `rerun`, printed as `<gate> <n> s on the re-run`. Each directive also writes
  `land.json["start_pending"]`: `{sid, round, candidate_head, plan_tree,
  command}`. `plan` (ready path and `--session`) and `begin --sessions` (with
  or without `--resume`) refuse that sid while
  `git merge-base --is-ancestor <candidate_head> HEAD` fails in the plan tree.
  `plan` answers `blocked` with `reason: repair-start-pending` and the command;
  `begin` exits 1 before any worktree or status change. A candidate that is
  gone, or is no longer the land tree's HEAD, tells the operator to run `land` for a new directive. These reads never write
  `land.json`; `land` rewrites `start_pending` with each directive and clears
  it only when its session is terminal or retired.
  Fix round (operator-approved, after rework 2), one line per fix:
  (1) a held test that fails only on the re-run is re-held too; the re-run's
  own failing ids are kept as `rerun_tests` beside the first run's `tests`.
  (2) a new candidate no longer clears `start_pending`, so a land that parks
  without a directive leaves the repair session refused.
  (3) a round whose session never started (its fast-forward never ran) is
  replaced, not counted: same round and `sid`, plus `amend_session` when that session is already in the plan.
  Fix round (rework 1 after the operator-approved redispatch), one line per fix:
  (1) every land-tree run of an argv gate (first run and re-run, not the base
  run) re-anchors and saves a failing held test at once, so a skill-verdict
  round trip before `repair` can no longer lose the new anchor.
  (2) a green re-gate drops a `start_pending` whose session was never added,
  and marks a still-TODO one `green`; `plan` and `begin` then refuse it with
  `REPAIR NOT NEEDED` and the exact `run.py retire-session <dir> --session <sid>`
  command. Land cannot retire it itself: it holds the ship lock that
  `retire-session` refuses under. The gone-candidate refusal names the same command.
  (3) unchanged: a round still counts as started once its fast-forward ran,
  because a status-only rule breaks three repair-round tests that repair without
  adding the session.
  Fix round (rework 2 after the operator-approved redispatch), one line per fix:
  (1) a green re-gate with that repair session still TODO now parks
  `repair-not-needed` instead of reaching awaits-review; after `retire-session`, `land` proceeds.
  (2) every generated `run.py land` and `run.py retire-session` command shell-quotes its paths and reason.
  Final fix round (operator-approved), one fail-closed rule: land never treats a
  check as passed on a tree where that check already failed, unless the plan
  changed something since that failure. `land_flaky.hold` is its one write:
  (1) a red-then-green gate whose output names no test id holds each file a
  collection error names (`ERROR <path> - ...`) like a test, and otherwise holds
  the whole gate, which clears on any plan change.
  (2) a repair round's failures that go green while the plan head and the base
  are still the round's are flaky, not repaired: they go through the same hold
  and the red-then-green note, and `repair-not-needed` no longer says nothing
  is left to repair.
  (3) a green re-gate with the repair session open but not TODO (DOING,
  AWAITS_REVIEW, ...) parks the new kind `repair-in-progress`.
  Fix round (rework 1 after the second redispatch). The rule, restated: land
  never counts a check as passed on the tree it failed on; "the plan changed"
  means the plan's OWN patch changed, not that its head moved.
  (A) every failing land-tree run of an argv gate (first run and re-run, never
  the base run) is recorded at once by `land_flaky.record`, anchored to the
  plan head and the base, before any early return; the other paths only read.
  (B) a green re-gate judges each record against its tree (the plan head's diff
  outside `_plans/<slug>/`): unchanged means red-then-green, and a held one parks
  `gate-flaky`; a known flake clears only by the clearing rule above (its file's plan
  patch changes by content, or any file's for a whole gate); a
  failure never seen to pass on its tree ends once that tree changes (a repair,
  or the base fixing it). `range_files` uses `--no-renames`, so a moved file
  counts as changed. `repair-not-needed`'s "flaky, not repaired" uses the same
  unchanged-tree test. A `gate-flaky` brief names a still-open repair session
  and its `retire-session` command.
  Fix round (rework 2 after the second redispatch): "the plan's own patch
  changed a file" is judged by that file's patch content, not by subtracting
  the files the base changed, so a plan repair of a file main also edited clears its hold.
- **2026-10-04 (finish-plan-follow-ups s04, LND-14; no shape change).**
  `--accept-inherited` was left out under the session's stop rule (operator,
  2026-10-04, after the cross-family review found a new HIGH in six fix
  rounds): no CLI flag, no park-brief command, no doc or port-coverage rows.
  `gate-inherited` still parks, and `run.py land-ack` still refuses it (exit
  non-zero, no ack recorded), proved by
  `test_land_at_land.py::test_land_ack_refuses_a_gate_inherited_park`. The
  attempted code is kept for a later plan in this plan's
  `_evidence/s04/accept-inherited-fix-round-5.patch`. Review findings still
  open at the stop: direct-run JUnit ids were checked by count only, and a
  failing id whose file path contains a space escaped the log checks. The repair round for a failure only the plan caused was part of the same deferred work: today, when a red gate also fails on the base, land parks it as `gate-inherited` at gate level, even if the plan added a new failing test inside that gate. That park brief says "The plan did not break them", which can be false in that case; the later plan that ships `--accept-inherited` fixes the wording.
- **2026-10-03 (finish-plan-follow-ups s05, FIN-12 and FIN-13; additive shape change).**
  `finish.json` gains `steps_done_at`, `steps` (the record, leftovers and
  checkout results) and `generation`. Each finish cycle writes a fresh
  `generation` id and clears the last cycle's results under the lease, then
  saves `steps_done_at`, `mode`, `target_sha` and `steps` after the checkout step
  and BEFORE the lease is released and the CI poll starts. A re-run that finds
  `steps_done_at` and no `finished_at` skips steps 1-3, polls CI on the saved
  `ci_sha` when no verdict was saved (a pending verdict is re-polled as before,
  with no remote it stamps without a poll) and stamps `finished_at`. A
  report-only `steps_done_at` never skips the steps of an `--apply` run.
  After any CI poll, finish takes the lease briefly, re-reads `finish.json` and
  writes only if the `generation` and the `finished_at`, `checked_at` and `ci`
  it read still match; otherwise the action is `finish-superseded` and nothing
  is written. A busy lease at that write parks with `lease-busy` (re-run
  `finish`); `finished` is reported only after the write. New `record.park_reason`
  `record-special-file`: a FIFO, socket or device file in the plan folder
  (judged with `lstat`, runtime-local paths ignored) parks and names each path;
  ordinary directories are traversed. The orchestrator runs `finish` in the
  background and waits on it. A remote plan branch still `{"kept": ...}` after a
  finished apply-mode finish is no longer promised a retry: the `land` message
  and the finish brief give the exact
  `git push --force-with-lease=refs/heads/<branch>:<tip> <remote> :refs/heads/<branch>`.
  A kept dict whose retry finds `not-pushed` is recorded as `already-gone`.
- **2026-10-04 (finish-plan-follow-ups s10, FIN-16; additive shape change).**
  (1) Every read-compare-write of `finish.json` (the CI save, the new-cycle
  reset, the steps checkpoint and `arm`) holds an exclusive OS lock
  (`flock`) on the sidecar `finish.json.lock`: the `git:<root>` lease still
  excludes other plans, but a second finish run of the same plan re-enters it.
  (2) `arm` (`run.py begin`) also starts a fresh cycle when `steps_done_at` is
  set (a finish stopped during its CI poll), with a new `generation`, so a poll
  still running from the old cycle ends `finish-superseded` and the next finish
  runs the git steps again. (3) The late lease-busy park is the top-level
  `park_reason: ci-save-busy`; `preflight.park_reason` `lease-busy` now means
  only the early park, before the git steps. The brief's first line says the
  git steps ran, and the brief names the lease holder and the `--no-wait`
  command. (4) The `finish-superseded` note says which case it is: a newer run
  saved its result (a `--no-wait` re-run reads it), or one has not yet (wait,
  do not re-run). (5) Land's flaky records (`plan_touched_flakes`,
  `named_failures`) are keyed per gate, `"<gate>: <test id>"`, with `name`
  stored in each record, so the same pytest id from two gates keeps two holds;
  a name-only record from an older `land.json` is re-keyed under its recorded
  `gate`. Park `tests` and `plan_touched` print `<gate>: <test id>` only when
  two gates share that id.
- **2026-10-04 (finish-plan-follow-ups s11, FIN-17; no shape change).**
  (1) The new-cycle reset re-reads `finish.json` under the `flock` and resets
  only if `finished_at`, `steps_done_at` and `generation` are still what finish
  read before taking the lease (a second run of the same plan re-enters it).
  Otherwise nothing is reset, no git step runs, the lease is released and the
  action is `finish-superseded`. (2) If the steps checkpoint finds the state
  superseded, the CI poll is skipped and the action is `finish-superseded` at
  once. (3) The `finish-superseded` note has three cases, by the saved state:
  `finished_at` (re-run `--no-wait` to read it), `steps_done_at` without
  `finished_at` (wait for the run you started; if none is running it died, so
  re-run `finish`), and neither (the plan was reopened or a newer run stopped
  before its git steps: run `plan` and follow it; a re-run of `finish` is
  safe). (4) A land flaky hold from a relative test id anchors to the path
  resolved against the gate's working directory when that path is in the
  plan's diff, and to the raw id only when it is not.
- **2026-10-04 (finish-plan-follow-ups s12, FIN-18; additive value).**
  (1) A finish run takes a per-plan run lock (`flock` with `LOCK_NB` on the
  sidecar `finish.run.lock`) before it reads `finish.json`, and holds it until
  its steps checkpoint is written; it drops it before any CI poll. A second
  run of the same plan that finds it held parks at once with the top-level
  `park_reason: finish-running`: nothing is read, written or pushed. The
  FIN-17 admission re-check stays. (2) Land: a red gate whose re-run could not
  decide (a timeout or its indeterminate exit) is not a second failure. No base
  run and no repair session follow; it parks `gate-indeterminate`, and the
  undecided re-run is not saved, so the next land re-runs it. (3) The kept
  remote branch note says the remote is gone, with no command, when the
  repository has no remote any more.
