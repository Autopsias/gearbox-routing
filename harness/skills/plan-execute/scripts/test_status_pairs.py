"""ENG-04 — one `status_changed` row per real status change, and the reader.

Run: pytest skills/plan-execute/scripts/test_status_pairs.py -q
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import plan_mutate as pm  # noqa: E402
import run_state_io as rsi  # noqa: E402
import status_pairs as sp  # noqa: E402
from test_shipping import make_plan  # noqa: E402


@pytest.fixture
def plan(tmp_path):
    sessions = [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
         "dispatch": {"depends_on": ["s01"]}},
    ]
    return Path(make_plan(tmp_path, sessions))


def _rows(plan_dir):
    p = Path(plan_dir) / "run.ndjson"
    if not p.is_file():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r["event"] == "status_changed"]


def test_one_row_per_change_none_for_note_or_repeat(plan):
    html = plan / "PLAN.html"
    ab.apply_mutation(html, "s01", status="DOING")
    ab.apply_mutation(html, "s01", status="DOING")               # repeat
    ab.apply_mutation(html, "s01", status="DOING", note="more")  # note only
    ab.apply_mutation(html, "s01", status="DONE")
    rows = _rows(plan)
    assert [(r["article"], r["from"], r["to"]) for r in rows] == [
        ("s01", "TODO", "DOING"), ("s01", "DOING", "DONE")]
    # same keys as a row log_event writes
    rsi.log_event(plan, "probe", article="x", **{"from": "A", "to": "B"})
    probe = json.loads((plan / "run.ndjson").read_text().splitlines()[-1])
    assert set(rows[0]) == set(probe) - {"event"} | {"event"}


def test_rebuild_carry_over_writes_no_row(plan):
    ab.apply_mutation(plan / "PLAN.html", "s01", status="DOING")
    before = len(_rows(plan))
    lived_in = (plan / "PLAN.html").read_text()
    ab.carry_over_state(lived_in, lived_in)
    assert len(_rows(plan)) == before


def test_plan_mutate_retire_writes_one_row(plan):
    pm.retire_session(str(plan), "s02", reason="not needed")
    rows = [(r["article"], r["from"], r["to"]) for r in _rows(plan)]
    assert ("s02", "TODO", "WONTFIX") in rows
    assert len(rows) == len(set(rows))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unwritable_log_does_not_stop_status_write(plan, capsys):
    log = plan / "run.ndjson"
    log.write_text("")
    log.chmod(0o444)
    ab.apply_mutation(plan / "PLAN.html", "s01", status="DOING")
    assert ab.read_status((plan / "PLAN.html").read_text(), "s01") == "DOING"
    assert "row not written" in capsys.readouterr().err


def test_html_outside_a_plan_dir_creates_no_log(plan, tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    shutil.copy(plan / "PLAN.html", loose / "PLAN.html")
    ab.apply_mutation(loose / "PLAN.html", "s01", status="DOING")
    assert not (loose / "run.ndjson").exists()


def test_reader_flags_pair_hand_edit_and_no_data(plan, tmp_path):
    assert sp.report([plan]).endswith("NO DATA")
    html = plan / "PLAN.html"
    ab.apply_mutation(html, "s01", status="DONE")
    ab.apply_mutation(html, "s01", status="DOING")               # DONE -> DOING planted
    out = sp.report([plan])
    assert "DONE -> DOING: 1  [NOT EXPECTED]" in out
    assert "DIFFERENCE" not in out
    html.write_bytes(ab.mutate_text(html.read_bytes().decode(), "s01", status="DONE").encode())  # hand edit
    assert "DIFFERENCE s01: last logged DOING, PLAN.html DONE" in sp.report([plan])
    assert sp.main([str(plan)]) == 0
