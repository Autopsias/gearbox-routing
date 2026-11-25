"""Docs drift check: the hand-maintained docs must match the tree.

harness/ is re-exported from a private source on every sync, so a module card
in docs/modules/ goes stale the moment the export renames or drops a file.
These tests fail CI when that happens, instead of letting a reader copy a path
that does not exist.

1. Every relative Markdown link in a hand-maintained doc resolves to a file.
2. Every `harness/...` path a module card names exists (braces and globs expanded).
3. Every skill folder, command and agent under harness/ is named by some card.
4. docs/CATALOG.md matches what scripts/build_catalog.py generates from harness/.
5. CHANGELOG.md names the date of the last sync (harness/SYNCED-FROM exported_at).
"""
import glob
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = [ROOT / "docs" / "MODULES.md", *sorted((ROOT / "docs" / "modules").glob("*.md"))]
DOCS = [
    *[ROOT / n for n in ("README.md", "ARCHITECTURE.md", "CONTRIBUTING.md", "GENERICIZATION.md",
                         "SECURITY.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md")],
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted((ROOT / "claude").rglob("README.md")),
    ROOT / "scripts" / "README.md",
]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HARNESS_PATH = re.compile(r"harness/[A-Za-z0-9_.\-/{},*]*")


def _expand_braces(token):
    m = re.search(r"\{([^{}]*)\}", token)
    if not m:
        return [token]
    return [x for alt in m.group(1).split(",")
            for x in _expand_braces(token[:m.start()] + alt + token[m.end():])]


def _card_paths():
    for card in CARDS:
        for token in HARNESS_PATH.findall(card.read_text(encoding="utf-8")):
            token = token.rstrip(".,")
            if token.endswith("/*"):  # "harness/scripts/govrun*.py" style is fine; a bare dir glob is prose
                token = token[:-1]
            for path in _expand_braces(token):
                yield card, path


def _published():
    """Files git would publish: tracked, plus new files that are not ignored."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    files = {(ROOT / f).resolve() for f in out.splitlines()}
    return files | {d for f in files for d in f.parents}


def test_relative_links_resolve():
    published = _published()
    broken = []
    for doc in DOCS:
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path = (doc.parent / target.split("#", 1)[0]).resolve()
            if path not in published:
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not broken, "broken relative links:\n" + "\n".join(broken)


def test_card_paths_exist():
    missing = sorted({f"{card.relative_to(ROOT)}: {path}"
                      for card, path in _card_paths()
                      if not glob.glob(str(ROOT / path))})
    assert not missing, "module cards name harness paths that do not exist:\n" + "\n".join(missing)


def test_every_skill_command_and_agent_has_a_card():
    text = "\n".join(c.read_text(encoding="utf-8") for c in CARDS)
    named = {p for _, path in _card_paths() for p in glob.glob(str(ROOT / path))}
    units = [*(ROOT / "harness" / "skills").iterdir(),
             *(ROOT / "harness" / "commands").glob("*.md"),
             *(ROOT / "harness" / "agents").glob("*.md")]
    orphans = sorted(str(u.relative_to(ROOT)) for u in units
                     if u.name != "__pycache__" and str(u) not in named
                     and u.stem not in text)
    assert not orphans, "harness parts with no module card:\n" + "\n".join(orphans)


def test_catalog_is_current():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_catalog.py"), "--check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_changelog_names_the_last_sync_date():
    stamp = (ROOT / "harness" / "SYNCED-FROM").read_text(encoding="utf-8")
    date = re.search(r"^exported_at:\s*(\d{4}-\d{2}-\d{2})", stamp, re.M).group(1)
    assert f"(harness sync of {date})" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), (
        f"CHANGELOG.md has no entry for the sync of {date}: describe what changed for users, "
        "in the module cards and the changelog, before you commit the sync")


def test_cross_platform_agent_context():
    """AGENTS.md is the cross-platform SSOT; CLAUDE.md is a thin @AGENTS.md adapter."""
    agents_md = ROOT / "AGENTS.md"
    claude_md = ROOT / "CLAUDE.md"
    gemini_md = ROOT / "GEMINI.md"

    assert agents_md.is_file() and not agents_md.is_symlink(), "AGENTS.md must be a regular file"
    assert claude_md.is_file() and not claude_md.is_symlink(), (
        "CLAUDE.md must be a regular file (not a symlink) so Windows checkouts and Edit tools work")
    assert not gemini_md.exists(), (
        "GEMINI.md must not coexist with AGENTS.md at repo root (Antigravity loads both cumulatively)")

    agents_bytes = agents_md.read_bytes()
    assert len(agents_bytes) <= 24_000, (
        f"AGENTS.md is {len(agents_bytes)} bytes, exceeding Antigravity's 24,000-byte per-file cap")
    agents_text = agents_bytes.decode("utf-8")
    assert "<!-- BEGIN ROUTING -->" in agents_text and "<!-- END ROUTING -->" in agents_text
    assert not re.search(r"^\s*@\S+", agents_text, re.M), (
        "AGENTS.md must not use @path imports (Codex and OpenCode do not expand them)")

    claude_lines = [line.strip() for line in claude_md.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert claude_lines and claude_lines[0] == "@AGENTS.md", (
        "CLAUDE.md must start with @AGENTS.md so Claude Code imports AGENTS.md without duplication")
    assert "<!-- BEGIN ROUTING -->" not in claude_md.read_text(encoding="utf-8"), (
        "Routing digest belongs only in AGENTS.md, not duplicated in CLAUDE.md")
