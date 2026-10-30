#!/usr/bin/env python3
"""Exposed opt-in and the Cloudflare route for repo-health --deep.

A repo says it is reachable from outside by committing .claude/exposure.json:
    {"exposed": true, "reason": "<how it is reachable>", "since": "YYYY-MM-DD"}
No file means not exposed. A broken or symlinked file is an error.

    exposure.py <repo>          route: cloudflare-quick <dir> (line 2: reason: ...),
                                sentry, sentry-downgrade <path>, or error: ... (exit 2)
    exposure.py record <repo> <status> --detail-file <path>    (Sentry routes)
    exposure.py record-route <repo>                  (an error or a downgrade)
    exposure.py check-run <out_dir> <repo> [--record --agents N]
    exposure.py prior-runs <repo>          earlier run folders, newest first
    exposure.py open-items <repo>          items still open from earlier recorded runs
    exposure.py repair-log <repo>          rebuild unreadable acceptance state (exit 2: refused,
                                           nothing written, when any marker is unreadable)

Run-derived text (finding titles, blockers, the reason) is recorded by this
script through health.set_result, never pasted into a shell by the agent.
"""
import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runlog import (ACCEPTED, ACCEPTED_LOG, DOCS, KINDS, LOCK, MAX_DOC, MAX_NS,  # noqa: E402,F401
                    RUN_NAME, TooDeep, _accepted, _accepted_names, _digest, _newest, accept, bad_text,
                    cf_output_dir, loads, open_set, prior_runs, read_file, read_why, repair_log,
                    repo_lock, runs_parent)

SKILL = Path(__file__).resolve().parent.parent
VENDOR = SKILL / "vendor" / "cloudflare-security-audit"
# The vendored tree check_run was written against. A re-pin must re-read the
# run contract (run-metadata.json, ledger states, findings branches) first.
CONTRACT_TREE = "ccbc33ed119b28c3ef7b44a7e2081ab191ad63af"
EXPOSURE = Path(".claude") / "exposure.json"
# the only files repo-health itself writes in a repo (card.save_card, health_render.render)
GENERATED = tuple(f".claude/health/{n}" for n in ("scorecard.json", "HEALTH.html", "history.jsonl"))
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEP = " | "
MAX_DETAIL = 20000            # characters of a Sentry-route detail file
MAX_OPT_IN = 64 * 1024        # bytes of exposure.json
MAX_LINE = 2000               # characters of a crash line on the scorecard


# ---------------------------------------------------------------- opt-in file

def read_exposure(repo):
    return _exposure(repo)[0]


def _exposure(repo):
    """(parsed opt-in, the exact bytes it was parsed from, or None)."""
    path = Path(repo).resolve() / EXPOSURE
    out = {"exposed": False, "reason": None, "since": None, "error": None}

    def bad(why):
        return {**out, "error": f"{path}: {why}"}, None

    # A symlink lets an uncommitted target decide the route: refuse it.
    if path.parent.is_symlink() or path.is_symlink():
        return bad("must be a regular file in the repo, not a symlink")
    try:                                # read_file: a FIFO or a link refused, never blocked on
        text = read_file(path, MAX_OPT_IN)
    except FileNotFoundError:
        return out, None
    except (OSError, ValueError) as e:
        return bad(f"not readable ({read_why(e, path)})")
    raw = text.encode()
    try:                                # loads: too deep a nesting is a ValueError too
        data = loads(text)
    except ValueError as e:
        return bad(f"not readable JSON ({e})")
    if not isinstance(data, dict):
        return bad("must be a JSON object")
    if not isinstance(data.get("exposed"), bool):
        return bad('"exposed" must be true or false')
    exposed = data["exposed"]
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        return bad('"reason" must be a string')
    if bad_text(reason):
        return bad('"reason" must be valid UTF-8 text')
    reason = " ".join((reason or "").split()) or None
    if exposed and not reason:
        return bad('"exposed": true needs a non-empty "reason"')
    since = data.get("since")
    if since is not None and not (isinstance(since, str) and DATE.match(since)):
        return bad('"since" must be YYYY-MM-DD')
    return {"exposed": exposed, "reason": reason, "since": since, "error": None}, raw


# ---------------------------------------------------------------- open set

def open_floor(repo, status, detail):
    """(status, detail) for the Sentry routes of a repo with items still open from the
    exposed route: an open confirmed finding keeps fail, any other open item at least
    warn, and the detail names them. Nothing open: unchanged."""
    try:
        items, _ = open_set(repo)
    except ValueError as e:
        return "pending", SEP.join(x for x in (detail, f"open-set state unreadable: {e}") if x)
    if not items:
        return status, detail
    names = sorted(f"{k} {i['label']}" for (k, _), i in items.items())
    line = "still open from the exposed route: " + ", ".join(
        names[:5] + ([f"(+{len(names) - 5} more)"] if len(names) > 5 else []))
    if any(k == "confirmed" for k, _ in items):
        status = "fail"
    elif status == "pass":
        status = "warn"
    return status, detail if line in detail else SEP.join(x for x in (detail, line) if x)


def _git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def head(repo):
    rc, out = _git(repo, "rev-parse", "--verify", "HEAD")
    return out.strip() if rc == 0 else None


def dirty(repo, *paths):
    """Changed or untracked paths, except the files repo-health writes (GENERATED);
    any other file under .claude/health/, tracked or not, is source like the rest."""
    rc, out = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    if rc:
        return ["git status failed"]
    paths = [line[3:] for line in out.splitlines() if line.strip()]
    return [p for p in paths if " -> " in p or p not in GENERATED]


def dirty_message(changed):
    more = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
    return "worktree dirty, commit or stash first: " + ", ".join(changed[:5]) + more


def _show(r, rev, strict=False):
    """Bytes of exposure.json at `rev` when it is a regular file there, else None.
    `strict`: a git failure raises ValueError instead (an unread version may hide
    "exposed": true); a commit without the file, or with a non-file there, is still None."""
    rc, mode = _git(r, "ls-tree", rev, "--", str(EXPOSURE))
    if rc and strict:
        raise ValueError(f"history: git ls-tree failed at commit {rev[:12]}")
    if rc or not mode.startswith(("100644 ", "100755 ")):
        return None
    p = subprocess.run(["git", "-C", str(r), "show", f"{rev}:{EXPOSURE}"], capture_output=True)
    if p.returncode and strict:
        raise ValueError(f"history: cannot read {EXPOSURE} at commit {rev[:12]}")
    return p.stdout if p.returncode == 0 else None


def was_exposed(r):
    """This clone ran the exposed route, or any commit ever set "exposed": true.

    The history check survives a fresh clone, which has no run folders yet. It
    parses each version as JSON, so any valid spelling of the key counts."""
    if (runs_parent(r) / ACCEPTED_LOG).exists():    # accept() ran here at least once
        return True
    if head(r) is None:                 # no commit yet: no history, unless git itself fails
        if _git(r, "rev-parse", "--git-dir")[0]:
            raise ValueError(f"history: {r} is not a readable git repository")
        return False
    # --full-history: a merge that drops the file must not hide the commits behind it
    rc, out = _git(r, "log", "--full-history", "--format=%H", "--", str(EXPOSURE))
    if rc:                              # fail closed, like dirty(): never "never exposed"
        raise ValueError(f"history: git log of {EXPOSURE} failed")
    for sha in out.split():
        blob = _show(r, sha, strict=True)   # outside the try: a git failure is never skipped
        try:
            data = loads(blob or b"null")
        except TooDeep:                 # it may hide "exposed": true: an error, never skipped
            raise ValueError(f"history: {EXPOSURE} at commit {sha[:12]} is nested too deeply "
                             "to read") from None
        except ValueError:              # malformed old JSON never set the key
            continue
        if isinstance(data, dict) and data.get("exposed") is True:
            return True
    return False


def _vendored_tree():
    """The git tree of the vendored folder as it sits on disk (vendor.py's hash)."""
    import vendor
    return vendor.local_tree({"name": "cloudflare-security-audit"})


def preflight():
    """What would stop check-run, known before the ~25-call audit is spent."""
    if _vendored_tree() != CONTRACT_TREE:
        return (f"check-run was written for tree {CONTRACT_TREE[:8]}; "
                "re-check the run contract after the re-pin")
    if not shutil.which("node"):
        return "node not found: the Cloudflare validators need Node"
    return None


def route(repo, make_dir=True):
    r = Path(repo).resolve()
    exp, raw = _exposure(r)
    if exp["error"]:
        return "error", exp["error"]
    if exp["exposed"]:
        if head(r) is None:
            return "error", f"{r}: not a git repository with a commit"
        changed = dirty(r)
        if changed:
            return "error", dirty_message(changed)
        blocked = preflight()
        if blocked:
            return "error", blocked
        return "cloudflare-quick", str(cf_output_dir(r)) if make_dir else None
    # Once exposed, always exposed until the owner says otherwise in a
    # COMMITTED file with a reason: a missing or uncommitted file is a downgrade.
    acknowledged = exp["reason"] and raw is not None and raw == _show(r, "HEAD")
    try:
        downgrade = not acknowledged and was_exposed(r)
    except ValueError as e:
        return "error", str(e)
    if downgrade:
        return "sentry-downgrade", str(r / EXPOSURE)
    return "sentry", None


def record(repo, status, detail):
    """Write sec.sast through health.py's own set, with no shell in between."""
    import health
    if bad_text(detail):                # last net: text that cannot be written never blocks it
        status, detail = "pending", "detail is not valid UTF-8 text: " + detail.encode(
            "utf-8", "backslashreplace").decode()
    if not (detail or "").strip():      # set_result keeps the old detail when given none
        status, detail = "pending", "empty detail: nothing recorded to stand under the status"
    health.set_result(Path(repo).resolve(), "sec.sast", status, detail)


def record_route(repo):
    r = Path(repo).resolve()
    kind, arg = route(r, make_dir=False)
    if kind == "error":
        prefix = "exposure error" if arg.startswith(str(r / EXPOSURE)) else "route error"
        record(r, "pending", f"{prefix}: {arg}")
    elif kind == "sentry-downgrade":
        import health
        cur = next((c for c in health.load_card(r)["checks"] if c["id"] == "sec.sast"), {})
        what = "missing" if not os.path.lexists(arg) else "not committed with a reason"
        line = f"route changed from cloudflare-quick to sentry: exposure.json {what} at {arg}"
        prev = cur.get("detail") or ""
        detail = prev if prev.startswith(line) else SEP.join(x for x in (line, prev) if x)
        # fail and pending (the Sentry pass did not record) stand; anything else warns
        status = cur.get("status") if cur.get("status") in ("fail", "pending") else "warn"
        record(r, *open_floor(r, status, detail))
    else:
        print(f"route {kind}: nothing to record here")


def _check_run(ns, cf_check):
    sha = head(ns.repo)
    ns.out_dir = Path(ns.out_dir).resolve()     # one path for the check, the marker and the log
    res = cf_check.check_run(ns.out_dir, ns.repo, ns.agents)
    if res["status"] != "pending" and (head(ns.repo) != sha or dirty(ns.repo)):  # once more
        res = {"status": "pending", "detail": "source changed during check-run"}
    print(res["status"], res["detail"], sep="\n")
    if ns.record:
        # pending until accept() has written the open set: a stop or crash in between
        # leaves pending on the scorecard, never a status newer than the open set
        if res["status"] != "pending":
            record(ns.repo, "pending", cf_check.cf_detail(
                ns.repo, ns.out_dir, ns.agents, f"accepting a {res['status']} result"))
            accept(ns.out_dir, res["status"], res["open"], res["digest"])
        record(ns.repo, res["status"],
               cf_check.cf_detail(ns.repo, ns.out_dir, ns.agents, res["detail"]))


def _main_check_run(args, cf_check):
    import argparse
    ap = argparse.ArgumentParser(prog="exposure.py check-run")
    ap.add_argument("out_dir"), ap.add_argument("repo")
    ap.add_argument("--record", action="store_true", help="write sec.sast itself")
    ap.add_argument("--agents", type=int, help="agent calls the audit spent")
    ns = ap.parse_args(args)
    if ns.record and ns.agents is None:
        ap.error("--record needs --agents N")
    with contextlib.ExitStack() as held:
        try:                        # the open set is read under the lock, after any earlier run
            held.enter_context(repo_lock(ns.repo))
            _check_run(ns, cf_check)
        except Exception as e:      # never leave an older result standing
            why = f"check-run error: {type(e).__name__}: {e}"[:MAX_LINE]
            if ns.record:           # a fixed detail: no further read of untrusted input
                record(ns.repo, "pending", SEP.join([why, f"output: {ns.out_dir}"]))
            print("pending", why, sep="\n")
            return 2
    return 0


def _main_record(args):
    import argparse
    ap = argparse.ArgumentParser(prog="exposure.py record")
    ap.add_argument("repo"), ap.add_argument("status")
    ap.add_argument("--detail-file", required=True,
                    help="a file you wrote; its text becomes the sec.sast detail")
    ns = ap.parse_args(args)
    kind, _ = route(ns.repo, make_dir=False)
    why = {"cloudflare-quick": "this repo is exposed: sec.sast is recorded only by "
                               "check-run --record",
           "error": "the route is an error: run record-route"}.get(kind)
    if kind == "sentry-downgrade" and ns.status == "pass":
        why = "pass on a downgraded route: record warn or fail, then run record-route"
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    status = ns.status
    try:                            # a character is at most 4 bytes
        detail = read_file(ns.detail_file, 4 * MAX_DETAIL).strip()
        if len(detail) > MAX_DETAIL:
            raise ValueError(f"over {MAX_DETAIL} characters")
    except (OSError, ValueError) as e:  # never leave an older result standing
        why = read_why(e, ns.detail_file)
        status, detail = "pending", f"detail file unreadable: {why}"
    record(ns.repo, *open_floor(ns.repo, status, detail))
    return 0


def _main_read(cmd, repo):
    try:
        runs = prior_runs(repo) if cmd == "prior-runs" else None
        items, since = open_set(repo)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    for run in runs or []:
        print(run)
    if runs is not None:
        return 0
    print(f"open items from the run accepted {since}: {len(items)}" if items
          else "no open items from earlier recorded runs")
    for (kind, key), item in sorted(items.items()):
        print(f"{kind}\t{key}\t{' '.join(str(item['label']).split())}")  # one line each
    return 0


def _main_repair_log(repo):
    try:
        with repo_lock(repo):
            read, kept = repair_log(repo)
    except ValueError as e:
        print(f"refused: {e}")
        return 2
    print(f"repaired: {read} acceptance markers read, {kept} open items kept")
    return 0


def _main_record_route(repo):
    record_route(repo)
    return 0


def _main_route(repo):
    kind, arg = route(repo)
    if kind == "error":
        print(f"error: {arg}")
        return 2
    print(kind if arg is None else f"{kind} {arg}")
    if kind == "cloudflare-quick":
        print(f"reason: {read_exposure(repo)['reason']}")
    return 0


def main(argv):
    import cf_check                     # here, not at the top: cf_check imports this module
    if argv[:1] == ["check-run"]:
        return _main_check_run(argv[1:], cf_check)
    if argv[:1] == ["record"]:
        return _main_record(argv[1:])
    if len(argv) == 2 and argv[0] in ("prior-runs", "open-items"):
        return _main_read(*argv)
    one_repo = {"repair-log": _main_repair_log, "record-route": _main_record_route}
    if len(argv) == 2 and argv[0] in one_repo:
        return one_repo[argv[0]](argv[1])
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    return _main_route(argv[0])


if __name__ == "__main__":
    sys.modules["exposure"] = sys.modules[__name__]    # one module, not two copies
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv[1:]))
