"""EXE-01 — plan-execute resolves the model and effort at dispatch (schema v8).

Authority: ../references/route-at-dispatch-contract.md §3 and §4. Every check
pins a VALUE (a model, an effort, a provenance string), never key presence:
`_routing_provenance` once returned `pinned_override` for 114 of 114 records
while key-presence tests stayed green (3b2e26fb).

Run: pytest skills/plan-execute/scripts/test_route_at_dispatch.py -q
"""
import hashlib
import json
import re
import shutil

import pytest

from escalation_helpers import (REPO, _arm, _begin, _record_refusal, _stamp, esca, make_plan,
                               mio, rsi, run)
import outcomes as outc
import rework
import route_at_dispatch as rad

RR = run._import_resolver()

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
PINNED = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
          "reasoning": "high", "task_class": "standard_build", "prompt": "work"}


@pytest.fixture
def routing(tmp_path, monkeypatch):
    """A private copy of the real routing file, so a test can edit it."""
    path = tmp_path / "model-routing.yaml"
    shutil.copy(REPO / "model-routing.yaml", path)
    monkeypatch.setenv(run._SSOT_ENV, str(path))
    monkeypatch.delenv("PLAN_EXECUTE_ROUTING_PROVIDER", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv(rad.ENV, raising=False)
    return path


def _v8_plan(tmp_path, sessions, *, version=8):
    """A built plan, re-stamped v8 by hand. A session with `model: ""` is written
    exactly as the v8 builder writes an unpinned one (no reasoning key)."""
    plan_dir = make_plan(tmp_path, [{**s, "model": s.get("model") or "Sonnet"} for s in sessions])
    _stamp(plan_dir, version)
    m = plan_dir / "manifest.json"
    man = json.loads(m.read_text())
    for ms, s in zip(man["sessions"], sessions):
        for k in ("model", "reasoning", "why_model", "peer_triggers"):
            if k in s:
                ms[k] = s[k]
            elif k == "reasoning":
                ms.pop(k, None)
    m.write_text(json.dumps(man, indent=2))
    return plan_dir


def _unpinned(task_class="standard_build", sid="s01"):
    return {"id": sid, "title": sid.upper(), "items": [f"it-{sid}"], "model": "",
            "task_class": task_class, "prompt": "work"}


def _cell(task_class, provider, routing):
    got = RR.resolve(task_class, provider, ssot_path=str(routing))
    return got["model_id"], got["native_effort"] or ""


def _status(plan_dir, sid):
    import article_block as ab
    return ab.read_all_statuses((plan_dir / "PLAN.html").read_text()).get(sid)


# --------------------------------------------------------------------------
# 1. A v7 manifest dispatches byte-identically (golden captured BEFORE run.py
#    changed: same fixture, same normalisation, sha256 of begin's stdout)
# --------------------------------------------------------------------------
V7 = [
    {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet", "reasoning": "high",
     "task_class": "agentic_build", "prompt": "work"},
    {"id": "s02", "title": "S2", "items": ["it-2"], "model": "Opus", "task_class": "deep_reasoning",
     "prompt": "work"},
    {"id": "s03", "title": "S3", "items": ["it-3"], "model": "Haiku", "task_class": "mechanical",
     "prompt": "work"},
]
GOLDEN_V7 = {
    "claude": "44b298cdfecdbee0c25f2f4dd7dcbf4f3a1791b8b32051d12189de6c742b6131",
    "codex": "a17f95af7ce8093a3fedec0f08fa020b903ef667b7c93515cff6b3c580f7018f",
}


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_v7_manifest_dispatches_byte_identically(tmp_path, capsys, ssot, harness):
    """The pinned test SSOT keeps a routing-file update from moving the golden.
    To re-capture after a deliberate payload change: print `text` below."""
    ssot()
    plan_dir = make_plan(tmp_path, V7)
    _stamp(plan_dir, 7)
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01", "s02", "s03"], harness=harness)
    cap = capsys.readouterr()
    text = cap.out.replace(str(tmp_path), "<TMP>")
    text = re.sub(r"\d{3,}-\d{9,}", "<STAMP>", text)
    assert hashlib.sha256(text.encode()).hexdigest() == GOLDEN_V7[harness], text
    assert "route:" not in cap.err                       # no v8 receipt below v8
    assert rad.FREEZE_KEY not in rsi.load_state(plan_dir)
    assert rad.SWITCH_KEY not in rsi.load_state(plan_dir)


# --------------------------------------------------------------------------
# 2. Unpinned v8 resolves to the SSOT cell of the lane's provider
# --------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["anthropic", "zai", "openai"])
def test_unpinned_v8_standard_build_resolves_to_the_ssot_cell(tmp_path, capsys, routing,
                                                               monkeypatch, provider):
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    want = _cell("standard_build", provider, routing)
    if provider == "zai":
        monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "zai")
    harness = "codex" if provider == "openai" else "claude"
    by_id, _ = _begin(plan_dir, ["s01"], capsys, harness=harness)
    m = by_id["s01"]
    got = (m["codex_model"], m["codex_effort"]) if harness == "codex" \
        else (m["model_arg"], m["reasoning"])
    assert got == want
    frozen = rad.frozen_cell(plan_dir, "s01")
    assert (frozen["model"], frozen["reasoning"], frozen["provider"]) == (*want, provider)
    assert frozen["routing_version"] == rad.routing_version(routing.read_text())
    run.cmd_release(plan_dir)


def test_the_receipt_names_the_cell_the_mechanism_and_the_reason(tmp_path, capsys, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"])
    err = capsys.readouterr().err
    model, effort = _cell("standard_build", "anthropic", routing)
    version = rad.routing_version(routing.read_text())
    assert (f"route: s01 standard_build -> {model}@{effort} [tier_agent] "
            f"(class default, routing v{version})") in err
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# 3. An authored override keeps its model
# --------------------------------------------------------------------------
def test_an_authored_override_keeps_its_model(tmp_path, capsys, routing):
    sess = {**_unpinned(), "model": "Opus", "reasoning": "medium",
            "why_model": "needs judgement"}
    plan_dir = _v8_plan(tmp_path, [sess])
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"])
    cap = capsys.readouterr()
    m = {x["id"]: x for x in json.loads(cap.out)["batch"]}["s01"]
    assert (m["model_arg"], m["reasoning"]) == ("opus", "medium")
    assert _cell("standard_build", "anthropic", routing) != ("opus", "medium")  # a real override
    assert "(override: needs judgement)" in cap.err
    assert rad.frozen_cell(plan_dir, "s01") is None      # nothing resolved, nothing frozen
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# 4. The escalation ladder climbs from the RESOLVED cell
# --------------------------------------------------------------------------
def test_unpinned_v8_climbs_from_its_resolved_cell_after_two_same_signature_failures(
        tmp_path, capsys, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    base = _cell("standard_build", "anthropic", routing)
    nxt = RR.escalate("standard_build", "anthropic",
                      current={"model_id": base[0], "native_effort": base[1] or None},
                      ssot_path=str(routing))
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["escalated_from"]["authored"] == {"model": base[0], "reasoning": base[1]}
    assert m["escalated_from"]["rung"] == 1
    assert (m["model_arg"], m["reasoning"]) == (nxt["model_id"], nxt["native_effort"])
    # The readers after begin see the same base — none of them reads "no model".
    manifest = mio.load_manifest(plan_dir)
    session = mio.session_by_id(manifest)["s01"]
    assert rad.effective_cell(plan_dir, manifest, session) == base
    desc = rework._escalation_descriptor(plan_dir, "s01")
    assert desc["authored"] == {"model": base[0], "reasoning": base[1]}
    # apply's guard against a rework nobody dispatched through begin sees a ladder
    # cell too: one more recorded failure and it refuses the next closeout.
    state = rsi.load_state(plan_dir)
    state["stuck"]["s01"]["attempts"] += 1
    rsi.save_state(plan_dir, state)
    assert "was not dispatched through `begin`" in esca.undispatched_rework(plan_dir, manifest,
                                                                            session)
    run.cmd_release(plan_dir)


def test_record_refusal_validates_a_rung_of_the_resolved_ladder(tmp_path, capsys, routing):
    """`_assert_real_rung` reads the frozen cell: without it an unpinned session has
    "no ladder cell" and every real refusal would be rejected."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    ran = by_id["s01"]["escalated_from"]["ran"]
    out = _record_refusal(plan_dir, "s01", ran["model"], ran["reasoning"], capsys)
    assert out["recorded"] is True
    assert esca.rung_key(ran["model"], ran["reasoning"]) in \
        esca.session_state(plan_dir, "s01")["refused"]


# --------------------------------------------------------------------------
# 5. A routing-file change between attempts does not move the frozen cell
# --------------------------------------------------------------------------
def _move_standard_build(routing):
    text = routing.read_text()
    old = rad.routing_version(text)
    text = re.sub(r"^version:\s*\d+", f"version: {old + 1}", text, count=1, flags=re.M)
    text, n = re.subn(r"(  standard_build: \{ tier: workhorse,\s+effort: )standard",
                      r"\1thorough", text, count=1)
    assert n == 1, "the fixture edit did not land — the test would pass on nothing"
    routing.write_text(text)
    return old, old + 1


def test_a_routing_file_change_between_attempts_does_not_move_the_frozen_cell(
        tmp_path, capsys, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    first, _ = _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    before = (first["s01"]["model_arg"], first["s01"]["reasoning"])
    old, new = _move_standard_build(routing)
    moved = _cell("standard_build", "anthropic", routing)
    assert moved != before, "known positive: the edit really moves the class default"
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"])
    cap = capsys.readouterr()
    m = {x["id"]: x for x in json.loads(cap.out)["batch"]}["s01"]
    assert (m["model_arg"], m["reasoning"]) == before
    assert f"from routing v{old}; the routing file is now v{new}" in cap.err
    run.cmd_release(plan_dir)
    # A redispatch starts a new generation, and only that resolves again.
    esca.reset(plan_dir, "s01", why="operator redispatch")
    third, _ = _begin(plan_dir, ["s01"], capsys)
    assert (third["s01"]["model_arg"], third["s01"]["reasoning"]) == moved
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# 6. --harness codex means openai, whatever the provider env says
# --------------------------------------------------------------------------
def test_zai_provider_env_under_the_codex_harness_resolves_for_openai(
        tmp_path, capsys, routing, monkeypatch):
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "zai")
    plan_dir = _v8_plan(tmp_path, [_unpinned("agentic_build")])
    by_id, _ = _begin(plan_dir, ["s01"], capsys, harness="codex")
    want = _cell("agentic_build", "openai", routing)
    assert want != _cell("agentic_build", "zai", routing)
    assert (by_id["s01"]["codex_model"], by_id["s01"]["codex_effort"]) == want
    assert rad.frozen_cell(plan_dir, "s01")["provider"] == "openai"


@pytest.mark.parametrize("path", ["harness", "dial"])
def test_the_codex_paths_dispatch_the_frozen_cell_not_the_live_file(
        tmp_path, capsys, routing, monkeypatch, path):
    """Both Codex spec builders read the frozen cell: `--harness codex`, and the
    two-layer wrapper an opted-in class gets under the openai dial. A planted
    freeze that differs from the class default is the known positive."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    assert _cell("standard_build", "openai", routing) != ("gpt-5.6-sol", "xhigh")
    state = rsi.load_state(plan_dir)
    state[rad.FREEZE_KEY] = {"s01": {"model": "gpt-5.6-sol", "reasoning": "xhigh",
                                     "provider": "openai", "generation": 0,
                                     "routing_version": 1, "task_class": "standard_build"}}
    rsi.save_state(plan_dir, state)
    if path == "dial":
        real = run._load_routing
        monkeypatch.setattr(run, "_load_routing", lambda: ("openai", real()[1]))
        monkeypatch.setattr(run, "_executor_for", lambda _t: frozenset({"standard_build"}))
    by_id, _ = _begin(plan_dir, ["s01"], capsys, harness="codex" if path == "harness" else "claude")
    assert (by_id["s01"]["codex_model"], by_id["s01"]["codex_effort"]) == ("gpt-5.6-sol", "xhigh")
    run.cmd_release(plan_dir)


def test_a_session_the_openai_dial_fails_closed_to_claude_resolves_for_anthropic(
        tmp_path, capsys, routing, monkeypatch):
    """The lane's provider, not the raw dial: a gpt cell on the Claude lane would not
    normalise, and the session would silently inherit the orchestrator's model."""
    real = run._load_routing
    monkeypatch.setattr(run, "_load_routing", lambda: ("openai", real()[1]))
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "claude"
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == \
        _cell("standard_build", "anthropic", routing)
    assert rad.frozen_cell(plan_dir, "s01")["provider"] == "anthropic"
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# 7. The ledger's routing_provenance — the exact VALUE
# --------------------------------------------------------------------------
def test_routing_provenance_is_the_exact_value_for_unpinned_and_pinned_v8(tmp_path, capsys,
                                                                          routing):
    model, effort = _cell("standard_build", "anthropic", routing)
    same_as_default = {**_unpinned(sid="s02"), "model": model.capitalize(),
                       "reasoning": effort, "why_model": "pinned on purpose"}
    plan_dir = _v8_plan(tmp_path, [_unpinned(), same_as_default])
    _begin(plan_dir, ["s01", "s02"], capsys)
    manifest = mio.load_manifest(plan_dir)
    by_id = mio.session_by_id(manifest)
    prov = {sid: outc._routing_provenance(plan_dir, by_id[sid], "standard_build", "claude",
                                          model, effort, manifest=manifest)
            for sid in ("s01", "s02")}
    assert prov == {"s01": "default_resolved", "s02": "pinned_override"}
    rec = outc.compose(plan_dir, "s01", resolution="passed", result="passed", verified=True)
    assert (rec["routing_provenance"], rec["model_authored"], rec["reasoning_authored"]) == \
        ("default_resolved", model, effort)
    assert outc.compose(plan_dir, "s02", resolution="passed", result="passed",
                        verified=True)["routing_provenance"] == "pinned_override"
    run.cmd_release(plan_dir)


def test_an_unpinned_v8_reader_with_no_frozen_cell_says_so_out_loud(tmp_path, capsys, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    manifest = mio.load_manifest(plan_dir)
    capsys.readouterr()
    cell = rad.effective_cell(plan_dir, manifest, mio.session_by_id(manifest)["s01"])
    assert cell[0] == ""
    assert "has no frozen cell" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 8. The off switch, the version ceiling, and the other refusals
# --------------------------------------------------------------------------
@pytest.mark.parametrize("via", ["flag", "env"])
def test_the_off_switch_refuses_an_unpinned_v8_session(tmp_path, capsys, routing,
                                                       monkeypatch, via):
    plan_dir = _v8_plan(tmp_path, [_unpinned(), {**PINNED, "id": "s02", "items": ["it-2"],
                                                 "why_model": "fixed"}])
    if via == "env":
        monkeypatch.setenv(rad.ENV, "0")
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"], no_route=(via == "flag"))
    assert "s01: names no model and route-at-dispatch is off" in str(e.value)
    assert _status(plan_dir, "s01") == "TODO"            # refused before any mutation
    # ALLOW CONTROL: a pinned session dispatches under the switch, and the switch is
    # recorded, so it keeps winning over any frozen cell in the readers.
    by_id, _ = _begin(plan_dir, ["s02"], capsys, no_route=(via == "flag"))
    assert by_id["s02"]["model_arg"] == "sonnet"
    assert rsi.load_state(plan_dir)[rad.SWITCH_KEY]["enabled"] is False
    run.cmd_release(plan_dir)


def test_a_manifest_stamped_above_the_supported_version_is_refused(tmp_path, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned()], version=9)
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"])
    assert "plan_schema_version 9" in str(e.value) and "supports (8)" in str(e.value)
    assert _status(plan_dir, "s01") == "TODO"


def test_a_lone_half_of_the_override_pair_is_refused(tmp_path, routing):
    lone = {**_unpinned(), "model": "Opus"}               # model without reasoning
    plan_dir = _v8_plan(tmp_path, [lone])
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"])
    assert "s01: sets model without reasoning" in str(e.value)


def test_a_class_that_does_not_resolve_is_refused_never_inherited(tmp_path, routing):
    plan_dir = _v8_plan(tmp_path, [_unpinned("no_such_class")])
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"])
    msg = str(e.value)
    assert "task_class 'no_such_class'" in msg and "provider 'anthropic'" in msg
    assert _status(plan_dir, "s01") == "TODO"
    assert rad.FREEZE_KEY not in rsi.load_state(plan_dir)


def test_a_below_floor_override_on_a_linchpin_session_is_refused_at_begin(tmp_path, capsys,
                                                                          routing):
    """Defence in depth for a hand-edited manifest: the builder's own floor walk."""
    low = {**_unpinned("linchpin"), "model": "Sonnet", "reasoning": "medium",
           "why_model": "cheaper"}
    plan_dir = _v8_plan(tmp_path, [low])
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"])
    assert "BELOW the class default" in str(e.value)
    # ALLOW CONTROL: the class default itself, pinned, is zero steps below — allowed.
    (tmp_path / "ok").mkdir()
    at = {**low, "model": "Opus", "reasoning": "high"}
    ok_dir = _v8_plan(tmp_path / "ok", [at])
    by_id, _ = _begin(ok_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    run.cmd_release(ok_dir)


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_an_undispatchable_pinned_model_is_refused_never_inherited(tmp_path, routing, harness):
    """A pinned model that names nothing this lane can dispatch would run on the
    orchestrator's model (Claude lane) or halt the plan (Codex lane). Both refuse at
    begin, before any state change."""
    bad = {**_unpinned(), "model": "Gemini", "reasoning": "high", "why_model": "typo"}
    plan_dir = _v8_plan(tmp_path, [bad])
    before = (plan_dir / "run_state.json").read_bytes() \
        if (plan_dir / "run_state.json").exists() else None
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"], harness=harness)
    msg = str(e.value)
    assert "refusing to begin (route-at-dispatch)" in msg and "s01: pinned model 'Gemini'" in msg
    assert _status(plan_dir, "s01") == "TODO"
    after = (plan_dir / "run_state.json").read_bytes() \
        if (plan_dir / "run_state.json").exists() else None
    assert after == before                               # no halt, no freeze, no switch


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_pinned_reasoning_the_executor_does_not_know_is_refused(tmp_path, capsys, routing,
                                                                  harness):
    """`banana` normalises to no tier on every lane: no tier agent, no directive, and
    the session silently runs at the orchestrator's effort (Codex: at `standard`). The
    builder refuses it, so only a hand-edited manifest carries one — begin refuses it."""
    pin = {**_unpinned(), "model": "sonnet", "reasoning": "high", "why_model": "typo"}
    plan_dir = _v8_plan(tmp_path, [pin])
    m = plan_dir / "manifest.json"
    man = json.loads(m.read_text())
    man["sessions"][0]["reasoning"] = "banana"            # the hand edit
    m.write_text(json.dumps(man, indent=2))
    with pytest.raises(SystemExit) as e:
        run.cmd_begin(plan_dir, ["s01"], harness=harness)
    msg = str(e.value)
    assert "s01: pinned reasoning 'banana' is not an effort level" in msg
    assert "'sonnet'" in msg
    assert _status(plan_dir, "s01") == "TODO"
    assert rad.SWITCH_KEY not in rsi.load_state(plan_dir)  # refused before any write
    # ALLOW CONTROL: xhigh has no sonnet tier agent (a dead rung) but IS a known level,
    # so it dispatches — the check refuses the unknown, not the unbound.
    (tmp_path / "ok").mkdir()
    ok_dir = _v8_plan(tmp_path / "ok", [{**pin, "reasoning": "xhigh"}])
    by_id, _ = _begin(ok_dir, ["s01"], capsys, harness=harness)
    got = by_id["s01"]
    assert got["backend"] == harness
    if harness == "claude":
        assert (got["model_arg"], got["reasoning"]) == ("sonnet", "xhigh")
    run.cmd_release(ok_dir)


def test_the_frozen_cell_takes_the_generation_read_under_the_plan_lock(tmp_path, capsys, routing,
                                                                       monkeypatch):
    """A redispatch or amend can land between `prepare` and the plan lock. The
    freeze must carry the generation current UNDER the lock: stamped with the one
    `prepare` saw, it matches no generation, and every reader after begin (the
    escalation ladder, the ledger) sees no model."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    real = rad.prepare

    def prepare_then_bump(pdir, *a, **kw):
        real(pdir, *a, **kw)
        esca.reset(pdir, "s01", why="a redispatch lands mid-begin")

    monkeypatch.setattr(rad, "prepare", prepare_then_bump)
    want = _cell("standard_build", "anthropic", routing)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == want   # pre-lock readers
    assert esca.session_state(plan_dir, "s01")["generation"] == 1           # the bump landed
    stored = rsi.load_state(plan_dir)[rad.FREEZE_KEY]["s01"]
    assert (stored["model"], stored["reasoning"], stored["generation"]) == (*want, 1)
    manifest = mio.load_manifest(plan_dir)
    assert rad.effective_cell(plan_dir, manifest, mio.session_by_id(manifest)["s01"]) == want
    run.cmd_release(plan_dir)


@pytest.mark.parametrize("mutate", ["manifest", "routing", "manifest_mid_resolve",
                                    "routing_mid_resolve", "none"])
def test_a_plan_or_routing_change_between_prepare_and_the_lock_refuses_the_begin(
        tmp_path, capsys, routing, monkeypatch, mutate):
    """`prepare` resolves the cells from the manifest and routing file begin read BEFORE
    the plan lock; `commit` runs under it. An amend or a routing bump landing in
    between would otherwise freeze a cell for a plan that no longer exists. `commit`
    compares against what the cells were resolved FROM and refuses — nothing frozen,
    every session still TODO. A change landing DURING resolution (`*_mid_resolve`)
    must refuse too: a baseline taken after the loop would accept it. The control
    (nothing moves) dispatches."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    real, real_resolve = rad.prepare, rad._resolve

    def amend_manifest(pdir):
        m = pdir / "manifest.json"
        man = json.loads(m.read_text())
        man["sessions"][0].update(model="Opus", reasoning="high", why_model="amended mid-begin")
        m.write_text(json.dumps(man, indent=2))

    def prepare_then_mutate(pdir, *a, **kw):
        real(pdir, *a, **kw)
        if mutate == "manifest":
            amend_manifest(pdir)
        elif mutate == "routing":
            _move_standard_build(routing)

    def mutate_then_resolve(pdir, *a, **kw):
        if not moved:
            moved.append(1)
            if mutate == "manifest_mid_resolve":
                amend_manifest(pdir)
            else:
                _move_standard_build(routing)
        return real_resolve(pdir, *a, **kw)

    moved = []
    if mutate.endswith("_mid_resolve"):
        monkeypatch.setattr(rad, "_resolve", mutate_then_resolve)
    else:
        monkeypatch.setattr(rad, "prepare", prepare_then_mutate)
    if mutate == "none":
        by_id, _ = _begin(plan_dir, ["s01"], capsys)
        assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == _cell(
            "standard_build", "anthropic", routing)
        assert _status(plan_dir, "s01") == "DOING"
        run.cmd_release(plan_dir)
        return
    with pytest.raises(SystemExit, match="changed during begin"):
        run.cmd_begin(plan_dir, ["s01"])
    assert _route_writes(plan_dir) == ([], [])
    assert _status(plan_dir, "s01") == "TODO"
    assert not rsi._lock_path(plan_dir).exists()          # the refusal released the lock
    if mutate.endswith("_mid_resolve"):
        assert moved                                       # the change really landed mid-loop
    # KNOWN POSITIVE: the same plan, with nothing moving, dispatches afterwards.
    monkeypatch.setattr(rad, "prepare", real)
    monkeypatch.setattr(rad, "_resolve", real_resolve)
    _begin(plan_dir, ["s01"], capsys)
    assert _status(plan_dir, "s01") == "DOING"
    run.cmd_release(plan_dir)


def test_an_unpinned_v8_session_frozen_for_zai_climbs_the_zai_ladder(tmp_path, capsys, routing,
                                                                    monkeypatch):
    """The ladder follows the provider the cell was FROZEN for, not the process that
    later asks (verify's rework announcement can run off the GLM tree)."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "zai")
    _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    monkeypatch.delenv("PLAN_EXECUTE_ROUTING_PROVIDER")
    assert run.pl.escalation_provider("claude") == "anthropic"   # the mismatch under test
    base = _cell("standard_build", "zai", routing)
    assert rad.frozen_cell(plan_dir, "s01")["provider"] == "zai"
    nxt = RR.escalate("standard_build", "zai",
                      current={"model_id": base[0], "native_effort": base[1] or None},
                      ssot_path=str(routing))
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    desc = rework._escalation_descriptor(plan_dir, "s01")
    assert desc["authored"] == {"model": base[0], "reasoning": base[1]}
    assert desc["rung"] == 1
    assert (desc["ran"]["model"], desc["ran"]["reasoning"]) == (nxt["model_id"],
                                                                nxt["native_effort"])


def _route_writes(plan_dir):
    state = rsi.load_state(plan_dir)
    log = plan_dir / "run.ndjson"
    events = [json.loads(x)["event"] for x in log.read_text().splitlines()] if log.exists() else []
    return ([k for k in (rad.FREEZE_KEY, rad.SWITCH_KEY) if k in state],
            [e for e in events if e in ("route_resolved", "route_at_dispatch_gate")])


def test_begin_writes_no_route_state_before_the_plan_lock(tmp_path, capsys, routing,
                                                          monkeypatch):
    """Two concurrent begins must not race on run_state.json: the switch and the
    frozen cells are written only once the lock is held. (The isolation gate takes
    its own short hold of the lock; test_group_scope covers it.)"""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    real = rsi.acquire_lock

    def held(_plan_dir):
        raise rsi.LockError("another /plan-execute appears to be running")

    monkeypatch.setattr(rsi, "acquire_lock", held)
    with pytest.raises(rsi.LockError):
        run.cmd_begin(plan_dir, ["s01"])
    assert _route_writes(plan_dir) == ([], [])
    assert _status(plan_dir, "s01") == "TODO"
    # KNOWN POSITIVE: with the lock held, the same begin does write both.
    monkeypatch.setattr(rsi, "acquire_lock", real)
    _begin(plan_dir, ["s01"], capsys)
    assert _route_writes(plan_dir) == ([rad.FREEZE_KEY, rad.SWITCH_KEY],
                                       ["route_at_dispatch_gate", "route_resolved"])
    run.cmd_release(plan_dir)


@pytest.mark.parametrize("bump", [False, True])
def test_a_freeze_another_begin_wrote_first_is_kept_for_the_same_generation(
        tmp_path, capsys, routing, monkeypatch, bump):
    """Two begins both read "no freeze" before the lock. The one that commits
    second must keep the first freeze of that generation, not overwrite it. A NEW
    generation (amend / escalation reset) still replaces it — the control."""
    plan_dir = _v8_plan(tmp_path, [_unpinned()])
    real = rad.prepare
    rival = {"model": "rival-model", "reasoning": "low", "routing_version": 0,
             "provider": "anthropic", "task_class": "standard_build", "generation": 0}

    def prepare_then_rival(pdir, *a, **kw):
        real(pdir, *a, **kw)
        state = rsi.load_state(pdir)
        state[rad.FREEZE_KEY] = {"s01": dict(rival)}
        rsi.save_state(pdir, state)
        if bump:
            esca.reset(pdir, "s01", why="amend lands after the rival froze gen 0")

    monkeypatch.setattr(rad, "prepare", prepare_then_rival)
    _begin(plan_dir, ["s01"], capsys)
    stored = rsi.load_state(plan_dir)[rad.FREEZE_KEY]["s01"]
    events = [json.loads(x)["event"] for x in (plan_dir / "run.ndjson").read_text().splitlines()]
    if bump:
        assert (stored["model"], stored["reasoning"], stored["generation"]) == (
            *_cell("standard_build", "anthropic", routing), 1)
        assert "route_freeze_kept" not in events
    else:
        assert stored == rival
        assert "route_freeze_kept" in events
    run.cmd_release(plan_dir)
