#!/usr/bin/env python3
"""The fixture corpus: what a healthy repo looks like, and one defect per check.

Split from probe_checks.py, which is the engine that runs this table. The line
between them is data and machinery: nothing here knows how a probe is judged,
and nothing there knows what a dependabot config looks like.

The git-mutating setup steps live in probe_setup.py, one more split along the
same line: this file is the file CONTENT a fixture is built from, that one is the
state git has to make true afterwards. TOOL_PROBES — the cases an EXTERNAL tool
judges, not the collector — lives in probe_tool_cases.py and is re-exported here.

Every case is BASE — a repo where every check passes — plus the smallest
override that plants ONE defect. Several checks carry more than one case: the
supported input families their contract names (a workflow, a pure-TypeScript
repo, a VCS requirement), the regressions this suite has actually missed
before, boundary pairs either side of a threshold, and false-positive controls
where the right answer is that nothing moves.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The fixture builder is the tests' own, not a second copy: two make_repo()s is
# how the suite and the probes would start disagreeing about what a repo is.
# HASH_PINNED / VCS_REQS / PROSE are the artifacts of real defects.
from probe_tool_cases import TOOL_PROBES  # noqa: E402,F401  (re-exported)
from probe_setup import (add_remote, age_commit, dangling_symlink,  # noqa: E402
                         hide_from_worktree, leave_stash, unmerged_branch)
from test_health_shape import (HASH_PINNED, PROSE, VCS_REQS,  # noqa: E402
                               git, make_repo)  # noqa: F401  (make_repo re-exported)


def sub(text, old, new=""):
    """Mutate a fixture, refusing to no-op.

    A plant that silently does not land produces a probe that "passes clean"
    twice and reports health it never measured — the exact defect this module
    exists to find, one level up. A neuter that does not land is easy to miss
    in review, so the check is here instead.
    """
    if old not in text:
        raise ValueError(f"fixture mutation is a no-op: {old!r} not in fixture")
    return text.replace(old, new, 1)


# ---------- the base fixture: a repo where every check passes ----------

WF = """\
name: ci
on: [push]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - uses: actions/cache@0c45773b623bea8c8e75f6c82b208c3cf94ea4f9
      - run: make test
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          retention-days: 7
"""
PERMS = "permissions:\n  contents: read\n"
CONCUR = "concurrency:\n  group: ci-${{ github.ref }}\n  cancel-in-progress: true\n"
CACHE = "      - uses: actions/cache@0c45773b623bea8c8e75f6c82b208c3cf94ea4f9\n"
RUN_STEP = "      - run: make test\n"
EVENT_MSG = "${{ github.event.head_commit.message }}"
# The repo's own exclude list, holding a GLOB — the shape the house ratchet's
# is_excluded() accepts and this collector used to ignore.
GLOB_EXCL = '[tool.claude-quality]\nexclude = ["_plans/*/_evidence"]\n'
CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
README = ("# demo\n\n" + "\n".join(f"paragraph {i}" for i in range(24))
          + "\n\n```\nmake test\n```\n")
THIN_README = "# demo\n"
NO_CMD_README = "# demo\n\n" + "\n".join(f"paragraph {i}" for i in range(24)) + "\n"
NB_CLEAN = json.dumps({"cells": [{"cell_type": "code", "source": [], "outputs": []}]})
NB_DIRTY = json.dumps({"cells": [{"cell_type": "code", "source": [],
                                  "outputs": [{"text": "secret"}]}]})

BASE = {
    "README.md": README,
    "CLAUDE.md": "# demo\n\nRun `make test` before pushing.\n",
    "Makefile": "test:\n\t@echo ok\ncheck:\n\t@echo ok\n",
    ".pre-commit-config.yaml": "repos: []\n",
    "requirements.txt": "requests==2.32.3\n",
    "uv.lock": "version = 1\n",
    ".github/workflows/ci.yml": WF,
    ".github/dependabot.yml": "version: 2\nupdates: []\n",
    "src/app.py": "def main():\n    return 1\n",
    "scripts/quality/check_file_sizes.py": "print('ok')\n",
}
NO_PY = {"src/app.py": None, "scripts/quality/check_file_sizes.py": None}


def lines(n, body="x = 1"):
    return "\n".join([body] * n) + "\n"


def func(body_lines, name="f"):
    """A module holding ONE function exactly `body_lines + 1` lines long.

    def line + one statement per body line, and nothing after it, so the AST's
    end_lineno - lineno + 1 is the number the boundary case asserts on.
    """
    return (f"def {name}():\n"
            + "".join(f"    x{i} = {i}\n" for i in range(body_lines)))


def many_todos(n):
    return {"src/todos.py": "\n".join(f"# TODO item {i}" for i in range(n)) + "\n"}


def C(cid, case, plant, plant_expect, clean=None, clean_expect=("pass", None),
      clean_setup=None, plant_setup=None, kind="plant"):
    # There is deliberately no `slow` flag. One existed, and the only case that
    # carried it (hyg.large-files, below) was thereby the only coverage a check id
    # had — so the subset CI ran could not refuse a 5 GB threshold. Cost belongs
    # to --tools, which is opt-in; a deterministic case either runs or is deleted.
    return dict(id=cid, case=case, plant=plant, plant_expect=plant_expect,
                clean=clean, clean_expect=clean_expect, kind=kind,
                clean_setup=clean_setup, plant_setup=plant_setup)


WFP = ".github/workflows/ci.yml"
SELF_HOSTED = sub(sub(WF, CACHE), "runs-on: ubuntu-latest",
                  "runs-on: [self-hosted, macOS, ARM64]")
NO_MANIFEST = r"no dependency manifest tracked — looked for requirements"

CASES = [
    # ---- workflow security ----
    C("sec.workflow-permissions", "workflow with no top-level permissions block",
      {WFP: sub(WF, PERMS)}, ("fail", r"missing top-level permissions block: " + re.escape(WFP))),
    C("sec.action-pinning", "third-party action referenced by tag",
      {WFP: sub(WF, "actions/cache@0c45773b623bea8c8e75f6c82b208c3cf94ea4f9",
                "codecov/codecov-action@v4")},
      ("fail", r"codecov/codecov-action@v4")),
    C("sec.action-pinning", "boundary: FIRST-party action by tag is warn, not fail",
      {WFP: sub(WF, CHECKOUT, "actions/checkout@v4")}, ("warn", r"actions/checkout@v4")),
    C("sec.dangerous-workflow", "pull_request_target checking out the PR head",
      {WFP: sub(sub(WF, "on: [push]", "on: [pull_request_target]"),
                CHECKOUT, CHECKOUT + "\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}")},
      ("fail", r"pull_request_target \+ PR-head checkout")),
    C("sec.dangerous-workflow", "boundary: event data interpolated into run: is warn",
      {WFP: sub(WF, RUN_STEP, f"      - run: echo {EVENT_MSG}\n")},
      ("warn", r"event data interpolated inside run:/script:")),
    C("sec.dangerous-workflow", "event data inside a block-scalar run: body is warn too",
      {WFP: sub(WF, RUN_STEP, f"      - run: |\n          make test\n          echo {EVENT_MSG}\n")},
      ("warn", r"event data interpolated inside run:/script:")),
    # The check's own fix line says "pass event data via env" — and it kept
    # warning on every workflow that had done exactly that, because it asked only
    # whether the FILE held both `run:` and the expression.
    C("sec.dangerous-workflow", "control: event data passed through env: is the FIX, "
                                "not a finding — even right after a run: block",
      {WFP: sub(WF, RUN_STEP, '      - run: |\n          echo "$MSG"\n        env:\n'
                              f"          MSG: {EVENT_MSG}\n")},
      ("pass", r"no pull_request_target/injection patterns found"), kind="control"),
    C("ci.timeouts", "job with no timeout-minutes",
      {WFP: sub(WF, "    timeout-minutes: 30\n")},
      ("warn", r"jobs without timeout-minutes in: " + re.escape(WFP))),
    C("ci.concurrency", "no concurrency group in any workflow",
      {WFP: sub(WF, CONCUR)}, ("warn", r"no workflow sets a concurrency group")),
    C("ci.caching", "lockfile tracked but no caching step",
      {WFP: sub(WF, CACHE)}, ("warn", r"lockfile tracked but no dependency caching")),
    C("ci.caching", "control: no lockfile means nothing to cache, so still pass",
      {WFP: sub(WF, CACHE), "uv.lock": None}, ("pass", r"nothing to cache"), kind="control"),
    # A self-hosted runner keeps its own disk: whether dependencies are cached is
    # a fact about the machine, which no workflow line states either way.
    C("ci.caching", "every job on a self-hosted runner: the cache is on its disk, not in the YAML",
      {WFP: SELF_HOSTED}, ("na", r"1 job\(s\) name a self-hosted runner and none names")),
    C("ci.caching", "a runner taken from a repository variable is unreadable, not hosted",
      {WFP: SELF_HOSTED,
       ".github/workflows/other.yml": sub(sub(WF, CACHE), "runs-on: ubuntu-latest",
                                          "runs-on: ${{ fromJSON(vars.RUNS_ON || '\"ubuntu-latest\"') }}")},
      ("na", r"1 take the runner from a variable this check cannot read"),
      clean={".github/workflows/other.yml": WF}),
    C("ci.caching", "boundary: a matrix runner beside the self-hosted ones still warns",
      {WFP: SELF_HOSTED,
       ".github/workflows/other.yml": sub(sub(WF, CACHE), "runs-on: ubuntu-latest",
                                          "runs-on: ${{ matrix.os }}")},
      ("warn", r"lockfile tracked but no dependency caching"),
      clean={".github/workflows/other.yml": WF}),
    C("ci.caching", "boundary: ONE hosted job beside the self-hosted ones still warns",
      {WFP: SELF_HOSTED, ".github/workflows/other.yml": sub(WF, CACHE)},
      ("warn", r"lockfile tracked but no dependency caching"),
      clean={".github/workflows/other.yml": WF}),
    C("ci.retention", "upload-artifact with no retention-days",
      {WFP: sub(WF, "        with:\n          retention-days: 7\n")},
      ("warn", r"upload-artifact without retention-days")),
    C("ci.wall-clock", "gate: no workflows at all",
      {WFP: None}, ("na", r"runs no GitHub Actions"), clean_expect=("pending", r"gh run list")),

    # ---- static security + CI shape ----
    C("sec.tracked-sensitive", "a tracked .env file",
      {".env": "TOKEN=x\n"}, ("fail", r"tracked: \.env")),
    C("sec.tracked-sensitive", "a tracked private key at depth",
      {"deploy/id_rsa": "-----BEGIN PRIVATE KEY-----\n"}, ("fail", r"deploy/id_rsa")),
    C("sec.dep-update-config", "manifest tracked, no dependabot/renovate config",
      {".github/dependabot.yml": None}, ("warn", r"none of \.github/dependabot\.yml")),
    C("sec.dep-update-config", "gate: no manifest at all",
      {".github/dependabot.yml": None, "requirements.txt": None},
      ("na", NO_MANIFEST)),
    C("ci.pre-commit", "no local hook gate",
      {".pre-commit-config.yaml": None}, ("warn", r"no local hook gate")),
    C("ci.server-side-gate", "remote exists but nothing re-runs the checks",
      {WFP: None}, ("warn", r"pushes to the remote are unverified"),
      clean_expect=("pass", r"workflow\(s\) re-run the checks"),
      clean_setup=add_remote, plant_setup=add_remote),
    C("ci.server-side-gate", "no git remote at all: na, not a bogus warn",
      {WFP: None}, ("na", r"no git remote"),
      clean_expect=("pass", r"re-run the checks"), clean_setup=add_remote),

    # ---- code quality / hygiene ----
    C("cq.function-length", "boundary: a 100-line function passes, 101 refuses",
      {"src/app.py": func(100)},
      ("warn", r"over 100 lines: src/app\.py:f\(\) \(101\)"),
      clean={"src/app.py": func(99)},
      clean_expect=("pass", r"no function over 100 lines")),
    C("cq.function-length", "gate: no .py files (function length is read from the AST)",
      {**NO_PY, "src/app.ts": "export const x = 1;\n"},
      ("na", r"no first-party \.py files tracked")),
    C("cq.file-size", "boundary: 500 lines pass, 501 lines refuse",
      {"src/app.py": lines(501)}, ("warn", r"over 500 lines: src/app\.py \(501\)"),
      clean={"src/app.py": lines(500)}, clean_expect=("pass", r"no oversized")),
    # The glob pair, one per size check: the SAME oversized file is a finding
    # outside the excluded tree and silent inside it. Previously excluded()
    # matched bare path components only, so the clean half of each case warned.
    C("cq.file-size", "a GLOB in the repo's own exclude list is read the way the ratchet reads it",
      {"pyproject.toml": GLOB_EXCL, "_plans/p1/probe.py": lines(501)},
      ("warn", r"over 500 lines: _plans/p1/probe\.py \(501\)"),
      clean={"pyproject.toml": GLOB_EXCL, "_plans/p1/_evidence/probe.py": lines(501)},
      clean_expect=("pass", r"no oversized")),
    C("cq.function-length", "the same glob keeps an excluded script out of the function walk",
      {"pyproject.toml": GLOB_EXCL, "_plans/p1/probe.py": func(100)},
      ("warn", r"over 100 lines: _plans/p1/probe\.py:f\(\) \(101\)"),
      clean={"pyproject.toml": GLOB_EXCL, "_plans/p1/_evidence/probe.py": func(100)},
      clean_expect=("pass", r"no function over 100 lines")),
    # The empty-input pair, one per size check. Both said "none found" over a
    # file list every one of whose members had been filtered out inside the loop
    # — a verdict measured over nothing (found by review).
    C("cq.file-size", "gate: the only tracked source is build output, so zero files were read",
      {**NO_PY, "build/app.py": lines(501)},
      ("na", r"no first-party source file was in scope")),
    C("cq.function-length", "gate: the only tracked .py is build output, so zero were parsed",
      {**NO_PY, "build/app.py": func(200)},
      ("na", r"no first-party \.py files tracked")),
    C("hyg.large-files", "a tracked file whose size cannot be read is not 'no large files'",
      {}, ("warn", r"could not be measured: dangling"), plant_setup=dangling_symlink),
    # A tracked workflow that will not OPEN used to reach every workflow check as
    # "", which has no pull_request_target and no unpinned action — two BLOCKING
    # security checks passing over bytes nothing read (found by review).
    C("sec.dangerous-workflow", "one of two tracked workflows cannot be read: the "
                                "one that was read still speaks, the other is named",
      {".github/workflows/other.yml": WF},
      ("warn", r"1 tracked workflow\(s\) could not be read and were NOT measured: "
               + re.escape(WFP)),
      clean={".github/workflows/other.yml": WF},
      clean_expect=("pass", r"no pull_request_target/injection patterns found"),
      plant_setup=hide_from_worktree(WFP)),
    # `pending`, NOT `na`: `na` is CLEAR to fix-routes, so routing "nothing could
    # be read" there made three blocking security checks vanish from the fix list
    # (found by the second review of this fix).
    C("sec.action-pinning", "gate: EVERY tracked workflow is unreadable, so nothing "
                            "was measured — not 'every action pinned', and not `na`",
      {}, ("pending", r"1 tracked workflow\(s\) could not be read"),
      plant_setup=hide_from_worktree(WFP)),
    C("sec.dep-vulns", "control: a tracked manifest that will not open must not take "
                       "the check off the board",
      {}, ("pending", r"run: pip-audit"), kind="control",
      clean_expect=("pending", r"run: pip-audit"),
      plant_setup=hide_from_worktree("requirements.txt")),
    C("hyg.tracked-junk", "a committed .venv (a DOT directory — the miss)",
      {".venv/lib/site.py": "x = 1\n"}, ("warn", r"tracked: \.venv/lib/site\.py")),
    C("hyg.tracked-junk", "node_modules NOT at the repo root (the startswith miss)",
      {"web/node_modules/a.js": "x\n"}, ("warn", r"web/node_modules/a\.js")),
    C("hyg.tracked-junk", "control: a tree the repo itself excludes is not junk",
      {"web/node_modules/a.js": "x\n",
       "pyproject.toml": '[tool.claude-quality]\nexclude = ["web"]\n'},
      ("pass", r"no venv/node_modules/pyc tracked"), kind="control",
      clean={"pyproject.toml": '[tool.claude-quality]\nexclude = ["web"]\n'}),
    C("hyg.large-files", "boundary: 5 MB passes, one byte more refuses",
      {"big.bin": "x" * 5_000_001}, ("warn", r"over 5 MB: big\.bin"),
      clean={"big.bin": "x" * 5_000_000}, clean_expect=("pass", r"no tracked file over 5 MB")),
    # `git ls-files` without -z C-quotes a non-ASCII name, and the quoted string
    # is not a path: the file read as "could not be measured".
    C("hyg.large-files", "a non-ASCII file name is still a path, and still measured",
      {"docs/“big”.bin": "x" * 5_000_001}, ("warn", r"over 5 MB: docs/“big”\.bin"),
      clean={"docs/“big”.bin": "x"}, clean_expect=("pass", r"no tracked file over 5 MB")),
    C("hyg.readme", "a one-line README",
      {"README.md": THIN_README}, ("warn", r"README missing, thin, or without")),
    C("hyg.readme", "boundary: long README with no runnable block still refuses",
      {"README.md": NO_CMD_README}, ("warn", r"README missing, thin, or without")),
    C("hyg.todo-density", "boundary: 100 markers pass, 101 refuse",
      many_todos(101), ("warn", r"101 TODO/FIXME markers"),
      clean=many_todos(100), clean_expect=("pass", r"100 TODO/FIXME markers")),
    C("hyg.activity", "last commit older than 90 days",
      {}, ("warn", r"last commit (9[1-9]|[1-9]\d\d+) days ago"),
      clean_expect=("pass", r"last commit [0-9] days ago"), plant_setup=age_commit),
    C("hyg.unmerged-work", "a branch that was never merged",
      {}, ("warn", r"unmerged branches: feature"), plant_setup=unmerged_branch),
    C("hyg.unmerged-work", "a leftover stash",
      {}, ("warn", r"stashes: 1"), plant_setup=leave_stash),
    C("hyg.notebook-outputs", "a notebook with committed outputs",
      {"nb.ipynb": NB_DIRTY}, ("warn", r"outputs committed in: nb\.ipynb"),
      clean={"nb.ipynb": NB_CLEAN}, clean_expect=("pass", r"outputs stripped")),
    C("hyg.notebook-outputs", "gate: no notebooks tracked",
      {}, ("na", r"no notebooks tracked"),
      clean={"nb.ipynb": NB_CLEAN}, clean_expect=("pass", r"outputs stripped")),

    # ---- AI readiness ----
    C("ai.agents-md", "no AGENTS.md and no CLAUDE.md",
      {"CLAUDE.md": None}, ("fail", r"no AGENTS\.md or CLAUDE\.md at repo root")),
    C("ai.agents-md", "boundary: an agents file with no runnable command",
      {"CLAUDE.md": "# demo\n\nBe careful around the billing code.\n"},
      ("warn", r"no runnable commands")),
    C("ai.verify-command", "neither a suite nor a lint gate",
      {"Makefile": None, ".pre-commit-config.yaml": None},
      ("fail", r"suite: none; lint gate: none found")),
    C("ai.verify-command", "boundary: a suite but no lint gate",
      {"Makefile": "test:\n\t@echo ok\n", ".pre-commit-config.yaml": None},
      ("warn", r"suite: make test; lint gate: none found")),
    C("ai.lockfiles", "an unpinned requirement",
      {"requirements.txt": "requests>=2.0\n"},
      ("warn", r"unpinned requirements: requirements\.txt")),
    C("ai.lockfiles", "no lockfile committed",
      {"uv.lock": None}, ("warn", r"no lockfile committed")),
    C("ai.lockfiles", "a lockfile with no manifest to lock",
      {"requirements.txt": None},
      ("warn", r"lockfile committed \(uv\.lock\) but no manifest detected")),
    C("ai.lockfiles", "a VCS requirement pinned to a branch, not a sha",
      {"requirements.txt": VCS_REQS}, ("warn", r"unpinned requirements"),
      clean={"requirements.txt": HASH_PINNED}, clean_expect=("pass", r"lockfile: uv\.lock")),

    # ---- gates on the expensive probes: pending vs na ----
    C("sec.dep-vulns", "gate: manifest removed",
      {"requirements.txt": None}, ("na", NO_MANIFEST),
      clean_expect=("pending", r"run: pip-audit")),
    C("hyg.dep-unused", "gate: manifest removed",
      {"requirements.txt": None}, ("na", NO_MANIFEST),
      clean_expect=("pending", r"deptry")),
    C("hyg.dep-freshness", "gate: manifest removed",
      {"requirements.txt": None}, ("na", NO_MANIFEST),
      clean_expect=("pending", r"pip list --outdated")),
    C("sec.supply-chain", "gate: manifest removed",
      {"requirements.txt": None}, ("na", NO_MANIFEST),
      clean_expect=("pending", r"collect\.py")),
    C("sec.dep-vulns", "prose is not a manifest (clean side: requirements-ci.txt is)",
      {"requirements.txt": None, "functional_requirements.txt": PROSE},
      ("na", NO_MANIFEST),
      clean={"requirements.txt": None, "requirements-ci.txt": "requests==2.32.3\n"},
      clean_expect=("pending", r"run: pip-audit")),
    C("cq.lint", "gate: no first-party source at all",
      dict(NO_PY), ("na", r"no first-party \.py/\.ts/\.js files tracked"),
      clean_expect=("pending", r"ruff check \.")),
    C("cq.lint", "control: a pure-TypeScript repo gets eslint, not na",
      {**NO_PY, "src/app.ts": "export const x = 1;\n"}, ("pending", r"npx eslint \."), kind="control",
      clean_expect=("pending", r"ruff check \.")),
    C("sec.sast", "gate: no first-party source at all",
      dict(NO_PY), ("na", r"no first-party \.py/\.ts/\.js files tracked"),
      clean_expect=("pending", r"semgrep scan")),
    C("cq.complexity", "gate: no .py files (ruff-only rules really are Python-only)",
      {**NO_PY, "src/app.ts": "export const x = 1;\n"},
      ("na", r"no first-party \.py files tracked"),
      clean_expect=("pending", r"C901")),
    C("cq.slop", "gate: no .py files",
      {**NO_PY, "src/app.ts": "export const x = 1;\n"},
      ("na", r"no first-party \.py files tracked"),
      clean_expect=("pending", r"F401,F841")),
    C("cq.ratchet", "gate: repo has no house ratchet script",
      {"scripts/quality/check_file_sizes.py": None}, ("na", r"no house ratchet"),
      clean_expect=("pending", r"check_file_sizes\.py")),
    # vendor_quality.py puts the ratchet in tools/ in every ADOPTING repo; looking
    # in scripts/quality/ alone read every adopter as "no house ratchet".
    C("cq.ratchet", "control: the ratchet VENDORED into tools/ is a house ratchet too",
      {"scripts/quality/check_file_sizes.py": None, "tools/check_file_sizes.py": "print('ok')\n"},
      ("pending", r"run: python3 tools/check_file_sizes\.py"), kind="control",
      clean_expect=("pending", r"run: python3 scripts/quality/check_file_sizes\.py")),
    C("test.suite", "gate: no test suite detected",
      {"Makefile": None}, ("na", r"no test suite detected"),
      clean_expect=("pending", r"run: make test")),
    C("test.runtime", "gate: no test suite detected",
      {"Makefile": None}, ("na", r"no test suite detected"),
      clean_expect=("pending", r"wall-clock seconds")),
    C("test.collection-cost", "gate: no test suite detected",
      {"Makefile": None}, ("na", r"no test suite detected"),
      clean_expect=("pending", r"--collect-only")),
    C("test.parallel-safety", "gate: no test suite detected",
      {"Makefile": None}, ("na", r"no test suite detected"),
      clean_expect=("pending", r"xdist")),
    C("sec.diff-review", "gate: no commits inside the review window",
      {}, ("na", r"no commits in the last 30 days"),
      clean_expect=("pending", r"scoped reviewer agents"), plant_setup=age_commit),
    C("sec.secrets-history", "always emitted, always pending — gitleaks does the refusing",
      {"src/app.py": "def main():\n    return 2\n"},
      ("pending", r"gitleaks git \."), clean_expect=("pending", r"gitleaks git \."), kind="control"),
]
# sec.secrets-history has no repo-shape gate at all: it is pending on every repo
# and the refusal lives in gitleaks (see TOOL_PROBES). The case above only proves
# it is emitted; judge() would call an unchanged result CANNOT FAIL, so the plant
# changes a file to keep the pair honest about what it does and does not prove.


def plant_pattern(case):
    """The pattern the PLANT side must match — CASES and TOOL_PROBES alike.

    Two tables spell it two ways (`plant_expect[1]` / `plant_pat`); one reader so
    the check below cannot be right about one table and silently blind to the other.
    """
    return case.get("plant_pat") or (case.get("plant_expect") or ("", ""))[1] or ""


def cases_that_cannot_fail(tables):
    """Ids whose plant pattern is EMPTY — probes that could never have failed.

    `re.search("", anything)` returns a match, so an empty plant pattern turns a
    case into a gate that waves through any message at all. judge() now refuses
    one at run time; this refuses it at IMPORT time, so such a case can never be
    committed. Written as a function rather than inline so it can itself be fed a
    known positive (test_probe_checks.py does) — a check nobody ever watched fail
    is the thing this whole corpus exists to catch.
    """
    return [c.get("id", "<case with no id>") for t in tables for c in t
            if not plant_pattern(c)]


_CANNOT_FAIL = cases_that_cannot_fail((CASES, TOOL_PROBES))
if _CANNOT_FAIL:
    raise ValueError("empty plant pattern — these probes could never fail: "
                     + ", ".join(_CANNOT_FAIL))

# NOT probed, and recorded as gaps rather than as `na` — see the transcript:
#   ci.wall-clock   needs an authenticated `gh` against real workflow runs
#   sec.diff-review / hyg.dep-freshness / test.runtime / test.collection-cost /
#   test.parallel-safety / sec.supply-chain / cq.ratchet / sec.vendor-pins —
#   their refusal is an operator judgement over numbers or agent output, or
#   needs a tool run that is not always available. Every one of them HAS its
#   repo-shape gate probed above; it is the refusal side that is unverified.


