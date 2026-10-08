"""PS-01 — structural DONE-gate.

The #1 recurring correction across three weeks of sessions (30+ messages,
escalating to profanity) was "you didn't update the plan HTML." Two prior
attempts to fix it with instruction/prompt guidance alone regressed. This
suite exercises the structural (code, not prose) version of the fix:

  * `structural_gate.check_landed` / `check_js_parses` / `run_gate` as units.
  * `run.cmd_apply` refuses to let a session stand as its claimed status when
    the dashboard write it just made did not actually land (simulating the
    exact "apply_mutation ran, but the write never reached the file" bug
    class via monkeypatch) — session flips to BLOCKED + halt instead.
  * The happy path still finalizes normally and carries `structural_gate` /
    `render_verify` in its output.
  * `verify.verify_finalize` applies the same gate to the session-level write
    it makes after a verify-pending session's gates all pass.
  * Every completion-shaped `_out(...)` call carries an absolute `plan_url`
    (`file://...`) so a session close never leaves the path to be hunted for.

Run: pytest plan-execute/scripts/test_structural_gate.py -q
"""

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import manifest_io as mio  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import structural_gate as sg  # noqa: E402
import verify as vfy  # noqa: E402


# --------------------------------------------------------------------------
# Fixture builder (mirrors test_verify.py / test_shipping.py)
# --------------------------------------------------------------------------
def _spec(sessions, phases=None):
    items = sorted({iid for s in sessions for iid in s.get("items", [])})
    return {
        "title": "Structural Gate Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": iid, "title": iid.upper(), "category": "work"} for iid in items],
        "phases": phases or [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": items[:1]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def make_plan(tmp_path, sessions, *, phases=None, gates=None):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text(json.dumps(gates or {}))
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec(sessions, phases), plan_dir, project_root=str(project_root))
    return plan_dir


def write_closeout_file(tmp_path, plan_dir, session_id, *, result="DONE",
                         completed=None, checkpoint=None):
    manifest = mio.load_manifest(plan_dir)
    items = mio.session_by_id(manifest)[session_id]["items"]
    completed = items if completed is None else completed
    body = {
        "session": session_id, "result": result, "items_completed": completed,
        "items_blocked": [i for i in items if i not in completed], "notes": {},
        "dispatch_next": True, "human_checkpoint_reason": checkpoint,
    }
    text = "<plan-execute-closeout>\n" + json.dumps(body) + "\n</plan-execute-closeout>"
    out_file = tmp_path / f"{session_id}-closeout.txt"
    out_file.write_text(text)
    return out_file


def write_closeout(plan_dir, session_id, *, result="DONE", completed=None, checkpoint=None):
    manifest = mio.load_manifest(plan_dir)
    items = mio.session_by_id(manifest)[session_id]["items"]
    completed = items if completed is None else completed
    parsed = {"session": session_id, "result": result, "items_completed": completed,
              "items_blocked": [i for i in items if i not in completed], "notes": {},
              "human_checkpoint_reason": checkpoint}
    cp.persist(plan_dir, session_id, parsed)


def _status(plan_dir, sid):
    return ab.read_all_statuses((plan_dir / "PLAN.html").read_text()).get(sid)


def _begin_doing(plan_dir, sid):
    ab.apply_mutation(plan_dir / "PLAN.html", sid, status="DOING", note=None)


SESS = {"id": "s01", "title": "Session 1", "items": ["it-1"], "model": "Sonnet", "prompt": "do it"}


# --------------------------------------------------------------------------
# Unit-level: structural_gate module
# --------------------------------------------------------------------------
def test_check_landed_passes_when_status_matches(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note="x")
    ok, mismatches, warnings = sg.check_landed(plan_dir, {"s01": "DONE"})
    assert ok and not mismatches


def test_check_landed_flags_mismatch(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    ok, mismatches, _ = sg.check_landed(plan_dir, {"s01": "DONE"})  # still DOING, never applied
    assert not ok
    assert "s01" in mismatches[0]
    assert "DOING" in mismatches[0]


def test_check_js_parses_the_real_dashboard_script(tmp_path, monkeypatch):
    # THE ONE TEST THAT MUST NOT TAKE THE SPEED-UP. conftest's autouse
    # `_skip_browser_checks` sets PLAN_EXECUTE_SKIP_BROWSER_CHECKS=1 so the ~5 s of
    # `npx eslint` does not run once per test — correct everywhere except HERE,
    # where parsing the real dashboard script IS the assertion. Left on, this test
    # asserts only that a skip reports itself as a skip, and a broken repaint
    # script ships green. Unsetting it for this test keeps the suite fast and the
    # gate real; do not delete either half.
    monkeypatch.delenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", raising=False)
    plan_dir = make_plan(tmp_path, [SESS])
    result = sg.check_js_parses(plan_dir)
    # Never "unavailable-as-a-silent-pass": either it actually ran (passed/failed)
    # or it explicitly skipped with a reason (no node/eslint on this host).
    assert result["status"] in ("passed", "skipped")
    if result["status"] == "skipped":
        # ...and the ONE reason that is no longer acceptable here is the opt-out.
        assert result.get("reason")
        assert "PLAN_EXECUTE_SKIP_BROWSER_CHECKS" not in result["reason"], (
            "the real dashboard-JS parse was skipped by the speed-up flag — this "
            "test exists to run it for real"
        )
        # ...nor by eslint declining to look. This assertion is why the gate was
        # dead for six days while this very test stayed green: "outside of base
        # path" is an honest skip, so it slipped through the clause above.
        assert "outside of base path" not in result["reason"], (
            "eslint ignored the scratch file — the check linted NOTHING. Its base "
            "path is the CWD, so check_js_parses must run eslint with "
            "cwd=_ESLINT_CONFIG.parent (measured 2026-08-25)"
        )


@pytest.mark.parametrize("rc,stdout", [(1, ""), (0, ""), (1, "["), (0, "[]")])
def test_an_eslint_run_that_LINTED_NOTHING_is_not_a_pass(rc, stdout):
    """The second way this gate went blind. `npx --no-install --offline eslint`
    exits 1 with EMPTY stdout when eslint is not in the npx cache, and rc=1 is also
    eslint's "found problems" code — so an empty report read as a clean one. On
    GitHub's runner that made the PLANTED-identifier probe below report `passed`
    (measured 2026-09-07). Only the report tells "clean" from "never ran"."""
    proc = SimpleNamespace(returncode=rc, stdout=stdout, stderr="npm error code ENOTCACHED")
    assert sg._eslint_verdict(proc)["status"] == "skipped"


def test_a_PLANTED_undefined_identifier_is_CAUGHT(tmp_path, monkeypatch):
    """KNOWN POSITIVE — the only test that proves this gate can fail at all.

    Every "does it pass?" assertion above was green for six days while eslint
    was ignoring the file entirely (base path = CWD, not the config's dir). A
    plant is the only probe that tells "clean" apart from "never looked"."""
    monkeypatch.delenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", raising=False)
    plan_dir = make_plan(tmp_path, [SESS])
    # RUN FROM A DIRECTORY THAT IS NOT AN ANCESTOR OF THE CONFIG. Without this
    # the probe is broken: in the SOURCE tree the eslint config lives under the
    # repo root, so a subprocess inheriting the repo-root cwd lints the file
    # happily and the base-path bug cannot reproduce — measured 2026-08-25, the
    # neuter passed 10/10 until this line was added. In the DEPLOYED tree
    # (~/.claude) the config is outside the plan's repo, which is where the gate
    # actually ran dead for six days.
    monkeypatch.chdir(tmp_path)
    html = plan_dir / "PLAN.html"
    text = html.read_text()
    marker = "<script>"
    assert marker in text
    # PLANT: reference an identifier nothing declares — the 2026-06-06
    # `isOpus is not defined` class that froze the dashboard nav.
    text = text.replace(marker, marker + "\nvoid mostDefinitelyNotDeclaredAnywhere;\n", 1)
    html.write_text(text)

    result = sg.check_js_parses(plan_dir)
    if result["status"] == "skipped":
        # A skip is acceptable ONLY for genuinely absent tooling. "outside of base
        # path" is the DEAD-GATE symptom this probe exists to catch — skipping on
        # it rebuilds the very loophole being fixed (measured 2026-08-25: the first
        # version of this probe did exactly that and reported "2 skipped").
        assert "outside of base path" not in result.get("reason", ""), result
        pytest.skip(f"eslint unavailable on this host: {result.get('reason')}")
    assert result["status"] == "failed", result
    assert "mostDefinitelyNotDeclaredAnywhere" in json.dumps(result), result


def test_a_SCRIPT_TAG_INSIDE_AN_HTML_COMMENT_is_not_extracted(tmp_path, monkeypatch):
    """base-template.html explains the repaint/audit split in prose containing the
    literal text `<script>`. The extraction regex matched that COMMENT as a real
    tag — no `data-layout-audit` before its `>`, so the exclusion never fired —
    and fed eslint English, failing 3 of the last 4 real plans with
    `Parsing error: Unexpected token nav` (measured 2026-08-25)."""
    monkeypatch.delenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", raising=False)
    plan_dir = make_plan(tmp_path, [SESS])
    monkeypatch.chdir(tmp_path)
    html = plan_dir / "PLAN.html"
    text = html.read_text()
    planted = (
        "<!--\n"
        "     <script> (the nav repaint above); this block carries prose, not code,\n"
        "     and must never reach the linter.\n"
        "-->\n"
    )
    html.write_text(text.replace("<script>", planted + "<script>", 1))

    result = sg.check_js_parses(plan_dir)
    if result["status"] == "skipped":
        # A skip is acceptable ONLY for genuinely absent tooling. "outside of base
        # path" is the DEAD-GATE symptom this probe exists to catch — skipping on
        # it rebuilds the very loophole being fixed (measured 2026-08-25: the first
        # version of this probe did exactly that and reported "2 skipped").
        assert "outside of base path" not in result.get("reason", ""), result
        pytest.skip(f"eslint unavailable on this host: {result.get('reason')}")
    assert result["status"] == "passed", result


def test_run_gate_combines_both_checks(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note="x")
    out = sg.run_gate(plan_dir, {"s01": "DONE"})
    assert out["status"] == "passed"
    assert out["mismatches"] == []


# --------------------------------------------------------------------------
# Integration: cmd_apply refuses when a dashboard write silently fails
# --------------------------------------------------------------------------
def test_apply_happy_path_carries_structural_and_render_verify(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    out_file = write_closeout_file(tmp_path, plan_dir, "s01")
    run.cmd_apply(str(plan_dir), "s01", str(out_file))
    printed = json.loads(capsys.readouterr().out)
    assert printed["applied"] is True
    assert printed["result"] == "DONE"
    assert printed["structural_gate"]["status"] == "passed"
    assert "render_verify" in printed
    assert printed["render_verify"]["status"] in ("confirmed", "not_applicable", "unavailable")
    assert printed["plan_url"].startswith("file://")
    assert printed["plan_url"].endswith("PLAN.html")
    assert _status(plan_dir, "s01") == "DONE"


def test_apply_refuses_done_when_dashboard_write_silently_fails(tmp_path, monkeypatch, capsys):
    """Simulates the exact regression class: `apply_mutation` is CALLED (no
    exception, apparent success) but the write never reaches PLAN.html — e.g.
    a race, a stale path, a markup drift the mutation regex didn't expect.
    The structural gate must catch this from the re-read, not trust the call
    succeeded, and downgrade the session to BLOCKED + halt with the mismatch
    named explicitly."""
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    out_file = write_closeout_file(tmp_path, plan_dir, "s01")

    real_apply = ab.apply_mutation
    swallowed = {"hit": False}

    def fake_apply(html_path, aid, *, status, note=None, updated=None):
        if aid == "s01" and status == "DONE" and not swallowed["hit"]:
            swallowed["hit"] = True
            return status  # "succeeds" but never touches disk — the regression class
        return real_apply(html_path, aid, status=status, note=note, updated=updated)

    monkeypatch.setattr(run.ab, "apply_mutation", fake_apply)

    with pytest.raises(SystemExit):
        run.cmd_apply(str(plan_dir), "s01", str(out_file))

    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "BLOCKED"
    assert printed["structural_gate"]["status"] == "failed"
    assert any("s01" in m for m in printed["structural_gate"]["mismatches"])
    assert printed["halted"] is True
    assert printed["plan_url"].startswith("file://")
    # The downgrade mutation (a genuinely-real ab.apply_mutation call, since the
    # swallow only intercepts the FIRST DONE call) landed for real:
    assert _status(plan_dir, "s01") == "BLOCKED"
    assert rsi.is_halted(plan_dir)

    # This transcript is the evidence artifact required by S07's verify block.
    transcript_path = Path(
        "~/.claude/_plans/"
        "claude-code-reliability-selfassessment-2026-07-03/_evidence/s07/"
        "refused-done-transcript.txt"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "PS-01 structural DONE-gate refusal transcript "
        "(test_structural_gate.py::test_apply_refuses_done_when_dashboard_write_silently_fails)\n\n"
        "Scenario: ab.apply_mutation('s01', status='DONE') is called and returns normally, "
        "but the write is monkeypatched to a no-op the first time it is invoked for this "
        "target — reproducing the exact bug class where a dashboard mutation call "
        "'succeeds' without the file actually changing.\n\n"
        "run.cmd_apply(plan_dir, 's01', closeout_file) output:\n"
        + json.dumps(printed, indent=2)
        + "\n\nOutcome: DONE was REFUSED. Session status re-read from PLAN.html after the "
        "attempted mutation: " + str(_status(plan_dir, "s01")) + " (BLOCKED). "
        "Halt set: " + str(rsi.is_halted(plan_dir)) + ".\n"
    )


# --------------------------------------------------------------------------
# verify.py — same gate applied to the deferred session-level write
# --------------------------------------------------------------------------
GATE_PASS = {"smoke": {"kind": "argv", "argv": ["true"]}}


def _verify_sess(gates, on_fail="rework", max_rework=1, **extra):
    return {**SESS, "verify": {"gates": gates, "on_fail": on_fail, "max_rework": max_rework}, **extra}


def test_verify_finalize_carries_structural_and_render_verify(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")
    fin = vfy.verify_finalize(plan_dir, "s01")
    assert fin["final_status"] == "DONE"
    assert fin["structural_gate"]["status"] == "passed"
    assert "render_verify" in fin
    assert _status(plan_dir, "s01") == "DONE"


def test_verify_finalize_reworks_when_session_write_silently_fails(tmp_path, monkeypatch):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"], max_rework=1)], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")

    import verify as vfy_mod
    real_apply = ab.apply_mutation

    def fake_apply(html_path, aid, *, status, note=None, updated=None):
        if aid == "s01" and status == "DONE":
            return status  # swallow the finalize write
        return real_apply(html_path, aid, status=status, note=note, updated=updated)

    monkeypatch.setattr(vfy_mod.ab, "apply_mutation", fake_apply)
    out = vfy.verify_finalize(plan_dir, "s01")
    assert out["action"] in ("rework", "halted")
    assert out["gate"] == "structural"


def test_run_gate_surfaces_a_skipped_js_check_as_a_warning(tmp_path, monkeypatch):
    """A skipped lint must not block, but it must be visible outside `js_check`."""
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note="x")
    monkeypatch.setattr(
        sg, "check_js_parses", lambda _p: {"status": "skipped", "reason": "eslint timed out"}
    )
    result = sg.run_gate(plan_dir, {"s01": "DONE"})
    assert result["status"] == "passed"
    assert any("NOT linted" in w and "eslint timed out" in w for w in result["warnings"])
    # and a passing lint adds no such line
    monkeypatch.setattr(sg, "check_js_parses", lambda _p: {"status": "passed"})
    assert not any("NOT linted" in w for w in sg.run_gate(plan_dir, {"s01": "DONE"})["warnings"])


def test_eslint_is_spawned_offline(monkeypatch, tmp_path):
    """The npx argv must carry --offline: without it each spawn asks the registry
    (16-40 s measured) and can outrun the 60 s timeout into a silent skip."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        raise FileNotFoundError("stop here")

    monkeypatch.delenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", raising=False)
    monkeypatch.setattr(sg, "_js_check_preconditions", lambda _p: (["1;"], None))
    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    assert sg.check_js_parses(tmp_path)["status"] == "skipped"
    assert "--offline" in seen["argv"]
