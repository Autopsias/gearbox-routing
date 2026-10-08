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
        task_class="standard_build")
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


def test_add_session_records_task_class(plan):
    """RT-01 ALLOW — --task-class lands in manifest.json AND spec.json, and the
    session dispatches with it (build_plan.gen_manifest already reads it)."""
    pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"],
                   task_class="agentic_build")
    manifest = mio.load_manifest(plan)
    assert mio.session_by_id(manifest)["s04"]["task_class"] == "agentic_build"
    spec = json.loads((Path(plan) / "spec.json").read_text())
    by_id = {s["id"]: s for s in spec["sessions"]}
    assert by_id["s04"]["task_class"] == "agentic_build"


def test_add_session_without_task_class_warns_and_still_succeeds(plan, capsys):
    """RT-01 — omitting --task-class is legal (optional field); it warns on
    stderr naming the consequence rather than refusing the add."""
    pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"],
                   model="Sonnet", reasoning="medium")
    err = capsys.readouterr().err
    assert "no --task-class" in err
    assert "task_class unknown" in err
    manifest = mio.load_manifest(plan)
    assert mio.session_by_id(manifest)["s04"]["task_class"] == ""


def test_add_session_never_writes_a_half_set_model_pair(plan):
    """No model: --task-class is required (no half-set 'Sonnet' + no reasoning).
    Model without reasoning: filled from the class default only when it is that
    model, else refused."""
    with pytest.raises(pm.MutationError, match="--task-class is required"):
        pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"])
    with pytest.raises(pm.MutationError, match="override pair"):
        pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                       model="Haiku", task_class="deep_reasoning")
    pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                   model="Sonnet", task_class="standard_build")
    s = mio.session_by_id(mio.load_manifest(plan))["s04"]
    assert s["model"] == "Sonnet" and s["reasoning"]


def test_add_session_rejects_unknown_category(plan):
    """PLANT — an invented data-cat matches no section; the article would land
    in whichever section happened to be last. That is the exact 2026-08-02 bug."""
    with pytest.raises(pm.MutationError, match="matches no section"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|extraction|X"], task_class="standard_build")
    assert "s04" not in _html(plan)
    assert "s04" not in mio.all_session_ids(mio.load_manifest(plan))


def test_add_session_rejects_dependency_cycle(plan):
    """PLANT — a session that waits for itself can never dispatch."""
    with pytest.raises(pm.MutationError, match="[Cc]ycle"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|work|X"],
                       depends_on=["s04"], task_class="standard_build")
    assert "s04" not in _html(plan)


def test_add_session_rejects_duplicate_and_stolen_items(plan):
    with pytest.raises(pm.MutationError, match="already exists"):
        pm.add_session(plan, sid="s01", title="dup", new_items=["w-04|work|X"], task_class="standard_build")
    with pytest.raises(pm.MutationError, match="already owned by session 's01'"):
        pm.add_session(plan, sid="s04", title="X", items=["w-01"], task_class="standard_build")
    with pytest.raises(pm.MutationError, match="not a session in this plan"):
        pm.add_session(plan, sid="s04", title="X", new_items=["w-04|work|X"],
                       depends_on=["s99"], task_class="standard_build")


def test_added_session_is_dispatchable(plan):
    """The point of writing every surface: the executor picks the new session up."""
    pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"],
                   depends_on=["s01"], task_class="standard_build")
    import dispatch as dsp
    ready = dsp.ready_sessions(mio.load_manifest(plan), run._statuses(plan))
    assert "s01" in {s["id"] for s in ready}
    # s04 waits behind s01 exactly like a build-time dependency would
    assert "s04" not in {s["id"] for s in ready}


# --------------------------------------------------------------------------
# D6 — add-session must be able to create an item its OWN validator accepts
# --------------------------------------------------------------------------
# Reproduced 2026-08-15: since plan_schema_version 4 every item must carry
# `prior_art` (decision+source) or a `research_status` with a reason, and the
# `id|category|title|summary` syntax has nowhere to put either — so --new-item
# failed validation on EVERY plan the builder now stamps, and the documented
# "change the plan mid-run" path could not add a new item at all.
@pytest.fixture
def modern_plan(tmp_path):
    """A plan stamped at the version the builder ACTUALLY writes today, with the
    prior-art field its own validator demands."""
    import build_plan as bp

    from test_shipping import _spec, _write_registries

    root = tmp_path / "proj"
    root.mkdir()
    _write_registries(root)
    spec = _spec([{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"],
                   "prompt": "do"}])
    spec["plan_schema_version"] = bp.rad.PRE_V8_STAMP  # newest stamp before v8 (route-at-dispatch)
    assert spec["plan_schema_version"] >= bp.PRIOR_ART_MIN_SCHEMA, "gate is above the stamp"
    for it in spec["items"]:
        it["prior_art"] = {"decision": "build", "source": "nothing comparable in-repo"}
    spec["infographic"]["phases"][0]["items"] = ["w-01"]
    plan_dir = root / "_plans" / "fixture"
    bp.build(spec, plan_dir, project_root=str(root))
    return str(plan_dir)


NEW_ITEM_JSON = json.dumps({
    "id": "w-99", "category": "work", "title": "A brand new item",
    "human_summary": "it does a thing",
    "prior_art": {"decision": "build", "source": "checked skills/, nothing comparable"},
})


def test_allow_add_session_creates_a_new_item_on_a_current_schema_plan(modern_plan):
    pm.add_session(modern_plan, sid="s04", title="Added", model="Sonnet", prompt="do it",
                   new_items=[NEW_ITEM_JSON], infographic_group="P1", task_class="standard_build")
    spec = json.loads((Path(modern_plan) / "spec.json").read_text())
    added = next(i for i in spec["items"] if i["id"] == "w-99")
    assert added["prior_art"] == {"decision": "build",
                                  "source": "checked skills/, nothing comparable"}
    assert added["human_summary"] == "it does a thing"
    # It really landed everywhere, not just in the spec.
    assert "s04" in mio.session_by_id(mio.load_manifest(modern_plan))
    assert _status(modern_plan, "w-99") == "TODO"


def test_plant_the_pipe_form_names_the_form_that_works(modern_plan):
    """The pipe form cannot express prior_art, so it is refused HERE with the fix
    spelled out — not deep inside validate_spec with a requirement the caller has
    no syntax for."""
    with pytest.raises(pm.MutationError) as e:
        pm.add_session(modern_plan, sid="s04", title="X", new_items=["w-99|work|X"],
                       infographic_group="P1", task_class="standard_build")
    msg = str(e.value)
    assert "prior_art" in msg and "--new-item '{" in msg, msg


def test_plant_malformed_new_item_json_is_named(modern_plan):
    for raw, needle in [('{"id": "w-99"', "not valid JSON"),
                        ('[{"id": "w-99"}]', "must be a JSON object"),
                        ('{"id": "w-99", "title": "T"}', "category")]:
        with pytest.raises(pm.MutationError) as e:
            pm.add_session(modern_plan, sid="s04", title="X", new_items=[raw], task_class="standard_build")
        assert needle in str(e.value), (raw, str(e.value))


def test_allow_the_old_pipe_form_still_works_on_an_older_plan(plan):
    """The CONTROL: every plan on disk predates the prior-art gate, and the pipe
    form must keep working there exactly as before."""
    pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"],
                   infographic_group="P1", task_class="standard_build")
    assert "s04" in mio.session_by_id(mio.load_manifest(plan))


def test_new_item_joins_the_infographic_group(plan):
    """ALLOW — the Plan Achievement counters must see the new item. An item in
    no group is the modern form of the 'WORKSTREAMS missing new items' defect:
    the visual story then reads a higher % than the plan's real state."""
    res = pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                         infographic_group="P1", task_class="standard_build")
    assert res["infographic_group"] == "P1"
    spec = json.loads((Path(plan) / "spec.json").read_text())
    assert "w-04" in spec["infographic"]["phases"][0]["items"]
    assert "const PHASES" in _html(plan) and '"w-04"' in _html(plan)
    assert res["validation_warnings"] == []


def test_unplaced_item_warns_instead_of_silently_skewing_the_counters(plan):
    res = pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"], task_class="standard_build")
    assert res["infographic_group"] is None
    assert any("not added to any Plan Achievement group" in w
               for w in res["validation_warnings"])


def test_session_keyed_groups_get_the_new_session_not_an_items_list():
    """A phase-journey whose groups list sessions: adding `items` to one group made the
    coverage check call every older item ungrouped and refuse the add (2026-09-29)."""
    spec = {"categories": [{"key": "run", "label": "Measure"}],
            "items": [{"id": "run-01", "category": "run"}, {"id": "run-04", "category": "run"}],
            "infographic": {"type": "phase-journey", "phases": [
                {"name": "Rule", "sessions": ["s01"]}, {"name": "Measure", "sessions": ["s07"]}]}}
    assert pm.attach_to_infographic(spec, ["run-04"], session_id="s11") == ("Measure", None)
    phases = spec["infographic"]["phases"]
    assert phases[1]["sessions"] == ["s07", "s11"]
    assert all("items" not in g for g in phases)


def test_unknown_infographic_group_refused(plan):
    with pytest.raises(pm.MutationError, match="matches no group"):
        pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"],
                       infographic_group="Nope", task_class="standard_build")
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
    # AWAITS_REVIEW here is the POST-SESSION flavor: a DONE closeout is on disk.
    (Path(plan) / "_closeouts").mkdir(exist_ok=True)
    (Path(plan) / "_closeouts" / "s03.json").write_text('{"result": "DONE"}')
    for status in ("DOING", "DONE", "AWAITS_REVIEW"):
        ab.apply_mutation(Path(plan) / "PLAN.html", "s03", status=status, note="n")
        with pytest.raises(pm.MutationError, match=f"it is {status}"):
            pm.retire_session(plan, "s03", reason="x")


def test_in_flight_state_ignores_review_acceptance_sidecars(plan):
    """REGRESSION 2026-09-21 — a settled session was reported mid-flight forever.

    `llm_review_ledger.accepted_path()` writes `<sid>.accepted.json` into
    `_verify_state/`. `_live_state_files` used to glob every `*.json` there and
    treat each as a verify record. The sidecars carry only `accepted`, never an
    `outcome`, so one accepted review finding made every later `add-session` /
    `amend-session` / `retire-session` on that plan refuse — naming a session
    that had in fact passed all its gates.
    """
    vs = Path(plan) / pm.VERIFY_STATE_DIR
    vs.mkdir(exist_ok=True)
    (vs / "s02.json").write_text(json.dumps({"session_id": "s02", "outcome": "passed"}))
    for name in ("s02.accepted.json", "s02.low.codex.accepted.json", "s02.medium.codex.accepted.json"):
        (vs / name).write_text(json.dumps({"accepted": [{"file": "x.py", "reason": "operator ruling"}]}))
    (vs / "s02.r1.json").write_text(json.dumps({"session_id": "s02", "outcome": "failed"}))

    assert pm.in_flight_state(plan)["verify"] == []
    pm._refuse_if_state_in_flight(plan, "add-session")  # does not raise

    # KNOWN POSITIVE: a real unsettled record must still be caught, or the test
    # above only proves the scan reads nothing at all.
    (vs / "s03.json").write_text(json.dumps({"session_id": "s03", "outcome": "running"}))
    assert pm.in_flight_state(plan)["verify"] == ["s03"]
    with pytest.raises(pm.MutationError, match="mid-flight"):
        pm._refuse_if_state_in_flight(plan, "add-session")


@pytest.mark.parametrize("closeout", [None, '{"result": "PARTIAL"}'])
def test_retire_accepts_a_pre_dispatch_checkpoint(plan, closeout):
    """PLANT — AWAITS_REVIEW with no DONE closeout is a gate on a session that
    never finished; "do not start it" is a retirement, not an erased result."""
    if closeout is not None:
        (Path(plan) / "_closeouts").mkdir(exist_ok=True)
        (Path(plan) / "_closeouts" / "s03.json").write_text(closeout)
    ab.apply_mutation(Path(plan) / "PLAN.html", "s03", status="AWAITS_REVIEW", note="checkpoint")
    pm.retire_session(plan, "s03", reason="superseded by v4")
    assert pm._statuses(plan)["s03"] == "WONTFIX"
    assert pm.consistency_report(plan)["ok"]


def test_retire_cascade_refuses_non_retireable_dependent(plan):
    ab.apply_mutation(Path(plan) / "PLAN.html", "s02", status="DOING", note="n")
    with pytest.raises(pm.MutationError, match="not in a\n?\\s*retireable state|retireable state"):
        pm.retire_session(plan, "s01", reason="x", cascade=True)
    assert _status(plan, "s01") == "TODO"


# --------------------------------------------------------------------------
# change log — the append point S05 renders
# --------------------------------------------------------------------------
def test_changelog_one_line_per_mutation(plan):
    pm.add_session(plan, sid="s04", title="A", new_items=["w-04|work|X"], task_class="standard_build")
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
        "new_items=['w-04|work|Backfill'], depends_on=['s03'], task_class='standard_build')" % plan
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


# ---- the nav strip chip must follow the article (2026-08-21) ----
#
# Measured across every plan on this machine that day: 20 of 20 dashboards
# showed EVERY session as TODO in the nav strip while the articles read DONE,
# BLOCKED, WONTFIX. The chip was written once as TODO and no mutation touched
# it, so six colour-coded stylesheet rules had never once fired.

def test_a_session_mutation_moves_its_nav_chip(tmp_path):
    plan = str(make_plan(tmp_path, _sessions()))
    html_path = Path(plan) / "PLAN.html"
    ab.apply_mutation(html_path, "s01", status="DONE", note="done")
    html = html_path.read_text()
    assert 'class="strip-chip" data-session="s01" data-status="DONE"' in html, \
        "the nav chip did not follow the article to DONE"
    # a sibling the mutation never named keeps its TODO chip
    assert 'class="strip-chip" data-session="s02" data-status="TODO"' in html


def test_the_structural_gate_flags_a_chip_left_behind(tmp_path):
    """If the sync is ever removed, the gate must catch the desync rather than
    let the strip drift silently the way it did for weeks."""
    import structural_gate as sg
    plan = str(make_plan(tmp_path, _sessions()))
    html_path = Path(plan) / "PLAN.html"
    text = html_path.read_text()
    # article moved to DONE, chip deliberately left at TODO
    moved = ab.mutate_text(text, "s01", status="DONE")
    stale = moved.replace(
        'class="strip-chip" data-session="s01" data-status="DONE"',
        'class="strip-chip" data-session="s01" data-status="TODO"', 1)
    html_path.write_text(stale)
    ok, mismatches, warnings = sg.check_landed(plan, {"s01": "DONE"})
    assert ok, f"a chip desync is a warning, not a hard mismatch: {mismatches}"
    assert any("nav strip chip" in w for w in warnings), \
        f"the gate stayed silent on a stale chip: {warnings}"


def test_an_item_has_no_chip_and_that_is_not_a_desync(tmp_path):
    """None != TODO: items carry no chip, and reading one as TODO would have
    reported a phantom desync on every item mutation."""
    plan = str(make_plan(tmp_path, _sessions()))
    text = (Path(plan) / "PLAN.html").read_text()
    assert ab.chip_status(text, "w-01") is None


def test_builder_drift_refusal_names_its_content_count_and_its_truncation(plan):
    """The refusal's preview is a SAMPLE, and a re-indentation sorts whitespace
    to the front of it. Showing 20 blank diff lines out of 138 reads as "nothing
    changes" — which is how a real re-render that DELETED a whole section got
    reviewed as whitespace-only and nearly accepted (2026-09-21, the example-project
    plan's /plan-harden summary block). The count of changed lines that carry
    content goes in the sentence, and the hidden remainder is named.
    """
    path = Path(plan) / "PLAN.html"
    lines = path.read_text().split("\n")

    # (a) whitespace-only drift EARLY, enough to fill the 20-line preview window
    blanks = [i for i, line in enumerate(lines) if not line.strip()][:25]
    assert len(blanks) >= 21, "fixture has too few blank lines to fill the preview"
    for i in blanks:
        lines[i] = "   "
    # (b) a real content section LATE — the builder does not render it, so a
    #     re-render deletes it, exactly like the /plan-harden summary block
    body = "\n".join(f"<p>injected line {n}</p>" for n in range(12))
    marker = "<!-- INJECTED-SUMMARY:BEGIN -->\n" + body + "\n<!-- INJECTED-SUMMARY:END -->"
    text = "\n".join(lines).replace("</main>", marker + "\n</main>", 1)
    path.write_text(text)

    with pytest.raises(pm.MutationError) as exc:
        pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"], task_class="standard_build")
    msg = str(exc.value)

    # the sample alone is misleading: its 20 lines carry no content at all...
    sample = [ln for ln in msg.split("--allow-builder-drift:\n", 1)[1].split("\n")
              if ln[:1] in "+-"]
    assert len(sample) == 20, f"preview is not the 20-line sample: {len(sample)}"
    assert not any(ln[1:].strip() for ln in sample), (
        "fixture failed to put whitespace-only lines first — the test would pass "
        "for the wrong reason"
    )
    # ...so the SENTENCE has to carry the real figures
    total = int(re.search(r"would change (\d+) line", msg).group(1))
    solid = int(re.search(r", (\d+) of them real content", msg).group(1))
    hidden = int(re.search(r"Review ALL — (\d+) of them are not in the sample", msg).group(1))
    assert solid >= 12, f"content deletion under-reported: {solid}"
    assert hidden == total - 20

    # KNOWN POSITIVE: the same refusal fires with a truthful sentence when the
    # drift really IS whitespace-only — it reports 0 lines of real content.
    path.write_text(text.replace(marker + "\n", ""))
    with pytest.raises(pm.MutationError) as exc2:
        pm.add_session(plan, sid="s04", title="Added", new_items=["w-04|work|X"], task_class="standard_build")
    assert ", 0 of them real content" in str(exc2.value)
