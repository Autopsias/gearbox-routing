"""CP-02 — `--harness codex`: the orchestrator as a Codex session.

Under this harness the orchestrator runs each dispatch command in its own shell
and feeds the -o file straight back through `apply`. Every ready session must
therefore get a RUNNABLE command — including Claude-pinned ones, translated
through the SSOT with a receipt naming what the translation lost.

Contract + probe evidence: references/dual-harness-contract.md.
Split out of test_codex_dispatch.py. Shared fixtures: codex_helpers.py.

Run: pytest skills/plan-execute/scripts/test_codex_harness.py -q
"""
import json
from pathlib import Path

import pytest

from codex_helpers import (
    _begin, _begin_codex, _plant_secret, make_plan, rsi, run,
)
import ssot_policy

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
                     "--sandbox workspace-write", "-o ", " - < ", "--json"):
            assert flag in m["dispatch_cmd"], (sid, flag)
        # stdout (the event stream, carries usage) goes to a sibling of the -o file.
        assert m["dispatch_cmd"].endswith(f"> {m['last_message_file']}.events.jsonl")
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
    # opus-pinned: translated via the SSOT, carrying the receipt. s03:
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

    # KNOWN-NEGATIVE CONTROL, now built the other way round (s03): the
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

    with pytest.raises(ssot_policy.UnroutableCodexSession) as ei:
        run._resolve_codex_dispatch("gpt-5.5", "medium", retired)
    msg = str(ei.value)
    assert "RETIRED" in msg
    assert "amend-session" in msg                                  # the fix, named
    for repl in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):  # the replacements, named
        assert repl in msg, f"block message does not name {repl}: {msg}"


def test_luna_has_a_fallback_rung(tmp_path, capsys, ssot):
    # Before CL-03 a haiku-pinned session translated to luna@max had a NULL
    # fallback: one refused mechanical session took the whole run to NO-CODEX.
    # v1.15 RE-POINTED that rescue: gpt-5.5 is retired with the whole
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


