#!/usr/bin/env python3
"""Verify that a refactoring agent actually made changes.

Used by the code-quality orchestrator to detect hallucinated refactoring.
Checks git diff to confirm files were actually modified.

Exit codes:
  0 - verification passed (changes detected)
  1 - verification failed (no changes or hallucination detected)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git_changed_files(project_path: Path) -> set[str]:
    """Return set of files modified in git working tree."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = set(result.stdout.strip().splitlines())
        files.update(staged.stdout.strip().splitlines())
        return files
    except Exception:
        return set()


def count_lines(filepath: Path) -> int:
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def check_file(target_file: str, project_path: Path) -> dict:
    """Check if a specific file was actually modified."""
    changed = git_changed_files(project_path)
    file_in_diff = target_file in changed or any(
        c.endswith(target_file) or target_file.endswith(c) for c in changed
    )

    filepath = project_path / target_file
    actual_loc = count_lines(filepath) if filepath.exists() else None

    return {
        "file": target_file,
        "git_shows_changes": file_in_diff,
        "actual_loc": actual_loc,
        "changed_files": sorted(changed),
    }


def verify_state_file(state_file: Path, project_path: Path) -> dict:
    """Verify all 'completed' entries in a batch state file."""
    with open(state_file) as f:
        state = json.load(f)

    changed = git_changed_files(project_path)
    results = []
    hallucinations = []

    for entry in state.get("file_status", {}).get("completed", []):
        filepath = entry.get("file", "")
        in_diff = filepath in changed or any(
            c.endswith(filepath) or filepath.endswith(c) for c in changed
        )
        result = {
            "file": filepath,
            "git_shows_changes": in_diff,
            "claimed_new_loc": entry.get("new_loc"),
            "actual_loc": count_lines(project_path / filepath) if filepath else None,
        }
        results.append(result)
        if not in_diff:
            hallucinations.append(filepath)

    return {
        "total_completed": len(results),
        "verified": len(results) - len(hallucinations),
        "hallucinations": hallucinations,
        "details": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify refactoring agent changes")
    parser.add_argument("--git-check", metavar="FILE",
                        help="Check if a specific file was modified in git")
    parser.add_argument("--state-file", metavar="PATH",
                        help="Verify all completed entries in a batch state file")
    parser.add_argument("--project", default=".", help="Project root directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    project_path = Path(args.project).resolve()

    if args.git_check:
        result = check_file(args.git_check, project_path)
        if args.json:
            print(json.dumps(result))
        else:
            status = "VERIFIED" if result["git_shows_changes"] else "HALLUCINATION"
            print(f"[{status}] {args.git_check}")
            if not result["git_shows_changes"]:
                print(f"  Expected in git diff but not found.")
                print(f"  Changed files: {result['changed_files'] or '(none)'}")
        sys.exit(0 if result["git_shows_changes"] else 1)

    elif args.state_file:
        result = verify_state_file(Path(args.state_file), project_path)
        if args.json:
            print(json.dumps(result))
        else:
            print(f"Verified: {result['verified']}/{result['total_completed']} entries")
            if result["hallucinations"]:
                print(f"Hallucinations detected ({len(result['hallucinations'])}):")
                for f in result["hallucinations"]:
                    print(f"  - {f}")
        sys.exit(1 if result["hallucinations"] else 0)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
