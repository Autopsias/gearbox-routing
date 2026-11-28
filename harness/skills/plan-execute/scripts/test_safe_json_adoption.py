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
_IMPORT = re.compile(r"^\s*(?:import safe_json\b|from safe_json import\b)", re.M)
_READER = re.compile(r"\b(?:load_dict|load_rows|as_dict|get_(?:str|int|bool|list|dict))\b")  # importing only resolve_inside is not adoption


class ADOPTED:
    @staticmethod
    def search(text):
        return _IMPORT.search(text) and _READER.search(text)

# file name -> why its raw json.load(s) calls stay raw. Shrink, never grow.
ALLOWLIST = {
    "codex_helpers.py": "test helper: parses the output of the code under test",
    "escalation_helpers.py": "test helper: parses the output of the code under test",
    "_land_fixture.py": "test fixture: reads files the same test just wrote",
    "_evidence_gate_scoping.py": "one-off evidence script; a wrong-shape crash is a red result",
    "_evidence_ship_scoping.py": "one-off evidence script; a wrong-shape crash is a red result",
    "finish_ci.py": "parses gh output whose valid shape is a list or object; each caller checks it",
    "llm_review_gate.py": "parses model output whose valid shape is a list or object; each site checks it",
    "review_finding_lines.py": "moved out of llm_review_gate.py: parses model output, and the site checks for a list",
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


# --- the other folders table A of the review-cause-checks s01 sites list names ---------
# Repo-relative path -> why its raw json.load(s) calls stay raw. The hooks stay
# self-contained on purpose (they run on every tool call), so they do not import
# safe_json; their row readers carry their own try/except. Shrink, never grow.
R_STDIN = "parses its own hook payload from stdin inside a try; a bad payload exits quietly"
R_GUARDED = "reads rows inside try/except and degrades; a hook stays self-contained: only turnend-guard.py imports from skills/, and only the resolve_inside path guard"
R_CONFIG = "parses its own config or a file this tool wrote itself"
R_SPEC = "parses the plan spec the author hands in; a wrong shape fails loudly as the author's error"
R_SUB = "parses the output of a command it just ran"
OTHER_FOLDERS = ("hooks", "scripts", "skills/plan-builder/scripts")
OTHER_ALLOWLIST = {
    "hooks/agent-elapsed-nag.py": R_STDIN,
    "hooks/agent-janitor-sessionend.py": R_STDIN,
    "hooks/command-rules.py": R_STDIN,
    "hooks/compact-policy.py": R_STDIN,
    "hooks/compact_activation.py": R_GUARDED,
    "hooks/compact_jev_shadow.py": R_SUB,
    "hooks/compact_policy_neuters.py": R_GUARDED,
    "hooks/compact_policy_testkit.py": R_CONFIG,
    "hooks/compact_reorient.py": R_GUARDED,
    "hooks/compact_store.py": R_GUARDED,
    "hooks/context_tokens.py": R_GUARDED,
    "hooks/dispatch-audit.py": R_STDIN,
    "hooks/git-tree-guard.py": R_STDIN,
    "hooks/governor-hook.py": R_STDIN,
    "hooks/improve-nudge.py": R_STDIN,
    "hooks/instructions-inventory.py": R_GUARDED,
    "hooks/memory-index-line.py": R_STDIN,
    "hooks/merge-ff-main.py": R_STDIN,
    "hooks/model-nudge.py": R_STDIN,
    "hooks/model_nudge_lib.py": R_GUARDED,
    "hooks/probe_shadow_lifecycle.py": R_GUARDED,
    "hooks/routing-cadence-check.py": R_CONFIG,
    "hooks/shadow-readout.py": "a stand-alone command that install-runtime copies alone to ~/.gearbox-state/telemetry, so it cannot import safe_json; it has its own guard (bad JSON and non-object rows are counted)",
    "hooks/shadow-sampling.py": R_CONFIG,
    "hooks/skill-usage-log.py": R_STDIN,
    "hooks/stopfailure-alert.py": R_STDIN,
    "hooks/test-shadow-runner.py": R_STDIN,
    "hooks/turnend-guard.py": R_GUARDED,
    "hooks/verify-state-guard.py": R_STDIN,
    "hooks/vision-line.py": R_STDIN,
    "scripts/govrun_config.py": R_CONFIG,
    "scripts/govrun_locks.py": R_CONFIG,
    "scripts/govrun_probes.py": R_SUB,
    "scripts/govrun_status.py": R_SUB,
    "skills/plan-builder/scripts/build_canary.py": R_CONFIG,
    "skills/plan-builder/scripts/build_plan.py": R_SPEC,
    "skills/plan-builder/scripts/parallel_shaping_fixtures.py": R_CONFIG,
    "skills/plan-builder/scripts/project_registries.py": R_CONFIG,
    "skills/plan-builder/scripts/schema_check_harness.py": R_CONFIG,
    "skills/plan-builder/scripts/validate_spec.py": R_SPEC,
}
ROOT = SCRIPTS.parents[2]


def other_violations():
    """Repo-relative paths in OTHER_FOLDERS that parse JSON without importing safe_json
    (scripts/ is only the govrun* files and review_rounds.py, as the s01 list says)."""
    out = []
    for folder in OTHER_FOLDERS:
        for n in violations(ROOT / folder):
            if folder == "scripts" and not (n.startswith("govrun") or n == "review_rounds.py"):
                continue
            out.append(f"{folder}/{n}")
    return out


@pytest.mark.skipif(not (ROOT / "hooks").is_dir(), reason="repo folders not beside the skill")
def test_every_json_reader_in_the_other_folders_is_adopted_or_waived():
    bad = [n for n in other_violations() if n not in OTHER_ALLOWLIST]
    assert not bad, f"{bad} parse JSON without safe_json: adopt sj.load_rows/sj.load_dict or add to OTHER_ALLOWLIST with a reason"
    stale = sorted(set(OTHER_ALLOWLIST) - set(other_violations()))
    assert not stale, f"{stale} no longer need the waiver; remove them from OTHER_ALLOWLIST"


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


# --- every R_GUARDED hook degrades on a row that is not a dict ---------------
HOOKS_DIR = ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))  # the hooks import each other by bare name


def _hook(filename):
    import importlib.util

    spec = importlib.util.spec_from_file_location(filename.replace("-", "_").removesuffix(".py"), HOOKS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledgers(tmp_path, bad):
    """A one-row ledger plus the compaction ledgers under the throwaway HOME."""
    import compact_store

    ledger = tmp_path / "ledger.ndjson"
    ledger.write_text(bad + "\n")
    root = compact_store.root()
    Path(root).mkdir(parents=True, exist_ok=True)
    for name in ("activations.ndjson", "decisions.ndjson"):
        (Path(root) / name).write_text(bad + "\n")
    return ledger


def _g_compact_activation(tmp_path, monkeypatch, bad):
    _ledgers(tmp_path, bad)
    assert _hook("compact_activation.py").read_activations() == []


def _g_compact_policy_neuters(tmp_path, monkeypatch, bad):
    import types

    _ledgers(tmp_path, bad)
    holder = types.SimpleNamespace(reorient=types.SimpleNamespace())
    _hook("compact_policy_neuters.py").n_pending_cross_session(holder)
    assert holder.reorient.take_pending("s") is None


def _g_compact_reorient(tmp_path, monkeypatch, bad):
    _ledgers(tmp_path, bad)
    assert _hook("compact_reorient.py")._last_compacted_line("s") is None


def _g_compact_store(tmp_path, monkeypatch, bad):
    broken = tmp_path / "broken.json"
    broken.write_text(bad)
    assert _hook("compact_store.py").read_json(str(broken)) is None


def _g_context_tokens(tmp_path, monkeypatch, bad):
    mod = _hook("context_tokens.py")
    assert mod.usage_from_line(bad) is None
    assert mod.context_after_compaction(str(_ledgers(tmp_path, bad)))[0] is None


def _g_instructions_inventory(tmp_path, monkeypatch, bad):
    assert _hook("instructions-inventory.py")._rows(str(_ledgers(tmp_path, bad))) == []


def _g_model_nudge_lib(tmp_path, monkeypatch, bad):
    mod = _hook("model_nudge_lib.py")
    ledger = _ledgers(tmp_path, bad)
    monkeypatch.setattr(mod, "NUDGE_DIR", tmp_path)
    monkeypatch.setattr(mod, "SETTINGS_PATH", ledger)
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "abc.json").write_text(bad)
    assert mod.read_live("abc") == {}
    assert mod.settings_value("model") is None
    assert mod._tail_records(str(ledger), 10_000) == ([], True)
    assert mod.entrypoint_of({"transcript_path": str(ledger)}) is None


def _g_probe_shadow_lifecycle(tmp_path, monkeypatch, bad):
    assert not _hook("probe_shadow_lifecycle.py")._event_counts(_ledgers(tmp_path, bad))


GUARDED_CASES = {
    "compact_activation.py": _g_compact_activation,
    "compact_policy_neuters.py": _g_compact_policy_neuters,
    "compact_reorient.py": _g_compact_reorient,
    "compact_store.py": _g_compact_store,
    "context_tokens.py": _g_context_tokens,
    "instructions-inventory.py": _g_instructions_inventory,
    "model_nudge_lib.py": _g_model_nudge_lib,
    "probe_shadow_lifecycle.py": _g_probe_shadow_lifecycle,
}


def test_every_guarded_hook_has_a_case():
    """The R_GUARDED list and the cases below move together; turnend-guard's cases run a subprocess, in its own file."""
    guarded = {p.removeprefix("hooks/") for p, why in OTHER_ALLOWLIST.items() if why == R_GUARDED}
    assert guarded == set(GUARDED_CASES) | {"turnend-guard.py"}


@pytest.mark.parametrize("bad", ["[]", '"x"', "1"])
@pytest.mark.parametrize("hook", GUARDED_CASES)
def test_guarded_hook_degrades_on_a_row_that_is_not_a_dict(tmp_path, monkeypatch, hook, bad):
    """turnend-guard has its own cases in hooks/test_turnend_guard.py (it reads rows from a subprocess)."""
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.delenv("GEARBOX_COMPACT_SOURCE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    GUARDED_CASES[hook](tmp_path, monkeypatch, bad)
