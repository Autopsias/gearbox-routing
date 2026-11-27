"""The Repo verify skill section appears only when the project root has the skill."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import session_bundle as sb  # noqa: E402

SESSION = {"id": "s01", "title": "t", "items": [], "prompt": "do it"}


def test_section_only_when_skill_present(tmp_path):
    bare = sb.gen_session_prompt_md(SESSION, "p", {}, project_root=str(tmp_path))
    assert bare == sb.gen_session_prompt_md(SESSION, "p", {})
    assert "Repo verify skill" not in bare
    skill = tmp_path / ".claude" / "skills" / "verify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    assert "## Repo verify skill" in sb.gen_session_prompt_md(SESSION, "p", {}, project_root=str(tmp_path))
