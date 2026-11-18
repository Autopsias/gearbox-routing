"""begin_guard: `begin` refuses finished sessions and sessions waiting for the owner.

The refusal writes no run.ndjson row of its own; the `concurrent_begin_*` row comes
from `_begin_preflight` (before the check) and only when another plan is active, so
these fixtures hold a single plan.

Run: pytest skills/plan-execute/scripts/test_begin_guard.py -q
"""
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import begin_guard as bg  # noqa: E402
import manifest_io as mio  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_checkpoint_answer import FILE, _answer_file, _event, _resume, _sessions  # noqa: E402
from test_shipping import make_plan, write_closeout  # noqa: E402

STATUSES = ["TODO", "DOING", "DONE", "PARTIAL", "BLOCKED", "AWAITS_REVIEW", "WONTFIX", "DEFERRED"]


def _set(plan_dir, sid, status):
    ab.apply_mutation(Path(plan_dir) / "PLAN.html", sid, status=status, note="t")


def _snapshot(plan_dir):
    plan_dir = Path(plan_dir)
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(plan_dir.rglob("*")) if p.is_file()}


def _begin(plan_dir, sids, **kw):
    return run.cmd_begin(str(plan_dir), sids, **kw)


def _statuses(plan_dir):
    return ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_text())


def _build(tmp_path, flag):
    s = _sessions()
    if not flag:
        s[0].pop("dispatch")
    return make_plan(tmp_path, s)


def _arrange(plan_dir, status, answer, closeout, kind):
    """Put s01 into the cell. answer: none|live|no_answer|unreadable."""
    _set(plan_dir, "s01", "AWAITS_REVIEW" if answer != "none" or kind else status)
    if kind == "pre_dispatch":
        _event(plan_dir, kind="pre_dispatch", ts="2026-11-12T10:00:00+00:00")
    elif kind == "none":
        _event(plan_dir)  # a checkpoint event with no kind
    if answer in ("live", "no_answer"):
        _set(plan_dir, "s01", "AWAITS_REVIEW")
        if kind != "pre_dispatch":
            _event(plan_dir, kind="pre_dispatch", ts="2026-11-12T10:00:00+00:00")
        row = {"session": "s01", "given": answer == "live", "answer": "go",
               "answers_checkpoint": "2026-11-12T10:00:00+00:00", "at": "2026-11-12T11:00:00+00:00"}
        (Path(plan_dir) / FILE).write_text(json.dumps(row) + "\n")
    elif answer == "unreadable":
        (Path(plan_dir) / FILE).write_bytes(b'{"session": "s01", \xff\xfe\n')
    if closeout:
        write_closeout(plan_dir, "s01", result="DONE")
    _set(plan_dir, "s01", status)


def _expected(status, flag, answer, closeout, kind):
    """Independent restatement of the spec table. True = refuse."""
    if status in ("DONE", "WONTFIX", "DEFERRED"):
        return True
    if status == "AWAITS_REVIEW":
        if closeout:
            return True
        if flag and kind == "pre_dispatch":
            return answer in ("none", "unreadable")
        return False
    if status == "TODO":
        return flag and answer in ("none", "unreadable")
    return False


CELLS = [
    (st, flag, ans, co, kind)
    for st, flag, ans, co, kind in itertools.product(
        STATUSES, (True, False), ("none", "live", "no_answer", "unreadable"), (False, True),
        ("pre_dispatch", "none"))
    if (co is False or st == "AWAITS_REVIEW")
    and (kind == "pre_dispatch" or st == "AWAITS_REVIEW")
    and (ans in ("none", "unreadable") or kind == "pre_dispatch")
]


@pytest.mark.parametrize("status,flag,answer,closeout,kind", CELLS)
def test_matrix(tmp_path, status, flag, answer, closeout, kind):
    plan = _build(tmp_path, flag)
    _arrange(plan, status, answer, closeout, kind)
    got = bg.refusal(str(plan), mio.session_by_id(mio.load_manifest(plan))["s01"], status)
    assert (got is not None) == _expected(status, flag, answer, closeout, kind), (got, status)
    if got:
        assert "s01" in got


def test_matrix_is_not_empty():
    assert len(CELLS) > 40
    assert any(_expected(*c) for c in CELLS) and not all(_expected(*c) for c in CELLS)


def test_probe_unanswered_checkpoint_session_is_refused_and_nothing_moves(tmp_path, capsys):
    plan = make_plan(tmp_path, _sessions())
    before = _snapshot(plan)
    with pytest.raises(SystemExit) as e:
        _begin(plan, ["s01"])
    assert "run.py checkpoint" in str(e.value) and "--answer-file" in str(e.value)
    assert _snapshot(plan) == before and _statuses(plan)["s01"] == "TODO"


def test_probe_done_session_is_refused_and_stays_done(tmp_path):
    plan = make_plan(tmp_path, _sessions())
    _set(plan, "s02", "DONE")
    before = _snapshot(plan)
    with pytest.raises(SystemExit) as e:
        _begin(plan, ["s02"])
    assert "redispatch" in str(e.value) and "--reason" in str(e.value)
    assert _snapshot(plan) == before and _statuses(plan)["s02"] == "DONE"


def test_resume_does_not_open_the_refusal(tmp_path):
    plan = make_plan(tmp_path, _sessions())
    _set(plan, "s02", "DONE")
    with pytest.raises(SystemExit):
        _begin(plan, ["s02"], resume=True)
    assert _statuses(plan)["s02"] == "DONE"


def test_one_refused_session_flips_no_session(tmp_path):
    plan = make_plan(tmp_path, _sessions())
    _set(plan, "s01", "DONE")
    before = _snapshot(plan)
    with pytest.raises(SystemExit):
        _begin(plan, ["s02", "s01"])
    assert _snapshot(plan) == before
    assert _statuses(plan)["s02"] == "TODO"
    assert not (Path(plan) / "run_state.json").exists() or rsi.load_state(plan).get("last_batch") in (None, [])


def test_answered_checkpoint_session_begins(tmp_path, capsys):
    plan = make_plan(tmp_path, _sessions())
    run.cmd_checkpoint(str(plan), "s01")
    capsys.readouterr()
    _resume(plan, capsys, file=_answer_file(plan, "go"))
    capsys.readouterr()
    _begin(plan, ["s01"])
    assert _statuses(plan)["s01"] == "DOING"


def test_non_head_flagged_member_is_refused_known_limit(tmp_path):
    plan = make_plan(tmp_path, _sessions(peer=True))
    flagged = mio.session_by_id(mio.load_manifest(plan))["s01"]
    # s02 is the batch head here in spirit: s01 flagged, no way to answer it -> refused.
    msg = bg.refusal(str(plan), flagged, "TODO")
    assert msg and "not the head of a parallel batch" in msg


def test_command_line_prints_one_line_per_session_and_writes_nothing(tmp_path):
    plan = make_plan(tmp_path, _sessions())
    _set(plan, "s02", "DONE")
    before = _snapshot(plan)
    out = subprocess.run([sys.executable, str(SCRIPTS / "begin_guard.py"), str(plan)],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == 2 and out[0].startswith("s01 TODO refusing") and out[1] == "s02 DONE refusing to begin s02: it is DONE, a finished session. To re-run it on purpose: " + out[1].split("purpose: ")[1]
    assert _snapshot(plan) == before


def test_a_session_finished_while_begin_waited_for_the_lock_is_refused(tmp_path, monkeypatch):
    plan = make_plan(tmp_path, _sessions())
    real = rsi.acquire_lock

    def lock_after_another_run_finished(plan_dir):   # the race: the status moves before the lock is ours
        _set(plan_dir, "s02", "DONE")
        real(plan_dir)

    monkeypatch.setattr(rsi, "acquire_lock", lock_after_another_run_finished)
    with pytest.raises(SystemExit) as e:
        _begin(plan, ["s02"])
    assert "redispatch" in str(e.value) and _statuses(plan)["s02"] == "DONE"
    assert not (plan / ".lock").exists()
