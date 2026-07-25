#!/usr/bin/env python3
"""Check Python file sizes against configured thresholds.

Reads thresholds from pyproject.toml [tool.claude-quality] section.
Respects .file-size-exceptions baseline file.

Exit codes:
  0 - no blocking violations
  1 - blocking violations found
"""

from __future__ import annotations

import argparse
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

# Dirs to always skip when scanning
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "node_modules", "dist", "build",
    ".tox", "site-packages", "opensrc",
})

DEFAULT_CONFIG = {
    "warning": 400,
    "limit": 500,
    "test_limit": 800,
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
    fs = q.get("file-size", {})
    tfs = q.get("test-file-size", {})

    config["warning"] = fs.get("warning", config["warning"])
    config["limit"] = fs.get("limit", config["limit"])
    config["test_limit"] = tfs.get("limit", config["test_limit"])
    config["exclude"] = q.get("exclude", config["exclude"])
    return config


def load_exceptions(project_path: Path) -> set[str]:
    exc_file = project_path / ".file-size-exceptions"
    if not exc_file.exists():
        return set()
    try:
        with open(exc_file) as f:
            data = json.load(f)
        return {e["file"] for e in data.get("exceptions", [])}
    except Exception:
        return set()


def count_lines(filepath: Path) -> int:
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def is_test_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return (
        any(p in ("tests", "test") for p in parts)
        or Path(rel_path).name.startswith("test_")
        or Path(rel_path).name.endswith("_test.py")
    )


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
        # Skip if any path component is in SKIP_DIRS or starts with "."
        if any(
            part in SKIP_DIRS or (part.startswith(".") and part != ".")
            for part in rel.parts
        ):
            continue
        if is_excluded(rel, exclude):
            continue
        yield py_file


def find_violations(
    project_path: Path, config: dict, exceptions: set[str]
) -> tuple[list[dict], list[dict]]:
    blocking: list[dict] = []
    warnings: list[dict] = []

    for py_file in iter_python_files(project_path, config.get("exclude")):
        rel = str(py_file.relative_to(project_path))
        if rel in exceptions:
            continue

        loc = count_lines(py_file)
        if is_test_file(rel):
            limit = config["test_limit"]
            if loc > limit:
                blocking.append({"file": rel, "loc": loc, "limit": limit, "is_test": True})
        else:
            limit = config["limit"]
            if loc > limit:
                blocking.append({"file": rel, "loc": loc, "limit": limit, "is_test": False})
            elif loc >= config["warning"]:
                warnings.append({
                    "file": rel, "loc": loc,
                    "limit": limit, "warning": config["warning"], "is_test": False,
                })

    blocking.sort(key=lambda x: -x["loc"])
    warnings.sort(key=lambda x: -x["loc"])
    return blocking, warnings


def generate_baseline(project_path: Path, config: dict) -> None:
    blocking, _ = find_violations(project_path, config, set())
    exc_file = project_path / ".file-size-exceptions"
    data = {"exceptions": [{"file": v["file"], "loc": v["loc"]} for v in blocking]}
    with open(exc_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Generated baseline: {len(blocking)} exceptions -> {exc_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Python file sizes")
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

    # Human-readable output (skill parses "BLOCKING" keyword)
    if blocking:
        print(f"\n=== File Size Violations ({len(blocking)} BLOCKING) ===")
        for v in blocking:
            label = "(test)" if v["is_test"] else ""
            print(f"  {v['file']}: {v['loc']} LOC [LIMIT={v['limit']}] BLOCKING {label}")

    if warnings:
        print(f"\n=== File Size Warnings ({len(warnings)} approaching limit) ===")
        for w in warnings:
            print(
                f"  {w['file']}: {w['loc']} LOC "
                f"[WARNING>={w['warning']}, LIMIT={w['limit']}] WARNING"
            )

    if not blocking and not warnings:
        print("✓ All files within size limits")

    print(
        f"\nSummary: {len(blocking)} BLOCKING violation(s), {len(warnings)} warning(s)"
        f"\nThresholds: production limit={config['limit']} LOC "
        f"(warning={config['warning']}), test limit={config['test_limit']} LOC"
    )

    if blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
