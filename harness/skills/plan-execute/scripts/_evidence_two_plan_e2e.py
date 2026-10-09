"""E2E-01 evidence — the committed record of the two-plan end-to-end proof.

    python3 skills/plan-execute/scripts/_evidence_two_plan_e2e.py \
        [--out <path>] [--neuter <path>]

A DELIBERATE, EXPLICIT step, never a test side effect: a suite that rewrote the
artifact it is judged by could go green and green-looking at the same time. Two
things go in the record and neither is typed by hand —

  * the REAL `pytest test_two_plan_e2e.py -v` run: its command, exit code and
    collected/passed/failed/skipped counts. A run that is entirely SKIPS is
    REFUSED here rather than written out for a reader to notice, because on a
    host without git every test in that file skips and the file would otherwise
    read exactly like a proof;
  * one two-plan run driven end to end through the same fixture the tests use,
    with every sha it produced — the base, both plan branch heads, both land
    merges, origin/main before and after, and the outer checkout's content hash
    on both sides.

The negative control is READ from the neuter-once transcript rather than
described: `failed_tests` is parsed out of that file, so "the suite can fail" is
a quotation from a run, not a claim.

Split from the test module for the reason `_land_fixture.py` states: builders
and proofs are two things, and this repo's 500-LOC ratchet is right about it.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import test_two_plan_e2e as e2e  # noqa: E402
from worktree import git  # noqa: E402

TEST_FILE = "test_two_plan_e2e.py"


def _pytest_counts(text):
    """collected / passed / failed / skipped, read off pytest's own output."""
    import re
    counts = {"collected": None, "passed": 0, "failed": 0, "skipped": 0, "error": 0}
    m = re.search(r"collected (\d+) items?", text)
    if m:
        counts["collected"] = int(m.group(1))
    for key in ("passed", "failed", "skipped", "error"):
        m = re.search(rf"(\d+) {key}s?\b", text)
        if m:
            counts[key] = int(m.group(1))
    return counts


def _fixture_shas(tmp_path):
    """ONE two-plan run, end to end, with every sha it produced recorded."""
    fx = e2e._build(tmp_path)
    a, b = fx["plans"]["plan-a"], fx["plans"]["plan-b"]
    e2e._begin(a)
    e2e._begin(b)
    e2e._session_writes(a, {"src/plan_a.py": "A = 1\n"})
    e2e._session_writes(b, {"src/plan_b.py": "B = 1\n"})
    e2e._ship_commit(a)
    e2e._ship_commit(b)
    for plan_dir in (a, b):
        e2e._close_done(plan_dir)
    shas = {"base": fx["base"],
            "origin_main_before": git(["rev-parse", "refs/heads/main"],
                                      fx["origin"], check=True)[1],
            "outer_tree_hash_before": e2e._tree_hash(fx["root"]),
            "plan_branch_heads": {slug: git(["rev-parse", f"plan/{slug}"],
                                            fx["root"], check=True)[1]
                                  for slug in ("plan-a", "plan-b")},
            "branch_files": {slug: e2e._branch_files(fx["root"], fx["base"], f"plan/{slug}")
                             for slug in ("plan-a", "plan-b")}}
    shas["land"] = {slug: e2e._ack_and_land(fx["plans"][slug])["merge_sha"]
                    for slug in ("plan-a", "plan-b")}
    shas["origin_main_after"] = git(["rev-parse", "refs/heads/main"],
                                    fx["origin"], check=True)[1]
    shas["outer_tree_hash_after"] = e2e._tree_hash(fx["root"])
    shas["origin_main_files"] = git(["ls-tree", "-r", "--name-only",
                                     shas["origin_main_after"]], fx["origin"],
                                    check=True)[1].split()
    shas["leftovers"] = {
        "plan_branches_local": git(["for-each-ref", "--format=%(refname)",
                                    "refs/heads/plan/"], fx["root"], check=True)[1].split(),
        "plan_branches_origin": git(["for-each-ref", "--format=%(refname)",
                                     "refs/heads/plan/"], fx["origin"],
                                    check=True)[1].split(),
        "worktrees": git(["worktree", "list"], fx["root"], check=True)[1].splitlines()}
    return shas


def generate_evidence(out_path=None, neuter_path=None):
    import tempfile
    root = SCRIPTS.parent.parent.parent
    evidence = root / "_plans" / "example-isolation-plan-2026-08-20" / "_evidence" / "s10"
    out_path = Path(out_path) if out_path else evidence / "two-plan-e2e.json"
    neuter_path = Path(neuter_path) if neuter_path else evidence / "neuter-once.txt"
    argv = [sys.executable, "-m", "pytest", TEST_FILE, "-v"]
    proc = subprocess.run(argv, cwd=str(SCRIPTS), capture_output=True, text=True,
                          timeout=3600)
    counts = _pytest_counts(proc.stdout)
    # A run that is entirely SKIPS is not a proof. Refused here rather than
    # written out and left for a reader to notice.
    if counts["passed"] < 1 or counts["failed"] or counts["error"]:
        raise SystemExit(f"refusing to write evidence: {counts}\n{proc.stdout[-4000:]}")
    neuter = neuter_path.read_text() if neuter_path.is_file() else ""
    neuter_failed = sorted({ln.split("::")[1].split()[0] for ln in neuter.splitlines()
                            if ln.startswith("FAILED ") and "::" in ln})
    with tempfile.TemporaryDirectory() as td:
        shas = _fixture_shas(Path(td))
    record = {
        "item": "E2E-01",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_head": git(["rev-parse", "HEAD"], root, check=True)[1],
        "command": " ".join(argv),
        "cwd": str(SCRIPTS),
        "exit_code": proc.returncode,
        "counts": counts,
        "tests": sorted(ln.split("::")[1].split()[0] for ln in proc.stdout.splitlines()
                        if "::" in ln and (" PASSED" in ln or " SKIPPED" in ln)),
        "host_git_supported": e2e._GIT_OK,
        "fixture_shas": shas,
        "negative_control": {
            "artifact": str(neuter_path.relative_to(root)) if neuter_path.is_file()
                        else str(neuter_path),
            "present": neuter_path.is_file(),
            "sabotage": "plan-a's session ALSO writes plan-b's declared path "
                        "(src/plan_b.py) into the SHARED outer checkout",
            "failed_tests": neuter_failed,
            "result": "suite FAILED" if neuter_failed else "NOT RECORDED"},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":                            # pragma: no cover
    args = sys.argv[1:]
    out = args[args.index("--out") + 1] if "--out" in args else None
    neu = args[args.index("--neuter") + 1] if "--neuter" in args else None
    rec = generate_evidence(out, neu)
    print(json.dumps({"counts": rec["counts"], "exit_code": rec["exit_code"],
                      "negative_control": rec["negative_control"]["result"]}, indent=2))
