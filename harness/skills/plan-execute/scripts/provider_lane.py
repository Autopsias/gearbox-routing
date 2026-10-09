"""provider_lane.py — routing-source loading + provider resolution + the zai
(GLM) substrate lane: cell clamp, tier table, lane-profile choice (v1.21).

Extracted from run.py at the size ratchet's demand, and better for it: the
routing-loader trio (SSOT_ENV / routing_ssot_path / load_routing) and every
piece of provider-lane logic live here, while run.py keeps monkeypatch-shaped
thin wrappers (its tests replace `run._load_routing` by module attribute) and
the code-authoritative ladder dicts the drift guard ast-parses in place
(_FALLBACK_LADDER / _DEGRADE_EFFORT — check (d) reads run.py, never this file).

WHAT "ZAI" IS: the ~/.claude-glm tree runs the same Claude Code binary against
z.ai's Anthropic-compatible endpoint, with ANTHROPIC_DEFAULT_*_MODEL remapping
the Task-plane tokens (opus→glm-5.3, sonnet→glm-5.3-flash). The profile's
`models:` in model-routing.yaml are therefore the TOKENS, not glm-* wire ids —
the Task tool only accepts tokens (a literal "glm-5.3-max" id is rejected
client-side — unrecognized_model — probed), and the wire mapping
stays in the GLM tree's env where it already lived.

EVIDENCE (SSOT providers.zai.calibration + the v1.21 decision history): DeepSWE
v1.1 glm-5.3[max] 69%±3/$3.99 · glm-5.3-flash[max] 63%±4/$0.24 · glm-5.2[max]
44%±2 (the rough zone); z.ai's docs recommend reasoning_effort max for coding,
for flash explicitly; live probes: the Claude effort dial REACHES z.ai (thinking
chars on one prompt: low 49 / max 642 / xhigh 1450), and a bare fable alias
resolves to the SONNET slot — the silent downgrade the clamp below prevents.

GLM is EFFORT-STEEP (the luna/terra pattern): every serious zai rung is max;
`low` is the one preserved sub-max rung, for mechanical work.
"""

import os
import re
from pathlib import Path

SSOT_ENV = "PLAN_EXECUTE_ROUTING_SSOT"  # test override; default = <tree>/model-routing.yaml
PROVIDER_ENV = "PLAN_EXECUTE_ROUTING_PROVIDER"  # explicit override; GLM settings.json sets "zai"


def routing_ssot_path():
    """The SSOT this tree's run.py resolves: env override, else the file beside
    the skill tree (parents[3] of this module == the config root, same as run.py)."""
    override = os.environ.get(SSOT_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "model-routing.yaml"


def load_routing():
    """(provider, ssot_text) — best-effort; (resolved-or-None, None) when the
    SSOT is unreadable (the anthropic default path must keep working without
    it; only a Codex-declared session hard-fails on it). Provider resolution
    order: PROVIDER_ENV wins, else CLAUDE_CONFIG_DIR basename `.claude-glm`
    selects zai, else the SSOT dial. See effective_provider."""
    try:
        text = routing_ssot_path().read_text(encoding="utf-8")
    except OSError:
        text = None
    m = re.search(r"^active_provider:\s*([\w-]+)", text, re.M) if text else None
    return effective_provider(m.group(1) if m else None), text


def effective_provider(ssot_provider, env=None):
    """Resolution order: PROVIDER_ENV env wins; else a CLAUDE_CONFIG_DIR basename
    of `.claude-glm` selects zai (that tree IS the z.ai substrate, whatever its
    SSOT copy's dial says); else the SSOT dial, unchanged. The Claude mainline
    sets neither signal and keeps resolving exactly as before."""
    env = os.environ if env is None else env
    override = (env.get(PROVIDER_ENV) or "").strip()
    if override:
        return override
    cfg = (env.get("CLAUDE_CONFIG_DIR") or "").rstrip("/")
    if cfg.endswith(".claude-glm"):
        return "zai"
    return ssot_provider


# The zai tier table — SEPARATE from run.py's anthropic _TIER_AGENTS, never
# merged: on the anthropic lane every `max` cell stays deliberately unmapped
# ("never opus+max" is an anthropic-lane rule — opus-5's max is measured-WEAK
# there), and global rows would change what an authored opus@max manifest binds
# on the mainline. On the zai lane GLM is effort-steep, so max IS the standing
# serious rung and these three agents are what bind it.
TIER_AGENTS = {
    ("sonnet", "low"): "tier-sonnet-low",   # zai mechanical — flash@low
    ("sonnet", "max"): "tier-sonnet-max",   # zai workhorse serious — flash@max
    ("opus", "max"): "tier-opus-max",       # zai frontier ceiling — glm-5.3@max
}


# The agent the UNBOUND path dispatches (both lanes). Since SSOT v26
# agents/general-purpose.md replaces the built-in and pins effort medium, so an
# untyped dispatch no longer inherits the session effort. A (model, effort) cell
# with no tier agent must keep inheriting it — that is what `begin` announces —
# so it names this agent, which carries NO `effort:` key. Deliberately not a
# `tier-` name: outcomes.py and the ESC-02 climb read that prefix as "bound".
INHERIT_AGENT = "session-effort-worker"


def inherit_agent(agent_dir):
    """INHERIT_AGENT if its definition exists on disk, else None (the built-in
    general-purpose) — same existence rule as run._tier_agent: a missing file
    would make Task fail the dispatch outright."""
    return INHERIT_AGENT if (agent_dir / f"{INHERIT_AGENT}.md").is_file() else None


def clamp_cell(model_arg, reason_tier):
    """Manifest (model, reasoning) cell -> the zai Task-plane cell. `low` is
    preserved (true mechanical); every other tier on a pinned model — including
    UNSET — clamps to max; fable collapses to opus (no zai apex above glm-5.3);
    haiku collapses to sonnet at its own tier (the zai workhorse token IS flash
    under the env remap; tier-haiku has no zai counterpart)."""
    if model_arg == "fable":
        model_arg = "opus"
    elif model_arg == "haiku":
        model_arg = "sonnet"
    if model_arg and reason_tier != "low":
        reason_tier = "max"
    return model_arg, reason_tier


def lane_profile(lane, provider):
    """The SSOT profile a lane's climb walks. The codex lane is the OpenAI
    family. The claude lane walks anthropic UNLESS the substrate is zai: a
    session that FAILS CLOSED to the claude lane under an openai DIAL still
    runs Claude tokens, so walking providers.openai would make the resolver
    raise on a `sonnet` cell and silently kill the climb — the measured
    2026-08-14 bug test_escalation_integration 5b pins."""
    if lane == "codex":
        return "openai"
    return "zai" if provider == "zai" else "anthropic"


def escalation_provider(lane):
    """lane_profile with the provider loaded here (run.py keeps a wrapper so its
    tests can monkeypatch run._escalation_provider by module attribute)."""
    return lane_profile(lane, load_routing()[0])
