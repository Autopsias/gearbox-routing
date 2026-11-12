#!/usr/bin/env python3
"""testvalue.py — the countable half of the test-value review.

A test earns its place when it is the only thing that would notice a defect.
This script gives two leads on which tests do not, so the review states numbers
instead of guessing them:

  - per-test coverage: from a coverage.py data file recorded with
    `dynamic_context = test_function`, the tests that own no source line of
    their own (every line they run, another test runs too), and for each one a
    test whose lines contain all of its lines;
  - static signals per test file: the count of test functions, of mock or patch
    lines, and of lines that reach into a private name (`module._name`).

Both are LEADS, never proof. Two tests can run the same lines and assert
different things, and a test that runs code only in a subprocess records no
lines at all (it is reported as untraced, not as worthless). The proof is the
mutation result: see references/reviews.md#test-value.

Usage:
  testvalue.py <tests-dir> [--data <coverage-data-file>] [--json]
"""
import argparse
import collections
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w*)", re.M)
MOCK = re.compile(r"\b(?:Mock|MagicMock|AsyncMock|patch|monkeypatch|mocker)\b")
# `obj._name` on anything but self/cls, and `import _name`; dunders are public protocol
PRIVATE = re.compile(r"(?<![\w.])(?!self\b|cls\b)\w+\._(?!_)\w+|\bimport _(?!_)\w+")


def analyse(line_sets):
    """line_sets: {test name: set of (file, line)} -> the per-test coverage report."""
    owners = collections.defaultdict(set)
    for test, lines in line_sets.items():
        for line in lines:
            owners[line].add(test)
    zero = []
    for test, lines in sorted(line_sets.items()):
        if not lines or any(len(owners[line]) == 1 for line in lines):
            continue
        covering = set.intersection(*(owners[line] for line in lines)) - {test}
        zero.append({"test": test, "lines": len(lines),
                     "subsumed_by": min(covering, key=lambda t: (len(line_sets[t]), t)) if covering else None})
    return {
        "tests_traced": sum(1 for lines in line_sets.values() if lines),
        "source_lines": len(owners),
        "single_owner_lines": sum(1 for tests in owners.values() if len(tests) == 1),
        "zero_unique": zero,
    }


def load_line_sets(data_file):
    """Read a coverage.py data file recorded with dynamic_context = test_function."""
    try:  # lazy: the static half must run where coverage is absent. By name: a
        # checker run from a repo with a `coverage/` folder resolves that folder instead.
        coverage: Any = importlib.import_module("coverage")
    except ImportError:
        sys.exit("testvalue: --data needs coverage.py; run with `uv run --with coverage`")
    data = coverage.CoverageData(basename=str(data_file))
    data.read()
    sets = {}
    for context in sorted(data.measured_contexts()):
        if not context:
            continue
        data.set_query_context(context)
        sets[context] = {(f, line) for f in data.measured_files() for line in (data.lines(f) or [])}
    return sets


def static_signals(tests_dir):
    """Per test file: test functions, mock lines, lines that name a private member."""
    rows = []
    for path in sorted(Path(tests_dir).rglob("*.py")):
        if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        rows.append({
            "file": str(path.relative_to(tests_dir)),
            "tests": len(TEST_DEF.findall(text)),
            "lines": len(lines),
            "mock_lines": sum(1 for line in lines if MOCK.search(line)),
            "private_lines": sum(1 for line in lines if PRIVATE.search(line)),
        })
    return rows


def build_report(tests_dir, data_file=None):
    files = static_signals(tests_dir)
    report = {"files": files, "tests_defined": sum(f["tests"] for f in files)}
    if data_file:
        report["coverage"] = analyse(load_line_sets(data_file))
        # a parametrized test is one traced context, so the difference is whole functions
        report["coverage"]["tests_untraced"] = max(
            0, report["tests_defined"] - report["coverage"]["tests_traced"])
    return report


def render(report):
    out = [f"test functions defined: {report['tests_defined']} in {len(report['files'])} files"]
    cov = report.get("coverage")
    if cov:
        zero = cov["zero_unique"]
        out += [
            f"traced: {cov['tests_traced']}   untraced (often subprocess-only): {cov['tests_untraced']}",
            f"source lines run: {cov['source_lines']}   run by exactly one test: {cov['single_owner_lines']}",
            f"tests that own no line of their own: {len(zero)} of {cov['tests_traced']}"
            f"   with a single test that contains them: {sum(1 for z in zero if z['subsumed_by'])}",
            "", "LEADS (zero own lines; confirm each with the mutation result):",
        ]
        out += [f"  {z['test']}  ({z['lines']} lines)  <= {z['subsumed_by'] or 'several tests together'}"
                for z in zero]
    out += ["", f"{'file':<44}{'tests':>6}{'mock':>6}{'private':>8}"]
    out += [f"{f['file']:<44}{f['tests']:>6}{f['mock_lines']:>6}{f['private_lines']:>8}"
            for f in sorted(report["files"], key=lambda f: -(f["mock_lines"] + f["private_lines"]))]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("tests_dir", type=Path)
    ap.add_argument("--data", type=Path, help="coverage.py data file with per-test contexts")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.tests_dir.is_dir():
        ap.error(f"not a directory: {args.tests_dir}")
    if args.data and not args.data.is_file():
        ap.error(f"no coverage data file: {args.data}")
    report = build_report(args.tests_dir, args.data)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
