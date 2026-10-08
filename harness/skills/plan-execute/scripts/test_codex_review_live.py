"""The ONE thing a shim cannot prove: the real reviewer, on a real defect.

WHY THIS FILE EXISTS SEPARATELY. `test_codex_review_backend.py` stands a
recorded `codex` on PATH and hands it a canned answer, so every behavioural
claim it makes resolves to a script this repo wrote. It proves the WIRING and
nothing about the reviewer. This file spends a real `codex exec` to prove the
other half — that the gate, pointed at code with a real bug in it, comes back
saying so, and pointed at clean code comes back green.

IT ALREADY EARNED ITS KEEP. The first live run degraded on `codex is not logged
in` against a box that WAS logged in: the real `codex login status` answers on
STDERR while `--version` answers on stdout, and the availability probe read
stdout alone. Every shim in the suite next door printed to stdout, so all 24
were green over a cross-family gate that could never once run. See
`test_a_login_status_answered_on_STDERR_still_reads_as_LOGGED_IN`.

AND THE FIX OPENED A SECOND HOLE, so re-run this file whenever `availability()`
changes: reading stderr let a LOGGED-OUT box reach the substring test, and "not
logged in" CONTAINS "logged in", so it read as available. Pinned next door in
`test_codex_review_availability.py` — but only a live run proves the same
predicate still says YES to the real binary on a box that IS logged in.

OPT-IN, and never part of `make test`: it costs a real model call and 30-130s
per run (measured below), and the session gate already runs the default suite
in ~535s. `-rP` is what prints a PASSING test's output, which is the whole
point of running it:

    CODEX_LIVE=1 python3 -m pytest \
        skills/plan-execute/scripts/test_codex_review_live.py -q --no-header -rP

MEASURED 2026-08-25, codex-cli 0.147.0 / gpt-5.6-sol, over a 1-file 8-line
diff, over repeated runs — the numbers s02 sizes the registry timeouts from.
Each is the whole `llm_review_gate.main` call, egress scan included:

    level low    (effort medium)  planted 30.5-61.4s   control 30.2-130.9s
    level medium (effort high)    planted 40.3-131.4s  control 50.6s
    level high   (effort xhigh)   planted 40.7s

TWO THINGS THE SPREAD SAYS. Most runs landed in 30-51s; the five slow ones
(61, 81, 121, 131, 131s) all fell in windows when unrelated 8-way
parallel test suites were running on this box — checked with `ps` mid-run, not
inferred. So the honest figure is ~30-50s on a quiet box and ~130s observed
under load, and a timeout sized from the quiet number would fail whenever the
machine is busy. And the EFFORT RUNG barely moves it: xhigh measured the same
as medium, so per-level timeouts want the same budget rather than a ladder.
Size for the loaded tail (the 900s used here is ~7x the slowest observed) and
let the supervisor's idle-kill — `timeout // 3` with no output growth — be what
catches a real stall.
"""
import os
import subprocess
import sys
import time

import pytest

import llm_review_gate as g
from test_codex_review_backend import _plan

pytestmark = pytest.mark.skipif(
    os.environ.get("CODEX_LIVE") != "1",
    reason="live `codex exec` smoke — opt in with CODEX_LIVE=1")

#: The clean version of the reviewed file: a rule evaluator that parses instead
#: of executing.
_SAFE = '''import ast


def run_rule(expr, row):
    """Evaluate a user-supplied filter rule against one row."""
    tree = ast.parse(expr, mode="eval")
    if not isinstance(tree.body, ast.Compare):
        raise ValueError("only comparisons are allowed")
    return bool(ast.literal_eval(tree.body.comparators[0]) == row.get("v"))
'''

#: THE PLANTED DEFECT — arbitrary code execution from an HTTP parameter. It is
#: deliberately unambiguous: the gate blocks on severity (`high` and up), so a
#: defect a reviewer might reasonably call `medium` would make this test a coin
#: flip about severity rather than a check on whether the bug was FOUND.
_PLANTED = '''def run_rule(expr, row):
    """Evaluate a user-supplied filter rule against one row."""
    return bool(eval(expr, {"row": row}))
'''

#: A clean change of the same size, for the control: new code, no defect.
_CLEAN_CHANGE = _SAFE + '''

def rule_count(exprs):
    """How many rules were supplied."""
    return len(list(exprs))
'''

#: Committed beside it, so `expr` is visibly attacker-controlled rather than
#: something a reviewer has to assume.
_CALLER = '''from rules import run_rule


def handle(request, rows):
    """`request.args["rule"]` is whatever the HTTP client sent."""
    expr = request.args["rule"]
    return [r for r in rows if run_rule(expr, r)]
'''


def _live_tree(tmp_path, body):
    """A real git work tree whose UNCOMMITTED change is `body`."""
    tree = tmp_path / "tree"
    tree.mkdir()

    def run(*a):
        subprocess.run(list(a), cwd=tree, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tree / "rules.py").write_text(_SAFE)
    (tree / "api.py").write_text(_CALLER)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (tree / "rules.py").write_text(body)
    return tree


def _live_gate(tmp_path, body, level):
    """-> the gate's exit code, having printed its elapsed seconds.

    The REAL binary: no shim, no PATH games, and the real egress guard — this is
    the whole path or it is not evidence."""
    tree, plan = _live_tree(tmp_path, body), _plan(tmp_path)
    t0 = time.time()
    rc = g.main(["--level", level, "--reviewer", "codex", "--cwd", str(tree),
                 "--plan-dir", str(plan), "--session", "s01", "--timeout", "900"])
    print(f"\n=== LIVE level={level} rc={rc} elapsed={time.time() - t0:.1f}s ===")
    return rc


@pytest.mark.parametrize("level", ["low", "medium"])
def test_the_REAL_codex_FAILS_the_gate_on_a_planted_defect(tmp_path, level, capsys):
    """The known positive, spent on the real reviewer.

    Asserted on the gate's own three-valued exit code, never on prose: INDETERMINATE
    here would mean the run produced nothing usable, which is a transport result
    and not a review, and the difference is the whole point of the code under test.
    """
    rc = _live_gate(tmp_path, _PLANTED, level)
    out = capsys.readouterr().out
    print(out)                       # the transcript IS the evidence artifact
    assert rc == g.FINDINGS, out
    assert "VERIFIER: cross_family" in out, out
    assert "rules.py" in out, "the finding must name the file the defect is in"
    # Line 1 names the BINARY that ran -- the one claim in this file that a shim
    # could have faked, and the reason the version is probed rather than assumed.
    assert out.splitlines()[0].startswith(
        "[llm-review-gate] reviewer: family=codex version=codex-cli"), out


def test_the_REAL_codex_PASSES_the_gate_on_a_clean_change(tmp_path, capsys):
    """The control. Without it, `exit 1` above is equally satisfied by a reviewer
    that fails everything — the same reason a neuter probe pairs with a positive."""
    rc = _live_gate(tmp_path, _CLEAN_CHANGE, "low")
    out = capsys.readouterr().out
    print(out)
    assert rc == g.PASS, out
    assert "VERIFIER: cross_family" in out, out
    assert "DEGRADED_FROM" not in out, "a degrade is not a pass"


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q", "--no-header", "-rP"]))
