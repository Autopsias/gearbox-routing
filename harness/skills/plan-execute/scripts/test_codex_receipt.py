"""DISPATCH RECEIPT — `apply` refuses a closeout with no matching dispatch.

THE FAILURE: under `--harness codex` the orchestrator is an interactive Codex
model told, in prose, to run each `dispatch_cmd`. One that does the work inline
instead produces a valid closeout, statuses go DONE, and the plan's per-session
model selection silently evaporates. The receipt is the file `codex exec -o`
leaves behind.

These tests prove a DISPATCH HAPPENED, not that the closeout came out of it — an
orchestrator with a shell can `touch` the path. The check turns a silent
omission into a refusal; it does not defend against forgery.

Split out of test_codex_dispatch.py. Shared fixtures: codex_helpers.py.

Run: pytest skills/plan-execute/scripts/test_codex_receipt.py -q
"""
import json
import subprocess
import time
from pathlib import Path

import pytest

from codex_helpers import (
    _CLOSEOUT, _begin, _begin_codex, _codex_session, make_plan, rsi, run,
)
import codex_command
import ssot_policy


# The emitted command must hold in the operator's real shells. zsh is the macOS
# default and bash is what CI runs; GitHub's Ubuntu runner has no /bin/zsh at all,
# and hard-coding it there is 9 failures that say nothing about the command.
SHELLS = [sh for sh in ("/bin/bash", "/bin/zsh") if Path(sh).exists()]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("stale", [False, True, "symlink", "dangling"])
@pytest.mark.parametrize("success", [False, True])
def test_dispatch_preserves_old_receipt_without_deletion(tmp_path, shell, stale, success):
    """Execute the emitted shell; failed Codex cannot expose an old closeout."""
    prompt = tmp_path / "prompt ' quoted.md"
    receipt = tmp_path / "receipt ' quoted.txt"
    prompt.write_text("input")
    target = tmp_path / "original.txt"
    if stale in ("symlink", "dangling"):
        if stale == "symlink":
            target.write_text("OLD")
        receipt.symlink_to(target)
    elif stale:
        receipt.write_text("OLD")
    cmd = codex_command._codex_cmd("model", "high", str(prompt), str(receipt), workdir=tmp_path)
    assert "rm -f" not in cmd
    # Only the external model process is replaced; real shell and filesystem run.
    stub = "codex() { while [ \"$1\" != '-o' ]; do shift; done; shift; "
    stub += 'printf NEW > "$1"; }\n' if success else "return 42; }\n"
    result = subprocess.run([shell, "-c", stub + cmd], capture_output=True, text=True)
    assert result.returncode == (0 if success else 42), result.stderr
    assert receipt.read_text() == "NEW" if success else not receipt.exists()
    archives = list(tmp_path.glob(receipt.name + ".stale.*/last-message.txt"))
    assert len(archives) == int(bool(stale))
    if stale and stale != "dangling":
        assert archives[0].read_text() == "OLD"
    if stale == "dangling":
        assert archives[0].is_symlink() and not target.exists()
    if stale == "symlink":
        assert target.read_text() == "OLD"
        assert archives[0].is_symlink()


def test_archive_failure_prevents_launch(tmp_path):
    receipt = tmp_path / "receipt.txt"
    receipt.write_text("OLD")
    cmd = codex_command._codex_cmd("model", "high", "/unused/prompt", str(receipt))
    result = subprocess.run(
        [SHELLS[0], "-c", "mktemp() { return 42; }; codex() { echo LAUNCHED; }; " + cmd],
        capture_output=True, text=True,
    )
    assert result.returncode == 42 and result.stdout == ""
    assert receipt.read_text() == "OLD"

# ==========================================================================
# DISPATCH RECEIPT — `apply` refuses a closeout with no matching dispatch.
#
# THE FAILURE: under `--harness codex` the orchestrator is an interactive Codex
# model told, in prose, to run each `dispatch_cmd`. One that does the work inline
# instead produces a valid closeout, statuses go DONE, and the plan's per-session
# model selection silently evaporates — everything ran on the orchestrator's
# model. Nothing caught that. The receipt is the file `codex exec -o` leaves.
#
# What these tests prove is that a DISPATCH HAPPENED, not that the closeout came
# out of it — an orchestrator with a shell can `touch` the path. The check turns
# a silent omission into a refusal; it does not defend against forgery.
# ==========================================================================
def _dispatch_codex_session(tmp_path, capsys, ssot):
    """begin one session under --harness codex; return (plan_dir, member)."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    return plan_dir, by_id["s01"]


def test_codex_closeout_applies_when_the_dispatch_receipt_exists(
    tmp_path, capsys, ssot, egress_root
):
    # THE LEGITIMATE PATH, and the operator's real recovery path with it: after a
    # loop interruption they re-applied a closeout by passing the `_codex`
    # last-message file straight to `--output-file`. That file IS the receipt, so
    # the honest replay keeps working — and keeps working a second time, because
    # nothing consumes or removes it.
    plan_dir, m = _dispatch_codex_session(tmp_path, capsys, ssot)
    lm = Path(m["last_message_file"])
    lm.write_text(_CLOSEOUT)                       # what `codex exec -o` would leave

    run.cmd_apply(str(plan_dir), "s01", str(lm))
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert run._statuses(plan_dir)["s01"] == "DONE"
    assert not rsi.is_halted(plan_dir)

    run.cmd_apply(str(plan_dir), "s01", str(lm))   # re-runnable by design
    assert json.loads(capsys.readouterr().out)["applied"] is True
    run.cmd_release(plan_dir)


def test_codex_closeout_without_a_dispatch_receipt_is_refused(
    tmp_path, capsys, ssot, egress_root
):
    # THE PLANT: the same valid closeout, from an orchestrator that never ran the
    # dispatch command. Fail-closed exactly like a malformed closeout — BLOCKED +
    # halt, never a silent DONE — and the refusal has to say WHY in plain words.
    plan_dir, m = _dispatch_codex_session(tmp_path, capsys, ssot)
    assert not Path(m["last_message_file"]).exists()      # nothing ever ran
    inline = tmp_path / "written-by-the-orchestrator.txt"
    inline.write_text(_CLOSEOUT)

    with pytest.raises(SystemExit):
        run.cmd_apply(str(plan_dir), "s01", str(inline))
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False
    assert out["failure"] == "missing_dispatch_receipt"
    assert "no Codex dispatch receipt" in out["reason"]
    assert m["last_message_file"] in out["reason"]        # names the file it wanted
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"
    assert rsi.is_halted(plan_dir)


def test_dispatch_receipt_check_can_fail(tmp_path, capsys, ssot, egress_root):
    # FALSIFICATION CONTROL (house pattern). Swap the check for a pass-through and
    # the refusal above must stop happening — otherwise it is proving something
    # else, like the closeout being unreadable.
    plan_dir, m = _dispatch_codex_session(tmp_path, capsys, ssot)
    inline = tmp_path / "written-by-the-orchestrator.txt"
    inline.write_text(_CLOSEOUT)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(run, "_missing_dispatch_receipt", lambda *a, **kw: None)
    try:
        run.cmd_apply(str(plan_dir), "s01", str(inline))
    finally:
        monkeypatch.undo()
    assert json.loads(capsys.readouterr().out)["applied"] is True, (
        "with the receipt check neutered the same closeout must apply — it did not, "
        "so the refusal test is not proving the receipt check"
    )
    run.cmd_release(plan_dir)


def test_receipt_is_found_when_begin_recorded_a_relative_path(
    tmp_path, capsys, ssot, egress_root
):
    # `begin` records the -o path exactly as it spelled it, so a relative plan dir
    # (the shipped loop-smoke fixture uses one) yields a relative receipt path.
    # Resolving that literally would make the check depend on `apply`'s cwd — a
    # false refusal of a real dispatch. It is also looked up under the plan dir's
    # own `_codex/`, where the file always lives.
    plan_dir, m = _dispatch_codex_session(tmp_path, capsys, ssot)
    lm = Path(m["last_message_file"])
    lm.write_text(_CLOSEOUT)
    nd = Path(plan_dir) / "run.ndjson"
    nd.write_text(nd.read_text().replace(str(Path(plan_dir)) + "/", "SOMEWHERE-ELSE/"))
    assert run._missing_dispatch_receipt(str(plan_dir), "s01") is None
    run.cmd_apply(str(plan_dir), "s01", str(lm))
    assert json.loads(capsys.readouterr().out)["applied"] is True
    run.cmd_release(plan_dir)


def test_fallback_dispatch_is_a_valid_receipt(tmp_path, capsys, ssot, egress_root):
    # A session degraded onto `fallback_cmd` writes to the -fb file and the
    # primary path never appears. That IS a real dispatch; refusing it would break
    # the documented degradation path.
    plan_dir, m = _dispatch_codex_session(tmp_path, capsys, ssot)
    assert m["fallback_last_message_file"]
    Path(m["fallback_last_message_file"]).write_text(_CLOSEOUT)
    run.cmd_apply(str(plan_dir), "s01", m["fallback_last_message_file"])
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert run._statuses(plan_dir)["s01"] == "DONE"
    run.cmd_release(plan_dir)


def test_a_stale_receipt_does_not_vouch_for_a_newer_dispatch(
    tmp_path, capsys, ssot, egress_root
):
    # Only the MOST RECENT dispatch counts. Attempt 1 really ran and left its
    # file; attempt 2 did not. An "any receipt on disk" check would wave attempt 2
    # through on attempt 1's evidence.
    plan_dir, first = _dispatch_codex_session(tmp_path, capsys, ssot)
    Path(first["last_message_file"]).write_text(_CLOSEOUT)
    run.cmd_apply(str(plan_dir), "s01", first["last_message_file"])
    run.cmd_release(plan_dir)
    capsys.readouterr()

    time.sleep(1.1)                       # the -o path is stamped per second
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    second = by_id["s01"]
    assert second["last_message_file"] != first["last_message_file"]
    assert Path(first["last_message_file"]).exists()      # still there, still stale
    with pytest.raises(SystemExit):
        run.cmd_apply(str(plan_dir), "s01", first["last_message_file"])
    out = json.loads(capsys.readouterr().out)
    assert out["failure"] == "missing_dispatch_receipt"
    assert second["last_message_file"] in out["reason"]


def test_claude_harness_closeouts_need_no_receipt(tmp_path, capsys, ssot, egress_root):
    # THE CONTROL. Claude-harness sessions log no `codex_dispatch` event and must
    # be untouched — including the two-layer Claude wrapper around a Codex model,
    # whose -o file lives in /tmp and reaches `apply` through the wrapper's
    # transcript, never as a file path.
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [{"id": "s01", "title": "S1", "items": ["i1"],
                                     "model": "gpt-5.6-sol"}])
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"          # Codex model, Claude harness
    relayed = tmp_path / "closeout-s01.txt"            # what the orchestrator writes
    relayed.write_text(_CLOSEOUT)
    run.cmd_apply(str(plan_dir), "s01", str(relayed))
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert run._statuses(plan_dir)["s01"] == "DONE"
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# BACKWARD COMPAT (contract § 8): the --harness claude payload is byte-identical
# to the no-flag payload, and carries exactly today's keys — no additive
# `harness` field, no reordering, nothing new to break a consumer.
# --------------------------------------------------------------------------
_CLAUDE_MEMBER_KEYS = {
    "id", "title", "subagent_type", "prompt_file", "prompt_text", "items", "model",
    "model_arg", "backend", "reasoning", "effort_enforced", "effort_mechanism",
    "fallback_model", "fallback_reasoning", "executor_family", "verifier_family",
    "verifier_mode",
}


def test_claude_harness_payload_is_byte_identical(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
                 "reasoning": "high", "task_class": "agentic_build"}]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01"])                       # no flag at all
    implicit = capsys.readouterr().out
    run.cmd_release(plan_dir)
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"], harness="claude")     # explicit default
    explicit = capsys.readouterr().out
    run.cmd_release(plan_dir)
    capsys.readouterr()
    assert implicit == explicit                            # byte for byte

    out = json.loads(implicit)
    assert set(out) == {"action", "active_provider", "batch", "plan_url"}
    assert set(out["batch"][0]) == _CLAUDE_MEMBER_KEYS


# --------------------------------------------------------------------------
# dispatch.codex_shell — declared shell capabilities (2026-07-29)
#
# MEASURED on codex-cli 0.145.0 before this feature was written: inside
# `codex exec --sandbox workspace-write` a write outside the repo workspace is
# denied, network is off, and a nested `codex exec` dies with "failed to
# initialize in-process app-server client". The two `-c` overrides below were
# measured to grant the first two THROUGH `--ignore-user-config`; only
# `danger-full-access` grants the third (contract probe P3).
# --------------------------------------------------------------------------
def test_codex_shell_default_is_byte_identical(tmp_path, capsys, ssot):
    """No codex_shell (and an empty one) => the same default command exactly."""
    ssot("anthropic")
    base = codex_command._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
                          workdir="/repo")
    assert "--sandbox workspace-write" in base
    assert "sandbox_workspace_write" not in base          # no sandbox -c overrides
    assert "-c project_doc_max_bytes=262144 " in base     # whole AGENTS.md, not 32 KiB
    for shell in (None, {}, {"writable_roots": [], "network": False}):
        session = {"id": "s01", "dispatch": ({"codex_shell": shell} if shell is not None else {})}
        grant = codex_command._codex_shell_grant(session)
        assert grant is None, shell
        assert codex_command._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
                              workdir="/repo", grant=grant) == base


def test_codex_shell_grants_writable_roots_and_network(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus", "reasoning": "high",
         "dispatch": {"codex_shell": {"writable_roots": ["~/.dyno"], "network": True}}},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out, err = _begin_codex(plan_dir, ["s01"], capsys)
    cmd = by_id["s01"]["dispatch_cmd"]

    home_dyno = str(Path("~/.dyno").expanduser().resolve())
    assert "--sandbox workspace-write" in cmd              # mode NOT widened
    assert f'sandbox_workspace_write.writable_roots=["{home_dyno}"]' in cmd
    assert "sandbox_workspace_write.network_access=true" in cmd
    # ~ is expanded at build time: inside a quoted TOML string the shell can't.
    assert "~/.dyno" not in cmd
    # The grant is disclosed, never silent.
    assert "sandbox=workspace-write" in err and "network_access=true" in err
    assert by_id["s01"]["codex_shell_grant"]["network"] is True

    run.cmd_release(plan_dir)


def test_a_quote_in_a_writable_root_cannot_add_a_second_root():
    """One declared root reaches codex as ONE root, whatever characters it holds.

    Before: the root went into the TOML array as f'"{r}"', so a `"` closed the
    string and the declaration below granted `/Users` and `/x` as well -- while
    the receipt printed one root. Parsed back with tomllib, as codex parses it.
    """
    import shlex
    import tomllib
    declared = '/tmp/ok", "/Users", "/x'
    grant = codex_command._codex_shell_grant(
        {"id": "s01", "dispatch": {"codex_shell": {"writable_roots": [declared]}}})
    toks = shlex.split(codex_command._codex_cmd("m", "high", "/p", "/o", workdir="/w",
                                                grant=grant))
    value = next(toks[i + 1] for i, t in enumerate(toks)
                 if t == "-c" and "writable_roots" in toks[i + 1])
    roots = tomllib.loads(value)["sandbox_workspace_write"]["writable_roots"]
    assert roots == grant["writable_roots"] and len(roots) == 1, roots


def test_codex_shell_passes_only_declared_environment_names():
    session = {
        "id": "s01",
        "dispatch": {"codex_shell": {"env_include": ["ANTHROPIC_API_KEY"]}},
    }
    grant = codex_command._codex_shell_grant(session)
    cmd = codex_command._codex_cmd(
        "gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
        workdir="/repo", grant=grant,
    )

    assert "shell_environment_policy.inherit=all" in cmd
    assert "shell_environment_policy.ignore_default_excludes=true" in cmd
    assert 'shell_environment_policy.include_only=["PATH","HOME","TMPDIR","ANTHROPIC_API_KEY"]' in cmd
    assert "credential-value" not in cmd
    assert grant["env_include"] == ["ANTHROPIC_API_KEY"]


def test_codex_shell_full_access_requires_a_human_gate(tmp_path, capsys, ssot):
    """The gate is NOT vacuous: identical sessions, one gated, one not."""
    ssot("anthropic")
    shell = {"codex_shell": {"sandbox": "danger-full-access"}}

    ungated = {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
               "reasoning": "high", "dispatch": dict(shell)}
    with pytest.raises(codex_command.UngatedFullAccessSession) as ei:
        codex_command._assert_full_access_gated("s01", ungated, codex_command._codex_shell_grant(ungated))
    assert "no human gates it" in str(ei.value)
    # It must be treated as unroutable by every existing handler, not swallowed.
    assert isinstance(ei.value, ssot_policy.UnroutableCodexSession)

    gated = dict(ungated)
    gated["dispatch"] = {**shell, "guards_irreversible": True}
    grant = codex_command._codex_shell_grant(gated)
    codex_command._assert_full_access_gated("s01", gated, grant)     # does not raise
    cmd = codex_command._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
                         workdir="/repo", grant=grant)
    assert "--sandbox danger-full-access" in cmd
    assert "sandbox_workspace_write" not in cmd            # no redundant -c overrides


def test_codex_shell_full_access_blocked_when_the_manifest_is_edited(tmp_path, capsys, ssot):
    """The SECOND enforcement point, tested against its actual threat model.

    `build_plan.py` refuses to BUILD an ungated full-access session, so the only
    way this state reaches disk is a hand-edited manifest — which is precisely why
    `run.py` re-checks. Build it gated, strip the gate the way an editor would, and
    assert `begin` refuses: BLOCKED + halt, never a dispatch, never a silent
    fall-through to Claude."""
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus", "reasoning": "high",
         "dispatch": {"codex_shell": {"sandbox": "danger-full-access"},
                      "guards_irreversible": True}},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    mpath = Path(plan_dir) / "manifest.json"
    man = json.loads(mpath.read_text())
    for s in man["sessions"]:
        s["dispatch"]["guards_irreversible"] = False          # the hand edit
    mpath.write_text(json.dumps(man, indent=2))

    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"], harness="codex")
    html = (Path(plan_dir) / "PLAN.html").read_text()
    assert 'data-session-id="s01"' in html
    assert "BLOCKED" in html
    state = rsi.load_state(plan_dir)
    assert state["halt"]["set"] is True
    assert "no human gates it" in state["halt"]["reason"]


def test_codex_shell_full_access_gated_session_checkpoints_then_dispatches(tmp_path, capsys, ssot):
    """A gated full-access session parks for the human first (D4c), and the
    command it eventually runs is the unsandboxed one it declared."""
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus", "reasoning": "high",
         "dispatch": {"codex_shell": {"sandbox": "danger-full-access"},
                      "guards_irreversible": True}},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    _, out, err = _begin_codex(plan_dir, ["s01"], capsys)
    assert out["action"] == "checkpoint"
    assert "guards_irreversible" in out["checkpoint_briefs"]["s01"]
    assert "danger-full-access" in err and "UNSANDBOXED" in err

    by_id, out2, _ = _begin_codex(plan_dir, ["s01"], capsys)
    assert out2["action"] == "dispatch"
    assert "--sandbox danger-full-access" in by_id["s01"]["dispatch_cmd"]

    run.cmd_release(plan_dir)


def test_codex_shell_rejects_an_unknown_sandbox_mode(tmp_path, capsys, ssot):
    ssot("anthropic")
    session = {"id": "s01", "dispatch": {"codex_shell": {"sandbox": "read-only"}}}
    with pytest.raises(ssot_policy.UnroutableCodexSession):
        codex_command._codex_shell_grant(session)


def test_codex_shell_rejects_wildcard_environment_grants():
    session = {"id": "s01", "dispatch": {"codex_shell": {"env_include": ["*_API_KEY"]}}}
    with pytest.raises(ssot_policy.UnroutableCodexSession, match="environment variable names"):
        codex_command._codex_shell_grant(session)
