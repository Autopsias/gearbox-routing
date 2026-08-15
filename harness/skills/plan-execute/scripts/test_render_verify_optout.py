"""Known-positive control for the unit suite's browser/eslint opt-out.

conftest.py sets PLAN_EXECUTE_SKIP_BROWSER_CHECKS=1 for every test, because
launching headless Chrome and npx costs ~11.8 s PER TEST (measured). That is a
speed change, and a speed change that quietly disabled a real check would be
exactly the kind of silent-green this repo keeps getting burned by.

So these tests do the opposite of the rest of the suite: they UNSET the flag and
assert the expensive path still runs for real. If someone later makes the skip
unconditional, or defaults it on outside the suite, these fail.
"""
import os

import pytest

import render_verify as rv
import structural_gate as sg


@pytest.fixture
def plan_with_dashboard(tmp_path):
    (tmp_path / "PLAN.html").write_text(
        '<html><body><div id="layout-audit-banner" hidden></div>'
        "<script>const x = 1; void x;</script></body></html>"
    )
    return tmp_path


def test_flag_on_skips_without_launching_anything(plan_with_dashboard, monkeypatch):
    monkeypatch.setenv(rv.SKIP_ENV, "1")
    out = rv.check(plan_with_dashboard)
    assert out["status"] == "unavailable"
    assert rv.SKIP_ENV in out["reason"]
    js = sg.check_js_parses(plan_with_dashboard)
    assert js["status"] == "skipped"
    # The whole point: "skipped" is NOT "passed".
    assert js["status"] != "passed"


def test_flag_off_actually_runs_the_real_check(plan_with_dashboard, monkeypatch):
    """The control. With the flag unset the real implementation must engage —
    reaching a genuine verdict, or an `unavailable`/`skipped` that names the
    MISSING TOOL rather than the opt-out."""
    monkeypatch.delenv(rv.SKIP_ENV, raising=False)
    out = rv.check(plan_with_dashboard)
    assert rv.SKIP_ENV not in (out.get("reason") or ""), \
        "flag is unset, so the skip branch must not be what answered"
    assert out["status"] in {"confirmed", "failed", "not_applicable", "unavailable"}

    js = sg.check_js_parses(plan_with_dashboard)
    assert sg._SKIP_ENV not in (js.get("reason") or ""), \
        "flag is unset, so the eslint skip branch must not be what answered"
    assert js["status"] in {"passed", "failed", "skipped"}


def test_the_modules_do_not_DEFAULT_to_skipping(plan_with_dashboard, monkeypatch):
    """Nothing outside the unit suite may turn these off. conftest sets the flag
    via monkeypatch (torn down per test); a module that skipped by DEFAULT would
    silently disable the checks for every real plan run.

    Probed on a REAL dashboard, not a missing directory: `/nonexistent-plan-dir`
    answers `unavailable` from an earlier guard, so inverting the default would
    have left the old version of this test green — it could not fail."""
    monkeypatch.delenv(rv.SKIP_ENV, raising=False)
    assert os.environ.get(rv.SKIP_ENV) is None
    for out in (rv.check(plan_with_dashboard), sg.check_js_parses(plan_with_dashboard)):
        assert rv.SKIP_ENV not in (out.get("reason") or ""), (
            "with the flag UNSET the opt-out branch still answered — the module "
            f"defaults to skipping: {out}"
        )


def test_the_builders_own_no_undef_guard_is_not_defaulted_off(monkeypatch, capsys):
    """The third holder of the same kill-switch — `build_plan.validate_dashboard_js`
    — needs its own control; the two above cover only render_verify and
    structural_gate. PLANT + ALLOW CONTROL in one: the flag ON must not raise on
    a script full of undefined identifiers, and the flag OFF must."""
    import build_plan

    broken = ("<html><body><script>function f(){ return isOpus + nope; } f();"
              "</script></body></html>")
    monkeypatch.setenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", "1")
    build_plan.validate_dashboard_js(broken)            # skipped: must not raise

    monkeypatch.delenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", raising=False)
    try:
        build_plan.validate_dashboard_js(broken)
    except ValueError as e:
        assert "isOpus" in str(e) or "no-undef" in str(e), e
        return
    # No raise is only acceptable when the toolchain is genuinely absent, and the
    # function says so on stderr. Anything else means the guard is inert.
    warned = capsys.readouterr().err
    assert "skipped" in warned and ("node/npx" in warned or "eslint" in warned), (
        "flag unset and no undefined-identifier error raised, with no missing-tool "
        f"warning to explain it — the no-undef guard is inert. stderr: {warned!r}"
    )
