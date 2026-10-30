"""Tests for the one protected reader (s08 rework round 2): every file a run depends on
is read the way a later step reads it, and any refusal is a failed check, never a hang
and never an older pass left standing. Also the archived unresolved candidate."""

import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
from exposure_testlib import (  # noqa: E402
    CONF, _card_sast, _deep_text, _run, archived_candidate, fast, git, lead, make_repo, needs_node, plant,
    record_run, rejected)


def _main_record(repo, out):
    return exposure.main(["check-run", str(out), str(repo), "--record", "--agents", "25"])


def _older_pass(repo):
    health.collect(repo)
    exposure.record(repo, "pass", "an older pass")


# S8R2-H1: a report is read as text: blank, bad UTF-8 or a FIFO is a failed check
@pytest.mark.parametrize("kind", ["blank", "bad-utf8", "fifo", "valid"])
def test_report_is_read_as_text(exposure_repo, exposure_node, kind):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)        # lead-a open
    out = plant(exposure_repo, [rejected("lead-a")])
    rep = out / "REPORT.md"
    if kind == "blank":
        rep.write_text("  \n\t\n")
    elif kind == "bad-utf8":
        rep.write_bytes(b"\xff report")
    elif kind == "fifo":
        rep.unlink()
        os.mkfifo(rep)
    with fast():
        res = cf_check.check_run(out, exposure_repo)
    if kind == "valid":                 # the known positive: a readable report resolves lead-a
        assert res["status"] == "pass", res
        return
    why = {"blank": "blank", "bad-utf8": "not valid UTF-8 text", "fifo": "not a regular file"}[kind]
    assert res["status"] == "warn" and f"report file not readable text: REPORT.md ({why})" \
        in res["detail"], res
    assert ("lead", "lead-a") in {(i["kind"], i["id"]) for i in res["open"]}


# S8R2-M1: a FIFO at exposure.json is an exposure error, recorded pending, never a hang
def test_fifo_exposure_file_is_recorded_pending(exposure_repo, exposure_node):
    _older_pass(exposure_repo)
    opt_in = exposure_repo / exposure.EXPOSURE
    opt_in.unlink()
    os.mkfifo(opt_in)
    with fast():
        kind, why = exposure.route(exposure_repo, make_dir=False)
        assert kind == "error" and why.endswith("not readable (not a regular file)")
        assert _main_record(exposure_repo, plant(exposure_repo)) == 0
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and "not readable (not a regular file)" in card["detail"]


# S8R2-M2: the newest marker is read by the same reader everywhere: check-run, accept,
# prior runs and repair-log all refuse a FIFO or a symlink there, fast
@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_special_newest_marker_is_refused_everywhere(tmp_path, exposure_repo, exposure_node,
                                                     exposure_home, kind):
    _older_pass(exposure_repo)
    newest = plant(exposure_repo, [lead("lead-a")])
    record_run(newest, exposure_repo)
    mark = newest / exposure.ACCEPTED
    good = mark.read_text()
    mark.unlink()
    if kind == "fifo":
        os.mkfifo(mark)
    else:
        (tmp_path / "planted.json").write_text(json.dumps({**json.loads(good), "open": []}))
        mark.symlink_to(tmp_path / "planted.json")
    with fast():
        res = cf_check.check_run(plant(exposure_repo), exposure_repo)
        assert res["status"] == "pending" and "open-set state unreadable" in res["detail"]
        assert "not a regular file" in res["detail"]
        assert exposure.prior_runs(exposure_repo) == []
        with pytest.raises(ValueError, match="not a regular file"):
            exposure.accept(plant(exposure_repo), "pass", [])
        assert _main_record(exposure_repo, plant(exposure_repo)) == 0
        assert exposure.main(["repair-log", str(exposure_repo)]) == 2
    assert _card_sast(exposure_repo)["status"] == "pending"


# s08 r2 re-walk: the Sentry detail file is read by the same reader
def test_fifo_detail_file_is_recorded_pending(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    _older_pass(repo)
    df = tmp_path / "detail.txt"
    os.mkfifo(df)
    with fast():
        assert exposure.main(["record", str(repo), "pass", "--detail-file", str(df)]) == 0
    card = _card_sast(repo)
    assert card["status"] == "pending" and card["detail"] == \
        "detail file unreadable: not a regular file"


# C8R2-M1: an ARCHIVED candidate attempt with unresolved reasons is a failed check (real validators)
@needs_node
def test_archived_unresolved_candidate_blocks_resolution(exposure_repo):
    assert _run(exposure_repo, [lead("lead-a")])["status"] == "warn"           # run 1
    ledger = archived_candidate()
    ledger[0]["attempts"][0]["unresolved"] = ["validation_budget_exhausted"]
    res = _run(exposure_repo, [rejected("lead-a"), rejected("cand-1")], ledger)  # run 2
    assert res["status"] == "warn", res
    assert "archived candidate attempt (wave 1) of" in res["detail"]
    assert "unresolved (validation_budget_exhausted): cand-1" in res["detail"]
    assert {(i["kind"], i["id"]) for i in res["open"]} == {("lead", "lead-a"), ("candidate", "cand-1")}
    res = _run(exposure_repo)                                                  # run 3: omit
    assert res["status"] == "warn"
    assert "missing here: unvalidated candidate cand-1, lead Lead lead-a" in res["detail"]


# C8R2-M2 (owner default: the rule stays literal): --deep says what a run that can clear items is
def test_deep_text_says_what_a_run_that_can_clear_items_looks_like():
    text = _deep_text()
    assert "**A run that can clear items** has no failed check at all" in text
    assert "is read by one protected reader (`runlog.read_file`)" in text


def _small_stack(fn):
    """fn() in a thread with a 1 MiB stack. Python 3.14 bounds JSON nesting by the C
    stack, not the recursion limit, so this makes deep nesting raise there as it
    already does on 3.11-3.13 (measured: 32000 levels)."""
    res = {}

    def go():
        try:
            res["rc"] = fn()
        except BaseException as e:      # reported to the test, not swallowed
            res["error"] = e
    old = threading.stack_size(1024 * 1024)
    try:
        t = threading.Thread(target=go)
        t.start()
        t.join()
    finally:
        threading.stack_size(old)
    return res


# S8R3-H1: a deeply nested exposure.json is refused and recorded pending over an older pass
def test_nested_exposure_file_is_recorded_pending(exposure_repo, exposure_node):
    _older_pass(exposure_repo)
    out = plant(exposure_repo)
    depth = exposure.MAX_OPT_IN // 2 - 1        # the deepest nesting under the size limit
    (exposure_repo / exposure.EXPOSURE).write_text("[" * depth + "]" * depth)
    assert _small_stack(lambda: _main_record(exposure_repo, out)) == {"rc": 0}
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and "not readable JSON (nested too deeply)" in card["detail"]


# S8R3-H1: the crash handler records a fixed detail and never re-reads exposure.json
def test_crash_handler_never_reads_the_exposure_file(exposure_repo, exposure_node, monkeypatch):
    _older_pass(exposure_repo)
    out, reads, real = plant(exposure_repo), [], exposure.read_file
    monkeypatch.setattr(exposure, "read_file",
                        lambda p, *a, **k: reads.append(str(p)) or real(p, *a, **k))

    def boom(*a):
        raise RecursionError("planted")
    monkeypatch.setattr(cf_check, "check_run", boom)
    assert _main_record(exposure_repo, out) == 2
    assert not [p for p in reads if p.endswith("exposure.json")], reads
    card = _card_sast(exposure_repo)
    assert (card["status"], card["detail"]) == (
        "pending", f"check-run error: RecursionError: planted | output: {out}")


# S9 Claude L2: the crash line is capped like the other detail lines, and recorded first
def test_crash_handler_caps_a_long_message(exposure_repo, exposure_node, monkeypatch):
    _older_pass(exposure_repo)
    out = plant(exposure_repo)

    def boom(*a):
        raise ValueError("x" * 10000)
    monkeypatch.setattr(cf_check, "check_run", boom)
    assert _main_record(exposure_repo, out) == 2
    line = ("check-run error: ValueError: " + "x" * 10000)[:exposure.MAX_LINE]
    assert exposure.MAX_LINE == 2000
    assert (_card_sast(exposure_repo)["status"], _card_sast(exposure_repo)["detail"]) == (
        "pending", f"{line} | output: {out}")


# S9 Codex H1: accept() takes the digest of the bytes check_run validated; documents that
# change or stop reading in between are pending, and the earlier open set stays
@pytest.mark.parametrize("swap", ["fifo", "bytes"])
def test_documents_swapped_before_accept_are_pending(exposure_repo, exposure_node, monkeypatch,
                                                      swap):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)        # lead-a open
    _older_pass(exposure_repo)
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    before = log.read_text()
    out, real = plant(exposure_repo, [rejected("lead-a")]), cf_check.check_run

    def then_swap(*a):
        res = real(*a)
        assert res["status"] == "pass", res          # the validated bytes do resolve lead-a
        (out / "findings.json").unlink()
        if swap == "fifo":
            os.mkfifo(out / "findings.json")
        else:
            (out / "findings.json").write_text("[]")
        return res
    monkeypatch.setattr(cf_check, "check_run", then_swap)
    with fast():
        assert _main_record(exposure_repo, out) == 2
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and "changed since check-run validated them" in card["detail"]
    assert not os.path.lexists(out / exposure.ACCEPTED) and log.read_text() == before
    assert set(exposure.open_set(exposure_repo)[0]) == {("lead", "lead-a")}
    assert all(json.loads(m.read_text())["digest"] for m in
               exposure.runs_parent(exposure_repo).glob(f"*/{exposure.ACCEPTED}"))


# S9 Codex M1: an old exposure.json too deeply nested to read is a route error, never skipped;
# malformed old JSON is still skipped (the known positive: the older pass stands)
@pytest.mark.parametrize("old", ["deep", "malformed"])
def test_nested_exposure_in_history_is_a_route_error(tmp_path, exposure_home, exposure_node, old):
    repo = make_repo(tmp_path / "h")
    depth = exposure.MAX_OPT_IN // 2 - 1
    (repo / ".claude").mkdir()
    (repo / exposure.EXPOSURE).write_text('{"exposed": true, "reason": "public", "extra": ' + (
        "[" * depth + "]" * depth + "}" if old == "deep" else "[}"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "exposed")
    git(repo, "rm", "-q", str(exposure.EXPOSURE))
    git(repo, "commit", "-qm", "removed")
    _older_pass(repo)
    assert _small_stack(lambda: exposure.record_route(repo)) == {"rc": None}
    card = _card_sast(repo)
    if old == "malformed":
        assert (card["status"], card["detail"]) == ("pass", "an older pass")
        return
    assert card["status"] == "pending" and card["detail"].startswith(
        f"route error: history: {exposure.EXPOSURE} at commit ") and "nested too deeply" in card["detail"]


# s10 rework: a run folder given through a symlink is accepted under its real path, so the
# accepted-runs log names it and its confirmed finding stays open
def test_symlinked_run_folder_keeps_the_confirmed_finding(exposure_repo, exposure_node, tmp_path):
    health.collect(exposure_repo)
    assert record_run(plant(exposure_repo), exposure_repo)["status"] == "pass"     # run 1
    out = plant(exposure_repo, [CONF])
    link = tmp_path / "elsewhere" / out.name
    link.parent.mkdir()
    link.symlink_to(out)
    assert _main_record(exposure_repo, link) == 0                                   # run 2
    assert _card_sast(exposure_repo)["status"] == "fail"
    assert ("confirmed", "conf-1") in exposure.open_set(exposure_repo)[0]
    assert not (link.parent / exposure.ACCEPTED_LOG).exists()
    assert _run(exposure_repo)["status"] == "fail"                                   # run 3: clean
