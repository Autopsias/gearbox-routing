"""CI's copy of the known-positive probe suite — plus probes of the prober.

`probe_checks.py` is the answer to "can this check refuse the defect it exists
to catch". That question decays: a check gets rewritten, its message changes,
a fixture stops planting anything, and the suite goes on reporting OK. So the
deterministic probes run on every push here, and the harness itself is fed known
positives — a check stubbed to crash, a check that ignores the plant, and a
check that refuses with the wrong message must come out ERROR / CANNOT FAIL /
ERROR, never "refused". A harness that cannot report a broken gate is the same
defect as the gate it was written to find.

Every guard below reads the `ci_records` fixture — the records the run ACTUALLY
produced — never probe_cases.CASES. See its docstring for why.
"""
import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import probe_cases  # noqa: E402
import probe_checks  # noqa: E402
from test_health_shape import ALL_CHECK_IDS  # noqa: E402


@pytest.fixture(scope="module")
def ci_records():
    """The records this run ACTUALLY produced — the only thing a guard may read.

    The guards used to compare ALL_CHECK_IDS against probe_cases.CASES, the
    static table. That measures what was WRITTEN, not what ran: `run_all` filtered
    the table (`slow=False`) and dropped every probe for hyg.large-files and
    sec.vendor-pins while all three guards stayed green — neutering health.py's
    5 MB threshold to 5 GB kept CI green too (found). A guard whose
    input is a table can only prove the table.
    """
    return probe_checks.run_all(tools=False)


# The ONE check whose real plant needs an external tool: sec.secrets-history is
# `pending` on every repo and gitleaks does the refusing, so the deterministic
# run can only prove it is emitted. Enumerated here rather than assumed — and
# asserted below to be real, in-scope, and not covering an id that has a plant.
TOOL_ONLY_PLANTS = {"sec.secrets-history"}


def test_every_check_refuses_its_known_positive(ci_records):
    """Every deterministic check, clean vs planted."""
    bad = [(r["id"], r["case"], r["verdict"], r.get("note"))
           for r in ci_records if r["verdict"] != "OK"]
    assert not bad, "checks that did not refuse their known positive: " + repr(bad)


def test_every_emitted_check_id_was_actually_probed(ci_records):
    """No check may be added without a known positive — that is the whole point."""
    assert not ALL_CHECK_IDS - {r["id"] for r in ci_records}


def test_every_check_id_was_fed_a_real_plant_not_only_a_control(ci_records):
    """A control asserts nothing changed; on its own it proves nothing can fail."""
    planted = {r["id"] for r in ci_records if r.get("kind") != "control"}
    assert not ALL_CHECK_IDS - planted - TOOL_ONLY_PLANTS


def test_the_tool_only_plant_exception_is_real_in_scope_and_not_stale(ci_records):
    """An exception list that over-covers is how a gate gets excused quietly."""
    assert TOOL_ONLY_PLANTS <= {p["id"] for p in probe_cases.TOOL_PROBES}
    assert TOOL_ONLY_PLANTS <= ALL_CHECK_IDS
    planted = {r["id"] for r in ci_records if r.get("kind") != "control"}
    assert not TOOL_ONLY_PLANTS & planted, "no longer needs the exception"


# ---------- meta-plants: the harness fed defects of its own ----------

def _stub(status_by_phase, detail_by_phase):
    def collector(root, slug, overrides, setup=None):
        phase = "clean" if slug.endswith("-clean") else "plant"
        return {probe_cases.CASES[0]["id"]:
                {"id": probe_cases.CASES[0]["id"], "status": status_by_phase[phase],
                 "detail": detail_by_phase[phase]}}
    return collector


CLEAN_DETAIL = "all 1 workflows declare a permissions block"


def test_a_check_that_crashes_is_recorded_ERROR_not_refused():
    def boom(*_a, **_kw):
        raise RuntimeError("stubbed crash")
    rec = probe_checks.run_case(probe_cases.CASES[0], 0, collector=boom)
    assert rec["verdict"] == "ERROR"
    assert "stubbed crash" in rec["note"]


def test_a_check_that_ignores_the_plant_is_recorded_CANNOT_FAIL():
    rec = probe_checks.run_case(
        probe_cases.CASES[0], 0,
        collector=_stub({"clean": "pass", "plant": "pass"},
                        {"clean": CLEAN_DETAIL, "plant": CLEAN_DETAIL}))
    assert rec["verdict"] == "CANNOT FAIL"


def test_a_refusal_with_the_wrong_message_is_ERROR_not_refused():
    rec = probe_checks.run_case(
        probe_cases.CASES[0], 0,
        collector=_stub({"clean": "pass", "plant": "fail"},
                        {"clean": CLEAN_DETAIL, "plant": "disk full"}))
    assert rec["verdict"] == "ERROR"
    assert "wrong message" in rec["note"]


def test_a_case_with_an_empty_plant_pattern_is_ERROR_not_a_silent_pass():
    """`re.search("", anything)` matches, so an empty plant pattern cannot fail.

    The CLEAN side has been guarded this way since it was written (`if cpat and
    ...`); the plant side was not, and there an absent pattern means the opposite
    thing — a probe that waves any message through, in the file written to catch
    exactly that. Nothing had ever exercised it because every case in the table
    carries a pattern; that is what a known positive is for.
    """
    case = {"id": "cq.file-size", "case": "c", "kind": "plant",
            "clean_expect": ("pass", None), "plant_expect": ("warn", "")}
    verdict, note = probe_checks.judge(
        case, {"cq.file-size": {"status": "pass", "detail": "no oversized source files"}},
        {"cq.file-size": {"status": "warn", "detail": "over 500 lines: a.py (501)"}})
    assert verdict == "ERROR", (verdict, note)
    assert "empty" in note


def test_the_case_table_refuses_an_empty_plant_pattern_before_it_can_run():
    """The import-time guard, fed a known positive — and both real tables.

    A check nobody has watched fail is the thing this corpus exists to catch, so
    the guard is a function rather than an inline loop.
    """
    assert probe_cases.cases_that_cannot_fail(
        ([{"id": "x", "plant_expect": ("warn", "")}], [{"id": "y", "plant_pat": ""}])
    ) == ["x", "y"]
    assert probe_cases.cases_that_cannot_fail(
        (probe_cases.CASES, probe_cases.TOOL_PROBES)) == []


def test_the_two_dependency_checks_without_a_probe_now_have_one():
    """Both were once UNVERIFIED GAPs — pip-audit and deptry were not installed.

    Structural on purpose: actually running them needs `uvx` and the network, and
    CI's deterministic set must stay hermetic. The live refusal is in the probe
    transcript, which is produced by the `--tools` run.
    """
    by_id = {p["id"]: p for p in probe_cases.TOOL_PROBES}
    assert {"sec.dep-vulns", "hyg.dep-unused"} <= set(by_id)
    # -r builds a throwaway venv whose ensurepip dies in this sandbox; the probe
    # has to reach pip-audit the other way, and a silent revert to -r would make
    # the probe a permanent ERROR rather than a refusal.
    assert "--with-requirements" in by_id["sec.dep-vulns"]["cmd"]


def test_a_check_that_vanishes_is_recorded_ERROR():
    rec = probe_checks.run_case(probe_cases.CASES[0], 0,
                                collector=lambda *a, **k: {})
    assert rec["verdict"] == "ERROR"
    assert "not emitted" in rec["note"]


def test_a_fixture_mutation_that_does_not_land_raises():
    with pytest.raises(ValueError, match="no-op"):
        probe_cases.sub("permissions: read", "concurrency:")


@pytest.mark.skipif(not shutil.which("ruff"), reason="ruff not installed")
def test_a_tool_probe_whose_clean_fixture_carries_the_defect_is_ERROR():
    """The semgrep probe shipped in exactly this state."""
    ruff_slop = next(t for t in probe_cases.TOOL_PROBES if t["id"] == "cq.slop")
    dirty = dict(ruff_slop)
    dirty["clean"] = dirty["plant"]        # a clean side that carries the defect
    rec = probe_checks.run_tool_probe(dirty)
    assert rec["verdict"] == "ERROR"
    assert "CLEAN fixture already trips" in rec["note"]


@pytest.mark.skipif(shutil.which("definitely-not-a-real-tool"), reason="impossible")
def test_a_missing_tool_is_a_GAP_never_an_na():
    """Unverified is not not-applicable: an unrun probe stays a gap."""
    rec = probe_checks.run_tool_probe(
        {**probe_cases.TOOL_PROBES[0], "tool": "definitely-not-a-real-tool"})
    assert rec["verdict"] == "GAP"
    assert "not installed" in rec["note"]


# ---------- "we could not look" is not "the gate is broken" ----------

# An OFFLINE runner used to record the two uvx probes and semgrep as ERROR, so
# `probe_checks.py --tools` exited 1 ("a gate is broken") where the truth was 2
# ("we did not look") — the one distinction this file's exit code exists to keep.
# `.invalid` is reserved by RFC 2606 and never resolves, so these run the same on
# a networked machine and on an air-gapped one.

OFFLINE = {"id": "sec.dep-vulns", "tool": "python3", "net": "pypi.invalid",
           "case": "the runner has no network",
           "cmd": ["python3", "-c", "print('error: could not reach the index')"],
           "clean": {"requirements.txt": "jinja2==3.1.6\n"},
           "plant": {"requirements.txt": "jinja2==2.11.3\n"},
           "clean_pat": r"No known vulnerabilities found",
           "plant_pat": r"jinja2 +2\.11\.3 +(PYSEC|GHSA)"}


def test_a_tool_that_cannot_reach_its_feed_is_a_GAP_not_a_broken_gate():
    rec = probe_checks.run_tool_probe(dict(OFFLINE))
    assert rec["verdict"] == "GAP", rec
    assert "could not reach pypi.invalid" in rec["note"]


def test_the_same_reading_WITH_the_feed_reachable_is_still_an_ERROR(monkeypatch):
    """The known negative for the guard above: it must not swallow real breakage.

    Same fixture, same output, only the reachability answer flipped — if this
    came out GAP too, the guard would be excusing every broken tool probe.
    """
    monkeypatch.setattr(probe_checks, "reachable", lambda *a, **k: True)
    rec = probe_checks.run_tool_probe(dict(OFFLINE))
    assert rec["verdict"] == "ERROR", rec
    assert "did not read clean" in rec["note"]


def test_a_fixture_defect_stays_ERROR_even_with_the_feed_unreachable():
    """No outage explains a clean side that already carries the planted defect."""
    dirty = {**OFFLINE, "cmd": ["python3", "-c", "print('jinja2 2.11.3 PYSEC-1')"]}
    dirty["clean"] = dirty["plant"]
    rec = probe_checks.run_tool_probe(dirty)
    assert rec["verdict"] == "ERROR", rec
    assert "CLEAN fixture already trips" in rec["note"]


def test_every_probe_using_a_network_tool_declares_the_host_it_needs():
    """Derived from the table itself, so a NEW uvx probe cannot arrive without it.

    The set of network-reading tools is read off the probes that already declare
    a host — never a second hand-written list, which is how the two would drift.
    """
    net_tools = {p["tool"] for p in probe_cases.TOOL_PROBES if p.get("net")}
    assert net_tools, "no tool probe declares a feed — the guard is unreachable"
    for p in probe_cases.TOOL_PROBES:
        if p["tool"] in net_tools:
            assert p.get("net"), (f"{p['id']} runs {p['tool']}, which reads from the "
                                  "network, but declares no host — an offline runner "
                                  "will record it as a broken gate")


# ---------- a crash outside run_case is one record, not a lost run ----------

def test_a_probe_that_crashes_outside_run_case_is_one_ERROR_record():
    def boom():
        raise subprocess.CalledProcessError(1, "health.py")
    rec = probe_checks.guarded({"id": "sec.vendor-pins", "case": "gate"}, boom)
    assert (rec["verdict"], rec["id"]) == ("ERROR", "sec.vendor-pins")
    assert "CalledProcessError" in rec["note"]


def test_a_crashing_vendor_probe_does_not_discard_the_other_records(monkeypatch):
    """One `check=True` away from throwing all 57 records on the floor."""
    monkeypatch.setattr(probe_checks, "CASES", probe_cases.CASES[:1])

    def boom():
        raise RuntimeError("stubbed vendor crash")
    monkeypatch.setattr(probe_checks, "probe_vendor_pins", boom)
    recs = probe_checks.run_all(tools=False)
    assert [r["verdict"] for r in recs] == ["OK", "ERROR"]
    assert recs[-1]["id"] == "sec.vendor-pins"
    assert "stubbed vendor crash" in recs[-1]["note"]


# ---------- exit code: green must not mean "we did not look" ----------

@pytest.mark.parametrize("verdicts, code", [
    (["OK", "OK"], 0),
    (["OK", "GAP"], 2),                 # a probe was skipped — we did not look
    (["OK", "CANNOT FAIL"], 1),
    (["OK", "ERROR"], 1),
    (["GAP", "CANNOT FAIL"], 1),        # a broken gate outranks an unrun one
])
def test_a_skipped_tool_probe_cannot_exit_green(monkeypatch, capsys, verdicts, code):
    recs = [{"id": "sec.secrets-history", "case": "c", "clean": "", "planted": "",
             "verdict": v} for v in verdicts]
    monkeypatch.setattr(probe_checks, "run_all", lambda **_kw: recs)
    assert probe_checks.main(["--tools"]) == code
    capsys.readouterr()


def test_a_run_that_never_asked_for_the_tool_probes_cannot_exit_green(monkeypatch, capsys):
    """Six declared probes did not run. Green would say they did."""
    recs = [{"id": "gh.perms", "case": "c", "clean": "", "planted": "", "verdict": "OK"}]
    monkeypatch.setattr(probe_checks, "run_all", lambda **_kw: recs)
    assert probe_checks.main([]) == 2
    assert probe_checks.main(["--tools"]) == 0
    capsys.readouterr()


def test_a_malformed_case_entry_is_one_ERROR_not_a_lost_run(monkeypatch):
    """The identity net cannot need a net.

    `case_rec(c)` is an ARGUMENT to guarded(), evaluated before guarded() can
    catch anything, so a case entry missing a key raised straight past it and
    discarded the run — the exact failure the guarded() wiring was added to remove.
    """
    monkeypatch.setattr(probe_checks, "CASES", [{"id": "cq.slop"}])   # no case/kind/plant
    recs = probe_checks.run_all(tools=False)
    assert [r["verdict"] for r in recs] == ["ERROR", "OK"]
    assert recs[0]["id"] == "cq.slop"


def test_a_crash_past_run_cases_own_try_is_one_ERROR_not_a_lost_run(monkeypatch):
    """judge() and the record formatting sit outside run_case's try."""
    monkeypatch.setattr(probe_checks, "CASES", probe_cases.CASES[:1])

    def boom(*_a):
        raise KeyError("detail")
    monkeypatch.setattr(probe_checks, "judge", boom)
    recs = probe_checks.run_all(tools=False)
    assert [r["verdict"] for r in recs] == ["ERROR", "OK"]
    assert recs[0]["id"] == probe_cases.CASES[0]["id"]
    assert "KeyError" in recs[0]["note"]


# ---------- the transcript's own word must match its own recorded result ----------

@pytest.mark.parametrize("planted, word", [
    ("fail — tracked: .env", "refused"),
    ("warn — over 500 lines: src/app.py (501)", "refused"),
    ("na — no dependency manifest tracked", "did not apply"),
    ("pending — run: pip-audit", "did not run"),
])
def test_the_transcript_word_is_derived_from_the_recorded_result(planted, word):
    """A gate case that resolved `na` must not be written up as "refused".

    An earlier transcript said "refused" for ~14 ids whose plant
    resolves to `na`/`pending` — the check never applied, or never ran. The word
    was chosen beside the result (`kind != "control"` → "refused") instead of
    derived from it, so the two could disagree, and did. That is the exact defect
    class this file exists to catch, in the artifact that certifies its absence.
    """
    rec = {"id": "x", "case": "c", "kind": "plant", "clean": "pass — ok",
           "planted": planted, "verdict": "OK", "note": "", "pattern": "p"}
    line = next(ln for ln in probe_checks.transcript([rec], False).splitlines()
                if ln.startswith("- planted:"))
    assert line == f"- planted: {word} ({planted})"


# ---------- the docs' claims about the probes, checked against the probes ----------

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def test_the_prose_count_of_external_probes_matches_the_probes():
    """SKILL.md once said "four" while TOOL_PROBES held six.

    A written claim about behaviour is the least-tested surface there is: a
    docstring can assert the opposite of what its code returns. Prose drifts silently because
    nothing reads it. This reads it.
    """
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    claims = re.findall(r"\b(\w+) external-tool probes\b", skill)
    assert claims, "SKILL.md no longer states how many external-tool probes exist"
    for c in claims:
        assert WORDS.get(c.lower()) == len(probe_cases.TOOL_PROBES), (
            f"SKILL.md says {c!r} external-tool probes, "
            f"TOOL_PROBES holds {len(probe_cases.TOOL_PROBES)}")


def test_every_tool_the_docs_promise_is_a_tool_that_is_probed():
    """A tool named in the external-probe list, but absent from TOOL_PROBES,
    promises coverage that never runs.

    Scoped to the slash-separated list that names the external-tool probes.
    A first draft matched every tool name anywhere in SKILL.md and flagged
    `bandit` and `pip-audit` — which the check TABLE names as tools the operator
    runs, not tools this corpus probes. A prose check that cannot tell the two
    apart raises noise and gets muted, which is how a real one stops being read.
    """
    real = {p["tool"] for p in probe_cases.TOOL_PROBES}
    docs = probe_checks.__doc__ + (Path(__file__).parents[1] / "SKILL.md").read_text()
    lists = [m for m in re.findall(r"(?:[\w-]+ / )+[\w-]+", docs) if "gitleaks" in m]
    assert lists, "no doc list names the external-tool probes any more"
    for lst in lists:
        named = {t.strip() for t in lst.split("/")}
        assert named <= real, f"docs promise tools that are never probed: {sorted(named - real)}"


def test_both_docs_agree_on_what_exit_2_means():
    """SKILL.md and the module docstring state the same exit-2 contract.

    Found the hard way: `main()` changed to return 2 when `--tools`
    is omitted, SKILL.md's table was updated, and probe_checks.py's OWN module
    docstring — three inches above the changed code — still said 2 meant only
    "its tool is not installed". Fixed in the same session that added the
    SKILL.md claim guards, by the author of those guards. The lesson is that one
    claim stated in two places needs the two compared, not read more carefully.

    Asserts the CONDITIONS both docs must carry, not their wording.
    """
    lines, doc2 = probe_checks.__doc__.splitlines(), []
    for i, ln in enumerate(lines):
        if ln.startswith("  2  "):
            doc2 = [ln]
            doc2 += list(itertools.takewhile(lambda x: x.startswith("     "),
                                             lines[i + 1:]))
            break
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    row2 = re.search(r"^\| `2` \|(.+)$", skill, re.M)
    assert doc2 and row2, "the exit-2 contract is no longer stated in both docs"
    for label, text in (("module docstring", " ".join(doc2)), ("SKILL.md", row2.group(1))):
        assert "--tools" in text, (
            f"{label} describes exit 2 without the omitted-`--tools` case, which "
            f"main() returns 2 for: {text.strip()!r}")
        assert "reach" in text, (
            f"{label} describes exit 2 without the could-not-reach-its-feed case, "
            f"which run_tool_probe records as GAP: {text.strip()!r}")
