"""Shared pytest fixtures for the plan-execute scripts suite."""

from pathlib import Path

import pytest

import outcomes as outc
import provider_lane  # noqa: E402
import egress
import run


@pytest.fixture(autouse=True)
def checkout_tier_agents(monkeypatch):
    """Tier-agent definitions come from THIS checkout's `agents/`, never the
    deployed `~/.claude/agents`. `run._TIER_AGENT_DIR` is bound to the deploy
    target at import, so under CI's empty HOME every tier lookup missed and
    dispatch degraded to `prompt_directive_advisory` — red on main from 11d8055f
    while green on the dev box. A test that needs a missing or custom agent dir
    still monkeypatches it itself, after this runs."""
    monkeypatch.setattr(run, "_TIER_AGENT_DIR", Path(__file__).resolve().parents[3] / "agents")


@pytest.fixture(autouse=True)
def isolated_outcome_ledger(tmp_path, monkeypatch):
    """EVERY test in this suite writes outcome-ledger records to an ISOLATED
    path, never the real tracked `evals/routing/outcomes.ndjson` — any test
    that exercises `apply`/`verify_finalize`/`retire_session` now writes a
    ledger record as a side effect, and without this the whole suite quietly
    contaminates the seeded source-tree file with throwaway fixture data
    (measured: a full-suite run left 23 fixture records in the real file)."""
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(tmp_path / "outcomes-test.ndjson"))


@pytest.fixture(autouse=True)
def egress_root(tmp_path, monkeypatch):
    """Every test gets a CLEAN working tree for the data_sensitivity_guard scan —
    never the real cwd. Two reasons, both load-bearing: the guard now shells out
    to `gitleaks` over the whole tree (1.8 s on this repo vs 54 ms on an empty
    dir), and a developer whose cwd legitimately holds a `.env` would otherwise
    watch unrelated dispatch tests refuse."""
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setenv(egress._EGRESS_ROOT_ENV, str(root))
    return root


@pytest.fixture(autouse=True)
def _skip_browser_checks(monkeypatch):
    """Unit tests do NOT launch a browser or npx.

    MEASURED by profiling one test: of 18.2 s wall clock, 17.9 s was
    subprocess — headless Chrome 6.6 s (render_verify) plus three `npx eslint`
    spawns totalling 5.2 s (structural_gate.js_check). The suite calls that path
    once per verify/apply test, which is most of its 16 min 33 s.

    ACCURACY, corrected 2026-08-14 after a reviewer caught the first wording:
    this does NOT leave the whole primary gate alone. structural_gate documents
    TWO primary sub-checks and this flag skips one of them (check_js_parses,
    which is browser-FREE — it shells out to node/eslint, so the flag's name is
    about SUBPROCESS COST, not about browsers). check_landed, the sub-check that
    actually verifies data-status attributes landed on disk, is untouched and
    still runs on every test.

    Skipping check_js_parses is nonetheless safe rather than a weakening, for a
    reason in the code and not in this comment: run_gate computes
    `ok = landed_ok and not js_failed`, so a "skipped" result is NOT a failure
    and was never folded into a pass either — unavailable tooling has always
    taken this exact path (a machine without node behaves identically). The real
    paths keep their own coverage in test_render_verify_optout.py, which unsets
    the flag and asserts they actually fire.
    """
    monkeypatch.setenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", "1")


# Lifted out of codex_helpers.py when test_codex_dispatch.py was split: five
# suites take `ssot` as an argument, and an IMPORTED fixture is shadowed by the
# test's own parameter of the same name (ruff F811). conftest is where pytest
# resolves it without anyone importing anything.
from codex_helpers import SSOT_TEMPLATE  # noqa: E402


@pytest.fixture
def ssot(tmp_path, monkeypatch):
    def _write(provider="anthropic", openai_status="researched",
               executor_for="standard_build, agentic_build, deep_reasoning",
               opt_ins="      []", allowlist="      []"):
        p = tmp_path / "model-routing.yaml"
        p.write_text(SSOT_TEMPLATE.format(
            provider=provider, openai_status=openai_status,
            executor_for=executor_for, opt_ins=opt_ins, allowlist=allowlist,
        ))
        monkeypatch.setenv(run._SSOT_ENV, str(p))
        return p

    return _write


@pytest.fixture(autouse=True)
def _no_ambient_plan_execute_env(monkeypatch):
    """Scrub PLAN_EXECUTE_* from the environment for every test.

    This suite runs INSIDE plan-execute sessions -- the repo's own plans verify
    changes to plan-execute through code-review-gate, which runs this suite. On
    2026-08-20 verify handed s13's review base and scope to that gate, pytest
    inherited them, and every test calling llm_review_gate.main() without --base
    read the ambient PLAN_EXECUTE_REVIEW_BASE and diffed a commit its temp repo
    did not have: INDETERMINATE. A test must see only what it sets.
    """
    # ONLY the keys verify injects for a review gate. The suite uses PLAN_EXECUTE_*
    # as its own config namespace (SKIP_BROWSER_CHECKS, EGRESS_ROOT, OUTCOME_LEDGER);
    # a whole-prefix scrub fought those fixtures and proved nothing.
    for k in ("PLAN_EXECUTE_REVIEW_BASE", "PLAN_EXECUTE_REVIEW_SCOPE",
              "PLAN_EXECUTE_PLAN_DIR", "PLAN_EXECUTE_SESSION", "PLAN_EXECUTE_INTENT_FILE"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_provider_env(monkeypatch):
    """Scrub the PROVIDER-RESOLUTION signals for every test (v1.21, zai).

    provider_lane resolves the provider from
    PLAN_EXECUTE_ROUTING_PROVIDER, then CLAUDE_CONFIG_DIR (a `.claude-glm`
    basename means the z.ai substrate). Both are AMBIENT in a GLM-tree session
    — the tree's settings.json exports them into every child, pytest included —
    and under them the suite's mainline-behavior tests resolved through the zai
    profile and failed 12 at once (measured, this suite run from
    inside a GLM session). A test must see only what it sets:
    test_zai_provider.py sets these explicitly per test and is unaffected.
    """
    monkeypatch.delenv(provider_lane.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def _no_live_build_canary(monkeypatch):
    """plan-builder's build_canary tags a NEW plan's build sessions from the LIVE outcome
    ledger, so a fixture plan built here would change shape whenever a canary is armed.
    test_route_at_dispatch_e2e.py passes this key into its own whitelisted env."""
    monkeypatch.setenv("PLAN_BUILDER_BUILD_CANARY", "off")


# The public export ships an example routing policy (its file starts "# HARNESS COPY")
# in place of the source repo's. These tests pin the source policy's models, ladders
# and rows, or replay plans pinned to it, so they skip against the example copy.
_PINS_SOURCE_POLICY = {
    ("test_codex_effort_climb.py", "test_no_derivable_codex_dispatch_ever_names_a_retired_model"),
    ("test_dispatch_construction.py", "test_ssot_actually_defines_fanout_policy_default_model"),
    ("test_escalation.py", "test_a_declared_non_tier_agent_declines_the_climb_out_loud"),
    ("test_escalation.py", "test_a_refused_rung_does_not_cap_the_rest_of_the_ladder"),
    ("test_escalation.py", "test_a_rung_key_that_names_no_rung_is_REFUSED_not_silently_recorded"),
    ("test_escalation.py", "test_an_escalated_member_is_told_to_refuse_not_to_degrade"),
    ("test_escalation.py", "test_every_anthropic_start_cell_climbs_to_the_apex_then_exhausts"),
    ("test_escalation.py", "test_frozen_v5_claude_lane_dispatch_is_byte_identical"),
    ("test_escalation.py", "test_frozen_v5_gpt55_pin_still_blocks_with_guidance"),
    ("test_escalation.py", "test_no_existing_spec_is_NEWLY_refused_by_the_new_builder"),
    ("test_escalation.py", "test_openai_climb_ends_at_sol_max_and_never_crosses_providers"),
    ("test_escalation.py", "test_refused_rung_falls_back_to_the_previous_rung_and_is_never_re_proposed"),
    ("test_escalation.py", "test_same_inputs_yield_the_same_rung_however_often_begin_runs"),
    ("test_escalation.py", "test_the_documented_refusal_recovery_actually_walks"),
    ("test_escalation.py", "test_the_floor_holds_on_the_composite_escalate_then_refuse_path"),
    ("test_escalation.py", "test_v5_manifest_never_escalates_but_v6_does"),
    ("test_escalation_integration.py", "test_a_SECOND_root_cause_climbs_from_the_rung_reached_not_from_the_floor"),
    ("test_escalation_integration.py", "test_a_codex_harness_session_is_announced_its_OWN_rung_not_a_claude_one"),
    ("test_escalation_integration.py", "test_a_different_error_holds_the_rung_instead_of_handing_the_model_back"),
    ("test_escalation_integration.py", "test_allow_an_attributable_failure_still_buys_its_rung"),
    ("test_escalation_integration.py", "test_an_all_refused_halt_says_so_instead_of_falling_silent"),
    ("test_escalation_integration.py", "test_exhausting_the_budget_mid_ladder_names_the_untried_rungs"),
    ("test_escalation_integration.py", "test_rework_loop_climbs_after_the_stuck_protocol_arms"),
    ("test_escalation_integration.py", "test_the_apex_halt_does_not_blame_a_refusal_that_never_happened"),
    ("test_escalation_integration.py", "test_the_climb_walks_the_lanes_provider_not_the_run_level_dial"),
    ("test_escalation_integration.py", "test_the_halt_names_the_rung_that_ran_even_when_the_last_error_differs"),
    ("test_escalation_integration.py", "test_the_ledger_records_the_mechanism_that_ACTUALLY_bound_the_effort"),
    ("test_outcomes.py", "test_routing_provenance_resolves_the_class_default"),
    ("test_plan_limits.py", "test_the_ceiling_is_the_longest_ladder_the_ssot_actually_defines"),
    ("test_route_at_dispatch.py", "test_a_plan_or_routing_change_between_prepare_and_the_lock_refuses_the_begin"),
    ("test_route_at_dispatch.py", "test_a_routing_file_change_between_attempts_does_not_move_the_frozen_cell"),
    ("test_route_at_dispatch.py", "test_a_v7_manifest_dispatches_byte_identically"),
    ("test_route_at_dispatch.py", "test_the_codex_paths_dispatch_the_frozen_cell_not_the_live_file"),
    ("test_route_at_dispatch_e2e.py", "test_a_routing_file_edit_between_two_begins_leaves_the_frozen_cell_unchanged"),
}
_ROUTING = Path(__file__).resolve().parents[3] / "model-routing.yaml"


def pytest_collection_modifyitems(config, items):
    if not _ROUTING.read_text(encoding="utf-8").startswith("# HARNESS COPY"):
        return
    skip = pytest.mark.skip(reason="pins the source repo's routing policy; this copy ships an example one")
    for item in items:
        if (item.path.name, item.originalname) in _PINS_SOURCE_POLICY:
            item.add_marker(skip)
