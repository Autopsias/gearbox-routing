"""llm_review_policy: the canary flags only ever LOWER a block, never touch a prior, and are off by default."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parent))
import llm_review_gate as g  # noqa: E402
import llm_review_ledger as L  # noqa: E402
import llm_review_policy as P  # noqa: E402

F_DELTA = {"file": "fix.py", "line": 3, "severity": "high", "summary": "new bug in the fix"}
F_FIRST = {"file": "orig.py", "line": 9, "severity": "high", "summary": "bug in the original work",
           "repro": "pytest t.py::test_x fails"}
PRIOR = {"fid": "p1", "file": "orig.py", "severity": "high", "summary": "old", "status": "open"}


def recs(*findings, new_in="delta"):
    return [{"kind": "finding", "fid": L.fingerprint(f), "severity": f["severity"], "file": f["file"],
             "line": f["line"], "summary": f["summary"], "status": "open", "new_in": new_in} for f in findings]


def args(stop=0, repro=False):
    return NS(stop_after_round=stop, require_repro=repro)


def test_off_by_default_changes_nothing():
    led = mkled(4)
    r = recs(F_DELTA)
    assert P.apply(args(), led, [F_DELTA], r, True) == (True, r, [])
    assert P.prompt_extra(args()) == ""


def mkled(attempt, delta=("fix.py",), priors=()):
    return NS(attempt=attempt, priors=list(priors), delta=set(delta))


def test_stop_round_moves_only_new_fix_code_findings_after_the_round():
    r = recs(F_DELTA)
    assert P.apply(args(stop=2), mkled(2), [F_DELTA], r, True)[0] is True  # round 2: still blocks
    r = recs(F_DELTA)
    fail, out, moved = P.apply(args(stop=2), mkled(3), [F_DELTA], r, True)
    assert fail is False and out[0]["status"] == "noted" and out[0]["policy"] == "followup" and moved
    r = recs(F_FIRST, new_in="outside")  # original work still blocks after the stop round
    assert P.apply(args(stop=2), mkled(3), [F_FIRST], r, True)[0] is True


def test_a_reviewer_delta_tag_outside_the_gates_delta_still_blocks():
    r = recs(F_FIRST, new_in="delta")  # orig.py is NOT in the gate's own delta; the tag is the reviewer's
    fail, out, moved = P.apply(args(stop=2), mkled(3), [F_FIRST], r, True)
    assert fail is True and out[0]["status"] == "open" and moved == []


def test_open_prior_always_blocks():
    r = [dict(PRIOR, kind="finding", new_in="delta")]
    assert P.apply(args(stop=1, repro=True), mkled(5, priors=[PRIOR]), [], r, True)[0] is True


def test_require_repro_labels_but_never_clears_a_block():
    r = recs(F_DELTA, new_in="first")  # no repro: a reviewer told to omit it must not clear the block
    fail, out, moved = P.apply(args(repro=True), mkled(1), [F_DELTA], r, True)
    assert fail is True and out[0]["status"] == "open" and out[0]["policy"] == "unproven"
    r = recs(F_FIRST, new_in="first")
    assert P.apply(args(repro=True), mkled(1), [F_FIRST], r, True)[2] == []  # has repro
    assert "repro" in P.prompt_extra(args(repro=True)) and "STILL blocking" in P.report(moved)


def test_gate_cli_accepts_the_flags():
    a = g._parse_args(["--level", "medium", "--stop-after-round", "2", "--require-repro"])
    assert a.stop_after_round == 2 and a.require_repro is True
    assert g._parse_args(["--level", "low"]).stop_after_round == 0
