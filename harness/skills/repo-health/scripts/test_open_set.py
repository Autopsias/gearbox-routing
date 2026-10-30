"""Tests for the open set: items a recorded run left open inherit across runs and
leave only on positive evidence; prior runs, acceptance and the per-repo lock."""

import json
import os
import signal
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
import runlog  # noqa: E402
from exposure_testlib import (  # noqa: E402
    CONF, DEFERRED_ADMIN, TODAY, _accepted_prior, _cli_record, _commit_change,
    _deep_text, _revalidation, _reviewing, _run, cli, lead, make_repo,
    archived_candidate, needs_node, plant, record_run, rejected, unit)


@needs_node
def test_prior_deferred_unit_marked_not_applicable_stays_open(exposure_repo):
    _accepted_prior(exposure_repo)
    na = unit("admin", "not_applicable", unresolved=["said so"])
    res = cf_check.check_run(plant(exposure_repo, [rejected("lead-a")], ledger=[unit("api"), na]),
                             exposure_repo)
    assert res["status"] == "warn"
    assert ("still open from earlier runs, missing here: open unit admin / Access control"
            in res["detail"])


# 5f: prior runs
def test_prior_runs_newest_first_and_per_repo(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    other = make_repo(tmp_path / "x" / "r")
    old = exposure.cf_output_dir(repo, datetime(2026, 10, 1, 9, 0, 0))
    new = exposure.cf_output_dir(repo, datetime(2026, 10, 23, 9, 0, 0))
    for rp, run in ((repo, old), (repo, new), (other, exposure.cf_output_dir(other))):
        exposure.accept(plant(rp, out=run), "pass")
    plant(repo)                                     # R2-M5/R3-M2: never accepted, not a run
    assert exposure.prior_runs(repo) == [new, old]
    p = cli("prior-runs", repo, env={**os.environ, "HOME": str(exposure_home)})
    assert p.stdout.splitlines() == [str(new), str(old)]


@needs_node
def test_dropping_prior_unresolved_items_is_not_a_pass(exposure_repo):
    _accepted_prior(exposure_repo)
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)          # same commit, both omitted
    assert res["status"] == "warn"
    assert ("still open from earlier runs, missing here: lead Lead lead-a, "
            "open unit admin / Access control") in res["detail"]


@needs_node
def test_prior_items_resolved_by_a_final_record_can_pass(exposure_repo):
    _accepted_prior(exposure_repo)
    res = cf_check.check_run(plant(exposure_repo, [rejected("lead-a")],
                                   ledger=[unit("api"), unit("admin")]), exposure_repo)
    assert res["status"] == "pass", res
    assert "carried: 0" in res["detail"]


# R3-M2: only runs check-run accepted and recorded are prior runs
def test_only_accepted_runs_are_prior_runs(exposure_repo, exposure_node, exposure_home):
    out = plant(exposure_repo)
    assert exposure.prior_runs(exposure_repo) == []                 # never recorded
    health.collect(exposure_repo)
    env = {**os.environ, "HOME": str(exposure_home)}
    cli("check-run", out, exposure_repo, "--record", "--agents", "41", env=env)   # pending
    assert exposure.prior_runs(exposure_repo) == []
    p = cli("check-run", out, exposure_repo, "--record", "--agents", "25", env=env)
    assert p.stdout.splitlines()[0] == "pass"
    assert exposure.prior_runs(exposure_repo) == [out]
    (out / "findings.json").write_text("[]\n")               # edited after acceptance
    assert exposure.prior_runs(exposure_repo) == []


# R4-H1: an item dropped in run 2 is still open in run 3 (real validators)
@needs_node
def test_dropped_item_stays_open_across_runs(exposure_repo):
    first = _run(exposure_repo, [lead("lead-a")], [unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)])
    assert first["status"] == "warn"
    for _ in range(2):                              # run 2 drops both, run 3 still lacks them
        res = _run(exposure_repo)
        assert res["status"] == "warn"
        assert ("still open from earlier runs, missing here: lead Lead lead-a, "
                "open unit admin / Access control") in res["detail"]
        assert {(i["kind"], i["id"]) for i in res["open"]} == {
            ("lead", "lead-a"), ("unit", unit("admin")["coverage_id"])}


# R4-H2: a confirmed finding stays open until a final record rejects it (real validators)
@needs_node
def test_omitted_confirmed_finding_stays_fail(exposure_repo):
    assert _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])]
                )["status"] == "fail"
    res = _run(exposure_repo)                              # same source, finding omitted
    assert res["status"] == "fail"
    assert "missing here: confirmed finding Planted IDOR" in res["detail"]
    res = _run(exposure_repo)                              # and again
    assert res["status"] == "fail"


@needs_node
def test_demoted_confirmed_finding_stays_fail(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    demoted = dict(lead("conf-1"), title="Planted IDOR")
    res = _run(exposure_repo, [demoted], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    assert res["status"] == "fail"
    # carried once, as confirmed: a demotion never turns it into a mere lead
    assert [i["kind"] for i in res["open"] if i["id"] == "conf-1"] == ["confirmed"]


# Land review r6 LOW: the detail names a demoted finding as still confirmed
@needs_node
def test_a_demoted_finding_is_named_still_confirmed_in_the_detail(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    res = _run(exposure_repo, [lead("conf-1")], [unit("api")])
    assert res["status"] == "fail"
    assert "still confirmed (reported here as a lead): Planted IDOR" in res["detail"], res
    # follow-up r7: the summary does not count it among the leads as well
    assert "0 need validation, 1 still confirmed" in res["detail"], res
    assert "still confirmed" not in _run(exposure_repo, [CONF], [unit(
        "api", "candidate", result_fingerprints=["conf-1"])])["detail"]


@needs_node
def test_items_leave_the_open_set_only_through_a_final_record(exposure_repo):
    _run(exposure_repo, [lead("lead-a"), CONF], [unit("api", "candidate", result_fingerprints=[
        "conf-1", "lead-a"]), unit("admin", "deferred", **DEFERRED_ADMIN)])
    assert _run(exposure_repo)["status"] == "fail"                         # run 2 drops all three
    res = _run(exposure_repo, [rejected("lead-a"), rejected("conf-1")],    # run 3 resolves them
               [unit("api", "candidate", result_fingerprints=["conf-1", "lead-a"]),
                unit("admin")])
    assert res["status"] == "pass", res
    assert res["open"] == []
    assert ("resolved here: confirmed finding Planted IDOR (rejected), lead Lead lead-a "
            "(rejected), open unit admin / Access control (covered)") in res["detail"]
    assert _run(exposure_repo)["status"] == "pass"                         # nothing left to carry


# R4-M2: "new since" is measured against the inherited open set
@needs_node
def test_new_since_counts_against_the_open_set(exposure_repo):
    _run(exposure_repo, [lead("lead-a")])
    _run(exposure_repo, [lead("lead-b")])                 # drops lead-a, which stays open
    res = _run(exposure_repo, [lead("lead-a"), lead("lead-b")])
    assert res["detail"].startswith(f"new since {TODAY}: 0 leads, 0 deferred units; carried: 2")


def test_open_set_survives_edits_to_the_accepted_run(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    (out / "findings.json").write_text("[]")        # the run is no longer a prior run ...
    assert exposure.prior_runs(exposure_repo) == []
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]   # ... but its obligations hold


# attacker pass, several runs: editing an accepted run and recording it again
@needs_node
def test_rerecording_an_edited_run_keeps_its_obligations(exposure_repo):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    (out / "findings.json").write_text("[]")        # the auditor edits its own output ...
    res = cf_check.check_run(out, exposure_repo)    # ... and checks the same folder again
    assert res["status"] == "pending" and "already holds gearbox-accepted.json" in res["detail"]
    res = record_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "warn" and "missing here: lead Lead lead-a" in res["detail"]


def test_acceptance_order_survives_a_clock_going_backwards(exposure_repo, exposure_node, monkeypatch):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    monkeypatch.setattr(runlog.time, "time_ns", lambda: 1)       # clock jumps to 1970
    res = record_run(plant(exposure_repo, [rejected("lead-a")]), exposure_repo)
    assert res["open"] == []
    assert exposure.open_set(exposure_repo)[0] == {}      # the later acceptance still wins


# R5-H1, reversed by the owner at land review r5: a fixed finding stays open until a
# complete run reports it rejected; a revalidation unit on a changed own file never clears it
@needs_node
def test_fixed_confirmed_finding_stays_open_on_revalidation_alone(exposure_repo):
    assert _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])]
                )["status"] == "fail"
    _commit_change(exposure_repo, "src/api.py", "x = 2  # owner check added\n")
    res = _run(exposure_repo, [], [_revalidation()])
    assert res["status"] == "fail", res
    assert _kinds(res) == {("confirmed", "conf-1")}
    assert "resolved here" not in res["detail"]
    assert "missing here: confirmed finding Planted IDOR" in res["detail"]
    res = _run(exposure_repo, [rejected("conf-1")], [unit("api", "candidate",
                                                         result_fingerprints=["conf-1"])])
    assert res["status"] == "pass" and res["open"] == [], res      # the one way out


# Land review r6 MEDIUM: the documented way out for a fixed finding (--deep step 2).
# Complete runs that only cover its revalidation unit never clear it, however many ...
@needs_node
def test_a_fixed_finding_stays_fail_while_its_revalidation_unit_is_only_covered(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "src/api.py", "x = 9  # owner check added\n")
    for _ in range(3):
        res = _run(exposure_repo, [], [_revalidation()])
        assert res["status"] == "fail" and _kinds(res) == {("confirmed", "conf-1")}, res
        assert "resolved here" not in res["detail"]


# ... and the documented sequence clears it: in one complete run its revalidation unit is a
# candidate for that fingerprint and the verifier writes a rejected final record
@needs_node
def test_a_fixed_finding_leaves_on_its_revalidation_candidate_rejected(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "src/api.py", "x = 9  # owner check added\n")
    reval = dict(_revalidation(), status="candidate", result_fingerprints=["conf-1"])
    res = _run(exposure_repo, [rejected("conf-1")], [reval])
    assert res["status"] == "pass" and res["open"] == [], res
    assert "resolved here: confirmed finding Planted IDOR (rejected)" in res["detail"]


def test_deep_text_says_how_a_fixed_finding_gets_its_rejected_record():
    text = _deep_text()
    assert ("report its revalidation unit as `candidate` with that fingerprint in "
            "`result_fingerprints`") in text
    assert "Until such a run reports it `rejected`, a fixed finding stays at `fail`" in text
    assert "Until such a run, a fixed finding" not in text


@needs_node
def test_unchanged_source_omission_never_resolves(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    res = _run(exposure_repo, [], [_revalidation()])                   # same commit
    assert res["status"] == "fail" and "missing here: confirmed finding Planted IDOR" in \
        res["detail"]
    _commit_change(exposure_repo, "src/db.py", "x = 3\n")              # a file the unit did not review
    assert _run(exposure_repo, [], [_revalidation()])["status"] == "fail"
    assert _run(exposure_repo, [], [unit("api", prior_status="prior_confirmed_same_source",
                                   revalidates=["conf-1"])])["status"] == "fail"
    _commit_change(exposure_repo, "src/api.py", "x = 2\n")
    assert _run(exposure_repo)["status"] == "fail"                     # changed, but omitted


# s11 review H: revalidation must touch the item's OWN trace files (src/api.py, src/db.py)
@needs_node
def test_revalidation_needs_a_changed_file_of_the_item_itself(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "app.py", "print('v2')\n")         # not a file of conf-1
    other = _reviewing("app.py")
    other.update(prior_status="prior_confirmed_changed_source", revalidates=["conf-1"])
    res = _run(exposure_repo, [], [other])
    assert res["status"] == "fail", res
    assert [(i["id"], i["files"]) for i in res["open"]] == [("conf-1", ["src/api.py", "src/db.py"])]
    assert "resolved here" not in res["detail"]


@needs_node
def test_an_open_item_stored_without_its_files_never_clears_by_revalidation(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    marker = exposure.prior_runs(exposure_repo)[0] / runlog.ACCEPTED
    mark = json.loads(marker.read_text())
    for item in mark["open"]:
        del item["files"]                                              # an older marker
    marker.write_text(json.dumps(mark))
    _commit_change(exposure_repo, "src/api.py", "x = 2  # owner check added\n")
    res = _run(exposure_repo, [], [_revalidation()])
    assert res["status"] == "fail" and {i["id"] for i in res["open"]} == {"conf-1"}, res


@needs_node
def test_critic_resolves_a_prior_lead_only_with_a_reason(exposure_repo):
    _run(exposure_repo, [lead("lead-a"), CONF], [unit("api", "candidate", result_fingerprints=[
        "conf-1", "lead-a"])])
    no_reason = [{"fingerprint": "lead-a", "reason": " "}, {"fingerprint": "conf-1",
                                                           "reason": "Fixed."}]
    res = record_run(plant(exposure_repo, resolved_prior_leads=no_reason), exposure_repo)
    assert res["status"] == "fail"                               # a critic never clears confirmed
    assert {i["id"] for i in res["open"]} == {"lead-a", "conf-1"}
    why = [{"fingerprint": "lead-a", "reason": "The proxy strips X-User (src/admin.py:1)."}]
    res = record_run(plant(exposure_repo, resolved_prior_leads=why), exposure_repo)
    assert ("lead Lead lead-a (critic: The proxy strips X-User (src/admin.py:1).)"
            in res["detail"])
    assert {i["id"] for i in res["open"]} == {"conf-1"}


def test_open_items_carry_the_commit_they_were_last_reported_at(exposure_repo, exposure_node):
    old = exposure.head(exposure_repo)
    _run(exposure_repo, [lead("lead-a")])
    _commit_change(exposure_repo, "app.py", "print('v2')\n")
    assert _run(exposure_repo)["open"][0]["ref"] == old                # carried: keeps its ref
    assert _run(exposure_repo, [lead("lead-a")])["open"][0]["ref"] == exposure.head(exposure_repo)


# R5-M1: overlapping check-runs cannot erase an inherited item
def test_overlapping_check_runs_keep_inherited_items(exposure_repo, exposure_node, monkeypatch):
    import threading
    _run(exposure_repo, [lead("lead-a")])
    out_a = plant(exposure_repo, [lead("lead-a"), lead("lead-b")])     # A adds lead-b
    out_b = plant(exposure_repo, [rejected("lead-a")])                 # B resolves lead-a
    b_read, a_done = threading.Event(), threading.Event()
    real_open, real_accept = cf_check.open_set, exposure.accept

    def open_set(repo):
        res = real_open(repo)
        if threading.current_thread() is not threading.main_thread():
            b_read.set()
        return res

    def accept(run, *a):
        if Path(run) == out_a:          # A is about to accept: let B read the open set now
            b.start()
            b_read.wait(2)
            real_accept(run, *a)
            a_done.set()
        else:
            a_done.wait(5)              # B accepts after A, as in the reproduction
            real_accept(run, *a)

    monkeypatch.setattr(cf_check, "open_set", open_set)
    monkeypatch.setattr(exposure, "accept", accept)
    monkeypatch.setattr(exposure, "record", lambda *a: None)
    b = threading.Thread(target=exposure.main, args=(
        ["check-run", str(out_b), str(exposure_repo), "--record", "--agents", "25"],))
    exposure.main(["check-run", str(out_a), str(exposure_repo), "--record", "--agents", "25"])
    b.join(30)
    assert set(real_open(exposure_repo)[0]) == {("lead", "lead-b")}


# R5-M3: the audit agent can see the inherited open set
def test_open_items_prints_the_inherited_open_set(exposure_repo, exposure_node, exposure_home):
    env = {**os.environ, "HOME": str(exposure_home)}
    assert cli("open-items", exposure_repo, env=env).stdout == \
        "no open items from earlier recorded runs\n"
    _run(exposure_repo, [lead("lead-a")], [unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)])
    lines = cli("open-items", exposure_repo, env=env).stdout.splitlines()
    assert lines == [f"open items from the run accepted {TODAY}: 2", "lead\tlead-a\tLead lead-a",
                     f"unit\t{unit('admin')['coverage_id']}\tadmin / Access control"]
    assert "exposure.py open-items ." in _deep_text()


# attacker pass: a marker the audit writes into its own folder is not the open set
def test_a_marker_the_audit_writes_never_becomes_the_open_set(exposure_repo, exposure_node):
    _run(exposure_repo, [lead("lead-a")])
    out = plant(exposure_repo)
    (out / exposure.ACCEPTED).write_text(json.dumps({
        "status": "pass", "digest": exposure._digest(out), "accepted_at": 2 ** 62, "open": []}))
    res = cf_check.check_run(out, exposure_repo)            # S7R3-M1: refused outright
    assert res["status"] == "pending" and "already holds gearbox-accepted.json" in res["detail"]
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]


def _kinds(res):
    return {(i["kind"], i["id"]) for i in res["open"]}


# land review MEDIUM (was R5-L1): a deleted newest run folder is pending, never an empty
# open set; repair-log rebuilds the open set from the older markers
def test_deleting_the_newest_accepted_run_is_pending_until_repair(exposure_repo, exposure_node,
                                                                 exposure_home):
    ledger = [unit("api", "candidate", result_fingerprints=["conf-1"])]
    _run(exposure_repo, [CONF], ledger)
    newest = plant(exposure_repo, [CONF], ledger=ledger)
    record_run(newest, exposure_repo)
    shutil.rmtree(newest)
    with pytest.raises(ValueError, match="logged run folder is missing"):
        exposure.open_set(exposure_repo)
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pending" and "open-set state unreadable" in res["detail"]
    p = cli("repair-log", exposure_repo, env={**os.environ, "HOME": str(exposure_home)})
    assert p.returncode == 0, p.stdout + p.stderr
    res = record_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "fail" and ("confirmed", "conf-1") in _kinds(res)
    assert "Deleting it leaves check-run `pending` until repair-log rebuilds" in _deep_text()


# land review HIGH: a critic clearing a LEAD never clears a CONFIRMED finding of that id,
# and a failed run never re-adds the lead beside the confirmed finding
def test_a_lead_clearance_never_clears_a_confirmed_finding(exposure_repo, exposure_node):
    _run(exposure_repo, [lead("conf-1")])
    b = record_run(plant(exposure_repo, [CONF], ledger=[unit("api", "candidate",
                   result_fingerprints=["conf-1"])], run_status="partial"), exposure_repo)
    assert ("confirmed", "conf-1") in _kinds(b) and ("lead", "conf-1") not in _kinds(b)
    why = [{"fingerprint": "conf-1", "reason": "The proxy strips X-User (src/admin.py:1)."}]
    c = record_run(plant(exposure_repo, resolved_prior_leads=why), exposure_repo)
    assert c["status"] == "fail" and ("confirmed", "conf-1") in _kinds(c), c["detail"]


def test_a_critic_entry_clears_only_the_lead_of_that_id(exposure_repo):
    item = {"label": "conf-1", "ref": None, "files": None}
    old = {("confirmed", "conf-1"): item, ("lead", "conf-1"): item}
    meta = {"resolved_prior_leads": [{"fingerprint": "conf-1", "reason": "Fixed."}]}
    cleared = cf_check._cleared(old, meta, [])
    assert set(cleared) == {("lead", "conf-1")}
    now, _, _ = cf_check._carry(old, [], [], cleared)
    assert set(now) == {("confirmed", "conf-1")} and cf_check._status(now, []) == "fail"


# S7-M3: order comes only from accept()'s own log; timestamps are bounded
def test_a_planted_marker_is_ignored_and_overwritten_end_to_end(exposure_repo, exposure_node, exposure_home):
    health.collect(exposure_repo)
    _cli_record(exposure_repo, plant(exposure_repo, [lead("lead-a")]), exposure_home)
    out = plant(exposure_repo)
    (out / exposure.ACCEPTED).write_text(json.dumps({
        "status": "pass", "digest": exposure._digest(out), "accepted_at": 10 ** 30, "open": []}))
    assert out not in exposure.prior_runs(exposure_repo)       # accept() never named it
    p = _cli_record(exposure_repo, out, exposure_home)      # S7R3-M1: refused, left alone
    assert p.stdout.startswith("pending\nrun folder already holds gearbox-accepted.json")
    assert json.loads((out / exposure.ACCEPTED).read_text())["accepted_at"] == 10 ** 30
    p = _cli_record(exposure_repo, plant(exposure_repo), exposure_home)        # the next run does not overflow
    assert p.returncode == 0 and p.stdout.startswith("warn"), p.stdout + p.stderr
    assert "lead\tlead-a" in cli("open-items", exposure_repo, env={**os.environ, "HOME": str(exposure_home)}
                                 ).stdout


def test_a_tampered_newest_marker_is_pending(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    mark = json.loads((out / exposure.ACCEPTED).read_text())
    (out / exposure.ACCEPTED).write_text(json.dumps({**mark, "accepted_at": 10 ** 30}))
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pending" and "open-set state unreadable" in res["detail"]


# S7-M5, reversed at land review r5: no hint says adding `revalidates` would clear it,
# because a revalidation unit never clears anything (real validators)
@needs_node
def test_a_revalidation_unit_without_the_link_gets_no_may_fix_hint(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "src/api.py", "x = 2\n")
    res = _run(exposure_repo, [], [unit("api", prior_status="prior_confirmed_changed_source")])
    assert res["status"] == "fail" and _kinds(res) == {("confirmed", "conf-1")}, res
    assert "may fix" not in res["detail"] and "hint:" not in res["detail"]


# S7-L1: one exposed audit per repo at a time
def test_deep_text_says_one_exposed_audit_at_a_time():
    assert "one exposed audit per repo at a time" in _deep_text()


# s07 inventory: listing prior runs never blocks on a FIFO swapped into a run folder
def test_prior_runs_skip_a_run_whose_document_became_a_fifo(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    (out / "findings.json").unlink()
    os.mkfifo(out / "findings.json")

    class Blocked(BaseException):        # not an OSError, so no reader's except can swallow it
        pass

    def blocked(*_):                     # without the guard, open() waits for a writer forever
        raise Blocked("prior_runs blocked on a FIFO")
    old = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(10)
    try:
        assert exposure.prior_runs(exposure_repo) == []
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]   # its obligations hold


# ------------------------------------------------------------ s07 rework round 2

# S7R2-H1: an unvalidated candidate stays open until a final record settles it
@needs_node
def test_unresolved_candidate_stays_open_across_recorded_runs(exposure_repo):
    res = _run(exposure_repo, [], [unit("api", "candidate", result_fingerprints=["lost-candidate"])])
    assert res["status"] == "warn"
    assert ("candidate", "lost-candidate") in {(i["kind"], i["id"]) for i in res["open"]}
    for _ in range(2):                              # runs 2 and 3 omit it
        res = _run(exposure_repo)
        assert res["status"] == "warn"
        assert "missing here: unvalidated candidate lost-candidate" in res["detail"]
    res = _run(exposure_repo, [rejected("lost-candidate")])
    assert res["status"] == "pass", res
    assert "unvalidated candidate lost-candidate (rejected)" in res["detail"]


@needs_node
def test_archived_candidate_stays_open_until_a_final_record(exposure_repo):
    assert _run(exposure_repo, [], archived_candidate())["status"] == "warn"
    res = _run(exposure_repo)
    assert res["status"] == "warn" and "unvalidated candidate cand-1" in res["detail"]
    res = _run(exposure_repo, [lead("cand-1")])     # settled, but as a lead: still open
    assert {(i["kind"], i["id"]) for i in res["open"]} == {("lead", "cand-1")}
    assert _run(exposure_repo, [dict(CONF, fingerprint="cand-1")], [unit(
        "api", "candidate", result_fingerprints=["cand-1"])])["status"] == "fail"


@pytest.mark.parametrize("state", ["blocked", "out_of_scope", "planned", "in_progress"])
def test_unfinished_unit_stays_open_across_recorded_runs(exposure_repo, exposure_node, state):
    _run(exposure_repo, [], [unit("api"), unit("admin", state, unresolved=["needs the owner"])])
    res = _run(exposure_repo)                       # run 2 drops the unit
    assert res["status"] == "warn"
    assert "missing here: open unit admin / Access control" in res["detail"]
    assert _run(exposure_repo, [], [unit("api"), unit("admin")])["status"] == "pass"


# S7R2-M1: a tampered marker label cannot crash a print
def test_marker_label_that_is_not_utf8_is_refused(exposure_repo, exposure_node, exposure_home):
    out = plant(exposure_repo, [lead("lead-a")])
    record_run(out, exposure_repo)
    mark = json.loads((out / exposure.ACCEPTED).read_text())
    mark["open"][0]["label"] = "bad \ud800"
    (out / exposure.ACCEPTED).write_text(json.dumps(mark))
    p = cli("open-items", exposure_repo, env={**os.environ, "HOME": str(exposure_home)})
    assert p.returncode == 2 and p.stdout.startswith("error: ")
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pending" and "open-set state unreadable" in res["detail"]


# S7R2-M2 + C8-M1: repair-log never loses an item; an unreadable marker refuses the repair
def test_repair_log_keeps_every_open_item(exposure_repo, exposure_node, exposure_home):
    env = {**os.environ, "HOME": str(exposure_home)}
    _run(exposure_repo, [lead("lead-a")], [unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)])
    _run(exposure_repo, [lead("lead-b")])           # lead-a and admin carried
    third = plant(exposure_repo, [CONF], ledger=[unit("api", "candidate",
                                                      result_fingerprints=["conf-1"])])
    record_run(third, exposure_repo)
    good = (third / exposure.ACCEPTED).read_text()
    (third / exposure.ACCEPTED).write_text("{broken")           # the newest state cannot be read
    log = exposure.runs_parent(exposure_repo) / exposure.ACCEPTED_LOG
    log.write_text("garbage\n")
    p = cli("repair-log", exposure_repo, env=env)
    assert p.returncode == 2 and "nothing written" in p.stdout, p.stdout   # it may hold conf-1
    assert log.read_text() == "garbage\n"
    assert "open-set state unreadable" in cf_check.check_run(plant(exposure_repo),
                                                             exposure_repo)["detail"]
    (third / exposure.ACCEPTED).write_text(good)                # the owner restores it
    p = cli("repair-log", exposure_repo, env=env)
    assert (p.returncode, p.stdout.splitlines()[0]) == (
        0, "repaired: 3 acceptance markers read, 4 open items kept"), p.stdout
    res = record_run(plant(exposure_repo), exposure_repo)        # usable again, nothing lost
    assert res["status"] == "fail"
    assert {i["id"] for i in res["open"]} == {"lead-a", "lead-b", "conf-1",
                                              unit("admin")["coverage_id"]}
    assert "exposure.py repair-log ." in _deep_text()


def _files(res):
    return [(i["files"], i["ref"]) for i in res["open"] if (i["kind"], i["id"]) == (
        "confirmed", "conf-1")]


def _at_admin(rec):
    return dict(rec, trace=[dict(rec["trace"][0], file="src/admin.py")],
                evidence=[dict(rec["evidence"][0], file="src/admin.py")])


# land review r2 HIGH: an incomplete run that demotes a confirmed finding to a lead, or
# re-reports it with other files, never replaces the confirmed finding's files and ref
@needs_node
def test_an_incomplete_demotion_keeps_the_confirmed_trace_files(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    kept = [(["src/api.py", "src/db.py"], exposure.head(exposure_repo))]
    for rec in (lead("conf-1"), CONF):
        res = record_run(plant(exposure_repo, [_at_admin(rec)], run_status="partial"),
                         exposure_repo)
        assert res["status"] == "fail" and _files(res) == kept, res["open"]
    _commit_change(exposure_repo, "src/admin.py", "x = 2\n")    # not a file of conf-1
    res = _run(exposure_repo, [], [_revalidation("admin")])
    assert res["status"] == "fail" and _files(res) == kept, res
    assert "resolved here" not in res["detail"]


@needs_node
def test_a_complete_run_rereporting_a_confirmed_finding_takes_its_new_files(exposure_repo):
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "app.py", "print('v2')\n")
    res = _run(exposure_repo, [_at_admin(CONF)],
               [unit("admin", "candidate", result_fingerprints=["conf-1"])])
    assert res["status"] == "fail" and "incomplete run" not in res["detail"], res["detail"]
    assert _files(res) == [(["src/admin.py"], exposure.head(exposure_repo))]


# land review r3 MEDIUM: an incomplete run that confirms an inherited lead at another file
# keeps the lead's files, so a change to that other file never clears it; the ref moves
# to that run (land review r4 HIGH)
@needs_node
def test_an_incomplete_confirmation_keeps_the_inherited_lead_files(exposure_repo):
    res = _run(exposure_repo, [lead("conf-1")])
    assert res["status"] == "warn", res
    _commit_change(exposure_repo, "app.py", "print('v2')\n")      # so the ref moves on
    kept = [(["src/api.py"], exposure.head(exposure_repo))]
    res = record_run(plant(exposure_repo, [_at_admin(CONF)], run_status="partial",
                           ledger=[unit("admin", "candidate", result_fingerprints=["conf-1"])]),
                     exposure_repo)
    assert res["status"] == "fail" and _files(res) == kept, res["open"]
    _commit_change(exposure_repo, "src/admin.py", "x = 2\n")    # not a file of the lead
    res = _run(exposure_repo, [], [_revalidation("admin")])
    assert res["status"] == "fail" and _files(res) == kept, res
    assert "resolved here" not in res["detail"]


@needs_node
def test_a_complete_run_confirming_an_inherited_lead_takes_its_new_files(exposure_repo):
    _run(exposure_repo, [lead("conf-1")])
    _commit_change(exposure_repo, "app.py", "print('v2')\n")
    res = _run(exposure_repo, [_at_admin(CONF)],
               [unit("admin", "candidate", result_fingerprints=["conf-1"])])
    assert res["status"] == "fail" and "incomplete run" not in res["detail"], res["detail"]
    assert _files(res) == [(["src/admin.py"], exposure.head(exposure_repo))]


# land review r4 HIGH: a failed run that reports an item again moves its ref to this run,
# so a change made BEFORE that report never clears it later
@needs_node
@pytest.mark.parametrize("failed", [True, False])
def test_a_rereport_moves_the_ref_so_an_earlier_change_never_clears(exposure_repo, failed):
    cand = [unit("api", "candidate", result_fingerprints=["conf-1"])]
    _run(exposure_repo, [CONF], cand)
    _commit_change(exposure_repo, "src/api.py", "x = 2  # touched, bug still present\n")
    meta = {"run_status": "partial"} if failed else {}
    mid = record_run(plant(exposure_repo, [CONF], ledger=cand, **meta), exposure_repo)
    res = _run(exposure_repo, [], [_revalidation("api")])      # nothing changed since
    assert res["status"] == "fail" and "resolved here" not in res["detail"], res
    assert _files(mid) == [(["src/api.py", "src/db.py"], exposure.head(exposure_repo))]


@needs_node
def test_a_failed_confirmation_of_a_lead_moves_the_ref_and_keeps_the_lead_files(exposure_repo):
    _run(exposure_repo, [lead("conf-1")])
    _commit_change(exposure_repo, "src/api.py", "x = 2\n")
    mid = record_run(plant(exposure_repo, [_at_admin(CONF)], run_status="partial", ledger=[
        unit("admin", "candidate", result_fingerprints=["conf-1"])]), exposure_repo)
    res = _run(exposure_repo, [], [_revalidation("api")])      # nothing changed since
    assert res["status"] == "fail" and "resolved here" not in res["detail"], res
    assert _files(mid) == [(["src/api.py"], exposure.head(exposure_repo))], mid["open"]


# land review r5 (owner option 2): ported repros. Revalidation clears nothing, whichever
# files a re-report stored and whenever the source changed; each item stays open
def _demoted_complete_then_admin_changed(r):
    _run(r, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _run(r, [_at_admin(lead("conf-1"))], [unit("admin")])          # complete: lead at admin
    _commit_change(r, "src/admin.py", "x = 3  # unrelated edit\n")
    return _run(r, [], [_revalidation("admin")])


def _failed_confirmation_elsewhere_then_api_changed(r):
    _run(r, [lead("conf-1")])
    record_run(plant(r, [_at_admin(CONF)], run_status="partial",
                     ledger=[unit("admin", "candidate", result_fingerprints=["conf-1"])]), r)
    _commit_change(r, "src/api.py", "x = 3\n")
    return _run(r, [], [_revalidation("api")])


def _failed_rejection_after_an_own_file_change(r):      # Codex r5 HIGH
    _run(r, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(r, "src/api.py", "x = 2\n")
    record_run(plant(r, [rejected("conf-1")], ledger=[unit("api")], run_status="partial"), r)
    return _run(r, [], [_revalidation("api")])


@needs_node
@pytest.mark.parametrize("scenario", [_demoted_complete_then_admin_changed,
                                      _failed_confirmation_elsewhere_then_api_changed,
                                      _failed_rejection_after_an_own_file_change],
                         ids=["demote_complete", "failed_confirm_other_file", "failed_rejection"])
def test_r5_repros_never_clear_by_revalidation(exposure_repo, scenario):
    res = scenario(exposure_repo)
    assert res["status"] == "fail" and _kinds(res) == {("confirmed", "conf-1")}, res
    assert "resolved here" not in res["detail"], res["detail"]
