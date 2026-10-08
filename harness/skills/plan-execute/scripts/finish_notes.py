"""Plain-language notes for ``finish``'s late outcomes: the ``ci-save-busy`` park,
the ``finish-running`` park and the three ``finish-superseded`` cases. Split out of finish.py (file-size
bound); ``../references/finish-contract.md`` is the authority for the wording's
conditions."""

import re
import shlex

from finish_leftovers import _reason

REPOLL = ("pending", "unknown")

# FIN-16: the late park (top-level ``park_reason``) is not a preflight park
LATE = {"ci-save-busy": " The git steps (record, leftovers, checkout) ran; only the "
                        "CI result was not saved."}


def busy(plan_dir, why):
    """The ``ci-save-busy`` note: who holds the lease (from the LockError text),
    and the exact command to run once it is released."""
    held = re.search(r"\bplan=([^,)]+)", why or "")
    who = f"the run of {held.group(1)}" if held else "another run"
    return ("FINISH PARKED — the finish lease was busy (held by " + who + ") when this "
            "run went to save its CI result, so that result was not saved. The git steps "
            "(record, leftovers, checkout) had already run and are not undone. Once " + who
            + " has released the lease, run:\n  run.py finish "
            + shlex.quote(str(plan_dir)) + " --no-wait\nIt skips the git steps and goes "
            "straight to CI.")


def running(plan_dir):
    """FIN-18: the ``finish-running`` park; another run of this plan holds the run lock."""
    return ("FINISH PARKED — another finish run of this plan is running, so this run "
            "read, wrote and pushed nothing. Wait for that run to end, then run:\n"
            "  run.py finish " + shlex.quote(str(plan_dir)) + " --no-wait")


def case(cur):
    """What the saved state says, for the ``finish-superseded`` note."""
    return "finished" if cur.get("finished_at") else \
        "polling" if cur.get("steps_done_at") else "restarted"


def superseded(plan_dir, saved):
    """The ``finish-superseded`` note for ``case``: finished, polling or restarted."""
    q = shlex.quote(str(plan_dir))
    if saved == "finished":
        return ("A newer finish run (or another poll) saved its result first: this run "
                "wrote nothing. To read the saved result, run:\n  run.py finish " + q
                + " --no-wait")
    if saved == "polling":
        return ("A newer finish run ran the git steps and has not saved its CI result "
                "yet: this run wrote nothing. Wait for the finish run you started. If none "
                "is running, it died: run:\n  run.py finish " + q)
    return ("The plan was reopened, or a newer finish run stopped before its git steps: "
            "this run wrote nothing. Run:\n  run.py plan " + q + "\nand follow it. A "
            "re-run of finish is safe.")


def brief(out, notes):
    """The plain-language summary of one finish result (moved from finish.py)."""
    lines = [f"FINISH {out['plan']} ({out['mode']}): {out['action']}."
             + LATE.get(out.get("park_reason"), "")]
    pre, rec, left, co, ci = (out.get(k) for k in
                              ("preflight", "record", "leftovers", "checkout", "ci"))
    if pre and pre.get("park_reason"):
        lines.append(f"Preflight parked: {pre['park_reason']}.")
    if rec:
        lines.append(f"Record: {rec['status']}" + (f" ({rec['park_reason']})"
                                                  if rec.get("park_reason") else "")
                     + (f", {len(rec['paths'])} path(s)." if rec["paths"] else "."))
        lines += [f"  outside the record: {p}" for p in rec.get("outside_record") or []]
    if left:
        lines.append(f"Leftovers: {left['unpushed_default']} unpushed commit(s) on "
                     f"{out['default_branch']}; plan worktree {left['plan_worktree']}, "
                     f"plan branch {left['plan_branch']}"
                     + (f", remote plan branch {_reason(left['plan_branch_remote'])}"
                        if left.get("plan_branch_remote") else "") + ".")
        lines += [f"  retry {r['target']} {r['path']}: {r['result']}"
                  + (f" — {r['reason']}" if r["reason"] else "") for r in left["retried"]]
    if co:
        lines.append(f"Checkout {co['owner']}: {co['status']}.")
        lines += [f"  blocked by {b['path'] or '(owner)'}: {b['why']}"
                  + (f" (plan {b['plan']})" if b["plan"] else "") for b in co["blocking"]]
        if co["command"]:
            lines.append("  Run it yourself once that is resolved:\n    "
                         + co["command"].replace("\n", "\n    "))
    if ci:
        lines.append(f"CI: {ci.get('verdict')}" + (f" ({ci['reason']})" if ci.get("reason")
                                                   else "") + ".")
        if ci.get("match") == "descendant":       # finish_ci: the brief must say so
            lines.append(f"  CI tested a later commit, {ci.get('run_sha')}, not this one: "
                         "that run also covers commits this plan did not make.")
        if ci.get("verdict") == "red":
            lines += [f"  failed: {c.get('workflow')} / {c.get('job')}"
                      for c in ci.get("failed_checks") or []]
            lines.append("  The base had the same failures." if ci.get("inherited")
                         else "  The base did not have them all, or could not be compared.")
        if ci.get("verdict") in REPOLL:
            lines.append(f"  Re-run later: run.py finish {out['plan_dir']} --no-wait")
    return "\n".join(lines + [n for n in notes if n])
