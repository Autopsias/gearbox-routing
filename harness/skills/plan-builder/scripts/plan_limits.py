"""Measured limits a plan author should know about -- and why.

Split out of build_plan.py (which sits at its file-size baseline).
Both numbers here come from measurements, and both exist because an LLM review
pass is a SAMPLE of a diff, not an audit: SWE-PRBench (2026) measures frontier
reviewers at 15-31% recall per pass, falling as the diff grows. A session that is
too wide will be reviewed as a sample and may never converge; a rework loop that
is too short cannot verify its own fix. The full numbers and the findings ledger
that makes rework verify instead of re-sample: plan-execute/references/
verify-gates.md, "Sizing the surface".
"""
import re
import sys

#: `verify.max_rework` default. 1 -> 2, measured: at 1 the loop is
#: one review, one fix, one re-review -- nothing is left for the reviewer to
#: verify the fix. Over 21h / two plans, 16 of 17 verify failures were one LLM
#: review gate, and sessions halted at 1-2 on the reviewer's second SAMPLE.
DEFAULT_MAX_REWORK = 2

#: `verify.max_rework` CEILING -- the largest value `build_plan.validate_spec`
#: accepts. 5 -> 6, then 6 -> 7 when routing SSOT
#: v21 (96570b6) armed `opus@xhigh` as an escalation rung and lengthened every
#: ladder that passes through opus by one. Re-measured against the live SSOT
#: with `resolve_route.escalate`: the longest escalation ladder any legal
#: starting rung has is
#:     sonnet@medium -> sonnet@high -> opus@high  -> opus@xhigh
#:                   -> fable@medium -> fable@high -> fable@xhigh
#: which needs SEVEN rework rounds (the mandatory same-rung retry, then six
#: climbs). Rungs to apex by starting pair: opus@high 5, opus@medium 6,
#: sonnet@high 6, sonnet@medium 7. At a cap below that, a sonnet/medium gated
#: session could not be AUTHORED to reach its own apex -- and plan-harden's
#: `max-rework-cannot-reach-apex` lint could only tell the author to clamp to a
#: number that still cannot get there, which reads as compliance and is not.
MAX_REWORK_CEILING = 7

#: Declared WRITES per session -- files, not prompt chars -- measured
#: over every plan on this machine: 74 sessions / 18 plans, median 3, p75 5,
#: p90 6, max 13. Review burden is set by what a session TOUCHES (patch size +
#: files predict it at AUC 0.957 on 33k agent-authored PRs). The 10-file session
#: on the repo-health plan took seven review rounds; the 3-file ones took one or
#: two. Advisory: a wide session can be right, with its eyes open.
SESSION_TOUCHES_P90 = 6


def review_scope(session):
    """The path prefixes a session's review surface is bounded to, normalised;
    [] when undeclared (= the whole tree). /plan-execute hands it to the
    llm-review-* gates as PLAN_EXECUTE_REVIEW_SCOPE so a concurrent plan's files
    in the same checkout cannot raise findings against this session. Docs:
    references/schemas.md `review_scope`."""
    raw = (session or {}).get("review_scope") or []
    if isinstance(raw, str):
        raw = [raw]
    # a leading `./` PREFIX, never lstrip("./"): that strips the character set and
    # turns a root dotfile like `.complexity-exceptions` into a path that matches
    # nothing -- found in the first manifest this emitted.
    return [re.sub(r"^(\./)+", "", str(x).strip()) for x in raw if str(x).strip()]


def wide_sessions(declared_writes, limit=SESSION_TOUCHES_P90):
    """`[(session_id, n_files)]` over the limit. `declared_writes` is
    build_plan._declared_writes(spec): {sid: [paths] | None}; None (unparseable)
    is skipped -- it is already treated as conflicting with everything."""
    return sorted(((sid, len(paths)) for sid, paths in (declared_writes or {}).items()
                   if paths is not None and len(paths) > limit), key=lambda x: -x[1])




#: Session-id tokens named in a plan's PROSE. That is the dominant defect class of
#: a hardening pass in one line: a change applied in one place while the text it
#: replaces survives in another. Structural validation cannot see it, because
#: prose is not a reference the schema resolves.
#:
#: Measured over every plan on this machine (29 plans), counted by HIT
#: and not by plan -- the first pass counted plans, called it 5 of 29, and hid a
#: 33% precision rate inside a number that looked fine. Raw: 12 hits, of which
#: only 4 were real. The 8 others were a path segment (3), a split annotation
#: naming the old id in order to document the split (4), and a quoted notice
#: copied from another plan (1). `_not_a_reference` suppresses all three shapes
#: plus a deliberate negation, and the sweep now runs 4 hits / 4 real over the
#: same corpus. Advisory, never a refusal.
_SESSION_REF = re.compile(r"\bs\d{2}[a-z]?\b")
#: A negation immediately before the id ("no s04b exists", "s07 no longer runs").
_NEGATED = re.compile(r"\b(no|not|never|nor|without|neither)\b[^.!?]{0,40}$", re.I)

#: Lines inside a heredoc in a session prompt. Measured: across every
#: plan on this machine NO session prompt carries a heredoc at all, and the one
#: that ever did -- a 139-line arming program -- sat at 8, then 8, then 6 open
#: review findings across three hardening rounds, and closed all six the moment
#: it moved into a script with tests. A review pass READS prose; it does not RUN
#: code, so an embedded program is the one part of a prompt review cannot close.
#: 10 is far under the only instance ever observed and far over an illustration.
EMBEDDED_PROGRAM_LINES = 10

_PROSE_KEYS = ("prompt", "deliverable", "human_summary", "why", "why_model", "notes", "title")


def _prose(spec):
    """(owner_id, field, text) for every prose field a stale id can hide in."""
    for s in spec.get("sessions") or []:
        sid = s.get("id")
        for k in _PROSE_KEYS:
            if isinstance(s.get(k), str):
                yield sid, k, s[k]
        for k, v in ((s.get("dispatch") or {}).get("checkpoint") or {}).items():
            if isinstance(v, str):
                yield sid, f"dispatch.checkpoint.{k}", v
        for chk in (s.get("verify") or {}).get("checks") or []:
            for k, v in (chk or {}).items():
                if isinstance(v, str):
                    yield sid, f"verify.checks.{k}", v
    for it in spec.get("items") or []:
        for k in _PROSE_KEYS:
            if isinstance(it.get(k), str):
                yield it.get("id"), k, it[k]


def _not_a_reference(text, m, ref, live):
    """The three shapes that LOOK like a stale session id and are not. Measured
    2026-08-21 over all 29 plans on this machine: without these, 8 of 12 hits
    were noise (paths 3, split annotations 4, a quoted notice 1), and a check at 33% precision teaches its reader to skip it."""
    before, after = text[max(0, m.start() - 120):m.start()], text[m.end():m.end() + 120]
    # 1. a PATH SEGMENT -- `_evidence/s04/wave1-proof-pack.md` names a directory,
    #    very often in ANOTHER plan's evidence tree. 3 of the 12.
    if before.endswith("/") or after.startswith("/"):
        return True
    # 1b. a HYPHENATED COMPOUND -- the id is a SUFFIX inside a longer token, as in
    #    the tag `adv-consensus-s07-split`. Only a hyphen BEFORE disqualifies:
    #    `the s03-defined operational rule` is a real reference and must survive.
    if before.endswith("-"):
        return True
    # 2. the SPLIT ANNOTATION -- `s07a half of the s07 -> s07a/s07b split` names
    #    the old id precisely to document that it is gone. If a LIVE session id
    #    extends this one and sits within the same window, the text is explaining
    #    the split, not following it. 4 of the 8.
    if any(x != ref and x.startswith(ref) and x in (before + after) for x in live):
        return True
    # 3. a QUOTATION -- an odd number of quote marks between the start of the line
    #    and the match means the id sits inside quoted text, usually a notice
    #    copied from elsewhere. 1 of the 8.
    line = text[:m.start()]
    if (line[line.rfind("\n") + 1:].count('"') % 2) == 1:
        return True
    # 4. a sentence that explicitly NEGATES the id ("no s04b exists to own it").
    return bool(_NEGATED.search(before[-60:]))


def stale_session_refs(spec):
    """`[(owner_id, field, referenced_id)]` -- prose naming a session that is not
    in the plan. See `_not_a_reference` for what is deliberately skipped.

    KNOWN GAP: `plan_mutate.retire_session` KEEPS the id in `sessions` (status
    RETIRED), so prose pointing at a retired session is stale in meaning and
    invisible here -- only a session that leaves the spec entirely is caught. No
    plan on this machine carries a retired session today (measured),
    so this is a gap on paper, not an observed miss."""
    live = {s.get("id") for s in spec.get("sessions") or []}
    out = []
    for owner, field, text in _prose(spec):
        for m in _SESSION_REF.finditer(text):
            ref = m.group(0)
            if ref in live or ref == owner:
                continue
            if _not_a_reference(text, m, ref, live):
                continue
            if (owner, field, ref) not in out:
                out.append((owner, field, ref))
    return out


def stale_item_refs(spec):
    r"""The same class in the ITEM half of a plan's vocabulary: `[(owner, field,
    referenced_id)]` for prose naming `<prefix>-NN` where the plan's own items use
    that prefix and no item has that id. Deriving the prefixes from the plan
    itself is what keeps ordinary English out -- an untethered `[a-z]+-\d+` match
    reads "top-10" and "task-10" as ids (measured: 2 of 3 hits were exactly that).
    Measured: one plan references a dropped `cal-01` five times while
    carrying only `cal-02` and `cal-03`."""
    live = {i.get("id") for i in spec.get("items") or []}
    prefixes = {i.rsplit("-", 1)[0] for i in live if re.match(r"^[a-z0-9]+-\d+$", i or "")}
    if not prefixes:
        return []
    pat = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, prefixes))) + r")-\d{1,3}\b")
    out = []
    for owner, field, text in _prose(spec):
        for m in pat.finditer(text):
            ref = m.group(0)
            if ref in live or ref == owner:
                continue
            if _not_a_reference(text, m, ref, live):
                continue
            if (owner, field, ref) not in out:
                out.append((owner, field, ref))
    return out


def embedded_programs(spec, limit=EMBEDDED_PROGRAM_LINES):
    """`[(session_id, heredoc_tag, n_lines)]` for every heredoc in a session
    prompt at or over `limit` lines -- a program a builder will run, shipped
    inside text a reviewer only reads."""
    opener = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
    out = []
    for s in spec.get("sessions") or []:
        lines = (s.get("prompt") or "").split("\n")
        i = 0
        while i < len(lines):
            m = opener.search(lines[i])
            if m:
                tag, j = m.group(2), i + 1
                while j < len(lines) and lines[j].strip() != tag:
                    j += 1
                if j - i - 1 >= limit:
                    out.append((s.get("id"), tag, j - i - 1))
                i = j
            i += 1
    return out


def plan_risk_warnings(spec, declared_writes=None):
    """Every advisory this module knows about, as plain strings. `build_plan`
    prints them at build time; `plan_mutate` folds them into a mutation's
    `validation_warnings`, which is the half that matters -- a mid-flight
    add/amend/retire is exactly when a sibling's prose goes stale."""
    msgs = []
    wide = wide_sessions(declared_writes) if declared_writes else []
    if wide:
        msgs.append(
            f"WARNING: {len(wide)} session(s) declare writes across more than "
            f"{SESSION_TOUCHES_P90} files (the p90 of every plan measured on this machine): "
            + ", ".join(f"{sid} ({n} files)" for sid, n in wide)
            + ". One LLM review pass samples a surface that size at 15-31% recall and falls "
            "further as it grows, so rework rounds will keep finding NEW things in untouched "
            "code -- the sample, not regressions -- and the session may not converge. Consider "
            "splitting at an item boundary. Advisory: a deliberately wide session is fine, with "
            "its eyes open."
        )
    stale = stale_session_refs(spec) + stale_item_refs(spec)
    if stale:
        msgs.append(
            f"WARNING: {len(stale)} prose reference(s) name a session or item this plan does "
            "not have: "
            + ", ".join(f"{o}.{f} -> {r}" for o, f, r in stale)
            + ". A session that was split, or an item that was dropped or renumbered, leaves "
            "its old id behind in a sibling's text -- where no schema check can see it, because "
            "prose is not a reference the schema resolves, and the next builder follows it. Fix "
            "the prose, or rephrase so the id is not named. Advisory."
        )
    progs = embedded_programs(spec)
    if progs:
        msgs.append(
            f"WARNING: {len(progs)} session prompt(s) embed a program of "
            f"{EMBEDDED_PROGRAM_LINES}+ lines: "
            + ", ".join(f"{sid} (<<{tag}, {n} lines)" for sid, tag, n in progs)
            + ". A review pass reads prose and cannot run code, so an embedded program is the "
            "one part of a prompt hardening cannot close -- measured, one stayed at "
            "6-8 open findings for three rounds and closed entirely once it moved into a script "
            "with tests. Have the session BUILD the script and run it, or point it at one that "
            "already exists. Advisory."
        )
    return msgs


def warn_plan_risks(spec, declared_writes=None, out=None):
    """Print `plan_risk_warnings`; returns them. build_plan's one call site."""
    msgs = plan_risk_warnings(spec, declared_writes)
    for m in msgs:
        print(m, file=out or sys.stderr)
    return msgs


def warn_wide_sessions(declared_writes, out=None):
    """Back-compat shim -- the message itself now lives once, in
    `plan_risk_warnings`. Kept because `test_escalation`'s refusal differential
    loads the COMMITTED `build_plan` against the WORKING `plan_limits`, so a
    symbol the previous commit calls by name must survive one commit. Removing
    it makes every plan look refused at HEAD and the differential goes vacuous."""
    for m in plan_risk_warnings({}, declared_writes):
        print(m, file=out or sys.stderr)
    return wide_sessions(declared_writes)
