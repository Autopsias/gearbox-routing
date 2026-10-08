"""LND-02 — `at_land` gates run in land's re-gate, before the push
(finish-contract.md "Registry flag at_land"; plan-isolation-contract.md §4.4 R2).

Real git, real `run.py land`, on the shared `_land_fixture`. The gates are tiny
`/bin/sh` argv commands — never the repo's real `make check`.

    pytest skills/plan-execute/scripts/test_land_at_land.py -q
"""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_at_land as lal  # noqa: E402
import land_gate as lg  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import (GATE_ARGV, build_repo, isolate, local_main,  # noqa: E402
                           main_side, origin_main, run_cli, work)
from worktree import git  # noqa: E402

REG = ".claude/eval-gates.json"
MARKER = {"kind": "argv", "argv": GATE_ARGV, "cwd": ".", "timeout": 120}
WARN = "CI's check does not run before the push"
BUNDLED = "skills/plan-execute/references/eval-gates.default.json"


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _registry(**extra):
    return json.dumps({"marker-gate": MARKER, **extra})


def _ci(argv_sh, **kw):
    return {"kind": "argv", "argv": ["/bin/sh", "-c", argv_sh], "cwd": ".",
            "timeout": 60, "at_land": True, **kw}


def _gates(plan_dir):
    return {g["gate_id"]: g for g in lst.load(plan_dir)["gates"]}


def test_an_at_land_gate_runs_on_the_merged_tree_and_reports_its_wall_time(fx):
    iso = isolate(fx, "plan-a")
    main_side(fx, "from-main.txt", "main\n")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # Passes ONLY where both sides exist together: the merged candidate.
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("test -f from-main.txt && test -f src/f.py")}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    gates = _gates(iso["plan_dir"])
    assert gates["ci-gate"]["outcome"] == "pass"        # not plan-declared, still ran
    assert set(gates) == {"marker-gate", "ci-gate"}
    for g in gates.values():
        assert isinstance(g["wall_s"], float) and g["wall_s"] >= 0
    assert lst.load(iso["plan_dir"])["gate_digest"] == lst.gate_digest(list(gates.values()))
    assert "ci-gate" in out["brief"] and " s)" in out["brief"]
    assert "this plan added the at_land gate 'ci-gate'" in out["brief"]


def test_an_at_land_gate_only_on_origin_is_still_found(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # Arrives on origin AFTER the plan branched; the plan's own trees never had it.
    main_side(fx, REG, _registry(**{"late-ci": _ci("true")}))
    assert "late-ci" not in (iso["tree"] / REG).read_text()
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _gates(iso["plan_dir"])["late-ci"]["outcome"] == "pass"
    assert "at_land gate 'late-ci'" not in out["brief"]  # origin's own gate: no plan change


def test_a_failing_at_land_gate_parks_and_pushes_nothing(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("echo boom; exit 1")}))
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])
    # Red on the base too, so LND-03 adds no repair session: `gate-inherited`.
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-inherited"), out
    assert out["failed"] == ["ci-gate"]
    assert origin_main(fx) == before == local_main(fx)


def test_land_ack_refuses_a_gate_inherited_park(fx):
    # LND-14 stop rule (finish-contract.md, 2026-10-04): there is no way to accept
    # an inherited red gate, so a `gate-inherited` park cannot be acked through.
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("exit 1")}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-inherited"), out
    rc, out = run_cli("land-ack", iso["plan_dir"], "--note", "accept it anyway")
    assert rc != 0 and out["action"] == "error", out
    st = lst.load(iso["plan_dir"])
    assert (st["state"], st["park"]["kind"], st.get("ack")) == ("parked", "gate-inherited", None)


def test_the_candidates_at_land_entry_wins_over_a_plan_declared_one(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # origin re-defines the plan-declared marker-gate as an at_land gate that fails;
    # the primary checkout's registry (today's resolution) still has the passing one.
    main_side(fx, REG, json.dumps({"marker-gate": _ci("echo candidate; exit 1")}))
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-inherited"), out
    assert out["failed"] == ["marker-gate"]
    assert origin_main(fx) == before


def test_the_candidates_own_bundled_default_is_read(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # A repo that ships the skill: its bundled default changes on the plan branch.
    work(iso["tree"], BUNDLED, json.dumps({"bundled-ci": _ci("true")}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _gates(iso["plan_dir"])["bundled-ci"]["outcome"] == "pass"


@pytest.mark.parametrize("text,needle", [
    ("{not json", "JSONDecodeError"),
    ("[]", "not a JSON object"),
    (_registry(**{"ci-gate": _ci("true", at_land="yes")}), "non-boolean at_land"),
])
def test_an_unreadable_project_registry_parks_and_pushes_nothing(fx, text, needle):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, text)
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked",
                                                "at-land-registry-unreadable"), out
    assert needle in out["registry_error"] and "eval-gates.json" in out["registry_error"]
    assert origin_main(fx) == before


def test_a_plan_with_no_at_land_gate_behaves_as_before(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    st = lst.load(iso["plan_dir"])
    assert [g["gate_id"] for g in st["gates"]] == ["marker-gate"]
    assert st["at_land"] == [] and st["at_land_warning"] is None
    # Same digest the pre-LND-02 re-gate computed for the same verdict.
    assert st["gate_digest"] == lst.gate_digest([{"gate_id": "marker-gate", "outcome": "pass"}])
    assert WARN not in out["brief"]                     # no workflows, no warning


@pytest.mark.parametrize("workflow", ["ci.yml", "ci.yaml"])
def test_the_ci_warning_needs_workflows_and_no_at_land_gate(fx, workflow):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], f".github/workflows/{workflow}", "on: push\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert sum(WARN in ln for ln in out["brief"].splitlines()) == 1
    # KNOWN NEGATIVE — the same repo with an at_land gate gets no warning.
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("true")}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert WARN not in out["brief"]


def test_registry_discovery_unit(tmp_path):
    assert lal.registry(tmp_path).get("marker-gate") is None    # absent file: no gates
    (tmp_path / ".claude").mkdir()
    os.symlink(tmp_path / "missing.json", tmp_path / REG)       # present but unreadable
    with pytest.raises(lal.Unreadable):
        lal.registry(tmp_path)
    (tmp_path / REG).unlink()
    (tmp_path / REG).write_text(_registry(**{"x": _ci("true", cwd="sub")}))
    g = dict(lal.at_land_gates(tmp_path, "/plan/tree"))["x"]   # beside the shared review
    assert (g["cwd"], g["kind"]) == ("/plan/tree/sub", "argv")
    (tmp_path / BUNDLED).parent.mkdir(parents=True)
    (tmp_path / BUNDLED).write_text("{not json")               # candidate's bundled copy
    with pytest.raises(lal.Unreadable, match="eval-gates.default.json"):
        lal.registry(tmp_path)


@pytest.mark.parametrize("sh,kind", [("true", "land-parked"),
                                     ("echo TESTS ACTUALLY RAN", "land-awaits-review")])
def test_an_at_land_gate_is_held_to_its_expect(fx, sh, kind):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci(sh, expect="TESTS ACTUALLY RAN")}))
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, kind), out
    if kind == "land-parked":       # exit 0 with no EXPECT line is a fail, said out loud
        assert out["kind"] == "gate-inherited" and out["failed"] == ["ci-gate"], out
        assert "did not contain EXPECT 'TESTS ACTUALLY RAN'" in out["brief"]
        assert lst.load(iso["plan_dir"]).get("argv_gates", {}).get("ci-gate") is None
    assert origin_main(fx) == before


def test_a_candidate_at_land_entry_resolves_a_gate_the_plan_registry_lacks(fx):
    m = fx["plans"]["plan-a"] / "manifest.json"
    m.write_text(m.read_text().replace('"marker-gate"]', '"marker-gate", "ci-gate"]'))
    git(["commit", "-qam", "declare ci-gate"], fx["root"], check=True)
    git(["push", "-q", "origin", "main"], fx["root"], check=True)
    iso = isolate(fx, "plan-a")
    # The primary checkout's registry has no ci-gate; the candidate supplies it.
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("true")}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _gates(iso["plan_dir"])["ci-gate"]["outcome"] == "pass"


def test_plan_changes_names_each_at_land_gate_the_plan_side_touched(tmp_path):
    def commit(text):
        (tmp_path / REG).write_text(text)
        git(["add", "-A"], tmp_path, check=True)
        git(["-c", "user.email=t@e", "-c", "user.name=T", "commit", "-qm", "o"], tmp_path,
            check=True)
    git(["init", "-q"], tmp_path, check=True)
    (tmp_path / ".claude").mkdir()
    origin = {"same": _ci("true"), "edit": _ci("true"), "gone": _ci("true"), "plain": MARKER}
    commit(json.dumps(origin))
    (tmp_path / REG).write_text(json.dumps({**origin, "edit": _ci("false"), "gone": MARKER,
                                            "new": _ci("true"), "plain": {**MARKER, "timeout": 1}}))
    lines = lal.plan_changes(tmp_path, "HEAD")
    assert [ln.split("'")[1] for ln in lines] == ["edit", "gone", "new"], lines
    assert [w in ln for w, ln in zip(("changed", "removed", "added"), lines)] == [True] * 3
    (tmp_path / REG).write_text(json.dumps(origin))
    assert lal.plan_changes(tmp_path, "HEAD") == []                 # no plan change: silent
    commit("{bad")
    (tmp_path / REG).write_text(json.dumps(origin))
    [line] = lal.plan_changes(tmp_path, "HEAD")
    assert "could not read origin" in line and "JSONDecodeError" in line


def test_a_registry_lstat_permission_error_parks_not_reads_as_absent(tmp_path, monkeypatch):
    real = os.lstat

    def lstat(path, *a, **kw):
        if str(path).endswith(REG):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(lal.os, "lstat", lstat)
    # `os.path.lexists` read this as ABSENT and dropped the file; Unreadable parks
    # at-land-registry-unreadable (test_an_unreadable_project_registry_parks...).
    with pytest.raises(lal.Unreadable, match="PermissionError"):
        lal.registry(tmp_path)


def test_a_candidate_cwd_symlinked_outside_the_land_tree_parks(tmp_path):
    plan, land, outside = (tmp_path / n for n in ("plan", "land", "outside"))
    for d in (plan / "sub", land / "ok", outside):
        d.mkdir(parents=True)
    os.symlink(outside, land / "sub")   # the plan's `sub` is real; the candidate's escapes
    # None is the caller's park (`gate-cwd-outside-land`, test_land.py).
    assert lg.gate_cwd(str(plan / "sub"), plan, land) is None
    assert lg.gate_cwd(str(plan / "ok"), plan, land) == str((land / "ok").resolve())


def test_a_plan_side_symlink_cannot_hide_an_escaping_candidate_cwd(tmp_path):
    plan, land, outside = (tmp_path / n for n in ("plan", "land", "outside"))
    for d in (plan / "ok", land / "ok", outside):
        d.mkdir(parents=True)
    os.symlink(plan / "ok", plan / "sub")    # resolving the PLAN side would check land/ok
    os.symlink(outside, land / "sub")        # ... while the gate would run in land/sub
    assert lg.gate_cwd(str(plan / "sub"), plan, land) is None
    assert lg.gate_cwd(".", plan, land) == str(land.resolve())


def test_a_claude_path_that_is_a_plain_file_means_no_project_gates(tmp_path):
    (tmp_path / ".claude").write_text("")    # lstat of .claude/eval-gates.json: NotADirectoryError
    assert lal.registry(tmp_path).get("marker-gate") is None


@pytest.mark.parametrize("cwd", [[], "", 0, False, {}])
def test_an_at_land_gate_with_a_malformed_cwd_parks_and_runs_nothing(fx, cwd):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, _registry(**{"ci-gate": _ci("touch ran", cwd=cwd)}))
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked",
                                                "at-land-registry-unreadable"), out
    assert "invalid cwd" in out["registry_error"], out
    assert not lst.load(iso["plan_dir"]).get("gates")          # nothing ran
    assert not any(Path(lst.load(iso["plan_dir"])["land_path"]).rglob("ran"))
    assert origin_main(fx) == before


def test_an_at_land_gate_with_no_cwd_runs_at_the_tree_root(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    gate = _ci("test -f src/f.py")
    del gate["cwd"]
    work(iso["tree"], REG, _registry(**{"ci-gate": gate}))
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    assert _gates(iso["plan_dir"])["ci-gate"]["outcome"] == "pass"


def test_a_repo_without_its_own_list_gets_the_shared_review_at_land(tmp_path):
    # No skills/ copy and no .claude/eval-gates.json: the runtime default applies.
    assert "llm-review-high" in dict(lal.at_land_gates(tmp_path, tmp_path))


def test_a_review_at_land_does_not_hide_the_ci_warning(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\n")
    assert lal.ci_warning(tmp_path, ["llm-review-high"]) is not None
    (tmp_path / ".claude").mkdir()
    (tmp_path / REG).write_text(_registry(**{"ci-gate": _ci("true")}))
    assert lal.ci_warning(tmp_path, ["ci-gate", "llm-review-high"]) is None
