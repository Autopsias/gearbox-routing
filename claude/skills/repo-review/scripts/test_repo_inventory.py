"""Unit tests for repo_inventory.py."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "repo_inventory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("repo_inventory", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_repo_passes_with_zero_warnings_and_finds_surfaces(tmp_path):
    mod = _load_module()
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "# Repo\n\nSee [README](README.md) and [evals](evals/README.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "README.md").write_text("# Evals\n", encoding="utf-8")
    (tmp_path / "evals" / "eval_routing.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\non: push\njobs:\n  test:\n    steps:\n      - run: pytest tests/ evals/ -q\n"
        "      # checks AGENTS.md\n",
        encoding="utf-8",
    )
    (tmp_path / ".agents" / "skills" / "demo-skill").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo.\n---\nUses Claude Fable and Gemini Neon.\n",
        encoding="utf-8",
    )

    data = mod.scan_repo(tmp_path)
    assert data["agentic_surfaces"]["warnings"] == []
    assert data["rot"]["broken_markdown_links"] == []
    assert data["rot"]["broken_symlinks"] == []
    assert "pyproject.toml" in data["manifests"]["build_and_task"]
    assert "uv.lock" in data["manifests"]["lockfiles"]
    assert data["evals_and_tests"]["test_file_count"] == 1
    assert data["evals_and_tests"]["eval_file_count"] == 2
    assert data["ci_and_hooks"]["workflows"][0]["mentions_agentic_surfaces"] is True
    assert any("pytest tests/ evals/ -q" in c["command"] for c in data["ci_and_hooks"]["extracted_commands"])
    assert any(m["module"] == "tests" for m in data["repo"]["top_level_modules"])
    assert "fable" in data["model_references"]
    assert "neon" in data["model_references"]


def test_detects_context_rot_conflicts_and_broken_symlinks(tmp_path):
    mod = _load_module()
    filler = "x" * 24_500
    (tmp_path / "AGENTS.md").write_text(
        "@docs/nonportable.md\n"
        "See [missing](docs/missing.md).\n"
        "```md\n[ignored-in-fence](does/not/exist.md)\n```\n"
        f"{filler}\n",
        encoding="utf-8",
    )
    (tmp_path / "GEMINI.md").write_text("# Duplicate Gemini rules\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Duplicate Claude rules\n", encoding="utf-8")
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "ghost").symlink_to(tmp_path / ".agents" / "skills" / "ghost")

    data = mod.scan_repo(tmp_path)
    codes = {w["code"] for w in data["agentic_surfaces"]["warnings"]}
    assert "agents_md_over_24kb" in codes
    assert "agents_md_at_import" in codes
    assert "agents_and_gemini_coexist" in codes
    assert "claude_md_not_thin_shim" in codes
    assert "broken_symlink" in codes

    broken_targets = [bl["target"] for bl in data["rot"]["broken_markdown_links"]]
    assert broken_targets == ["docs/missing.md"]
    assert len(data["rot"]["broken_symlinks"]) == 1


def test_detects_symlinked_agents_md_oversized_skill_toc_caution_and_unreferenced_fixtures(tmp_path):
    mod = _load_module()
    (tmp_path / "CLAUDE.md").write_text("# Real file\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "CLAUDE.md")

    # Oversized skill without frontmatter + >100-line reference without TOC
    skill_dir = tmp_path / "skills" / "huge-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("line\n" * 520, encoding="utf-8")
    (skill_dir / "references" / "long.md").write_text("# Long\n" + "detail\n" * 110, encoding="utf-8")

    # Nested CLAUDE.md with high caution-caps density
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "CLAUDE.md").write_text(
        "# Rules\n" + ("NEVER ALWAYS MUST do thing.\n" * 55),
        encoding="utf-8",
    )

    # Unreferenced fixture dir vs tested fixture dir
    (tmp_path / "fixtures" / "orphan-case").mkdir(parents=True)
    (tmp_path / "fixtures" / "orphan-case" / "bad.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "fixtures" / "tested-case").mkdir(parents=True)
    (tmp_path / "fixtures" / "tested-case" / "test_it.py").write_text("def test_a(): pass\n", encoding="utf-8")

    data = mod.scan_repo(tmp_path)
    codes = {w["code"] for w in data["agentic_surfaces"]["warnings"]}
    assert "agents_md_is_symlink" in codes
    assert "skill_md_over_500_lines" in codes
    assert "skill_missing_frontmatter" in codes
    assert "reference_missing_toc" in codes
    assert "high_caution_caps_density" in codes
    assert "unreferenced_fixture_dir" in codes
    assert data["evals_and_tests"]["unreferenced_fixture_dirs"] == ["fixtures/orphan-case"]


def test_cli_json_and_markdown_and_missing_dir(tmp_path):
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")

    out_json = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(tmp_path), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out_json.returncode == 0, out_json.stderr
    parsed = json.loads(out_json.stdout)
    assert parsed["repo"]["is_git"] is False

    out_md = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(tmp_path), "--format", "markdown"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out_md.returncode == 0, out_md.stderr
    assert "# Repo Inventory:" in out_md.stdout

    missing = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2


def test_skips_worktrees_backups_and_venv_ci_while_keeping_claude_configs(tmp_path):
    mod = _load_module()
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    # .claude config files that MUST remain visible
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text('{"model": "opus"}\n', encoding="utf-8")
    (claude_dir / "eval-gates.json").write_text('{"gates": []}\n', encoding="utf-8")

    # Ignored trees populated with files and orphan fixtures that must NOT be scanned
    ignored_files = [
        tmp_path / ".claude" / "worktrees" / "stale-branch" / "fixtures" / "orphan-case" / "bad.yaml",
        tmp_path / ".claude" / "worktrees" / "stale-branch" / "AGENTS.md",
        tmp_path / ".plan-worktrees" / "plan-s01" / "scripts" / "foo.py",
        tmp_path / ".backups" / "2026-11-01" / "hooks" / "old.py",
        tmp_path / ".venv-ci" / "lib" / "python3.12" / "site.py",
    ]
    for path in ignored_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ignored\n", encoding="utf-8")

    data = mod.scan_repo(tmp_path)
    assert data["repo"]["scan_truncated"] is False
    assert data["repo"]["total_files_scanned"] == 3  # README.md + .claude/settings.json + .claude/eval-gates.json
    assert ".claude/settings.json" in data["agentic_surfaces"]["mcp_and_configs"]
    assert ".claude/eval-gates.json" in data["evals_and_tests"]["eval_paths"]
    assert data["evals_and_tests"]["unreferenced_fixture_dirs"] == []

    modules = {m["module"]: m["file_count"] for m in data["repo"]["top_level_modules"]}
    assert modules.get(".claude") == 2
    for skipped in (".plan-worktrees", ".backups", ".venv-ci"):
        assert skipped not in modules
