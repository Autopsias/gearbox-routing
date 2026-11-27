"""Main moved only in docs and record files: the gate results stand, the ack does not.

    pytest skills/plan-execute/scripts/test_land_regate_reuse.py -q

Real `run.py` against a real bare origin, the gate counts its runs. A move that touches
skills/ or a CLAUDE.md re-runs the gate, as before.
"""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_regate as lrg  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import ack_only, build_repo, isolate, main_side, run_cli, work  # noqa: E402
from worktree import git  # noqa: E402

GATE = "import sys; open(sys.argv[1], 'a').write('ran\\n')"


def counting_fixture(tmp_path, monkeypatch):
    """A land whose one gate counts its runs in `counter`, a python3 with pytest on PATH, and a
    plan with one commit of its own. Returns (fx, iso, counter)."""
    counter = tmp_path / "gate-runs.txt"
    gate = {"kind": "argv", "cwd": ".", "timeout": 120,
            "argv": [sys.executable, "-c", GATE, str(counter)]}
    fx = build_repo(tmp_path, gates={"marker-gate": gate})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    shim = tmp_path / "shim" / "python3"                  # the gates' python3 needs pytest
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    return fx, iso, counter


def runs(counter):
    return len(counter.read_text().split()) if counter.exists() else 0


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return [r for r in rows if r.get("event") == name]


def test_a_docs_only_main_move_reuses_the_results_and_still_needs_a_new_ack(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    plan_dir = iso["plan_dir"]
    first = run_cli("land", plan_dir)[1]
    assert first["action"] == "land-awaits-review" and runs(counter) == 1
    old = ack_only(plan_dir, "looks right")[1]["ack"]
    main_side(fx, "docs/guide.md", "words\n")
    main_side(fx, "projects/q/memory/note.md", "a note\n")
    out = run_cli("land", plan_dir)[1]
    assert out["action"] == "land-awaits-review", out                 # a fresh ack is required
    assert runs(counter) == 1                                         # the gate did not run again
    assert out["candidate"]["gate_digest"] == first["candidate"]["gate_digest"]
    assert out["candidate"]["main_head"] != first["candidate"]["main_head"]
    assert out["candidate"]["merge_sha"] != first["candidate"]["merge_sha"]
    assert out["invalidated_prior_ack"]["note"] == "looks right" and old["main_head"] != out["candidate"]["main_head"]
    assert "gates reused: main moved only in 2 docs/record files" in out["brief"]
    st = lst.load(str(plan_dir))
    assert st["state"] == "awaiting_review" and not st.get("ack")
    assert st["regate_reuse"]["files"] == ["docs/guide.md", "projects/q/memory/note.md"]
    assert st["gates"][0]["outcome"] == "pass" and st["argv_gates"]["marker-gate"]["gate_key"] == st["gate_key"]
    (event,) = _events(plan_dir, "plan_land_gates_reused")
    assert event["files"] == st["regate_reuse"]["files"] and event["gate_digest"] == st["gate_digest"]
    # the new ack, and only it, lands the new merge
    landed = run_cli("land-ack", plan_dir, "--note", "again")[1]
    assert landed["action"] == "landed" and runs(counter) == 1, landed
    on_main = git(["merge-base", "--is-ancestor", st["merge_sha"], "refs/heads/main"], fx["origin"])[0]
    assert on_main == 0 and git(["show", "main:docs/guide.md"], fx["origin"])[1] == "words"


@pytest.mark.parametrize("rel", ["skills/x/SKILL.md", "CLAUDE.md", "docs/deep/CLAUDE.md", "hooks/h.sh",
                                 "rules/r.md", "scripts/s.sh", "settings.json", "docs/settings.local.json"])
def test_a_move_that_touches_code_or_config_regates(tmp_path, monkeypatch, rel):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review" and runs(counter) == 1
    main_side(fx, "docs/guide.md", "words\n")
    main_side(fx, rel, "{}\n" if rel.endswith(".json") else "x\n")
    out = run_cli("land", iso["plan_dir"])[1]
    assert out["action"] == "land-awaits-review" and runs(counter) == 2, out
    assert "gates reused" not in out["brief"] and "regate_reuse" not in lst.load(str(iso["plan_dir"]))
    assert not _events(iso["plan_dir"], "plan_land_gates_reused")


def test_a_plan_that_changed_since_the_results_regates(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    work(iso["tree"], "src/g.py", "G = 1\n")                       # the plan side moved too
    main_side(fx, "docs/guide.md", "words\n")
    out = run_cli("land", iso["plan_dir"])[1]
    assert out["action"] == "land-awaits-review" and runs(counter) == 2, out


@pytest.mark.parametrize("path,ok", [
    ("docs/a/b.md", True), ("_plans/p/x.json", True), ("projects/q/memory/n.md", True),
    ("evals/routing/outcomes.ndjson", True), ("evals/routing/other.ndjson", False),
    ("skills/a/docs/x.md", False), ("skills/a/memory/x.md", False), ("scripts/memory/x.md", False),
    ("docs/CLAUDE.md", False), ("CLAUDE.md", False), ("settings.json", False),
    ("docs/settings.local.json", False), ("src/a.py", False), ("README.md", False),
    ("_plans_index.md", True), ("_plans/p/_closeouts/s01.json", True), ("_plans/p/spec.json", False),
    ("_plans/p/manifest.json", False), ("_plans/p/tools/x.py", False), ("docs/tool.py", False)])
def test_which_paths_read_no_gate(path, ok):
    assert lrg.reads_no_gate(path) is ok
