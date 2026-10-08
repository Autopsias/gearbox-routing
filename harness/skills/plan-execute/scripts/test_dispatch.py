"""next_action blocked-scoping (regression for the item-level BLOCKED wedge).

`statuses` (read from PLAN.html article blocks) carries BOTH item and session
statuses. Before this fix next_action's stop-the-world check iterated over the
whole dict, so a single BLOCKED *item* inside a PARTIAL session wedged the loop
forever (and the item id was surfaced under "sessions"), contradicting the
documented PARTIAL semantics (loop re-dispatches the session).

Run: pytest plan-execute/scripts/test_dispatch.py -q
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import dispatch as dsp  # noqa: E402
import codex_command as cc  # noqa: E402
import run  # noqa: E402
from test_shipping import make_plan  # noqa: E402

# HARNESS-01 — the three Fable 5.1 guide lines (plan_scope.CLAUDE_DISPATCH_GUIDANCE)
# that must reach every Claude-harness dispatch prompt, and none of Codex's.
_SCOPE_TESTS_LINE = "no extra features and no speculative refactors"
_TARGETED_EDITS_LINE = "Prefer small, targeted edits to the existing code over rewriting a file"
_OUTPUT_STYLE_LINE = "~/.claude/output-styles/<your-style>.md"


def _dispatch_one(tmp_path, session):
    """Build a real one-session plan and return its dispatch member (via cmd_begin's
    JSON batch output) — the actual funnel the orchestrator dispatches through."""
    import io
    from contextlib import redirect_stdout

    plan_dir = make_plan(tmp_path, [session])
    buf = io.StringIO()
    with redirect_stdout(buf):
        run.cmd_begin(plan_dir, [session["id"]])
    out = json.loads(buf.getvalue())
    return {m["id"]: m for m in out["batch"]}[session["id"]]


def _manifest():
    return {
        "plan_schema_version": 2,
        "items": [{"id": "IT-1"}, {"id": "IT-2"}, {"id": "IT-3"}],
        "sessions": [
            {"id": "s01", "items": ["IT-1", "IT-2"], "dispatch": {}},
            {"id": "s02", "items": ["IT-3"], "dispatch": {"depends_on": ["s01"]}},
        ],
    }


def test_blocked_item_does_not_wedge_partial_session():
    # One item blocked, session PARTIAL -> loop must re-dispatch the session,
    # not report blocked; the blocked item stays visible in blocked_items.
    statuses = {"s01": "PARTIAL", "IT-1": "BLOCKED", "IT-2": "DONE", "s02": "TODO"}
    action = dsp.next_action(_manifest(), statuses)
    assert action["action"] == "dispatch"
    assert [m["id"] for m in action["batch"]] == ["s01"]
    assert action["blocked_items"] == ["IT-1"]


def test_doing_session_with_verify_block_reports_verify_pending():
    m = _manifest()
    m["sessions"][0]["verify"] = {"require_evidence": True}
    action = dsp.next_action(m, {"s01": "DOING", "s02": "TODO"})
    assert action["action"] == "verify-pending"
    assert action["sessions"] == ["s01"]
    assert "verify-finalize" in action["message"]


def test_blocked_session_still_stops_the_world():
    statuses = {"s01": "BLOCKED", "IT-1": "BLOCKED", "IT-2": "DONE", "s02": "TODO"}
    action = dsp.next_action(_manifest(), statuses)
    assert action["action"] == "blocked"
    assert action["sessions"] == ["s01"]
    assert action["blocked_items"] == ["IT-1"]


def test_item_awaits_review_never_reads_as_session_checkpoint():
    # AWAITS_REVIEW is a session-only status; a stray item value must not
    # produce a checkpoint action.
    statuses = {"s01": "DONE", "IT-1": "AWAITS_REVIEW", "IT-2": "DONE", "s02": "DONE"}
    action = dsp.next_action(_manifest(), statuses)
    assert action["action"] == "complete"


def test_no_blocked_items_key_when_none_blocked():
    statuses = {"s01": "TODO", "s02": "TODO"}
    action = dsp.next_action(_manifest(), statuses)
    assert action["action"] == "dispatch"
    assert "blocked_items" not in action


def test_checkpoint_action_carries_decision_brief():
    # The operator must see WHY the gate exists and WHAT they are deciding —
    # the checkpoint action carries the author's brief from the manifest.
    brief = {"reason": "Deploy is irreversible.", "decision": "Ship v2 now or hold?"}
    m = _manifest()
    m["sessions"][1]["dispatch"]["requires_human_checkpoint"] = True
    m["sessions"][1]["dispatch"]["checkpoint"] = brief
    action = dsp.next_action(m, {"s01": "DONE", "IT-1": "DONE", "IT-2": "DONE", "s02": "TODO"})
    assert action["action"] == "checkpoint"
    assert action["checkpoint_session"] == "s02"
    assert action["checkpoint"] == brief


def test_checkpoint_action_brief_none_on_legacy_manifest():
    m = _manifest()
    m["sessions"][1]["dispatch"]["requires_human_checkpoint"] = True
    action = dsp.next_action(m, {"s01": "DONE", "IT-1": "DONE", "IT-2": "DONE", "s02": "TODO"})
    assert action["action"] == "checkpoint"
    assert action["checkpoint"] is None


# --------------------------------------------------------------------------
# HARNESS-01 — Fable 5.1 guide lines reach every Claude dispatch, never Codex
# --------------------------------------------------------------------------
def test_tier_agent_dispatch_carries_the_fable51_guidance(tmp_path, monkeypatch):
    """A session with a TIER AGENT (sonnet/medium has one on disk) never reaches
    the `prompt_directive_advisory` fallback — this proves the guidance rides the
    universal `ps.dispatch_preamble` funnel, not that fallback branch.

    The agent definitions are read from `~/.claude/agents`, the deploy target. A
    clean CI checkout has none, so the dispatch degraded to the advisory branch
    and the test asserted against the wrong path. Point it at THIS repo's
    `agents/`, which is the source those definitions deploy from.
    """
    monkeypatch.setattr(run, "_TIER_AGENT_DIR", SCRIPTS.parents[2] / "agents")
    sess = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
            "reasoning": "medium", "task_class": "agentic_build", "prompt": "do the thing"}
    member = _dispatch_one(tmp_path, sess)
    assert member["effort_mechanism"] == "tier_agent"  # sanity: really the tier path
    for line in (_SCOPE_TESTS_LINE, _TARGETED_EDITS_LINE, _OUTPUT_STYLE_LINE):
        assert line in member["prompt_text"], f"missing on tier-agent dispatch: {line!r}"


def test_unbound_cell_names_the_agent_with_no_effort_key(tmp_path, monkeypatch):
    """SSOT v26: agents/general-purpose.md pins effort medium, so an UNTYPED dispatch
    no longer inherits the session effort. A cell with no tier agent (opus/max) must
    keep inheriting it — `begin` says so — so it names the one agent that carries no
    `effort:` key. With that file missing it degrades to untyped, never to a name
    Task would refuse.
    """
    agents = SCRIPTS.parents[2] / "agents"
    sess = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Opus",
            "reasoning": "max", "task_class": "linchpin", "prompt": "do the thing"}
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    monkeypatch.setattr(run, "_TIER_AGENT_DIR", agents)
    member = _dispatch_one(tmp_path / "a", sess)
    assert member["effort_mechanism"] == "prompt_directive_advisory"
    assert member["subagent_type"] == "session-effort-worker"
    front = (agents / "session-effort-worker.md").read_text().split("---")[1]
    assert "effort:" not in front, "an effort key here silently pins the unbound path"
    assert "effort: medium" in (agents / "general-purpose.md").read_text().split("---")[1]

    monkeypatch.setattr(run, "_TIER_AGENT_DIR", tmp_path / "no-agents")
    assert _dispatch_one(tmp_path / "b", sess)["subagent_type"] is None


def test_low_effort_dispatch_carries_the_fable51_guidance(tmp_path):
    """`reasoning: low` has no tier agent for sonnet (falls to the advisory
    branch) AND `_REASONING_DIRECTIVE['low']` is the empty string — the exact
    combination that skipped the wrapper line before this session's fix, so the
    guidance must not be riding on that prepend."""
    sess = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
            "reasoning": "low", "task_class": "mechanical", "prompt": "do the small thing"}
    member = _dispatch_one(tmp_path, sess)
    assert member["effort_mechanism"] == "prompt_directive_advisory"  # sanity: the fallback
    for line in (_SCOPE_TESTS_LINE, _TARGETED_EDITS_LINE, _OUTPUT_STYLE_LINE):
        assert line in member["prompt_text"], f"missing on low-effort dispatch: {line!r}"


def test_codex_wrapper_prompt_carries_none_of_the_guidance():
    """The Codex dispatch prompt (codex_command._codex_wrapper_prompt) is a
    different funnel entirely — it must never pick up Claude-only guidance."""
    prompt = cc._codex_wrapper_prompt("s01", "codex exec …", "/tmp/last.txt")
    for line in (_SCOPE_TESTS_LINE, _TARGETED_EDITS_LINE, _OUTPUT_STYLE_LINE):
        assert line not in prompt, f"Codex wrapper prompt must not carry: {line!r}"
