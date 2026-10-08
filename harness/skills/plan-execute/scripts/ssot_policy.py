#!/usr/bin/env python3
"""Parsers for the routing SSOT's executor_policy block.

A leaf module: egress.py and run.py both read these, and routing them through
either would make the import graph a cycle. Every parser FAILS CLOSED — an
absent, empty or unparseable block grants nothing.
"""
import os
import re
import time


def _ssot_block(ssot_text, key):
    """Comment-stripped body of the YAML block under the first non-commented
    `key:` line (the following lines indented deeper than the key line). The
    regex matchers below run ONLY inside their owning block, so commented-out
    or unrelated-block text can never manufacture policy (adversarial-review
    2026-07-10, Codex HIGH: a commented-out egress opt-in previously matched)."""
    lines = (ssot_text or "").splitlines()
    out, key_indent = [], None
    for ln in lines:
        stripped = ln.strip()
        if key_indent is None:
            m = re.match(rf"^(\s*){re.escape(key)}:", ln)
            if m and not stripped.startswith("#"):
                key_indent = len(m.group(1))
            continue
        if stripped and not stripped.startswith("#"):
            if len(ln) - len(ln.lstrip()) <= key_indent:
                break
            out.append(ln)
    return "\n".join(out)


# Inline-mapping entries ONLY ({ <path_key>: ..., expiry: ... } on one line) —
# block-style entries deliberately DON'T parse (fails closed; the SSOT documents
# the required shape beside each block). `(?<!\w)` keeps the `path:` matcher off
# `repo_path:`, so the two blocks can never read each other's entries.
def _inline_entry_re(path_key):
    return re.compile(
        rf"-\s*\{{[^}}]*(?<!\w){path_key}:\s*\"?([^\",}}]+)\"?[^}}]*"
        rf"expiry:\s*\"?(\d{{4}}-\d{{2}}-\d{{2}})\"?[^}}]*\}}"
    )


_OPT_IN_RE = _inline_entry_re("repo_path")
_ALLOWLIST_RE = _inline_entry_re("path")


def _content_allowlist(ssot_text):
    """Realpath'd, lowercased set of FILES with an UNEXPIRED entry in
    data_sensitivity_guard.content_scan_allowlist — a reviewed finding that no
    longer has to force a whole-repo egress opt-in.

    Same shape and the same fail-closed parsing as egress_opt_ins: inline
    mappings only, parsed from inside that block with comments stripped, so a
    block-style, commented-out or out-of-block entry never allows anything. It
    only ever silences a CONTENT finding — a `.env*` file is not allowlistable."""
    today = time.strftime("%Y-%m-%d")
    return {
        os.path.realpath(os.path.expanduser(m.group(1).strip())).lower()
        for m in _ALLOWLIST_RE.finditer(_ssot_block(ssot_text, "content_scan_allowlist"))
        if m.group(2) >= today
    }


def _egress_opt_in(ssot_text, root):
    """True iff data_sensitivity_guard.egress_opt_ins (that block ONLY, comments
    stripped) carries an UNEXPIRED entry whose repo_path realpath-matches `root`
    (case-insensitive). No env-var bypass exists by design — the SSOT entry is
    the only key."""
    block = _ssot_block(ssot_text, "egress_opt_ins")
    if not block:
        return False
    real_root = os.path.realpath(str(root)).lower()
    today = time.strftime("%Y-%m-%d")
    for m in _OPT_IN_RE.finditer(block):
        repo_path, expiry = m.group(1).strip(), m.group(2)
        if os.path.realpath(os.path.expanduser(repo_path)).lower() == real_root and expiry >= today:
            return True
    return False


_EXECUTOR_FOR_RE = re.compile(r"^\s*executor_for:\s*\[([^\]]*)\]", re.M)


def _executor_for(ssot_text):
    """Set of task_class names opted into dial-driven Codex execution — parsed
    ONLY from inside the `executor_policy:` block (comments stripped). Absent
    block / empty or unparseable list = opted into NOTHING (fail closed)."""
    m = _EXECUTOR_FOR_RE.search(_ssot_block(ssot_text, "executor_policy"))
    if not m:
        return frozenset()
    return frozenset(t.strip().lower() for t in m.group(1).split(",") if t.strip())


# Executor policy, enforcement half: which sessions are barred from
# unsupervised Codex execution, and the exception that refusing one raises.
# Lives here rather than in run.py so codex_command.py can enforce the bar
# without importing the orchestrator back.
def _session_barred(session):
    """Reason string when this session is barred from unsupervised Codex
    execution (linchpin class or irreversible work), else None.
    security_sensitive is deliberately NOT barred (SSOT executor_policy)."""
    if (session.get("task_class") or "").strip().lower() == "linchpin":
        return "task_class is linchpin"
    if "irreversible_change" in (session.get("peer_triggers") or []):
        return "peer_triggers declares irreversible_change"
    if (session.get("dispatch") or {}).get("guards_irreversible"):
        return "dispatch.guards_irreversible is set"
    return None


class UnroutableCodexSession(Exception):
    """A session declared/required to run on Codex whose Codex dispatch cannot be
    constructed. HARD-FAIL INVARIANT (s04 edge-case #2): this must surface as a
    loud BLOCKED, never fall through to the silent None→inherit-Claude path —
    a mis-routed Codex session quietly executing on Claude is the exact opposite
    of the feature."""
