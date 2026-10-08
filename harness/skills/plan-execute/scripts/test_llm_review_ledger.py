"""The findings ledger: a rework VERIFIES fixes instead of RE-SAMPLING.

Every test here fails against the pre-ledger gate, where attempt 2 was a fresh
sample of the whole surface with no memory of attempt 1.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import llm_review_gate as g  # noqa: E402
import llm_review_ledger as L  # noqa: E402
import rework  # noqa: E402


# ---------- stable ids ----------

def test_fingerprint_ignores_line_case_and_punctuation_but_not_file():
    a = L.fingerprint({"file": "x.py", "line": 10, "summary": "Unguarded read of c[id]."})
    b = L.fingerprint({"file": "x.py", "line": 99, "summary": "unguarded read of c id"})
    c = L.fingerprint({"file": "y.py", "line": 10, "summary": "Unguarded read of c[id]."})
    assert a == b and a != c


# ---------- the fix delta is content, not git ----------

def test_first_attempt_has_no_prior_so_every_file_is_delta(tmp_path):
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "b.py").write_text("2")
    h = L.surface_hashes(str(tmp_path), ["a.py", "b.py"])
    assert L.fix_delta([], h) == {"a.py", "b.py"}


def test_delta_is_only_what_changed_since_the_last_surface(tmp_path):
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "b.py").write_text("2")
    h1 = L.surface_hashes(str(tmp_path), ["a.py", "b.py"])
    recs = [{"kind": "surface", "attempt": 1, "hashes": h1}]
    (tmp_path / "a.py").write_text("1 changed")
    (tmp_path / "c.py").write_text("new")
    h2 = L.surface_hashes(str(tmp_path), ["a.py", "b.py", "c.py"])
    assert L.fix_delta(recs, h2) == {"a.py", "c.py"}


def test_latest_status_per_finding_wins():
    recs = [{"kind": "finding", "attempt": 1, "fid": "f1", "status": "open"},
            {"kind": "finding", "attempt": 1, "fid": "f2", "status": "open"},
            {"kind": "finding", "attempt": 2, "fid": "f1", "status": "fixed"}]
    assert [p["fid"] for p in L.open_priors(recs)] == ["f2"]


# ---------- the policy, as a table ----------

class _Ctx:
    def __init__(self, attempt, priors=(), delta=()):
        self.attempt, self.priors, self.delta = attempt, list(priors), set(delta)
        self.plan_dir = self.session = "x"
        self.hashes = {}


# THE POLICY TABLE. Changed 2026-08-22: the severity floor replaced "location
# decides", because location-based blocking never terminated -- every fix is new
# code, so every fix produced new in-delta findings that blocked again. Measured
# over 28 plans: 36 of the 56 findings that blocked a rework were medium or low.
@pytest.mark.parametrize("attempt,finding,fail", [
    (1, {"file": "a.py", "severity": "low", "summary": "s"}, False),           # floor applies on round 1
    (1, {"file": "a.py", "severity": "high", "summary": "s"}, True),
    (2, {"file": "a.py", "severity": "low", "summary": "s", "new_in": "delta"}, False),
    (2, {"file": "a.py", "severity": "medium", "summary": "s"}, False),        # in delta, below floor
    (2, {"file": "a.py", "severity": "high", "summary": "s"}, True),           # in delta, at floor
    (2, {"file": "z.py", "severity": "medium", "summary": "s"}, False),        # outside, below floor
    (2, {"file": "z.py", "severity": "high", "summary": "s"}, True),           # outside, at floor
    (2, {"file": "z.py", "severity": "critical", "summary": "s"}, True),
])
def test_judge_policy(attempt, finding, fail):
    got, counts, recs = L.judge(_Ctx(attempt, delta={"a.py"}), [finding])
    assert got is fail, (counts, recs)


def test_a_prior_the_reviewer_marks_fixed_does_not_block():
    prior = {"kind": "finding", "fid": "abc", "file": "a.py", "summary": "old", "status": "open"}
    fail, counts, recs = L.judge(_Ctx(2, priors=[prior]),
                                 [{"prior_id": "abc", "prior": "fixed", "summary": "guard added"}])
    assert not fail and counts["prior_fixed"] == 1 and recs[0]["status"] == "fixed"


def test_a_prior_the_reviewer_OMITS_is_open_silence_is_not_a_fix():
    prior = {"kind": "finding", "fid": "abc", "file": "a.py", "summary": "old", "status": "open"}
    fail, counts, recs = L.judge(_Ctx(2, priors=[prior]), [])
    assert fail and counts["prior_open"] == 1 and "not addressed" in recs[0]["evidence"]


def test_no_plan_context_keeps_the_old_rule():
    assert L.judge(None, [{"file": "a.py"}]) == (True, {}, [])
    assert L.judge(None, []) == (False, {}, [])


def test_classify_does_not_count_a_fixed_prior_as_a_finding():
    arr = json.dumps([{"prior_id": "x", "prior": "fixed"}, {"file": "a.py", "summary": "new"}])
    assert g.classify("```json\n" + arr + "\n```")[:2] == ("findings", 1)
    arr = json.dumps([{"prior_id": "x", "prior": "fixed"}])
    assert g.classify("```json\n" + arr + "\n```")[:2] == ("empty", 0)


def test_prompt_section_names_every_prior_id_and_delta_file():
    priors = [{"fid": "id1", "file": "a.py", "line": 3, "severity": "high", "summary": "bad"}]
    sec = L.prompt_section(2, priors, {"a.py", "b.py"}, unchanged_n=7)
    assert "id1" in sec and "a.py" in sec and "b.py" in sec and "7" in sec
    assert "OMIT is treated as OPEN" in sec
    assert L.prompt_section(1, [], set(), 0) == ""


# ---------- the contract between the gate's line and rework's reader ----------

def test_rework_parses_the_exact_convergence_line_the_gate_prints():
    counts = dict(attempt=3, prior_fixed=2, prior_open=0, new_in_delta=0, new_outside=1, noted=1)
    line = L.convergence_line(counts)
    m = rework._CONVERGENCE.search(line)
    assert m and m.group("open") == "0" and m.group("outside") == "1" and m.group("delta") == "0"
    assert "SAMPLE" in line.upper() and "split the session" in line


# ---------- end to end: three attempts through main() ----------

def _plan_and_tree(tmp_path):
    plan = tmp_path / "plan"
    (plan / "_verify_state").mkdir(parents=True)
    # A real plan dir always has its journal. Without it the gate now REFUSES
    # rather than silently reviewing `git diff HEAD`, which for a session that
    # committed is not its work at all (llm_review_surface, 2026-08-20).
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-08-20T00:00:00+00:00"}\n')
    tree = tmp_path / "tree"
    tree.mkdir()
    def run(*a):
        return subprocess.run(list(a), cwd=tree, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tree / "a.py").write_text("x = 1\n")
    (tree / "b.py").write_text("y = 1\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (tree / "a.py").write_text("x = 2\n")
    (tree / "b.py").write_text("y = 2\n")
    return plan, tree


def _stub(tmp_path, answers):
    """A fake `claude` that returns answers[attempt] and attests 2 files."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "n").write_text("0")
    script = f'''#!/usr/bin/env python3
import json, pathlib, sys
n = pathlib.Path({str(tmp_path / "n")!r}); i = int(n.read_text()); n.write_text(str(i + 1))
answers = {json.dumps(answers)}
body = "REVIEWED_FILES: 2\\n\\n```json\\n" + json.dumps(answers[i]) + "\\n```"
print(json.dumps({{"type": "result", "is_error": False, "result": body}}))
'''
    stub = bindir / "claude"
    stub.write_text(script)
    stub.chmod(0o755)
    return bindir


def test_three_attempts_converge_and_the_ledger_records_it(tmp_path, monkeypatch, capsys):
    plan, tree = _plan_and_tree(tmp_path)
    f_a = {"file": "a.py", "line": 1, "severity": "high", "summary": "a is unguarded"}
    # HIGH, so it is a BLOCKING prior: this test is about a prior surviving to
    # the next round, and after the severity floor (2026-08-22) only a finding at
    # the floor becomes one. The advisory path is covered by the medium below.
    f_b = {"file": "b.py", "line": 1, "severity": "high", "summary": "b is unguarded"}
    id_a, id_b = L.fingerprint(f_a), L.fingerprint(f_b)
    answers = [
        [f_a, f_b],                                                   # attempt 1: two findings
        [{"prior_id": id_a, "prior": "fixed", "summary": "guarded"},  # attempt 2: a fixed,
         {"prior_id": id_b, "prior": "open", "summary": "still bare"},#            b open,
         {"file": "b.py", "line": 9, "severity": "medium",            #            + new medium
          "summary": "b has a second bare read", "new_in": "outside"}],#            outside delta
        [{"prior_id": id_b, "prior": "fixed", "summary": "guarded now"},   # attempt 3: all fixed,
         {"prior_id": L.fingerprint({"file": "b.py", "summary": "b has a second bare read"}),
          "prior": "open", "summary": "still there, minor"}],              # one medium outside -> ?
    ]
    bindir = _stub(tmp_path, answers)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    argv = ["--level", "low", "--cwd", str(tree), "--plan-dir", str(plan), "--session", "s01",
            "--timeout", "30"]

    assert g.main(argv) == g.FINDINGS                  # attempt 1: blocks
    out1 = capsys.readouterr().out
    assert "convergence: attempt=1" in out1 and "new_in_delta=2" in out1

    (tree / "a.py").write_text("x = 3  # fixed\n")     # the agent "fixes" a.py only
    assert g.main(argv) == g.FINDINGS                  # attempt 2: b still open -> blocks
    out2 = capsys.readouterr().out
    assert "prior_fixed=1 prior_open=1" in out2 and "new_outside=1 noted=1" in out2
    # END-TO-END PROOF that the delta is the SURFACE and not just prompt advice:
    # a.py was reviewed on attempt 1 and is unchanged, so attempt 2 must not be
    # handed it again. Asserted on the real gate's own output, not the unit.
    assert "surface NARROWED to the fix delta" in out2, out2
    # b.py rides along even though the fix never touched it: it holds an OPEN
    # prior the reviewer must mark fixed or still-open, so 2 of 2 here. The
    # narrowing shows itself on a surface with a file that is neither.
    assert "surface NARROWED" in out2, out2

    (tree / "b.py").write_text("y = 3  # fixed\n")
    # attempt 3: the only thing left is the MEDIUM finding from attempt 2, which the
    # stub marks `open` -- it was recorded `noted` (outside-delta, medium), so it is
    # NOT a prior the reviewer must clear, and the gate converges.
    assert g.main(argv) == g.PASS
    out3 = capsys.readouterr().out
    assert "convergence: attempt=3" in out3 and "prior_open=0" in out3

    # THE LEDGER KEY IS `<session>.<level>.<reviewer>`, not the bare session id.
    # This call was left on `"s01"` when the LEVEL joined the key (969feba), so it
    # loaded a file that never existed and the three assertions below ran against
    # an EMPTY list -- a check pointed at nothing. Failing at HEAD before the
    # REVIEWER joined the key too; fixed here rather than carried.
    recs = L.load(str(plan), "s01.low.claude")
    assert {r["kind"] for r in recs} == {"finding", "surface"}
    assert L.last_attempt(recs) == 3
    assert L.open_priors(recs) == [], [r for r in recs if r.get("status") == "open"]


def test_a_prior_OUTSIDE_this_attempts_surface_cannot_block_forever():
    """`review_scope` narrows the surface mid-session, and every prior raised
    before the narrowing may now sit in a file the reviewer is never shown. It
    cannot mark those fixed, so "omission is not a fix" made the gate unpassable
    for the rest of the session -- no amount of fixing could clear it. Carried and
    counted, never silently dropped (found by the isolation session, 2026-08-20)."""
    prior = {"kind": "finding", "fid": "abc", "file": "skills/repo-health/x.py",
             "summary": "old", "status": "open"}
    ctx = _Ctx(2, priors=[prior])
    ctx.hashes = {"skills/plan-execute/y.py": "h1"}      # a surface without the prior's file
    fail, counts, recs = L.judge(ctx, [])
    assert not fail, recs                                # it must not block
    assert counts["prior_unverifiable"] == 1 and counts["prior_open"] == 0
    assert recs[0]["status"] == "unverifiable" and "outside" in recs[0]["evidence"]
    assert "prior_unverifiable=1" in L.convergence_line(counts)   # visible, not silent
    # and it is still carried, so it returns the moment the file is in scope again
    assert [p["fid"] for p in L.open_priors(recs)] == ["abc"]


def test_a_prior_INSIDE_the_surface_that_is_omitted_still_blocks():
    """The control. Narrowing the surface must not become a way to drop a finding
    the reviewer simply did not answer."""
    prior = {"kind": "finding", "fid": "abc", "file": "a.py", "summary": "old", "status": "open"}
    ctx = _Ctx(2, priors=[prior])
    ctx.hashes = {"a.py": "h1"}
    fail, counts, _ = L.judge(ctx, [])
    assert fail and counts["prior_open"] == 1 and counts["prior_unverifiable"] == 0


def test_a_finding_on_a_ROOT_DOTFILE_is_not_demoted_out_of_the_delta():
    """`lstrip("./")` strips a character SET, so `.complexity-exceptions` came back
    as `complexity-exceptions`, never matched the delta, and a real medium finding
    on it was classed `outside` and demoted to non-blocking `noted`. Fifth instance
    of this exact call in this repo (2026-08-20)."""
    ctx = _Ctx(2, delta={".complexity-exceptions"})
    fail, counts, recs = L.judge(
        ctx, [{"file": ".complexity-exceptions", "severity": "medium", "summary": "s"}])
    # The bug was CLASSIFICATION, so that is what this asserts. Whether a medium
    # blocks is the separate severity-floor decision (2026-08-22) and is not the
    # subject here -- a high one on the same path still fails the gate, below.
    assert recs[0]["new_in"] == "delta", recs
    assert L.judge(ctx, [{"file": ".complexity-exceptions", "severity": "high",
                          "summary": "s"}])[0], "a root dotfile must still be blockable"
    assert counts["new_in_delta"] == 1, "counted as delta, not outside -- the whole point"
    # control: the same path with a ./ prefix normalises to the same finding
    assert L.fingerprint({"file": "./.complexity-exceptions", "summary": "s"}) == \
           L.fingerprint({"file": ".complexity-exceptions", "summary": "s"})


def test_a_reviewer_cannot_tag_its_own_finding_out_of_the_delta():
    """The reviewer's `new_in` tag used to be read BEFORE the delta membership the
    ledger computes itself, so a finding in a file the fix just touched could be
    demoted by the reviewer's own mislabel. Delta membership is local ground truth
    (content hashes, computed in fix_delta); the tag is only consulted for a path
    we have nothing to check it against. Sixth instance of the demoted-out-of-the-
    delta family in this repo (2026-08-21).

    Asserted on CLASSIFICATION, not on `fail`: after the severity floor
    (2026-08-22) severity decides blocking and location does not, so a mislabelled
    HIGH is the case that still has to block."""
    ctx = _Ctx(2, delta={"a.py"})
    fail, counts, recs = L.judge(
        ctx, [{"file": "a.py", "severity": "high", "summary": "s", "new_in": "outside"}])
    assert fail, recs                                  # the mislabel does not save it
    assert counts["new_in_delta"] == 1
    assert recs[0]["new_in"] == "delta"
    # CONTROL: genuinely outside the delta, the tag is honoured -- the fix must not
    # turn every finding into a blocker just because a reviewer named a file.
    _, counts2, recs2 = L.judge(
        ctx, [{"file": "z.py", "severity": "medium", "summary": "s", "new_in": "outside"}])
    assert recs2[0]["new_in"] == "outside" and counts2["noted"] == 1


# ---------- the severity floor (2026-08-22) ----------
# Measured across 28 plans: of the findings that BLOCKED a rework, 36 of 56 were
# medium or low. Each one spent a full dispatch, and the fix for it introduced
# new code that the next round then found new findings in. The floor is the
# termination property those rounds never had.

def _ctx(tmp_path, plan="p", session="s01", files=("a.py",), floor=None):
    d = tmp_path / plan
    (d / "_verify_state").mkdir(parents=True, exist_ok=True)
    for f in files:
        (tmp_path / f).write_text("x")
    return L.context(str(d), session, str(tmp_path), list(files), floor)


def test_a_medium_finding_is_recorded_but_does_not_block(tmp_path):
    ctx = _ctx(tmp_path)
    fail, counts, recs = L.judge(ctx, [
        {"file": "a.py", "line": 1, "severity": "medium", "summary": "m"}])
    assert fail is False, "a medium finding must not spend another dispatch"
    assert counts["noted"] == 1
    assert recs[0]["status"] == "noted", "and it must still be RECORDED, not dropped"


def test_a_high_finding_still_blocks(tmp_path):
    fail, _, recs = L.judge(_ctx(tmp_path), [
        {"file": "a.py", "line": 1, "severity": "high", "summary": "h"}])
    assert fail is True and recs[0]["status"] == "open"


def test_the_floor_applies_on_attempt_one_too(tmp_path):
    """A low finding on round 1 starts the same loop as one on round 5."""
    ctx = _ctx(tmp_path)
    assert ctx.attempt == 1
    fail, _, recs = L.judge(ctx, [
        {"file": "a.py", "line": 1, "severity": "low", "summary": "l"}])
    assert fail is False and recs[0]["new_in"] == "first"


def test_a_stricter_floor_is_honoured(tmp_path):
    fail, _, _ = L.judge(_ctx(tmp_path, floor={"high", "medium"}), [
        {"file": "a.py", "line": 1, "severity": "medium", "summary": "m"}])
    assert fail is True, "--blocking-severity high,medium must still block a medium"


def test_parse_floor_reads_both_separators_and_defaults_to_none():
    assert L.parse_floor("high,medium") == {"high", "medium"}
    assert L.parse_floor("High:Low") == {"high", "low"}
    assert L.parse_floor("") is None and L.parse_floor(None) is None


def test_a_legacy_open_medium_prior_stops_blocking(tmp_path):
    """A ledger written before the floor must not pin a live session to a rule
    it was never judged under -- but the record stays in the file."""
    recs = [{"kind": "finding", "attempt": 1, "fid": "abc", "file": "a.py",
             "severity": "medium", "summary": "m", "status": "open"}]
    assert L.open_priors(recs) == []
    assert L.open_priors(recs, {"high", "medium"})[0]["fid"] == "abc"


def test_the_reviewers_own_new_in_cannot_force_a_block(tmp_path):
    """`new_in` is bookkeeping; the delta is a hash comparison the gate did."""
    fail, _, recs = L.judge(_ctx(tmp_path), [
        {"file": "a.py", "line": 1, "severity": "low", "summary": "l",
         "new_in": "delta"}])
    assert fail is False


# ---------- the delta IS the surface, not advice about it ----------

def test_attempt_one_narrows_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    focus, changed, new, note = L.narrow_to_delta(ctx, ["a.py"], ("b.py",))
    assert focus is None and changed == ["a.py"] and new == ("b.py",) and note == ""


def test_a_rework_reviews_only_the_files_the_fix_touched(tmp_path):
    d = tmp_path / "p"
    (d / "_verify_state").mkdir(parents=True)
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "b.py").write_text("2")
    first = L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py"])
    L.record(first, [])
    (tmp_path / "b.py").write_text("CHANGED")          # the fix touched b only
    second = L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py"])
    focus, changed, new, note = L.narrow_to_delta(second, ["a.py", "b.py"], ())
    assert focus == {"b.py"}, "a.py was reviewed on attempt 1 and must not be re-sampled"
    assert changed == ["b.py"]
    assert "NARROWED" in note


def test_an_unchanged_surface_after_a_clean_review_keeps_its_pass(tmp_path):
    """2026-10-06: this used to assert INDETERMINATE, which parked a land re-gate
    forever after main moved. A recorded review of these exact bytes that left
    nothing blocking open is a pass; a DELETED surface file is not "unchanged"."""
    d = tmp_path / "p"
    (d / "_verify_state").mkdir(parents=True)
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "b.py").write_text("1")
    L.record(L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py"]), [])
    second = L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py"])
    focus, _, _, note = L.narrow_to_delta(second, ["a.py", "b.py"], ())
    assert focus == set(), "an empty delta must be distinguishable from 'do not narrow'"
    assert L.reviewed_unchanged(second) and "PASSED" in note

    (tmp_path / "b.py").unlink()
    gone = L.context(str(d), "s01", str(tmp_path), ["a.py"])
    focus, _, _, note = L.narrow_to_delta(gone, ["a.py"], ())
    assert focus == set() and not L.reviewed_unchanged(gone)
    assert "INDETERMINATE" in note and "not a pass" in note


def test_an_open_priors_file_rides_along_even_when_the_fix_never_touched_it(tmp_path):
    """Otherwise the gate asks "did you fix b.py?" while handing over only a.py,
    and the honest `unverifiable` answer does not block -- so an unfixed HIGH
    finding clears itself the moment the agent stops touching its file."""
    d = tmp_path / "p"
    (d / "_verify_state").mkdir(parents=True)
    for f in ("a.py", "b.py", "c.py"):
        (tmp_path / f).write_text("1")
    first = L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py", "c.py"])
    L.record(first, [{"kind": "finding", "attempt": 1, "fid": "z1", "file": "b.py",
                      "severity": "high", "summary": "bare", "status": "open"}])
    (tmp_path / "a.py").write_text("FIXED")
    second = L.context(str(d), "s01", str(tmp_path), ["a.py", "b.py", "c.py"])
    focus, changed, _, note = L.narrow_to_delta(second, ["a.py", "b.py", "c.py"], ())
    assert focus == {"a.py", "b.py"}, "the fix delta PLUS the open prior's file"
    assert "c.py" not in changed, "c.py is unchanged and carries no prior: not re-sampled"


# ---------- the ledger KEY: one per gate, whatever the harness flip does ----------

def _one_gate_tree(tmp_path):
    """A real git work tree with one committed-then-modified file, plus a plan dir."""
    tree = tmp_path / "tree"
    tree.mkdir()

    def run(*a):
        subprocess.run(list(a), cwd=tree, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tree / "a.py").write_text("x = 1\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (tree / "a.py").write_text("x = 2\n")
    plan = tmp_path / "plan"
    (plan / "_verify_state").mkdir(parents=True)
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["s01"], '
        '"ts": "2026-08-25T00:00:00+00:00"}\n')
    return tree, plan


def _claude_stub(tmp_path, monkeypatch):
    """A `claude` FIRST on PATH that answers one reviewed file and no findings."""
    bindir = tmp_path / "cbin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(
        {"type": "result", "is_error": False,
         "result": "REVIEWED_FILES: 1\n\n```json\n[]\n```"}) + "\nEOF\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_the_OPT_IN_does_not_collapse_the_two_GATES_onto_one_ledger(tmp_path, monkeypatch):
    """WIRE-07 finding 2. `vpk.reviewer_for` is MANY-TO-ONE: under the CODEX
    harness with `claude_verifier_under_codex_harness: true` it maps BOTH
    `--reviewer claude` and `--reviewer codex` to claude, because the family that
    did not write Codex-built code is Claude either way.

    Keyed on the family that RUNS, `llm-review-<lvl>` and
    `cross-family-review-<lvl>` therefore landed on the SAME ledger key the moment
    the opt-in was switched on — and at `land`, which re-runs the union of every
    session's gates under the single session id `land`, the second of the two
    reads the first's surface record, finds nothing changed since it, and exits
    INDETERMINATE having reviewed nothing. That is exactly the collision the
    reviewer axis was added to prevent, restored by the setting that is supposed
    to make cross-family review possible at all.

    `test_the_two_REVIEWERS_do_not_share_a_ledger` (test_codex_review_backend.py)
    cannot see this: it runs under the CLAUDE harness, where `reviewer_for` is a
    no-op and the two declared values survive unchanged.
    """
    import verifier_park as vpk

    ssot = tmp_path / "model-routing.yaml"
    ssot.write_text("verification:\n  claude_verifier_under_codex_harness: true\n")
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_SSOT", str(ssot))
    # THE PREMISE, ASSERTED. Without this line the test could pass because the
    # opt-in never took effect and no flip ever happened.
    assert vpk.reviewer_for("codex", "codex") == vpk.reviewer_for("codex", "claude") == "claude"

    tree, plan = _one_gate_tree(tmp_path)
    _claude_stub(tmp_path, monkeypatch)
    base = ["--level", "medium", "--cwd", str(tree), "--plan-dir", str(plan),
            "--session", "s01", "--harness", "codex", "--timeout", "30"]
    # `llm-review-medium`'s argv (no --reviewer), then `cross-family-review-medium`'s.
    assert g.main(base) == g.PASS
    assert g.main([*base, "--reviewer", "codex"]) == g.PASS, (
        "the second gate exited non-PASS — it inherited the first's surface record "
        "and skipped its own review")

    led = plan / "_verify_state"
    assert (led / "s01.medium.claude.findings.ndjson").exists()
    assert (led / "s01.medium.codex.findings.ndjson").exists(), (
        "both gates keyed on the family that RAN, not the family the argv declared")
    for path in led.glob("s01.medium.*.findings.ndjson"):
        kinds = [json.loads(ln)["kind"] for ln in path.read_text().splitlines()]
        assert "surface" in kinds, f"{path.name} holds no surface record — it never reviewed"

    # KNOWN NEGATIVE for the same guard: the SAME gate id, run again against an
    # unchanged tree, MUST still inherit and refuse. Without this the assertions
    # above would also pass on a ledger that never carried anything forward.
    again = L.context(str(plan), "s01.medium.codex", str(tree), ["a.py"])
    assert again.attempt == 2
    assert L.narrow_to_delta(again, ["a.py"], [])[0] == set()


# ---------- the accept list: a DECLARED finding, cleared with a reason ----------
#
# Why this exists: a finding the plan has deliberately deferred is still a `high`,
# and severity is the whole blocking rule -- so it blocked the land forever with no
# way to say "known, and not this session's job". The reviewer also REWORDS its
# summary between attempts, which is why the key is a phrase and not the finding
# id: the same uv runner-option gap arrived under two different ids on attempts 4
# and 5 (2026-09-08).

UV = {"file": "scripts/govrun_pytest_budget.py", "line": 218, "severity": "high",
      "summary": "Runner options still bypass both enforcement layers: uv run "
                 "--with and --module reach pytest."}


def _accept_file(tmp_path, entries):
    """A plan dir carrying an accept file, and the session name that reads it."""
    d = tmp_path / "_verify_state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s06.accepted.json").write_text(json.dumps({"accepted": entries}))
    return str(tmp_path), "s06"


class _AcceptCtx(_Ctx):
    def __init__(self, accepted, attempt=2, delta=("scripts/govrun_pytest_budget.py",)):
        super().__init__(attempt, delta=delta)
        self.accepted = accepted


def test_a_declared_finding_does_not_block_and_carries_its_reason(tmp_path):
    plan, session = _accept_file(tmp_path, [
        {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options still bypass",
         "severity": "high", "reason": "deliberate scope boundary (s11)"}])
    accepted = L.load_accepted(plan, session)
    fail, counts, recs = L.judge(_AcceptCtx(accepted), [UV])
    assert not fail, recs
    assert counts["accepted"] == 1
    assert recs[0]["status"] == "accepted"
    assert recs[0]["accept_reason"] == "deliberate scope boundary (s11)"
    # KNOWN POSITIVE: the very same finding blocks when nothing is declared.
    assert L.judge(_AcceptCtx([]), [UV])[0] is True


def test_the_accept_survives_the_reviewer_REWORDING_the_finding(tmp_path):
    """The reason the key is a phrase and not the finding id."""
    plan, session = _accept_file(tmp_path, [
        {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options still bypass",
         "reason": "deferred"}])
    accepted = L.load_accepted(plan, session)
    reworded = dict(UV, summary="Runner options still bypass both enforcement layers: "
                                "installed 'uv run --help' confirms '-w' is valid.")
    assert L.fingerprint(reworded) != L.fingerprint(UV)   # a different id ...
    assert not L.judge(_AcceptCtx(accepted), [reworded])[0]  # ... same accept


@pytest.mark.parametrize("entry", [
    {"file": "scripts/govrun_pytest_budget.py", "contains": "", "reason": "r"},   # matches ALL
    {"file": "scripts/govrun_pytest_budget.py", "reason": "r"},                   # no phrase
    {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options"},    # no reason
    {"contains": "runner options", "reason": "r"},                                # no file
    {"file": "scripts/govrun_pytest_budget.py", "contains": "!!!", "reason": "r"},  # empty once normalised
    "not-a-dict",
])
def test_a_malformed_entry_is_dropped_never_honoured(tmp_path, entry):
    """FAIL CLOSED. An empty phrase would clear every finding in the file."""
    plan, session = _accept_file(tmp_path, [entry])
    assert L.load_accepted(plan, session) == []
    assert L.judge(_AcceptCtx(L.load_accepted(plan, session)), [UV])[0] is True


def test_no_accept_file_or_unreadable_json_accepts_nothing(tmp_path):
    assert L.load_accepted(str(tmp_path), "s06") == []          # absent
    d = tmp_path / "_verify_state"
    d.mkdir(parents=True)
    (d / "s06.accepted.json").write_text("{not json")
    assert L.load_accepted(str(tmp_path), "s06") == []          # unparseable
    (d / "s06.accepted.json").write_text('["a list, not an object"]')
    assert L.load_accepted(str(tmp_path), "s06") == []          # wrong shape


def test_an_accept_is_pinned_to_its_file_and_severity(tmp_path):
    plan, session = _accept_file(tmp_path, [
        {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options still bypass",
         "severity": "high", "reason": "deferred"}])
    accepted = L.load_accepted(plan, session)
    # The same words in ANOTHER file are a different defect and still block.
    assert L.judge(_AcceptCtx(accepted), [dict(UV, file="hooks/governor_parse.py")])[0] is True
    # A severity on the entry pins it: a `critical` is not the thing declared.
    assert L.judge(_AcceptCtx(accepted), [dict(UV, severity="critical")])[0] is True
    # A different finding in the SAME file still blocks.
    assert L.judge(_AcceptCtx(accepted), [dict(UV, summary="peel bound is cached")])[0] is True


def test_an_accepted_finding_never_becomes_a_blocking_prior(tmp_path):
    """It is recorded, so it is auditable -- but it must not ride along as open."""
    plan, session = _accept_file(tmp_path, [
        {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options still bypass",
         "reason": "deferred"}])
    _, _, recs = L.judge(_AcceptCtx(L.load_accepted(plan, session)), [UV])
    assert L.open_priors(recs) == []


def test_the_convergence_line_reports_what_it_accepted():
    line = L.convergence_line(dict(attempt=2, prior_fixed=0, prior_open=0,
                                   new_in_delta=0, new_outside=0, noted=0, accepted=2))
    assert "accepted=2" in line
    assert "accepted=" not in L.convergence_line(
        dict(attempt=2, prior_fixed=0, prior_open=0, new_in_delta=0,
             new_outside=0, noted=0, accepted=0))


def test_the_gate_prints_what_it_accepted_and_why(tmp_path):
    """The audit trail. A pass that steps over a finding must SAY so."""
    plan, session = _accept_file(tmp_path, [
        {"file": "scripts/govrun_pytest_budget.py", "contains": "runner options still bypass",
         "reason": "deliberate scope boundary (s11)"}])
    _, counts, recs = L.judge(_AcceptCtx(L.load_accepted(plan, session)), [UV])
    block = L.accepted_block(recs)
    assert "ACCEPTED scripts/govrun_pytest_budget.py:218" in block
    assert "deliberate scope boundary (s11)" in block
    assert "accepted=1" in L.convergence_line(counts) + block
    # KNOWN NEGATIVE: nothing accepted, nothing printed.
    assert L.accepted_block(L.judge(_AcceptCtx([]), [UV])[2]) == ""


def test_a_surface_path_the_gate_cannot_hash_never_inherits_a_pass(tmp_path):
    """`surface_hashes` skips what it cannot read. git lists an embedded repo or a
    submodule as ONE path that is a directory, and a symlink as its own blob, so
    content can change under a skipped path while every hash still matches. A
    path that exists but was not hashed is not "unchanged"; a path that is gone
    (a deleted file) is stable and still allowed."""
    d = tmp_path / "p"
    (d / "_verify_state").mkdir(parents=True)
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("1")
    (tmp_path / "link").symlink_to("nowhere")
    for odd in ("sub", "link"):
        surface = ["a.py", odd, "deleted.py"]
        L.record(L.context(str(d), odd, str(tmp_path), surface), [])
        again = L.context(str(d), odd, str(tmp_path), surface)
        assert not L.reviewed_unchanged(again), odd
    # control: the deleted path alone does not block the pass
    L.record(L.context(str(d), "ok", str(tmp_path), ["a.py", "deleted.py"]), [])
    assert L.reviewed_unchanged(L.context(str(d), "ok", str(tmp_path), ["a.py", "deleted.py"]))
