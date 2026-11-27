"""Fail a suite run that leaves a test-made file in the real ~/.gearbox-state.

The match spec is frozen from plan dyno-test-leak-guard-2026-11-22 (s01 evidence): the one
leak family the suite ever made is the land-hold pointer `land-holds/<name>__origin.json`
whose `locks_dir` sits under a temporary folder. Read only: never writes, moves or deletes.
CLI: python3 dyno_guard.py --scan --since YYYY-MM-DD   (lists matching files in the real ~/.gearbox-state)
"""

import fnmatch
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import safe_json


class Rule(TypedDict):
    folder: str
    pattern: str
    key: str
    any_prefix: tuple[str, ...]


SPEC: list[Rule] = [{
    "folder": "land-holds",
    "pattern": "*__origin.json",
    "key": "locks_dir",
    "any_prefix": ("/private/var/folders/", "/var/folders/", "/private/tmp/", "/tmp/"),
}]


def _matches_content(path, rule):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    value = safe_json.get_str(safe_json.load_dict(text), rule["key"])
    return value is not None and value.startswith(rule["any_prefix"])


def _names(home):
    out = {}
    for rule in SPEC:
        d = Path(home) / ".gearbox-state" / rule["folder"]
        out[rule["folder"]] = (
            {p.name: p.stat().st_mtime_ns for p in d.iterdir() if fnmatch.fnmatch(p.name, rule["pattern"])}
            if d.is_dir()
            else {}
        )
    return out


def snapshot(home):
    """{folder: {name: mtime_ns}} for names matching the glob, as they are now."""
    return _names(home)


def new_leaks(before, home):
    """Paths created or rewritten since `before` that match the glob AND the content rule."""
    after = _names(home)
    found = []
    for rule in SPEC:
        old = before.get(rule["folder"], {})
        for name in sorted(n for n, m in after[rule["folder"]].items() if old.get(n) != m):
            p = Path(home) / ".gearbox-state" / rule["folder"] / name
            if _matches_content(p, rule):
                found.append(p)
    return found


def scan_since(home, since_ts):
    return [
        p
        for rule in SPEC
        for name in sorted(_names(home)[rule["folder"]])
        for p in [Path(home) / ".gearbox-state" / rule["folder"] / name]
        if p.stat().st_mtime > since_ts and _matches_content(p, rule)
    ]


# pytest hooks. Two conftests re-export them: the repo root (tree runs) and skills/plan-execute/scripts
# (its own pytest.ini makes that folder the rootdir, so a per-file run never loads the root one).
_REAL_HOME = Path.home()  # read at import, before any test can change HOME
_before = None


def pytest_sessionstart(session):
    """Note the test-made files already in the real ~/.gearbox-state (xdist controller only; an error warns)."""
    global _before
    if hasattr(session.config, "workerinput"):
        return
    try:
        _before = snapshot(_REAL_HOME)
    except Exception as e:  # noqa: BLE001
        print(f"GEARBOX_STATE-GUARD warning: snapshot failed: {e}")


def pytest_sessionfinish(session, exitstatus):
    """Fail the run, naming each path, when it left a test-made file in the real ~/.gearbox-state."""
    global _before
    before, _before = _before, None  # both conftests may call this: report once
    if before is None or hasattr(session.config, "workerinput"):
        return
    try:
        leaks = new_leaks(before, _REAL_HOME)
    except Exception as e:  # noqa: BLE001
        print(f"GEARBOX_STATE-GUARD warning: check failed: {e}")
        return
    for p in leaks:
        print(f"GEARBOX_STATE-LEAK: {p}")
    if leaks:
        import pytest

        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main(argv):
    if "--scan" not in argv or "--since" not in argv:
        print("usage: dyno_guard.py --scan --since YYYY-MM-DD", file=sys.stderr)
        return 2
    since = datetime.strptime(argv[argv.index("--since") + 1], "%Y-%m-%d").timestamp()
    hits = scan_since(Path.home(), since)
    for p in hits:
        print(f"GEARBOX_STATE-LEAK: {p}")
    print(f"{len(hits)} matching file(s) since {argv[argv.index('--since') + 1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
