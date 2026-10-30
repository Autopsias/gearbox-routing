"""One exhaustive test of the clearing rule (owner, land review r5): revalidation never
clears an open finding. A confirmed finding or lead leaves the open set only on a
COMPLETE run's rejected record; a lead also leaves by a complete confirmation (it lives
on as confirmed) or a critic entry. A failed run clears nothing.

Every combination: an item first opened at F (lead or confirmed), a next run that
reports it (or not) at F or at G, complete or failed (two ways: run_status partial, or
a report file missing), a source change at one point, then a complete run that
revalidates F and G. Real validators, as the other tests."""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from exposure_testlib import (  # noqa: E402
    CONF, _commit_change, _revalidation, _run, lead, needs_node, plant, record_run, rejected,
    unit)
from revalidation import REVALIDATION  # noqa: E402

F, G = "api", "admin"               # src/api.py is a file of both fixtures; admin.py of neither
CHANGES = ("none", "F before", "F after", "G after")    # relative to the next run (the re-report)
# shortcut: a candidate at F and at G are the same for the item (neither is its record),
# so only the F candidate runs; add G if candidates ever start carrying files.
REPORTS = [("none", F), ("lead", F), ("lead", G), ("confirmed", F), ("confirmed", G),
           ("candidate", F), ("rejected", F), ("rejected", G)]
FAILS = ("complete", "partial", "no-report")      # how the next run ends
# shortcut: each way to fail runs with half the source changes (128 cases, not 192), to keep
# the matrix fast; run every pair if a failure cause ever depends on the source change.
CASES = [c for c in itertools.product(("lead", "confirmed"), REPORTS, FAILS, CHANGES)
         if c[2] == "complete" or (c[2] == "partial") == (CHANGES.index(c[3]) % 2 == 0)]


def _at(rec, surface):
    path = f"src/{surface}.py"
    return dict(rec, trace=[dict(rec["trace"][0], file=path)],
                evidence=[dict(rec["evidence"][0], file=path)])


def _next_run(kind, surface):
    """(findings, ledger) of the next run, reporting conf-1 as `kind` at `surface`."""
    cand = [unit(surface, "candidate", result_fingerprints=["conf-1"])]
    return {"none": ([], [unit(F)]), "candidate": ([], cand),
            "lead": ([_at(lead("conf-1"), surface)], [unit(surface)]),
            "confirmed": ([_at(CONF, surface)], cand),
            "rejected": ([_at(rejected("conf-1"), surface)], cand)}[kind]


def _expected(inherited, kind, failed):
    """The rule, from the timeline alone, never from the code: the kind conf-1 is open as
    at the end, or None once it left. Only a complete run's rejected record clears it; a
    confirmed report makes it confirmed (a complete one is how a lead leaves); revalidation,
    source changes and files never matter."""
    if kind == "rejected" and not failed:
        return None
    return "confirmed" if "confirmed" in (inherited, kind) else "lead"


def _item(res):
    return [i["kind"] for i in res["open"] if i["id"] == "conf-1" and i["kind"] in REVALIDATION]


def _open_at_f(r, inherited):
    if inherited == "lead":
        _run(r, [lead("conf-1")])
    else:
        _run(r, [CONF], [unit(F, "candidate", result_fingerprints=["conf-1"])])


@needs_node
@pytest.mark.parametrize("inherited,report,how,change", CASES,
                         ids=lambda v: "@".join(v) if isinstance(v, tuple) else str(v).replace(" ", "-"))
def test_only_a_complete_rejection_clears_never_revalidation(
        exposure_repo, inherited, report, how, change):
    r, combo, failed = exposure_repo, f"{inherited=} {report=} {how=} {change=}", how != "complete"
    _open_at_f(r, inherited)
    if change == "F before":
        _commit_change(r, "src/api.py", "x = 2  # bug still present\n")
    findings, ledger = _next_run(*report)
    out = plant(r, findings, ledger=ledger, **({"run_status": "partial"} if how == "partial" else {}))
    if how == "no-report":
        (out / "REPORT.md").unlink()
    mid = record_run(out, r)
    want = _expected(inherited, report[0], failed)
    assert _item(mid) == ([want] if want else []), (combo, mid["open"])
    # the status rule, from the timeline: a failed run gives at least warn
    assert mid["status"] == ("fail" if want == "confirmed" else "warn" if failed or want
                             else "pass"), (combo, mid)
    if failed:
        assert ("run_status partial" if how == "partial"
                else "report file not readable text: REPORT.md") in mid["detail"], (combo, mid)
    if change in ("F after", "G after"):
        _commit_change(r, f"src/{F if change == 'F after' else G}.py", "x = 3\n")
    final = _run(r, [], [_revalidation(s, prior=REVALIDATION[want or inherited]) for s in (F, G)])
    assert "resolved here" not in final["detail"], (combo, mid["open"], final["detail"])
    if want is None:
        # known positive: a complete rejection clears it, and the matrix can tell
        assert "resolved here" in mid["detail"] and _item(final) == [], (combo, final)
        assert final["status"] == "pass", (combo, final)
    else:
        assert _item(final) == [want], (combo, mid["open"], final)
        assert final["status"] == ("fail" if want == "confirmed" else "warn"), (combo, final)


# known-positive controls beside the matrix: a critic entry clears a lead, never a
# confirmed finding, even beside a changed own file and a revalidation unit
@needs_node
@pytest.mark.parametrize("inherited", ["lead", "confirmed"])
def test_a_critic_entry_clears_a_lead_never_a_confirmed_finding(exposure_repo, inherited):
    r = exposure_repo
    _open_at_f(r, inherited)
    _commit_change(r, "src/api.py", "x = 3\n")
    why = [{"fingerprint": "conf-1", "reason": "The owner check was added (src/api.py:1)."}]
    final = record_run(plant(r, [], ledger=[_revalidation(F, prior=REVALIDATION[inherited])],
                             resolved_prior_leads=why), r)
    if inherited == "lead":
        assert _item(final) == [] and final["status"] == "pass", final
        assert "(critic: The owner check was added" in final["detail"]
    else:
        assert _item(final) == ["confirmed"] and final["status"] == "fail", final
        assert "resolved here" not in final["detail"]


def test_the_matrix_covers_the_whole_space():
    assert len(CASES) == 2 * len(REPORTS) * 2 * len(CHANGES) == 128
    for how in FAILS:       # every way to end the next run meets every item and report
        assert {c[:2] for c in CASES if c[2] == how} == set(
            itertools.product(("lead", "confirmed"), REPORTS))
