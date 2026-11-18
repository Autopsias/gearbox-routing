"""LND-14 — a land test gate that is red only because the machine is busy costs a
targeted re-run of its failing tests (about 2 minutes), not a whole re-run
(finish-contract.md, "Load-flaky tests").

A gate opts in with ``rerun_tests: {"argv": [...], "timeout": s}`` in its registry
entry. The argv runs the failing pytest ids, appended as arguments, alone and
serially, through govrun with a bounded ``--max-wait``. Declare it only on a gate
where a failure that names test ids means nothing else failed: ``make check`` runs
``test`` LAST for this reason, so a red lint never reaches pytest and names no id.

``retry`` runs right after a failing land-tree run of such a gate (the first run,
never a base run or LND-12's whole-gate re-run), under ``land_flaky``'s fail-closed
rule and never a weaker one. The targeted re-run runs only when every failing name
is a test id (no collection error, no whole gate), none is HELD (in a file the plan
changed, or git cannot read the diff), pytest did not abort (``INTERNALERROR``,
``Interrupted``), and pytest's own summary counts exactly those failures. Then:

* every id passes alone -> the gate is ``pass`` for this candidate, with
  ``load_flaky`` naming each id, and each id goes into the ledger;
* any id fails again alone -> the first run's red, exactly as before;
* govrun's queue timeout (exit 75), a timeout or an exec error -> ``skipped``:
  undecided, never a pass.

The ledger is ``~/.gearbox-state/land/load-flaky.ndjson``: outside both versioned trees, for
the reason ``hooks/dispatch-audit.py`` gives (a runtime log in a deploy target
blocks the next deploy). A ledger that cannot be read or written never changes a
verdict. The land brief names an id the ledger saw before as a fix task.
"""

import json
import os
import re
import time
from pathlib import Path

import land_flaky as lfk
import lease_guard as lg
import land_state as lst
import safe_json as sj
import ship_state_io as ssio

QUEUE_TIMEOUT = 75          # govrun: no slot within --max-wait, nothing ran
_SUMMARY = re.compile(r"(?m)^=*\s*(\d+ \w+(?:, \d+ \w+)*) in [\d.]+s\b")
_ABORT = re.compile(r"(?m)^INTERNALERROR>|^!+ .*(?:Interrupted|KeyboardInterrupt)")
_WAITED = re.compile(r"govrun: acquired \S+ after (\d+)s")


def counts(text):
    """``{word: n}`` from pytest's LAST summary line (``2 failed, 5 passed in 1.2s``)."""
    found = _SUMMARY.findall(text or "")
    return {w: int(n) for n, w in re.findall(r"(\d+) (\w+)", found[-1])} if found else {}


def _failed(c):
    return c.get("failed", 0) + c.get("error", 0) + c.get("errors", 0)


def ledger():
    return Path.home() / ".gearbox-state" / "land" / "load-flaky.ndjson"


def _why_not(st, gid, text, ids):
    """Why the targeted re-run may not run, or None (module docstring)."""
    named = lfk.names(gid, ids, text)
    held = st.get("plan_touched_flakes") or {}
    if not ids or len(named) != len(ids):
        return "the failure names no test id, or names a file that failed to collect"
    if any(lfk.key(gid, n) in held for n, _ in named):
        return "a failing test is in a file this plan changed"
    if _ABORT.search(text):
        return "pytest aborted, so tests after the abort never ran"
    if _failed(counts(text)) != len(ids):
        return "pytest's summary does not count exactly these failures"
    return None


def _log(slug, gid, ids, load):
    """``[{test, seen_before}]``: each id's count in the ledger, then one line each."""
    path, seen = ledger(), {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    for ln in lines:
        t = (sj.load_dict(ln) or {}).get("test")
        seen[t] = seen.get(t, 0) + 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.writelines(json.dumps({"at": lst.now(), "plan": slug, "gate": gid, "test": t,
                                     "load1": load}) + "\n" for t in ids)
    except OSError:
        pass
    return [{"test": t, "seen_before": seen.get(t, 0)} for t in ids]


def retry(ctx, st, gid, g, rec):
    """``rec`` (a land-tree gate run) as the targeted re-run decides it, or the
    ``lease-lost-renew`` park (an ``action`` dict) when the lease is no longer ours."""
    spec = g.get("rerun_tests")
    if rec["outcome"] != "fail" or not isinstance(spec, dict) or not spec.get("argv"):
        return rec
    text = lfk._text(rec) or ""
    ids = lfk.tests(text)
    why = _why_not(st, gid, text, ids)
    if why:
        return {**rec, "excerpt": f"No targeted re-run: {why}.\n{rec.get('excerpt') or ''}"}
    lost = lg.renew_or_park(ctx["plan_dir"], ctx, st, f"before the re-run of {gid}")
    if lost is not None:                                      # §17.4 inv. 9: park, run nothing
        return lost
    load = round(os.getloadavg()[0], 1)
    t0 = time.monotonic()
    res = ssio.run_deploy_argv([*spec["argv"], *ids], cwd=rec["cwd"],
                               timeout=lst.timeout_for(spec.get("timeout", 900)),
                               env_allowlist=g.get("env_allowlist", []))
    out = (res.get("stderr") or "") + (res.get("stdout") or "")
    rc, waited = res.get("returncode"), _WAITED.search(out)
    alone = {"tests": ids, "returncode": rc, "wall_s": round(time.monotonic() - t0, 1),
             "load1": load, "waited_s": int(waited.group(1)) if waited else None}
    tail = out[-1500:]
    if rc is None or rc == QUEUE_TIMEOUT:
        cause = "govrun queue timeout, nothing ran" if rc else "a timeout or exec error"
        return {**rec, "outcome": "skipped", "rerun_alone": alone, "excerpt": (
            f"The full run failed {ids}; re-running them alone could not decide "
            f"({cause}). Undecided, never a pass.\n{tail}")}
    c = counts(out)
    if rc == 0 and c.get("passed") == len(ids) and not _failed(c):
        return {**rec, "outcome": "pass", "findings_count": 0, "rerun_alone": alone,
                "load_flaky": _log(ctx["slug"], gid, ids, load),
                "excerpt": f"Failed in the full run, passed alone: {ids}\n{tail}"}
    return {**rec, "rerun_alone": alone, "excerpt": (
        f"Re-run alone, still red (rc={rc}): a real failure, not load.\n{tail}\n"
        f"--- the full run ---\n{rec.get('excerpt') or ''}")}


def note(st):
    """The land brief's lines for every load-flaky gate of this candidate."""
    lines = []
    for r in st.get("gates") or []:
        a = r.get("rerun_alone") or {}
        for e in r.get("load_flaky") or []:
            again = (f"; the ledger saw it {e['seen_before']} time(s) before, so make it a "
                     "fix task" if e["seen_before"] else "")
            lines.append(f"  {r['gate_id']}: {e['test']} (load average {a.get('load1')}, "
                         f"waited {a.get('waited_s')} s for the slot{again})")
    return ("Failed in the full gate run, then passed alone (load-flaky; the gate counts "
            f"green for this candidate, ledger {ledger()}):\n" + "\n".join(lines)) if lines else None
