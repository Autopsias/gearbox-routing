"""Refuse a session that wrote a file it declared in the MAIN checkout, not its worktree.

Incident 2026-11-20 (plan spec-builder-2026-11-20): a session wrote a declared
`_plans/<plan>/_decisions/land-followups.md` in the main checkout, land carried an
empty change, and the owner fixed it by hand. `refuse_if_declared_write_in_main`
runs at apply and says where the file belongs. All logic lives here because run.py
is at its size bound.

Rule (rule text and skip reasons: _evidence/s01/rule.md). A declared path is refused when
  1. its main-checkout copy was modified AFTER the session's latest `dispatch_started`
     (filesystem mtime, so ignored files count; git status never lists them), AND
  2. the session's worktree shows no change at that path (`git status --ignored`).
A path already dirty before dispatch passes. Orchestrator-written plan-record paths
(PLAN_RECORD) are never judged. A session with no worktree is not judged.
shortcut: a declared ignored path DELETED in the main checkout has no mtime and is not
caught; add a dispatch-time snapshot only if a real case shows up.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import parallel_contract as pc
import plan_worktree as pw
import safe_json as sj
import worktree as wt

# ONE list of what the orchestrator itself writes inside the plan folder of the main
# checkout, each with where the code writes it. Everything else in the plan folder
# (`_decisions/`, `_evidence/`) is a session deliverable and IS judged when declared.
PLAN_RECORD = {
    "PLAN.html": "run.py:232 PLAN_HTML",
    "run_state.json": "run_state_io.py:73",
    "run.ndjson": "run_state_io.py:513",
    "HALT_NOTICE.txt": "run_state_io.py:143",
    ".lock": "run_state_io.py:349",
    "manifest.json": "plan_mutate.py:368",
    "spec.json": "plan_mutate.py:367",
    "_changelog.ndjson": "plan_mutate.py:115",
    "_checkpoint_answers.ndjson": "findings_digest.py:22",
    "_plan_worktree.json": "plan_worktree.py:58",
    "_plan_worktree.json.bak": "ship_state_io durable_write_json backup of the line above",
    "_closeouts": "plan_mutate.py:117",
    "_verify_state": "run.py:3644",
    "_shipping_state": "ship_state_io.py:126",
    "_shipping_locks": "ship_locks.py:98",
    "_worktrees": "worktree.py:219",
    "LAND_NOTICE.txt": "plan_record.py:27 RUNTIME_LOCAL",
    ".land-run.lock": "lease_guard.py:247",
    "_codex": "apply_guards.py missing_dispatch_receipt docstring (shared plan _codex/)",
}


def dispatched_at(plan_dir, session_id):
    """Epoch of the session's newest `dispatch_started` (run_state_io.log_event writes `ts`), or None."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    ts = None
    for ln in lines:
        rec = sj.load_dict(ln)
        if rec and rec.get("event") == "dispatch_started" and session_id in sj.get_list(rec, "session_ids", []):
            ts = rec.get("ts")
    try:
        return datetime.fromisoformat(ts).timestamp() if ts else None
    except ValueError:
        return None


def declared_paths(touches):
    """(paths, not_paths) from a comma/newline string or a list. A path is relative, has no
    whitespace and no `..`; anything else (prose, absolute, empty after cleaning) is returned in not_paths."""
    paths, junk = [], []
    for p in pc._touch_paths(touches):  # noqa: SLF001 — the one parser the plan builder also uses
        if p in ("", ".") or p.startswith("/") or ".." in p.split("/") or any(c.isspace() for c in p):
            junk.append(p)
        else:
            paths.append(p)
    return paths, junk


def _worktree_of(plan_dir, session_id):
    """The session's checkout: its isolated member worktree, else the plan worktree; None = shared tree."""
    member = wt.member_path(plan_dir, session_id)
    if member:
        return Path(member)
    state = pw.load_state(plan_dir)
    path = (state or {}).get("path")
    return Path(path) if path and Path(path).is_dir() else None


def _newest_mtime(path, skip):
    """Newest mtime of a file, or of any file under a directory; None when missing. `skip` = absolute record paths."""
    path = Path(path)
    if str(path) in skip or not os.path.lexists(path):
        return None
    if not path.is_dir():
        return path.lstat().st_mtime
    best = path.lstat().st_mtime if not any(path.iterdir()) else None
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in skip and d != ".git"]
        for f in files:
            if os.path.join(root, f) not in skip:
                m = os.lstat(os.path.join(root, f)).st_mtime
                best = m if best is None else max(best, m)
    return best


def judge(plan_dir, manifest, session_id):
    """{"verdict": "ok"|"refused"|"error"|"not-judged", ...} — never raises on git trouble."""
    plan_dir = Path(plan_dir)
    tree = _worktree_of(plan_dir, session_id)
    if tree is None:
        return {"verdict": "not-judged", "reason": "no plan worktree (shared tree)"}
    main = wt.repo_root(plan_dir)
    if main is None:
        return {"verdict": "error", "reason": f"cannot find the main checkout above {plan_dir}"}
    since = dispatched_at(plan_dir, session_id)
    if since is None:
        return {"verdict": "not-judged", "reason": "no dispatch time recorded"}
    by_item = {it.get("id"): it.get("touches") for it in (manifest.get("items") or [])}
    session = next((s for s in manifest.get("sessions") or [] if s.get("id") == session_id), {})
    paths, junk = [], []
    for iid in session.get("items") or []:
        p, j = declared_paths(by_item.get(iid))
        paths += p
        junk += j
    rc, out, err = wt.git(["status", "--porcelain", "--ignored=matching"], tree, strip=False)
    if rc != 0:
        return {"verdict": "error", "reason": f"git status failed in {tree} (rc={rc}): {err}"}
    tree_dirty = wt._status_paths(out.splitlines())  # noqa: SLF001
    rel = Path(plan_dir).resolve().relative_to(Path(main).resolve()).as_posix()
    skip = {str(Path(main) / rel / n) for n in PLAN_RECORD}
    refused, predispatch = [], []
    for p in sorted(set(paths)):
        if any(p == f"{rel}/{n}" or p.startswith(f"{rel}/{n}/") for n in PLAN_RECORD):
            continue
        mtime = _newest_mtime(Path(main) / p, skip)
        if mtime is None:
            continue
        if mtime <= since:
            predispatch.append(p)
        elif not any(pc._overlaps(p, d) for d in tree_dirty):  # noqa: SLF001
            refused.append(p)
    return {"verdict": "refused" if refused else "ok", "paths": refused, "predispatch": predispatch,
            "not_paths": junk, "main": str(main), "worktree": str(tree)}


def refuse_if_declared_write_in_main(plan_dir, manifest, session_id, log_event, out):
    """Apply guard: exit 1 with the path and where it belongs, or return."""
    res = judge(plan_dir, manifest, session_id)
    slug = Path(plan_dir).resolve().name
    for p in res.get("predispatch", []):
        log_event(plan_dir, "touch_guard_passed_predispatch", session_ids=[session_id], plan=slug, session=session_id, path=p)
    for p in res.get("not_paths", []):
        log_event(plan_dir, "touch_guard_skipped_entry", session_ids=[session_id], plan=slug, session=session_id, entry=p)
    if res["verdict"] == "error":
        log_event(plan_dir, "touch_guard_refused", session_ids=[session_id], plan=slug, session=session_id,
                  path=None, reason=res["reason"])
        out({"applied": False, "failure": "touch-guard-error", "reason": res["reason"]}, plan_dir=plan_dir)
        sys.exit(1)
    if res["verdict"] != "refused":
        return
    for p in res["paths"]:
        log_event(plan_dir, "touch_guard_refused", session_ids=[session_id], plan=slug, session=session_id, path=p)
    fix = "; ".join(f"mv {res['main']}/{p} {res['worktree']}/{p}" for p in res["paths"])
    out({"applied": False, "failure": "declared-write-in-main-checkout",
         "reason": f"{session_id} declared {res['paths']} but wrote it in the main checkout {res['main']}, "
                   f"not in its worktree {res['worktree']}. Land would carry nothing. Move each file "
                   f"into the worktree ({fix}) and apply again."}, plan_dir=plan_dir)
    sys.exit(1)
