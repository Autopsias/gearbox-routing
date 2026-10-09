# Route-at-dispatch contract — schema v8 (contract v1)

**This file is the single statement of record for who picks a session's model and
effort from plan schema v8 on.** The plan author says what the work is, what is
risky and how it is checked. The executor (`/plan-execute begin`) says which
model and effort run it. The builder (`plan-builder`) and the executor both
change against this text. Where another document disagrees, this file wins.

Frozen by plan session S03 of `_plans/example-dispatch-plan-2026-09-29/` (item
CON-01) before the builder and executor sessions implement it. It changes by a
superseding plan session that bumps the contract version in this heading, never
by drift. Cases decided in this session rather than in the plan-harden grill are
marked **DECIDED-HERE (S03)**; cases the grill decided are marked
**DECIDED-HERE (plan-harden grill)**.

## Contents

- [1. Version gate](#1-version-gate)
- [2. What the author decides](#2-what-the-author-decides)
- [3. What the executor decides](#3-what-the-executor-decides)
- [4. Overrides](#4-overrides)
- [5. Locked checks (`verify.locked`)](#5-locked-checks-verifylocked)
- [6. Usage per attempt](#6-usage-per-attempt)
- [7. Words](#7-words)
- [8. Release-2 entry gate and the measured baseline](#8-release-2-entry-gate-and-the-measured-baseline)
- [9. Where each rule is enforced](#9-where-each-rule-is-enforced)

## 1. Version gate

DECIDED-HERE (plan-harden grill).

- Everything in this contract applies from `plan_schema_version` 8, named
  `ROUTE_AT_DISPATCH_MIN_SCHEMA = 8` in `plan_version_gate.py` (next to
  `ISOLATION_MIN_SCHEMA = 7`). `SUPPORTED_MAX_SCHEMA = 8` in the same file.
- A plan below 8 dispatches byte-identically to today.
- **The manifest's `plan_schema_version` governs execution.** The spec's value
  governs only what the builder stamps.
- **Fresh stamp:** the builder stamps 8 only on a spec that declares
  `plan_schema_version` 8. Every other spec of 8 or below keeps today's stamp
  of 7. So a spec below 8 still builds a byte-identical manifest.
- **Future schemas are refused, never downgraded.** The builder refuses a spec
  that declares a `plan_schema_version` above `SUPPORTED_MAX_SCHEMA` (8). The
  error names both numbers. The builder never stamps a lower version than the
  spec declared. DECIDED-HERE (s03, operator redispatch).
- **`--preserve-state` rebuild:** keeps the prior manifest's published stamp
  exactly as today, with one refusal. A rebuild across the v8 boundary is
  refused in either direction:
  - a spec declaring 8 or more over a manifest stamped below 8;
  - a spec below 8 over a manifest stamped 8 or more.

  Reason: validation would follow one version while execution follows the other.
  The refusal names both numbers and says to rebuild fresh (no
  `--preserve-state`) or to align the spec.
- `begin` refuses a manifest whose `plan_schema_version` is above
  `SUPPORTED_MAX_SCHEMA`. This protects only an executor that has this code; see
  the rollback limits in [3.6](#36-off-switch-and-rollback).

## 2. What the author decides

DECIDED-HERE (plan-harden grill). The author decides **what, how and when**:

| Field | Where | Rule at v8 |
|---|---|---|
| `task_class` | session | **Required.** One of the classes in `model-routing.yaml` (`mechanical`, `standard_build`, `agentic_build`, `deep_reasoning`, `linchpin`). A v8 session without it fails the build. |
| `peer_triggers` | session | The existing risk flags. **No new field.** They feed the override floor in [section 4](#4-overrides). |
| `touches` | every item | Required on every item, as today. |
| `verify.gates`, checks, evidence contracts | session | Unchanged. |
| `verify.locked` | session | **New, optional.** See [section 5](#5-locked-checks-verifylocked). |
| `model`, `reasoning` | session | **Optional, as a pair: set both or neither.** Set only as an override; setting only one fails the build. See [section 4](#4-overrides). |

The author no longer picks the model and effort. A required `task_class` adds no
Codex eligibility (see [3.4](#34-codex-eligibility)).

## 3. What the executor decides

DECIDED-HERE (plan-harden grill). The executor decides **who**.

### 3.1 Resolution

For a v8 session with no `model`, `begin` calls
`scripts/resolve_route.py resolve(task_class, active_provider)`. The result is
the **class default** cell `{model, reasoning}`.

`active_provider`, in this order:

1. `--harness codex` → `openai`.
2. Otherwise `provider_lane.load_routing()`'s existing order:
   `PLAN_EXECUTE_ROUTING_PROVIDER`, then a `CLAUDE_CONFIG_DIR` whose basename is
   `.claude-glm` (→ `zai`), then the routing file's `active_provider`
   (→ `anthropic` on the Claude tree today).

No new lane detection is written; `provider_lane.effective_provider` is reused.

### 3.2 After resolution nothing changes

Tier-agent selection (`_translate_claude_token`), the zai clamp
(`provider_lane.clamp_cell`), escalation (`escalate()`) and degrade run
unchanged, starting from the resolved cell. Rework stays same-rung first, as
today.

### 3.3 Resolve once per escalation generation

- The **first** `begin` of an escalation generation resolves the cell and freezes
  `{model, reasoning, routing_version}` in session state
  (`run_state.json` under the session, key `resolved_cell`).
- Every later attempt, rework, escalation step, refuse-rung check and ledger
  record reads that frozen cell through **one helper** (`frozen_cell(plan_dir,
  sid)`). No caller calls `resolve()` a second time. A routing-file change
  mid-plan therefore cannot move the base.
- Only an operator redispatch or amend starts a new generation, and only that
  resolves again.
- DECIDED-HERE (S03): if the routing file's `version` has moved since the freeze,
  `begin` prints both versions in the receipt and keeps the frozen cell. It does
  not warn-and-stop; the freeze exists so the base does not move.
- DECIDED-HERE (S03): if `resolve()` raises or returns no cell for the
  `task_class` on the active provider, `begin` refuses the session with a brief
  naming the class and provider. It never falls back to the orchestrator's model.

### 3.4 Codex eligibility

- `executor_policy.executor_for` in `model-routing.yaml` (read by run.py
  `_executor_for`) stays the gate for implicit, provider-driven Codex dispatch.
- An explicit Codex pin and `--harness codex` keep their existing waivers and
  safeguards (run.py `_resolve_session_spec`, around lines 2550-2570).
- A required `task_class` adds no Codex eligibility.

### 3.5 The begin receipt: `effort_mechanism`

The receipt prints `effort_mechanism` for every resolved cell, reusing run.py's
existing values, plus one new one:

| Value | Meaning |
|---|---|
| `agent_definition` | The dispatch agent's frontmatter sets the effort. |
| `tier_agent` | A `tier-<model>-<effort>` agent binds the effort. |
| `prompt_directive_advisory` | No agent binds it; the cell runs at the **orchestrator's** effort. |
| `codex_cli` | Codex dispatch; effort set by `-c model_reasoning_effort`. |

A zai cell keeps the label of its Claude-side mechanism. Whether the Z.ai
endpoint applies the effort is an open question (the 2026-08-30 probes
conflict). This contract claims neither answer.

### 3.6 Off switch and rollback

- `begin --no-route-at-dispatch`, or env `PLAN_EXECUTE_ROUTE_AT_DISPATCH=0`,
  turns route-at-dispatch off, like `--no-isolate` for isolation.
- Under the switch a v8 manifest dispatches **authored cells only**. An unpinned
  session is **refused with a brief**. It never inherits the orchestrator's
  model.
- The switch is recorded in plan state like the isolation gate
  (`record_isolation_gate`'s pattern).
- The switch wins over a frozen cell: while it is on, frozen cells are ignored
  and unpinned sessions are refused.
- **Limits of the schema refusal.** A reverted copy of this code, or an older
  tree, runs a v8 manifest without error and lets unpinned sessions inherit the
  orchestrator's model. So:
  - roll back with the switch, **never a revert of s05**;
  - before a v8 plan runs on any tree, confirm that tree's
    `plan_version_gate.py` has `SUPPORTED_MAX_SCHEMA` of 8 or more.
- **The GLM tree.** `~/.claude-glm` keeps its own copy of plan-execute and of the
  routing file (version 22). `gearbox deploy` does not update it.
  A v8 plan must not run there until that copy has v8.

## 4. Overrides

DECIDED-HERE (plan-harden grill).

- `model` and `reasoning` stay optional, but only as an **override pair**. When
  the pair is set, the session carries an **override**:
  - `why_model` is required whenever the pair is set (the build fails without it);
  - the ledger records `routing_provenance: pinned_override`.
- DECIDED-HERE (s03, pair rule): at v8, `model` and `reasoning` are an override
  pair: set both or neither.
  - The builder refuses a v8 session that sets only one of them (`model` without
    `reasoning`, or `reasoning` without `model`). The error names the session and
    the missing field.
  - `begin` applies the same refusal to a v8 manifest, as defence in depth for a
    hand-edited manifest.
  - Why a model alone is refused: a model without reasoning has no rung, so
    escalation, the floor check and the ledger cell are undefined. Today's
    executor already treats that case as "never climbs"
    (`escalation.dispatchable_cell` requires both halves).
  - Why reasoning alone is refused: reasoning without a model has no provider, so
    the floor check cannot pick a ladder.
  - Pre-v8 plans keep today's behavior unchanged: a v7 session may still declare
    a model without reasoning.
- **Floor.** An override is **below the floor** when its cell ranks LOWER than
  the class default's cell on the provider's escalation ladder (operator decision
  2026-09-30). Rank orders tier first, by the provider's `models:` declaration
  (ascending; the `model_ladder` walks the same order), then effort, by `low <
  medium < high < xhigh < max` (no dial lowest). Reachability by `escalate()`
  steps is NOT the test: for `deep_reasoning` on anthropic (default `opus@medium`)
  the walk from `sonnet@high` enters opus at `high` and never visits the default,
  yet `sonnet@high` is below. A below-floor override is refused when the session
  has any `peer_triggers` or `task_class: linchpin`. Otherwise it is allowed and
  recorded.
- The floor check ranks on the escalation ladder of the **override's own
  provider**, through one shared helper, `resolve_route.below_floor` (the
  builder's refusal and plan-harden's lint both call it). Check order:
  1. **cross-provider** (the override's provider differs from the tree's): skip;
  2. **not on the ladder** (the model is not in that provider's `models:`, the
     effort is not a known level, or the provider has no escalation ladder): skip;
  3. **rank compare**: strictly lower is below, which includes the default's own
     model at a lower effort.
- **Cross-provider override** (for example a Codex model named on the Claude
  tree): skips the floor check and is recorded `pinned_override`. The existing bar
  on Codex pins for linchpin or irreversible work still applies.
- DECIDED-HERE (S03): an override equal to the class default is still an
  override for the ledger (`pinned_override`) and still needs `why_model`. The
  floor check passes it, because an equal rank is not "below".
- DECIDED-HERE (S03): if the override's provider has no escalation ladder in the
  routing file, the floor check cannot rank it and the override is treated as
  cross-provider (skip, record).

## 5. Locked checks (`verify.locked`)

DECIDED-HERE (plan-harden grill).

- `verify.locked` is a list of repo-relative file paths that must exist at
  dispatch.
- **Where paths resolve:** the folder where the worker writes — the plan
  worktree for an isolated plan, the member worktree for a parallel member,
  otherwise the shared checkout.
- **At begin:** sha256 of each path into `_verify_state/<sid>.locked.json`. A
  path missing at dispatch **refuses the session**.
- **At verify-begin, before any gate:** re-hash. A changed or deleted path fails
  the attempt as `LOCKED_CHECK_EDITED`.
- **Known limit (accepted 2026-09-30).** The snapshot lives in the plan folder,
  which the worker can write. A worker that edits a locked file AND rewrites the
  snapshot passes the check; a deleted snapshot fails the attempt, but the next
  begin re-snapshots the edited tree. The check catches accidental edits, not a
  worker that forges its own evidence. The operator accepted this for a
  single-operator harness; moving the hashes under the plan lock is the upgrade.
- **Generations.** The snapshot belongs to an escalation generation. Rework keeps
  the first snapshot. An operator redispatch or amend takes a new one, so a check
  the operator corrected does not fail forever.
- **Test-configuration fingerprint.** When a session declares `verify.locked`,
  begin also fingerprints (path and sha256) every protected file — tracked,
  untracked or ignored — at the begin commit. verify-begin refuses an **added,
  deleted or changed** one. Reason: a new `conftest.py` can make an untouched
  test report PASSED (the SWE-bench conftest exploit). Edit and deletion both
  fail hard.
- **The protected set is a class, not a list of examples.** It is every file
  that pytest reads as configuration or plugins, plus every file Python reads
  or runs at interpreter startup. DECIDED-HERE (S03), checked against two
  primary sources:
  - *pytest* — docs "Configuration" (docs.pytest.org/en/stable/reference/customize.html)
    and the source of pytest 9.1.1 (`_pytest/config/findpaths.py`,
    `_pytest/config/__init__.py`):
    - config files: `pytest.toml`, `.pytest.toml`, `pytest.ini`,
      `.pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`;
    - `conftest.py` at any depth;
    - plugin entry points: `entry_points.txt` inside any `*.dist-info/` or
      `*.egg-info/` folder. pytest loads every `pytest11` entry point that
      `importlib.metadata` finds on `sys.path`, and the rootdir is often on
      `sys.path`, so a metadata folder in the tree registers a plugin.
  - *Python startup* — docs for the `site` module
    (docs.python.org/3/library/site.html) and CPython 3.14 `Lib/site.py`;
    module forms from `importlib.machinery` (`SOURCE_SUFFIXES`,
    `BYTECODE_SUFFIXES`, `EXTENSION_SUFFIXES`):
    - every file or folder that Python's import system could load as the
      top-level module `sitecustomize` or `usercustomize` (imported by
      `execsitecustomize()` and `execusercustomize()`). The rule is by import
      name, not file name. "Could load" means any of these forms:
      a source file (`<name>.py`); a sourceless bytecode file (`<name>.pyc`
      where the source would sit); a package folder (`<name>/`, with or
      without `__init__.py`, which covers namespace packages); an extension
      module (`<name>` plus any suffix in `EXTENSION_SUFFIXES`, for example
      `.so`, `.pyd`, `.abi3.so`, `.cpython-314-darwin.so`). A package folder
      is fingerprinted by every file inside it. Any folder on `sys.path` can
      supply the module, so this matches at any depth. The fresh
      `PYTHONPYCACHEPREFIX` below covers caches in `__pycache__`; a sourceless
      `.pyc` is a module in its own right, so it is covered here by import
      name. DECIDED-HERE (s03, operator redispatch, import-name rule);
    - `*.pth` (each `import` line is executed);
    - `pyvenv.cfg` (sets the interpreter home and which site folders load).
  - Matching is by file name (by import name for the startup modules) at any
    depth under the write root. The walk skips
    only `.git/`.
  - *Package initializers of a locked path* — bounded, not at any depth.
    DECIDED-HERE (s06 rework 2). Every import form of `__init__` (`__init__`
    plus any suffix above) in the locked path's own folder and in each folder
    above it, up to and including the write root. Absent ones are recorded, so
    adding one fails as "added". Reason: pytest runs these before any test code
    in the locked file — at collection when the package chain is unbroken, and
    at `Package` setup across a gap. Probe, pytest 9.1.1: a
    two-line `pkg/__init__.py` made a failing `pkg/sub/test_x.py` pass, with and
    without `pkg/sub/__init__.py`. An extension-module `__init__` is imported
    before `__init__.py` (the loader order in `importlib`), so every form counts.
- **Bytecode caches.** Python loads a cached `.pyc` instead of the source when
  the cache header matches the source's size and mtime. So a planted cache can
  replace an unchanged `conftest.py` or locked test. Hashing caches does not
  work: gate runs write them legitimately. DECIDED-HERE (S03): verify-begin
  runs every gate with `PYTHONPYCACHEPREFIX` set to a fresh empty folder per
  attempt. Python and pytest's assertion rewriter both honour it
  (`sys.pycache_prefix`), so no cache in the tree or from an earlier attempt is
  read. Probe, Python 3.14.6: a stale cache with a matching
  header loaded the old code; a fresh prefix loaded the edited source.
- **Out of scope, stated rather than hidden:**
  - Environment variables (`PYTHONSTARTUP`, `PYTHONPATH`, `PYTEST_ADDOPTS`,
    `PYTEST_PLUGINS`). They are not files in the write folder. The orchestrator
    sets the gate environment, and `PYTHONSTARTUP` runs only in interactive
    mode.
  - Command-line options (`-c`, `-p`, `--rootdir`). The gate command owns them.
  - Files outside the write root: the interpreter's site-packages and the user
    site folder, where the `usercustomize` module normally lives. The worker could
    touch them, but they are not part of the plan's tree.
  - Any Python module that pytest, a plugin entry point, `conftest.py` or a
    test imports at run time. This includes modules named in `pytest_plugins`
    and a local module named by an `entry_points.txt` `pytest11` entry. Such
    code runs inside the test process and can alter pytest. No file list can
    close this: the import graph is open-ended and can change during the
    attempt. The review gate is the control. `entry_points.txt` stays in the
    fingerprint because it still catches a NEW plugin registration.
  - Every other `__init__.py` — in a sibling folder, or in any folder that is
    not the locked path's own folder or above it. It is code under test: a
    worker edits it legitimately, so it is not fingerprinted, and the review
    gate is the control. DECIDED-HERE (s06 rework 2).
- DECIDED-HERE (S03): a locked path that is a directory or a symlink is refused
  at build time. Only regular files are hashed.

## 6. Usage per attempt

DECIDED-HERE (plan-harden grill). Each worker attempt records:

```json
{"input_tokens": null, "output_tokens": null, "cache_read_tokens": null,
 "cache_creation_tokens": null, "cost_usd": null, "cost_source": "none",
 "source": "unavailable", "scope": "worker"}
```

| Field | Rule |
|---|---|
| `source` | `claude-json`, `subagent-transcript`, `codex-json` or `unavailable`. |
| `cost_source` | `reported`, `model_prices` or `none`. |
| `scope` | Always `worker`. Gate and orchestrator spend are **not** in it. |
| `input_tokens` | **Non-cached input, for every source.** Codex reports `cached_input_tokens` inside `input_tokens`; subtract it, or `model_prices` bills cached tokens twice. |
| `output_tokens` | Includes reasoning output. |
| Codex totals | The sum of its `turn.completed` events. |
| Pricing key | The **served** model id from attestation, never the alias (the `sonnet` alias changed its served model). |
| Missing rate | When `model_prices` has no rate for a charged token category (it has none for cache creation today), `cost_usd` is `null` and `cost_source` is `none`. Never price a subset. |

Cost comes only from a reported cost or from `model_prices`, never guessed.

DECIDED-HERE (S03): with `source: unavailable`, all four token counts are `null`,
not `0`, so a missing reading is never averaged in as a free attempt.

DECIDED-HERE (S07), from real transcripts read on this machine:

- `subagent-transcript` covers Agent-tool and Workflow-member dispatches alike
  (`subagents/[workflows/<wf>/]agent-<agentId>.jsonl`). Only a line with
  `stop_reason` carries a message's final `output_tokens`; the others are
  streaming snapshots. If any message lacks its final line, `output_tokens` is
  `null`, never a partial sum. The transcript's `message.model` is attestation.
- Codex: `input_tokens` excludes both `cached_input_tokens` and
  `cache_write_input_tokens`, so the four categories never overlap. A stream
  without `cache_write_input_tokens` leaves `cache_creation_tokens` `null`.
- Codex has no attested served id, so its cost stays `null` today.

## 7. Words

DECIDED-HERE (plan-harden grill) for the code name; the human name is open.

- Code keeps **`session`** everywhere: field names, file names, CLI flags.
- The human-facing name is the operator's pick at the S04 checkpoint:
  `slice` (proposed), `work package`, or keep `session`.

Definitions:

| Word | Meaning |
|---|---|
| **attempt** | One worker dispatch for a session. Rework and escalation each start a new attempt. |
| **rung** | One `{model, reasoning}` cell on a provider's escalation ladder. |
| **class default** | The cell `resolve(task_class, provider)` returns. |
| **override** | An authored `model`/`reasoning` on a v8 session; needs `why_model`. |
| **locked check** | A file named in `verify.locked`; the worker may not change it. |
| **escalation generation** | Attempts between two operator redispatches or amends. The frozen cell and the lock snapshot live here. |

## 8. Release-2 entry gate and the measured baseline

DECIDED-HERE (plan-harden grill).

**Entry gate.** Release 2 (the cost comparison) may start only when
`~/.claude/evals/routing/outcomes.ndjson` holds **at least 30 v8 sessions in
which every worker attempt has a non-null `cost_usd`**. A session with any
unpriced attempt is excluded and counted in the entry report, per dispatch path
(Claude tier agent, zai, Codex).

**Baseline (measured, before v8).** One unit per
`(project, plan, session, generation)`; the latest attempt row stands for the
unit. First-pass = latest row `result: passed` at `attempt: 1`. Final-pass =
latest row `result: passed`. Denominator = units with a terminal result
(every result except `rework`, which is still in flight). Same-rung versus climb
= reworked units (`rework_count > 0`) with `escalated_from` null versus set.

Totals: 1293 rows, **773 units** (581 passed, 85 wontfix, 59 blocked,
33 exhausted, 9 rework, 6 done_unverified). Terminal units 764.
**412 first-try passes**; 581 final passes. Of the 581 passing units, 412
(71%) passed first try. Reworked terminal units: 201, of which **168 same-rung
and 33 climbed**.

| task_class | cell (authored) | units | first-pass | final-pass |
|---|---|---|---|---|
| agentic_build | opus@high | 228 | 103 (45%) | 186 (82%) |
| deep_reasoning | opus@medium | 126 | 99 (79%) | 112 (89%) |
| standard_build | sonnet@medium | 100 | 65 (65%) | 82 (82%) |
| standard_build | sonnet@high | 83 | 46 (55%) | 68 (82%) |
| linchpin | opus@high | 33 | 12 (36%) | 20 (61%) |
| agentic_build | gpt-5.6-sol@xhigh | 33 | 9 (27%) | 15 (45%) |
| agentic_build | sonnet@max | 29 | 22 (76%) | 22 (76%) |
| standard_build | gpt-5.6-luna@max | 27 | 15 (56%) | 18 (67%) |
| mechanical | sonnet@max | 20 | 0 (0%) | 0 (0%) |
| (none) | opus@high | 13 | 6 (46%) | 11 (85%) |
| deep_reasoning | opus@low | 13 | 8 (62%) | 9 (69%) |
| agentic_build | sonnet@high | 12 | 6 (50%) | 12 (100%) |
| deep_reasoning | gpt-5.6-sol@xhigh | 5 | 3 (60%) | 4 (80%) |

Cells under 5 units are left out of the table: 22 cells, 42 units.
No row before v8 carries `cost_usd`, so the entry-gate count today is 0.

Command (re-run it; do not copy these numbers forward):

```bash
python3 - <<'EOF'
import json,collections,os
rows=[json.loads(l) for l in open(os.path.expanduser('~/.claude/evals/routing/outcomes.ndjson')) if l.strip()]
u={}
for r in rows:
    k=(r.get('project'),r.get('plan'),r.get('session'),r.get('generation'))
    if k not in u or (r.get('attempt') or 0,r['ts'])>=(u[k].get('attempt') or 0,u[k]['ts']): u[k]=r
res=collections.Counter(r.get('result') for r in u.values())
dec=[r for r in u.values() if r.get('result')!='rework']
first=[r for r in dec if r.get('result')=='passed' and (r.get('attempt') or 1)==1]
fin=[r for r in dec if r.get('result')=='passed']
print('rows',len(rows),'units',len(u),'results',dict(res))
print('decided',len(dec),'first_pass',len(first),'final_pass',len(fin))
rw=[r for r in dec if (r.get('rework_count') or 0)>0]
print('rework units',len(rw),'same_rung',sum(1 for r in rw if not r.get('escalated_from')),'climb',sum(1 for r in rw if r.get('escalated_from')))
t=collections.defaultdict(lambda:[0,0,0])
for r in dec:
    c=(r.get('task_class'),r.get('tier_authored'))
    t[c][0]+=1; t[c][1]+=r in first; t[c][2]+=r.get('result')=='passed'
for c,(n,f,p) in sorted(t.items(),key=lambda x:-x[1][0]):
    if n>=5: print(f'| {c[0]} | {c[1]} | {n} | {f} ({f/n:.0%}) | {p} ({p/n:.0%}) |')
print('cells with <5 units omitted:',sum(1 for v in t.values() if v[0]<5),'cells,',sum(v[0] for v in t.values() if v[0]<5),'units')
EOF
```

The plan predicted about 770 units and 410 first-try passes out of 579 passes.
Measured: 773 units, 412 first-try passes out of 581 passes. The small rise is
new rows written since the prediction (this plan's own sessions among them).

## 9. Where each rule is enforced

| Rule | Enforced in |
|---|---|
| Stamp, cross-boundary refusal, above-`SUPPORTED_MAX_SCHEMA` spec refusal | `plan-builder` `build_plan.py` |
| `task_class` required, `why_model` with override, locked paths are files | `plan-builder` validation |
| `SUPPORTED_MAX_SCHEMA` refusal, off switch, resolve-once, floor, receipt | `plan-execute` `run.py begin`, `plan_version_gate.py` |
| Lock snapshot and test-config fingerprint | `run.py begin` (snapshot), verify-begin (re-check) |
| Usage fields | the outcome ledger writer |
| Release-2 entry gate | the release-2 entry report |
