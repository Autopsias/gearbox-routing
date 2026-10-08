#!/usr/bin/env python3
"""One short digest of findings other sessions of THIS plan already got.

WHY: reviewers keep re-raising the same class of mistake across sessions of one
plan because each dispatched session starts cold, with no memory of what a
reviewer already flagged two sessions ago. This reads every OTHER session's
findings ledger under `_verify_state/*.findings.ndjson`, keeps the
medium-or-worse ones still open, and hands the next dispatch a capped,
deduplicated list to read before it repeats the mistake.

Ledger filenames vary by reviewer family and round, but ALL of them start with
the bare session id followed by a dot: `sNN.findings.ndjson` (the on-box
Claude reviewer, via `llm_review_ledger.ledger_path`), `sNN.medium.claude.
findings.ndjson` / `sNN.medium.codex.findings.ndjson` (llm_review_surface's
`{sid}.{level}.{reviewer}` session, one per reviewer family), and any of those
one redispatch round later: `sNN.r1.findings.ndjson`,
`sNN.r1.medium.codex.findings.ndjson` (run.py's `_archive_session_state`).
Reading the bare id off the front of the filename -- not comparing the whole
stem to `session_id` -- is what lets "exclude THIS session's own findings"
work across every one of those variants at once.

A digest must never block a dispatch: any unreadable file or line is skipped,
never raised, and one file's stat/decode failure never blanks the rest. Every
field pulled off a ledger record is also coerced to a plain type before use --
a ledger is data written by another process/reviewer round, so its shape is
not guaranteed even when the JSON itself parses.

CHECKPOINT ANSWER (ANS-01): when the owner answered the pre-dispatch checkpoint
this session was parked at, the answer (kept in `_checkpoint_answers.ndjson`) goes
at the TOP of the digest, so both harnesses hand it to the dispatched session. It
is read here, by bytes and strict UTF-8, in its own function OUTSIDE the findings
part's fail-open `except`: a changed byte would pass changed words off as the
owner's, so an unreadable answer yields a stop block instead of nothing. A plan
with no live answer produces exactly the output it did before.
"""
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ANSWERS_FILE = "_checkpoint_answers.ndjson"
#: Change-log ops that void an answer recorded before them: the session was sent
#: back, amended or retired, so the answer was given against state that changed.
RETIRING_OPS = {"redispatch", "amend-session", "retire-session", "resolve-replan"}

#: Severities worth surfacing to a later session. Below the ledger's own
#: BLOCKING_SEVERITY floor (medium counts here, unlike the gate's own floor)
#: because a medium finding is exactly the class of mistake worth not repeating,
#: even though it does not block the session that raised it.
_DIGEST_SEVERITIES = {"medium", "high", "critical", "blocker"}
_CAP = 12
_SUMMARY_CUT = 200
_FILE_CUT = 200
_LINE_CUT = 20
_WS_RE = re.compile(r"\s+")


def _collapse(value):
    """Coerce to str and collapse all whitespace (including embedded newlines
    and carriage returns) to single spaces. A ledger field renders as ONE
    bullet line; without this, a crafted `summary` or `file` could inject a
    blank line and a new "## heading" into the dispatched prompt."""
    return _WS_RE.sub(" ", str(value)).strip()


def _session_of(filename):
    """The bare session id a ledger filename belongs to: the part before the
    first dot. See the module docstring for the filename variants this must
    cover."""
    return filename.split(".", 1)[0]


def _mtime(f):
    """Sort key that never raises: one file's stat error must not blank the
    whole digest, only leave that file's position under-determined."""
    try:
        return f.stat().st_mtime
    except OSError:
        return 0.0


def _finding(ln):
    """One ledger line as a finding record with a usable fid, or None.
    Severity and status are judged later, on the latest record per fid."""
    try:
        rec = json.loads(ln)
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("kind") != "finding":
        return None
    fid = rec.get("fid")
    # fid must be a plain non-empty str: it is used as a dict key AND as an
    # `in` membership check by the caller, so a list/dict fid would raise
    # TypeError (unhashable) rather than degrade gracefully.
    if not isinstance(fid, str) or not fid:
        return None
    return rec


def _still_worth_showing(rec):
    """Filters that run on the LATEST record per fid, never before dedupe: a
    later low-severity or "accepted" update must supersede (and so drop) an
    earlier medium-or-worse open record for the same fid."""
    if _collapse(rec.get("status", "")) == "accepted":
        return False
    return _collapse(rec.get("severity", "")).lower() in _DIGEST_SEVERITIES


def _ndjson_objects(path):
    """Every JSON-object line of `path` as bytes-split lines decoded strictly;
    lines that are blank, not JSON, not an object or not UTF-8 are skipped.
    A missing file reads as empty."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    out = []
    for ln in raw.split(b"\n"):
        try:
            rec = json.loads(ln.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def current_checkpoint(plan_dir, session_id):
    """The session's NEWEST `checkpoint_reached` event of any kind (file order,
    run.ndjson is append-only), or None. Origin needs positive evidence, so the
    caller reads `kind` and `ts` off this one event."""
    newest = None
    for rec in _ndjson_objects(Path(plan_dir) / "run.ndjson"):
        if rec.get("event") == "checkpoint_reached" and session_id in (rec.get("session_ids") or []):
            newest = rec
    return newest


def _when(value):
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _retired(plan_dir, session_id, answer_at):
    """Is there a retiring change-log entry for the session newer than the answer?
    An entry whose time cannot be parsed counts as retiring (fail closed)."""
    at = _when(answer_at)
    if at is None:
        return True
    for rec in _ndjson_objects(Path(plan_dir) / "_changelog.ndjson"):
        if rec.get("op") not in RETIRING_OPS:
            continue
        touched = rec.get("sessions_touched")
        if rec.get("session") != session_id and session_id not in (touched if isinstance(touched, list) else []):
            continue
        when = _when(rec.get("at"))
        if when is None or when > at:
            return True
    return False


def live_answer(plan_dir, session_id):
    """The session's live checkpoint answer.

    Returns ``None`` (nothing live), ``("ok", record)`` or ``("unreadable", why)``.
    Live = bound to the session's CURRENT pre-dispatch checkpoint stamp and not
    retired by a newer change-log entry. Strict: never decodes with replace."""
    cur = current_checkpoint(plan_dir, session_id)
    if not cur or cur.get("kind") != "pre_dispatch":
        return None
    path = Path(plan_dir) / ANSWERS_FILE
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError as e:
        return ("unreadable", f"{ANSWERS_FILE} cannot be read ({e.__class__.__name__})")
    best = None
    for ln in raw.split(b"\n"):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln.decode("utf-8"))
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            # Cannot tell whose line it is by parsing; name the session if its id
            # shows up in a lenient view of the bytes (view only, never recorded).
            if f'"{session_id}"' in ln.decode("utf-8", errors="replace"):
                return ("unreadable", f"a line of {ANSWERS_FILE} for {session_id} is not valid UTF-8 JSON")
            continue
        if rec.get("session") == session_id and rec.get("answers_checkpoint") == cur.get("ts"):
            best = rec
    if best is None or _retired(plan_dir, session_id, best.get("at")):
        return None
    return ("ok", best)


def _answer_block(plan_dir, session_id):
    """The block that opens the digest, or "" when no answer is live."""
    got = live_answer(plan_dir, session_id)
    if got is None:
        return ""
    head = "--- OWNER'S ANSWER AT THE CHECKPOINT BEFORE THIS SESSION ---\n"
    tail = "--- END OWNER'S ANSWER ---\n\n---\n\n"
    state, rec = got
    if state == "unreadable":
        return (head + f"  The owner's checkpoint answer could not be read: {rec}.\n"
                "  Stop and flag this in your closeout. Do not guess what the owner said.\n" + tail)
    text = rec.get("answer")
    if rec.get("given") is not True or not isinstance(text, str):
        return (head + "  The owner resumed without an answer. This is NOT agreement with any\n"
                "  option in the checkpoint brief. Follow this session's prompt as written\n"
                "  and record in your closeout that no answer was given.\n" + tail)
    body = "".join("  " + part for part in text.splitlines(True))
    if not body.endswith("\n"):
        body += "\n"
    return (head + body +
            "Treat it as the decision for this session, within this session's verify contract\n"
            "(its gates, checks, evidence paths and locked files). If it contradicts your prompt,\n"
            "follow the answer and record the contradiction as a deviation in your closeout. If it\n"
            "changes what verify checks, stop and raise a replan through the closeout `plan_impact`\n"
            "field instead of failing verify.\n" + tail)


def _digest(plan_dir, session_id):
    """Real implementation. See `digest()` for the fail-open public wrapper."""
    verify_dir = Path(plan_dir) / "_verify_state"
    try:
        files = sorted(verify_dir.glob("*.findings.ndjson"), key=_mtime)
    except OSError:
        return ""
    order = []  # fid insertion order; a fid is moved to the end on update
    records = {}  # fid -> (bare_session_id, record)
    for f in files:
        session = _session_of(f.name)
        if session == session_id:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for ln in lines:
            rec = _finding(ln)
            if rec is None:
                continue
            fid = rec["fid"]
            # LATEST record per id wins, same rule as the ledger itself -- a
            # finding's status/summary can change across attempts. Move it to
            # the end of `order` on every update, so "newest" tracks the most
            # recent WRITE, not just the fid's first appearance.
            if fid in records:
                order.remove(fid)
            order.append(fid)
            records[fid] = (session, rec)
    rows = [records[fid] for fid in reversed(order)
            if _still_worth_showing(records[fid][1])][:_CAP]
    if not rows:
        return ""
    lines = ["Findings reviewers already raised in this plan (avoid repeating these classes of mistake):"]
    for session, rec in rows:
        summary = _collapse(rec.get("summary") or "").split(". ")[0][:_SUMMARY_CUT]
        severity = _collapse(rec.get("severity", "")).lower()
        file = _collapse(rec.get("file", "?"))[:_FILE_CUT]
        line = _collapse(rec.get("line", "?"))[:_LINE_CUT]
        lines.append(f"- [{session}, {severity}] {summary} ({file}:{line})")
    return "\n".join(lines) + "\n\n---\n\n"


def digest(plan_dir, session_id):
    """A short markdown-ish block of findings raised on OTHER sessions of this
    plan, newest-update first, capped at 12 -- or "" when nothing qualifies.

    # ponytail: broad except is deliberate -- this is the dispatch path's
    # fail-open backstop. `_digest` already skips bad files/lines/fids one at
    # a time; this catch exists for whatever shape that per-field handling
    # does not anticipate, because a digest must NEVER block a dispatch.
    """
    try:
        findings = _digest(plan_dir, session_id)
    except Exception:
        findings = ""
    return _answer_block(plan_dir, session_id) + findings
