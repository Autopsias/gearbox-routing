"""The SECOND clear of one session's halt needs a cost line.

Measured on one plan run: 9 halts, 10 clears, ONE session, and two
memory rules written the same morning saying "clear at most once" did not
hold that afternoon — a memory fires at recall, and nothing recalls it
mid-loop. So the count lives here, in the tool, and the act the rule asks
for (post the cost line) is the price of the second clear. `run_state_io`
re-exports `clear_halt` from here; the imports are lazy to avoid the cycle.
"""
from pathlib import Path


class HaltClearRefused(Exception):
    pass


#: The only ways out of a review halt from its THIRD clear on (2026-11-16, s04:
#: 12 clears, 13 review runs, each clear a free-text "one more review").
EXITS = ("accept", "split", "escalate")
_LEDGER = ".findings.ndjson"


def _ledgers(plan_dir, sid):
    """(ledger session name, its records) for every review gate of `sid`."""
    import llm_review_ledger as lrl
    import safe_json as sj
    vs = Path(plan_dir) / "_verify_state"
    try:
        sj.resolve_inside(vs, sid)
    except ValueError:  # an empty or escaping session id has no ledgers (clear_halt passes "" when no session halted)
        return []
    names = sorted(f.name[:-len(_LEDGER)] for f in vs.glob(f"{sid}.*{_LEDGER}"))
    return [(n, lrl.load(plan_dir, n)) for n in names]


def open_unaccepted(plan_dir, sid):
    """Blocking review findings of `sid` that no accept entry covers.
    shortcut: default severity floor, and a prior whose file left the surface still
    counts; upgrade when a plan uses --blocking-severity with this exit."""
    import llm_review_ledger as lrl
    out = []
    for name, recs in _ledgers(plan_dir, sid):
        acc = lrl.load_accepted(plan_dir, name)
        out += [p for p in lrl.open_priors(recs)
                if not lrl.accepted_match(acc, p.get("file"), p.get("summary"), p.get("severity"))]
    return out


def rounds_line(plan_dir, sid):
    """New findings per review round, for the halt reason: the number that shows a
    loop which does not converge. '' with fewer than two rounds."""
    out = []
    for name, recs in _ledgers(plan_dir, sid):
        first = {}
        for r in recs:
            if r.get("kind") == "finding":
                first.setdefault(r["fid"], r.get("attempt", 0))
        rounds = max((r.get("attempt", 0) for r in recs), default=0)
        if rounds >= 2:
            new = ",".join(str(list(first.values()).count(a)) for a in range(1, rounds + 1))
            out.append(f"{name}: {rounds} review rounds, new findings per round {new}")
    return ("; ".join(out) + ". A count that does not fall is the reviewer's sample of a "
            "surface too large for one pass: split, or accept on record.") if out else ""


def unmet_exit(plan_dir, sid):
    """'' when `sid` may run a gate. Else why not: its last halt clear promised a
    plan change (`--exit split|escalate`) and the change log has no newer entry."""
    import plan_mutate as pm
    import run_state_io as rsi
    p = (rsi.load_state(plan_dir).get("review_exit") or {}).get(sid)
    import findings_digest as fd
    if not p or any(str(e.get("at", "")) > p["at"] for e in pm.read_changelog(plan_dir)
                    if e.get("op") not in fd.DISPLAY_ONLY_OPS):
        return ""
    return (f"{sid}: the halt was cleared with --exit {p['exit']}, and the plan did not change "
            "since. Change the plan first (add-session, retire-session): another review of the "
            "same surface is not an exit.")


def _check_exit(plan_dir, state, sid, n, exit_, now):
    import llm_review_ledger as lrl
    # A REVIEW halt = a review ledger of this session holds an open blocking finding.
    # Not the stuck record's class: an unchanged-surface fail signs as "unclassified".
    if n < 2 or not any(lrl.open_priors(recs) for _, recs in _ledgers(plan_dir, sid)):
        return
    if exit_ not in EXITS:
        raise HaltClearRefused(
            f"{sid} failed its review again and its halt was already cleared {n}x. One more "
            "review round is not an exit. Choose one and re-run with it: --exit accept (every "
            f"open blocking finding has an entry in _verify_state/{sid}.accepted.json), or "
            "--exit split / --exit escalate (you change the plan with add-session or "
            "retire-session before any gate runs again for this session).")
    if exit_ != "accept":
        state.setdefault("review_exit", {})[sid] = {"exit": exit_, "at": now}
        return
    left = open_unaccepted(plan_dir, sid)
    if left:
        raise HaltClearRefused(
            f"--exit accept, but {len(left)} open blocking finding(s) of {sid} have no accept "
            "entry: " + "; ".join(f"{p.get('file')}: {str(p.get('summary'))[:80]}" for p in left))


def _check_answer_target(plan_dir, halt, sid):
    """Refuse an --answer-file that could not reach a prompt; nothing has changed yet."""
    import article_block as ab
    if not halt.get("set"):
        raise HaltClearRefused("no halt is set; a halt answer must go in the same clear-halt call "
                               "that clears the halt")
    if not sid:
        raise HaltClearRefused("this halt names no session, so an answer has no prompt to reach. "
                               "Clear without --answer-file, then redispatch or amend the session "
                               "that needs the words")
    if halt.get("kind") == "replan":
        raise HaltClearRefused("this is a replan halt: answer it with resolve-replan, not --answer-file")
    try:
        status = ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_text()).get(sid)
    except OSError:
        status = None
    if status in ("DONE", "WONTFIX"):
        raise HaltClearRefused(f"{sid} is {status}. clear-halt without --answer-file, then "
                               f"redispatch --session {sid} --reason ...; a halt answer cannot "
                               "reach a finished session")


def clear_halt(plan_dir, cost_report=None, decision=None, exit_=None, answer_file=None):
    import checkpoint_answer as ca
    import run_state_io as rsi
    import stuck_protocol as sp
    state = rsi.load_state(plan_dir)
    sid = (state.get("halt") or {}).get("by_session") or ""
    if answer_file:
        _check_answer_target(plan_dir, state.get("halt") or {}, sid)
    clears = state.setdefault("halt_clears", {})
    n = int(clears.get(sid, 0))
    if n >= 1 and not cost_report:
        raise HaltClearRefused(
            f"halt on {sid or '(unknown)'} was already cleared {n}x this run. "
            "Post the cost line to the owner first, then re-run with it: "
            f"clear-halt {plan_dir} --cost-report "
            "'<elapsed, passes so far, what changed, the cut you propose>'")
    _check_exit(plan_dir, state, sid, n, exit_, rsi._now())
    decision = decision or exit_
    if sp.armed((state.get("stuck") or {}).get(sid)) and not (decision or "").strip():
        # The stuck protocol said "same root cause, repeated" and the loop went on
        # anyway (s02, 2026-11-10: 8 rounds). A clear is now a choice, on the record.
        raise HaltClearRefused(
            f"{sid or '(unknown)'} is stuck: the same failure repeated. Choose how to cut it "
            f"(simplify, accept the finding on record, or stop), then re-run: clear-halt "
            f"{plan_dir} --decision '<the option you chose and why>'")
    text = ca._read_answer(answer_file) if answer_file else None  # SystemExit on a secret: nothing changed
    if sid:
        # Record BEFORE the clear; the same state write that clears the halt saves the id, so a
        # crash between the two leaves the record unreferenced. A bare clear drops an older id.
        state.setdefault("halt_answer", {})[sid] = ca.record_halt_answer(plan_dir, sid, text) if text else None
    clears[sid] = n + 1
    state["halt"] = {"set": False, "reason": None, "by_session": None, "at": None, "kind": None}
    rsi.save_state(plan_dir, state)
    notice = Path(plan_dir) / "HALT_NOTICE.txt"
    if notice.exists():
        notice.unlink()
    rsi.log_event(plan_dir, "halt_cleared", session=sid or None, clears=n + 1,
                  cost_report=cost_report, decision=decision, exit=exit_, answered=bool(text))
    if text:
        ca.display_halt_answer(plan_dir, sid, text)
    return state


def clear_halt_cli(plan_dir, cost_report, out, decision=None, exit_=None, answer_file=None):
    """run.py's `clear-halt`: print through `out`, exit 1 on a refusal."""
    try:
        state = clear_halt(plan_dir, cost_report=cost_report, decision=decision, exit_=exit_,
                           answer_file=answer_file)
    except HaltClearRefused as e:
        out({"refused": str(e)})
        raise SystemExit(1)
    out({"halt": state["halt"], "clears": state.get("halt_clears", {})})
