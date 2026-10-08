#!/usr/bin/env python3
"""register_plan_in_project: what goes into _plans_index.md, and what does not.

A plan built in a temp directory used to write its absolute build path into the
project index — /private/var/folders/.../tmp.XXXX/PLAN.html. That link resolves
on no other machine, and the de-dup regex keys on the same path, so every temp
build appended one more dead entry (4 of them in this repo before the guard).

Run: pytest skills/plan-builder/scripts/test_register_plan.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_plan  # noqa: E402

SPEC = {"title": "Test Plan", "sessions": [{"id": "s01"}], "items": [{"id": "i1"}]}


def _plan_html(plan_dir):
    plan_dir.mkdir(parents=True, exist_ok=True)
    html = plan_dir / "PLAN.html"
    html.write_text("<html></html>")
    return html


def test_plan_inside_project_is_registered_by_relative_path(tmp_path):
    project = tmp_path / "project"
    html = _plan_html(project / "_plans" / "test-plan-2026-09-07")

    index_path, snippet = build_plan.register_plan_in_project(html, SPEC, project)

    body = index_path.read_text()
    assert "(_plans/test-plan-2026-09-07/PLAN.html)" in body
    assert str(tmp_path) not in body
    assert "_plans/test-plan-2026-09-07" in snippet


def test_plan_outside_project_writes_no_entry(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    html = _plan_html(tmp_path / "elsewhere" / "rt")

    index_path, snippet = build_plan.register_plan_in_project(html, SPEC, project)

    assert (index_path, snippet) == (None, None)
    assert not (project / "_plans_index.md").exists()


def test_existing_index_keeps_its_entries_when_the_build_is_outside(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    index = project / "_plans_index.md"
    index.write_text("# Active Plans\n\n- **[Kept](_plans/kept/PLAN.html)** · 1 sessions\n")
    html = _plan_html(tmp_path / "elsewhere" / "rt")

    build_plan.register_plan_in_project(html, SPEC, project)

    assert index.read_text().count("Kept") == 1
    assert "elsewhere" not in index.read_text()


def test_rebuild_without_register_in_reads_the_project_gates(tmp_path, monkeypatch):
    """A --rebuild without --register-in must still read the project's
    .claude/eval-gates.json. It used to skip the file and refuse every
    project-only verify gate as "not in the project's .claude/eval-gates.json"
    (reproduced 2026-10-01 on _plans/finish-every-plan-2026-10-01)."""
    import json

    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "eval-gates.json").write_text(
        json.dumps({"proj-only": {"kind": "argv", "argv": ["true"]}}))
    spec = {
        "title": "Rebuild Fixture",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": "it-1", "title": "IT-1", "category": "work"}],
        "phases": [],
        "sessions": [{"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
                      "prompt": "do it", "verify": {"gates": ["proj-only"]}}],
        "infographic": {"type": "phase-journey", "title": "t",
                        "phases": [{"num": 1, "name": "P1", "items": ["it-1"]}],
                        "anchor_now": {"name": "a", "tagline": "b"},
                        "anchor_goal": {"name": "c", "tagline": "d"}},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    plan_dir = project / "_plans" / "fx"
    build_plan.build(spec, plan_dir, project_root=str(project))

    monkeypatch.setattr(sys, "argv", ["build_plan.py", str(spec_path), str(plan_dir), "--rebuild"])
    build_plan.main()

    assert (plan_dir / "manifest.json").is_file()
    # The _plans_index.md write stays gated on --register-in.
    assert not (project / "_plans_index.md").exists()
