#!/usr/bin/env python3
"""Version gates and the research-tool probe — the second schema-check suite.

Split out of test_schema_hardening.py, which stood at 1091 lines around a single
973-line function. These checks all turn on a VERSION: a pre-bump spec carrying
a violation must still validate — that is what keeps plan_mutate working on the
live plans in this repo — while the SAME violation at the current version is
refused. Both directions are asserted for every gate.

run_gate_checks() returns only the names IT recorded, so this suite's manifest
stays independent of its sibling's even though both record into the one shared
RESULTS in schema_check_harness.

Run: pytest skills/plan-builder/scripts/test_schema_gates.py -q
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_plan  # noqa: E402
from schema_check_harness import (  # noqa: E402
    BASE, RESULTS, bad, expect_invalid, expect_valid, item, ok,
)

class _patched_probe:
    """Swap build_plan.probe_research_tools for a deterministic stub, so these
    tests assert the LOGIC rather than whatever MCP servers happen to be
    configured on the machine running them."""
    def __init__(self, available, signals=()):
        self.record = {"available": available, "signals": list(signals),
                       "sources": ["<stub>"], "method": "stub"}
        self.calls = 0
    def __enter__(self):
        self._real = build_plan.probe_research_tools
        def _stub(*a, **k):
            self.calls += 1
            return dict(self.record)
        build_plan.probe_research_tools = _stub
        return self
    def __exit__(self, *exc):
        build_plan.probe_research_tools = self._real
        return False

def _write(root, rel, obj):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return p

_minimal_prebump_spec = {
    "title": "Unrelated fixture", "categories": [{"key": "c", "label": "C"}],
    "items": [{"id": "it-1", "title": "IT-1", "category": "c"}],
    "sessions": [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["it-1"],
                  "prompt": "...", "dispatch": {"subagent_type": None, "depends_on": []}}],
    "infographic": {"type": "phase-journey", "title": "T",
                     "phases": [{"num": 1, "name": "P", "tagline": "t", "items": ["it-1"]}],
                     "anchor_now": {"name": "n", "tagline": "t"},
                     "anchor_goal": {"name": "g", "tagline": "t"}},
}


def _check_version_gate():
    # ── THE VERSION GATE (2026-08-12 rework) ────────────────────────────────────
    # Pre-bump control: no top-level plan_schema_version at all -- an item missing
    # prior_art entirely still validates clean. This is the control that was
    # missing when the gate first shipped unconditionally.
    s = copy.deepcopy(BASE)
    del s["plan_schema_version"]
    del item(s, "qw-01")["prior_art"]
    expect_valid(s, "prior_art: pre-bump spec (no plan_schema_version) with a missing prior_art still validates clean")

    # Off-by-one guard: an explicit version BELOW the threshold is equally inert.
    s = copy.deepcopy(BASE)
    s["plan_schema_version"] = 3
    del item(s, "qw-01")["prior_art"]
    expect_valid(s, "prior_art: explicit plan_schema_version=3 (below threshold) also unenforced")

    # Mirror: the SAME missing item at plan_schema_version=4 IS refused -- proves
    # the gate is load-bearing, not just permanently open.
    s = copy.deepcopy(BASE)
    s["plan_schema_version"] = 4
    del item(s, "qw-01")["prior_art"]
    expect_invalid(s, "prior_art: explicit plan_schema_version=4 with a missing prior_art still refused",
                   "must carry either prior_art")

    # The exact regression this rework fixes: build_plan.build() on an UNRELATED,
    # pre-bump, single-item spec with no prior_art notion at all (the shape every
    # other skill's test suite uses build() to materialize) must not raise.
    with tempfile.TemporaryDirectory() as td:
        try:
            build_plan.build(copy.deepcopy(_minimal_prebump_spec), Path(td) / "plan")
            ok("build(): an unrelated pre-bump 1-item spec with no prior_art notion builds clean (the regression)")
        except Exception as e:
            bad("build(): unrelated pre-bump spec regression", f"{type(e).__name__}: {e}")

    # Plant 1: omission refused -- neither prior_art nor research_status present.
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    expect_invalid(s, "prior_art: item with neither prior_art nor research_status refused",
                   "must carry either prior_art")

    # Plant 2: a build decision with no source refused.
    s = copy.deepcopy(BASE)
    item(s, "ft-03")["prior_art"] = {"decision": "build"}
    expect_invalid(s, "prior_art: build decision with no source refused", "source is required")
    s = copy.deepcopy(BASE)
    item(s, "ft-03")["prior_art"] = {"decision": "build", "source": "   "}
    expect_invalid(s, "prior_art: build decision with blank source refused", "source is required")

    # Plant 3: research_status "skipped" + research_reason is the explicit
    # degrade path -- accepted, and distinct from a silent pass (the reason is
    # mandatory too, proving the notice can't itself be silent/empty).
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "skipped"
    it["research_reason"] = "Synthetic: no research MCP tier was reachable (simulated MCP-absent run)."
    expect_valid(s, "prior_art: research_status='skipped' + research_reason accepted (explicit degrade)")
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "skipped"
    expect_invalid(s, "prior_art: research_status='skipped' with no research_reason refused",
                   "research_reason is required")
    s = copy.deepcopy(BASE)
    item(s, "qw-01")["research_status"] = "not-a-real-status"
    expect_invalid(s, "prior_art: research_status other than 'skipped' refused", "research_status")

    # Shape validation on prior_art itself.
    s = copy.deepcopy(BASE)
    item(s, "qw-01")["prior_art"]["decision"] = "buy"
    expect_invalid(s, "prior_art: decision outside adopt|adapt|build refused", "decision")
    s = copy.deepcopy(BASE)
    item(s, "qw-01")["prior_art"]["bogus"] = "x"
    expect_invalid(s, "prior_art: unknown key refused", "unknown key")
    s = copy.deepcopy(BASE)
    item(s, "qw-01")["prior_art"] = "not-an-object"
    expect_invalid(s, "prior_art: non-object refused", "prior_art")

    # Allow control: a fully-decided spec (BASE itself, every item carries one of
    # the two shapes) validates clean -- restated explicitly here as the named
    # control, even though every earlier expect_valid(BASE-derived spec) already
    # exercised it implicitly.
    expect_valid(copy.deepcopy(BASE), "prior_art: allow control — fully-decided spec validates clean")


def _check_prior_art_and_research_skip():
    # ── build: prior_art renders in the agent-spec; research-skipped renders too ──
    s = copy.deepcopy(BASE)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plan"
        try:
            build_plan.build(s, out)
            html = (out / "PLAN.html").read_text()
            assert "<h4>Prior art</h4>" in html, "prior_art block missing from agent-spec"
            assert "Synthetic OSS project B" in html, "prior_art.source not rendered"
            assert "project B lacks the feature" in html, "prior_art.note not rendered"
            assert "Prior art — research skipped" in html, "research_status=skipped block missing"
            assert "no research MCP tier was reachable during authoring" in html, \
                "research_reason not rendered"
            ok("build: prior_art and research-skipped both render in the agent-spec")
        except AssertionError as e:
            bad("build: prior_art rendering", e)
        except Exception as e:
            bad("build: prior_art rendering", f"{type(e).__name__}: {e}")

    # ── Decision hotspots: a build-with-note item is pulled in WITHOUT tweak_likelihood ──
    s = copy.deepcopy(BASE)  # ft-01, or-03, sa-01 all carry prior_art build+note
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plan"
        try:
            build_plan.build(s, out)
            html = (out / "PLAN.html").read_text()
            assert "Decision hotspots" in html, "decision-hotspots section missing"
            assert "built despite" in html, "prior-art hotspot line missing"
            assert "library H is unmaintained" in html, "sa-01's build-despite note not surfaced"
            ok("build: decision hotspots pull in build-despite-alternatives items (no tweak_likelihood needed)")
            # ft-03 / ps-02 are build WITHOUT a note (no credible alternative surfaced) --
            # must NOT be pulled in as a hotspot merely for being a build decision.
            hotspot_section = html[html.index('data-cat="decision-hotspots"'):]
            hotspot_section = hotspot_section[:hotspot_section.index("</section>")]
            assert "no established solution found" not in hotspot_section, \
                "a plain build (no note) leaked into decision hotspots"
            ok("build: a build decision with no note (no alternative found) stays OUT of decision hotspots")
        except AssertionError as e:
            bad("build: decision hotspots prior-art pull-in", e)
        except Exception as e:
            bad("build: decision hotspots prior-art pull-in", f"{type(e).__name__}: {e}")


def _check_contract_first_shaping():
    # ---------------------------------------------------------------------------
    # S07 / PL-03 — contract-first shaping: the pure-chain warning, `serial_reason`,
    # the auto-emitted integration session, and the build-time half of the FROZEN
    # parallel-group contract (S06 / PL-02).
    #
    # The four fixtures live in `parallel_shaping_fixtures.py` so the evidence
    # artifact and these assertions run the SAME cases — an evidence file generated
    # by code nothing asserts is how a transcript starts describing behaviour the
    # build no longer has.
    # ---------------------------------------------------------------------------
    import parallel_shaping_fixtures as _shaping  # noqa: E402

    for _name, _desc, _res, _failures in _shaping.run_all():
        if _failures:
            bad(f"shaping[{_name}]: {_desc}", "; ".join(_failures))
        else:
            ok(f"shaping[{_name}]: {_desc}")

    # The version gate on the contract itself, with the same two directions the
    # prior-art gate needed: a pre-bump spec carrying a violation must still
    # validate (that is what keeps `plan_mutate` working on the 5 live plans in
    # this repo that violate M1/M2a/M5), while the SAME violation at v4 is refused.
    _ships = _shaping.member_ships()
    del _ships["plan_schema_version"]
    expect_valid(_ships, "parallel contract: pre-bump spec with an M1 violation still validates (mutation-safe)")
    _ships["plan_schema_version"] = 4
    expect_invalid(_ships, "parallel contract: the same M1 violation at v4 is refused", "[M1]")

    # `serial_reason` shape: it exists only to silence the pure-chain warning, so an
    # empty one would silence it while answering nothing.
    _sr = _shaping.chained_disjoint()
    _sr["serial_reason"] = "   "
    expect_invalid(_sr, "serial_reason: blank string refused", "serial_reason")
    _sr = _shaping.chained_disjoint()
    _sr["serial_reason"] = ["a"]
    expect_invalid(_sr, "serial_reason: non-string refused", "serial_reason")

    # Emission is IDEMPOTENT — a --rebuild must not stack a second integration
    # session on the group (and an author-written integrator must be left alone).
    _wt = _shaping.worktree_group()
    with _shaping.Isolated():
        _once = build_plan.synthesize_integration_sessions(_wt)
        _twice = build_plan.synthesize_integration_sessions(dict(_wt, sessions=_once))
    if len(_once) == len(_wt["sessions"]) + 1 and len(_twice) == len(_once):
        ok("integration session: emitted once, and a re-run adds no duplicate")
    else:
        bad("integration session: idempotence",
            f"{len(_wt['sessions'])} -> {len(_once)} -> {len(_twice)} sessions")

    # Unparseable `touches` must NOT read as file-disjoint (the conservative rule):
    # prose in one session's touches removes it from the groupable-pair suggestions.
    _prose = _shaping.chained_disjoint()
    _prose["items"][1]["touches"] = "the beta module and anything it imports"
    _rep = build_plan.parallelism_report(_prose)
    if "s02" in _rep["opaque"] and not any("s02" in (a, b) for a, b, _x, _y in _rep["groupable"]):
        ok("touches: unparseable prose is treated as conflicting with everything")
    else:
        bad("touches: unparseable prose", f"opaque={_rep['opaque']} groupable={_rep['groupable']}")

    # ---------------------------------------------------------------------------
    # Research-tool availability probe (RS-06, 2026-08-13)
    # ---------------------------------------------------------------------------
    # The gap this closes: `research_status` was a field the authoring agent
    # hand-wrote. "The research tools were absent, so the pass was skipped" was an
    # assertion the builder took on trust — there was no probe anywhere in the
    # builder, so the skip record was never emitted by the system.
    #
    # What is now enforced, stated honestly: `probe_research_tools()` reads
    # CONFIGURATION ONLY (MCP server names + permission deny-lists). It cannot test
    # reachability, and nothing here claims it does. The structured claim
    # `research_status: "unavailable"` is refused when that probe can see research
    # capability configured, and any skip claim gets the probe's own record stamped
    # into spec.json / manifest.json as `research_env`.
    #
    # NOT version-gated, and it needs no gate: "unavailable" was an ILLEGAL value
    # until this change, so no spec that ever built can carry it. The pre-bump
    # controls below prove the old paths are bit-for-bit unaffected.
    # ---------------------------------------------------------------------------


def _check_research_probe():
    # ── The probe itself, against synthetic config trees ────────────────────────
    # Known POSITIVE first (a check whose all-clear was never proved to be able to
    # turn red is worse than no check).
    with tempfile.TemporaryDirectory() as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        proj.mkdir(parents=True)
        _write(home, ".claude.json", {"mcpServers": {"exa": {"command": "x"}}})
        r = build_plan.probe_research_tools(project_root=proj, home=home)
        if r["available"] and "mcp:exa" in r["signals"]:
            ok("probe: known positive — a configured research MCP server is detected")
        else:
            bad("probe: known positive (mcp)", r)

    with tempfile.TemporaryDirectory() as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        proj.mkdir(parents=True)
        _write(home, ".claude/settings.json", {"permissions": {"allow": []}})
        r = build_plan.probe_research_tools(project_root=proj, home=home)
        if r["available"] and "builtin:WebSearch" in r["signals"]:
            ok("probe: known positive — built-in WebSearch counts when nothing denies it")
        else:
            bad("probe: known positive (builtin)", r)

    # Known NEGATIVE — no config at all. This is the ONLY shape that legitimises an
    # `unavailable` claim, and it fails permissive on purpose (observed nothing).
    with tempfile.TemporaryDirectory() as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        home.mkdir()
        proj.mkdir()
        r = build_plan.probe_research_tools(project_root=proj, home=home)
        if not r["available"] and r["signals"] == []:
            ok("probe: known negative — a bare environment reports no research capability")
        else:
            bad("probe: known negative (bare)", r)

    # Known NEGATIVE — harness present, but the built-ins are denied and the only
    # configured MCP server is not research-capable.
    with tempfile.TemporaryDirectory() as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        proj.mkdir(parents=True)
        _write(home, ".claude.json", {"mcpServers": {"transcriber": {}, "chrome-devtools": {}}})
        _write(home, ".claude/settings.json",
               {"permissions": {"deny": ["WebSearch", "WebFetch"]}})
        r = build_plan.probe_research_tools(project_root=proj, home=home)
        if not r["available"]:
            ok("probe: known negative — built-ins denied + no research-capable MCP server")
        else:
            bad("probe: known negative (denied)", r)

    # The probe reads NAMES and permission entries only — never a value out of an
    # MCP server block. A credential parked in the config must not reach the record.
    with tempfile.TemporaryDirectory() as td:
        home, proj = Path(td) / "home", Path(td) / "proj"
        proj.mkdir(parents=True)
        _write(home, ".claude.json",
               {"mcpServers": {"exa": {"env": {"EXA_API_KEY": "sk-CANARY-do-not-leak"}}}})
        r = build_plan.probe_research_tools(project_root=proj, home=home)
        if "CANARY" not in json.dumps(r):
            ok("probe: never carries a config VALUE (credential canary absent from the record)")
        else:
            bad("probe: credential canary leaked into the record", r)

    # Name matching is token-based, not substring: a server merely containing the
    # letters of a token must not read as research-capable.
    if build_plan._is_research_server("claude_ai_Exa") and not build_plan._is_research_server("preferences"):
        ok("probe: research-server matching is token-based (claude_ai_Exa hits, 'preferences' does not)")
    else:
        bad("probe: research-server matching", "token matching wrong")

    # ── PLANT: the contradicted `unavailable` claim is refused ──────────────────
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "unavailable"
    with _patched_probe(True, ["mcp:exa", "builtin:WebSearch"]) as pp:
        expect_invalid(s, "RS-06 PLANT: research_status='unavailable' refused when the probe sees research capability",
                       "the build-time probe found research capability")
        if pp.calls:
            ok("RS-06 PLANT: the refusal came from the probe (it was actually consulted)")
        else:
            bad("RS-06 PLANT: probe not consulted", "validate_spec refused without probing")

    # ── ALLOW: the same claim stands when the probe agrees ──────────────────────
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "unavailable"
    with _patched_probe(False):
        expect_valid(s, "RS-06 ALLOW: research_status='unavailable' accepted when the probe finds nothing")


def _check_research_status_claims():
    # ── ALLOW: the pre-existing 'skipped' path is untouched, and never probes ───
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "skipped"
    it["research_reason"] = "Synthetic: author chose not to research this one-line item."
    with _patched_probe(True, ["mcp:exa"]) as pp:
        expect_valid(s, "RS-06 ALLOW: research_status='skipped' unaffected by the probe verdict")
        if pp.calls == 0:
            ok("RS-06: validate_spec does not probe at all for a 'skipped' item (zero added I/O)")
        else:
            bad("RS-06: probe called for 'skipped'", f"{pp.calls} calls")

    # ── VERSION CONTROL: an older plan is untouched ─────────────────────────────
    # A pre-bump spec (no plan_schema_version at all) carrying the OLD legal value
    # validates exactly as it did before RS-06 existed, without probing.
    s = copy.deepcopy(BASE)
    del s["plan_schema_version"]
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "skipped"
    it["research_reason"] = "Synthetic pre-bump degrade."
    with _patched_probe(True, ["mcp:exa"]) as pp:
        expect_valid(s, "RS-06 VERSION CONTROL: pre-bump spec with research_status='skipped' validates unchanged")
        if pp.calls == 0:
            ok("RS-06 VERSION CONTROL: pre-bump spec never reaches the probe")
        else:
            bad("RS-06 VERSION CONTROL: pre-bump spec probed", f"{pp.calls} calls")

    # A blank research_reason alongside 'unavailable' is still a hollow record.
    s = copy.deepcopy(BASE)
    it = item(s, "qw-01")
    del it["prior_art"]
    it["research_status"] = "unavailable"
    it["research_reason"] = "   "
    with _patched_probe(False):
        expect_invalid(s, "RS-06: blank research_reason alongside 'unavailable' refused", "must be a non-empty string")

    # ── research_env: emitted for a skip claim, absent otherwise ────────────────
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plan"
        try:
            with _patched_probe(False):
                build_plan.build(copy.deepcopy(BASE), out)
            man = json.loads((out / "manifest.json").read_text())
            spec_out = json.loads((out / "spec.json").read_text())
            env = man.get("research_env")
            assert isinstance(env, dict), "manifest.research_env missing on a spec with skip claims"
            assert env.get("probed_at"), "research_env carries no probed_at"
            assert env == spec_out.get("research_env"), "spec.json and manifest.json disagree"
            # The plan_mutate consistency invariant: manifest.json must be exactly
            # gen_manifest(spec.json). If gen_manifest re-probed instead of reading
            # the spec, this would drift on any other machine.
            regen = build_plan.gen_manifest(spec_out)
            assert regen == man, "gen_manifest(spec.json) != manifest.json (research_env is not pure)"
            ok("RS-06: research_env stamped into spec.json + manifest.json, and gen_manifest stays pure")
        except AssertionError as e:
            bad("RS-06: research_env emission", e)
        except Exception as e:
            bad("RS-06: research_env emission", f"{type(e).__name__}: {e}")

    # The byte-identical guarantee for every plan built before RS-06: a spec with no
    # research_status anywhere gains NO new key.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plan"
        try:
            build_plan.build(copy.deepcopy(_minimal_prebump_spec), out)
            man = json.loads((out / "manifest.json").read_text())
            spec_out = json.loads((out / "spec.json").read_text())
            assert "research_env" not in man, "manifest gained research_env on a spec with no skip claim"
            assert "research_env" not in spec_out, "spec.json gained research_env on a spec with no skip claim"
            ok("RS-06: a spec with no research_status gains no research_env (pre-RS-06 plans unchanged)")
        except AssertionError as e:
            bad("RS-06: no-skip-claim spec untouched", e)
        except Exception as e:
            bad("RS-06: no-skip-claim spec untouched", f"{type(e).__name__}: {e}")

    # The system-verified heading renders only for the machine-adjudicated value.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plan"
        s = copy.deepcopy(BASE)
        it = item(s, "qw-01")
        del it["prior_art"]
        it["research_status"] = "unavailable"
        try:
            with _patched_probe(False):
                build_plan.build(s, out)
            html = (out / "PLAN.html").read_text()
            assert "no research tooling available (verified at build time)" in html, \
                "the verified-unavailable block did not render"
            ok("RS-06: an 'unavailable' item renders the build-time-verified prior-art block")
        except AssertionError as e:
            bad("RS-06: unavailable rendering", e)
        except Exception as e:
            bad("RS-06: unavailable rendering", f"{type(e).__name__}: {e}")


def _check_infographic_coverage():
    # ---------------------------------------------------------------------------
    # INFOGRAPHIC_COVERAGE_MIN_SCHEMA (2026-08-13) — the progress bar counts through
    # the infographic's group list, so an item in no group is missing from the
    # DENOMINATOR: the bar reports progress over a subset while looking like it
    # covers the plan. Refused at build time, where it is still cheap to fix.
    # ---------------------------------------------------------------------------
    def _spec_coverage(stamp, drop_item=None):
        import copy
        spec = copy.deepcopy(BASE)
        spec["plan_schema_version"] = stamp
        if drop_item:
            key = build_plan.INFOGRAPHIC_GROUP_KEYS[spec["infographic"]["type"]]
            for g in spec["infographic"][key]:
                g["items"] = [i for i in (g.get("items") or []) if i != drop_item]
        return spec

    # PLANT — one item pulled out of every group is refused, and the refusal names
    # the item and the real counts rather than saying "invalid infographic".
    expect_invalid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA, drop_item="ft-02"),
                   "coverage: PLANT — an item in no group is refused at the gating version",
                   needle="ft-02")
    expect_invalid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA, drop_item="ft-02"),
                   "coverage: the refusal states the denominator it would have counted",
                   needle="13 of 14")

    # ALLOW — the same spec with every item placed builds at the same version, so
    # the plant fails for the stated reason and not because the version bumped.
    expect_valid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA),
                 "coverage: ALLOW — a fully-placed spec validates at the gating version")

    # CONTROL — the identical defect one version below is untouched. Four real
    # plans on disk carry unplaced items; none carries a stamp, so none is newly
    # refused (measured 2026-08-13 across all 25 spec.json under _plans/).
    expect_valid(_spec_coverage(build_plan.INFOGRAPHIC_COVERAGE_MIN_SCHEMA - 1, drop_item="ft-02"),
                 "coverage: CONTROL — the same defect below the gate still validates")
    expect_valid(_spec_coverage(None, drop_item="ft-02"),
                 "coverage: CONTROL — an unstamped spec (every plan on disk) still validates")

    # The mapping has ONE definition: the mutation engine aliases the builder's, so
    # a new infographic shape cannot be placeable by one and invisible to the other.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"))
        import infographic_place as _pm  # plan_mutate's placement helper (35bf5ca8)
        if _pm._INFOGRAPHIC_GROUPS is build_plan.INFOGRAPHIC_GROUP_KEYS:
            ok("coverage: plan_mutate aliases the builder's group-key map (one definition)")
        else:
            bad("coverage: group-key map is duplicated", "plan_mutate holds its own copy")
    except Exception as e:
        bad("coverage: group-key map is shared", f"{type(e).__name__}: {e}")



# --- pytest surface -------------------------------------------------------
# Every check above is reported as its own test. The checks share progressively
# built state, so they run as one pass inside a session fixture; parametrisation
# reports each verdict separately rather than hiding 109 results behind one dot.


def run_gate_checks():
    """Run every gate check once, and report only what THIS suite recorded.

    RESULTS is shared with the hardening suite; diffing against a snapshot is
    what keeps the two manifests independent.
    """
    before = set(RESULTS)
    _check_version_gate()
    _check_prior_art_and_research_skip()
    _check_contract_first_shaping()
    _check_research_probe()
    _check_research_status_claims()
    _check_infographic_coverage()
    return {k: v for k, v in RESULTS.items() if k not in before}


EXPECTED_CHECKS = tuple(
    ln for ln in (Path(__file__).resolve().parent / "fixtures" / "expected-checks-gates.txt")
    .read_text().splitlines() if ln.strip()
)


@pytest.fixture(scope="session")
def gate_results():
    """Run the whole gate pass once. Kept OUT of import so collection stays cheap
    and an unrelated `-k` run pays nothing for it."""
    return run_gate_checks()


@pytest.mark.parametrize("name", EXPECTED_CHECKS)
def test_check(name, gate_results):
    assert name in gate_results, "check never ran"
    assert gate_results[name] is None, gate_results[name]


def test_no_check_was_added_or_lost(gate_results):
    """Drift guard: the manifest is the contract. Update
    fixtures/expected-checks-gates.txt in the same commit that adds or removes a
    check."""
    got, want = set(gate_results), set(EXPECTED_CHECKS)
    assert got == want, f"missing: {sorted(want - got)}\nunexpected: {sorted(got - want)}"
