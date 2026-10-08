#!/usr/bin/env python3
"""Check Python file sizes against configured thresholds.

Reads thresholds from pyproject.toml [tool.claude-quality] section.
Respects .file-size-exceptions baseline file.

Source of truth: gearbox scripts/quality/ (deployed to ~/.claude/scripts/quality/).
Adopting repos vendor this file into tools/ via vendor_quality.py — never edit a
vendored copy; re-sync instead.

Modes:
  default            whole-project scan (CI, adoption snapshots)
  --staged           judge only the files staged in git (pre-commit hooks).
                     A file blocks only when this commit makes it worse: over
                     its bound AND larger than at every commit parent. Debt a
                     commit merely inherits (a merge, a bypassed past commit)
                     warns and tells you to re-record the baseline; the CI
                     whole-project run stays red until that happens.

Exit codes:
  0 - no blocking violations
  1 - blocking violations found
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path

EXC_FILE = ".file-size-exceptions"

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


def load_exceptions(project_path: Path) -> dict[str, int]:
    """Map each grandfathered file to the LOC it was baselined at.

    ponytail: keyed on file AND magnitude, not the file name alone. Name-only
    matching let a baselined file grow without limit -- cos.py could go from
    its recorded 6,684 LOC to 20,000 and never block, which is not a ratchet.
    """
    exc_file = project_path / EXC_FILE
    if not exc_file.exists():
        return {}
    return parse_exceptions(exc_file.read_text())


def parse_exceptions(source: str) -> dict[str, int]:
    """The baseline text -> {file: loc}. Shared with the parent-version read."""
    try:
        return {e["file"]: e["loc"] for e in json.loads(source).get("exceptions", [])}
    except Exception:
        return {}


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
    # ponytail: prune during the walk, never rglob-then-filter. rglob("*.py")
    # descends into .git/.venv/node_modules in full before anything is
    # discarded -- measured: 69,255 files in 4.02s, against 274 files in
    # 0.02s pruned. Same violation set either way.
    exclude = exclude or []
    for dirpath, dirnames, filenames in os.walk(project_path):
        rel_dir = Path(dirpath).relative_to(project_path)
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith(".")
            and not is_excluded(rel_dir / d, exclude)
        ]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            rel = rel_dir / name
            if is_excluded(rel, exclude):
                continue
            yield project_path / rel


def find_violations(
    project_path: Path, config: dict, exceptions: dict[str, int]
) -> tuple[list[dict], list[dict]]:
    blocking: list[dict] = []
    warnings: list[dict] = []

    for py_file in iter_python_files(project_path, config.get("exclude")):
        rel = str(py_file.relative_to(project_path))
        loc = count_lines(py_file)

        if rel in exceptions:
            # Grandfathered at a recorded size: shrinking is always fine,
            # growing past what was baselined is not.
            if loc > exceptions[rel]:
                blocking.append({
                    "file": rel, "loc": loc, "limit": exceptions[rel],
                    "is_test": is_test_file(rel), "baselined": True,
                })
            continue

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



def judge_raised_bounds(
    project_path: Path, config: dict, exceptions: dict[str, int], raised: dict,
) -> tuple[list[str], list[str]]:
    """Split this commit's raised bounds into (blocking, warning) lines.

    Judged whether or not the .py file is staged: a "re-record the baselines"
    commit stages the baseline and no source at all, and that is the commit
    shape that historically moved a baseline the most.
    """
    import ratchetlib

    bad: list[str] = []
    ok: list[str] = []
    for rel, was in sorted(raised.items()):
        if is_excluded(Path(rel), config.get("exclude") or []):
            continue
        bound = exceptions[rel]
        had = f"was {was}" if was is not None else "not baselined before"
        line = f"  {rel}: baseline raised to {bound} ({had})"
        parents = [
            len(src.splitlines())
            for src in ratchetlib.parent_sources(project_path, rel)
        ]
        # A parent that already carried the file at this size is the evidence
        # that the commit RECORDS existing debt. Without one, the commit both
        # grew the file and wrote the bar it is judged against.
        if parents and max(parents) >= bound:
            ok.append(f"{line} — a parent had {max(parents)}")
        elif parents:
            bad.append(f"{line} — largest parent was {max(parents)}")
        else:
            bad.append(f"{line} — no parent has this file")
    return bad, ok


def check_staged(project_path: Path, config: dict, exceptions: dict[str, int]) -> int:
    """Judge only staged files; block only what THIS commit makes worse."""
    import ratchetlib

    blocking: list[str] = []
    inherited: list[str] = []
    self_recorded: list[str] = []
    # Bounds this very commit introduces or raises. Judging a file against a
    # number the same commit wrote is how debt gets created and blessed in one
    # move, so those files are never silently skipped below.
    raised = ratchetlib.self_recorded_bounds(
        project_path, EXC_FILE, parse_exceptions, exceptions,
    )
    for rel in ratchetlib.staged_py_files(project_path):
        if is_excluded(Path(rel), config.get("exclude") or []):
            continue
        loc = count_lines(project_path / rel)
        if rel in exceptions:
            bound, label = exceptions[rel], "baseline"
        elif is_test_file(rel):
            bound, label = config["test_limit"], "LIMIT"
        else:
            bound, label = config["limit"], "LIMIT"
        parents = [
            len(src.splitlines())
            for src in ratchetlib.parent_sources(project_path, rel)
        ]
        if loc <= bound:
            continue
        line = f"  {rel}: {loc} LOC [{label}={bound}]"
        if parents and loc <= max(parents):
            inherited.append(line)
        else:
            blocking.append(line)

    raised_bad, raised_ok = judge_raised_bounds(
        project_path, config, exceptions, raised,
    )
    blocking.extend(raised_bad)
    self_recorded.extend(raised_ok)

    if blocking:
        print(f"\n=== File Size Violations ({len(blocking)} BLOCKING, staged) ===")
        print("\n".join(blocking))
        print(
            "\nThe bound above is a ratchet: it records the largest size this"
            f" file was\nallowed to reach, so {EXC_FILE} never lets a file"
            " grow past what it already\nrecorded. Raising the number in"
            f" {EXC_FILE} is not the fix.\n\nThe fix: extract a cohesive"
            " group of functions into a new module and import\nit back here."
            "\n\nRe-run: python3 scripts/quality/check_file_sizes.py --staged"
        )
    if inherited:
        print(f"\n=== Inherited debt ({len(inherited)} WARNING, staged) ===")
        print("\n".join(inherited))
        print(
            "\nOver the bound, but not made worse by this commit (merge or"
            " pre-existing).\nRe-record the baseline in this commit"
            " (--generate-baseline); CI stays red until you do."
        )
    if self_recorded:
        print(
            f"\n=== Baseline raised by this commit ({len(self_recorded)}"
            " WARNING, staged) ==="
        )
        print("\n".join(self_recorded))
        print(
            "\nA parent already carried each of these at this size, so the"
            " commit is\nrecording existing debt, not authoring it. Reported"
            " rather than blocked —\nbut it is never a silent pass: read the"
            " diff and confirm that is what it is."
        )
    if not blocking and not inherited and not self_recorded:
        print("✓ Staged files within size limits")
    return 1 if blocking else 0


def generate_baseline(project_path: Path, config: dict) -> None:
    blocking, _ = find_violations(project_path, config, {})
    exc_file = project_path / EXC_FILE

    existing: set[str] = set()
    if exc_file.exists():
        try:
            with open(exc_file) as f:
                existing = {e["file"] for e in json.load(f).get("exceptions", [])}
        except Exception:
            pass

    data = {"exceptions": [{"file": v["file"], "loc": v["loc"]} for v in blocking]}
    with open(exc_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    removed = len(existing - {v["file"] for v in blocking})
    print(
        f"Generated baseline: {len(blocking)} exceptions -> {exc_file}"
        + (f" ({removed} stale entries removed)" if removed > 0 else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Python file sizes")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--staged", action="store_true",
                        help="Judge only git-staged files (pre-commit mode)")
    parser.add_argument("--generate-baseline", action="store_true",
                        help="Write current violations as exceptions baseline. NOT safe in a tree other sessions are editing — it baselines whatever is dirty right now; hand-edit instead (see ratchetlib.py)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    project_path = Path(args.project).resolve()
    config = load_config(project_path)

    if args.generate_baseline:
        generate_baseline(project_path, config)
        return

    exceptions = load_exceptions(project_path)

    if args.staged:
        sys.exit(check_staged(project_path, config, exceptions))

    blocking, warnings = find_violations(project_path, config, exceptions)

    if args.json:
        print(json.dumps({"violations": blocking, "warnings": warnings, "config": config}))
        sys.exit(1 if blocking else 0)

    # Human-readable output (skill parses "BLOCKING" keyword)
    if blocking:
        print(f"\n=== File Size Violations ({len(blocking)} BLOCKING) ===")
        for v in blocking:
            label = "(test)" if v["is_test"] else ""
            bound = "baseline" if v.get("baselined") else "LIMIT"
            print(f"  {v['file']}: {v['loc']} LOC [{bound}={v['limit']}] BLOCKING {label}")

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
