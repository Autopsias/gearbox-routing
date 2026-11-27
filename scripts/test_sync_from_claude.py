"""Unit tests for sync-from-claude.py CLAUDE.md template export and marker fail-closed guard."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sync_from_claude", Path(__file__).with_name("sync-from-claude.py")
)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def test_claude_md_exports_as_template_removes_stale_and_applies_scrub_rules(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "CLAUDE.md").write_text(
        "# Private rules\n"
        "Secret path: /Users/private-user/work\n"
        "<!-- BEGIN ROUTING (model-routing.yaml v45) -->\n"
        "private calibration table\n"
        "<!-- END ROUTING -->\n"
        "Footer.\n",
        encoding="utf-8",
    )

    harness = tmp_path / "harness"
    harness.mkdir()
    stale = harness / "CLAUDE.md"
    stale.write_text("stale file that must be removed on sync\n", encoding="utf-8")

    manifest = {
        "entries": [{"path": "CLAUDE.md", "verdict": "scrub-then-publish"}],
        "scrub_rules": [
            {"find": "/Users/private-user", "replace": "~", "path": "CLAUDE.md"},
        ],
    }
    scrub_rules = sync.compile_scrub_rules(manifest)
    copied = sync.copy_entries(manifest, source, harness, scrub_rules)
    sync.scrub_tree(harness, scrub_rules)

    assert copied == ["CLAUDE.md.template"]
    assert not stale.exists(), "stale harness/CLAUDE.md must be removed on sync"
    template = harness / "CLAUDE.md.template"
    assert template.is_file()
    text = template.read_text(encoding="utf-8")
    assert "private calibration table" not in text
    assert "deliberately not exported" in text
    assert "/Users/private-user" not in text
    assert "Secret path: ~/work" in text


@pytest.mark.parametrize(
    "bad_text",
    [
        "# No markers at all\n",
        "# Missing BEGIN marker\nprivate table\n<!-- END ROUTING -->\n",
        "# Missing END marker\n<!-- BEGIN ROUTING -->\nprivate table\n",
        "# Missing END marker with versioned BEGIN\n<!-- BEGIN ROUTING (model-routing.yaml v45) -->\nprivate table\n",
        (
            "# Complete block followed by an unfinished second BEGIN block\n"
            "<!-- BEGIN ROUTING (model-routing.yaml v45) -->\n"
            "first private table\n"
            "<!-- END ROUTING -->\n"
            "<!-- BEGIN ROUTING (model-routing.yaml v46) -->\n"
            "leaked second private table\n"
        ),
        (
            "# Complete block followed by a stray END marker\n"
            "<!-- BEGIN ROUTING -->\n"
            "first private table\n"
            "<!-- END ROUTING -->\n"
            "<!-- END ROUTING -->\n"
        ),
        (
            "# Reversed END before BEGIN\n"
            "<!-- END ROUTING -->\n"
            "private table\n"
            "<!-- BEGIN ROUTING -->\n"
        ),
    ],
)
def test_scrub_claude_md_fails_closed_on_missing_begin_or_end_markers(bad_text):
    with pytest.raises(sync.SyncError, match="BEGIN/END ROUTING"):
        sync._scrub_claude_md(bad_text)
