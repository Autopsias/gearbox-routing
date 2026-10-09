"""LND-01 — the ONE land fixture: a real bare origin, a real primary checkout,
real locked worktrees.

Both consumers import it — ``test_land.py`` and ``_evidence_land_proofs.py`` — so
the tests and the shipped evidence exercise the SAME fixture and cannot drift
apart. It lives in its own module because the evidence generator was over this
repo's 500-LOC bound with it inline, and the ratchet was right: builders and
proofs are two things.

Nothing here fakes anything. `run_cli` shells out to the real ``run.py``, the
worktrees are created by the production ``plan_worktree.ensure_plan_worktree``,
and ``main_side`` advances ``origin/main`` from a THIRD clone — the shape another
plan landing first actually has.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
import plan_worktree as pwt  # noqa: E402
from worktree import git  # noqa: E402

RUN_PY = SCRIPTS / "run.py"
GATE_ARGV = ["/bin/sh", "-c", "test ! -f GATE_FAIL"]


def _built_plan(root, slug):
    """A plan made by plan-builder's own ``build`` (spec.json, real PLAN.html), so
    ``add-session`` and ``begin`` can run on it. Stamped v7: isolated."""
    sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))
    import build_plan  # noqa: PLC0415
    item = f"{slug}-i1"
    d = root / "_plans" / slug
    build_plan.build({
        "title": f"Plan {slug}", "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": item, "title": "I1", "category": "work",
                   "research_status": "skipped", "research_reason": "fixture"}],
        "phases": [],
        "sessions": [{"id": "s01", "title": "S01", "model": "Sonnet", "reasoning": "medium",
                      "items": [item], "prompt": "do the work",
                      "verify": {"gates": ["marker-gate"]}}],
        "infographic": {"type": "phase-journey", "title": "t",
                        "phases": [{"num": 1, "name": "P1", "items": [item]}],
                        "anchor_now": {"name": "a", "tagline": "b"},
                        "anchor_goal": {"name": "c", "tagline": "d"}}},
        d, project_root=str(root))
    m = json.loads((d / "manifest.json").read_text())
    (d / "manifest.json").write_text(json.dumps({**m, "plan_schema_version": 7}, indent=2))
    return d


def build_repo(tmp, slugs=("plan-a",), *, gates=None, built=False):
    """A primary checkout on ``main`` with a real ``origin`` and N plan dirs.
    ``gates`` adds registry entries; ``built`` makes each plan with plan-builder."""
    tmp = Path(tmp)
    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / "shared.txt").write_text("base\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / ".claude").mkdir()
    # An EMPTY shared default in the repo's own tree: land reads that copy before
    # the runtime one (land_at_land._bundled), so the runtime's at_land
    # llm-review-high never starts a real model review inside a land test.
    ref = root / "skills" / "plan-execute" / "references"
    ref.mkdir(parents=True)
    (ref / "eval-gates.default.json").write_text("{}\n")
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "argv": GATE_ARGV, "cwd": ".", "timeout": 120},
         **(gates or {})}))
    plans = {}
    for slug in slugs:
        if built:
            plans[slug] = _built_plan(root, slug)
            continue
        d = root / "_plans" / slug
        d.mkdir(parents=True)
        (d / "PLAN.html").write_text("<html>base</html>\n")
        (d / "manifest.json").write_text(json.dumps(
            {"plan_schema_version": 7,
             "sessions": [{"id": "s01", "verify": {"gates": ["marker-gate"]}}]}))
        plans[slug] = d
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    # `git init` + `remote add` + `push -u` does NOT create refs/remotes/origin/HEAD
    # (measured: `fatal: ref refs/remotes/origin/HEAD is not a symbolic ref`).
    # Without it `_pin_base` falls back to the local branch name and §4.0's
    # "did the default branch get renamed" assertion in `land._preflight` can
    # never fire — a check whose only coverage is the path where it does nothing.
    git(["remote", "set-head", "origin", "main"], root, check=True)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "post-commit").chmod(0o755)
    return {"root": root, "origin": origin, "plans": plans, "tmp": tmp}


def isolate(fx, slug):
    """The state ``begin --isolate`` leaves behind: branch + LOCKED worktree."""
    plan_dir = fx["plans"][slug]
    state = pwt.ensure_plan_worktree(plan_dir, project_root=fx["root"])
    return {"plan_dir": plan_dir, "tree": Path(state["path"]), "branch": state["branch"]}


def work(tree, rel, text, msg="session work"):
    """One session's commit ON THE PLAN BRANCH, inside the plan worktree."""
    p = Path(tree) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(["add", "--", rel], tree, check=True)
    git(["commit", "-q", "-m", msg], tree, check=True)
    return git(["rev-parse", "HEAD"], tree, check=True)[1]


def main_side(fx, rel, text, msg="main moves"):
    """Advance ``origin/main`` from a THIRD clone — the shape another plan landing
    first actually has. The primary checkout is never touched."""
    other = fx["tmp"] / f"other-{int(time.time() * 1000) % 100000}"
    subprocess.run(["git", "clone", "-q", str(fx["origin"]), str(other)], check=True)
    git(["config", "user.email", "o@example.com"], other, check=True)
    git(["config", "user.name", "O"], other, check=True)
    (Path(other) / rel).parent.mkdir(parents=True, exist_ok=True)
    (Path(other) / rel).write_text(text)
    git(["add", "-A"], other, check=True)
    git(["commit", "-q", "-m", msg], other, check=True)
    git(["push", "-q", "origin", "HEAD:main"], other, check=True)
    return git(["rev-parse", "HEAD"], other, check=True)[1]


def origin_main(fx):
    return git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]


def local_main(fx):
    return git(["rev-parse", "refs/heads/main"], fx["root"], check=True)[1]


def checked_out_branch(fx):
    return git(["rev-parse", "--abbrev-ref", "HEAD"], fx["root"], check=True)[1]


def json_tail(text):
    """The JSON object `run.py` printed, ignoring any human-readable banner above it.

    `run.py` deliberately prints text ABOVE the JSON — `HALT_NOTICE.txt` in
    `cmd_status`, and the land brief in `_land_cli` — because §4.6's recovery
    commands are only copy-pasteable outside `json.dumps`. A parser that assumed
    stdout was pure JSON turned that banner into 21 `KeyError: 'action'` failures
    in one run, which is the tests misreading the product, not the product
    breaking. `_out` emits `json.dumps(..., indent=2)`, so the payload starts at
    the first line that is exactly `{`.
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.rstrip() == "{":
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    return None


def run_cli(*argv, cwd=None):
    """`run.py` as a REAL subprocess — the production path, exit code and all."""
    p = subprocess.run([sys.executable, str(RUN_PY), *[str(a) for a in argv]],
                       capture_output=True, text=True, cwd=str(cwd or SCRIPTS), timeout=900)
    payload = json_tail(p.stdout)
    if payload is None:
        payload = {"_stdout": p.stdout[-2000:], "_stderr": p.stderr[-2000:]}
    return p.returncode, payload


# --------------------------------------------------------------------------
# ARMING THE FIXTURE — a competing lander, a slow merge, a slow gate, a kill.
# These live with the fixture rather than with the proofs because both the
# tests and the evidence generator arm the same conditions, and a second copy
# is a second thing to drift.
# --------------------------------------------------------------------------
def _arm_prepush(fx, times=1):
    """A pre-push hook that advances origin/main from a third clone, ``times``
    times, then stands down — a REAL competing lander inside our push window."""
    hook = Path(fx["root"]) / ".git" / "hooks" / "pre-push"
    # `unset GIT_DIR ...` is LOAD-BEARING, not hygiene. git exports GIT_DIR (and
    # friends) into every hook, and a `git clone` + `git push` run under them
    # operates on the PUSHING repository rather than the clone — so this hook
    # re-entered itself and fired 99 times in one land, pushing OUR OWN merge as
    # the "competing" commit. Measured. Without the unset the proof
    # tests something that never happens in production.
    hook.write_text(f"""#!/bin/sh
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_PREFIX
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_QUARANTINE_PATH
C="{fx['root']}/.prepush-count"
n=$(cat "$C" 2>/dev/null || echo 0)
[ "$n" -ge {times} ] && exit 0
echo $((n + 1)) > "$C"
d=$(mktemp -d)
git clone -q "{fx['origin']}" "$d/c" || exit 0
cd "$d/c" || exit 0
git config user.email r@example.com && git config user.name R
echo "race $n" > raced-$n.txt && git add -A && git commit -q -m "race $n"
git push -q origin HEAD:main
exit 0
""")
    hook.chmod(0o755)

def _arm_slow_commit(fx):
    """Make the land-worktree merge SLOW so a kill lands inside it: `git merge`
    runs `prepare-commit-msg` before it writes the merge commit, so a kill there
    leaves MERGE_HEAD and a staged merge with no commit — the real shape."""
    hook = Path(fx["root"]) / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text(f"#!/bin/sh\n[ -f '{fx['root']}/.SLOW' ] && sleep 60\nexit 0\n")
    hook.chmod(0o755)
    (Path(fx["root"]) / ".SLOW").touch()

def _kill_when(fx, iso, ready, pick, timeout=60):
    """Start a REAL `run.py land` child, SIGKILL it once `ready(state)` holds."""
    proc = subprocess.Popen([sys.executable, str(RUN_PY), "land", str(iso["plan_dir"])],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(SCRIPTS))
    picked, deadline = None, time.time() + timeout
    while time.time() < deadline:
        st = lst.load(iso["plan_dir"]) or {}
        if ready(st):
            picked = pick(st)
            break
        time.sleep(0.3)
    proc.kill()
    proc.wait(timeout=30)
    marker = Path(fx["root"]) / ".SLOW"
    if marker.exists():
        marker.unlink()
    marker = Path(fx["root"]) / ".SLOWGATE"
    if marker.exists():
        marker.unlink()
    return proc, picked

def sl_lock_path(plan_dir, resource):
    """Where the repo lease's lock file lives — fixture-side plumbing for the
    cross-process exclusion proof (moved here from the proofs for the 500-LOC
    bound; lock manipulation is fixture arming, not a proof)."""
    import ship_locks as sl
    return sl._ship_lock_path(plan_dir, resource)


def release_lock(plan_dir, resource):
    import ship_locks as sl
    try:
        sl.release_ship_lock(plan_dir, resource)
    except Exception:                                  # noqa: BLE001
        pass
    Path(sl._ship_lock_path(plan_dir, resource)).unlink(missing_ok=True)


def _arm_slow_gate(fx):
    (Path(fx["root"]) / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 300,
                         "argv": ["/bin/sh", "-c",
                                  f"[ -f '{fx['root']}/.SLOWGATE' ] && sleep 60; "
                                  "test ! -f GATE_FAIL"]}}))
    (Path(fx["root"]) / ".SLOWGATE").touch()
