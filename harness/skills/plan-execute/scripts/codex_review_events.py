#!/usr/bin/env python3
"""Codex's `--json` EVENT STREAM, read as events — the mirror of the prompt half.

`codex_review_prompt.py` holds what codex is ASKED. This holds the one thing the
backend must learn from what codex said WHILE IT RAN, rather than from the `-o`
last message it ends with: whether a failed run failed because the account is out
of budget (an UNAVAILABLE reviewer, which degrades) rather than for a transport
reason (an unreadable one, which retries).

WHY THIS IS NOT A GREP OVER THE LOG. That stream is not a transcript of errors.
It carries the per-turn token usage and the `aggregated_output` of every command
the reviewer ran — i.e. the source under review. Measured against a recorded review run:
dozens of `command_execution` items, one carrying a whole file's text, a few
`agent_message` and `error` items, and no error about quota. A free-text search over
that reads `"input_tokens": 4291` as a 429, and reads the pattern below — this
file's own source, whenever the gate reviews these scripts — as a refusal. Both
stamp `cross_family_unavailable` on a reviewer that never refused AND skip the
gate's second attempt, so the match is scoped to ERROR RECORDS.
"""
from __future__ import annotations

import json
import re

#: A rate/usage refusal is NOT an indeterminate reviewer, it is an unavailable
#: one, and the two want different dispositions. Matched lowercased against an
#: ERROR RECORD, never the whole log — and `429` only as a whole word, because
#: the number that reaches this pattern by accident is always part of a longer
#: one (a token count) and never a status on its own.
_QUOTA = re.compile(r"usage limit|quota|rate.?limit|insufficient_quota|\b429\b")


def _errorish(ev):
    """Is ONE parsed event an error record?

    `None` means the line did not parse as JSON, which makes it codex's own
    stderr: the supervisor merges stderr into this same file, and a CLI-level
    refusal can arrive as plain text with no event around it.
    """
    if not isinstance(ev, dict):
        return ev is None
    item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
    # A POPULATED `error` payload, never the mere presence of the key: an event
    # that reports `"error": null` is reporting that nothing went wrong, and
    # counting it in would hand the whole line back to a free-text search.
    return bool(ev.get("error")) or "error" in f"{ev.get('type')}{item.get('type')}"


def quota_hit(log):
    """Did codex refuse on usage/quota limits? -> bool.

    An unreadable log is NOT a refusal: the caller's other outcome (an unusable
    answer) is the honest one when nothing can be read about why.
    """
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return False
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            ev = None
        if _errorish(ev) and _QUOTA.search(ln.lower()):
            return True
    return False
