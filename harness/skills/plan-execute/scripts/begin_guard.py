#!/usr/bin/env python3
"""`begin` guard: refuse to start a session that is finished or waits for the owner.

``refusal(plan_dir, session, status)`` returns a refusal text or None. It refuses on
positive evidence only:

  (a) status DONE, WONTFIX or DEFERRED  -> the legal re-run is `redispatch`;
  (b) AWAITS_REVIEW with a closeout whose result is DONE (a post-session park)
      -> `ack-checkpoint`;
  (c) a session with dispatch.requires_human_checkpoint that is at TODO, or at
      AWAITS_REVIEW whose newest checkpoint_reached event is kind pre_dispatch,
      when findings_digest.live_answer is not ("ok", record). A recorded
      `--no-answer` is an ("ok", record) and passes. ("unreadable", why) refuses.

Everything else is allowed: TODO/PARTIAL/BLOCKED/DOING without the flag; PARTIAL,
BLOCKED or DOING with the flag (its answer came before its first dispatch and may be
retired since); AWAITS_REVIEW with a non-DONE closeout and a newest checkpoint event
with no kind (a session parked mid-flight by its own question, or by the Codex
barred-session park: no command can record an answer for it, and `plan --resume`
starts it today).

Known limit: `plan` surfaces a checkpoint for the head of a batch only
(dispatch._dispatch_or_checkpoint reads batch[0]), so a flagged session that is not
the head of a parallel batch is refused and no command records its answer (0 of 93
flagged sessions in _plans/). A flagged session at DOING that never had
an answer (hand edit, or a run that began before this guard) is allowed.

The refusal writes no run.ndjson row of its own; the `concurrent_begin_*` row comes
from `_begin_preflight`, which runs before this check.

Read-only command line: ``python3 begin_guard.py <plan_dir>`` prints one line per
session: id, status, `allow` or the refusal text. It writes nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import article_block as ab  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import findings_digest as fd  # noqa: E402
import manifest_io as mio  # noqa: E402

# Deliberate (plan s02, rule a; owner accepted 2026-11-13): BLOCKED is not here. A blocked session is investigated, then
# started again by `begin` or `redispatch`; refusing it would strand a halted plan.
FINISHED = {"DONE", "WONTFIX", "DEFERRED"}


def _closeout_done(plan_dir, sid):
    try:
        co = cp.load_closeout(plan_dir, sid)
    except (OSError, ValueError):
        return False
    return bool(co) and co.get("result") == "DONE"


def refusal(plan_dir, session, status):
    """Refusal text for beginning ``session`` (a manifest dict) at ``status``, or None."""
    sid = session["id"]
    if status in FINISHED:
        return (f"refusing to begin {sid}: it is {status}, a finished session. To re-run it on "
                f"purpose: run.py redispatch {plan_dir} --session {sid} --reason R")
    if status == "AWAITS_REVIEW" and _closeout_done(plan_dir, sid):
        return (f"refusing to begin {sid}: it is AWAITS_REVIEW with a finished (DONE) closeout. "
                f"Approve it with: run.py ack-checkpoint {plan_dir} --session {sid}")
    if not session.get("dispatch", {}).get("requires_human_checkpoint"):
        return None
    if status == "AWAITS_REVIEW":
        cur = fd.current_checkpoint(plan_dir, sid)
        if not cur or cur.get("kind") != "pre_dispatch":
            # Deliberate (plan s02, grill): no kind = parked mid-flight; no command can record an
            # answer for it, and `plan --resume` starts it today. Refusing would deadlock it.
            return None
    elif status != "TODO":
        return None
    live = fd.live_answer(plan_dir, sid)
    if live and live[0] == "ok":
        return None
    if live:
        return (f"refusing to begin {sid}: its recorded checkpoint answer cannot be read "
                f"({live[1]}). Fix that file, then run.py plan {plan_dir} --resume")
    resume = (f"run.py plan {plan_dir} --resume --answer-file <file> "
              f"(or --no-answer when the owner gave none)")
    if status == "TODO":
        return (f"refusing to begin {sid}: it waits for the owner's answer. First run.py checkpoint "
                f"{plan_dir} --session {sid}, then {resume}. If {sid} is not the head of a parallel "
                f"batch, no command can record its answer: report that")
    return f"refusing to begin {sid}: it waits for the owner's answer. Run {resume}"


def check(plan_dir, by_id, statuses, sessions):
    """First refusal text over ``sessions``, or None."""
    for sid in sessions:
        text = refusal(plan_dir, by_id[sid], statuses.get(sid))
        if text:
            return text
    return None


def main(argv):
    plan_dir = argv[1]
    statuses = ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_text())
    for sid, s in mio.session_by_id(mio.load_manifest(plan_dir)).items():
        print(sid, statuses.get(sid), refusal(plan_dir, s, statuses.get(sid)) or "allow")


if __name__ == "__main__":
    main(sys.argv)
