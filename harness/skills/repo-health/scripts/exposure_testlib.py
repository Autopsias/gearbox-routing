"""Shared helpers for the exposure.py and check-run tests: planted repos, run
folders and ledger units. Fixtures are small hand-made JSON; they describe no real
repo and no real finding."""

import contextlib
import html
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
import health_render as render  # noqa: E402


SCRIPT = Path(__file__).parent / "exposure.py"
GIT = shutil.which("git") or "git"  # missing git then fails loudly in subprocess
REAL_NODE = shutil.which("node")
REAL_VENDORED_TREE = exposure._vendored_tree
needs_node = pytest.mark.skipif(REAL_NODE is None,
                                reason="node not on PATH: the real validators need it")


def git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], check=True, capture_output=True)


def make_repo(path, exposure_doc=None, raw=None):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "app.py").write_text("print('hi')\n")
    (path / "src").mkdir()
    for name in SURFACES:                      # the paths unit() says were reviewed
        (path / "src" / f"{name}.py").write_text("x = 1\n")
    if exposure_doc is not None or raw is not None:
        (path / ".claude").mkdir()
        (path / ".claude" / "exposure.json").write_text(
            raw if raw is not None else json.dumps(exposure_doc))
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


SURFACES = ("api", "db", "admin", "billing", "vendor")
EXPOSED = {"exposed": True, "reason": "public HTTP API on the internet", "since": "2026-10-01"}


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, env=env)


def cov_id(refs):
    return "::".join(quote(refs[k], safe="-._~")
                     for k in ("surface", "boundary", "subsystem", "attack_class"))


def unit(surface, status="covered", **over):
    refs = {"surface": f"src/{surface}.py#handler", "boundary": "src/auth.py#require_user",
            "subsystem": "profile/quick/all-in-scope-subsystems",
            "attack_class": "ATTACK-CLASSES.md#Access control"}
    u = {"coverage_id": cov_id(refs), "canonical_refs": refs, "surface": surface,
         "boundary": "Signed-in user", "subsystem": "All in-scope subsystems",
         "attack_class": "Access control", "starting_paths": [f"src/{surface}.py"],
         "ordinary_attack_class_block": "ATTACK-CLASSES.md#Access control",
         "selected_companion_blocks": [], "excluded_blocks": [], "prior_status": "none",
         "attempts": [], "wave": 1, "status": status, "agent_id": None,
         "reviewed_paths": [], "local_checks": [], "result_fingerprints": [], "unresolved": []}
    if status in ("covered", "candidate", "blocked"):
        u.update(agent_id="hunter-1", reviewed_paths=[f"src/{surface}.py"],
                 local_checks=[{"agent_id": "hunter-1", "reviewed_paths": [f"src/{surface}.py"],
                                "invariant": "The handler checks the session user.",
                                "method": "source", "result": "The check runs first.",
                                "artifact": None}])
    if status == "in_progress":
        u.update(agent_id="hunter-1")
    u.update(over)
    return u


def lead(fp, blockers=("The proxy may strip the header.",), line=12):
    return {"verdict": "needs_validation", "fingerprint": fp, "title": f"Lead {fp}",
            "description": "A planted lead.", "claimed_root_cause": "Header trusted as identity.",
            "trace": [{"kind": "sink", "file": "src/api.py", "line": line, "scope": "handler",
                       "description": "Reads the header."}],
            "evidence": [{"file": "src/api.py", "line": line, "description": "Header read."}],
            "blockers": list(blockers),
            "validation_plan": {"deployment": "Ask the owner whether the proxy strips X-User."}}


CONFIRMED = {"verdict": "confirmed", "fingerprint": "planted-confirmed", "title": "Planted bug",
             "trace": [{"kind": "sink", "file": "src/api.py", "line": 7, "scope": "h",
                        "description": "d"}],
             "severity": {"overall_severity": "high"}}


def record_run(out, repo):
    """What check-run --record does after recording: accept with the open set."""
    res = cf_check.check_run(out, repo)
    assert res["status"] != "pending", res
    exposure.accept(out, res["status"], res["open"], res["digest"])
    return res


TODAY = datetime.now().strftime("%Y-%m-%d")


def plant(repo, findings=(), ledger=None, out=None, **meta):
    out = out or exposure.cf_output_dir(repo)
    doc = {"run_id": "planted", "repo": repo.name, "target": str(repo.resolve()),
           "source_ref": exposure.head(repo), "profile": "quick", "scope_paths": ["."],
           "budget": cf_check.AGENT_CAP, "execution_policy": "sandboxed-source-and-local-only",
           "run_status": "complete"}
    doc.update(meta)
    (out / "run-metadata.json").write_text(json.dumps({k: v for k, v in doc.items()
                                                       if v is not ...}))
    (out / "findings.json").write_text(json.dumps(          # the validator wants id order
        sorted(findings, key=lambda f: f["fingerprint"])))
    ledger = ledger if ledger is not None else [unit("api")]
    (out / "coverage-ledger.json").write_text(json.dumps(   # the validator wants id order
        sorted(ledger, key=lambda u: u["coverage_id"])))
    for name in cf_check.REPORTS:
        (out / name).write_text("# planted\n")
    return out


# 5g: a removed exposure.json is a downgrade, every run, until acknowledged
def _card_sast(repo):
    card = json.loads((repo / ".claude" / "health" / "scorecard.json").read_text())
    return next(c for c in card["checks"] if c["id"] == "sec.sast")


def _page(repo):
    render.render(repo)
    return (repo / ".claude" / "health" / "HEALTH.html").read_text()


SENTRY = "route: sentry | 1 code files across 1 scopes reviewed, 0 confirmed"


def _sentry_then_downgrade(repo, line):
    health.set_result(repo, "sec.sast", "pass", SENTRY)     # the Sentry pass records ...
    exposure.record_route(repo)                             # ... then the downgrade lands
    assert _card_sast(repo)["status"] == "warn"
    assert _card_sast(repo)["detail"] == f"{line} | {SENTRY}"
    # render may set a path-like tail apart as <code>, so look for the tail
    assert html.escape(line.split(": ", 1)[1]) in _page(repo)


# H2: an uncommitted acknowledgement never bypasses the route
ACK = {"exposed": False, "reason": "internal only now"}


# H1: run-derived text is recorded by exposure.py, never through a shell
HOSTILE = 'Bad "title" `touch BACKTICK` $(touch DOLLAR) ; touch SEMI'


def rejected(fp):
    return {"verdict": "rejected", "fingerprint": fp, "title": f"Not a bug {fp}",
            "description": "Planted.", "claimed_root_cause": "Header trusted as identity.",
            "trace": [{"kind": "sink", "file": "src/api.py", "line": 3, "scope": "handler",
                       "description": "Reads the header."}],
            "evidence": [{"file": "src/api.py", "line": 3, "description": "Header read."}],
            "reason": "The session check runs first."}


def archived_candidate():
    """A unit whose candidate attempt was archived; the live attempt is covered."""
    old = unit("api", "candidate", result_fingerprints=["cand-1"])
    attempt = {k: old[k] for k in ("wave", "status", "agent_id", "reviewed_paths",
                                   "local_checks", "result_fingerprints", "unresolved")}
    attempt["reassignment_reason"] = "The critic found an unchecked parallel path."
    live = unit("api", wave=2, agent_id="hunter-2", attempts=[attempt])
    live["local_checks"][0]["agent_id"] = "hunter-2"
    return [live]


# R3-H2: prior unresolved items must be carried or resolved (real validators)
def _accepted_prior(repo):
    prev = exposure.cf_output_dir(repo, datetime(2026, 10, 1, 9, 0, 0))
    deferred = unit("admin", "deferred", unresolved=["quick_profile_final_critic"])
    return record_run(plant(repo, [lead("lead-a")], ledger=[unit("api"), deferred], out=prev),
                      repo)


CONF = {"verdict": "confirmed", "fingerprint": "conf-1", "title": "Planted IDOR",
        "description": "A planted confirmed finding.",
        "root_cause": "The handler skips the owner check.",
        "intended_behavior": "Only the owner can read the record.",
        "trace": [{"kind": "entrypoint", "file": "src/api.py", "line": 1, "scope": "handler",
                   "description": "Takes the record id."},
                  {"kind": "sink", "file": "src/db.py", "line": 1, "scope": "load",
                   "description": "Loads any record."}],
        "evidence": [{"file": "src/api.py", "line": 1, "description": "No owner check."}],
        "conditions": [{"kind": "authentication_level", "description": "Any signed-in user."}],
        "execution": {"attacker_perspective": "A second dummy user.",
                      "payloads": ["GET /records/2"],
                      "instructions": ["Call the handler with another user's id."],
                      "observed_result": "The other user's dummy record is returned."},
        "remediation": {"strategy": "Check the owner before loading."},
        "severity": {"likelihood": {"score": "medium", "reason": "Needs an account."},
                     "impact": {"score": "high", "reason": "Cross-user read."},
                     "overall_severity": "high"},
        "confidence": {"score": "high", "reason": "Reproduced with a fixture."}}
DEFERRED_ADMIN = {"unresolved": ["quick_profile_final_critic"]}


def _run(repo, findings=(), ledger=None):
    return record_run(plant(repo, findings, ledger=ledger), repo)


def _commit_change(repo, path, text):
    (repo / path).write_text(text)
    git(repo, "commit", "-qam", f"change {path}")


def _revalidation(surface="api", fp="conf-1", prior="prior_confirmed_changed_source"):
    return unit(surface, prior_status=prior, revalidates=[fp])


def _deep_text():
    text = (Path(__file__).parent.parent / "SKILL.md").read_text()
    return " ".join(text.split("## `--deep`", 1)[1].split("\n## ", 1)[0].split())


# R5-H2: a reviewed path must be a regular file at HEAD
def _reviewing(path):
    u = unit("api", reviewed_paths=[path])
    u["local_checks"][0]["reviewed_paths"] = [path]
    return u


def _cli_record(repo, out, exposure_home):
    return cli("check-run", out, repo, "--record", "--agents", "25",
               env={**os.environ, "HOME": str(exposure_home)})


@contextlib.contextmanager
def fast(seconds=10):
    """Fail, never hang, when a FIFO would block a reader or writer."""
    class Blocked(BaseException):        # not an OSError, so no except in the code swallows it
        pass

    def blocked(*_):
        raise Blocked(f"blocked for {seconds} s: a FIFO was opened")
    old = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
