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
    for name in ("routing-update", "routing-retro", "repo-review"):
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


def _make_legacy(home):
    """An install made before the ledger existed."""
    (home / "claude" / ".gearbox-install-ledger").unlink()


def test_legacy_uninstall_ignores_other_backups_in_its_folders(tmp_path):
    home = tmp_path / "home"
    assert _install(home).returncode == 0
    _make_legacy(home)
    (home / "claude" / "notes.txt").write_text("mine\n")
    (home / "claude" / "notes.txt.bak-20990101000000").write_text("not install.sh's\n")
    assert _run(home, "--uninstall").returncode == 0
    assert (home / "claude" / "notes.txt").read_text() == "mine\n"


def test_updating_a_legacy_install_keeps_the_original_as_the_restore_target(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("my own rules\n")
    assert _install(home).returncode == 0
    _make_legacy(home)
    import time
    time.sleep(1.1)  # a new timestamp for the next backup
    switch = _run(home, "--accept-example-profile", "--provider", "openai")
    assert switch.returncode == 0, switch.stdout + switch.stderr
    assert len(list(home.glob("CLAUDE.md.bak-*"))) == 2
    assert _run(home, "--uninstall").returncode == 0
    assert (home / "CLAUDE.md").read_text() == "my own rules\n"


def test_a_missing_backup_never_strips_a_claude_md_that_existed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("my own rules\n")
    assert _install(home).returncode == 0
    for bak in home.glob("CLAUDE.md.bak-*"):
        bak.unlink()
    out = _run(home, "--uninstall")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "missing" in out.stderr
    assert "BEGIN ROUTING" in (home / "CLAUDE.md").read_text()


def _run_raw(*args, env=None):
    return subprocess.run([str(ROOT / "install.sh"), *args],
                          cwd=ROOT, capture_output=True, text=True, env=env)


def test_codex_and_antigravity_platforms_install_agents_md_and_default_providers(tmp_path):
    for platform, expected_provider in (("codex", "openai"), ("antigravity", "gemini")):
        home = tmp_path / platform
        first = _run_raw("--platform", platform, "--target-home", str(home), "--accept-example-profile")
        assert first.returncode == 0, first.stdout + first.stderr
        agents_md = home / "AGENTS.md"
        assert agents_md.is_file()
        assert not (home / "CLAUDE.md").exists()
        assert not (home / "GEMINI.md").exists()
        text = agents_md.read_text()
        assert "BEGIN ROUTING" in text
        assert f"active_provider={expected_provider}" in text
        for name in ("routing-update", "routing-retro", "repo-review"):
            assert (home / "skills" / name / "SKILL.md").is_file(), name

        second = _run_raw("--platform", platform, "--target-home", str(home), "--accept-example-profile")
        assert second.returncode == 0, second.stdout + second.stderr
        assert "files backed up:  0" in second.stdout
        assert not list(home.rglob("*.bak-*"))


def test_agents_md_backup_and_uninstall_autodetects_from_ledger(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "AGENTS.md").write_text("my codex rules\n")
    inst = _run_raw("--platform", "codex", "--target-home", str(home), "--accept-example-profile")
    assert inst.returncode == 0, inst.stdout + inst.stderr
    assert "BEGIN ROUTING" in (home / "AGENTS.md").read_text()

    # Omit --platform on --uninstall: ledger should auto-detect AGENTS.md.
    out = _run_raw("--target-home", str(home), "--uninstall")
    assert out.returncode == 0, out.stdout + out.stderr
    assert (home / "AGENTS.md").read_text() == "my codex rules\n"


def test_live_home_guard_protects_codex_and_antigravity_homes(tmp_path):
    fake_home = tmp_path / "user"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".gemini" / "config").mkdir(parents=True)
    codex_alias = tmp_path / "codex-alias"
    codex_alias.symlink_to(fake_home / ".codex")
    env = {**os.environ, "HOME": str(fake_home)}

    for platform, target in (
        ("codex", fake_home / ".codex"),
        ("codex", codex_alias),
        ("antigravity", fake_home / ".gemini" / "config"),
    ):
        refused = _run_raw("--platform", platform, "--target-home", str(target),
                           "--accept-example-profile", env=env)
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert "real" in refused.stderr
        assert not (target / "AGENTS.md").exists()

    ok = _run_raw("--platform", "antigravity", "--target-home", str(fake_home / ".gemini" / "config"),
                  "--i-understand-this-mutates-live-home", "--accept-example-profile", env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "BEGIN ROUTING" in (fake_home / ".gemini" / "config" / "AGENTS.md").read_text()


def test_invalid_platform_and_conflicting_home_flags_refuse(tmp_path):
    bad_plat = _run_raw("--platform", "bogus", "--target-home", str(tmp_path / "x"), "--accept-example-profile")
    assert bad_plat.returncode == 2
    assert not (tmp_path / "x").exists()

    conflict = _run_raw("--target-home", str(tmp_path / "a"), "--claude-home", str(tmp_path / "b"),
                        "--accept-example-profile")
    assert conflict.returncode == 2
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()
