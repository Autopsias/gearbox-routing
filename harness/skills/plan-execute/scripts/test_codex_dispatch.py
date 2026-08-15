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
import os
import re
import subprocess
import sys
import time
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

task_classes:
  mechanical:     {{ tier: cheap_fast,        effort: light    }}
  standard_build: {{ tier: workhorse,         effort: standard }}
  agentic_build:  {{ tier: workhorse,         effort: thorough }}
  deep_reasoning: {{ tier: frontier_reasoner, effort: standard }}
  linchpin:       {{ tier: frontier_reasoner, effort: thorough }}

executor_policy:
  executor_for: [{executor_for}]

codex_peer:
  data_sensitivity_guard:
    content_scan_allowlist:
{allowlist}
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
    # Mirrors the live providers.anthropic ladder, so a session that FAILS CLOSED
    # off the codex lane climbs the ladder it will really be dispatched on.
    escalation:
      trigger: "2 failures at the same root cause"
      effort_ladder:
        workhorse:         [low, medium, high]
        frontier_reasoner: [low, medium, high]
        apex_reasoner:     [low, medium, high, xhigh]
      model_ladder: [cheap_fast, workhorse, frontier_reasoner, apex_reasoner]
      model_ladder_entry: {{ frontier_reasoner: high, apex_reasoner: medium }}
    degrade:
      signals: [entitlement, unavailable]
      ladder: {{ apex_reasoner: frontier_reasoner, frontier_reasoner: workhorse }}
      floor: workhorse
      effort_on_degrade: {{ frontier_reasoner: high, workhorse: high }}

  openai:
    calibration:
      status: {openai_status}
    # RE-POINTED TO THE REAL THREE-TIER SHAPE (s03, 2026-08-13), as the v1.15 note
    # here said it would be. The `workhorse` tier is GONE with gpt-5.5, which is
    # precisely why dial-driven translation no longer matches Claude tier NAMES
    # against OpenAI tier names: `sonnet` is workhorse under providers.anthropic,
    # and there is no workhorse here to land on. Translation now resolves
    # (task_class, openai) through resolve_route — hence the `task_classes:`
    # blocks above and below, which this fixture previously had no need for.
    models:
      cheap_fast:        gpt-5.6-luna
      frontier_reasoner: gpt-5.6-terra
      apex_reasoner:     gpt-5.6-sol
    task_classes:
      mechanical:     {{ tier: cheap_fast,    effort: light    }}
      standard_build: {{ tier: cheap_fast,    effort: standard }}
      agentic_build:  {{ tier: apex_reasoner, effort: thorough }}
      deep_reasoning: {{ tier: apex_reasoner, effort: thorough }}
      linchpin:       {{ tier: apex_reasoner, effort: maximal  }}
    effort:
      control: reasoning_effort
      map:
        cheap_fast:        {{ light: max,    standard: max,  thorough: max,   exhaustive: max,   maximal: max   }}
        frontier_reasoner: {{ light: max,    standard: max,  thorough: max,   exhaustive: max,   maximal: max   }}
        apex_reasoner:     {{ light: medium, standard: high, thorough: xhigh, exhaustive: xhigh, maximal: max   }}
    # THE CODEX CLIMB (ESC-03, s04) — mirrors the live providers.openai block. luna
    # has NO effort_ladder at all (its curve collapses below max, so `max` is the
    # only cell it ever emits and the climb leaves the tier immediately); terra's
    # ladder is exactly [max] for the same reason; sol enters at xhigh, never at
    # its first rung (`high` would be BELOW the terra@max just left).
    escalation:
      trigger: "2 failures at the same root cause"
      effort_ladder:
        frontier_reasoner: [max]
        apex_reasoner:     [high, xhigh, max]
      model_ladder: [cheap_fast, frontier_reasoner, apex_reasoner]
      model_ladder_entry: {{ apex_reasoner: xhigh }}
    degrade:
      signals: [entitlement, unavailable]
      ladder: {{ apex_reasoner: frontier_reasoner, cheap_fast: frontier_reasoner }}
      floor: frontier_reasoner
      effort_on_degrade: {{ frontier_reasoner: max }}
"""


# The `egress_root` fixture (a clean per-test working tree) lives in conftest.py —
# every suite in this directory needs it now that the guard shells out to gitleaks.

# A SYNTHETIC AWS key: the canonical gitleaks/AWS documentation example, assembled
# at runtime so this source file never itself carries the literal (the repo's own
# githooks/pre-commit gitleaks gate would refuse the commit, correctly). NEVER use
# a real credential here.
_FAKE_AWS_KEY = "AKIA" + "IMNOJVGFDXXXE4OA"


def _plant_secret(path):
    """Write a file whose CONTENT trips gitleaks. The filename is deliberately
    innocent — the whole point of the content scan is that a live key hides in a
    file called `config.py`, not in one called `secrets`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"AWS_ACCESS_KEY_ID = '{_FAKE_AWS_KEY}'\n")
    return path


def _opt_in_line(repo_path, expiry="2099-01-01"):
    return (
        f'      - {{ repo_path: "{repo_path}", approved_by: test-operator, '
        f"date: 2026-07-10, expiry: {expiry} }}"
    )


def _allow_line(path, expiry="2099-01-01"):
    return (
        f'      - {{ path: "{path}", approved_by: test-operator, '
        f"date: 2026-07-28, expiry: {expiry} }}"
    )


@pytest.fixture
def ssot(tmp_path, monkeypatch):
    def _write(provider="anthropic", openai_status="researched",
               executor_for="standard_build, agentic_build, deep_reasoning",
               opt_ins="      []", allowlist="      []"):
        p = tmp_path / "model-routing.yaml"
        p.write_text(SSOT_TEMPLATE.format(
            provider=provider, openai_status=openai_status,
            executor_for=executor_for, opt_ins=opt_ins, allowlist=allowlist,
        ))
        monkeypatch.setenv(run._SSOT_ENV, str(p))
        return p

    return _write


def _begin(plan_dir, sessions, capsys, **kw):
    run.cmd_begin(plan_dir, sessions, **kw)
    out = json.loads(capsys.readouterr().out)
    return {m["id"]: m for m in out["batch"]}, out


def _begin_codex(plan_dir, sessions, capsys):
    """begin under the CODEX harness — returns (members, payload, stderr)."""
    run.cmd_begin(plan_dir, sessions, harness="codex")
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    return {m["id"]: m for m in out.get("batch", [])}, out, cap.err


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
        with pytest.raises(run.UnroutableCodexSession):
            run._resolve_codex_dispatch("Gemini", "high", text, task_class=tc)
    # ...and an absent model with NO class has no signal at all -> block.
    with pytest.raises(run.UnroutableCodexSession):
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


# --------------------------------------------------------------------------
# data_sensitivity_guard, CONTENT-AWARE (2026-07-28). The guard used to match
# FILENAME substrings (corpus/creds/credential/secret) and never read a byte:
# it refused a repo over an analysis script called `migrate_corpus.py` while a
# live key in `config.py` sailed past. It now runs `gitleaks` — the scanner
# githooks/pre-commit already uses — over the tree, and keeps only the `.env*`
# filename rule (a `.env` of plain KEY=value trips no content rule).
#
# Every case below is proved in BOTH directions: a plant that must REFUSE and
# the matching control that must PASS. `test_content_scan_can_fail` neuters the
# checker and re-runs the plant, so none of it can go green vacuously.
# --------------------------------------------------------------------------
def _codex_session():
    return [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]


def _refuses(plan_dir, capsys):
    """Run begin, assert it REFUSED, return the refusal reason for s01."""
    capsys.readouterr()          # drop any earlier command's JSON from the buffer
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert "DO-NOT-SEND" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"
    return out["unroutable"]["s01"]


@pytest.mark.parametrize("plant", ["nested_env", "untracked_secret", "file_symlink",
                                   "dir_symlink", "env_via_symlink"])
def test_egress_guard_refuses_restricted_tree(tmp_path, capsys, ssot, egress_root, plant):
    # PLANT side. Restricted content anywhere in the FULL tree — including
    # git-ignored files and symlink targets, which is exactly what `codex exec`
    # would upload — refuses the dispatch BEFORE any command exists.
    if plant == "nested_env":
        (egress_root / "app" / "config" / ".env.production").parent.mkdir(parents=True)
        (egress_root / "app" / "config" / ".env.production").write_text("KEY=1")
        expect = ".env"
    elif plant == "untracked_secret":
        # NOT a git work tree (pytest's tmp_path never is), so the `.gitignore`
        # sitting here is inert and the FULL-TREE fallback applies — every file
        # is a candidate. The git-scoped behaviour is proved separately, below.
        (egress_root / ".gitignore").write_text("build/\n")
        _plant_secret(egress_root / "build" / "dump.log")
        expect = "dump.log"
    elif plant == "file_symlink":
        outside = _plant_secret(tmp_path / "elsewhere" / "keys.txt")
        (egress_root / "link.txt").symlink_to(outside)
        expect = "link.txt"
    elif plant == "dir_symlink":
        # gitleaks does NOT descend into symlinked directories — run.py walks
        # them itself and hands each out-of-tree target its own scan pass.
        _plant_secret(tmp_path / "elsewhere" / "cleanname" / "config.py")
        (egress_root / "vendored").symlink_to(tmp_path / "elsewhere" / "cleanname",
                                              target_is_directory=True)
        expect = "config.py"
    else:  # env_via_symlink — a benignly-named link whose TARGET is a .env
        (tmp_path / "elsewhere").mkdir(parents=True, exist_ok=True)
        (tmp_path / "elsewhere" / ".env").write_text("K=1")
        (egress_root / "settings").symlink_to(tmp_path / "elsewhere" / ".env")
        expect = ".env"
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    reason = _refuses(plan_dir, capsys)
    assert expect in reason, f"refusal must name the offending path: {reason}"


def test_innocent_names_are_not_restricted(tmp_path, capsys, ssot, egress_root):
    # ALLOW CONTROL — the exact false positive that motivated the rewrite. A tree
    # full of scary-SOUNDING filenames with no secret in any of them dispatches.
    # This is a test, not a claim: `migrate_corpus.py` is the file that blocked a
    # real repo under the old substring matcher.
    (egress_root / "migrate_corpus.py").write_text("# rebuild the corpus index\n")
    (egress_root / "creds").mkdir()
    (egress_root / "creds" / "README.md").write_text("How we rotate credentials.\n")
    (egress_root / "secrets_helper.py").write_text("def load_secret(name): ...\n")
    (egress_root / "credential_policy.md").write_text("No secret may be committed.\n")
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)


def test_removing_the_secret_clears_the_same_tree(tmp_path, capsys, ssot, egress_root):
    # ALLOW CONTROL, same tree, one file different — proves the refusal tracks the
    # CONTENT and not something incidental to the fixture.
    leak = _plant_secret(egress_root / "app" / "config.py")
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    assert "config.py" in _refuses(plan_dir, capsys)

    leak.write_text("AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']\n")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", _codex_session())
    by_id, _ = _begin(plan_dir2, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir2)


# --------------------------------------------------------------------------
# SCOPE (2026-07-28, second pass). Rule 2 first shipped over the FULL tree and
# was unusable on a real repo: 16 GB / 103,727 files → 6 min 5 s and 826
# findings, every one of them inside git-ignored build output. It now scans the
# REVIEWED SURFACE — git-tracked plus untracked-not-ignored — which measured
# 570 files / 6.4 MB / 1.0 s on the same repo. Rule 1 (`.env*`) stays full-tree.
# --------------------------------------------------------------------------
@pytest.fixture
def git_root(egress_root, monkeypatch):
    """`egress_root`, turned into a git work tree — hermetically. The two
    GIT_CONFIG_* overrides matter: without them a developer's global
    `core.excludesFile` decides which planted file `--exclude-standard` hides,
    and the same test passes on one machine and fails on another."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    subprocess.run(["git", "init", "-q"], cwd=str(egress_root), check=True)
    return egress_root


def _git_add(root):
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)


def _bulk(path, megabytes=2):
    """Enough git-ignored build output that a whole-tree scan cannot be mistaken
    for a scan of the candidate set (the scope assertion's bound is 2× + 1 MiB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// bundled output, nothing secret here\n" * (megabytes * 30000))


def test_git_ignored_build_output_is_not_scanned_but_tracked_source_is(
    tmp_path, capsys, ssot, git_root
):
    # THE PLANT + THE ALLOW CONTROL for the scoping, in one tree.
    #
    # ALLOWED, deliberately: the same synthetic key sitting in a git-IGNORED
    # `dist/` is NOT a refusal. Git-ignored build output is not part of the repo
    # and is not what a reviewer reads or a push ships; before this scoping it was
    # 826 of 826 findings on the operator's real repo and the guard was simply
    # routed around. If a secret really lives in build output, it lives in the
    # source that generated it — which IS scanned, and is the fixable copy.
    (git_root / ".gitignore").write_text("dist/\n")
    _bulk(git_root / "dist" / "bundle.js")
    _plant_secret(git_root / "dist" / "leak.js")
    (git_root / "src").mkdir()
    (git_root / "src" / "config.py").write_text("KEY = os.environ['KEY']\n")
    _git_add(git_root)
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # REFUSED: the same key in a TRACKED source file. One line of difference.
    _plant_secret(git_root / "src" / "config.py")
    _git_add(git_root)
    (tmp_path / "p2").mkdir()
    assert "config.py" in _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)


def test_untracked_not_ignored_file_is_part_of_the_surface(
    tmp_path, capsys, ssot, git_root
):
    # A file you just wrote and have not committed is exactly what a session is
    # about to ship. `git ls-files --others --exclude-standard` is half the
    # candidate set for that reason — tracked-only would be a hole you could walk
    # a fresh `notes.py` through.
    _plant_secret(git_root / "notes.py")          # never `git add`ed, never ignored
    ssot("anthropic")
    assert "notes.py" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_compiled_bytecode_is_not_scanned(tmp_path, capsys, ssot, git_root):
    # Two of the three findings on the real repo were `__pycache__/*.pyc` copies of
    # ONE Python docstring. Bytecode is a build product of source we DO scan.
    _plant_secret(git_root / "app" / "__pycache__" / "config.cpython-313.pyc")
    (git_root / "app" / "config.py").write_text("KEY = os.environ['KEY']\n")
    _git_add(git_root)                            # tracked on purpose: the skip is
    ssot("anthropic")                             # ours, not git's, in this tree
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # ALLOW CONTROL — the .py the bytecode came from is NOT skipped.
    _plant_secret(git_root / "app" / "config.py")
    _git_add(git_root)
    (tmp_path / "p2").mkdir()
    assert "config.py" in _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)


def test_scan_scope_assertion_catches_a_widened_scan(
    tmp_path, capsys, ssot, git_root, monkeypatch
):
    # THE TRAP, regression-proved. `gitleaks dir a b c d` takes ONE path argument:
    # the other three are silently dropped and it scans the CWD tree instead —
    # measured 6 min 40 s over 16 GB where the four directories cost 1.8 s. The
    # scan still "ran", still reported, still exited 0. That is a gate that cannot
    # fail, and the only defence is asserting the scanned BYTE COUNT against the
    # surface we selected.
    (git_root / ".gitignore").write_text("dist/\n")
    _bulk(git_root / "dist" / "bundle.js")        # 2 MB of git-ignored output
    (git_root / "app.py").write_text("print('hello')\n")
    _git_add(git_root)
    ssot("anthropic")

    # ALLOW CONTROL FIRST — correctly scoped, the same tree dispatches. Without
    # this the assertion below could be firing on every tree and prove nothing.
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Now reproduce the trap's OUTCOME: hand the pass the whole tree while the
    # candidate set says a few hundred bytes.
    real = run._scoped_link_tree
    monkeypatch.setattr(
        run, "_scoped_link_tree",
        lambda root, stack: (str(root),) + tuple(real(root, stack)[1:]),
    )
    (tmp_path / "p2").mkdir()
    reason = _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)
    assert "WIDENED" in reason, reason


def test_non_git_tree_falls_back_to_the_full_scan(tmp_path, capsys, ssot, egress_root):
    # Requirement stated honestly: with no git index there is no ignore
    # information, so there is nothing to scope BY — scan it all. Such trees are
    # small in practice; the 16 GB tree that motivated the scoping is a git repo
    # whose bulk is ignored. (`egress_root` is a bare tmp dir — no `git init`.)
    assert run._git_candidates(egress_root) is None
    _plant_secret(egress_root / "deep" / "nested" / "anything.py")
    ssot("anthropic")
    assert "anything.py" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_content_scan_allowlist_clears_one_file_without_a_repo_opt_in(
    tmp_path, capsys, ssot, egress_root
):
    # One reviewed file must not force a whole-repo opt-in. Same fail-closed
    # parsing as egress_opt_ins: expired / commented-out / block-style never allow.
    leak = _plant_secret(egress_root / "fixtures" / "sample_key.py")

    def plan(name):
        """A fresh plan dir per case — a refused session is left BLOCKED."""
        (tmp_path / name).mkdir()
        return make_plan(tmp_path / name, _codex_session())

    ssot("anthropic", allowlist=_allow_line(str(leak)))
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Expired entry is dead.
    ssot("anthropic", allowlist=_allow_line(str(leak), expiry="2020-01-01"))
    assert "sample_key.py" in _refuses(plan("p2"), capsys)

    # Commented-out entry never allows.
    ssot("anthropic", allowlist="      # " + _allow_line(str(leak)).strip())
    assert "sample_key.py" in _refuses(plan("p3"), capsys)

    # Block-style entry deliberately does not parse (fails closed).
    ssot("anthropic", allowlist=(f'      - path: "{leak}"\n'
                                 "        approved_by: test-operator\n"
                                 "        expiry: 2099-01-01"))
    assert "sample_key.py" in _refuses(plan("p4"), capsys)

    # And it clears only THAT file — a second leak elsewhere still refuses.
    ssot("anthropic", allowlist=_allow_line(str(leak)))
    _plant_secret(egress_root / "other.py")
    assert "other.py" in _refuses(plan("p5"), capsys)


def test_repo_gitleaksignore_is_actually_honored(tmp_path, capsys, ssot, egress_root):
    # The SSOT and the contract both said the scanned tree's own `.gitleaksignore`
    # is honored via gitleaks' `-i`. It never was: `-i` matches gitleaks' OWN
    # fingerprints, built from the path it was handed, and run.py hands it
    # ABSOLUTE paths — while `gitleaks protect --staged` (the commit hook, the
    # thing that writes those pins) produces repo-RELATIVE ones. Measured on this
    # repo 2026-07-28: `gitleaks dir .` honors all 16 pins and reports 0 findings;
    # the identical tree scanned by absolute path honors 0 and reports all 16. So
    # every repo carrying a `.gitleaksignore` was permanently DO-NOT-SEND, for a
    # reason no message ever mentioned.
    leak = _plant_secret(egress_root / "fixtures" / "sample_key.py")
    ssot("anthropic")

    def plan(name):
        (tmp_path / name).mkdir()
        return make_plan(tmp_path / name, _codex_session())

    reason = _refuses(plan("p1"), capsys)
    rule, line = re.search(r"gitleaks rule (\S+), line (\d+)\)", reason).groups()
    rel = leak.relative_to(egress_root)

    # A WRONG pin (right file, wrong line) must not clear it — proves the match is
    # the fingerprint, not the filename.
    (egress_root / ".gitleaksignore").write_text(f"# pinned\n{rel}:{rule}:999\n")
    assert "sample_key.py" in _refuses(plan("p2"), capsys)

    # The real fingerprint, in the exact form the commit hook writes, clears it.
    (egress_root / ".gitleaksignore").write_text(f"# pinned\n{rel}:{rule}:{line}\n")
    p3 = plan("p3")
    by_id, _ = _begin(p3, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(p3)


def test_allowlist_never_clears_a_dot_env(tmp_path, capsys, ssot, egress_root):
    # The `.env*` filename rule is not allowlistable — a .env is a decision, not
    # a guess. Only a whole-repo egress_opt_ins entry clears it.
    env = egress_root / ".env"
    env.write_text("FOO=bar\n")
    ssot("anthropic", allowlist=_allow_line(str(env)))
    assert ".env" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_missing_gitleaks_fails_closed(tmp_path, capsys, ssot, egress_root, monkeypatch):
    # DEPENDENCY CONTROL. With no scanner on PATH the guard cannot know whether
    # the tree is clean, so it must REFUSE — never silently pass, never invent a
    # fallback scanner. The refusal has to name the missing dependency.
    (egress_root / "app.py").write_text("print('hello')\n")   # demonstrably clean tree
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    ssot("anthropic")
    reason = _refuses(make_plan(tmp_path, _codex_session()), capsys)
    assert "gitleaks" in reason and "PATH" in reason, reason
    assert "brew install gitleaks" in reason, "the refusal must say how to fix it"


def test_content_scan_can_fail(tmp_path, capsys, ssot, egress_root, monkeypatch):
    # FALSIFICATION CONTROL (house pattern: test_schema_hardening.py § 13). Swap
    # the content scan for a pass-through and the SAME planted tree must stop
    # refusing. If it still refuses, the plant test above proves nothing.
    _plant_secret(egress_root / "config.py")
    ssot("anthropic")
    monkeypatch.setattr(run, "_content_scan", lambda *a, **kw: None)
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex", (
        "with the content scan neutered the planted tree must dispatch — it did not, "
        "so the plant test is not proving the content scan"
    )
    run.cmd_release(plan_dir)


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
    _plant_secret(egress_root / "config.py")
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


# ==========================================================================
# CP-02 — `--harness codex`: the SECOND driver.
#
# Under --harness codex the orchestrator IS a Codex session: it runs each
# dispatch command in its own shell and feeds the -o file straight back through
# `apply`. Every ready session must therefore get a RUNNABLE command — including
# Claude-pinned ones, translated through the SSOT with a receipt that names what
# the translation lost. Contract + probe evidence:
# skills/plan-execute/references/dual-harness-contract.md.
# ==========================================================================
def test_harness_codex_emits_a_command_for_every_session(tmp_path, capsys, ssot, egress_root):
    # `lane_scoped` on purpose: require_calibrated is WAIVED under this harness
    # (D4a) — it guards an IMPLICIT execution default, and a typed --harness
    # codex is a per-run election. Left in place it would block 100% of sessions.
    ssot("anthropic", openai_status="lane_scoped")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol",
         "reasoning": "high"},                                    # Codex-pinned → passthrough
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus",
         "reasoning": "high", "task_class": "agentic_build",
         "dispatch": {"subagent_type": "tier-opus-high"}},        # Claude-pinned → translated
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out, err = _begin_codex(plan_dir, ["s01", "s02"], capsys)

    assert out["action"] == "dispatch"
    assert out["harness"] == "codex"

    for sid in ("s01", "s02"):
        m = by_id[sid]
        assert m["backend"] == "codex"
        # ONE-layer dispatch: no wrapper agent, so no wrapper prompt and no Task model.
        assert "wrapper_prompt" not in m and "prompt_text" not in m
        assert "model_arg" not in m
        # The command is runnable as-is, pinned to the scanned tree.
        assert m["dispatch_cmd"].startswith(f"cd {egress_root} && ")
        for flag in ("codex exec", "--ignore-user-config", "--ignore-rules",
                     "--sandbox workspace-write", "-o ", " - < "):
            assert flag in m["dispatch_cmd"], (sid, flag)
        # § 3.6 crash recovery: the -o file lives INSIDE the plan dir, per attempt.
        lm = Path(m["last_message_file"])
        assert lm.parent == Path(plan_dir) / "_codex"
        assert lm.name.startswith(f"{sid}.") and lm.name.endswith(".last-message.txt")
        assert m["last_message_file"] in m["dispatch_cmd"]
        assert m["executor_family"] == "openai"
        assert m["verifier_family"] == "anthropic"
        assert m["verifier_mode"] == "on_box_human"   # D4e recommended default

    # gpt-pinned: passes through untranslated.
    assert by_id["s01"]["codex_model"] == "gpt-5.6-sol"
    assert by_id["s01"]["codex_effort"] == "xhigh"
    assert by_id["s01"]["translated_from"] is None
    # opus-pinned: translated via the SSOT, carrying the receipt. s03 (2026-08-13):
    # the session declares task_class agentic_build, so the route comes from
    # (task_class, openai) -> apex_reasoner -> sol@xhigh, NOT from matching the
    # Claude tier name `frontier_reasoner` against an OpenAI tier of the same name.
    # The tier-NAME fallback still covers a session that declares no task_class —
    # pinned by test_translation_receipt_names_what_was_lost below, whose s01 has none.
    assert by_id["s02"]["codex_model"] == "gpt-5.6-sol"
    assert by_id["s02"]["codex_effort"] == "xhigh"
    assert by_id["s02"]["translated_from"] == {"model": "Opus", "reasoning": "high"}
    assert "harness=codex  translate s02:" in err

    # The dispatch breadcrumb recovery reads (§ 3.6) landed in run.ndjson.
    events = [json.loads(ln) for ln in (Path(plan_dir) / "run.ndjson").read_text().splitlines()]
    dispatched = {e["session_ids"][0]: e for e in events if e["event"] == "codex_dispatch"}
    assert set(dispatched) == {"s01", "s02"}
    assert dispatched["s02"]["last_message_file"] == by_id["s02"]["last_message_file"]
    started = [e for e in events if e["event"] == "dispatch_started"][-1]
    assert started["harness"] == "codex"
    assert [e for e in events if e["event"] == "codex_translation"]
    run.cmd_release(plan_dir)


def test_translation_receipt_names_what_was_lost(tmp_path, capsys, ssot):
    # § 4.3: fidelity is COMPUTED from the effort.map row, never asserted — a
    # receipt that always prints the same reassuring line is a gate that cannot fail.
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus", "reasoning": "high",
         "dispatch": {"subagent_type": "tier-opus-high"}},   # frontier_reasoner row is flat
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Fable", "reasoning": "medium"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _, err = _begin_codex(plan_dir, ["s01", "s02"], capsys)

    m = by_id["s01"]
    assert m["effort_fidelity"] == "flat_map"      # terra maps every intent to max
    tr = m["translation"]
    assert tr["tier"] == "frontier_reasoner" and tr["intent"] == "thorough"
    assert tr["calibration_status"] == "researched"
    # The Claude-only dispatch field is DROPPED, and the drop is named (§ 4.4).
    assert any("tier-opus-high" in d for d in tr["dropped"])
    assert "does NOT survive translation" in tr["receipt"]
    assert "Opus · high  ->  gpt-5.6-terra · max" in tr["receipt"]
    assert tr["receipt"] in err

    # A tier whose row is NOT flat and whose intent has its own cell: exact.
    assert by_id["s02"]["effort_fidelity"] == "exact"      # sol standard → high
    assert by_id["s02"]["codex_effort"] == "high"
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# CL-03 — effort unclamp: `max` means max.
# --------------------------------------------------------------------------
def test_manifest_max_reaches_codex_natively(tmp_path, capsys, ssot):
    # Before CL-03 _INTENT_FROM_REASONING sent BOTH xhigh and max to `thorough`,
    # so a session asking for maximum thinking could only ever land on the tier's
    # `thorough` cell (sol xhigh). `max` is a REAL rung: `codex debug models` on
    # codex-cli 0.145.0 lists low|medium|high|xhigh|max|ultra for gpt-5.6-sol.
    ssot("anthropic")
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol", "reasoning": "max"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Fable", "reasoning": "max"},
        {"id": "s03", "title": "S3", "items": ["i3"], "model": "gpt-5.6-sol", "reasoning": "xhigh"},
        {"id": "s04", "title": "S4", "items": ["i4"], "model": "Sonnet", "reasoning": "max",
         "task_class": "standard_build"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _, _ = _begin_codex(plan_dir, ["s01", "s02", "s03", "s04"], capsys)

    for sid in ("s01", "s02"):
        assert by_id[sid]["codex_model"] == "gpt-5.6-sol"
        assert by_id[sid]["codex_effort"] == "max", sid
        assert "model_reasoning_effort=max" in by_id[sid]["dispatch_cmd"]
        assert by_id[sid]["effort_fidelity"] == "exact"   # no lower intent reaches max
    # xhigh keeps its OWN rung — unclamping must not over-promote it to max either.
    assert by_id["s03"]["codex_effort"] == "xhigh"
    # THE CLAMP LEG RETIRED WITH ITS TIER (v1.15/s03). It used to assert that a
    # Sonnet session landed on gpt-5.5 @ xhigh — that model's native ceiling, so a
    # `max` request was clamped and SAID so. gpt-5.5 is retired and the whole
    # `workhorse` tier with it: every model in the 5.6-only lane reaches `max`, so
    # nothing clamps any more, and asserting a clamp would be asserting a fiction.
    # What s04 pins instead is the OTHER half of the same change — a Sonnet session
    # still ROUTES, now via (task_class, openai) rather than a tier name that no
    # longer has a counterpart. standard_build -> cheap_fast -> luna, whose curve
    # collapses below max, so `max` is honest here rather than clamped.
    assert by_id["s04"]["codex_model"] == "gpt-5.6-luna"
    assert by_id["s04"]["codex_effort"] == "max"
    assert by_id["s04"]["effort_fidelity"] == "flat_map"
    run.cmd_release(plan_dir)


def test_intent_rung_missing_from_effort_map_silently_downgrades(tmp_path, capsys, ssot):
    # THE TRAP the SSOT-lockstep comment on _INTENT_FROM_REASONING names, proved
    # rather than asserted: resolve_route's native_effort() falls back to the
    # row's `standard` cell for an UNKNOWN intent, so a run.py intent rung with no
    # SSOT cell DOWNGRADES max instead of failing. This is why the effort-map
    # extension ships in the same commit as the intent map.
    p = ssot("anthropic")
    p.write_text(p.read_text().replace(
        "apex_reasoner:     { light: medium, standard: high, thorough: xhigh, "
        "exhaustive: xhigh, maximal: max   }",
        "apex_reasoner:     { light: medium, standard: high, thorough: xhigh }"))
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol",
                 "reasoning": "max"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["codex_effort"] == "high"   # the `standard` cell — silently
    run.cmd_release(plan_dir)
    capsys.readouterr()

    # ALLOW CONTROL: the same session against the shipped map reaches max.
    ssot("anthropic")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    by_id2, _, _ = _begin_codex(plan_dir2, ["s01"], capsys)
    assert by_id2["s01"]["codex_effort"] == "max"
    run.cmd_release(plan_dir2)


def test_retired_gpt55_pin_blocks_with_guidance(ssot):
    """A legacy `gpt-5.5` manifest pin is BLOCKED-WITH-GUIDANCE, the behaviour
    providers.openai's v1.15 entry declares. Before this the raise was the generic
    "resolves to neither a providers.openai model ... nor a translatable Claude tier
    token", which named neither `amend-session` nor any replacement — so the SSOT
    documented a message the code did not have."""
    p = ssot("openai")
    retired = p.read_text()                    # the real 5.6-only three-tier lineup
    # Checked against the PARSED lineup, not the raw text: the fixture's own prose
    # names gpt-5.5 to explain why it is gone, and a whole-text assertion would
    # trip on the explanation rather than on the lineup.
    _rr = run._import_resolver()
    assert "gpt-5.5" not in _rr._Profile(retired, "openai").models.values()

    # KNOWN-NEGATIVE CONTROL, now built the other way round (s03, 2026-08-13): the
    # fixture itself is the retired lineup, so the control RE-ADDS a synthetic
    # `workhorse: gpt-5.5` tier and proves the pin RESOLVES there. Without it this
    # test would pass even if `gpt-5.5` were unresolvable for some unrelated reason
    # — the block has to come from the RETIREMENT, not from an absent tier.
    legacy = retired.replace(
        "      cheap_fast:        gpt-5.6-luna\n",
        "      cheap_fast:        gpt-5.6-luna\n      workhorse:         gpt-5.5\n",
    ).replace(
        "        cheap_fast:        { light: max,    standard: max,  thorough: max,   "
        "exhaustive: max,   maximal: max   }\n",
        "        cheap_fast:        { light: max,    standard: max,  thorough: max,   "
        "exhaustive: max,   maximal: max   }\n"
        "        workhorse:         { light: medium, standard: high, thorough: xhigh, "
        "exhaustive: xhigh, maximal: xhigh }\n",
    )
    assert legacy != retired, "the legacy-lineup control did not apply"
    assert run._resolve_codex_dispatch("gpt-5.5", "medium", legacy)[0] == "gpt-5.5"

    with pytest.raises(run.UnroutableCodexSession) as ei:
        run._resolve_codex_dispatch("gpt-5.5", "medium", retired)
    msg = str(ei.value)
    assert "RETIRED" in msg
    assert "amend-session" in msg                                  # the fix, named
    for repl in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):  # the replacements, named
        assert repl in msg, f"block message does not name {repl}: {msg}"


def test_luna_has_a_fallback_rung(tmp_path, capsys, ssot):
    # Before CL-03 a haiku-pinned session translated to luna@max had a NULL
    # fallback: one refused mechanical session took the whole run to NO-CODEX.
    # v1.15 (2026-08-13) RE-POINTED that rescue: gpt-5.5 is retired with the whole
    # `workhorse` tier, so luna now rescues UP to terra@max. The invariant this test
    # exists for is unchanged — the BOTTOM tier must never have a null fallback.
    assert run._fallback_for("gpt-5.6-luna", "openai") == ("gpt-5.6-terra", "max")
    # ...and terra is the SINK, so the rescue cannot become a terra->luna->terra loop.
    # (It is also what keeps judgement work off the mechanical-only luna: nothing
    # degrades ONTO luna at all — providers.openai.escalation.invariants.)
    assert run._fallback_for("gpt-5.6-terra", "openai") is None
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Haiku",
                 "reasoning": "low"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["codex_model"] == "gpt-5.6-luna"
    assert m["codex_effort"] == "max"              # luna collapses below max
    assert m["effort_fidelity"] == "flat_map"
    assert m["fallback_model"] == "gpt-5.6-terra"
    assert m["fallback_reasoning"] == "max"
    assert "-m gpt-5.6-luna" in m["dispatch_cmd"]
    assert "-m gpt-5.6-terra" in m["fallback_cmd"]
    assert "model_reasoning_effort=max" in m["fallback_cmd"]
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# D4c — barred work: ALLOWED on Codex, never dispatched unsupervised.
# --------------------------------------------------------------------------
def test_barred_session_checkpoints_before_dispatch_then_runs_on_resume(
    tmp_path, capsys, ssot
):
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
                 "reasoning": "high", "task_class": "linchpin"}]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01"], harness="codex")
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "checkpoint"
    assert out["sessions"] == ["s01"]
    brief = out["checkpoint_briefs"]["s01"]
    assert "barred from unsupervised Codex execution" in brief
    # linchpin -> apex_reasoner -> sol. `high` maps to thorough = xhigh, but the
    # linchpin row itself prescribes `maximal` = max and the class floors it there.
    assert "gpt-5.6-sol · max" in brief            # names the model it WOULD run on
    assert "no `--harness`" in brief               # ...and the Claude-side alternative
    assert run._statuses(plan_dir)["s01"] == "AWAITS_REVIEW"   # NOT DOING
    # Nothing dispatched and no lock left behind.
    rsi.acquire_lock(plan_dir)
    rsi.release_lock(plan_dir)

    # The human said go (`plan --resume` re-offers an AWAITS_REVIEW session): the
    # answered gate is the status itself, so the second begin dispatches.
    by_id, out2, _ = _begin_codex(plan_dir, ["s01"], capsys)
    assert out2["action"] == "dispatch"
    assert by_id["s01"]["backend"] == "codex"
    assert run._statuses(plan_dir)["s01"] == "DOING"
    run.cmd_release(plan_dir)


def test_barred_batch_dispatches_nothing_while_the_human_decides(tmp_path, capsys, ssot):
    ssot("anthropic")
    sessions = [
        # Both carry a task_class: since the 5.6-only collapse a Claude tier NAME has
        # no guaranteed counterpart on the OpenAI lane, so a translated session
        # resolves from (task_class, openai) — see the block message in run.py.
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
         "task_class": "standard_build"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus",
         "task_class": "agentic_build", "dispatch": {"guards_irreversible": True}},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    run.cmd_begin(plan_dir, ["s01", "s02"], harness="codex")
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "checkpoint"
    statuses = run._statuses(plan_dir)
    assert statuses["s02"] == "AWAITS_REVIEW"
    assert statuses["s01"] == "TODO"          # no half-run batch
    rsi.acquire_lock(plan_dir)
    rsi.release_lock(plan_dir)


def test_explicit_codex_pin_on_barred_work_still_blocks_under_codex_harness(
    tmp_path, capsys, ssot
):
    # The one thing --harness codex does NOT relax: a plan that asks for
    # unsupervised Codex on work it declared unsafe for it contradicts itself,
    # and only the plan author can resolve that.
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol",
                 "task_class": "linchpin"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"], harness="codex")
    out = json.loads(capsys.readouterr().out)
    assert "barred" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"


# --------------------------------------------------------------------------
# THE CONTROL (abort condition): the Claude harness must NEVER auto-route
# linchpin / irreversible work to Codex — that is what this whole feature is
# forbidden to change.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("session_extra", [
    {"task_class": "linchpin"},
    {"task_class": "agentic_build", "peer_triggers": ["irreversible_change"],
     "prompt": "run /adversarial-review before finalizing"},
    {"task_class": "agentic_build", "dispatch": {"guards_irreversible": True}},
])
def test_claude_harness_never_auto_routes_barred_work_to_codex(
    tmp_path, capsys, ssot, session_extra
):
    # Maximum pressure: the dial says openai AND the class is opted in AND the
    # session is Claude-pinned. Without --harness, every one of these must still
    # execute on Claude.
    ssot("openai", executor_for="linchpin, agentic_build")
    sessions = [dict({"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
                      "reasoning": "high"}, **session_extra)]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, out = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "claude"
    assert "codex_cmd" not in m and "dispatch_cmd" not in m
    assert "barred" in m["executor_policy_note"]
    assert "harness" not in out
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# § 4.4 fork, D1 sandbox guard, D4d turn-one egress disclosure
# --------------------------------------------------------------------------
def test_fork_session_blocks_under_codex_harness(tmp_path, capsys, ssot):
    # `fork` means "this session needs the orchestrator's LIVE conversation
    # context", which codex exec reading a prompt file cannot reproduce.
    # Downgrading it silently would hand the session a dependency it was
    # authored to rely on and not given.
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
                 "dispatch": {"subagent_type": "fork"}}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"], harness="codex")
    out = json.loads(capsys.readouterr().out)
    assert "fork" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"

    # ALLOW CONTROL: the same fork session on the Claude harness is untouched.
    ssot("anthropic")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    by_id, _ = _begin(plan_dir2, ["s01"], capsys)
    assert by_id["s01"]["subagent_type"] == "fork"
    assert by_id["s01"]["backend"] == "claude"
    run.cmd_release(plan_dir2)


def test_codex_sandbox_env_refuses_begin(tmp_path, capsys, ssot, monkeypatch):
    # D1: the ONE surviving env check is a NEGATIVE guard, not detection —
    # CODEX_SANDBOX is set exactly in the mode where a nested codex exec dies
    # ("failed to initialize in-process app-server client", contract probe P2).
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    monkeypatch.setenv(run._CODEX_SANDBOX_ENV, "seatbelt")
    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan_dir, ["s01"], harness="codex")
    assert "danger-full-access" in str(exc.value)
    assert run._statuses(plan_dir)["s01"] == "TODO"       # nothing mutated

    # ALLOW CONTROL — and the proof it is not detection: the SAME variable does
    # not affect the Claude harness at all.
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"            # explicit pin, Claude harness
    run.cmd_release(plan_dir)


def test_plan_harness_codex_discloses_egress_on_turn_one(tmp_path, capsys, ssot, egress_root):
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus"}]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_plan(plan_dir, False, None, False, "codex")
    out = json.loads(capsys.readouterr().out)
    assert out["harness"] == "codex"
    assert out["egress"] == {"root": str(egress_root), "restricted_hit": None,
                             "opted_in": False}

    # A restricted tree is named BEFORE any dispatch — by then the orchestrator
    # (itself a Codex process) has already read the tree, so this is the only
    # moment the disclosure can still help.
    _plant_secret(egress_root / "settings" / "k.json")
    run.cmd_plan(plan_dir, False, None, False, "codex")
    out = json.loads(capsys.readouterr().out)
    assert "k.json" in out["egress"]["restricted_hit"]
    assert out["egress"]["opted_in"] is False

    # The Claude harness payload is untouched by any of it.
    run.cmd_plan(plan_dir, False, None, False)
    out = json.loads(capsys.readouterr().out)
    assert "egress" not in out and "harness" not in out


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
_CLOSEOUT = (
    '<plan-execute-closeout>\n'
    '{"session":"s01","result":"DONE","items_completed":["i1"],'
    '"items_blocked":[],"notes":{},"dispatch_next":false,'
    '"human_checkpoint_reason":null}\n'
    '</plan-execute-closeout>'
)


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
    """No codex_shell (and an empty one) => the pre-2026-07-29 command exactly."""
    ssot("anthropic")
    base = run._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
                          workdir="/repo")
    assert "--sandbox workspace-write" in base
    assert "sandbox_workspace_write" not in base          # no -c overrides at all
    for shell in (None, {}, {"writable_roots": [], "network": False}):
        session = {"id": "s01", "dispatch": ({"codex_shell": shell} if shell is not None else {})}
        grant = run._codex_shell_grant(session)
        assert grant is None, shell
        assert run._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
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


def test_codex_shell_passes_only_declared_environment_names():
    session = {
        "id": "s01",
        "dispatch": {"codex_shell": {"env_include": ["ANTHROPIC_API_KEY"]}},
    }
    grant = run._codex_shell_grant(session)
    cmd = run._codex_cmd(
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
    with pytest.raises(run.UngatedFullAccessSession) as ei:
        run._assert_full_access_gated("s01", ungated, run._codex_shell_grant(ungated))
    assert "no human gates it" in str(ei.value)
    # It must be treated as unroutable by every existing handler, not swallowed.
    assert isinstance(ei.value, run.UnroutableCodexSession)

    gated = dict(ungated)
    gated["dispatch"] = {**shell, "guards_irreversible": True}
    grant = run._codex_shell_grant(gated)
    run._assert_full_access_gated("s01", gated, grant)     # does not raise
    cmd = run._codex_cmd("gpt-5.6-terra", "max", "/p/s01.prompt.md", "/tmp/lm.txt",
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
    with pytest.raises(run.UnroutableCodexSession):
        run._codex_shell_grant(session)


def test_codex_shell_rejects_wildcard_environment_grants():
    session = {"id": "s01", "dispatch": {"codex_shell": {"env_include": ["*_API_KEY"]}}}
    with pytest.raises(run.UnroutableCodexSession, match="environment variable names"):
        run._codex_shell_grant(session)


# --------------------------------------------------------------------------
# ESC-03 — the CODEX effort climb (s04). Same engine as the Claude lane
# (`escalation.compute` -> `resolve_route.escalate`), walked on
# `providers.openai`, applied to the RESOLVED (codex_model, codex_effort) cell.
#
# Every case below is PAIRED — a PLANT (the wrong rung must be detected) and an
# ALLOW CONTROL (the right one must not trip) — for the reason the escalation
# suite states: a check that only ever sees the passing case cannot fail.
# --------------------------------------------------------------------------
import shlex  # noqa: E402

import article_block as ab  # noqa: E402
import escalation as esca  # noqa: E402
from test_escalation import _arm, _stamp  # noqa: E402

REPO_SSOT = SCRIPTS.parents[2] / "model-routing.yaml"

# The stamp `_codex_cmd` puts in the -o path is per-invocation (pid + epoch), so
# two dispatches of one session differ there for reasons that are not the climb.
_LM_STAMP = re.compile(r"codex-s\d+-\d+-\d+(-fb)?\.last-message\.txt")


def _norm(cmd):
    return _LM_STAMP.sub("LAST-MESSAGE", cmd or "")


def _codex_sess(model, **kw):
    return {"id": "s01", "title": "S1", "items": ["i1"], "model": model, **kw}


def _dispatch(plan_dir, capsys, *, arm=None):
    """One `begin` of s01, optionally after planting N same-signature failures.

    Drains the capture buffer first: `cmd_release` and the mutation below print
    their own JSON, and a stale blob in front of the batch makes `_begin`'s parse
    fail for a reason that has nothing to do with the dispatch."""
    if arm is not None:
        _arm(plan_dir, "s01", consecutive=arm, reworks=arm)
    capsys.readouterr()
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    capsys.readouterr()
    return by_id["s01"]


def test_luna_escalates_by_changing_MODEL_never_by_lowering_its_effort(tmp_path, capsys, ssot):
    """luna's accuracy COLLAPSES below max (high 44%, medium 11%), so an escalation
    FROM luna moves to the next model — it never walks luna's own effort dial,
    because luna has no effort ladder at all."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    base = _dispatch(plan_dir, capsys)                       # ALLOW CONTROL: as authored
    assert (base["codex_model"], base["codex_effort"]) == ("gpt-5.6-luna", "max")
    assert "escalated_from" not in base

    m = _dispatch(plan_dir, capsys, arm=2)                   # first escalated attempt
    assert (m["codex_model"], m["codex_effort"]) == ("gpt-5.6-terra", "max")
    # THE FLOOR, asserted mechanically rather than trusted: no rung of this climb
    # ever runs luna below max.
    assert "-m gpt-5.6-luna" not in m["codex_cmd"]
    assert "model_reasoning_effort=max" in m["codex_cmd"]


def test_terra_enters_sol_at_xhigh_never_at_sols_first_rung(tmp_path, capsys, ssot):
    """sol's effort ladder starts at `high` (69%) — BELOW the terra@max (70%) the
    session just left. `model_ladder_entry` is what makes the first sol rung an
    escalation rather than a descent; without it this test reads sol@high."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-terra")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    base = _dispatch(plan_dir, capsys)
    assert (base["codex_model"], base["codex_effort"]) == ("gpt-5.6-terra", "max")

    m = _dispatch(plan_dir, capsys, arm=2)
    assert (m["codex_model"], m["codex_effort"]) == ("gpt-5.6-sol", "xhigh")
    assert "model_reasoning_effort=high " not in m["codex_cmd"] + " "


def test_the_codex_climb_stops_at_sol_max_and_never_emits_ultra(tmp_path, capsys, ssot):
    """sol@max is the automatic ceiling (operator-approved 2026-08-13). `ultra`
    exists on sol/terra and is operator-opt-in only, so no automatic rung reaches
    it — and further failures buy nothing above the ceiling."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-sol", reasoning="high")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    base = _dispatch(plan_dir, capsys)
    assert (base["codex_model"], base["codex_effort"]) == ("gpt-5.6-sol", "xhigh")

    one = _dispatch(plan_dir, capsys, arm=2)
    assert (one["codex_model"], one["codex_effort"]) == ("gpt-5.6-sol", "max")
    assert one["escalated_from"]["rung"] == 1

    # ...and it STAYS there however many more times the same cause fails.
    for streak in (3, 4, 5):
        top = _dispatch(plan_dir, capsys, arm=streak)
        assert (top["codex_model"], top["codex_effort"]) == ("gpt-5.6-sol", "max")
        assert "ultra" not in top["codex_cmd"]


def test_an_escalated_codex_dispatch_moves_ONLY_the_model_and_effort_flags(
    tmp_path, capsys, ssot
):
    """Byte-identity of everything else. The two cases together isolate both
    halves: luna->terra moves the model with the effort unchanged, and
    sol@xhigh->sol@max moves the effort with the model unchanged."""
    ssot("anthropic")
    for pin, kw, moved in (
        ("gpt-5.6-luna", {}, {"gpt-5.6-luna"}),
        ("gpt-5.6-sol", {"reasoning": "high"}, {"model_reasoning_effort=xhigh"}),
    ):
        root = tmp_path / f"case-{pin[-4:]}{len(kw)}"
        root.mkdir()
        plan_dir = make_plan(root, [_codex_sess(pin, **kw)])
        _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
        before = shlex.split(_norm(_dispatch(plan_dir, capsys)["codex_cmd"]))
        after = shlex.split(_norm(_dispatch(plan_dir, capsys, arm=2)["codex_cmd"]))
        assert len(before) == len(after), (before, after)
        differing = {b for b, a in zip(before, after) if b != a}
        assert differing == moved, (pin, differing)


def test_escalated_from_is_recorded_on_the_codex_path_exactly_as_on_claude(
    tmp_path, capsys, ssot
):
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    m = _dispatch(plan_dir, capsys, arm=2)

    assert m["escalated_from"] == {
        "authored": {"model": "gpt-5.6-luna", "reasoning": "max"},
        "ran": {"model": "gpt-5.6-terra", "reasoning": "max"},
        "attempt": 3, "rung": 1, "generation": 0,
    }
    assert m["model_ran"] == "gpt-5.6-terra"
    assert m["reasoning_ran"] == "max"
    assert m["model_ran_source"] == "requested"      # never "attested"
    events = [json.loads(x) for x in (plan_dir / "run.ndjson").read_text().splitlines()]
    applied = [e for e in events if e["event"] == "escalation_applied"]
    assert applied and applied[-1]["ran"] == {"model": "gpt-5.6-terra", "reasoning": "max"}
    # The downward ladder is SUPPRESSED on an escalated member and replaced by the
    # refusal instruction — two instructions on one observed event is the defect.
    assert m["fallback_model"] is None
    assert m["fallback_prompt_text"] is None
    assert "record-refusal" in m["on_dispatch_refusal"]
    assert "--model gpt-5.6-terra --reasoning max" in m["on_dispatch_refusal"]
    # NO-CODEX STAYS NO-CODEX. A climb never opens a route back onto Claude: the
    # wrapper still relays `quota_exhausted` as a stop signal, and the only Claude
    # token anywhere in the member is the fixed WRAPPER model that runs the Bash
    # command — never a model the session's work could land on.
    assert "quota_exhausted" in m["prompt_text"]
    assert m["model_arg"] == run._CODEX_WRAPPER_MODEL
    assert m["executor_family"] == "openai"
    for claude in ("fable", "opus", "haiku"):
        assert claude not in m["codex_cmd"]


def test_the_codex_HARNESS_path_climbs_too(tmp_path, capsys, ssot):
    """The other codex dispatch site. `--harness codex` builds its command in
    `_codex_harness_spec` rather than in `cmd_begin`'s branch, and a climb wired
    into only one of the two is the same lane-shaped gap ESC-03 exists to close."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)     # ALLOW CONTROL
    assert by_id["s01"]["codex_model"] == "gpt-5.6-luna"
    assert "escalated_from" not in by_id["s01"]
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)

    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    capsys.readouterr()
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert (m["codex_model"], m["codex_effort"]) == ("gpt-5.6-terra", "max")
    assert "-m gpt-5.6-terra" in m["dispatch_cmd"]
    assert m["escalated_from"]["ran"] == {"model": "gpt-5.6-terra", "reasoning": "max"}
    assert m["fallback_cmd"] is None and m["fallback_model"] is None
    assert "record-refusal" in m["on_dispatch_refusal"]
    run.cmd_release(plan_dir)


def test_a_refused_escalated_codex_rung_falls_back_to_the_PREVIOUS_rung(
    tmp_path, capsys, ssot
):
    """The refusal rule, through the REAL interface. A refused rung re-dispatches
    one rung DOWN — floored at the authored cell, never onto the downward fallback
    ladder — costs no rework budget, and is never proposed again."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["codex_model"] == "gpt-5.6-sol"       # 2 rungs up
    assert by_id["s01"]["codex_effort"] == "xhigh"
    run.cmd_release(plan_dir)

    run.cmd_record_refusal(plan_dir, "s01", "gpt-5.6-sol", "xhigh",
                           "codex exec: model not available", "wrapper")
    capsys.readouterr()
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert (m["codex_model"], m["codex_effort"]) == ("gpt-5.6-terra", "max")
    assert "gpt-5.6-sol" not in m["codex_cmd"]
    run.cmd_release(plan_dir)

    # PLANT: a rung that is not on THIS session's openai ladder is refused loudly,
    # rather than recorded as a key that matches nothing.
    with pytest.raises(SystemExit, match="not an escalated rung"):
        run.cmd_record_refusal(plan_dir, "s01", "fable", "medium", "nope", "wrapper")


def test_a_refused_rung_never_comes_back_as_the_downward_FALLBACK(tmp_path, capsys, ssot):
    """[HARDENED:r3-deep-review] The refused set is AUTHORITATIVE over the downward
    edge, on both codex dispatch paths.

    `_refusal_instruction` already settles the fight one level up: an ESCALATED
    member carries no `fallback_model` at all, so the two instructions never fire on
    one observed event. The same fight repeats one level down and nothing settled
    it: a step-down off a refused rung that lands back on the AUTHORED cell is no
    longer escalated, so that suppression lapses — and the static fallback ladder,
    which knows nothing about refusals, handed the operator the very rung they had
    just refused, as a ready-to-run `codex exec`.

    Asserted on the EMITTED COMMANDS, never on the refused set itself: a rung that
    never reaches a command line is what this is about."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    # ALLOW CONTROL: with nothing refused, luna's downward edge IS terra@max and it
    # ships a runnable command. (Without this the assertions below could pass on a
    # session that simply never had a fallback.)
    base = _dispatch(plan_dir, capsys)
    assert (base["fallback_model"], base["fallback_reasoning"]) == ("gpt-5.6-terra", "max")
    assert "-m gpt-5.6-terra" in base["fallback_prompt_text"]

    up = _dispatch(plan_dir, capsys, arm=2)                  # climbs onto terra@max
    assert (up["codex_model"], up["codex_effort"]) == ("gpt-5.6-terra", "max")
    run.cmd_record_refusal(plan_dir, "s01", "gpt-5.6-terra", "max",
                           "codex exec: model not available", "wrapper")
    capsys.readouterr()

    # The step-down floors at the authored cell, so this member is NOT escalated —
    # the only mechanism left that can hold the refusal is the fallback edge itself.
    back = _dispatch(plan_dir, capsys)
    assert (back["codex_model"], back["codex_effort"]) == ("gpt-5.6-luna", "max")
    assert "escalated_from" not in back
    assert back["fallback_model"] is None and back["fallback_reasoning"] is None
    assert back["fallback_prompt_text"] is None
    for field in ("codex_cmd", "prompt_text", "fallback_prompt_text"):
        assert "gpt-5.6-terra" not in (back.get(field) or ""), field

    # THE OTHER DISPATCH PATH, same session, same refusal: `--harness codex` builds
    # its fallback in `_codex_harness_spec`, and a guard wired into only one of the
    # two is the lane-shaped gap this plan exists to close.
    by_id, _, _ = _begin_codex(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["codex_model"] == "gpt-5.6-luna"
    assert m["fallback_model"] is None and m["fallback_cmd"] is None
    assert "gpt-5.6-terra" not in m["dispatch_cmd"]
    run.cmd_release(plan_dir)


def test_an_escalated_receipt_attributes_the_raise_to_the_CLIMB_not_the_task_class(
    tmp_path, capsys, ssot
):
    """[HARDENED:r3-deep-review] The translation receipt describes the cell the
    SSOT resolved, and reports the climb as its own raise.

    Built from the POST-climb pair it stated a FALSE cause and persisted it as
    `effort_fidelity` on the member and in the `codex_translation` event: an
    escalation-raised effort read as `raised_by_class` — attributing it to a
    task_class row that prescribes nothing of the sort — and a climb that changed
    the MODEL read as a passthrough of a model the session never pinned."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-sol", reasoning="high")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)

    # CONTROL — the un-escalated receipt, unchanged: sol@thorough has its own cell.
    by_id, _, err = _begin_codex(plan_dir, ["s01"], capsys)
    ctl = by_id["s01"]
    assert ctl["effort_fidelity"] == "exact"
    assert ctl["translation"]["base"] == {"model": "gpt-5.6-sol", "effort": "xhigh"}
    assert ctl["translation"]["escalated_to"] is None
    assert "ESCALATION climb" not in err
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)

    _arm(plan_dir, "s01", consecutive=2, reworks=2)           # -> sol@max, rung 1
    capsys.readouterr()
    by_id, _, err = _begin_codex(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert (m["codex_model"], m["codex_effort"]) == ("gpt-5.6-sol", "max")

    tr = m["translation"]
    assert m["effort_fidelity"] == "raised_by_escalation"
    assert tr["effort_fidelity"] == "raised_by_escalation"
    assert tr["base"] == {"model": "gpt-5.6-sol", "effort": "xhigh"}
    assert tr["escalated_to"] == {"model": "gpt-5.6-sol", "effort": "max", "rung": 1}
    # The row is printed as the SSOT writes it, and the raise is named as the CLIMB.
    assert "effort.map.apex_reasoner.thorough = xhigh" in tr["receipt"]
    assert "ESCALATION climb (rung 1" in tr["receipt"]
    assert "raised_by_class" not in tr["receipt"]
    assert "by the session's task_class" not in tr["receipt"]
    assert tr["receipt"] in err

    # PERSISTED, not just printed — s05's ledger reads the event, not the stderr.
    events = [json.loads(x) for x in (plan_dir / "run.ndjson").read_text().splitlines()]
    ev = [e for e in events if e["event"] == "codex_translation"][-1]
    assert ev["effort_fidelity"] == "raised_by_escalation"
    assert ev["base"] == {"model": "gpt-5.6-sol", "effort": "xhigh"}
    assert ev["escalated_to"] == {"model": "gpt-5.6-sol", "effort": "max", "rung": 1}
    assert (ev["codex_model"], ev["codex_effort"]) == ("gpt-5.6-sol", "max")
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)

    # A climb that changes the MODEL: the head line must still name the cell the
    # session actually pins, not the rung the climb reached.
    root = tmp_path / "climbs-model"
    root.mkdir()
    other = make_plan(root, [_codex_sess("gpt-5.6-luna")])
    _stamp(other, esca.ESCALATION_MIN_SCHEMA)
    _arm(other, "s01", consecutive=2, reworks=2)
    capsys.readouterr()
    by_id, _, _ = _begin_codex(other, ["s01"], capsys)
    tr = by_id["s01"]["translation"]
    assert by_id["s01"]["codex_model"] == "gpt-5.6-terra"
    assert tr["tier"] == "cheap_fast"                        # luna's tier, not terra's
    assert "passthrough s01:  gpt-5.6-luna · max" in tr["receipt"]
    assert "RAISED to gpt-5.6-terra · max by the ESCALATION climb" in tr["receipt"]
    run.cmd_release(other)


def test_a_v5_manifest_never_escalates_a_CODEX_member(tmp_path, capsys, ssot):
    """The version gate covers both lanes. A plan built before ESC-03 dispatches
    its codex members byte-for-byte as it did — including the fallback rung, which
    an escalated member suppresses."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-sol", reasoning="high")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA - 1)
    frozen = _dispatch(plan_dir, capsys)
    armed = _dispatch(plan_dir, capsys, arm=3)
    assert _norm(armed["codex_cmd"]) == _norm(frozen["codex_cmd"])
    assert "escalated_from" not in armed
    assert armed["fallback_model"] == "gpt-5.6-terra"          # ladder untouched
    assert "on_dispatch_refusal" not in armed

    # ALLOW CONTROL: the SAME state on a v6 plan climbs.
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    assert _dispatch(plan_dir, capsys, arm=3)["codex_model"] == "gpt-5.6-sol"
    assert _dispatch(plan_dir, capsys, arm=3)["codex_effort"] == "max"


def test_the_escalated_dispatch_is_gated_exactly_like_the_first(
    tmp_path, capsys, ssot, egress_root
):
    """FAIL-CLOSED RULES ARE NOT WAIVED BY A CLIMB. The egress guard refuses an
    escalated dispatch the same as a first one — the escalated command is built
    AFTER the guard, so a restricted tree never produces one at all."""
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, [_codex_sess("gpt-5.6-luna")])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    _plant_secret(egress_root / "config.py")
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "blocked"
    assert "DO-NOT-SEND to Codex" in out["unroutable"]["s01"]


def test_an_escalated_codex_session_still_fails_closed_to_claude_when_ineligible(
    tmp_path, capsys, ssot
):
    """executor_policy gates the LANE, and the climb never re-opens it: a session
    whose task_class is not opted in dispatches on CLAUDE under an openai dial —
    and therefore climbs the ANTHROPIC ladder, not the OpenAI one."""
    ssot("openai", executor_for="mechanical")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Opus",
                 "reasoning": "high", "task_class": "agentic_build"}]
    plan_dir = make_plan(tmp_path, sessions)
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "claude"
    assert m["model_arg"] == "fable"                    # opus@high -> fable@medium
    assert not m["escalated_from"]["ran"]["model"].startswith("gpt-")
    run.cmd_release(plan_dir)


def test_no_derivable_codex_dispatch_ever_names_a_retired_model(monkeypatch):
    """[HARDENED:r2-verify-r1] The OpenAI lane is 5.6-ONLY. Rather than clamp a
    gpt-5.5 rung, the model is RETIRED — so the assertion is that no cell this
    repo's SSOT can produce, by ANY route, names gpt-5.5 or gpt-5.4.

    Routes walked: every task_class baseline, every rung of the escalation ladder
    above each of them, and every reactive-degradation target. Each is rendered
    into the real `codex exec` command, because a model name that never reaches a
    command line is not what the directive is about."""
    monkeypatch.setenv(run._SSOT_ENV, str(REPO_SSOT))
    rr = run._import_resolver()
    ssot_text = REPO_SSOT.read_text()
    profile = rr._Profile(ssot_text, "openai")
    classes = ["mechanical", "standard_build", "agentic_build", "deep_reasoning", "linchpin"]

    cells, seen_models = [], set()
    for cls in classes:
        cur = rr.resolve(cls, "openai", ssot_path=str(REPO_SSOT))
        for _ in range(12):
            if cur == rr.EXHAUSTED:
                break
            cells.append(cur)
            seen_models.add(cur["model_id"])
            fb = run._fallback_for(cur["model_id"], "openai")
            if fb:
                cells.append({"model_id": fb[0], "native_effort": fb[1]})
                seen_models.add(fb[0])
            cur = rr.escalate(cls, "openai", current=cur, ssot_path=str(REPO_SSOT))
        else:
            pytest.fail(f"the openai ladder from {cls} did not terminate in 12 rungs")

    assert cells, "walked nothing — an empty walk would pass this test vacuously"
    for cell in cells:
        cmd = run._codex_cmd(cell["model_id"], cell["native_effort"],
                             "/p/s01.prompt.md", "/tmp/lm.txt", workdir="/repo")
        assert "gpt-5.5" not in cmd, cmd
        assert "gpt-5.4" not in cmd, cmd
        assert "ultra" not in cmd, cmd
        # THE PROVIDER WALL: an OpenAI walk never crosses into a Claude model.
        assert cell["model_id"] in set(profile.models.values()), cell
    # KNOWN-POSITIVE PROBE: the same assertion MUST fail on a cell that does name a
    # retired model, or it is checking nothing.
    assert "gpt-5.5" in run._codex_cmd("gpt-5.5", "xhigh", "/p/s01.prompt.md",
                                       "/tmp/lm.txt", workdir="/repo")
    assert seen_models == {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}


def test_a_ladder_that_revisits_one_cell_FAILS_instead_of_conflating_rungs():
    """[HARDENED:grill-r2] Cheap insurance against a future re-pin.

    The refused set, `remaining`, and `record-refusal --model/--reasoning` all
    identify a rung by its (model, effort) pair, so two rungs resolving to the same
    pair would be indistinguishable: refusing one would silently refuse the other,
    and a rung that HAD been removed would still be reported as untried. The SSOT
    makes that unreachable today (verify-routing.sh fails any provider whose two
    tiers resolve to one model, and the 5.6-only lane has three distinct models),
    which is exactly why the guard is asserted against a SYNTHETIC ladder — a check
    with no reachable failing input cannot be trusted to fail.

    Failing loudly rather than de-duplicating is the deliberate half: a ladder that
    walks the same cell twice is a broken provider profile, not a dispatch to paper
    over."""
    class _Conflated:
        EXHAUSTED = "exhausted"
        rungs = [{"model_id": "gpt-5.6-terra", "native_effort": "max"},
                 {"model_id": "gpt-5.6-terra", "native_effort": "max"}]   # ALIASED TIER

        def escalate(self, task_class, provider, current, ssot_path=None):
            i = self.rungs.index(current) + 1 if current in self.rungs else 0
            return self.rungs[i] if i < len(self.rungs) else self.EXHAUSTED

    authored = {"model_id": "gpt-5.6-luna", "native_effort": "max"}
    with pytest.raises(esca.EscalationError, match="revisits rung"):
        esca._ladder(_Conflated(), "standard_build", "openai", authored)

    # ALLOW CONTROL: the same walk with the duplicate removed resolves normally.
    class _Distinct(_Conflated):
        rungs = [{"model_id": "gpt-5.6-terra", "native_effort": "max"},
                 {"model_id": "gpt-5.6-sol", "native_effort": "xhigh"}]

    rungs, hit_top = esca._ladder(_Distinct(), "standard_build", "openai", authored)
    assert [r["model_id"] for r in rungs] == ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
    assert hit_top
