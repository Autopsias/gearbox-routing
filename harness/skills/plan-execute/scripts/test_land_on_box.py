"""WIRE-07 finding 1 — a Codex-built plan must be able to LAND.

THE DEFECT, reproduced end to end here. Every review gate in the shipped registry
declares `PLAN_EXECUTE_HARNESS` in its `env_allowlist`, and `review_context.
land_harness` answers `codex` for any plan with even one Codex-built session. So
the land re-gate really does run `llm_review_gate.py --harness codex`, and without
`verification.claude_verifier_under_codex_harness` the gate's `verifier_park.
refusal()` prints `VERIFIER: on_box_human` and exits 2 BEFORE reading a byte of the
tree. That exit is the gate's declared `indeterminate_exit`, so it used to reach
`land_gate._verdict` as an ordinary undecided and park `gate-indeterminate` —
whose brief, and whose row in `references/failure-modes.md`, say to re-run
`run.py land`, raise the timeout, narrow the scope, or run the gate by hand.

EVERY ONE OF THOSE IS INERT AGAINST A POLICY REFUSAL. It is decided from the SSOT
and the harness before the tree is read; it takes the same time and gives the same
answer on every retry. The plan could not land by any documented command.

So a policy refusal now gets its own park with its own resolution — the LAND-scope
twin of `ack-checkpoint --session sNN --decision verified-on-box|blocked`. The two
constraints these tests hold it to:

  * the refusal is never silently reclassified as a pass. `verified-on-box` is its
    own gate outcome, recorded by a HUMAN, carried into §5.1c's digest, and bound
    to the candidate it judged;
  * the egress rule is untouched. No reviewer runs — the gate is not re-executed
    after the disposition, which is asserted against a counter the gate itself
    increments.

Real bare origin, real worktrees, real `run.py` subprocesses: the fixture is
`_land_fixture`, shared with `test_land.py` and the shipped land evidence.

    pytest skills/plan-execute/scripts/test_land_on_box.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land  # noqa: E402
import land_gate as lgt  # noqa: E402
import land_state as lst  # noqa: E402
import verifier_park as vpk  # noqa: E402
from _land_fixture import (build_repo, isolate, local_main,  # noqa: E402
                           origin_main, run_cli, work)
from worktree import git  # noqa: E402

GATE = "marker-gate"


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _refusing_gate(fx, counter):
    """Re-point the plan's one gate at a REAL policy refusal.

    Byte-for-byte the shape `verifier_park.refusal` writes: a preamble line, then
    the `VERIFIER:`/`DEGRADED_FROM:` pair, then exit 2 — the gate's declared
    `indeterminate_exit`. The preamble matters: `cause_in` finds the marker by
    FIRST prefix match over the WHOLE stream, never at a fixed line, because the
    real gate's surface preamble prints one line per untracked file.

    `$0` is the counter path, so every invocation is countable from outside the
    land worktree the gate runs in.
    """
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {GATE: {"kind": "argv", "cwd": ".", "timeout": 60, "indeterminate_exit": 2,
                "argv": ["/bin/sh", "-c",
                         'echo ran >> "$0"; '
                         'echo "[llm-review-gate] reviewer: family=claude"; '
                         'echo "VERIFIER: on_box_human"; '
                         'echo "DEGRADED_FROM: codex_harness_no_claude_verifier"; '
                         'exit 2', str(counter)]}}))


def _runs(counter):
    return len(Path(counter).read_text().splitlines()) if Path(counter).exists() else 0


def _parked(fx, tmp_path, slug="plan-a"):
    """A plan parked on a real policy refusal. -> (iso, counter)."""
    counter = tmp_path / "gate-runs.txt"
    _refusing_gate(fx, counter)
    iso = isolate(fx, slug)
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked",
                                                "gate-on-box-verification"), out
    return iso, counter, out


# --------------------------------------------------------------------------
def test_a_policy_refusal_parks_for_a_human_and_re_running_cannot_clear_it(fx, tmp_path):
    """The park itself, and the property that made the old one a dead end."""
    iso, counter, out = _parked(fx, tmp_path)
    assert out["on_box"] == [GATE]
    assert out["causes"] == [vpk.CAUSE_NO_OPT_IN]
    st = lst.load(iso["plan_dir"])
    assert [g["outcome"] for g in st["gates"]] == [lgt.ON_BOX]
    # The brief must name the command that RESOLVES this, and must not repeat the
    # indeterminate brief's advice, which is what made the old park a loop.
    assert "land-verify" in out["brief"]
    assert f"--decision {lgt.VERIFIED_ON_BOX}" in out["brief"]
    assert f"--decision {lgt.BLOCKED}" in out["brief"]
    assert "an indeterminate gate is not a finding" not in out["brief"]
    # ...and an ORCHESTRATOR reading only the dispatch loop's dict sees the same
    # command. Without it the loop's one visible option is to re-run `land`.
    act = land.land_action(iso["plan_dir"], {"action": "complete"})
    assert act["action"] == "land" and "land-verify" in act["verify_with"], act

    # THE DEFECT ITSELF: re-running reproduces the identical refusal. The gate DID
    # run again (it is not cached — only a pass is), and the answer did not move.
    before = _runs(counter)
    rc2, out2 = run_cli("land", iso["plan_dir"])
    assert (rc2, out2["kind"]) == (1, "gate-on-box-verification"), out2
    assert _runs(counter) == before + 1, "the gate was cached — a refusal is not a pass"
    assert origin_main(fx) == local_main(fx), "a park must never advance the target"


def test_an_undecided_gate_WITHOUT_the_marker_is_still_gate_indeterminate(fx, tmp_path):
    """The KNOWN NEGATIVE for the classifier. Both refusals leave by the same
    `indeterminate_exit`; if the split keyed on the exit code instead of the
    marker, every real timeout would land in the human's lap and the recovery
    advice would be wrong for it."""
    counter = tmp_path / "gate-runs.txt"
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {GATE: {"kind": "argv", "cwd": ".", "timeout": 60, "indeterminate_exit": 2,
                "argv": ["/bin/sh", "-c", 'echo ran >> "$0"; '
                                          'echo "the reviewer never answered"; exit 2',
                         str(counter)]}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "gate-indeterminate"), out
    assert lst.load(iso["plan_dir"])["gates"][0]["outcome"] == "skipped"
    # ...and `land-verify` is not a way past it: no gate REFUSED, so there is no
    # human disposition to record.
    rc, ref = run_cli("land-verify", iso["plan_dir"], "--decision", lgt.VERIFIED_ON_BOX)
    assert (rc, ref["action"]) == (1, "error"), ref
    assert "no on-box verification park" in ref["message"]


def test_verified_on_box_lands_and_is_NEVER_recorded_as_a_pass(fx, tmp_path):
    iso, counter, _ = _parked(fx, tmp_path)
    after_park = _runs(counter)

    rc, rec = run_cli("land-verify", iso["plan_dir"], "--decision", lgt.VERIFIED_ON_BOX,
                      "--note", "read the merged diff myself")
    assert (rc, rec["action"], rec["gates"]) == (0, "land-verified", [GATE]), rec

    rc, parked = run_cli("land", iso["plan_dir"])
    assert (rc, parked["action"]) == (1, "land-awaits-review"), parked
    # THE EGRESS RULE IS UNTOUCHED: the gate was not re-executed. Its refusal
    # stands; what changed is that a human answered it.
    assert _runs(counter) == after_park, "the refused gate ran again after the ack"

    st = lst.load(iso["plan_dir"])
    assert {g["gate_id"]: g["outcome"] for g in st["gates"]} == {GATE: lgt.VERIFIED_ON_BOX}
    assert st["gates"][0]["outcome"] != "pass", "a policy refusal must never become a pass"
    assert st["on_box_gates"][GATE]["note"] == "read the merged diff myself"
    assert st["on_box_gates"][GATE]["cause"] == vpk.CAUSE_NO_OPT_IN, (
        "the disposition records WHICH refusal the human was answering")
    assert st["on_box_gates"][GATE]["gate_key"] == st["gate_key"]
    # The digest the human's ack binds to SAYS a human cleared it — it is not the
    # digest the same gate would have produced by passing.
    assert st["gate_digest"] != lst.gate_digest([{"gate_id": GATE, "outcome": "pass"}])
    assert st["gate_digest"] == lst.gate_digest(
        [{"gate_id": GATE, "outcome": lgt.VERIFIED_ON_BOX}])

    rc, acked = run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    assert (rc, acked["action"]) == (0, "land-acked"), acked
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed"), out
    # The approved merge really reached the shared branch (§4.8 pushes to origin
    # by CAS and deliberately does NOT move the operator's local ref, so this asks
    # git for ancestry rather than comparing the two refs).
    assert git(["merge-base", "--is-ancestor", out["merge_sha"], "origin/main"],
               fx["root"])[0] == 0, out


def test_blocked_records_the_disposition_and_ships_nothing(fx, tmp_path):
    iso, counter, _ = _parked(fx, tmp_path)
    rc, rec = run_cli("land-verify", iso["plan_dir"], "--decision", lgt.BLOCKED,
                      "--note", "the merge breaks src/f.py")
    assert (rc, rec["action"], rec["decision"]) == (0, "land-verified", lgt.BLOCKED), rec

    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-on-box-blocked"), out
    assert out["blocked"] == [GATE]
    assert origin_main(fx) == local_main(fx), "nothing ships on a BLOCKED disposition"
    # NO WORK IS EVER DELETED BY A PARK — the plan branch and its worktree survive.
    assert Path(iso["tree"]).is_dir()
    st = lst.load(iso["plan_dir"])
    assert st["on_box_gates"][GATE]["note"] == "the merge breaks src/f.py"


def test_a_disposition_does_not_carry_across_candidates(fx, tmp_path):
    """A human's answer judged ONE merged tree. Reused across a different one it
    would be an approval of work nobody saw — the same binding `land_record`'s
    `gate_key` stamp enforces for a skill verdict."""
    iso, counter, _ = _parked(fx, tmp_path)
    run_cli("land-verify", iso["plan_dir"], "--decision", lgt.VERIFIED_ON_BOX)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    stamped = lst.load(iso["plan_dir"])["gate_key"]

    work(iso["tree"], "src/late.py", "LATE = 1\n")        # a DIFFERENT candidate
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "gate-on-box-verification"), out
    st = lst.load(iso["plan_dir"])
    assert st["gate_key"] != stamped
    assert st.get("on_box_gates") == {}, "the old disposition was carried forward"
    # THE SECOND LAYER, asserted directly. End to end it is `step_gate`'s reset that
    # fires, so the key stamp inside `disposition` would otherwise never be
    # exercised — and an unexercised guard is indistinguishable from a missing one.
    # It is kept because `_cached_pass` carries the identical comparison: the reset
    # and the stamp are written together today, and a record that outlives that
    # invariant must still not be read as an answer about this candidate.
    rec = {"gate_id": GATE, "outcome": lgt.VERIFIED_ON_BOX, "gate_key": stamped}
    assert lgt.disposition({"on_box_gates": {GATE: rec}}, GATE, stamped) is rec
    assert lgt.disposition({"on_box_gates": {GATE: rec}}, GATE, "another-candidate") is None


def test_the_RESTRICTED_TREE_degrade_parks_the_same_way(fx, tmp_path):
    """The other documented `DEGRADED_FROM:` cause, and the one `model-routing.yaml`
    `verification.restricted_repos` already names the remedy for: on a restricted
    tree sending the code to Codex is forbidden egress, so `codex_review_backend`
    degrades to `VERIFIER: on_box_human` / `DEGRADED_FROM: cross_family` at exit 2
    and the SSOT says the cross-family requirement is then met by "human/on-box
    review at the checkpoint, recorded as an explicit VERIFIED-ON-BOX (or BLOCKED)
    disposition". At land scope that recording did not exist. It does now, and it
    is cause-agnostic: the park reads whichever cause the gate printed."""
    counter = tmp_path / "gate-runs.txt"
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {GATE: {"kind": "argv", "cwd": ".", "timeout": 60, "indeterminate_exit": 2,
                "argv": ["/bin/sh", "-c",
                         'echo ran >> "$0"; echo "VERIFIER: on_box_human"; '
                         'echo "DEGRADED_FROM: cross_family"; exit 2', str(counter)]}}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "gate-on-box-verification"), out
    assert out["causes"] == ["cross_family"]
    assert "RESTRICTED" in out["brief"], "the brief must name THIS cause, not a canned one"
    rc, _ = run_cli("land-verify", iso["plan_dir"], "--decision", lgt.VERIFIED_ON_BOX,
                    "--note", "reviewed on box; the tree may not leave this machine")
    assert rc == 0
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
    st = lst.load(iso["plan_dir"])
    assert st["on_box_gates"][GATE]["cause"] == "cross_family", (
        "the cause is read PER GATE from what that gate printed, never a canned one")


def test_the_two_scopes_share_ONE_disposition_vocabulary():
    """`land-verify --decision X` and `ack-checkpoint --decision X` are the same
    two words at two scopes. A second copy of that vocabulary is a contract that
    drifts, so the land reads the session park's."""
    assert sorted(lgt.DECISIONS) == sorted(vpk.DECISIONS)
    assert lgt.VERIFIED_ON_BOX == "verified-on-box" and lgt.BLOCKED == "blocked"


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
