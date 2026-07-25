#!/usr/bin/env python3
"""Check Python function/method lengths against configured thresholds.

Reads thresholds from pyproject.toml [tool.claude-quality] section.
Respects .function-length-exceptions baseline file.

Exit codes:
  0 - no blocking violations
  1 - blocking violations found
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "node_modules", "dist", "build",
    ".tox", "site-packages", "opensrc",
})

DEFAULT_CONFIG = {
    "warning": 50,
    "limit": 100,
    "exclude": [],
}


def load_config(project_path: Path) -> dict:
    pyproject = project_path / "pyproject.toml"
    config = dict(DEFAULT_CONFIG)

    if not pyproject.exists() or tomllib is None:
        return config

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return config

    q = data.get("tool", {}).get("claude-quality", {})
    fl = q.get("function-length", {})
    config["warning"] = fl.get("warning", config["warning"])
    config["limit"] = fl.get("limit", config["limit"])
    config["exclude"] = q.get("exclude", config["exclude"])
    return config


def exception_key(rel_path: str, lineno: int, name: str) -> str:
    return f"{rel_path}:{lineno}:{name}"


def load_exceptions(project_path: Path) -> set[str]:
    exc_file = project_path / ".function-length-exceptions"
    if not exc_file.exists():
        return set()
    try:
        with open(exc_file) as f:
            data = json.load(f)
        return set(data.get("exceptions", {}).keys())
    except Exception:
        return set()


def is_excluded(rel_path: Path, exclude: list[str]) -> bool:
    """True if rel_path matches a configured exclude pattern.

    Each pattern matches as: an exact path component (dir name), a glob
    against the posix relative path, or a directory-prefix of that path.
    """
    parts = rel_path.parts
    posix = rel_path.as_posix()
    for pat in exclude:
        pat = pat.rstrip("/")
        if pat in parts:
            return True
        if fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(posix, f"{pat}/*"):
            return True
    return False


def iter_python_files(project_path: Path, exclude: list[str] | None = None):
    exclude = exclude or []
    for py_file in sorted(project_path.rglob("*.py")):
        rel = py_file.relative_to(project_path)
        if any(
            part in SKIP_DIRS or (part.startswith(".") and part != ".")
            for part in rel.parts
        ):
            continue
        if is_excluded(rel, exclude):
            continue
        yield py_file


def get_functions(filepath: Path) -> list[tuple[int, str, int]]:
    """Return list of (lineno, name, length) for all functions in file."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []
    except Exception:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if hasattr(node, "end_lineno"):
                length = node.end_lineno - node.lineno + 1
                results.append((node.lineno, node.name, length))
    return results


def find_violations(
    project_path: Path, config: dict, exceptions: set[str]
) -> tuple[list[dict], list[dict]]:
    blocking: list[dict] = []
    warnings: list[dict] = []

    for py_file in iter_python_files(project_path, config.get("exclude")):
        rel = str(py_file.relative_to(project_path))
        for lineno, name, length in get_functions(py_file):
            key = exception_key(rel, lineno, name)
            if key in exceptions:
                continue

            entry = {"file": rel, "lineno": lineno, "name": name, "lines": length}
            if length > config["limit"]:
                blocking.append(entry)
            elif length >= config["warning"]:
                warnings.append(entry)

    blocking.sort(key=lambda x: -x["lines"])
    warnings.sort(key=lambda x: -x["lines"])
    return blocking, warnings


def generate_baseline(project_path: Path, config: dict) -> None:
    blocking, _ = find_violations(project_path, config, set())
    exc_file = project_path / ".function-length-exceptions"

    # Load existing to preserve any manual entries not in current scan
    existing: dict = {}
    if exc_file.exists():
        try:
            with open(exc_file) as f:
                existing = json.load(f).get("exceptions", {})
        except Exception:
            pass

    new_exceptions = {
        exception_key(v["file"], v["lineno"], v["name"]): {
            "file": v["file"],
            "lineno": v["lineno"],
            "name": v["name"],
            "lines": v["lines"],
        }
        for v in blocking
    }

    # Only keep exceptions that still exist in the codebase
    data = {"exceptions": new_exceptions}
    with open(exc_file, "w") as f:
        json.dump(data, f, indent=2)

    removed = len(existing) - len(new_exceptions)
    print(
        f"Generated baseline: {len(new_exceptions)} exceptions -> {exc_file}"
        + (f" ({removed} stale entries removed)" if removed > 0 else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Python function lengths")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--generate-baseline", action="store_true",
                        help="Write current violations as exceptions baseline")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    project_path = Path(args.project).resolve()
    config = load_config(project_path)

    if args.generate_baseline:
        generate_baseline(project_path, config)
        return

    exceptions = load_exceptions(project_path)
    blocking, warnings = find_violations(project_path, config, exceptions)

    if args.json:
        print(json.dumps({"violations": blocking, "warnings": warnings, "config": config}))
        sys.exit(1 if blocking else 0)

    # Human-readable output (skill parses "BLOCKING" and ">100 lines" keywords)
    if blocking:
        print(f"\n=== Function Length Violations ({len(blocking)} BLOCKING) ===")
        for v in blocking:
            print(
                f"  {v['file']}:{v['lineno']}  {v['name']}()  "
                f"{v['lines']} lines [LIMIT={config['limit']}] BLOCKING  >100 lines"
            )

    if warnings:
        print(f"\n=== Function Length Warnings ({len(warnings)} approaching limit) ===")
        for w in warnings:
            print(
                f"  {w['file']}:{w['lineno']}  {w['name']}()  "
                f"{w['lines']} lines [WARNING>={config['warning']}] WARNING"
            )

    if not blocking and not warnings:
        print("✓ All functions within length limits")

    print(
        f"\nSummary: {len(blocking)} BLOCKING violation(s), {len(warnings)} warning(s)"
        f"\nThresholds: limit={config['limit']} lines (warning={config['warning']})"
    )

    if blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
