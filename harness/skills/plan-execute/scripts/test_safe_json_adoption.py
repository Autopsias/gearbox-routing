"""Every non-test script here that parses JSON goes through safe_json, or is on the
allowlist below with a reason.

A reader that calls ``json.load``/``json.loads`` on a record another process wrote
(ledger, receipt, descriptor, closeout) and never checks the parsed shape crashes,
or bypasses its never-raises guard, on ``[]``, ``"x"``, a list-valued fid or a
missing key (memory: guard-the-parse-not-the-type; review fids 4f91b0ac1d,
738b8623b1, 40aa2e59a9). safe_json landed but most readers never
adopted it, so the gap stayed open. This test makes adoption the default for every
reader added later.

The check is per FILE: a file that imports safe_json passes. The allowlist is keyed
by file name, never by line number (memory: line-anchored-allowlists-decay-silently).

    pytest skills/plan-execute/scripts/test_safe_json_adoption.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import apply_guards as ag  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import gate_durations as gd  # noqa: E402
import manifest_io as mio  # noqa: E402
import outcomes as outc  # noqa: E402
import plan_mutate as pm  # noqa: E402
import replan  # noqa: E402
import review_context as rvc  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_locks as sl  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import shipping as shp  # noqa: E402
import verifier_park as vpk  # noqa: E402

PARSE = re.compile(r"\bjson\.loads?\(")
ADOPTED = re.compile(r"^\s*(?:import safe_json\b|from safe_json import\b)", re.M)

# file name -> why its raw json.load(s) calls stay raw. Shrink, never grow.
ALLOWLIST = {
    "codex_helpers.py": "test helper: parses the output of the code under test",
    "escalation_helpers.py": "test helper: parses the output of the code under test",
    "_land_fixture.py": "test fixture: reads files the same test just wrote",
    "_evidence_gate_scoping.py": "one-off evidence script; a wrong-shape crash is a red result",
    "_evidence_ship_scoping.py": "one-off evidence script; a wrong-shape crash is a red result",
    "finish_ci.py": "parses gh output whose valid shape is a list or object; each caller checks it",
    "llm_review_gate.py": "parses model output whose valid shape is a list or object; each site checks it",
    "codex_review_backend.py": "a non-object codex answer is refused with its own message",
    "codex_review_events.py": "a non-object event is told apart from stderr text in _errorish",
    "plan_version_gate.py": "reports UNREADABLE and NOT-AN-OBJECT as two distinct values",
    "verifier_park.py": "_closeout already reads a non-object closeout as None",
}


def violations(folder):
    """Non-test .py files in ``folder`` that parse JSON without importing safe_json."""
    out = []
    for p in sorted(Path(folder).glob("*.py")):
        if p.name.startswith("test_") or p.name in ("safe_json.py", "conftest.py"):
            continue
        text = p.read_text(encoding="utf-8")
        if PARSE.search(text) and not ADOPTED.search(text):
            out.append(p.name)
    return out


def test_every_json_reader_goes_through_safe_json():
    bad = [n for n in violations(SCRIPTS) if n not in ALLOWLIST]
    assert not bad, (
        f"{bad} parse JSON without safe_json. Read records another process wrote "
        "with sj.load_dict / sj.as_dict and the sj.get_* getters, or add the file to "
        "ALLOWLIST with a one-line reason.")


def test_allowlist_has_no_stale_entries():
    stale = sorted(set(ALLOWLIST) - set(violations(SCRIPTS)))
    assert not stale, f"{stale} no longer need the waiver; remove them from ALLOWLIST"


def test_the_scan_catches_a_planted_violation(tmp_path):
    (tmp_path / "reader.py").write_text("import json\nrec = json.loads(line)\n")
    (tmp_path / "reader_ok.py").write_text(
        "import json\nimport safe_json as sj\nrec = sj.as_dict(json.load(fh))\n")
    (tmp_path / "test_reader.py").write_text("import json\njson.loads('{}')\n")
    (tmp_path / "no_json.py").write_text("x = 1\n")
    assert violations(tmp_path) == ["reader.py"]


# --- the readers moved onto safe_json: every wrong shape reads as absent -------

WRONG = ["[]", '"x"', "1", "null", '["s01"]']
# a str where the list of ids belongs: `"s01" in "s01"` used to match it
STR_IDS = {"event": "dispatch_started", "harness": "codex", "session_ids": "s01", "ts": "bad"}


def _log(tmp_path, *recs):
    lines = WRONG + [json.dumps(STR_IDS)] + [json.dumps(r) for r in recs]
    (tmp_path / "run.ndjson").write_text("\n".join(lines) + "\n")


def test_run_log_readers_skip_wrong_shapes(tmp_path):
    _log(tmp_path, {"event": "dispatch_started", "session_ids": ["s01"], "ts": "T1"},
         {"event": "codex_dispatch", "session_ids": ["s01"], "n": 1})
    assert ag.last_dispatch_harness(tmp_path, "s01") == "claude"
    assert ag.last_codex_dispatch(tmp_path, "s01")["n"] == 1
    assert rvc.first_dispatch_at(tmp_path, "s01") == "T1"
    assert [r["n"] for r in outc._events(tmp_path, "codex_dispatch", "s01")] == [1]


def test_run_log_readers_on_only_wrong_shapes(tmp_path):
    _log(tmp_path)
    assert ag.last_dispatch_harness(tmp_path, "s01") is None
    assert ag.last_codex_dispatch(tmp_path, "s01") is None
    assert rvc.first_dispatch_at(tmp_path, "s01") is None
    assert outc._events(tmp_path, "dispatch_started", "s01") == []


@pytest.mark.parametrize("text", WRONG)
def test_record_readers_read_a_wrong_shape_as_absent(tmp_path, text):
    (tmp_path / "_closeouts").mkdir()
    (tmp_path / "_closeouts" / "s01.json").write_text(text)
    replan.record_path(tmp_path, "s01").parent.mkdir(parents=True, exist_ok=True)
    replan.record_path(tmp_path, "s01").write_text(text)
    (tmp_path / "m.json").write_text(text)
    assert cp.load_closeout(tmp_path, "s01") is None
    cp.mark_replayed(tmp_path, "s01")                       # no raise, file untouched
    assert (tmp_path / "_closeouts" / "s01.json").read_text() == text
    assert vpk._closeout(tmp_path, "s01")[1] is None
    assert replan.load(tmp_path, "s01") is None
    assert pm._read_json(tmp_path / "m.json") is None


@pytest.mark.parametrize("text", WRONG)
def test_manifest_of_wrong_shape_is_a_manifest_error(tmp_path, text):
    (tmp_path / "manifest.json").write_text(text)
    with pytest.raises(mio.ManifestError):
        mio.load_manifest(tmp_path)


@pytest.mark.parametrize("version", ["5", True, [5]])
def test_manifest_version_of_wrong_type_is_a_manifest_error(tmp_path, version):
    (tmp_path / "manifest.json").write_text(json.dumps({"plan_schema_version": version}))
    with pytest.raises(mio.ManifestError):
        mio.load_manifest(tmp_path)


@pytest.mark.parametrize("text", WRONG)
def test_ship_state_of_wrong_shape_falls_back_to_bak(tmp_path, text):
    f = tmp_path / "state.json"
    f.write_text(text)
    with pytest.raises(ssio.ShipStateError):
        ssio.read_json_with_bak(f)
    (tmp_path / "state.json.bak").write_text('{"ok": 1}')
    assert ssio.read_json_with_bak(f) == {"ok": 1}


def test_gate_durations_skip_a_non_string_ts(tmp_path):
    plan = tmp_path / "_plans" / "p"
    plan.mkdir(parents=True)
    recs = [{"event": "gate_run", "gate": "g", "seconds": 2, "ts": 1},
            {"event": "gate_run", "gate": "g", "seconds": 3, "ts": "2026-10-20"}]
    (plan / "run.ndjson").write_text("\n".join(WRONG + [json.dumps(r) for r in recs]))
    assert dict(gd.runs(tmp_path, since="2026")) == {"g": [3.0]}


def test_a_non_utf8_record_reads_as_absent(tmp_path):
    replan.record_path(tmp_path, "s01").parent.mkdir(parents=True)
    replan.record_path(tmp_path, "s01").write_bytes(b"\xff{")
    (tmp_path / "m.json").write_bytes(b"\xff{")
    assert replan.load(tmp_path, "s01") is None
    assert pm._read_json(tmp_path / "m.json") is None
    with pytest.raises(ssio.ShipStateError):
        ssio.read_json_with_bak(tmp_path / "m.json")


@pytest.mark.parametrize("raw", [b"\xff{", b"[]", b'"x"'])
def test_lock_and_run_state_readers_degrade_on_bad_bytes(tmp_path, raw):
    (tmp_path / "run_state.json").write_bytes(raw)
    (tmp_path / ".lock").write_bytes(raw)
    assert rsi.load_state(tmp_path) == rsi._default_state()
    assert rsi.lock_holder(tmp_path) is None
    assert sl._read_lock(tmp_path / ".lock") == {}


def test_run_state_keeps_an_empty_object(tmp_path):
    (tmp_path / "run_state.json").write_text("{}")
    assert rsi.load_state(tmp_path) == {}


@pytest.mark.parametrize("raw", [b'"x"', b'[["a", "b"]]', b"\xff{"])
def test_registry_of_wrong_shape_is_skipped(tmp_path, monkeypatch, raw):
    (tmp_path / "bundled.json").write_text('{"g": {"argv": ["true"]}}')
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "local.json").write_bytes(raw)
    monkeypatch.setattr(shp, "_bundled_default", lambda name: tmp_path / name)
    monkeypatch.setattr(shp, "find_project_root", lambda plan_dir: tmp_path)
    assert shp._load_registry(tmp_path, "local.json", "bundled.json") == {"g": {"argv": ["true"]}}
