"""The land cost line: a land that was red twice on the SAME plan head needs one.

Measured (plan checks-and-burn-down, s68e00177): one land gate re-ran with no code
change, 836 s and 904 s each, and no one said what it cost. Accepted (session
6f7c80aa) and recorded in the ledger as done, but only ``clear-halt`` got the guard. This is the
same rule for ``land`` and ``land-resume``: one free re-run, then a cost line.

Counted in ``land.json`` (RUNTIME state) by ``land_state.park``. A new commit on the plan
branch is a real change, so it resets the count and needs no cost line.
"""

import land_state as lst
import run_state_io as rsi

FREE_REDS = 2                     # the first red, and one re-run that is red again

USAGE = ("run.py land {plan_dir} --cost-report '<elapsed, passes so far, what changed, "
         "the cut you propose>'")


def check(plan_dir, ctx, st, cost_report):
    """None to go on, or a refusal payload. A given cost line is logged and resets the count."""
    head = lst.rev(plan_dir, ctx["branch"])
    if (st.get("state") != "parked" or (st.get("park") or {}).get("kind") not in lst.RED_KINDS
            or int(st.get("red_parks", 0)) < FREE_REDS or st.get("red_head") != head):
        return None
    if cost_report:
        st["red_parks"] = 0
        lst.save(plan_dir, ctx, st)
        rsi.log_event(plan_dir, "land_cost_reported", session_ids=[], slug=ctx["slug"],
                      cost_report=cost_report)
        return None
    n = int(st["red_parks"])
    return {"action": "land-refused", "kind": "cost-report-required",
            "brief": (f"LAND NOT RUN — the gates were red {n}x on the same plan head "
                      f"({(head or '')[:10]}) and nothing changed. Post the cost to the owner "
                      "first (elapsed, passes so far, what changed, the cut you propose), "
                      "then re-run with it:\n  " + USAGE.format(plan_dir=plan_dir)
                      + "\nA new commit on the plan branch needs no cost line.")}
