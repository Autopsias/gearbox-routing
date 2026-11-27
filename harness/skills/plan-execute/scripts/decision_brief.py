"""The `decision_brief` a BLOCKED closeout carries (RS-04) and its `owner_call`
(schema v9): the schema check and the plain-text render for HALT_NOTICE.

Split out of closeout_pipeline.py (file-size ratchet);
closeout_pipeline re-exports every public name, so callers are unchanged.
"""

# The manifest `plan_schema_version` at which a BLOCKED closeout MUST carry a
# `decision_brief` (RS-04). Same gating discipline, same reason: a plan already
# on disk keeps the contract it was built under. Set to the version that
# introduced the field, so `build_plan.PLAN_SCHEMA_VERSION` and this constant
# move together and every plan built before the bump is untouched.
DECISION_BRIEF_MIN_SCHEMA = 5

MAX_BRIEF_OPTIONS = 3

# From v9 a BLOCKED brief names WHY the owner must answer (`owner_call`). The first
# four are the owner-only calls of CLAUDE.md ("authorization, money, credentials,
# taste"); `stuck` = the stuck protocol ran out or a declared abort condition hit;
# `wait` = blocked on a date or event, named in `wait_for` (plan-execute has no
# timed re-dispatch, so a wait still needs the owner to resume it). Anything else
# the session decides itself and records why. Owner decision.
OWNER_CALL_MIN_SCHEMA = 9
OWNER_CALLS = ("authorization", "money", "credentials", "taste", "stuck", "wait")


def _option_text(opt):
    """One option's text, whether it was written as a plain string or as an
    object (`{"option": …, "tradeoff": …}`). Both shapes are accepted — refusing
    the object form would be pedantry, not a safety property."""
    if isinstance(opt, str):
        return opt.strip()
    if isinstance(opt, dict):
        head = str(opt.get("option") or opt.get("title") or "").strip()
        tail = str(opt.get("tradeoff") or opt.get("trade_off") or "").strip()
        return f"{head} — {tail}" if head and tail else head
    return ""


def _brief_option_violations(brief, v):
    """The options list and the recommendation that has to name one of them."""
    attempts = brief.get("attempts")
    if not isinstance(attempts, list) or not attempts or not all(
        isinstance(a, str) and a.strip() for a in attempts
    ):
        v.append("'decision_brief.attempts' must be a non-empty list of non-empty strings "
                 "saying WHAT YOU TRIED")


def _brief_findings_violations(brief, v):
    """The findings list: each entry needs a source and a takeaway."""
    options = brief.get("options")
    if not isinstance(options, list) or not options:
        v.append("'decision_brief.options' must be a list of 1-3 options")
    elif len(options) > MAX_BRIEF_OPTIONS:
        v.append(f"'decision_brief.options' has {len(options)} entries; at most "
                 f"{MAX_BRIEF_OPTIONS} are allowed — more than three is not a decision, "
                 "it is a menu")
    elif not all(_option_text(o) for o in options):
        v.append("every entry in 'decision_brief.options' must be a non-empty string "
                 "(or an object with a non-empty 'option')")


def _owner_call_violations(brief, required, v):
    """`owner_call` (one of OWNER_CALLS) and, for a wait, `wait_for`."""
    call = brief.get("owner_call")
    if call is None:
        if required:
            v.append("'decision_brief.owner_call' is required on a BLOCKED closeout: name "
                     f"why only the owner can answer, one of {list(OWNER_CALLS)}. If none "
                     "fits, the choice is yours: decide it, record why in 'notes', and "
                     "close DONE or PARTIAL instead of BLOCKED")
    elif call not in OWNER_CALLS:
        v.append(f"'decision_brief.owner_call' is {call!r}; it must be one of {list(OWNER_CALLS)}")
    elif call == "wait":
        w = brief.get("wait_for")
        if not isinstance(w, str) or not w.strip():
            v.append("'decision_brief.wait_for' must name the date or event the session "
                     "waits for when owner_call is 'wait'")


def decision_brief_violations(data, require_owner_call=False):
    """Schema check for `decision_brief` (RS-04).

    Shape::

        {"attempts": ["…", …],
         "findings": [{"source": "…", "takeaway": "…"}, …],
         "options":  ["…", …]  (1-3),
         "recommendation": "…"}

    REQUIRED when ``result == "BLOCKED"``; optional (but recommended) otherwise —
    a session parking on ``human_checkpoint_reason`` is handing a human a
    decision, and the same four things are what makes that decision answerable.

    This is what turns "stuck, please advise" into a report the operator can act
    on without re-deriving the session's whole afternoon. The refusal is a LOUD
    closeout error that reworks exactly like any malformed closeout — a blocked
    session with no brief has not finished reporting.
    """
    brief = data.get("decision_brief")
    blocked = data.get("result") == "BLOCKED"
    if brief is None:
        if not blocked:
            return []
        return [
            "result=BLOCKED requires a 'decision_brief' — a bare block is not a report. "
            "Add decision_brief {attempts: [what you tried], findings: [{source, takeaway}] "
            "from the stuck-protocol research pass, options: 1-3, recommendation: <one>}"
        ]
    if not isinstance(brief, dict):
        return [f"'decision_brief' must be an object, got {type(brief).__name__}"]

    v = []
    _brief_option_violations(brief, v)

    findings = brief.get("findings")
    if not isinstance(findings, list) or not findings:
        v.append("'decision_brief.findings' must be a non-empty list of "
                 "{source, takeaway} objects — what OUTSIDE sources say (the "
                 "stuck-protocol research pass); use [{'source': 'none', 'takeaway': "
                 "'searched X, nothing found'}] if the pass genuinely found nothing")
    else:
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                v.append(f"'decision_brief.findings[{i}]' must be an object with "
                         "'source' and 'takeaway'")
                continue
            for key in ("source", "takeaway"):
                if not isinstance(f.get(key), str) or not f[key].strip():
                    v.append(f"'decision_brief.findings[{i}].{key}' must be a non-empty string")

    _brief_findings_violations(brief, v)

    rec = brief.get("recommendation")
    if not isinstance(rec, str) or not rec.strip():
        v.append("'decision_brief.recommendation' must be a non-empty string naming "
                 "the option you would pick — an unrecommended brief pushes the work back")

    _owner_call_violations(brief, require_owner_call and blocked, v)

    allowed = {"attempts", "findings", "options", "recommendation", "owner_call", "wait_for"}
    extra = sorted(set(brief) - allowed)
    if extra:
        v.append(f"unknown key(s) in decision_brief: {extra} "
                 f"(allowed: {', '.join(sorted(allowed))})")
    return v


def format_decision_brief(brief):
    """Render a `decision_brief` as plain text for the HALT_NOTICE / checkpoint
    surfaces — options and recommendation visible to the operator WITHOUT opening
    `_closeouts/<sid>.json`. Returns "" when there is no usable brief."""
    if not isinstance(brief, dict):
        return ""
    lines = ["DECISION BRIEF"]
    attempts = [a for a in (brief.get("attempts") or []) if isinstance(a, str) and a.strip()]
    if attempts:
        lines.append("  Tried:")
        lines += [f"    - {a.strip()}" for a in attempts]
    findings = [f for f in (brief.get("findings") or []) if isinstance(f, dict)]
    if findings:
        lines.append("  Sources:")
        for f in findings:
            src = str(f.get("source") or "").strip() or "(unnamed source)"
            lines.append(f"    - {src}: {str(f.get('takeaway') or '').strip()}")
    options = [_option_text(o) for o in (brief.get("options") or [])]
    options = [o for o in options if o]
    if options:
        lines.append("  Options:")
        lines += [f"    {i}. {o}" for i, o in enumerate(options, 1)]
    rec = str(brief.get("recommendation") or "").strip()
    if rec:
        lines.append(f"  RECOMMENDATION: {rec}")
    call = str(brief.get("owner_call") or "").strip()
    if call:
        wait = str(brief.get("wait_for") or "").strip()
        lines.append(f"  OWNER CALL: {call}" + (f" (waits for: {wait})" if wait else ""))
    return "\n".join(lines) if len(lines) > 1 else ""
