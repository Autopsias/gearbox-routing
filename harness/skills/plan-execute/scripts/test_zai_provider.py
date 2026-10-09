"""providers.zai — the GLM substrate lane (v1.21).

Covers the three zai mechanisms (lane logic in provider_lane.py, wiring in run.py):
  * provider RESOLUTION — PLAN_EXECUTE_ROUTING_PROVIDER env, then CLAUDE_CONFIG_DIR
    basename detection, else the SSOT dial (the Claude mainline never sets either
    signal and must keep resolving `anthropic` byte-identically).
  * the CELL CLAMP — provider_lane.clamp_cell translates a manifest (model, reasoning)
    pair to the zai Task-plane cell: low preserved, everything else on a pinned
    model clamps to max, fable/haiku collapse onto opus/sonnet.
  * lane + tier + fallback wiring — a zai run dispatches on the CLAUDE lane
    (`backend: "claude"`), resolves zai tier agents, and degrades KEEPING max.

Run: pytest plan-execute/scripts/test_zai_provider.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run  # noqa: E402
import provider_lane as pl  # noqa: E402
from test_shipping import make_plan  # noqa: E402  (reuse the plan fixture builder)


# ---- provider resolution ----------------------------------------------------
def test_provider_env_override_wins(monkeypatch):
    monkeypatch.setenv(pl.PROVIDER_ENV, "zai")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    provider, _ = run._load_routing()
    assert provider == "zai"


def test_provider_glm_tree_detected_from_claude_config_dir(monkeypatch):
    # No explicit override: a CLAUDE_CONFIG_DIR ending in .claude-glm IS the z.ai
    # substrate, whatever the SSOT copy's dial says.
    monkeypatch.delenv(pl.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude-glm"))
    provider, _ = run._load_routing()
    assert provider == "zai"


def test_provider_mainline_stays_ssot_dial(monkeypatch):
    # The Claude mainline sets neither signal — provider comes from the SSOT dial
    # untouched (the committed SSOT says anthropic).
    monkeypatch.delenv(pl.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    provider, text = run._load_routing()
    assert provider == "anthropic"
    assert text  # SSOT still loads; only Codex-declared sessions hard-require it


def test_dispatch_lane_zai_rides_claude():
    session = {"id": "s01", "model": "Sonnet"}
    assert run._dispatch_lane(session, "zai", None, harness="claude") == "claude"


def test_escalation_provider_resolves_per_lane(monkeypatch):
    monkeypatch.setenv(pl.PROVIDER_ENV, "zai")
    assert pl.escalation_provider("claude") == "zai"
    assert pl.escalation_provider("codex") == "openai"
    monkeypatch.delenv(pl.PROVIDER_ENV, raising=False)
    # hermetic: this test may RUN under the GLM tree, where CLAUDE_CONFIG_DIR is
    # ambient — pin the mainline explicitly instead of inheriting the session env
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    assert pl.escalation_provider("claude") == "anthropic"


# ---- the cell clamp ----------------------------------------------------------
@pytest.mark.parametrize(
    "model,reasoning,expect",
    [
        # low is preserved — the one sub-max rung, mechanical only
        ("sonnet", "low", ("sonnet", "low")),
        ("opus", "low", ("opus", "low")),
        # everything else on a pinned model — including UNSET — clamps to max
        ("sonnet", "medium", ("sonnet", "max")),
        ("sonnet", "high", ("sonnet", "max")),
        ("sonnet", "", ("sonnet", "max")),
        ("opus", "medium", ("opus", "max")),
        ("opus", "xhigh", ("opus", "max")),
        ("opus", "max", ("opus", "max")),
        # fable collapses onto opus (no zai apex; a bare fable alias under the GLM
        # env resolves to the SONNET slot — the silent downgrade this prevents)
        ("fable", "xhigh", ("opus", "max")),
        # haiku collapses onto sonnet at its own tier (the zai workhorse token IS
        # flash under the env remap; tier-haiku's no-dial invariant never meets
        # a dial-capable model)
        ("haiku", "low", ("sonnet", "low")),
        ("haiku", "", ("sonnet", "max")),
        # no model pinned: nothing to clamp — the subagent inherits
        (None, "high", (None, "high")),
    ],
)
def test_zai_cell(model, reasoning, expect):
    assert pl.clamp_cell(model, reasoning) == expect


# ---- tier resolution is provider-SCOPED --------------------------------------
@pytest.fixture
def agent_dir(tmp_path):
    """A minimal agents dir holding the definitions these tests resolve (existence
    is all `_tier_agent` checks — same pattern as test_tier_agent.py's `_agent_dir`;
    the DEPLOYED ~/.claude/agents lags the repo until gearbox deploy runs)."""
    d = tmp_path / "agents"
    d.mkdir()
    for name in ("tier-sonnet-low", "tier-sonnet-max", "tier-opus-max",
                 "tier-sonnet-medium", "tier-fable-xhigh"):
        (d / f"{name}.md").write_text("# fixture tier agent\n")
    return d


def test_tier_agent_zai_table(agent_dir):
    assert run._tier_agent("sonnet", "max", agent_dir=agent_dir, provider="zai") == "tier-sonnet-max"
    assert run._tier_agent("opus", "max", agent_dir=agent_dir, provider="zai") == "tier-opus-max"
    assert run._tier_agent("sonnet", "low", agent_dir=agent_dir, provider="zai") == "tier-sonnet-low"


def test_tier_agent_max_stays_unmapped_on_anthropic():
    # "never opus+max" is an ANTHROPIC-lane rule: the same cells that bind on zai
    # must stay deliberately unmapped on the mainline.
    assert run._tier_agent("opus", "max", provider="anthropic") is None
    assert run._tier_agent("sonnet", "max", provider="anthropic") is None


def test_fallback_zai_keeps_max():
    # Effort-steep inversion of the anthropic high-landing: degrade KEEPS max.
    assert run._fallback_for("opus", "zai") == ("sonnet", "max")
    assert run._fallback_for("sonnet", "zai") is None  # workhorse is the floor
    assert run._fallback_for("fable", "zai") is None   # no zai apex — the clamp
    #                                                    remaps fable before lookup


# ---- end to end: begin under the zai provider --------------------------------
def _member_for(tmp_path, capsys, monkeypatch, session, zai):
    monkeypatch.setenv(pl.PROVIDER_ENV, "zai" if zai else "anthropic")
    plan_dir = make_plan(tmp_path, [session])
    run.cmd_begin(plan_dir, [session["id"]])
    out = json.loads(capsys.readouterr().out)
    run.cmd_release(plan_dir)
    capsys.readouterr()  # drain the release receipt so the next call reads clean
    return {m["id"]: m for m in out["batch"]}[session["id"]]


def test_begin_under_zai_clamps_cells_and_binds_zai_tiers(tmp_path, capsys, monkeypatch, agent_dir):
    sessions = [
        {"id": "s01", "title": "normal", "items": ["i1"], "model": "Sonnet", "reasoning": "medium"},
        {"id": "s02", "title": "hard", "items": ["i1"], "model": "Opus", "reasoning": "high"},
        {"id": "s03", "title": "mech", "items": ["i1"], "model": "Haiku"},
        {"id": "s04", "title": "apex pin", "items": ["i1"], "model": "Fable", "reasoning": "xhigh"},
    ]
    monkeypatch.setenv(pl.PROVIDER_ENV, "zai")
    monkeypatch.setattr(run, "_TIER_AGENT_DIR", agent_dir)
    plan_dir = make_plan(tmp_path, sessions)
    run.cmd_begin(plan_dir, [s["id"] for s in sessions])
    out = json.loads(capsys.readouterr().out)
    assert out["active_provider"] == "zai"
    by_id = {m["id"]: m for m in out["batch"]}
    run.cmd_release(plan_dir)

    # normal work: Sonnet@medium -> flash@max, bound by the zai tier agent
    assert by_id["s01"]["backend"] == "claude"
    assert by_id["s01"]["model"] == "Sonnet"          # raw manifest pair stays visible
    assert by_id["s01"]["model_arg"] == "sonnet"
    assert by_id["s01"]["reasoning"] == "max"
    assert by_id["s01"]["subagent_type"] == "tier-sonnet-max"
    assert by_id["s01"]["effort_enforced"] is True

    # hard work: Opus@high -> glm-5.3@max, degrade keeps max
    assert by_id["s02"]["subagent_type"] == "tier-opus-max"
    assert by_id["s02"]["fallback_model"] == "sonnet"
    assert by_id["s02"]["fallback_reasoning"] == "max"

    # mechanical: Haiku (no reasoning) -> sonnet@max (unset clamps to max)
    assert by_id["s03"]["model_arg"] == "sonnet"
    assert by_id["s03"]["reasoning"] == "max"
    assert by_id["s03"]["subagent_type"] == "tier-sonnet-max"

    # a fable pin collapses onto opus@max — never the GLM env's silent flash slot
    assert by_id["s04"]["model_arg"] == "opus"
    assert by_id["s04"]["reasoning"] == "max"
    assert by_id["s04"]["subagent_type"] == "tier-opus-max"


def test_begin_without_zai_env_is_byte_identical_mainline(tmp_path, capsys, monkeypatch, agent_dir):
    # The same manifest under the Claude mainline resolves the ANTHROPIC cells —
    # the provider addition changed nothing for the mainline. CLAUDE_CONFIG_DIR is
    # pinned because this test may run under the GLM tree, where it is ambient.
    sessions = [
        {"id": "s01", "title": "normal", "items": ["i1"], "model": "Sonnet", "reasoning": "medium"},
        {"id": "s02", "title": "apex pin", "items": ["i1"], "model": "Fable", "reasoning": "xhigh"},
    ]
    monkeypatch.setenv(pl.PROVIDER_ENV, "anthropic")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    monkeypatch.setattr(run, "_TIER_AGENT_DIR", agent_dir)
    by_id = _member_for(tmp_path, capsys, monkeypatch, sessions[0], zai=False)
    assert by_id["model_arg"] == "sonnet"
    assert by_id["reasoning"] == "medium"
    assert by_id["subagent_type"] == "tier-sonnet-medium"
    assert by_id["fallback_model"] is None  # sonnet is the anthropic floor

    (tmp_path / "again").mkdir()
    by_id = _member_for(tmp_path / "again", capsys, monkeypatch, sessions[1], zai=False)
    assert by_id["model_arg"] == "fable"
    assert by_id["subagent_type"] == "tier-fable-xhigh"
