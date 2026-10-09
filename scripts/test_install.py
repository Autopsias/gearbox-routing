"""install.sh puts the routing skills where Claude Code loads personal skills.

Claude Code reads personal skills only from <home>/skills/<name>/SKILL.md. An
earlier install.sh put them under <home>/claude/skills/, so /routing-update and
/routing-retro never showed up. Also checks that a second run changes nothing.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install(home):
    return subprocess.run(
        [str(ROOT / "install.sh"), "--claude-home", str(home),
         "--accept-example-profile", "--provider", "anthropic"],
        cwd=ROOT, capture_output=True, text=True)


def test_skills_land_where_claude_code_loads_them(tmp_path):
    home = tmp_path / "home"
    first = _install(home)
    assert first.returncode == 0, first.stdout + first.stderr
    for name in ("routing-update", "routing-retro"):
        assert (home / "skills" / name / "SKILL.md").is_file(), name
    assert (home / "claude" / "model-routing.yaml").is_file()

    second = _install(home)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "files backed up:  0" in second.stdout
    assert not list(home.rglob("*.bak-*"))
