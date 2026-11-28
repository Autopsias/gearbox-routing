"""Unit tests for rust_inventory.py."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent / "rust_inventory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rust_inventory", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "app/parser/lexer.py",
           "import re\n\nTOKEN = re.compile(r'\\w+')\n\n\ndef lex(s):\n    return TOKEN.findall(s)\n")
    _write(tmp_path, "app/api/views.py",
           "import requests\n\n\ndef get(u):\n    return requests.get(u).json()\n")
    _write(tmp_path, "app/plugins/load.py",
           "def load(name, obj):\n    return getattr(obj, name)()\n\n\ndef run(src):\n    eval(src)\n")
    _write(tmp_path, "native/buf.c",
           "#include <string.h>\nvoid f(char *d, const char *s) { strcpy(d, s); }\n")
    _write(tmp_path, "tests/test_lexer.py", "import re\n\ndef test_lex():\n    assert re.compile('a')\n")
    _write(tmp_path, "node_modules/x/index.js", "eval('1')\n")
    _write(tmp_path, "requirements.txt", "requests>=2\n# comment\nnumpy==2.1 ; python_version>'3'\n-e .\n")
    _write(tmp_path, "web/package.json",
           json.dumps({"dependencies": {"react": "^19"}, "devDependencies": {"jest": "1"}}))
    _write(tmp_path, "web/src/App.tsx", "import React from 'react'\nconst x: any = 1\n")
    _write(tmp_path, "core/Cargo.toml", "[package]\nname = 'core'\n")
    _write(tmp_path, "core/src/lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    return tmp_path


def _unit(rep: dict, name: str) -> dict:
    return next(u for u in rep["units"] if u["unit"] == name)


def test_units_lines_tests_and_skipped_dirs(demo_repo):
    rep = _load_module().build_report(demo_repo, depth=2, use_git=False)
    names = {u["unit"] for u in rep["units"]}
    assert "node_modules/x" not in names
    assert _unit(rep, "app/parser")["code_lines"] == 4
    assert _unit(rep, "tests")["test_lines"] == 3 and _unit(rep, "tests")["code_lines"] == 0
    assert rep["rust_present"] == {"cargo_manifests": ["core/Cargo.toml"], "rust_lines": 1}
    assert rep["languages"]["python"] == 4 + 3 + 4  # lexer, views, plugins; tests excluded
    assert rep["churn_window_days"] is None


@pytest.mark.parametrize(("unit", "signal", "direction", "example"), [
    ("app/parser", "parsing_serialization", "for", "app/parser/lexer.py:3"),
    ("app/api", "io_bound_call", "against", "app/api/views.py:5"),
    ("app/plugins", "dynamic_python", "against", "app/plugins/load.py:2"),
    ("native", "unsafe_memory_api", "for", "native/buf.c:2"),
    ("web/src", "ui_framework", "against", "web/src/App.tsx:1"),
    ("web/src", "dynamic_js", "against", "web/src/App.tsx:2"),
])
def test_signals_carry_direction_and_path_line(demo_repo, unit, signal, direction, example):
    rep = _load_module().build_report(demo_repo, depth=2, use_git=False)
    sig = _unit(rep, unit)["signals"][signal]
    assert sig["direction"] == direction
    assert example in sig["examples"]


def test_signals_ignore_test_files(demo_repo):
    rep = _load_module().build_report(demo_repo, depth=2, use_git=False)
    assert _unit(rep, "tests")["signals"] == {}


def test_dependencies_runtime_only(demo_repo):
    rep = _load_module().build_report(demo_repo, depth=2, use_git=False)
    by_manifest = {d["manifest"]: d["runtime_deps"] for d in rep["dependencies"]}
    assert by_manifest["requirements.txt"] == ["requests", "numpy"]
    assert by_manifest["web/package.json"] == ["react"]


def test_bad_manifest_is_reported_not_fatal(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project\nbroken")
    rep = _load_module().build_report(tmp_path, use_git=False)
    assert "error" in rep["dependencies"][0]


def test_large_files_are_skipped(tmp_path):
    mod = _load_module()
    _write(tmp_path, "dist2/bundle.js", "x=1;\n" * (mod.MAX_FILE_BYTES // 4))
    rep = mod.build_report(tmp_path, use_git=False)
    assert rep["skipped_large_files"] == ["dist2/bundle.js"]
    assert rep["total_code_lines"] == 0


def test_git_churn_counts_changed_source_files(demo_repo):
    git = ["git", "-C", str(demo_repo), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    rep = _load_module().build_report(demo_repo, depth=2, use_git=True)
    assert _unit(rep, "app/parser")["churn"] == 1
    assert rep["churn_window_days"] == 365


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_cli_formats(demo_repo, fmt):
    out = subprocess.run([sys.executable, str(SCRIPT_PATH), str(demo_repo), "--no-git", "--format", fmt],
                         capture_output=True, text=True, check=True).stdout
    if fmt == "json":
        assert json.loads(out)["total_code_lines"] > 0
    else:
        assert "| Unit | Code | Tests | Churn |" in out and "app/parser" in out


def test_cli_rejects_missing_dir(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT_PATH), str(tmp_path / "nope")],
                          capture_output=True, text=True)
    assert proc.returncode == 2


def test_manifest_symlink_is_skipped_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-dependency\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").symlink_to(outside)
    found, _ = _load_module().scan_dependencies(repo)
    assert found == [{"manifest": "requirements.txt", "ecosystem": "pypi",
                      "error": "skipped: symlink, not a regular file, or over the size cap"}]


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen to sign a commit")
def test_git_churn_ignores_repo_config_that_runs_a_program(tmp_path: Path) -> None:
    repo, marker = tmp_path / "repo", tmp_path / "ran"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "key")], check=True)
    _write(repo, "src/a.py", "x = 1\n")
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["-c", "gpg.format=ssh", "-c", f"user.signingkey={tmp_path / 'key'}",
                          "commit", "-q", "-S", "-m", "signed"], check=True)
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", encoding="utf-8")
    evil.chmod(0o755)
    (tmp_path / "signers").write_text("", encoding="utf-8")
    for key, value in (("log.showSignature", "true"), ("gpg.ssh.program", str(evil)),
                       ("gpg.ssh.allowedSignersFile", str(tmp_path / "signers"))):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    # control: plain git log does run the planted program, so the check below can fail
    subprocess.run(["git", "-C", str(repo), "log", "--name-only", "--pretty=format:"], capture_output=True)
    if not marker.exists():
        pytest.skip("this git does not verify signatures in log; the attack cannot be shown here")
    marker.unlink()
    churn = _load_module().git_churn(repo, 2, 365)
    assert not marker.exists()
    assert churn == {"src": 1}
