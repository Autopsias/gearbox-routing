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


def clear_halt(plan_dir, cost_report=None):
    import run_state_io as rsi
    state = rsi.load_state(plan_dir)
    sid = (state.get("halt") or {}).get("by_session") or ""
    clears = state.setdefault("halt_clears", {})
    n = int(clears.get(sid, 0))
    if n >= 1 and not cost_report:
        raise HaltClearRefused(
            f"halt on {sid or '(unknown)'} was already cleared {n}x this run. "
            "Post the cost line to the owner first, then re-run with it: "
            f"clear-halt {plan_dir} --cost-report "
            "'<elapsed, passes so far, what changed, the cut you propose>'")
    clears[sid] = n + 1
    state["halt"] = {"set": False, "reason": None, "by_session": None, "at": None, "kind": None}
    rsi.save_state(plan_dir, state)
    notice = Path(plan_dir) / "HALT_NOTICE.txt"
    if notice.exists():
        notice.unlink()
    rsi.log_event(plan_dir, "halt_cleared", session=sid or None, clears=n + 1,
                  cost_report=cost_report)
    return state


def clear_halt_cli(plan_dir, cost_report, out):
    """run.py's `clear-halt`: print through `out`, exit 1 on a refusal."""
    try:
        state = clear_halt(plan_dir, cost_report=cost_report)
    except HaltClearRefused as e:
        out({"refused": str(e)})
        raise SystemExit(1)
    out({"halt": state["halt"], "clears": state.get("halt_clears", {})})
