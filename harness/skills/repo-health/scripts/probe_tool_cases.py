#!/usr/bin/env python3
"""TOOL_PROBES: the cases an EXTERNAL tool judges, not the collector.

Split from probe_cases.py when that file reached the 500-LOC blocking limit.
The line is who answers: CASES there are fixtures the collector itself reads;
every row here is a command (gitleaks, semgrep, ruff, pip-audit, deptry, make)
run against a clean repo and a planted one. Nothing here needs the BASE fixture.

Imported by probe_cases.py, which re-exports TOOL_PROBES — never the other way
round.
"""

# Assembled at runtime, never stored as a literal: a secret-shaped constant here
# makes gitleaks refuse THIS repo's own commits (hit), and pinning a
# .gitleaksignore fingerprint would allowlist the exact shape this probe exists to
# prove is caught. Deterministic so the fixture is stable across runs.
_FAKE_KEY = "AKIA" + "JQ7XR2NM" + "4TVBW9DP"
AWS_KEY = f'AWS_KEY = "{_FAKE_KEY}"\n'
TAR_BAD = "import tarfile\n\n\ndef x(p):\n    t = tarfile.open(p)\n    t.extractall('/tmp/o')\n"
# NOT `extractall(..., filter='data')`: the trailofbits rule fires on the call
# whatever its arguments, so that "clean" fixture carried the defect and the
# probe passed on a clean side that was never clean (found by the
# clean-must-not-match-the-plant rule in run_tool_probe below).
TAR_OK = "import tarfile\n\n\ndef x(p):\n    t = tarfile.open(p)\n    return t.getnames()\n"
COMPLEX = ("def f(x):\n"
           + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(15))
           + "    return 0\n")

TOOL_PROBES = [
    {"id": "sec.secrets-history", "tool": "gitleaks",
     "case": "a real-shaped AWS access key committed to history",
     "cmd": ["gitleaks", "git", ".", "--no-banner", "--redact"], "git": True,
     "clean": {"a.py": "x = 1\n"}, "plant": {"a.py": "x = 1\n", "s.py": AWS_KEY},
     "clean_pat": r"no leaks found", "plant_pat": r"leaks found: [1-9]"},
    {"id": "sec.sast", "tool": "semgrep", "net": "semgrep.dev",
     "case": "tarfile.extractall() with no filter= (path traversal)",
     "cmd": ["semgrep", "scan", "--config", "p/default", "--config", "p/python",
             "--error", "."],
     "clean": {"a.py": TAR_OK}, "plant": {"a.py": TAR_BAD},
     "clean_pat": r"Ran \d+ rules on \d+ files?: 0 findings",
     "plant_pat": r"tarfile-extractall-traversal"},
    {"id": "cq.lint", "tool": "ruff",
     "case": "an undefined name (F821)",
     "cmd": ["ruff", "check", "."],
     "clean": {"a.py": "def f():\n    return 1\n"},
     "plant": {"a.py": "def f():\n    return missing_name\n"},
     "clean_pat": r"All checks passed", "plant_pat": r"F821.*[Uu]ndefined name"},
    {"id": "cq.complexity", "tool": "ruff",
     "case": "a 16-branch function (C901)",
     "cmd": ["ruff", "check", "--select", "C901", "."],
     "clean": {"a.py": "def f(x):\n    return x + 1\n"}, "plant": {"a.py": COMPLEX},
     "clean_pat": r"All checks passed", "plant_pat": r"C901 .* is too complex"},
    {"id": "cq.slop", "tool": "ruff",
     "case": "an unused import (F401)",
     "cmd": ["ruff", "check", "--select", "F401,F841,E722,ERA001,BLE001", "."],
     "clean": {"a.py": "import os\n\n\ndef f():\n    return os.getpid()\n"},
     "plant": {"a.py": "import os\n\n\ndef f():\n    return 1\n"},
     "clean_pat": r"All checks passed", "plant_pat": r"F401.*imported but unused"},
    # sec.dep-vulns and hyg.dep-unused were once UNVERIFIED GAPS: pip-audit
    # and deptry were not installed, and `uvx pip-audit -r <file>` cannot run here
    # at all — it builds a throwaway venv whose `ensurepip` dies with SIGABRT in
    # this sandbox (reproduced, with and without --no-deps). So the
    # probe asks pip-audit the same question through the input mode that does NOT
    # build a venv: `--with-requirements` makes uv resolve the file into the
    # ephemeral tool env, and pip-audit then audits the environment it is running
    # in. Different plumbing, same refusal — which is the thing being probed.
    # Both need the network (an advisory feed, a package index), like semgrep's,
    # and that is what `net` declares: an OFFLINE runner has NOT looked, and
    # run_tool_probe must say GAP rather than accuse the gate of being broken.
    {"id": "sec.dep-vulns", "tool": "uvx", "net": "pypi.org",
     "case": "pip-audit against a known-vulnerable pin (jinja2 2.11.3)",
     "cmd": ["uvx", "--with-requirements", "requirements.txt", "pip-audit"],
     "clean": {"requirements.txt": "jinja2==3.1.6\n"},
     "plant": {"requirements.txt": "jinja2==2.11.3\n"},
     "clean_pat": r"No known vulnerabilities found",
     "plant_pat": r"jinja2 +2\.11\.3 +(PYSEC|GHSA)"},
    {"id": "hyg.dep-unused", "tool": "uvx", "net": "pypi.org",
     "case": "deptry on a declared dependency nothing imports (DEP002)",
     "cmd": ["uvx", "deptry", "."],
     "clean": {"requirements.txt": "requests==2.32.3\n",
               "app.py": "import requests\n\nprint(requests.__name__)\n"},
     "plant": {"requirements.txt": "requests==2.32.3\n", "app.py": "x = 1\n"},
     "clean_pat": r"No dependency issues found",
     "plant_pat": r"DEP002 'requests' defined as a dependency but not used"},
    {"id": "test.suite", "tool": "make",
     "case": "the suite command the collector prints, on a repo whose tests fail",
     "cmd": ["make", "test"],
     "clean": {"Makefile": "test:\n\t@python3 -c 'print(\"suite green\")'\n"},
     "plant": {"Makefile": "test:\n\t@python3 -c 'assert 1 == 2, \"boom\"'\n"},
     "clean_pat": r"suite green", "plant_pat": r"AssertionError: boom"},
]
