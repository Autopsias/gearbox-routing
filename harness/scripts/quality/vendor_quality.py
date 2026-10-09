#!/usr/bin/env python3
"""Vendor the quality-ratchet checkers into an adopting repo, or check drift.

The one source of truth for the checkers is this directory (gearbox
scripts/quality/, deployed to ~/.claude/scripts/quality/). A repo that adopts
the ratchet gets byte-identical copies in tools/ plus the CI workflow, because
a pre-commit hook pointing at a home directory fails for every other clone and
in CI. This script IS the sync step: a fix to a checker lands here once and
reaches every adopting repo on its next re-sync — the same pattern
package_clients.py uses for skills.

Usage:
  python3 ~/.claude/scripts/quality/vendor_quality.py <repo> [--check]

  <repo>     target repository root (must contain .git)
  --check    report drift between the vendored copies and this source; exit 1
             on any difference, write nothing.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
CHECKERS = [
    "check_file_sizes.py",
    "check_function_lengths.py",
    "check_complexity.py",
    "ratchetlib.py",
]
WORKFLOW = "quality-ratchet.yml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="Target repository root")
    parser.add_argument("--check", action="store_true",
                        help="Report drift only; write nothing")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"ERROR: {repo} is not a git repository root", file=sys.stderr)
        return 2

    pairs = [(SOURCE / name, repo / "tools" / name) for name in CHECKERS]
    pairs.append((SOURCE / WORKFLOW, repo / ".github" / "workflows" / WORKFLOW))

    if args.check:
        drifted = [
            dest for src, dest in pairs
            if not dest.exists() or dest.read_bytes() != src.read_bytes()
        ]
        for dest in drifted:
            print(f"DRIFT: {dest}")
        if drifted:
            print("\nRe-sync with: python3 "
                  f"{SOURCE / 'vendor_quality.py'} {repo}")
            return 1
        print(f"✓ {len(pairs)} vendored file(s) match the source of truth")
        return 0

    for src, dest in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        changed = not dest.exists() or dest.read_bytes() != src.read_bytes()
        shutil.copyfile(src, dest)
        print(f"{'synced' if changed else 'up-to-date'}: {dest.relative_to(repo)}")

    print(
        "\nNext (one sitting — see the adoption recipe in "
        "~/.claude/references/code-quality/brownfield-adoption.md):\n"
        "  1. Wire the three --staged hooks into .pre-commit-config.yaml\n"
        "  2. Generate the baselines ON THE BRANCH YOU MERGE FROM and commit them\n"
        "  3. Commit the CI workflow (.github/workflows/quality-ratchet.yml)\n"
        "  4. Write the SKIP and merge-time rules into the repo's agent context file"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
