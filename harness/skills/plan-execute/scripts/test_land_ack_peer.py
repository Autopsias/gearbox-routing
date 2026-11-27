"""`land-ack` on a plan that is already acked names that ack, not a missing candidate.

Two runs of one plan can race to ack; the loser used to read "there is no land candidate
awaiting review", which is false and sends the owner to run `land` again. It now prints the
first ack's time and note and points at `land-status`.

    pytest skills/plan-execute/scripts/test_land_ack_peer.py -q
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land  # noqa: E402
import land_brief as lb  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import ack_only, build_repo, isolate, run_cli, work  # noqa: E402


def _acks(plan_dir):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("event") == "plan_land_acked"]


def test_a_second_ack_names_the_first_and_changes_nothing(tmp_path):
    fx = build_repo(tmp_path)
    iso = isolate(fx, "plan-a")
    plan_dir = iso["plan_dir"]
    work(iso["tree"], "src/f.py", "F = 1\n")
    early = land.land_ack(str(plan_dir), "too early")
    assert early["action"] == "error" and "no land candidate awaiting review" in early["message"]
    assert run_cli("land", plan_dir)[1]["action"] == "land-awaits-review"
    first = ack_only(plan_dir, "first note")[1]["ack"]
    state_file = lst.state_path(lst.context(str(plan_dir))["root"], "plan-a")
    before = state_file.read_bytes()
    out = land.land_ack(str(plan_dir), "second note")
    assert out["action"] == "error" and out["state"] == "acked", out
    assert first["at"] in out["message"] and "'first note'" in out["message"], out
    assert f"run.py land-status {plan_dir}" in out["message"] and "may be landing" in out["message"]
    assert "second note" not in out["message"]
    assert state_file.read_bytes() == before                       # the state is byte-identical
    assert [e["note"] for e in _acks(plan_dir)] == ["first note"]  # no second plan_land_acked


def test_the_message_covers_landing_and_landed_and_a_missing_ack():
    ack = {"at": "2026-11-24T10:00:00Z", "note": None}
    for state in ("acked", "landing", "landed"):
        out = lb.no_candidate("/p", {"state": state, "ack": ack})
        assert (out["action"], out["state"]) == ("error", state)
        assert "2026-11-24T10:00:00Z" in out["message"] and "None" in out["message"]
    for st in ({"state": "acked"}, {"state": "parked", "ack": ack}, {}):
        assert "no land candidate awaiting review" in lb.no_candidate("/p", st)["message"]
