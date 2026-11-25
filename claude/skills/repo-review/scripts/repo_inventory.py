#!/usr/bin/env python3
"""repo_inventory.py — deterministic, read-only repo & agentic-surface scanner for /repo-review.

Stdlib Python 3 only (uses tiktoken if installed, otherwise chars/4 heuristic).
Never executes target-repository code or modifies files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import inventory_checks as ic  # noqa: E402


def _collect_ci_and_hooks(root: Path) -> dict[str, Any]:
    ci_workflows: list[dict[str, Any]] = []
    extracted_commands: list[dict[str, str]] = []

    gh_wf_dir = root / ".github" / "workflows"
    if gh_wf_dir.is_dir():
        for wf in sorted(gh_wf_dir.iterdir()):
            if wf.suffix.lower() in {".yml", ".yaml"} and wf.is_file():
                rel_wf = ic.rel_path(wf, root)
                text = ic.read_text_safe(wf) or ""
                ci_workflows.append({
                    "path": rel_wf,
                    "mentions_agentic_surfaces": bool(ic.CI_AGENTIC_SIGNAL_RE.search(text)),
                })
                extracted_commands.extend(ic.extract_verify_commands(rel_wf, text))

    for alt_ci in (".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile"):
        p = root / alt_ci
        if p.is_file():
            text = ic.read_text_safe(p) or ""
            ci_workflows.append({
                "path": alt_ci,
                "mentions_agentic_surfaces": bool(ic.CI_AGENTIC_SIGNAL_RE.search(text)),
            })
            extracted_commands.extend(ic.extract_verify_commands(alt_ci, text))

    for task_file in ("Makefile", "justfile", "package.json"):
        p = root / task_file
        if p.is_file():
            extracted_commands.extend(ic.extract_verify_commands(task_file, ic.read_text_safe(p) or ""))

    hooks_found: list[str] = []
    for hp in ic.HOOK_PATHS:
        p = root / hp
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() or child.is_symlink():
                    hooks_found.append(ic.rel_path(child, root))
        elif p.is_file():
            hooks_found.append(hp)

    return {
        "workflows": ci_workflows,
        "extracted_commands": extracted_commands[:25],
        "hooks": hooks_found,
    }


def _check_root_context_hygiene(root: Path) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    agents_p, claude_p, gemini_p = root / "AGENTS.md", root / "CLAUDE.md", root / "GEMINI.md"
    if not (agents_p.exists() or agents_p.is_symlink()):
        return warnings

    if agents_p.is_symlink():
        warnings.append({
            "code": "agents_md_is_symlink", "path": "AGENTS.md",
            "detail": "AGENTS.md is a symlink; keep AGENTS.md as the real portable SSOT file",
        })
    agents_text = ic.read_text_safe(agents_p) or ""
    agents_bytes = agents_p.stat().st_size if agents_p.is_file() else 0
    if agents_bytes > ic.ANTIGRAVITY_FILE_CAP_BYTES:
        warnings.append({
            "code": "agents_md_over_24kb", "path": "AGENTS.md",
            "detail": f"{agents_bytes} bytes exceeds 24,000-byte per-file cap on Antigravity",
        })
    if ic.AT_IMPORT_RE.search(ic.strip_fenced_code(agents_text)):
        warnings.append({
            "code": "agents_md_at_import", "path": "AGENTS.md",
            "detail": "AGENTS.md uses @path import syntax, which Codex and some harnesses do not expand",
        })
    if gemini_p.exists() or gemini_p.is_symlink():
        warnings.append({
            "code": "agents_and_gemini_coexist", "path": "GEMINI.md",
            "detail": "Root GEMINI.md coexists with AGENTS.md (Antigravity loads both cumulatively)",
        })
    if claude_p.exists() or claude_p.is_symlink():
        if claude_p.is_symlink():
            warnings.append({
                "code": "claude_md_is_symlink", "path": "CLAUDE.md",
                "detail": "CLAUDE.md is a symlink; prefer a regular file containing '@AGENTS.md'",
            })
        else:
            claude_text = ic.read_text_safe(claude_p) or ""
            first_nonempty = next((ln.strip() for ln in claude_text.splitlines() if ln.strip()), "")
            if first_nonempty != "@AGENTS.md":
                warnings.append({
                    "code": "claude_md_not_thin_shim", "path": "CLAUDE.md",
                    "detail": "CLAUDE.md coexists with AGENTS.md without starting with '@AGENTS.md' (duplication/drift risk)",
                })
    return warnings


def _collect_agentic_surfaces(root: Path, all_files: list[Path]) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    surfaces: list[dict[str, Any]] = []
    broken_links: list[dict[str, str]] = []
    broken_symlinks: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    model_signals: set[str] = set()

    def _inspect_text(path: Path, text: str | None) -> None:
        if text is None:
            return
        broken_links.extend(ic.find_broken_md_links(path, root, text))
        for m in ic.MODEL_SIGNAL_RE.finditer(text):
            model_signals.add(m.group(1).lower())

    for rel_ctx in ic.ROOT_CONTEXT_FILES:
        p = root / rel_ctx
        if p.exists() or p.is_symlink():
            rec = ic.measure_surface(p, root, kind="invariants" if rel_ctx == "INVARIANTS.md" else "context_root",
                                     auto_loaded=(rel_ctx != "INVARIANTS.md"))
            surfaces.append(rec)
            if rec["symlink_broken"]:
                broken_symlinks.append({"path": rel_ctx, "reason": "broken root context symlink"})
            _inspect_text(p, ic.read_text_safe(p))

    root_ctx_set = {str(root / rel_ctx) for rel_ctx in ic.ROOT_CONTEXT_FILES}
    for f in all_files:
        if str(f) in root_ctx_set:
            continue
        rel_f = ic.rel_path(f, root)
        if f.name in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}:
            rec = ic.measure_surface(f, root, kind="context_nested", auto_loaded=False)
            surfaces.append(rec)
            _inspect_text(f, ic.read_text_safe(f))
            if rec["lines"] >= 50 and rec["caution_caps_per_100_lines"] >= 10.0:
                warnings.append({
                    "code": "high_caution_caps_density", "path": rel_f,
                    "detail": f"{rec['caution_caps_count']} all-caps NEVER/ALWAYS/MUST markers ({rec['caution_caps_per_100_lines']}/100 lines — over-scaffolding risk)",
                })
        elif rel_f.startswith((".agents/rules/", ".cursor/rules/", ".claude/rules/")) and f.suffix in {".md", ".mdc"}:
            surfaces.append(ic.measure_surface(f, root, kind="rules", auto_loaded=False))

    skill_entries, skill_broken, skill_warnings = ic.collect_skill_entries(root)
    surfaces.extend(skill_entries)
    broken_symlinks.extend(skill_broken)
    warnings.extend(skill_warnings)
    for sk in skill_entries:
        _inspect_text(root / sk["path"], ic.read_text_safe(root / sk["path"]))
        if sk["lines"] > 500:
            warnings.append({
                "code": "skill_md_over_500_lines", "path": sk["path"],
                "detail": f"{sk['lines']} lines (exceeds 500-line ceiling; split reference material)",
            })

    mcp_and_configs: list[str] = []
    for rel_cfg in ic.MCP_AND_HARNESS_CONFIGS:
        p = root / rel_cfg
        if p.is_file():
            mcp_and_configs.append(rel_cfg)
            text = ic.read_text_safe(p)
            if text is not None:
                for m in ic.MODEL_SIGNAL_RE.finditer(text):
                    model_signals.add(m.group(1).lower())

    warnings.extend(_check_root_context_hygiene(root))
    auto_surfaces = [s for s in surfaces if s["auto_loaded"]]
    auto_tokens = sum(s["est_tokens"] for s in auto_surfaces)
    if auto_tokens > 8_000:
        warnings.append({"code": "auto_loaded_tokens_high", "path": ".", "detail": f"Auto-loaded context ~{auto_tokens} tokens ({ic.TOKENIZER_LABEL}) exceeds 8,000-token redline"})
    elif auto_tokens > 4_000:
        warnings.append({"code": "auto_loaded_tokens_warn", "path": ".", "detail": f"Auto-loaded context ~{auto_tokens} tokens ({ic.TOKENIZER_LABEL}) exceeds 4,000-token target"})
    for bs in broken_symlinks:
        warnings.append({"code": "broken_symlink", "path": bs["path"], "detail": bs["reason"]})

    agentic = {
        "files": surfaces,
        "mcp_and_configs": mcp_and_configs,
        "context_budget": {
            "tokenizer": ic.TOKENIZER_LABEL,
            "auto_loaded_files": len(auto_surfaces),
            "auto_loaded_bytes": sum(s["bytes"] for s in auto_surfaces),
            "auto_loaded_lines": sum(s["lines"] for s in auto_surfaces),
            "auto_loaded_est_tokens": auto_tokens,
        },
        "warnings": warnings,
    }
    rot = {"broken_markdown_links": broken_links, "broken_symlinks": broken_symlinks}
    return agentic, rot, model_signals


def _collect_evals_and_tests(root: Path, all_files: list[Path], ci_workflows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    test_files, eval_paths = [], []
    fixture_dirs, test_dirs = set(), set()

    for f in all_files:
        rel_f = ic.rel_path(f, root)
        parts = Path(rel_f).parts
        lower_parts = [pt.lower() for pt in parts]
        name = f.name

        for idx, pt in enumerate(lower_parts[:-1]):
            if pt in {"tests", "test", "__tests__", "spec"}:
                test_dirs.add("/".join(parts[: idx + 1]))
                break
        if (
            (name.startswith("test_") and name.endswith(".py"))
            or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.js"))
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

    unreferenced = ic.find_unreferenced_fixture_dirs(root, sorted(fixture_dirs), test_files, ci_workflows)
    warnings = [
        {
            "code": "unreferenced_fixture_dir", "path": uf,
            "detail": "Fixture directory has no local test file and is not referenced by any test or CI workflow",
        }
        for uf in unreferenced
    ]
    return {
        "test_dirs": sorted(test_dirs),
        "test_file_count": len(test_files),
        "sample_test_files": test_files[:15],
        "eval_file_count": len(eval_paths),
        "eval_paths": eval_paths[:30],
        "fixture_dirs": sorted(fixture_dirs),
        "unreferenced_fixture_dirs": unreferenced,
    }, warnings


def scan_repo(root: Path, max_files: int = 10_000) -> dict[str, Any]:
    root = root.resolve()
    all_files, truncated = ic.walk_repo(root, max_files=max_files)
    ci_and_hooks = _collect_ci_and_hooks(root)
    agentic, rot, model_signals = _collect_agentic_surfaces(root, all_files)
    evals_and_tests, eval_warnings = _collect_evals_and_tests(root, all_files, ci_and_hooks["workflows"])
    agentic["warnings"].extend(eval_warnings)

    doc_anchors = [name for name in ic.DOC_ANCHOR_NAMES if (root / name).is_file()]
    if (root / "docs" / "adr").is_dir():
        doc_anchors.append("docs/adr/")
    if (root / "docs" / "README.md").is_file():
        doc_anchors.append("docs/README.md")

    return {
        "repo": {
            "root": str(root),
            "total_files_scanned": len(all_files),
            "scan_truncated": truncated,
            "top_level_modules": ic.summarize_top_level_modules(root, all_files),
            **ic.collect_git_info(root),
        },
        "manifests": {
            "build_and_task": [n for n in ic.MANIFEST_NAMES if (root / n).is_file()],
            "lockfiles": [n for n in ic.LOCKFILE_NAMES if (root / n).is_file()],
            "env_templates": [n for n in ic.ENV_TEMPLATE_NAMES if (root / n).is_file()],
            "doc_anchors": doc_anchors,
        },
        "ci_and_hooks": ci_and_hooks,
        "agentic_surfaces": agentic,
        "rot": rot,
        "evals_and_tests": evals_and_tests,
        "model_references": sorted(model_signals),
    }


def render_markdown(data: dict[str, Any]) -> str:
    r, cb, et, rot = data["repo"], data["agentic_surfaces"]["context_budget"], data["evals_and_tests"], data["rot"]
    top_mod_str = ", ".join(f"{m['module']} ({m['file_count']})" for m in r["top_level_modules"][:10])
    lines = [
        f"# Repo Inventory: `{Path(r['root']).name}`", "",
        f"- **Git**: `{r['branch']}` @ `{r['head_short']}` (commits 30d/90d: {r['commits_30d']}/{r['commits_90d']})"
        if r["is_git"] else "- **Git**: not a git repository",
        f"- **Files scanned**: {r['total_files_scanned']}" + (" (truncated)" if r["scan_truncated"] else ""),
        f"- **Top-level modules**: {top_mod_str}",
        f"- **Manifests**: {', '.join(data['manifests']['build_and_task']) or 'none'}",
        f"- **Lockfiles**: {', '.join(data['manifests']['lockfiles']) or 'none'}",
        f"- **CI workflows**: {len(data['ci_and_hooks']['workflows'])} · **Hooks**: {len(data['ci_and_hooks']['hooks'])}",
        f"- **Tests**: {et['test_file_count']} files · **Evals**: {et['eval_file_count']} files",
        f"- **Auto-loaded context**: {cb['auto_loaded_files']} files, {cb['auto_loaded_lines']} lines, "
        f"{cb['auto_loaded_bytes']} bytes (~{cb['auto_loaded_est_tokens']} tokens via `{cb['tokenizer']}`)",
        f"- **Rot**: {len(rot['broken_markdown_links'])} broken relative links, {len(rot['broken_symlinks'])} broken symlinks",
        f"- **Model/harness signals**: {', '.join(data['model_references']) or 'none'}", "",
    ]
    if data["ci_and_hooks"]["extracted_commands"]:
        lines.append("## Extracted verification commands")
        lines.extend(f"- `{c['command']}` *(from `{c['source']}`)*" for c in data["ci_and_hooks"]["extracted_commands"][:12])
        lines.append("")
    if data["agentic_surfaces"]["warnings"]:
        lines.append("## Warnings")
        lines.extend(f"- `{w['code']}` (`{w['path']}`): {w['detail']}" for w in data["agentic_surfaces"]["warnings"])
        lines.append("")
    if rot["broken_markdown_links"]:
        lines.append("## Broken relative Markdown links")
        lines.extend(f"- `{bl['file']}` -> `{bl['target']}`" for bl in rot["broken_markdown_links"][:25])
        lines.append("")
    if r["recent_source_files"]:
        lines.append("## Recent non-trivial source files (dry-run candidates)")
        lines.extend(f"- `{sf}`" for sf in r["recent_source_files"])
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic read-only repository & agentic-surface inventory.")
    parser.add_argument("repo", nargs="?", default=".", help="Path to target repository (default: .)")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format")
    parser.add_argument("--max-files", type=int, default=10_000, help="Maximum files to walk")
    args = parser.parse_args(argv)

    target = Path(args.repo)
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}", file=sys.stderr)
        return 2
    data = scan_repo(target, max_files=args.max_files)
    print(render_markdown(data) if args.format == "markdown" else json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
