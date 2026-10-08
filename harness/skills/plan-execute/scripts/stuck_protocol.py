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
    never a digest input, so it can never trip ``state-drift``). It counts a
    repeat when the signature matches EXACTLY **or** when a finding CARRIED OVER
    from the previous attempt — see :func:`_carry_keys` for where that line is
    drawn and why identity alone could never arm on a rework loop.
  * :func:`research_prompt` renders the research pass the executor appends to the
    rework feedback file once the counter reaches :data:`TRIGGER_AT`.

The trigger is deliberately CONSECUTIVE, not cumulative: a different error on the
next attempt is progress, and progress resets the counter to 1.

Escalation-ladder position: the research pass is the rung BETWEEN "retry" and
"raise the model". See ``references/closeout-contract.md`` § "The stuck protocol".
"""

import hashlib
import re

import llm_review_gate as lrg
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

# `llm-review-gate`'s own machine-readable verdict line. An INDETERMINATE run
# names no error class and emits no findings block, so it used to fall through to
# the unattributable branch and could never arm — see `signature`'s docstring.
_INDETERMINATE = re.compile(
    r"^\[llm-review-gate\]\s+level=(\S+)\s+verdict=indeterminate", re.I | re.M)


def _normalise(line):
    """Strip the volatile half of a failure line: paths, line numbers, addresses,
    whitespace, case. What survives is the message LOCUS — the part that is the
    same on every re-run of the same broken thing."""
    s = _ADDR.sub("<addr>", line)
    s = _PATH.sub("<path>", s)
    s = _NUM.sub("<n>", s)
    return _WS.sub(" ", s).strip().lower()


# A locus long enough to be unreadable in the research prompt. The SIGNATURE is
# still computed over the full text — only the stored/displayed locus is clipped,
# so two finding sets sharing a prefix still sign differently.
_LOCUS_MAX = 400


def _carry_keys(normalised_findings):
    """One CARRY-OVER key per finding: ``file|severity``, prose dropped.

    `findings_digest` emits ``file|line|severity|summary``. The summary is the
    reviewer's sentence about the defect and the line number moves with any edit
    above it — both change on a rework of the SAME defect, which is why signing
    the whole set never armed. What survives a rework unchanged, when the defect
    itself survives, is which file it is in and how bad it is.

    A finding that is not in the pinned four-field shape has no separable locus,
    so the whole normalised string is its key: strictly identity, which is the
    under-arming direction.
    """
    keys = []
    for f in normalised_findings:
        parts = f.split("|", 3)
        keys.append(f"{parts[0].strip()}|{parts[2].strip()}" if len(parts) == 4 else f)
    return sorted(set(k for k in keys if k.strip(" |")))


def _review_signature(text):
    """The findings branch, or None when the excerpt carries no findings block.

    Sorted: the reviewer's ordering is not stable, the SET of findings is.
    NOT `_normalise`: it strips paths, and a finding's path is repo-relative,
    stable, and the most discriminating field it has — stripping it collapsed
    `aggregate_outcomes.py` and `render_report.py` to the same `<path>`. Only the
    line number genuinely drifts between attempts, so only numbers go.
    """
    findings = lrg.findings_digest(text)
    if not findings:
        return None
    normalised = sorted(_WS.sub(" ", _NUM.sub("<n>", f)).strip().lower() for f in findings)
    locus = " ; ".join(normalised)
    blob = f"review-findings|{locus}"
    return {
        "class": "review-findings",
        "locus": locus[:_LOCUS_MAX],
        "sig": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12],
        "armable": True,
        "keys": _carry_keys(normalised),
    }


def _indeterminate_signature(text):
    """A review gate that could not reach a verdict, or None.

    Keyed on the GATE and its LEVEL only. The attempt notes below the banner quote
    whatever the reviewer said, which differs every run; the fact that this gate at
    this level could not decide is the stable part, and it is what recurs. Reached
    by signing this shape EXPLICITLY, never by loosening `armable` — a bare banner
    or a markdown fence still refuses.
    """
    m = _INDETERMINATE.search(text)
    if not m:
        return None
    locus = f"llm-review-gate level={m.group(1).lower()} verdict=indeterminate"
    return {
        "class": "review-indeterminate",
        "locus": locus,
        "sig": hashlib.sha256(locus.encode("utf-8")).hexdigest()[:12],
        "armable": True,
        "keys": [locus],
    }


def signature(excerpt):
    """Reduce a gate-failure excerpt to ``{class, locus, sig, armable}``.

    ``class`` is the deepest error class name found anywhere in the text (a
    traceback's last frame is its real cause); ``locus`` is the normalised LAST
    error-ish line; ``sig`` is a short stable digest of the pair, which is what
    the consecutive counter compares.

    TWO THINGS THAT LOOK LIKE POLISH AND ARE NOT (both measured 2026-08-15):

    * A REVIEW GATE'S FAILURE IS ITS FINDINGS, NEVER ITS BANNER. ``llm-review-*``
      ends every failing run with the same sentence ("failed with <n> finding(s).
      fix them, or …"), and that sentence is the only error-ish line in the whole
      excerpt — the findings themselves contain no error word. So EVERY review-gate
      failure signed ``6c61d90dec14`` regardless of what was found, and two
      unrelated findings read as one recurring root cause. When the excerpt carries
      a parseable findings block, the findings ARE the locus.

    * ``armable`` is False when nothing in the excerpt identifies WHAT failed — no
      error class and no findings. Such a locus is a banner, a prompt echo, or a
      markdown fence (s05 latched onto ``` once), and arming on it asserts a repeat
      that was never observed. Refusing under-arms; arming falsely buys a model
      rung on evidence that does not exist, which is the direction that costs.
      KNOWN NARROWING, measured on real gate output 2026-08-15: a `ruff check`
      failure ends `Found 3 errors.` with no error class and no findings array, so
      a repeated lint failure no longer arms. It did before — on the locus
      `found <n> error.`, which every lint failure with that error COUNT shares,
      i.e. the same false-recurrence defect wearing different clothes. Refusing is
      the honest answer; widen it only with a real excerpt and a real signature,
      never by loosening the shape (see `llm_review_gate.findings_digest`).

    A THIRD, measured 2026-08-20 over 182 outcome records: SIGNING ON IDENTITY
    ALONE MEANT THE REVIEW BRANCH COULD NEVER ARM. A rework loop changes the
    finding set on every attempt by construction — the worker fixes findings and
    the reviewer raises others — so `consecutive` reset to 1 forever, `armed()`
    was never true, and `escalation.climb_steps` returned 0 on every session ever
    run. 40 cohorts needed >=2 attempts; exactly one changed model or effort;
    `escalated_from` was null on all 182 records. The fix is NOT to coarsen this
    signature back (that is the 2026-08-15 bug): `sig` still means "identical
    failure". A separate `keys` field carries the weaker CARRY-OVER key that
    `record_failure` also accepts.

    ``keys`` is likewise how an INDETERMINATE review verdict became armable. It
    names no error class and emits no findings, so it fell to the unattributable
    branch and a session could repeat it forever without arming (repo-health s13,
    three times). It is signed EXPLICITLY on gate+level, not by relaxing
    ``armable`` — a bare banner or a markdown fence still refuses.
    """
    text = excerpt or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for branch in (_review_signature, _indeterminate_signature):
        sig = branch(text)
        if sig:
            return sig
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
        "locus": locus[:_LOCUS_MAX],
        "sig": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12],
        "armable": bool(cls),
        # No carry-over key: a traceback's locus IS its identity, and the only
        # weaker key available (the error class alone) would make any two
        # AssertionErrors one root cause. Identity signing is correct here.
        "keys": [],
    }


def armed(rec):
    """Is this stuck record real evidence of a repeat?

    THE ONE DEFINITION. Both consumers read it: the research pass in
    ``verify._gate_failed`` and — the expensive one — ``escalation.climb_steps``,
    which buys a model rung off this same counter. A record written before
    ``armable`` existed has no such key and keeps the old meaning.
    """
    return bool(rec) and rec.get("armable", True) and int(rec.get("consecutive") or 0) >= TRIGGER_AT


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
    # SAME ROOT CAUSE = the signature repeated exactly, OR a finding CARRIED OVER.
    # The second clause is the whole point: on a rework loop the finding set
    # changes by construction, so identity alone reset the counter to 1 forever
    # and the ladder below it was unreachable (measured 2026-08-20 — 40
    # multi-attempt cohorts, 1 rung change, 0 of 182 records with `escalated_from`).
    carried = sorted(set(prev.get("keys") or []) & set(sig["keys"]))
    same = prev.get("sig") == sig["sig"] or bool(carried)
    rec = {
        "sig": sig["sig"],
        "class": sig["class"],
        "locus": sig["locus"],
        "consecutive": (prev.get("consecutive", 0) + 1) if same else 1,
        "attempts": prev.get("attempts", 0) + 1,
        "previous_sig": prev.get("sig"),
        # Persisted, because `escalation` reads this record straight out of
        # run_state and must reach the same verdict as the caller here.
        "armable": sig["armable"],
        "keys": sig["keys"],
        "carried": carried,
    }
    stuck[session_id] = rec
    rsi.save_state(plan_dir, state)
    out = dict(rec)
    out["triggered"] = armed(rec)
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
    carried = rec.get("carried") or []
    # The FULL locus always ships — it is what the agent has to act on. The
    # carry-over line only says WHY this counts as a repeat when the two attempts
    # were not byte-identical, which is the case that armed nothing before.
    why = (("**survived your last rework** — still present after the fix:\n\n    "
            + "\n    ".join(carried))
           if carried else
           f"**repeated exactly** (`{rec['sig']}`, class `{rec['class']}`).")
    return f"""
## STUCK PROTOCOL — ARMED (attempt {rec['attempts']}, same root cause {rec['consecutive']}x)

That is **consecutive failure #{rec['consecutive']}**. The root cause {why}

This attempt's failure, class `{rec['class']}`:

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
