"""R7 §18.2 / §18.9 — the code route: a main move by code that touches none of
the plan's files carries the owner's ack (switch `on`), and every other cell
resets. Split from ``test_land_carry.py``, whose fixtures it imports.

    pytest skills/plan-execute/scripts/test_land_carry_code_route.py -q
"""

import sys

import pytest

import land_carry as lc
import land_state as lst
from _land_fixture import _arm_prepush, ack_only, build_repo, isolate, main_side, origin_main, run_cli, work
from test_land_carry import (GATES, KEY, SCRIPTS, SLUG, Repo, _break_classifier, _commit,
                             _events, _g, _harvest, _settings, _state)


# --------------------------------------------------------------------------
# R7 §18.2 — the code route: a path-disjoint code move, with the switch `on`
# --------------------------------------------------------------------------
def _code(r, rel="src/other.py"):
    """A code move on main that touches none of the plan's files."""
    return r.main_moves(rel, "O = 1\n")


def _main_rm(r, rel):
    r.on("main")
    _g(r.root, "rm", "-q", rel)
    _g(r.root, "commit", "-q", "-m", "revert")


def _rename_into_own_plan_dir(r):
    r.on("main")
    _g(r.root, "mv", "src/a.py", f"_plans/{SLUG}/a.py")
    _g(r.root, "commit", "-q", "-m", "rename")


# (id, arrange(repo, st) -> verdict, decision, reason); every row runs with `on`
CODE_ROWS = [
    ("a path-disjoint code move carries",
     lambda r, st: (_code(r), r.decide(st))[1], "carry", None),
    ("a code move with a churn key beside it carries",
     lambda r, st: (_code(r), r.main_moves("settings.json", _settings(model="sonnet")),
                    r.decide(st))[2], "carry", None),
    ("a harness key in settings.json resets",
     lambda r, st: (_code(r), r.main_moves("settings.json", _settings(hooks={"Stop": [1]})),
                    r.decide(st))[2], "reset", "force-reset-path"),
    ("a force-reset path changed then reverted resets",
     lambda r, st: (r.main_moves("Makefile", "all:\n"), _main_rm(r, "Makefile"),
                    r.decide(st))[2], "reset", "force-reset-path"),
    ("a plan path changed then reverted resets",
     lambda r, st: (r.main_moves("src/f.py", "def f():\n    return 1\n"), _main_rm(r, "src/f.py"),
                    r.decide(st))[2], "reset", "plan-path-touched"),
    ("a code file renamed into the plan's own directory resets",
     lambda r, st: (_rename_into_own_plan_dir(r), r.decide(st))[1], "reset", "plan-path-touched"),
    ("a changed plan diff resets",
     lambda r, st: (_code(r), r.on("plan"), _commit(r.root, "src/f.py", "def f():\n    return 2\n"),
                    r.decide(st))[3], "reset", "patch-id"),
    ("a hand-resolved merge resets",
     lambda r, st: (_code(r), lc.decide(r.plan_dir, r.ctx, {
         **st, **r.candidate(), "resolution_required": True}, KEY))[1],
     "reset", "plan-side-changed"),
    ("a changed gate set resets",
     lambda r, st: (_code(r), r.decide(st, key="other"))[1], "reset", "gate-set-key"),
    ("a changed outer registry resets",
     lambda r, st: (_code(r), (r.root / ".claude" / "eval-gates.json").write_text('{"g": {}}\n'),
                    r.decide(st))[2], "reset", "registry"),
    ("an unparseable settings.json resets",
     lambda r, st: (_code(r), r.main_moves("settings.json", "{\n"), r.decide(st))[2],
     "reset", "classify-error"),
    ("an unloadable classifier resets",
     lambda r, st: (_break_classifier(), _code(r), r.decide(st))[2], "reset", "classify-error"),
    ("a git error resets",
     lambda r, st: (_code(r), r.decide({**st, "carry_basis": {**st["carry_basis"],
                                                               "merge_sha": "f" * 40}}))[1],
     "reset", "git-error"),
]
FORCED = ["githooks/pre-push", "Makefile", "tools/Makefile", "requirements.txt",
          "sub/requirements-dev.txt", "uv.lock", "sub/Cargo.lock", "package-lock.json",
          "Pipfile.lock", "poetry.lock", "pyproject.toml", "pytest.ini", "setup.cfg",
          "tests/conftest.py", "scripts/gearbox-classify.py", "scripts/deploy.pathspec",
          "model-routing.yaml", ".claude/eval-gates.json",
          "skills/plan-execute/references/eval-gates.default.json"]


@pytest.mark.parametrize("arrange,decision,reason", [r[1:] for r in CODE_ROWS],
                         ids=[r[0] for r in CODE_ROWS])
def test_decide_code_route_matrix(tmp_path, monkeypatch, arrange, decision, reason):
    """Carry only when the paths are disjoint, the digests and registries are
    unchanged and the merge is clean; every other cell resets (§18.2)."""
    monkeypatch.setattr(lc, "_CLASSIFIER", lc._CLASSIFIER)
    monkeypatch.setattr(lc, "_mode", lambda: "on")
    r = Repo(tmp_path)
    st = r.approved()
    out = arrange(r, st)
    assert (out["decision"], out["reason"]) == (decision, reason), out
    assert out["carry_version"] == 2 and "shadow" not in out
    if reason != "classify-error":
        assert out["route"] == "code" and out["moved_paths"], out


@pytest.mark.parametrize("rel", FORCED)
def test_decide_a_force_reset_path_resets_on_the_code_route(tmp_path, monkeypatch, rel):
    monkeypatch.setattr(lc, "_mode", lambda: "on")
    r = Repo(tmp_path)
    st = r.approved()
    r.main_moves(rel, "changed\n")
    out = r.decide(st)
    assert (out["decision"], out["reason"], out["route"]) == (
        "reset", "force-reset-path", "code"), out
    assert lc.force_reset_hits(out["moved_paths"], set()) == [rel]


def test_force_reset_hits_reads_settings_by_class():
    """settings.json is a hit only when a commit changed a non-churn key."""
    paths = ["settings.json", "src/a.py", "lib/x.lockfile", "requirements.md"]
    assert lc.force_reset_hits(paths, {"src/a.py"}) == []
    assert lc.force_reset_hits(paths, {"settings.json"}) == ["settings.json"]


def test_decide_a_merge_whose_side_lacks_a_force_reset_change_resets(tmp_path, monkeypatch):
    """Merge parents: a side cut before an approved Makefile change lists it again."""
    monkeypatch.setattr(lc, "_mode", lambda: "on")
    r = Repo(tmp_path)
    base = _g(r.root, "rev-parse", "main")
    r.main_moves("Makefile", "all:\n")
    st = r.approved()
    _g(r.root, "checkout", "-q", "-b", "side", base)
    _commit(r.root, "src/side.py", "S = 1\n", "side")
    r.on("main")
    _g(r.root, "merge", "-q", "--no-ff", "--no-edit", "side")
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "force-reset-path"), out
    assert {"Makefile", "src/side.py"} <= set(out["moved_paths"])


@pytest.mark.parametrize("mode,decision,reason,shadow", [
    ("off", "reset", "harness-class", None),
    ("shadow", "reset", "harness-class", {"decision": "carry", "reason": None}),
    ("on", "carry", None, None)])
def test_decide_the_switch_decides_the_code_route(tmp_path, monkeypatch, mode, decision,
                                                  reason, shadow):
    """§18.11: `off` and `shadow` reset exactly as today; `shadow` logs what `on` does."""
    monkeypatch.setattr(lc, "_mode", lambda: mode)
    r = Repo(tmp_path)
    st = r.approved()
    _code(r)
    out = r.decide(st)
    assert (out["decision"], out["reason"], out.get("shadow")) == (decision, reason, shadow)
    assert (out["route"], out["moved_paths"]) == ("code", ["src/other.py"])


def test_decide_shadow_names_the_reason_on_would_reset_with(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "_mode", lambda: "shadow")
    r = Repo(tmp_path)
    st = r.approved()
    r.main_moves("Makefile", "all:\n")
    out = r.decide(st)
    assert (out["reason"], out["shadow"]) == (
        "harness-class", {"decision": "reset", "reason": "force-reset-path"}), out


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_decide_the_harvest_route_ignores_the_switch(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(lc, "_mode", lambda: mode)
    r = Repo(tmp_path)
    st = r.approved()
    _harvest(r)
    out = r.decide(st)
    assert (out["decision"], out["route"], "shadow" in out) == ("carry", "harvest", False), out


@pytest.mark.parametrize("module,want", [(None, "off"), ("bogus", "off"), ("ON", "off"),
                                         (KeyError, "off"), ("shadow", "shadow"), ("on", "on")])
def test_mode_unknown_missing_or_broken_is_off(monkeypatch, module, want):
    """§18.11 invariant 2: a typo, or a switch that raises, never turns phase 2 on."""
    import types  # noqa: PLC0415

    def mode():
        if module is KeyError:
            raise KeyError("PHASE2_MODE")
        return module
    monkeypatch.setitem(sys.modules, "land_phase2",
                        None if module is None else types.SimpleNamespace(mode=mode))
    assert lc._mode() == want


def test_apply_moved_since_ack_is_the_union_under_one_ack_and_goes_with_it(tmp_path):
    """§18.9 invariant 2: never shortened while one ack stands; a reset drops it."""
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    ctx = lst.context(iso["plan_dir"])
    st = _state()
    lc.apply(iso["plan_dir"], ctx, st, GATES, "k2",
             {"decision": "carry", "reason": None, "classes": {}, "route": "code",
              "moved_paths": ["b.py", "a.py"], "basis": {**st["carry_basis"], "main_head": "M2",
                                                         "plan_head": "P2", "merge_sha": "X2"}})
    st.update({"expected": "M3", "merge_sha": "X3"})
    lc.apply(iso["plan_dir"], ctx, st, GATES, "k3",
             {"decision": "carry", "reason": None, "classes": {}, "route": "harvest",
              "moved_paths": ["c.md"], "basis": None})
    assert st["moved_since_ack"] == ["a.py", "b.py", "c.md"]
    assert [c["route"] for c in st["ack"]["carries"]] == ["code", "harvest"]
    lc.apply(iso["plan_dir"], ctx, st, GATES, "k4",
             {"decision": "reset", "reason": "force-reset-path", "classes": {}, "basis": None})
    assert st["ack"] is None and "moved_since_ack" not in st


def _acked_noisy(tmp_path):
    """An acked land whose one test gate prints a new line on every run (its output
    changes) and fails once `src/STOP` exists on main."""
    fx = build_repo(tmp_path, gates={"marker-gate": {     # replaces the fixture's gate
        "kind": "argv", "cwd": ".", "timeout": 60,
        "argv": ["/bin/sh", "-c", "date +%s%N; head -c 8 /dev/urandom | od -An; "
                                  "test ! -f src/STOP"]}})
    iso = isolate(fx, SLUG)
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    assert ack_only(iso["plan_dir"], "ok")[1]["action"] == "land-acked"
    return fx, iso


def _land_in_process(plan_dir, monkeypatch, mode):
    """`land.land` in this process, so the §18.11 switch can be set for the run."""
    import land  # noqa: PLC0415
    monkeypatch.setattr(lc, "_mode", lambda: mode)
    monkeypatch.chdir(SCRIPTS)
    return land.land(plan_dir)


def test_land_a_path_disjoint_code_move_carries_the_ack_and_lands(tmp_path, monkeypatch):
    """§18.2 + §18.9 with the switch `on`: main_head and merge_sha re-bind, the gate
    re-runs with a different output, and the owner's ack still matches."""
    fx, iso = _acked_noisy(tmp_path)
    before = lst.load(iso["plan_dir"])["ack"]
    moved = main_side(fx, "src/other.py", "O = 1\n", "feat: unrelated code")
    out = _land_in_process(iso["plan_dir"], monkeypatch, "on")
    assert out["action"] == "landed", out
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["route"], ev["moved_paths"], ev["to_main"]) == (
        "carry", "code", ["src/other.py"], moved), ev
    assert ev["rerun"] == ["marker-gate"]
    st = lst.load(iso["plan_dir"])
    ack = st["ack"]
    assert ack["gate_digest"] == before["gate_digest"]          # never re-bound
    assert (ack["main_head"], ack["merge_sha"]) != (before["main_head"], before["merge_sha"])
    assert ack["carries"][-1]["moved_paths"] == ["src/other.py"]
    assert st["moved_since_ack"] == ["src/other.py"]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_land_the_same_move_resets_while_the_switch_is_not_on(tmp_path, monkeypatch, mode):
    fx, iso = _acked_noisy(tmp_path)
    main_side(fx, "src/other.py", "O = 1\n", "feat: unrelated code")
    out = _land_in_process(iso["plan_dir"], monkeypatch, mode)
    assert out["action"] == "land-awaits-review", out
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["reason"], ev.get("shadow")) == (
        "reset", "harness-class",
        {"decision": "carry", "reason": None} if mode == "shadow" else None), ev


def test_land_a_code_race_inside_the_push_window_resyncs_with_the_switch_on(
        tmp_path, monkeypatch):
    """`_resync`: a path-disjoint code move during the push re-binds the ack to the new
    main and merge (plan_head and the result digest unchanged) and pushes again."""
    fx, iso = _acked_noisy(tmp_path)
    before = lst.load(iso["plan_dir"])["ack"]
    _arm_prepush(fx, times=1, race_dir="src/race")
    out = _land_in_process(iso["plan_dir"], monkeypatch, "on")
    assert out["action"] == "landed", out
    ack = lst.load(iso["plan_dir"])["ack"]
    assert (ack["plan_head"], ack["gate_digest"]) == (before["plan_head"], before["gate_digest"])
    assert ack["main_head"] != before["main_head"] and ack["resyncs"]
    assert ack["carries"][-1]["moved_paths"] == ["src/race/raced-0.txt"]


def test_land_a_carried_code_move_whose_gate_fails_never_pushes(tmp_path, monkeypatch):
    """A failed gate never carries: the carry was conditional on the re-run."""
    fx, iso = _acked_noisy(tmp_path)
    moved = main_side(fx, "src/STOP", "stop\n", "feat: unrelated code")
    out = _land_in_process(iso["plan_dir"], monkeypatch, "on")
    assert out["action"] == "land-parked" and out["kind"] == "gate-failed", out
    assert origin_main(fx) == moved
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["invalidated_from"]["carries"]
    ev = _events(iso["plan_dir"])
    assert (ev[-2]["decision"], ev[-2]["route"]) == ("carry", "code")
    assert (ev[-1]["decision"], ev[-1]["reason"]) == ("reset", "gate-result"), ev[-1]
