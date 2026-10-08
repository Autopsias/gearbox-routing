"""durable_write_json ends every file with a newline.

Without it, a repo's end-of-file hook rewrites _verify_state/*.json inside the
plan record, and the land's final-record commit fails as an empty commit.

Run: pytest skills/plan-execute/scripts/test_ship_state_io_newline.py -q
"""
import json

import ship_state_io as ssio


def test_written_json_ends_with_one_newline_and_still_parses(tmp_path):
    p = tmp_path / "_verify_state" / "s01.json"
    ssio.durable_write_json(p, {"status": "passed"})
    raw = p.read_text()
    assert raw.endswith("}\n") and not raw.endswith("\n\n")
    assert json.loads(raw) == {"status": "passed"}
