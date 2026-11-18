#!/usr/bin/env python3
"""Type-error ratchet: ty's diagnostics may only go down.

ty (Astral's type checker, pinned in requirements-ci.txt) checks the scope in
pyproject.toml [tool.ty.src]. Every diagnostic is counted per (file, rule) and
compared with .type-exceptions, the recorded baseline:

  - a count ABOVE its baseline blocks, and so does any diagnostic in a file the
    baseline does not list -- a new error anywhere fails the run;
  - a count BELOW its baseline passes and asks for a re-record, so the bar can
    move down but never back up -- the same rule as .file-size-exceptions.

Counts per (file, rule), not line numbers: a line-anchored allowlist goes stale
on every unrelated edit above it. A count only moves when an error is added or
removed.

The baseline is environment-bound. ty resolves third-party imports from the
interpreter that runs this script (--python sys.prefix): a bare interpreter
reports `pytest`, `yaml` and `jsonschema` as unresolved, a venv built from
requirements-ci.txt does not (measured: 814 vs 624 diagnostics on
the same tree). Record and judge in the CI environment, never in a tree other
sessions are editing (see ratchetlib.py).

Exit codes: 0 clean, 1 a count rose, 2 ty missing or failed to run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

EXC_FILE = ".type-exceptions"
# ty --output-format concise: `path:line:col: error[rule] message`
LINE = re.compile(r"^(?P<file>[^:\n]+):\d+:\d+: (?:error|warning)\[(?P<rule>[a-z0-9-]+)\] ")

Key = tuple[str, str]


def find_ty() -> str | None:
    """$TY, then PATH, then the running interpreter's own bin/."""
    if os.environ.get("TY"):
        return os.environ["TY"]
    beside = Path(sys.prefix) / "bin" / "ty"
    return shutil.which("ty") or (str(beside) if beside.exists() else None)


def run_ty(project: Path, ty: str) -> str:
    proc = subprocess.run(
        [ty, "check", "--output-format", "concise", "--exit-zero", "--python", sys.prefix],
        cwd=project, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(2)
    return proc.stdout


def count_diagnostics(output: str) -> Counter[Key]:
    counts: Counter[Key] = Counter()
    for line in output.splitlines():
        m = LINE.match(line)
        if m:
            counts[(m["file"], m["rule"])] += 1
    return counts


def parse_exceptions(source: str) -> dict[Key, int]:
    try:
        return {(e["file"], e["rule"]): e["count"] for e in json.loads(source).get("exceptions", [])}
    except Exception:
        return {}


def write_baseline(project: Path, counts: Counter[Key]) -> None:
    entries = [{"file": f, "rule": r, "count": n} for (f, r), n in sorted(counts.items())]
    (project / EXC_FILE).write_text(json.dumps({"exceptions": entries}, indent=2) + "\n")
    print(f"Wrote {len(entries)} exceptions ({sum(counts.values())} diagnostics) to {EXC_FILE}")


def judge(counts: Counter[Key], baseline: dict[Key, int]) -> tuple[list, list]:
    """(blocking, stale): counts over their bound, and bounds the tree is now under."""
    blocking = [
        (f, r, n, baseline.get((f, r), 0))
        for (f, r), n in sorted(counts.items()) if n > baseline.get((f, r), 0)
    ]
    stale = [
        (f, r, b, counts.get((f, r), 0))
        for (f, r), b in sorted(baseline.items()) if counts.get((f, r), 0) < b
    ]
    return blocking, stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", default=".")
    ap.add_argument("--generate-baseline", action="store_true")
    args = ap.parse_args()
    project = Path(args.project).resolve()

    ty = find_ty()
    if ty is None:
        print("check_types: ty not found (pip install -r requirements-ci.txt)", file=sys.stderr)
        return 2
    counts = count_diagnostics(run_ty(project, ty))

    if args.generate_baseline:
        write_baseline(project, counts)
        return 0

    exc = project / EXC_FILE
    baseline = parse_exceptions(exc.read_text()) if exc.exists() else {}
    blocking, stale = judge(counts, baseline)
    for f, r, n, b in blocking:
        print(f"BLOCK {f}: {n} x {r} (baseline {b})")
    for f, r, b, n in stale:
        print(f"below baseline {f}: {r} {b} -> {n}; re-record with --generate-baseline")
    print(f"{sum(counts.values())} diagnostics, {len(blocking)} (file, rule) pairs over baseline, {len(stale)} under")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
