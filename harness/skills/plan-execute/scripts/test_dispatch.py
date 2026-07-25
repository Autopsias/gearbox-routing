"""next_action blocked-scoping (regression for the item-level BLOCKED wedge).

`statuses` (read from PLAN.html article blocks) carries BOTH item and session
statuses. Before this fix next_action's stop-the-world check iterated over the
whole dict, so a single BLOCKED *item* inside a PARTIAL session wedged the loop
forever (and the item id was surfaced under "sessions"), contradicting the
documented PARTIAL semantics (loop re-dispatches the session).

Run: pytest plan-execute/scripts/test_dispatch.py -q
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import dispatch as dsp  # noqa: E402


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
