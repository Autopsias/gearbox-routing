"""Tests for exposure.py text handling: text that is not valid UTF-8 and the
detail-file limits. Each one ends in pending, never an older pass."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import exposure  # noqa: E402
import health  # noqa: E402
from exposure_testlib import _card_sast, cli, make_repo  # noqa: E402

SURROGATE = '{"exposed": %s, "reason": "bad \\ud800 text", "since": "2026-10-01"}'


# s07 inventory: an unreadable detail file records pending, never leaves an older pass
def test_unreadable_detail_file_records_pending(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    health.collect(repo)
    env = {**os.environ, "HOME": str(exposure_home)}
    df = tmp_path / "detail.txt"
    df.write_text("route: sentry | 0 confirmed")
    assert cli("record", repo, "pass", "--detail-file", df, env=env).returncode == 0
    for data in (b"\xff\xfe not utf-8", b"x" * 20001):
        df.write_bytes(data)
        cli("record", repo, "pass", "--detail-file", df, env=env)
        card = _card_sast(repo)
        assert card["status"] == "pending" and "detail file unreadable" in card["detail"]

# ------------------------------------------------------------ s07 rework round 2

# S7R2-M1: a reason that is not valid UTF-8 is an exposure error, recorded as pending
@pytest.mark.parametrize("flag", ["true", "false"])
def test_reason_that_is_not_utf8_is_an_exposure_error(tmp_path, exposure_home, flag):
    repo = make_repo(tmp_path / "r", raw=SURROGATE % flag)
    health.collect(repo)
    exposure.record(repo, "pass", "an older pass")
    env = {**os.environ, "HOME": str(exposure_home)}
    p = cli(repo, env=env)
    assert p.returncode == 2 and '"reason" must be valid UTF-8 text' in p.stdout, p.stderr
    p = cli("record-route", repo, env=env)
    assert p.returncode == 0, p.stderr
    card = _card_sast(repo)
    assert card["status"] == "pending" and card["detail"].startswith("exposure error: ")


def test_record_never_leaves_an_older_pass_for_unwritable_text(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    health.collect(repo)
    exposure.record(repo, "pass", "an older pass")
    exposure.record(repo, "pass", "bad \ud800 text")
    card = _card_sast(repo)
    assert card["status"] == "pending" and "not valid UTF-8" in card["detail"]


# S7R2-L1: the detail limit counts characters, not bytes
def test_detail_limit_counts_multibyte_characters(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    health.collect(repo)
    env = {**os.environ, "HOME": str(exposure_home)}
    df = tmp_path / "detail.txt"
    df.write_text("界" * 20000, encoding="utf-8")
    cli("record", repo, "pass", "--detail-file", df, env=env)
    card = _card_sast(repo)
    assert card["status"] == "pass" and len(card["detail"]) == 20000
    df.write_text("界" * 20001, encoding="utf-8")
    cli("record", repo, "pass", "--detail-file", df, env=env)
    card = _card_sast(repo)
    assert card["status"] == "pending" and "over 20000 characters" in card["detail"]
