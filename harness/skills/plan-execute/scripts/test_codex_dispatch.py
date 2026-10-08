"""Provider-aware dispatch (s04, DSP-02): routing a session to the codex wrapper.

THE CORE CONSTRAINT under test: Task only accepts Claude tokens, so a
Codex-backed session must dispatch a WRAPPER (Claude model for Task + the
concrete Codex model embedded in a Bash `codex exec -m ...` command) and a
session declared for Codex whose path can't be constructed must BLOCK loudly —
never fall through to the silent None->inherit-Claude path.

Covered: explicit Codex pins, the unroutable-session hard fail, the run-level
dial, byte-for-byte anthropic backward compat, and executor_policy opt-in.
The egress guard, the --harness codex driver, the dispatch receipt and the
effort climb each have their own file. Shared fixtures: codex_helpers.py.

Run: pytest skills/plan-execute/scripts/test_codex_dispatch.py -q
"""
import json
import os
from pathlib import Path

import pytest

from codex_helpers import _begin, _begin_codex, make_plan, rsi, run
import ssot_policy

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
    # v1.15/s03: the floor moved with the lineup. gpt-5.5 (workhorse) is RETIRED, so
    # the model with no rung below it is now gpt-5.6-terra — the SINK of the
    # 5.6-only ladder (sol->terra->NO-CODEX). The invariant is unchanged: at the
    # floor, exhaustion means NO-CODEX, never a silent inherit onto Claude.
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-terra"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "codex"
    assert m["codex_model"] == "gpt-5.6-terra"
    assert m["codex_effort"] == "max"   # terra never below max (effort-steep)
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
    # THE REGRESSION THIS TEST NOW PINS (s03, 2026-08-13): a dial-driven SONNET
    # session must route. Under the retired tier-NAME translation it resolved
    # `sonnet` → providers.anthropic `workhorse` → providers.openai `workhorse` →
    # None, and halted as unroutable the moment gpt-5.5 was retired — probed
    # before the change. Translation now resolves (task_class, openai):
    # standard_build → cheap_fast → gpt-5.6-luna, whose curve collapses below max.
    assert by_id["s01"]["backend"] == "codex"
    assert by_id["s01"]["codex_model"] == "gpt-5.6-luna"
    assert by_id["s01"]["codex_effort"] == "max"
    # deep_reasoning → apex_reasoner → gpt-5.6-sol. `low` maps to intent `light` =
    # medium, but the CLASS row prescribes `thorough` = xhigh and translation never
    # routes below either signal, so the class floors it at xhigh.
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol"
    assert by_id["s02"]["codex_effort"] == "xhigh"
    run.cmd_release(plan_dir)


def test_dial_driven_translation_ignores_tier_name_symmetry(tmp_path, capsys, ssot):
    """Two sessions on the SAME Claude model but DIFFERENT task classes must
    resolve to DIFFERENT Codex models.

    The ALLOW CONTROL for the assertion above: if translation were still keyed on
    the Claude tier name, both of these would land on the same model regardless of
    class, and the test above could pass for the wrong reason."""
    ssot("openai", executor_for="standard_build, agentic_build, deep_reasoning, mechanical")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "reasoning": "medium", "task_class": "mechanical"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Sonnet",
         "reasoning": "medium", "task_class": "agentic_build"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01", "s02"], capsys)
    assert by_id["s01"]["codex_model"] == "gpt-5.6-luna"
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol"
    run.cmd_release(plan_dir)


def test_translation_never_routes_BELOW_either_signal(tmp_path, capsys, ssot):
    """The pin and the class are two independent statements of how much
    capability the work needs, and translation takes the STRONGER of the two.

    Both single-signal rules were measured to under-serve, in opposite directions
    (2026-08-14) — this asserts both PLANTS at once:
      * class only: `Opus` + `standard_build` routed to LUNA, the pin silently
        downgraded by an unrelated field, and terra unreachable by translation
        (no task_class maps to frontier_reasoner);
      * model only: `Opus` + `linchpin` routed to TERRA, taking the plan's most
        consequential class off the apex model."""
    ssot("openai", executor_for="standard_build, agentic_build, deep_reasoning, mechanical")
    sessions = [
        # class alone says luna; the Opus pin says terra -> terra.
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
         "reasoning": "high", "task_class": "standard_build"},
        # the Opus pin says terra; the class says sol -> sol.
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus",
         "reasoning": "high", "task_class": "deep_reasoning"},
        # ALLOW CONTROL: workhorse has no counterpart at all, so the class carries
        # the route alone rather than the session halting as unroutable.
        {"id": "s03", "title": "S3", "items": ["i3"], "model": "Sonnet",
         "reasoning": "medium", "task_class": "mechanical"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01", "s02", "s03"], capsys)
    assert by_id["s01"]["codex_model"] == "gpt-5.6-terra", "the Opus pin was downgraded"
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol", "deep_reasoning left the apex"
    assert by_id["s03"]["codex_model"] == "gpt-5.6-luna"
    run.cmd_release(plan_dir)


def test_the_class_effort_floor_holds_at_every_reasoning_tier(tmp_path, capsys, ssot):
    """The EFFORT obeys the same "stronger of both signals" rule as the model, at
    every reasoning tier — including the ones whose native level the tier's
    ESCALATION ladder never mentions.

    PLANT: ranking the two levels against the escalation ladder alone made the
    weakest tier unrankable (`apex_reasoner` walks [high, xhigh, max], while
    `reasoning: low` maps to `medium`), and the comparison then silently kept the
    WEAKER value — `linchpin` dispatched at sol@medium where its own SSOT row
    prescribes sol@max."""
    ssot("openai")
    sessions = [{"id": f"s{i:02d}", "title": f"S{i}", "items": [f"i{i}"], "model": "Opus",
                 "reasoning": r, "task_class": "linchpin"}
                for i, r in enumerate(("low", "medium", "high", "xhigh", "max"), start=1)]
    plan_dir = make_plan(tmp_path, sessions)
    ids = [s["id"] for s in sessions]
    run.cmd_begin(plan_dir, ids, harness="codex")     # linchpin is barred on the dial
    out = json.loads(capsys.readouterr().out)
    briefs = out.get("checkpoint_briefs", {})
    for sid in ids:
        assert "gpt-5.6-sol · max" in briefs[sid], (sid, briefs[sid])
    # ALLOW CONTROL: a class the row prescribes NO raise for keeps its own effort,
    # so the floor is a floor and not a blanket upgrade to max.
    (tmp_path / "ctl").mkdir()
    plan2 = make_plan(tmp_path / "ctl", [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
         "reasoning": "high", "task_class": "deep_reasoning"}])
    by_id, _ = _begin(plan2, ["s01"], capsys, harness="codex")
    assert (by_id["s01"]["codex_model"], by_id["s01"]["codex_effort"]) == ("gpt-5.6-sol", "xhigh")
    run.cmd_release(plan2)


def test_an_ABSENT_model_routes_on_task_class_while_a_BAD_one_still_blocks(tmp_path, ssot):
    """The garbage-model guard is for an authoring ERROR, not for an absent field.

    Exercised at the resolver rather than through `begin`: plan-builder requires a
    `model` on every session, so a manifest cannot carry an absent one today — but
    `_resolve_codex_dispatch` is also reached with hand-built and amended input,
    and it refused a perfectly resolvable task_class purely because the model slot
    was empty. PLANT and ALLOW CONTROL are the two halves below."""
    ssot("openai")
    text = (tmp_path / "model-routing.yaml").read_text() if (tmp_path / "model-routing.yaml").exists() \
        else Path(os.environ[run._SSOT_ENV]).read_text()
    # ABSENT model + a class that resolves -> routes on the class alone.
    assert run._resolve_codex_dispatch(None, "high", text,
                                       task_class="deep_reasoning")[0] == "gpt-5.6-sol"
    assert run._resolve_codex_dispatch("", "high", text,
                                       task_class="deep_reasoning")[0] == "gpt-5.6-sol"
    # WRITTEN but unresolvable -> still a loud block, class or no class.
    for tc in ("deep_reasoning", None):
        with pytest.raises(ssot_policy.UnroutableCodexSession):
            run._resolve_codex_dispatch("Gemini", "high", text, task_class=tc)
    # ...and an absent model with NO class has no signal at all -> block.
    with pytest.raises(ssot_policy.UnroutableCodexSession):
        run._resolve_codex_dispatch(None, "high", text, task_class=None)


def test_a_typo_in_task_class_loses_a_signal_it_does_not_halt_the_batch(
    tmp_path, capsys, ssot
):
    """Nothing validates the `task_class` VOCABULARY at build time, so a typo
    reaches dispatch. It costs one of the two routing signals — it must not take
    down the whole `begin` batch and halt the plan when the session's own model
    resolves perfectly well."""
    ssot("openai")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
         "reasoning": "high", "task_class": "deep_reasoning"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus",
         "reasoning": "high", "task_class": "reserch"},          # typo
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out, err = _begin_codex(plan_dir, ["s01", "s02"], capsys)
    assert "unroutable" not in out, out                          # the batch survives
    assert by_id["s02"]["codex_model"] == "gpt-5.6-terra"        # the MODEL still routes
    assert "does not resolve under providers.openai" in err      # ...and it is loud
    assert by_id["s01"]["codex_model"] == "gpt-5.6-sol"          # the good one is unaffected
    run.cmd_release(plan_dir)

    # ALLOW CONTROL: with NO usable signal at all it still blocks loudly, so the
    # degrade is scoped to "one signal lost", not "never block".
    (tmp_path / "ctl").mkdir()
    plan2 = make_plan(tmp_path / "ctl", [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "reasoning": "high", "task_class": "reserch"}])          # workhorse: no counterpart
    capsys.readouterr()                                           # drain cmd_release
    with pytest.raises(SystemExit):
        run.cmd_begin(plan2, ["s01"], harness="codex")
    assert "unroutable" in json.loads(capsys.readouterr().out)


def test_the_receipt_never_claims_an_SSOT_row_it_did_not_read(tmp_path, capsys, ssot):
    """When the task_class raises the effort, the receipt must not print it as
    `effort.map.<tier>.<intent> = <raised>` — that names a row the SSOT does not
    contain. Print the row's REAL value, then the raise, as two lines."""
    ssot("openai")
    plan_dir = make_plan(tmp_path, [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
         "reasoning": "low", "task_class": "deep_reasoning"}])
    _, _, err = _begin_codex(plan_dir, ["s01"], capsys)
    assert "effort.map.apex_reasoner.light = medium" in err, err   # the row, as written
    assert "RAISED to" in err and "deep_reasoning" in err, err      # the raise, named
    assert "effort.map.apex_reasoner.light = xhigh" not in err      # never the lie
    # ...and the FIDELITY verdict moves with it. Left on the row's own value it
    # printed "a lower reasoning tier also resolves to `xhigh`" and persisted
    # `clamped` for a session that in fact bought MORE depth than its tier asked.
    assert "raised_by_class" in err, err
    assert "buys no extra depth" not in err, err
    run.cmd_release(plan_dir)


def test_legacy_gpt55_pin_blocks_with_guidance_even_with_a_task_class(tmp_path, capsys, ssot):
    """A retired `gpt-5.5` pin BLOCKS loudly — it is never silently rerouted onto
    a 5.6 model, and a `task_class` on the same session does not open that door.

    ORDERING IS THE WHOLE GUARANTEE: dial-driven translation resolves from
    task_class, so had the retired-pin check stayed BELOW it, this session would
    have resolved to gpt-5.6-sol and dispatched — converting the one loudly
    blocked case into exactly the silent reroute the operator directive forbids."""
    ssot("openai")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.5",
                 "reasoning": "high", "task_class": "agentic_build"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    why = out["unroutable"]["s01"]
    assert "RETIRED" in why
    assert "amend-session" in why                       # names the fix
    for repl in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        assert repl in why                              # names the replacements
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"


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
    assert m["fallback_reasoning"] == "high"
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


