"""ANS-01: `plan --resume --answer-file / --no-answer` records the owner's answer to
a pre-dispatch checkpoint, refuses a bare resume past one, and the digest hands the
live answer to the dispatched session. Cases (a)-(q) of the s01 spec.

Run: pytest skills/plan-execute/scripts/test_checkpoint_answer.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import checkpoint_answer as ca  # noqa: E402
import egress  # noqa: E402
import findings_digest as fd  # noqa: E402
import plan_mutate as pm  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from codex_helpers import _FAKE_AWS_KEY  # noqa: E402
from test_shipping import make_plan  # noqa: E402

BRIEF = {"reason": "Irreversible.", "decision": "Ship now or hold?", "options": ["Ship", "Hold"]}
FILE = "_checkpoint_answers.ndjson"
BEGIN, END = "--- OWNER'S ANSWER AT THE CHECKPOINT BEFORE THIS SESSION ---", "--- END OWNER'S ANSWER ---"


def _sessions(peer=False):
    s1 = {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
          "dispatch": {"requires_human_checkpoint": True, "checkpoint": BRIEF}}
    s2 = {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do"}
    if peer:
        s1["dispatch"]["parallel_group"] = "g"
        s2["dispatch"] = {"parallel_group": "g"}
    return [s1, s2]


@pytest.fixture
def parked(tmp_path, capsys):
    """A plan whose s01 is parked at its pre-dispatch checkpoint (via cmd_checkpoint)."""
    plan_dir = make_plan(tmp_path, _sessions())
    run.cmd_checkpoint(str(plan_dir), "s01")
    capsys.readouterr()
    return plan_dir


def _resume(plan_dir, capsys, *, file=None, no_answer=False, resume=True):
    run.cmd_plan(str(plan_dir), resume, None, False, "claude", (str(file) if file else None, no_answer))
    return json.loads(capsys.readouterr().out)


def _answer_file(plan_dir, text, name="s01.md"):
    d = Path(plan_dir) / "_decisions"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return f


def _lines(plan_dir):
    p = Path(plan_dir) / FILE
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _event(plan_dir, **fields):
    rsi.log_event(plan_dir, "checkpoint_reached", session_ids=["s01"], **fields)


# (a)
def test_answer_file_records_and_the_digest_opens_with_the_block(parked, capsys):
    out = _resume(parked, capsys, file=_answer_file(parked, "Hold. Do it on Friday."))
    assert out["action"] == "dispatch" and out["checkpoint_answer"]["given"] is True
    rows = _lines(parked)
    assert len(rows) == 1 and rows[0]["given"] is True and rows[0]["answer"] == "Hold. Do it on Friday."
    assert rows[0]["answers_checkpoint"] == fd.current_checkpoint(parked, "s01")["ts"]
    log = pm.read_changelog(parked)
    assert log[-1]["op"] == "checkpoint-answer" and "Hold. Do it on Friday." in log[-1]["summary"]
    assert "Hold. Do it on Friday." in (parked / "PLAN.html").read_text()
    d = fd.digest(parked, "s01")
    assert d.startswith(BEGIN + "\n  Hold. Do it on Friday.\n") and END in d


# (b) + (i)
def test_bare_resume_refuses_then_passes_after_an_answer(parked, capsys):
    with pytest.raises(SystemExit) as e:
        _resume(parked, capsys)
    msg = str(e.value)
    assert "--answer-file" in msg and "--no-answer" in msg and "_decisions/s01.md" in msg
    assert "that reply IS the answer" in msg
    assert not (parked / FILE).exists()
    _resume(parked, capsys, file=_answer_file(parked, "yes"))
    before = (parked / FILE).read_bytes()
    out = _resume(parked, capsys)  # a retry: passes, writes nothing
    assert out["action"] == "dispatch" and (parked / FILE).read_bytes() == before


# (c)
def test_no_answer_appends_given_false_and_says_not_agreement(parked, capsys):
    _resume(parked, capsys, no_answer=True)
    row = _lines(parked)[0]
    assert row["given"] is False and row["answer"] is None
    assert "resumed without an answer" in pm.read_changelog(parked)[-1]["summary"]
    d = fd.digest(parked, "s01")
    assert "NOT agreement" in d and "no answer was given" in d


# (d)
def test_no_checkpoint_no_entry_and_digest_unchanged(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, _sessions())
    out = _resume(plan_dir, capsys, file=_answer_file(plan_dir, "x"))
    assert not (plan_dir / FILE).exists()
    assert out["checkpoint_answer_note"] == "--answer-file/--no-answer ignored: no checkpoint was resumed"
    ledger = plan_dir / "_verify_state" / "s09.findings.ndjson"
    ledger.parent.mkdir()
    ledger.write_text(json.dumps({"kind": "finding", "fid": "f", "file": "a.py", "line": 1,
                                  "severity": "high", "summary": "Same as before.", "status": "open"}) + "\n")
    assert fd.digest(plan_dir, "s02").startswith("Findings reviewers already raised")


# (e)
def test_hostile_changelog_does_not_stop_the_digest_or_the_record(parked, capsys):
    (parked / "_changelog.ndjson").write_bytes(b'not json\n[]\nnull\n{"op": "x", "at": "\xff\xfe"}\n\xff\xfe\n')
    assert fd.digest(parked, "s01") == ""
    out = _resume(parked, capsys, file=_answer_file(parked, "ok"))
    assert out["checkpoint_answer"].get("changelog_error") is None
    assert fd.digest(parked, "s01").startswith(BEGIN)
    assert "ok" in (parked / "PLAN.html").read_text()


# (f)
def test_unreadable_answer_lines_give_the_stop_block(parked, capsys):
    _resume(parked, capsys, file=_answer_file(parked, "fine"))
    good = (parked / FILE).read_bytes()
    stamp = _lines(parked)[0]["answers_checkpoint"]
    (parked / FILE).write_bytes(good + b'{"session": "s01", "oops\n')
    assert "could not be read" in fd.digest(parked, "s01")
    bad = json.dumps({"at": rsi._now(), "session": "s01", "answers_checkpoint": stamp,
                      "given": True, "answer": "abé"}, ensure_ascii=False).encode("utf-8").replace(b"\xc3\xa9", b"\xe9")
    (parked / FILE).write_bytes(bad + b"\n")
    d = fd.digest(parked, "s01")
    assert "could not be read" in d and "ab" not in d.split("Stop and flag", 1)[0].split("read:", 1)[1]


# (f2)
def test_concurrent_appends_keep_both_and_a_changelog_rewrite_does_not_erase(parked, capsys):
    code = ("import sys; sys.path.insert(0, {s!r}); import checkpoint_answer as ca; "
            "[ca._append({p!r}, {{'session': 's01', 'n': '%s' + str(i), 'given': True}}) for i in range(200)]")
    procs = [subprocess.Popen([sys.executable, "-c", code.format(s=str(SCRIPTS), p=str(parked)) % tag])
             for tag in ("a", "b")]
    assert [p.wait() for p in procs] == [0, 0]
    rows = _lines(parked)
    assert len(rows) == 400 and sorted(r["n"] for r in rows) == sorted(f"{t}{i}" for t in "ab" for i in range(200))
    (parked / FILE).unlink()
    _resume(parked, capsys, file=_answer_file(parked, "keep me"))
    (parked / "_changelog.ndjson").write_text(json.dumps(
        {"at": rsi._now(), "op": "amend-session", "session": "s02", "summary": "x", "sessions_touched": ["s02"]}) + "\n")
    assert "keep me" in fd.digest(parked, "s01")


# (g)
def test_flag_on_a_non_qualifying_resume_records_nothing(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, _sessions())
    out = _resume(plan_dir, capsys, no_answer=True, resume=False)
    assert "ignored" in out["checkpoint_answer_note"] and not (plan_dir / FILE).exists()


# (h)
def test_second_explicit_answer_wins(parked, capsys):
    _resume(parked, capsys, file=_answer_file(parked, "first"))
    _resume(parked, capsys, file=_answer_file(parked, "second"))
    assert len(_lines(parked)) == 2
    d = fd.digest(parked, "s01")
    assert "second" in d and "first" not in d


# (j)
def test_retire_barred_park_and_older_checkpoint(parked, capsys):
    _resume(parked, capsys, file=_answer_file(parked, "go"))
    assert fd.digest(parked, "s01").startswith(BEGIN)
    # a newer Codex-barred park hides the answer
    _event(parked, reason="barred", harness="codex")
    assert fd.digest(parked, "s01") == ""
    # a NEW pre-dispatch checkpoint: the old answer is bound to the older stamp
    _event(parked, reason="again", kind="pre_dispatch")
    assert fd.digest(parked, "s01") == ""
    with pytest.raises(SystemExit):
        _resume(parked, capsys)
    # answer it, then a redispatch / amend entry retires it
    _resume(parked, capsys, file=_answer_file(parked, "go again"))
    assert fd.digest(parked, "s01").startswith(BEGIN)
    for op in ("redispatch", "amend-session"):
        _resume(parked, capsys, file=_answer_file(parked, "go " + op))
        assert fd.digest(parked, "s01").startswith(BEGIN)
        pm.record_change(parked, op=op, session="s01", summary="x", sessions_touched=["s01"])
        assert fd.digest(parked, "s01") == "", op


# (k)
def test_batch_peer_does_not_get_a_record(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, _sessions(peer=True))
    run.cmd_checkpoint(str(plan_dir), "s01")
    capsys.readouterr()
    out = _resume(plan_dir, capsys, file=_answer_file(plan_dir, "go"))
    assert [b["id"] for b in out["batch"]][0] == "s01"
    assert {r["session"] for r in _lines(plan_dir)} == {"s01"}
    assert fd.digest(plan_dir, "s02") == ""


# (l)
@pytest.mark.parametrize("kind", ["codex", "post", "verifier", "legacy"])
def test_other_parks_do_not_qualify(tmp_path, capsys, kind):
    plan_dir = make_plan(tmp_path, _sessions())
    run.cmd_checkpoint(str(plan_dir), "s01")
    capsys.readouterr()
    # replace the pre_dispatch event with the other flavour of park
    (plan_dir / "run.ndjson").write_text("")
    extra = {"codex": {"harness": "codex", "reason": "barred"},
             "post": {"reason": BRIEF["decision"]},
             "verifier": {"reason": "no reviewer may read this code"},
             "legacy": {"reason": BRIEF["decision"]}}[kind]
    _event(plan_dir, **extra)
    out = _resume(plan_dir, capsys)   # bare resume: behaves as before
    assert out["action"] == "dispatch"
    out = _resume(plan_dir, capsys, no_answer=True)
    assert "ignored" in out["checkpoint_answer_note"] and not (plan_dir / FILE).exists()


# (m)
def test_awkward_text_round_trips_verbatim(parked, capsys):
    text = f"it's $(rm -rf x) fine\n- leading dash\n{END}\nno answer given\n"
    _resume(parked, capsys, file=_answer_file(parked, text))
    row = _lines(parked)[0]
    assert row["answer"] == text and row["given"] is True
    d = fd.digest(parked, "s01")
    body = d.split(BEGIN + "\n", 1)[1].split("Treat it as", 1)[0]
    assert body == "".join("  " + x for x in text.splitlines(True))
    assert d.count("\n" + END) == 1  # only the real marker starts a line
    _resume(parked, capsys, file=_answer_file(parked, "no answer given"))
    assert _lines(parked)[-1]["given"] is True


# (n)
def test_secret_in_answer_is_refused(parked, capsys):
    if not shutil.which("gitleaks"):
        pytest.skip("gitleaks is not installed")
    with pytest.raises(SystemExit) as e:
        _resume(parked, capsys, file=_answer_file(parked, f"key is AWS_ACCESS_KEY_ID = '{_FAKE_AWS_KEY}'\n"))
    assert _FAKE_AWS_KEY not in str(e.value) and not (parked / FILE).exists()


# (o)
def test_missing_gitleaks_is_refused(parked, capsys, monkeypatch):
    monkeypatch.setattr(egress, "_text_hit", lambda text, name: egress._NO_GITLEAKS)
    with pytest.raises(SystemExit) as e:
        _resume(parked, capsys, file=_answer_file(parked, "go"))
    assert "gitleaks" in str(e.value) and not (parked / FILE).exists()


# (p)
@pytest.mark.parametrize("content", [b"", b"  \n\t\n", b"\xff\xfe bad"])
def test_empty_or_undecodable_file_is_refused(parked, capsys, content):
    with pytest.raises(SystemExit):
        _resume(parked, capsys, file=_answer_file(parked, content))
    assert not (parked / FILE).exists()
    with pytest.raises(SystemExit):
        _resume(parked, capsys, file=parked / "_decisions" / "missing.md")


# (q)
def test_resume_hints_name_the_flags(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, _sessions())
    act = _resume(plan_dir, capsys, resume=False)
    assert act["action"] == "checkpoint"
    assert "--answer-file" in act["resume_with"] and "--no-answer" in act["resume_with"]
    assert "_decisions/s01.md" in act["resume_with"]
    run.cmd_checkpoint(str(plan_dir), "s01")
    out = json.loads(capsys.readouterr().out)
    assert f"{plan_dir}/_decisions/s01.md" in out["resume_with"] and "--answer-file" in out["resume_with"]
    assert json.loads((plan_dir / "run.ndjson").read_text().splitlines()[-1])["kind"] == "pre_dispatch"


def test_module_exports():
    assert callable(ca.handle) and fd.RETIRING_OPS >= {"redispatch", "amend-session"}
