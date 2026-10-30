"""install-module: every module card installs cleanly into a scratch home.

These tests run the real card blocks, so a card that names a missing file, or
installs a script without the modules it imports, fails here instead of on a
reader's machine.
"""
import ast
import importlib.machinery
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("install_module", str(ROOT / "install-module"))
spec = importlib.util.spec_from_loader("install_module", loader)
im = importlib.util.module_from_spec(spec)
loader.exec_module(im)

MODULES = sorted(p.stem for p in im.CARDS.glob("*.md") if p.stem not in im.NOT_MODULES)


@pytest.mark.parametrize("name", MODULES)
def test_card_installs_and_its_python_finds_its_imports(name, tmp_path):
    home = tmp_path / "claude"
    im.install(name, home, dry=False, with_optional=True)
    missing = []
    for f in home.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                    else [n.module] if isinstance(n, ast.ImportFrom) and n.module and n.level == 0 else [])
            for mod in mods:
                rel = f.parent.relative_to(home) / (mod.split(".")[0] + ".py")
                if (ROOT / "harness" / rel).exists() and not (home / rel).exists():
                    missing.append(f"{f.relative_to(home)} imports {rel}")
    assert not missing, f"{name}: the card does not install a module its files import:\n" + "\n".join(sorted(set(missing)))


@pytest.mark.parametrize("name", MODULES)
def test_install_twice_then_remove_leaves_settings_as_before(name, tmp_path):
    home = tmp_path / "claude"
    home.mkdir()
    before = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]}]},
              "model": "opus"}
    (home / "settings.json").write_text(json.dumps(before))
    im.install(name, home, dry=False, with_optional=True)
    once = json.loads((home / "settings.json").read_text())
    im.install(name, home, dry=False, with_optional=True)
    assert json.loads((home / "settings.json").read_text()) == once, "a second install changed settings.json"
    im.remove(name, home, dry=False)
    assert json.loads((home / "settings.json").read_text()) == before


def _hook_file(group):
    return {m for h in group["hooks"] for m in re.findall(r"hooks/([\w.-]+)", h["command"])}


def test_card_hook_entries_match_the_exported_settings():
    """A card's hook entry must equal harness/settings.json's entry for the same hook file."""
    exported = json.loads((ROOT / "harness" / "settings.json").read_text())["hooks"]
    by_file = {}
    for event, groups in exported.items():
        for g in groups:
            for f in _hook_file(g):
                by_file.setdefault((event, f), []).append(g)
    drift = []
    for name in MODULES:
        for frag in im.fragments(im.card(name), True):
            for event, groups in frag.get("hooks", {}).items():
                for g in groups:
                    for f in _hook_file(g):
                        want = by_file.get((event, f))
                        if want and g not in want:
                            drift.append(f"{name}: {event} {f}\n  card:     {json.dumps(g)}\n  exported: {json.dumps(want[0])}")
    assert not drift, "card hook entries differ from harness/settings.json:\n" + "\n".join(drift)


def test_only_safe_commands_run():
    run, show, _ = im.split_script(
        "mkdir -p ~/.claude/x && cp a ~/.claude/x/\npython3 ~/.claude/x/setup.py install\n"
        "rm -r ~/.claude/skills/x/codex\nrm -rf ~/.claude\nrm -r ~/.claude/skills\n", True)
    assert run == ["mkdir -p ~/.claude/x && cp a ~/.claude/x/", "rm -r ~/.claude/skills/x/codex"]
    assert show == ["python3 ~/.claude/x/setup.py install", "rm -rf ~/.claude", "rm -r ~/.claude/skills"]


def test_bad_settings_file_is_refused_untouched(tmp_path):
    home = tmp_path / "claude"
    home.mkdir()
    (home / "settings.json").write_text("{ not json")
    with pytest.raises(SystemExit):
        im.install("git-safety", home, dry=False, with_optional=False)
    assert (home / "settings.json").read_text() == "{ not json"
