"""A findings LEDGER per session, so a rework round VERIFIES fixes instead of
RE-SAMPLING the whole surface.

WHY, measured over 21h and two plans: 16 of 17 verify failures were
`llm-review-medium`, and the loop never converged. Each rework round handed the
reviewer the session's ENTIRE accumulated diff with no memory of the last round,
so round 2 raised findings on round-0 files that round 1 had simply not sampled.
SWE-PRBench (2026) measures frontier reviewers at 15-31% recall PER PASS on a
diff, falling as the diff grows -- a single review is a sample, and a loop that
re-samples a growing haystack has no termination property. `max_rework` was a
cutoff on that process, not a convergence criterion.

What converges (Codity / Calimero / daiv / review-agent, 2025-26): carry the
prior findings INTO the next review with stable ids; make the reviewer mark each
FIXED or OPEN; review the FIX DELTA as new code; tag anything else as found
OUTSIDE the delta. Then:

  every attempt  a finding blocks only at or above the SEVERITY FLOOR
                 (`BLOCKING_SEVERITY`, default high). Lower ones are RECORDED as
                 `noted`, printed and carried forward; they never spend a
                 dispatch. A prior that is still open blocks on the same rule.
  attempt >= 2   the reviewer is handed ONLY the fix delta (`review_focus`), so
                 a finding outside it cannot be raised at all.

MEASURED, across 28 plans / 153 verified sessions / 125 findings.
Classification alone did not terminate the loop, because the reviewer was still
handed the WHOLE session diff every round and that diff only ever grows: s02 ran
41 -> 47 files over nine rounds, s06 16 -> 32 over eight. 105 of 125 findings
were raised on round 2 or later, and 22 of those were fresh findings in code the
session never touched -- pure re-sample. 60 more were defects in the FIX code,
which is the other half of the loop: the fix is never smaller than the finding.
14 of the 25 sessions that entered rework ended at exactly their cap, i.e. by
attrition rather than by converging.

So the delta is no longer advice to the reviewer. It is the surface.

OPT-IN BY CONTEXT. With no plan dir + session the gate keeps its old rule (any
finding fails) -- this adds a memory, it never loosens a gate that has none.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import safe_json as sj
from latest_by_key import latest_then_filter
from llm_review_accept import (  # noqa: F401 -- re-exported: the ledger's public names
    _norm, _norm_summary, accept_note, accepted_match, accepted_path, accepted_paths, load_accepted)
from llm_review_prior_section import prompt_section  # noqa: F401 -- also re-exported

#: THE SEVERITY FLOOR. A finding at or above this blocks; anything below is
#: recorded `noted` -- visible, carried forward, never a reason to re-dispatch.
#: Applies on EVERY attempt, including the first: a low-severity finding on
#: round 1 starts exactly the same loop as one on round 5. Override per run with
#: `--blocking-severity` (the gate flag) when a session wants a stricter gate.
BLOCKING_SEVERITY = {"high", "critical", "blocker"}
#: Back-compat alias; `BLOCKING_SEVERITY` is the name that means what it says.
BLOCKING_OUTSIDE = BLOCKING_SEVERITY
#: mirrors llm_review_gate._JSON_FENCE; duplicated rather than imported back,
#: which would be a cycle.
_FENCE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.S)


def ledger_path(plan_dir, session):
    return Path(plan_dir) / "_verify_state" / f"{session}.findings.ndjson"


def fingerprint(f):
    """Stable id for a finding: file + normalised summary. LINE IS EXCLUDED --
    it drifts with every edit above it, which is how a line-anchored allowlist
    unpins itself (a lesson this repo has already paid for)."""
    return hashlib.sha1(
        f"{_norm(f.get('file', ''))}|{_norm_summary(f.get('summary', ''))}".encode()
    ).hexdigest()[:10]


def findings_array(result):
    """The reviewer's JSON findings array as dicts, or [] -- same fence rule as
    `classify`, but it returns the OBJECTS, which the ledger needs."""
    s = (result or "").strip()
    blocks = _FENCE.findall(s) or ([s] if s.startswith("[") else [])
    for raw in reversed(blocks):
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, list):
            return [f for f in parsed if isinstance(f, dict)]
    return []


def load(plan_dir, session):
    p = ledger_path(plan_dir, session)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        rec = sj.load_dict(ln)
        if rec is not None:
            out.append(rec)
    return out


def last_attempt(records):
    return max((r.get("attempt", 0) for r in records), default=0)


def last_surface(records):
    surf = [r for r in records if r.get("kind") == "surface"]
    return (surf[-1].get("hashes") or {}) if surf else {}


def last_rule(records):
    """The trimmed blocking rule the last surface record was reviewed under ('' = none)."""
    surf = [r for r in records if r.get("kind") == "surface"]
    return (surf[-1].get("rule") or "") if surf else ""


def surface_hashes(cwd, paths):
    """Content hash per surface file. Content, not mtime or git: the tree is
    uncommitted and a rework may touch a file without changing it."""
    out = {}
    for p in paths:
        try:
            out[p] = hashlib.sha256(Path(cwd, p).read_bytes()).hexdigest()[:16]
        except OSError:
            continue
    return out


def fix_delta(records, hashes_now):
    """Paths whose content changed since the previous attempt's surface, or are
    new to it. With no previous surface there is no delta -- every file is."""
    prev = last_surface(records)
    if not prev:
        return set(hashes_now)
    return {p for p, h in hashes_now.items() if prev.get(p) != h}


#: Severities the gate RECOGNISES. Anything outside this set -- including a
#: missing one -- blocks, because "the reviewer did not say" must never be the
#: cheapest way past the gate. Demoting an unlabelled finding to advisory would
#: make omission the winning move for a reviewer under a token budget.
KNOWN_SEVERITIES = {"blocker", "critical", "high", "medium", "low",
                    "info", "minor", "nit", "trivial"}


def blocks(severity, floor=None):
    """Is this finding blocking? The ONE place that decides. Fails CLOSED."""
    sev = str(severity or "").strip().lower()
    if sev not in KNOWN_SEVERITIES:
        return True
    return sev in (floor or BLOCKING_SEVERITY)


def rule_blocks(r):
    """A recorded finding the blocking rule tied to a rule number (or left unreadable):
    the severity floor never lowers it. Only a record written under a rule has the key."""
    return "rule" in r and str(r["rule"] or "").strip().lower() != "none"


def left_surface(p, inside, before):
    """A prior whose file is not in `inside` (this attempt's surface). A rule-blocked
    prior counts only when its file was in the `before` surface and LEFT it: a file the
    surface never held, or no file at all, cannot leave it, so it stays judged and
    blocking (a finding naming no surface file must never retire itself to a PASS)."""
    f = _norm(p.get("file") or "")
    return f not in inside and (not rule_blocks(p) or f in before)


def open_priors(records, floor=None):
    """Findings not yet marked fixed. The LATEST status per id wins.

    Filtered by the SEVERITY FLOOR, so a ledger written before the floor existed
    stops blocking on its recorded mediums instead of pinning a live session to
    a rule it was never judged under. They stay in the file and stay printed.
    """
    return _latest_findings(records, lambda r: r.get("status", "open") in ("open", "unverifiable")
                            and (blocks(r.get("severity"), floor) or rule_blocks(r)))


def latest_by_id(records):
    """Every finding ever recorded, latest record per id -- advisory ones too."""
    return {r["fid"]: r for r in _latest_findings(records)}


def _latest_findings(records, keep=None):
    rows = sorted((r for r in records if r.get("kind") == "finding"),
                  key=lambda r: r.get("attempt", 0))
    return latest_then_filter(rows, lambda r: r["fid"], keep)


def review_focus(ctx):
    """The paths to hand the reviewer, or None for "the whole surface".

    None on attempt 1 (there is no delta yet) and whenever the ledger is off.
    An EMPTY set is not None and not "everything": it means the re-dispatch
    changed no file in the surface, which the gate must refuse rather than
    review nothing and call it green.

    THE DELTA IS NOT THE WHOLE ANSWER. Every OPEN prior's file rides along even
    when the fix never touched it, because the reviewer is asked to mark each
    prior fixed or still-open, and a prior in a file it was never shown is a
    question about evidence nobody was given. Without this the gate would ask
    "did you fix b.py?" while handing over only a.py -- and the honest answer,
    `unverifiable`, does not block, so an unfixed HIGH finding would clear
    itself the moment the agent stopped touching its file.
    """
    # A changed blocking rule is a new question: the saved pass answered the old one.
    if ctx is None or ctx.attempt < 2 or ctx.rule != last_rule(ctx.records):
        return None
    delta = set(ctx.delta)
    if not delta:
        return delta
    return delta | {_norm(p.get("file") or "") for p in ctx.priors if p.get("file")}


class Context:
    """Everything the gate needs from the ledger for ONE run, computed once."""

    def __init__(self, plan_dir, session, cwd, surface_paths, floor=None, rule=""):
        self.plan_dir, self.session, self.rule = plan_dir, session, rule
        self.floor = floor or BLOCKING_SEVERITY
        self.records = load(plan_dir, session)
        self.attempt = last_attempt(self.records) + 1
        self.hashes = surface_hashes(cwd, surface_paths)
        # Present but not hashed (a directory, a dangling symlink): unseen content.
        self.unhashed = [p for p in surface_paths
                         if p not in self.hashes and os.path.lexists(Path(cwd, p))]
        self.delta = fix_delta(self.records, self.hashes) if self.attempt > 1 else set()
        self.priors = open_priors(self.records, self.floor)
        # A prior whose file left the surface (removed, reverted) is moot: retire it
        # so it neither blocks nor is listed to the reviewer (2026-11-10, s02).
        inside = {_norm(x) for x in surface_paths}
        before = {_norm(x) for x in last_surface(self.records)}
        self.retired = [p for p in self.priors if inside and left_surface(p, inside, before)]
        self.priors = [p for p in self.priors if p not in self.retired]
        self.accepted = load_accepted(plan_dir, session)
        # A prior the owner accepted does not block an UNCHANGED surface. It did:
        # the accept list was applied only when a reviewer ran, and an unchanged
        # surface never runs one, so the owner's exit could not be taken (s04).
        self.cleared = [{**p, "status": "accepted", "accept_reason": hit["reason"]} for p in self.priors
                        if (hit := accepted_match(self.accepted, p.get("file"), p.get("summary"),
                                                  p.get("severity")))]
        gone = {p["fid"] for p in self.cleared}
        self.blocking = [p for p in self.priors if p["fid"] not in gone]
        self.accept_note = accept_note(plan_dir, session, self.accepted)
        unchanged = len([p for p in self.hashes if p not in self.delta])
        self.section = prompt_section(self.attempt, self.priors, self.delta, unchanged)


def context(plan_dir, session, cwd, surface_paths, floor=None, rule=""):
    """None when the gate has no plan context -- the ledger is opt-in."""
    if not plan_dir or not session:
        return None
    return Context(plan_dir, session, cwd, surface_paths, floor, rule)


def parse_floor(raw):
    """The severities that block, or None for the default floor."""
    vals = {v.strip().lower() for v in (raw or "").replace(":", ",").split(",") if v.strip()}
    return vals or None


def reviewed_unchanged(ctx):
    """The surface is EXACTLY the last reviewed one and no blocking prior is open.
    Only `record` writes an attempt-numbered surface, after a parsed, attested
    review, so those bytes WERE reviewed. Same KEYS: a deleted file leaves every
    other hash equal. Every present path HASHED: a skipped directory can change
    with every hash still equal."""
    return (ctx is not None and ctx.attempt > 1 and not ctx.blocking
            and bool(ctx.hashes) and not ctx.unhashed
            and ctx.hashes == last_surface(ctx.records))


def narrow_to_delta(ctx, changed, new_files):
    """-> (focus, changed, new_files, note). THE REWORK SURFACE IS THE FIX.

    `focus` is None when nothing is narrowed (attempt 1, or no ledger), the
    empty set when the re-dispatch changed no file in the surface -- which the
    caller must treat as INDETERMINATE, because reviewing zero files and
    answering `[]` is the silent green this module exists to refuse (or as
    FINDINGS on open blocking priors, or PASS when `reviewed_unchanged`) -- and
    otherwise the delta paths that ARE the diff handed over.
    """
    focus = review_focus(ctx)
    if focus is None:
        return None, changed, new_files, ""
    if not focus and ctx.blocking:
        # AN UNCHANGED SURFACE KEEPS ITS VERDICT. The last attempt left blocking
        # findings open and nothing in the surface moved, so they are still open:
        # this is a FAIL, not "nothing to review". Scored INDETERMINATE it went
        # down verify's transport-retry path and halted the plan as `blocked`,
        # which plan_mutate treats as in flight -- no retire, no amend, and a
        # resume only repeated the indeterminate (g1-integration).
        return set(), changed, new_files, (
            f"llm-review: FAILED — attempt {ctx.attempt} and no file in the reviewed "
            f"surface changed since attempt {ctx.attempt - 1}, which left "
            f"{len(ctx.blocking)} blocking finding(s) open. They are still open:\n"
            + "\n".join(f"  - {p['fid']}  {p.get('file')}:{p.get('line')}  "
                        f"[{p.get('severity')}]  {p.get('summary')}" for p in ctx.blocking))
    if not focus and reviewed_unchanged(ctx):
        # THE PASS TWIN: a land re-gate after main moved hit INDETERMINATE here on
        # every retry, and an argv gate cannot be waived.
        return set(), changed, new_files, (
            f"llm-review: PASSED — attempt {ctx.attempt} and the reviewed surface is "
            f"byte-identical to attempt {ctx.attempt - 1}, which was reviewed and left "
            f"no blocking finding open. The verdict stands; nothing was re-sampled."
            + accepted_block(ctx.cleared))
    if not focus:
        return set(), changed, new_files, (
            f"llm-review: INDETERMINATE — attempt {ctx.attempt} and no file in the "
            f"reviewed surface changed since attempt {ctx.attempt - 1}. There is nothing "
            f"new to review, so this is not a pass. Re-dispatch the session with the "
            f"feedback attached, or narrow the gate.")
    before = len(changed) + len(new_files)
    changed = [c for c in changed if c in focus]
    new_files = tuple(n for n in new_files if n in focus)
    return focus, changed, new_files, (
        f"[llm-review-gate] attempt {ctx.attempt}: surface NARROWED to the fix delta — "
        f"{len(changed) + len(new_files)} of {before} file(s). Everything unchanged since "
        f"the last attempt was reviewed then and is not re-sampled now.")


def _open_or_accepted(ctx, p, counts):
    """An open prior the owner declared is accepted: its old-id copy clears too (s07)."""
    hit = accepted_match(getattr(ctx, "accepted", []), p.get("file"), p.get("summary"), p.get("severity"))
    counts["accepted" if hit else "prior_open"] += 1
    return {"status": "accepted", "accept_reason": hit["reason"]} if hit else {"status": "open"}


def rule_noted(f):
    """True only for an explicit `"rule": "none"`. A number, garbage, null or a missing
    field is False, so under a blocking rule the finding blocks (fails closed)."""
    v = f.get("rule")
    return isinstance(v, str) and v.strip().lower() == "none"


def not_blocking_block(parsed):
    """The findings a blocking rule set aside, printed so none is lost from the output."""
    rows = [f for f in parsed if rule_noted(f)]
    return "".join(f"\n[llm-review-gate] not blocking: {f.get('file')}:{f.get('line')} "
                   f"[{f.get('severity')}] {f.get('summary')}" for f in rows)


def judge(ctx, parsed, floor=None, rule_on=False):
    """-> (fail, counts, records). With no ctx: the old rule, any finding fails
    (under a blocking rule: any finding the reviewer did not mark `none`).
    `rule_on`: the rule number decides, not the severity floor."""
    if ctx is None:
        return any(not (rule_on and rule_noted(f)) for f in parsed), {}, []
    a = ctx.attempt
    prior_by_id = {p["fid"]: p for p in ctx.priors}
    known = latest_by_id(getattr(ctx, "records", []) or [])
    floor = floor or getattr(ctx, "floor", None) or BLOCKING_SEVERITY
    counts = dict(attempt=a, prior_fixed=0, prior_open=0, prior_unverifiable=0,
                  new_in_delta=0, new_outside=0, noted=0, accepted=0)
    recs, seen = [], set()
    for f in parsed:
        pid = f.get("prior_id")
        # A prior the FLOOR demoted (or the owner accepted): carried, never re-blocked.
        # NOT a fixed HIGH echoed by id, nor one re-raised past the floor.
        k = known.get(pid) if pid not in prior_by_id else None
        if k and (k.get("status") == "accepted" or not (
                blocks(k.get("severity"), floor) or blocks(f.get("severity") or k.get("severity"), floor)
                or rule_blocks(k) or (rule_on and not rule_noted(f)))):
            counts["noted"] += 1
            recs.append({**k, "kind": "finding", "attempt": a, "status": "noted",
                         **({"rule": f.get("rule")} if rule_on else {}), "evidence": f.get("summary")})
            continue
        if pid and pid in prior_by_id:
            fixed = str(f.get("prior", "")).lower() == "fixed"
            seen.add(pid)
            counts["prior_fixed"] += fixed
            # under a rule the reviewer's CURRENT tag decides, never the one recorded before
            noted = not fixed and rule_on and rule_noted(f)
            counts["noted"] += noted
            st = ({"status": "fixed"} if fixed else {"status": "noted"} if noted
                  else _open_or_accepted(ctx, prior_by_id[pid], counts))
            recs.append({**prior_by_id[pid], "kind": "finding", "attempt": a, **st,
                         **({"rule": f.get("rule")} if rule_on else {}), "evidence": f.get("summary")})
            continue
        path = _norm(f.get("file", ""))
        sev = str(f.get("severity", "")).lower()
        # LOCAL GROUND TRUTH WINS, IN BOTH DIRECTIONS. `ctx.delta` is computed
        # here from content hashes of this attempt's surface against the last
        # one; the reviewer cannot know it better than we do. Trusting its
        # self-reported tag broke both ways: `"new_in": "outside"` on a real defect in
        # the fixed file DEMOTED it to `noted`; `"delta"` on anything FORCED a block.
        # The tag is bookkeeping now, consulted only for a path outside the delta.
        new_in = ("first" if a == 1 else
                  ("delta" if path in ctx.delta else f.get("new_in") or "outside"))
        # THE FLOOR IS THE WHOLE RULE. Severity decides blocking; location does not.
        status = ("noted" if rule_noted(f) else "open") if rule_on else (
            "open" if blocks(sev, floor) else "noted")
        # ... unless the operator declared this one, by file and phrase, with a
        # reason. Only a BLOCKING finding is worth accepting, and the reason rides
        # in the ledger beside it so the clearance is never anonymous.
        hit = accepted_match(getattr(ctx, "accepted", None),
                             f.get("file"), f.get("summary"), sev) if status == "open" else None
        if hit:
            status = "accepted"
            counts["accepted"] += 1
        counts["new_in_delta" if new_in in ("delta", "first") else "new_outside"] += 1
        counts["noted"] += status == "noted"
        recs.append({"kind": "finding", "attempt": a, "fid": fingerprint(f),
                     "file": f.get("file"), "line": f.get("line"), "severity": f.get("severity"),
                     "summary": f.get("summary"), "status": status, "new_in": new_in,
                     **({"rule": f.get("rule")} if rule_on else {}),
                     **({"accept_reason": hit["reason"]} if hit else {})})
    surface = {_norm(x) for x in (ctx.hashes or ())}
    before = {_norm(x) for x in last_surface(getattr(ctx, "records", []) or [])}
    for pid, p in prior_by_id.items():
        if pid in seen:
            continue
        # A prior in a file this attempt never SHOWED the reviewer cannot be marked
        # fixed, and its silence is not evidence either way. Counting it OPEN makes
        # the gate unpassable for the rest of the session -- which is exactly what
        # narrowing `review_scope` does to priors raised before the narrowing
        # (found by the example-isolation-plan session). Recorded,
        # counted and printed, never silently dropped.
        if surface and left_surface(p, surface, before):
            counts["prior_unverifiable"] += 1
            recs.append({**p, "kind": "finding", "attempt": a, "status": "unverifiable",
                         "evidence": "outside this attempt's reviewed surface; not judged"})
            continue
        st = _open_or_accepted(ctx, p, counts)   # omission IS not a fix, in surface
        recs.append({**p, "kind": "finding", "attempt": a, **st, "evidence": "not addressed "
                     f"by the reviewer; treated as {st['status'].upper()}"})
    for p in getattr(ctx, "retired", ()):
        recs.append({**p, "kind": "finding", "attempt": a, "status": "retired",
                     "evidence": "its file left the reviewed surface; kept in the ledger, not judged"})
    counts["accept_note"] = getattr(ctx, "accept_note", "")
    # One id, one verdict per round: two findings with the same file and words share a fid, and the
    # later record wins on read, so a `noted` twin would hide the open one. The open one stays.
    opened = {r["fid"] for r in recs if r["status"] == "open"}
    recs = [r for r in recs if r["status"] == "open" or r["fid"] not in opened]
    return any(r["status"] == "open" for r in recs), counts, recs


def record(ctx, recs):
    p = ledger_path(ctx.plan_dir, ctx.session)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
        fh.write(json.dumps({"kind": "surface", "attempt": ctx.attempt, "hashes": ctx.hashes,
                             **({"rule": ctx.rule} if ctx.rule else {})}) + "\n")


def accepted_block(recs):
    """The audit trail for accepted findings, appended to the convergence line:
    what was cleared, and the reason -- or '' when nothing was. A clearance is
    never anonymous, so the gate prints this every time it steps over a finding."""
    return "".join(
        f"\n[llm-review-gate] ACCEPTED {r.get('file')}:{r.get('line')} "
        f"[{r.get('severity')}] {r.get('summary')}"
        f"\n    reason: {r.get('accept_reason')}"
        for r in recs if r.get("status") == "accepted")


def convergence_line(counts):
    """One greppable line. rework.py reads it to word a halt honestly."""
    c = counts
    line = (f"[llm-review-gate] convergence: attempt={c['attempt']} prior_fixed={c['prior_fixed']} "
            f"prior_open={c['prior_open']} new_in_delta={c['new_in_delta']} "
            f"new_outside={c['new_outside']} noted={c['noted']}")
    if c.get("accept_note"):
        line += " " + c["accept_note"]
    if c.get("accepted"):
        line += (f" accepted={c['accepted']} (declared in the accept file by file and "
                 "phrase, each with a reason -- recorded and printed, not blocking)")
    if c.get("prior_unverifiable"):
        line += (f" prior_unverifiable={c['prior_unverifiable']} (priors outside this "
                 "attempt's surface -- carried, not judged, not blocking)")
    if c["attempt"] >= 2 and c["new_outside"] and not c["prior_open"] and not c["new_in_delta"]:
        line += (" -- every finding this round is in code UNTOUCHED since the last attempt: "
                 "that is the reviewer's sample, not the agent's regression. The surface is too "
                 "large for one pass to cover; split the session rather than redispatching it.")
    if c.get("noted"):
        line += (f" -- {c['noted']} finding(s) below the severity floor recorded as advisory; "
                 "they are in the ledger and do not re-dispatch the session.")
    return line
