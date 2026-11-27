"""inventory_checks.py — constants and deterministic surface helpers for repo_inventory.py.

Stdlib Python 3 only (uses tiktoken when installed, otherwise chars/4 heuristic).
Read-only: never executes target-repository code or modifies files.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".nuxt",
    ".tox", ".eggs", "coverage", ".turbo", "vendor",
    ".plan-worktrees", ".backups", ".venv-ci",
}

SKIP_REL_DIRS = {
    ".claude/worktrees",
}

MANIFEST_NAMES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile",
    "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "Gemfile", "composer.json", "CMakeLists.txt",
    "Makefile", "justfile", "Taskfile.yml", "Taskfile.yaml", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
)

LOCKFILE_NAMES = (
    "uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "Cargo.lock",
    "go.sum", "Gemfile.lock", "composer.lock",
)

ENV_TEMPLATE_NAMES = (".env.example", ".env.sample", ".env.template")

DOC_ANCHOR_NAMES = (
    "README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "SECURITY.md",
    "CHANGELOG.md", "CODE_OF_CONDUCT.md", "DEVELOPMENT.md",
)

ROOT_CONTEXT_FILES = (
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", "CURSOR.md", "INVARIANTS.md",
    ".cursorrules", ".clinerules", ".github/copilot-instructions.md",
)

SKILL_PARENT_DIRS = (
    ".agents/skills", ".claude/skills", "claude/skills", "skills", "harness/skills",
)

MCP_AND_HARNESS_CONFIGS = (
    ".mcp.json", "mcp.json", "mcp_config.json", ".claude/settings.json",
    ".claude/settings.local.json", ".gemini/config/hooks.json",
    ".gemini/config/skills.json", ".codex/config.toml",
    "claude/model-routing.yaml", "model-routing.yaml",
)

HOOK_PATHS = (".githooks", ".pre-commit-config.yaml", ".husky")

SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".cs",
    ".swift", ".sh", ".bash", ".zsh",
}

NON_TRIVIAL_SKIP_NAMES = {
    "CHANGELOG.md", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "uv.lock", "poetry.lock", "Cargo.lock", "go.sum",
}

MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$")
AT_IMPORT_RE = re.compile(r"(?m)^\s*@[A-Za-z0-9_./~-]+\S*")
CAUTION_CAPS_RE = re.compile(r"\b(NEVER|ALWAYS|MUST|DO NOT|FORBIDDEN)\b")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", re.S)

MODEL_SIGNAL_RE = re.compile(
    r"\b(claude|fable|opus|sonnet|haiku|gemini|neon|hydrogen|gpt-[456]|codex|"
    r"antigravity|thinking_level|thinking_budget|reasoning\.effort|effort_policy|"
    r"active_provider)\b",
    re.IGNORECASE,
)

CI_AGENTIC_SIGNAL_RE = re.compile(
    r"(AGENTS\.md|CLAUDE\.md|skills/|evals/|model-routing|verify-routing|test_docs)",
    re.IGNORECASE,
)

VERIFY_CMD_RE = re.compile(
    r"^\s*(?:-\s*)?(?:run:\s*)?((?:python3?\s+-m\s+pytest|pytest|npm\s+(?:run\s+)?(?:test|lint|check|typecheck)|"
    r"pnpm\s+(?:test|lint|check)|yarn\s+(?:test|lint|check)|cargo\s+(?:test|clippy|check)|"
    r"go\s+test|ruff\s+check|mypy\s+|pyright|eslint|bash\s+\S*verify\S*|make\s+(?:test|lint|check))\b[^\n]*)",
    re.IGNORECASE,
)

MAX_READ_BYTES = 512_000
ANTIGRAVITY_FILE_CAP_BYTES = 24_000

_TIKTOKEN_ENC = None
TOKENIZER_LABEL = "heuristic(chars/4)"
try:
    import tiktoken  # type: ignore

    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    TOKENIZER_LABEL = "tiktoken(cl100k_base)"
except (ImportError, KeyError, ValueError, OSError):
    _TIKTOKEN_ENC = None


def count_tokens(text: str) -> int:
    if _TIKTOKEN_ENC is not None:
        return len(_TIKTOKEN_ENC.encode(text, disallowed_special=()))
    return (len(text) + 3) // 4


def read_text_safe(path: Path) -> str | None:
    try:
        if path.is_symlink() and not path.exists():
            return None
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_READ_BYTES:
            raw = raw[:MAX_READ_BYTES]
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def rel_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def run_git(root: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def strip_fenced_code(text: str) -> str:
    return FENCE_RE.sub("", text)


def find_broken_md_links(file_path: Path, root: Path, text: str) -> list[dict[str, str]]:
    broken: list[dict[str, str]] = []
    for match in MD_LINK_RE.finditer(strip_fenced_code(text)):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith("#"):
            continue
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw_target):
            continue
        if any(ch in raw_target for ch in ("<", ">", "{", "}", "*", "$")):
            continue
        target_no_anchor = raw_target.split("#", 1)[0].split("?", 1)[0]
        if not target_no_anchor:
            continue
        candidate = (file_path.parent / target_no_anchor).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if not candidate.exists():
            broken.append({"file": rel_path(file_path, root), "target": raw_target})
    return broken


def measure_surface(path: Path, root: Path, kind: str, auto_loaded: bool) -> dict[str, Any]:
    is_symlink = path.is_symlink()
    broken = is_symlink and not path.exists()
    size_bytes, lines, tokens, caution_caps = 0, 0, 0, 0
    if not broken and path.is_file():
        try:
            raw = path.read_bytes()
            size_bytes = len(raw)
            text = raw.decode("utf-8", errors="replace")
            lines = len(text.splitlines())
            tokens = count_tokens(text)
            caution_caps = len(CAUTION_CAPS_RE.findall(strip_fenced_code(text)))
        except OSError:
            pass
    return {
        "path": rel_path(path, root),
        "kind": kind,
        "auto_loaded": auto_loaded,
        "is_symlink": is_symlink,
        "symlink_broken": broken,
        "bytes": size_bytes,
        "lines": lines,
        "est_tokens": tokens,
        "caution_caps_count": caution_caps,
        "caution_caps_per_100_lines": round((caution_caps * 100.0) / max(lines, 1), 1),
    }


def walk_repo(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and rel_path(base / d, root) not in SKIP_REL_DIRS
        )
        for name in sorted(filenames):
            files.append(base / name)
            if len(files) >= max_files:
                return files, True
    return files, False


def summarize_top_level_modules(root: Path, all_files: list[Path]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    ext_counts: dict[str, Counter[str]] = {}
    for f in all_files:
        parts = Path(rel_path(f, root)).parts
        top = parts[0] if len(parts) > 1 else "(root)"
        counts[top] += 1
        ext_counts.setdefault(top, Counter())[f.suffix.lower() or "(no-ext)"] += 1
    return [
        {
            "module": top,
            "file_count": count,
            "top_extensions": [ext for ext, _ in ext_counts[top].most_common(4)],
        }
        for top, count in counts.most_common()
    ]


def collect_git_info(root: Path) -> dict[str, Any]:
    if not run_git(root, ["rev-parse", "--show-toplevel"]):
        return {
            "is_git": False, "branch": None, "head_short": None,
            "commits_30d": None, "commits_90d": None, "recent_source_files": [],
        }
    c30 = run_git(root, ["rev-list", "--count", "--since=30.days", "HEAD"])
    c90 = run_git(root, ["rev-list", "--count", "--since=90.days", "HEAD"])
    recent_raw = run_git(root, ["log", "-n", "40", "--name-only", "--pretty=format:"]) or ""
    seen: set[str] = set()
    recent_source: list[str] = []
    for line in recent_raw.splitlines():
        rel = line.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        p = root / rel
        if p.name in NON_TRIVIAL_SKIP_NAMES or p.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in Path(rel).parts) or not p.is_file():
            continue
        recent_source.append(rel)
        if len(recent_source) >= 5:
            break
    return {
        "is_git": True,
        "branch": run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "head_short": run_git(root, ["rev-parse", "--short", "HEAD"]),
        "commits_30d": int(c30) if c30 and c30.isdigit() else 0,
        "commits_90d": int(c90) if c90 and c90.isdigit() else 0,
        "recent_source_files": recent_source,
    }


def extract_verify_commands(source_path: str, text: str) -> list[dict[str, str]]:
    cmds: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in re.sub(r"\\\n[ \t]*", " ", text).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = VERIFY_CMD_RE.match(line)
        if m:
            cmd = re.sub(r"\s+", " ", m.group(1)).strip()
            if cmd not in seen:
                seen.add(cmd)
                cmds.append({"source": source_path, "command": cmd})
    return cmds


def _check_skill_metadata(child: Path, skill_md: Path, root: Path) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    rel_md = rel_path(skill_md, root)
    text = read_text_safe(skill_md) or ""
    fm = FRONTMATTER_RE.match(text)
    has_name = bool(fm and re.search(r"(?m)^name:\s*\S+", fm.group(1)))
    has_desc = bool(fm and re.search(r"(?m)^description:\s*\S+", fm.group(1)))
    if not (has_name and has_desc):
        warnings.append({
            "code": "skill_missing_frontmatter",
            "path": rel_md,
            "detail": "SKILL.md missing required YAML frontmatter name/description",
        })
    refs_dir = child / "references"
    if not refs_dir.is_dir():
        return warnings
    for ref_file in sorted(refs_dir.glob("*.md")):
        ref_text = read_text_safe(ref_file)
        if ref_text is None:
            continue
        ref_lines = len(ref_text.splitlines())
        if ref_lines > 100 and "## Contents" not in ref_text and "## Table of Contents" not in ref_text:
            warnings.append({
                "code": "reference_missing_toc",
                "path": rel_path(ref_file, root),
                "detail": f"{ref_lines} lines (>100) without a '## Contents' TOC",
            })
    return warnings


def _inspect_skill_child(
    child: Path,
    root: Path,
    seen_paths: set[str],
) -> tuple[dict[str, Any] | None, dict[str, str] | None, list[dict[str, str]]]:
    if child.is_symlink() and not child.exists():
        return None, {"path": rel_path(child, root), "reason": "broken skill symlink"}, []
    skill_md = child / "SKILL.md" if child.is_dir() else None
    if not skill_md or (not skill_md.exists() and not skill_md.is_symlink()):
        return None, None, []
    rel_md = rel_path(skill_md, root)
    if rel_md in seen_paths:
        return None, None, []
    seen_paths.add(rel_md)
    rec = measure_surface(skill_md, root, kind="skill", auto_loaded=False)
    rec["parent_symlink"] = child.is_symlink()
    if rec["symlink_broken"]:
        return rec, {"path": rel_md, "reason": "broken SKILL.md symlink"}, []
    return rec, None, _check_skill_metadata(child, skill_md, root)


def collect_skill_entries(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    entries, broken_symlinks, warnings = [], [], []
    seen_paths: set[str] = set()
    for rel_parent in SKILL_PARENT_DIRS:
        parent = root / rel_parent
        if parent.is_symlink() and not parent.exists():
            broken_symlinks.append({"path": rel_parent, "reason": "broken skill directory symlink"})
            continue
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            rec, broken, child_warnings = _inspect_skill_child(child, root, seen_paths)
            if rec is not None:
                entries.append(rec)
            if broken is not None:
                broken_symlinks.append(broken)
            warnings.extend(child_warnings)
    return entries, broken_symlinks, warnings


def find_unreferenced_fixture_dirs(
    root: Path,
    fixture_dirs: list[str],
    test_files: list[str],
    ci_workflows: list[dict[str, Any]],
) -> list[str]:
    if not fixture_dirs:
        return []
    parts = [read_text_safe(root / tf) or "" for tf in test_files]
    parts.extend(read_text_safe(root / wf["path"]) or "" for wf in ci_workflows)
    combined = "\n".join(parts)

    unreferenced: list[str] = []
    for fdir in fixture_dirs:
        p = root / fdir
        subdirs = [c for c in sorted(p.iterdir()) if c.is_dir() and c.name not in SKIP_DIRS] if p.is_dir() else []
        targets = [rel_path(c, root) for c in subdirs] if subdirs else [fdir]
        for target in targets:
            t_path = root / target
            has_local_test = any(
                (f.name.startswith("test_") and f.name.endswith(".py")) or f.name.endswith("_test.py")
                for f in t_path.rglob("*") if f.is_file()
            )
            if has_local_test:
                continue
            if target not in combined and Path(target).name not in combined:
                unreferenced.append(target)
    return sorted(set(unreferenced))
