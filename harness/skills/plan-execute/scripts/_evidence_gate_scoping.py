"""ISO-03 evidence — a quality gate under plan isolation sees ONLY the plan, and
still goes RED for both shapes of violation §10.1 exists to catch.

    python3 skills/plan-execute/scripts/_evidence_gate_scoping.py \
        --out _plans/<slug>/_evidence/s07/gate-scoping.json

Builds a REAL git repository, isolates a REAL schema-7 plan into a REAL locked
worktree, then runs one gate three times through the two production functions
`verify._run_gate` is made of — `verify.gate_cwd` (which tree) and
`ship_state_io.run_deploy_argv` (the execution, `shell=False`, allow-listed env)
— and records each run's gate cwd, FILE-SET SIZE and EXIT CODE.

THREE RUNS, AND THE TWO REDS ARE THE POINT (neuter-once, both plants):

  1. GREEN, with the OUTER checkout deliberately dirty and carrying the very
     violation the gate looks for, in BOTH a tracked file and a new untracked
     one. A gate that could not see its own tree would be green here too — so
     this run is worth nothing without runs 2 and 3.
  2. RED, violation planted in a TRACKED, MODIFIED file inside the plan worktree.
  3. RED, violation planted in a file that exists ONLY as a NEW UNTRACKED file
     inside the plan worktree — the shape `git diff HEAD` cannot see at all, and
     therefore the shape a gate reading it passes with exit 0 and zero findings
     before `git add -A` commits it unexamined.

Runs 2 and 3 also record `git diff HEAD`'s own answer for the same tree, so the
"an untracked-only change is invisible to the forbidden input" claim is a
measurement in the artifact rather than a sentence in a docstring.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import gate_files as gf  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import shipping as shp  # noqa: E402
import verify as vfy  # noqa: E402
from worktree import git  # noqa: E402

SLUG = "evidence-plan"
SESSION = "s07"
GATE_ID = "scope-probe"
MARKER = "PLANTED_GATE_VIOLATION"
# Registered in the fixture's own `.claude/eval-gates.json` and resolved through
# `shipping.resolve_gate`, exactly as a real verify gate is — never hand-built
# here. A hand-built descriptor skips `_resolve_cwd`, and the answer then depends
# on this process's cwd rather than on the plan's isolation.
GATE_ENTRY = {"kind": "argv", "argv": [sys.executable, "tools/marker_gate.py"],
              "cwd": ".", "timeout": 120}

# The gate script. Deliberately tiny, and it derives its file set from the SAME
# `gate_files.changed_files` that `scripts/session-quality-gate.sh` calls in
# production — one definition, exercised here rather than re-implemented.
_GATE_SRC = '''\
import json, sys
sys.path.insert(0, {scripts!r})
import gate_files as gf
files = gf.changed_files(".")
if files is None:
    print("INDETERMINATE: cannot read the file set", file=sys.stderr)
    sys.exit(2)
hits = []
for f in files:
    try:
        if {marker!r} in open(f, encoding="utf-8", errors="replace").read():
            hits.append(f)
    except OSError:
        pass
print(json.dumps({{"files_examined": len(files), "files": files, "hits": hits}}))
sys.exit(1 if hits else 0)
'''


def _manifest():
    return {"plan_schema_version": 7,
            "sessions": [{"id": SESSION, "items": [], "dispatch": {"depends_on": []}}],
            "items": []}


def _build(tmp):
    """A repo with an origin, a schema-7 plan, and that plan already isolated."""
    origin = tmp / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text("A = 1\n")
    (root / "src" / "beta.py").write_text("B = 1\n")
    (root / "tools").mkdir()
    (root / "tools" / "marker_gate.py").write_text(
        _GATE_SRC.format(scripts=str(SCRIPTS), marker=MARKER))
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "eval-gates.json").write_text(json.dumps({GATE_ID: GATE_ENTRY}))
    plan_dir = root / "_plans" / SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / "manifest.json").write_text(json.dumps(_manifest()))
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "evidence@example.com"], root, check=True)
    git(["config", "user.name", "Evidence"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    return root, plan_dir, Path(state["path"])


def _diff_head(cwd):
    """The FORBIDDEN §10.1 input, recorded beside the real one for contrast."""
    rc, out, _ = git(["diff", "--name-only", "HEAD"], cwd, strip=False)
    return sorted(p.strip() for p in out.splitlines() if p.strip()) if rc == 0 else None


def _run(plan_dir, label, expect):
    """One gate run through the production path, recorded.

    `shipping.resolve_gate` -> `verify.gate_cwd` -> `ship_state_io.run_deploy_argv`
    is, verbatim, what `verify._run_gate` does for an argv gate that is not a dry
    run: `compute_gates` resolves the registry entry, `_run_gate` re-asks
    `gate_cwd` (idempotent — `already_under` short-circuits), and runs it.
    """
    g = shp.resolve_gate(plan_dir, GATE_ID, SESSION)
    cwd = vfy.gate_cwd(plan_dir, SESSION, g)
    res = ssio.run_deploy_argv(g["argv"], cwd=cwd, timeout=g["timeout"],
                               env_allowlist=g.get("env_allowlist", []), env_extra={})
    try:
        report = json.loads((res.get("stdout") or "").strip() or "{}")
    except ValueError:
        report = {}
    rc = res.get("returncode")
    return {
        "run": label,
        "expected": expect,
        "gate_cwd": str(cwd),
        "exit_code": rc,
        "verdict": {0: "GREEN", 1: "RED", 2: "INDETERMINATE"}.get(rc, "ERROR"),
        "matches_expectation": ({0: "GREEN", 1: "RED"}.get(rc) == expect),
        "files_examined": report.get("files_examined"),
        "files": report.get("files"),
        "violations": report.get("hits"),
        "git_diff_HEAD_would_have_seen": _diff_head(cwd),
        "stderr": (res.get("stderr") or "")[:400],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="where to write the evidence JSON")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="gate-scoping-") as td:
        root, plan_dir, tree = _build(Path(td))
        runs = []

        # ---- RUN 1: the outer checkout is dirty AND violating; the plan is not.
        # The plan worktree carries its own CLEAN work in both shapes, so this
        # green is "examined 2 of my own files and none of the neighbour's",
        # not §10.3's unexamined zero — which would be green for a gate pointed
        # at nothing at all and would prove exactly nothing about scoping.
        (root / "src" / "alpha.py").write_text(f"A = 'neighbour plan: {MARKER}'\n")
        (root / "src" / "neighbour_new.py").write_text(f"# {MARKER}\n")
        outer_set = gf.changed_files(root)
        (tree / "src" / "beta.py").write_text("B = 'my own clean edit'\n")
        (tree / "src" / "my_new_module.py").write_text("MINE = 1\n")
        runs.append(_run(plan_dir, "green-dirty-outer-checkout", "GREEN"))
        runs[-1]["neighbour_files_in_my_set"] = sorted(
            set(runs[-1]["files"] or []) & set(outer_set or []))
        git(["checkout", "--", "src/beta.py"], tree, check=True)
        (tree / "src" / "my_new_module.py").unlink()

        # ---- RUN 2: a TRACKED file, modified inside the plan worktree.
        (tree / "src" / "beta.py").write_text(f"B = 2  # {MARKER}\n")
        runs.append(_run(plan_dir, "red-planted-in-tracked-modified-file", "RED"))
        git(["checkout", "--", "src/beta.py"], tree, check=True)

        # ---- RUN 3: a file that exists ONLY as a NEW UNTRACKED file.
        (tree / "src" / "brand_new.py").write_text(f"# {MARKER}\ndef f():\n    return 1\n")
        runs.append(_run(plan_dir, "red-planted-in-new-untracked-file-only", "RED"))

        payload = {
            "artifact": "gate-scoping",
            "session": SESSION,
            "item": "ISO-03",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "generator": "skills/plan-execute/scripts/_evidence_gate_scoping.py",
            "contract": ["plan-isolation-contract.md §10.1/§10.3",
                         "parallel-group-contract.md V3-2 §2 M4"],
            "production_path": [
                "shipping.resolve_gate -> shipping._resolve_cwd (the registry entry)",
                "verify.gate_cwd -> plan_scope.plan_cwd -> plan_scope.plan_worktree",
                "ship_state_io.run_deploy_argv (shell=False, allow-listed env)",
                "gate_files.changed_files -> git status --porcelain=v1 -z -uall",
            ],
            "fixture": {
                "plan_slug": SLUG,
                "plan_branch": pwt.plan_branch(SLUG),
                "outer_checkout_dirty_files": outer_set,
                "outer_checkout_dirty_count": len(outer_set or []),
                "marker": MARKER,
            },
            "runs": runs,
            "all_expectations_met": all(r["matches_expectation"] for r in runs)
            and runs[0]["files_examined"] > 0            # §10.3: not a vacuous green
            and runs[0]["neighbour_files_in_my_set"] == [],
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"out": str(out),
                      "all_expectations_met": payload["all_expectations_met"],
                      "runs": [{k: r[k] for k in
                                ("run", "verdict", "exit_code", "files_examined")}
                               for r in runs]}, indent=2))
    return 0 if payload["all_expectations_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
