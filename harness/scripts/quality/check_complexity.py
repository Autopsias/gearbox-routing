#!/usr/bin/env python3
"""Check cyclomatic complexity against a ratcheting baseline.

Runs ruff's C901 rule and compares each function against
``.complexity-exceptions``. A function fails when it is absent from the
baseline, or when it scored WORSE than the baseline records. Improving a
function never fails; the baseline only ever shrinks.

Companion to check_file_sizes.py / check_function_lengths.py, which own the
same ratchet for file size and function length. Shares their exclude list
(pyproject.toml ``[tool.claude-quality] exclude``) and their exit codes.

Source of truth: gearbox scripts/quality/ (deployed to ~/.claude/scripts/quality/).
Adopting repos vendor this file into tools/ via vendor_quality.py — never edit a
vendored copy; re-sync instead.

Modes:
  default            whole-project scan (CI, adoption snapshots)
  --staged           judge only the files staged in git (pre-commit hooks).
                     A function blocks only when this commit makes it worse:
                     over its bound AND more complex than at every commit
                     parent. Inherited debt warns and asks for a baseline
                     re-record; the CI whole-project run stays red until then.

Exit codes:
  0 - no blocking violations
  1 - blocking violations found
  2 - ruff missing or unusable (the check could not run)

Usage:
  python3 tools/check_complexity.py [--project DIR] [--staged] [--generate-baseline]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - py<3.11
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

MAX_COMPLEXITY = 12
BASELINE_NAME = ".complexity-exceptions"

# Dirs ruff should never walk, mirroring check_file_sizes.SKIP_DIRS plus the
# worktree tree (a sibling session's checkout, not this commit's source).
SKIP_DIRS = [
    ".venv", "venv", "node_modules", "dist", "build", ".tox", "opensrc",
    ".claude/worktrees",
]

# `some_function` is too complex (13 > 12)
_MESSAGE = re.compile(r"^`(?P<func>[^`]+)` is too complex \((?P<score>\d+) > \d+\)$")


def load_excludes(project: Path) -> list[str]:
    """The [tool.claude-quality] exclude list, so all three gates share a scope."""
    pyproject = project / "pyproject.toml"
    if not pyproject.exists() or tomllib is None:
        return list(SKIP_DIRS)
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return list(SKIP_DIRS)
    configured = data.get("tool", {}).get("claude-quality", {}).get("exclude", [])
    return list(SKIP_DIRS) + [str(item) for item in configured]


def run_ruff(cwd: Path, excludes: list[str], paths: list[str]) -> dict[str, int]:
    """Map ``relative/path.py::function`` -> complexity, for every C901 hit."""
    if shutil.which("ruff") is None:
        print("ERROR: ruff not found; cannot check complexity.", file=sys.stderr)
        raise SystemExit(2)

    result = subprocess.run(
        [
            "ruff", "check", "--select", "C901",
            "--config", f"lint.mccabe.max-complexity={MAX_COMPLEXITY}",
            "--exclude", ",".join(excludes),
            "--no-cache", "--output-format", "json", *paths,
        ],
        cwd=cwd, capture_output=True, text=True,
    )
    # ruff exits 1 when it finds violations, which is the normal path here.
    if result.returncode not in (0, 1) or not result.stdout.strip():
        print(f"ERROR: ruff failed ({result.returncode}): {result.stderr.strip()}",
              file=sys.stderr)
        raise SystemExit(2)

    found: dict[str, int] = {}
    for item in json.loads(result.stdout):
        match = _MESSAGE.match(item["message"])
        if match is None:
            # ponytail: an unparseable message means ruff changed its wording.
            # Fail loud rather than silently under-reporting.
            print(f"ERROR: unrecognised ruff message: {item['message']}", file=sys.stderr)
            raise SystemExit(2)
        rel = Path(item["filename"]).resolve().relative_to(cwd.resolve())
        found[f"{rel}::{match['func']}"] = int(match["score"])
    return found


def scan(project: Path) -> dict[str, int]:
    return run_ruff(project, load_excludes(project), ["."])


def load_baseline(project: Path) -> dict[str, int]:
    path = project / BASELINE_NAME
    if not path.exists():
        return {}
    with open(path) as fh:
        data = json.load(fh)
    return {
        f"{e['file']}::{e['function']}": e["complexity"]
        for e in data.get("exceptions", [])
    }


def write_baseline(project: Path, found: dict[str, int]) -> None:
    exceptions = [
        {"file": key.split("::", 1)[0], "function": key.split("::", 1)[1],
         "complexity": score}
        for key, score in sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    path = project / BASELINE_NAME
    with open(path, "w") as fh:
        json.dump({"exceptions": exceptions}, fh, indent=2)
        fh.write("\n")
    print(f"Wrote {len(exceptions)} exception(s) to {path}")


def is_excluded(rel: str, excludes: list[str]) -> bool:
    """ruff's --exclude does not apply to explicitly named files, so the
    staged list is filtered here with the same semantics as the two sibling
    checkers: component, glob, or directory prefix."""
    posix = Path(rel).as_posix()
    for pat in excludes:
        pat = pat.rstrip("/")
        if (pat in Path(rel).parts or fnmatch.fnmatch(posix, pat)
                or fnmatch.fnmatch(posix, f"{pat}/*")):
            return True
    return False


def parent_complexities(project: Path, over_files: set[str]) -> dict[str, int]:
    """Worst C901 score per function across the commit parents' versions.

    Only for the files that are actually over: materialize each parent's
    version of those files in a temp tree and run ruff once per parent there.
    """
    import ratchetlib

    scores: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for rel in over_files:
            for i, src in enumerate(ratchetlib.parent_sources(project, rel)):
                dest = tmp_path / f"p{i}" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(src, encoding="utf-8")
        for parent_dir in sorted(p for p in tmp_path.iterdir() if p.is_dir()):
            for key, score in run_ruff(parent_dir, list(SKIP_DIRS), ["."]).items():
                scores[key] = max(scores.get(key, 0), score)
    return scores


def check_staged(project: Path, baseline: dict[str, int]) -> int:
    """Judge only staged files; block only what THIS commit makes worse."""
    import ratchetlib

    excludes = load_excludes(project)
    staged = [
        rel for rel in ratchetlib.staged_py_files(project)
        if not is_excluded(rel, excludes)
    ]
    if not staged:
        print("✓ No staged Python files to check for complexity")
        return 0

    found = run_ruff(project, excludes, staged)
    over = {k: s for k, s in found.items() if s > baseline.get(k, MAX_COMPLEXITY)}

    over_files = {k.split("::", 1)[0] for k in over}
    parent_scores = parent_complexities(project, over_files) if over_files else {}

    blocking: list[str] = []
    inherited: list[str] = []
    for key, score in sorted(over.items()):
        path, func = key.split("::", 1)
        was = baseline.get(key)
        bound = f"baseline={was}" if was is not None else f"LIMIT={MAX_COMPLEXITY}"
        line = f"  {path}: {func}() complexity {score} [{bound}]"
        if key in parent_scores and score <= parent_scores[key]:
            inherited.append(line)
        else:
            blocking.append(line)

    if blocking:
        print(f"\n=== Complexity Violations ({len(blocking)} BLOCKING, staged) ===")
        print("\n".join(blocking))
        print("\nThis commit makes the function more complex. Simplify or split it.")
    if inherited:
        print(f"\n=== Inherited debt ({len(inherited)} WARNING, staged) ===")
        print("\n".join(inherited))
        print(
            "\nOver the bound, but not made worse by this commit (merge or"
            " pre-existing).\nRe-record the baseline in this commit"
            " (--generate-baseline); CI stays red until you do."
        )
    if not blocking and not inherited:
        print("✓ Staged functions within the complexity limit")
    return 1 if blocking else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check cyclomatic complexity")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--staged", action="store_true",
                        help="Judge only git-staged files (pre-commit mode)")
    parser.add_argument("--generate-baseline", action="store_true",
                        help="Write current violations as the exceptions baseline. NOT safe in a tree other sessions are editing — it baselines whatever is dirty right now; hand-edit instead (see ratchetlib.py)")
    args = parser.parse_args()

    project = Path(args.project).resolve()

    if args.generate_baseline:
        write_baseline(project, scan(project))
        return 0

    baseline = load_baseline(project)

    if args.staged:
        return check_staged(project, baseline)

    found = scan(project)
    blocking = sorted(
        (key, score) for key, score in found.items()
        if score > baseline.get(key, MAX_COMPLEXITY)
    )

    if blocking:
        print(f"\n=== Complexity Violations ({len(blocking)} blocking) ===")
        for key, score in blocking:
            path, func = key.split("::", 1)
            was = baseline.get(key)
            limit = f"baseline={was}" if was is not None else f"LIMIT={MAX_COMPLEXITY}"
            print(f"  {path}: {func}() complexity {score} [{limit}] BLOCKING")

    stale = sorted(set(baseline) - set(found))
    if stale:
        print(f"\n{len(stale)} baselined function(s) no longer violate "
              f"(run --generate-baseline to shrink the baseline).")

    print(f"\nSummary: {len(blocking)} BLOCKING violation(s), "
          f"{len(baseline)} baselined, limit={MAX_COMPLEXITY}")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
