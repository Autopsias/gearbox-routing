"""Shared pytest fixtures for the plan-execute scripts suite."""

import pytest

import outcomes as outc
import run


@pytest.fixture(autouse=True)
def isolated_outcome_ledger(tmp_path, monkeypatch):
    """EVERY test in this suite writes outcome-ledger records to an ISOLATED
    path, never the real tracked `evals/routing/outcomes.ndjson` — any test
    that exercises `apply`/`verify_finalize`/`retire_session` now writes a
    ledger record as a side effect, and without this the whole suite quietly
    contaminates the seeded source-tree file with throwaway fixture data
    (measured: a full-suite run left 23 fixture records in the real file)."""
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(tmp_path / "outcomes-test.ndjson"))


@pytest.fixture(autouse=True)
def egress_root(tmp_path, monkeypatch):
    """Every test gets a CLEAN working tree for the data_sensitivity_guard scan —
    never the real cwd. Two reasons, both load-bearing: the guard now shells out
    to `gitleaks` over the whole tree (1.8 s on this repo vs 54 ms on an empty
    dir), and a developer whose cwd legitimately holds a `.env` would otherwise
    watch unrelated dispatch tests refuse."""
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setenv(run._EGRESS_ROOT_ENV, str(root))
    return root


@pytest.fixture(autouse=True)
def _skip_browser_checks(monkeypatch):
    """Unit tests do NOT launch a browser or npx.

    MEASURED 2026-08-14 by profiling one test: of 18.2 s wall clock, 17.9 s was
    subprocess — headless Chrome 6.6 s (render_verify) plus three `npx eslint`
    spawns totalling 5.2 s (structural_gate.js_check). The suite calls that path
    once per verify/apply test, which is most of its 16 min 33 s.

    ACCURACY, corrected 2026-08-14 after a reviewer caught the first wording:
    this does NOT leave the whole primary gate alone. structural_gate documents
    TWO primary sub-checks and this flag skips one of them (check_js_parses,
    which is browser-FREE — it shells out to node/eslint, so the flag's name is
    about SUBPROCESS COST, not about browsers). check_landed, the sub-check that
    actually verifies data-status attributes landed on disk, is untouched and
    still runs on every test.

    Skipping check_js_parses is nonetheless safe rather than a weakening, for a
    reason in the code and not in this comment: run_gate computes
    `ok = landed_ok and not js_failed`, so a "skipped" result is NOT a failure
    and was never folded into a pass either — unavailable tooling has always
    taken this exact path (a machine without node behaves identically). The real
    paths keep their own coverage in test_render_verify_optout.py, which unsets
    the flag and asserts they actually fire.
    """
    monkeypatch.setenv("PLAN_EXECUTE_SKIP_BROWSER_CHECKS", "1")
