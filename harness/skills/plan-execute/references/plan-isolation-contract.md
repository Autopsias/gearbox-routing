# Plan-isolation contract — FROZEN 2026-08-21 (~~contract v1~~ contract v2, amended 2026-10-01 by R1–R5, see §16)

**This file is the single statement of record for how `/plan-execute` isolates a
plan into its own git branch and worktree, and how that work lands.** Sessions
s05–s10 of `_plans/plan-level-git-isolation-2026-08-20/` implement it; where an
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

**Isolation is version-gated and inert today.** Measured 2026-08-21 across
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
| `plan_schema_version` on disk: 28 manifests, 0 at ≥ 7 | **measured 2026-08-21** | §6 |
| Claude Code writes an absolute `core.hooksPath` into `$GIT_COMMON_DIR/worktrees/<wt>/config.worktree`, and worktree-scoped config beats the shared value | **documented, NOT re-measured here** | anthropics/claude-code#60620 |
| Shared `.git/hooks` shims bake absolute paths | **documented, NOT re-measured here** | lefthook#1398 |
| Git Town (MIT, **3.4k stars**, re-read 2026-08-21) implements sync → ship → undo; its `fast-forward` ship strategy exists to "prevent false merge conflicts when using stacked changes" and to "Ship several branches in a stack without unnecessary CI runs", and requires the feature branch to be up to date | **documented** | git-town.com/preferences/ship-strategy; github.com/git-town/git-town |
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
| Group member branch under a plan | `plan/<plan-slug>__<group>__<sid>` (amended 2026-08-22 — the nested form `plan/<plan-slug>/<group>/<sid>` is a ref git refuses beside `plan/<plan-slug>`; see `parallel-group-contract.md` V3-1) |
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
  `gearbox-dyno-v3-2026-07-27` and `gearbox-dyno-v4-2026-07-27`.

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

*Amended 2026-10-03 (§14; finish-plan-follow-ups s05, cross-reference
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

**3.5 — EXPIRY ALONE NEVER TRANSFERS OWNERSHIP.** A contender may reclaim an
expired lease **only after recording the takeover** in three places: the new lock
file (carrying the displaced holder's token, plan, host, pid and `expires_at`),
the taker's `run.ndjson` as `shipping_lease_taken_over`, and the victim's
`run.ndjson` as `shipping_lease_lost`. The original holder's `ship-record` then
**REFUSES**: `{"action": "failed", "reason": "lease-lost", "lease_status":
"taken-over"}`, the step is left `running` and never marked `done`, the plan
halts, and a `decision_brief` names the options. Measured end to end in s02 §5,
with the neuter-once control in §6 (lease intact → `action: done`).

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
  **Measured 2026-08-21 in a scratch fixture, both directions** — because a lease
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

#### R1 (2026-10-01) — `finish` fast-forwards the owner, only when provably safe

**Revised through §14.** The measurement: after the model-rating-and-gateways
land, `git status` in the primary checkout showed `LAND_NOTICE.txt`,
`_worktrees/g1.json` and `_plans_index.md` modified, so the printed command
alone left the checkout dirty and behind. The operator decided on 2026-10-01.

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

#### R2 (2026-10-01) — the re-gate adds every `at_land` gate, and a red one can be repaired

**Operator decision, 2026-10-01.** The re-gate's gate set is the plan's union
of verify gates **plus every gate the candidate merged tree's registry
(`<land_path>/.claude/eval-gates.json`) flags `at_land: true`**
(`finish-contract.md`). Those gates enter the gate digest (§5.1) like any
other. When every non-passing gate is an `at_land`, `kind: "argv"` gate whose
outcome is `fail`, `land` runs each once on the base (`expected`). A gate that
also fails there parks `gate-inherited`: the plan did not break it. Otherwise
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


**5.2** Any of the following **INVALIDATES** the ack and returns the plan to
`AWAITS_REVIEW`: a re-sync that changed the plan-side tree or the gate digest
(§4.5a — a re-sync meeting **both** unattended conditions does **not**
invalidate), a hook rewriting the tree during the merge commit, an amended commit
on either side, a changed gate result, or **`record-plan` (§8.c) staging anything
outside `_plans/<plan-slug>/`**.

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

**R3 (2026-10-01) — a third, explicitly-permitted `<default>` mutation: the
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

**R4 (2026-10-01) — `LAND_NOTICE.txt` and `_worktrees/` are RUNTIME, kept
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

---

## 13. Prior art and the BUILD decision

Recorded, not assumed. Both alternatives were read before the decision.

- **Git Town** — MIT, **3.4k stars** (re-read 2026-08-21) — already implements
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
| **park** (session blocked or gate failed) | **kept** | **kept** | **kept, still locked** | **released** — a parked plan holds nothing | kept, `state: parked` | written; the park reason recorded |
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

**R5 (2026-10-01) — two new rows: `land-repair` and `finish`.**

| State | Local plan branch | Remote plan branch | Worktrees + lock | Lease | `$GIT_COMMON_DIR/plan-state/<slug>/` | `_plans/<slug>/` record |
|---|---|---|---|---|---|---|
| **land-repair** (R2) | **kept**; fast-forwarded to the candidate's `HEAD`, then the repair session commits on it | kept | plan worktree kept, locked; the land worktree kept until the next `land` rebuilds the candidate | released | `land.json` counts the repair rounds | the repair session is added to the plan |
| **finish** (after `land-success`, or after the last terminal session of a non-isolated plan) | already deleted (isolated) | already deleted | a `preserved` plan worktree is retried once, never forced | one fresh `git:` lease for the record push and the fast-forward | `finish.json`: `armed_at` (from `begin`), pinned shas, `finished_at` once done | after the R3 preflight, the uncommitted remainder is pushed (R3; report-only when unarmed); the owner is fast-forwarded only under R1 |

## 16. Supersession

~~This is contract **v1**, frozen 2026-08-21.~~ This is contract **v2**, amended
2026-10-01 by the operator's decisions recorded in `finish-contract.md`. v1 was
frozen 2026-08-21. "Frozen" means an implementer may not
change it; it does not mean a later decision cannot. Amendments arrive by §14 or
by a superseding plan session that bumps the version in this heading.

It has one companion: `parallel-group-contract.md` **v3**, amended in the same
session, which nests group branches under the plan branch and redefines "the
shared tree" as the plan worktree. The two are consistent by construction; where
they overlap, the parallel-group contract governs group *membership* rules and
this file governs the *plan* branch, worktree and land.

**v2 revisions (2026-10-01):** R1 (§4.3, `finish` may fast-forward the owner),
R2 (§4.4, `at_land` gates and `land-repair`), R3 (§8.c2, the `finish` record
push), R4 (§8.e, `LAND_NOTICE.txt` and `_worktrees/` are local runtime), R5
(§15, the `land-repair` and `finish` rows). The `finish` step itself is
specified in `finish-contract.md`, which governs where the two overlap.
