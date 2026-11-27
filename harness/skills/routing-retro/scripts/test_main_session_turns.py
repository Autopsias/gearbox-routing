"""Self-check for main_session_turns.py: thresholds fire only above their size, bad lines are counted.

Run: ~/.claude/scripts/pytest-m -q skills/routing-retro/scripts/test_main_session_turns.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks"))
pytest.importorskip("model_nudge_lib", reason="hooks/model_nudge_lib.py is not shipped in this export")

import main_session_turns as mst  # noqa: E402

NOW = datetime(2026, 12, 1, 12, 0, 0, tzinfo=timezone.utc)


def rec(session="s1", turn=1, ts="2026-11-30T10:00:00Z", task="quick", model="claude-haiku-5-5", effort="high", **kw):
    r = {"ts": ts, "session": session, "turn": turn, "task_type": task, "model": model, "effort": effort,
         "pushback_count": 0, "effort_source": "stop", "entrypoint": "cli", "nudge_shown": False, "would_nudge": False}
    r.update(kw)
    return {k: v for k, v in r.items() if v is not None}


def run(tmp_path, recs, raw=(), **kw):
    p = tmp_path / "ledger.ndjson"
    p.write_text("\n".join([json.dumps(r) for r in recs] + list(raw)) + "\n")
    return mst.analyse(p, now=NOW, **kw)


def proposals(out, kind, status="proposed"):
    return [p for p in out["table_proposals"] if p["kind"] == kind and p["status"] == status]


def test_since_is_inclusive_at_the_boundary(tmp_path):
    recs = [rec(ts="2026-11-18T23:59:59Z"), rec(turn=2, ts="2026-11-19T00:00:00Z"), rec(turn=3, ts="2026-11-20T00:00:00Z")]
    out = run(tmp_path, recs, since="2026-11-19")
    assert out["health"]["lines"] == 2 and out["before_since"] == 1


def test_three_malformed_lines_print_malformed_three(tmp_path):
    bad = ["not json", json.dumps(rec(task="bogus_type")), json.dumps(rec(effort="turbo"))]
    out = run(tmp_path, [rec()], raw=bad)
    assert out["malformed"] == 3
    assert out["malformed_by_field"] == {"json": 1, "task_type": 1, "effort": 1}
    assert out["health"]["lines"] == 1


def test_non_interactive_and_non_owner_switch_lines_are_counted_by_value(tmp_path):
    recs = [rec(entrypoint="sdk-cli"), rec(turn=2, entrypoint="sdk-cli"),
            rec(turn=3, model="claude-sonnet-5-5", model_changed_from="claude-haiku-5-5", switch_source="api"),
            rec(turn=4, model="claude-opus-5-5", model_changed_from="claude-sonnet-5-5", switch_source="picker")]
    out = run(tmp_path, recs)
    assert out["skipped_entrypoint"] == {"sdk-cli": 2}
    assert out["skipped_non_owner_switch"] == {"api": 1}
    assert out["health"]["lines"] == 1
    assert run(tmp_path, recs, include_headless=True)["health"]["lines"] == 3


def _turns(n, sessions, model, effort, task="quick", **kw):
    return [rec(session="a%d" % (i % sessions), turn=i // sessions + 1, task=task, model=model, effort=effort, **kw)
            for i in range(n)]


def _baseline():
    """Two advised-step turns (sonnet low) of one session: turn 1 has a known pushback, turn 2 has none."""
    return [rec(session="z", turn=t, model="claude-sonnet-5-5", effort="low") for t in (1, 2)]


@pytest.mark.parametrize("n,sessions,expect", [(20, 5, True), (19, 5, False), (20, 4, False)])
def test_lower_row_only_at_or_above_its_size(tmp_path, n, sessions, expect):
    # quick advises sonnet low..medium; haiku is one step below; two advised-step lines give the baseline
    # n counts turns with a known pushback: the last turn of each session has none, so add one turn per session
    out = run(tmp_path, _turns(n + sessions, sessions, "claude-haiku-5-5", "low") + _baseline())
    assert bool(proposals(out, "lower_row")) is expect
    if not expect:
        assert proposals(out, "lower_row", "below_min_n")[0]["n"] >= 1


def test_lower_row_blocked_when_pushback_is_higher_below(tmp_path):
    low = [rec(session="a%d" % (i % 5), turn=i // 5 + 1, model="claude-haiku-5-5", effort="low", pushback_count=i // 5)
           for i in range(20)]
    out = run(tmp_path, low + _baseline())
    assert not proposals(out, "lower_row")


@pytest.mark.parametrize("shape,expect", [
    ({10: (1, 3)}, False),                    # no answer has its next turn
    ({10: (1, 2)}, False),                    # 20 turns but only 10 known pushback outcomes
    ({10: (1, 2, 3)}, True),                  # 20 known outcomes over 10 sessions
    ({4: tuple(range(1, 7)), 6: (1, 3)}, False),  # 20 known outcomes, but in only 4 sessions
])
def test_lower_row_needs_known_pushback_not_a_missing_next_turn(tmp_path, shape, expect):
    # turn counts and session counts must come from turns whose pushback is known (unknown is not "zero")
    low, g = [], 0
    for n_sessions, turns in shape.items():
        for _ in range(n_sessions):
            low += [rec(session="g%d" % g, turn=t, model="claude-haiku-5-5", effort="low") for t in turns]
            g += 1
    assert bool(proposals(run(tmp_path, low + _baseline()), "lower_row")) is expect


def _one_pushback(prefix, n, model, effort):
    # n turns with a known pushback outcome: the last turn of each of the 5 sessions has none, so add 5 more
    recs = [rec(session="%s%d" % (prefix, i % 5), turn=i // 5 + 1, model=model, effort=effort) for i in range(n + 5)]
    recs[-5]["pushback_count"] = 1  # the turn before it takes the blame (a known outcome)
    return recs


@pytest.mark.parametrize("low_n,adv_n,expect", [(107, 108, False), (108, 107, True), (107, 107, True)])
def test_lower_row_compares_raw_pushback_fractions_not_rounded_rates(tmp_path, low_n, adv_n, expect):
    # 1/107 and 1/108 both round to 0.0093, but 1/107 is more pushback than 1/108
    out = run(tmp_path, _one_pushback("l", low_n, "claude-haiku-5-5", "low") + _one_pushback("v", adv_n, "claude-sonnet-5-5", "low"))
    assert bool(proposals(out, "lower_row")) is expect


def test_lower_row_ignores_effort_guessed_from_settings(tmp_path):
    # an effort inferred from settings is not evidence: the same turns measured at the step propose, guessed ones do not
    def low(src):
        return [rec(session="a%d" % (i % 5), turn=i // 5 + 1, model="claude-haiku-5-5", effort="low", effort_source=src)
                for i in range(25)]
    assert proposals(run(tmp_path, low("stop") + _baseline()), "lower_row")
    assert not proposals(run(tmp_path, low("settings") + _baseline()), "lower_row")


def _raise_ledger(n, ups, src="stop", next_task="quick", next_model="claude-sonnet-5-5", next_effort="auto",
                  advised=None):
    """n turns at the advised step (sonnet low); the first `ups` are followed by a higher-effort turn."""
    advised = {"model": "sonnet", "effort": "low"} if advised is None else advised
    recs = [rec(session="r0", turn=1, model="claude-haiku-5-5", effort="high", nudge_shown=True, would_nudge=True,
                nudge_kind="up", requested=list(advised) or ["model"], advised=advised)]
    for i in range(n):
        s = "q%d" % i
        recs.append(rec(session=s, turn=1, model="claude-sonnet-5-5", effort="low", effort_source=src))
        eff = ("high" if i < ups else "low") if next_effort == "auto" else next_effort
        recs.append(rec(session=s, turn=2, task=next_task, model=next_model, effort=eff, effort_source=src))
    return recs


@pytest.mark.parametrize("n,ups,expect", [(20, 6, True), (20, 5, False), (19, 19, False)])
def test_raise_row_only_at_or_above_its_size(tmp_path, n, ups, expect):
    out = run(tmp_path, _raise_ledger(n, ups))
    assert bool(proposals(out, "raise_row")) is expect
    assert len(out["table_proposals"]) >= 1


@pytest.mark.parametrize("kw,expect", [({}, True),                                   # known positive
                                       ({"src": "settings"}, False),                  # inferred effort is not evidence
                                       ({"next_task": "planning", "next_model": "claude-opus-5-5"}, False),  # task change
                                       ({"advised": {"model": "sonnet"}}, True),      # model-only advice is a valid ledger line
                                       ({"advised": {"effort": "low"}}, True),        # so is effort-only
                                       ({"advised": {}}, False)])                     # empty advice is malformed: no step to test
def test_raise_row_needs_measured_effort_and_the_same_task(tmp_path, kw, expect):
    assert bool(proposals(run(tmp_path, _raise_ledger(20, 20, **kw)), "raise_row")) is expect


@pytest.mark.parametrize("kw,rate", [({"next_effort": None}, None),                    # same model, next effort unknown: cannot judge
                                     ({"next_effort": None, "next_model": "claude-opus-5-5"}, 1.0),  # a rise in model alone counts
                                     ({"next_effort": "low"}, 0.0)])                   # known no rise stays False
def test_moved_up_is_unknown_when_neither_model_rose_nor_effort_is_comparable(tmp_path, kw, rate):
    cell = [c for c in run(tmp_path, _raise_ledger(20, 20, **kw))["cells"] if (c["model"], c["effort"]) == ("sonnet", "low")][0]
    assert cell["moved_up_after_rate"] == rate


@pytest.mark.parametrize("gap,haiku_rate", [(False, 0.5), ("malformed", None), ("skipped", 0.0)])
def test_pushback_lands_on_turn_before_only_when_it_exists(tmp_path, gap, haiku_rate):
    # turn 1 answer, turn 2 (valid / malformed / headless-skipped), turn 3 carries the complaint
    recs = [rec(turn=1, model="claude-haiku-5-5"), rec(turn=3, model="claude-sonnet-5-5", pushback_count=1)]
    raw = []
    if gap == "malformed":
        raw = [json.dumps(rec(turn=2, effort="turbo"))]
    elif gap:
        recs.append(rec(turn=2, model="claude-haiku-5-5", entrypoint="sdk-cli"))
    else:
        recs.append(rec(turn=2, model="claude-haiku-5-5"))
    cells = {c["model"]: c["pushback_rate"] for c in run(tmp_path, recs, raw=raw)["cells"]}
    # the last turn of a session (sonnet) and an answer whose next turn is missing (malformed gap) have no judgement
    assert cells["haiku"] == haiku_rate and cells["sonnet"] is None


def test_followed_credit_uses_a_skipped_line_that_carries_the_verdict(tmp_path):
    # the line holding the verdict is a headless one (skipped from cells); a later line must not take the credit
    recs = []
    for i in range(20):
        s = "k%d" % i
        recs += [rec(session=s, turn=1, nudge_shown=True, would_nudge=True, nudge_kind="up", requested=["model"],
                     advised={"model": "sonnet"}),
                 rec(session=s, turn=2, entrypoint="sdk-cli", followed_prev_nudge=False),
                 rec(session=s, turn=3, followed_prev_nudge=True)]
    out = run(tmp_path, recs)
    assert out["health"]["followed_n"] == 20 and out["health"]["followed_share"] == 0.0


@pytest.mark.parametrize("bad_models,verdict", [(12, "broken"), (4, "keep")])  # 30% vs 10% unknown ids
def test_unrecognized_model_ids_count_toward_broken(tmp_path, bad_models, verdict):
    recs = _verdict_ledger(20, 20)
    for r in recs[:bad_models]:
        r["model"] = "gpt-9"
    out = run(tmp_path, recs)
    assert out["display_verdict"] == verdict
    assert out["health"]["model_unrecognized_share"] == round(bad_models / 40, 4)


@pytest.mark.parametrize("shows,ignored,expect", [(10, 8, True), (10, 7, False), (9, 9, False)])
def test_rule_wrong_only_at_or_above_its_size(tmp_path, shows, ignored, expect):
    recs = []
    for i in range(shows):
        s = "n%d" % i
        recs.append(rec(session=s, turn=1, nudge_shown=True, would_nudge=True, nudge_kind="up", requested=["model"],
                        advised={"model": "sonnet"}))
        recs.append(rec(session=s, turn=2, followed_prev_nudge=i >= ignored))
    assert bool(proposals(run(tmp_path, recs), "rule_wrong")) is expect


def _verdict_ledger(shows, followed, ts="2026-11-30T10:00:00Z", model="claude-haiku-5-5"):
    recs = []
    for i in range(shows):
        s = "v%d" % i
        recs.append(rec(session=s, turn=1, ts=ts, model=model, nudge_shown=True, would_nudge=True, nudge_kind="up",
                        requested=["model"], advised={"model": "sonnet"}))
        recs.append(rec(session=s, turn=2, ts=ts, model=model, followed_prev_nudge=i < followed))
    return recs


@pytest.mark.parametrize("recs,since,verdict", [
    (_verdict_ledger(19, 19), None, "wait"),
    (_verdict_ledger(20, 5), None, "keep"),
    (_verdict_ledger(20, 20, ), None, "keep"),
    (_verdict_ledger(20, 0), None, "silent"),
    (_verdict_ledger(20, 19)[:-1], None, "wait"),                                  # 19 known outcomes: not enough to judge
    (_verdict_ledger(20, 4), None, "silent"),
    (_verdict_ledger(20, 20, model=None), None, "broken"),                    # model null in half the lines
    (_verdict_ledger(20, 20, ts="2026-11-10T10:00:00Z"), "2026-11-09", "broken"),  # nothing in the last 7 days
    (_verdict_ledger(20, 20, ts="2026-11-30T10:00:00Z"), "2026-11-28", "keep"),    # window under 7 days
    (_verdict_ledger(1, 1, ts="2026-11-30T10:00:00Z")[:1], "2026-12-01", "wait"),  # short empty window: not broken
    (_verdict_ledger(1, 1, ts="2026-12-01T10:00:00Z")[:1], "2026-12-01", "wait"),  # control: one line inside it
])
def test_display_verdict_order(tmp_path, recs, since, verdict):
    assert run(tmp_path, recs, since=since)["display_verdict"] == verdict


def test_unknown_outcomes_are_not_zero_percent(tmp_path):
    # 20 shown nudges, none judged (no later followed value): wait, never silent
    recs = [rec(session="u%d" % i, nudge_shown=True, would_nudge=True, nudge_kind="up", requested=["model"],
                advised={"model": "sonnet"}) for i in range(20)]
    out = run(tmp_path, recs)
    assert out["health"]["followed_n"] == 0 and out["display_verdict"] == "wait"


@pytest.mark.parametrize("missing,followed_n", [(None, 1), (2, 0)])
def test_followed_verdict_is_dropped_when_a_turn_is_missing(tmp_path, missing, followed_n):
    # turn 2 (dropped) held a newer nudge; the verdict on turn 3 is about it, so turn 1 must not take the credit
    shown = dict(nudge_shown=True, would_nudge=True, nudge_kind="up", requested=["model"], advised={"model": "sonnet"})
    recs = [rec(turn=1, **shown), rec(turn=2, **shown), rec(turn=3, followed_prev_nudge=True)]
    out = run(tmp_path, [r for r in recs if r["turn"] != missing])
    assert out["health"]["followed_n"] == followed_n


def test_machine_turn_between_nudge_and_switch_keeps_the_outcome(tmp_path):
    recs = []
    for i in range(20):
        s = "m%d" % i
        recs += [rec(session=s, turn=1, nudge_shown=True, would_nudge=True, nudge_kind="up", requested=["model"],
                     advised={"model": "sonnet"}),
                 rec(session=s, turn=2, task="machine"),
                 rec(session=s, turn=3, followed_prev_nudge=True)]
    out = run(tmp_path, recs)
    assert out["health"]["followed_n"] == 20 and out["display_verdict"] == "keep"


def test_push_back_and_switch_up_blames_the_lower_step(tmp_path):
    recs = []
    for i in range(5):
        s = "p%d" % i
        recs += [rec(session=s, turn=t) for t in range(1, 5)]
        recs.append(rec(session=s, turn=5, model="claude-sonnet-5-5", effort="low", pushback_count=1,
                        model_changed_from="claude-haiku-5-5", switch_source="command"))
        recs.append(rec(session=s, turn=6, model="claude-sonnet-5-5", effort="low", pushback_count=1))
    out = run(tmp_path, recs)
    cells = {c["model"]: c for c in out["cells"]}
    assert cells["haiku"]["pushback_rate"] == 0.25 and cells["sonnet"]["pushback_rate"] == 0.0
    assert not proposals(out, "lower_row")


def test_pushback_count_before_since_is_not_a_new_pushback(tmp_path):
    recs = [rec(turn=1, ts="2026-11-18T10:00:00Z", pushback_count=1), rec(turn=2, ts="2026-11-20T10:00:00Z", pushback_count=1),
            rec(turn=3, ts="2026-11-20T10:01:00Z", pushback_count=1)]
    out = run(tmp_path, recs, since="2026-11-19")
    assert out["cells"][0]["pushback_rate"] == 0.0


def test_impossible_date_is_malformed_and_the_run_continues(tmp_path):
    bad = ["not json", json.dumps(rec(ts="2026-11-31T10:00:00Z")), json.dumps(rec(effort="turbo"))]
    out = run(tmp_path, [rec()], raw=bad)
    assert out["malformed"] == 3 and out["malformed_by_field"]["ts"] == 1


def test_raise_row_sees_silent_would_nudge_but_not_stuck(tmp_path):
    def led(kind):
        return [r if r["session"] != "r0" else {**r, "nudge_shown": False, "nudge_kind": kind} for r in _raise_ledger(20, 20)]
    assert proposals(run(tmp_path, led("up")), "raise_row")
    assert not [p for p in run(tmp_path, led("stuck"))["table_proposals"] if p["kind"] == "raise_row"]


@pytest.mark.parametrize("make", [lambda p: p / "nope.ndjson", lambda p: p / "empty.ndjson"])
@pytest.mark.parametrize("since", [None, "2026-11-01"])
def test_missing_or_empty_ledger_reads_broken(tmp_path, make, since):
    path = make(tmp_path)
    if path.name == "empty.ndjson":
        path.write_text("not json\n")
    out = mst.analyse(path, since=since, now=NOW)
    assert out["display_verdict"] == "broken" and out.get("ledger_missing", False) is (path.name == "nope.ndjson")


def test_effort_source_is_counted_per_cell(tmp_path):
    out = run(tmp_path, [rec(effort_source="settings"), rec(turn=2, effort_source="stop")])
    assert out["cells"][0]["effort_sources"] == {"settings": 1, "stop": 1}
