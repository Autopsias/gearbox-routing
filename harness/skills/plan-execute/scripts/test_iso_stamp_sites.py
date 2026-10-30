"""The five `datetime.now(UTC) - datetime.fromisoformat(x)` sites, moved onto
safe_json.parse_iso_aware: a naive stamp reads as UTC, and any other unparseable
value (``""``, ``None``, ``1``, a list) takes the site's existing None path
instead of raising TypeError.

One table per site, same six shapes each.

    pytest skills/plan-execute/scripts/test_iso_stamp_sites.py -q
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import evidence_proof as ep  # noqa: E402
import registry_plans as rp  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_locks as sl  # noqa: E402
import shipping as shp  # noqa: E402

PAST = datetime(2026, 1, 1, tzinfo=UTC)
NAIVE_PAST, AWARE_PAST = "2026-01-01T00:00:00", "2026-01-01T00:00:00+00:00"
NAIVE_FUTURE, AWARE_FUTURE = "2999-01-01T00:00:00", "2999-01-01T00:00:00+00:00"
LIST = [AWARE_PAST]                  # non-empty, so it reaches the parse


def _age_ok(got):
    want = (datetime.now(UTC) - PAST).total_seconds()
    return isinstance(got, float) and abs(got - want) < 60


AGE_TABLE = [(NAIVE_PAST, True), (AWARE_PAST, True), ("", False), (None, False),
             (1, False), (LIST, False)]


@pytest.mark.parametrize("stamp, has_age", AGE_TABLE)
def test_run_state_io_lock_age_seconds(stamp, has_age):
    got = rsi.lock_age_seconds({"started_at": stamp})
    assert _age_ok(got) if has_age else got is None


@pytest.mark.parametrize("stamp, has_age", AGE_TABLE)
def test_registry_plans_lock_info_age(tmp_path, stamp, has_age):
    (tmp_path / ".lock").write_text(json.dumps({"pid": None, "started_at": stamp}))
    got = rp.lock_info(tmp_path)["age_s"]
    assert _age_ok(got) if has_age else got is None


@pytest.mark.parametrize("expires_at, want", [
    (NAIVE_FUTURE, "live"), (AWARE_FUTURE, "live"),
    # unparseable -> fallback to started_at age; none here, so "expired"
    ("", "expired"), (None, "expired"), (1, "expired"), (LIST, "expired"),
])
def test_ship_locks_lease_state(expires_at, want):
    assert sl._lease_state({"token": "t", "expires_at": expires_at}) == want


@pytest.mark.parametrize("ts, want", [
    (NAIVE_PAST, None), (AWARE_PAST, None),        # written after dispatch: proof
    ("", None), (None, None),                       # no dispatch, no run.ndjson: no run
    (1, "INDETERMINATE"), (LIST, "INDETERMINATE"),  # unparseable: refuse, never grant
])
def test_evidence_proof_since_dispatch(tmp_path, monkeypatch, ts, want):
    monkeypatch.setattr(ep.rc, "first_dispatch_at", lambda plan_dir, sid: ts)
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("x")
    got = ep._since_dispatch(tmp_path, "s01", artifact, "untracked")
    assert got is None if want is None else want in got


@pytest.mark.parametrize("stamp, has_ms", [
    (NAIVE_PAST, True), (AWARE_PAST, True), ("", False), (None, False),
    (1, False), (LIST, False),
])
def test_shipping_ship_finalize_durations(tmp_path, stamp, has_ms):
    out = shp.ship_finalize(tmp_path, "s01", _state={"session_id": "s01", "started_at": stamp})
    got = out["durations_ms"]
    assert (isinstance(got, int) and got > 0) if has_ms else got is None
