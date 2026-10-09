"""FIN-CI — the CI verdict for a landed sha. Read-only: it watches, it never fixes.

Authority: ``../references/finish-contract.md`` (frozen 2026-10-01). A red gate
is repaired BEFORE the push, in ``land`` (the ``land-repair`` directive). After
the push ``finish`` only watches CI and reports. Nothing here may push, re-run
a workflow, or edit a file.

The signature and the return shape below are FROZEN (s01); s03 built the body.
Tests: ``test_finish_ci.py`` (a fake ``gh`` on PATH, a real temporary git repo).
"""

import contextvars
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

VERDICTS = ("green", "red", "pending", "unknown")
MATCHES = ("exact", "descendant")
REASONS = ("gh-missing", "gh-unauthenticated", "no-workflows", "no-run-for-sha", "timeout")


def ci_verdict(root, sha, *, branch, timeout_s, gh="gh", base_sha=None):
    """Return the CI verdict for ``sha`` on ``branch`` in the repo at ``root``.

    ``sha`` is pinned by the caller (``finish.json``'s ``ci_sha``) and never
    re-read from a moving ref.

    Identity. This RELAXES ``references/ship-tail/ci-loop.md`` invariant 6
    (repo + head SHA + run/job correlation, log after the push timestamp), on
    purpose and only for a report-only verdict: repo + exact sha, else the
    nearest descendant; no push-timestamp check. It does not claim to follow
    invariant 6, and nothing that pushes may reuse it. EXACT first: the candidates are the
    runs whose ``headSha == sha``; ``match`` is ``"exact"``. Only when there are
    none, fall back to runs on ``branch`` whose ``headSha`` descends from ``sha``
    (``git merge-base --is-ancestor <sha> <headSha>``), taking each workflow's
    nearest descendant sha only, so two workflows may decide from different
    descendant shas, on purpose (R6); ``match`` is ``"descendant"`` and
    ``run_sha != sha``.
    The brief must say a descendant was used, because that run also tests
    commits this plan did not make.

    Aggregation: group the candidates by workflow (its ``workflowDatabaseId``:
    two workflow files can share a name). Per workflow, a ``cancelled``
    run is not a verdict: follow the newest run of the same workflow and sha.
    Every run looked at goes into ``followed_runs``. The verdict is:

    - ``red`` if ANY workflow's deciding run concluded ``failure``,
      ``timed_out`` or ``startup_failure`` (one green run never hides a red
      sibling). Returned as soon as it is known; pending siblings do not delay it.
    - ``green`` only if EVERY workflow's deciding run completed with
      ``success``, ``neutral`` or ``skipped``.
    - ``pending`` if any run is still queued or in progress at the timeout.
    - ``unknown`` otherwise (see ``reason``).

    Poll with a bound; ``timeout_s=0`` is a single look (``run.py finish
    --no-wait``). At the timeout the answer is ``pending``, never ``green``.
    Re-running ``run.py finish`` re-polls the SAME ``sha``.

    ``inherited`` (red only): ``base_sha`` is the baseline, frozen by the
    caller (``land.json``'s ``landed_base``: the origin tip the pushed merge was
    built on, never ``landed_sha^1``; ``None`` for a non-isolated plan or a
    land.json without ``landed_base``). Look up its runs by EXACT ``headSha`` only, with the
    same aggregation. ``True`` when every failed ``(workflow, job)`` here also
    failed on the baseline; ``False`` when the baseline has a completed run of
    each failing workflow and at least one of them passed there; ``None`` when
    ``base_sha`` is ``None`` or the baseline has no comparable completed run.

    Returns a dict with exactly these keys::

        {
          "verdict": "green" | "red" | "pending" | "unknown",
          "match": "exact" | "descendant" | None,   # None when no run was found
          "run_id": int | None,          # the deciding run: the first red one,
                                         # else the newest one looked at
          "run_sha": str | None,         # that run's headSha
          "failed_checks": [             # [] unless red
            {"workflow": str, "job": str, "step": str | None, "tests": [str]}
          ],
          "followed_runs": [             # every run looked at, oldest first
            {"run_id": int, "workflow": str, "head_sha": str,
             "status": str, "conclusion": str | None}
          ],
          "inherited": bool | None,      # see above; None unless red
          "reason": str | None,          # unknown/pending only: one of REASONS
        }

    ``unknown`` covers: ``gh`` missing or unauthenticated, no workflow in the
    repo, or no candidate run at all. A path filter can mean a push starts no
    run; with no descendant either, that is ``unknown`` with reason
    ``no-run-for-sha``, never ``green``.

    R6: a workflow whose exact runs are all cancelled falls back
    per workflow; see ``references/finish-contract.md`` § Revision notes.
    ``match`` is ``"descendant"`` when any deciding run is one, even when the reported
    run (``run_id``/``run_sha``) is an exact red one. Only ``push`` runs count, so
    the weekly ``schedule`` run never decides anything.
    """
    start, deadline = _clock(timeout_s)  # before auth: timeout_s bounds the whole call
    gh_path, res = _preflight(root, gh)
    if res is not None:
        return res
    while True:
        look = _look(root, gh_path, sha, branch)
        res = _verdict(root, gh_path, look, base_sha) if look else res  # failed look: keep last
        if _settled(res, min(deadline, start + NO_RUN_GRACE_S)):
            return res
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_S, remaining))
    if res is None:  # `gh run list` failed on every look: no verdict, never green
        return _result("unknown")
    res["reason"] = res["reason"] or "timeout"  # a no-run look keeps no-run-for-sha
    return res


def _preflight(root, gh):
    """``(gh path, None)`` when gh, its login and a workflow are all there; else
    ``(None, unknown result)``. Also pins every later ``gh run`` call to the origin repo."""
    gh_path = shutil.which(gh)
    if gh_path is None:
        return None, _result("unknown", "gh-missing")
    _REPO.set(_repo(root))  # every `gh run` call is pinned to the origin repo (see _gh)
    rc, _ = _run([gh_path, "auth", "status", "--active", "--hostname", _host(root)], root)
    if rc != 0:
        return None, _result("unknown", "gh-unauthenticated")
    workflows = Path(root) / ".github" / "workflows"
    if not any(p.suffix in (".yml", ".yaml") for p in workflows.glob("*")):
        return None, _result("unknown", "no-workflows")
    return gh_path, None


def _settled(res, grace_end):
    """Final: not pending, and not a no-run look inside the grace (a run can register late)."""
    return (res is not None and res["verdict"] != "pending"
            and not (res["reason"] == "no-run-for-sha" and time.monotonic() < grace_end))


POLL_INTERVAL_S = 30
NO_RUN_GRACE_S = 90  # bounded wait for a push's run to register; never past the deadline
_CALL_TIMEOUT_S = 60
_DEADLINE = contextvars.ContextVar("finish_ci_deadline", default=None)
_REPO = contextvars.ContextVar("finish_ci_repo", default=None)
_FIELDS = ("databaseId,headSha,status,conclusion,createdAt,workflowName,workflowDatabaseId,"
           "event,attempt")
_PASS = ("success", "neutral", "skipped")
_RED = ("failure", "timed_out", "startup_failure")
_FAILED_TEST = re.compile(r"\bFAILED (\S+::\S+)")
_NO_JOB = "(no failed job reported)"  # jobs lookup failed or named no red job
_SCP_HOST = re.compile(r"^(?:[^@/:]+@)?([^/:]+):(?!//)")


def _result(verdict, reason=None, **fields):
    out = {"verdict": verdict, "match": None, "run_id": None, "run_sha": None,
           "failed_checks": [], "followed_runs": [], "inherited": None, "reason": reason}
    out.update(fields)
    return out


def _clock(timeout_s):
    """``(start, deadline)``; arms ``_run``'s per-call cap (none for a single look)."""
    start = time.monotonic()
    deadline = start + max(0, timeout_s)
    _DEADLINE.set(deadline if timeout_s > 0 else None)
    return start, deadline


def _run(argv, cwd):
    """``(returncode, stdout)``; ``(None, "")`` when it cannot start or times out.
    Each call is capped by what is left of ``ci_verdict``'s deadline; none starts past it.
    ``GH_REPO`` is dropped from every child's environment: no variable may redirect a lookup."""
    end = _DEADLINE.get()
    cap = _CALL_TIMEOUT_S if end is None else min(_CALL_TIMEOUT_S, end - time.monotonic())
    if cap <= 0:
        return None, ""
    env = {k: v for k, v in os.environ.items() if k != "GH_REPO"}
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=cap, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return p.returncode, p.stdout


def _gh(gh, root, args):
    """Every ``gh run`` call goes through here, with ``--repo`` set to the origin repo.
    Origin unparseable: no ``--repo`` (gh's own failure then reads as unknown); never GH_REPO."""
    repo = _REPO.get()
    return _run([gh, *args, *(["--repo", repo] if repo else [])], root)


def _gh_json(gh, root, args):
    rc, out = _gh(gh, root, args)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _list_runs(gh, root, selector):
    """Push runs for ``selector`` (``--commit X`` or ``--branch B``); None on error.
    ``--limit 100``: gh's default of 20 can push the run we need off the page."""
    data = _gh_json(gh, root, ["run", "list", *selector, "--event", "push",
                               "--limit", "100", "--json", _FIELDS])
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict) and r.get("event") == "push"
            and isinstance(r.get("headSha"), str) and isinstance(r.get("databaseId"), int)]


def _key(run):
    return (str(run.get("createdAt") or ""), run["databaseId"])


def _wf(run):
    return str(run.get("workflowName") or "?")


def _wf_key(run):
    """Workflow identity: its database id. Two workflow files may share a name."""
    wid = run.get("workflowDatabaseId")
    return wid if isinstance(wid, int) else _wf(run)


def _by_workflow(runs):
    groups = {}
    for r in runs:
        groups.setdefault(_wf_key(r), []).append(r)
    return groups


def _decide(runs):
    """The newest run that is not cancelled, or None when every run was cancelled."""
    live = [r for r in runs
            if not (r.get("status") == "completed" and r.get("conclusion") == "cancelled")]
    return max(live, key=_key) if live else None


def _state(run):
    if run.get("status") != "completed":
        return "pending"
    if run.get("conclusion") in _RED:
        return "red"
    return "green" if run.get("conclusion") in _PASS else "unknown"


def _git(root, *args):
    return _run(["git", "-C", str(root), *args], None)


def _remote(root):
    rc, out = _git(root, "remote")
    names = out.split() if rc == 0 else []
    return "origin" if "origin" in names else (names[0] if len(names) == 1 else None)


def _origin(root):
    """``(host, path)`` of the repo's remote URL; ``(None, None)`` when unparseable
    (a local path). ponytail: an ssh-config host alias is taken literally."""
    remote = _remote(root)
    rc, url = _git(root, "remote", "get-url", remote) if remote else (1, "")
    url = url.strip() if rc == 0 else ""
    if "://" in url:
        u = urlparse(url)
        return u.hostname, u.path
    m = _SCP_HOST.match(url)
    return (m.group(1), url[m.end():]) if m else (None, None)


def _host(root):
    """The GitHub host of the repo's remote, so the auth check ignores other hosts.
    Unparseable (a local path) -> ``GH_HOST``, else ``github.com`` (gh's default)."""
    return _origin(root)[0] or os.environ.get("GH_HOST") or "github.com"


def _repo(root):
    """``[HOST/]OWNER/NAME`` for gh's ``--repo``, from the remote URL; None if unparseable."""
    host, path = _origin(root)
    parts = (path or "").strip("/").removesuffix(".git").split("/")
    if not host or len(parts) != 2 or not all(parts):
        return None
    return "/".join(parts) if host == "github.com" else "/".join([host, *parts])


def _fetch(root, branch):
    remote = _remote(root)
    if remote:
        _git(root, "fetch", "--quiet", remote, branch)


def _distance(root, sha, head):
    """Commits from ``sha`` to ``head`` when ``head`` descends from it, else None."""
    if _git(root, "merge-base", "--is-ancestor", sha, head)[0] != 0:
        return None
    rc, out = _git(root, "rev-list", "--count", f"{sha}..{head}")
    return int(out) if rc == 0 and out.strip().isdigit() else None


def _descendants(root, gh, sha, branch):
    """``(runs on branch whose head descends from sha, {head: distance})``, or None."""
    _fetch(root, branch)
    runs = _list_runs(gh, root, ["--branch", branch])
    if runs is None:
        return None
    dist = {}
    for head in {r["headSha"] for r in runs if r["headSha"] != sha}:
        d = _distance(root, sha, head)
        if d is not None:
            dist[head] = d
    return [r for r in runs if r["headSha"] in dist], dist


def _nearest(runs, dist):
    """Walk one workflow's descendant shas nearest first; the first with a run
    that is not cancelled decides. Returns ``(deciding run or None, looked at)``."""
    looked = []
    for head in sorted({r["headSha"] for r in runs}, key=lambda h: (dist[h], h)):
        on_head = [r for r in runs if r["headSha"] == head]
        looked += on_head
        deciding = _decide(on_head)
        if deciding:
            return deciding, looked
    return None, looked


def _look(root, gh, sha, branch):
    """One look: ``({workflow: deciding run or None}, followed, descendant workflows)``."""
    exact = _list_runs(gh, root, ["--commit", sha])
    if exact is None:
        return None
    exact = [r for r in exact if r["headSha"] == sha]
    followed = list(exact)
    deciding = {wf: _decide(rs) for wf, rs in _by_workflow(exact).items()}
    fallback = [wf for wf, run in deciding.items() if run is None]
    used_descendant = set()
    if fallback or not exact:
        got = _descendants(root, gh, sha, branch)
        if got is None:
            return None
        by_wf = _by_workflow(got[0])
        for wf in fallback if exact else list(by_wf):
            run, looked = _nearest(by_wf.get(wf, []), got[1])
            followed += looked
            deciding[wf] = run
            if run:
                used_descendant.add(wf)
    return deciding, followed, used_descendant


def _verdict(root, gh, look, base_sha):
    deciding, followed, used_descendant = look
    followed = sorted({r["databaseId"]: r for r in followed}.values(), key=_key)
    runs = [r for r in deciding.values() if r]
    states = [_state(r) for r in runs]
    red = sorted((r for r in runs if _state(r) == "red"), key=_key)
    chosen = red[0] if red else (followed[-1] if followed else None)
    res = _result(
        "red" if red else "pending" if "pending" in states
        else "unknown" if not runs or None in deciding.values() or "unknown" in states
        else "green",
        match=("descendant" if used_descendant else "exact") if runs else None,
        run_id=chosen["databaseId"] if chosen else None,
        run_sha=chosen["headSha"] if chosen else None,
        followed_runs=[{"run_id": r["databaseId"], "workflow": _wf(r),
                        "head_sha": r["headSha"], "status": r.get("status"),
                        "conclusion": r.get("conclusion") or None} for r in followed])
    if res["verdict"] == "unknown" and (not runs or None in deciding.values()):
        res["reason"] = "no-run-for-sha"
    if red:
        pairs = [(_wf_key(run), c) for run in red
                 for c in _failed_checks(root, gh, _wf(run), run, tests=True)]
        res["failed_checks"] = [c for _, c in pairs]
        res["inherited"] = _inherited(root, gh, pairs, base_sha)
    return res


def _failed_checks(root, gh, wf, run, *, tests):
    """Failed jobs and their first failed step; failing test ids from --log-failed."""
    data = _gh_json(gh, root, ["run", "view", str(run["databaseId"]), "--json", "jobs"])
    jobs = data.get("jobs") if isinstance(data, dict) else None
    bad = [j for j in jobs if isinstance(j, dict) and j.get("conclusion") in _RED] \
        if isinstance(jobs, list) else []
    if not bad:
        return [{"workflow": wf, "job": _NO_JOB, "step": None, "tests": []}]
    found = _failed_tests(root, gh, run) if tests else {}
    out = []
    for job in bad:
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        step = next((s.get("name") for s in steps
                     if isinstance(s, dict) and s.get("conclusion") in _RED), None)
        name = str(job.get("name") or "?")
        out.append({"workflow": wf, "job": name, "step": step, "tests": found.get(name, [])})
    return out


def _failed_tests(root, gh, run):
    """``{job name: [test id]}`` from ``gh run view --log-failed`` (job<TAB>step<TAB>line)."""
    rc, out = _gh(gh, root, ["run", "view", str(run["databaseId"]), "--log-failed"])
    found = {}
    for line in out.splitlines() if rc == 0 else []:
        parts = line.split("\t", 2)
        m = _FAILED_TEST.search(parts[-1]) if len(parts) == 3 else None
        if m and m.group(1) not in found.setdefault(parts[0], []):
            found[parts[0]].append(m.group(1))
    return found


def _inherited(root, gh, pairs, base_sha):
    """True / False / None, from the baseline's EXACT runs only (see ci_verdict).
    ``pairs`` is ``[(workflow key, failed check)]``; workflows match by key."""
    if base_sha is None:
        return None
    runs = _list_runs(gh, root, ["--commit", base_sha])
    if runs is None:
        return None
    deciding = {k: _decide(rs) for k, rs in
                _by_workflow([r for r in runs if r["headSha"] == base_sha]).items()}
    base = {}
    for k in {k for k, _ in pairs}:
        run = deciding.get(k)
        if run is None or _state(run) not in ("red", "green"):
            return None  # no comparable completed run on the baseline
        base[k] = run
    if any(_state(run) == "green" for run in base.values()):
        return False
    if any(c["job"] == _NO_JOB for _, c in pairs):
        return None  # no job data here: nothing to compare job by job
    failed_there = {(k, c["job"]) for k, run in base.items()
                    for c in _failed_checks(root, gh, _wf(run), run, tests=False)}
    return True if all((k, c["job"]) in failed_there for k, c in pairs) else None
