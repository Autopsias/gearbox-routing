"""RP-04 — the containment gate: eight PLANTS and one ALLOW control.

The status gate (`test_structural_gate.py`) proves a session cannot stand as DONE
unless its `data-status` write landed. It did NOT stop the 2026-08-02 corruption:
six sessions hand-added to a live plan, every pass breaking a different page
surface, every one passing the status gate and an anchor-balance check.

This suite plants each of those defects into a REAL built plan and proves the
check fires — because a gate whose plant never fired is a gate that cannot fail,
and the repo's dominant historical defect is exactly a check that could not fail:

  1  data-cat        an item article moved into the wrong <section>
  2  session-strip   a nav chip missing while the manifest still has the session
  3  section-blurb   "N sessions · M items" left stale
  4  workstreams     the const array the progress bar counts through, missing an
                     item (and referencing a ghost one)
  5  document-order  session articles reordered — "Up next" reads document order
  6  header-totals   the SESSION_TOTAL_COUNT stamp / header meta line stale
  7  membership      a manifest item with no article at all
  8  parallel-group  group members with different depends_on (the batch silently
                     dissolves into sequential dispatch, with no error anywhere)
  9  verify-state    a mutation under an unsettled verify cycle is refused, and a
                     settled one is migrated forward with `rework_count` intact

Every plant is paired with the ALLOW control (`test_allow_control_is_clean`): the
same untouched plan reports zero problems, so none of the above can be passing
for the trivial reason that the checker always complains.

`main()` re-runs every plant and prints the fired output as markdown — that is
how `_evidence/s04b/containment-plants.md` is produced, from this same code
rather than from a hand-written transcript.

Run: pytest skills/plan-execute/scripts/test_containment_gate.py -q
"""

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import plan_mutate as pm  # noqa: E402
import structural_gate as sg  # noqa: E402
import verify as vfy  # noqa: E402


# --------------------------------------------------------------------------
# Fixture — a CONSISTENT plan (the allow control) with two categories, a
# parallel group, and an infographic whose groups cover every item.
# --------------------------------------------------------------------------
SESSIONS = [
    {"id": "s01", "title": "Groundwork", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
    {"id": "s02", "title": "Build", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
     "dispatch": {"depends_on": ["s01"], "parallel_group": "g1"}},
    {"id": "s03", "title": "Document", "model": "Sonnet", "items": ["d-01"], "prompt": "do",
     "dispatch": {"depends_on": ["s01"], "parallel_group": "g1"}},
]


def _spec():
    return {
        "title": "Containment Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}, {"key": "docs", "label": "Docs"}],
        "items": [
            {"id": "w-01", "title": "W1", "category": "work"},
            {"id": "w-02", "title": "W2", "category": "work"},
            {"id": "d-01", "title": "D1", "category": "docs"},
        ],
        "phases": [],
        "sessions": SESSIONS,
        # before-after is the shape whose const is literally WORKSTREAMS — the
        # array named in the 2026-08-02 incident.
        "infographic": {
            "type": "before-after", "title": "t",
            "before": {"name": "Before", "bullets": ["a"]},
            "after": {"name": "After", "bullets": ["b"]},
            # Group names deliberately DON'T match the category labels, so
            # `attach_to_infographic`'s auto-placement cannot quietly cover for
            # the orphaned-item plant below.
            "workstreams": [
                {"name": "Track A", "items": ["w-01", "w-02"]},
                {"name": "Track B", "items": ["d-01"]},
            ],
        },
    }


def build_fixture(tmp_path):
    project_root = Path(tmp_path) / "proj"
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text("{}")
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec(), plan_dir, project_root=str(project_root))
    return plan_dir


@pytest.fixture
def plan(tmp_path):
    return build_fixture(tmp_path)


def _read(plan_dir):
    html = (Path(plan_dir) / "PLAN.html").read_bytes().decode("utf-8")
    manifest = json.loads((Path(plan_dir) / "manifest.json").read_text())
    return html, manifest


def _move_block(html, aid, *, after):
    """Cut article `aid` out and re-insert it after article `after`'s block —
    the literal 2026-08-02 corruption (articles appended past the last anchor,
    landing in whichever section came last)."""
    s, e, block = ab.extract_block(html, aid)
    html = html[:s] + html[e:]
    _, dst_end, _ = ab.extract_block(html, after)
    return html[:dst_end] + "\n" + block + html[dst_end:]


def tagged(problems, tag):
    return [p for p in problems if p.startswith(f"[{tag}]")]


# --------------------------------------------------------------------------
# The plants. Each returns (label, tag, problems) so the pytest cases and the
# evidence writer run the SAME code — a plant proven in one and narrated in the
# other is not proven.
# --------------------------------------------------------------------------
def plant_data_cat(plan_dir):
    html, manifest = _read(plan_dir)
    # w-01 keeps data-cat="work" but is relocated into the Docs section.
    html = _move_block(html, "w-01", after="d-01")
    return sg.containment_report(html, manifest)


def plant_session_strip(plan_dir):
    html, manifest = _read(plan_dir)
    start = html.index('<a href="#s03" class="strip-chip"')
    end = html.index("</a>", start) + 4
    return sg.containment_report(html[:start] + html[end:], manifest)


def plant_section_blurb(plan_dir):
    html, manifest = _read(plan_dir)
    html = html.replace(
        '<span class="section-blurb">3 sessions · 3 items</span>',
        '<span class="section-blurb">2 sessions · 2 items</span>',
    )
    return sg.containment_report(html, manifest)


def plant_workstreams(plan_dir):
    html, manifest = _read(plan_dir)
    # The exact defect: an item never added to a group (the progress bar counts
    # a short denominator), plus a group entry pointing at an item that is gone.
    m = re.search(r"\bconst WORKSTREAMS\s*=\s*", html)
    groups, end = json.JSONDecoder().raw_decode(html, m.end())
    groups[1]["items"] = ["ghost-99"]
    return sg.containment_report(html[:m.end()] + json.dumps(groups) + html[end:], manifest)


def plant_document_order(plan_dir):
    html, manifest = _read(plan_dir)
    html = _move_block(html, "s01", after="s03")
    return sg.containment_report(html, manifest)


def plant_header_totals(plan_dir):
    html, manifest = _read(plan_dir)
    html = html.replace("SESSION_TOTAL_COUNT: 3", "SESSION_TOTAL_COUNT: 2")
    html = html.replace('<div class="meta">3 sessions · 3 items</div>',
                        '<div class="meta">2 sessions · 2 items</div>')
    return sg.containment_report(html, manifest)


def plant_membership(plan_dir):
    html, manifest = _read(plan_dir)
    s, e, _ = ab.extract_block(html, "d-01")
    return sg.containment_report(html[:s] + html[e:], manifest)


def plant_parallel_group(plan_dir):
    html, manifest = _read(plan_dir)
    for s in manifest["sessions"]:
        if s["id"] == "s03":
            s["dispatch"]["depends_on"] = ["s02"]
    return sg.containment_report(html, manifest)


PLANTS = [
    ("item data-cat vs enclosing <section data-cat>", "data-cat", plant_data_cat),
    ('<nav class="session-strip"> chip set', "session-strip", plant_session_strip),
    ('<span class="section-blurb">N sessions · M items</span>', "section-blurb",
     plant_section_blurb),
    ("const WORKSTREAMS (progress bar denominator)", "workstreams", plant_workstreams),
    ('document order feeding the "Up next" panel', "document-order", plant_document_order),
    ("header totals (SESSION_TOTAL_COUNT + meta line)", "header-totals", plant_header_totals),
    ("manifest ⇄ article membership", "membership", plant_membership),
    ("parallel_group depends_on symmetry", "parallel-group", plant_parallel_group),
]


@pytest.mark.parametrize("label,tag,fn", PLANTS, ids=[p[1] for p in PLANTS])
def test_plant_fires(plan, label, tag, fn):
    problems = fn(plan)
    assert tagged(problems, tag), f"plant {label!r} did not fire: {problems}"


def test_allow_control_is_clean(plan):
    """The untouched plan reports nothing — without this, every plant above
    could be firing because the checker always complains."""
    html, manifest = _read(plan)
    assert sg.containment_report(html, manifest) == []
    assert sg.check_containment(plan) == {"status": "passed", "problems": []}


# --------------------------------------------------------------------------
# Wiring — the gate refuses a corrupting mutation BEFORE the write
# --------------------------------------------------------------------------
def test_amend_breaking_group_symmetry_is_refused_before_write(plan):
    """Two independent guards, and the mutation needs only one of them to hold:
    plan-builder's `validate_spec` catches it in the SPEC, and
    `group_coherence_problems` catches it in the MANIFEST — which is the copy
    `dispatch.next_action` actually reads, and the one a hand edit touches."""
    before = (plan / "PLAN.html").read_bytes()
    before_manifest = (plan / "manifest.json").read_bytes()
    with pytest.raises(pm.MutationError) as e:
        pm.amend_session(plan, "s03", depends_on=["s02"])
    assert "mismatched depends_on" in str(e.value)
    broken = json.loads(before_manifest)
    for s in broken["sessions"]:
        if s["id"] == "s03":
            s["dispatch"]["depends_on"] = ["s02"]
    assert tagged(sg.group_coherence_problems(broken), "parallel-group")
    # Refused BEFORE the commit point: not one live file moved.
    assert (plan / "PLAN.html").read_bytes() == before
    assert (plan / "manifest.json").read_bytes() == before_manifest
    assert not (plan / "_mutation").exists()
    assert not (plan / "_changelog.ndjson").exists()


def test_allow_control_mutation_lands_and_stays_clean(plan):
    """The same command, valid: add a session and the page is still coherent."""
    res = pm.add_session(plan, sid="s04", title="Extra", new_items=["w-03|work|W3"],
                         depends_on=["s01"], infographic_group="Track A")
    assert res["op"] == "add-session"
    assert sg.check_containment(plan)["status"] == "passed"


def test_add_session_that_would_orphan_an_item_is_refused(plan):
    """A new item placed in NO infographic group is the literal 92%-vs-70%
    defect. The mutation is refused rather than quietly shipping a wrong bar."""
    with pytest.raises(pm.MutationError) as e:
        pm.add_session(plan, sid="s04", title="Extra", new_items=["w-03|work|W3"],
                       infographic_group=None)
    assert "[workstreams]" in str(e.value)
    assert (plan / "spec.json").is_file() and not (plan / "_changelog.ndjson").exists()


def test_prior_breach_downgrades_the_same_check_to_a_warning(plan):
    """A plan that already leaves items out of the progress bar never had that
    invariant, so one more must not strand it — it warns and proceeds. The same
    mutation on the CLEAN plan is refused (test above): that contrast is the
    whole rule, and testing only one half would prove nothing."""
    html = (plan / "PLAN.html").read_bytes().decode("utf-8")
    spec = json.loads((plan / "spec.json").read_text())
    spec["infographic"]["workstreams"][1]["items"] = []  # d-01 orphaned already
    (plan / "spec.json").write_text(json.dumps(spec, indent=2))
    (plan / "PLAN.html").write_bytes(
        ab.carry_over_state(html, build_plan.render_html(spec, plan)).encode("utf-8")
    )
    res = pm.add_session(plan, sid="s04", title="Extra", new_items=["w-03|work|W3"])
    assert any("[workstreams]" in w and "w-03" in w for w in res["validation_warnings"]), \
        res["validation_warnings"]
    # …and the pre-existing d-01 breach is NOT re-reported as this mutation's doing.
    assert not any("d-01" in w for w in res["validation_warnings"])


# --------------------------------------------------------------------------
# 9 — recorded verify state across a mutation
# --------------------------------------------------------------------------
def _verify_state(plan_dir, sid, **over):
    state = {
        "session_id": sid,
        "manifest_digest": pm.hashlib.sha256(
            (Path(plan_dir) / "manifest.json").read_bytes()).hexdigest(),
        "closeout_digest": None, "gates": ["smoke"], "on_fail": "rework",
        "max_rework": 1, "rework_count": 1, "gate_status": {"smoke": "failed"},
        "outcome": "halted", "failures": {},
    }
    state.update(over)
    p = vfy.verify_state_path(plan_dir, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))
    return p


def test_mutation_refused_while_verify_state_is_unsettled(plan):
    _verify_state(plan, "s01", outcome="rework", rework_count=1)
    before = (plan / "PLAN.html").read_bytes()
    with pytest.raises(pm.MutationError) as e:
        pm.amend_session(plan, "s02", prompt="new text")
    assert "mid-flight" in str(e.value) and "s01" in str(e.value)
    assert (plan / "PLAN.html").read_bytes() == before


def test_mutation_refused_while_a_closeout_is_unreplayed(plan):
    cp.persist(plan, "s01", {"session": "s01", "result": "DONE",
                             "items_completed": ["w-01"], "items_blocked": [], "notes": {}})
    with pytest.raises(pm.MutationError) as e:
        pm.amend_session(plan, "s02", prompt="new text")
    assert "unreplayed closeout" in str(e.value) and "s01" in str(e.value)
    cp.mark_replayed(plan, "s01")
    pm.amend_session(plan, "s02", prompt="new text")  # unblocked once replayed


def test_settled_verify_state_migrates_and_keeps_rework_count(plan):
    """A session that exhausted `max_rework` must not come out of a
    restructuring with a fresh budget."""
    p = _verify_state(plan, "s01", outcome="halted", rework_count=1, max_rework=1)
    old_digest = json.loads(p.read_text())["manifest_digest"]

    res = pm.add_session(plan, sid="s04", title="Extra", new_items=["w-03|work|W3"],
                         depends_on=["s01"], infographic_group="Track A")

    new_digest = pm.hashlib.sha256((plan / "manifest.json").read_bytes()).hexdigest()
    assert new_digest != old_digest, "fixture bug: the manifest did not change"
    state = json.loads(p.read_text())
    assert state["manifest_digest"] == new_digest      # no state-drift refusal
    assert state["rework_count"] == 1                  # budget NOT reset
    assert state["max_rework"] == 1
    assert state["migrated_from_digest"] == old_digest
    assert "_verify_state/s01.json" in res["verify_state_migrated"]
    # And the migration went through the SAME journalled transaction.
    assert not (plan / "_mutation").exists()


# --------------------------------------------------------------------------
# Evidence writer — same plants, rendered as markdown
# --------------------------------------------------------------------------
def main(out_path, cli_transcript=None, suite_result=""):  # pragma: no cover - evidence
    import tempfile
    import textwrap

    lines = [
        "# S04B · RP-04 — containment-gate plant evidence",
        "",
        "**What this proves:** every containment check FIRES on a planted defect, the",
        "untouched plan reports nothing, a corrupting mutation is refused before any",
        "live file is written, and settled verify state survives a mutation with its",
        "`rework_count` intact.",
        "",
        "| Claim | Where |",
        "|---|---|",
        "| six named PLAN.html surfaces (data-cat containment · session-strip chips · "
        "section-blurb counts · const WORKSTREAMS · UP NEXT order · header totals) each "
        "FIRE on a plant | §1.1–§1.6 |",
        "| plant 7 — parallel-group coherence FIRES | §1.8 |",
        "| plant 8 — verify-state digest: a mutation under an unsettled cycle is REFUSED "
        "| §4 |",
        "| (bonus) manifest ⇄ article membership FIRES | §1.7 |",
        "| the allow control is silent — the checker does not just always complain | §2 |",
        "| a corrupting mutation is refused BEFORE the commit point | §3 |",
        "| digest migration: `rework_count` survives a manifest mutation | §5 |",
        "| the same checks against the REAL live plan, through the real CLI | §6 |",
        "",
        "Reproduce with:",
        "",
        "```",
        "cd skills/plan-execute/scripts",
        "./_evidence_containment_cli.sh /tmp/cli-transcript.md",
        "python3 test_containment_gate.py <this-file> /tmp/cli-transcript.md \"$(pytest -q | tail -1)\"",
        "```",
        "",
        "The plant sections below run the SAME functions the pytest cases assert on, so",
        "nothing here is hand-narrated.",
        "",
        "## 1 — Plants (each must FIRE)",
        "",
    ]
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        html, manifest = _read(plan_dir)
        for n, (label, tag, fn) in enumerate(PLANTS, start=1):
            fired = tagged(fn(plan_dir), tag)
            status = "FIRED" if fired else "*** DID NOT FIRE ***"
            lines += [f"### {n}. {label} — {status}", "", "```"]
            lines += [textwrap.fill(p, 96) for p in fired] or ["(nothing)"]
            lines += ["```", ""]

        lines += ["## 2 — Allow control (must be SILENT)", "", "```"]
        clean = sg.containment_report(html, manifest)
        lines += [f"containment_report(untouched plan) -> {clean}",
                  f"check_containment(plan_dir)        -> {sg.check_containment(plan_dir)}",
                  "```", ""]

    lines += ["## 3 — Mutation wiring (refused BEFORE the write)", "", "```"]
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        before = (plan_dir / "PLAN.html").read_bytes()
        for what, fn in (
            ("amend-session s03 --depends-on s02  (breaks parallel_group symmetry)",
             lambda p: pm.amend_session(p, "s03", depends_on=["s02"])),
            ("add-session s04 --new-item w-03  (item in no WORKSTREAMS group)",
             lambda p: pm.add_session(p, sid="s04", title="Extra",
                                      new_items=["w-03|work|W3"])),
        ):
            lines.append(f"$ {what}")
            try:
                fn(plan_dir)
                lines.append("!!! NOT REFUSED")
            except pm.MutationError as e:
                lines += [str(e), "  [refused]"]
            lines.append(f"  PLAN.html unchanged: {(plan_dir / 'PLAN.html').read_bytes() == before}")
            lines.append("")
        lines += ["```", "",
                  "## 4 — Mutation refused while recorded state is mid-flight", "", "```"]
        for what, fn in (
            ("add-session under an UNSETTLED verify cycle (outcome=rework)",
             lambda p: pm.add_session(p, sid="s05", title="X",
                                      new_items=["w-05|work|W5"],
                                      infographic_group="Track A")),
        ):
            _verify_state(plan_dir, "s02", outcome="rework", rework_count=1)
            lines.append(f"$ {what}")
            try:
                fn(plan_dir)
                lines.append("!!! NOT REFUSED")
            except pm.MutationError as e:
                lines += [str(e), "  [refused]"]
            vfy.verify_state_path(plan_dir, "s02").unlink()
            lines.append("")
        lines += ["```", "", "## 5 — Verify-state digest migration", "", "```"]
        p = _verify_state(plan_dir, "s01", outcome="halted", rework_count=1, max_rework=1)
        old = json.loads(p.read_text())
        res = pm.add_session(plan_dir, sid="s04", title="Extra",
                             new_items=["w-03|work|W3"], depends_on=["s01"],
                             infographic_group="Track A")
        new = json.loads(p.read_text())
        lines += [
            f"before: digest={old['manifest_digest'][:12]}… rework_count={old['rework_count']}"
            f"/{old['max_rework']} outcome={old['outcome']}",
            f"after : digest={new['manifest_digest'][:12]}… rework_count={new['rework_count']}"
            f"/{new['max_rework']} outcome={new['outcome']}",
            f"manifest digest now: "
            f"{pm.hashlib.sha256((plan_dir / 'manifest.json').read_bytes()).hexdigest()[:12]}…",
            f"migrated in txn {res['txn']}: {res['verify_state_migrated']}",
            "```",
            "",
        ]

    # Not a fixture-only check: the same code, run against the live plan that
    # dispatched this session.
    live = Path(__file__).resolve().parents[3] / "_plans" / "plan-framework-upgrade-2026-08-12"
    if live.is_dir():
        lines += [
            "## 6 — The same check against THIS plan (not a fixture)",
            "",
            "```",
            f"$ structural_gate.check_containment({live.name})",
            json.dumps(sg.check_containment(str(live)), indent=2),
            "```",
            "",
            "A real, pre-existing finding of the 2026-08-02 class — which is also why the",
            "mutation path refuses only on problems a mutation INTRODUCES: refusing over a",
            "defect no session caused would strand this plan.",
            "",
        ]
    if cli_transcript and Path(cli_transcript).is_file():
        lines += [Path(cli_transcript).read_text(), ""]
    if suite_result:
        lines += ["## 7 — Regression run", "",
                  "`pytest skills/plan-execute/scripts -q` (the whole plan-execute suite):", "",
                  "```", suite_result.strip(), "```", ""]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines) + "\n")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    main(*sys.argv[1:])
    print(f"wrote {sys.argv[1]}")
