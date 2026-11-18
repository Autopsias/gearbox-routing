"""Orphan Codex broker detection for agent_janitor's reap pass.

The openai-codex plugin starts one ``app-server-broker.mjs serve`` per working
directory and never stops it on its own. When a plan worktree is removed, its
broker keeps a ``codex app-server`` plus every MCP server that app-server
started (measured: 3 brokers for removed worktrees held 267
processes and 10.8 GB). A broker whose ``--cwd`` no longer exists can serve
nothing, so that is the one broker shape the janitor may kill.

Kept out of agent_janitor.py (file-size ratchet).
"""
import os
import re

BROKER_RE = re.compile(r"/scripts/app-server-broker\.mjs\s+serve(?:\s|$)")
# Up to the next " --flag", so a path with spaces is read whole, never cut short.
_CWD_RE = re.compile(r"\s--cwd\s+(.+?)(?=\s--|$)")


def broker_cwd(command: str):
    """The ``--cwd`` a broker serves, or None when argv names none."""
    m = _CWD_RE.search(command)
    return m.group(1) if m else None


def classify_broker(command: str):
    """-> (cls, reason). Only a broker whose --cwd is gone is killable."""
    cwd = broker_cwd(command)
    if cwd is None:
        return "unknown", "broker-cwd-unnamed"
    if os.path.isdir(cwd):
        return "unknown", "broker-cwd-exists"
    return "dead-session", "broker-cwd-gone"
