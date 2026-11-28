"""Strictly read-only SAST/Cloudflare route state and verified open-evidence reader.

Reads ~/.gearbox-state/repo-health/<repo-hash>/ (markers, digest-verified findings.json,
last-refusal.json, last-sentry.json) and .claude/exposure.json without creating
.claude/health/ or ~/.gearbox-state directories and without requiring Node or the vendored
Cloudflare validator tree.
"""
from pathlib import Path

import card
import exposure
import runlog

UNHASHED_NOTE = (
    "Confirmed and lead records below come from digest-verified findings.json files. "
    "Markdown reports (architecture.md, REPORT.md, FINDINGS-DETAIL.md, NEEDS-VALIDATION.md) "
    "are unhashed supplementary prose and are not covered by the acceptance digest."
)


def _route_kind(r):
    """Route kind (cloudflare-quick|sentry|sentry-downgrade|error) without Node or vendor/."""
    exp, raw = exposure._exposure(r)
    if exp["error"]:
        return "error"
    if exp["exposed"]:
        return "error" if exposure.head(r) is None else "cloudflare-quick"
    acknowledged = bool(exp["reason"]) and raw is not None and raw == exposure._show(r, "HEAD")
    try:
        downgrade = not acknowledged and exposure.was_exposed(r)
    except ValueError:
        return "error"
    return "sentry-downgrade" if downgrade else "sentry"


def _read_sidecar(path):
    try:
        data = runlog.loads(runlog.read_file(path))
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_run_mark(run_dir):
    if not run_dir.is_dir() or run_dir.is_symlink():
        return None
    try:
        return runlog._read_mark(run_dir / runlog.ACCEPTED)
    except (OSError, ValueError):
        return None


def _verified_records(run_dir):
    """Non-rejected findings indexed by fingerprint from a digest-verified run, or None."""
    if not run_dir.is_dir() or run_dir.is_symlink() or not runlog._accepted(run_dir):
        return None
    try:
        raw = runlog.loads(runlog.read_file(run_dir / "findings.json"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    by_fp = {}
    for rec in raw:
        if isinstance(rec, dict) and rec.get("verdict") in ("confirmed", "needs_validation"):
            fp = rec.get("fingerprint")
            if isinstance(fp, str):
                by_fp.setdefault(fp, []).append(rec)
    return by_fp


def _scan_runs(parent):
    """(latest_complete, verified_runs_newest_first, verified_by_name, any_unverified_non_repair)."""
    names = list(dict.fromkeys(reversed(runlog._accepted_names(parent))))
    latest_complete = None
    found_latest = False
    verified_runs = []
    any_unverified = False
    for name in names:
        run_dir = parent / name
        mark = _read_run_mark(run_dir)
        if mark is not None and mark.get("status") == "repair":
            continue
        if not found_latest:
            found_latest = True
            latest_complete = mark.get("complete") if mark is not None else None
        recs = _verified_records(run_dir) if mark is not None else None
        if recs is None:
            any_unverified = True
        else:
            verified_runs.append({
                "name": name, "dir": run_dir,
                "complete": mark.get("complete"), "records": recs})
    return latest_complete, verified_runs, {v["name"]: v for v in verified_runs}, any_unverified


def _needs_deployment(rec):
    vp = rec.get("validation_plan")
    if not isinstance(vp, dict):
        return False
    dep = vp.get("deployment")
    return bool(dep.strip()) if isinstance(dep, str) else bool(dep)


def _governing_records(item, verified_runs, verified_by_name, any_unverified):
    """(list of (run_name, run_dir, complete, rec), missing_evidence_bool) for one finding."""
    item_id = item["id"]
    ev_runs = item.get("evidence_runs")
    out = []
    if isinstance(ev_runs, list):
        missing = not ev_runs
        stopped = False
        for rn in ev_runs:
            info = verified_by_name.get(rn)
            recs = info["records"].get(item_id, []) if info else []
            if not recs:
                missing = True
                continue
            if not stopped:
                for rec in recs:
                    out.append((rn, info["dir"], info["complete"], rec))
                if info["complete"] is True:
                    stopped = True
        return out, missing
    for info in verified_runs:
        recs = info["records"].get(item_id, [])
        if recs:
            for rec in recs:
                out.append((info["name"], info["dir"], info["complete"], rec))
            if info["complete"] is True:
                break
    return out, (any_unverified or not out)


def _sentry_state(parent, cur_tree):
    rec = _read_sidecar(parent / runlog.LAST_SENTRY)
    if rec is None:
        return None, False, False, False
    rec_tree = rec.get("tree") if isinstance(rec.get("tree"), str) else ""
    sentry_current = bool(cur_tree) and rec_tree == cur_tree
    sentry_positive = bool(rec.get("sentry_finding"))
    sentry_carried = sentry_positive and not sentry_current
    sentry_rec = {
        "sentry_finding": sentry_positive,
        "status": rec.get("status"),
        "detail": rec.get("detail"),
        "tree": rec_tree,
        "sentry_current": sentry_current,
        "sentry_positive": sentry_positive,
        "sentry_carried": sentry_carried,
    }
    return sentry_rec, sentry_current, sentry_positive, sentry_carried


def _partition_open(items):
    confirmed, leads, coverage = [], [], []
    for (kind, key), meta in sorted(items.items()):
        entry = {"kind": kind, "id": key, "label": meta["label"], "ref": meta["ref"],
                 "files": meta["files"], "evidence_runs": meta.get("evidence_runs")}
        if kind == "confirmed":
            confirmed.append(entry)
        elif kind == "lead":
            leads.append(entry)
        else:
            coverage.append(entry)
    return confirmed, leads, coverage


def _inspect(repo):
    r = Path(repo).resolve()
    parent = runlog.runs_parent(r)
    cur_tree = card.tree_key(r)
    refusal = _read_sidecar(parent / runlog.LAST_REFUSAL)
    last_refusal = ({"origin": refusal.get("origin"), "reason": refusal.get("reason"),
                     "tree": refusal.get("tree")} if refusal is not None else None)
    last_sentry, sentry_current, sentry_positive, sentry_carried = _sentry_state(parent, cur_tree)

    open_set_error, latest_complete = None, None
    try:
        items, _ = runlog.open_set(r)
        latest_complete, verified_runs, verified_by_name, any_unverified = _scan_runs(parent)
    except ValueError as e:
        open_set_error, items = str(e), {}
        verified_runs, verified_by_name, any_unverified = [], {}, False

    confirmed, leads, coverage = _partition_open(items)
    missing_ids, has_deploy, records_by_key = [], False, {}
    for item in confirmed + leads:
        recs, missing = _governing_records(item, verified_runs, verified_by_name, any_unverified)
        records_by_key[(item["kind"], item["id"])] = recs
        if missing:
            missing_ids.append(item["id"])
        if any(_needs_deployment(rec) for _, _, _, rec in recs):
            has_deploy = True

    state = {
        "repo_root": str(r), "route_kind": _route_kind(r), "latest_complete": latest_complete,
        "open_confirmed": len(confirmed), "open_leads": len(leads), "open_coverage": len(coverage),
        "open_set_error": open_set_error, "missing_verified_ids": sorted(set(missing_ids)),
        "has_deployment_check": has_deploy, "last_refusal": last_refusal,
        "last_sentry": last_sentry, "sentry_current": sentry_current,
        "sentry_positive": sentry_positive, "sentry_carried": sentry_carried,
    }
    return state, confirmed, leads, coverage, records_by_key


def sast_state(repo):
    """Strictly read-only SAST/Cloudflare route state for `repo`."""
    return _inspect(repo)[0]


def show_open_payload(repo):
    """Read-only JSON payload of digest-verified open findings, coverage, and Sentry state."""
    state, confirmed, leads, coverage, records_by_key = _inspect(repo)

    def _pack(items):
        return [{
            "id": item["id"],
            "label": item["label"],
            "ref": item["ref"],
            "files": item["files"],
            "evidence_runs": item["evidence_runs"],
            "records": [{"run": rn, "run_dir": str(rdir), "complete": comp, "finding": rec}
                        for rn, rdir, comp, rec in records_by_key.get((item["kind"], item["id"]), [])],
        } for item in items]

    sentry = state["last_sentry"]
    sentry_out = None if sentry is None else {
        "sentry_finding": sentry["sentry_finding"],
        "status": sentry["status"],
        "detail": sentry["detail"],
        "tree": sentry["tree"],
        "freshness": "current" if state["sentry_current"] else "carried (prior or dirty tree)",
    }
    return {
        "repo_root": state["repo_root"],
        "route_kind": state["route_kind"],
        "latest_complete": state["latest_complete"],
        "open_set_error": state["open_set_error"],
        "missing_verified_ids": state["missing_verified_ids"],
        "has_deployment_check": state["has_deployment_check"],
        "note": UNHASHED_NOTE,
        "confirmed": _pack(confirmed),
        "leads": _pack(leads),
        "coverage": [{"kind": c["kind"], "id": c["id"], "label": c["label"], "ref": c["ref"]}
                     for c in coverage],
        "last_refusal": state["last_refusal"],
        "last_sentry": sentry_out,
    }
