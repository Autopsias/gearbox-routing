"""Tests for repair-log and accept() timing (s08): the s07 round-3 findings M2, M3,
L1 and L2. A repair never drops an open item silently and never trusts a marker the
accepted-runs log did not name for time or ref."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
import runlog  # noqa: E402
from exposure_testlib import (  # noqa: E402
    CONF, _card_sast, _deep_text, cli, fast, lead, plant, record_run, rejected, unit)


def _env(home):
    return {**os.environ, "HOME": str(home)}


def _folders(repo):
    return sorted(p.name for p in exposure.runs_parent(repo).iterdir() if p.is_dir())


def _break_log(repo):
    log = exposure.runs_parent(repo) / exposure.ACCEPTED_LOG
    log.write_text(log.read_text() + "garbage\n")         # open-set state unreadable
    with pytest.raises(ValueError):
        exposure.open_set(repo)


# S7R3-M2 + L2: an unreadable marker of a logged run refuses the repair, writing nothing
@pytest.mark.parametrize("kind", ["broken", "symlink", "fifo"])
def test_repair_log_refuses_an_unreadable_logged_marker(tmp_path, exposure_repo, exposure_node,
                                                        exposure_home, kind):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    second = plant(exposure_repo, [lead("lead-a"), lead("lead-b")])
    record_run(second, exposure_repo)
    mark = second / exposure.ACCEPTED
    mark.unlink()
    if kind == "broken":
        mark.write_text("{broken")
    elif kind == "symlink":
        (tmp_path / "elsewhere.json").write_text(json.dumps({"accepted_at": 1, "open": []}))
        mark.symlink_to(tmp_path / "elsewhere.json")
    else:
        os.mkfifo(mark)
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    before, folders = log.read_bytes(), _folders(exposure_repo)
    with fast():
        p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert p.returncode == 2 and p.stdout.startswith(
        f"refused: logged run {second.name} has an unreadable marker"), p.stdout + p.stderr
    assert "nothing written" in p.stdout
    assert log.read_bytes() == before and _folders(exposure_repo) == folders


# S7R3-L2 + C8-M1: a marker the log never named is refused before reading when it is not
# a file, and the repair writes nothing: that marker could hold the newest state
@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_repair_log_refuses_a_special_unlogged_marker_before_reading(
        tmp_path, exposure_repo, exposure_node, exposure_home, kind):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    stray = plant(exposure_repo)
    if kind == "symlink":
        (tmp_path / "planted.json").write_text(json.dumps({"accepted_at": 1, "open": []}))
        (stray / exposure.ACCEPTED).symlink_to(tmp_path / "planted.json")
    else:
        os.mkfifo(stray / exposure.ACCEPTED)
    _break_log(exposure_repo)
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    before, folders = log.read_bytes(), _folders(exposure_repo)
    with fast():
        p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert p.returncode == 2 and "not a regular file" in p.stdout and "nothing written" in p.stdout
    # C8R2-L2: the refusal names the folder and says deleting an unlogged one is safe
    assert f"unreadable marker in run folder {stray} (" in p.stdout
    assert "deleting that unlogged run folder is safe" in p.stdout
    assert log.read_bytes() == before and _folders(exposure_repo) == folders


# S7R3-M3: no time and no ref from a marker the log did not name
def test_repair_log_takes_no_time_or_ref_from_unlogged_markers(exposure_repo, exposure_node,
                                                               exposure_home):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    head = exposure.head(exposure_repo)
    stray = plant(exposure_repo)
    (stray / exposure.ACCEPTED).write_text(json.dumps({
        "status": "pass", "digest": None, "accepted_at": runlog.MAX_NS - 1,
        "open": [{"kind": "lead", "id": "lead-a", "label": "planted", "ref": "0" * 40},
                 {"kind": "lead", "id": "lead-z", "label": "planted z", "ref": "f" * 40}]}))
    _break_log(exposure_repo)
    p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert p.returncode == 0 and "3 open items" not in p.stdout, p.stdout + p.stderr
    assert p.stdout.splitlines()[0] == "repaired: 2 acceptance markers read, 2 open items kept"
    newest = exposure.runs_parent(exposure_repo) / runlog._accepted_names(
        exposure.runs_parent(exposure_repo))[-1] / exposure.ACCEPTED
    mark = json.loads(newest.read_text())
    assert mark["accepted_at"] < 2 ** 62                   # the clock, not the planted time
    refs = {i["id"]: (i["label"], i["ref"]) for i in mark["open"]}
    assert refs == {"lead-a": ("Lead lead-a", head), "lead-z": ("planted z", None)}
    for _ in range(2):                                     # later acceptances are not blocked
        res = record_run(plant(exposure_repo, [lead("lead-a"), lead("lead-z")]), exposure_repo)
        assert res["status"] == "warn"


# S7R3-M3: accept() never raises on a previous acceptance time in the future
def test_accept_survives_a_previous_time_in_the_future(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    mark = json.loads((out / exposure.ACCEPTED).read_text())
    (out / exposure.ACCEPTED).write_text(json.dumps({**mark, "accepted_at": runlog.MAX_NS}))
    for _ in range(2):
        nxt = plant(exposure_repo, [lead("lead-a")])
        assert record_run(nxt, exposure_repo)["status"] == "warn"
        assert json.loads((nxt / exposure.ACCEPTED).read_text())["accepted_at"] == runlog.MAX_NS
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]


# S7R3-L1: the repair seeds from the newest logged marker, so a resolved item stays resolved
def test_repair_log_never_reopens_what_a_later_logged_run_resolved(exposure_repo, exposure_node,
                                                                   exposure_home):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    assert record_run(plant(exposure_repo, [rejected("lead-a")]), exposure_repo)["open"] == []
    _break_log(exposure_repo)
    p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert (p.returncode, p.stdout.splitlines()[0]) == (
        0, "repaired: 2 acceptance markers read, 0 open items kept"), p.stdout + p.stderr
    assert record_run(plant(exposure_repo), exposure_repo)["status"] == "pass"


# S7R3-M2 + L1: the --deep text says what repair-log can and cannot keep
def test_deep_text_describes_repair_log_honestly():
    text = _deep_text()
    assert "keeps the union of their open items" not in text
    assert "Exit 2 (`refused: …`) means a logged run's marker, or any other marker, cannot be" in text
    assert "Exit 1" not in text
    assert "a false fail, never a false pass" in text
    # S9 Claude L1: a lost marker goes to the owner, never to repair-log (which refuses it)
    assert "and a logged run folder whose marker is gone — run" not in text
    assert "Do not run repair-log for it: it always refuses that case. Stop and name the folder" in text
    assert "Deleting that folder leaves check-run `pending` until repair-log rebuilds" in text


# S7R3-M1 (same rule for the log): a FIFO in place of the log is refused, never opened
def test_a_fifo_in_place_of_the_log_is_pending_and_refused(exposure_repo, exposure_node,
                                                           exposure_home):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    log.unlink()
    os.mkfifo(log)
    with fast():
        res = cf_check.check_run(plant(exposure_repo), exposure_repo)
        p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert res["status"] == "pending" and "not a regular file" in res["detail"]
    assert p.returncode == 2 and "not a regular file" in p.stdout


# D3-2: accepted markers with a missing or blank log are pending until repair-log
@pytest.mark.parametrize("how", ["missing", "blank"])
def test_markers_without_a_log_are_pending_until_repair(exposure_repo, exposure_node,
                                                        exposure_home, how):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)          # lead-a open
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    if how == "missing":
        log.unlink()
    else:
        log.write_text(" \n")
    assert cf_check.check_run(plant(exposure_repo), exposure_repo) == {"status": "pending",
        "detail": "open-set state unreadable: accepted markers exist but the log is empty: "
                  "run repair-log"}
    assert cli("repair-log", exposure_repo, env=_env(exposure_home)).returncode == 0
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "warn" and ("lead", "lead-a") in {(i["kind"], i["id"])
                                                              for i in res["open"]}


# D3-2: a first run (no log and no marker) still records
def test_a_first_run_needs_no_log(exposure_repo, exposure_node):
    out = plant(exposure_repo)
    parent = exposure.runs_parent(exposure_repo)
    assert not (parent / exposure.ACCEPTED_LOG).exists()
    assert not list(parent.glob(f"*/{exposure.ACCEPTED}"))
    assert record_run(out, exposure_repo)["status"] == "pass"
    assert (parent / exposure.ACCEPTED_LOG).read_text() == out.name + "\n"


# C8R3-L1: a logged run folder that lost its marker is unreadable state, not "no prior run"
def test_a_logged_folder_without_its_marker_is_pending(exposure_repo, exposure_node):
    first = plant(exposure_repo, [lead("lead-a")])
    record_run(first, exposure_repo)
    (first / exposure.ACCEPTED).unlink()
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pending" and "missing from a logged run folder" in res["detail"], res


# C8R3-L2: the lock file and the log append never block on a FIFO or follow a symlink
def test_lock_and_log_refuse_a_fifo_or_a_symlink(tmp_path, exposure_repo, exposure_node,
                                                 monkeypatch):
    health.collect(exposure_repo)
    exposure.record(exposure_repo, "pass", "an older pass")
    out = plant(exposure_repo)
    parent = exposure.runs_parent(exposure_repo)
    os.mkfifo(parent / exposure.LOCK)
    with fast():
        assert exposure.main(["check-run", str(out), str(exposure_repo), "--record",
                              "--agents", "25"]) == 2
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and f"{exposure.LOCK}: cannot open" in card["detail"]
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    (parent / exposure.ACCEPTED_LOG).symlink_to(outside)
    monkeypatch.setattr(runlog, "_newest", lambda _: None)     # a link planted after the read
    with pytest.raises(ValueError, match="cannot open"):
        exposure.accept(out, "pass", [])
    assert outside.read_text() == "keep me\n"


# land review HIGH (repair_log part): an unlogged lead never reopens beside a confirmed
# finding of the same id
def test_repair_log_skips_an_unlogged_lead_already_open_as_confirmed(exposure_repo, exposure_node,
                                                                     exposure_home):
    record_run(plant(exposure_repo, [lead("conf-1")]), exposure_repo)
    b = plant(exposure_repo, [CONF], ledger=[unit("api", "candidate", result_fingerprints=["conf-1"])])
    record_run(b, exposure_repo)
    (exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG).write_text(b.name + "\n")
    p = cli("repair-log", exposure_repo, env=_env(exposure_home))
    assert p.returncode == 0, p.stdout + p.stderr
    assert set(exposure.open_set(exposure_repo)[0]) == {("confirmed", "conf-1")}
