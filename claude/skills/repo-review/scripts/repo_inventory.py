#!/usr/bin/env python3
"""repo_inventory.py — deterministic, read-only repo & agentic-surface scanner for /repo-review.

Stdlib Python 3 only (uses tiktoken if installed, otherwise chars/4 heuristic).
Never executes target-repository code or modifies files.
Inventories top-level modules, build/task manifests, CI workflows + extracted
verification commands, hooks, agentic context/skills, context-budget & caution-
density metrics, broken relative Markdown links, symlinks, eval/test surfaces,
unreferenced eval/fixture dirs, recent non-trivial source files, and model signals.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".tox",
    ".eggs",
    "coverage",
    ".turbo",
    "vendor",
}

MANIFEST_NAMES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "CMakeLists.txt",
    "Makefile",
    "justfile",
    "Taskfile.yml",
    "Taskfile.yaml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
)

LOCKFILE_NAMES = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "bun.lock",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
    "composer.lock",
)

ENV_TEMPLATE_NAMES = (
    ".env.example",
    ".env.sample",
    ".env.template",
)

DOC_ANCHOR_NAMES = (
    "README.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "DEVELOPMENT.md",
)

ROOT_CONTEXT_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CURSOR.md",
    "INVARIANTS.md",
    ".cursorrules",
    ".clinerules",
    ".github/copilot-instructions.md",
)

SKILL_PARENT_DIRS = (
    ".agents/skills",
    ".claude/skills",
    "claude/skills",
    "skills",
    "harness/skills",
)

MCP_AND_HARNESS_CONFIGS = (
    ".mcp.json",
    "mcp.json",
    "mcp_config.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".gemini/config/hooks.json",
    ".gemini/config/skills.json",
    ".codex/config.toml",
    "claude/model-routing.yaml",
    "model-routing.yaml",
)

HOOK_PATHS = (
    ".githooks",
    ".pre-commit-config.yaml",
    ".husky",
)

SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h",
    ".cs", ".swift", ".sh", ".bash", ".zsh",
}

NON_TRIVIAL_SKIP_NAMES = {
    "CHANGELOG.md",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "go.sum",
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
_TOKENIZER_LABEL = "heuristic(chars/4)"
try:  # optional exact tokenizer when available
    import tiktoken  # type: ignore

    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    _TOKENIZER_LABEL = "tiktoken(cl100k_base)"
except Exception:
    _TIKTOKEN_ENC = None


def _count_tokens(text: str) -> int:
    if _TIKTOKEN_ENC is not None:
        return len(_TIKTOKEN_ENC.encode(text, disallowed_special=()))
    return (len(text) + 3) // 4


def _read_text_safe(path: Path) -> str | None:
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


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _run_git(root: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _strip_fenced_code(text: str) -> str:
    return FENCE_RE.sub("", text)


def _find_broken_md_links(file_path: Path, root: Path, text: str) -> list[dict[str, str]]:
    broken: list[dict[str, str]] = []
    cleaned = _strip_fenced_code(text)
    for match in MD_LINK_RE.finditer(cleaned):
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
            broken.append({"file": _rel(file_path, root), "target": raw_target})
    return broken


def _measure_surface(path: Path, root: Path, kind: str, auto_loaded: bool) -> dict[str, Any]:
    is_symlink = path.is_symlink()
    broken = is_symlink and not path.exists()
    size_bytes = 0
    lines = 0
    tokens = 0
    caution_caps = 0
    if not broken and path.is_file():
        try:
            raw = path.read_bytes()
            size_bytes = len(raw)
            text = raw.decode("utf-8", errors="replace")
            lines = len(text.splitlines())
            tokens = _count_tokens(text)
            caution_caps = len(CAUTION_CAPS_RE.findall(_strip_fenced_code(text)))
        except OSError:
            pass
    caution_per_100 = round((caution_caps * 100.0) / max(lines, 1), 1)
    return {
        "path": _rel(path, root),
        "kind": kind,
        "auto_loaded": auto_loaded,
        "is_symlink": is_symlink,
        "symlink_broken": broken,
        "bytes": size_bytes,
        "lines": lines,
        "est_tokens": tokens,
        "caution_caps_count": caution_caps,
        "caution_caps_per_100_lines": caution_per_100,
    }


def _walk_repo(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        base = Path(dirpath)
        for name in sorted(filenames):
            files.append(base / name)
            if len(files) >= max_files:
                truncated = True
                return files, truncated
    return files, truncated


def _summarize_top_level_modules(root: Path, all_files: list[Path]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    ext_counts: dict[str, Counter[str]] = {}
    for f in all_files:
        parts = Path(_rel(f, root)).parts
        top = parts[0] if len(parts) > 1 else "(root)"
        counts[top] += 1
        ext = f.suffix.lower() or "(no-ext)"
        ext_counts.setdefault(top, Counter())[ext] += 1
    result: list[dict[str, Any]] = []
    for top, count in counts.most_common():
        top_exts = [ext for ext, _ in ext_counts[top].most_common(4)]
        result.append({"module": top, "file_count": count, "top_extensions": top_exts})
    return result


def _collect_git_info(root: Path) -> dict[str, Any]:
    top = _run_git(root, ["rev-parse", "--show-toplevel"])
    if not top:
        return {
            "is_git": False,
            "branch": None,
            "head_short": None,
            "commits_30d": None,
            "commits_90d": None,
            "recent_source_files": [],
        }

    branch = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    head_short = _run_git(root, ["rev-parse", "--short", "HEAD"])
    c30 = _run_git(root, ["rev-list", "--count", "--since=30.days", "HEAD"])
    c90 = _run_git(root, ["rev-list", "--count", "--since=90.days", "HEAD"])

    recent_raw = _run_git(root, ["log", "-n", "40", "--name-only", "--pretty=format:"]) or ""
    seen: set[str] = set()
    recent_source: list[str] = []
    for line in recent_raw.splitlines():
        rel = line.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        p = root / rel
        if p.name in NON_TRIVIAL_SKIP_NAMES:
            continue
        if p.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        if p.is_file():
            recent_source.append(rel)
        if len(recent_source) >= 5:
            break

    return {
        "is_git": True,
        "branch": branch,
        "head_short": head_short,
        "commits_30d": int(c30) if c30 and c30.isdigit() else 0,
        "commits_90d": int(c90) if c90 and c90.isdigit() else 0,
        "recent_source_files": recent_source,
    }


def _extract_verify_commands(source_path: str, text: str) -> list[dict[str, str]]:
    cmds: list[dict[str, str]] = []
    seen: set[str] = set()
    # Join shell backslash continuations for cleaner command extraction
    joined = re.sub(r"\\\n[ \t]*", " ", text)
    for line in joined.splitlines():
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


def _collect_skill_entries(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    entries: list[dict[str, Any]] = []
    broken_symlinks: list[dict[str, str]] = []
    skill_warnings: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for rel_parent in SKILL_PARENT_DIRS:
        parent = root / rel_parent
        if not parent.exists() and not parent.is_symlink():
            continue
        if parent.is_symlink() and not parent.exists():
            broken_symlinks.append({"path": rel_parent, "reason": "broken skill directory symlink"})
            continue
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            rel_child = _rel(child, root)
            if child.is_symlink() and not child.exists():
                broken_symlinks.append({"path": rel_child, "reason": "broken skill symlink"})
                continue
            skill_md = child / "SKILL.md" if child.is_dir() else None
            if skill_md and (skill_md.exists() or skill_md.is_symlink()):
                rel_md = _rel(skill_md, root)
                if rel_md in seen_paths:
                    continue
                seen_paths.add(rel_md)
                rec = _measure_surface(skill_md, root, kind="skill", auto_loaded=False)
                rec["parent_symlink"] = child.is_symlink()
                entries.append(rec)
                if rec["symlink_broken"]:
                    broken_symlinks.append({"path": rel_md, "reason": "broken SKILL.md symlink"})
                    continue
                text = _read_text_safe(skill_md) or ""
                fm = FRONTMATTER_RE.match(text)
                if not fm or not re.search(r"(?m)^name:\s*\S+", fm.group(1)) or not re.search(r"(?m)^description:\s*\S+", fm.group(1)):
                    skill_warnings.append({
                        "code": "skill_missing_frontmatter",
                        "path": rel_md,
                        "detail": "SKILL.md missing required YAML frontmatter name/description",
                    })
                # Check reference files >100 lines for a TOC
                refs_dir = child / "references"
                if refs_dir.is_dir():
                    for ref_file in sorted(refs_dir.glob("*.md")):
                        ref_text = _read_text_safe(ref_file)
                        if ref_text is None:
                            continue
                        ref_lines = len(ref_text.splitlines())
                        if ref_lines > 100 and "## Contents" not in ref_text and "## Table of Contents" not in ref_text:
                            skill_warnings.append({
                                "code": "reference_missing_toc",
                                "path": _rel(ref_file, root),
                                "detail": f"{ref_lines} lines (>100) without a '## Contents' TOC",
                            })
    return entries, broken_symlinks, skill_warnings


def _find_unreferenced_fixture_dirs(
    root: Path,
    fixture_dirs: list[str],
    test_files: list[str],
    ci_workflows: list[dict[str, Any]],
) -> list[str]:
    if not fixture_dirs:
        return []
    searchable_texts: list[str] = []
    for tf in test_files:
        txt = _read_text_safe(root / tf)
        if txt:
            searchable_texts.append(txt)
    for wf in ci_workflows:
        txt = _read_text_safe(root / wf["path"])
        if txt:
            searchable_texts.append(txt)
    combined = "\n".join(searchable_texts)

    unreferenced: list[str] = []
    for fdir in fixture_dirs:
        p = root / fdir
        # Check child fixture subdirectories (e.g. claude/fixtures/routing-guard)
        subdirs = [c for c in sorted(p.iterdir()) if c.is_dir() and c.name not in SKIP_DIRS] if p.is_dir() else []
        targets = [_rel(c, root) for c in subdirs] if subdirs else [fdir]
        for target in targets:
            t_path = root / target
            # If the fixture subdir itself contains a test_*.py file, it IS tested
            has_local_test = any(
                (f.name.startswith("test_") and f.name.endswith(".py")) or f.name.endswith("_test.py")
                for f in t_path.rglob("*")
                if f.is_file()
            )
            if has_local_test:
                continue
            leaf = Path(target).name
            if target not in combined and leaf not in combined:
                unreferenced.append(target)
    return sorted(set(unreferenced))


def scan_repo(root: Path, max_files: int = 10_000) -> dict[str, Any]:
    root = root.resolve()
    all_files, truncated = _walk_repo(root, max_files=max_files)
    git_info = _collect_git_info(root)
    top_modules = _summarize_top_level_modules(root, all_files)

    manifests = [name for name in MANIFEST_NAMES if (root / name).is_file()]
    lockfiles = [name for name in LOCKFILE_NAMES if (root / name).is_file()]
    env_templates = [name for name in ENV_TEMPLATE_NAMES if (root / name).is_file()]
    doc_anchors = [name for name in DOC_ANCHOR_NAMES if (root / name).is_file()]
    if (root / "docs" / "adr").is_dir():
        doc_anchors.append("docs/adr/")
    if (root / "docs" / "README.md").is_file():
        doc_anchors.append("docs/README.md")

    # CI workflows, extracted verify commands, and hooks
    ci_workflows: list[dict[str, Any]] = []
    extracted_commands: list[dict[str, str]] = []
    gh_wf_dir = root / ".github" / "workflows"
    if gh_wf_dir.is_dir():
        for wf in sorted(gh_wf_dir.iterdir()):
            if wf.suffix.lower() in {".yml", ".yaml"} and wf.is_file():
                rel_wf = _rel(wf, root)
                text = _read_text_safe(wf) or ""
                ci_workflows.append({
                    "path": rel_wf,
                    "mentions_agentic_surfaces": bool(CI_AGENTIC_SIGNAL_RE.search(text)),
                })
                extracted_commands.extend(_extract_verify_commands(rel_wf, text))
    for alt_ci in (".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile"):
        p = root / alt_ci
        if p.is_file():
            text = _read_text_safe(p) or ""
            ci_workflows.append({
                "path": alt_ci,
                "mentions_agentic_surfaces": bool(CI_AGENTIC_SIGNAL_RE.search(text)),
            })
            extracted_commands.extend(_extract_verify_commands(alt_ci, text))

    for task_file in ("Makefile", "justfile", "package.json"):
        p = root / task_file
        if p.is_file():
            text = _read_text_safe(p) or ""
            extracted_commands.extend(_extract_verify_commands(task_file, text))

    hooks_found: list[str] = []
    for hp in HOOK_PATHS:
        p = root / hp
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() or child.is_symlink():
                    hooks_found.append(_rel(child, root))
        elif p.is_file():
            hooks_found.append(hp)

    # Agentic surfaces
    surfaces: list[dict[str, Any]] = []
    broken_links: list[dict[str, str]] = []
    broken_symlinks: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    model_signals: set[str] = set()

    for rel_ctx in ROOT_CONTEXT_FILES:
        p = root / rel_ctx
        if p.exists() or p.is_symlink():
            auto_loaded = rel_ctx != "INVARIANTS.md"
            kind = "invariants" if rel_ctx == "INVARIANTS.md" else "context_root"
            rec = _measure_surface(p, root, kind=kind, auto_loaded=auto_loaded)
            surfaces.append(rec)
            if rec["symlink_broken"]:
                broken_symlinks.append({"path": rel_ctx, "reason": "broken root context symlink"})
            text = _read_text_safe(p)
            if text is not None:
                broken_links.extend(_find_broken_md_links(p, root, text))
                for m in MODEL_SIGNAL_RE.finditer(text):
                    model_signals.add(m.group(1).lower())

    # Nested AGENTS.md / CLAUDE.md / GEMINI.md / rule files
    root_ctx_set = {str(root / rel_ctx) for rel_ctx in ROOT_CONTEXT_FILES}
    for f in all_files:
        if str(f) in root_ctx_set:
            continue
        rel_f = _rel(f, root)
        if f.name in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}:
            rec = _measure_surface(f, root, kind="context_nested", auto_loaded=False)
            surfaces.append(rec)
            text = _read_text_safe(f)
            if text is not None:
                broken_links.extend(_find_broken_md_links(f, root, text))
                for m in MODEL_SIGNAL_RE.finditer(text):
                    model_signals.add(m.group(1).lower())
            if rec["lines"] >= 50 and rec["caution_caps_per_100_lines"] >= 10.0:
                warnings.append({
                    "code": "high_caution_caps_density",
                    "path": rel_f,
                    "detail": (
                        f"{rec['caution_caps_count']} all-caps NEVER/ALWAYS/MUST markers "
                        f"({rec['caution_caps_per_100_lines']}/100 lines — over-scaffolding risk)"
                    ),
                })
        elif rel_f.startswith((".agents/rules/", ".cursor/rules/", ".claude/rules/")) and f.suffix in {".md", ".mdc"}:
            rec = _measure_surface(f, root, kind="rules", auto_loaded=False)
            surfaces.append(rec)

    skill_entries, skill_broken_symlinks, skill_warnings = _collect_skill_entries(root)
    surfaces.extend(skill_entries)
    broken_symlinks.extend(skill_broken_symlinks)
    warnings.extend(skill_warnings)
    for sk in skill_entries:
        p = root / sk["path"]
        text = _read_text_safe(p)
        if text is not None:
            broken_links.extend(_find_broken_md_links(p, root, text))
            for m in MODEL_SIGNAL_RE.finditer(text):
                model_signals.add(m.group(1).lower())
        if sk["lines"] > 500:
            warnings.append({
                "code": "skill_md_over_500_lines",
                "path": sk["path"],
                "detail": f"{sk['lines']} lines (exceeds 500-line ceiling; split reference material)",
            })

    mcp_and_configs: list[str] = []
    for rel_cfg in MCP_AND_HARNESS_CONFIGS:
        p = root / rel_cfg
        if p.is_file():
            mcp_and_configs.append(rel_cfg)
            text = _read_text_safe(p)
            if text is not None:
                for m in MODEL_SIGNAL_RE.finditer(text):
                    model_signals.add(m.group(1).lower())

    # Cross-platform root context checks
    agents_p = root / "AGENTS.md"
    claude_p = root / "CLAUDE.md"
    gemini_p = root / "GEMINI.md"

    if agents_p.exists() or agents_p.is_symlink():
        if agents_p.is_symlink():
            warnings.append({
                "code": "agents_md_is_symlink",
                "path": "AGENTS.md",
                "detail": "AGENTS.md is a symlink; keep AGENTS.md as the real portable SSOT file",
            })
        agents_text = _read_text_safe(agents_p) or ""
        agents_bytes = agents_p.stat().st_size if agents_p.is_file() else 0
        if agents_bytes > ANTIGRAVITY_FILE_CAP_BYTES:
            warnings.append({
                "code": "agents_md_over_24kb",
                "path": "AGENTS.md",
                "detail": f"{agents_bytes} bytes exceeds 24,000-byte per-file cap on Antigravity",
            })
        cleaned_agents = _strip_fenced_code(agents_text)
        if AT_IMPORT_RE.search(cleaned_agents):
            warnings.append({
                "code": "agents_md_at_import",
                "path": "AGENTS.md",
                "detail": "AGENTS.md uses @path import syntax, which Codex and some harnesses do not expand",
            })
        if gemini_p.exists() or gemini_p.is_symlink():
            warnings.append({
                "code": "agents_and_gemini_coexist",
                "path": "GEMINI.md",
                "detail": "Root GEMINI.md coexists with AGENTS.md (Antigravity loads both cumulatively)",
            })
        if claude_p.exists() or claude_p.is_symlink():
            if claude_p.is_symlink():
                warnings.append({
                    "code": "claude_md_is_symlink",
                    "path": "CLAUDE.md",
                    "detail": "CLAUDE.md is a symlink; prefer a regular file containing '@AGENTS.md'",
                })
            else:
                claude_text = _read_text_safe(claude_p) or ""
                first_nonempty = next((ln.strip() for ln in claude_text.splitlines() if ln.strip()), "")
                if first_nonempty != "@AGENTS.md":
                    warnings.append({
                        "code": "claude_md_not_thin_shim",
                        "path": "CLAUDE.md",
                        "detail": "CLAUDE.md coexists with AGENTS.md without starting with '@AGENTS.md' (duplication/drift risk)",
                    })

    # Context budget rollup
    auto_surfaces = [s for s in surfaces if s["auto_loaded"]]
    auto_bytes = sum(s["bytes"] for s in auto_surfaces)
    auto_lines = sum(s["lines"] for s in auto_surfaces)
    auto_tokens = sum(s["est_tokens"] for s in auto_surfaces)
    if auto_tokens > 8_000:
        warnings.append({
            "code": "auto_loaded_tokens_high",
            "path": ".",
            "detail": f"Auto-loaded context ~{auto_tokens} tokens ({_TOKENIZER_LABEL}) exceeds 8,000-token redline",
        })
    elif auto_tokens > 4_000:
        warnings.append({
            "code": "auto_loaded_tokens_warn",
            "path": ".",
            "detail": f"Auto-loaded context ~{auto_tokens} tokens ({_TOKENIZER_LABEL}) exceeds 4,000-token target",
        })

    for bs in broken_symlinks:
        warnings.append({
            "code": "broken_symlink",
            "path": bs["path"],
            "detail": bs["reason"],
        })

    # Tests, evals, fixtures
    test_files: list[str] = []
    eval_paths: list[str] = []
    fixture_dirs: set[str] = set()
    test_dirs: set[str] = set()

    for f in all_files:
        rel_f = _rel(f, root)
        parts = Path(rel_f).parts
        lower_parts = [pt.lower() for pt in parts]
        name = f.name

        if any(pt in {"tests", "test", "__tests__", "spec"} for pt in lower_parts[:-1]):
            for idx, pt in enumerate(lower_parts[:-1]):
                if pt in {"tests", "test", "__tests__", "spec"}:
                    test_dirs.add("/".join(parts[: idx + 1]))
                    break

        if (
            name.startswith("test_") and name.endswith(".py")
            or name.endswith("_test.py")
            or name.endswith(("_test.go", ".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.js"))
        ):
            test_files.append(rel_f)

        if (
            any(pt in {"evals", "eval", "benchmarks", "benchmark"} for pt in lower_parts[:-1])
            or name == "MISROUTES.md"
            or ("eval" in name.lower() and f.suffix in {".py", ".ts", ".js", ".yaml", ".yml", ".json"})
        ):
            eval_paths.append(rel_f)

        for idx, pt in enumerate(lower_parts[:-1]):
            if pt == "fixtures":
                fixture_dirs.add("/".join(parts[: idx + 1]))
                break

    unreferenced_fixtures = _find_unreferenced_fixture_dirs(
        root, sorted(fixture_dirs), test_files, ci_workflows
    )
    for uf in unreferenced_fixtures:
        warnings.append({
            "code": "unreferenced_fixture_dir",
            "path": uf,
            "detail": "Fixture directory has no local test file and is not referenced by any test or CI workflow",
        })

    return {
        "repo": {
            "root": str(root),
            "total_files_scanned": len(all_files),
            "scan_truncated": truncated,
            "top_level_modules": top_modules,
            **git_info,
        },
        "manifests": {
            "build_and_task": manifests,
            "lockfiles": lockfiles,
            "env_templates": env_templates,
            "doc_anchors": doc_anchors,
        },
        "ci_and_hooks": {
            "workflows": ci_workflows,
            "extracted_commands": extracted_commands[:25],
            "hooks": hooks_found,
        },
        "agentic_surfaces": {
            "files": surfaces,
            "mcp_and_configs": mcp_and_configs,
            "context_budget": {
                "tokenizer": _TOKENIZER_LABEL,
                "auto_loaded_files": len(auto_surfaces),
                "auto_loaded_bytes": auto_bytes,
                "auto_loaded_lines": auto_lines,
                "auto_loaded_est_tokens": auto_tokens,
            },
            "warnings": warnings,
        },
        "rot": {
            "broken_markdown_links": broken_links,
            "broken_symlinks": broken_symlinks,
        },
        "evals_and_tests": {
            "test_dirs": sorted(test_dirs),
            "test_file_count": len(test_files),
            "sample_test_files": test_files[:15],
            "eval_file_count": len(eval_paths),
            "eval_paths": eval_paths[:30],
            "fixture_dirs": sorted(fixture_dirs),
            "unreferenced_fixture_dirs": unreferenced_fixtures,
        },
        "model_references": sorted(model_signals),
    }


def render_markdown(data: dict[str, Any]) -> str:
    r = data["repo"]
    cb = data["agentic_surfaces"]["context_budget"]
    et = data["evals_and_tests"]
    rot = data["rot"]
    top_mod_str = ", ".join(
        f"{m['module']} ({m['file_count']})" for m in r["top_level_modules"][:10]
    )
    lines = [
        f"# Repo Inventory: `{Path(r['root']).name}`",
        "",
        f"- **Git**: `{r['branch']}` @ `{r['head_short']}` (commits 30d/90d: {r['commits_30d']}/{r['commits_90d']})"
        if r["is_git"]
        else "- **Git**: not a git repository",
        f"- **Files scanned**: {r['total_files_scanned']}" + (" (truncated)" if r["scan_truncated"] else ""),
        f"- **Top-level modules**: {top_mod_str}",
        f"- **Manifests**: {', '.join(data['manifests']['build_and_task']) or 'none'}",
        f"- **Lockfiles**: {', '.join(data['manifests']['lockfiles']) or 'none'}",
        f"- **CI workflows**: {len(data['ci_and_hooks']['workflows'])} · **Hooks**: {len(data['ci_and_hooks']['hooks'])}",
        f"- **Tests**: {et['test_file_count']} files · **Evals**: {et['eval_file_count']} files",
        f"- **Auto-loaded context**: {cb['auto_loaded_files']} files, {cb['auto_loaded_lines']} lines, "
        f"{cb['auto_loaded_bytes']} bytes (~{cb['auto_loaded_est_tokens']} tokens via `{cb['tokenizer']}`)",
        f"- **Rot**: {len(rot['broken_markdown_links'])} broken relative links, {len(rot['broken_symlinks'])} broken symlinks",
        f"- **Model/harness signals**: {', '.join(data['model_references']) or 'none'}",
        "",
    ]
    if data["ci_and_hooks"]["extracted_commands"]:
        lines.append("## Extracted verification commands")
        for c in data["ci_and_hooks"]["extracted_commands"][:12]:
            lines.append(f"- `{c['command']}` *(from `{c['source']}`)*")
        lines.append("")
    if data["agentic_surfaces"]["warnings"]:
        lines.append("## Warnings")
        for w in data["agentic_surfaces"]["warnings"]:
            lines.append(f"- `{w['code']}` (`{w['path']}`): {w['detail']}")
        lines.append("")
    if rot["broken_markdown_links"]:
        lines.append("## Broken relative Markdown links")
        for bl in rot["broken_markdown_links"][:25]:
            lines.append(f"- `{bl['file']}` -> `{bl['target']}`")
        lines.append("")
    if r["recent_source_files"]:
        lines.append("## Recent non-trivial source files (dry-run candidates)")
        for sf in r["recent_source_files"]:
            lines.append(f"- `{sf}`")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic read-only repository & agentic-surface inventory."
    )
    parser.add_argument("repo", nargs="?", default=".", help="Path to target repository (default: .)")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format")
    parser.add_argument("--max-files", type=int, default=10_000, help="Maximum files to walk")
    args = parser.parse_args(argv)

    target = Path(args.repo)
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}", file=sys.stderr)
        return 2

    data = scan_repo(target, max_files=args.max_files)
    if args.format == "markdown":
        print(render_markdown(data))
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
