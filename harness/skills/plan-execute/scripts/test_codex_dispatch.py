"""Provider-aware dispatch (s04, DSP-02): the two-layer codex-wrapper path.

THE CORE CONSTRAINT under test: Task only accepts Claude tokens, so a
Codex-backed session must dispatch a WRAPPER (Claude model for Task + the
concrete Codex model embedded in a Bash `codex exec -m ...` command) and a
session declared for Codex whose path can't be constructed must BLOCK loudly —
never fall through to the silent None→inherit-Claude path.

Uses a fixture SSOT via the PLAN_EXECUTE_ROUTING_SSOT env override so the tests
are hermetic w.r.t. the live ~/.claude/model-routing.yaml.

Run: pytest skills/plan-execute/scripts/test_codex_dispatch.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_shipping import make_plan  # noqa: E402

# Mirrors the live SSOT's providers block shape (models ascending + effort maps);
# only what _Profile parses eagerly plus active_provider.
SSOT_TEMPLATE = """version: 99
active_provider: {provider}

executor_policy:
  executor_for: [{executor_for}]

codex_peer:
  data_sensitivity_guard:
    egress_opt_ins:
{opt_ins}

providers:
  anthropic:
    calibration:
      status: researched
    models:
      cheap_fast:        haiku
      workhorse:         sonnet
      frontier_reasoner: opus
      apex_reasoner:     fable
    effort:
      control: effort
      map:
        cheap_fast:        {{ light: null, standard: null,   thorough: null }}
        workhorse:         {{ light: low,  standard: medium, thorough: high }}
        frontier_reasoner: {{ light: low,  standard: medium, thorough: high }}
        apex_reasoner:     {{ light: low,  standard: medium, thorough: high }}

  openai:
    calibration:
      status: {openai_status}
    models:
      cheap_fast:        gpt-5.6-luna
      workhorse:         gpt-5.5
      frontier_reasoner: gpt-5.6-terra
      apex_reasoner:     gpt-5.6-sol
    effort:
      control: reasoning_effort
      map:
        cheap_fast:        {{ light: max,    standard: max,  thorough: max   }}
        workhorse:         {{ light: medium, standard: high, thorough: xhigh }}
        frontier_reasoner: {{ light: max,    standard: max,  thorough: max   }}
        apex_reasoner:     {{ light: medium, standard: high, thorough: xhigh }}
"""


@pytest.fixture(autouse=True)
def egress_root(tmp_path, monkeypatch):
    """Every test gets a CLEAN working tree for the data_sensitivity_guard scan —
    never the real cwd (which may legitimately contain .env/creds content)."""
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setenv(run._EGRESS_ROOT_ENV, str(root))
    return root


def _opt_in_line(repo_path, expiry="2099-01-01"):
    return (
        f'      - {{ repo_path: "{repo_path}", approved_by: test-operator, '
        f"date: 2026-07-10, expiry: {expiry} }}"
    )


@pytest.fixture
def ssot(tmp_path, monkeypatch):
    def _write(provider="anthropic", openai_status="researched",
               executor_for="standard_build, agentic_build, deep_reasoning",
               opt_ins="      []"):
        p = tmp_path / "model-routing.yaml"
        p.write_text(SSOT_TEMPLATE.format(
            provider=provider, openai_status=openai_status,
            executor_for=executor_for, opt_ins=opt_ins,
        ))
        monkeypatch.setenv(run._SSOT_ENV, str(p))
        return p

    return _write


def _begin(plan_dir, sessions, capsys):
    run.cmd_begin(plan_dir, sessions)
    out = json.loads(capsys.readouterr().out)
    return {m["id"]: m for m in out["batch"]}, out


# --------------------------------------------------------------------------
# Explicit Codex model → wrapper dispatch (even under anthropic active_provider)
# --------------------------------------------------------------------------
def test_codex_declared_session_emits_wrapper(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol",
         "reasoning": "high"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]

    assert m["backend"] == "codex"
    # Task layer: fixed Claude wrapper model — NEVER the Codex model.
    assert m["model_arg"] == run._CODEX_WRAPPER_MODEL
    # Bash layer: the concrete Codex model + native effort (high reasoning →
    # thorough intent → sol xhigh).
    assert m["codex_model"] == "gpt-5.6-sol"
    assert m["codex_effort"] == "xhigh"
    for flag in ("--ignore-user-config", "--ignore-rules",
                 "--sandbox workspace-write", "-m gpt-5.6-sol",
                 "model_reasoning_effort=xhigh", "-o "):
        assert flag in m["codex_cmd"], flag
    # The wrapper prompt embeds the command + the closeout relay contract.
    assert m["codex_cmd"] in m["prompt_text"]
    assert "CODEX-DISPATCH-FAILED" in m["prompt_text"]
    assert "plan-execute-closeout" in m["prompt_text"]
    # No Anthropic thinking directive — effort rides the codex flag.
    assert not m["prompt_text"].startswith("Think hard")
    # Degrade chain: sol → terra @ max, with a ready-made fallback wrapper prompt.
    assert m["fallback_model"] == "gpt-5.6-terra"
    assert m["fallback_reasoning"] == "max"
    assert "-m gpt-5.6-terra" in m["fallback_prompt_text"]
    assert "model_reasoning_effort=max" in m["fallback_prompt_text"]

    run.cmd_release(plan_dir)


def test_codex_floor_model_has_no_fallback(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.5"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "codex"
    assert m["codex_model"] == "gpt-5.5"
    assert m["codex_effort"] == "high"  # unset reasoning → standard intent
    # Workhorse floor: exhaustion = NO-CODEX — null fallback, never inherit Claude.
    assert m["fallback_model"] is None
    assert m["fallback_prompt_text"] is None
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# THE PROBE (hard-fail invariant, edge-case #2): unroutable Codex session BLOCKS
# pre-lock — never DOING, never silently inherits Claude.
# --------------------------------------------------------------------------
def test_unroutable_codex_session_blocks_loudly(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5"},  # unknown Codex model
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Sonnet"},
    ]
    plan_dir = make_plan(tmp_path, sessions)

    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan_dir, ["s01", "s02"])
    assert exc.value.code == 1

    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "blocked"
    assert "s01" in out["unroutable"]

    statuses = run._statuses(plan_dir)
    assert statuses["s01"] == "BLOCKED"       # loud, atomic
    assert statuses["s02"] != "DOING"          # nothing in the batch was dispatched
    assert rsi.is_halted(plan_dir)             # halt reason recorded
    # The lock was released on the failure path — a follow-up begin can lock.
    rsi.acquire_lock(plan_dir)
    rsi.release_lock(plan_dir)


def test_unreadable_ssot_blocks_codex_but_not_claude(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(run._SSOT_ENV, str(tmp_path / "missing.yaml"))
    # Claude sessions keep working without the SSOT (backward compat mandatory)...
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "claude"
    assert by_id["s01"]["model_arg"] == "sonnet"
    run.cmd_release(plan_dir)

    # ...but a Codex-declared session hard-fails on it.
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir2, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir2)["s01"] == "BLOCKED"


# --------------------------------------------------------------------------
# Run-level dial: active_provider=openai translates Claude tier vocabulary.
# --------------------------------------------------------------------------
def test_openai_active_provider_translates_claude_tokens(tmp_path, capsys, ssot):
    ssot("openai")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "reasoning": "medium", "task_class": "standard_build"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Fable",
         "reasoning": "low", "task_class": "deep_reasoning"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out = _begin(plan_dir, ["s01", "s02"], capsys)

    assert out["active_provider"] == "openai"
    # sonnet (workhorse) → gpt-5.5; medium reasoning → standard intent → high.
    assert by_id["s01"]["backend"] == "codex"
    assert by_id["s01"]["codex_model"] == "gpt-5.5"
    assert by_id["s01"]["codex_effort"] == "high"
    # fable (apex_reasoner) → gpt-5.6-sol; low → light intent → medium.
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol"
    assert by_id["s02"]["codex_effort"] == "medium"
    run.cmd_release(plan_dir)


def test_lane_scoped_profile_blocks_dial_but_not_explicit_pin(tmp_path, capsys, ssot):
    # adversarial-review 2026-07-10 (Codex HIGH): the run-level dial must not
    # dispatch through an uncalibrated (lane_scoped) profile; an explicit
    # per-session Codex pin remains a deliberate opt-in.
    ssot("openai", openai_status="lane_scoped")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "task_class": "standard_build"},                                        # dial-driven → block
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "gpt-5.6-sol"},  # explicit pin → ok
    ]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01", "s02"])
    out = json.loads(capsys.readouterr().out)
    assert list(out["unroutable"]) == ["s01"]
    assert "lane_scoped" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"

    # The explicit pin alone dispatches fine after clearing the halt.
    rsi.clear_halt(plan_dir)
    by_id, _ = _begin(plan_dir, ["s02"], capsys)
    assert by_id["s02"]["backend"] == "codex"
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol"
    run.cmd_release(plan_dir)


def test_openai_active_provider_blocks_unresolvable_model(tmp_path, capsys, ssot):
    # Under a codex-focused run, a session that can't resolve (garbage model)
    # must BLOCK — silently running it on Claude is the exact opposite of the
    # feature.
    ssot("openai")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Gemini",
                 "task_class": "standard_build"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"
    assert rsi.is_halted(plan_dir)


# --------------------------------------------------------------------------
# Backward compat: the anthropic default path is byte-for-byte unchanged.
# --------------------------------------------------------------------------
def test_anthropic_default_path_unchanged(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Fable",
         "reasoning": "max"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert out["active_provider"] == "anthropic"
    assert m["backend"] == "claude"
    assert m["model_arg"] == "fable"
    assert m["prompt_text"].startswith("Ultrathink")  # directive still prepended
    assert m["fallback_model"] == "opus"
    assert m["fallback_reasoning"] == "xhigh"
    assert "codex_cmd" not in m
    # Provider-symmetric verification stamp: Claude executes → OpenAI verifies.
    assert m["executor_family"] == "anthropic"
    assert m["verifier_family"] == "openai"
    assert m["verifier_mode"] == "cross_family"
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# executor_policy (s06, EXE-01): dial opt-in, linchpin/irreversible bar,
# data_sensitivity_guard egress, symmetric-verifier stamps.
# --------------------------------------------------------------------------
def test_dial_non_opted_class_dispatches_claude(tmp_path, capsys, ssot):
    ssot("openai", executor_for="standard_build")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "task_class": "mechanical"},                       # not opted in
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Sonnet"},  # no class at all
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out = _begin(plan_dir, ["s01", "s02"], capsys)
    for sid in ("s01", "s02"):
        m = by_id[sid]
        assert m["backend"] == "claude"           # FAIL CLOSED to Claude, never Codex
        assert "codex_cmd" not in m
        assert "executor_policy" in m["executor_policy_note"] or "not opted" in m["executor_policy_note"]
        # Verifier derives from the ACTUAL executor: Claude executed → OpenAI verifies.
        assert m["executor_family"] == "anthropic"
        assert m["verifier_family"] == "openai"
    run.cmd_release(plan_dir)


def test_dial_barred_session_dispatches_claude(tmp_path, capsys, ssot):
    ssot("openai", executor_for="standard_build, linchpin")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Fable",
         "task_class": "linchpin"},                          # barred even if listed
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Sonnet",
         "task_class": "standard_build",
         "prompt": "run /adversarial-review before finalizing",
         "peer_triggers": ["irreversible_change"]},          # irreversible → barred
        {"id": "s03", "title": "S3", "items": ["i3"], "model": "Sonnet",
         "task_class": "standard_build",
         "dispatch": {"guards_irreversible": True}},         # barred via dispatch flag
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01", "s02", "s03"], capsys)
    for sid in ("s01", "s02", "s03"):
        assert by_id[sid]["backend"] == "claude"
        assert "barred" in by_id[sid]["executor_policy_note"]
    run.cmd_release(plan_dir)


def test_security_sensitive_is_not_barred(tmp_path, capsys, ssot):
    # security_sensitive triggers a PEER review; it does NOT block Codex execution.
    ssot("openai", executor_for="agentic_build")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
                 "task_class": "agentic_build",
                 "prompt": "run /adversarial-review before finalizing",
                 "peer_triggers": ["security_sensitive"]}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)


def test_explicit_codex_pin_on_irreversible_blocks_loudly(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol",
                 "prompt": "run /adversarial-review before finalizing",
                 "peer_triggers": ["irreversible_change"]}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert "barred" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"


@pytest.mark.parametrize("plant", ["nested_env", "gitignored_creds", "symlink"])
def test_egress_guard_refuses_restricted_tree(tmp_path, capsys, ssot, egress_root, plant):
    # Restricted content anywhere in the FULL tree (incl. git-ignored, incl.
    # symlink targets) refuses the codex dispatch BEFORE any command exists.
    if plant == "nested_env":
        (egress_root / "app" / "config").mkdir(parents=True)
        (egress_root / "app" / "config" / ".env.production").write_text("KEY=1")
    elif plant == "gitignored_creds":
        (egress_root / ".gitignore").write_text("creds/\n")
        (egress_root / "creds").mkdir()
        (egress_root / "creds" / "token.json").write_text("{}")
    else:
        outside = tmp_path / "elsewhere" / "secrets"
        outside.mkdir(parents=True)
        (outside / "k.pem").write_text("x")
        (egress_root / "link").symlink_to(outside / "k.pem")
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert "DO-NOT-SEND" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"


def test_symlinked_dir_with_nested_restricted_content_refused(tmp_path, capsys, ssot, egress_root):
    # A benignly-NAMED directory symlink whose target CONTAINS restricted files
    # must still refuse (adversarial-review 2026-07-10, Codex HIGH: os.walk
    # without followlinks skipped symlinked subtrees).
    outside = tmp_path / "elsewhere" / "cleanname"
    outside.mkdir(parents=True)
    (outside / ".env.local").write_text("K=1")
    (egress_root / "vendored").symlink_to(outside, target_is_directory=True)
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert "DO-NOT-SEND" in out["unroutable"]["s01"]


def test_commented_or_out_of_block_opt_in_never_authorizes(tmp_path, capsys, ssot, egress_root):
    # adversarial-review 2026-07-10 (Codex HIGH): a commented-out entry, or one
    # outside the egress_opt_ins block, previously matched the raw-text regex.
    (egress_root / ".env").write_text("K=1")
    commented = "      # " + _opt_in_line(str(egress_root)).strip()
    ssot("anthropic", opt_ins=commented)
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"

    # Out-of-block: the same entry appended at the END of the SSOT (under an
    # unrelated top-level key) must not authorize either.
    p = ssot("anthropic")
    p.write_text(p.read_text() + "\nunrelated_block:\n" + _opt_in_line(str(egress_root)) + "\n")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir2, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir2)["s01"] == "BLOCKED"


def test_codex_cmd_pins_scanned_root(tmp_path, capsys, ssot, egress_root):
    # The scanned egress root is bound into the command (`cd <root> && ...`) so
    # the scanned tree and the shipped tree are the same path by construction.
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["codex_cmd"].startswith(f"cd {egress_root} && ")
    run.cmd_release(plan_dir)


def test_egress_opt_in_allows_and_expiry_is_enforced(tmp_path, capsys, ssot, egress_root):
    (egress_root / ".env").write_text("KEY=1")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]

    # Unexpired per-repo opt-in → dispatches.
    ssot("anthropic", opt_ins=_opt_in_line(str(egress_root)))
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Expired opt-in → refused again (no env-var bypass exists).
    ssot("anthropic", opt_ins=_opt_in_line(str(egress_root), expiry="2020-01-01"))
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir2, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir2)["s01"] == "BLOCKED"


def test_restricted_tree_claude_executor_gets_on_box_verification(
    tmp_path, capsys, ssot, egress_root
):
    # Restricted repo whose ACTUAL executor is Claude: no Codex process may start
    # even for VERIFICATION — the on-box disposition is stamped instead.
    (egress_root / "corpus").mkdir()
    (egress_root / "corpus" / "doc.md").write_text("client data")
    ssot("openai", executor_for="standard_build")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
                 "task_class": "mechanical"}]  # not opted in → Claude executes
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "claude"
    assert "codex_cmd" not in m                 # no Codex process, executor OR verifier
    assert m["verifier_family"] == "openai"
    assert m["verifier_mode"] == "on_box_human"
    run.cmd_release(plan_dir)
