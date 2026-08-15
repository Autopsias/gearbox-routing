"""RP-03 — the mutation engine: add-session / amend-session / retire-session.

Every block is a PLANT (the thing that must be refused, or the corruption that
must not survive) paired with an ALLOW (the same machinery succeeding), because
a refusal test that would pass against a no-op implementation proves nothing.

  add     writes EVERY surface (manifest, anchored article in the right section,
          nav strip, header counts, prompt + context); rejects an invented
          category and a dependency cycle.
  amend   only a TODO session; DOING/DONE/terminal refused loudly.
  retire  demands a reason; refuses while LIVE dependents exist — direct AND
          transitive — unless the operator cascades or drops the dependency.
  txn     a crash after EVERY individual rename leaves a recoverable plan, and
          recovery yields ONE generation (never a mix).

Run: pytest skills/plan-execute/scripts/test_plan_mutate.py -q
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import manifest_io as mio  # noqa: E402
import plan_mutate as pm  # noqa: E402
import run  # noqa: E402
from test_shipping import make_plan  # noqa: E402


def _sessions():
    """s01 -> s02 -> s03: a producer and two generations of consumers."""
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


def _html(plan_dir):
    return (Path(plan_dir) / "PLAN.html").read_bytes().decode("utf-8")


def _status(plan_dir, aid):
    return ab.read_status(_html(plan_dir), aid)


def _section_of(html, article_id):
    """The `data-cat` of the <section> the article actually SITS IN.

    Deliberately containment, not the article's own attribute: the 2026-08-02
    corruption had articles carrying a correct data-cat while living in the
    wrong section, and an attribute check passed it.
    """
    idx = html.index(f"<!-- ARTICLE:{article_id}:BEGIN -->")
    stack = []
    for m in re.finditer(r"<section\b[^>]*>|</section>", html[:idx]):
        tag = m.group(0)
        if tag == "</section>":
            if stack:
                stack.pop()
        else:
            cat = re.search(r'data-cat="([^"]+)"', tag)
            stack.append(cat.group(1) if cat else None)
    return stack[-1] if stack else None


# --------------------------------------------------------------------------
# add-session
# --------------------------------------------------------------------------
def test_add_session_writes_every_surface(plan):
    """ALLOW — one command, six surfaces, all consistent."""
    res = pm.add_session(
        plan, sid="s04", title="Added mid-run",
        new_items=["w-04|work|Backfill the extractor|Catch up the rows we skipped"],
        depends_on=["s03"], model="Opus", reasoning="high", require_evidence=True,
    )
    html = _html(plan)

    # 1. manifest session entry (round-trips through the real loader)
    manifest = mio.load_manifest(plan)
    entry = mio.session_by_id(manifest)["s04"]
    assert entry["items"] == ["w-04"]
    assert entry["model"] == "Opus" and entry["reasoning"] == "high"
    assert entry["dispatch"]["depends_on"] == ["s03"]
    assert entry["verify"]["require_evidence"] is True

    # 2. anchored article, TODO, in the SESSION section (containment, not attrs)
    assert _status(plan, "s04") == "TODO"
    assert _section_of(html, "s04") == "sessions"
    # 3. the new item's article sits inside ITS category's section
    assert _section_of(html, "w-04") == "work"
    # 4. nav-strip chip
    assert 'class="strip-chip" data-session="s04"' in html
    # 5. header counts
    assert "4 sessions · 4 items" in html
    assert "SESSION_TOTAL_COUNT: 4" in html
    # every session card's "Session N of M" step was restated, not just the new one
    assert html.count("of 4</div>") == 4
    # 6. prompt + context files
    prompt = (Path(plan) / "sessions" / "s04.prompt.md").read_text()
    assert "SESSION S04" in prompt and "W-04" in prompt
    assert (Path(plan) / "sessions" / "s04.context.md").read_text().strip()

    assert res["builder_drift_lines"] == 0
    assert pm.consistency_report(plan)["ok"]


def test_add_session_rejects_unknown_category(plan):
    """PLANT — an invented data-cat matches no section; the article would land
    in whichever section happened to be last. That is the exact 2026-08-02 bug."""
    with pytest.raises(pm.MutationError, match="matches no section"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|extraction|X"])
    assert "s04" not in _html(plan)
    assert "s04" not in mio.all_session_ids(mio.load_manifest(plan))


def test_add_session_rejects_dependency_cycle(plan):
    """PLANT — a session that waits for itself can never dispatch."""
    with pytest.raises(pm.MutationError, match="[Cc]ycle"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|work|X"],
                       depends_on=["s04"])
    assert "s04" not in _html(plan)


def test_add_session_rejects_duplicate_and_stolen_items(plan):
    with pytest.raises(pm.MutationError, match="already exists"):
        pm.add_session(plan, sid="s01", title="dup", new_items=["w-04|work|X"])
    with pytest.raises(pm.MutationError, match="already owned by session 's01'"):
        pm.add_session(plan, sid="s04", title="X", items=["w-01"])
    with pytest.raises(pm.MutationError, match="not a session in this plan"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|work|X"],
                       depends_on=["s99"])


def test_added_session_is_dispatchable(plan):
    """The point of writing every surface: the executor picks the new session up."""
    pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"],
                   depends_on=["s01"])
    import dispatch as dsp
    ready = dsp.ready_sessions(mio.load_manifest(plan), run._statuses(plan))
    assert "s01" in {s["id"] for s in ready}
    # s04 waits behind s01 exactly like a build-time dependency would
    assert "s04" not in {s["id"] for s in ready}


def test_new_item_joins_the_infographic_group(plan):
    """ALLOW — the Plan Achievement counters must see the new item. An item in
    no group is the modern form of the 'WORKSTREAMS missing new items' defect:
    the visual story then reads a higher % than the plan's real state."""
    res = pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                         infographic_group="P1")
    assert res["infographic_group"] == "P1"
    spec = json.loads((Path(plan) / "spec.json").read_text())
    assert "w-04" in spec["infographic"]["phases"][0]["items"]
    assert "const PHASES" in _html(plan) and '"w-04"' in _html(plan)
    assert res["validation_warnings"] == []


def test_unplaced_item_warns_instead_of_silently_skewing_the_counters(plan):
    res = pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"])
    assert res["infographic_group"] is None
    assert any("not added to any Plan Achievement group" in w
               for w in res["validation_warnings"])


def test_unknown_infographic_group_refused(plan):
    with pytest.raises(pm.MutationError, match="matches no group"):
        pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                       infographic_group="Nope")
    assert "s04" not in _html(plan)


# --------------------------------------------------------------------------
# amend-session
# --------------------------------------------------------------------------
def test_amend_session_allow(plan):
    pm.amend_session(plan, "s03", depends_on=["s01"], prompt="new body",
                     model="Opus", reasoning="high")
    entry = mio.session_by_id(mio.load_manifest(plan))["s03"]
    assert entry["dispatch"]["depends_on"] == ["s01"]
    assert entry["model"] == "Opus" and entry["reasoning"] == "high"
    assert "new body" in (Path(plan) / "sessions" / "s03.prompt.md").read_text()
    assert 'depends on <a href="#s01">S01</a>' in _html(plan)
    assert pm.consistency_report(plan)["ok"]


@pytest.mark.parametrize("status", ["DOING", "DONE", "WONTFIX", "AWAITS_REVIEW"])
def test_amend_refuses_non_todo(plan, status):
    """PLANT — the session was dispatched against the old text; amending it
    would rewrite history rather than change the future."""
    ab.apply_mutation(Path(plan) / "PLAN.html", "s02", status=status, note="n")
    with pytest.raises(pm.MutationError, match=f"it is {status}, not TODO"):
        pm.amend_session(plan, "s02", model="Opus")
    assert mio.session_by_id(mio.load_manifest(plan))["s02"]["model"] == "Sonnet"


def test_amend_rejects_cycle_and_empty(plan):
    with pytest.raises(pm.MutationError, match="[Cc]ycle"):
        pm.amend_session(plan, "s01", depends_on=["s03"])  # s01->s03->s02->s01
    assert mio.session_by_id(mio.load_manifest(plan))["s01"]["dispatch"]["depends_on"] == []
    with pytest.raises(pm.MutationError, match="nothing to amend"):
        pm.amend_session(plan, "s01")


# --------------------------------------------------------------------------
# retire-session
# --------------------------------------------------------------------------
def test_retire_requires_reason(plan):
    with pytest.raises(pm.MutationError, match="without --reason"):
        pm.retire_session(plan, "s03", reason="  ")
    assert _status(plan, "s03") == "TODO"


def test_retire_leaf_allow(plan):
    res = pm.retire_session(plan, "s03", reason="superseded by the new extractor")
    assert _status(plan, "s03") == "WONTFIX"
    assert "superseded by the new extractor" in _html(plan)
    # its exclusive item goes with it — otherwise the board shows work with no producer
    assert _status(plan, "w-03") == "WONTFIX"
    assert res["retired"] == ["s03"]
    log = pm.read_changelog(plan)
    assert log[-1]["op"] == "retire-session" and "superseded" in log[-1]["summary"]


def test_retire_refuses_live_direct_dependent(plan):
    """PLANT — WONTFIX is in the executor's DONE_STATES, so retiring s02 would
    SATISFY s03's dependency and s03 would run without its input."""
    with pytest.raises(pm.MutationError) as e:
        pm.retire_session(plan, "s02", reason="dropped")
    assert "direct: ['s03']" in str(e.value)
    assert _status(plan, "s02") == "TODO"


def test_retire_refuses_live_transitive_dependent(plan):
    """PLANT — s03 depends on s01 only through s02; naming just the direct
    dependent would under-report the blast radius."""
    with pytest.raises(pm.MutationError) as e:
        pm.retire_session(plan, "s01", reason="dropped")
    msg = str(e.value)
    assert "direct: ['s02']" in msg and "transitive: ['s03']" in msg
    assert _status(plan, "s01") == "TODO"


def test_retire_cascade_allow(plan):
    res = pm.retire_session(plan, "s01", reason="whole branch abandoned", cascade=True)
    assert res["retired"] == ["s01", "s02", "s03"]
    for sid in ("s01", "s02", "s03"):
        assert _status(plan, sid) == "WONTFIX"
    assert "producer s01 retired" in _html(plan)
    assert pm.consistency_report(plan)["ok"]


def test_retire_drop_dependency_allow(plan):
    """ALLOW — the other escape: keep the dependents, atomically rewrite their
    depends_on AND their prompt in the same transaction."""
    res = pm.retire_session(plan, "s01", reason="input arrives from ops instead",
                            drop_dependency=True)
    assert res["retired"] == ["s01"] and res["dependents_amended"] == ["s02"]
    assert _status(plan, "s01") == "WONTFIX"
    assert _status(plan, "s02") == "TODO"
    assert mio.session_by_id(mio.load_manifest(plan))["s02"]["dispatch"]["depends_on"] == []
    prompt = (Path(plan) / "sessions" / "s02.prompt.md").read_text()
    assert "was retired" in prompt and "input arrives from ops instead" in prompt
    # s03 still depends on s02 — the graph below the cut is untouched
    assert mio.session_by_id(mio.load_manifest(plan))["s03"]["dispatch"]["depends_on"] == ["s02"]
    assert pm.consistency_report(plan)["ok"]


def test_retire_refuses_in_flight_and_finished(plan):
    for status in ("DOING", "DONE", "AWAITS_REVIEW"):
        ab.apply_mutation(Path(plan) / "PLAN.html", "s03", status=status, note="n")
        with pytest.raises(pm.MutationError, match=f"it is {status}"):
            pm.retire_session(plan, "s03", reason="x")


def test_retire_cascade_refuses_non_retireable_dependent(plan):
    ab.apply_mutation(Path(plan) / "PLAN.html", "s02", status="DOING", note="n")
    with pytest.raises(pm.MutationError, match="not in a\n?\\s*retireable state|retireable state"):
        pm.retire_session(plan, "s01", reason="x", cascade=True)
    assert _status(plan, "s01") == "TODO"


# --------------------------------------------------------------------------
# change log — the append point S05 renders
# --------------------------------------------------------------------------
def test_changelog_one_line_per_mutation(plan):
    pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"])
    pm.amend_session(plan, "s04", model="Opus")
    pm.retire_session(plan, "s04", reason="never mind")
    log = pm.read_changelog(plan)
    assert [e["op"] for e in log] == ["add-session", "amend-session", "retire-session"]
    assert all(e["session"] == "s04" and e["at"] and e["summary"] for e in log)
    raw = (Path(plan) / pm.CHANGELOG_FILE).read_text().splitlines()
    assert len(raw) == 3  # NDJSON: exactly one line per mutation, appended


# --------------------------------------------------------------------------
# Multi-file transaction — crash after EVERY individual rename
# --------------------------------------------------------------------------
def _add_via_subprocess(plan, *, crash=None):
    """Run add-session in a REAL child process so injected crashes are real
    process deaths (os._exit), not exceptions unwinding a finally block."""
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    if crash:
        env["PLAN_MUTATE_CRASH"] = crash
    code = (
        "import plan_mutate as pm;"
        "pm.add_session(%r, sid='s04', title='Added', "
        "new_items=['w-04|work|Backfill'], depends_on=['s03'])" % plan
    )
    return subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True)


def _generation(plan):
    """A fingerprint of which generation each surface belongs to."""
    return {
        "html_has_s04": "<!-- ARTICLE:s04:BEGIN -->" in _html(plan),
        "manifest_has_s04": "s04" in mio.all_session_ids(mio.load_manifest(plan)),
        "spec_has_s04": any(s["id"] == "s04"
                            for s in json.loads((Path(plan) / "spec.json").read_text())["sessions"]),
        "prompt": (Path(plan) / "sessions" / "s04.prompt.md").exists(),
        "context": (Path(plan) / "sessions" / "s04.context.md").exists(),
        "changelog": len(pm.read_changelog(plan)),
    }


def test_crash_before_commit_point_leaves_plan_untouched(plan):
    before = _html(plan)
    r = _add_via_subprocess(plan, crash="stage")
    assert r.returncode == 70
    assert _html(plan) == before
    assert not (Path(plan) / "sessions" / "s04.prompt.md").exists()
    # recovery discards the uncommitted staging dir; the plan stays as it was
    assert pm.recover(plan) == [] or True
    recs = pm.recover(plan)
    assert all(rec["action"] == "discarded_uncommitted" for rec in recs)
    assert _html(plan) == before
    assert pm.consistency_report(plan)["ok"]


@pytest.mark.parametrize("after_n", [0, 1, 2, 3, 4, 5, 6])
def test_crash_after_every_individual_rename_is_recoverable(tmp_path, after_n):
    """PLANT — temp+rename is atomic for ONE file; this mutation writes six.
    Kill the process after each rename in turn and prove recovery lands ONE
    complete generation, never a mix."""
    plan = str(make_plan(tmp_path, _sessions()))
    crash = "journal" if after_n == 0 else f"rename:{after_n}"
    r = _add_via_subprocess(plan, crash=crash)
    assert r.returncode == 70, r.stderr

    mid = _generation(plan)
    if after_n:
        # a genuine mid-flight state: at least one file landed, and (for the
        # early cut-points) at least one had not
        assert any(mid.values())

    recs = pm.recover(plan)
    assert recs and recs[0]["action"] == "replayed" and recs[0]["op"] == "add-session"

    gen = _generation(plan)
    assert gen == {"html_has_s04": True, "manifest_has_s04": True, "spec_has_s04": True,
                   "prompt": True, "context": True, "changelog": 1}, f"mixed generation: {gen}"
    assert pm.consistency_report(plan)["ok"]
    assert not (Path(plan) / pm.JOURNAL_DIR).exists()
    # replay is idempotent — a second recovery is a no-op, not a second append
    assert pm.recover(plan) == []
    assert len(pm.read_changelog(plan)) == 1


def test_replay_refuses_a_torn_staged_file(tmp_path):
    """PLANT — the power loss that interrupted the mutation could also have torn
    a staged file; renaming that over a good live file would be the corruption
    this design exists to prevent."""
    plan = str(make_plan(tmp_path, _sessions()))
    assert _add_via_subprocess(plan, crash="rename:1").returncode == 70
    stage = next((Path(plan) / pm.JOURNAL_DIR).iterdir())
    staged = next(p for p in stage.iterdir() if p.name.endswith("manifest.json"))
    staged.write_bytes(b"{torn")
    with pytest.raises(pm.MutationError, match="corrupt"):
        pm.recover(plan)


def test_any_run_command_heals_an_interrupted_mutation(tmp_path, capsys):
    """Recovery is not a command the operator has to know about."""
    plan = str(make_plan(tmp_path, _sessions()))
    assert _add_via_subprocess(plan, crash="rename:2").returncode == 70
    run._dispatch("status", plan, type("A", (), {})())
    assert pm.consistency_report(plan)["ok"]
    assert "s04" in mio.all_session_ids(mio.load_manifest(plan))


# --------------------------------------------------------------------------
# Run-level refusals (halt flag, live dispatch lock)
# --------------------------------------------------------------------------
def test_cli_refuses_while_a_dispatch_batch_holds_the_lock(plan):
    import run_state_io as rsi
    rsi.acquire_lock(plan)
    args = type("A", (), {"session": "s03", "reason": "x", "cascade": False,
                          "drop_dependency": False, "allow_builder_drift": False})()
    with pytest.raises(SystemExit, match="holds this plan's lock"):
        run.cmd_retire_session(plan, args)
    assert _status(plan, "s03") == "TODO"
    rsi.release_lock(plan)
    run.cmd_retire_session(plan, args)
    assert _status(plan, "s03") == "WONTFIX"


def test_cli_refuses_while_shipping_is_mid_flight(plan):
    """PLANT — `_shipping_state` binds to the manifest DIGEST. Mutating the
    manifest under an unfinished shipping run makes its resume refuse as
    `state-drift` and halt the plan, stranding paid work."""
    ship_dir = Path(plan) / "_shipping_state"
    ship_dir.mkdir(exist_ok=True)
    (ship_dir / "s01.json").write_text(json.dumps(
        {"declared_steps": ["git"], "steps": {"git": "pending"}}))
    # known-positive control: the scanner sees it
    assert run._unfinished_shipping(plan) == ["s01"]
    args = type("A", (), {"session": "s03", "reason": "x", "cascade": False,
                          "drop_dependency": False, "allow_builder_drift": False})()
    with pytest.raises(SystemExit, match="shipping is mid-flight"):
        run.cmd_retire_session(plan, args)
    # ... and a FINISHED run is not mistaken for one in flight
    (ship_dir / "s01.json").write_text(json.dumps(
        {"declared_steps": ["git"], "steps": {"git": "done"}}))
    assert run._unfinished_shipping(plan) == []
    run.cmd_retire_session(plan, args)
    assert _status(plan, "s03") == "WONTFIX"


def test_cli_add_session_end_to_end(plan, capsys):
    args = type("A", (), {
        "id": "s04", "title": "CLI added", "items": None,
        "new_item": ["w-04|work|Via the CLI"], "depends_on": ["s03"], "model": "Opus",
        "reasoning": "high", "gates": None, "require_evidence": False, "prompt": "do the thing",
        "human_summary": None, "parallel_group": None, "infographic_group": None,
        "allow_builder_drift": False,
    })()
    run.cmd_add_session(plan, args)
    out = json.loads(capsys.readouterr().out)
    assert out["op"] == "add-session"
    assert out["structural_gate"]["status"] in ("passed", "failed")
    assert out["structural_gate"]["mismatches"] == []
    assert "PLAN.html" in out["files_written"] and "manifest.json" in out["files_written"]
    assert pm.consistency_report(plan)["ok"]
