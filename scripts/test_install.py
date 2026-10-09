"""install.sh puts the routing skills where Claude Code loads personal skills.

Claude Code reads personal skills only from <home>/skills/<name>/SKILL.md. An
earlier install.sh put them under <home>/claude/skills/, so /routing-update and
/routing-retro never showed up. Also checks that a second run changes nothing.
"""
import os
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


def _run(home, *extra, env=None):
    return subprocess.run([str(ROOT / "install.sh"), "--claude-home", str(home), *extra],
                          cwd=ROOT, capture_output=True, text=True, env=env)


def test_uninstall_restores_only_what_install_backed_up(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("my own rules\n")
    (home / "other.json").write_text("current\n")
    (home / "other.json.bak-1").write_text("another tool's backup\n")
    assert _install(home).returncode == 0
    assert "BEGIN ROUTING" in (home / "CLAUDE.md").read_text()

    out = _run(home, "--uninstall")
    assert out.returncode == 0, out.stdout + out.stderr
    assert (home / "CLAUDE.md").read_text() == "my own rules\n"
    assert (home / "other.json").read_text() == "current\n"


def test_uninstall_strips_a_claude_md_install_created_even_after_an_update(tmp_path):
    home = tmp_path / "home"
    assert _install(home).returncode == 0
    switch = _run(home, "--accept-example-profile", "--provider", "openai")
    assert switch.returncode == 0, switch.stdout + switch.stderr
    assert list(home.glob("CLAUDE.md.bak-*")), "the provider switch should back up CLAUDE.md"

    assert _run(home, "--uninstall").returncode == 0
    assert "BEGIN ROUTING" not in (home / "CLAUDE.md").read_text()


def test_broken_markers_refuse_before_any_write(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bad = "<!-- BEGIN ROUTING -->\nx\n<!-- END ROUTING -->\n<!-- BEGIN ROUTING -->\ny\n<!-- END ROUTING -->\n"
    (home / "CLAUDE.md").write_text(bad)
    out = _install(home)
    assert out.returncode == 2, out.stdout + out.stderr
    assert not (home / "claude").exists()
    assert (home / "CLAUDE.md").read_text() == bad


def test_a_symlink_to_the_real_home_needs_the_opt_in(tmp_path):
    fake_home = tmp_path / "user"
    (fake_home / ".claude").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(fake_home / ".claude")
    env = {**os.environ, "HOME": str(fake_home)}
    out = _run(alias, "--accept-example-profile", env=env)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "real" in out.stderr
    assert not (fake_home / ".claude" / "claude").exists()
