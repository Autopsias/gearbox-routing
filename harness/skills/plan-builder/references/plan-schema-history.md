# `plan_schema_version` — what each version turned on

The highest stamp lives in ONE place, `scripts/build_plan.py`'s
`PLAN_SCHEMA_VERSION` (8); which stamp a build writes is
`route_at_dispatch_build.manifest_stamp` (see version 8 below). This file is
its history, extracted 2026-08-23 when the s10 activation bump pushed
`build_plan.py` past its file-size baseline and the
ratchet was right about what to do with it: 55 lines of changelog inside a
constant's docstring is a changelog, not a docstring.

Every version below is OPT-IN BY STAMP. `/plan-execute` accepts any plan at
version 2 or above, and each feature switches on only for manifests stamped at
or above its own minimum — so a plan built before a feature existed never gains
a new refusal mode retroactively. The per-feature minimums are constants in
`plan-execute/scripts/` (`closeout_pipeline.DECISION_BRIEF_MIN_SCHEMA`,
`escalation.ESCALATION_MIN_SCHEMA`, `plan_version_gate.ISOLATION_MIN_SCHEMA`),
never re-typed here.

History: 2 — the executable-plan schema (2026-07). 3 — `plan_impact`
closeouts + the REPLAN checkpoint + the rendered plan change log.
4 — the mandatory per-item prior-art decision (`prior_art` or
`research_status: "skipped"`, RS-01/RS-02): validate_spec() refuses
a build where an item carries neither, but ONLY on a spec that opts in — see
`PRIOR_ART_MIN_SCHEMA` in `scripts/build_plan.py`. (First shipped unconditional; broke
11/15 plan-execute test shards plus the live mutation engine within the same
session, because validate_spec() is shared plumbing dozens of unrelated
callers reuse to materialize a throwaway spec — reworked same-day to gate on
an explicit opt-in instead, exactly like every other v3+/v4+ feature here.)
5 — the BLOCKED `decision_brief` (RS-04): a closeout with
`result: "BLOCKED"` must carry attempts / sourced findings / at most three
options / a recommendation, or the closeout is refused. Gated by
`closeout_pipeline.DECISION_BRIEF_MIN_SCHEMA` on the MANIFEST's stamp, so every
plan already on disk (all at 3 or 4) keeps the old contract — measured against
all ten live plans in `_plans/` before the bump landed, none newly refused.
6 — the UPWARD ESCALATION climb (ESC-02): a session whose gate keeps
failing at the same root cause is re-dispatched one rung UP the SSOT's ladder
instead of on the same rung forever. Gated by
`plan-execute/scripts/escalation.ESCALATION_MIN_SCHEMA` on the MANIFEST's stamp,
so every plan already on disk dispatches byte-identically — proved by a FROZEN v5
fixture manifest (`plan-execute/fixtures/v5-claude-lane/`) whose `begin` output is
asserted unchanged, not by regenerating historical plans (a read-only probe found
only 1 of 26 plans on disk regenerates identically TODAY, before any change, so a
regenerate-and-compare gate would have been false on arrival). A session opts out
with `escalation: false`. The same version carries the optional
`routing_experiment: {kind, proposal_id}` session tag, validated here and
propagated into the manifest for the outcome ledger to read back.
7 — PLAN-LEVEL GIT ISOLATION: a plan gets its own branch and its
own LOCKED worktree at `begin`, every session and gate runs inside it, and the
work reaches the default branch through the `land` protocol's compare-and-swap
push instead of a commit in the operator's checkout. Gated by
`plan-execute/scripts/plan_version_gate.ISOLATION_MIN_SCHEMA` on the MANIFEST's
stamp, so every plan already on disk keeps running in the shared checkout
exactly as it does today — measured, not assumed: the s05 sweep re-run at the
bump reports the crossing count over every manifest under `_plans/`, alongside
the number of manifests it actually read, because "zero crossings" and "I
scanned nothing" are otherwise the same result.

THIS BUMP IS THE ACTIVATION STEP, and it was deliberately held back twice —
s05 built the worktree with the stamp at 6, s09 left it there — so that nothing
isolated by default until the mechanism had been proven end to end AND once for
real. The two receipts are
`_plans/example-isolation-plan-2026-08-20/_evidence/s10/two-plan-e2e.json`
(two plans, one repo, commit/gate/land isolation, with a recorded neuter-once
failure) and `.../canary-real-plan.txt` (one throwaway v7 plan run end to end in
a clone of this repo with a real dispatched subagent, real hooks and a real
land). `plan-execute/scripts/test_plan_worktree.py` carries the tripwire that
fires if this constant ever crosses the gate again by accident.

8 — ROUTE AT DISPATCH (2026-09-29, route-at-dispatch s04): the author names the
kind of work (`task_class`, now required) and `/plan-execute begin` picks the
model and effort. `model` + `reasoning` become an optional override pair that
needs a `why_model`; a below-floor override on a linchpin or `peer_triggers`
session is refused; `verify.locked` names check files a session may not edit;
every item declares `touches` (`[]` for none). Rules in
`scripts/route_at_dispatch_build.py`, authority
`plan-execute/references/route-at-dispatch-contract.md`.

UNLIKE 3-7, THE FRESH STAMP FOLLOWS THE SPEC. A spec that does not declare 8
still stamps 7, so every existing plan rebuilds to a byte-identical manifest —
measured by `test_route_at_dispatch_build.py`, which runs the pre-v8 builder
(a pinned commit) in a subprocess over every pre-v8 `_plans/*/spec.json`
and compares, with a v8 spec as the positive control. `--preserve-state`
refuses a rebuild that crosses the v8 boundary, and a spec above
`SUPPORTED_MAX_SCHEMA` (8) is refused rather than stamped lower. The executor's
side (`plan_version_gate`) lands in the next session.
