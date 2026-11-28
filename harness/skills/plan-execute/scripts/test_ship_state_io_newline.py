"""durable_write_json ends every file with a newline.

Without it, a repo's end-of-file hook rewrites _verify_state/*.json inside the
plan record, and the land's final-record commit fails as an empty commit.

Run: pytest skills/plan-execute/scripts/test_ship_state_io_newline.py -q
"""
import json

import pytest

import ship_state_io as ssio


def test_written_json_ends_with_one_newline_and_still_parses(tmp_path):
    p = tmp_path / "_verify_state" / "s01.json"
    ssio.durable_write_json(p, {"status": "passed"})
    raw = p.read_text()
    assert raw.endswith("}\n") and not raw.endswith("\n\n")
    assert json.loads(raw) == {"status": "passed"}

def test_a_failed_second_write_keeps_the_first_and_the_next_write_recovers(tmp_path, monkeypatch):
    """C-04 rows 6-7: the temp file is written, then os.replace (or the dir fsync, or the
    re-read) fails. The earlier state stays readable, no temp file is left, and the next
    save lands."""
    plan = tmp_path / "plan"
    ssio.save_ship_state(plan, "s01", {"v": 1})
    path = ssio.ship_state_path(plan, "s01")
    fired = []

    def boom(*a, **k):
        fired.append(1)
        raise OSError("injected")

    with monkeypatch.context() as mp:
        mp.setattr(ssio.os, "replace", boom)
        with pytest.raises(OSError):
            ssio.save_ship_state(plan, "s01", {"v": 2})
    assert fired and ssio.load_ship_state(plan, "s01") == {"v": 1}
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".tmp-")] == []
    fired.clear()
    with monkeypatch.context() as mp:                 # crash after the rename: the new file is there
        mp.setattr(ssio, "_fsync_dir", boom)
        with pytest.raises(OSError):
            ssio.save_ship_state(plan, "s01", {"v": 3})
    assert fired and ssio.load_ship_state(plan, "s01") == {"v": 3}
    ssio.save_ship_state(plan, "s01", {"v": 4})
    assert ssio.load_ship_state(plan, "s01") == {"v": 4}


def test_a_write_that_does_not_validate_is_a_named_error_and_a_torn_file_reads_from_the_bak(tmp_path, monkeypatch):
    plan = tmp_path / "plan"
    ssio.save_ship_state(plan, "s01", {"v": 1})
    ssio.save_ship_state(plan, "s01", {"v": 2})       # .bak now holds v1
    path = ssio.ship_state_path(plan, "s01")
    fired = []

    def torn(*a, **k):
        fired.append(1)
        raise json.JSONDecodeError("injected", "", 0)

    with monkeypatch.context() as mp:
        mp.setattr(ssio.json, "loads", torn)
        with pytest.raises(ssio.ShipStateError):      # never a quiet success
            ssio.save_ship_state(plan, "s01", {"v": 3})
    assert fired
    path.write_text("{")                              # a torn primary: the reader falls back to the .bak (v2)
    assert ssio.load_ship_state(plan, "s01") == {"v": 2}
    path.with_name(path.name + ".bak").write_text("{")
    with pytest.raises(ssio.ShipStateError):
        ssio.load_ship_state(plan, "s01")
