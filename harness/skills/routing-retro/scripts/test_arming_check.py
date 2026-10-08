"""Self-checks for arming_check.py (s07/PF-01, MOVE 5).

s09 runs this script to decide whether a criterion can be judged at all, so the
suite carries a KNOWN POSITIVE that arms all nine criteria plus one negative per
branch, each failing for ITS OWN reason. A passing suite you have not seen fail
proves nothing — every assertion here was checked against a deliberately broken
copy of the module before it was kept.

Run: pytest skills/routing-retro/scripts/test_arming_check.py -q
"""

import json

import arming_check as ac

HOOKS_TS = "2026-08-22T09:00:00Z"
LAST_TS = "2026-08-22T10:00:00Z"      # base_context + routing, one deploy
AFTER = "2026-08-22T11:00:00Z"


def _act(iv, ts, seq=1, **over):
    row = {"schema": 1, "event_id": f"{iv}#{seq}", "ts_utc": ts, "intervention": iv,
           "scope": "s", "evidence": "e", "source": "session"}
    row.update(over)
    return row


def _write(path, rows):
    path.write_text("".join((json.dumps(r) if isinstance(r, dict) else r) + "\n"
                            for r in rows))


def _root(tmp_path, activations, decisions=(), sessions=(), version=1):
    root = tmp_path / "dyno"
    (root / "policy").mkdir(parents=True)
    _write(root / "activations.ndjson", activations)
    _write(root / "decisions.ndjson", decisions)
    _write(root / "sessions.ndjson", sessions)
    (root / "policy" / "VERSION.json").write_text(json.dumps({"policy_version": version}))
    return root


def _outcomes(tmp_path, n=6, ctx=True):
    p = tmp_path / "outcomes.ndjson"
    _write(p, [{"ts": AFTER, "session": f"s{i}", "task_class": "agentic_build",
                "orchestrator_ctx_tokens": (1000 + i) if ctx else None}
               for i in range(n)])
    return str(p)


def _full_activations():
    """One activation row per name in compaction_ledger.INTERVENTIONS.

    Criteria 7 and 9 are bound to the WHOLE tuple, so a name added to it and not
    to this fixture leaves them unexposed and the known positive stops being one.
    That is what `compact_window` did when the reader learned the name the writer
    already emitted, so the list is asserted against the tuple below.
    """
    return [_act("hooks", HOOKS_TS), _act("repo_diet", "2026-08-22T09:30:00Z"),
            _act("compact_window", "2026-08-22T09:45:00Z"),
            _act("base_context", LAST_TS), _act("routing", LAST_TS)]


def test_the_known_positive_activates_every_intervention_the_reader_knows():
    """Guards the fixture itself: a new INTERVENTIONS name must arrive here too,
    or criteria 7 and 9 silently go unexposed again."""
    from compaction_ledger import INTERVENTIONS
    assert {a["intervention"] for a in _full_activations()} == set(INTERVENTIONS)


def _known_positive(tmp_path, n_sessions=16, per_session=3):
    """40+ live COMPACTION records at the current policy_version over >= 15
    sessions, all of them started after EVERY intervention's activation. The
    event must be a compaction one: this fixture once used `event: prompt` and
    thereby pinned the very defect the ledger criteria had — arming on
    prompt-classification lines."""
    sids = [f"s{i:02d}" for i in range(n_sessions)]
    decisions = [{"ts": AFTER, "session": sid, "event": "pre-compact", "source": "live",
                  "policy_version": 1, "reason": "safe_point_ok", "decision": "allow"}
                 for sid in sids for _ in range(per_session)]
    sessions = [{"ts": AFTER, "session": sid, "type": "default", "policy_version": 1,
                 "kill_switch": "off", "hooks_registered": True} for sid in sids]
    root = _root(tmp_path, _full_activations(), decisions, sessions)
    scan = {"sessions": [{"session_id": sid, "started": AFTER,
                          "ended": "2026-08-22T11:30:00Z"} for sid in sids]}
    return root, scan


def _armed(rep):
    return {c["criterion"] for c in rep["criteria"] if c["armed"]}


# --------------------------------------------------------------------------
# The known positive
# --------------------------------------------------------------------------
def test_known_positive_arms_all_nine_criteria(tmp_path):
    root, scan = _known_positive(tmp_path)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert _armed(rep) == set(range(1, 10)), rep["criteria"]
    assert rep["decision"] == "ANY ARMED"


def test_report_renders_without_a_scan_file(tmp_path):
    root, _ = _known_positive(tmp_path)
    rep = ac.check(scan=None, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert "ARMING CHECK" in ac.format_report(rep)


# --------------------------------------------------------------------------
# One negative per branch — each failing for its OWN reason
# --------------------------------------------------------------------------
def test_no_activations_at_all_is_blocked(tmp_path):
    root = _root(tmp_path, [])
    rep = ac.check(scan={"sessions": []}, root=str(root))
    assert rep["decision"] == "BLOCKED"
    assert rep["ledgers"]["activations"]["any_object"] is False
    assert _armed(rep) == {8}          # integrity only, and it never decides


def test_a_malformed_only_activation_ledger_is_partial_not_blocked(tmp_path):
    """"an activation object exists" is tracked SEPARATELY from "a valid
    activation was derived"."""
    root = _root(tmp_path, [_act("hooks", HOOKS_TS, schema=2)])
    rep = ac.check(scan={"sessions": []}, root=str(root))
    assert rep["decision"] == "PARTIAL"
    assert rep["ledgers"]["activations"]["any_object"] is True
    assert rep["ledgers"]["activations"]["any_valid"] is False


def test_trailing_newline_event_id_does_not_arm_its_criteria(tmp_path):
    root, scan = _known_positive(tmp_path)
    acts = _full_activations()
    acts[0] = _act("hooks", HOOKS_TS, **{"event_id": "hooks#1\n"})
    _write(root / "activations.ndjson", acts)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    # The row is refused AND it names `hooks`, so the ledger is broken for hooks
    # rather than silent about it. Either way the criteria do not arm.
    assert rep["interventions"]["hooks"]["status"] == "activation_unknown"
    assert 1 not in _armed(rep) and 4 not in _armed(rep)


def test_schema_other_than_one_does_not_arm_its_criteria(tmp_path):
    root, scan = _known_positive(tmp_path)
    acts = _full_activations()
    acts[3] = _act("routing", LAST_TS, schema="1")
    _write(root / "activations.ndjson", acts)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert 5 not in _armed(rep) and 6 not in _armed(rep)


def test_a_corrupt_ledger_line_refuses_the_ledger_criteria_outright(tmp_path):
    """A partly corrupt ledger must never arm a compaction-ledger criterion —
    a review round found exactly that hole."""
    root, scan = _known_positive(tmp_path)
    with open(root / "decisions.ndjson", "a", encoding="utf-8") as f:
        f.write("not json at all\n")
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert rep["ledgers"]["decisions"]["rejected_lines"] == 1
    assert _armed(rep).isdisjoint({1, 2, 7, 9})


def test_a_non_object_ledger_line_is_rejected_and_counted(tmp_path):
    root, scan = _known_positive(tmp_path)
    with open(root / "decisions.ndjson", "a", encoding="utf-8") as f:
        f.write("[1, 2, 3]\n")
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert rep["ledgers"]["decisions"]["rejected_lines"] == 1


def test_a_ledger_below_its_floor_does_not_arm(tmp_path):
    root, scan = _known_positive(tmp_path, n_sessions=16, per_session=1)   # 16 < 40 records
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert _armed(rep).isdisjoint({1, 2, 7, 9})
    detail = next(c["detail"] for c in rep["criteria"] if c["criterion"] == 1)
    assert "16/40" in detail


def test_too_few_distinct_sessions_does_not_arm(tmp_path):
    root, scan = _known_positive(tmp_path, n_sessions=5, per_session=10)   # 50 records, 5 sessions
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert _armed(rep).isdisjoint({1, 2, 7, 9})


def test_an_activation_with_a_zero_after_cohort_does_not_arm(tmp_path):
    root, scan = _known_positive(tmp_path)
    acts = _full_activations()
    acts[3] = _act("routing", "2026-08-23T00:00:00Z")    # after every session
    _write(root / "activations.ndjson", acts)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert rep["interventions"]["routing"]["after_sessions"] == 0
    assert 5 not in _armed(rep) and 6 not in _armed(rep)


def test_a_session_that_straddles_an_activation_is_in_no_after_cohort(tmp_path):
    root, scan = _known_positive(tmp_path)
    scan["sessions"].append({"session_id": "straddler", "started": "2026-08-22T08:00:00Z",
                             "ended": "2026-08-22T23:00:00Z"})
    with open(root / "sessions.ndjson", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-08-22T08:00:00Z", "session": "straddler",
                            "type": "default", "policy_version": 1,
                            "kill_switch": "off", "hooks_registered": True}) + "\n")
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert rep["interventions"]["hooks"]["spanning"] == 1
    assert rep["interventions"]["hooks"]["after_sessions"] == 16     # unchanged


def test_no_orchestrator_ctx_tokens_leaves_criterion_four_unarmed(tmp_path):
    root, scan = _known_positive(tmp_path)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path, ctx=False))
    assert 4 not in _armed(rep)
    assert 1 in _armed(rep)          # its siblings are unaffected


def test_missing_outcomes_ledger_is_a_result_not_an_exception(tmp_path):
    root, scan = _known_positive(tmp_path)
    rep = ac.check(scan=scan, root=str(root), outcomes_path=str(tmp_path / "nope.ndjson"))
    assert rep["ledgers"]["outcomes"]["exists"] is False
    assert _armed(rep).isdisjoint({4, 5, 6})


def test_integrity_criterion_never_masks_nothing_was_ever_live(tmp_path):
    """C8 is always armed. It must NEVER be what turns 'nothing was ever live'
    into ANY ARMED — a review round found precisely that."""
    root = _root(tmp_path, [])
    rep = ac.check(scan={"sessions": []}, root=str(root))
    assert 8 in _armed(rep)
    assert rep["decision"] == "BLOCKED"


def test_a_multi_intervention_criterion_uses_the_intersection_not_the_union(tmp_path):
    """Criteria 7 and 9 depend on all four interventions, so only a session
    exposed to ALL of them can speak to them."""
    root, scan = _known_positive(tmp_path)
    acts = _full_activations()
    acts[3] = _act("routing", "2026-08-22T10:30:00Z")
    _write(root / "activations.ndjson", acts)
    for s in scan["sessions"][:8]:
        s["started"] = "2026-08-22T10:15:00Z"       # after hooks, BEFORE routing
        s["ended"] = "2026-08-22T10:20:00Z"
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert 1 in _armed(rep)                          # hooks-only criterion still fine
    assert 7 not in _armed(rep) and 9 not in _armed(rep)


def test_missing_every_ledger_is_reported_never_raised(tmp_path):
    rep = ac.check(scan=None, root=str(tmp_path / "nothing-here"))
    assert rep["decision"] == "BLOCKED"
    assert rep["ledgers"]["decisions"]["exists"] is False


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def test_an_undated_outcome_record_arms_nothing(tmp_path):
    """C4 and C5/6 must agree: a record with no `ts` cannot be shown to post-date
    the activation, so it is evidence for neither. Before the fix, C4's
    `(parse_ts(ts) or act_ts) >= act_ts` was unconditionally true for an undated
    row and armed 6/5 while its sibling refused the identical rows 0/5."""
    root, scan = _known_positive(tmp_path)
    p = tmp_path / "undated.ndjson"
    _write(p, [{"session": f"s{i}", "task_class": "agentic_build",
                "orchestrator_ctx_tokens": 1000 + i} for i in range(6)])
    rep = ac.check(scan=scan, root=str(root), outcomes_path=str(p))
    by = {c["criterion"]: c for c in rep["criteria"]}
    assert not by[4]["armed"], "undated outcome records must not arm criterion 4"
    assert not by[5]["armed"]
    assert not by[6]["armed"]


def test_prompt_classification_lines_never_arm_the_ledger_criteria(tmp_path):
    """C1's stated evidence is the compaction ledger. 40+ live `event: prompt`
    rows are prompt classifications, not compactions — arming "the veto is
    honoured" on them arms it on evidence that is not about vetoes at all."""
    sids = [f"s{i:02d}" for i in range(16)]
    decisions = [{"ts": AFTER, "session": sid, "event": "prompt", "source": "live",
                  "policy_version": 1, "reason": "classified:default", "decision": "allow"}
                 for sid in sids for _ in range(3)]
    sessions = [{"ts": AFTER, "session": sid, "type": "default", "policy_version": 1,
                 "kill_switch": "off", "hooks_registered": True} for sid in sids]
    root = _root(tmp_path, _full_activations(), decisions, sessions)
    scan = {"sessions": [{"session_id": sid, "started": AFTER,
                          "ended": "2026-08-22T11:30:00Z"} for sid in sids]}
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    assert not ({1, 2, 7, 9} & _armed(rep)), \
        "prompt-classification lines must not arm the compaction-ledger criteria"


def test_a_ledger_of_hook_crashes_never_arms_the_ledger_criteria(tmp_path):
    """A fail-open crash writes a spine-valid pre-compact row (reason
    'hook-error', decision 'allow'). 48 crashes over 16 sessions are not
    evidence the veto is honoured — they are evidence the instrument is down."""
    root, scan = _known_positive(tmp_path)
    sids = [f"s{i:02d}" for i in range(16)]
    _write(root / "decisions.ndjson",
           [{"ts": AFTER, "session": sid, "event": "pre-compact", "source": "live",
             "policy_version": 1, "reason": "hook-error", "decision": "allow"}
            for sid in sids for _ in range(3)])
    rep = ac.check(scan=scan, root=str(root), outcomes_path=_outcomes(tmp_path))
    armed = _armed(rep)
    assert not armed & {1, 2, 7, 9}, f"crash rows armed {armed & {1, 2, 7, 9}}"


def test_criterion_3_arms_from_either_of_its_interventions_alone(tmp_path):
    """Criterion 3 is deliberately a UNION over its two interventions — unlike
    _ledger_criterion's intersection — because base context falls from either
    intervention on its own. This pins the documented choice: a repo_diet-only
    rollout with enough after-sessions arms it."""
    sids = [f"s{i}" for i in range(5)]
    root = _root(tmp_path, [_act("repo_diet", HOOKS_TS)],
                 sessions=[{"ts": AFTER, "session": sid, "type": "default",
                            "policy_version": 1, "kill_switch": "off",
                            "hooks_registered": True} for sid in sids])
    rep = ac.check(scan=None, root=str(root))
    assert 3 in _armed(rep)


def test_unusable_scan_refuses_rather_than_reporting_a_different_population(
        tmp_path, capsys):
    """A --scan the reader cannot use was discarded in silence, so the gate ran
    on heartbeat-only dates and printed a complete ANY ARMED report, exit 0,
    over a population the caller never asked for. Every unusable shape refuses,
    each for its own reason — and a usable one still runs."""
    missing = tmp_path / "gone.json"
    broken = tmp_path / "broken.json"
    broken.write_text("not json{")
    scalar = tmp_path / "scalar.json"
    scalar.write_text('"a string"')
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"sessions": []}))

    for bad in (missing, broken, scalar):
        assert ac.main(["--scan", str(bad)]) == 2
        assert "Refusing to report" in capsys.readouterr().err

    assert ac.main(["--scan", str(good)]) == 0
    assert ac.main([]) == 0
