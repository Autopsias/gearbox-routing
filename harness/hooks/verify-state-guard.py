#!/usr/bin/env python3
"""verify-state-guard — PreToolUse(Bash|Write|Edit|MultiEdit|NotebookEdit):
a DISPATCHED AGENT may read a plan's `_verify_state/`, never write it.

WHY THIS EXISTS. `_verify_state/` holds the review gates' own
record: the findings ledger (`<sid>.findings.ndjson`), the owner's accepted
findings (`<sid>.accepted.json`) and the verify state (`<sid>.json`). A security
review of this ledger measured three forgeries an agent with a shell could make:
one surface row for the current bytes is a PASS with no review at all; one
`"status": "fixed"` row clears an open HIGH finding; one stale surface row
narrows the next review to a file the agent picks. The gate trusted the agents
it checks not to edit its record. This hook is the mechanical half of that.

WHO: only calls that carry `agent_id`, which Claude Code sets only inside a
subagent (code.claude.com/docs/en/hooks, "common input fields"). The main
session is the orchestrator; it writes `accepted.json` for the owner and is
never touched.

WHAT: a file tool aimed into `_verify_state/`, or a Bash command that names
`_verify_state` anywhere except as an argument to a known read-only command.
Reads stay open: a rework agent reads `<sid>.feedback.md`.

KNOWN LIMIT, deliberate: this reads the command TEXT, so it stops a careless or
plainly-written write, NOT an agent that tries. Any spelling that hides the
literal name gets through, and that is trivial: `_verify_s*`, `_verify_'state'`,
`_verify_\\state`, `cd _plans/x/_verify* && cp f s01.findings.ndjson`, a variable,
or a write inside a script file (all probed). Three security-review
rounds each found the next spelling; a text parser over shell has no closure, so
the patching STOPPED there by decision. The real fix is to keep the record out
of the agent's reach, not more parsing. Codex-lane sessions and headless
`claude -p` runs carry no `agent_id` and are not covered at all. No escape
hatch: one the agent can type protects nothing.

In short: this guard parses shell command text and blocks known command shapes only.
It is a best-effort safety net, not a security boundary, and a differently written
command can get past it.

Wired from settings.json hooks.PreToolUse.
Self-check: hooks/test_verify_state_guard.py
"""

import json
import os
import re
import sys

MARK = "_verify_state"
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# ONLY commands with no file-writing flag and no way to run another program.
# `sort -o`, `uniq IN OUT`, sed's `w`, `find -fls`/`-ok` and `rg --pre` each
# wrote while reading as a read (found in a security review of this hook).
READERS = {"cat", "head", "tail", "grep", "egrep", "fgrep", "wc", "jq", "ls",
           "stat", "file", "diff", "cut", "echo", "printf", "test", "["}
GIT_READS = {"log", "show", "diff", "status", "blame", "ls-files", "grep"}
# `--output` writes a file; `-c` sets a pager or alias, which runs a program.
_GIT_WRITE_OPT = re.compile(r"^(?:--output|-c$|--config-env|--exec-path)")
# A command or process substitution runs a command INSIDE a read's arguments.
_SUBST = re.compile(r"\$\(|`|<\(|>\(")

# A heredoc body is DATA when it feeds `git` (a commit message describing the
# ledger); fed to an interpreter it is a program, and stays in view.
_HEREDOC = re.compile(r"^([^\n]*?)<<-?\s*[\"']?(\w+)[\"']?([^\n]*)\n(.*?)^\s*\2\s*$",
                      re.S | re.M)
_SEP = re.compile(r"\n|;|&&|\|\||\|")
_REDIRECT_IN = re.compile(r">>?\s*['\"]?[^\s;&|]*" + MARK)
_ASSIGN = re.compile(r"^\w+=")


def _strip_git_heredocs(command):
    def keep(m):
        words = m.group(1).split()
        return m.group(0) if not words or words[0] != "git" else m.group(1) + m.group(3)
    return _HEREDOC.sub(keep, command)


def _segment_writes(seg):
    """Does this one simple command (which names MARK) write, or might it?"""
    if _REDIRECT_IN.search(seg):
        return True
    words = [w for w in seg.split() if not _ASSIGN.match(w)]
    if not words:
        return True                    # `d=..._verify_state` builds a path to use later
    cmd = os.path.basename(words[0])
    if cmd == "git":
        sub = next((w for w in words[1:] if not w.startswith("-")), "")
        return sub not in GIT_READS or any(_GIT_WRITE_OPT.match(w) for w in words)
    return cmd not in READERS


def bash_writes(command):
    command = _strip_git_heredocs(command)
    if _SUBST.search(command):
        return True
    return any(_segment_writes(s) for s in _SEP.split(command) if MARK in s)


def refused(payload):
    if not payload.get("agent_id"):
        return None
    tool, ti = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in FILE_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        return path if f"/{MARK}/" in "/" + path else None
    cmd = ti.get("command") if tool == "Bash" else None
    return cmd if isinstance(cmd, str) and MARK in cmd and bash_writes(cmd) else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    what = refused(payload) if isinstance(payload, dict) else None
    if what is None:
        return 0
    sys.stderr.write(
        "BLOCKED: a dispatched agent may read a plan's _verify_state/, never write it.\n"
        f"  refused: {what[:200]}\n\n"
        "_verify_state/ is the review gates' own record (findings ledger, accepted\n"
        "findings, verify state). One forged row there can pass a gate with no review.\n"
        "If a finding is wrong, say so in your closeout; the orchestrator and the owner\n"
        "decide what is accepted.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
