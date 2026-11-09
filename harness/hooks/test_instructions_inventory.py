#!/usr/bin/env python3
"""BD-01 — behaviour tests for hooks/instructions-inventory.py.

Every test drives the hook the way Claude Code does: a REAL InstructionsLoaded
payload on stdin, against a temp HOME. The payload fixture below is the shape
read off the installed binary's own schema (2.1.239), so a Claude Code release
that changes it breaks these tests rather than silently emptying the inventory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys

import pytest

HOOKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOOKS)

_spec = importlib.util.spec_from_file_location(
    "instructions_inventory", os.path.join(HOOKS, "instructions-inventory.py"),
)
assert _spec is not None and _spec.loader is not None
inv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inv)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never the real ~/.gearbox-state/compaction — each test gets its own root."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GEARBOX_COMPACT_SOURCE", raising=False)
    monkeypatch.delenv("GEARBOX_COMPACT_POLICY", raising=False)
    return home


def payload(tmp_path, *, name="CLAUDE.md", body="# rules\n", cwd="/Users/x/proj",
            reason="session_start", session="sess-1", memory_type="Project",
            create=True):
    """The event as the binary emits it: file_path + memory_type + load_reason on
    the standard {session_id, transcript_path, cwd} spine. NOTE: no byte count —
    that absence is the reason the hook stats the file itself."""
    path = tmp_path / name
    if create:
        path.write_text(body)
    return {
        "session_id": session,
        "transcript_path": str(tmp_path / "t.jsonl"),
        "cwd": cwd,
        "hook_event_name": "InstructionsLoaded",
        "file_path": str(path),
        "memory_type": memory_type,
        "load_reason": reason,
    }


def feed(event):
    return inv.main([], stream=io.StringIO(json.dumps(event)))


def rows():
    return inv._rows(inv.ledger_path())


# --------------------------------------------------------------------------
# The recorded line
# --------------------------------------------------------------------------
def test_one_load_appends_one_line_with_the_five_fields(tmp_path):
    assert feed(payload(tmp_path, body="x" * 4096)) == 0
    written = rows()
    assert len(written) == 1
    row = written[0]
    assert row["session"] == "sess-1"
    assert row["cwd"] == "/Users/x/proj"
    assert row["file"].endswith("CLAUDE.md")
    assert row["reason"] == "session_start"
    assert row["bytes"] == 4096          # stat()'d here; the event carries no size


def test_bytes_is_null_not_zero_when_the_file_is_gone(tmp_path):
    """None and 0 are different claims — 0 would read as 'this file is empty'."""
    assert feed(payload(tmp_path, name="VANISHED.md", create=False)) == 0
    assert rows()[0]["bytes"] is None


def test_every_documented_load_reason_is_recorded_verbatim(tmp_path):
    for reason in inv.LOAD_REASONS:
        feed(payload(tmp_path, reason=reason))
    assert [r["reason"] for r in rows()] == list(inv.LOAD_REASONS)


def test_the_documented_reason_set_is_the_binarys_own(tmp_path):
    """The closed set the module encodes, asserted rather than described. If a
    Claude Code release adds a sixth, this is where it has to be noticed."""
    assert inv.LOAD_REASONS == ("session_start", "nested_traversal",
                                "path_glob_match", "include", "compact")


def test_loads_accumulate_across_sessions_and_projects(tmp_path):
    feed(payload(tmp_path, cwd="/a", session="s1"))
    feed(payload(tmp_path, cwd="/a", session="s2"))
    feed(payload(tmp_path, name="OTHER.md", cwd="/b", session="s3"))
    assert len(rows()) == 3


# --------------------------------------------------------------------------
# Observer, never a gate
# --------------------------------------------------------------------------
def test_malformed_stdin_exits_zero_and_writes_nothing():
    assert inv.main([], stream=io.StringIO("not json at all")) == 0
    assert rows() == []


def test_a_payload_without_a_file_path_writes_nothing(tmp_path):
    event = payload(tmp_path)
    del event["file_path"]
    assert feed(event) == 0
    assert rows() == []


def test_kill_switch_env_stops_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("GEARBOX_COMPACT_POLICY", "off")
    assert feed(payload(tmp_path)) == 0
    assert rows() == []


def test_kill_switch_file_stops_recording(tmp_path, isolated_home):
    live = isolated_home / ".gearbox-state" / "compaction"
    live.mkdir(parents=True)
    (live / "DISABLED").write_text("")
    assert feed(payload(tmp_path)) == 0
    assert rows() == []


def test_probe_mode_cannot_write_to_the_live_ledger(tmp_path, monkeypatch, isolated_home):
    monkeypatch.setenv("GEARBOX_COMPACT_SOURCE", "probe")
    assert feed(payload(tmp_path)) == 0
    live = isolated_home / ".gearbox-state" / "compaction" / inv.LEDGER_NAME
    probe = isolated_home / ".gearbox-state" / "compaction" / "probe" / inv.LEDGER_NAME
    assert probe.exists() and not live.exists()


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def test_summary_totals_bytes_per_cwd_over_distinct_files(tmp_path):
    """The same file loading twice costs its size once — a summary that summed
    every load would report how often a project was worked on, not its weight."""
    feed(payload(tmp_path, name="A.md", body="a" * 100, cwd="/proj-a", session="s1"))
    feed(payload(tmp_path, name="A.md", body="a" * 100, cwd="/proj-a", session="s2"))
    feed(payload(tmp_path, name="B.md", body="b" * 30, cwd="/proj-a",
                 reason="nested_traversal"))
    feed(payload(tmp_path, name="C.md", body="c" * 7, cwd="/proj-b"))

    summary = inv.summarize(rows())
    assert sum(summary["/proj-a"]["files"].values()) == 130
    assert summary["/proj-a"]["loads"] == 3
    assert summary["/proj-a"]["reasons"] == {"session_start": 2, "nested_traversal": 1}
    assert sum(summary["/proj-b"]["files"].values()) == 7

    out = io.StringIO()
    assert inv.main(["summary"], out=out) == 0
    text = out.getvalue()
    assert "/proj-a" in text and "130 bytes across 2 file(s), 3 load(s)" in text
    assert "/proj-a" in text.split("/proj-b")[0]        # heaviest project first
    assert "FLOOR on base context" in text              # the limit, stated in the report


def test_an_unknown_future_reason_is_reported_as_other_not_blended_in(tmp_path):
    feed(payload(tmp_path, reason="some_new_reason_2027"))
    assert inv.summarize(rows())["/Users/x/proj"]["reasons"] == {"other": 1}


def test_summary_on_an_empty_ledger_says_so(tmp_path):
    out = io.StringIO()
    assert inv.main(["summary"], out=out) == 0
    assert "no instruction loads recorded yet" in out.getvalue()


def test_summary_skips_a_torn_line_rather_than_aborting(tmp_path):
    feed(payload(tmp_path, body="z" * 11))
    with open(inv.ledger_path(), "a", encoding="utf-8") as handle:
        handle.write('{"file": "hal\n')
    feed(payload(tmp_path, name="D.md", body="z" * 22))
    assert sum(inv.summarize(rows())["/Users/x/proj"]["files"].values()) == 33


# --------------------------------------------------------------------------
# Registration — the hook is inert unless settings.json actually calls it
# --------------------------------------------------------------------------
def test_settings_registers_the_hook_on_instructionsloaded():
    root = os.path.dirname(HOOKS)
    with open(os.path.join(root, "settings.json"), encoding="utf-8") as handle:
        settings = json.load(handle)
    entries = settings.get("hooks", {}).get("InstructionsLoaded") or []
    commands = [h.get("command", "") for entry in entries for h in entry.get("hooks", [])]
    assert any("instructions-inventory.py" in c for c in commands), commands
    # Fails OPEN on a half-copied deploy, exactly like the compact-policy hooks:
    # a missing file must exit 0, never exit 2 and refuse the session.
    assert all("[ -f" in c for c in commands if "instructions-inventory.py" in c)
