"""WAKE-01 — an interim stop is not a return: apply refuses it without halting.

An agent that stops to wait for its own background command sends a notification
that holds no closeout. `apply` used to read that as a missing closeout: BLOCKED
plus the halt flag. Rows I1-I8 pin the new behaviour and its limits.

Run: pytest skills/plan-execute/scripts/test_apply_interim_stop.py -q
"""
import json
import re
from pathlib import Path

from escalation_helpers import SESS, make_plan, rsi, run

HOOK = Path(__file__).resolve().parents[3] / "hooks" / "turnend-guard.py"
INTERIM_TEXT = ("This agent has not reported yet: it is waiting on its own background work. "
                "This agent stopped with background work of its own still running.")
GOOD = ('<plan-execute-closeout>\n{"session":"s01","result":"DONE",'
        '"items_completed":["it-1"],"items_blocked":[],"notes":%s,'
        '"dispatch_next":false,"human_checkpoint_reason":null}\n</plan-execute-closeout>')


def _apply(plan_dir, tmp_path, capsys, text):
    f = tmp_path / "out.txt"
    f.write_text(text)
    capsys.readouterr()
    code = 0
    try:
        run.cmd_apply(str(plan_dir), "s01", str(f))
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def _fixture(tmp_path):
    return make_plan(tmp_path, [SESS])


def test_i1_interim_text_changes_nothing(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    before = run._statuses(plan_dir)["s01"]
    code, out = _apply(plan_dir, tmp_path, capsys, INTERIM_TEXT)
    assert code == 1 and out["applied"] is False and out["failure"] == "agent-not-finished"
    assert run._statuses(plan_dir)["s01"] == before != "BLOCKED"
    assert not rsi.is_halted(plan_dir)
    run.cmd_release(plan_dir)


def test_i2_closeout_quoting_a_marker_applies(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    notes = json.dumps({"it-1": "handled 'This agent has not reported yet' text"})
    code, out = _apply(plan_dir, tmp_path, capsys, GOOD % notes)
    assert out["applied"] is True
    assert run._statuses(plan_dir)["s01"] == "DONE"
    run.cmd_release(plan_dir)


def test_i3_no_closeout_no_marker_still_blocks(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    code, out = _apply(plan_dir, tmp_path, capsys, "I finished everything.")
    assert code == 1 and out["failure"] != "agent-not-finished"
    assert run._statuses(plan_dir)["s01"] == "BLOCKED" and rsi.is_halted(plan_dir)
    run.cmd_release(plan_dir)


def test_i4_malformed_closeout_plus_marker_still_blocks(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    text = INTERIM_TEXT + "\n<plan-execute-closeout>\n{not json\n</plan-execute-closeout>"
    code, out = _apply(plan_dir, tmp_path, capsys, text)
    assert code == 1 and out["failure"] != "agent-not-finished"
    assert run._statuses(plan_dir)["s01"] == "BLOCKED" and rsi.is_halted(plan_dir)
    run.cmd_release(plan_dir)


def test_i5_final_message_applies_after_an_interim_refusal(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    _apply(plan_dir, tmp_path, capsys, INTERIM_TEXT)
    code, out = _apply(plan_dir, tmp_path, capsys, GOOD % "{}")
    assert out["applied"] is True and run._statuses(plan_dir)["s01"] == "DONE"
    run.cmd_release(plan_dir)


def test_i6_hook_string_is_in_the_marker_list():
    hook = re.search(r'^INTERIM = "([^"]+)"', HOOK.read_text(), re.M)
    assert hook is not None
    import apply_guards
    assert any(hook.group(1) in o for o in apply_guards.INTERIM_OPENINGS)


def test_i7_final_message_about_another_service_still_blocks(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    code, out = _apply(plan_dir, tmp_path, capsys, "Done, but the billing service has not reported yet.")
    assert code == 1 and out["failure"] != "agent-not-finished"
    assert run._statuses(plan_dir)["s01"] == "BLOCKED" and rsi.is_halted(plan_dir)
    run.cmd_release(plan_dir)


def test_i8_final_message_quoting_an_interim_sentence_still_blocks(tmp_path, capsys):
    plan_dir = _fixture(tmp_path)
    text = ("Status from the app: This agent has not reported yet: "
            "it is waiting on its own background work")
    code, out = _apply(plan_dir, tmp_path, capsys, text)
    assert code == 1 and out["failure"] != "agent-not-finished"
    assert run._statuses(plan_dir)["s01"] == "BLOCKED" and rsi.is_halted(plan_dir)
    run.cmd_release(plan_dir)
