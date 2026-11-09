#!/usr/bin/env python3
"""BD-01 — measure which instruction files actually load, per project.

An `InstructionsLoaded` hook. It appends one line per loaded file to
`instructions.ndjson`, so the CLAUDE.md diet starts from a measured inventory
instead of a guess, and can be re-measured against the same file afterwards.

TWO LIMITS, ENCODED HERE RATHER THAN DISCOVERED LATER. Both were read off the
INSTALLED binary (2.1.239) rather than assumed:

  1. IT ONLY EVER SEES INSTRUCTION FILES. The event fires for CLAUDE.md and
     `.claude/rules/*.md` loads and nothing else, with `load_reason` drawn from
     the closed set in :data:`LOAD_REASONS`. The skill listing, agent
     descriptions and MCP tool schemas are the OTHER, larger half of base
     context, and this hook can never see any of them. So the inventory
     explains the CLAUDE.md half of base context and never the whole — read a
     total here as a floor on base context, never as base context.
  2. IT CARRIES NO BYTE COUNT. The payload is `{file_path, memory_type,
     load_reason, globs?, trigger_file_path?, parent_file_path?}` on the usual
     `{session_id, transcript_path, cwd, ...}` spine — there is no size field
     anywhere in it. `bytes` below is stat()'d here, and is null when the file
     has already moved or the stat fails.

OBSERVER, NEVER A GATE, like every other hook in this family: it exits 0 on
every path, prints nothing to stdout, and honours the shared kill switch. A
half-deployed sibling module must not put a traceback on the user's screen at
session start, so even the imports fail open.

COST, MEASURED (10 runs, median): 73 ms per invocation, essentially
all of it Python startup. The event fires once per instruction file at SESSION
START only — never inside the edit loop — so a typical session pays this a
handful of times and nothing on the tool path pays it at all.

Usage:
    instructions-inventory.py              # hook mode — payload on stdin
    instructions-inventory.py summary      # totals per cwd, biggest first
"""

from __future__ import annotations

import json
import os
import sys

try:
    from compact_store import append_line, iso_utc, kill_switch, root

    IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - exercised by a dedicated test
    IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

# The closed set the binary's own schema declares. Kept as data because the
# summary reports per-reason counts against it: a reason NOT in this set is
# printed under `other`, which is how a future Claude Code release adding one
# announces itself here instead of silently blending in.
LOAD_REASONS = ("session_start", "nested_traversal", "path_glob_match",
                "include", "compact")

LEDGER_NAME = "instructions.ndjson"


def ledger_path():
    return os.path.join(root(), LEDGER_NAME)


def _file_bytes(path):
    """The size the event does not carry. None rather than 0 on any failure —
    a 0 would read as "this file is empty", which is a different claim."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    return size if isinstance(size, int) and size >= 0 else None


def record(payload):
    """{session, cwd, file, reason, bytes} for one InstructionsLoaded event, or
    None when the payload carries no file path (nothing to inventory)."""
    if not isinstance(payload, dict):
        return None
    path = payload.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    return {
        "ts": iso_utc(),
        "session": payload.get("session_id") or "unknown",
        "cwd": payload.get("cwd") or "unknown",
        "file": path,
        "reason": payload.get("load_reason") or "unknown",
        "bytes": _file_bytes(path),
    }


def cmd_hook(stream):
    if kill_switch():
        return 0
    try:
        payload = json.load(stream)
    except Exception:  # noqa: BLE001 — a malformed payload is not worth a traceback
        return 0
    row = record(payload)
    if row is not None:
        try:
            append_line(ledger_path(), row)
        except OSError:
            pass  # an unwritable ledger is a lost measurement, never a blocked session
    return 0


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def _rows(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a torn line from a crash — skip it, never abort the report
        if isinstance(row, dict):
            rows.append(row)
    return rows


def summarize(rows):
    """{cwd: {loads, reasons, files: {file: bytes}}}.

    Bytes are summed over DISTINCT FILES, not over loads: the same CLAUDE.md
    loading in forty sessions costs its size once per session, so summing every
    load would report a number that is about how often this project was worked
    on rather than about how heavy its instructions are.
    """
    out = {}
    for row in rows:
        cwd = row.get("cwd") or "unknown"
        entry = out.setdefault(cwd, {"loads": 0, "reasons": {}, "files": {}})
        entry["loads"] += 1
        reason = row.get("reason")
        key = reason if reason in LOAD_REASONS else "other"
        entry["reasons"][key] = entry["reasons"].get(key, 0) + 1
        size = row.get("bytes")
        if isinstance(size, int):
            entry["files"][row.get("file")] = size
        else:
            entry["files"].setdefault(row.get("file"), None)
    return out


def _format_summary(path, summary):
    lines = ["%s" % path]
    if not summary:
        lines.append("  no instruction loads recorded yet")
        return "\n".join(lines) + "\n"
    ordered = sorted(summary.items(),
                     key=lambda kv: -sum(v for v in kv[1]["files"].values() if v))
    for cwd, entry in ordered:
        total = sum(v for v in entry["files"].values() if v)
        reasons = ", ".join("%s=%d" % (k, entry["reasons"][k])
                            for k in sorted(entry["reasons"]))
        lines.append("")
        lines.append("  %s" % cwd)
        lines.append("    %d bytes across %d file(s), %d load(s) — %s"
                     % (total, len(entry["files"]), entry["loads"], reasons))
        for name, size in sorted(entry["files"].items(),
                                 key=lambda kv: -(kv[1] or 0)):
            lines.append("      %8s  %s" % ("?" if size is None else size, name))
    lines.append("")
    lines.append("  NOTE: CLAUDE.md and rule files only. Skill listings, agent")
    lines.append("  descriptions and MCP schemas never fire this event — these")
    lines.append("  totals are a FLOOR on base context, not base context.")
    return "\n".join(lines) + "\n"


def cmd_summary(out):
    path = ledger_path()
    out.write(_format_summary(path, summarize(_rows(path))))
    return 0


def main(argv, stream=None, out=None):
    if IMPORT_ERROR:
        sys.stderr.write("instructions-inventory: %s. Nothing recorded.\n" % IMPORT_ERROR)
        return 0
    if argv and argv[0] == "summary":
        return cmd_summary(out or sys.stdout)
    return cmd_hook(stream or sys.stdin)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 — a hook never crashes its session
        sys.stderr.write("instructions-inventory: %s: %s\n" % (type(exc).__name__, exc))
        sys.exit(0)
