"""The REPLAN gate (S05): `plan_impact` closeouts, the REPLAN park, and the
rendered plan change log.

Each block pairs a PLANT (what must be refused / must not happen) with an ALLOW
(the same machinery succeeding), because a refusal test that would also pass
against a no-op implementation proves nothing.

  RP-05  a closeout's `plan_impact` parks the plan in a REPLAN checkpoint whose
         brief names the invalidated sessions and offers exactly three options;
         a bad session id is a LOUD closeout error; a v2 plan is UNAFFECTED;
         a human checkpoint in the same closeout goes FIRST; a crash between the
         closeout persist and the park replays idempotently.
  RP-06  every mid-run amendment appends one dated line to the rendered
         'Plan changes' section.

Run: pytest skills/plan-execute/scripts/test_replan_gate.py -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import plan_mutate as pm  # noqa: E402
import replan as rp  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_shipping import make_plan  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def _sessions():
    """s01 -> s02 -> s03. s01 is the discoverer; s02/s03 are the future work
    a `plan_impact` can invalidate."""
    return [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
         "dispatch": {"depends_on": ["s01"]}},
        {"id": "s03", "title": "S3", "model": "Sonnet", "items": ["w-03"], "prompt": "do",
         "dispatch": {"depends_on": ["s02"]}},
    ]


@pytest.fixture
def plan(tmp_path):
    return str(make_plan(tmp_path, _sessions()))


def _closeout_text(sid, item, *, result="DONE", checkpoint=None, plan_impact=None):
    body = {
        "session": sid, "result": result, "items_completed": [item], "items_blocked": [],
        "notes": {item: "done", sid: f"{sid} complete"}, "dispatch_next": True,
        "human_checkpoint_reason": checkpoint,
    }
    if plan_impact is not None:
        body["plan_impact"] = plan_impact
    return (f"work done.\n\n<plan-execute-closeout>\n{json.dumps(body)}\n"
            f"</plan-execute-closeout>\n")


def _write_out(tmp_path, sid, item, **kw):
    out = Path(tmp_path) / f"{sid}.out.md"
    out.write_text(_closeout_text(sid, item, **kw))
    return str(out)


def _run_session(plan_dir, sid, item, tmp_path, capsys=None, **kw):
    """Drive one session through the REAL begin -> apply path.

    With `capsys`, returns APPLY's parsed JSON — `begin`'s blob is drained
    before it and `release`'s after it, so the caller gets exactly one document.
    """
    out = _write_out(tmp_path, sid, item, **kw)
    run.cmd_begin(plan_dir, [sid])
    if capsys is not None:
        capsys.readouterr()
    try:
        run.cmd_apply(plan_dir, sid, out)
    except SystemExit as e:
        assert e.code in (0, None), f"apply exited {e.code}"
    applied = _json_out(capsys) if capsys is not None else None
    run.cmd_release(plan_dir)
    if capsys is not None:
        capsys.readouterr()
    return applied


def _status(plan_dir, aid):
    return ab.read_status((Path(plan_dir) / "PLAN.html").read_text(), aid)


def _events(plan_dir, name=None):
    p = Path(plan_dir) / "run.ndjson"
    evs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [e for e in evs if name is None or e.get("event") == name]


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def _change_rows(plan_dir):
    """The rendered <li> rows of the Plan-changes section, as plain text."""
    html = (Path(plan_dir) / "PLAN.html").read_text()
    m = ab._CHANGES_LIST_RE.search(html)
    assert m, "PLAN.html has no anchored Plan-changes list"
    import re
    return [re.sub(r"<[^>]+>", "", li) for li in re.findall(r"<li .*?</li>", m.group(2))]


def _row_op(plan_dir, index):
    """The subcommand pill of the index-th rendered change-log row."""
    import re
    html = (Path(plan_dir) / "PLAN.html").read_text()
    m = ab._CHANGES_LIST_RE.search(html)
    pills = re.findall(r'<span class="hotspot-pill low">([^<]*)</span>', m.group(2))
    return pills[index]


def _downgrade_to_v2(plan_dir):
    """Restamp a built plan as schema v2 — a plan built BEFORE this feature."""
    p = Path(plan_dir) / "manifest.json"
    m = json.loads(p.read_text())
    m["plan_schema_version"] = 2
    p.write_text(json.dumps(m, indent=2))


IMPACT = {"invalidates": ["s02", "s03"],
          "reason": "the vendor API we planned s02/s03 around was deprecated this week"}


def _recommend(plan_dir, sid, capsys, text="amend — the replacement endpoint covers both"):
    """RP-08: the brief owes a recommendation before it can be resolved. Its own
    plant/allow/control pairs live in test_replan_recommendation.py; here it is
    just the step every resolve flow now goes through."""
    run.cmd_recommend_replan(plan_dir, sid, text)
    capsys.readouterr()


# ==========================================================================
# RP-05 — closeout schema: plan_impact is validated, never silently dropped
# ==========================================================================
# PLANT: an id that names no session in the manifest is a closeout ERROR — the
# session is BLOCKED and the plan halts, exactly like any other bad closeout.
def test_plant_unknown_session_id_is_a_loud_closeout_error(plan, tmp_path, capsys):
    out = _write_out(tmp_path, "s01", "w-01",
                     plan_impact={"invalidates": ["s02", "s99"], "reason": "x"})
    run.cmd_begin(plan, ["s01"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        run.cmd_apply(plan, "s01", out)
    assert exc.value.code == 1
    printed = _json_out(capsys)
    assert printed["failure"] == "semantic_error"
    assert "s99" in printed["reason"]
    assert _status(plan, "s01") == "BLOCKED"
    assert rsi.is_halted(plan)
    # And nothing was parked from a closeout that never validated.
    assert rp.pending(plan) == []


@pytest.mark.parametrize(
    "impact,needle",
    [
        ({"invalidates": [], "reason": "x"}, "non-empty list"),
        ({"invalidates": ["s02"]}, "plan_impact.reason"),
        ({"invalidates": ["s02"], "reason": "   "}, "plan_impact.reason"),
        ({"invalidates": ["s01"], "reason": "x"}, "reporting session"),
        ({"invalidates": ["s02"], "reason": "x", "sessions": ["s03"]}, "unknown key"),
        ("s02", "must be an object"),
    ],
)
def test_plant_malformed_plan_impact_shapes_are_refused(plan, tmp_path, impact, needle, capsys):
    out = _write_out(tmp_path, "s01", "w-01", plan_impact=impact)
    run.cmd_begin(plan, ["s01"])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run.cmd_apply(plan, "s01", out)
    assert needle in _json_out(capsys)["reason"]
    assert _status(plan, "s01") == "BLOCKED"


# CONTROL (the version gate — the whole point of bumping the stamp to 3): the
# SAME closeout that is refused above applies CLEANLY on a plan stamped v2. A
# plan built before this feature must not gain a new refusal mode retroactively,
# and must not gain a new park either.
def test_control_v2_plan_is_unaffected_by_plan_impact(plan, tmp_path, capsys):
    _downgrade_to_v2(plan)
    out = _write_out(tmp_path, "s01", "w-01",
                     plan_impact={"invalidates": ["s02", "s99"], "reason": "x"})
    run.cmd_begin(plan, ["s01"])
    capsys.readouterr()
    run.cmd_apply(plan, "s01", out)          # no SystemExit
    printed = _json_out(capsys)
    run.cmd_release(plan)

    assert printed["applied"] is True and printed["result"] == "DONE"
    assert "replan" not in printed and "replan_deferred" not in printed
    assert _status(plan, "s01") == "DONE"
    assert not rsi.is_halted(plan)
    assert rp.pending(plan) == []
    assert not _events(plan, "replan_checkpoint")
    # The field rides through onto the record verbatim — ignored, never eaten.
    assert cp.load_closeout(plan, "s01")["plan_impact"]["invalidates"] == ["s02", "s99"]


# CONTROL: the same valid closeout on a v3 plan DOES park. Without this pairing
# the test above would pass against an implementation that does nothing at all.
def test_control_v3_plan_enforces_what_v2_ignores(plan, tmp_path, capsys):
    printed = _run_session(plan, "s01", "w-01", tmp_path, capsys, plan_impact=IMPACT)
    assert printed["replan"]["parked"] is True
    assert rsi.is_halted(plan)


# The digest must NOT move: every plan on disk was digested before this field
# existed, so folding it in would read as state-drift on the next resume.
def test_plan_impact_does_not_change_the_closeout_digest():
    base = {"session": "s01", "result": "DONE", "items_completed": ["w-01"],
            "items_blocked": [], "notes": {"w-01": "done"}}
    assert cp.closeout_digest(base) == cp.closeout_digest({**base, "plan_impact": IMPACT})


# ==========================================================================
# RP-05 — the REPLAN park
# ==========================================================================
# ALLOW: the headline behaviour. A valid plan_impact halts the plan with a brief
# naming the invalidated sessions, the reason, and AT MOST three options.
def test_allow_plan_impact_parks_a_replan_checkpoint_with_a_brief(plan, tmp_path, capsys):
    printed = _run_session(plan, "s01", "w-01", tmp_path, capsys, plan_impact=IMPACT)

    # The session itself SUCCEEDED — a REPLAN reports a fact about the plan, it
    # is not a failure of the session that found it.
    assert printed["result"] == "DONE"
    assert _status(plan, "s01") == "DONE"

    brief = printed["replan"]["brief"]
    assert brief["kind"] == "replan" and brief["by_session"] == "s01"
    assert brief["reason"] == IMPACT["reason"]
    assert [i["id"] for i in brief["invalidates"]] == ["s02", "s03"]
    assert [i["title"] for i in brief["invalidates"]] == ["S2", "S3"]
    assert len(brief["options"]) <= 3
    assert [o["key"] for o in brief["options"]] == ["amend", "retire", "proceed"]
    assert "recommendation" in brief          # room for the orchestrator's pick

    # HALT semantics: the plan is stopped, and the stop is flavoured `replan`.
    state = rsi.load_state(plan)
    assert state["halt"]["set"] is True and state["halt"]["kind"] == "replan"
    ev = _events(plan, "replan_checkpoint")
    assert len(ev) == 1 and ev[0]["invalidates"] == ["s02", "s03"]

    # The discovery is visible on the page, on the discoverer AND on the
    # sessions it invalidated (each keeping its own status).
    html = (Path(plan) / "PLAN.html").read_text()
    assert "REPLAN: invalidates s02, s03" in html
    assert "flagged by s01&#x27;s plan_impact" in html or "flagged by s01's plan_impact" in html
    assert _status(plan, "s02") == "TODO" and _status(plan, "s03") == "TODO"


# PLANT: the loop must STOP. Neither the front door (`plan`) nor the side door
# (`begin --sessions`) may advance a REPLAN-parked plan — s03 closed the halt
# hole on `begin`, and this asserts it stays closed for this second park flavor.
def test_plant_replan_park_stops_both_dispatch_doors(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()

    run.cmd_plan(plan, False, None)
    action = _json_out(capsys)
    assert action["action"] == "replan"
    assert action["replan"][0]["by_session"] == "s01"
    assert "resolve-replan" in action["hint"]

    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan, ["s02"])
    assert "HALTED" in str(exc.value)
    assert _status(plan, "s02") == "TODO"           # no TODO -> DOING flip
    assert not (Path(plan) / ".lock").exists()


# PLANT: the halt must not deadlock the operator against the gate they are
# answering — the three commands that CAN resolve a replan are let through,
# while ack-checkpoint (which resolves a different gate) is not.
def test_allow_resolving_commands_pass_through_a_replan_halt(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()

    run.cmd_amend_session(plan, _amend_args("s02", prompt="rewritten for the new API"))
    assert _json_out(capsys)["op"] == "amend-session"

    with pytest.raises(SystemExit) as exc:
        run.cmd_ack_checkpoint(plan, "s01")
    assert "HALTED" in str(exc.value)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _amend_args(sid, **kw):
    base = {"session": sid, "depends_on": None, "prompt": None, "model": None,
            "reasoning": None, "allow_builder_drift": False}
    base.update(kw)
    return _Args(**base)


def _retire_args(sid, reason, **kw):
    base = {"session": sid, "reason": reason, "cascade": False,
            "drop_dependency": False, "allow_builder_drift": False}
    base.update(kw)
    return _Args(**base)


# ==========================================================================
# RP-05 — resolving the park
# ==========================================================================
# PLANT: `--decision amend` must not be a rubber stamp. Clearing the halt while
# the invalidated sessions are untouched is exactly the failure the gate exists
# to prevent.
@pytest.mark.parametrize("decision", ["amend", "retire"])
def test_plant_resolve_refused_until_the_amendment_actually_landed(
    plan, tmp_path, capsys, decision
):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()
    _recommend(plan, "s01", capsys)
    with pytest.raises(SystemExit) as exc:
        run.cmd_resolve_replan(plan, "s01", decision, "trust me")
    assert "change log" in str(exc.value)
    assert rsi.is_halted(plan)
    assert rp.pending(plan)


def test_plant_resolve_refused_without_reason_or_park(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        run.cmd_resolve_replan(plan, "s01", "proceed", "   ")
    assert "--reason" in str(exc.value)
    # And a session that never raised a REPLAN cannot be "resolved".
    with pytest.raises(SystemExit) as exc2:
        run.cmd_resolve_replan(plan, "s02", "proceed", "why not")
    assert "no REPLAN park" in str(exc2.value)


# ALLOW: amend the invalidated session, THEN resolve. The halt clears, the loop
# advances, and the decision is on the page.
def test_allow_amend_then_resolve_clears_the_halt_and_unblocks_the_loop(
    plan, tmp_path, capsys
):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()
    run.cmd_amend_session(plan, _amend_args("s02", prompt="use the replacement API"))
    capsys.readouterr()
    _recommend(plan, "s01", capsys)

    run.cmd_resolve_replan(plan, "s01", "amend", "s02 rewritten for the replacement API")
    out = _json_out(capsys)
    assert out["resolved"] is True and out["halt_cleared"] is True
    assert not rsi.is_halted(plan)
    assert rp.pending(plan) == []
    ev = _events(plan, "replan_resolved")
    assert len(ev) == 1 and ev[0]["decision"] == "amend"

    # Idempotent: a retry is a no-op success, not an error and not a second event.
    run.cmd_resolve_replan(plan, "s01", "amend", "again")
    assert _json_out(capsys)["already"] is True
    assert len(_events(plan, "replan_resolved")) == 1

    run.cmd_plan(plan, False, None)
    assert [m["id"] for m in _json_out(capsys)["batch"]] == ["s02"]


# ALLOW: `proceed` needs no mutation — it is the honest "the discovery does not
# actually invalidate them" answer, and it is recorded rather than silent.
def test_allow_proceed_needs_no_mutation_but_is_recorded(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()
    _recommend(plan, "s01", capsys)
    run.cmd_resolve_replan(plan, "s01", "proceed", "s02/s03 never touched that endpoint")
    out = _json_out(capsys)
    assert out["resolved"] is True and out["halt_cleared"] is True
    assert _row_op(plan, 0) == "resolve-replan"
    assert "proceed" in _change_rows(plan)[0]


# ALLOW: retirement counts as the amendment too — retire-session logs the
# retired session id, which is what the gate looks for.
def test_allow_retire_then_resolve(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact={
        "invalidates": ["s03"], "reason": "s03's premise is gone"})
    capsys.readouterr()
    run.cmd_retire_session(plan, _retire_args("s03", "premise removed by s01's finding"))
    capsys.readouterr()
    _recommend(plan, "s01", capsys, "retire — s03's premise is gone, there is nothing to amend")
    run.cmd_resolve_replan(plan, "s01", "retire", "s03 retired")
    assert _json_out(capsys)["resolved"] is True
    assert _status(plan, "s03") == "WONTFIX"


# ==========================================================================
# RP-05 — precedence: human checkpoint FIRST, REPLAN second
# ==========================================================================
def test_allow_human_checkpoint_parks_before_the_replan(plan, tmp_path, capsys):
    printed = _run_session(plan, "s01", "w-01", tmp_path, capsys,
                           checkpoint="is the replacement API acceptable?",
                           plan_impact=IMPACT)

    # Gate 1 only: the human checkpoint. The plan is NOT halted yet, and the
    # REPLAN is reported as deferred rather than dropped.
    assert _status(plan, "s01") == "AWAITS_REVIEW"
    assert not rsi.is_halted(plan)
    assert "replan" not in printed
    assert printed["replan_deferred"]["until"] == "ack-checkpoint"
    assert rp.pending(plan) == []
    assert not _events(plan, "replan_checkpoint")

    # Gate 2, and only after the first is acked: the REPLAN park lands.
    run.cmd_ack_checkpoint(plan, "s01")
    acked = _json_out(capsys)
    assert acked["acked"] is True
    assert acked["replan"]["parked"] is True
    assert "resolve-replan" in acked["next"]
    assert rsi.load_state(plan)["halt"]["kind"] == "replan"
    assert [p["session"] for p in rp.pending(plan)] == ["s01"]


# ==========================================================================
# RP-05 — crash between the closeout persist and the park
# ==========================================================================
def _apply_subprocess(plan_dir, sid, out_file, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "run.py"), "apply", str(plan_dir),
         "--session", sid, "--output-file", out_file],
        capture_output=True, text=True, env=e,
    )


def test_crash_between_persist_and_park_replays_idempotently(plan, tmp_path, capsys):
    """A REAL process death (os._exit) between the write-ahead closeout persist
    and the REPLAN park. Re-running `apply` must re-park, exactly once."""
    out = _write_out(tmp_path, "s01", "w-01", plan_impact=IMPACT)
    run.cmd_begin(plan, ["s01"])
    capsys.readouterr()

    proc = _apply_subprocess(plan, "s01", out, {"PLAN_APPLY_CRASH": "park"})
    assert proc.returncode == 70, proc.stderr
    assert "crash-injection" in proc.stderr

    # The write-ahead record landed; the park did NOT.
    assert cp.load_closeout(plan, "s01")["result"] == "DONE"
    assert rp.pending(plan) == []
    assert not rsi.is_halted(plan)
    assert not _events(plan, "replan_checkpoint")

    # Replay: the same apply, no crash. The park now lands.
    ok = _apply_subprocess(plan, "s01", out)
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert [p["session"] for p in rp.pending(plan)] == ["s01"]
    assert rsi.load_state(plan)["halt"]["kind"] == "replan"
    assert len(_events(plan, "replan_checkpoint")) == 1

    # And replaying AGAIN is idempotent: one park record, one unresolved entry.
    again = _apply_subprocess(plan, "s01", out)
    assert again.returncode == 0, again.stderr + again.stdout
    assert len(rp.pending(plan)) == 1
    assert len(list((Path(plan) / rp.REPLAN_DIR).glob("*.json"))) == 1


def test_a_resolved_park_is_never_re_raised_by_a_replayed_closeout(plan, tmp_path, capsys):
    """The other half of idempotence: re-applying an OLD closeout must not
    re-halt a decision the operator already made."""
    out = _write_out(tmp_path, "s01", "w-01", plan_impact=IMPACT)
    _run_session(plan, "s01", "w-01", tmp_path, plan_impact=IMPACT)
    capsys.readouterr()
    _recommend(plan, "s01", capsys, "proceed — neither session touches the retired endpoint")
    run.cmd_resolve_replan(plan, "s01", "proceed", "not actually affected")
    capsys.readouterr()
    assert not rsi.is_halted(plan)

    replayed = _apply_subprocess(plan, "s01", out)
    assert replayed.returncode == 0, replayed.stderr + replayed.stdout
    assert not rsi.is_halted(plan)
    assert rp.pending(plan) == []


# ==========================================================================
# RP-06 — the rendered plan change log
# ==========================================================================
def test_allow_every_mutation_appends_one_dated_change_log_line(plan, tmp_path, capsys):
    # A fresh plan shows the empty state, not a stale or missing section.
    assert _change_rows(plan) == [
        "No plan changes recorded — this plan has run as it was built."
    ]

    run.cmd_amend_session(plan, _amend_args("s02", model="Opus"))
    capsys.readouterr()
    run.cmd_add_session(plan, _Args(
        id="s04", title="Follow-up", items=None, new_item=["w-04|work|New work"],
        depends_on="s01", model="Sonnet", reasoning=None, gates=None,
        require_evidence=False, prompt="do it", human_summary=None,
        parallel_group=None, infographic_group=None, allow_builder_drift=False,
    ))
    capsys.readouterr()
    run.cmd_retire_session(plan, _retire_args("s04", "turned out to be unnecessary"))
    capsys.readouterr()
    _run_session(plan, "s01", "w-01", tmp_path)
    capsys.readouterr()
    run.cmd_redispatch(plan, "s01", "input data was wrong")
    capsys.readouterr()

    # One row per mutation, in order, each carrying date · session · reason
    # (the subcommand is the row's pill — see `_change_li`).
    rows = _change_rows(plan)
    ops = [_row_op(plan, i) for i in range(len(rows))]
    assert ops == ["amend-session", "add-session", "retire-session", "redispatch"]
    for r in rows:
        date, rest = r.split(" · ", 1)
        assert len(date) == 10 and date.count("-") == 2, r      # dated
        assert rest.split(" — ")[0] in ("s01", "s02", "s04"), r  # session id
        assert " — " in rest and rest.split(" — ", 1)[1].strip(), r  # reason/summary
    assert "turned out to be unnecessary" in rows[2]
    assert "input data was wrong" in rows[3]

    # The rendered section and the ndjson record agree, entry for entry.
    log = pm.read_changelog(plan)
    assert [e["op"] for e in log] == ops


def test_change_log_survives_a_re_render(plan, capsys):
    """A later mutation re-renders the whole page through plan-builder; the log
    is runtime state, so `carry_over_state` must carry it — the same rule that
    keeps statuses and notes alive across a rebuild."""
    run.cmd_amend_session(plan, _amend_args("s02", model="Opus"))
    capsys.readouterr()
    run.cmd_amend_session(plan, _amend_args("s03", model="Opus"))
    capsys.readouterr()
    rows = _change_rows(plan)
    assert len(rows) == 2 and all("amend-session" in r for r in rows)


def test_carry_over_state_carries_the_change_log(plan, capsys):
    """The unit `build_plan --rebuild --preserve-state` calls (via
    `carry_over_recorded_state`) — asserted directly so the rebuild path is
    covered without paying for a second full build."""
    run.cmd_amend_session(plan, _amend_args("s02", model="Opus"))
    capsys.readouterr()
    lived_in = (Path(plan) / "PLAN.html").read_text()
    fresh = ab.set_change_log(lived_in, [])          # a freshly rendered page
    assert "amend-session" not in ab._CHANGES_LIST_RE.search(fresh).group(2)
    carried = ab.carry_over_state(lived_in, fresh)
    assert "amend-session" in ab._CHANGES_LIST_RE.search(carried).group(2)


def test_change_log_writers_tolerate_a_plan_without_the_section(plan, tmp_path):
    """Plans built before this section exists have no anchors. Every writer is a
    no-op there, never an error — the executor must keep running old plans."""
    html = Path(plan) / "PLAN.html"
    text = html.read_text()
    start = text.index(ab.CHANGES_BEGIN)
    end = text.index(ab.CHANGES_END) + len(ab.CHANGES_END)
    html.write_text(text[:start] + text[end:])

    assert ab.set_change_log(html.read_text(), [{"op": "x"}]) == html.read_text()
    assert ab.write_change_log(html, [{"op": "x"}]) is False
    rec = pm.record_change(plan, op="redispatch", session="s01", summary="no section here")
    assert rec["rendered"] is False
    # The RECORD still lands — the page is a view, the ndjson is the record.
    assert [e["op"] for e in pm.read_changelog(plan)] == ["redispatch"]


def test_change_log_escapes_html_in_a_reason(plan, capsys):
    run.cmd_retire_session(
        plan, _retire_args("s03", "dropped <script>alert(1)</script> & the rest"))
    capsys.readouterr()
    html = (Path(plan) / "PLAN.html").read_text()
    m = ab._CHANGES_LIST_RE.search(html)
    assert "<script>alert(1)</script>" not in m.group(2)
    assert "&lt;script&gt;" in m.group(2)
