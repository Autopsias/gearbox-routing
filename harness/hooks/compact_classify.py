#!/usr/bin/env python3
"""Whole-prompt session classifier for compact-policy.py.

`classify()` in compact-policy.py matches the FIRST WORD of a prompt — a slash
command, or the word `research`. Measured over the ledger
(~/.gearbox-state/compaction/decisions.ndjson): that form marked only a small share of
sessions as orchestrator/planning and the rest as default. A research session that
opened with a plain-language request to analyse a document was `default`, compacted
early, and left most of a 1,000,000 window unused; many compactions on 1M-window
sessions were of that kind. This module reads the
WHOLE opening prompt.

`content_kind(text)` returns "planning" or None — never "default": the caller
owns the fall-through, and a None here costs exactly today's behaviour (one
compaction at the window). A FALSE POSITIVE costs more: planning is STICKY, so
it arms the veto for the rest of the session. Hence planning needs ALL of:

  1. a planning signal — an analysis verb or a document noun, as a WORD
     (`researching` is not `research`: that substring match is the incident
     compact-policy.py's PLANNING_WORDS comment records);
  2. no build signal anywhere — an ambiguous prompt ("investigate why
     test_foo is flaky") stays default on purpose;
  3. not a harness dispatch — the plan-execute review gate opens headless
     sessions with "Review the diff in the file …" / "Invoke the code-review
     skill …". Their window is not ours to widen.

MEASURED against the opening prompts on disk (120-char prefixes): rule 1
alone matched many, all three together only a handful — every one an
analysis session, none of them a review-gate dispatch. Re-measure with the
same replay (policy/*.json -> content_kind) before trusting these numbers.
`/research <topic>` (commands/research.md) is the explicit override when the
words do not match.
"""

from __future__ import annotations

import re

PLANNING_SIGNALS = re.compile(
    r"\b(analy[sz]e|analysis|research|review|study|summari[sz]e|compare|"
    r"investigate|evaluate|assess|audit|explain|deep[- ]dive|dig into|"
    r"transcript|document|paper|article|course|report|proposal)\b",
    re.IGNORECASE,
)
BUILD_SIGNALS = re.compile(
    r"\b(fix(?:es|ed|ing)?|implement(?:s|ed|ing|ation)?|"
    r"build(?:s|ing)?|refactor(?:s|ed|ing)?|deploy(?:s|ed|ing)?|run(?:s|ning)?|"
    r"commit(?:s|ted|ting)?|push(?:es|ed|ing)?|install(?:s|ed|ing)?|"
    r"rename(?:s|d|ing)?|tests?|test_\w+|testing|debug(?:s|ged|ging)?|"
    r"write a|create a)\b",
    re.IGNORECASE,
)
# `add` is NOT a build signal: "worth it to be added" hid a real research
# session (measured: one real research session hidden). An analysis word plus `add`
# resolves to planning; the cost of that false positive is a wider window on a
# coding session, never a lost session.
# A path is not prose: `PLAN-REVIEW-LOG.md` carries the word `review` and
# `@file` references are common openers. Tokens with a slash are dropped
# before matching (measured: removed a false positive).
PATH_TOKEN = re.compile(r"\S*[/\\]\S*")
# ponytail: substrings of the review gate's own prompt text. If the gate
# rephrases, these go stale and a gate session merely stays `default` —
# today's behaviour, never a wrong block.
DISPATCH_MARKERS = ("Review the diff in the file", "Invoke the code-review skill")


def content_kind(text):
    """"planning" when the whole prompt reads as analysis work, else None."""
    text = text or ""
    if text.lstrip().startswith("/"):
        return None  # commands belong to the caller's tables
    if any(marker in text for marker in DISPATCH_MARKERS):
        return None
    prose = PATH_TOKEN.sub(" ", text)
    if not PLANNING_SIGNALS.search(prose) or BUILD_SIGNALS.search(prose):
        return None
    return "planning"
