"""A findings LEDGER per session, so a rework round VERIFIES fixes instead of
RE-SAMPLING the whole surface.

WHY, measured 2026-08-20 over 21h and two plans: 16 of 17 verify failures were
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

MEASURED 2026-08-22, across 28 plans / 153 verified sessions / 125 findings.
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


def _norm(path):
    """Strip leading `./` only. NOT `lstrip("./")` -- that strips a character SET,
    so a root dotfile like `.complexity-exceptions` came back as
    `complexity-exceptions`: it never matched the delta, so a real finding on it
    was classed `outside` and demoted to non-blocking `noted` (2026-08-20, the
    fifth instance of this same call in this repo)."""
    return re.sub(r"^(\./)+", "", str(path).strip())


def _norm_summary(text):
    """A summary reduced to comparable words: lowercase, letters and spaces only,
    single-spaced. The fingerprint and the accept list both read a summary through
    this, so they agree about what two wordings have in common."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", str(text).lower())).strip()


def fingerprint(f):
    """Stable id for a finding: file + normalised summary. LINE IS EXCLUDED --
    it drifts with every edit above it, which is how a line-anchored allowlist
    unpins itself (a lesson this repo has already paid for)."""
    return hashlib.sha1(
        f"{_norm(f.get('file', ''))}|{_norm_summary(f.get('summary', ''))}".encode()
    ).hexdigest()[:10]


def accepted_path(plan_dir, session):
    return Path(plan_dir) / "_verify_state" / f"{session}.accepted.json"


def load_accepted(plan_dir, session):
    """Findings the operator has declared this session does not have to fix.

    OPT-IN and FAIL-CLOSED. A missing file, unreadable JSON, or a malformed entry
    yields NO accepts -- never a blanket one. An entry needs a `file`, a non-empty
    `contains` phrase and a `reason`; an EMPTY phrase would match every finding in
    the file, so it is dropped rather than honoured.

    KEYED ON A PHRASE, NEVER ON THE FINDING ID. The id hashes the whole summary
    and the reviewer rewords its summary between attempts, so one deferred gap
    arrived under two different ids on attempts 4 and 5 (2026-09-08). A phrase
    that survives the rewording is the only key that keeps matching.

    This CANNOT clear a real regression by itself: it names one file and one
    phrase, the reason is recorded beside every finding it clears, and the
    convergence line counts what it cleared.
    """
    try:
        raw = json.loads(accepted_path(plan_dir, session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = (raw.get("accepted") or []) if isinstance(raw, dict) else []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        phrase = _norm_summary(e.get("contains", ""))
        if not (e.get("file") and phrase and str(e.get("reason", "")).strip()):
            continue
        out.append({"file": _norm(e["file"]), "contains": phrase,
                    "severity": str(e.get("severity", "")).strip().lower(),
                    "reason": str(e["reason"]).strip()})
    return out


def accepted_match(accepted, file, summary, severity):
    """The accept entry covering this finding, or None. The file must match and the
    phrase must appear in the summary; a `severity` on the entry pins that too."""
    path, summ = _norm(file or ""), _norm_summary(summary)
    sev = str(severity or "").strip().lower()
    for e in accepted or []:
        if e["file"] == path and e["contains"] in summ and \
                (not e["severity"] or e["severity"] == sev):
            return e
    return None


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
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def last_attempt(records):
    return max((r.get("attempt", 0) for r in records), default=0)


def last_surface(records):
    surf = [r for r in records if r.get("kind") == "surface"]
    return (surf[-1].get("hashes") or {}) if surf else {}


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


def open_priors(records, floor=None):
    """Findings not yet marked fixed. The LATEST status per id wins.

    Filtered by the SEVERITY FLOOR, so a ledger written before the floor existed
    stops blocking on its recorded mediums instead of pinning a live session to
    a rule it was never judged under. They stay in the file and stay printed.
    """
    by, status = {}, {}
    for r in sorted((r for r in records if r.get("kind") == "finding"),
                    key=lambda r: r.get("attempt", 0)):
        by[r["fid"]] = r
        status[r["fid"]] = r.get("status", "open")
    return [by[f] for f, s in status.items()
            if s in ("open", "unverifiable") and blocks(by[f].get("severity"), floor)]


def latest_by_id(records):
    """Every finding ever recorded, latest record per id -- advisory ones too."""
    out = {}
    for r in sorted((r for r in records if r.get("kind") == "finding"),
                    key=lambda r: r.get("attempt", 0)):
        out[r["fid"]] = r
    return out


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
    if ctx is None or ctx.attempt < 2:
        return None
    delta = set(ctx.delta)
    if not delta:
        return delta
    return delta | {_norm(p.get("file") or "") for p in ctx.priors if p.get("file")}


def prompt_section(attempt, priors, delta, unchanged_n):
    """What the reviewer is told about the previous attempt. Empty on the first."""
    if attempt <= 1:
        return ""
    lines = [f"===PRIOR REVIEW (attempt {attempt - 1}) -- VERIFY, DO NOT RE-DISCOVER==="]
    if priors:
        lines += [
            "These findings were raised on the previous attempt. For EACH ONE, emit exactly "
            "one object in the findings array carrying `\"prior_id\": \"<id>\"` and "
            "`\"prior\": \"fixed\"` or `\"prior\": \"open\"`, with one line of evidence in "
            "`summary`. A prior you OMIT is treated as OPEN -- silence is not a fix.",
        ] + [f"  - {p['fid']}  {p.get('file')}:{p.get('line')}  [{p.get('severity')}]  "
             f"{p.get('summary')}" for p in priors]
    else:
        lines.append("No prior finding is open.")
    lines.append("FILES CHANGED SINCE THAT ATTEMPT -- the fix delta. Review these as NEW code; "
                 "a fix is new code and can carry a fresh defect:")
    lines += [f"  - {p}" for p in sorted(delta)] or ["  (none)"]
    lines.append(f"FILES UNCHANGED SINCE THAT ATTEMPT: {unchanged_n}. You MAY report a new "
                 "finding in one; tag it `\"new_in\": \"outside\"`. The gate records those as a "
                 "convergence signal, blocks only on high severity, and carries the rest forward.")
    lines.append("Every NEW finding (no prior_id) carries `\"new_in\": \"delta\"` or "
                 "`\"new_in\": \"outside\"`.")
    lines.append("===END PRIOR REVIEW===")
    return "\n".join(lines)


class Context:
    """Everything the gate needs from the ledger for ONE run, computed once."""

    def __init__(self, plan_dir, session, cwd, surface_paths, floor=None):
        self.plan_dir, self.session = plan_dir, session
        self.floor = floor or BLOCKING_SEVERITY
        self.records = load(plan_dir, session)
        self.attempt = last_attempt(self.records) + 1
        self.hashes = surface_hashes(cwd, surface_paths)
        # Present but not hashed (a directory, a dangling symlink): unseen content.
        self.unhashed = [p for p in surface_paths
                         if p not in self.hashes and os.path.lexists(Path(cwd, p))]
        self.delta = fix_delta(self.records, self.hashes) if self.attempt > 1 else set()
        self.priors = open_priors(self.records, self.floor)
        self.accepted = load_accepted(plan_dir, session)
        unchanged = len([p for p in self.hashes if p not in self.delta])
        self.section = prompt_section(self.attempt, self.priors, self.delta, unchanged)


def context(plan_dir, session, cwd, surface_paths, floor=None):
    """None when the gate has no plan context -- the ledger is opt-in."""
    if not plan_dir or not session:
        return None
    return Context(plan_dir, session, cwd, surface_paths, floor)


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
    return (ctx is not None and ctx.attempt > 1 and not ctx.priors
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
    if not focus and ctx.priors:
        # AN UNCHANGED SURFACE KEEPS ITS VERDICT. The last attempt left blocking
        # findings open and nothing in the surface moved, so they are still open:
        # this is a FAIL, not "nothing to review". Scored INDETERMINATE it went
        # down verify's transport-retry path and halted the plan as `blocked`,
        # which plan_mutate treats as in flight -- no retire, no amend, and a
        # resume only repeated the indeterminate (g1-integration, 2026-10-02).
        return set(), changed, new_files, (
            f"llm-review: FAILED — attempt {ctx.attempt} and no file in the reviewed "
            f"surface changed since attempt {ctx.attempt - 1}, which left "
            f"{len(ctx.priors)} blocking finding(s) open. They are still open:\n"
            + "\n".join(f"  - {p['fid']}  {p.get('file')}:{p.get('line')}  "
                        f"[{p.get('severity')}]  {p.get('summary')}" for p in ctx.priors))
    if not focus and reviewed_unchanged(ctx):
        # THE PASS TWIN: a land re-gate after main moved hit INDETERMINATE here on
        # every retry, and an argv gate cannot be waived (2026-10-06).
        return set(), changed, new_files, (
            f"llm-review: PASSED — attempt {ctx.attempt} and the reviewed surface is "
            f"byte-identical to attempt {ctx.attempt - 1}, which was reviewed and left "
            f"no blocking finding open. The verdict stands; nothing was re-sampled.")
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


def judge(ctx, parsed, floor=None):
    """-> (fail, counts, records). With no ctx: the old rule, any finding fails."""
    if ctx is None:
        return bool(parsed), {}, []
    a = ctx.attempt
    prior_by_id = {p["fid"]: p for p in ctx.priors}
    known = latest_by_id(getattr(ctx, "records", []) or [])
    floor = floor or getattr(ctx, "floor", None) or BLOCKING_SEVERITY
    counts = dict(attempt=a, prior_fixed=0, prior_open=0, prior_unverifiable=0,
                  new_in_delta=0, new_outside=0, noted=0, accepted=0)
    recs, seen = [], set()
    for f in parsed:
        pid = f.get("prior_id")
        if pid and pid not in prior_by_id and pid in known:
            # A prior the FLOOR already demoted. The reviewer is right that it is
            # still there; it is still not worth a dispatch. Carried at its own
            # severity -- never re-blocked, and never duplicated as a new finding
            # with no severity, which would fail closed and pin the session.
            counts["noted"] += 1
            recs.append({**known[pid], "kind": "finding", "attempt": a,
                         "status": "noted", "evidence": f.get("summary")})
            continue
        if pid and pid in prior_by_id:
            st = "fixed" if str(f.get("prior", "")).lower() == "fixed" else "open"
            seen.add(pid)
            counts["prior_" + st] += 1
            recs.append({**prior_by_id[pid], "kind": "finding", "attempt": a, "status": st,
                         "evidence": f.get("summary")})
            continue
        path = _norm(f.get("file", ""))
        sev = str(f.get("severity", "")).lower()
        # LOCAL GROUND TRUTH WINS, IN BOTH DIRECTIONS. `ctx.delta` is computed
        # here from content hashes of this attempt's surface against the last
        # one; the reviewer cannot know it better than we do. Trusting its
        # self-reported tag broke coming and going: tag a real defect in the file
        # the fix just touched `"new_in": "outside"` and it was DEMOTED to
        # `noted`, so the gate passed; tag anything `"delta"` and it FORCED a
        # block. Both were reachable, and both were found independently. The tag
        # is bookkeeping now -- consulted only for a path we have nothing to
        # check it against, i.e. one genuinely outside the delta.
        new_in = ("first" if a == 1 else
                  ("delta" if path in ctx.delta else f.get("new_in") or "outside"))
        # THE FLOOR IS THE WHOLE RULE. Severity decides blocking; location does not.
        status = "open" if blocks(sev, floor) else "noted"
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
                     **({"accept_reason": hit["reason"]} if hit else {})})
    surface = {_norm(x) for x in (ctx.hashes or ())}
    for pid, p in prior_by_id.items():
        if pid in seen:
            continue
        # A prior in a file this attempt never SHOWED the reviewer cannot be marked
        # fixed, and its silence is not evidence either way. Counting it OPEN makes
        # the gate unpassable for the rest of the session -- which is exactly what
        # narrowing `review_scope` does to priors raised before the narrowing
        # (found by the plan-level-git-isolation session, 2026-08-20). Recorded,
        # counted and printed, never silently dropped.
        if surface and _norm(p.get("file") or "") not in surface:
            counts["prior_unverifiable"] += 1
            recs.append({**p, "kind": "finding", "attempt": a, "status": "unverifiable",
                         "evidence": "outside this attempt's reviewed surface; not judged"})
            continue
        counts["prior_open"] += 1                # omission IS not a fix, in surface
        recs.append({**p, "kind": "finding", "attempt": a, "status": "open",
                     "evidence": "not addressed by the reviewer; treated as OPEN"})
    return any(r["status"] == "open" for r in recs), counts, recs


def record(ctx, recs):
    p = ledger_path(ctx.plan_dir, ctx.session)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
        fh.write(json.dumps({"kind": "surface", "attempt": ctx.attempt, "hashes": ctx.hashes}) + "\n")


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
