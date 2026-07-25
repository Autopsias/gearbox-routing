#!/usr/bin/env python3
"""
mcp-health.py — surface dead/auth-failing user-level MCP servers fast.

Why: the Perplexity MCP sat dead (401/quota) for a full month across
3 projects before anyone noticed (see ~/.claude/reflection-notes.md #4).
This script closes that gap by scanning recent Claude Code session
transcripts for known auth/quota failure signatures and attributing
them to the MCP server that produced them, so a dead server surfaces
within a day instead of thirty.

Usage:
    python3 ~/.claude/scripts/mcp-health.py [--days N] [--json]

    --days N   lookback window over transcript mtimes (default 3)
    --json     machine-readable output instead of the human report

Exit code: 0 always (informational tool). Non-zero only on a hard
error (e.g. ~/.claude.json unreadable).

What it does:
    1. Reads ~/.claude.json -> mcpServers, lists configured servers.
    2. Walks ~/.claude/projects/**/*.jsonl transcripts modified within
       the lookback window.
    3. For each line, looks for a tool_use of an mcp__<server>__<tool>
       tool followed (same or next few lines) by a tool_result whose
       content matches a known auth/quota-failure pattern (401, 403,
       Unauthorized, insufficient_quota, invalid_api_key, expired
       token, ECONNREFUSED for stdio spawn failures).
    4. Reports servers with 1+ failures in the window, with counts and
       the most recent failure snippet.

Cheap and best-effort: transcript JSON schemas drift across Claude
Code versions, so pattern matching is intentionally loose (regex over
raw lines, not strict JSON-schema parsing) to stay resilient.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
CLAUDE_JSON = Path(os.path.expanduser("~/.claude.json"))
PROJECTS_DIR = CLAUDE_HOME / "projects"

FAILURE_PATTERNS = [
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
    re.compile(r"[Uu]nauthorized"),
    re.compile(r"insufficient_quota"),
    re.compile(r"invalid_api_key"),
    re.compile(r"[Ee]xpired token"),
    re.compile(r"ECONNREFUSED"),
    re.compile(r"authentication (failed|error)", re.I),
]

TOOL_USE_RE = re.compile(r'"name"\s*:\s*"(mcp__([a-zA-Z0-9_\-]+)__[a-zA-Z0-9_\-]+)"')


def load_configured_servers():
    if not CLAUDE_JSON.exists():
        return {}
    try:
        data = json.loads(CLAUDE_JSON.read_text())
    except Exception as e:
        print(f"ERROR: could not parse {CLAUDE_JSON}: {e}", file=sys.stderr)
        return {}
    return data.get("mcpServers", {})


def iter_recent_transcripts(days):
    if not PROJECTS_DIR.exists():
        return
    cutoff = time.time() - days * 86400
    for path in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                yield path
        except OSError:
            continue


def scan_transcript(path, counts, samples):
    """Best-effort line-window scan: track the last-seen mcp server name,
    and when a failure pattern appears within the next few lines, credit it."""
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return
    last_server = None
    last_server_idx = -1
    WINDOW = 6
    for i, line in enumerate(lines):
        m = TOOL_USE_RE.search(line)
        if m:
            last_server = m.group(2)
            last_server_idx = i
            continue
        if last_server and (i - last_server_idx) <= WINDOW:
            for pat in FAILURE_PATTERNS:
                if pat.search(line):
                    counts[last_server] = counts.get(last_server, 0) + 1
                    snippet = line.strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    samples.setdefault(last_server, []).append(
                        {"file": str(path), "line": i + 1, "snippet": snippet}
                    )
                    break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    configured = load_configured_servers()
    counts = {}
    samples = {}

    for path in iter_recent_transcripts(args.days):
        scan_transcript(path, counts, samples)

    result = {
        "configured_servers": sorted(configured.keys()),
        "lookback_days": args.days,
        "failures": {
            server: {
                "count": n,
                "most_recent": samples.get(server, [])[-1] if samples.get(server) else None,
            }
            for server, n in sorted(counts.items(), key=lambda kv: -kv[1])
        },
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"MCP health probe — {len(configured)} configured server(s), "
          f"{args.days}-day transcript lookback")
    print(f"Configured: {', '.join(sorted(configured.keys())) or '(none)'}")
    print()
    if not counts:
        print("No auth/quota failure signatures found in recent transcripts.")
        return 0

    print("Servers with auth/quota failure signatures:")
    for server, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        flag = "DEAD?" if server not in configured else "auth-flagged"
        print(f"  [{flag}] {server}: {n} failure line(s) in window")
        recent = samples.get(server, [])[-1]
        if recent:
            print(f"      last: {recent['file']}:{recent['line']}")
            print(f"      {recent['snippet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
