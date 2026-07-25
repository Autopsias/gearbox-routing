# Changelog

All notable changes to the Gearbox routing policy and framework. Format follows
[Keep a Changelog](https://keepachangelog.com); versioning rules are in
`docs/VERSIONING.md`.

## [Unreleased]

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
