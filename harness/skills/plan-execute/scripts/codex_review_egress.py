#!/usr/bin/env python3
"""The EGRESS half of the Codex reviewer: may this review's bytes go to Codex?

Split out of `codex_review_backend.py` at its size bound, at the seam the
backend already drew ("availability and egress, both decided BEFORE any codex
process is started"). Two checks, because codex is sent two kinds of bytes: the
reviewed TREE (`egress_reason`) and TEXT built outside it (`text_egress_reason`).
The backend re-exports both names, and tests pin them there.
"""
from __future__ import annotations

from pathlib import Path


def egress_reason(cwd):
    """The data_sensitivity_guard's reason to keep this tree off Codex, or None.

    RE-RUN AT GATE TIME, never read from a stamp `begin` left behind: the tree
    changes between dispatch and verify, and a `.env` written by the session
    under review is exactly the case a stale verdict would miss. The scanned root
    is the REVIEWED tree (`--cwd`), not the process cwd, so the tree that was
    cleared and the tree that would be sent are the same one by construction.

    `PLAN_EXECUTE_EGRESS_ROOT` is deliberately NOT honoured here. On the dispatch
    path it is safe because `_codex_cmd` `cd`s into that same root; here codex
    runs in `cwd` whatever the variable says, so honouring it cleared one tree and
    shipped another (a `.env` in `cwd` passed with the variable on a clean dir).
    """
    from egress import _egress_verdict  # local: keeps import light
    from run import _load_routing

    eg = _egress_verdict(_load_routing()[1], Path(cwd))
    if eg["restricted_hit"] and not eg["opted_in"]:
        return f"{eg['restricted_hit']} (tree {eg['root']})"
    return None


def text_egress_reason(cwd, texts):
    """The guard's reason to keep TEXT from outside the tree off Codex, or None.

    The tree scan reads the tree as it is NOW. What codex is handed also holds
    bytes that scan never sees: the diff file (its `-` lines are content the
    session deleted, so a secret removed from the tree still rides in the diff),
    the prior-findings section and the intent text, both from the plan
    directory. Same scanner and the same per-repo opt-in as the dispatch path's
    `codex_command._refuse_restricted_text`.
    """
    from egress import _text_hit
    from run import _load_routing
    from ssot_policy import _egress_opt_in

    if _egress_opt_in(_load_routing()[1], Path(cwd)):
        return None
    for name, text in texts.items():
        hit = _text_hit(text, name) if text else None
        if hit:
            return f"restricted content in the {name} this review would send: {hit}"
    return None


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""      # codex cannot read it either, so nothing of it is sent
