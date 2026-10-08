# Parallel-group manifest contract — AMENDED 2026-08-21 (contract v3; supersedes v2, v1)

**This file is the single statement of record for what a `parallel_group` may and
may not do.** Where any other document (including
`autonomous-execution-primitives.md` and `plan-harden/parallelization-lint.md`)
appears to say something different about worktree isolation in the plan dispatch
path, this file wins.

Originally frozen by plan session S06 (`_plans/plan-framework-upgrade-2026-08-12/`,
item PL-02) *before* the executor (S06B) and builder (S07) implement against it.
Contract v2 is the 2026-08-15 amendment that makes the already-built
orchestrator-managed worktree lifecycle harness-neutral: Claude and Codex both
honour the same declaration. It changes by decision — a superseding plan session
that bumps the contract version in this heading — never by drift from an
implementation that found it inconvenient.

## Contents

- [Verdict: ROUTE A, scoped to orchestrator-managed worktrees](#verdict-route-a-scoped-to-orchestrator-managed-worktrees)
- [Capability ledger](#capability-ledger)
- [1. What a parallel group is](#1-what-a-parallel-group-is)
- [2. Member rules (normative)](#2-member-rules-normative)
- [3. The integration session](#3-the-integration-session)
- [4. Group lifecycle](#4-group-lifecycle)
- [5. Where each rule is enforced](#5-where-each-rule-is-enforced)
- [6. Version gate](#6-version-gate)
- [7. What these gates do NOT catch](#7-what-these-gates-do-not-catch)
- [8. Supersession](#8-supersession)
- [9. Contract v3 amendment (2026-08-21) — groups under plan isolation](#9-contract-v3-amendment-2026-08-21--groups-under-plan-isolation)

## Verdict: ROUTE A, scoped to orchestrator-managed worktrees

**A parallel group MAY declare worktree isolation. The mechanism is
orchestrator-managed `git worktree add` — never the Agent tool's
`isolation: "worktree"`, which is barred by name.**

The lifecycle is harness-neutral. Claude member dispatch is given the member
worktree in its prompt; Codex member dispatch starts its `codex exec` command in
that checkout and places the prompt plus `--output-last-message` receipt under
its ignored `.plan-worktrees/codex/` runtime directory. The integration session
always runs in the shared checkout after producer branches merge into it.

The two mechanisms are not interchangeable, and the difference is the whole
verdict:

- **Task-level `isolation: "worktree"` — BARRED.** S02 measured it silently
  destroying real on-disk agent output that lived in an untracked or gitignored
  path, on the first zero-contention attempt, with no error and no signal to the
  orchestrator (`_evidence/s02/worktree-spike.md` §6 probe 1; §9 row
  *Auto-cleanup safety*: "Confirmed unsafe"). A mechanism that can lose an
  agent's work is not viable regardless of how cleanly it merges.
- **Orchestrator-managed `git worktree add` — the mechanism this contract
  builds on.** S02's own verdict: *"The orchestrator-managed `git worktree add`
  path is the mechanism P1 should build on"*. Its §9 row for *Auto-cleanup
  safety* reads "N/A — `git worktree add` never auto-removes; cleanup is always
  an explicit, orchestrator-issued command." The defect that disqualifies the
  Task-level mechanism does not exist here.

**Containment is ADVISORY, not harness-enforced. This is the contract's most
important limitation and it is stated first rather than buried.** A member is
*told* its worktree path in its prompt; nothing in the harness confines it there.
S06 measured the alternative and it is not available — calling
`EnterWorktree(path=…)` from a freshly dispatched subagent is refused:

> `Cannot enter worktree: the current working directory
> ~/your-private-harness is the repository root, not an isolated
> worktree — switching is only available to sessions whose working directory is
> inside a worktree of this repository.`

(That refusal contradicts the `EnterWorktree` tool's own documentation, which
claims by-path entry works "from agents whose working directory was pinned at
launch". Do not re-attempt it on the strength of the docs; re-measure first.)

Advisory containment is still worth having, because the failure mode of a member
that ignores its worktree is *exactly the shared tree we have today* — never
worse. But it means **the tree-protecting guarantees rest on the member rules in
§2, not on isolation**, and it means isolation must be *detected* rather than
assumed: §3 requires the integration session to verify each member's writes
actually landed in that member's worktree.

## Capability ledger

Every claim this contract depends on, and how it is known. Anything not measured
is labelled as such — an implementer may not promote a "documented" row to a
"measured" one without re-measuring.

| Claim | Status | Source |
|---|---|---|
| Task-level `isolation:"worktree"` destroys git-invisible output on task end | **measured** | S02 §6 probe 1 |
| Task-level isolation cannot be aimed at another path/repo (no parameter) | **measured** | S02 §0 |
| `git worktree add` never auto-removes; cleanup is explicit | **measured** | S02 §9 |
| Worktree creation ~55–70 ms; commit ~40 ms under 5-way concurrency | **measured** | S02 §2 |
| 10 concurrent `git worktree add` + commit: 10/10 survived | **measured**, environment-specific | S02 §3 (its own "honest limitation" applies) |
| `git worktree add -b` with no start-point bases on local `HEAD`, not `origin/<default>` | **measured** | S02 §4 |
| A fresh worktree is tracked-files-only; untracked/ignored never carry over | **measured** | S02 §5 |
| Codex `exec` exposes `-C/--cd`, `workspace-write`, and `--output-last-message` for a worktree-rooted dispatch | **measured** | dual-harness P0; 2026-08-15 Codex worktree dispatch regression |
| Producer-first merges land clean; a same-line conflict fails loudly (exit 1, `UU`, markers) | **measured** | S02 §7 |
| `EnterWorktree(path=…)` from a dispatched subagent at repo root is REFUSED | **measured** | S06 probe, quoted above |
| A dispatched subagent honours a prompt-directed working directory | **observed**, not enforced | S06/S06B lane sessions ran correctly in orchestrator-created worktrees |
| `.git/index.lock` contention at real dispatch fan-out (issue #55724) | **OPEN — not cleared** | S02 §3, §Verdict |

The last row is why M1 exists in its strongest form: under this contract no
member ever commits, so the reported contention mode is not merely mitigated, it
is never entered.

## 1. What a parallel group is

A `parallel_group` is a set of sessions the dispatcher runs concurrently as
subagents. Membership is `sessions[].dispatch.parallel_group`.

- The value MUST be a **non-empty string**. `null` means "not grouped"; an empty
  string is a manifest error, refused at both gates. (Without this rule §1 and
  `dispatch.next_action` disagree: the batcher tests truthiness, so `""` would be
  a member here and not a group there.)
- **R1 — dependency symmetry.** Every member of a group MUST have an identical
  `depends_on` set. `dispatch.next_action` batches *the first ready session plus
  every ready peer sharing its group*, so asymmetric deps make peers become ready
  at different times and the group silently degrades to sequential dispatch with
  no error anywhere.
- **Isolation is declared at group level, on every member**, as
  `dispatch.isolation: "worktree"`. Every member of a group MUST carry the same
  value — a half-isolated group is refused. Absent/`null` means a shared-tree
  group, which stays legal for existing plans (§6).

## 2. Member rules (normative)

A **member** is any session whose `dispatch.parallel_group` is a non-empty
string. All five rules apply to isolated and shared-tree groups alike unless a
rule says otherwise.

### M1 — a member MUST NOT commit or push

`post_session.git` MUST be absent, `null`, or `"none"` for a member. `"commit"`,
`"commit-push"` and `"commit-push-pr"` are refused.

*Why:* concurrent committers race on `.git/index.lock` — the failure S02 left
open (see ledger) — and on a shared tree a commit made mid-flight captures
whatever half-written state its peers have on disk. Shipping is a whole-group
act (§3), not a per-member one.

**Honest limit:** this refuses a *declared* shipping action. It cannot stop a
member from running `git commit` itself from a prompt or a script. Under
isolation that stray commit lands on the member's own branch and is contained;
on a shared tree it is not. §3's HEAD-baseline check is what detects it.

### M2 — a member MUST NOT change dependencies or lockfiles

No member item's `touches` may name a dependency manifest or lockfile.

*Why:* two agents each running an install regenerate the whole lockfile and
conflict on nearly every line — and an agent asked to "resolve" that silently
drops dependencies. Dependency changes belong to a sequential session or to the
integration session, never to a group member. Isolation does not help here: the
conflict is at merge, and its resolution is the dangerous part.

Detected deterministically from the **basename** of each path in the member's
items' `touches` (case-insensitive), against a closed list (`package.json`,
`package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`,
`bun.lockb`, `requirements*.txt`, `constraints.txt`, `Pipfile`, `Pipfile.lock`,
`pyproject.toml`, `poetry.lock`, `uv.lock`, `setup.py`, `setup.cfg`, `Gemfile`,
`Gemfile.lock`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `composer.json`,
`composer.lock`, `Podfile`, `Podfile.lock`, `pubspec.yaml`, `pubspec.lock`,
`mix.exs`, `mix.lock`, `build.gradle`, `build.gradle.kts`, `pom.xml`) plus the
patterns `requirements*.txt` and `*.lock`.

### M2a — `touches` is MANDATORY for a member's items, and the gate FAILS CLOSED

Every item owned by a member MUST declare a non-empty `touches`, and
`manifest.json` MUST carry it. The one exception is an explicit empty list
`[]` (schema v8): it declares that the item writes nothing, so it passes M2a
and cannot trip M2 or M5. A missing key, `null`, or an empty or blank string
is still refused.

This rule exists because the contract was nearly frozen with a gate that could
never fire. `touches` is optional in the spec schema and, at freeze time,
`build_plan.py` did **not** copy it into the manifest at all — measured across
every plan in this repo: **307 manifest items, 0 with `touches`; 177 spec items
with `touches`.** A dispatch-time M2 keyed on manifest `touches` would have
returned "clean" on every plan ever built. A check that passes because its input
is empty is worse than no check.

Therefore:

- **Builder (S07):** emit `items[].touches` into `manifest.json`. Refuse at build
  time any group member owning an item with no `touches`.
- **Executor:** if a member's item has no `touches` in the manifest, **refuse the
  dispatch** naming the item. Do not skip the check, and do not warn-and-continue
  — M2 and M5 are both computed from this field, so absent input means the two
  rules protecting the tree are both silently inert.

If you want a session to run in parallel, you must declare what it writes.
There is deliberately no opt-out flag: a per-session override on a
tree-protecting gate is the same hole as no gate. The escape for a member that
merely *reads* a dependency file is to narrow `touches` to what it writes, or to
take the session out of the group.

### M3 — the isolation mechanism is fixed by name

`dispatch.isolation` may only be absent, `null`, or the string `"worktree"`,
meaning **orchestrator-managed `git worktree add`**. Refusal keys on **key
presence and value**, never on truthiness, so a typo or an unknown mechanism is
refused rather than quietly treated as "no isolation".

The executor MUST honour `"worktree"` for both plan-execute harnesses using the
orchestrator-managed lifecycle above. It MUST refuse before status mutation if
worktree preparation, base verification, prompt/receipt placement, merge, or
containment checks fail; it may not silently fall back to the shared tree. A
Codex member's command MUST `cd` into its member checkout, and its prompt and
receipt MUST be inside that checkout so `workspace-write` cannot be asked to
write outside its sandbox. The integration command has no member worktree and
MUST run from the shared checkout after the producer-first merge.

The Agent tool's own `isolation: "worktree"` parameter MUST NOT be used to
dispatch a plan session, for the reason in the verdict.

### M4 — the group's verify gates must be concurrency-safe

A gate that derives its file set from the whole working tree (anything reading
`git status --porcelain`) tests its peers' half-finished edits. Such a gate must
not be placed on a shared-tree member. Under isolation a per-member gate runs
**inside that member's worktree**, which resolves the common case.

**This rule is NOT machine-enforced** — the executor cannot read a gate script's
intent — and it appears in §5's table marked as such rather than omitted. It is
carried by author judgment plus `plan-harden`'s parallelization lint. Do not
read its presence here as a guarantee.

### M5 — members must not write the same file

No two members of a group may declare overlapping written paths in their items'
`touches` (overlap = identical path, or one path being a directory prefix of the
other).

- **Shared-tree group: REFUSED.** Interleaved writes to one file corrupt both
  members' work, and nothing downstream can reconstruct it.
- **Isolated group: WARNING**, quantified by the overlapping paths. With separate
  worktrees an overlap is a *merge cost*, not corruption: S02 §7 measured a
  planted same-line conflict failing loudly (exit 1, `UU`, conflict markers), so
  the failure is visible and recoverable rather than silent.

This split is the contract's answer to `plan-harden`'s `file-write-conflict`
blocker, and it is what S08 re-derives the lint against.

## 3. The integration session

An integration session is **declared, not derived**: exactly one session per
isolated group carries `dispatch.integrates_group: "<group name>"`.

Derivation was tried and rejected during review: defining the role as "the
session that owns the group's single git action" cannot be evaluated without
already knowing which session it is, admits two qualifying sessions with no
tie-break, and silently conscripts any final capstone that depends on everything
and commits — handing it requirements its author never saw.

Rules, all refused at both gates:

1. **Exactly one** integration session per isolated group. Zero or two is an
   error, not an ambiguity.
2. It MUST `depends_on` **every** member of the group (a strict superset is fine)
   and MUST NOT itself be a member.
3. Its `verify.gates` MUST be a **superset of the union of its members' gates** —
   this is what makes "re-run the full gate set on the merged tree" a checkable
   requirement rather than prose. A clean textual merge does not imply a working
   tree; the semantic break (member A renames a symbol, member B adds a caller of
   the old name) merges without complaint and is caught only here.
4. It owns the group's **single** `post_session.git` action.
5. It merges member branches **producer-first**. A conflict halts loudly with
   every member branch intact — never an automated resolution (S02 §7 measured
   the loud failure; an agent "resolving" a merge is how work disappears).
6. Before merging it MUST verify **containment**: each member's changes are
   present in that member's own worktree/branch, and the shared tree has not been
   written by a member. Advisory containment (see verdict) is only worth
   something if it is checked.
7. Before merging it MUST record a **baseline**: repository `HEAD`, branch, and
   the pre-existing dirty state. It stages only group-owned paths and never
   sweeps up unrelated edits that were in the tree before the group ran.

A **shared-tree group** takes rules 1–4 and 6–7 in the form that still apply
(there is nothing to merge), and its integration session is **optional**: a plan
that never commits is legitimate. Its absence is a WARNING naming the group,
because M1 bars the members from shipping, so without one the group's work never
reaches a commit.

## 4. Group lifecycle

Frozen so the executor does not have to invent it and the builder does not have
to guess it.

- **Membership is immutable once the group is in flight.** `add-session`,
  `amend-session` and `retire-session` MUST NOT change any member's
  `parallel_group`, `dispatch.isolation`, or `depends_on` once any member has
  left `TODO`. A member added to an already-dispatched group would simply
  dispatch alone — the same silent degradation R1 exists to prevent, arriving
  through a door R1 does not cover.
- **A member that ends non-DONE blocks integration.** If any member closes
  `BLOCKED`, or ends `PARTIAL`, the integration session does not dispatch. This
  overrides `depends_on_policy: "completed_or_terminal"` for integration
  sessions specifically: merging a half-finished member's branch is exactly the
  "partial output mixed with successful peer output" failure.
- **Retry is per member, in its own worktree.** A re-dispatched member reuses its
  worktree and branch; the orchestrator never deletes a member worktree that has
  uncommitted or unmerged content (that is the S02 §6 data-loss shape, and the
  one thing orchestrator-managed cleanup must never imitate).
- **Cleanup is explicit and last.** Member worktrees and branches are removed
  only after the integration session's gates pass and its commit lands.

## 5. Where each rule is enforced

One implementation, two call sites, so the two gates cannot drift:
`skills/plan-execute/scripts/parallel_contract.py`.

**Disposition of the rules that already had homes:** R1 was implemented twice —
`structural_gate._group_coherence` (reached from `containment_report`, from
`plan_mutate._run` as a refusal, and from `run_gate` as a warning) and again in
plan-builder's `validate_spec`. `parallel_contract` now owns the rule;
`_group_coherence` is a thin delegation that keeps its existing containment
call sites and their warning-vs-refusal behaviour unchanged; `validate_spec`
imports the same module. No rule gets a third implementation.

| Rule | Build time (`validate_spec.py`) | Dispatch time (`run.py begin`) | Version-gated? |
|---|---|---|---|
| §1 non-empty group id | refuse | refuse | v3+ |
| R1 dependency symmetry | refuse | refuse | **no — ungated, as today** |
| §1 uniform isolation across members | refuse | refuse | v3+ |
| M1 no commit/push | refuse | refuse | v3+ |
| M2 no dependency/lockfile touch | refuse | refuse | v3+ |
| M2a `touches` present (fail closed) | refuse | refuse | v3+ |
| M3 isolation value / unhonourable mechanism | refuse | enforce; refuse on preparation/dispatch failure | v3+ |
| M4 gate concurrency-safety | **not machine-enforced** — author judgment + `plan-harden` lint | same | n/a |
| M5 overlapping writes | refuse (shared) / warn (isolated) | refuse (shared) / warn (isolated) | v3+ |
| §3 integration session rules 1–4 | refuse (isolated) / warn if absent (shared) | same | v3+ |
| §3 rules 5–7 (merge, containment, baseline) | n/a | executor behaviour, not a manifest check | v3+ |
| §4 lifecycle | n/a | refuse the mutation / withhold dispatch | v3+ |

**Both gates are mandatory.** The dispatch-time gate is not redundant: a manifest
can be hand-edited, produced by an older builder, or mutated in flight by
`run.py add-session` / `amend-session`. *The gate protecting the tree must not
live only in the tool that wrote the manifest.*

Refusals fire in `cmd_begin` **before the lock and before any status mutation**,
and are keyed on manifest membership rather than on the batch actually being
dispatched — so a member dispatched alone via `--only-session` is still checked
as a member.

## 6. Version gate

The rules marked v3+ in §5 apply only to manifests stamped
`plan_schema_version >= 3` — the same gating pattern `plan_impact` uses
(`closeout_pipeline.PLAN_IMPACT_MIN_SCHEMA`). A missing or non-integer version is
treated as below the threshold.

Gating is enumerated **by rule ID in the §5 table**, not by section number, and
R1 is deliberately ungated: it is enforced for all versions today, and a contract
that version-gated it would *regress* existing behaviour.

This is load-bearing, not ceremony. At freeze time two v2 plans in this repo
(`claude-code-reliability-selfassessment-2026-07-03`,
`skill-quality-audit-2026-07-05`) carry parallel members with
`post_session.git: "commit"`, which M1 now forbids. Ungated, this contract would
refuse to resume them.

Below the threshold the executor **warns** rather than staying silent, so the
diagnostic still reaches the operator on exactly the "produced by an older
builder" case §5 gives as the reason the dispatch gate must exist.

**The version stamp is a compatibility mechanism, not a security boundary.**
Hand-stamping a v3 manifest back to v2 disables these rules. That is accepted:
every gate here defends against mistakes and drift, not against an operator
deliberately editing the manifest to defeat them.

## 7. What these gates do NOT catch

Stated plainly so no one mistakes the enforcement table for a guarantee:

- **Actual filesystem and git effects.** Every check reads declared manifest
  fields. A member that runs `git commit`, `uv add`, or writes a file it never
  declared passes all of them. §3's containment and baseline checks are what turn
  some of that into a detection after the fact.
- **Prose.** Only structured fields are scanned. A prompt that describes a
  dependency bump the item's `touches` omits is invisible here — that is the
  parallelization lint's judgment call. The gate is deterministic on purpose: a
  gate that guesses from prose cannot be trusted to refuse, and a refusal that
  fires on a hunch trains authors to route around it.
- **Gate script intent** (M4).
- **A member ignoring its assigned worktree**, since containment is advisory.

## 8. Supersession

This is contract **v2**, which supersedes v1 only on the harness boundary: the
same Route A lifecycle now applies to Claude and Codex dispatch. A future
revision that, say, gains harness-enforced containment would supersede the
verdict's advisory-containment paragraph and §3 rule 6, and leave the rest
standing. "Frozen" means an implementer may not change it; it does not mean a
later decision cannot.

**Contract v3 (2026-08-21)** supersedes v2 on one axis only — where a group's
branches and worktrees live when the plan itself is isolated — and is stated in
full in §9. Every v2 rule (M1–M5, §3's integration rules, §4's lifecycle, §5's
enforcement table, §6's version gate) stands unchanged. v3 is the amendment
`plan-isolation-contract.md` §16 names as its companion; it exists because that
contract's §1 nests group branches under a plan branch, which is a change to this
file, and this file declares itself frozen against implementer change.


## 9. Contract v3 amendment (2026-08-21) — groups under plan isolation

Written by plan session S04 of `_plans/plan-level-git-isolation-2026-08-20/`
(item CON-01), alongside `plan-isolation-contract.md` v1. It is a **decision**,
in the sense §8 requires: it changes this contract because a plan-level
authority now exists above it, not because an implementation found v2
inconvenient.

Three rules, then a verdict.

### V3-1 — Group branches and worktrees nest under the plan's

When the enclosing plan is isolated (`plan_schema_version >= 7`, or
`--isolate`), a group member's branch and worktree are:

| | v2 (unchanged for non-isolated plans) | **v3, isolated plan** |
|---|---|---|
| Member branch | `plan/<group>/<sid>` | `plan/<plan-slug>__<group>__<sid>` |
| Member worktree | `.plan-worktrees/<group>/<sid>/` | `.plan-worktrees/<plan-slug>__<group>__<sid>/` (a **sibling** of the plan worktree, never nested inside it — `plan-isolation-contract.md` §1.1a) |
| Member base | repository `HEAD` | the **plan branch** `plan/<plan-slug>` |
| Integration merges into | the shared checkout | the **plan worktree** |

`worktree.py`'s `member_branch()` and `member_path()` are the two call sites; both
gain the plan slug **only** above the version gate, so every existing plan keeps
`plan/<group>/<sid>` exactly as measured today at `worktree.py:51-52` and `:292`.

**AMENDED 2026-08-22 (operator decision, s07).** This row first read
`plan/<plan-slug>/<group>/<sid>` — a name git cannot create. Git will not hold a
ref and a ref-directory of the same name, so `refs/heads/plan/<slug>/<group>/<sid>`
is refused (exit 128) while the plan's own branch `refs/heads/plan/<slug>` exists,
and `git worktree add -b` fails the same way at exit 255. Measured as loose refs
and after `git pack-refs --all`; transcript in
`_plans/plan-level-git-isolation-2026-08-20/_evidence/s07/v3-member-branch-refused-by-git.txt`.
Under the nested name that collision was not a rare legacy case but the shape of
**every** group of **every** isolated plan, so the rule refused the design it
belongs to. The member branch therefore uses the same `__` separator §1.1a already
uses for the member worktree path: one flat ref per member, the plan slug retained,
every other row of this table unchanged.

**A ref-namespace collision is still a refusal, not a warning**, and one case
remains: a legacy group whose branch `plan/<X>/<sid>` collides with a new plan
whose own branch is `plan/<X>`. `begin` refuses, naming the conflicting ref. See
`plan-isolation-contract.md` §1.4.

**Ownership is still never inferred from the name** (`plan-isolation-contract.md`
§1.5, ADR-0002). A branch matching `plan/<plan-slug>__<group>__<sid>` that maps to
no plan state file is UNKNOWN and untouchable; one claimed by two plans is
CONFLICT. The nesting makes names *more* legible, which is precisely why it must
not be allowed to become an ownership signal.

### V3-2 — "The shared tree" means the PLAN WORKTREE

Every v2 rule that says "the shared tree", "the shared checkout" or "the
repository checkout" now reads **"the plan worktree"** when the enclosing plan is
isolated. Concretely:

- **§2 M1** — a member still MUST NOT commit or push. Under isolation its stray
  commit lands on `plan/<plan-slug>__<group>__<sid>`, contained inside the plan
  branch rather than on the operator's tree.
- **§2 M4** — a gate reading `git status --porcelain` in a member worktree now
  sees only that member. The plan worktree bounds it a second time, so the "gate
  tests its peers' half-finished edits" failure needs both isolations to be off.
  Derive the file set as `plan-isolation-contract.md` §10 requires — including
  untracked files, never `git diff HEAD`.
- **§2 M5** — an overlap between two members stays a WARNING under isolation and
  a REFUSAL on a shared-tree group. Unchanged; only the tree it is measured
  against moves.
- **§3 rule 5** — the integration session merges member branches producer-first
  **into the plan worktree**, on `plan/<plan-slug>`, never into `main` and never
  into the primary checkout. Landing on `main` is the plan's land stage, and it
  belongs to `plan-isolation-contract.md` §4 alone.
- **§3 rules 6–7** — containment and baseline are checked against the **plan
  worktree's** HEAD and dirty state, not the primary checkout's.
- **§4** — worktree cleanup ordering gains
  `plan-isolation-contract.md` §12.3: `unlock` → `remove` → `prune`, because
  `git worktree prune` skips locked worktrees and the plan worktree is created
  locked.

**What does NOT move:** the plan's own `_plans/<plan-slug>/` directory. The
orchestrator writes the **primary checkout's** copy for the whole run, and no
commit from inside any plan or member worktree stages a `_plans/` path
(`plan-isolation-contract.md` §8). A member worktree's copy of `_plans/` is
frozen twice over — once at the plan worktree, once at the member's — and is
never the plan's record.

### V3-3 — The Agent tool's native `isolation: "worktree"` STAYS BARRED, BY NAME

§2 M3 and the v2 verdict bar it; v3 restates the bar rather than letting the
arrival of plan-level worktrees be read as a relaxation.

**S02 measured the Agent tool's task-level `isolation: "worktree"` silently
destroying real on-disk agent output living in an untracked or gitignored path —
on the first zero-contention attempt, with no error and no signal to the
orchestrator** (`_evidence/s02/worktree-spike.md` §6 probe 1; §9 row *Auto-cleanup
safety*: "Confirmed unsafe").

Nothing in plan-level isolation changes that measurement, and plan-level
isolation makes the temptation *worse*, not better: once a plan runs in a
worktree, "the harness already has a worktree mechanism, use that one" is the
obvious-looking simplification. It is the wrong one. The two mechanisms differ on
the only axis that matters — orchestrator-managed `git worktree add` **never
auto-removes** (S02 §9: "cleanup is always an explicit, orchestrator-issued
command"), and the task-level mechanism removes on task end, taking git-invisible
output with it.

The bar is on the **named parameter**: `isolation: "worktree"` on the Agent tool
MUST NOT be used to dispatch a plan session, a group member, or an integration
session, at any schema version, isolated plan or not.

### Verdict: nest the branches, move the tree, keep the bar

**Group isolation nests inside plan isolation rather than sitting beside it.**
A member's branch and worktree are addressed by `(plan-slug, group, sid)`, the
member's base is the plan branch, and the integration session merges into the
plan worktree — so a group's entire lifecycle happens below `plan/<plan-slug>`
and reaches `main` only through the plan's single land (`plan-isolation-contract.md`
§4).

Three consequences worth stating plainly:

1. **Every v2 tree-protecting rule gets stronger, and none gets weaker.** The
   rules are unchanged; the tree they protect is smaller and no longer the
   operator's.
2. **`begin` gains one new refusal** — the `plan/<slug>` vs `plan/<slug>/<sid>`
   ref collision between a new plan and a LEGACY group of that name (V3-1). It is
   a refusal because git itself cannot represent both, so a warning would be a
   warning about something that is already broken. V3's own member names cannot
   produce this pair: they are flat (`plan/<slug>__<group>__<sid>`).
3. **The Agent tool's native worktree isolation is barred by name and stays
   barred.** A mechanism that can lose an agent's work is not viable regardless
   of how conveniently it now rhymes with the plan's own worktree.

Below the version gate nothing in §9 applies: a non-isolated plan's groups keep
`plan/<group>/<sid>`, `.plan-worktrees/<group>/<sid>/`, and the shared checkout,
exactly as v2 specifies and as measured on all 28 manifests on disk today, of
which 0 are at `plan_schema_version >= 7`.
