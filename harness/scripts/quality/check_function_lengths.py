#!/usr/bin/env python3
"""Check Python function/method lengths against configured thresholds.

Reads thresholds from pyproject.toml [tool.claude-quality] section.
Respects .function-length-exceptions baseline file.

Source of truth: gearbox scripts/quality/ (deployed to ~/.claude/scripts/quality/).
Adopting repos vendor this file into tools/ via vendor_quality.py — never edit a
vendored copy; re-sync instead.

Modes:
  default            whole-project scan (CI, adoption snapshots)
  --staged           judge only the files staged in git (pre-commit hooks).
                     A function blocks only when this commit makes it worse:
                     over its bound AND longer than at every commit parent.
                     Inherited debt warns and asks for a baseline re-record;
                     the CI whole-project run stays red until that happens.

Exit codes:
  0 - no blocking violations
  1 - blocking violations found
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import sys
from pathlib import Path

EXC_FILE = ".function-length-exceptions"

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


def exception_key(rel_path: str, name: str) -> str:
    """Identify a grandfathered function by file and name — never by line.

    A line number in the key made the baseline break on contact: inserting one
    line anywhere above a forgiven function shifted its lineno, missed the key,
    and re-blocked a function nobody had touched.
    """
    return f"{rel_path}:{name}"


def load_exceptions(project_path: Path) -> dict[str, int]:
    """Map each grandfathered function to the length it was baselined at.

    ponytail: keyed on identity AND magnitude, not identity alone. Name-only
    matching let a baselined function grow without limit -- cli.py::_main
    could go from its recorded 1,657 lines to 5,000 and never block, which is
    not a ratchet.
    """
    exc_file = project_path / EXC_FILE
    if not exc_file.exists():
        return {}
    return parse_exceptions(exc_file.read_text())


def parse_exceptions(source: str) -> dict[str, int]:
    """The baseline text -> {key: lines}. Shared with the parent-version read.

    Rebuilt from each entry's own fields, so a baseline written under the old
    file:lineno:name scheme keeps working without regeneration.
    """
    try:
        return {
            exception_key(e["file"], e["name"]): e["lines"]
            for e in json.loads(source).get("exceptions", {}).values()
        }
    except Exception:
        return {}


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
    # ponytail: prune during the walk, never rglob-then-filter (measured:
    # 69,255 files in 4.02s unpruned, 274 files in 0.02s pruned).
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


def functions_in_source(source: str, filename: str = "<source>") -> list[tuple[int, str, int]]:
    """Return list of (lineno, name, length) for all functions in the source."""
    try:
        tree = ast.parse(source, filename=filename)
    except Exception:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if hasattr(node, "end_lineno"):
                length = node.end_lineno - node.lineno + 1
                results.append((node.lineno, node.name, length))
    return results


def get_functions(filepath: Path) -> list[tuple[int, str, int]]:
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return functions_in_source(source, str(filepath))


def find_violations(
    project_path: Path, config: dict, exceptions: dict[str, int]
) -> tuple[list[dict], list[dict]]:
    blocking: list[dict] = []
    warnings: list[dict] = []

    for py_file in iter_python_files(project_path, config.get("exclude")):
        rel = str(py_file.relative_to(project_path))
        for lineno, name, length in get_functions(py_file):
            key = exception_key(rel, name)
            entry = {"file": rel, "lineno": lineno, "name": name, "lines": length}

            if key in exceptions:
                # Grandfathered at a recorded length: shrinking is always fine,
                # growing past what was baselined is not.
                if length > exceptions[key]:
                    blocking.append({**entry, "baseline": exceptions[key]})
                continue

            if length > config["limit"]:
                blocking.append(entry)
            elif length >= config["warning"]:
                warnings.append(entry)

    blocking.sort(key=lambda x: -x["lines"])
    warnings.sort(key=lambda x: -x["lines"])
    return blocking, warnings



def parent_lengths_of(project_path: Path, rel: str, name: str) -> list[int]:
    """How long `name` was in each parent commit's version of `rel`."""
    import ratchetlib

    out = []
    for src in ratchetlib.parent_sources(project_path, rel):
        for _, fname, length in functions_in_source(src):
            if fname == name:
                out.append(length)
    return out


def judge_raised_bounds(
    project_path: Path, config: dict, exceptions: dict[str, int], raised: dict,
) -> tuple[list[str], list[str]]:
    """Split this commit's raised bounds into (blocking, warning) lines.

    Judged whether or not the .py file is staged: a "re-record the baselines"
    commit stages the baseline and no source at all.
    """
    bad: list[str] = []
    ok: list[str] = []
    for key, was in sorted(raised.items()):
        rel, name = key.rsplit(":", 1)
        if is_excluded(Path(rel), config.get("exclude") or []):
            continue
        bound = exceptions[key]
        had = f"was {was}" if was is not None else "not baselined before"
        line = f"  {rel}:{name}(): baseline raised to {bound} ({had})"
        # A parent that already carried it this long is the evidence the commit
        # RECORDS debt; without one, the commit grew it AND wrote its own bar.
        befores = parent_lengths_of(project_path, rel, name)
        if befores and max(befores) >= bound:
            ok.append(f"{line} — a parent had {max(befores)}")
        elif befores:
            bad.append(f"{line} — longest parent was {max(befores)}")
        else:
            bad.append(f"{line} — no parent has this function")
    return bad, ok


def judge_staged_file(
    project_path: Path, rel: str, config: dict, exceptions: dict[str, int],
) -> tuple[list[str], list[str]]:
    """One staged file's over-bound functions, split into (blocking, inherited)."""
    import ratchetlib

    parent_lengths: dict[str, int] = {}
    for src in ratchetlib.parent_sources(project_path, rel):
        for _, name, length in functions_in_source(src):
            parent_lengths[name] = max(parent_lengths.get(name, 0), length)

    bad: list[str] = []
    carried: list[str] = []
    for lineno, name, length in get_functions(project_path / rel):
        key = exception_key(rel, name)
        if key in exceptions:
            bound, label = exceptions[key], "baseline"
        else:
            bound, label = config["limit"], "LIMIT"
        if length <= bound:
            continue
        line = f"  {rel}:{lineno}  {name}()  {length} lines [{label}={bound}]"
        if name in parent_lengths and length <= parent_lengths[name]:
            carried.append(line)
        else:
            bad.append(line)
    return bad, carried


def check_staged(project_path: Path, config: dict, exceptions: dict[str, int]) -> int:
    """Judge only staged files; block only what THIS commit makes worse."""
    import ratchetlib

    blocking: list[str] = []
    inherited: list[str] = []
    self_recorded: list[str] = []
    # Bounds this very commit introduces or raises. A ratchet whose bar the
    # same commit can rewrite is not a ratchet, so these are judged on their
    # own below — including in a commit that stages no .py file at all.
    raised = ratchetlib.self_recorded_bounds(
        project_path, EXC_FILE, parse_exceptions, exceptions,
    )
    for rel in ratchetlib.staged_py_files(project_path):
        if is_excluded(Path(rel), config.get("exclude") or []):
            continue
        bad, carried = judge_staged_file(project_path, rel, config, exceptions)
        blocking.extend(bad)
        inherited.extend(carried)

    raised_bad, raised_ok = judge_raised_bounds(
        project_path, config, exceptions, raised,
    )
    blocking.extend(raised_bad)
    self_recorded.extend(raised_ok)

    if blocking:
        print(f"\n=== Function Length Violations ({len(blocking)} BLOCKING, staged) ===")
        print("\n".join(blocking))
        print("\nThis commit grows the function past its bound. Extract helpers.")
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
            "\nA parent already carried each of these at this length, so the"
            " commit is\nrecording existing debt, not authoring it. Reported"
            " rather than blocked —\nbut it is never a silent pass: read the"
            " diff and confirm that is what it is."
        )
    if not blocking and not inherited and not self_recorded:
        print("✓ Staged functions within length limits")
    return 1 if blocking else 0


def generate_baseline(project_path: Path, config: dict) -> None:
    blocking, _ = find_violations(project_path, config, {})
    exc_file = project_path / EXC_FILE

    # Load existing to preserve any manual entries not in current scan
    existing: dict = {}
    if exc_file.exists():
        try:
            with open(exc_file) as f:
                existing = json.load(f).get("exceptions", {})
        except Exception:
            pass

    new_exceptions = {
        exception_key(v["file"], v["name"]): {
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
        f.write("\n")

    removed = len(existing) - len(new_exceptions)
    print(
        f"Generated baseline: {len(new_exceptions)} exceptions -> {exc_file}"
        + (f" ({removed} stale entries removed)" if removed > 0 else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Python function lengths")
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

    # Human-readable output (skill parses the "BLOCKING" keyword)
    if blocking:
        print(f"\n=== Function Length Violations ({len(blocking)} BLOCKING) ===")
        for v in blocking:
            bound = (
                f"baseline={v['baseline']}" if "baseline" in v
                else f"LIMIT={config['limit']}"
            )
            print(
                f"  {v['file']}:{v['lineno']}  {v['name']}()  "
                f"{v['lines']} lines [{bound}] BLOCKING"
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
