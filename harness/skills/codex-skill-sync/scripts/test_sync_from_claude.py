import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("sync_from_claude.py")


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def generated(name: str) -> str:
    return f"""---
name: {name}
description: old
---

<!-- codex-skill-sync source=~/.claude/skills/{name}/SKILL.md -->
"""


def run(
    home: Path, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), **(extra_env or {})},
        check=False,
    )


def build_fixture(home: Path) -> None:
    write(
        home / ".claude/skills/active/SKILL.md",
        "---\nname: active\ndescription: Active skill. Use when active.\n---\n\n# Current\n",
    )
    write(
        home / ".claude/skills/ported/SKILL.md",
        "---\nname: ported\ndescription: Ported skill. Use when ported.\n---\n",
    )
    write(home / ".codex/skills/ported/SKILL.md", "hand-authored port\n")

    write(home / ".agents/skills/active/SKILL.md", generated("active") + "old body\n")
    write(home / ".agents/skills/stale/SKILL.md", generated("stale"))
    write(home / ".agents/skills/ported/SKILL.md", generated("ported"))
    write(home / ".agents/skills/nlm-skill/SKILL.md", generated("nlm-skill"))
    write(
        home / ".agents/skills/manual/SKILL.md",
        "# user-authored; mentioning kind=sync is not provenance\n",
    )

    # The arrow runs from ~/.agents into Claude for this one. Even a historical
    # sync marker is not permission to remove or wrap the canonical destination.
    write(home / ".agents/skills/reverse/SKILL.md", generated("reverse"))
    (home / ".claude/skills/reverse").symlink_to(
        home / ".agents/skills/reverse", target_is_directory=True
    )


def test_dry_run_reports_but_does_not_remove(tmp_path: Path) -> None:
    build_fixture(tmp_path)

    proc = run(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "removed skills:    2" in proc.stdout
    assert "ported, stale" in proc.stdout
    assert (tmp_path / ".agents/skills/stale").is_dir()
    assert (tmp_path / ".agents/skills/ported").is_dir()


def test_apply_removes_only_generated_stale_mirrors_and_settles(tmp_path: Path) -> None:
    build_fixture(tmp_path)

    applied = run(tmp_path, "--apply")

    assert applied.returncode == 0, applied.stderr
    assert not (tmp_path / ".agents/skills/stale").exists()
    assert not (tmp_path / ".agents/skills/ported").exists()
    assert (tmp_path / ".agents/skills/manual/SKILL.md").read_text().startswith("# user-authored")
    assert (tmp_path / ".agents/skills/nlm-skill/SKILL.md").exists()
    assert (tmp_path / ".agents/skills/reverse/SKILL.md").read_text() == generated("reverse")
    assert "# Current" in (tmp_path / ".agents/skills/active/SKILL.md").read_text()

    settled = run(tmp_path)
    assert settled.returncode == 0, settled.stderr
    assert "new skills:        0" in settled.stdout
    assert "updated skills:    0" in settled.stdout
    assert "reference files:   0" in settled.stdout
    assert "removed skills:    0" in settled.stdout


def test_apply_refuses_a_skill_directory_symlink(tmp_path: Path) -> None:
    write(
        tmp_path / ".claude/skills/active/SKILL.md",
        "---\nname: active\ndescription: Active. Use when active.\n---\n",
    )
    outside = tmp_path / "outside"
    write(outside / "SKILL.md", "must stay unchanged\n")
    (tmp_path / ".agents/skills").mkdir(parents=True)
    (tmp_path / ".agents/skills/active").symlink_to(outside, target_is_directory=True)

    proc = run(tmp_path, "--apply")

    assert proc.returncode != 0
    assert "destination contains a symlink" in proc.stderr
    assert (outside / "SKILL.md").read_text() == "must stay unchanged\n"


def test_kind_sync_text_is_not_overwrite_provenance(tmp_path: Path) -> None:
    write(
        tmp_path / ".claude/skills/active/SKILL.md",
        "---\nname: active\ndescription: Active. Use when active.\n---\n",
    )
    manual = "# user-authored\nThis note happens to mention kind=sync.\n"
    write(tmp_path / ".agents/skills/active/SKILL.md", manual)

    proc = run(tmp_path, "--apply")

    assert proc.returncode == 0, proc.stderr
    assert "protected (hand-adapted, review by hand): 1" in proc.stdout
    assert (tmp_path / ".agents/skills/active/SKILL.md").read_text() == manual


def test_apply_refuses_a_supporting_tree_symlink(tmp_path: Path) -> None:
    write(
        tmp_path / ".claude/skills/active/SKILL.md",
        "---\nname: active\ndescription: Active. Use when active.\n---\n",
    )
    write(tmp_path / ".claude/skills/active/references/note.md", "canonical\n")
    write(tmp_path / ".agents/skills/active/SKILL.md", generated("active"))
    outside = tmp_path / "outside"
    write(outside / "note.md", "must stay unchanged\n")
    (tmp_path / ".agents/skills/active/references").symlink_to(
        outside, target_is_directory=True
    )

    proc = run(tmp_path, "--apply")

    assert proc.returncode != 0
    assert "destination contains a symlink" in proc.stderr
    assert (outside / "note.md").read_text() == "must stay unchanged\n"
    assert (tmp_path / ".agents/skills/active/SKILL.md").read_text() == generated("active")


def test_references_travel_from_where_each_kind_keeps_them(tmp_path: Path) -> None:
    # Command reference docs live in ~/.claude/references/, outside commands/,
    # so Claude Code does not list each one as a slash command. Agents keep
    # theirs in agents/references/.
    write(
        tmp_path / ".claude/commands/demo.md",
        "---\ndescription: Demo. Use when demo.\n---\n\nRead `~/.claude/references/demo/guide.md`.\n",
    )
    write(tmp_path / ".claude/references/demo/guide.md", "the guide\n")
    write(
        tmp_path / ".claude/agents/helper.md",
        "---\ndescription: Helper. Use when helping.\n---\n\nSee `references/helper/notes.md`.\n",
    )
    write(tmp_path / ".claude/agents/references/helper/notes.md", "agent notes\n")

    proc = run(tmp_path, "--apply")

    assert proc.returncode == 0, proc.stderr
    mirror = tmp_path / ".agents/skills/demo"
    body = (mirror / "SKILL.md").read_text()
    assert "Read `references/demo/guide.md`." in body
    assert "~/.claude/references/" not in body
    assert (mirror / "references/demo/guide.md").read_text() == "the guide\n"
    agent = tmp_path / ".agents/skills/helper/references/helper/notes.md"
    assert agent.read_text() == "agent notes\n"


def test_a_pointer_inside_a_copied_doc_still_reaches_the_deployed_tree(
    tmp_path: Path,
) -> None:
    # ci-orchestrate/agent-routing.md cites ~/.claude/references/lib/, which no
    # command body names. Docs are copied byte for byte, so that absolute pointer
    # reads the deployed tree and lib/ needs no copy. Following doc-to-doc
    # citations instead copied many extra files into several mirrors: a shared doc
    # that lists its consumers reads as citing them.
    write(
        tmp_path / ".claude/commands/demo.md",
        "---\ndescription: Demo. Use when demo.\n---\n\nRead `~/.claude/references/demo/guide.md`.\n",
    )
    write(
        tmp_path / ".claude/references/demo/guide.md",
        "See `~/.claude/references/lib/helper.md`.\n",
    )
    write(tmp_path / ".claude/references/lib/helper.md", "the helper\n")

    proc = run(tmp_path, "--apply")

    assert proc.returncode == 0, proc.stderr
    mirror = tmp_path / ".agents/skills/demo/references"
    copied = (mirror / "demo/guide.md").read_text()
    assert copied == "See `~/.claude/references/lib/helper.md`.\n"
    assert not (mirror / "lib").exists()


def test_claude_dir_override_selects_the_deployed_canon(tmp_path: Path) -> None:
    home = tmp_path / "unrelated-home"
    canon = tmp_path / "deployed-claude"
    agents = tmp_path / "agents"
    ports = tmp_path / "ports"
    write(
        canon / "skills/active/SKILL.md",
        "---\nname: active\ndescription: Active. Use when active.\n---\n",
    )

    proc = run(
        home,
        "--apply",
        extra_env={
            "CLAUDE_DIR": str(canon),
            "GEARBOX_AGENT_SKILLS": str(agents),
            "GEARBOX_CODEX_SKILLS": str(ports),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert (agents / "active/SKILL.md").is_file()
