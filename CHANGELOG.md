# Changelog

All notable changes to the Gearbox routing policy and framework. Format follows
[Keep a Changelog](https://keepachangelog.com); versioning rules are in
`docs/VERSIONING.md`.

## [Unreleased]

### Added
- **Second harness refresh: new skills, hooks and scripts (2026-10-09).**
  - **Skills:** `adversarial-review`, `codex-skill-sync`, `cost-audit`, `eli5`, `janitor-status`, `janitor-stop`, `jev-fit-scan`,
    `memo-loop`, `mods`, `plan-harden`, `repo-health` and `worth-adopting`.
  - **Hooks:** guards that stop risky git and plan-state commands
    (`git-tree-guard.py`, `verify-state-guard.py`, `turnend-guard.py`), a nag
    for long-running agents, a routing-cadence check, and a context-compaction
    policy (`compact-policy.py` and its helpers). The agent janitor's session-end
    hook ships unwired: you opt in to it.
  - **Scripts:** the agent janitor, a machine governor (`govrun`) that queues
    heavy test runs, a code-quality ratchet (`scripts/quality/`), a supervised
    Codex runner, a memory-budget check, `model_prices.py` (prices by served
    model id), the status line script, and the harness's own route resolver.
  - **A rule** on keeping LLM reviews, gates and dispatched agents efficient.
  - **`harness/model-routing.yaml`** is now a copy of the public
    `claude/model-routing.yaml`, made by the sync script, so plan-execute and
    routing-retro find the policy and resolver where they look for them.
  - **Platform limits.** The agent janitor runs only on macOS (launchd,
    macOS `ps` and `lsof`), kills orphaned agent processes and deletes old temp
    files and Codex session logs without asking. `govrun` keeps its slot locks
    under your home directory (`~/.machine-governor`, or `GOVRUN_STATE_DIR`).
  - **Docs.** `ARCHITECTURE.md` §7 lists every hook `harness/settings.json`
    wires and gives the opt-in snippet for the janitor's session-end hook. The
    README has a short "What is new in the harness" section. `install.sh` is
    unchanged: it installs the routing policy only.

### Fixed
- **Two security fixes in the refreshed harness.** `jev-fit-scan` deep mode
  no longer gives its judge web access, and it wraps all fetched web text in
  marked untrusted-data blocks that the agents are told never to obey. The
  agent janitor now refuses to delete anything under a scratch folder that
  another user owns or can write, which closes a symlink-swap attack on its
  cleanup.
- **`test_openai_deep_reasoning` passes.** It expected `medium` while the policy
  has always said `high` (`deep_reasoning` is `thorough`). The policy is the
  authority, so the test changed. CI now runs pytest on `claude/` and
  `scripts/`, so a failing test can no longer sit unseen.

### Changed
- **`claude/model-routing.yaml` → v2.4.0 (MINOR: a new provider profile and
  additive blocks; no `task_classes` row moved).** Five lessons from a private
  deployment's history, written without its calibration data:
  - **`effort_policy`:** effort is a default plus escalation. At most one
    escalation-only rung sits above the standing map, and an optional apex model
    (`escalation.apex_model`) is reached only after the frontier tier's whole
    ladder has failed.
  - **`orchestrator_lane`:** a second vendor's model for independent review,
    named once; the peer lane points at it.
  - **`providers.zai`:** an effort-steep example profile next to the other three.
  - **`model_prices` and `cost_accounting`:** cache writes are billed above the
    input rate, so cost estimates must count them.
  - **ADR 0001** (`docs/adr/0001-low-effort-non-inferiority.md`): how to test
    whether a cheaper default effort is "no worse", and why a small, tie-heavy
    task set cannot answer it.

### Changed
- **`harness/` refreshed from the private deployment (2026-10-08).** Seven
  weeks of work on the plan runner (`/plan-execute`), the plan builder, the
  routing skills and the agents. What a reader will notice:
  - **Plan isolation.** Each plan now runs on its own git branch, in its own
    worktree under `.plan-worktrees/`, so two plans can run in one repo
    without editing each other's files. The rules are in
    `skills/plan-execute/references/plan-isolation-contract.md`.
  - **A land stage.** When every session is done, `land` merges the plan
    branch back to the default branch, runs the gates again on the merged
    tree, and can repair a failure before it pushes. `finish` then checks
    that the whole plan record reached the default branch and reports CI's
    verdict (`references/finish-contract.md`).
  - **Gates are judged on their output.** A gate can declare an `expect`
    pattern, and it passes only when its output matches, not on exit code
    alone. A review gate passes only on a verdict with no blocking finding.
    A review scope that matches zero files fails instead of passing.
  - **Per-session review scope.** A session can declare `review_scope`, so
    its review reads only the paths it owns, even while another plan is
    live in the same repo.
  - **Gate timing.** Every gate run is logged with its duration, and
    `harness/skills/plan-execute/scripts/gate_durations.py` prints runs, median and longest time per
    gate and repo.
  - **New tier agents.** `tier-opus-low`, `tier-opus-xhigh`, `tier-opus-max`,
    `tier-sonnet-low` and `tier-sonnet-max` bind more model and effort
    pairs. `session-effort-worker` runs a session at the caller's own effort
    when no tier agent fits. A `general-purpose` agent replaces the built-in
    one so that it gets an effort of its own instead of copying the
    session's.

### Changed
- **`claude/model-routing.yaml` → v2.3.0 — the shipped EXAMPLE grid was
  deliberately DE-CALIBRATED (MINOR: example-profile shape only, no vocabulary
  or schema change).** The previous revision published a grid that read like a
  measured result: specific tier pins, truncated effort ladders, and a
  narrative explaining what a re-measurement had found. That is calibration
  data, and `claude/` is contractually the home of *illustrations*, not
  calibration. What shipped instead:
  - **The grid is now explicitly an illustration**, with a banner saying so and
    a statement that a real calibration would likely move at least two of its
    rows. `agentic_build` sits back on `workhorse`;
    `main_session.advisory_default_effort` sits back on `light`.
  - **Effort ladders are shown UNTRUNCATED**, running to the provider's exposed
    rungs. That is the naive starting point, not a recommendation — the comment
    now says so, and points at the procedure for finding your own saturation
    point.
  - **The methodology moved to `docs/METHODOLOGY.md` §2a/§2b** as two named,
    reusable effects with detection procedures — *ladder saturation* (your last
    useful effort rung is often below the one the vendor recommends) and *tier
    dominance* (a calmer pricier tier can win on both accuracy and cost per
    solved task, and this relation INVERTS across provider generations).
    Written as hypotheticals; no measured cell survives anywhere in the repo.
  - The lesson is unchanged and is the whole point: **do not inherit anyone's
    grid.** Run `/routing-update` and the §2 sweep on your own task mix.

### Added
- `claude/model-routing.yaml`, the routing policy SSOT: provider-neutral
  `task_classes:` (`{ tier, effort }`), a `providers:` block with fully
  researched `anthropic` / `openai` / `gemini` example profiles (models,
  effort maps, escalation/degrade ladders, prices — all dated
  verify-before-use examples), `escalation:` / `degradation:` / `codex_peer:`
  / `fanout_policy:` neutral policy blocks, an illustrative `agents:` snapshot,
  and a `consumers:` registry of files that bind to it.
- `claude/skills/routing-update/` — research-gated skill that regenerates
  provider profiles, bumps `version:`, and appends a CHANGELOG entry through
  one operator-approved changeset; hard-stops without a connected research
  tool rather than asserting model facts from memory.
- `claude/skills/routing-retro/` — read-only retrospective over recent
  sessions and `claude/evals/routing/MISROUTES.md` for over/under-modeling
  and cost-outlier signal.
- `docs/METHODOLOGY.md` — task classification, the retro → update loop, and
  the add-a-provider procedure.
- `claude/scripts/resolve_route.py` (S09) — the vendored, provider-neutral
  stateless resolver: `resolve(task_class, active_provider, current, signal)`
  is the one normative entry point (ARCHITECTURE.md §3); `escalate()`/
  `degrade()` are thin convenience wrappers over it. stdlib-only regex
  parsing of the SSOT (no PyYAML), matching the house rule already used by
  verify-routing.sh / render-routing-digest.py / retro_scan.py. Hardened via
  a dual-model adversarial review (2026-07-05, Claude + Codex) that found and
  fixed: a malformed effort map silently dropping the effort dial instead of
  raising, an escalation ladder that could step below the absolute floor on a
  misordered `model_ladder`, a missing `escalation:`/`degrade:` key silently
  treated the same as the explicit `none` opt-out, and an unrecognized tier
  name silently defaulting instead of raising in floor comparisons.
  `claude/fixtures/route-resolver/` carries its unit tests (resolve for each
  example provider, an escalation rung sequence, a degrade step, the
  no-declared-ladder path, and regressions for every review finding above).

### Changed
- ARCHITECTURE.md hardened via dual-model adversarial review (2026-07-05): added
  normative stateless resolver contract (typed caller signals, ladder walk order),
  required `calibration:` profile field with `unresearched` = hard error when active,
  split degrade `on:` taxonomy (refusal surfaces to operator, not auto-rerouted),
  tier-aliasing rule, per-class `min_tier` floors, guard-verified mirror requirement,
  and a deferred-open-questions section.
- `ARCHITECTURE.md` §3 Schema rules + `docs/METHODOLOGY.md` §3: named the
  dev-cost decision rule explicitly — the escalation trigger is
  evidence-gated (2 failures at the same root cause), never cost-blind;
  cheap-tier-first is a reason to try cheap and fail fast, never a reason to
  skip the escalation ladder once the evidence bar is met. Doc-only
  clarification of existing behavior, no schema/resolver change.
- `docs/INTEGRATION.md`: added "(e) Optional — a pre-origin correctness
  gate," documenting a third-party pre-commit/pre-origin tool
  ([no-mistakes](https://github.com/kunchenguid/no-mistakes)) as an
  optional, never-installed-by-`install.sh` companion, same graceful-no-op
  framing as the existing codex-peer-lane entry.
- `harness/`: added the manifest-driven export tree (skills, commands,
  hooks, scripts, rules, agents, docs) synced from the private `~/.claude`
  deployment via `scripts/sync-from-claude.py`, plus `docs/HARNESS.md`
  documenting the two-tier private→public workflow, the scrub/scan
  pipeline, and how to add a new manifest entry.

### Changed
- `claude/model-routing.yaml` **2.0.0 → 2.1.0** (MINOR — escalation ladder
  re-bound + fan-out policy additions, within the existing schema; 2026-07-10):
  - `escalation.ladder` gains a **`consult_advisor`** rung as step 2 (between
    `raise_effort_one_rung` and `raise_tier_one_rung`) — a single-dispatch
    frontier_reasoner+light advisory agent reads a stuck worker's state and hands
    back a plan while the worker continues at its own tier, a cheaper middle rung
    than raising the tier. Per-executor-tier nudge calibration added under
    `providers.anthropic.escalation.advisor_nudge` (Anthropic-measured: +7pp on
    cheap_fast at ~turn 2, no effect on workhorse, negative on frontier_reasoner).
    Evidence: Anthropic advisor-tool doc, beta `advisor-tool-2026-03-01`.
  - `fanout_policy` gains three advisory keys: `shard_floor_cost` (over-splitting
    adds coordination overhead that eats tier savings — CMA plan_big_execute_small
    cookbook, principle 2), `quarantine_least_privilege` (untrusted-content readers
    get read-only toolsets, never high-privilege actions — cookbook principle 4),
    and `workflow_agent_model_inheritance` (an omitted per-dispatch tier silently
    inherits the frontier main-session tier — the mechanism behind a real ~30-agent
    fan-out cost incident).
  - Digest escalate bullet re-rendered to include the advisor rung
    (`raise effort → advisor → tier → second-model peer`). `task_classes`,
    provider maps, prices, and agent pins unchanged.

## [1.0.0] - 2026-07-05

### Added
- Initial architecture: capability-tier vocabulary (`cheap_fast` / `workhorse` /
  `frontier_reasoner`), abstract effort *intent* labels (`light` / `standard` /
  `thorough`), provider-profile schema with per-provider effort maps and
  escalation/degrade ladders (`ARCHITECTURE.md`).
- Versioning scheme and this changelog (`docs/VERSIONING.md`).
- Genericization/scrub rules for porting from the source deployment
  (`GENERICIZATION.md`).
- Repo skeleton: `claude/{skills,scripts,evals/routing,fixtures/routing-guard}`,
  `docs/`.
