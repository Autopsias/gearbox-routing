"""The Codex global instructions are rendered from CLAUDE.md; these pin the
marker grammar and that the committed render is current."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "render_codex_instructions",
    Path(__file__).with_name("render-codex-instructions.py"))
rci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rci)

SOURCE = """# Claude Code - User Configuration

- shared rule (why: docs/x.md#a)

<!-- claude-only -->
- CLAUDE-ONLY-LINE
<!-- /claude-only -->
<!-- codex-only
- CODEX-ONLY-LINE
-->
Text naming `<!-- claude-only -->` inline stays.

<!-- BEGIN ROUTING (model-routing.yaml v1) -->
ROUTING-LINE
<!-- END ROUTING -->
"""


def test_markers_route_lines_to_the_right_tool():
    out = rci.render_claude_md(SOURCE)
    assert "shared rule (why: ~/.claude/docs/x.md#a)" in out
    assert "CODEX-ONLY-LINE" in out
    assert "inline stays" in out
    for gone in ("CLAUDE-ONLY-LINE", "ROUTING-LINE", "Claude Code - User", "codex-only"):
        assert gone not in out


@pytest.mark.parametrize("bad", [
    "<!-- claude-only -->\nno close\n",
    "<!-- /claude-only -->\n",
    "<!-- codex-only\n<!-- claude-only -->\n-->\n",
])
def test_broken_markers_fail_closed(bad):
    with pytest.raises(rci.RenderError):
        rci.render_claude_md(bad)


@pytest.mark.skipif(
    not (rci.REPO / rci.OUT).exists(),
    reason="no committed codex/global-instructions.md in this layout (public export)")
def test_committed_render_is_current():
    assert rci.main(["--check"]) == 0
