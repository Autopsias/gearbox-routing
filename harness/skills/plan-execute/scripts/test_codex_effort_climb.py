"""ESC-03 — the CODEX effort climb (s04).

Same escalation engine as the Claude lane, walked against the Codex cell: the
rung is path-dependent, the apex holds, and a refused rung never comes back as a
downward fallback.

Split out of test_codex_dispatch.py. Shared fixtures: codex_helpers.py; the
armed-stuck-state helpers come from escalation_helpers.py.

Run: pytest skills/plan-execute/scripts/test_codex_effort_climb.py -q
"""
import json
import re
import shlex

import pytest

import article_block as ab
import escalation as esca
from codex_helpers import (
    SCRIPTS, _begin, _begin_codex, _plant_secret, make_plan, run,
)
import codex_command
from escalation_helpers import _arm, _stamp

# --------------------------------------------------------------------------
# ESC-03 — the CODEX effort climb (s04). Same engine as the Claude lane
# (`escalation.compute` -> `resolve_route.escalate`), walked on
# `providers.openai`, applied to the RESOLVED (codex_model, codex_effort) cell.
#
# Every case below is PAIRED — a PLANT (the wrong rung must be detected) and an
# ALLOW CONTROL (the right one must not trip) — for the reason the escalation
# suite states: a check that only ever sees the passing case cannot fail.
# --------------------------------------------------------------------------


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
        cmd = codex_command._codex_cmd(cell["model_id"], cell["native_effort"],
                             "/p/s01.prompt.md", "/tmp/lm.txt", workdir="/repo")
        assert "gpt-5.5" not in cmd, cmd
        assert "gpt-5.4" not in cmd, cmd
        assert "ultra" not in cmd, cmd
        # THE PROVIDER WALL: an OpenAI walk never crosses into a Claude model.
        assert cell["model_id"] in set(profile.models.values()), cell
    # KNOWN-POSITIVE PROBE: the same assertion MUST fail on a cell that does name a
    # retired model, or it is checking nothing.
    assert "gpt-5.5" in codex_command._codex_cmd("gpt-5.5", "xhigh", "/p/s01.prompt.md",
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
