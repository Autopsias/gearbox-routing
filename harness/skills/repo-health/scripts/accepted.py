"""Owner-accepted risks, project-root and CI-duration helpers for the probes.

Three small answers the probe instructions used to leave to the agent, each of
which made a row report the probe instead of the repo:

  * `.claude/health/accepted-risks.json` — risks the owner already ruled on.
    A matching, unexpired row is reported as accepted and does not fail the row.
  * `project_dir` / `site_packages` — where the Python manifest lives, so
    supply-chain and pip-audit run from (and against) the project, not the repo
    root or uvx's own tool environment.
  * `median_minutes` — `gh run list` has no duration field; derive it.
"""
import re
import shlex
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

from common import loads, run
from shape import detect_shape, tracked_files

KEYS = ("id", "package", "kind", "reason", "ruling", "accepted_on", "review_by",
        "expires", "fix_available", "accept_despite_fix")
KINDS = ("advisory", "maintenance")
SAFE_DIR = re.compile(r"[A-Za-z0-9._/-]+")
PY_MANIFESTS = ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile")


def load_rows(repo):
    """(valid rows, problems). A missing file is no rows; a broken one is named,
    never silently read as 'nothing accepted' (that would hide the owner's ruling)."""
    p = Path(repo) / ".claude" / "health" / "accepted-risks.json"
    if not p.exists():
        return [], []
    try:
        data = loads(p.read_text())
    except (OSError, ValueError) as e:
        return [], [f"accepted-risks.json unreadable: {e}"]
    if not isinstance(data, list):
        return [], ["accepted-risks.json is not a JSON list"]
    rows, problems = [], []
    for i, r in enumerate(data):
        bad = (not isinstance(r, dict) or set(r) != set(KEYS) or r.get("kind") not in KINDS
               or not isinstance(r.get("fix_available"), bool)
               or not isinstance(r.get("accept_despite_fix"), bool))
        if bad:
            problems.append(f"accepted-risks.json row {i} ignored: schema mismatch")
        else:
            rows.append(r)
    return rows, problems


def _expired(row, today):
    try:
        return date.fromisoformat(row["expires"]) < today
    except (TypeError, ValueError):
        return True                      # an unreadable date cannot prove validity


def classify(rows, findings, today=None):
    """findings: [(kind, ident)] — advisory id, or package name for maintenance.

    Returns (accepted_rows, expired_rows, open_findings). A row accepts a finding
    when kind and ident match, it is unexpired, and a fix-available row also has
    accept_despite_fix. An expired matching row is ignored and named."""
    today = today or date.today()
    accepted, expired, open_ = [], [], []
    for kind, ident in findings:
        hits = [r for r in rows if r["kind"] == kind
                and r["id" if kind == "advisory" else "package"] == ident]
        live = [r for r in hits if not _expired(r, today)
                and (not r["fix_available"] or r["accept_despite_fix"])]
        if live:
            accepted.append(live[0])
        else:
            open_.append((kind, ident))
            expired += [r for r in hits if _expired(r, today)]
    return accepted, expired, open_


def detail(accepted, expired, open_, problems=()):
    parts = [f"{r['id']} accepted ({r['ruling']})" for r in accepted]
    parts += [f"{r['id']} expired {r['expires']}, ignored" for r in expired]
    parts += [f"{k} {i} NOT accepted" for k, i in open_]
    parts += list(problems)
    return "; ".join(parts)


def project_dir(manifests):
    """Directory (repo-relative, '.' at the root) holding the Python manifest.

    Takes shape['manifests'] — the same detection health.py uses. A pyproject.toml
    wins over requirements files; the shallowest one wins among equals."""
    def depth(f):
        return f.count("/")
    py = [f for f in manifests if Path(f).name in PY_MANIFESTS]
    py = py or [f for f in manifests if re.match(r"requirements.*\.txt$", Path(f).name)]
    if not py:
        return None
    best = min(py, key=lambda f: (Path(f).name != "pyproject.toml", depth(f)))
    d = str(Path(best).parent)
    # The folder lands in a command the model runs: anything outside this set is
    # refused (callers fall back to "." and say so), and callers also shlex.quote.
    return d if SAFE_DIR.fullmatch(d) else None


def site_packages(repo, pdir):
    """The project venv's site-packages, found from its manifest directory."""
    base = Path(repo) / pdir
    for venv in (".venv", "venv"):
        hits = sorted((base / venv / "lib").glob("python*/site-packages"))
        if hits:
            return str(hits[-1])
    return None


def cd_prefix(pdir):
    return "" if pdir == "." else f"cd {shlex.quote(pdir)} && "


def audit_commands(repo, shape, pdir):
    """The sec.dep-vulns commands. uvx pip-audit audits uvx's OWN environment, so
    pip-audit is pointed at the project's venv; without one, install it first."""
    sp = site_packages(repo, pdir) if shape["py"] else None
    where = cd_prefix(pdir)
    venv = shlex.quote(pdir)
    py = (f"{where}pip-audit --path {shlex.quote(sp)}" if sp else
          f"install the project in {venv}/.venv (uv sync), then "
          f"pip-audit --path {venv}/.venv/lib/python*/site-packages")
    return ([py + "  (never bare pip-audit/uvx: that audits the tool's own environment)"]
            if shape["py"] else []) \
        + (["npm audit --audit-level=high"]
           if "package.json" in " ".join(shape["manifests"]) else [])


def median_minutes(runs):
    """Median duration in minutes of successful runs from
    `gh run list --json startedAt,updatedAt,conclusion,workflowName`, else None."""
    mins = []
    for r in runs:
        if r.get("conclusion") != "success":
            continue
        try:
            a = datetime.fromisoformat(r["startedAt"])
            b = datetime.fromisoformat(r["updatedAt"])
        except (KeyError, ValueError, AttributeError):
            continue
        mins.append((b - a).total_seconds() / 60)
    return round(statistics.median(mins), 1) if mins else None


def live_worktree_branches(repo):
    """Branches checked out in a linked worktree: a plan is working on them."""
    rc, out = run(["git", "worktree", "list", "--porcelain"], repo)
    if rc != 0:
        return set()
    blocks = out.split("\n\n")[1:]          # the first block is the main checkout
    return {ln[len("branch refs/heads/"):] for b in blocks for ln in b.splitlines()
            if ln.startswith("branch refs/heads/")}


def helper_command(cmd, repo, args):
    """CLI helpers behind the probes: the agent runs the tool, these answer the rest."""
    if cmd == "ci-median":      # stdin: `gh run list --json startedAt,updatedAt,conclusion,workflowName`
        return {"median_minutes": median_minutes(loads(sys.stdin.read()))}
    pdir = project_dir(detect_shape(repo, tracked_files(repo))["manifests"])
    if cmd == "project":
        return {"dir": pdir, "site_packages": pdir and site_packages(repo, pdir)}
    # args: "advisory:<id>" / "maintenance:<package>", one per finding
    found = [tuple(a.split(":", 1)) for a in args if ":" in a]
    rows, problems = load_rows(repo)
    acc, exp, open_ = classify(rows, found)
    return {"open": open_, "all_accepted": bool(found) and not open_,
            "detail": detail(acc, exp, open_, problems)}
