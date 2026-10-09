#!/usr/bin/env python3
"""Behaviour tests for hooks/compact-policy.py and hooks/context_tokens.py.

The hook is exercised as a separate process over the same stdin/stdout contract
Claude Code uses, against a throwaway HOME.  Nothing here reads or writes the
real ~/.gearbox-state.

Every check in this file has been NEUTERED once: the rule is broken inside
the test process and the check is required to fail with the failure it names.
A check nobody has seen fail proves nothing.  The probes themselves — breaker,
check and required phrase — live in compact_policy_neuters.py; the runner at
the bottom of this file drives them.
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compact_policy_neuters as neuters  # noqa: E402
import compact_policy_spec as spec  # noqa: E402
import compact_policy_testkit as kit  # noqa: E402
import compact_store as store  # noqa: E402

REQUIRED_MIN_HEADROOM = spec.REQUIRED_MIN_HEADROOM
RECORDED_RUN_TRIGGERS = spec.RECORDED_RUN_TRIGGERS
RECORDED_RUN_MODEL_WINDOW = spec.RECORDED_RUN_MODEL_WINDOW
RECORDED_RUN_DEATH_CTX = spec.RECORDED_RUN_DEATH_CTX
RECORDED_RUN_MAX_TURN_GROWTH = spec.RECORDED_RUN_MAX_TURN_GROWTH
_replay_recorded_run = spec.replay_recorded_run
_illegal_blocks = spec.illegal_blocks

WINDOW_1M = 1_000_000


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """The hook imported in-process, with HOME pointed at a throwaway dir."""
    monkeypatch.setenv("HOME", str(tmp_path / "inproc"))
    monkeypatch.delenv("GEARBOX_COMPACT_POLICY", raising=False)
    monkeypatch.delenv("GEARBOX_COMPACT_SOURCE", raising=False)
    return kit.load_policy()


# --------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind,fraction,sticky",
    [
        ("/plan-execute _plans/whatever", "orchestrator", 0.75, True),
        ("/plan-harden --quick", "planning", 0.75, True),
        ("research how compaction triggers fire", "planning", 0.75, True),
        ("Research: how do we cut the context budget?", "planning", 0.75, True),
        # A WORD, not a prefix. `startswith` on the whole prompt sent this one
        # to planning — which is STICKY — arming the veto on ordinary debugging.
        ("researching why test_foo is flaky", "default", 0.5, False),
        ("researcher notes for later", "default", 0.5, False),
        ("fix the flaky test in hooks/", "default", 0.5, False),
        # v3 — the WHOLE prompt decides (compact_classify.py). A real research
        # opening of this shape compacted early on a 1M window.
        ("Please analyse this article and summarise what applies to us, "
         "and tell me if I can use some of this knowledge", "planning", 0.75, True),
        ("/research how do other harnesses classify sessions", "planning", 0.75, True),
        # a build verb anywhere keeps an ambiguous prompt provisional
        ("investigate why test_foo is flaky", "default", 0.5, False),
        ("analyse the failure and fix it", "default", 0.5, False),
        # the review gate's headless dispatches are not ours to widen
        ("Review the diff in the file /tmp/plan-execute-review-1.diff (3 files)",
         "default", 0.5, False),
        ("Invoke the code-review skill (Skill tool, skill=\"code-review\") over the diff",
         "default", 0.5, False),
    ],
)
def test_classification_writes_type_fraction_and_a_real_sticky_boolean(
    home, text, kind, fraction, sticky
):
    done = kit.prompt(home, "s1", text)
    assert done.returncode == 0
    assert done.stdout == "", (
        "UserPromptSubmit stdout is injected straight into the model's context — "
        "the classifier must never write there"
    )
    got = kit.policy(home, "s1")
    assert got["type"] == kind, "classifier routed %r to %r, expected %r" % (
        text, got["type"], kind,
    )
    assert got["ceiling_fraction"] == fraction, (
        "ceiling_fraction must be the FRACTION for this type, never a token count"
    )
    assert got["sticky"] is sticky, "sticky must be a written boolean, not implied"


def test_a_provisional_default_is_upgraded_and_sticky_flips(home):
    kit.prompt(home, "s2", "just fix this typo")
    before = kit.policy(home, "s2")
    assert before["type"] == "default" and before["sticky"] is False
    kit.prompt(home, "s2", "/plan-builder a new plan")
    after = kit.policy(home, "s2")
    assert after["type"] == "planning", "a provisional default must upgrade"
    assert after["sticky"] is True, "sticky must be REWRITTEN true on upgrade"
    assert after["ceiling_fraction"] == 0.75


def test_a_sticky_classification_is_never_downgraded(home):
    kit.prompt(home, "s3", "/plan-execute run it")
    kit.prompt(home, "s3", "now fix this typo")
    assert kit.policy(home, "s3")["type"] == "orchestrator", (
        "nothing may move a session back to default"
    )


def test_the_heartbeat_is_written_once_per_session(home):
    kit.prompt(home, "s4", "/plan-execute go")
    kit.prompt(home, "s4", "another turn")
    kit.prompt(home, "s5", "hello")
    rows = kit.sessions(home)
    assert [r["session"] for r in rows] == ["s4", "s5"], (
        "exactly one heartbeat per session, on the first prompt only"
    )
    assert rows[0]["kill_switch"] == "off"
    assert isinstance(rows[0]["hooks_registered"], bool)
    assert rows[0]["policy_version"] == 1


# --------------------------------------------------------------------------
# The kill switch, and the order it sits in
# --------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["env", "file"])
def test_the_kill_switch_silences_the_hooks_but_never_activation(home, tmp_path, via):
    env = {}
    if via == "env":
        env["GEARBOX_COMPACT_POLICY"] = "off"
    else:
        os.makedirs(kit.live_root(home), exist_ok=True)
        open(kit.live_root(home, "DISABLED"), "w").close()
    kit.prompt(home, "k1", "/plan-harden x", env=env)
    assert kit.policy(home, "k1") is None, "prompt must write no policy under the switch"
    assert kit.decisions(home) == [], "no hook subcommand may write a decision line"
    assert kit.pre_compact(home, "k1").stdout == "", "pre-compact must be silent"

    head = kit.git_repo(str(tmp_path / "repo"))
    done = kit.run(
        home, "activation", "--intervention", "repo_diet", "--evidence",
        "landed as commit %s" % head, "--source", "session",
        env=dict(env, GEARBOX_REPO_DIR=str(tmp_path / "repo")),
    )
    assert done.returncode == 0, done.stderr
    assert len(kit.activations(home)) == 1, (
        "the kill switch must NOT gag activation: operator bookkeeping, not hook execution"
    )


def test_the_heartbeat_is_written_before_the_kill_switch_is_consulted(home):
    kit.prompt(home, "k2", "/plan-execute go", env={"GEARBOX_COMPACT_POLICY": "off"})
    rows = kit.sessions(home)
    assert len(rows) == 1, (
        "a session run with the instrument OFF must still leave a heartbeat, "
        "or a plan session scores it `unknown` instead of `hook_inactive`"
    )
    assert rows[0]["kill_switch"] == "on"


# --------------------------------------------------------------------------
# The veto
# --------------------------------------------------------------------------


def _transcript(tmp_path, name, ctx, window=WINDOW_1M):
    path = str(tmp_path / name)
    kit.write_transcript(path, [kit.assistant_line(input_tokens=ctx, window=window)])
    return path


def test_pre_compact_allows_a_session_with_no_policy_file(home, tmp_path):
    done = kit.pre_compact(home, "nope", _transcript(tmp_path, "t.jsonl", 300_000))
    assert done.returncode == 0 and done.stdout == ""
    assert kit.decisions(home)[-1]["reason"] == "no_policy", (
        "an unclassified session must behave exactly as it does today"
    )


@pytest.mark.parametrize("rule", list("abcdefg"))
def test_each_allow_rule_fires_on_its_own(home, tmp_path, rule):
    expected = {
        "a": "trigger:error", "b": "no_policy", "c": "default_type_not_blocked",
        "d": "safe_point_ok", "e": "unreadable_context", "f": "no_headroom",
        "g": "at_ceiling",
    }[rule]
    session = "r" + rule
    if rule != "b":
        kit.prompt(home, session, "/plan-execute go" if rule == "c" else "/plan-harden go")
    transcript = _transcript(tmp_path, "t%s.jsonl" % rule, 300_000)
    if rule == "e":
        transcript = str(tmp_path / "absent.jsonl")
    if rule == "d":
        kit.run(home, "safe-point", "--state", "ok", "--session", session)
    if rule == "f":  # 200k model window, so there is nowhere to defer TO
        transcript = _transcript(tmp_path, "tf.jsonl", 150_000, window=200_000)
    if rule == "g":  # effective_window recorded low, then context runs past the ceiling
        kit.pre_compact(home, session, _transcript(tmp_path, "tg0.jsonl", 200_000))
        transcript = _transcript(tmp_path, "tg1.jsonl", 900_000)
    done = kit.pre_compact(home, session, transcript, trigger="error" if rule == "a" else "auto")
    assert done.stdout == "", "rule (%s) must ALLOW: %s" % (rule, done.stdout)
    assert kit.decisions(home)[-1]["reason"].startswith(expected), (
        "rule (%s) should have allowed with %r, got %r"
        % (rule, expected, kit.decisions(home)[-1]["reason"])
    )


def test_the_one_remaining_path_blocks(home, tmp_path):
    kit.prompt(home, "b1", "/plan-harden go")
    done = kit.pre_compact(home, "b1", _transcript(tmp_path, "tb.jsonl", 200_000))
    assert json.loads(done.stdout)["decision"] == "block", (
        "a planning session with headroom below its ceiling must be deferred"
    )
    last = kit.decisions(home)[-1]
    assert last["decision"] == "block" and last["reason"] == "deferred"
    assert last["effective_window"] == 200_000, (
        "effective_window is DERIVED from ctx at the first PreCompact call"
    )
    assert last["ceiling"] == 800_000


def test_a_default_type_session_is_never_blocked(home, tmp_path):
    kit.prompt(home, "b2", "fix a typo")
    done = kit.pre_compact(home, "b2", _transcript(tmp_path, "tb2.jsonl", 200_000))
    assert done.stdout == "", "a 'default' session with safe_point 'unknown' is NEVER blocked"
    assert kit.decisions(home)[-1]["reason"] == "default_type_not_blocked"


def test_a_raising_context_tokens_still_exits_zero_and_prints_nothing(mod, monkeypatch, capsys):
    def boom(_path):
        raise RuntimeError("transcript exploded")

    monkeypatch.setattr(mod, "context_tokens", boom)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": "x", "trigger": "auto"}))
    )
    assert mod.run_stdin_hook("pre-compact") == 0
    assert capsys.readouterr().out == "", "fail open means print NOTHING"
    rows = kit.decisions(os.environ["HOME"])
    assert rows[-1]["reason"] == "hook-error" and rows[-1]["decision"] == "allow"
    assert all(field in rows[-1] for field in kit.SPINE), (
        "the fail-open path is the one most likely to drop a spine field"
    )


# Post-compact and session-start live in test_compact_reorient.py
# — split out when this file hit its 800-line bound. See that file's docstring.


# --------------------------------------------------------------------------
# safe-point
# --------------------------------------------------------------------------


def test_safe_point_flips_a_block_into_an_allow(home, tmp_path):
    kit.prompt(home, "sp", "/plan-harden go")
    transcript = _transcript(tmp_path, "tsp.jsonl", 200_000)
    assert kit.pre_compact(home, "sp", transcript).stdout != ""
    kit.run(home, "safe-point", "--state", "ok", "--session", "sp")
    assert kit.pre_compact(home, "sp", transcript).stdout == ""
    assert kit.decisions(home)[-1]["reason"] == "safe_point_ok"


def test_safe_point_without_a_session_id_writes_nothing(home):
    done = kit.run(home, "safe-point", "--state", "hold")
    assert done.returncode == 0
    assert "no session id" in done.stderr
    assert not os.path.isdir(kit.live_root(home, "policy")), "never guess a session id"


@pytest.mark.parametrize("marker", ["CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_FORK_SUBAGENT"])
def test_safe_point_refuses_to_resolve_a_session_id_inside_a_worker(home, marker):
    """A subagent's $CLAUDE_CODE_SESSION_ID is the WORKER's, so a skill step
    running in one would write its hold/ok to a different session's policy file
    — silently, because both ids are well-formed and both writes succeed."""
    env = {marker: "1", "CLAUDE_CODE_SESSION_ID": "worker-sid"}
    done = kit.run(home, "safe-point", "--state", "hold", env=env)
    assert done.returncode == 0
    assert "worker" in done.stderr and "--session" in done.stderr
    assert kit.policy(home, "worker-sid") is None, (
        "a worker wrote a safe point using an id that is not the session it meant"
    )
    named = kit.run(home, "safe-point", "--state", "hold", "--session", "real-sid", env=env)
    assert named.returncode == 0 and kit.policy(home, "real-sid")["safe_point"] == "hold", (
        "--session must still work everywhere: the caller that knows may say so"
    )


def test_an_expired_hold_and_a_stale_phase_both_read_unknown(mod):
    fresh = mod.now_ms() + 60_000
    assert mod.effective_safe_point({"safe_point": "hold", "safe_point_expires_ms": fresh}) == "hold"
    assert mod.effective_safe_point(
        {"safe_point": "hold", "safe_point_expires_ms": mod.now_ms() - 1}
    ) == "unknown", "EVERY safe point expires"
    assert mod.effective_safe_point(
        {"safe_point": "hold", "safe_point_expires_ms": fresh,
         "safe_point_phase": "build", "current_phase": "verify"}
    ) == "unknown", "a hold written in another phase is stale"


def test_a_hold_on_an_orchestrator_session_is_recorded_as_real(home):
    """v2: rule (c) is retired, so an orchestrator's `hold` is a
    real brake and must be receipted `recorded` like anyone else's — the old
    `inert` receipt would tell plan-execute its seam marker does nothing."""
    kit.prompt(home, "orc", "/plan-execute run the plan")
    done = kit.run(home, "safe-point", "--state", "hold", "--session", "orc")
    assert done.returncode == 0 and "INERT" not in done.stderr
    line = kit.decisions(home)[-1]
    assert (line["decision"], line["reason"]) == ("recorded", "safe_point:hold")

    kit.prompt(home, "pl", "/plan-harden go")
    kit.run(home, "safe-point", "--state", "hold", "--session", "pl")
    assert kit.decisions(home)[-1]["decision"] == "recorded", (
        "a hold the veto WILL read must still be plain `recorded`"
    )


# --------------------------------------------------------------------------
# The ledger spine
# --------------------------------------------------------------------------


def test_every_subcommand_stamps_the_full_spine(home, tmp_path):
    repo = str(tmp_path / "repo")
    head = kit.git_repo(repo)
    kit.prompt(home, "sp1", "/plan-harden go")
    kit.pre_compact(home, "sp1", _transcript(tmp_path, "tsn.jsonl", 200_000))
    kit.run(home, "safe-point", "--state", "ok", "--session", "sp1")
    kit.run(home, "activation", "--intervention", "repo_diet", "--evidence",
            "commit %s" % head, "--source", "session", env={"GEARBOX_REPO_DIR": repo})
    rows = kit.decisions(home)
    events = {row["event"] for row in rows}
    assert events == {"prompt", "pre-compact", "safe-point", "activation"}
    for row in rows:
        missing = [field for field in kit.SPINE if field not in row]
        assert not missing, "ledger line %r is missing spine fields %s" % (row, missing)
        assert row["source"] == "live" and row["policy_version"] == 1


def test_a_probe_run_is_tagged_AND_cannot_touch_any_live_surface(home, tmp_path):
    """Tagging decisions.ndjson was never enough: the heartbeat, policy/<id>.json
    and VERSION.json carry no source field, so a probe run against a real
    session id wrote a fabricated policy that the real session would then READ.
    A probe's whole state root moves instead."""
    probe = {"GEARBOX_COMPACT_SOURCE": "probe"}
    kit.prompt(home, "pr", "/plan-harden go", env=probe)
    kit.pre_compact(home, "pr", _transcript(tmp_path, "tp.jsonl", 200_000), env=probe)
    kit.run(home, "safe-point", "--state", "ok", "--session", "pr", env=probe)

    assert kit.live_surfaces(home) == [], (
        "a probe wrote into the LIVE state: %s" % kit.live_surfaces(home)
    )
    rows = kit.ndjson(kit.probe_root(home, "decisions.ndjson"))
    assert [r["source"] for r in rows] == ["probe"] * len(rows) and rows, (
        "a plan session excludes probe rows from every cohort — the tag stays too"
    )
    assert os.path.exists(kit.probe_root(home, "policy", "pr.json"))
    assert os.path.exists(kit.probe_root(home, "sessions.ndjson"))

    kit.prompt(home, "live1", "hello")  # and a live run still writes live state
    assert "sessions.ndjson" in kit.live_surfaces(home)


def test_the_kill_switch_still_reaches_a_probe(home):
    """The DISABLED file lives on the LIVE root, so an operator's rollback
    governs hand invocations too."""
    os.makedirs(kit.live_root(home), exist_ok=True)
    open(kit.live_root(home, "DISABLED"), "w").close()
    kit.prompt(home, "pk", "/plan-harden go", env={"GEARBOX_COMPACT_SOURCE": "probe"})
    assert kit.ndjson(kit.probe_root(home, "decisions.ndjson")) == []


# --------------------------------------------------------------------------
# activation
# --------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    path = str(tmp_path / "repo")
    return path, kit.git_repo(path)


def _activate(home, repo_path, *extra, evidence, source="session"):
    return kit.run(
        home, "activation", "--intervention", "repo_diet", "--evidence", evidence,
        "--source", source, *extra, env={"GEARBOX_REPO_DIR": repo_path},
    )


def test_activation_first_write_has_the_exact_row_shape(home, repo):
    path, head = repo
    done = _activate(home, path, evidence="repo_diet landed as %s" % head)
    assert done.returncode == 0, done.stderr
    row = kit.activations(home)[0]
    assert row["schema"] == 1 and row["event_id"] == "repo_diet#1"
    assert set(row) == {"schema", "event_id", "ts_utc", "intervention", "scope",
                        "evidence", "source"}
    assert row["ts_utc"].endswith("Z") and row["source"] == "session"


def test_activation_refuses_a_duplicate_and_prints_the_row_that_stands(home, repo):
    path, head = repo
    _activate(home, path, evidence="landed as %s" % head)
    again = _activate(home, path, evidence="landed as %s" % head)
    assert "REFUSED (duplicate)" in again.stdout
    assert "repo_diet#1" in again.stdout
    assert len(kit.activations(home)) == 1, "a plain re-run is a no-op, never a second row"


def test_restage_increments_the_seq(home, repo):
    path, head = repo
    _activate(home, path, evidence="landed as %s" % head)
    _activate(home, path, "--restage", evidence="restaged at %s" % head)
    assert [r["event_id"] for r in kit.activations(home)] == ["repo_diet#1", "repo_diet#2"]


@pytest.mark.parametrize(
    "intervention,evidence",
    [
        ("repo_diet", "no sha here at all"),
        ("repo_diet", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"),
        ("hooks", "I ran gearbox deploy and it seemed fine"),
    ],
)
def test_malformed_evidence_is_refused(home, repo, intervention, evidence):
    path, _ = repo
    done = kit.run(
        home, "activation", "--intervention", intervention, "--evidence", evidence,
        "--source", "operator", env={"GEARBOX_REPO_DIR": path, "GEARBOX_CLAUDE_DIR": path},
    )
    assert done.returncode == 2, done.stdout
    assert "REFUSED (evidence)" in done.stderr
    assert kit.activations(home) == [], "never write an unverified activation row"


def test_a_verified_deploy_claim_is_accepted_and_a_no_op_deploy_is_not(home, repo):
    path, head = repo
    ok = kit.run(home, "activation", "--intervention", "hooks", "--evidence",
                 kit.deploy_output(head), "--source", "operator",
                 env={"GEARBOX_CLAUDE_DIR": path})
    assert ok.returncode == 0, ok.stderr
    noop = kit.run(home, "activation", "--intervention", "routing", "--evidence",
                   kit.deploy_output(head, changed=False), "--source", "operator",
                   env={"GEARBOX_CLAUDE_DIR": path})
    assert noop.returncode == 2 and "clean no-op" in noop.stderr


def test_source_is_required_and_both_values_are_accepted(home, repo):
    path, head = repo
    missing = kit.run(home, "activation", "--intervention", "repo_diet",
                      "--evidence", "landed as %s" % head, env={"GEARBOX_REPO_DIR": path})
    assert missing.returncode != 0 and "--source" in missing.stderr
    assert _activate(home, path, evidence="a %s" % head, source="operator").returncode == 0
    assert _activate(home, path, "--restage", evidence="b %s" % head,
                     source="session").returncode == 0
    assert [r["source"] for r in kit.activations(home)] == ["operator", "session"]


def test_concurrent_appends_never_interleave(tmp_path):
    """The ledger has no lock: it relies on ONE os.write() under O_APPEND, which
    POSIX makes atomic for a payload below PIPE_BUF. That guarantee was claimed
    in a docstring and tested nowhere, so a future refactor into two writes — or
    into a buffered file object that flushes on its own schedule — would corrupt
    rows only under concurrency, which is exactly when nobody is watching.

    Writers run as separate PROCESSES, not threads: the GIL would serialise
    threads and the test would pass over a broken writer."""
    import multiprocessing

    ledger = str(tmp_path / "sub" / "ledger.ndjson")
    # Long enough that a two-call write would be caught interleaving, short
    # enough to stay under PIPE_BUF (512 bytes is the POSIX floor).
    payloads = [{"i": i, "pad": chr(ord("a") + i % 26) * 300} for i in range(40)]

    procs = [multiprocessing.Process(target=store.append_line, args=(ledger, obj))
             for obj in payloads]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    lines = [ln for ln in open(ledger, encoding="utf-8").read().splitlines() if ln]
    assert len(lines) == len(payloads)
    seen = sorted(json.loads(ln)["i"] for ln in lines)   # raises on a torn line
    assert seen == list(range(len(payloads)))
    for line in lines:
        assert len(json.loads(line)["pad"]) == 300


def test_append_line_writes_exactly_once(tmp_path):
    """Atomicity above rests on there being ONE write() call. Count it, so a
    refactor that splits the payload fails here rather than in production."""
    calls = []
    real_write = os.write

    def counting_write(fd, data):
        calls.append(len(data))
        return real_write(fd, data)

    os.write = counting_write
    try:
        store.append_line(str(tmp_path / "one.ndjson"), {"a": 1})
    finally:
        os.write = real_write
    assert len(calls) == 1, calls
    assert open(tmp_path / "one.ndjson", encoding="utf-8").read() == '{"a":1}\n'


# --------------------------------------------------------------------------
# Fail open starts at the import
# --------------------------------------------------------------------------


def test_a_missing_sibling_module_exits_0_and_prints_nothing_to_stdout(tmp_path):
    """A half-deployed tree must not put a traceback on the user's screen at
    every prompt, and must never write to UserPromptSubmit's stdout (which is
    injected straight into the model's context)."""
    import shutil
    import subprocess

    broken = tmp_path / "hooks"
    broken.mkdir()
    for name in ("compact-policy.py", "compact_activation.py", "context_tokens.py"):
        shutil.copy(os.path.join(os.path.dirname(kit.HOOK), name), str(broken / name))
    # compact_store.py deliberately NOT copied.
    for sub, stdin in (("prompt", '{"session_id":"x","prompt":"hi"}'),
                       ("pre-compact", '{"session_id":"x","trigger":"auto"}')):
        done = subprocess.run(
            [sys.executable, str(broken / "compact-policy.py"), sub],
            input=stdin, capture_output=True, text=True,
            env=dict(os.environ, HOME=str(tmp_path / "home")), timeout=60,
        )
        assert done.returncode == 0, "%s exited %d on a broken import" % (sub, done.returncode)
        assert done.stdout == "", "%s wrote to stdout on a broken import" % sub
        assert "compact-policy: disabled" in done.stderr


def test_activation_fails_LOUD_on_a_broken_tree_instead_of_silently_recording_nothing(
    tmp_path,
):
    """The other half of the same seam. `activation` is the sole authority on
    whether an intervention went live, so exiting 0 having written no row would
    report success for a row that does not exist — and every downstream session
    would score that intervention `no_exposure` forever. A half-deployed tree is
    exactly where this happens."""
    import shutil
    import subprocess

    broken = tmp_path / "hooks"
    broken.mkdir()
    for name in ("compact-policy.py", "compact_activation.py", "context_tokens.py"):
        shutil.copy(os.path.join(os.path.dirname(kit.HOOK), name), str(broken / name))
    # compact_store.py deliberately NOT copied.
    for sub in ("activation", "status"):
        done = subprocess.run(
            [sys.executable, str(broken / "compact-policy.py"), sub, "--intervention",
             "hooks", "--evidence", "x" * 20, "--source", "operator"],
            capture_output=True, text=True,
            env=dict(os.environ, HOME=str(tmp_path / "home")), timeout=60,
        )
        assert done.returncode == 2, (
            "%s exited %d on a broken tree — operator bookkeeping must never go "
            "silent, because silence there is indistinguishable from success"
            % (sub, done.returncode)
        )
        assert "REFUSED" in done.stderr and "NOTHING was recorded" in done.stderr
    assert kit.activations(tmp_path / "home") == []


# --------------------------------------------------------------------------
# The safety envelope, swept exhaustively
# --------------------------------------------------------------------------


def test_no_input_combination_can_block_outside_the_one_permitted_case(mod):
    """The rule chain is read top-to-bottom by humans; this reads it by machine.

    Every combination of trigger x policy x safe_point x ctx x model_window x
    effective_window, every BLOCK checked against the spec.
    """
    assert mod.MIN_HEADROOM == REQUIRED_MIN_HEADROOM, (
        "the hook's MIN_HEADROOM (%s) no longer matches the spec's %s. Changing it "
        "changes when the veto disarms itself, which is the one thing measured to "
        "save a session — update the spec pin deliberately, never to make this pass."
        % (mod.MIN_HEADROOM, REQUIRED_MIN_HEADROOM)
    )
    blocked = kit.sweep_blocks(mod)
    assert blocked, "the sweep produced NO blocks at all — it is pointed at nothing"
    illegal = _illegal_blocks(mod, blocked)
    assert not illegal, "these input combinations BLOCK but must allow: %s" % illegal[:5]


# --------------------------------------------------------------------------
# THE MEASURED RUN, REPLAYED
# --------------------------------------------------------------------------


def test_the_veto_releases_before_the_recorded_run_died(mod):
    """The self-disarm, proved the way the defect was found: by replay.

    An earlier build bounded rule (f) and the ceiling on the headroom frozen at
    the FIRST PreCompact call — 200,000 - 75,035 = 124,965, a number that never
    shrinks. That put the ceiling at 168,758 and returned `block` for all seven
    triggers, the last at 167,281, immediately after which this very run died.
    A ceiling whose only proof is an argument is what shipped that.
    """
    decisions = _replay_recorded_run(mod)
    assert "allow" in decisions, (
        "the veto never released across the whole recorded run: %s. It blocked "
        "at ctx %s, the trigger the session died on." % (decisions, RECORDED_RUN_DEATH_CTX)
    )
    released_at = RECORDED_RUN_TRIGGERS[decisions.index("allow")]
    assert released_at < RECORDED_RUN_DEATH_CTX, (
        "the veto released at ctx %s, at or past the %s where the run actually "
        "died" % (released_at, RECORDED_RUN_DEATH_CTX)
    )
    assert set(decisions[decisions.index("allow"):]) == {"allow"}, (
        "once disarmed the veto must stay disarmed — context only grows"
    )
    blocked = [c for c, d in zip(RECORDED_RUN_TRIGGERS, decisions) if d == "block"]
    if blocked:
        assert RECORDED_RUN_MODEL_WINDOW - blocked[-1] >= 2 * RECORDED_RUN_MAX_TURN_GROWTH, (
            "the last block at ctx %s leaves less than two measured turns of "
            "growth before the model window — the compaction it defers may not "
            "get another chance" % blocked[-1]
        )


# --------------------------------------------------------------------------
# NEUTER PROBES — every check above is broken once and required to fail
# --------------------------------------------------------------------------


# Every probe lives in compact_policy_neuters.py except these two, which belong
# with the spec pins they read (compact_policy_spec.py).
NEUTERS = neuters.NEUTERS + [
    ("block-envelope-sweep", neuters.n_headroom, spec.c_envelope,
     "outside its permitted envelope"),
    ("recorded-run-self-disarm", neuters.n_headroom, spec.c_recorded_replay,
     "never released across the whole recorded run"),
    ("window-uncorroborated", neuters.n_window_trust, spec.c_window_uncorroborated,
     "this is the recorded run's death"),
]


def _call(check, module, home):
    if check.__code__.co_argcount == 2:
        return check(module, home)
    return check(module)


@pytest.mark.parametrize("name,neuter,check,phrase", NEUTERS, ids=[n[0] for n in NEUTERS])
def test_each_check_passes_clean_and_fails_neutered(
    tmp_path, monkeypatch, name, neuter, check, phrase
):
    with neuters.shared_modules_restored():
        clean_home = tmp_path / "clean"
        monkeypatch.setenv("HOME", str(clean_home))
        _call(check, kit.load_policy(), clean_home)  # known positive: passes un-neutered

        broken_home = tmp_path / "broken"
        monkeypatch.setenv("HOME", str(broken_home))
        module = kit.load_policy()
        neuter(module)
        with pytest.raises(AssertionError) as caught:
            _call(check, module, broken_home)
    message = str(caught.value)
    assert phrase in message, (
        "%s failed, but not with the failure it names. Expected %r in the message, "
        "got: %s" % (name, phrase, message.splitlines()[0] if message else "<empty>")
    )
    log = os.environ.get("GEARBOX_NEUTER_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("%-30s FAILED AS NAMED: %s\n" % (name, message.splitlines()[0]))


def test_two_interventions_cannot_ride_one_deploy(home, repo):
    """The exact confound: two interventions citing the same deploy sha share
    one cohort by construction, and neither can ever be measured. The reader reports that
    honestly; this asserts the WRITER can no longer create it."""
    path, head = repo
    first = kit.run(home, "activation", "--intervention", "base_context", "--evidence",
                    kit.deploy_output(head), "--source", "session",
                    env={"GEARBOX_CLAUDE_DIR": path})
    assert first.returncode == 0, first.stderr
    second = kit.run(home, "activation", "--intervention", "routing", "--evidence",
                     kit.deploy_output(head), "--source", "session",
                     env={"GEARBOX_CLAUDE_DIR": path})
    assert second.returncode == 2, second.stdout
    assert "REFUSED (same deploy)" in second.stderr
    assert "base_context#1" in second.stderr
    assert [r["intervention"] for r in kit.activations(home)] == ["base_context"], (
        "the confounded second row must never reach the ledger"
    )


def test_a_separate_deploy_for_the_second_intervention_is_accepted(home, repo):
    """The guard must not block the staggered rollout it exists to force. A
    KNOWN NEGATIVE beside the known positive above: same two interventions, two
    real deploys, both rows written."""
    path, head = repo
    assert kit.run(home, "activation", "--intervention", "base_context", "--evidence",
                   kit.deploy_output(head), "--source", "session",
                   env={"GEARBOX_CLAUDE_DIR": path}).returncode == 0
    later = kit.git_commit(path)          # ~/.claude moves on: a second deploy
    ok = kit.run(home, "activation", "--intervention", "routing", "--evidence",
                 kit.deploy_output(later), "--source", "session",
                 env={"GEARBOX_CLAUDE_DIR": path})
    assert ok.returncode == 0, ok.stderr
    assert [r["intervention"] for r in kit.activations(home)] == ["base_context", "routing"]


def test_restaging_the_same_intervention_on_its_own_deploy_still_works(home, repo):
    """A restage is one intervention going live twice, never two riding one
    deploy — the guard skips its own intervention's rows."""
    path, head = repo
    assert kit.run(home, "activation", "--intervention", "base_context", "--evidence",
                   kit.deploy_output(head), "--source", "session",
                   env={"GEARBOX_CLAUDE_DIR": path}).returncode == 0
    again = kit.run(home, "activation", "--intervention", "base_context", "--restage",
                    "--evidence", kit.deploy_output(head), "--source", "session",
                    env={"GEARBOX_CLAUDE_DIR": path})
    assert again.returncode == 0, again.stderr
    assert [r["event_id"] for r in kit.activations(home)] == ["base_context#1", "base_context#2"]


def test_a_seven_hex_prefix_inside_an_unrelated_sha_is_not_a_citation():
    """cites_sha compares whole hex tokens. A substring test would read the
    abbreviated form of one sha out of the middle of an unrelated 40-hex one."""
    import compact_activation as ca
    full = "aaaaaaa" + "b" * 33
    assert ca.cites_sha("deploy OK at %s" % full, full)
    assert ca.cites_sha("deploy OK at %s" % full[:7], full)
    assert not ca.cites_sha("deploy OK at %s" % ("c" * 7 + "aaaaaaa" + "d" * 26), "aaaaaaa")
