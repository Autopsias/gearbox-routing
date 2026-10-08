"""finish step 2, the leftovers: unpushed default-branch commits and this
plan's worktree and branches; an apply-mode finish retries their cleanup once.
Split out of finish.py (file-size bound); finish imports it back."""

import json

import land_state as lst
import plan_teardown as pt
import plan_worktree as pwt
from worktree import git


def _branch_exists(root, branch):
    return bool(branch) and git(["rev-parse", "--verify", "--quiet",
                                 f"refs/heads/{branch}"], root)[0] == 0


def _reason(detail):
    if detail is None or isinstance(detail, str):
        return detail
    return json.dumps(detail, ensure_ascii=False)[:500]


def _gone(remote_state):
    return remote_state in ("deleted", "already-gone", "not-pushed")


def leftovers(plan_dir, ctx, origin_sha, *, apply, lease_ok=lambda: True):
    root, default = ctx["root"], ctx["default"]
    n = git(["rev-list", "--count", f"{origin_sha}..refs/heads/{default}"], root)[1] \
        if origin_sha else ""
    res = {"unpushed_default": int(n) if n.isdigit() else 0,
           "plan_worktree": "not-isolated", "plan_branch": "not-isolated",
           "plan_branch_remote": None, "retried": []}
    if not ctx["isolated"]:
        return res
    path, branch = pwt.plan_worktree_path(root, ctx["slug"]), ctx["branch"]
    had_tree, had_branch = path.is_dir(), _branch_exists(root, branch)
    st = lst.load(plan_dir, ctx) or {}
    # LND-11: a remote branch land's cleanup KEPT (a dict, `{"kept": reason}`)
    # is a leftover even when the worktree and the local branch are gone.
    remote_was = (st.get("cleanup") or {}).get("branch_remote")
    had_remote = isinstance(remote_was, dict)
    remote_path = f"{ctx['remote']}/{branch}"
    res["plan_worktree"] = "preserved" if had_tree else "absent"
    res["plan_branch"] = "preserved" if had_branch else "absent"
    res["plan_branch_remote"] = remote_was
    if not apply or not (had_tree or had_branch or had_remote):
        return res
    if not lease_ok():
        # The teardown deletes; with the lease gone nothing more is written.
        why = f"lease-lost: this plan no longer holds {ctx['resource']}; not retried"
        res["retried"] = [{"target": t, "path": p, "result": "refused", "reason": why}
                          for t, p, had in (("worktree", str(path), had_tree),
                                            ("branch", branch, had_branch),
                                            ("remote-branch", remote_path, had_remote)) if had]
        return res
    if had_tree or had_branch:
        # ONE retry, never with force, through the lease-neutral teardown.
        cleanup = pt.teardown_plan_tree(plan_dir, ctx, st)
        remote_now = cleanup.get("branch_remote")
    else:
        cleanup, remote_now = {}, pt.retry_remote_branch(plan_dir, ctx, st)
    res["plan_branch_remote"] = remote_now
    if had_tree:
        gone = not path.is_dir()
        res["plan_worktree"] = "removed" if gone else "preserved"
        res["retried"].append({"target": "worktree", "path": str(path),
                               "result": "removed" if gone else "refused",
                               "reason": None if gone else _reason(cleanup.get("detail"))})
    if had_branch:
        gone = not _branch_exists(root, branch)
        res["plan_branch"] = "removed" if gone else "preserved"
        res["retried"].append({"target": "branch", "path": branch,
                               "result": "removed" if gone else "refused",
                               "reason": None if gone else _reason(cleanup.get("branch_local"))})
    if had_remote:
        gone = _gone(remote_now)
        res["retried"].append({"target": "remote-branch", "path": remote_path,
                               "result": "removed" if gone else "refused",
                               "reason": None if gone else _reason(remote_now)})
    return res
