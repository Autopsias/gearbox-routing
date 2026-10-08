"""Unit fixtures for the CODEX reviewer backend, against a recorded `codex` shim.

WHAT A SHIM CAN AND CANNOT PROVE. It proves the WIRING — that the gate builds the
right command, refuses the right things, and turns a schema'd answer into the
same three exit codes the Claude path produces. It cannot prove the reviewer
finds real defects; s01b owns the live `codex exec` run, the neuter probes and
the end-to-end restricted tree.

Every fixture tree is built under `tmp_path`. Nothing here plants a file in the
shared checkout — a planted failure in a tree other sessions are working in
becomes THEIR gate's finding (paid for 2026-08-20).
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

import codex_review_backend as crb
import llm_review_gate as g
import llm_review_ledger as ledger

# The shim answers three different invocations: `--version`, `login status`, and
# the review itself. It records EVERY argv, so a test can assert that exactly one
# review was started and with which sandbox.
_SHIM = '''#!/usr/bin/env python3
import json, os, pathlib, sys
argv = sys.argv[1:]
with open(os.environ["CODEX_SHIM_ARGV"], "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
if argv[:1] == ["--version"]:
    print("codex-cli 0.147.0-shim"); raise SystemExit(0)
if argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); raise SystemExit(0)
sys.stdin.read()                      # the prompt; a reader that never reads deadlocks
print(json.dumps({"type": "thread.started", "thread_id": "th_shim_1"}))
print(json.dumps({"type": "item.completed"}))
payload = os.environ.get("CODEX_SHIM_OUT", "")
if payload and "-o" in argv:
    pathlib.Path(argv[argv.index("-o") + 1]).write_text(payload)
raise SystemExit(int(os.environ.get("CODEX_SHIM_RC", "0")))
'''


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    """The supervisor polls output growth every 10s. Real runs take minutes, so
    that is right there and wrong here: it would add 10s of dead wait to every
    test below."""
    monkeypatch.setattr(crb._supervisor(), "POLL_S", 0.02)


@pytest.fixture
def shim(tmp_path, monkeypatch):
    """A recorded `codex` on PATH. -> the argv-record path."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "codex"
    exe.write_text(_SHIM)
    exe.chmod(0o755)
    argv_file = tmp_path / "codex-argv.ndjson"
    monkeypatch.setenv("CODEX_SHIM_ARGV", str(argv_file))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return argv_file


def _claude_shim(tmp_path, monkeypatch):
    """A recorded `claude` in the SAME bin dir as the recorded `codex`.

    The on-box reviewer has to be observable for the same reason the codex one
    does: "no automated Claude review ran" is only a measurement if the thing
    that would have recorded one is standing there, on PATH, able to answer.
    """
    rec = tmp_path / "claude-argv.ndjson"
    exe = tmp_path / "bin" / "claude"
    exe.write_text('#!/bin/sh\necho "$1" >> "$CLAUDE_SHIM_ARGV"\ncat <<\'EOF\'\n'
                   + json.dumps({"type": "result", "is_error": False,
                                 "result": "REVIEWED_FILES: 2\n\n```json\n[]\n```"})
                   + '\nEOF\n')
    exe.chmod(0o755)
    monkeypatch.setenv("CLAUDE_SHIM_ARGV", str(rec))
    return rec


_REAL_EGRESS = crb.egress_reason
_REAL_TEXT_EGRESS = crb.text_egress_reason


@pytest.fixture(autouse=True)
def no_egress_block(monkeypatch):
    """The egress guard has its own tests below (and s01b's end-to-end one). Here
    it is pinned OPEN so these fixtures measure the reviewer, not gitleaks — the
    scanner is a machine dependency, and a suite that silently degraded to
    on_box_human on a box without it would report green while testing nothing."""
    monkeypatch.setattr(crb, "egress_reason", lambda cwd: None)
    monkeypatch.setattr(crb, "text_egress_reason", lambda cwd, texts: None)


@pytest.fixture
def real_text_egress(monkeypatch):
    """Opt back IN to the TEXT half only -- the diff, prompt and intent scan."""
    monkeypatch.setattr(crb, "text_egress_reason", _REAL_TEXT_EGRESS)


@pytest.fixture
def real_egress(monkeypatch):
    """Opt back IN to the real guard. Requested fixtures are set up after autouse
    ones at the same scope, so this undoes the pin above for the two tests that
    are about the guard itself."""
    monkeypatch.setattr(crb, "egress_reason", _REAL_EGRESS)
    monkeypatch.setattr(crb, "text_egress_reason", _REAL_TEXT_EGRESS)


def _tree(tmp_path):
    """A real git work tree with two committed-then-modified files."""
    tree = tmp_path / "tree"
    tree.mkdir()
    def run(*a):
        subprocess.run(list(a), cwd=tree, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tree / "a.py").write_text("x = 1\n")
    (tree / "b.py").write_text("y = 1\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (tree / "a.py").write_text("x = 2  # the planted defect lives here\n")
    (tree / "b.py").write_text("y = 2\n")
    return tree


def _plan(tmp_path, sid="s01", name="plan"):
    plan = tmp_path / name
    (plan / "_verify_state").mkdir(parents=True)
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["%s"], '
        '"ts": "2026-08-25T00:00:00+00:00"}\n' % sid)
    return plan


def _answer(findings, reviewed=("a.py", "b.py"), verdict=None):
    return json.dumps({
        "verdict": verdict or ("FINDINGS" if findings else "PASS"),
        "reviewed": list(reviewed), "findings": findings})


_DEFECT = [{"file": "a.py", "line": 1, "severity": "high",
            "title": "planted defect", "why": "The value is read before it is set."}]


def _gate(tree, plan, sid="s01", level="medium", timeout=30):
    return g.main(["--level", level, "--reviewer", "codex", "--cwd", str(tree),
                   "--plan-dir", str(plan), "--session", sid, "--timeout", str(timeout)])


def _reviews(argv_file):
    """Only the REVIEW invocations — the probes (`--version`, `login status`) are
    recorded too, and counting them as reviews would hide a second spawn."""
    lines = argv_file.read_text().splitlines() if argv_file.exists() else []
    return [json.loads(ln) for ln in lines if "--output-schema" in json.loads(ln)]


# --------------------------------------------------------------------------
# (a) a planted defect -> FINDINGS, and the command that was actually run
# --------------------------------------------------------------------------
def test_a_planted_defect_fails_the_gate_and_names_the_file(tmp_path, monkeypatch,
                                                            shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer(_DEFECT))
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.FINDINGS, out
    assert "a.py" in out and "planted defect" in out

    runs = _reviews(shim)
    assert len(runs) == 1, runs           # ONE review, not a silent retry
    argv = runs[0]
    assert argv[0] == "exec", argv
    assert 'sandbox_mode="read-only"' in argv, argv
    assert "--output-schema" in argv and "--skip-git-repo-check" in argv, argv
    # The reviewed tree must not configure its own reviewer (see `crb.flags`).
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv, argv
    assert crb.MODEL in argv, argv
    # level medium -> effort high (a review reads harder than the work it reviews)
    assert f"model_reasoning_effort={crb.EFFORT['medium']}" in argv, argv
    schema = json.loads(open(argv[argv.index("--output-schema") + 1]).read())
    assert schema["properties"]["verdict"]["enum"] == ["PASS", "FINDINGS"]
    # Printed so the evidence artifact carries the RECORDED argv, not a retyped
    # one -- `pytest -s` is how the gate's command line leaves this suite.
    print("RECORDED CODEX ARGV: codex " + " ".join(argv))


def test_line_one_is_the_reviewer_identity_and_the_markers_are_prefixed(
        tmp_path, monkeypatch, shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    _gate(_tree(tmp_path), _plan(tmp_path))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("[llm-review-gate] reviewer:")
    assert "family=codex" in lines[0] and crb.MODEL in lines[0]
    assert "0.147.0-shim" in lines[0], "the identity must name the BINARY that ran"
    markers = {ln.split(":")[0] for ln in lines if ln.split(":")[0].isupper()}
    assert {"VERIFIER", "REVIEWED_FILES"} <= markers, lines[:10]
    # The attested count is the PREPARED count -- 2 changed files -- and never
    # the number the reviewer claims for itself.
    assert "REVIEWED_FILES: 2" in lines


# --------------------------------------------------------------------------
# (b) the control: a clean review passes
# --------------------------------------------------------------------------
def test_a_clean_review_passes(tmp_path, monkeypatch, shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    assert rc == g.PASS, capsys.readouterr().out
    assert len(_reviews(shim)) == 1


# --------------------------------------------------------------------------
# (c) exit 0 with no last message is NOT a pass
# --------------------------------------------------------------------------
def test_codex_exiting_0_without_writing_the_output_file_is_INDETERMINATE(
        tmp_path, monkeypatch, shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", "")       # exits 0, writes nothing
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    err = capsys.readouterr().err
    assert rc == g.INDETERMINATE
    assert "no usable last message" in err, err


def test_a_NON_ZERO_codex_exit_is_INDETERMINATE_even_with_output(
        tmp_path, monkeypatch, shim, capsys):
    """codex writes the last-message file LAST, so a well-formed answer beside a
    failed exit is a partial one -- and a partial review scored as a review is
    the silent green this gate exists to refuse."""
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    monkeypatch.setenv("CODEX_SHIM_RC", "1")
    assert _gate(_tree(tmp_path), _plan(tmp_path)) == g.INDETERMINATE
    assert "exit=1" in capsys.readouterr().err


# A shim whose TWO invocations differ. `_SHIM` above fails (or succeeds) the same
# way every time, which cannot express the interesting case: one attempt writing
# the `-o` file and the NEXT one exiting clean without touching it.
_SHIM_PREAMBLE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
argv = sys.argv[1:]
with open(os.environ["CODEX_SHIM_ARGV"], "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
if argv[:1] == ["--version"]:
    print("codex-cli 0.147.0-shim"); raise SystemExit(0)
if argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); raise SystemExit(0)
sys.stdin.read()                      # the prompt; a reader that never reads deadlocks
print(json.dumps({"type": "thread.started", "thread_id": "th_shim_1"}))
'''

#: attempt 1 writes a PERFECT answer and then exits 1; attempt 2 exits 0 having
#: written nothing at all.
_SHIM_WRITE_THEN_FAIL = _SHIM_PREAMBLE + '''
counter = pathlib.Path(os.environ["CODEX_SHIM_COUNT"])
n = len(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text("x" * n)
if n == 1:
    pathlib.Path(argv[argv.index("-o") + 1]).write_text(os.environ["CODEX_SHIM_OUT"])
    raise SystemExit(1)
raise SystemExit(0)
'''

#: every invocation refuses the way the API refuses when the account is out of
#: budget -- the string `_QUOTA` matches, in the JSONL event log it matches on.
_SHIM_QUOTA = _SHIM_PREAMBLE + '''
print(json.dumps({"type": "error", "message": "You have hit your usage limit."}))
raise SystemExit(1)
'''

#: a run that fails for an ORDINARY transport reason, having emitted nothing but
#: the usage event every turn ends with. `"input_tokens": 4291` contains `429`.
_SHIM_TOKENS_429 = _SHIM_PREAMBLE + '''
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": 4291, "output_tokens": 17}}))
raise SystemExit(1)
'''


def _reshim(tmp_path, body):
    """Swap the `shim` fixture's recorded `codex` for a different script."""
    exe = tmp_path / "bin" / "codex"
    exe.write_text(body)
    exe.chmod(0o755)
    return exe


def test_a_LEFTOVER_output_file_from_a_FAILED_attempt_is_never_the_NEXT_answer(
        tmp_path, monkeypatch, shim, capsys):
    """The two attempts inside `spawn` share ONE `-o` path.

    So a well-formed answer written by an attempt that then exited NON-ZERO is
    still sitting there when the next attempt exits 0 without writing anything —
    and `rc == 0 and out.exists()` reads the leftover as attempt 2's review. It
    is the same silent green the test above refuses, reached by a route that test
    cannot see: its shim fails on EVERY invocation, so no clean exit ever
    follows a dirty one.
    """
    _reshim(tmp_path, _SHIM_WRITE_THEN_FAIL)
    monkeypatch.setenv("CODEX_SHIM_COUNT", str(tmp_path / "invocations"))
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))     # a PASS, if it counted
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    cap = capsys.readouterr()
    assert rc == g.INDETERMINATE, cap.out
    assert len(_reviews(shim)) >= 2, "the fixture must reach the second attempt"
    assert "no usable last message" in cap.err, cap.err


def test_a_QUOTA_refusal_emits_ONE_self_consistent_verifier(tmp_path, monkeypatch,
                                                            shim, capsys):
    """A usage-limit refusal is an UNAVAILABLE reviewer, and says so once.

    The gate retries, so a marker printed on the refusal path is printed twice;
    and `VERIFIER: cross_family` announced before the attempt would be the FIRST
    prefix match a consumer finds — naming a cross-family verifier for a run
    where no review happened. The markers are the machine-read facts (see the
    module docstring), so they have to agree with each other and with the
    `.degraded.json` stamp beside them.
    """
    _reshim(tmp_path, _SHIM_QUOTA)
    plan = _plan(tmp_path)
    rc = _gate(_tree(tmp_path), plan)
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE, out
    assert [ln for ln in out.splitlines() if ln.startswith("VERIFIER:")] == [
        "VERIFIER: on_box_human"], out
    assert [ln for ln in out.splitlines() if ln.startswith("DEGRADED_FROM:")] == [
        "DEGRADED_FROM: cross_family_unavailable"], out
    stamp = plan / "_verify_state" / "s01.medium.codex.degraded.json"
    rec = json.loads(stamp.read_text())
    assert rec["degraded_from"] == "cross_family_unavailable"
    assert rec["verifier"] == "on_box_human"
    assert "usage/quota" in rec["reason"], rec
    assert [r["kind"] for r in ledger.load(str(plan), "s01.medium.codex")] == ["reviewer"]


def test_a_TOKEN_COUNT_that_contains_429_is_NOT_a_quota_refusal(tmp_path, monkeypatch,
                                                                shim, capsys):
    """`429` inside `"input_tokens": 4291` is a number, not an HTTP status.

    Searched as text, the event log turned an ordinary transport failure into a
    refusal that never happened: `DEGRADED_FROM: cross_family_unavailable`, a
    `.degraded.json` stamp saying the reviewer was unavailable, and — because a
    degrade is final — the gate's own second attempt skipped. Codex ran and
    answered unusably here, so the honest disposition is INDETERMINATE under the
    cross-family verifier that did run.
    """
    _reshim(tmp_path, _SHIM_TOKENS_429)
    plan = _plan(tmp_path)
    rc = _gate(_tree(tmp_path), plan)
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE, out
    assert "DEGRADED_FROM" not in out, out
    assert [ln for ln in out.splitlines() if ln.startswith("VERIFIER:")] == [
        "VERIFIER: cross_family"], out
    assert not (plan / "_verify_state" / "s01.medium.codex.degraded.json").exists()
    # AND THE RETRY SURVIVES: two gate attempts, two spawn attempts inside each.
    # A degrade short-circuits the second gate attempt, which is what makes this
    # count the difference between the two dispositions -- it reads 2 there.
    assert len(_reviews(shim)) == 4, _reviews(shim)


def test_the_SOURCE_UNDER_REVIEW_in_the_event_log_is_NOT_a_quota_refusal(tmp_path):
    """codex streams the `aggregated_output` of every command the reviewer runs.

    So a review of THESE scripts writes the gate's own quota pattern into the log
    the gate then reads -- 10 KB of one file's text in a recorded run
    (_plans/memo-loop-skill-2026-08-04/_evidence/s02, 44 command_execution items)
    -- and a benign `error` item sits beside it, verbatim from that same run.
    Neither is a refusal. The two known positives below are.
    """
    log = tmp_path / "events.jsonl"
    noise = [
        {"type": "item.completed", "item": {
            "id": "item_3", "type": "command_execution",
            "command": "/bin/zsh -lc \"sed -n '1,80p' codex_review_events.py\"",
            "aggregated_output": '_QUOTA = re.compile(r"usage limit|quota|429")',
            "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {
            "id": "item_0", "type": "error",
            "message": "clamping SessionEnd hook timeout to 3s in hooks.json"}},
        {"type": "turn.completed", "usage": {"input_tokens": 4291, "output_tokens": 17}},
    ]
    log.write_text("".join(json.dumps(e) + "\n" for e in noise))
    assert crb.quota_hit(log) is False, log.read_text()

    # KNOWN POSITIVE 1: the refusal itself, in the same file, as an error record.
    with open(log, "a") as fh:
        fh.write(json.dumps({"type": "error",
                             "message": "You have hit your usage limit."}) + "\n")
    assert crb.quota_hit(log) is True
    # KNOWN POSITIVE 2: the refusal carried as an error PAYLOAD rather than an
    # error type. A `null` one is not a refusal -- that is the last line here.
    log.write_text(json.dumps({"type": "turn.failed",
                               "error": {"code": "insufficient_quota"}}) + "\n")
    assert crb.quota_hit(log) is True
    # KNOWN POSITIVE 3: a CLI-level refusal arrives as plain stderr with no event
    # around it -- the supervisor merges stderr into this same file.
    log.write_text("error: 429 Too Many Requests\n")
    assert crb.quota_hit(log) is True
    # ...and the same token count under a null error payload is still not one.
    log.write_text(json.dumps({"type": "turn.completed", "error": None,
                               "usage": {"input_tokens": 4291}}) + "\n")
    assert crb.quota_hit(log) is False


def test_an_unparseable_last_message_is_INDETERMINATE(tmp_path, monkeypatch, shim,
                                                      capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", "I reviewed everything and it looks fine.")
    assert _gate(_tree(tmp_path), _plan(tmp_path)) == g.INDETERMINATE
    assert "not a JSON object" in capsys.readouterr().err


def test_verdict_FINDINGS_with_an_empty_array_is_INDETERMINATE(tmp_path, monkeypatch,
                                                               shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], verdict="FINDINGS"))
    assert _gate(_tree(tmp_path), _plan(tmp_path)) == g.INDETERMINATE
    assert "EMPTY findings array" in capsys.readouterr().err


# --------------------------------------------------------------------------
# (d) the attestation — the check that can actually fail
# --------------------------------------------------------------------------
def test_a_reviewed_list_SHORTFALL_is_INDETERMINATE_and_names_the_missing_path(
        tmp_path, monkeypatch, shim, capsys):
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], reviewed=["a.py"]))
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    err = capsys.readouterr().err
    assert rc == g.INDETERMINATE
    assert "SHORTFALL" in err and "b.py" in err, err


def test_the_attestation_is_not_a_tautology():
    """The known negative for the check above: it must PASS a reviewer that read
    the surface (including one that spells the paths `./x`), and FAIL one that
    did not. A comparison that cannot fail is the same as no comparison."""
    assert crb.attest(["a.py", "b.py"], ["./b.py", "a.py", "extra.py"]) == []
    assert crb.attest(["a.py", "b.py"], ["a.py"]) == ["b.py"]
    assert crb.attest(["a.py", "b.py"], []) == ["a.py", "b.py"]


#: THE REAL BINARY ANSWERS `login status` ON STDERR (measured, codex-cli
#: 0.147.0; `--version` answers on stdout). Every shim above prints both to
#: stdout, which is why no shim could see the availability probe reading stdout
#: alone -- and calling a logged-in box logged out.
_SHIM_STDERR_LOGIN = _SHIM.replace(
    'print("Logged in using ChatGPT")',
    'print("Logged in using ChatGPT", file=sys.stderr)')


def test_a_login_status_answered_on_STDERR_still_reads_as_LOGGED_IN(
        tmp_path, monkeypatch, shim, capsys):
    """The bug the live smoke found, and no shim fixture could.

    `availability()` read the probe's STDOUT only. The real `codex login status`
    prints "Logged in using ChatGPT" on STDERR and exits 0, so the probe came
    back empty, every box looked logged out, and the cross-family reviewer
    degraded to `on_box_human` permanently — with a green suite the whole time,
    because the shims answered where the tests had told them to.
    """
    _reshim(tmp_path, _SHIM_STDERR_LOGIN)
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    rc = _gate(_tree(tmp_path), _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.PASS, out
    assert "DEGRADED_FROM" not in out, out
    assert len(_reviews(shim)) == 1
    # The KNOWN NEGATIVES for this probe -- silence, and a logged-OUT answer -- are
    # in test_codex_review_availability.py, which needs none of the gate fixtures.


# --------------------------------------------------------------------------
# (e) no codex on this box -> the on-box disposition, never a retry loop
# --------------------------------------------------------------------------
def test_codex_absent_from_PATH_degrades_to_the_on_box_path(tmp_path, monkeypatch,
                                                            capsys):
    tools = {os.path.dirname(shutil.which(t)) for t in ("git", "python3")}
    monkeypatch.setenv("PATH", os.pathsep.join(sorted(tools)))
    assert shutil.which("codex") is None, "the fixture must actually remove codex"
    plan = _plan(tmp_path)
    rc = _gate(_tree(tmp_path), plan)
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE
    assert "DEGRADED_FROM: cross_family_unavailable" in out, out
    assert "VERIFIER: on_box_human" in out, out
    stamp = plan / "_verify_state" / "s01.medium.codex.degraded.json"
    assert json.loads(stamp.read_text())["degraded_from"] == "cross_family_unavailable"
    # NO VERDICT of any kind was written: a degrade is not a review.
    recs = ledger.load(str(plan), "s01.medium.codex")
    assert [r["kind"] for r in recs] == ["reviewer"], recs


def test_a_restricted_tree_starts_NO_reviewer_of_EITHER_family(
        tmp_path, monkeypatch, shim, real_egress, capsys):
    """GATE-02 end to end, through the REAL guard, with BOTH families on PATH.

    A `.env` file is the filename rule, which fires before the content scan, so
    the refusal half holds on a box without gitleaks too.

    And the disposition is `on_box_human`, NOT a quiet re-run of the Claude
    reviewer: the SSOT's restricted_repos clause wants an explicit
    VERIFIED-ON-BOX or BLOCKED, and a fall-back that can return 0 would put
    Claude back in front of Claude's own code under a degrade stamp. So a
    recorded `claude` stands beside the recorded `codex` here, and its silence
    is measured rather than assumed.

    BOTH PAIRED POSITIVES ARE IN THIS TEST, not in siblings. "Zero invocations"
    is worth nothing on its own — a shim that was never on PATH, or one that
    cannot run at all, records zero the same way — and a sibling that proves
    otherwise can skip, or be deleted, taking the meaning of this one with it.
    """
    tree = _tree(tmp_path)
    (tree / ".env").write_text("SOMETHING=1\n")
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(tree))
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    claude = _claude_shim(tmp_path, monkeypatch)
    plan = _plan(tmp_path)

    rc = _gate(tree, plan)
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE, out
    assert "DEGRADED_FROM: cross_family" in out and "VERIFIER: on_box_human" in out
    assert ".env" in out, "the stamp must name the guard's own reason"
    # NOTHING WAS SENT. The identity line probes `codex --version` before the
    # guard is consulted — a probe that ships no tree anywhere — so what is
    # asserted is that the REVIEW, the invocation that WOULD, never started.
    assert _reviews(shim) == [], "a restricted tree must not start a codex review"
    recorded = [json.loads(ln) for ln in shim.read_text().splitlines()]
    assert recorded == [["--version"]], recorded
    assert not claude.exists(), "no automated Claude review may run here either"
    assert json.loads((plan / "_verify_state" / "s01.medium.codex.degraded.json")
                      .read_text())["degraded_from"] == "cross_family"
    # NO VERDICT of any kind was written: a degrade is not a review.
    recs = ledger.load(str(plan), "s01.medium.codex")
    assert [r["kind"] for r in recs] == ["reviewer"], recs

    # PAIRED POSITIVE 1 — the SAME shim, the SAME PATH, the tree no longer
    # restricted: it records a review and the gate returns a verdict.
    (tree / ".env").unlink()
    if shutil.which("gitleaks") is None:
        # The filename half needs no scanner; clearing a CLEAN tree does, and a
        # skipped positive is a lost pairing. Pinned open for this half only —
        # the refusal above ran through the real guard either way.
        monkeypatch.setattr(crb, "egress_reason", lambda cwd: None)
    rc2 = _gate(tree, _plan(tmp_path, name="plan2"))
    out2 = capsys.readouterr().out
    assert rc2 == g.PASS, out2
    assert len(_reviews(shim)) == 1
    assert not claude.exists(), "the codex reviewer must not call claude either"

    # PAIRED POSITIVE 2 — and the `claude` shim is not inert: it records an
    # invocation the moment it IS the reviewer asked to run.
    rc3 = g.main(["--level", "medium", "--cwd", str(tree), "--session", "s01",
                  "--plan-dir", str(_plan(tmp_path, name="plan3")), "--timeout", "30"])
    assert rc3 == g.PASS, capsys.readouterr().out
    assert claude.read_text().split() == ["-p"], claude.read_text()
    # `pytest -rP` is how these leave the suite as evidence, the way the RECORDED
    # ARGV above does -- the three phases side by side are the whole pairing.
    print("RESTRICTED  markers: " + " | ".join(
        ln for ln in out.splitlines() if ln.startswith(("VERIFIER:", "DEGRADED_FROM:"))))
    print(f"RESTRICTED  rc={rc} codex_argv={recorded} claude=ABSENT "
          f"ledger={[r['kind'] for r in recs]}")
    print(f"CLEARED     rc={rc2} codex_reviews={len(_reviews(shim))} claude=ABSENT")
    print(f"CLAUDE PATH rc={rc3} claude_argv={claude.read_text().split()}")


# --------------------------------------------------------------------------
# The rework loop — TWO attempts, and the line moves under the fix
# --------------------------------------------------------------------------
def _rework(tmp_path, monkeypatch, second):
    """Attempt 1 reports the planted defect; the session then fixes it in a way
    that MOVES THE LINE, and attempt 2 answers `second`.

    Attempt 2's surface is narrowed to the fix delta, so `a.py` is the only path
    prepared — which is exactly what makes the inference below evidence.
    -> (rc1, rc2, ledger records).
    """
    tree, plan = _tree(tmp_path), _plan(tmp_path)
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer(_DEFECT))
    rc1 = _gate(tree, plan)
    (tree / "a.py").write_text("# the fix adds a guard,\n# two lines of it,\nx = 1\n")
    monkeypatch.setenv("CODEX_SHIM_OUT", second)
    rc2 = _gate(tree, plan)
    return rc1, rc2, ledger.load(str(plan), "s01.medium.codex")


def _record(out, recs, rc1, rc2):
    """What the two attempts observed, for `pytest -rP` to carry out as evidence."""
    print(f"REWORK exit codes: attempt1={rc1} attempt2={rc2}")
    for ln in out.splitlines():
        if "convergence:" in ln:
            print("  " + ln.strip())
    for r in recs:
        if r["kind"] == "finding":
            print(f"  ledger attempt={r['attempt']} line={r.get('line')} "
                  f"status={r['status']} fid={r['fid'][:12]}")


def test_a_prior_is_CLEARED_by_a_clean_attempt_2_and_the_session_PASSES(
        tmp_path, monkeypatch, shim, capsys):
    """WITHOUT THIS THE REWORK LOOP CANNOT TERMINATE for the Codex reviewer.

    The findings schema has no field in which a reviewer can say a prior is
    fixed — a Claude reviewer writes `"prior": "fixed"`, Codex cannot — so a
    prior raised in attempt 1 would be carried OPEN by `judge()` for the rest of
    the session no matter what the session did, and the gate could never return
    0. `adapt` infers it instead, from the one fact the gate verifies for itself:
    the path was PREPARED, the reviewer ATTESTED to reading it, and it did not
    re-report the finding.
    """
    rc1, rc2, recs = _rework(tmp_path, monkeypatch, _answer([], reviewed=["a.py"]))
    out = capsys.readouterr().out
    assert (rc1, rc2) == (g.FINDINGS, g.PASS), out
    assert "surface NARROWED to the fix delta" in out, "attempt 2 IS the fix delta"
    assert "prior_fixed=1" in out and "prior_open=0" in out, out
    second = [r for r in recs if r["kind"] == "finding" and r["attempt"] == 2]
    assert [r["status"] for r in second] == ["fixed"], second
    # ONE id across both attempts. The fix moved the code two lines down, and
    # `fingerprint` excludes the line for exactly this reason.
    assert len({r["fid"] for r in recs if r["kind"] == "finding"}) == 1, recs
    _record(out, recs, rc1, rc2)


def test_the_SAME_defect_re_reported_at_a_DRIFTED_line_is_the_SAME_open_prior(
        tmp_path, monkeypatch, shim, capsys):
    """The known negative for the test above, and the reason the line drifts.

    If the drift made the re-report look like a NEW finding, the ledger would
    carry two ids for one defect and mark the original `fixed` in the same
    round — a session that changed nothing would read as progress. Re-reported
    means still open, at whatever line it now sits on.
    """
    drifted = _answer([dict(_DEFECT[0], line=3)], reviewed=["a.py"])
    rc1, rc2, recs = _rework(tmp_path, monkeypatch, drifted)
    out = capsys.readouterr().out
    assert (rc1, rc2) == (g.FINDINGS, g.FINDINGS), out
    assert "prior_open=1" in out and "prior_fixed=0" in out, out
    second = [r for r in recs if r["kind"] == "finding" and r["attempt"] == 2]
    assert [r["status"] for r in second] == ["open"], second
    assert len({r["fid"] for r in recs if r["kind"] == "finding"}) == 1, recs
    _record(out, recs, rc1, rc2)


# --------------------------------------------------------------------------
# The ledger adapter
# --------------------------------------------------------------------------
def test_two_findings_in_ONE_file_get_two_fingerprints(tmp_path, monkeypatch, shim,
                                                       capsys):
    """`fingerprint()` is file + summary with the LINE excluded, so an empty or
    constant summary collapses every finding in a file onto one id — fix one and
    the ledger believes all of them are fixed."""
    two = [dict(_DEFECT[0], title="first defect"),
           dict(_DEFECT[0], line=9, title="second defect", why="A different bug.")]
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer(two))
    plan = _plan(tmp_path)
    assert _gate(_tree(tmp_path), plan) == g.FINDINGS, capsys.readouterr().out
    fids = {r["fid"] for r in ledger.load(str(plan), "s01.medium.codex")
            if r["kind"] == "finding"}
    assert len(fids) == 2, fids


def test_a_prior_is_matched_by_CONTENT_and_cleared_only_when_the_file_was_read():
    """Codex cannot emit a `prior_id` under the schema, so the adapter matches on
    the ledger's own fingerprint. Re-reported -> still open; read and not
    re-reported -> fixed; never shown -> neither (judge carries it)."""
    class _Led:
        plan_dir = session = ""
        priors = [{"fid": None, "file": "a.py", "summary": "planted defect: The value "
                                                           "is read before it is set."}]
        hashes = {"a.py": "h", "b.py": "h"}
    _Led.priors[0]["fid"] = ledger.fingerprint(_Led.priors[0])

    same = crb.adapt({"findings": _DEFECT}, ["a.py"], _Led, "medium")
    entry = [f for f in json.loads(same.split("```json")[1].split("```")[0])
             if f.get("prior_id")]
    assert entry and entry[0]["prior"] == "open", same

    gone = crb.adapt({"findings": []}, ["a.py"], _Led, "medium")
    entry = json.loads(gone.split("```json")[1].split("```")[0])
    assert entry == [{"prior_id": _Led.priors[0]["fid"], "prior": "fixed",
                      "summary": "not re-reported over the attested surface"}]

    # KNOWN NEGATIVE: a prior in a file this attempt never showed the reviewer is
    # NOT cleared by silence about it.
    _Led.hashes = {"b.py": "h"}
    assert json.loads(crb.adapt({"findings": []}, ["b.py"], _Led, "medium")
                      .split("```json")[1].split("```")[0]) == []


def test_a_prior_over_a_file_the_reviewer_was_never_SHOWN_is_not_cleared():
    """The shown set is `prepared`, not the ledger's surface.

    `led.hashes` counts every path in the surface, including the captured run
    output `prepared_paths` deliberately drops -- listed for the reviewer, never
    deep-read. Inferring "fixed" from `hashes` therefore retires a live finding
    over a file nobody was asked to open, and the attestation the inference
    leans on covers `prepared` and only `prepared`. `judge` must still see this
    prior as OPEN, which is what makes the gate fail rather than pass.
    """
    class _Led:
        plan_dir = session = ""
        floor = records = None
        attempt, delta = 2, {"a.py"}
        priors = [{"fid": None, "file": "_evidence/run.log", "severity": "high",
                   "summary": "leaked credential: the receipt carries a live key."}]
        hashes = {"a.py": "h", "_evidence/run.log": "h"}
    _Led.priors[0]["fid"] = ledger.fingerprint(_Led.priors[0])

    text = crb.adapt({"findings": []}, ["a.py"], _Led, "medium")
    assert json.loads(text.split("```json")[1].split("```")[0]) == [], text
    fail, counts, _ = ledger.judge(_Led, ledger.findings_array(text))
    assert (fail, counts["prior_fixed"], counts["prior_open"]) == (True, 0, 1), counts


def test_findings_text_cannot_break_the_fence_the_gate_parses():
    """A `]` in the reviewer's prose used to end the gate's non-greedy fence match
    early, turning a real review into INDETERMINATE."""
    f = [{"file": "a.py", "line": 2, "severity": "high", "title": "bad [index]",
          "why": "`arr[0]` is read when the list is empty. Second sentence."}]
    text = crb.adapt({"findings": f}, ["a.py"], None, "low")
    verdict, count, _ = g.classify(text)
    assert (verdict, count) == ("findings", 1), text
    assert g.reviewed_count(text) == 1
    assert "bad (index)" in ledger.findings_array(text)[0]["summary"]


# --------------------------------------------------------------------------
# V-1 — the ledger key carries the REVIEWER
# --------------------------------------------------------------------------
def test_the_two_REVIEWERS_do_not_share_a_ledger(tmp_path, monkeypatch, capsys):
    """`cross-family-review-medium` and `llm-review-medium` share the LEVEL
    string, and `land` re-runs the union of every session's gates. On a shared
    ledger the second family to run would read the first's surface record, see no
    file changed since it, and exit INDETERMINATE in ~1.5s without reviewing —
    the 969feba bug one axis over, and a cross-family gate that never runs is
    exactly what this pair of gates exists to make impossible."""
    tree, plan = _tree(tmp_path), _plan(tmp_path)
    bindir = tmp_path / "cbin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text('#!/bin/sh\ncat <<\'EOF\'\n' + json.dumps(
        {"type": "result", "is_error": False,
         "result": "REVIEWED_FILES: 2\n\n```json\n[]\n```"}) + '\nEOF\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert g.main(["--level", "medium", "--cwd", str(tree), "--plan-dir", str(plan),
                   "--session", "s01", "--timeout", "30"]) == g.PASS
    assert (plan / "_verify_state" / "s01.medium.claude.findings.ndjson").exists()

    surface = ["a.py", "b.py"]
    codex_ctx = ledger.context(str(plan), "s01.medium.codex", str(tree), surface)
    assert codex_ctx.attempt == 1, "the codex gate must review, not inherit"
    assert ledger.narrow_to_delta(codex_ctx, surface, [])[0] is None

    # KNOWN NEGATIVE for the same guard: the SAME reviewer re-reading its OWN
    # unchanged surface must still refuse, or this test would pass on a gate that
    # simply never converges.
    claude_ctx = ledger.context(str(plan), "s01.medium.claude", str(tree), surface)
    assert claude_ctx.attempt == 2
    assert ledger.narrow_to_delta(claude_ctx, surface, [])[0] == set()


def test_the_claude_path_is_unchanged_by_the_flag(tmp_path, monkeypatch):
    """`--reviewer claude` is the default and stays the old path: same reviewer
    call, same exit codes, no codex process, no probe of the reviewer binary."""
    calls = []
    monkeypatch.setattr(g, "run_once", lambda p, c, t: (
        calls.append(p) or ({"result": "REVIEWED_FILES: 2\n\n```json\n[]\n```"}, "")))
    tree, plan = _tree(tmp_path), _plan(tmp_path)
    assert g.main(["--level", "low", "--cwd", str(tree), "--plan-dir", str(plan),
                   "--session", "s01"]) == g.PASS
    assert len(calls) == 1
    assert "OUTPUT CONTRACT (CODEX BACKEND)" not in calls[0]
    assert crb.identity("claude", "low").startswith("[llm-review-gate] reviewer:")
    assert "family=claude" in crb.identity("claude", "low")


def test_the_effort_ladder_is_the_SSOT_one():
    assert crb.EFFORT == {"low": "medium", "medium": "high", "high": "xhigh"}
    assert crb.MODEL == "gpt-5.6-sol" and "ultra" not in crb.EFFORT.values()


def test_the_prompt_carries_the_prepared_paths_verbatim():
    body = crb.contract(["skills/x.py", "scripts/y.sh"])
    assert "  - skills/x.py" in body and "  - scripts/y.sh" in body
    assert "EXACTLY THESE 2 PATHS" in body
    assert "OVERRIDES EVERY OUTPUT INSTRUCTION ABOVE" in body


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

