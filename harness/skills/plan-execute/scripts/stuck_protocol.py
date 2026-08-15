"""RS-03 — the stuck protocol, as a MECHANISM rather than prose.

The rule the generated prompts state: *two consecutive failures at the SAME root
cause stop the retry loop; run one time-boxed research pass against outside
sources, then fix it or close BLOCKED carrying a decision brief.*

Prose alone cannot enforce that — nothing would record what the previous attempt
failed on, so "same root cause" would be a claim no test could exercise. This
module supplies the missing state:

  * :func:`signature` reduces a gate-failure excerpt to a NORMALISED root-cause
    signature — error class + message locus, with paths, line numbers, hex
    addresses, and whitespace stripped. Two runs of the same broken test produce
    the same signature; two genuinely different errors do not.
  * :func:`record_failure` stores that signature and a CONSECUTIVE counter per
    session in ``run_state.json`` (mutable runtime state — never the manifest,
    never a digest input, so it can never trip ``state-drift``).
  * :func:`research_prompt` renders the research pass the executor appends to the
    rework feedback file once the counter reaches :data:`TRIGGER_AT`.

The trigger is deliberately CONSECUTIVE, not cumulative: a different error on the
next attempt is progress, and progress resets the counter to 1.

Escalation-ladder position: the research pass is the rung BETWEEN "retry" and
"raise the model". See ``references/closeout-contract.md`` § "The stuck protocol".
"""

import hashlib
import re

import run_state_io as rsi

# Consecutive same-signature failures that arm the protocol.
TRIGGER_AT = 2

# A traceback/gate excerpt line worth treating as the failure locus.
_ERRORISH = re.compile(r"(error|exception|failure|failed|assert|fatal|refus|traceback)", re.I)

# A dotted error CLASS name (`ValueError`, `subprocess.CalledProcessError`, …).
_ERROR_CLASS = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
                          r"(?:Error|Exception|Failure))\b")

_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s:,;)\'\"\]]+")
# No \b anchors, and decimals collapse as ONE token: `\b\d+\b` left a digit glued
# to a letter alone, so pytest's `in 12.34s` normalised to `in <n>.12s` and drifted
# every run — two runs of the SAME failing test produced different signatures and
# the protocol never armed. Found 2026-08-13 by this plan's acceptance review.
_NUM = re.compile(r"\d+(?:\.\d+)*")
_WS = re.compile(r"\s+")

# pytest's run-summary banner (`=== 1 failed, 396 passed in 12.34s ===`) matches
# _ERRORISH and is the LAST such line, so it used to win the locus. Once numbers
# normalise correctly it is WORSE than useless as a locus: every failing run of
# every DIFFERENT test reduces to the same string, which would arm the protocol on
# two unrelated errors. Skip it and key on the real failure line instead.
_SUMMARYISH = re.compile(r"^=+.*=+$|^\s*\d+\s+(failed|passed|error)", re.I)


def _normalise(line):
    """Strip the volatile half of a failure line: paths, line numbers, addresses,
    whitespace, case. What survives is the message LOCUS — the part that is the
    same on every re-run of the same broken thing."""
    s = _ADDR.sub("<addr>", line)
    s = _PATH.sub("<path>", s)
    s = _NUM.sub("<n>", s)
    return _WS.sub(" ", s).strip().lower()


def signature(excerpt):
    """Reduce a gate-failure excerpt to ``{class, locus, sig}``.

    ``class`` is the deepest error class name found anywhere in the text (a
    traceback's last frame is its real cause); ``locus`` is the normalised LAST
    error-ish line; ``sig`` is a short stable digest of the pair, which is what
    the consecutive counter compares.
    """
    text = excerpt or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    classes = _ERROR_CLASS.findall(text)
    cls = classes[-1] if classes else ""
    locus_line = ""
    for ln in lines:
        if _ERRORISH.search(ln) and not _SUMMARYISH.match(ln):
            locus_line = ln
    if not locus_line:
        # Nothing but banners: fall back to the last error-ish line, then the last
        # line at all. A weak locus still beats no signature.
        for ln in lines:
            if _ERRORISH.search(ln):
                locus_line = ln
    if not locus_line and lines:
        locus_line = lines[-1]
    locus = _normalise(locus_line)
    blob = f"{cls.lower()}|{locus}"
    return {
        "class": cls or "unclassified",
        "locus": locus,
        "sig": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12],
    }


def record_failure(plan_dir, session_id, excerpt):
    """Record one failed attempt for ``session_id`` and return the running record.

    Returns ``{class, locus, sig, consecutive, attempts, triggered}``. ``triggered``
    is True the moment ``consecutive`` reaches :data:`TRIGGER_AT` — that is the
    executor's cue to surface the research pass instead of a bare "try again".
    """
    sig = signature(excerpt)
    state = rsi.load_state(plan_dir)
    stuck = state.setdefault("stuck", {})
    prev = stuck.get(session_id) or {}
    same = prev.get("sig") == sig["sig"]
    rec = {
        "sig": sig["sig"],
        "class": sig["class"],
        "locus": sig["locus"],
        "consecutive": (prev.get("consecutive", 0) + 1) if same else 1,
        "attempts": prev.get("attempts", 0) + 1,
        "previous_sig": prev.get("sig"),
    }
    stuck[session_id] = rec
    rsi.save_state(plan_dir, state)
    out = dict(rec)
    out["triggered"] = rec["consecutive"] >= TRIGGER_AT
    return out


def last_failure(plan_dir, session_id):
    """The recorded failure record for a session, or ``None``. Read-only."""
    return (rsi.load_state(plan_dir).get("stuck") or {}).get(session_id)


def clear(plan_dir, session_id):
    """Forget a session's failure history (used when a session is re-planned or
    its verify state is reset). Missing history is a no-op."""
    state = rsi.load_state(plan_dir)
    stuck = state.get("stuck") or {}
    if stuck.pop(session_id, None) is not None:
        state["stuck"] = stuck
        rsi.save_state(plan_dir, state)


def research_prompt(rec):
    """The time-boxed research pass, rendered for the rework feedback file.

    Tiered with graceful degradation: whichever research MCP is actually
    available is the one used, and having none is not an excuse to skip the pass —
    the built-in web search still counts, and a pass that finds nothing is
    reported as "nothing found", not silently dropped.
    """
    return f"""
## STUCK PROTOCOL — ARMED (attempt {rec['attempts']}, same root cause {rec['consecutive']}x)

That is **consecutive failure #{rec['consecutive']} with the SAME normalised
root-cause signature** (`{rec['sig']}`, class `{rec['class']}`):

    {rec['locus']}

Retrying the same approach a third time is the failure mode this protocol exists
to stop. Before you touch the code again:

1. **Run ONE time-boxed research pass — 10 minutes, hard stop.** Search OUTSIDE
   this repository for this exact error class + locus. Tiered, first available
   wins, degrade gracefully — if a tier's MCP is not configured, drop to the next
   and say so; if none is available, use the built-in web search:
     - **Perplexity** (`mcp__perplexity-ask__perplexity_ask`) — "why does X fail with Y"
     - **Exa** (`mcp__exa__web_search_exa`) — recent issues, changelogs, discussions
     - **Ref** (`mcp__ref__ref_search_documentation`) — the library's own docs
2. **Then do exactly one of two things:**
   - **Fix it**, citing what the research changed about your diagnosis; or
   - **Close `BLOCKED`** carrying a `decision_brief` — what you tried, what the
     sources said (with `source` + `takeaway` per finding), at most three options,
     and your recommendation. A BLOCKED closeout without that brief is REFUSED.
3. **Do NOT** silence the check, weaken the assertion, or retry blind.

This research pass is the rung between "retry" and "raise the model" on the
standing escalation ladder — do not skip past it to a model escalation.
"""
