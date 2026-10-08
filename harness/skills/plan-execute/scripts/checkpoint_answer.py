"""ANS-01 -- record the owner's answer to a pre-dispatch checkpoint.

`plan --resume` past a manifest checkpoint (`requires_human_checkpoint`) used to
take no answer at all, so the orchestrator hand-wrote one to an evidence file and
every plan spec had to say "STOP if it is missing". This keeps that fail-closed
rule in the tool: a resume that crosses such a checkpoint refuses unless it
carries `--answer-file` (the owner's words) or `--no-answer` (the owner declined).
Silence is never recorded as agreement.

The answer lives in `<plan>/_checkpoint_answers.ndjson`, append-only, one writer.
It is NOT kept in the change log: plan_mutate replaces `_changelog.ndjson` whole
from several paths, so an answer there could be erased. The change log gets a
display entry only. Reading an answer (and deciding whether it is still live)
lives in findings_digest, which is what hands it to the dispatched session.
"""
import fcntl
import json
import os
from pathlib import Path

import egress
import findings_digest as fd
import run_state_io as rsi

IGNORED = "--answer-file/--no-answer ignored: no checkpoint was resumed"
REFUSE_BARE = (
    "refusing to resume {sid}: it is parked at a human checkpoint and no answer was given. "
    "Pass `--answer-file {path}` with the owner's reply written to that file verbatim, or "
    "`--no-answer`. If the owner replied in any words, that reply IS the answer and goes in "
    "the file; `--no-answer` is only for an owner who explicitly declined to answer."
)


def qualifying_session(plan_dir, manifest, statuses, action, kind_of):
    """The session id this resume answers, or None. Positive evidence only: the
    batch head of a dispatch action, marked `requires_human_checkpoint`, at
    AWAITS_REVIEW, parked BEFORE dispatch, whose newest checkpoint event says
    `kind == "pre_dispatch"`. Anything else behaves as a bare resume always did."""
    if action.get("action") != "dispatch" or not action.get("batch"):
        return None
    sid = action["batch"][0]["id"]
    sess = next((s for s in manifest["sessions"] if s["id"] == sid), {})
    if not (sess.get("dispatch") or {}).get("requires_human_checkpoint"):
        return None
    if statuses.get(sid) != "AWAITS_REVIEW" or kind_of(plan_dir, sid) != "pre_dispatch":
        return None
    cur = fd.current_checkpoint(plan_dir, sid)
    return sid if cur and cur.get("kind") == "pre_dispatch" and cur.get("ts") else None


def _read_answer(path):
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SystemExit(f"refusing to record the answer: {path} cannot be read as UTF-8 ({e.__class__.__name__}). "
                         "Nothing was written.")
    if not text.strip():
        raise SystemExit(f"refusing to record the answer: {path} is empty. Write the owner's words "
                         "there, or pass --no-answer if the owner declined. Nothing was written.")
    hit = egress._text_hit(text, "checkpoint answer")
    if hit == egress._NO_GITLEAKS:
        raise SystemExit("refusing to record the answer: gitleaks is not installed, so it cannot be "
                         "scanned for secrets and the plan directory is committed. Install gitleaks "
                         "(`brew install gitleaks`) and run again. Nothing was written.")
    if hit:
        raise SystemExit(f"refusing to record the answer: it looks like it contains a secret ({hit}). "
                         "Remove the secret from the answer file and run again. Nothing was written.")
    return text


def _append(plan_dir, rec):
    """One `os.write` on an O_APPEND fd under an exclusive flock (finish.py's
    pattern); never read-modify-replace."""
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd_ = os.open(Path(plan_dir) / fd.ANSWERS_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd_, fcntl.LOCK_EX)
        os.write(fd_, line)
    finally:
        os.close(fd_)  # closing drops the flock


def handle(plan_dir, manifest, statuses, action, resume, answer, kind_of):
    """Called once from cmd_plan after the action is computed. `answer` is
    `(answer_file, no_answer)` or None. Returns extra keys for the JSON output;
    raises SystemExit (nothing written) to refuse."""
    answer_file, no_answer = answer or (None, False)
    sid = qualifying_session(plan_dir, manifest, statuses, action, kind_of) if resume else None
    if sid is None:
        return {"checkpoint_answer_note": IGNORED} if (answer_file or no_answer) else {}
    if not (answer_file or no_answer):
        if fd.live_answer(plan_dir, sid):
            return {}
        raise SystemExit(REFUSE_BARE.format(sid=sid, path=Path(plan_dir) / "_decisions" / f"{sid}.md"))
    text = _read_answer(answer_file) if answer_file else None
    stamp = fd.current_checkpoint(plan_dir, sid)["ts"]
    _append(plan_dir, {"at": rsi._now(), "session": sid, "answers_checkpoint": stamp,
                       "given": text is not None, "answer": text})
    out = {"checkpoint_answer": {"session": sid, "given": text is not None}}
    summary = f"owner answered: {text}" if text is not None else "owner resumed without an answer"
    try:
        import plan_mutate  # late: it imports the dashboard renderer; findings_digest must not
        plan_mutate.record_change(plan_dir, op="checkpoint-answer", session=sid,
                                  summary=summary, sessions_touched=[sid])
    except Exception as e:  # the answer is already on record; the page is display only
        out["checkpoint_answer"]["changelog_error"] = f"{e.__class__.__name__}: {e}"
    return out
