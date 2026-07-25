"""QW-02 (2026-07-03 reliability sweep) — cwd-robust plan-dir resolution +
bare-invocation latest-plan default in run.py.

Covers the two paper cuts from reflection-notes.md #9:
  1. An earlier `cd` shifts cwd relative to a still-relative <plan-dir> arg
     (raw session 960d2926) -> `_search_upward_for_plan` recovers it.
  2. No plan-dir argument at all -> `_resolve_bare_invocation` defaults to
     the CANONICAL-tagged (or highest-dated) row of `_plans_index.md`.

Run: pytest plan-execute/scripts/test_run_resolution.py -q
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run as r  # noqa: E402


def _make_plan(root, slug, extra_index_line=""):
    plan_dir = root / "_plans" / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / "manifest.json").write_text("{}")
    (plan_dir / "PLAN.html").write_text("<html></html>")
    return plan_dir


# --------------------------------------------------------------------------
# _search_upward_for_plan
# --------------------------------------------------------------------------
def test_upward_search_recovers_after_cd_into_plan_dir(tmp_path):
    plan_dir = _make_plan(tmp_path, "myplan-2026-07-01")
    # Simulate the regression: an earlier `cd` left cwd INSIDE the plan dir,
    # but the arg is still the original cwd-relative path.
    found = r._search_upward_for_plan(
        "_plans/myplan-2026-07-01", start=plan_dir
    )
    assert found == plan_dir


def test_upward_search_matches_cwd_itself(tmp_path):
    plan_dir = _make_plan(tmp_path, "myplan-2026-07-01")
    # cwd IS the plan dir and the arg is just its own basename.
    found = r._search_upward_for_plan("myplan-2026-07-01", start=plan_dir)
    assert found == plan_dir


def test_upward_search_finds_plans_dir_from_nested_subdir(tmp_path):
    plan_dir = _make_plan(tmp_path, "myplan-2026-07-01")
    nested = plan_dir / "sessions"
    nested.mkdir()
    found = r._search_upward_for_plan("myplan-2026-07-01", start=nested)
    assert found == plan_dir


def test_upward_search_returns_none_when_no_match(tmp_path):
    found = r._search_upward_for_plan("nonexistent-plan", start=tmp_path)
    assert found is None


# --------------------------------------------------------------------------
# _find_plans_index / _parse_plans_index / _pick_default_plan
# --------------------------------------------------------------------------
def test_find_plans_index_walks_upward(tmp_path):
    (tmp_path / "_plans_index.md").write_text("# Active Plans\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = r._find_plans_index(start=nested)
    assert found == tmp_path / "_plans_index.md"


def test_find_plans_index_returns_none_when_absent(tmp_path):
    assert r._find_plans_index(start=tmp_path) is None


def _index_text(rows):
    lines = ["# Active Plans", "", "## Plans"]
    for html_path, date, canonical in rows:
        tag = " **CANONICAL**" if canonical else ""
        lines.append(f"- **[Some Plan]({html_path})** · created {date}{tag}")
    return "\n".join(lines) + "\n"


def test_pick_default_plan_prefers_canonical_tag(tmp_path):
    _make_plan(tmp_path, "old-plan-2026-06-01")
    _make_plan(tmp_path, "new-plan-2026-07-02")
    index = tmp_path / "_plans_index.md"
    index.write_text(
        _index_text(
            [
                ("_plans/old-plan-2026-06-01/PLAN.html", "2026-06-01", True),
                ("_plans/new-plan-2026-07-02/PLAN.html", "2026-07-02", False),
            ]
        )
    )
    chosen = r._pick_default_plan(index)
    assert chosen == tmp_path / "_plans" / "old-plan-2026-06-01"


def test_pick_default_plan_falls_back_to_highest_date(tmp_path):
    _make_plan(tmp_path, "old-plan-2026-06-01")
    _make_plan(tmp_path, "new-plan-2026-07-02")
    index = tmp_path / "_plans_index.md"
    index.write_text(
        _index_text(
            [
                ("_plans/old-plan-2026-06-01/PLAN.html", "2026-06-01", False),
                ("_plans/new-plan-2026-07-02/PLAN.html", "2026-07-02", False),
            ]
        )
    )
    chosen = r._pick_default_plan(index)
    assert chosen == tmp_path / "_plans" / "new-plan-2026-07-02"


def test_pick_default_plan_none_on_empty_index(tmp_path):
    index = tmp_path / "_plans_index.md"
    index.write_text("# Active Plans\n\nNothing here.\n")
    assert r._pick_default_plan(index) is None


# --------------------------------------------------------------------------
# _resolve_bare_invocation (end-to-end: bare invocation -> canonical plan dir)
# --------------------------------------------------------------------------
def test_resolve_bare_invocation_end_to_end(tmp_path, monkeypatch, capsys):
    _make_plan(tmp_path, "new-plan-2026-07-02")
    index = tmp_path / "_plans_index.md"
    index.write_text(
        _index_text([("_plans/new-plan-2026-07-02/PLAN.html", "2026-07-02", False)])
    )
    monkeypatch.chdir(tmp_path)
    resolved = r._resolve_bare_invocation()
    assert resolved == tmp_path / "_plans" / "new-plan-2026-07-02"
    err = capsys.readouterr().err
    assert "defaulted to" in err


def test_resolve_bare_invocation_raises_when_no_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    try:
        r._resolve_bare_invocation()
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "_plans_index.md" in str(e)
