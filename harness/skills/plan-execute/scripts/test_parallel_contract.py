"""PL-02 — the frozen parallel-group contract's gate.

Every PLANT asserts a specific rule ID fires; every ALLOW asserts the same
shape passes clean. The allow-controls are not decoration: this gate was very
nearly shipped keyed on a manifest field the builder never wrote, which would
have made it return "clean" on every plan in existence. A plant-only suite
cannot catch that — only a control that would fail if the rule stopped firing,
paired with one that would fail if it fired on valid input.
"""

import parallel_contract as pc
import pytest

V3 = 3


def _doc(sessions, items=(), version=V3):
    return {"plan_schema_version": version, "sessions": list(sessions), "items": list(items)}


def _s(sid, *, group=None, deps=(), git=None, items=(), isolation=None,
       integrates=None, gates=()):
    d = {"depends_on": list(deps)}
    if group is not None:
        d["parallel_group"] = group
    if isolation is not None:
        d["isolation"] = isolation
    if integrates is not None:
        d["integrates_group"] = integrates
    s = {"id": sid, "dispatch": d, "items": list(items), "verify": {"gates": list(gates)}}
    if git is not None:
        s["post_session"] = {"git": git}
    return s


def _it(iid, touches=None):
    it = {"id": iid, "title": iid, "category": "c"}
    if touches is not None:
        it["touches"] = touches
    return it


def _ids(msgs):
    """The bracketed rule IDs that fired, e.g. {"M1", "M5"}."""
    return {m.split("]")[0].lstrip("[") for m in msgs if m.startswith("[")}


def _valid_group(**over):
    """A CLEAN v3 shared-tree group: two members, symmetric deps, disjoint
    declared writes, no shipping, plus a declared integration session."""
    sessions = [
        _s("s01", group="g", deps=["s00"], items=["i1"], gates=["gate-a"]),
        _s("s02", group="g", deps=["s00"], items=["i2"], gates=["gate-b"]),
        _s("s03", deps=["s00", "s01", "s02"], integrates="g", git="commit-push",
           gates=["gate-a", "gate-b"]),
    ]
    items = [_it("i1", "src/alpha.py"), _it("i2", "src/beta.py")]
    return _doc(over.get("sessions", sessions), over.get("items", items),
                over.get("version", V3))


# --------------------------------------------------------------------- ALLOW

def test_valid_group_is_clean():
    """KNOWN NEGATIVE. If this ever fails, the gate refuses legitimate plans."""
    refusals, warnings = pc.check(_valid_group(), schema_version=V3)
    assert refusals == [], refusals
    assert warnings == [], warnings


def test_ungrouped_plan_is_clean():
    doc = _doc([_s("s01", git="commit-push", items=["i1"])], [_it("i1", "uv.lock")])
    assert pc.check(doc, schema_version=V3) == ([], [])


# --------------------------------------------------------------------- PLANTS

def test_m1_member_declaring_commit_push_is_refused():
    doc = _valid_group()
    doc["sessions"][0]["post_session"] = {"git": "commit-push"}
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "M1" in _ids(refusals)
    assert "s01" in refusals[0] and "commit-push" in refusals[0]


@pytest.mark.parametrize("lockfile", ["uv.lock", "pyproject.toml", "package-lock.json",
                                      "requirements-dev.txt", "go.sum", "deps/Cargo.lock"])
def test_m2_dependency_touch_is_refused(lockfile):
    doc = _valid_group()
    doc["items"][0]["touches"] = f"src/alpha.py, {lockfile}"
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "M2" in _ids(refusals), refusals


def test_m2a_missing_touches_fails_closed():
    """The defect this whole rule exists for: no `touches` must REFUSE, never
    pass. A gate whose input is absent returns clean on everything."""
    doc = _valid_group()
    del doc["items"][0]["touches"]
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "M2a" in _ids(refusals), refusals
    # ...and M2 must NOT be reported as clean for that item.
    assert "i1" in refusals[0]


def test_m5_overlapping_writes_refused_on_shared_tree():
    doc = _valid_group()
    doc["items"][1]["touches"] = "src/alpha.py"
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "M5" in _ids(refusals), refusals


def test_m5_directory_prefix_counts_as_overlap():
    doc = _valid_group()
    doc["items"][0]["touches"] = "src"
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "M5" in _ids(refusals), refusals


def _isolated_group():
    doc = _valid_group()
    for s in doc["sessions"][:2]:
        s["dispatch"]["isolation"] = "worktree"
    return doc


def test_m3_isolation_is_refused_when_the_build_cannot_honour_it(monkeypatch):
    """The half of M3 that outlived S06B: an INERT isolation flag must never be
    accepted. Whatever ``ISOLATION_IMPLEMENTED`` happens to be, a build that
    cannot provide worktrees has to say so rather than hand the plan a shared
    tree it believes is isolated."""
    monkeypatch.setattr(pc, "ISOLATION_IMPLEMENTED", False)
    refusals, _ = pc.check(_isolated_group(), schema_version=V3)
    assert "M3" in _ids(refusals)
    assert any("does not implement" in r for r in refusals), refusals


def test_m3_isolation_is_accepted_now_that_the_mechanism_exists():
    """KNOWN NEGATIVE. S06B shipped orchestrator-managed `git worktree add`
    (worktree.py + run.py `_isolation_prep`), so a well-formed isolated group is
    legal. If this starts failing, isolation has regressed to a refusal and every
    isolated plan is stranded."""
    assert pc.ISOLATION_IMPLEMENTED is True
    refusals, _ = pc.check(_isolated_group(), schema_version=V3)
    assert refusals == [], refusals


def test_m5_overlap_downgrades_to_a_warning_under_isolation():
    """Contract M5: separate worktrees turn an overlap from CORRUPTION into a
    MERGE COST. The fixture's round 2 is that cost being paid, loudly."""
    doc = _isolated_group()
    doc["items"][1]["touches"] = "src/alpha.py"
    refusals, warnings = pc.check(doc, schema_version=V3)
    assert refusals == [], refusals
    assert any("M5" in w and "MERGE COST" in w for w in warnings), warnings


def test_m3_unknown_isolation_value_is_refused_not_ignored():
    doc = _valid_group()
    for s in doc["sessions"][:2]:
        s["dispatch"]["isolation"] = "worktre"          # typo
    refusals, _ = pc.check(doc, schema_version=V3)
    assert any("only legal value" in r for r in refusals), refusals


def test_half_isolated_group_is_refused():
    doc = _valid_group()
    doc["sessions"][0]["dispatch"]["isolation"] = "worktree"
    refusals, _ = pc.check(doc, schema_version=V3)
    assert any("half-isolated" in r for r in refusals), refusals


def test_empty_string_group_is_refused():
    doc = _doc([_s("s01", group="", items=["i1"])], [_it("i1", "a.py")])
    refusals, _ = pc.check(doc, schema_version=V3)
    assert "§1" in _ids(refusals), refusals


def test_missing_integration_session_warns_on_shared_tree():
    doc = _valid_group()
    doc["sessions"] = doc["sessions"][:2]
    refusals, warnings = pc.check(doc, schema_version=V3)
    assert refusals == [], refusals
    assert "§3" in _ids(warnings), warnings


def test_two_integration_sessions_are_refused():
    doc = _valid_group()
    doc["sessions"].append(_s("s04", deps=["s01", "s02"], integrates="g", git="commit",
                              gates=["gate-a", "gate-b"]))
    refusals, _ = pc.check(doc, schema_version=V3)
    assert any("Exactly one" in r for r in refusals), refusals


def test_integration_session_must_depend_on_every_member():
    doc = _valid_group()
    doc["sessions"][2]["dispatch"]["depends_on"] = ["s01"]
    refusals, _ = pc.check(doc, schema_version=V3)
    assert any("does not depend on every member" in r for r in refusals), refusals


def test_integration_session_must_rerun_the_full_gate_set():
    doc = _valid_group()
    doc["sessions"][2]["verify"]["gates"] = ["gate-a"]
    refusals, _ = pc.check(doc, schema_version=V3)
    assert any("does not re-run the full gate set" in r for r in refusals), refusals


# --------------------------------------------------------------- VERSION GATE

def test_v2_plan_keeps_its_grandfathered_shipping_members():
    """Two real v2 plans in this repo have members with git: commit. Gating is
    what stops this contract from refusing to resume them."""
    doc = _valid_group(version=2)
    doc["sessions"][0]["post_session"] = {"git": "commit"}
    refusals, warnings = pc.check(doc, schema_version=2)
    assert refusals == [], refusals
    assert any("below 3" in w for w in warnings), warnings


def test_r1_symmetry_is_enforced_on_every_version():
    """R1 is ungated on purpose — it is enforced for all versions today, and
    version-gating it would REGRESS existing behaviour."""
    for version in (2, 3):
        doc = _valid_group(version=version)
        doc["sessions"][1]["dispatch"]["depends_on"] = ["s00", "sXX"]
        refusals, _ = pc.check(doc, schema_version=version)
        assert "R1" in _ids(refusals), (version, refusals)


def test_missing_version_stamp_is_treated_as_below_threshold():
    doc = _valid_group(version=None)
    doc["sessions"][0]["post_session"] = {"git": "commit"}
    refusals, warnings = pc.check(doc, schema_version=None)
    assert refusals == []
    assert warnings, "a missing stamp must still warn, never go silent"


# ------------------------------------------------------------------------
# END-TO-END: the SAME plants against a REAL built plan, through BOTH gates.
#
# Build time  = the shared checker at spec level, called exactly as
#               plan-builder's validate_spec.py calls it.
# Dispatch time = `run.py begin` as a subprocess — the real command, real
#               argv, real exit code. Not an in-process call that could
#               accidentally bypass the wiring.
#
# `main()` re-runs all of it and prints the fired output as markdown; that is
# how `_evidence/s06/frozen-contract.md`'s transcript section is produced —
# from this code, rather than from a hand-written transcript.
# ------------------------------------------------------------------------

import json                                                          # noqa: E402
import os                                                            # noqa: E402
import re                                                            # noqa: E402
import subprocess                                                    # noqa: E402
import sys                                                           # noqa: E402
import tempfile                                                      # noqa: E402
import textwrap                                                      # noqa: E402
from pathlib import Path                                             # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import build_plan                                                    # noqa: E402

FIXTURE_SPEC = {
    "title": "Parallel Contract Fixture Plan",
    "categories": [{"key": "work", "label": "Work"}],
    "items": [
        {"id": "w-01", "title": "W1", "category": "work", "touches": "src/alpha.py"},
        {"id": "w-02", "title": "W2", "category": "work", "touches": "src/beta.py"},
        {"id": "w-03", "title": "W3", "category": "work", "touches": "docs/readme.md"},
    ],
    "phases": [],
    "sessions": [
        {"id": "s01", "title": "Groundwork", "model": "Sonnet", "items": ["w-03"],
         "prompt": "do"},
        {"id": "s02", "title": "Member A", "model": "Sonnet", "items": ["w-01"],
         "prompt": "do", "dispatch": {"depends_on": ["s01"], "parallel_group": "g1"}},
        {"id": "s03", "title": "Member B", "model": "Sonnet", "items": ["w-02"],
         "prompt": "do", "dispatch": {"depends_on": ["s01"], "parallel_group": "g1"}},
    ],
    "infographic": {
        "type": "before-after", "title": "t",
        "before": {"name": "Before", "bullets": ["a"]},
        "after": {"name": "After", "bullets": ["b"]},
        "workstreams": [{"name": "Track A", "items": ["w-01", "w-02", "w-03"]}],
    },
}


def build_fixture(tmp_path):
    project_root = Path(tmp_path) / "proj"
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text("{}")
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(json.loads(json.dumps(FIXTURE_SPEC)), plan_dir,
                     project_root=str(project_root))
    return plan_dir


def _mutate_manifest(plan_dir, fn):
    path = Path(plan_dir) / "manifest.json"
    m = json.loads(path.read_text())
    fn(m)
    path.write_text(json.dumps(m, indent=2))
    return m


def _carry_touches(m):
    """Restore what the builder now emits itself (S07 / PL-03). Kept so a case
    that starts from `_strip_touches` can put the field back, and so the plants
    below read the same before and after that landed."""
    by_id = {it["id"]: it for it in FIXTURE_SPEC["items"]}
    for it in m["items"]:
        it["touches"] = by_id[it["id"]]["touches"]


def _strip_touches(m):
    """A manifest with no `touches` — a hand edit, or one an older builder wrote."""
    for it in m["items"]:
        it.pop("touches", None)


def _begin(plan_dir, *sessions):
    """Run the REAL dispatch command. Returns (returncode, combined output)."""
    env = dict(os.environ, PLAN_EXECUTE_EGRESS_ROOT=str(Path(plan_dir).parent))
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "run.py"), "begin", str(plan_dir),
         "--sessions", *sessions],
        capture_output=True, text=True, env=env,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def test_builder_carries_touches_and_the_gate_still_fails_closed_without_it():
    """Both halves of M2a, in the order S06 → S07 landed them.

    S06 froze the rule and shipped the dispatch gate against a manifest the
    builder did not yet write `touches` into — so as BUILT, every group was
    refused. S07 landed the builder half, so a clean group now dispatches. The
    fail-closed half is unchanged and still proven here: strip the field back
    out of the manifest (a hand edit, an older builder) and the same plan is
    refused, because M2 and M5 would otherwise be silently inert."""
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        built = json.loads((Path(plan_dir) / "manifest.json").read_text())
        assert all(it.get("touches") for it in built["items"]), built["items"]
        rc, out = _begin(plan_dir, "s02", "s03")
        assert rc == 0, out
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        _mutate_manifest(plan_dir, _strip_touches)
        rc, out = _begin(plan_dir, "s02", "s03")
        assert rc != 0, out
        assert "[M2a]" in out, out


def test_dispatch_gate_allows_the_clean_group():
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        _mutate_manifest(plan_dir, _carry_touches)
        rc, out = _begin(plan_dir, "s02", "s03")
        assert rc == 0, out
        assert "contract violation" not in out, out


def test_dispatch_gate_refuses_a_member_declaring_commit_push():
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)

        def plant(m):
            _carry_touches(m)
            for s in m["sessions"]:
                if s["id"] == "s02":
                    s.setdefault("post_session", {})["git"] = "commit-push"

        _mutate_manifest(plan_dir, plant)
        rc, out = _begin(plan_dir, "s02", "s03")
        assert rc != 0, out
        assert "[M1]" in out and "s02" in out, out


def test_dispatch_gate_refuses_a_member_touching_a_lockfile():
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)

        def plant(m):
            _carry_touches(m)
            for it in m["items"]:
                if it["id"] == "w-01":
                    it["touches"] = "src/alpha.py, uv.lock"

        _mutate_manifest(plan_dir, plant)
        rc, out = _begin(plan_dir, "s02", "s03")
        assert rc != 0, out
        assert "[M2]" in out and "uv.lock" in out, out


# ------------------------------------------------------------------------ main

def _spec_with(**plant):
    spec = json.loads(json.dumps(FIXTURE_SPEC))
    if "git" in plant:
        for s in spec["sessions"]:
            if s["id"] == "s02":
                s["post_session"] = {"git": plant["git"]}
    if "touches" in plant:
        for it in spec["items"]:
            if it["id"] == "w-01":
                it["touches"] = plant["touches"]
    return spec


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    L = ["# S06 / PL-02 — parallel-group contract gate: plants and controls", "",
         "Generated by `test_parallel_contract.py main()`. Every block below is "
         "verbatim output of the code named above it.", ""]

    L += ["## 1 — BUILD TIME (spec level)", "",
          "The shared checker called exactly as plan-builder's `validate_spec.py` "
          "calls it: `parallel_contract.check(spec, schema_version="
          f"build_plan.PLAN_SCHEMA_VERSION)` (= {build_plan.PLAN_SCHEMA_VERSION}).", ""]
    for label, spec in (
        ("ALLOW control — the clean fixture spec", _spec_with()),
        ("PLANT M1 — member s02 declares post_session.git: commit-push",
         _spec_with(git="commit-push")),
        ("PLANT M2 — member s02's item declares a lockfile touch",
         _spec_with(touches="src/alpha.py, uv.lock")),
    ):
        refusals, warnings = pc.check(spec, schema_version=build_plan.PLAN_SCHEMA_VERSION)
        L += [f"### {label} — {'REFUSED' if refusals else 'clean'}", "", "```"]
        L += [textwrap.fill(r, 96) for r in refusals] or ["(no refusals)"]
        L += [f"warning: {textwrap.fill(w, 88)}" for w in warnings]
        L += ["```", ""]

    L += ["## 2 — DISPATCH TIME (`run.py begin`, real subprocess)", ""]
    with tempfile.TemporaryDirectory() as td:
        plan_dir = build_fixture(td)
        cases = [
            ("`touches` stripped from the manifest — M2a fail-closed", _strip_touches),
            # `_reset` strips first, so this restores exactly what the builder
            # itself now emits (S07) — the as-built manifest.
            ("ALLOW control — as BUILT (S07: the builder emits `touches`)", _carry_touches),
        ]

        def plant_m1(m):
            _carry_touches(m)
            for s in m["sessions"]:
                if s["id"] == "s02":
                    s.setdefault("post_session", {})["git"] = "commit-push"

        def plant_m2(m):
            _carry_touches(m)
            for it in m["items"]:
                if it["id"] == "w-01":
                    it["touches"] = "src/alpha.py, uv.lock"

        cases += [("PLANT M1 — member s02 declares commit-push", plant_m1),
                  ("PLANT M2 — member s02's item touches uv.lock", plant_m2)]
        for label, fn in cases:
            _mutate_manifest(plan_dir, lambda m, fn=fn: (_reset(m), fn(m)))
            rc, text = _begin(plan_dir, "s02", "s03")
            L += [f"### {label} — exit {rc}", "",
                  "```", "$ run.py begin <plan-dir> --sessions s02 s03"]
            if rc == 0:
                # A clean dispatch prints the whole batch payload (prompt text
                # and all). Show only what the control is proving.
                L += [ln for ln in text.splitlines()[:3]] + ["  … (batch payload elided)"]
                L += [f'grep -c "contract violation" -> {text.count("contract violation")}',
                      f'batch member ids -> {sorted(re.findall(chr(34) + "id" + chr(34) + r": .(s0\d)", text))}']
            else:
                L += [textwrap.fill(ln, 96) for ln in text.splitlines()[:20]]
            L += ["```", ""]

    L += ["## 3 — Unit suite", "", "```",
          subprocess.run([sys.executable, "-m", "pytest", str(Path(__file__)), "-q"],
                         capture_output=True, text=True).stdout.strip().splitlines()[-1],
          "```", ""]
    text = "\n".join(L) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")
    else:
        print(text)


def _reset(m):
    """Undo prior plants so each case starts from the as-built manifest."""
    for it in m["items"]:
        it.pop("touches", None)
    for s in m["sessions"]:
        if s["id"] in ("s02", "s03"):
            (s.get("post_session") or {}).pop("git", None)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "-q":
        raise SystemExit(pytest.main([__file__, "-q"]))
    main()
