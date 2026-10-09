#!/usr/bin/env python3
"""Read this session's live context size out of its own transcript tail.

The formula is byerlikaya/claude-starter-kit ``plugin/hooks/context-usage.sh``'s:
context = ``input_tokens`` + ``cache_read_input_tokens`` +
``cache_creation_input_tokens`` of the LAST assistant usage entry.  The
statusline's own ``context_window`` JSON is CUMULATIVE and wrong for this
purpose (anthropics/claude-code#13783).

WHOLE JSONL RECORDS, NEVER A BYTE WINDOW.  A single assistant record carrying
large tool output is routinely bigger than 64 KB, so the reader starts at a
64 KB tail, always drops the (necessarily partial) first line, and DOUBLES the
window up to a bounded 4 MB until it finds a complete record.  A reader that
truncated instead would return ``None``, which under the veto's rule (e) means
the veto silently stops working.

WHAT ``ctx`` HAS TO MEAN: the size of the NEXT request, not the size of the
last one.  Summing the last assistant record's input + cache tokens alone
measures what was already sent and stops there — it excludes that turn's own
``output_tokens`` and every tool result appended after it.  the recorded run's measured
trigger series steps ~15.5 k per turn on turns whose ``output_tokens`` is 1, so
almost all of that growth is the trailing tool results.  Under-reading context
is the same direction of error as over-stating the window: both leave the veto
deferring past the point where the session actually dies.  So the reader adds
the output tokens (exact) and a byte estimate of the records that follow
(crude, and deliberately biased high).

``context_tokens`` ALWAYS returns a THREE-tuple.  Every error path returns
``(None, None, 'measurement_unavailable:<reason>')`` — a malformed line, a
missing file, a permission error, the bounded search finding nothing.  A caller
unpacking three values must never meet two.
"""

from __future__ import annotations

import json
import os

START_BYTES = 64 * 1024
MAX_BYTES = 4 * 1024 * 1024

# The usage fields that make up the context of the NEXT request.
# INPUT_FIELDS is what was sent for the last assistant turn; at least one must
# be present for a record to count as a usage record at all.
INPUT_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
# CARRIED_FORWARD_FIELDS is what that turn ADDED and the next request must also
# carry. Optional: a record without them still counts, it just measures less.
CARRIED_FORWARD_FIELDS = ("output_tokens",)

# ponytail: bytes/4 is the standard crude token estimate, applied to the records
# that follow the last assistant usage block (tool results, user turns) because
# nothing in the transcript counts them for us. It runs HIGH on JSON-escaped
# text, and high is the safe direction here: over-reading ctx releases the veto
# earlier. Upgrade path if it ever matters — a real tokenizer, or a usage block
# that reports the trailing content, whichever Claude Code offers first.
BYTES_PER_TOKEN = 4

# THERE IS NO BUILT-IN WINDOW TABLE, AND THE REASON IS MEASURED.
#
# The design assumed the transcript's own ``contextWindow`` field would supply
# the window.  MEASURED on real transcripts: none carries
# that field.  So a fallback is the only source in practice — and the
# obvious fallback, a table keyed on the model id, CANNOT BE MADE SAFE here:
#
#   * every transcript writes the BARE id (``claude-opus-5``); none carries a
#     ``[1m]`` marker or any other window marker;
#   * a host can run BOTH variants of that id — settings.json selects
#     ``opus[1m]`` while subagent dispatches pin ``opus`` — so one id spans two
#     different windows and the transcript cannot tell them apart.
#
# A table entry derived from observed sessions is therefore a MAXIMUM over a
# mixed population, not a lower bound on any one session's window.  Applied to
# the smaller variant it OVERSTATES the window, which inflates the headroom,
# which is the single error that kills a session: the veto keeps deferring past
# the real limit and there is no recovery path (the recorded run measured zero
# error-triggered compactions and a run that died on "Prompt is too long").
#
# So: no guessing.  The window comes from the transcript, from the operator's
# override file, or from an id that literally names its own window.  Failing
# that it is None, which the veto's rule (e) reads as ALLOW.  A disarmed veto
# is a non-event; an overstated window is a dead session.
LONG_CONTEXT_MARKER = "[1m]"
LONG_CONTEXT_WINDOW = 1_000_000

# The operator's calibration knob, and the ONLY place a window belongs when the
# transcript does not carry one.  {"<model id prefix>": <tokens>} — absent by
# default, and absent means "the veto stays off", never "unlimited".  It lives
# beside the ledger and the kill switch, outside both versioned trees, so
# recording a window is never a harness hotfix.  Matching is by prefix, so an
# entry is the operator asserting that EVERY launch whose id starts with that
# prefix has at least that window on this machine — which is exactly the claim
# a bare `claude-opus-5` entry can get wrong.
OVERRIDE_FILE = "~/.gearbox-state/compaction/model-windows.json"


_INF = float("inf")


def _is_number(value) -> bool:
    """A REAL, FINITE number. json.loads accepts NaN and Infinity by default,
    and int(nan) raises ValueError while int(inf) raises OverflowError — either
    would escape context_tokens as an exception instead of a three-tuple."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and -_INF < value < _INF


def _first_int(*values):
    for value in values:
        if _is_number(value):
            return int(value)
    return None


def _overrides():
    try:
        with open(os.path.expanduser(OVERRIDE_FILE), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ()
    if not isinstance(data, dict):
        return ()
    return tuple(
        (str(k), int(v))
        for k, v in data.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
    )


def window_for_model(model):
    """Context window for a model id, or None when it cannot be known.

    Order: the operator's override file, then the model id's own "[1m]"
    self-declaration.  There is no built-in table — see the block at the top of
    this file for why one cannot be made safe.
    """
    if not isinstance(model, str) or not model:
        return None
    best = None
    for prefix, window in _overrides():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), window)
    if best is not None:
        return best[1]
    if LONG_CONTEXT_MARKER in model:
        return LONG_CONTEXT_WINDOW
    return None  # not a guess — rule (e) reads None as ALLOW


def usage_from_line(line, carry_forward=True):
    """(ctx_tokens, model_window) for one COMPLETE assistant usage record.

    Returns None for anything else — a fragment, a non-JSON line, a user
    record, an assistant record with no usage block.  Callers scan backwards
    and keep going until this returns a pair.

    ``carry_forward=False`` sums the INPUT fields only, dropping this turn's
    own output.  context_after_compaction() wants it: the input of the first
    request after a boundary IS the post-compaction context, and that turn's
    output is not part of it.
    """
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    if record.get("type") != "assistant" and message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    sent = tuple(usage.get(name) for name in INPUT_FIELDS)
    carried = tuple(usage.get(name) for name in CARRIED_FORWARD_FIELDS)
    # A part that is PRESENT but not a finite number makes the whole record
    # untrustworthy. Summing the rest would UNDER-report the context, and
    # under-reporting is the dangerous direction: a smaller ctx sits further
    # below the ceiling, so the veto defers past the point it should have
    # released at.
    if any(value is not None and not _is_number(value) for value in sent + carried):
        return None
    if not any(_is_number(value) for value in sent):
        return None
    counted = sent + carried if carry_forward else sent
    ctx = sum(int(value) for value in counted if _is_number(value))
    window = _first_int(
        usage.get("contextWindow"),
        message.get("contextWindow"),
        record.get("contextWindow"),
    )
    if window is None:
        window = window_for_model(message.get("model") or record.get("model"))
    return ctx, window


def _scan_tail(raw, start):
    """(last complete assistant usage record, bytes of the records AFTER it).

    The tail always runs to EOF, so every line after the record we pick is
    present and complete — which is what makes the trailing byte count worth
    anything. Those lines are the tool results and user turns that the next
    request carries but no usage block has counted yet.
    """
    lines = raw.split(b"\n")
    if start:
        # The first line of a mid-file tail is a fragment, never a record.
        lines = lines[1:]
    trailing = 0
    for line in reversed(lines):
        if not line.strip():
            continue
        found = usage_from_line(line)
        if found is not None:
            return found, trailing
        trailing += len(line) + 1  # + the newline that separated it
    return None, 0


def _resolve(found, trailing_bytes):
    ctx, model_window = found
    ctx += trailing_bytes // BYTES_PER_TOKEN
    if model_window is not None and ctx > model_window:
        # The bound is provably stale: this session is already bigger than the
        # window we would have used.  Say so and hand the veto a None, which
        # rule (e) reads as 'allow'.
        return (ctx, None, "read:model_window_contradicted")
    if model_window is None:
        return (ctx, None, "read:no_model_window")
    return (ctx, model_window, "read")


def context_tokens(transcript_path):
    """(tokens, model_window, source) — source is 'read' or a reason string."""
    if not transcript_path:
        return (None, None, "measurement_unavailable:no_transcript_path")
    try:
        size = os.path.getsize(transcript_path)
    except OSError as exc:
        return (None, None, "measurement_unavailable:" + type(exc).__name__)
    if size <= 0:
        return (None, None, "measurement_unavailable:empty_transcript")

    window = START_BYTES
    while True:
        start = max(0, size - window)
        try:
            with open(transcript_path, "rb") as handle:
                handle.seek(start)
                # Read a fixed snapshot: the file may be appended to mid-read.
                raw = handle.read(size - start)
        except OSError as exc:
            return (None, None, "measurement_unavailable:" + type(exc).__name__)
        found, trailing = _scan_tail(raw, start)
        if found is not None:
            return _resolve(found, trailing)
        if start == 0 or window >= MAX_BYTES:
            return (None, None, "measurement_unavailable:no_usage_record")
        window *= 2


def compact_summary_chars(transcript_path):
    """Chars in the summary Claude Code injects right after a compaction, or
    None when the transcript carries no boundary at all.

    MEASURED against a real on-disk transcript at a live compaction boundary:
    the boundary itself is a record shaped
    ``{"type":"system","subtype":"compact_boundary","content":"Conversation
    compacted",...}`` — its own ``content`` is a fixed label, never the
    summary. The actual re-orientation text the next turn reads is the record
    that follows it, ``{"type":"user","message":{"role":"user","content":
    "<summary text>"}}`` with a plain-string content. This counts THAT
    string's length; the boundary record's own label is never measured.

    ponytail: reads the WHOLE file rather than a bounded tail. Compaction
    shrinks the CONTEXT, not the transcript file — even a very large
    transcript carrying a compact_boundary reads in tens of ms, so the whole-file read stays cheap enough;
    upgrade to a bounded tail scan like context_tokens() if that measured cost
    ever stops holding.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    lines = [line for line in raw.split(b"\n") if line.strip()]
    boundary_at = None
    for index in range(len(lines) - 1, -1, -1):
        try:
            record = json.loads(lines[index])
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and record.get("subtype") == "compact_boundary":
            boundary_at = index
            break
    if boundary_at is None:
        return None
    for line in lines[boundary_at + 1:]:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        message = record.get("message") if isinstance(record, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            return len(content)
    return None


if __name__ == "__main__":  # pragma: no cover - hand probe
    import sys

    print(context_tokens(sys.argv[1] if len(sys.argv) > 1 else None))


# --- the post-compaction context, which PostCompact CANNOT measure ----------


def _last_boundary_index(lines):
    """Index of the last ``compact_boundary`` record in ``lines``, or None."""
    for index in range(len(lines) - 1, -1, -1):
        try:
            record = json.loads(lines[index])
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and record.get("subtype") == "compact_boundary":
            return index
    return None


def context_after_compaction(transcript_path):
    """(tokens, source) — the context the FIRST request after the last
    compaction carried.  ``(None, reason)`` when it cannot be read yet.

    WHY PostCompact CANNOT MEASURE THIS, AND NEVER COULD.  The transcript is
    append-only and the ``compact_boundary`` record lands AFTER the hook runs.
    MEASURED: the boundary record is stamped after the PostCompact hook runs,
    so ``context_tokens()`` called there walks back PAST
    the boundary to the last PRE-compaction assistant record and returns
    essentially ctx_before again.  That is why no ledger pair had ever
    shrunk, and why the ledger could not answer whether compaction shrinks
    context at all.  The fix is not more care in that hook; the number does not
    exist yet.  It appears only once the session takes its next assistant turn,
    so the measurement is deferred to a later UserPromptSubmit.

    INPUT SIDE ONLY, and the FIRST record after the boundary, not the last.
    input + cache_read + cache_creation of that record IS the size of the first
    post-compaction request.  Taking the FIRST one makes the number STABLE: it
    stops moving the moment that turn lands, so measuring late is still
    measuring the same thing.
    """
    if not transcript_path:
        return (None, "no_transcript_path")
    try:
        size = os.path.getsize(transcript_path)
    except OSError as exc:
        return (None, type(exc).__name__)
    if size <= 0:
        return (None, "empty_transcript")

    window = START_BYTES
    while True:
        start = max(0, size - window)
        try:
            with open(transcript_path, "rb") as handle:
                handle.seek(start)
                raw = handle.read(size - start)
        except OSError as exc:
            return (None, type(exc).__name__)
        lines = raw.split(b"\n")
        if start:
            # The first line of a mid-file tail is a fragment, never a record.
            lines = lines[1:]
        boundary = _last_boundary_index(lines)
        if boundary is not None:
            for line in lines[boundary + 1:]:
                found = usage_from_line(line, carry_forward=False)
                if found is not None:
                    return (found[0], "read_after_boundary")
            # Normal, not an error: the session has not answered yet.
            return (None, "no_turn_since_compaction")
        if start == 0 or window >= MAX_BYTES:
            return (None, "no_boundary_found")
        window *= 2
