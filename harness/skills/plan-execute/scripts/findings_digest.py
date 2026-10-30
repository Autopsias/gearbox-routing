"""The owner's checkpoint answer, handed to the session dispatched after it.

WHY: when the owner answers a pre-dispatch checkpoint, the dispatched session
must start with those words at the top of its prompt. The answer is kept in
`_checkpoint_answers.ndjson` (ANS-01). It is read here by bytes and strict
UTF-8: a changed byte would pass changed words off as the owner's, so an
unreadable answer yields a stop block instead of nothing. A plan with no live
answer dispatches byte for byte as before.

HISTORY: until 2026-10-13 `digest()` also prepended a list of findings that
reviewers raised on other sessions of the plan. A re-measure found the repeat
rate of finding classes did not fall (28% before, 36% after; ledger row
2026-09-24 Helix in docs/worth-adopting/LEDGER.md), so that part was removed.
The findings ledgers under `_verify_state/*.findings.ndjson` are untouched.
"""
import json
from pathlib import Path

import safe_json as sj

ANSWERS_FILE = "_checkpoint_answers.ndjson"
#: Change-log ops that void an answer recorded before them: the session was sent
#: back, amended or retired, so the answer was given against state that changed.
RETIRING_OPS = {"redispatch", "amend-session", "retire-session", "resolve-replan"}

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
        if rec.get("event") == "checkpoint_reached" and session_id in sj.get_list(rec, "session_ids", []):
            newest = rec
    return newest


def _retired(plan_dir, session_id, answer_at):
    """Is there a retiring change-log entry for the session newer than the answer?
    An entry whose time cannot be parsed counts as retiring (fail closed)."""
    at = sj.parse_iso_aware(answer_at)
    if at is None:
        return True
    for rec in _ndjson_objects(Path(plan_dir) / "_changelog.ndjson"):
        if rec.get("op") not in RETIRING_OPS:
            continue
        if rec.get("session") != session_id and session_id not in sj.get_list(rec, "sessions_touched", []):
            continue
        when = sj.parse_iso_aware(rec.get("at"))
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


def digest(plan_dir, session_id):
    """The owner's checkpoint answer block for this session, or "" when no
    answer is live. Keeps its old name: both dispatch funnels call it."""
    return _answer_block(plan_dir, session_id)
