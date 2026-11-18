# Plan-isolation contract — FROZEN 2026-08-21 (~~contract v1~~ ~~contract v2~~ ~~contract v3~~ contract v4, amended by R1–R5, 2026-11-14 by R6 and 2026-11-15 by R7, see §16)

**This file is the single statement of record for how `/plan-execute` isolates a
plan into its own git branch and worktree, and how that work lands.** Sessions
s05–s10 of `_plans/example-isolation-plan-2026-08-20/` implement it; where an
implementation and this file disagree, this file wins until it is amended by the
procedure in §14.

Every rule below was written **after** the s03b feasibility probes, not before.
Three rules contradict the plan's own original sketch, and each says so in its
rationale with the transcript that falsified it. A contract that restates a
design the probes disproved is the failure this document exists to prevent.

## Contents

- [Verdict](#verdict)
- [Capability ledger](#capability-ledger)
- [1. Namespace and ownership](#1-namespace-and-ownership)
- [2. Push posture](#2-push-posture)
- [3. Liveness and exclusion](#3-liveness-and-exclusion)
- [4. The land protocol](#4-the-land-protocol)
- [5. Approval binding](#5-approval-binding)
- [6. The version gate](#6-the-version-gate)
- [7. Refusals](#7-refusals)
- [8. The plan's own directory](#8-the-plans-own-directory)
- [9. Hooks](#9-hooks)
- [10. Gate inputs](#10-gate-inputs)
- [11. The shared-`.git` operation matrix](#11-the-shared-git-operation-matrix)
- [12. Git pitfalls, codified](#12-git-pitfalls-codified)
- [13. Prior art and the BUILD decision](#13-prior-art-and-the-build-decision)
- [14. The repair path](#14-the-repair-path)
- [15. Lifecycle: one row per state](#15-lifecycle-one-row-per-state)
- [16. Supersession](#16-supersession)
- [17. Keeping a land's results, its turn and its lock (R6)](#17-keeping-a-lands-results-its-turn-and-its-lock-r6-2026-11-14)
- [18. The two-phase land (R7)](#18-the-two-phase-land-r7-2026-11-15)

---

## Verdict

**A plan runs on its own branch in its own locked worktree, and lands by
advancing `origin/main` with a compare-and-swap push from a detached worktree.
~~It never writes a local branch ref, never moves the operator's `main`,~~ `land`
never writes a local branch ref and never moves the operator's `main` (only
`finish` may, by R1's guarded `merge --ff-only`), and it never
infers ownership of a branch or a path from its name.**

The three load-bearing consequences, each measured rather than assumed:

- **Nothing can land onto a checked-out `main`.** `git worktree add <path> main`
  and `git branch -f main` are both refused, exit 128, whether or not the primary
  checkout is dirty. The two overrides that *do* succeed —
  `git fetch --update-head-ok origin main:main` and `git update-ref
  refs/heads/main` — succeed with **exit 0 and silently desynchronise the
  operator's index from HEAD**. They are forbidden by name in §4.
- **Plan liveness is a lease, never a pid.** `run.py` exits before the operation
  it authorised begins, so a pid-keyed lock is dead for the whole window it
  guards. s03b measured **4 of 4 simultaneous contenders all "acquiring" the same
  lock, 3 trials of 3**, and every later contender reclaiming it as stale.
- **The plan's own `_plans/` directory is the sharpest edge in the design.** It
  is a tracked path inside the repo being isolated, so the worktree holds a
  frozen copy while the orchestrator keeps writing the outer one, and a second
  plan's merge can silently delete the first plan's record with exit 0 (§8).

**Isolation is version-gated and inert today.** Measured across
**every** manifest under `_plans/`: **28 manifests scanned, 24 at
`plan_schema_version` 2, 2 at 5, 2 at 6, and 0 at ≥ 7 — zero crossings.** No
existing plan changes behaviour when this contract ships.

## Capability ledger

Every claim this contract depends on, and how it is known. Nothing here may be
promoted from "documented" to "measured" without re-measuring.

| Claim | Status | Source |
|---|---|---|
| `git worktree add <path> main` while main is checked out → exit 128 `already used by worktree` | **measured** | s03b probe 1 (a) |
| `git branch -f main HEAD` from another worktree → exit 128 `cannot force update` | **measured** | s03b probe 1 (b) |
| `git push origin HEAD:main` from a detached worktree → exit 0, local `main` unmoved | **measured** | s03b probe 1 (c), WORKING SEQUENCE |
| A dirty primary changes none of the guards; it only breaks the operator's `--ff-only` recovery (exit 1) when the landed commits touch a dirty file | **measured** | s03b probe 1 (d1), ADDENDUM A |
| Primary on another branch moves the `main`-in-use guard to whichever worktree holds it; it does not remove it | **measured** | s03b probe 1 (d2) |
| `git push . HEAD:refs/heads/main` → exit 1, `remote rejected … branch is currently checked out` | **measured** | s03b probe 1 ADDENDUM C |
| `git fetch origin main:main` → exit 128 refusal; `--update-head-ok` → **exit 0 and corrupts** the index/worktree relationship | **measured** | s03b probe 1 CONTROL |
| `git update-ref refs/heads/main HEAD` from a worktree → **exit 0, no output, no guard** | **measured** | s03b probe 1 CONTROL |
| A push whose `origin/main` moved under it → exit 1 `! [rejected] HEAD -> main (fetch first)`; recovery is fetch → merge → push | **measured** | s03b probe 1 ADDENDUM B |
| `run.py ship-begin` exits with the recorded holder pid already dead | **measured** | s03b probe 2.1 |
| Check-then-write lock: 4 of 4 contenders acquire, 0 excluded, 3 trials of 3 | **measured** | s03b probe 2.5 |
| Two plans in one checkout wrote two different lock FILES for the same resource NAME | **measured** | s03b probe 2.3 (`same file? False`) |
| The exclusion logic *can* fail — planted live holder returns `action = locked` | **measured** (known-positive control) | s03b probe 2.4 |
| Repo-scoped `O_CREAT\|O_EXCL` lease: 1 winner of 4, 3 trials of 3; exclusion survives `run.py` exiting; expired-lease takeover is recorded and the victim's `ship-record` REFUSES | **measured, already shipped** | s02 `_evidence/s02/lease-exclusion.{txt,json}` |
| A second plan's merge carries through files its base never saw (own-directory-only case is benign) | **measured** | s03b probe 3 scenario 1 |
| A regenerated `_plans_index.md` conflicts, and `--theirs` silently drops the first plan's entry with exit 0 | **measured** | s03b probe 3 scenario 3 |
| A modify/delete conflict under `_plans/` stages a sibling deletion with **no conflict marker** | **measured** | s03b probe 3 scenario 4 |
| Two plans appending to one `run.ndjson` → `CONFLICT (add/add)`, exit 1 | **measured** | s03b probe 3 scenario 5 |
| `WORKTREE_DIRNAME = ".plan-worktrees"` and `BRANCH_PREFIX = "plan"` are already in use today for group worktrees | **measured** | `skills/plan-execute/scripts/worktree.py:51-52`, `member_branch()` at :292 |
| Branch ownership must be keyed `(plan, branch)`; a branch claimed by two plans is a CONFLICT, never resolved | **measured, already shipped** | s01 / `registry_owners.py`; ADR-0002 |
| `plan_schema_version` on disk: 28 manifests, 0 at ≥ 7 | **measured** | §6 |
| Claude Code writes an absolute `core.hooksPath` into `$GIT_COMMON_DIR/worktrees/<wt>/config.worktree`, and worktree-scoped config beats the shared value | **documented, NOT re-measured here** | anthropics/claude-code#60620 |
| Shared `.git/hooks` shims bake absolute paths | **documented, NOT re-measured here** | lefthook#1398 |
| Git Town (MIT, **3.4k stars**, re-read) implements sync → ship → undo; its `fast-forward` ship strategy exists to "prevent false merge conflicts when using stacked changes" and to "Ship several branches in a stack without unnecessary CI runs", and requires the feature branch to be up to date | **documented** | git-town.com/preferences/ship-strategy; github.com/git-town/git-town |
| Multiple superproject checkouts of a submodule-bearing repo are not recommended | **documented** | `git-worktree(1)`, BUGS |

Two rows are deliberately **documented, not measured**: the two hooks issues.
§9 is written so that the contract does not *depend* on them being true — the
hook canary proves the hooks actually run, in this repo, at this moment, whatever
the upstream bug status.

---

## 1. Namespace and ownership

**1.1 — Names are fixed.**

| Thing | Name |
|---|---|
| Plan branch | `plan/<plan-slug>` |
| Group member branch under a plan | `plan/<plan-slug>__<group>__<sid>` (amended — the nested form `plan/<plan-slug>/<group>/<sid>` is a ref git refuses beside `plan/<plan-slug>`; see `parallel-group-contract.md` V3-1) |
| Plan worktree | `<repo>/.plan-worktrees/<plan-slug>/` |
| Group member worktree under a plan | `<repo>/.plan-worktrees/<plan-slug>__<group>__<sid>/` |
| Land worktree | `<repo>/.plan-worktrees/<plan-slug>__land-<short-token>/` |

**1.1a — Every worktree is a SIBLING. No worktree is ever created inside another
worktree's working directory.** The member and land paths use `__` separators
precisely so they sit beside `<plan-slug>/` rather than under it. Measured
2026-08-21, in a scratch fixture, why this is a rule and not a preference:

- `git worktree add` into a path inside another worktree **succeeds** (exit 0) and
  the parent then reports the child as untracked — `?? nested/`. Under §10.1,
  which derives gate file sets from `git status --porcelain=v1 -z` *including
  untracked*, a gate in the plan worktree would enumerate the entire contents of
  every member and land checkout.
- `git worktree remove` on a worktree containing untracked content **fails, exit
  128**: `fatal: '<path>' contains modified or untracked files, use --force to
  delete it`. A nested child therefore makes the parent unremovable without
  `--force`, and §12.4 forbids reaching for `--force`. Teardown would deadlock
  behind its own children.

**1.1b — `.plan-worktrees/` MUST be ignored in the target repo, and `begin`
verifies it rather than assuming it.** `begin` runs
`git check-ignore -q .plan-worktrees/probe`; if the path is not ignored it adds
`/.plan-worktrees/` to the repo's `.gitignore` and says so in the run log. This
repo happens to be covered already by a blanket `/*` rule at `.gitignore:6`
(measured) — which is exactly why it must be *checked* and not assumed: a target
repo without such a rule would have every plan checkout show up as untracked in
the operator's own `git status`.

**1.2 — The plan worktree is created locked.** `git worktree add --lock
--reason "<plan-slug> started_at=<iso8601>" <path> <base>`. The reason string is
diagnostic, not authority — see 1.5.

**1.3 — The base ref is pinned at `begin` and verified before use.** `begin`
records the resolved sha of `origin/<default>` (falling back to local `HEAD` only
in a repo with no remote, and saying so in the run log). Every later stage
re-resolves that sha and refuses if the recorded value is not an ancestor of
what it now finds. A base that is *recorded* and never *re-verified* is the
"stale artifact as proof" shape: an artifact from an earlier state accepted as
proof about the current one.

**1.4 — THE NAMESPACE IS ALREADY OCCUPIED.** `worktree.py:51-52` sets
`WORKTREE_DIRNAME = ".plan-worktrees"` and `BRANCH_PREFIX = "plan"` **today**,
and `member_branch(group, sid)` returns `plan/<group>/<sid>` for every legacy
plan. Two concrete collisions follow, and both are refusals, not warnings:

- A legacy group branch `plan/<group>/<sid>` and a new plan branch
  `plan/<plan-slug>` collide in git's ref namespace whenever `<group>` equals a
  `<plan-slug>`: git cannot hold both `refs/heads/plan/X` and
  `refs/heads/plan/X/g/s01`. `begin` MUST check for existing refs under
  `refs/heads/plan/<plan-slug>` **and** for an existing exact ref
  `refs/heads/plan/<plan-slug>` before creating either, and refuse naming the
  conflicting ref.
- A legacy worktree at `.plan-worktrees/<group>/<sid>` and a new plan worktree at
  `.plan-worktrees/<plan-slug>/` collide on the filesystem under the same
  condition. `git worktree add` fails loudly here; the refusal must name the
  legacy worktree rather than surface git's raw error.

**1.5 — NO CONSUMER MAY INFER OWNERSHIP FROM A NAME.** This is ADR-0002's rule
applied to branches and worktrees. ADR-0002 ("reap only what a live registry does
not claim") rejected `PPID == 1` as an ownership signal because it "carries almost
no selectivity" — 327 user-owned processes sat at PPID 1 by design on the machine
that motivated it — and replaced it with a live registry whose absence of a claim,
not a name's shape, is what authorises action. The same discipline binds here:

- Ownership of a branch, a worktree or a lock is read **only** from a plan's own
  state files, keyed `(plan, branch)` — the shape `registry_owners.py` already
  ships after s01, where "a branch is returned as a LIST of owner records … a
  list with more than one DISTINCT plan is the collision itself, surfaced … as a
  fourth `conflict` class, never resolved here."
- A ref or worktree that matches the naming scheme but maps to **no** plan state
  file is **UNKNOWN**. UNKNOWN is untouchable: never pruned, never reused, never
  force-updated, never adopted. It is reported and left alone.
- A ref claimed by **two** plans is **CONFLICT**, never silently resolved to one
  owner. The peer-session finding this rule comes from was verified in this repo:
  `pg-prep` is declared as a `parallel_group` in *both*
  `example-plan-a-2026-07-27` and `example-plan-b-2026-07-27`.

*Why this and not a name convention:* a name is a claim anybody can make. An
operator's own `plan/experiment` branch, a legacy group branch, and a v7 plan
branch are byte-identical on the field a name-matcher inspects — exactly the
"byte-identical on the fields the conjunction inspects" failure ADR-0002 records.

---

## 2. Push posture

**2.1** The plan branch is pushed to `origin` on **ship steps only** — the same
steps that would have committed on a shared tree. It is not pushed on every
session close, and it is never pushed by a member session (parallel-group
contract M1 still applies).

**2.2** The first push sets upstream: `git push -u origin
plan/<plan-slug>`. Subsequent pushes are plain `git push`, never `--force`;
a rejected non-fast-forward push on the plan branch is a park with the reason,
because the only way a plan branch diverges is that something outside this plan
wrote it, which rule 1.5 says is not ours to overwrite.

**2.3** The remote plan branch is deleted **after** land, and **only after a
successful push of `main`**. Order is normative: push `main`, verify the push
result names the expected new sha, *then* `git push origin --delete
plan/<plan-slug>`. A delete that runs before the main push is how a rejected
land loses its work.

*Amended 2026-10-03 (§14; example-follow-up-plan s05, cross-reference
`finish-contract.md`, entries of 2026-10-03).* The delete is NOT an unleased
`git push origin --delete`. It runs only after `git merge-base --is-ancestor
<remote tip> <landed sha>` succeeds, and it is pushed as
`git push --force-with-lease=refs/heads/<branch>:<tip> <remote>
:refs/heads/<branch>`, so a branch that moved since the check is refused by the
remote. Any doubt keeps the branch as `{"kept": "<reason>"}`.

**2.4** In a repo with **no remote**, 2.1–2.3 are skipped entirely and the land
follows §4.8 instead.

---

## 3. Liveness and exclusion

**3.1 — Plan liveness is a recorded lifecycle state plus a renewable lease.
`os.getpid()` is never the answer.** s03b probe 2.1 measured the production
shape: `run.py ship-begin` printed `{"action": "invoke-skill", "skill":
"commit-orchestrate", …}` and **exited 0**, leaving a lock recording
`"pid": 19395` — a pid that `rsi._pid_alive()` reports **False** by the time the
skill it authorised runs. Two failures follow, both measured:

- probe 2.2 — the next contender reclaims the lock as stale and rewrites it to
  its own pid (19395 → 19396), with two `shipping_lock_acquired` events for one
  resource and no release between them. **Every** subsequent contender always
  sees a dead holder;
- a pid-keyed *registry* has the mirror defect: it calls every running plan
  abandoned. s01 measured `classify_plan` returning `lifecycle: active` with
  `lock.alive: False` — correct only because lifecycle is a recorded state, not
  a pid probe.

**3.2 — Every repo-scoped lock acquires by atomic exclusive create and carries a
lease token with an explicit expiry.** This is not a proposal; s02 shipped it and
measured it. The shape is normative and is stated here so §4 and §11 can rely on
it:

- one file per resource in the repo's **git common dir** —
  `<git-common-dir>/plan-locks/<resource-slug>.lock`, resolved with
  `git rev-parse --git-common-dir` so a linked worktree resolves to the *main*
  repo's `.git` (a `--git-dir` resolution would reintroduce the two-files defect
  one level up). Repos that are not checkouts fall back to
  `<root>/.plan-locks/`. Measured: `same_file: true`, `inside_git_dir: true` —
  where s03b measured `same file? False`;
- acquired with `O_CREAT|O_EXCL`, never check-then-write. Measured at a barrier:
  **1 winner of 4, 3 excluded, 3 trials of 3**, against s03b's 4-of-4;
- ownership is an opaque **token**, never a pid. **The sole authority for the
  token is `$GIT_COMMON_DIR/plan-state/<plan-slug>/lease.json`** (§8.d), not
  `run_state.json`. Any copy in `run_state.json` is an explicitly
  non-authoritative mirror for human reading; when the two disagree, `plan-state/`
  wins and the mirror is rewritten from it. *Why this is spelled out:*
  `run_state.json` is a tracked file in the outer `_plans/` tree, which §8.d
  measures as sweepable by a schema-6 neighbour's `git add -A` mid-run — putting
  the one value the lease depends on in the one place §8.d exists to evacuate.
  The same split applies to §3.5's lease events: `run.ndjson` is the
  human-readable record, `plan-state/` is the decision input;
- the lease dies at a wall-clock `expires_at`;
- `deploy:` and `gate:` resources stay **plan**-scoped in
  `<plan_dir>/_shipping_locks/`. Only `git:` and `push:` are repo-scoped.

**3.3 — The lease duration is 2 × the guarded step's timeout + 900 s of slack,
floored at 3600 s.** The number is stated, not implicit, and so is the reason:
the guarded step's own timeout is the only hard bound the system has, while the
orchestrator's turns around it (dispatch, the Skill tool's reasoning,
`ship-record`) are unbounded — hence the doubling plus a flat slack. The 3600 s
floor matches `rsi.STALE_LOCK_SECONDS` so a lease is never shorter than the run
lock beside it. With today's 600 s commit step this yields **3600 s**, measured
in the shipped directive as `lease_seconds: 3600`.

**3.4 — NOTHING RENEWS A LEASE WHILE THE GUARDED OPERATION IS IN FLIGHT, AND THE
CONTRACT SAYS SO RATHER THAN IMPLYING OTHERWISE.** `run.py` exits before the
directive runs. There is no heartbeat. The lease is therefore sized (3.3) so that
**expiry means the holder is gone, not merely slow** — that sentence is the whole
justification for the number, and if operations ever start outliving their lease
the fix is a heartbeat process, not a bigger number.

**R6 — §3.4 no longer holds for `land`'s own `git:` lease.** ~~NOTHING
RENEWS A LEASE WHILE THE GUARDED OPERATION IS IN FLIGHT~~ is now true only for
`push:`, `deploy:`, `ship-begin`, `finish` and worktree-setup leases. `land` runs its steps inside
its own process, so it renews between steps, before each gate and before each
push, and a live holder on this host is not taken over (§17.4). The measurement:
a land outlived its 4500 s lease while still running
(`_plans/owner-attention-fixes-2026-11-12/_decisions/land-followups.md`).

**3.5 — EXPIRY ALONE NEVER TRANSFERS OWNERSHIP.** A contender may reclaim an
expired lease **only after recording the takeover** in three places: the new lock
file (carrying the displaced holder's token, plan, host, pid and `expires_at`),
the taker's `run.ndjson` as `shipping_lease_taken_over`, and the victim's
`run.ndjson` as `shipping_lease_lost`. The original holder's `ship-record` then
**REFUSES**: `{"action": "failed", "reason": "lease-lost", "lease_status":
"taken-over"}`, the step is left `running` and never marked `done`, the plan
halts, and a `decision_brief` names the options. Measured end to end in s02 §5,
with the neuter-once control in §6 (lease intact → `action: done`).

**R6:** for a same-host `land` lease whose holder is alive, expiry
never permits a takeover, however old the lease is (§17.4). Only a dead holder
is taken over: pid not alive, or pid alive with a different `pid_start`. That
takeover is recorded exactly as above. A hung live holder stays until the owner
removes the lock file, as today's refusal message says (ship_locks.py:192-197).

**3.6 — Every holder re-verifies its token immediately before each git mutation
and immediately after it.** Before, so a takeover that happened while the
directive was thinking is caught before it writes; after, so a takeover that
happened *during* the write is detected rather than silently overwritten. A
post-mutation verification that fails is a **park**, not a rollback — the write
already happened, and the operator needs to see both writes, not one of them
undone.

**3.7 — THE NAMED, BOUNDED GAP: `/commit-orchestrate` is a Skill directive, not a
subprocess this lock wraps.** `run.py ship-begin` returns a directive
(`{"action": "invoke-skill", "skill": "commit-orchestrate", "lease_token": …}`)
that the **orchestrating conversation** executes. The token *is* threaded into
the directive — s02 measured `directive carried lease_token: e221d4da…` — and
`ship-record --lease-token` refuses a token that was taken over. What the
contract **cannot** guarantee:

- nothing forces the orchestrating conversation to carry the token from the
  directive to `ship-record`; a conversation that drops it gets a refusal, not a
  silent success, which is the safe direction but is still a gap;
- nothing confines `/commit-orchestrate`'s own git commands to the lease window.
  The lease **detects** an overlap; it does not **prevent** one;
- an orchestrating conversation is not a process, so no OS-level exclusion
  (flock, pid) is available against it at all.

This is a bounded gap with a stated boundary: the exclusion this contract
provides is between *`run.py` invocations*, and the detection it provides covers
*everything recorded through `ship-record`*. Anything a conversation does with
raw `git` outside that path is outside the lease. Do not read §11's matrix as
covering it.

---

## 4. The land protocol

**This section is written from probe 1's transcript. The plan's original sketch —
"sync main into the plan branch, merge into main under the repo lock" — was
measured to be impossible in its stated form and is not what follows.** Two of
its steps are refused by git outright, and the two overrides that would force
them through corrupt the operator's checkout with exit 0.

The steps are ordered. Order is normative.

### 4.0 — `<default>` is the RECORDED default branch, never the literal `main`

§1.3 pins `origin/<default>`; every command in §4 below writes `main` only as the
concrete instance measured in the probes. **`<default>` is resolved ONCE at
`begin`** — `git symbolic-ref --short refs/remotes/origin/HEAD`, falling back to
the local `HEAD`'s branch in a repo with no remote — and **recorded in
`$GIT_COMMON_DIR/plan-state/<plan-slug>/`** alongside the pinned base sha. Every
later stage reads it from there and never re-derives it.

Read every `main` in §4.1–§4.8 as `<default>`. A repo whose default branch is
`trunk` or `master` is fully supported and needs no change to this contract; an
implementation that hard-codes `main` would either fail outright or land onto the
wrong branch, and §13's BUILD decision rests on working in **any** target repo.

The land also asserts, immediately before the merge, that the recorded
`<default>` still resolves and still names the same ref it did at `begin`; a
default branch that was renamed mid-plan is a park with a decision brief, not a
guess.

### 4.1 — Locks FIRST, worktree second

Acquire the repo-scoped `git:` lease (§3) **before** creating the land worktree,
and the land worktree path is **unique per plan** —
`<repo>/.plan-worktrees/<plan-slug>__land-<short-token>/` — a **sibling** of the
plan worktree (§1.1a), never a fixed `_land` and never nested inside it.

*Why:* with a fixed path, two landers collide on `git worktree add` *before*
either reaches the lease, and the loser's error is a filesystem error whose text
says nothing about plans. Uniqueness makes the path collision impossible and
leaves the lease as the single arbiter.

**4.1a — EVERY worktree-administration sequence runs under the `git:` lease, not
just the land's.** §11's matrix already classifies worktree administration and
pruning as repo-coordinated, but the draft named the lease only here, leaving
`begin` and cleanup — which create, lock, remove and prune worktrees and refs —
unprotected. The atomic order is fixed for all three (`begin`, land, cleanup):

1. **acquire** the repo-scoped `git:` lease;
2. **classify** ownership of every ref and worktree the sequence will touch
   (§1.5) — UNKNOWN or CONFLICT refuses here, before anything is written;
3. **claim** in `$GIT_COMMON_DIR/plan-state/<plan-slug>/` — the state file is
   written BEFORE the ref or worktree exists, never after;
4. **create** the ref, then the worktree (or, at cleanup, remove in reverse:
   worktree, then ref);
5. **release** the lease.

*Why the claim precedes creation:* a crash between 4 and 5 leaves a claimed
resource that a sweep correctly reports as owned. A crash with the reverse order
leaves a branch and worktree that map to no state file — the permanently
untouchable UNKNOWN of §1.5, creatable by an ordinary interruption.

**Rollback on partial creation:** if step 4 fails halfway (ref created, worktree
add failed), the sequence removes what it created and clears the claim **while
still holding the lease**, then reports the failure. It never leaves the lease to
expire holding a half-built plan.

Concurrent `begin` operations are therefore serialised by the same lease that
serialises landers, which is what stops two of them racing on the registry or
both classifying the same ref as UNKNOWN.

### 4.2 — The land worktree is DETACHED at a captured `origin/main` sha, and `origin/main` advances by compare-and-swap push

```
EXPECTED=$(git rev-parse origin/main)                     # captured, recorded
git worktree add --detach <land-path> "$EXPECTED"
git -C <land-path> merge --no-ff -m "land <plan-slug>" plan/<plan-slug>
git -C <land-path> push --force-with-lease=main:"$EXPECTED" origin HEAD:refs/heads/main
```

Three things this sequence is chosen for, each measured:

- `git worktree add <path> main` (attaching the branch) is **refused, exit 128**:
  `fatal: 'main' is already used by worktree at '<primary>'`. Detached is not a
  stylistic choice; it is the only form that works.
- `git push origin HEAD:main` from the detached worktree is the **one thing that
  works**: exit 0, `7cec36b..612a5c0 HEAD -> main`, the bare origin advances and
  local `main` does not move.
- `--force-with-lease=main:$EXPECTED` makes the push a **compare-and-swap**.
  **Measured in a scratch fixture, both directions** — because a lease
  that cannot refuse is not a lease:
  - *known positive* — `origin/main` still at `$EXPECTED`:
    `git push --force-with-lease=main:$EXPECTED origin HEAD:refs/heads/main`
    → **exit 0**, `34a01bc..e7aebba  HEAD -> main`;
  - *known negative* — another party advanced `origin/main` first, our
    `$EXPECTED` now stale: → **exit 1**,
    `! [rejected]        HEAD -> main (stale info)`.

  Note the lease's refname is the **remote** ref (`main`), while the refspec
  pushes `HEAD:refs/heads/main`; the two differ and the combination was measured
  to work. Without the lease the only protection is the fast-forward check —
  measured separately as `! [rejected] HEAD -> main (non-fast-forward)` and, in
  s03b, as `(fetch first)` — which is real but says nothing about *which* sha you
  believed you were building on.

**FORBIDDEN BY NAME, all four measured:**

| Command | Measured | Why forbidden |
|---|---|---|
| `git branch -f main <sha>` | exit 128 `cannot force update the branch 'main' used by worktree at …` | refused anyway; never worth attempting |
| `git push . HEAD:refs/heads/main` | exit 1 `remote rejected … branch is currently checked out` | `receive.denyCurrentBranch` refuses |
| `git fetch --update-head-ok origin main:main` | **exit 0 — and CORRUPTS** | `main` moves, the operator's index/worktree are not updated: `MM README.md`, a phantom staged deletion `D y.txt`, and `git diff --stat HEAD` reporting a change nobody made |
| `git update-ref refs/heads/main HEAD` | **exit 0, no output, no guard at all** | HEAD moved out from under a live working tree; the operator's checkout becomes `[ahead 1]` with a staged deletion they never made |

The last two are the dangerous ones precisely because they succeed. Any
implementation that reaches for them has misread this section.

**A primary checkout on a different branch does not free `main`** — probe 1 (d2)
measured the guard simply moving to whichever worktree now holds `main`, and the
worktree holding it left `[behind 1]` with a stale working tree. There is no
configuration of the repo in which attaching `main` becomes safe.

### 4.3 — The operator's local `main` is ~~NEVER moved~~ NEVER moved by `land` (R1), and the land brief says exactly what to run

Land leaves the operator's checkout ~~**clean and behind**~~ **behind, and not always clean (R1)**: measured
`## main...origin/main [behind 2]`, working tree unchanged, the landed file not
yet present. The land brief MUST print, verbatim and copy-pasteable:

```
git -C <worktree-that-owns-main> fetch origin && \
git -C <worktree-that-owns-main> merge --ff-only origin/main
```

measured to fast-forward cleanly (exit 0) on a clean tree.

**The `-C <worktree-that-owns-main>` is normative, not decoration.** s03b probe 1
(d2) measured `main` living in a *different* worktree while the primary checkout
sat on another branch — and in that state the primary checkout's own
`git merge --ff-only origin/main` would merge into whatever branch the primary
has out, leaving the real `main` stale and the operator none the wiser. The land
brief therefore RESOLVES the owning worktree before printing the command:

```
git worktree list --porcelain | awk '/^worktree /{w=$2} /^branch refs\/heads\/main$/{print w}'
```

and prints that path inside the command. If no worktree holds `<default>` at all
(every one is detached), the brief says so and prints
`git fetch origin && git update-ref refs/heads/<default> origin/<default>` —
safe precisely because no working tree has it checked out, which is the one
condition §4.2's forbidden-list entry for `update-ref` does not cover.

**If local `<default>` is dirty, ahead, or diverged**, the brief says which and
stops recommending a fast-forward: dirty → commit or stash first (measured exit 1
otherwise, below); ahead or diverged → the operator has local commits and must
merge or rebase deliberately, which is not something this contract automates. And it MUST carry the
one caveat, also measured: if the operator has **uncommitted edits to a file the
landed commits touch**, that command fails exit 1 —
`error: Your local changes to the following files would be overwritten by merge`
— and they must commit or stash first. `git pull --ff-only` fails identically.

*Why this is a rule and not a footnote:* silence here is what makes the next plan
cut its base from a stale ref. An operator whose `main` is quietly two commits
behind will start the next plan from it, and §1.3's pinned base will faithfully
record the stale sha.

#### R1 — `finish` fast-forwards the owner, only when provably safe

**Revised through §14.** The measurement: after the model-rating-and-gateways
land, `git status` in the primary checkout showed `LAND_NOTICE.txt`,
`_worktrees/g1.json` and `_plans_index.md` modified, so the printed command
alone left the checkout dirty and behind. The operator decided.

So "clean and behind" above is struck. `land` still never moves
local `<default>`, and the land brief still prints this section's command.
Everything in §4.2's forbidden list stays forbidden. The new `finish` step
(`finish-contract.md`) fast-forwards the worktree that owns `<default>` with
`merge --ff-only <target_sha>` (a sha pinned after its own push, never a
moving ref) **only when the owner's `HEAD` is an ancestor of that sha, and
every dirty path there is this plan's own record (`_plans/<slug>/**` or
`_plans_index.md`) or `.gitignore`, unstaged, and byte-identical to that sha's
copy**. All of that is proven before any restore; an owner that is ahead or
diverged blocks (`owner-not-behind`). It then restores exactly those paths. If
the merge still fails after a restore, it reports `ff-failed` and prints the
commands; it never retries or resets. An untracked
`LAND_NOTICE.txt` or `_worktrees/` file of this plan (R4) is left in place and
does not block. Otherwise it changes nothing and prints this section's command,
naming each blocking path and the plan that owns it. Another plan in flight is
such a blocker, so with concurrent plans the fast-forward is expected not to
fire. The
`dirty-plan-record-only` advice in `land_brief.catch_up`
(`git checkout -- _plans/<slug>`, no byte check) is superseded by this rule: a
record path that differs from origin is never discarded.

### 4.4 — ONE merge strategy: ALWAYS `--no-ff`, ALWAYS re-gate

**Decided: every land produces a merge commit, and the re-gate always runs. There
is no fast-forward path and no skip-on-unmoved-main path.**

The plan's draft asserted both a merge commit and a permitted fast-forward, and
both an always-run and a skippable re-gate. An implementation cannot satisfy both,
and leaving it open would let every downstream session and test pick either.

The case *for* fast-forward is Git Town's, taken from its documentation rather
than re-derived: its `fast-forward` ship strategy runs `git merge --ff-only` and
exists to "prevent false merge conflicts when using stacked changes" and to
"allow to Ship several branches in a stack without unnecessary CI runs", and it
requires that "your feature branch must be up to date, i.e. the main branch must
not have received additional commits since you last synced". Git Town's own
`always-merge` strategy is documented for the opposite benefit: `git merge
--no-ff` "allows visually grouping related feature commits together which may aid
in understanding project history".

Both of Git Town's fast-forward benefits are **stack** benefits, and a plan is
not a stack — it is one branch, landed once. Neither applies. Against them stand
two things this contract needs:

- **§5's approval binding needs a distinct object to bind to.** The human ack
  records `{plan_head, main_head, gate_digest}`; a `--no-ff` land produces one
  merge commit that *is* the approved candidate and can be compared by sha. A
  fast-forward produces no such commit, so "did the thing I approved land?" has
  no answer expressible in git.
- **A clean textual merge does not imply a working tree.** The parallel-group
  contract already fixed this for groups (§3 rule 3: the integration session's
  gates must be a superset of its members'); the same semantic break — one
  session renames a symbol, another adds a caller of the old name — merges
  without complaint at the plan level too. The re-gate is the only thing that
  catches it, so it is not skippable on the grounds that `main` did not move.

#### R2 — the re-gate adds every `at_land` gate, and a red one can be repaired

**Operator decision.** The re-gate's gate set is the plan's union
of verify gates **plus every gate the candidate merged tree's registry
(`<land_path>/.claude/eval-gates.json`) flags `at_land: true`**
(`finish-contract.md`). Those gates enter the gate digest (§5.1) like any
other. When every non-passing gate is an `at_land`, `kind: "argv"` gate whose
outcome is `fail`, `land` runs each once on the base (`expected`). A gate that
also fails there with the same failures parks `gate-inherited`: the plan did not
break it. With other failures it parks `gate-base-differs`, which shows both
excerpts and does not say who broke it. Otherwise
`land` returns the `land-repair` directive instead of `land-parked`. The plan
branch is fast-forwarded to the candidate's `HEAD`, a repair session is added
with new items `add-session` accepts, and `land` runs again. `land` first
checks that repair session against the registry `add-session` reads
(`project_root_for(plan_dir)`), and parks `repair-gate-unresolvable` when it
fails. Registry discovery fails closed: an unreadable registry parks
`at-land-registry-unreadable`, never drops a gate. A gate whose timeout exceeds
540 s means the orchestrator runs `land` in the background and waits with
`Monitor`. At most 3 rounds; round 3 failing, or the same failing set twice
in a row, parks. Any other failed, skipped, blocked or on-box result parks as
before. The re-gate still always runs; nothing here skips it. A repo with
`.github/workflows/*.yml` and no `at_land` gate gets one warning line in the
land review brief.

### 4.5 — Re-sync is bounded at 3 attempts, then park

A `! [rejected] HEAD -> main (fetch first)` — measured verbatim when a second
plan pushed first — is **not a failure**. It is: `git fetch origin`, `git merge
--no-ff origin/main` into the detached land HEAD, re-run the gates (§4.4), push
again with a **freshly captured** `EXPECTED`. Measured recovery: exit 0.

Bounded at **3** attempts. On the fourth rejection the land **parks** with a
decision brief naming every attempt's expected and actual `origin/main` sha.

**4.5a — When a re-sync may retry unattended, and when it must go back to the
human.** §5.2 invalidates the ack on a re-sync, and read flatly that would make
attempt 2 unreachable and the bound of 3 a guard against a loop that cannot spin.
It is therefore carved out explicitly:

- **Retry unattended** while BOTH hold: the plan-side tree is unchanged (the merge
  needed no resolution, so the plan's contribution is byte-identical), AND the
  re-gate produces the **identical `gate_digest`**. The human approved *this
  plan's work against a passing gate set*; neither of those facts changed, and
  `main_head` moving under an unattended retry is recorded in the ack, not hidden.
- **Return to `AWAITS_REVIEW`** the moment either changes: the merge required a
  resolution, or the re-gate digest differs. Then §5.3's diff view shows the
  human the new `origin/main` commits and the changed gate results — the two
  things that actually differ — not the plan again.

Without this carve-out an implementer must choose between an unattended lander
and one demanding up to three acks per land, and no test could adjudicate.

**R6 — the carve-out is now §17.1's single decision.** ~~the plan-side
tree is unchanged (the merge needed no resolution, so the plan's contribution is
byte-identical), AND the re-gate produces the **identical `gate_digest`**~~ is
replaced by: `land_carry.decide` returns `carry` (§17.1 invariants 1-10), AND the
re-gate produces the identical **result digest**. The same decision now applies to
a main move found at the next `land` run, not only to a re-sync after a rejected
push. On a carry the owner answers and the review passes are kept, and the test
gates re-run (§17.1). "The plan-side tree is unchanged" means §17.1 invariant 8,
because `plan_head` always moves at `step_sync` (land_steps.py:64-71). The bound
of 3 is unchanged.

**R7:** with the §18.11 switch `on`, `land_carry.decide` may also
return `carry` on its code route (§18.2), and the re-sync runs inside phase 2's
one deadline (§18.4). The bound of 3 push attempts is unchanged. A stale
rejection on the last attempt resets to phase 1 (`phase2-retries-exhausted`)
instead of parking `push-rejected`. In `off` and `shadow` this section stands
as written.

*Why bounded:* an unbounded re-sync loop against a busy `main` is a livelock that
looks like progress, and each round re-runs the whole gate set. Three is enough
to absorb ordinary contention between two plans and small enough that a repo
where `main` moves faster than a gate run gets a human, which is the correct
outcome.

### 4.6 — Conflicts ALWAYS park

A merge conflict during 4.2 or 4.5 is **never** resolved automatically. The land
parks, the land worktree is **left in place** with the conflict intact, and the
brief names the recovery commands the operator sees:

```
cd <repo>/.plan-worktrees/<plan-slug>__land-<short-token>
git status                 # read ALL of it — see the warning below
git diff --name-only --diff-filter=U
# resolve, then:
git add <resolved paths> && git commit
# then hand control BACK to the land — do NOT push by hand (see below):
run.py land-resume <plan-dir>
# or abandon this land attempt:
git merge --abort
```

**The recovery ends at `land-resume`, NOT at a `git push`.** An earlier draft
printed the push command here, which would have let a hand-resolved conflict land
code with no current plan record, no gates, and under an approval issued for a
different tree — bypassing §8.c, §4.4 and §5 in one step, at exactly the moment
the tree is least like what anyone approved. `land-resume` re-enters the protocol
at the top:

1. re-verify the lease token (§3.6) — the park may have outlived the lease;
2. re-run `record-plan` (§8.c) so the record matches the resolved tree;
3. re-run the full re-gate (§4.4) and compute a **new** `gate_digest`;
4. compare against the ack: a resolution always changes the plan-side tree, so
   §4.5a's unattended carve-out does **not** apply — the plan returns to
   `AWAITS_REVIEW` for a fresh human ack (§5.2);
5. only then push, with a freshly captured `EXPECTED`.

A conflict resolution is a human editing the merged tree. Nothing that follows it
may be exempt from the checks that cover an ordinary land.

**Read the whole `git status`, not just the conflict list.** Probe 3 scenario 4
measured a modify/delete conflict on one file staging a *sibling* deletion with
**no conflict marker at all** (`UD _plans/plan-old/PLAN.html` alongside
`D  _plans/plan-old/manifest.json`). Committing the resolution without reading
the full status removes a file nobody decided to remove.

### 4.7 — Cleanup is CONDITIONAL on a successful push

- **Push succeeded**, and the returned ref update names the expected new sha →
  follow §8.c2's four-step order: **publish the final record with a second CAS
  push first**, then delete the remote plan branch (§2.3), then unlock → remove →
  prune the plan worktree and **the land worktree last** (§12.3), then delete the
  local plan branch. Deleting the branch before the final record is published
  destroys the only other copy of it.
- **Push rejected** — protected `main`, non-fast-forward past the 3-attempt
  bound, a pre-receive hook — → **park with the rejection text**, and **KEEP the
  branch, local and remote, and keep the land worktree**. Nothing is cleaned up
  on a rejected push. The work exists only there.
- **No remote at all** → see §4.8. Cleanup does **not** run until §4.8's
  durable ref exists.

### 4.8 — Land in a repo with NO REMOTE (a first-class path, not an exception)

Everything above assumes an `origin`. §7.2 keeps non-remote targets supported, so
the no-remote land needs its own protocol rather than a sentence — without one,
§4.2 has no `origin/main` to capture, §8.c has no push for the record to ride,
and §4.3 forbids the only local ref move, which would leave the entire plan's work
hanging off a **detached worktree HEAD** that §12.3's teardown would orphan and
`gc` would eventually collect. That is total, silent loss of a plan, and it is the
default shape of any scratch or local-only repo.

The substitutions, in order:

1. `EXPECTED` is captured from the **local default branch** (`main`), not
   `origin/main`.
2. The land worktree is created detached at `$EXPECTED` and the merge happens
   there, exactly as §4.2.
3. **In place of the push, the land writes a durable non-branch ref**:
   `git update-ref refs/plan-lands/<plan-slug> HEAD`. This is safe where §4.2's
   forbidden `git update-ref refs/heads/main HEAD` is not, and the difference is
   the whole point: `refs/plan-lands/*` is **not a branch**, is checked out by
   nobody, and moving it cannot desynchronise any working tree. §4.3 is about
   `refs/heads/main`; it is not a blanket ban on writing refs.
4. `record-plan` (§8.c) runs before step 3, so the record is inside the commit the
   ref names.
5. **Cleanup is conditional on that ref existing** — the same shape as §4.7's
   "conditional on a successful push". No worktree is unlocked, removed or pruned
   until `git rev-parse --verify refs/plan-lands/<plan-slug>` succeeds.
6. The land brief says plainly: *"This plan landed **locally only**. The merge
   commit is `<sha>`, kept alive by `refs/plan-lands/<plan-slug>`. Nothing was
   pushed. To bring it onto your `main`, run:*
   `git merge --no-ff refs/plan-lands/<plan-slug>`*."* The brief names both the
   sha and the ref, because in a no-remote repo the ref is the only thing keeping
   the commit reachable.

The plan branch and the durable ref are **both** kept until the operator confirms
the merge. Nothing here deletes the only two references to the work.

**4.8a — MEASURED, both directions, 2026-08-21 (git 2.48.1, scratch fixture).**
This rule exists because an adversarial review predicted total, silent loss of a
plan's work here. The prediction was reproduced and the fix was proven:

- *known negative — no ref written.* Detached land worktree, `merge --no-ff`, then
  `git worktree remove --force` → `git worktree prune` → `git branch -D` the plan
  branch → `git reflog expire --expire=now --expire-unreachable=now --all` →
  `git gc --prune=now --aggressive`. The merge commit is **GONE**:
  `fatal: git cat-file: could not get object info`, **exit 128**. An entire
  plan's work, destroyed by routine cleanup, with no error at any step.
- *known positive — `git update-ref refs/plan-lands/<slug> HEAD` written first.*
  Identical teardown and identical aggressive `gc`. The merge commit **survives**
  (`git cat-file -t` → `commit`, exit 0), `refs/plan-lands/<slug>` still resolves,
  and the operator's `git merge --no-ff refs/plan-lands/<slug>` succeeds (exit 0),
  bringing the landed file into their tree.
- *and local `main` never moved*: measured identical to the pre-land sha
  (`3b87c2c…` before and after), confirming §4.3 holds on this path too.

The non-branch ref is therefore not bookkeeping — it is the only thing standing
between a no-remote land and permanent data loss.

---

## 5. Approval binding

**5.1** The human land ack is recorded against the exact revision approved:
`{plan_head, main_head, gate_digest, record_pathspec}`, where `plan_head` is the
plan branch sha shown to the human, `main_head` is the `origin/main` sha the
candidate was merged onto, `gate_digest` is defined canonically in §5.1c,
and `record_pathspec` is the literal `_plans/<plan-slug>/`.

**5.1a — The ack covers the merge commit AND the `record-plan` commit on top of
it, and nothing else.** §8.c commits the plan record as a *descendant* of the
approved merge, so the pushed head is not the merge sha the human saw. That is
accepted, bounded, and checkable rather than hidden: what is approved is *the
merge sha, plus at most one commit whose entire staged diff is confined to
`record_pathspec`*. The land verifies that before pushing —
`git diff --name-only <merge_sha>..HEAD` must yield only paths under
`_plans/<plan-slug>/` — and any other path invalidates the ack (§5.2).

The record's content is deliberately **not** pre-approved: `run.ndjson` is
appended to while the copy runs, so it cannot be, even in principle. The
guarantee offered is about the **path**, not the bytes, and this sentence exists
so no downstream session mistakes one for the other.

**5.1b — Gate ordering is fixed: the §4.4 re-gate runs AFTER `record-plan`, on
the exact tree that will be pushed**, with `_plans/<plan-slug>/` excluded from the
gate's file set (§10.1). Gating the merge and then pushing a descendant would gate
a tree that never ships.
The path prefixes in the repo-root `.review-exclude` are excluded too, read from
the BASE commit (`git show <expected>:.review-exclude`), never from the plan's
tree: a plan cannot drop its own code from its land review by editing the list.
It is for stored data a reviewer cannot read in one pass, such as defect samples
that hold known defects on purpose (added).

**5.1c — `gate_digest` is defined canonically, because an undefined digest is not
a binding.** "A digest over the gate results" leaves the algorithm, the ordering
and the normalisation to each implementer, and two implementations would compute
different digests for the same land — making §5.2's invalidation unenforceable.
Fixed:

- **Input:** for each gate that ran, the triple `(gate_id, outcome, findings_count)`,
  where `outcome` is one of `pass` / `fail` / `skipped`.
- **Ordering:** sorted by `gate_id`, byte-wise ascending.
- **Serialisation:** JSON with sorted keys, no whitespace, UTF-8
  (`json.dumps(..., sort_keys=True, separators=(",", ":"))`).
- **Algorithm:** SHA-256 of those bytes, lowercase hex.

Deliberately **excluded** from the digest: timestamps, durations, machine names and
free-text finding bodies — all of which change between two runs that reached the
same verdict, and would make every re-gate look like a changed result.

**5.1d — The binding is CHECKED, twice, or it is decoration.** The land re-compares
`{plan_head, main_head, gate_digest}` against the recorded ack **immediately before
the merge** and **immediately again before the push** (the same before/after
discipline §3.6 applies to the lease). Any mismatch blocks the push and returns the
plan to `AWAITS_REVIEW` — it is never reported as a warning and never pushed "since
we're already here".

**R6:** one exception, and only one. When `plan_head`, `main_head` or
`merge_sha` differ and `land_carry.decide` returns `carry` (§17.1), the ack is
re-bound to the new values and the re-bind is appended to `ack.carries`. A
different result digest is never re-bound and always blocks the push (§17.1
invariant 13). The before-the-merge comparison no longer clears the ack: the
decision needs the new candidate, so it runs once in `step_gate` after the merge
(§17.1, "The ack, in order"). The before-the-push comparison in `step_ack` is
unchanged.


**5.2** Any of the following **INVALIDATES** the ack and returns the plan to
`AWAITS_REVIEW`: a re-sync that changed the plan-side tree or the gate digest
(§4.5a — a re-sync meeting **both** unattended conditions does **not**
invalidate), **a main move for which `land_carry.decide` returns `reset` (R6,
§17.1)**, a hook rewriting the tree during the merge commit, an amended commit
on either side, a changed gate result, or **`record-plan` (§8.c) staging anything
outside `_plans/<plan-slug>/`**.

**R7:** "a main move for which `land_carry.decide` returns `reset`"
also covers a reset from phase 2's limits (`phase2-budget` excepted, which never
enters phase 2): `phase2-deadline`, `gate-overrun`, `phase2-retries-exhausted`,
`carry-streak` and `carry-stale` (§18.4, §18.10). A code-route carry (§18.2)
does not invalidate.

*Why:* without this the operator approves one merge candidate and a different one
lands. This is not hypothetical — §4.5's re-sync **by construction** produces a
different merge commit onto a different `main_head`, and it is the normal path
under two-plan contention, which probe 1 measured as "not hypothetical".

**5.3** Re-approval after an invalidation shows the human the **diff between the
approved candidate and the new one**, not the whole plan again. An approval flow
that re-presents 40 files trains the operator to click through it, which is worse
than no gate.

---

## 6. The version gate

**6.1** Isolation applies **only** to manifests stamped `plan_schema_version >=
7`. A missing or non-integer version is treated as below the threshold — the same
fail-low pattern the parallel-group contract §6 uses.

**6.2** CLI overrides: `--isolate` forces isolation on for one run;
`--no-isolate` forces it off. Both are recorded in `run.ndjson` with the
manifest's actual version, so a run that behaved unlike its stamp is legible
afterwards.

**6.3 — MEASURED, not asserted.** Swept 2026-08-21 over **every** `manifest.json`
under `_plans/` in this repo:

| `plan_schema_version` | Manifests |
|---|---|
| 2 | 24 |
| 5 | 2 |
| 6 | 2 |
| **≥ 7 (would cross the gate)** | **0** |
| **Total scanned** | **28** |

Both numbers are reported: **28 scanned, 0 crossings**. A sweep that found
nothing must also report what it looked at, or "zero findings" and "I looked at
nothing" are the same result. The reproduction command is one line:

```
for f in _plans/*/manifest.json; do python3 -c "import json;print(json.load(open('$f')).get('plan_schema_version','MISSING'))"; done | sort | uniq -c
```

**6.4 — THE STAMP IS THE LAST ACTIVATION STEP.** `plan-builder` does **not**
begin emitting `plan_schema_version: 7` until the end-to-end proof (s10) has
passed. Stamping first would activate isolation for every plan built between the
stamp and the proof, on a path nothing has yet run end to end. The stamp is the
switch; it is thrown after the wiring is tested, not before.

**6.5** The stamp is a compatibility mechanism, not a security boundary.
Hand-editing a manifest to 7 turns isolation on for a plan nothing else prepared.
Accepted, as in the parallel-group contract: these gates defend against drift,
not against an operator defeating them deliberately.

**6.6 — ACTIVATED 2026-08-23 (s10).** The switch in 6.4 was thrown:
`build_plan.PLAN_SCHEMA_VERSION` is **7**, so every plan built from that date
isolates by default. Three things were true before it was thrown, and each has a
receipt rather than an assurance:

- the two-plan end-to-end proof is green, with a recorded neuter-once failure —
  `_evidence/s10/two-plan-e2e.json`, `_evidence/s10/neuter-once.txt`;
- one throwaway v7 plan ran end to end in a real clone of this repo, with a real
  dispatched subagent, this repo's real `githooks`, the real argv ship and a real
  land — `_evidence/s10/canary-real-plan.txt`;
- 6.3's sweep was RE-RUN at the bump and still reports zero crossings, with the
  scan count intact: **29 scanned, 0 crossings**
  (`_evidence/s10/activation-sweep.json`). Every plan already on disk keeps
  running in the shared checkout.

**6.7 — TWO ISOLATED PLANS DO NOT NEED `--concurrent`.** REG-02's `begin`
refusal exists because two plans dispatching into the SAME working tree race it.
Two plans at or above this gate have no shared working tree, so it does not fire
for them, and the run is logged as `concurrent_begin_isolated`. It still fires,
and still needs the override, when any active neighbour is below the gate or was
begun `--no-isolate` — §8.d is explicit that a sub-7 neighbour's repo-wide
`git add -A` reaches a v7 plan's live state and nothing the v7 plan does can
stop it. The judgement is on what a neighbour will DO, never on its stamp alone,
and it fails LOW: an unreadable neighbour still refuses.

---

## 7. Refusals

**7.1 — Submodule-bearing repos are REFUSED.** If `.gitmodules` exists at the
pinned base, `begin --isolate` refuses, naming the file. `git-worktree(1)`'s own
BUGS section records that multiple superproject checkouts are not recommended:
submodule state lives partly in the superproject's `.git/modules`, which linked
worktrees share, so two worktrees at different submodule revisions fight over one
gitdir. The refusal is a hard stop, not a warning — a partially-checked-out
submodule is exactly the kind of damage that looks like a clean tree.

**7.2 — Non-git targets keep today's behaviour**, unchanged and unwarned. A plan
in a directory that is not a checkout runs exactly as it does now; isolation is
simply not available, and §3.2's lock falls back to `<root>/.plan-locks/` as it
already does.

---

## 8. The plan's own directory

`_plans/**` is a **tracked path inside the repo being isolated**. That single
fact is the source of every hazard in this section: `git worktree add` checks out
a **frozen copy** of `_plans/<plan-slug>/` into the plan worktree while the
orchestrator keeps writing the **outer** one. This is one rule with four parts,
and the parts are not separable.

**8.a — The orchestrator writes the OUTER tree, for the whole run.** `PLAN.html`,
`manifest.json`, `run_state.json`, `run.ndjson`, `_closeouts/`, `_verify_state/`
and `_evidence/` are read and written in the **primary checkout**, never in the
plan worktree's frozen copy. `plans-status`, the registry and the operator's
dashboard all read the primary checkout; a run whose state lived in the worktree
would be invisible to all three.

**8.b — No commit made from inside the plan worktree may stage any path under
`_plans/`.** Not on the plan branch, not on a group branch. The worktree's copy
is stale by construction (8.a), so staging it commits state that was already
wrong when the worktree was created. Enforced by an explicit pathspec at every
staging site inside a plan worktree, never by a `.gitignore` — the path is
tracked, so ignoring it does nothing.

**8.c — WHO commits the plan record, and WHEN: the `record-plan` step, inside the
land worktree, after the merge and before the push.** 8.b removes
`/commit-orchestrate`'s `git add -A` (`commands/commit-orchestrate.md:310`) from
this job, and a rule that removes the only thing committing `PLAN.html`,
`run_state.json` and every closeout without naming a replacement would leave the
plan record permanently uncommitted. The replacement is concrete:

```
cp -R <primary>/_plans/<plan-slug>/  <land-path>/_plans/<plan-slug>/
git -C <land-path> add -- "_plans/<plan-slug>"
git -C <land-path> diff --cached --quiet -- "_plans/<plan-slug>" \
  || git -C <land-path> commit -m "plan(<plan-slug>): record"
```

Four properties, each load-bearing:

- it copies the **live outer** directory in, so the record is current, not frozen;
- it stages with an **explicit pathspec** and never `-A`, so nothing else is
  swept in;
- it stages **only the plan's own directory** — never `_plans_index.md`, never
  another plan's directory. Probe 3 measured why: scenario 1 (own-directory-only)
  is the **single benign case**, where "files B's base never saw are simply
  carried through"; scenario 3 (a regenerated `_plans_index.md`) conflicts and its
  natural `--theirs` resolution **silently deletes plan-a's entry, exit 0, no
  warning**; scenarios 4 and 5 are the deletion and add/add cases. `_plans_index.md`
  is therefore regenerated **only** in the primary checkout, from the primary
  checkout's full view of `_plans/`, and is committed ~~by the operator's own
  workflow~~ by the operator's own workflow, or (R3) by `finish` for this plan's
  one row only — never by a plan branch and never from a stale base;
- the record therefore **rides the same push** that advances `origin/<default>`
  (§4.0). No
  local ref is written, so §4.3 still holds.

**8.c2 — Publishing the FINAL record, and why it needs its own push.** The land's
own closeout, the cleanup entries and the abandon record are all written *after*
the land push, so a single push cannot carry them. The draft said they were
"committed during the cleanup stage" and stopped there — which would have left the
final record on a detached worktree that cleanup then removes, never on
`<default>` at all, and the plan branch already deleted. That is a second silent
loss, and it is fixed by ordering rather than by hope:

The final record is published by a **second, explicitly-permitted `<default>`
mutation**, obeying every rule the first one does: a **fresh `git:` lease**, a
**freshly captured `EXPECTED`**, a `record-plan` staging confined to
`_plans/<plan-slug>/`, and a **compare-and-swap push**. A `(fetch first)`
rejection re-syncs under §4.5's bound.

It is **not re-gated and not re-approved**, and that exemption is earned
mechanically rather than asserted: its staged diff is verified to contain **only**
paths under `_plans/<plan-slug>/` (`git diff --name-only <EXPECTED>..HEAD`) and
therefore no code. A diff naming any other path **parks** instead of pushing.

**The order is normative and it is what §4.7 must follow:**

1. land push (merge + record) succeeds and is verified;
2. **final-record push succeeds and is verified**;
3. *then* delete the remote plan branch (§2.3);
4. *then* unlock → remove → prune the worktrees (§12.3), **land worktree last** —
   it is where step 2 happens, so it cannot be removed before it.

In a **no-remote** repo, step 2 is instead
`git update-ref refs/plan-lands/<plan-slug> HEAD` re-pointed at the final record
commit (§4.8), and step 3 is skipped; the durable ref is what keeps the final
record reachable, exactly as §4.8a measured.

**R3 — a third, explicitly-permitted `<default>` mutation: the
`finish` record push.** The measurement is R1's: records written after the
final-record push, and this plan's `_plans_index.md` row, were left modified in
the primary checkout. Before any write, `finish` resolves the remote as
`land_state` does (`origin`, else the sole remote; several and no `origin`
parks), fetches once and pins `origin_sha`, and proves ancestry: the landed sha
(isolated) or local `<default>` (non-isolated) must be an ancestor of
`origin_sha`. A non-isolated plan whose local `<default>` is ahead or diverged
parks `local-ahead` and pushes nothing, because a record commit on origin's tip
would split the two histories. `finish` then publishes under every rule the
final-record push obeys: a fresh `git:` lease, `EXPECTED = origin_sha`, a
detached temporary worktree at it, a compare-and-swap push, re-sync bounded by
§4.5. It is not re-gated and not re-approved, and the exemption is earned the
same way: the commit may touch only `_plans/<slug>/**`; in `_plans_index.md`,
only lines containing `](_plans/<slug>/`; and in `.gitignore`, only appended
plan-builder ignore lines missing there (R4). All are applied to origin's
current copy, never regenerated from a stale base, which is what probe 3
measured deleting a neighbour's row. Anything else parks and pushes nothing. In
a no-remote repo it is skipped. A plan that completed before this shipped (no
`finish.json`) gets a report-only pass: no push without `run.py finish --apply`.

**8.d — A schema-6 neighbour's `git add -A` WILL sweep a v7 plan's live outer
state, and nothing the v7 plan does can stop it.** A neighbouring plan below the
version gate runs today's `/commit-orchestrate`, which stages the whole tree from
the primary checkout — including the v7 plan's `_plans/<plan-slug>/` **mid-run**.
Its staging is repo-wide and predates this contract; a v7 plan cannot narrow it.
Two consequences are fixed here:

- `record-plan` (8.c) MUST tolerate finding its directory already committed. That
  is what the `diff --cached --quiet ||` guard above is for: it commits only if
  the pathspec produces a non-empty staged diff, so a neighbour having already
  swept the record is a no-op, not an error.
- **Mutable runtime state that must not be swept does not live under `_plans/` at
  all.** The repo lease, the worktree/branch ownership registry and the land
  approval record (§5) live under **`$GIT_COMMON_DIR/plan-state/<plan-slug>/`** —
  outside every working tree, unreachable by any `git add -A` in any worktree,
  shared by every linked worktree, and gone when the repo is. Relocating them is
  the only answer to (d): as long as they are tracked files in the tree, a
  neighbour's blanket staging can commit a half-written lease or a mid-flight
  approval into `main`. This is the placement s02 **already shipped** for the
  repo lease (`<git-common-dir>/plan-locks/`, measured `inside_git_dir: true`),
  extended to the rest of the mutable state.

**8.e — EVERY file is classified RECORD or RUNTIME. There is no third class.**
§8.d moves the lease, the registry and the approval out of the tree, but
`run_state.json` and `run.ndjson` are themselves mutable and still under
`_plans/`, so "mutable state does not live under `_plans/`" was not yet true as
written. The full classification:

| File | Class | Lives in | Swept by a neighbour's `git add -A`? |
|---|---|---|---|
| `PLAN.html`, `manifest.json`, `spec.json` | RECORD | `_plans/<slug>/` (primary checkout) | yes, harmlessly — it is meant to be committed |
| `_closeouts/`, `_verify_state/`, `_evidence/` | RECORD | `_plans/<slug>/` | yes, harmlessly |
| `run.ndjson` | RECORD (append-only) | `_plans/<slug>/` | yes — append-only, so a swept prefix is still valid history |
| `run_state.json` | **RECORD MIRROR, non-authoritative** | `_plans/<slug>/` | yes — and that is why it is not authoritative for anything |
| lease + token, worktree/branch ownership registry, land approval (§5), pinned base + `<default>` (§4.0), per-session `{session_base, session_head}` (§10.4) | **RUNTIME** | `$GIT_COMMON_DIR/plan-state/<slug>/` | **no — unreachable from any working tree** |

**Every decision input is RUNTIME; everything under `_plans/` is either history or
a mirror of a decision made elsewhere.** That is the invariant, and it is what
makes §8.d's admission survivable: a neighbour *will* sweep a half-written
`run_state.json` into `main`, and it does not matter, because nothing reads it to
decide anything. Recovery for an already-swept mirror is to rewrite it from
`plan-state/` on the next orchestrator write — no repair step, no version check,
because the authority never left the git dir.

The distinction is durable and worth stating once: **`_plans/` holds the plan's
RECORD — append-mostly history a human reads and git should version.
`$GIT_COMMON_DIR/plan-state/` holds the plan's RUNTIME — mutable coordination
state that must never be committed by anybody.**

**R4 — `LAND_NOTICE.txt` and `_worktrees/` are RUNTIME, kept
local by name.** R1's measurement: both were modified in the primary
checkout after a land, because `step_finish` rewrites `LAND_NOTICE.txt` and the
group cleanup rewrites `_worktrees/*.json` after the final-record push. §8.c2's
order puts that cleanup after the push, so reordering cannot fix it.
~~Both are added to plan-builder's `GITIGNORE_LINES` and to this repo's
`.gitignore`; `plan_record._drop_ignored` already keeps ignored files out of
the record.~~ A `.gitignore` line alone is not enough: plan-builder writes it
uncommitted, and `_drop_ignored` asks the land worktree, built from origin's
tree, which lacks the line. So `plan_record` never adds, changes or deletes
either **by name** (`RUNTIME_LOCAL`), in every repo: an untracked one is left
out, and one the target commit already tracks keeps that commit's bytes. Both also join
`GITIGNORE_LINES`, and `finish`'s record push carries any missing ignore lines
to origin (R3), so no person has to commit them. Copies that older plans
already committed stay tracked; one modified in the owner blocks R1's
fast-forward.

| File | Class | Lives in | Swept by a neighbour's `git add -A`? |
|---|---|---|---|
| `LAND_NOTICE.txt`, `_worktrees/*.json` | **RUNTIME, local** | `_plans/<slug>/`, never written or deleted by any record (by name), and gitignored | no — excluded by name |
| `finish.json` | **RUNTIME** | `$GIT_COMMON_DIR/plan-state/<slug>/` | no — unreachable |

Observed, not changed here: `GITIGNORE_LINES` also ignores `run.ndjson`,
`run_state.json` and `_closeouts/`, which the table above calls RECORD or
RECORD MIRROR. The ignore list decides what git actually commits.

---

## 9. Hooks

**A plan worktree where `pre-commit` silently exits 0 is strictly worse than
today's collision, because it looks green.** Today a hook failure is a visible
failed commit. A worktree whose hooks never run produces clean commits that
nothing checked, and the first signal is CI or a reviewer.

Two documented mechanisms make this plausible (ledger rows marked *documented,
not re-measured*): anthropics/claude-code#60620 — the harness writes an absolute
`core.hooksPath` into `$GIT_COMMON_DIR/worktrees/<wt>/config.worktree`, and
worktree-scoped config **beats** the shared value; and lefthook#1398 — shared
`.git/hooks` shims bake absolute paths that are wrong from another checkout.

**9.0 — Measured live in a real linked worktree, 2026-08-21, because §9.2's
assertion must be known to be able to find something.** In this repo's own
isolated worktree (`gearbox-iso`, a linked worktree of `your-private-harness`):

- `git config --show-origin core.hooksPath` →
  `file:/…/your-private-harness/.git/config` + `/…/your-private-harness/githooks` — the
  value comes from the **shared** config, not a worktree-scoped one;
- `$GIT_COMMON_DIR/worktrees/gearbox-iso/config.worktree` **does not exist**;
- the path it names exists and holds executable `pre-commit` and `commit-msg`.

So today, in this repo, the hooks **do** run from a linked worktree: an absolute
shared `hooksPath` resolves correctly from any worktree as long as the main
checkout is present. That is the benign half of lefthook#1398, and it means
**§9.2's assertion currently finds nothing** — which is exactly why §9.3's canary
exists beside it. A check whose only observed outcome is "clean" has not been
shown to work; the canary is what proves the hook path can be observed failing.

It also means the danger is conditional, not universal: the shared absolute path
breaks when the main checkout moves or the harness writes a `config.worktree`
(claude-code#60620). §9 defends against both without depending on either being
true right now.

**9.1** Worktree creation sets `git config --worktree core.hooksPath`
**explicitly**, to the resolved absolute path of the repo's real shared hooks
directory. It never relies on inheritance.

**9.2** Creation **asserts** that no *inherited* `config.worktree` hooksPath
survives: read `$GIT_COMMON_DIR/worktrees/<wt>/config.worktree` after creation
and refuse if it names a `core.hooksPath` other than the one 9.1 just set.
(`extensions.worktreeConfig` must be enabled for `--worktree` to take effect;
enabling it is part of creation.)

**9.3 — THE HOOK CANARY, and where it must NOT run.** Prove the hooks actually
run in this worktree, at this moment. The canary runs in a **unique temporary
hooks directory** and **never** by installing a failing hook into the shared
`$GIT_COMMON_DIR/hooks`.

*Why the temp directory is normative:* the shared hooks directory is shared by
every linked worktree **and by the operator**. A canary planted there can reject
an unrelated commit in another worktree or in the operator's own terminal, and
two concurrent `begin`s overwrite each other's canary — the second removes the
first's while the first is still measuring, and the first records a PASS it never
earned.

Sequence, in order:

1. create `<tmp>/hooks-canary-<token>/` and write a `pre-commit` that exits 1
   with a unique marker string, **`chmod +x`** it (a non-executable hook is
   skipped silently by git — which would look exactly like a passing canary);
2. `git config --worktree core.hooksPath <tmp>/hooks-canary-<token>`;
3. **stage a real, disposable change** — write `.plan-canary-<token>` and
   `git add` it — then attempt the commit. The staged change is normative: a
   commit with nothing staged can exit non-zero for having nothing to commit,
   which is not the hook firing, and `--allow-empty` would make a *different*
   commit than the one the real hooks will see. Require the commit to be
   **REJECTED with the marker present in the output**. A rejection *without* the
   marker did not come from the canary and is treated as an inconclusive probe —
   **refuse the plan**, exactly as a pass does, because an unproven hook path is
   the thing §9 exists to prevent. A commit that **succeeds** refuses the plan,
   naming the worktree and the hooksPath in effect;
4. `git reset` the staged canary file, delete it, and remove the temp directory —
   deterministically, in a `finally`, so a refusal does not leave the plan
   worktree carrying canary debris or a temp hooksPath;
5. set the final worktree-scoped `core.hooksPath` to the real shared hooks
   directory (9.1) and re-assert 9.2. **Then VALIDATE the final path itself**:
   it must exist, be a directory, and contain at least one executable hook file.
   A `core.hooksPath` pointing at a missing or empty directory makes every commit
   in this worktree hook-free while every command still exits 0 — the precise
   "looks green" failure, arrived at through the fix rather than the bug.

**9.4** A canary that **PASSES** (the commit succeeds) refuses the plan. This is
the whole point: a check whose only outcome is "clean" is not a check. The canary
is a known-positive probe of the hook path, and it is the reason §9 does not
depend on the claude-code#60620 issue still being true.

**9.5 — WHAT THE CANARY DOES NOT COVER, stated rather than implied.** Steps 1–4
prove that *a worktree-scoped `core.hooksPath` is honoured in this worktree* —
the claude-code#60620 class. They do **not** prove that the repo's **real** shared
hooks do useful work once step 5 swaps to them. That is the lefthook#1398 class:
the real hooks exist, are found, execute, and exit 0 having checked nothing
because a baked absolute path resolves to the wrong checkout. §9's own opening —
"strictly worse than today's collision, because it looks green" — describes
exactly that case, and the temp-dir canary is blind to it.

Therefore, after step 5, run a **second canary through the real hooks**: a
throwaway commit carrying a deliberate, cheap violation the repo's actual
`pre-commit` is known to reject (in this repo, a file exceeding its
`.file-size-exceptions` ratchet is one such). Require rejection; a success refuses
the plan.

**Where no such reliable violation exists in the target repo, there is no second
canary, and that is a NAMED RESIDUAL GAP, not silent coverage:** the plan proceeds
with a run-log entry saying the real hooks were not proven to check anything, and
the first canary's PASS must never be reported as if it had covered this.

---

## 10. Gate inputs

**10.1 — Two inputs, one per phase, and the phase decides which.** A gate reading
only working-tree status goes blind the moment a session commits (`git status` is
then clean and reports **zero** files, which §10.3 would surface as a warning but
which an implementer could easily read as a pass). A gate reading only a commit
range misses everything not yet committed. The contract fixes both:

| Gate phase | Input | Why |
|---|---|---|
| **Pre-commit** (a session's work is still in the tree) | `git status --porcelain=v1 -z` — **staged, unstaged, renamed, deleted AND UNTRACKED** | uncommitted work exists nowhere else |
| **Post-commit** (session closed; the land re-gate; any rework round) | the commit range **`<session_base>..<session_head>`**, both recorded (§10.4) | committed work is invisible to `status` |

**`git diff HEAD` is forbidden as a gate input in both phases.**

A gate that spans the boundary — the §4.4 land re-gate is one — takes the **union**
of the range and the status set, deduplicated by path.

**10.2** `-z` is normative, not decoration: NUL-delimited output is the only form
that survives paths containing spaces, quotes or newlines without git's own
quoting, and a rename record's two paths are unambiguous.

*Why:* `git diff HEAD` **excludes untracked files entirely**. A session whose
output is all-new files — which describes most new-module sessions — produces an
**empty** `git diff HEAD` and passes every gate that reads it, after which
`git add -A` commits code nothing examined. The failure signature is
indistinguishable from a genuinely clean run: zero findings, exit 0. It is the
same shape as a check that returned "clean" because its input was empty.

**10.3** A gate whose derived file set is **empty** must say so explicitly in its
result — `files_examined: 0` — and an empty set on a session that closed `DONE`
is a **warning**, not a pass. An unexamined zero is a check pointed at nothing.

**10.4 — The surface is bounded at BOTH ends, and both ends are RECORDED.** A
session's review surface is its **own commits**, not
`base..whatever HEAD has become`. Concretely, `$GIT_COMMON_DIR/plan-state/<plan-slug>/`
records, per session and **per rework attempt**, a `{session_base, session_head}`
pair: `session_base` is pinned at that session's first dispatch and never moves;
`session_head` is re-recorded at each close, so attempt *n*'s surface is exactly
`session_base..session_head@n`. That pair is what §10.1's post-commit row consumes,
and it is the only thing that makes "my work" a computable set. Measured on this plan's own
s02: 41 files at rework attempt 3, **44 at attempt 4** — fixing the findings
enlarged the sample they came from. Two corollaries, which change how a verdict
reads:

- a rework round that finds **nothing** is weaker evidence than it looks;
- a round that finds something **new** is weaker evidence of *regression* than it
  looks.

Path scope cannot express this bound, because two sessions legitimately share a
path. Only a commit **range** or an isolated worktree makes "since my base" and
"my work" the same set — and there is no supported range bound today. **The plan
worktree is what provides it.** This is the strongest argument in this plan for
isolating *before* commits interleave rather than scoping *after*.

Operationally, for a session at its rework ceiling: separate findings **inside**
the delta (real — fix them) from findings in files an earlier attempt never
touched (the sample), and re-dispatch with `--reason` naming the sampling rather
than treating a draw from a distribution as a defect list.

---

## 11. The shared-`.git` operation matrix

**The loose claim that "`index.lock` contention means git operations stay
serialized in the orchestrator" is REPLACED. It is false in two independent
ways:** separate worktrees have separate `HEAD` and `index` files, so they never
contend on `index.lock` at all; and two *orchestrating conversations* are not
serialized by one conversation's sequencing.

Per operation class:

| Operation class | Coordination | Mechanism |
|---|---|---|
| Working-tree edits, `add`, `commit`, `status`, `stash` **within one worktree** | **Independent** | Each linked worktree has its own `HEAD`, `index`, `MERGE_HEAD`. Two worktrees never contend. |
| Ref updates on **distinct** plan branches (`plan/<slug-a>` vs `plan/<slug-b>`) | **Independent** | Separate ref transactions; git locks per-ref (`refs/heads/<name>.lock`), not repo-wide. |
| Updates to **`refs/heads/main`** | **Repo-coordinated — LEASE REQUIRED** | §3 `git:` lease + §4.2 compare-and-swap push. Two landers must not interleave merge-and-push. |
| Shared config (`$GIT_COMMON_DIR/config`) | **Repo-coordinated — LEASE REQUIRED** | One file, every worktree. Per-worktree settings use `--worktree` (§12.2) and need no lease. |
| Shared hooks (`$GIT_COMMON_DIR/hooks`) | **Repo-coordinated — LEASE REQUIRED** | Shared by every worktree *and the operator*; this is why §9.3's canary is banned from it. |
| Worktree administration (`add`, `lock`, `unlock`, `remove`, `move`, `repair`) | **Repo-coordinated — LEASE REQUIRED** | Mutates `$GIT_COMMON_DIR/worktrees/` for the whole repo. |
| Sweep / `prune` / stale-worktree reaping | **Repo-coordinated — LEASE REQUIRED** | Reads and deletes other plans' administrative state; §1.5's UNKNOWN class applies. |
| **Same-ref** changes by two holders | **Compare-and-swap** | `--force-with-lease=<ref>:<expected-old-sha>`; a moved ref is a refusal, never a silent overwrite. |
| `fetch` into remote-tracking refs (`refs/remotes/**`) | **Independent** | But `fetch <remote> <src>:<local-branch>` writes a *local* branch — see §4.2's forbidden list. |

The lease's boundary is §3.7's: it excludes `run.py` invocations from each other
and detects anything recorded through `ship-record`. It does not reach raw `git`
run by an orchestrating conversation outside that path.

---

## 12. Git pitfalls, codified

**12.1 — A worktree's `.git` is a gitdir POINTER FILE, not a directory. Never
move a worktree by hand.** Moving the directory breaks the two-way link between
the pointer and `$GIT_COMMON_DIR/worktrees/<name>/gitdir`. The fix is
`git worktree repair` (run from the main checkout, or with the new paths as
arguments) — never hand-editing either file. Use `git worktree move` if a
worktree must relocate.

**12.2 — Config is SHARED unless `--worktree`.** `git config <key> <value>` in a
linked worktree writes the repo-wide `$GIT_COMMON_DIR/config` and affects every
worktree and the operator. Per-worktree settings require
`git config --worktree`, which requires `extensions.worktreeConfig=true`. §9
depends on this in both directions: it is why an inherited `config.worktree`
hooksPath can silently win, and it is how §9.1 sets a safe one.

**12.3 — `git worktree prune` SKIPS LOCKED WORKTREES, so every teardown is
`unlock` → `remove` → `prune`, in that order.** §1.2 creates the plan worktree
locked, which means `prune` alone leaves its administrative entry behind
forever. Skipping the `unlock` produces a slow leak under
`$GIT_COMMON_DIR/worktrees/` that nothing reports.

**12.4 — Never delete a worktree with `rm -rf`.** `git worktree remove` refuses a
worktree with uncommitted or unmerged content; `rm -rf` does not, and the content
it destroys is exactly the git-invisible agent output the parallel-group contract
bars task-level isolation over. Removal is always `git worktree remove`, and a
refusal is a park, not a reason to reach for `--force`.
The land worktree is the one exception for ignored files: nobody works in it, so a
git-ignored file there is a land gate's own output and is removed with the worktree
(`teardown_worktree(..., drop_ignored=True)`). Untracked and modified files still park it.

---

## 13. Prior art and the BUILD decision

Recorded, not assumed. Both alternatives were read before the decision.

- **Git Town** — MIT, **3.4k stars** (re-read) — already implements
  `sync` → `ship` → `undo`, with four documented ship strategies (`api`,
  `always-merge`, `fast-forward`, `squash-merge`). §4.4 cites its fast-forward
  rationale directly rather than re-deriving one.
- **Worktrunk / cmux / gwq** — already implement the worktree + branch + setup-hook
  lifecycle this contract's §1 and §9 describe.

**Decision: BUILD.** Three reasons, all standing:

1. Both are **external binaries** (Go / Rust). This repo is **stdlib-only by
   policy**, and adding a compiled dependency is a larger change than the feature.
2. `/plan-execute` must work in **any target repo** without asking the operator to
   install a Rust or Go tool first. A prerequisite install is a refusal in every
   repo that lacks it.
3. What we need is a **subset** — one branch, one land, no stacks, no forge API —
   and the parts we need are the parts §4 had to measure anyway, because probe 1
   showed the naive form of them does not work.

**What BUILD costs, stated:** we own the maintenance of a land protocol that Git
Town has already debugged across many forges, and we will re-learn some of its
edge cases. §4.4's citation is a deliberate down-payment against that: where a
mature tool has already reasoned publicly about a choice, cite it rather than
re-deriving it.

---

## 14. The repair path

**A rule in this contract that a downstream implementation session FALSIFIES is
amendable. It is not a dead end.**

Sessions s05–s08 carry "flag, never amend" abort conditions, which is right for
drift and wrong for a measurement. Without this section, one wrong rule turns
into a dead plan with no session budgeted to reopen it.

Procedure:

1. The discovering session **parks** with a decision brief naming (a) the rule by
   number, (b) the **measurement** that falsifies it — a transcript, an exit code,
   a command and its output, never an opinion — and (c) at most three options with
   a recommendation.
2. A **human decides**. This is not automatable: the rules here trade safety
   against convenience, and that trade is the operator's.
3. The amendment is recorded as a **numbered contract revision** — a new
   subsection under the affected rule, dated, citing the measurement, with the
   superseded text left visible and struck rather than deleted. §16's version
   heading is bumped.

**What is NOT a repair-path case:** an implementation finding a rule
*inconvenient*, *slower than hoped*, or *awkward to test*. Those are drift, and
the abort conditions handle them. The repair path opens only on a measurement
that shows the rule cannot be satisfied as written.

---

## 15. Lifecycle: one row per state

**Nothing below this line may be left to a downstream session to invent.** The
draft named `begin`, `run` and `land` and left four states undefined; "abandon"
appeared three times in the whole document and was never specified. Each row names
the disposition of every resource the plan owns.

| State | Local plan branch | Remote plan branch | Worktrees + lock | Lease | `$GIT_COMMON_DIR/plan-state/<slug>/` | `_plans/<slug>/` record |
|---|---|---|---|---|---|---|
| **begin** | created at pinned base (§1.3) | not yet pushed | plan worktree created `--lock` (§1.2); hook canary passes (§9.3) | none held | created | written in the primary checkout (§8.a) |
| **run** | advances | pushed on ship steps (§2.1) | plan worktree live; member worktrees are siblings (§1.1a) | held only across a `run.py` directive; token re-verified before/after each mutation (§3.6) | live | written in the primary checkout |
| **park** (session blocked or gate failed) | **kept** | **kept** | **kept, still locked** | **released** — a parked plan holds nothing (R6: a real park also ends this plan's land reservation, §17.2) | kept, `state: parked` | written; the park reason recorded |
| **resume** after a park | re-verified: recorded tip must still be the branch tip | re-fetched | **lock and pinned base BOTH re-verified** (§1.3); a moved base refuses and returns a decision brief | re-acquired fresh; a token from before the park is never reused | `state: running` | appended |
| **land-park** (§4.6 conflict, or §4.7 rejected push) | **kept** | **kept** | land worktree **kept with the conflict intact**; plan worktree kept | released | kept, `state: land-parked`, recording the merge attempt | written |
| **land-success** | deleted **after** the push/ref succeeds | deleted **after** a successful main push (§2.3) | unlock → remove → prune (§12.3), land worktree first | released | archived, then removed | committed by `record-plan` (§8.c) |
| **abandon** (operator ends the plan unlanded) | **kept unless the operator names it** — see §15.1 | **kept unless the operator names it** | unlock → remove → prune, **only after** §15.1's confirmation | released | `state: abandoned`, kept | written and left in place |
| **orphan** (state file lost; §1.5 UNKNOWN) | **untouchable** | **untouchable** | **untouchable** | n/a | absent — that is what makes it orphaned | absent or unreadable |

**15.1 — Abandon requires an operator who NAMES the branch, and never deletes by
inference.** Abandon releases the lease and removes the worktrees, but the branch
— local and remote — survives until the operator confirms deletion **naming the
branch**. An abandoned plan's branch is the only copy of work nobody merged;
deleting it on a lifecycle transition is an irreversible delete authorised by a
state machine rather than a human.

**15.2 — `plan-adopt` and `plan-release` are the ONLY sanctioned exits from
UNKNOWN.** §1.5 makes an unmappable branch or worktree untouchable, and §3.5 makes
lease expiry non-transferring. Both are right, and together they mean **nothing in
the system can ever reap a plan whose state file was lost** — the untouchable
class only grows. The escape is not a heuristic; it is a human:

- `plan-release <branch|worktree>` — the operator, naming the exact target,
  declares it not owned by any live plan. Only then may it be pruned or deleted.
- `plan-adopt <plan-dir> <branch|worktree>` — the operator reattaches an orphan to
  a plan by writing the state file the sweep needs.

A human naming the target **is** the ownership signal §1.5 requires; it is the
same standard ADR-0002 applies to a reap. No name-shaped rule, age threshold or
"looks abandoned" heuristic may substitute for it — that is precisely the
inference ADR-0002 rejected, and a false positive here deletes a branch.

**15.3 — A land that parks and is never revisited is REPORTED, never reaped.**
`plans-status` lists every `land-parked` plan with the age of the park and the
conflicted land worktree's path. It accumulates visibly rather than being cleaned
up quietly; §15.2 is the only way one leaves.

**R5 — two new rows: `land-repair` and `finish`.**

| State | Local plan branch | Remote plan branch | Worktrees + lock | Lease | `$GIT_COMMON_DIR/plan-state/<slug>/` | `_plans/<slug>/` record |
|---|---|---|---|---|---|---|
| **land-repair** (R2) | **kept**; fast-forwarded to the candidate's `HEAD`, then the repair session commits on it | kept | plan worktree kept, locked; the land worktree kept until the next `land` rebuilds the candidate | released | `land.json` counts the repair rounds | the repair session is added to the plan |
| **finish** (after `land-success`, or after the last terminal session of a non-isolated plan) | already deleted (isolated) | already deleted | a `preserved` plan worktree is retried once, never forced | one fresh `git:` lease for the record push and the fast-forward | `finish.json`: `armed_at` (from `begin`), pinned shas, `finished_at` once done | after the R3 preflight, the uncommitted remainder is pushed (R3; report-only when unarmed); the owner is fast-forwarded only under R1 |

**R6 — one new row: the land owner stop.** The three owner stops are
`invoke-skill` from the re-gate, the `gate-on-box-verification` park and
`land-awaits-review` (§17.2).

| State | Local plan branch | Remote plan branch | Worktrees + lock | Lease | `$GIT_COMMON_DIR/plan-state/<slug>/` | `_plans/<slug>/` record |
|---|---|---|---|---|---|---|
| **land owner stop** (§17.2) | kept | kept | plan worktree and land worktree kept | **released**; a separate reservation record is left beside the lease file, default 60 minutes. It is not a lease: it blocks only another plan's land lease acquisition | `land.json` keeps the candidate, `carry_basis` and any ack | written; `land_reserved` logged |

**R7 — one new row: land phase 2.**

| State | Local plan branch | Remote plan branch | Worktrees + lock | Lease | `$GIT_COMMON_DIR/plan-state/<slug>/` | `_plans/<slug>/` record |
|---|---|---|---|---|---|---|
| **land phase 2** (§18.1) | kept | kept | plan worktree and land worktree kept | held, with `phase: "push"` and `phase_expires_at`; the host-wide marker is set (§18.6); both are removed before the release, on every exit, a park included | `land.json` `phase: "2"` and `phase2`; on a reset, `phase` returns to `"1"` | written; `land_phase2` logged |

## 16. Supersession

~~This is contract **v1**, frozen 2026-08-21.~~ ~~This is contract **v2**, amended
2026-10-01 by the operator's decisions recorded in `finish-contract.md`.~~ ~~This is
contract **v3**, amended by R6 (§17).~~ This is contract **v4**, amended
2026-11-15 by R7 (§18). v3 was amended by R6 (§17). v2 was amended by the
operator's decisions recorded in `finish-contract.md`. v1 was
frozen 2026-08-21. "Frozen" means an implementer may not
change it; it does not mean a later decision cannot. Amendments arrive by §14 or
by a superseding plan session that bumps the version in this heading.

It has one companion: `parallel-group-contract.md` **v3**, amended in the same
session, which nests group branches under the plan branch and redefines "the
shared tree" as the plan worktree. The two are consistent by construction; where
they overlap, the parallel-group contract governs group *membership* rules and
this file governs the *plan* branch, worktree and land.

**v2 revisions:** R1 (§4.3, `finish` may fast-forward the owner),
R2 (§4.4, `at_land` gates and `land-repair`), R3 (§8.c2, the `finish` record
push), R4 (§8.e, `LAND_NOTICE.txt` and `_worktrees/` are local runtime), R5
(§15, the `land-repair` and `finish` rows). The `finish` step itself is
specified in `finish-contract.md`, which governs where the two overlap.

**v3 revision:** R6 adds §17 and amends §3.4, §3.5, §4.5a, §5.1d,
§5.2 and §15. It comes from the owner's picks of 2026-11-14 in
`_plans/land-keeps-approval-2026-11-14/`, after the land delays recorded in
`_plans/owner-attention-fixes-2026-11-12/_decisions/land-followups.md`.

**v4 revision:** R7 adds §18 (the two-phase land) and amends §4.5,
§5.2, §15, §17.1, §17.3 and §17.5.3, each marked in place. It comes from plan
`_plans/two-phase-land-2026-11-15/`, after main moved three times during one
land of `_plans/land-keeps-approval-2026-11-14/`.

---

## 17. Keeping a land's results, its turn and its lock (R6)

**Who reads this:** whoever builds or reviews sessions s02–s05 of
`_plans/land-keeps-approval-2026-11-14/`, and anyone who later changes `land`.
This section is authority for those sessions; their function and field names
come from here. Every claim about today's code cites `file:line` on the plan's
base commit, under `skills/plan-execute/scripts/` unless another path is given.

**Why it exists.** On 2026-11-13 and 2026-11-14 one plan's land was blocked for
most of a day, with its code unchanged
(`_plans/owner-attention-fixes-2026-11-12/_decisions/land-followups.md`):

- main moved five times in one day, mostly through harvest commits of runtime
  files. Every move reset every gate result and the owner's on-box answer;
- another plan took the repo lease while the owner was reading the review
  brief. The owner's ack at about 07:50 died when main moved before the push;
- that other plan's lease expired at 00:01 while its land was still running.

Four rules follow. §17.1 keeps results across a harmless main move. §17.2 keeps a
waiting land's turn. §17.3 pushes in the same step as the approval. §17.4 keeps a
running land's lock. §17.5 holds the three mechanisms they share, and §17.6 the
migration rule.

**Words used below.**

- **Gate set key**: `land_gate._gate_set_digest` (land_gate.py:288-308). It hashes
  what is being run: gate ids, kind, argv, cwd, skill, `indeterminate_exit`,
  timeout, `env_allowlist`, `expect` and `rerun_tests`.
- **Result digest**: `land_state.gate_digest` (land_state.py:194-212), the §5.1c
  `gate_digest`. It hashes what came out: gate id, outcome and findings count.
  The two are never interchangeable, and the text below never says "gate digest"
  without one of these names.
- **Owner answer**: the land ack (`land.land_ack`, land.py:201-229) and an on-box
  disposition (`land.land_verify`, land.py:232-297, stored in `on_box_gates`).
- **Review pass**: a `pass` recorded for a gate `g` where
  `verify_pass.is_review_gate(g)` is true (verify_pass.py:83-86): every skill-kind
  gate, and every argv gate whose argv runs `llm_review_gate.py`.
  `indeterminate_exit` never makes a gate a review gate, because any argv gate
  may declare it (shipping.py:165-168).
- **Test gate**: every argv gate that is not a review gate.
- **M**: the main sha captured once per land attempt (§17.5.3).

### 17.1 — A runtime-only main move keeps the owner's answers and the review passes

**Today.** `step_gate` keys every result on
`expected:plan_head:merge_sha:<gate set key>` (land_gate.py:413-414). Any change
of that key empties `skill_gates`, `argv_gates` and `on_box_gates` together
(land_gate.py:415-424). `step_merge` clears the ack when `plan_head` or
`main_head` moved (`invalidate_stale_ack`, land_state.py:256-273, called at
land_steps.py:173). `step_sync` merges main into the plan branch and records a new
`plan_head` (land_steps.py:64-71), so `plan_head` moves on every main move.
`land_push._resync` already re-binds the ack to a moved main
(land_push.py:99-144, re-bind at :135-137). But it calls `step_gate` (land_push.py:121),
which wipes `on_box_gates` on the new `merge_sha`.

**The owner's pick, word for word:** "Keep them only when every new
main commit changes live-state or machine-churn paths (the harvest classifier's
own classes), none of them a file the plan changes, and the plan's diff patch-id
is unchanged".

**Added by this contract:** the gate set key and every gate registry file are
unchanged as well. Every input that cannot be classified, and every check that
fails or errors, means reset.

**R7:** with the §18.11 switch `on`, a range that holds harness
code no longer resets by that fact alone: it takes the code route of §18.2,
which carries a path-disjoint move the same way this section carries a harvest.
Invariants 1, 4, 12 and 15 below are amended by §18.2, §18.3 and §18.14.

**What carries, and what re-runs.** On a carry, owner answers and review passes
are kept and re-stamped with the new key. Every test gate re-runs on the new
merged tree. Reason: a harvest of `_plans/**` is live-state
(scripts/deploy.pathspec:28), and tests that glob `_plans/*/spec.json` can turn
red on it (GRILL-LOG.md Q1).

**Function.** `land_carry.decide(...)` in a new module
`skills/plan-execute/scripts/land_carry.py`. It returns
`{"decision": "carry" | "reset", "reason": <code or null>, "classes": {...},
"carry_version": 1}`. It is the ONE decision. It needs the new merged candidate,
so it runs only after the merge. `land_gate.step_gate` calls it before the reset
at land_gate.py:415. Nothing else calls it. `land_push._resync` runs `step_gate`
(land_push.py:121), so it gets the decision there and never calls `decide`
itself. Under R6 `_resync` drops its own plan-side test and re-bind
(land_push.py:129-137). It keeps only the result-digest check: it pushes
unattended only when `step_gate` re-bound the ack to the new `merge_sha` and
`gate_digest` equals the digest before the re-sync. Otherwise it returns
`land-awaits-review`.

**The ack, in order.** (1) `invalidate_stale_ack` runs before the merge
(land_steps.py:173). Today it clears an ack whose `plan_head` or `main_head`
differs (land_state.py:256-273). Under R6 it clears nothing: it leaves the ack
as it is. (2) `step_merge` builds the new candidate. (3) `step_gate` calls
`land_carry.decide` on it, once. On `carry` it re-binds the ack (invariant 13).
On `reset` it moves the ack to `invalidated_from`, exactly as
`invalidate_stale_ack` does today. (4) `step_ack` compares the ack with the
candidate (land_steps.py:379), unchanged. An ack that step 3 did not re-bind
never matches, so a land exit between steps 1 and 3 (a conflict park, say)
cannot push on it.

**Classifier.** One new pure function in `scripts/gearbox-classify.py`, named
`classify_paths`. It takes paths plus the old and new `settings.json` text and
returns classes. `main()` calls it in place of its inline loop
(scripts/gearbox-classify.py:208-222), so harvest and carry share one
implementation. The classes and `scripts/deploy.pathspec` do not change.
`land_carry` loads the module with `importlib.util.spec_from_file_location`,
because its file name has a hyphen. The path is
`Path(__file__).resolve().parents[3] / "scripts" / "gearbox-classify.py"`: the
repo root in the edit clone, and `~/.claude` in the deploy target. A missing or
unloadable module is `git-error`, so reset.

**State fields.** `land.json` (§8.d) gains `carry_basis`, written by `step_gate`
whenever it stores results under a key:

| Field | Meaning |
|---|---|
| `carry_version` | `1` |
| `main_head` | the M this candidate was merged onto |
| `plan_head` | the plan branch tip after `step_sync` |
| `merge_sha` | the merged candidate |
| `plan_commits` | sorted shas of `git rev-list --no-merges <plan_head> ^<main_head>` |
| `plan_paths` | `git diff --name-only <main_head> <plan_head>`, nothing excluded |
| `patch_id` | `git diff <main_head> <plan_head> -- . ':(exclude)_plans' \| git patch-id --stable` |
| `gate_set_key` | `_gate_set_digest(gates)` |
| `registry_blobs` | blob ids of `.claude/eval-gates.json` in the candidate and of `skills/plan-execute/references/eval-gates.default.json` (land_at_land.py:23-24, shipping.py:72-99) |
| `outer_registry_digest` | SHA-256 of `shipping.eval_gates(plan_dir)` (shipping.py:98-99) as canonical JSON |

The ack (§5.1) gains `carries`: one entry per re-bind, shaped like `resyncs`
(land_push.py:136-137).

**Event.** `land_carry` in `run.ndjson`, on EVERY candidate change, with
`decision`, `reason`, `from_key`, `to_key`, `classes` (path count per class),
`carried` (gate ids), `rerun` (gate ids) and `carry_version`.

**Invariants.**

1. **Carry needs every check below. The first check that fails names the
   reason.** The checks run in this order: `legacy-state`, `not-ancestor`,
   `harness-class`, `plan-path-touched`, `patch-id`, `plan-side-changed`,
   `blob`, `gate-set-key`, `registry`. Any git error, parse error or missing field
   at any point is `git-error`. Reset is the default; carry is the exception.
2. **`legacy-state`:** a land state with no `carry_basis`, or another
   `carry_version`, resets.
3. **`not-ancestor`:** the basis `main_head` must be an ancestor of M
   (`git merge-base --is-ancestor`). A rewritten main resets.
4. **`harness-class`: classify per commit, never the net diff.** List the paths
   of every commit in `<basis main_head>..M` with `git log -m --name-only`.
   Every path must be live-state or machine-churn under `classify_paths`. A
   source change and its revert inside the range still reset. An unlisted path
   is harness code.
5. **`settings.json` is churn only when every changed key in every commit is a
   churn key** (scripts/deploy.pathspec:48 onward, the rule at
   scripts/gearbox-classify.py:130-149). A text that does not parse is harness.
6. **`plan-path-touched`:** no commit in the range may touch a path in
   `plan_paths`. That includes `_plans/<slug>/`: only this plan writes there.
7. **`patch-id`:** the plan diff's patch-id must equal the basis `patch_id`. This
   is the owner's test, and it runs fifth, in the order of invariant 1. It is
   not enough alone: `--stable`
   ignores whitespace, so a re-indent keeps the id (GRILL-LOG.md Q2).
8. **`plan-side-changed`:** the plan side is unchanged when all three hold. (a)
   `git rev-list --no-merges <plan_head> ^M` gives the same shas as
   `plan_commits`. (b) The sync merge needed no conflict resolution. (c) The
   candidate was built by `step_merge`, not hand-resolved
   (`resolution_required` false, land_steps.py:188-195). `plan_head` itself is
   NOT compared: it always moves, because `step_sync` merges main into the plan
   branch (land_steps.py:64-71).
9. **`blob`:** every path in `plan_paths` has the same blob in the basis
   `merge_sha` and in the new merged candidate.
10. **`gate-set-key` and `registry`:** the new gate set key, `registry_blobs` and
    `outer_registry_digest` all equal the basis.
11. **Settled records carry; nothing else does.** A review pass (`pass`) and an
    accepted on-box answer (`verified-on-box`, the second settled outcome at
    land_gate.py:77-80, stamped as its own outcome at land.py:246-250) carry. Each
    keeps its outcome and its result-digest value unchanged. A `fail`, `skipped`,
    indeterminate, `blocked` or unresolved record never carries. On a carry it is
    dropped, so the gate re-runs or the owner is asked again.
12. **Test gates never carry.** Every test gate re-runs on the new merged tree,
    on every carry.
13. **The result digest is never re-bound.** A carry re-binds only `plan_head`,
    `main_head` and `merge_sha` in the ack, and appends to `ack.carries`. When a
    re-run test gate changes the result digest, the ack is invalid under §5.2.
    `step_ack` already handles this: it moves the ack to `invalidated_from` and
    returns `land-awaits-review` (land_steps.py:383-398). Only the ack is dropped.
    The carried review passes and on-box answers are kept, because they are
    stamped with the current key, and the new test results are kept too. The
    owner is asked for a new ack against the new result digest.
    `step_ack` calls `land_carry.log_reset(plan_dir, reason="gate-result", ...)`
    in the same call, right after it moves the ack to `invalidated_from` and
    before it returns `land-awaits-review`. That writes the `land_carry` event
    with `decision: reset`. It is logged only when an ack carried by
    `ack.carries` fails on its result digest.
14. **Re-stamp before any exit.** A carry writes the new `gate_key` into every
    kept record in `skill_gates`, `argv_gates` and `on_box_gates`, and saves, before
    `step_gate` can park or return a directive.
15. **One rule, not two.** §4.5a and §5.2 below are amended to use this
    decision. No other code path may decide that a moved candidate keeps an answer.

**R7 amends invariants 1, 4 and 12.** Invariant 1: the order is
§18.2's; it names `main-unmoved` (already checked at land_carry.py:190) and adds
`classify-error` and, on the code route, `force-reset-path`. Invariant 4: a
harness path takes the code route when the switch is `on`, instead of always
resetting. Invariant 12: in phase 2 a gate whose registry entry says
`phase2: "carry"` is copied, not re-run (§18.3); in phase 1 it stands. Invariant
15 stands: `decide` is still the only decision (§18.2 invariant 1).

### 17.2 — An owner stop leaves a reservation, never a lease

**Today.** `land` releases the repo `git:` lease on every exit, including the
review stop (land.py:195-198, "a parked plan holds nothing", §15). A second plan
can take it while the owner reads. On 2026-11-14 the other plan held it from
07:46 to 08:16 around the owner's ack (land-followups.md, land run 25).

**The owner's pick, word for word:** "The plan releases the lock but
leaves a reservation for a limited time (for example 60 minutes); another plan's
land waits or parks until it expires or the owner answers".

**Owner stops.** Exactly three land exits are owner stops:

- `invoke-skill` from the re-gate (land_gate.py:446-456);
- the `gate-on-box-verification` park (`ON_BOX_PARK`, land_gate.py:81);
- `land-awaits-review` (land_steps.py:384-398).

Every other land exit is a real park, or success.

**Record.** A reservation is its own file beside the lease file:
`<git-common-dir>/plan-locks/<resource-slug>.reservation.json`. It is never a
lease kept alive. Fields: `reservation_version` (`1`), `plan_dir`, `plan_slug`,
`token` (its own uuid, not the lease token), `reason` (one of the three stop
names), `host`, `created_at`, `expires_at` and `reservation_seconds` (default
`3600`). Nothing ever extends `expires_at`.

**Functions.** A new module `skills/plan-execute/scripts/land_reservation.py`:
`reserve(plan_dir, resource, reason)`, `end(plan_dir, resource, why)` and
`blocking(plan_dir, resource)`. The last one returns the record that blocks this
plan, or `None`.

**Events.** `land_reserved` and `land_reservation_ended` (with `why`:
`answered`, `own-land`, `expired`, `real-park`, `landed` or `retired`) go to the
reserving plan's `run.ndjson`.
`land_reservation_blocked` goes to the blocked plan's `run.ndjson`.

**Park.** A blocked land returns `{"action": "land-parked", "kind":
"land-reserved", "reserved_by": <slug>, "reason": <stop>, "expires_at": ...,
"retry_after_seconds": ...}`. It takes nothing and writes nothing else.

**Invariants.**

1. **Written before the release.** At an owner stop, `land` takes the host
   mutex (§17.5.1), writes the reservation, then deletes its lease, then drops
   the mutex. No other plan can acquire in between.
2. **Checked inside the same mutex as the acquisition.** Another plan's land
   reads the reservation and creates its lease in one mutex hold.
3. **It blocks only land's lease acquisition:** `land.land` (land.py:151, :173).
   Worktree setup (plan_worktree.py:353), `ship-begin`, `push:` and `deploy:`
   leases, and `finish`'s `git:` lease (finish.py:135), ignore it. (The test
   fixture `_land_lease_holder.py` is not a production lease taker.)
4. **The reserving plan passes its own reservation**, and ends it
   (`why: own-land`) when its next land run takes the lease.
5. **The owner's answer ends it, with no gap.** `land-ack`, `land-record` and
   `land-verify` are the answers. Each records the answer and then runs the next
   land step in the same process, under the same per-plan run lock (§17.5.2),
   exactly as `land-ack` pushes in the same step (§17.3). The order is: (a) take
   the run lock at CLI entry; (b) record the answer; (c) in ONE host-mutex hold,
   try to take the land lease and end the reservation (`why: answered`). Step
   (c) ends the reservation whatever the lease attempt returns. If the lease is
   free, the land step now holds it. If another plan holds it, the command
   returns `land-locked`, and the reservation is gone all the same: the owner
   has answered, and the owner's pick lets the other plan in at that point.
   There is no second process to wait for, so no other plan's land can take the
   lease between the answer and step (c). On 2026-11-14 the other plan took the
   lease in exactly that gap (GRILL-LOG.md Q12). If the land step then reaches
   another owner stop, it writes a new reservation (invariant 1). If the
   command stops before (b) (for example `land-running` at the run lock), there
   was no answer, and the reservation stays until it expires.
6. **It ends on its own** at `expires_at`. The next reader that finds it expired
   removes it and logs `why: expired`.
7. **A real park ends it.** Any land exit that is not an owner stop ends this
   plan's reservation, and so do `landed` and `retire-plan`. §15 "a parked plan
   holds nothing" stays true for every real park. Three exits are NOT real parks
   and never end this plan's own reservation by themselves: `land-reserved`,
   `land-locked` and `land-running`. They mean the run never started its work,
   so it has nothing to hand back. The one case where a reservation ends next
   to one of them is invariant 5: an answer was recorded, so step (c) ended it
   before `land-locked` was returned.
8. **One reservation per resource.** A live reservation of another plan is never
   overwritten. A plan that reaches an owner stop while another plan's
   reservation is live writes none, and logs that.
9. **A crash in the handoff is safe either way.** A reservation without a lease
   expires on its own. A lease without a reservation follows today's rules.
10. **An unreadable reservation is ignored and logged.** It guards a turn, not
    data, and the lease still provides the exclusion.
11. **Wait and retry, not STOP.** `land-reserved` and `land-locked` are not owner
    stops for the orchestrator. Both carry `expires_at` and
    `retry_after_seconds`. The orchestrator waits with `Monitor` and re-runs
    `land`, at most 3 times, then surfaces the brief and STOPs (SKILL.md, s04).

### 17.3 — The push runs in the same step as the land ack

**Today.** `land_ack` records the ack and returns `next: run.py land`
(land.py:219-229). The push happens in a later process. The lease is free
between the two (land.py:195-198), so main can move and another plan can take
the lease. Both happened to the 07:50 ack.

**Rule.** After `land_ack` records the ack, the same process runs the land
protocol. The `land-ack` CLI handler took the per-plan run lock at its entry
(§17.5.2), and the land protocol runs under that lock without taking it again.
It takes the land lease (its own reservation lets it through, §17.2 invariant
5). Then it walks the same `STEPS` (land_push.py:363-364). There is no second
push path. `land-record` and `land-verify` continue the same way after they
record their answer (§17.2 invariant 5); for them the next step is usually the
next gate, not the push.

**Outcomes.**

- Nothing moved: it pushes.
- A runtime-only main move (§17.1): it merges M, re-records, re-runs the test
  gates, re-binds the ack, and pushes.
- Any other move: it returns `land-awaits-review`. The ack is kept as
  `invalidated_from` for §5.3, and a `moved` field names the first failing
  §17.1 reason. **R7:** with the §18.11 switch `on`, a
  path-disjoint code move (§18.2) is not "any other move": it merges M, re-runs
  the fast gates, copies the carried ones, re-binds the ack and pushes, inside
  phase 2 (§18.1).
- Another plan holds the lease: it returns `land-locked`. The ack was recorded
  first, so the state stays `acked` and the ack stays valid. The reservation has
  ended (§17.2 invariant 5). The next `land` run starts from that ack, under the
  same rules as any other land run.
- Another process of this plan is running land: the run lock refuses `land-ack`
  at its CLI entry, before it records anything. It returns `land-running`. No ack
  is written and no event is logged. The owner re-runs `land-ack` once that run
  ends.

**Event.** `plan_land_acked` stays (land.py:226-228). `land_ack_push` follows it,
with `outcome` (`pushed`, `carried`, `awaits-review` or `locked`) and `moved`.
Both are written only after the ack was recorded.

**Invariants.**

1. **No push without an ack that binds the pushed candidate.** `step_ack` stays
   the only route to `step_push` (land_steps.py:351-398).
2. **The only re-bind is a §17.1 carry**, and it is recorded in `ack.carries`.
3. **A recorded ack is never lost to a refusal.** A `land-ack` refused at the
   run lock records nothing, so there is nothing to lose. A `land-ack` that
   recorded its ack and then meets `land-locked` keeps the ack, state `acked`.
4. **A push rejected because main moved again goes through `_resync`** (bound of
   3, §4.5). `_resync` calls `step_gate`, which makes the one `land_carry.decide`
   call for the new candidate; `_resync` does not call it again (§17.1).

### 17.4 — A running land renews its lease, and a live holder is never taken over

**Today.** Nothing renews a lease while its holder works (§3.4; ship_locks.py:66-72).
`_renew_own_lease` runs only when the same plan re-acquires, and only on a live
lease (ship_locks.py:371-397, refusal at :384-385). `_steal_expired` takes over an
expired same-host lease by the wall clock alone (ship_locks.py:222-247). A land
lease lasts 2 × 1800 + 900 = 4500 s (`LAND_TIMEOUT`, land_state.py:31;
`_lease_seconds`, ship_locks.py:150-155). Its gates run one after another with no
total bound (land_gate.py:439 onward), and a failed gate may be retried
(`land_load.retry`, land_load.py:96). On 2026-11-14 a land outlived its lease
(land-followups.md, land run 21).

**Scope.** Renewal and live-holder protection belong to the lease, not to the
taker. They cover only `git:` leases that `land` takes in its own process on
this host (land.py:151, :173), marked `holder_kind: "land"`. `push:`, `deploy:`
and `ship-begin` leases, and the `git:<root>` leases that `finish`
(finish.py:135) and worktree setup (plan_worktree.py:353) take, are not
protected: a contender takes them over by today's wall-clock rule (§3.4, §3.5).
The one exception is a legacy lease with a live pid during migration (§17.6).
The reason: `ship-begin` exits before the guarded skill runs, so its pid is dead
by design (ship_locks.py:54-58).

**Every taker obeys both shared rules.** `finish` and worktree setup take the
same `git:<root>` resource through `ship_locks.acquire_ship_lock`, whose
takeover path is `_steal_expired` (ship_locks.py:339-360, :222-247). So when
they find a land lease, they honour the host mutex (§17.5.1) and the
no-takeover-of-a-live-holder rule (invariant 4) like any other caller. The
check lives in `_steal_expired`, through `lease_guard.may_take_over`, not in
the callers. They ignore the reservation (§17.2 invariant 3).

**Same plan, other process: no back door.** `acquire_ship_lock` tries
`_renew_own_lease` before `_steal_expired` (ship_locks.py:353-356), and
`release_ship_lock` frees a lease whose token matches `run_state.json`
(ship_locks.py:453-456). `finish` and worktree setup of the same plan read that
same token. So for a lease with `holder_kind: "land"`, renewal and release also
require the caller to BE the holder process: `pid == os.getpid()` and the same
`pid_start`. A caller of the same plan in another process is a contender like
any other. While the holder lives, it is refused (invariant 4); renewal
returns `None` and release logs `shipping_lock_not_ours` and keeps the file.
Once the holder is dead, it takes over through `_steal_expired`, recorded on
both sides.

**Another host keeps today's rule.** Today that rule is: a lease taken on another
machine is never reclaimed from here, however old (`foreign-host`,
ship_locks.py:172-175 and :228-229). That stays as it is.

**State fields in the lease file.** `lease_version` (`2`), `holder_kind`
(`"land"`), `pid`, `pid_start` (the output of `ps -o lstart= -p <pid>`),
`renewed_at` and `renewals`.

**Functions.** A new module `skills/plan-execute/scripts/lease_guard.py`:

- `mutex(lock_path)`: the host mutex (§17.5.1);
- `renew(plan_dir, resource)`;
- `holder_alive(info)`: pid alive (`rsi._pid_alive`, run_state_io.py:352-363)
  and the same `pid_start`;
- `may_take_over(info)`, which returns a yes or no and a cause;
- `holds_lease(plan_dir, resource)`: reads the actual lock file inside the mutex;
- `run_lock(plan_dir)`: the per-plan run lock (§17.5.2).

`ship_locks.py` only calls these. It is at 498 lines against the 500-line ratchet.

**Events.** `shipping_lease_renewed` (exists, ship_locks.py:395-396).
`shipping_lease_renew_refused` when the caller is not the holder: another token,
or the same token from another process.
`shipping_lease_takeover_refused`, with `holder_pid`, `holder_host` and
`why: live-holder` (or `legacy-live-pid`, §17.6). `shipping_lease_taken_over` (exists) gains `cause`: `expired`
(a lease with no live-holder protection), `pid-dead` or `pid-reused`. A refusal
from a live holder is never logged as a takeover.

**Invariants.**

1. **Renewal points.** The holder renews between land steps (land.py,
   land_steps.py), before each gate and each retry inside `step_gate`, and before
   each push attempt. No heartbeat thread is needed.
2. **Only the holder process renews, and renewal keeps its identity.** It
   moves only `expires_at`, `lease_seconds`, `renewed_at` and `renewals`. The
   token, `pid` and `pid_start` never change, so they change together or not at
   all. Today's `cur["pid"] = os.getpid()` (ship_locks.py:392) is then a no-op
   for land leases. A renewal from another token or another process is refused
   and logged as `shipping_lease_renew_refused`.
3. **The holder process renews even after expiry**, because nobody took the
   lease. This replaces the live-only check at ship_locks.py:384-385, for land
   holders only.
4. **No takeover from a live holder.** A same-host land lease is never taken
   while its holder is alive with the same `pid_start`, however long after
   `expires_at`. There is no time cap. A dead holder is taken over: pid not alive
   (`cause: pid-dead`), or invariant 5. That takeover is recorded on both sides
   as §3.5 requires. A lease that has not expired is retaken at once only by the
   plan that wrote it (`lease_guard.dead_own_land`). Another plan's dead holder
   is taken over after the lease expires, as before; owner decision,
   open follow-up: let any plan retake a lease whose same-host holder is proven dead.
5. **pid reuse is death.** A live pid with a different `pid_start` is a dead
   holder (`cause: pid-reused`).
6. **A hung live holder waits for the owner.** The contender gets today's
   refusal message, which tells the owner to remove the lock file once that run
   is over (ship_locks.py:192-197). It logs `shipping_lease_takeover_refused`.
   Removing the file is the owner's act, not a takeover, and nothing logs it as
   one.
7. **The lease stays at least as long as the longest single gate timeout**, so
   one gate cannot outlive it between two renewals.
8. **The push guards read the lock file, not `run_state.json`.**
   `ship_locks.our_lease` reads only `run_state.json` (ship_locks.py:275-277).
   `land_push.py:168`, `:176` and `:249` use `lease_guard.holds_lease` instead.
9. **A holder that finds another token parks `lease-lost` and writes nothing**
   (§3.5, §3.6).

### 17.5 — Mechanisms the four rules share

1. **Host mutex.** Every read-check-write of a lease file or a reservation runs
   inside `fcntl.flock(LOCK_EX)` on the sibling file `<lock file>.mutex`. That
   covers acquire, renew, take over, release, reserve, end and check. The
   mutex is held only for that critical section, never across a git command or a
   gate. A re-read after a write is not a guard: it cannot see a record it
   overwrote (spec-REVIEW-LOG.md, C3). Probe 2026-11-14: a second process got
   `BlockingIOError` while the first held the lock, and a third got it after
   the first exited. Known limit, owner decision: `acquire_ship_lock`
   holds the mutex for each pass of its take-over loop, not across the loop. The
   deleting of an expired lease and the creating of the new one are two
   passes. Another plan may publish in the gap. It then wins, and the taker retries
   (4 passes at most). Exclusivity holds, because the create is one atomic link.
   Open follow-up: one mutex hold across delete and create.
2. **Per-plan run lock, taken once per process.** `lease_guard.run_lock(plan_dir)`
   is a context manager. It holds `fcntl.flock(LOCK_EX | LOCK_NB)` on
   `<plan_dir>/.land-run.lock`. It is taken ONCE, at the CLI entry of `land`,
   `land-resume`, `land-ack`, `land-record` and `land-verify`: in their handlers
   in the `land.CLI` table (land.py:413-422), which `run.py` dispatches through
   (run.py:4197-4201). The functions those handlers call, and the land steps
   that `land-ack`, `land-record` and `land-verify` run after their answer, run
   under that lock and never take it again. The reason is measured: a second
   `open()` plus `flock(LOCK_EX | LOCK_NB)` on the same file in the same process
   is refused with errno 35 on macOS (probe 2026-11-14), so an inner re-take
   would refuse its own caller. `land-status` only reads, and takes no lock. The
   OS releases the lock when the process exits. A second process of the same
   plan returns `land-running`. It is NOT `rsi.acquire_lock`, which checks, then
   overwrites, so two callers both get a token (run_state_io.py:397-417). The file
   must be git-ignored. Today `.gitignore` ignores only `_plans/*/.lock`
   (.gitignore:203).
3. **One captured main sha per land attempt.** An attempt captures
   `M = rev(<upstream>)` once. It classifies, merges, gates and pushes against M
   only, and merges M by sha, never by ref name. Before EVERY push it proves
   `git merge-base --is-ancestor <expected> HEAD`. That covers the land push
   (`_push_once`, land_push.py:173) and the final-record push (land_push.py:281).
   If the proof fails, the final record merges M first; the land push parks.
   Today `_resync` merges the ref and then reads it again (land_push.py:114-118).
   `publish_final_record` sets `expected` without any merge (land_push.py:251), so
   `--force-with-lease` (land_push.py:49) can push over a main commit that the
   pushed head does not contain.
   **R7:** in phase 2 every attempt also runs inside one monotonic
   deadline, and `step_push` is the only retry loop (§18.4).

### 17.6 — Migration

- New code writes `lease_version: 2` on every lease it creates. A same-host
  lease with no `lease_version`, or a land lease with no readable `pid_start`,
  is a legacy lease. Today's code writes `pid` but no start time
  (ship_locks.py:208). Missing identity fields never authorise a takeover:
  - its `pid` is alive: it is refused, exactly like a live holder (§17.4
    invariants 4 and 6), and logs `shipping_lease_takeover_refused` with
    `why: legacy-live-pid`;
  - its `pid` is gone: today's wall-clock rule applies (`cause: expired`).
  A reused pid can keep a legacy lease refused; the owner removes the file, as
  today's message says (ship_locks.py:192-197). Legacy leases end with the last
  land run of the old code.
- A land state with no `carry_basis` resets (`legacy-state`), with no exception.
- No reservation file means no reservation.
- Rollout gap (s07 finding D). An OLD deployed `ship_locks` in another session
  does not read `holder_kind` or `pid_start`. It can still take a new-format land
  lease on expiry alone, even while its holder lives. The new code cannot stop
  that. Close it in the deploy: deploy this code, then end or restart every
  running session that still has the old `ship_locks` loaded, before the next
  long land runs.
- A lease file that cannot be READ (an `OSError` other than a missing file) is
  not an empty lease. `_read_lock` raises `LockError`, so nobody takes it, and
  the holder's own renewal fails closed and parks (s07 finding A). Only bytes
  that read but are not a lease count as `unreadable`.

---

## 18. The two-phase land (R7)

**Who reads this:** whoever builds or reviews sessions s02–s07 of
`_plans/two-phase-land-2026-11-15/`, and anyone who later changes `land`. This
section is authority for those sessions; their field, function and event names
come from here. Every claim about today's code cites `file:line` on the plan's
base commit `56af6d78`, under `skills/plan-execute/scripts/` unless another path
is given.

**Words used below.** Every term of §17 keeps its meaning (gate set key, result
digest, owner answer, review pass, test gate, M). New terms:

- **Phase 1**: the land as it runs today, from `step_sync` to
  `land-awaits-review` (land_push.py:393-394). Local writers are not held.
- **Phase 2**: the short push window after the owner's ack, defined in §18.1.
- **Moved paths**: the union of every path in every commit of
  `<basis main_head>..M`, defined in §18.2.
- **Fast gate** and **carry gate**: the two gate classes of §18.3.
- **Marker**: the record that tells local writers a land is pushing (§18.6).
- **Harvest route** and **code route**: the two ways `decide` can return carry
  (§18.2). The harvest route is §17.1 unchanged.

**Why it exists.** On 2026-11-15 plan `land-keeps-approval-2026-11-14` was acked
at 14:29 on main `7a927b76`. Main then moved to `1d290f0d`, `ac128c6f` and
`96043c59`, and on to `a5f5d1e9`, before the plan landed at 17:24
(`_plans/land-keeps-approval-2026-11-14/run.ndjson`, events `plan_land_acked`,
`plan_land_worktree_created`, `plan_landed`). The moves were 17 commits
(`git rev-list --count 7a927b76..a5f5d1e9`) in bursts: pull request merges #82
to #85, plan additions and harvests. Measured for this section with
`git log -m --name-status -M` per move: none of the 85 moved path entries (23,
36, 9 and 17 per move) is one of the plan's 42 changed paths. Each restart cost
about 12 minutes: ci-check 672-732 s, the cross-family review 317 s, the full
suite 28-103 s (figures from this plan's context bundle, not re-measured here). Today every one of those moves resets, because each range holds
harness code (`harness-class`, land_carry.py:195-196).

The same measurement against this section's force-reset list (§18.8): the move
`7a927b76..1d290f0d` touches `Makefile` and `.claude/eval-gates.json` (commit
`002bf018`, "start pytest through scripts/pytest-m"), so it would still reset.
The other three moves would carry. The two `settings.json` changes in the range
(`fe02f83f`, `dc8822ff`) are harvests the classifier labelled machine-churn
(`dc8822ff`: churn key `model`), so they are not force-reset hits (§18.8).

Best practice agrees on three points (Zuul, the GitHub merge queue, Uber's
SubmitQueue, Chromium's commit queue): the tree that is pushed is tested
exactly, one writer moves main at a time, and changes that do not conflict
verify independently. This section keeps the first two and accepts one named
risk on the third (§18.3, "Accepted risk").

**The owner's ack, as a rule.** A path-disjoint code move carries
the owner's answers and review passes the same way a harvest-only move carries
them today (§17.1). Reason: in both cases nothing the owner looked at changed.
The plan's own diff, its commits and every blob of every plan path are proven
equal (§17.1 invariants 7-9), and the new main commits touch none of them.

### 18.1 — Two phases: a long one with no hold, a short one around the push

**Today.** The land takes the repo `git:` lease once per run (`_take_lease`,
land.py:128-138) with `LAND_TIMEOUT` 1800 s, so the lease lasts
2 × 1800 + 900 = 4500 s (land_state.py:31; `_lease_seconds`,
ship_locks.py:147-152). It walks `STEPS` (land_push.py:393-394) and releases the
lease on every exit (land.py:222-227). Nothing tells another writer that a push
is near. `land-ack` records the ack and runs the land in the same process
(`land_answer.ack_push`, land_answer.py:41-61).

**Rule.**

- **Phase 1** is today's land. It runs every gate of the union, under the lease,
  with no marker and no deadline, and ends at `land-awaits-review`.
- **Phase 2** starts when a land run begins with an ack in `land.json`
  (`st["ack"]` set): the `land-ack` continuation, or a later `land` run from
  state `acked`. It runs `step_sync` through `step_push`, under the same lease,
  with the marker set (§18.6) and one monotonic deadline (§18.4). It ends at the
  first of three things: a verified push (`_verify_landed`, land_push.py:60-70),
  a reset, or a park.
- **A reset ends phase 2 and continues as phase 1 in the same process.** The
  marker is removed first. The ack moves to `invalidated_from`, every gate
  re-runs, and the land returns `land-awaits-review` (§17.1 "The ack, in order",
  step 3). A park later in that run is a phase-1 park.
- **A park ends phase 2 the way it ends a land today.** A park is any stop that
  today's code already returns between `enter` and `leave`: a `park(...)` call
  (land_state.py:295-304) or a stop action (`land-awaits-review`, `invoke-skill`,
  `land-repair`). The full list, with every site, is
  `_evidence/s01/phase2-park-sites.txt` of this plan. In short: the lease-lost
  parks, the sync, worktree, merge and record parks, the gate parks, the push
  parks (`expected-not-in-head`, `push-unverified`, `push-rejected`), and the
  same steps reached again through `_resync`. Each keeps today's park kind and
  today's owner brief. The only new work on a park is `leave`. A park itself
  never carries a gate and never re-runs one. The one park §18 changes is named
  in §18.4: in `on`, a stale rejection on the last attempt is a reset.
- **The final-record push is outside phase 2.** `publish_final_record`
  (land_push.py:264-335) already merges a moved M by sha before it pushes
  (land_push.py:290-296), and it carries no code (§8.c2).
- **When phase 2 is not entered.** A run whose fast gates' last durations
  already sum past the deadline stays in phase 1 (§18.6, reason
  `phase2-budget`). So does every run while the switch is `off` or `shadow`
  (§18.11).

**State fields** in `land.json` (§8.d), written by the phase-2 controller
(`land_phase2.py`, built in s04):

| Field | Meaning |
|---|---|
| `phase` | `"1"` or `"2"`; absent means `"1"` |
| `phase2` | `{started_at, deadline_s, from_main, attempts, mode}` for the current window, removed when it ends |
| `moved_since_ack` | sorted union of moved paths over every carry since the ack (§18.9) |
| `carry_streaks` | per carry gate: `{streak, last_run_pass_at}` (§18.10) |
| `carried_gates` | on a verified push: the gate ids whose record on the pushed candidate has `carried_from` (§18.7) |

**Functions.** `land_phase2.enter(plan_dir, ctx, st)` returns None when phase 2
starts, else the reason the run stays in phase 1. `land_phase2.leave(plan_dir,
ctx, st, outcome)` removes the marker and the lease phase fields. `land.land`
calls `enter` after `_take_lease`, and `leave` in its `finally` before
`lr.release` (land.py:222-227). `step_push` also calls `leave` right after the
verified push (land_push.py:197-200), before `step_final_record` runs
(land_push.py:393-394); the `finally` call is the backstop.

`leave` is idempotent: a second call finds nothing to remove and does nothing.
It must also work after the lease is lost (the `lease-lost-*` parks). Removing
the marker never needs the lease, so it always runs, but only for a marker whose
`token` is this land's (a new holder's marker is never touched). The lease's
`phase` fields are cleared only while this token still holds the lease;
otherwise that part is skipped. The marker file is the part that must always go.

**Event.** `land_phase2` in `run.ndjson`, once per window, with `mode`,
`outcome` (`pushed`, `reset`, `parked`, `shadow` or `stayed-phase1`), `reason`,
`from_main`, `to_main`, `moved_paths`, `carried` (gate ids), `rerun` (gate ids),
`elapsed_s` and `attempts`. For `parked`, `reason` is today's park kind, or the
stop action when no `park()` was called.

**Invariants.**

1. **Phase 2 never starts without an ack.** No ack, no marker, no deadline.
2. **Phase 2 ends with a push, a reset or a park.** A park is any stop today's
   code already returns in the window (the bullet above), with its kind and
   brief unchanged, logged with `outcome: parked`. Every exit of `land.land`
   while `phase == "2"`, every park included, runs `leave` before the lease
   release (land.py:222-227).
3. **A reset never parks by itself.** It continues as phase 1, and phase 1 parks
   or stops exactly as today.

### 18.2 — The carry condition: ONE decision, extended by a code route

**Today.** `land_carry.decide` (land_carry.py:211-233) is the one decision.
`land_gate.step_gate` calls it whenever the gate key changed
(land_gate.py:437-444). `land_push._resync` reaches it only through `step_gate`
(land_push.py:138). The checks run in this order (land_carry.py:184-208):
`legacy-state`, `main-unmoved`, `not-ancestor`, `harness-class`,
`plan-path-touched`, `patch-id`, `plan-side-changed`, `blob`, `gate-set-key`,
`registry`. (`main-unmoved`, land_carry.py:190, is missing from §17.1
invariant 1's list; the code is right, and §18.14 amends the list.) Any harness
path in the range resets with `harness-class` (land_carry.py:195-196). A
classifier error is folded into the same reason (land_carry.py:175).

**Rule.** `decide` stays the ONE decision. No second decision point is built.
It gains a second route to carry, the code route. The checks run in this order,
and the first that fails names the reason:

1. `legacy-state` — no basis, or a basis whose `carry_version` is not `2`.
2. `main-unmoved` — unchanged.
3. `not-ancestor` — unchanged.
4. `classify-error` — NEW. Any path `classify_paths` returns in `errors`, any
   git or parse error while listing the range, or a `settings.json` text that
   does not parse. Today these fold into `harness-class`.
5. **Route choice, not a reason.** Every moved path is live-state or
   machine-churn: the harvest route, exactly §17.1. Otherwise the code route,
   which adds checks 6 and 7.
6. `harness-class` — code route only, and only while the switch is not `on`
   (§18.11). The reason string is today's, so `off` and `shadow` log exactly
   what they log today.
7. `force-reset-path` — NEW, code route only. Any moved path is on the
   force-reset list (§18.8).
8. `plan-path-touched`, `patch-id`, `plan-side-changed`, `blob`,
   `gate-set-key`, `registry` — unchanged, on both routes.

**Moved paths.** The union over every commit in `<basis main_head>..M`, each
commit against EACH of its parents. That is `_units` (land_carry.py:147-164),
which already runs `git diff --no-renames` per parent. `--no-renames` lists both
names of a rename. A merge commit lists its changes against each parent. A
change and its later revert both count. This is §17.1 invariant 4's rule,
"classify per commit, never the net diff", reused for the path set. The
reader's form is `git log -m --name-status -M <basis main_head>..M`, taking
both names of every `R` line.

**Disjoint** means all of these hold: no moved path is in the basis or the new
`plan_paths` (land_carry.py:197-199); no moved path is under `_plans/<slug>/`;
the gate set key and every gate registry are unchanged (land_carry.py:205-208);
the merge needed no resolution (`resolution_required` false,
land_carry.py:201-202); every moved path classifies (check 4). Anything else
resets.

**State fields.** `carry_basis` (§17.1 "State fields") keeps its content; its
`carry_version` becomes `2`. `decide`'s return gains `route` (`"harvest"` or
`"code"`), `moved_paths` (sorted) and, in shadow, `shadow` (`{"decision",
"reason"}` as `on` would have decided).

**Functions.** `land_carry.decide` and `land_carry._checks` (extended, s03);
`land_carry._range_classes` returns its error set apart instead of folding it
into `harness` (land_carry.py:175); a new pure
`land_carry.force_reset_hits(paths, settings_texts)`.

**Event.** `land_carry` (land_carry.py:297-303), with `carry_version: 2`, plus
`route` and `moved_paths`.

**Invariants.**

1. **One decision.** `decide` is called only from `step_gate`, as today
   (land_gate.py:444). The phase-2 controller and `_resync` read its verdict
   from `st["last_carry"]` (land_carry.py:289-290); neither calls it.
2. **Reset is the default.** Every check that errors is `git-error` or
   `classify-error`, never carry (the catch-all at land_carry.py:229-231 stays).
3. **A version-1 basis resets once** with `legacy-state`. §18.12 bounds when
   that can happen.
4. **The harvest route keeps §17.1's checks.** Only its gate handling in phase 2
   changes (§18.3).

### 18.3 — Gate classes come from the registry, never from a duration

**Today.** On a carry, `land_carry.apply` keeps only settled REVIEW records
(`_keep`, land_carry.py:255-258) and every test gate re-runs (§17.1 invariant
12). A review gate is `verify_pass.is_review_gate` (verify_pass.py:83-86).
`ci-check` runs `make check` with an empty `HOME` (.claude/eval-gates.json:417-436),
and `make check` ends with `test`, which runs every pytest suite (Makefile:20-21,
:49). `full-suite-gate` runs `scripts/session-quality-gate.sh --land-extras`
(.claude/eval-gates.json:45-52). That mode runs only the two classifier and Codex
scripts pytest does not collect, and the tests `conftest.py` skips under an
empty `HOME` (scripts/session-quality-gate.sh:96-107).

**Rule.**

- **A review gate always carries.** It is copied, never invoked (§18.10).
- **Every other argv gate has a registry field `phase2`: `"fast"` or
  `"carry"`.** A missing field means `fast`: the gate re-runs in phase 2.
- **`ci-check` is `carry`. `full-suite-gate` is `fast`.** s05 writes both into
  `.claude/eval-gates.json`.
- **Durations never decide a class.** The last recorded `wall_s` of each fast
  gate (land_gate.py:154, stored in the record at :180) only feeds the budget
  estimate (§18.6).
- **`phase2` enters the gate set key.** s05 adds it to `_gate_set_digest`
  (land_gate.py:290-309). A class change is then a `gate-set-key` reset, never a
  silent reclassification.
- **The classes apply in phase 2 only, on both routes.** In phase 1, §17.1
  invariant 12 is unchanged: every test gate re-runs.

**`full-suite-gate` is NOT the semantic-conflict detector.** It runs only the
`--land-extras` scope. `ci-check` is the gate that runs every pytest suite, and
in phase 2 it carries.

**Accepted risk (owner-visible).** Path-disjoint is not semantically disjoint.
Main renames a function; the plan adds a new caller of the old name in a file
main never touched. The paths are disjoint, the merge is clean, and no fast gate
runs the affected tests. In phase 2 this is caught only by the force-reset list
(when the rename is in one of its files, §18.8) and by the post-push CI (§18.7).
The same holds for a harvest of `_plans/**` that turns a test red (§17.1, "What
carries"): with `ci-check` carried, the post-push CI catches it.

**Measured cost of closing it.** An affected-suite re-run, the plan-execute
pytest suite sharded with xdist, on this plan's base tree, took 415 s wall
(2026-11-15, 12 CPUs, load average 18-22; 2150 passed, and 7 failed only
because the run used an empty `HOME`). That is over the 240 s phase-2 deadline,
so as a fast gate it would keep every land in phase 1. The command, its output
and the machine load are in `_evidence/s01/semantic-cost.txt` of this plan.
Making that suite, or a narrower affected part of it, a fast gate is a later
owner decision.

**Invariants.**

1. **A gate with no `phase2` field re-runs.** Missing is never `carry`.
2. **A review gate carries whatever its `phase2` field says.**
3. **A carried copy keeps its outcome, `findings_count` and `wall_s`.** The
   result digest therefore changes only when a fast gate's result changes.

### 18.4 — One captured main sha, one controller, one deadline

**Today.** `_resync` reads M once after the fetch and merges it by sha
(land_push.py:129-135). `_contains_expected` proves M is an ancestor of the
pushed head before every push (land_push.py:99-104, called at :183, :291 and
:313). `step_push` owns the retries: it pushes, and on a stale rejection calls
`_resync`, which merges and runs `step_record`, `step_gate` and `step_ack`
(land_push.py:165-220, :138-145). The bound is `MAX_RESYNC = 3` push attempts
(land_state.py:32, loop at land_push.py:176): the first push and at most two
re-syncs. A stale rejection on the last attempt parks `push-rejected`
(land_push.py:205-214). Each git call has its own timeout: fetch 600 s
(land_steps.py:59, land_push.py:129), merge 600 s (land_steps.py:65,
land_push.py:132), push 900 s (land_push.py:50). Each gate gets its registry
timeout (land_gate.py:152). No clock bounds the whole.

**Rule.**

- **One captured M per attempt**, proven an ancestor of the candidate before the
  push. Unchanged from §17.5.3.
- **One controller owns the retries: `land_push.step_push`.** Phase 2 adds no
  second loop. The phase-2 controller only supplies the deadline and the reset.
- **At most 3 push attempts in phase 2**, the existing `MAX_RESYNC`: the first
  push plus at most two re-syncs. "At most 3 retries in total" holds.
- **Exhaustion now resets, in `on` only.** A stale rejection on the last attempt
  resets to phase 1 (reason `phase2-retries-exhausted`) instead of parking
  `push-rejected`. This is the only park §18 turns into a reset. In `off` and
  `shadow`, today's park stays.
- **Every other stop in the loop stays today's park** (§18.1 invariant 2): a
  rejection that is not stale ("refused by the remote"), a lost lease, an
  `expected-not-in-head`, a `push-unverified`, and any park or stop of the steps
  `_resync` calls.
- **One monotonic deadline** covers the fetch, the merge, every gate, every
  retry and every push. It starts at `land_phase2.enter` and stops when a push
  exits 0. Start value: 240 s.
- **What the deadline does not cut.** Two steps cannot be killed safely, so
  they keep today's limit and are checked when they return: `git worktree add`
  in `step_worktree` (a kill leaves a registered, half-built worktree) and
  `record_plan` in `step_record` (a kill can leave a git lock). Fast reads and
  cleanups (`rev-parse`, `merge-base`, `merge --abort`, `worktree prune`) keep
  today's limit too. `land_phase2.run_step` checks the time left before every
  step and after every stop: none left is a reset, so no push starts after
  the deadline.
- **A land that pushed is never reset.** The clock stops when the push exits 0
  (`land_push._pushed`). The verification fetch after it (`_verify_landed`)
  keeps today's 600 s. The push cannot be undone, so a timeout there could
  only lose the record of it.
- **A push the deadline kills is never reset either.** A killed `git push` may
  already have reached the remote, so the clock stops and the timeout is
  today's error (`land_push.step_push`), never a reset.
- **Each operation gets `min(its own timeout, time left)`**: the registry
  timeout for a gate, today's constant for a git call.
- **A gate that overruns is killed and counts as failed.** That is a reset
  (reason `gate-overrun`), never a retry inside phase 2.
- **No push after the deadline.** `step_push` checks the time left before every
  push attempt. None left is a reset (reason `phase2-deadline`).

**Functions.** `land_phase2.remaining()` (seconds left, from
`time.monotonic`); `land_phase2.timeout_for(t)` returns `min(t, remaining())`.
`step_push`, `_resync`, `step_sync`, `step_merge` and `_run_argv_gate` take
their timeouts from it while `phase == "2"`.

**Events.** `land_phase2` (§18.1), and `land_carry` with `decision: reset` and
reason `phase2-deadline`, `gate-overrun` or `phase2-retries-exhausted`.

**Invariants.**

1. **One clock.** No fetch, merge, gate or push in phase 2 gets a timeout
   larger than the time left. The steps under "What the deadline does not cut"
   are checked when they return instead.
2. **One retry loop.** Only `step_push` retries a push.
3. **The deadline is the enforcement; the budget is only an estimate** (§18.6).

### 18.5 — Local writers wait for the push; server-side merges cannot

**Today.** Nothing holds a writer while a land pushes. `githooks/` holds only
`commit-msg` and `pre-commit`; there is no `pre-push`. Both clones on this host
point `core.hooksPath` at their own `githooks` (measured in
`~/your-private-harness` and `~/.claude`). The local writers are `gearbox harvest`
(scripts/gearbox:267) and `gearbox deploy` (scripts/gearbox:434, :449) in
`~/.claude`, and an operator's `git push` in either clone. A writer that moves
main mid-land makes the land's compare-and-swap push fail stale
(land_push.py:49-50), and the land re-syncs.

**Rule.**

- **A local writer waits for the marker, with a bounded wait.** The wait point
  is a new `githooks/pre-push` (s02). It waits while the marker is set and not
  expired, at most the hook wait cap (start: 300 s), polling.
- **It fails open.** Marker absent, expired or unreadable, or the cap reached:
  the push goes ahead. Every wait, and every fail-open, is logged. A read that
  never returns (a named pipe, a stalled disk) cannot outlast the cap: the hook
  arms its own timer at the cap plus 1 s, and a timeout logs `outcome: error`.
- **The land's own push never waits for itself.** The land passes its lease
  token in the environment variable `PLAN_LAND_PHASE_TOKEN`. The hook goes
  ahead at once when it equals the marker's token.
- **A server-side merge cannot be held.** A pull request merged on GitHub (for
  example by `scripts/merge_when_green.py`) moves main with no local hook. The
  land meets it as a stale push rejection and re-syncs (§18.4). This section
  does not, and cannot, close that.

**Functions.** `land_writers.wait_for_land(remote_url, cap_s)` (s02) returns
`clear`, `expired`, `cap`, `no-marker`, `own-token` or `error`.
`land_writers.marker_path(remote_url)` and `remote_hash(remote_url)` are the one
place the marker path of §18.6 is built; s04 writes the marker through them.

**How the hook reads the marker (s02).** It reads only the host-wide marker,
never the lease (the lease lives in another clone's `.git`). The marker is live
while `expires_at` (an ISO time with a timezone; `phase_expires_at` is read when
`expires_at` is absent) is ahead and its `phase` field, when present, is
`"push"`. Any other `phase` value means the land is not pushing: no wait. The
hook holds only a push whose remote ref is `refs/heads/<default>`, where
`<default>` comes from `refs/remotes/<remote>/HEAD` and falls back to `main`.
Every other push logs `outcome: other-branch` and goes on at once. The hook
never takes, renews or writes the lease.

**Which writers it covers (measured, s02).**

| Writer | Command that moves main | Covered |
|---|---|---|
| `gearbox harvest` in `~/.claude` | `git -C ~/.claude push` (scripts/gearbox:267) | only after `gearbox deploy` brings the hook to `~/.claude` |
| `gearbox deploy` in `~/.claude` | `git -C ~/.claude push` (scripts/gearbox:434, :449) | as above |
| Plan additions and harvested plan state | the harvest push above | as above |
| An operator's `git push` to main in `~/your-private-harness` | `git push` | once the primary checkout holds the merged hook |
| A plan's final-record push | `finish_record.py:300`, after `leave` removed the marker | waits only for another land's marker |
| The land's own push | `land_push.py:49` | never held: `PLAN_LAND_PHASE_TOKEN` |
| Plan-branch pushes (`plan_ship.push`, `plan_teardown`) | push a non-default ref | never held (`other-branch`) |
| Session commits, the janitor | commit only, no push | nothing to hold |
| Pull request merges on GitHub (`merge_when_green.py`, `gh pr merge`) | server side | **not covered**, and cannot be |

**The coverage gap.** `core.hooksPath` is per checkout. `~/.claude` points at its
own `githooks`, which changes only on deploy, and deploy stays the owner's
explicit request. A plan worktree uses `~/your-private-harness`'s absolute hooks path,
so the hook in `~/your-private-harness` is the primary checkout's copy. Until both
hold it, harvests are not held. `scripts/hook-status.py` reports, read-only, per
clone: `active`, `missing`, `not-executable`, `other-hook` or `not-a-repo`; exit 0
only when every clone is active. A shell-level failure (no `python3` on PATH) lets
the push go with no log line; every failure inside Python is logged as `error`.

**Event.** `land_writer_wait`, one line per pre-push, appended to
`~/.claude/.land-phase/writers.ndjson`: `remote_hash`, `outcome`, `waited_s`,
`marker_token` (when read), `error` (for `error`) and `ts`. `outcome` is one of
`wait_for_land`'s six values or `other-branch`. The hook has no plan directory
to log into.

**Invariants.**

1. **Never blocks past the cap.** The hook exits 0 after the cap, whatever the
   marker says.
2. **Never blocks on its own failure.** Any exception is `error`, logged, exit 0.

### 18.6 — The lease phase marker and the host-wide marker

**Today.** The lease file lives in `<git-common-dir>/plan-locks/`
(`_repo_lock_dir`, ship_locks.py:132-137; `_ship_lock_path`, :140-144). It is
held for the whole land protocol, 4500 s (§18.1). Its read-check-write runs
under the host mutex `<lock file>.mutex` (`lease_guard.mutex`,
lease_guard.py:40-59). A hook running in another clone (the `~/.claude` clone
that pushes harvests) has another `.git`, so it cannot find this lease file.

**Rule.**

- **On entering phase 2**, in ONE host-mutex hold: write `phase: "push"` and
  `phase_expires_at` into the lease file, and write the host-wide marker.
- **The marker** is `~/.claude/.land-phase/<h>.json`. `<h>` is the first 16 hex
  characters of SHA-256 of the remote URL, with any trailing `/` and `.git`
  removed. Fields: `marker_version` (`1`), `remote_hash`, `plan_slug`, `token`
  (the lease token), `pid`, `host`, `started_at` and `expires_at`. Both clones'
  `origin` URLs were identical when measured. The directory must
  be git-ignored in the source repo, or `gearbox drift` reports it (s04 adds
  the `.gitignore` rule).
- **Its expiry** is the phase-2 deadline plus 10 s, on the wall clock. The
  writer stores `expires_at` as an aware UTC time from `datetime.isoformat()`
  (`+00:00`); the hook also accepts a trailing `Z`, and any offset.
- **On leaving phase 2**, by any exit (push, reset or park), in ONE host-mutex
  hold: remove the marker, then the lease's `phase` and `phase_expires_at`. This
  happens before the lease release (land.py:222-227). When the lease is lost,
  only the marker is removed (§18.1, "`leave` is idempotent").
- **The budget.** Before entering phase 2, sum the last recorded `wall_s` of
  every fast gate; a fast gate with none counts its registry timeout. When the
  sum already passes the deadline, stay in phase 1 (reason `phase2-budget`).
  This is an estimate only; the deadline (§18.4) is the enforcement.
- **Three numbers, kept apart.** The phase-2 deadline (start 240 s), the marker
  expiry (deadline + 10 s) and the hook wait cap (start 300 s) are separate
  constants in `land_phase2.py` and `land_writers.py`.

**Event.** `land_phase2` with `outcome: stayed-phase1` and `reason:
phase2-budget` when the budget keeps a run in phase 1.

**Invariants.**

1. **The marker never outlives its window by more than 10 s.** A crashed land
   leaves an expired marker, which every reader ignores.
2. **A lease without `phase` behaves exactly as today.**
3. **Written and removed under the host mutex** of the lease file (§17.5.1).

### 18.7 — Post-push CI is the backstop; a red result is a decision brief

**Today.** `finish` asks `finish_ci.ci_verdict` about the pushed sha
(finish.py:331, :452). `finish_ci` tries runs on that exact sha first, then
falls back to the nearest descendant when an exact run is missing or every one
is cancelled (`_look`, finish_ci.py:334-357, fallback at :344-354;
`MATCHES = ("exact", "descendant")`, finish_ci.py:23). The verdict is a report;
nothing reverts.

**Rule.**

- **For a land that carried a gate, only a run on the pushed sha counts.** A
  descendant run is not accepted. "Carried a gate" means `land.json`
  `carried_gates` is not empty (§18.1); `finish` reads it.
- **Missing, pending, cancelled or unknown becomes a follow-up brief** with one
  owner action: re-run CI on the pushed sha, or accept the land without it.
- **A red result becomes a decision brief, never an automatic revert.**
- **Required probe (s05).** A pushed sha whose tree is broken, then a corrected
  descendant whose CI is green: `finish` must NOT report green for the land.

**Functions.** `finish_ci.ci_verdict(..., exact_only=True)` (s05).

**Event.** `plan_finish_ci` (finish.py:355) gains `exact_only` and `carried`.

**Invariants.**

1. **Never a revert.** No code path resets or reverts main on a red post-push
   CI.
2. **Exact sha only after a carry.** `match` is never `descendant` when
   `exact_only` is set.

### 18.8 — The force-reset list

**Rule.** The code route never carries when any moved path matches this list
(reason `force-reset-path`):

- `githooks/**`, `Makefile`, `settings.json`, `.claude/eval-gates.json`;
- `requirements*.txt` at any depth, and the lockfiles `*.lock`,
  `package-lock.json`, `uv.lock`, `poetry.lock` and `Pipfile.lock`;
- `scripts/gearbox-classify.py`, `scripts/deploy.pathspec`, `model-routing.yaml`;
- every gate registry file: `.claude/eval-gates.json` and
  `skills/plan-execute/references/eval-gates.default.json`
  (`_REGISTRY_PATHS`, land_carry.py:42-43);
- `pyproject.toml`, `pytest.ini`, `setup.cfg` and any `conftest.py`. This
  contract adds these four: the quality gate already treats them as able to
  change a suite's meaning (scripts/session-quality-gate.sh:116).

**`settings.json`** is a force-reset hit unless every changed key in every
commit is a churn key. That is §17.1 invariant 5's own test, through
`classify_paths` in `scripts/gearbox-classify.py`. Reason: `model` and
`effortLevel` rewrites arrive in many harvests (two in the 2026-11-15 range),
and resetting on them would undo §17.1's harvest carry.

`full-suite-gate` re-runs on the merged tree in phase 2. §18.3 says what it
does and does not cover.

**State, function and event.** No new state field: the hits are in the
`land_carry` event's `moved_paths`. Function `land_carry.force_reset_hits`
(§18.2). Event `land_carry` with `reason: force-reset-path`.

**Invariants.**

1. **Any classification error fails closed.** It resets (`classify-error`),
   never carries.
2. **The harvest route never needs this list.** A harvest has no harness path
   by definition (§18.2, check 5).

### 18.9 — Ack re-binding

**Today.** The ack binds the candidate `{plan_head, main_head, gate_digest,
merge_sha}` (`land_state.candidate`, land_state.py:252-253). Its `gate_digest`
is the RESULT digest (land_state.py:194-212). The gate SET key lives in the gate
key `expected:plan_head:merge_sha:<set key>` (land_gate.py:437-438), not in the
ack. A carry re-binds `plan_head`, `main_head` and `merge_sha` (`_ACK_PARTS`,
land_carry.py:237; re-bind at :276-282) and appends to `ack.carries`.
`step_ack` compares all four (land_steps.py:379-382).

**Rule.** Phase 2 re-binds through that same code:

- `main_head` moves to M1, and `merge_sha` to the new merge.
- `plan_head` moves to the new sync merge, because `step_sync` always merges
  main into the plan branch (land_steps.py:65-72). What must be unchanged is
  the plan side: `plan_commits`, the plan diff patch-id and every plan blob
  (§17.1 invariants 7-9).
- The gate SET key must be unchanged (`gate-set-key`). It is a different value
  from the RESULT digest, which is never re-bound (§17.1 invariant 13).
- Each `ack.carries` entry gains `route` and `moved_paths`.
- `land.json` `moved_since_ack` is the sorted union of every carry's
  `moved_paths` since the ack. The `plan_landed` event and the finish report
  list it, so the owner sees which main files moved after the ack.

**Event.** ONE `land_phase2` per window (§18.1), with `from_main`, `to_main`,
`moved_paths`, `carried` and `rerun`.

**Function.** `land_carry.apply` (land_carry.py:261-303) does the re-bind, as
today; s03 adds `route` and `moved_paths` to each `ack.carries` entry and
updates `moved_since_ack`.

**Invariants.**

1. **The result digest is never re-bound.** A fast gate that changes it sends
   the owner a new review (`step_ack`, land_steps.py:383-404).
2. **`moved_since_ack` is never shortened** while one ack stands.

### 18.10 — Carried gates are copied; the periodic full run

**Today.** A carry keeps a review pass by re-stamping it with the new key
(land_carry.py:270-275). Nothing records where it came from, and nothing limits
how often a pass is carried.

**Rule — copy, never invoke.** A carried record (review pass, carry-gate pass or
on-box answer) is the recorded record plus `carried_from` (the gate key it was
earned under) and `ran_at` (when it really ran). The gate is not invoked. The
plan's hardening notes that an argv review gate on an unchanged surface returns
INDETERMINATE and cannot be waived. That was not re-measured here, and the rule
does not depend on it. The land brief (s05, `land_brief.py`) shows a carried
gate as "carried from `<first 12 of the old merge>`, ran `<ran_at>`".

**Rule — periodic full run.** A land carries `ci-check` at most 4 times in a
row. It never carries it when the last real `ci-check` pass for this land is
older than 24 hours. Either limit resets phase 2 to phase 1 (reasons
`carry-streak` and `carry-stale`), so `ci-check` really runs.

**State field.** `land.json` `carry_streaks[<gate id>]`: `streak` (carries since
the last real run; a real pass sets it to 0) and `last_run_pass_at`.

**Functions.** `land_carry.apply` copies each kept record with `carried_from`
and `ran_at` (s03, s05). `land_phase2.streak_ok(st, gid)` checks both limits
before phase 2 copies a carry gate (s04).

**Events.** `land_phase2` lists copied gates in `carried`. A limit hit is a
`land_carry` reset with reason `carry-streak` or `carry-stale`.

**Invariants.**

1. **Only a settled record is copied** (`pass` or `verified-on-box`,
   `_SETTLED`, land_gate.py:82). A fail, skip or indeterminate is never copied.
2. **`ran_at` never moves on a copy.**

### 18.11 — The rollout switch

**Rule.** One setting, `PHASE2_MODE`, a module constant in
`skills/plan-execute/scripts/land_phase2.py`. Values `off`, `shadow` and `on`;
default `shadow`. Any other value reads as `off`.

- `off`: no phase 2, and nothing logged by it. Today's land exactly.
- `shadow`: phase 2 is computed and logged (`land_phase2` with `mode: shadow`,
  `land_carry` with a `shadow` field), and the land still resets exactly as
  today. No marker is written, so no writer waits. A park in the window logs
  `outcome: parked` exactly as in `on`, because shadow changes only carry
  decisions, never a park.
- `on`: the rules of this section.

Turning it `on` is a one-line change the owner makes after the criterion-7
count (§18.13) shows enough shadow decisions.

**State, function and event.** `land_phase2.mode()` reads `PHASE2_MODE`. Every
`land_phase2` event carries `mode`. No `land.json` field: the mode is code, not
state.

**Invariants.**

1. **In `off` and `shadow`, no land outcome differs from today's.** No marker is
   written, no ack is re-bound by the code route, and no gate is copied by its
   `phase2` class.
2. **Unknown means `off`.** A typo can never turn phase 2 on.

### 18.12 — Rollout order

- **Deploy only while no land is parked at an owner stop.** A `carry_basis` with
  `carry_version: 1` resets once with `legacy-state` (§18.2 invariant 3).
- **A lease without the `phase` field behaves as today** (§18.6 invariant 2).
- **The hook stays fail-open when the marker is absent** (§18.5).
- **This plan's own land runs under the old rules.** The running land process
  has today's code loaded, so a moving main can still restart it.
- **The registry change resets this plan's own carry, as expected.** s05 adds
  `phase2` to `.claude/eval-gates.json`. That file is a gate registry and on the
  force-reset list, so any carry on the landing plan resets (`registry`, or
  `gate-set-key` once `phase2` is in the set key).

**Invariants.**

1. **Old-format records read as today.** A basis of version 1 resets once; a
   lease with no `phase` and an absent marker change nothing.
2. **No owner answer is lost to the deploy** while no land waits at an owner
   stop.

### 18.13 — Event schema and the criterion-7 count

`land_carry` keeps its fields (land_carry.py:297-303) with `carry_version: 2`,
plus `route`, `moved_paths` and, in shadow, `shadow`. `land_phase2` (§18.1) and
`land_writer_wait` (§18.5) are new.

**Every new reset reason string:** `classify-error`, `force-reset-path`,
`phase2-budget`, `phase2-deadline`, `gate-overrun`,
`phase2-retries-exhausted`, `carry-streak` and `carry-stale`. A `land_phase2`
event with `outcome: parked` adds no reason string: its `reason` is one of
today's park kinds, and it is never written to a `land_carry` event.

The criterion-7 measurement (memory note `land-carry-measure-2026-12-14`)
counts a reset reason outside its `SOURCE` set as an unknown. Before it runs on
data from this code: add `force-reset-path` to `SOURCE`, because it is a source
change; count `phase2-budget`, `phase2-deadline`, `gate-overrun`,
`phase2-retries-exhausted`, `carry-streak` and `carry-stale` as a separate
phase-2-limit group; leave `classify-error` an unknown.

### 18.14 — Earlier rules this amends, by name

Each is marked in place with "R7".

- **§17.1 "Added by this contract"** ("every check that fails or errors, means
  reset") and **§17.1 invariant 4** ("every path must be live-state or
  machine-churn"): with the switch `on`, a harness path no longer always
  resets; it takes the code route (§18.2).
- **§17.1 invariant 1**: the order is §18.2's, which also names `main-unmoved`.
- **§17.1 invariant 12** ("test gates never carry"): in phase 2 a carry gate is
  copied (§18.3). Phase 1 is unchanged.
- **§17.1 invariant 15** and **§5.2**: "a main move for which
  `land_carry.decide` returns `reset`" now also covers phase 2's limit resets.
- **§17.3 "Outcomes"** ("any other move: it returns `land-awaits-review`"): a
  code-route carry pushes like a runtime-only move.
- **§4.5 / §4.5a**: exhaustion resets to phase 1 in `on` (§18.4).
- **§17.5.3**: one deadline and one retry owner (§18.4).
