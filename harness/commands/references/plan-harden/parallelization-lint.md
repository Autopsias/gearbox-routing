# Phase 4.0b — Parallelization-opportunity lint: full rule set

Read this from plan-harden.md's §4.0b (the placement rationale, skip condition, and
summary-slot stay inline there — this file is the checks themselves). Deterministic
first, judgment second: the blockers are computed from structured fields; only the
gate-intent check, the "semantic ordering" check and the decomposition suggestions are
prose-heuristics.

**Re-derived 2026-08-12 for worktree isolation (plan session S08 / item PL-04).** The
executor now really does run a declared group in per-member `git worktree` checkouts
(`skills/plan-execute/scripts/worktree.py`), so the rule that used to kill most
proposals — two sessions writing one file — is a *merge cost* for an isolated group and
a corruption risk only for a shared-tree one. What changed, and what deliberately did
not, is the table in [Rule ledger](#rule-ledger).

## Contents

- [Why this runs at Phase 4, not earlier](#why-this-runs-at-phase-4-not-earlier)
- [Inputs](#inputs)
- [Step 0 — isolation eligibility](#step-0--isolation-eligibility)
- [Step 1 — write-conflict matrix](#step-1--write-conflict-matrix)
- [Step 2 — ask the contract checker (deterministic)](#step-2--ask-the-contract-checker-deterministic)
- [Step 3 — lint-only blockers (judgment)](#step-3--lint-only-blockers-judgment)
- [Step 4 — semantic ordering (prose-heuristic)](#step-4--semantic-ordering-prose-heuristic)
- [Step 5 — decomposition opportunities](#step-5--decomposition-opportunities)
- [Step 6 — verdict + decision card](#step-6--verdict--decision-card)
- [Rule ledger](#rule-ledger)
- [Never auto-apply](#never-auto-apply)

## Why this runs at Phase 4, not earlier

Grouping is a function of the FINAL DAG, and Phases 1–2 mutate it — a hardening pass
can *add* dependencies (observed 2026-08-03: grilling made the pruner consume the
reaper's live-session registry, turning a file collision into a data dependency).
A grouping computed before hardening settles is computed against a plan that no
longer exists. Corollary: if the operator later elects an option that restructures
sessions (a split), recommend re-running `/plan-harden --from-phase 2` on the
modified plan — the new sessions were never adversarially reviewed.

## Inputs

`manifest.json` (fall back to session cards for a freeform plan):
`plan_schema_version`, `items[].touches`, session `prompt` text, `verify.gates`,
`post_session.git`, `dispatch.depends_on` / `parallel_group` / `isolation` /
`integrates_group` / `requires_human_checkpoint`, and the project's
`.claude/eval-gates.json` for gate definitions.

**The statement of record for what a group may do is
`skills/plan-execute/references/parallel-group-contract.md` (contract v1, FROZEN).
Read it before changing any check below, and cite it rather than re-deriving it.**
Its rule ids (R1, M1, M2, M2a, M3, M4, M5, §1, §3, §6) are the ones used here.

Verify machinery facts against the CURRENT source before citing them. The three
load-bearing ones, with their locations as of 2026-08-12:

- **Batch construction** — first ready session + ready peers sharing its
  `parallel_group`; members must share an identical `depends_on` set (R1)
  (`skills/plan-execute/scripts/dispatch.py`).
- **The contract checker** — `skills/plan-execute/scripts/parallel_contract.py`, the
  ONE implementation the builder (`validate_spec.py`) and the executor
  (`run.py begin`) both import. Step 2 calls it rather than restating it.
- **Gate placement under isolation** — `skills/plan-execute/scripts/verify.py`
  `gate_cwd()`: for an isolated member an argv gate runs *inside that member's
  worktree*, with its declared sub-path preserved. This is what retires the old
  `tree-scoped-gate` blocker for isolated pairs, and Step 3 quotes the one branch
  where it does NOT hold.

## Step 0 — isolation eligibility

Run this FIRST; it selects which form every rule below takes.

A candidate pair (two sessions with identical `depends_on` — the schema precondition
for sharing a group) is **isolation-eligible** when ALL of these hold:

1. `plan_schema_version >= parallel_contract.MIN_SCHEMA`. Read the constant from
   source, do not hardcode it. Below the threshold the contract rules are enforced at
   neither gate and the executor warns and runs a shared tree (contract §6) — so a v2
   plan gets the pre-worktree verdicts, unchanged, and that is correct rather than
   conservative.
2. `parallel_contract.ISOLATION_IMPLEMENTED` is True. False means this executor build
   REFUSES a worktree declaration rather than silently providing a shared tree
   (contract M3); every rule below then reverts to its shared-tree form.
3. Every item owned by either session declares a non-empty `touches` (M2a — the check
   FAILS CLOSED: measured at freeze time, 307 manifest items in this repo carried
   none, so a rule keyed on an absent field returns "clean" on every plan ever built),
   and none names a dependency manifest or lockfile (M2 — *"Isolation does not help
   here: the conflict is at merge, and its resolution is the dangerous part"*).
4. The plan contains a session that can serve as the group's **integration session**
   (§3): it `depends_on` every proposed member, is not itself a member, and can carry
   `verify.gates` ⊇ the union of the members' gates plus the group's single
   `post_session.git` action. An isolated group REQUIRES one — nothing else merges the
   member branches, and without it the work is stranded on them.

**A pair that is not isolation-eligible is not isolation-flagged, and every
shared-tree blocker applies to it in full.** Name the failing condition in the
evidence; "not eligible" without the reason is not a finding.

## Step 1 — write-conflict matrix

From each session's items' `touches` (plus file paths named in prompts as a
fallback), build sessions × files with W (writes/extends) or R (reads). Two
sessions both marked W on any file overlap. Treat "extend `<file>`" prompt
language as W even when `touches` omits it. Overlap is *identical path, or one path
being a directory prefix of the other* (`parallel_contract._overlaps`).

For an isolation-eligible pair this matrix is no longer a kill list — it is the
**merge-cost estimate** Step 2 quantifies.

## Step 2 — ask the contract checker (deterministic)

Do not re-derive the contract in prose; that is how this file went stale the first
time. For each candidate pair, build a probe copy of the manifest with the proposed
grouping applied — `dispatch.parallel_group` on both members,
`dispatch.isolation: "worktree"` when Step 0 said eligible, and
`dispatch.integrates_group` on the integrator Step 0 found — then call:

```python
refusals, warnings = parallel_contract.check(probe, schema_version=probe["plan_schema_version"])
```

That is the same code the builder and the executor run, so a pair the lint calls
groupable is a pair the gates will accept, and the returned message text is the
evidence to cite.

**One deliberate override.** When Step 0 condition 1 failed (an old
`plan_schema_version`), `check()` returns early with a "rules are NOT enforced"
warning and *no refusals at all* — so passing the manifest's own stamp would make a
legacy plan look clean. Pass `schema_version=parallel_contract.MIN_SCHEMA` instead,
with isolation OFF, and evaluate the pair under today's shared-tree rules. The lint is
advisory and never refuses, so the useful answer is what the pair actually risks;
report the stamp itself as part of the evidence, because the executor will *not*
enforce those rules until the plan is rebuilt. Every other Step 0 failure keeps the
manifest's own stamp.

Map the output onto the lint's rule ids:

| Checker output | Lint rule id | Disposition |
|---|---|---|
| `[M5]` **refusal** (pair not isolated) | `file-write-conflict` | 🔴 **hard blocker** — never propose the pair |
| `[M5]` **warning** (pair isolated) | `merge-cost` | ⚠️ quantified warning — the pair is still proposable |
| `[M1]` refusal | `member-shipping-declared` | 🔴 hard blocker (see Step 3) |
| `[M2]` / `[M2a]` refusal | — | Step 0 condition 3 failed; the pair is ineligible, so re-run the probe WITHOUT isolation and take the shared-tree verdicts |
| `[R1]` refusal | — | not a candidate pair at all (asymmetric `depends_on`); drop it silently |
| `[§1]`/`[M3]` refusal | — | a malformed or unhonourable isolation declaration already in the plan — report under 🟡 Polish, it is a plan bug independent of grouping |
| `[§3]` refusal | `integration-session-gap` | not a blocker on the pair; it is a **cost of the option** — state exactly what the integrator must gain (the missing members in `depends_on`, or the missing gates) |

**Quantify `merge-cost`** from the overlapping paths the M5 warning names:

- **low** — one or two overlapping *file* paths, no directory-prefix overlap. S02 §7
  measured a planted same-line conflict failing loudly (exit 1, `UU`, conflict
  markers), so the failure is visible and recoverable, not silent.
- **high** — a directory-prefix overlap (the merge surface is unbounded: neither
  session has declared which files inside it it writes), or more than two overlapping
  paths. Rate a directory overlap high even when it is the only one.

Carry the cost into the decision card against the wall-clock the pair saves. A `high`
merge cost buying two minutes is not an opportunity; say so rather than listing it.

## Step 3 — lint-only blockers (judgment)

Contract §7 states plainly what the checker cannot see: gate script intent (M4),
prose, and anything outside declared manifest fields. Those are this lint's half.

### `gate-mutates-global-state` — hard blocker, isolated AND shared-tree

Under isolation a per-member argv gate runs inside that member's worktree
(`verify.gate_cwd`, contract M4), which is what retires `tree-scoped-gate` for an
isolated pair: `scripts/session-quality-gate.sh` derives its file set from
`git status --porcelain`, and inside a worktree that IS the member's own change set,
with no peer edits in it. What isolation does NOT cover is a gate that mutates state
the worktree does not contain. Two signals:

- **Deterministic** — the gate's declared `cwd` resolves outside the repository.
  `verify.gate_cwd` leaves such a cwd alone rather than inventing a counterpart
  (*"Gate cwd lives outside the repository — a worktree has no counterpart for it, so
  leave it alone rather than invent one"*), so under isolation the gate still runs in
  one shared location for every member at once. At Phase 4.0b no worktrees exist yet,
  so the check is: resolve the declared `cwd` against the project root and flag it if
  it does not stay inside the repository. That is exactly the branch `gate_cwd` takes,
  and the corpus proves the correspondence by calling the real function against a
  throwaway repo (`gate-outside-repo` vs `gate-inside-repo`) rather than asserting it.
- **Judgment** — the gate's argv installs, syncs, links, deploys, migrates or
  restarts something outside its cwd (`uv sync`, `npm i -g`, `launchctl`, `docker`, a
  shared cache, a shared database, any network mutation). Worktrees isolate files on
  one machine; they do not isolate the machine. Flag with the argv quoted and let the
  operator judge.

### `tree-scoped-gate` — hard blocker, SHARED-TREE pairs only

Retained unchanged for a pair Step 0 found ineligible: both sessions declare a gate
whose script derives its file set from the whole working tree rather than the
session's own outputs (anything reading `git status --porcelain`). Each session's gate
would test the OTHER's half-finished edits — the silent-green class re-introduced from
the other direction. Conservative default for a shared-tree pair: any SHARED argv gate
is a blocker unless its script provably scopes to per-session artifacts.

### `member-shipping-declared` — hard blocker, both forms

Replaces the old `concurrent-commit-push`, which asked "do BOTH members commit?" and
so let a single committing member through. The contract's answer is structural: **no
member ships, ever** (M1), and exactly one declared integration session owns the
group's single git action (§3 rules 1–4). Verify three things:

1. Neither proposed member carries a `post_session.git` outside `absent | null |
   "none"`. The `[M1]` refusal in Step 2 is the evidence.
2. Exactly one session declares `dispatch.integrates_group` for the proposed group —
   zero or two is an error, not an ambiguity.
3. That integrator owns the git action and re-runs a superset of the members' gates.

M1's honest limit is worth repeating in the finding: it refuses a *declared* shipping
action and cannot stop a member running `git commit` from a prompt. Under isolation
that stray commit lands on the member's own branch and is contained; §3's HEAD-baseline
check detects it.

### `checkpoint-member` — hard blocker, unchanged

Either session has `dispatch.requires_human_checkpoint: true`. A checkpoint parks the
batch; a peer mid-flight makes the pause point ambiguous. Isolation changes nothing
here — the ambiguity is in the operator's attention, not the file system.

### `data-dependency-in-prose` — hard blocker, unchanged

One session's prompt names an artifact, function, or registry the other builds, even
though `depends_on` doesn't link them. Flag as a MISSING dependency (a plan bug worth
a 🟡 of its own), not as a grouping opportunity. Isolation makes this *worse*, not
better: the consumer would run against a base ref that predates the producer's output
and see nothing at all.

## Step 4 — semantic ordering (prose-heuristic)

Produce→approve→consume chains (a dry-run/evidence session, then a human
checkpoint, then an apply/acceptance session) are sequential by meaning; never
propose grouping across them. False positives acceptable — this check only
suppresses proposals, never blocks the plan.

## Step 5 — decomposition opportunities

When no existing pair is groupable, check whether a SPLIT would create one worth
having — only for sessions that mix independent surfaces:

- A session writing both shared code AND standalone new files (plists, docs,
  scripts) that conflict with nothing → split the standalone half into a session
  with `depends_on: []` and a self-scoped gate.
- Two independent modules forced into one file by plan shape → splitting the file
  layout may enable a parallel pair. Under isolation, count the cost honestly: the
  layout churn is often no longer worth paying, because the overlap the split was
  meant to remove is now a `low` merge cost the integration session absorbs. Propose
  the split only when Step 2 rated the merge cost `high`, or when a gate must be
  re-scoped anyway.

Estimate wall-clock saved from the sessions' `effort` chips. State it per option.

## Step 6 — verdict + decision card

Hold `PARALLEL_LINT = {verdict, blockers: [{pair, rule_id, evidence}], warnings:
[{pair, rule_id, cost}], options}`.

- `verdict: linear-optimal` — no groupable pair and no split worth its cost.
  One line in the summary block; NO decision card; do not pad options.
- `verdict: opportunities` — at most 3 options in the standard decision-card
  format (each with its trade-off in one line, a recommendation, and the
  do-nothing outcome), presented in the Phase 4.3 output. Cite the blocker
  evidence for every pair you did NOT propose. An option that proposes isolation
  MUST name all three parts the operator has to accept: the members'
  `dispatch.isolation: "worktree"`, the integration session, and the quantified
  merge cost.
- A `data-dependency-in-prose` hit is reported under 🟡 Polish regardless of
  verdict — it is a correctness finding, not a parallelism one.

The verdict line goes in the summary block's **Phases run** list either way, and
an operator decision (either way, including "keep linear") is recorded in the
summary so the question is not silently re-opened by a later run.

## Rule ledger

What the 2026-08-12 re-derivation changed, and what it deliberately did not. The
"regression corpus" column names the case in
`fixtures/plan-harden-lint/pl04-worktree-parallelization/` that proves the rule can
still fire (PLANT) and does not fire on clean input (ALLOW).

| Rule id | Isolation-eligible pair | Shared-tree pair | Regression corpus (PLANT / ALLOW) |
|---|---|---|---|
| `file-write-conflict` | **downgraded** → `merge-cost` warning, quantified | hard blocker (unchanged) | `legacy-shared-tree`, `lockfile-touch` / `disjoint-writes` |
| `merge-cost` | **new** — quantified `low`/`high` warning | n/a (the pair is blocked) | `base` (low), `merge-cost-high` (high) / `disjoint-writes` |
| `tree-scoped-gate` | **retired** — gates run in the member worktree (M4) | hard blocker (unchanged) | `legacy-shared-tree` / `base` |
| `gate-mutates-global-state` | **new** hard blocker | hard blocker | `gate-outside-repo` (deterministic), `gate-argv-global` (judgment) / `gate-inside-repo` |
| `concurrent-commit-push` | **replaced** by `member-shipping-declared` | replaced | — |
| `member-shipping-declared` | hard blocker | hard blocker | `member-ships` / `base` |
| `checkpoint-member` | hard blocker (unchanged) | hard blocker | `checkpoint-member` / `base` |
| `data-dependency-in-prose` | hard blocker (unchanged) | hard blocker | `prose-data-dep` / `prose-dep-declared` |
| `integration-session-gap` | **new** — a cost of the option, not a blocker | warning (a plan that never commits is legitimate) | `base` |

The corpus is EXECUTED, not read: `parallel_lint_walk.py` runs the rules above against
the live `parallel_contract` and `verify.gate_cwd`, asserts every PLANT fires and every
ALLOW stays silent, and exits non-zero otherwise. Changing a rule here without a case
there is how this file went stale the first time.

## Never auto-apply

This lint NEVER writes `parallel_group` or `isolation` itself. Setting either wrong
corrupts a run, members must share identical `depends_on` sets, and gate
concurrency-safety is a judgment the operator owns (M4 is explicitly not
machine-enforced). Isolation raises the stakes rather than lowering them: a group the
lint silently isolated would also need an integration session the operator never
agreed to, owning the plan's only commit. The lint recommends with evidence; the
operator elects; only then does an edit land (via the normal spec-edit → rebuild path
for plan-builder plans, per §4.2b).
