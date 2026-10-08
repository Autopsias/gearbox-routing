#!/usr/bin/env python3
"""Plan schema v8 in plan-builder — the builder half of the route-at-dispatch
contract (skills/plan-execute/references/route-at-dispatch-contract.md).

Every refusal is asserted next to a control the same validator accepts, so no
test here can go green by the builder refusing everything. The pre-v8 guarantee
("a spec below 8 builds a byte-identical manifest") is checked against the real
pre-v8 builder, run from git history in a SEPARATE process: importing two
build_plan modules into one interpreter shares sys.modules, and the comparison
then passes on nothing.

Run: pytest skills/plan-builder/scripts/test_route_at_dispatch_build.py -q
"""
import copy
import io
import json
import re
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_plan as bp  # noqa: E402
import route_at_dispatch_build as rad  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

# The last builder before schema v8: this branch's base. Deliberately NOT
# `origin/main` — once this work lands, origin/main IS the v8 builder, so the
# comparison would pass on nothing and the v8 positive control would fail.
PRE_V8_BUILDER = "e14bd6dd25b2a42000047108684496177b791a34"

_V8 = {
    "title": "Route at dispatch fixture",
    "plan_schema_version": 8,
    "created": "2026-09-29",
    "categories": [{"key": "c", "label": "C"}],
    "items": [
        {"id": "it-1", "title": "IT-1", "category": "c", "touches": "src/a.py, tests/test_a.py",
         "prior_art": {"decision": "build", "source": "nothing comparable in-repo"}},
        {"id": "it-2", "title": "IT-2", "category": "c", "touches": [],
         "prior_art": {"decision": "build", "source": "nothing comparable in-repo"}},
    ],
    "sessions": [
        {"id": "s01", "title": "S1", "task_class": "standard_build", "items": ["it-1"],
         "prompt": "build it", "dispatch": {"subagent_type": None, "depends_on": []}},
        {"id": "s02", "title": "S2", "task_class": "linchpin", "items": ["it-2"],
         "model": "Opus", "reasoning": "xhigh", "why_model": "one-shot migration",
         "prompt": "decide it", "dispatch": {"subagent_type": None, "depends_on": ["s01"]}},
    ],
    "infographic": {"type": "phase-journey",
                    "phases": [{"id": "p1", "label": "P1", "items": ["it-1", "it-2"]}]},
}


def _spec(version=8, **sessions):
    """The v8 fixture; `sessions` maps a session id to fields to set (None deletes)."""
    s = copy.deepcopy(_V8)
    s["plan_schema_version"] = version
    for sid, fields in sessions.items():
        sess = next(x for x in s["sessions"] if x["id"] == sid)
        for k, v in fields.items():
            if v is None:
                sess.pop(k, None)
            else:
                sess[k] = v
    return s


def _v7(spec):
    """The same plan as a v7 spec: every session pins a model, as v7 requires."""
    spec = copy.deepcopy(spec)
    spec["plan_schema_version"] = 7
    for s in spec["sessions"]:
        s.setdefault("model", "Sonnet")
    return spec


def _refused(spec, *needles):
    with pytest.raises(ValueError) as e:
        bp.validate_spec(spec)
    for n in needles:
        assert n in str(e.value), str(e.value)
    return str(e.value)


# -- the author names the class; the model is chosen at dispatch -------------
def test_a_v8_spec_with_no_models_builds(tmp_path):
    spec = _spec(s02={"model": None, "reasoning": None, "why_model": None})
    bp.build(spec, tmp_path / "plan")
    m = json.loads((tmp_path / "plan" / "manifest.json").read_text())
    assert m["plan_schema_version"] == 8
    s01, s02 = m["sessions"]
    assert (s01["model"], s01["reasoning"], s01["task_class"]) == ("", "", "standard_build")
    assert (s02["model"], s02["task_class"]) == ("", "linchpin")
    assert "why_model" not in s02
    page = (tmp_path / "plan" / "PLAN.html").read_text()
    assert page.count(rad.DISPATCH_LABEL) >= 2          # both cards, plus strip/hero
    assert '<span class="chip chip-task-class" title="Task class">linchpin</span>' in page
    assert 'class="chip model-sonnet"' not in page      # no invented default model


def test_an_override_carries_its_reason_into_the_manifest(tmp_path):
    bp.build(_spec(), tmp_path / "plan")
    s02 = json.loads((tmp_path / "plan" / "manifest.json").read_text())["sessions"][1]
    assert (s02["model"], s02["reasoning"], s02["why_model"]) == ("Opus", "xhigh",
                                                                  "one-shot migration")
    page = (tmp_path / "plan" / "PLAN.html").read_text()
    assert 'chip-task-class" title="Task class">linchpin</span> <span class="chip model-opus"' \
        in page


def test_task_class_is_required_at_v8_only():
    _refused(_spec(s01={"task_class": None}), "s01.task_class is required")
    _refused(_spec(s01={"task_class": "standard_buld"}), "s01.task_class")
    bp.validate_spec(_v7(_spec(s01={"task_class": None})))          # control: v7 unchanged


def test_a_model_without_why_model_is_refused():
    _refused(_spec(s02={"why_model": None}), "Session s02", "why_model")
    bp.validate_spec(_spec())                                       # control: with the reason


def test_model_and_reasoning_are_a_pair_at_v8():
    _refused(_spec(s02={"reasoning": None}), "Session s02 sets model without reasoning")
    _refused(_spec(s02={"model": None}), "Session s02 sets reasoning without model")
    _refused(_spec(s02={"model": None, "reasoning": None}), "s02 has why_model but no")
    bp.validate_spec(_v7(_spec(s02={"reasoning": None})))           # control: v7 keeps it legal


def test_a_below_floor_override_on_a_linchpin_is_refused():
    # linchpin's class default on anthropic is opus@high; opus@medium climbs to it.
    _refused(_spec(s02={"reasoning": "medium"}), "BELOW", "opus@high")
    _refused(_spec(s02={"model": "Sonnet", "reasoning": "high"}), "BELOW")
    # Controls: above the floor, equal to it (zero steps is not below), and the
    # same low override on a session with no risk flag.
    bp.validate_spec(_spec(s02={"reasoning": "xhigh"}))
    bp.validate_spec(_spec(s02={"reasoning": "high"}))
    bp.validate_spec(_spec(s02={"reasoning": "medium", "task_class": "agentic_build"}))


def test_peer_triggers_arm_the_floor_too():
    # deep_reasoning's default is opus@medium; opus@low climbs to it in one step.
    low = {"task_class": "deep_reasoning", "model": "Opus", "reasoning": "low"}
    _refused(_spec(s02={**low, "peer_triggers": ["security_sensitive"]}), "BELOW")
    bp.validate_spec(_spec(s02=low))                                # control: no risk flag


def test_the_floor_is_a_rank_not_a_walk(monkeypatch):
    # Operator decision 2026-09-30: deep_reasoning's default is opus@medium, and the walk
    # from sonnet@high enters opus at high, skipping medium - escalate() never reaches the
    # default, yet sonnet@high ranks lower on the ladder, so it is below the floor.
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "anthropic")
    son = {"task_class": "deep_reasoning", "model": "Sonnet", "reasoning": "high"}
    _refused(_spec(s02={**son, "peer_triggers": ["security_sensitive"]}), "BELOW", "opus@medium")
    _refused(_spec(s02={**son, "task_class": "linchpin"}), "BELOW")
    for effort in ("medium", "high"):                               # controls: at / above
        bp.validate_spec(_spec(s02={**son, "model": "Opus", "reasoning": effort,
                                    "peer_triggers": ["security_sensitive"]}))


def test_the_floor_skips_a_cross_provider_override(monkeypatch):
    # Contract section 4: an override whose provider differs from the tree's skips the floor.
    # Codex linchpin default is sol@max; sol@high would climb to it, but on the Claude tree
    # it is cross-provider -> allowed. Same-provider below-floor is still refused.
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "anthropic")
    bp.validate_spec(_spec(s02={"model": "gpt-5.6-sol", "reasoning": "high"}))
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "openai")
    _refused(_spec(s02={"model": "gpt-5.6-sol", "reasoning": "high"}), "on openai")
    bp.validate_spec(_spec(s02={"model": "gpt-5.6-sol", "reasoning": "max"}))


def test_the_floor_refuses_the_default_model_at_a_lower_effort(monkeypatch):
    # escalate() may jump models instead of raising effort, so the walk alone let
    # sol@low and sol@medium through under a sol@max linchpin default.
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_PROVIDER", "openai")
    for effort in ("low", "medium"):
        _refused(_spec(s02={"model": "gpt-5.6-sol", "reasoning": effort}), "BELOW")


def test_every_item_declares_touches_at_v8():
    spec = _spec()
    del spec["items"][0]["touches"]
    _refused(spec, "Item it-1 must declare touches")
    spec["items"][0]["touches"] = ""
    _refused(spec, "Item it-1 must declare touches")
    spec["items"][0]["touches"] = []                                # control: writes nothing
    bp.validate_spec(spec)
    del spec["items"][1]["touches"]
    bp.validate_spec(_v7(spec))                                     # control: v7 unchanged


def test_an_empty_touches_declaration_reaches_the_manifest(tmp_path):
    bp.build(_spec(), tmp_path / "v8")
    items = json.loads((tmp_path / "v8" / "manifest.json").read_text())["items"]
    assert [it.get("touches", "ABSENT") for it in items] == ["src/a.py, tests/test_a.py", []]
    bp.build(_v7(_spec()), tmp_path / "v7")                         # control: below v8 an empty
    items = json.loads((tmp_path / "v7" / "manifest.json").read_text())["items"]
    assert "touches" not in items[1]                                # declaration stays out, as before


def test_an_empty_touches_item_can_join_a_parallel_group(tmp_path):
    spec = _spec()
    for s in spec["sessions"]:
        s["dispatch"].update(parallel_group="g", isolation="worktree", depends_on=[])
    bp.build(spec, tmp_path / "plan")                               # it-2 declares touches: []
    spec["items"][1]["touches"] = ""                                # control: blank still refused
    _refused(spec, "it-2", "must declare touches")                  # (the v8 check fires before M2a)


def test_the_skill_says_task_class_is_required_at_v8():
    skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
    assert "Task class (optional" not in skill
    assert re.search(r"`task_class`[^\n]{0,20}REQUIRED at v8", skill)
    assert re.search(r"Model is an override, not a question", skill)


@pytest.mark.parametrize("nulls", [("model",), ("reasoning",), ("why_model",),
                                   ("model", "reasoning", "why_model")])
def test_an_explicit_null_override_field_reads_as_absent_at_v8(tmp_path, nulls):
    spec = _spec(s02={"model": None, "reasoning": None, "why_model": None})
    spec["sessions"][1].update(dict.fromkeys(nulls))                # explicit JSON null
    bp.build(spec, tmp_path / "plan")
    s02 = json.loads((tmp_path / "plan" / "manifest.json").read_text())["sessions"][1]
    assert (s02["model"], s02["reasoning"], "why_model" in s02) == ("", "", False)
    page = (tmp_path / "plan" / "PLAN.html").read_text()
    assert f'<span class="chip model-dispatch">{rad.DISPATCH_LABEL}</span>' in page


def test_a_null_half_of_the_pair_is_refused_like_a_missing_one():
    spec = _spec()
    spec["sessions"][1]["reasoning"] = None
    _refused(spec, "Session s02 sets model without reasoning")
    spec["sessions"][1]["why_model"] = None
    spec["sessions"][1]["reasoning"] = "xhigh"
    _refused(spec, "Session s02", "gives no why_model")


def test_a_non_string_override_field_is_refused_at_v8():
    for field, bad in (("model", 5), ("why_model", ["x"])):
        _refused(_spec(s02={field: bad}), f"Session s02.{field} must be a string")
    bp.validate_spec(_spec())                                       # control: strings pass

def _locked(paths):
    spec = _spec()
    spec["sessions"][0]["verify"] = {"gates": ["code-review-gate"], "locked": paths}
    return spec


def test_verify_locked_shape():
    bp.validate_spec(_locked(["tests/test_a.py"]))
    for bad in (["tests/*.py"], ["../x.py"], ["/abs/x.py"], ["a/../b.py"], [""], [], "t.py"):
        _refused(_locked(bad), "locked")
    _refused(_v7(_locked(["tests/test_a.py"])), "needs plan_schema_version 8")


def test_a_locked_directory_or_symlink_is_refused_at_build(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("x")
    (root / "link.py").symlink_to(root / "tests" / "test_a.py")
    (root / "linkdir").symlink_to(root / "tests")
    for bad in ("tests", "link.py", "linkdir/test_a.py"):
        with pytest.raises(ValueError, match="symlink or not a regular file"):
            rad.check_locked_files(_locked([bad]), str(root), root / "_plans" / "p")
    rad.check_locked_files(_locked(["tests/test_a.py", "tests/new.py"]), str(root),
                           root / "_plans" / "p")          # control: a file, and a future file


def test_a_locked_fifo_is_refused_at_build(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    os.mkfifo(root / "tests" / "pipe.py")
    with pytest.raises(ValueError, match="not a regular file"):
        rad.check_locked_files(_locked(["tests/pipe.py"]), str(root), root / "_plans" / "p")


def test_a_locked_only_session_verify_keeps_the_phase_gates(tmp_path):
    spec = _spec()
    spec["phases"] = [{"id": "ph", "verify": {"gates": ["code-review-gate"]}}]
    spec["sessions"][0]["phase"] = "ph"
    spec["sessions"][0]["verify"] = {"locked": ["tests/test_a.py"]}
    bp.build(spec, tmp_path / "plan")
    vb = json.loads((tmp_path / "plan" / "manifest.json").read_text())["sessions"][0]["verify"]
    assert (vb["gates"], vb["locked"]) == (["code-review-gate"], ["tests/test_a.py"])
    del spec["phases"][0]["verify"]                     # nothing to inherit: the lock would vanish
    _refused(spec, "Session s01.verify.locked")


def test_a_fork_session_is_refused_at_v8_and_only_warned_below(capsys):
    fork = {"dispatch": {"subagent_type": "fork", "depends_on": []}}
    _refused(_spec(s01=fork), "Session s01", "fork")
    _refused(_spec(s02={"dispatch": {"subagent_type": "fork", "depends_on": ["s01"]}}),
             "Session s02", "fork")                     # with an override pair too
    bp.validate_spec(_v7(_spec(s01=fork)))              # control: v7 keeps the P4 warning
    assert "subagent_type is 'fork'" in capsys.readouterr().err


def test_a_spec_above_the_supported_max_is_refused():
    msg = _refused(_spec(version=9), "plan_schema_version 9", "at most 8")
    assert "lower" in msg
    _refused(_spec(version="8"), "must be an integer")


def test_the_integration_session_leaves_the_model_to_dispatch():
    spec = _spec()
    for s in spec["sessions"]:
        s["dispatch"].update(parallel_group="g", isolation="worktree", depends_on=[])
    integ = bp.synthesize_integration_sessions(spec)[-1]
    assert integ["task_class"] == "agentic_build"
    assert not {"model", "reasoning", "why_model"} & set(integ)
    old = bp.synthesize_integration_sessions(_v7(spec))[-1]     # control: v7 still pins
    assert (old["model"], old["reasoning"]) == ("Opus", "high")


def test_the_docs_tell_a_new_spec_to_declare_8():
    refs = Path(__file__).resolve().parents[1]
    line = next(ln for ln in (refs / "references" / "schemas.md").read_text().splitlines()
                if ln.startswith("- **`plan_schema_version`**"))
    assert "stamp `5`" not in line and "always `build_plan.PLAN_SCHEMA_VERSION`" not in line
    assert "declare `plan_schema_version: 8`" in line and "stamps 7" in line
    skill = (refs / "SKILL.md").read_text()
    assert '"plan_schema_version": 6`' not in skill and "stamped `plan_schema_version: 7`" not in skill
    assert '"plan_schema_version": 8' in skill

# -- stamps --------------------------------------------------------------------
def test_a_v7_spec_builds_a_manifest_stamped_7(tmp_path):
    bp.build(_v7(_spec()), tmp_path / "plan")
    assert json.loads((tmp_path / "plan" / "manifest.json").read_text())[
        "plan_schema_version"] == 7


def test_preserve_state_refuses_a_newer_stamp_than_supported():
    spec = _spec()
    assert rad.manifest_stamp(spec, rad.SUPPORTED_MAX_SCHEMA, True) == rad.SUPPORTED_MAX_SCHEMA
    with pytest.raises(ValueError, match="supports at most"):
        rad.manifest_stamp(spec, rad.SUPPORTED_MAX_SCHEMA + 1, True)


def _restamp(plan_dir, version):
    p = plan_dir / "manifest.json"
    m = json.loads(p.read_text())
    m["plan_schema_version"] = version
    p.write_text(json.dumps(m, indent=2))


def test_preserve_state_keeps_an_older_stamp_and_refuses_the_v8_boundary(tmp_path):
    plan = tmp_path / "plan"
    v7 = _v7(_spec())
    bp.build(v7, plan)
    _restamp(plan, 6)
    bp.build(v7, plan, preserve_state=True)
    assert json.loads((plan / "manifest.json").read_text())["plan_schema_version"] == 6

    _restamp(plan, 7)
    with pytest.raises(ValueError, match="crosses the v8 boundary"):
        bp.build(_spec(), plan, preserve_state=True)        # v8 spec over a 7 stamp
    assert json.loads((plan / "manifest.json").read_text())["plan_schema_version"] == 7

    bp.build(_spec(), plan)                                  # fresh rebuild: allowed, stamps 8
    assert json.loads((plan / "manifest.json").read_text())["plan_schema_version"] == 8
    with pytest.raises(ValueError, match="crosses the v8 boundary"):
        bp.build(v7, plan, preserve_state=True)             # v7 spec over an 8 stamp
    bp.build(_spec(), plan, preserve_state=True)             # control: v8 over 8 keeps 8
    assert json.loads((plan / "manifest.json").read_text())["plan_schema_version"] == 8


# -- the pre-v8 guarantee, against the real pre-v8 builder -----------------------
_OLD = ("import json, sys; sys.path.insert(0, sys.argv[1]); import build_plan as bp; "
        "print(json.dumps({p: bp.gen_manifest(bp.with_integration_sessions("
        "json.load(open(p)))) for p in sys.argv[2:]}))")


def _old_builder(tmp_path):
    if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{PRE_V8_BUILDER}^{{commit}}"],
                      capture_output=True).returncode:
        pytest.skip(f"pre-v8 builder commit {PRE_V8_BUILDER[:12]} is not in this clone")
    tar = subprocess.run(
        ["git", "-C", str(REPO), "archive", PRE_V8_BUILDER, "skills/plan-builder",
         "skills/plan-execute/scripts", "scripts/resolve_route.py", "model-routing.yaml"],
        capture_output=True, check=True).stdout
    root = tmp_path / "pre-v8"
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(root, filter="data")
    return root / "skills" / "plan-builder" / "scripts"


def _manifests_by_old_builder(scripts, paths):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    out = subprocess.run([sys.executable, "-I", "-c", _OLD, str(scripts), *map(str, paths)],
                         capture_output=True, text=True, check=True, cwd=scripts, env=env)
    return json.loads(out.stdout)


def _manifest_now(path):
    return bp.gen_manifest(bp.with_integration_sessions(json.loads(Path(path).read_text())))


def test_every_pre_v8_spec_builds_the_manifest_the_pre_v8_builder_built(tmp_path):
    specs = [p for p in sorted(REPO.glob("_plans/*/spec.json"))
             if rad.spec_version(json.loads(p.read_text())) < 8]
    assert len(specs) >= 2, f"need at least two pre-v8 specs under {REPO}/_plans"
    v8 = tmp_path / "v8-spec.json"
    v8.write_text(json.dumps(_spec(s02={"model": None, "reasoning": None, "why_model": None})))
    old = _manifests_by_old_builder(_old_builder(tmp_path), [*specs, v8])
    # The old builder really is a different module: it cannot know schema v8.
    assert old[str(v8)] != _manifest_now(v8), "positive control: v8 must differ"
    assert old[str(v8)]["plan_schema_version"] == 7
    diffs = [str(p) for p in specs if old[str(p)] != _manifest_now(p)]
    assert not diffs, f"pre-v8 manifests changed: {diffs}"
    print(f"compared {len(specs)} pre-v8 specs")
