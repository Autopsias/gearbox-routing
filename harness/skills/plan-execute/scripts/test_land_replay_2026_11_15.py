"""REP-01 — replay the 2026-11-15 land and prove the phase-2 invariants.

    pytest skills/plan-execute/scripts/test_land_replay_2026_11_15.py -q
    REPLAY_LOG=<file> pytest ...        # also writes each case's input and result there

Every case runs the public `land` command (`land.CLI["land"]`) and a real `step_push`
against a real temporary bare origin; main is moved by real pushes from a third clone,
and nothing about git is mocked. The fixture, the pre-push hook and the lease and marker
snapshots come from `test_land_phase2.py`. Every GIT_* variable is cleared before git
runs; `test_replay_git_env_cleared_decoy_untouched` proves it with a decoy repository.

The three moves of 2026-11-15 are modelled by what the contract says each one was
(main 7a927b76 -> 1d290f0d -> ac128c6f -> 96043c59): a harvest of runtime output, a
code move that touches none of the plan's files, and another plan's folder.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_phase2 as lp2  # noqa: E402
import land_state as lst  # noqa: E402
import land_writers as lw  # noqa: E402
from _land_fixture import origin_main  # noqa: E402
from test_land_phase2 import (REVIEW, _events, _gate, _hook, _land, _markers,  # noqa: E402
                              _scrub_git_env, _setup, _snap, _window)
from worktree import git  # noqa: E402

HARVEST = [("w", "projects/-Users-x-example-project/memory/project_repo_health_open_findings.md", "m\n"),
           ("w", "improve-ledger.json", "{}\n"), ("w", "evals/routing/outcomes.ndjson", "{}\n")]
CODE = [("w", "scripts/worktree_report.py", "R = 1\n"),
        ("w", "scripts/test_worktree_report.py", "T = 1\n"), ("w", "skills/repo-tidy/SKILL.md", "s\n")]
PLAN_FOLDER = [("w", "_plans/checks-before-text-2026-11-15/PLAN.html", "<html/>\n"),
               ("w", "_plans/checks-before-text-2026-11-15/spec.json", "{}\n"),
               ("w", "_plans/checks-before-text-2026-11-15/sessions/s01.prompt.md", "p\n")]
PLAN_WORK = (("src/f.py", "F = 1\n"), ("src/a.py", "A = 1\nB = 2\n"))   # appends a line; main may prepend one
SKIP = ("unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_PREFIX "
        "GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_QUARANTINE_PATH")


def _note(case, setting, moves, **result):
    """One record of the evidence file: the case, its setting, its input and its result."""
    line = f"CASE {case} [{setting}]\n  input : {moves}\n  result: " + json.dumps(result, sort_keys=True)
    if os.environ.get("REPLAY_LOG"):
        with open(os.environ["REPLAY_LOG"], "a") as f:
            f.write(line + "\n\n")


def _script(fx, name, commits):
    """A shell script that pushes each commit of `commits` to origin/main from a third
    clone. A commit is a list of ops: ("w", path, text), ("rm", path), ("mv", src, dst)."""
    sh = [f"#!/bin/sh\n{SKIP}\nd=$(mktemp -d)\ngit clone -q {shlex.quote(str(fx['origin']))} $d/c || exit 1\n"
          "cd $d/c || exit 1\ngit config user.email o@example.com && git config user.name O"]
    for i, ops in enumerate(commits):
        for op in ops:
            if op[0] == "w":
                sh.append(f"mkdir -p {shlex.quote(os.path.dirname(op[1]) or '.')} && "
                          f"printf %s {shlex.quote(op[2])} > {shlex.quote(op[1])}")
            elif op[0] == "rm":
                sh.append(f"git rm -q {shlex.quote(op[1])}")
            else:
                sh.append(f"mkdir -p {shlex.quote(os.path.dirname(op[2]) or '.')} && "
                          f"git mv {shlex.quote(op[1])} {shlex.quote(op[2])}")
        sh.append(f"git add -A && git commit -q -m 'move {i}' && git push -q origin HEAD:main || exit 1")
    path = fx["cap"] / name
    path.write_text("\n".join(sh) + "\n")
    return path


def _push_window_move(fx, iso, commits, **kw):
    """The moves arrive inside our first push window: the pre-push hook runs them once."""
    script = _script(fx, "window.sh", commits)
    _hook(fx, iso, extra=f'[ "$n" = 0 ] && sh {script}', **kw)


def _carries(iso, since=0):
    return [(r["decision"], r["reason"], r.get("route")) for r in _events(iso["plan_dir"], "land_carry")[since:]]


def _pushes(fx):
    f = fx["cap"] / "count"
    return int(f.read_text()) if f.exists() else 0


def _pushed(fx, iso):
    """Is the plan's own commit on origin/main? (Not `src/f.py`: a move may add that file.)"""
    head = lst.load(iso["plan_dir"])["plan_head"]
    return git(["merge-base", "--is-ancestor", head, "refs/heads/main"], fx["origin"])[0] == 0


def _land_pushes(fx, iso):
    """(rejected pushes, push attempts) of the land. The hook also sees the final-record push
    that follows a landed land, so it is not counted here."""
    return len(lst.load(iso["plan_dir"]).get("push_attempts", [])), _window(iso)["attempts"]


def _view(fx, iso, out, since=0):
    st = lst.load(iso["plan_dir"])
    return {"action": out["action"], "hook_pushes": _pushes(fx), "on_main": _pushed(fx, iso),
            "ack_kept": bool(st.get("ack")), "carries": _carries(iso, since),
            "moved_since_ack": st.get("moved_since_ack")}


# --------------------------------------------------------------------------
# (1) the three moves, the switch on and at shadow; (10) a carried review is never run
# --------------------------------------------------------------------------
def _three_moves(tmp_path, monkeypatch, mode):
    """Move 1 (harvest) lands between the ack and `land`; move 2 (code) lands while the
    re-run gate runs; move 3 (another plan's folder) lands inside the push window."""
    runs = tmp_path / "review-runs"
    review = {**REVIEW, "argv": ["/bin/sh", "-c", f"echo r >> {runs}", "llm_review_gate.py"]}
    flag = tmp_path / "flag-m2"
    fx, iso = _setup(tmp_path, monkeypatch, mode, gates={
        "review-gate": review,
        "marker-gate": _gate(f"if [ -f {flag} ]; then rm {flag}; sh {_script_path(tmp_path)}; fi; "
                             "test ! -f GATE_FAIL")})
    assert runs.read_text().count("r") == 1                      # phase 1 ran the review once
    since = len(_events(iso["plan_dir"], "land_carry"))
    assert subprocess.run(["sh", str(_script(fx, "m1.sh", [HARVEST]))]).returncode == 0
    _script(fx, "m2.sh", [CODE])
    flag.touch()
    _push_window_move(fx, iso, [PLAN_FOLDER])
    return fx, iso, runs, since


def _script_path(tmp_path):
    return tmp_path / "cap" / "m2.sh"


def test_replay_three_moves_one_push_with_the_switch_on(tmp_path, monkeypatch):
    fx, iso, runs, since = _three_moves(tmp_path, monkeypatch, "on")
    out = _land(iso)
    assert out["action"] == "landed", out
    assert _land_pushes(fx, iso) == (1, 2) and _pushed(fx, iso)        # one rejected push, ONE that landed
    files = git(["ls-tree", "-r", "--name-only", "refs/heads/main"], fx["origin"])[1].split("\n")
    assert {p for _, p, *_ in HARVEST + CODE + PLAN_FOLDER} <= set(files)   # every move is on main
    assert [c[:2] for c in _carries(iso, since)] == [("carry", None)] * 2
    assert [c[2] for c in _carries(iso, since)] == ["harvest", "code"]
    ev = _window(iso)
    assert (ev["mode"], ev["outcome"], ev["attempts"]) == ("on", "pushed", 2)
    assert ev["carried"] == ["review-gate"] and ev["rerun"] == ["marker-gate"]
    assert runs.read_text().count("r") == 1                      # (10) never invoked after the ack
    ack = lst.load(iso["plan_dir"])["ack"]
    assert ack["carries"][-1]["moved_paths"] == sorted(p for _, p, *_ in CODE + PLAN_FOLDER)
    assert _markers(fx["home"]) == []
    _note("1", "on", {"moves": ["harvest", "code", "plan-folder"]}, **_view(fx, iso, out, since),
          review_runs=runs.read_text().count("r"), window=ev["outcome"])
    _note("10", "on", {"review gate": "recorded pass, three main moves"},
          review_runs_after_ack=runs.read_text().count("r") - 1, carried=ev["carried"])


def test_replay_three_moves_reset_as_today_at_shadow(tmp_path, monkeypatch):
    fx, iso, runs, since = _three_moves(tmp_path, monkeypatch, "shadow")
    out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    assert _pushes(fx) == 1 and not _pushed(fx, iso)
    kinds = _carries(iso, since)
    assert kinds[0] == ("carry", None, "harvest")                # harvests always carried
    assert kinds[1] == ("reset", "harness-class", "code")        # the code move resets
    last = _events(iso["plan_dir"], "land_carry")[-1]
    assert last["shadow"] == {"decision": "carry", "reason": None}   # and the decision is logged
    ev = _window(iso)
    assert (ev["mode"], ev["outcome"]) == ("shadow", "parked") or ev["mode"] == "shadow"
    assert _markers(fx["home"]) == []
    _note("1", "shadow", {"moves": ["harvest", "code", "plan-folder"]}, **_view(fx, iso, out, since),
          logged_shadow=last["shadow"], window=ev["outcome"])


# --------------------------------------------------------------------------
# (2)(3)(4)(8)(9)(12)(13) every move that must reset, through a real push
# --------------------------------------------------------------------------
RESET_CASES = [
    ("2", "a move that touches a plan-changed path (src/a.py, which the plan also edits)",
     [[("w", "src/a.py", "C = 0\nA = 1\n")]], ("reset", "plan-path-touched")),
    ("3", "a changed gate registry", [[("w", ".claude/eval-gates.json", '{"marker-gate": {"kind": "argv", '
                                         '"argv": ["/bin/sh", "-c", "true"], "cwd": ".", "timeout": 60}}\n')]],
     ("reset", "force-reset-path")),
    ("8", "a moved path in the force-reset list", [[("w", "Makefile", "all:\n")]],
     ("reset", "force-reset-path")),
    ("9", "a classification error (settings.json that does not parse)", [[("w", "settings.json", "{\n")]],
     ("reset", "classify-error")),
    ("12", "a plan path changed, then reverted", [[("w", "src/f.py", "F = 9\n")], [("rm", "src/f.py")]],
     ("reset", "plan-path-touched")),
    ("13", "a file renamed into the plan's own directory", [[("mv", "shared.txt", "_plans/plan-a/shared.txt")]],
     ("reset", "plan-path-touched")),
]


@pytest.mark.parametrize("case,label,commits,want", RESET_CASES, ids=[c[0] for c in RESET_CASES])
def test_replay_a_move_that_must_reset_resets_and_pushes_nothing(tmp_path, monkeypatch, case, label,
                                                                 commits, want):
    fx, iso = _setup(tmp_path, monkeypatch, "on", plan_work=PLAN_WORK)   # the plan edits src/a.py too
    since = len(_events(iso["plan_dir"], "land_carry"))
    _push_window_move(fx, iso, commits)
    before = None
    out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    assert not _pushed(fx, iso) and _pushes(fx) == 1                  # our only push was the rejected one
    first = _carries(iso, since)[0]
    assert first[:2] == want, _carries(iso, since)
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["invalidated_from"]          # the owner is asked again
    assert _markers(fx["home"]) == [] and before is None
    _note(case, "on", {"label": label, "commits": commits}, **_view(fx, iso, out, since))


def test_replay_a_conflict_resets(tmp_path, monkeypatch):
    """(4) main adds the file the plan adds, with other content: the merge conflicts."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    since = len(_events(iso["plan_dir"], "land_carry"))
    commits = [[("w", "src/f.py", "F = 'main'\n")]]
    _push_window_move(fx, iso, commits)
    out = _land(iso)
    assert not _pushed(fx, iso) and _pushes(fx) == 1, out
    assert out["action"] in ("land-awaits-review", "land-parked"), out
    st = lst.load(iso["plan_dir"])
    if out["action"] == "land-parked":
        assert out["kind"] == "merge-conflict", out
    assert lst.load(iso["plan_dir"]).get("ack") in (None, st.get("ack"))
    assert _markers(fx["home"]) == []
    _note("4", "on", {"label": "an add/add conflict", "commits": commits}, **_view(fx, iso, out, since),
          kind=out.get("kind"))


# --------------------------------------------------------------------------
# (5) a rival push inside phase 2; (6) a red fast gate; (7) green alone, red together
# --------------------------------------------------------------------------
def test_replay_a_rival_push_inside_phase_2_forces_one_retry_then_pushes(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    since = len(_events(iso["plan_dir"], "land_carry"))
    _hook(fx, iso, races=1, race_dir="lib")
    out = _land(iso)
    assert out["action"] == "landed" and _land_pushes(fx, iso) == (1, 2) and _pushed(fx, iso), out
    assert all(len(_snap(fx, n)[0]) == 1 for n in (0, 1))         # the marker held through the retry
    _note("5", "on", {"rival": "one push to main inside our push window"}, **_view(fx, iso, out, since))


def test_replay_a_red_fast_gate_never_pushes(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    since = len(_events(iso["plan_dir"], "land_carry"))
    commits = [[("w", "GATE_FAIL", "red\n")]]                     # code route: carried, the fast gate runs
    _push_window_move(fx, iso, commits)
    moved = origin_main(fx)
    out = _land(iso)
    assert (out["action"], out["kind"]) == ("land-parked", "gate-failed"), out
    assert not _pushed(fx, iso) and _pushes(fx) == 1 and origin_main(fx) != moved
    assert lst.load(iso["plan_dir"])["ack"] is None
    _note("6", "on", {"move": "GATE_FAIL appears on main; the fast gate fails on the merge"},
          **_view(fx, iso, out, since), kind=out["kind"])


def test_replay_green_alone_red_together_pushes_and_the_post_push_ci_check_reports_red(
        tmp_path, monkeypatch):
    """(7) the plan's src/f.py and main's src/other.py are each fine; together `ci-check`
    fails. Phase 2 copies the recorded pass (the check is not run), pushes, and the CI check
    on the pushed sha reports red as a decision brief."""
    import finish_ci  # noqa: PLC0415
    from test_finish_ci import FAKE_GH  # noqa: PLC0415
    runs = tmp_path / "ci-runs"
    ci = {**_gate(f"echo r >> {runs}; test ! \\( -f src/f.py -a -f src/other.py \\)"), "phase2": "carry"}
    fx, iso = _setup(tmp_path, monkeypatch, "on", gates={"ci-check": ci},
                     plan_gates=("marker-gate", "review-gate", "ci-check"))
    assert runs.read_text().count("r") == 1                       # green on the plan alone
    since = len(_events(iso["plan_dir"], "land_carry"))
    commits = [[("w", "src/other.py", "O = 1\n")]]
    _push_window_move(fx, iso, commits)
    out = _land(iso)
    assert out["action"] == "landed", out
    assert runs.read_text().count("r") == 1                       # the carried check was not run
    ev = _window(iso)
    assert sorted(ev["carried"]) == ["ci-check", "review-gate"] and ev["rerun"] == ["marker-gate"]
    sha = lst.load(iso["plan_dir"])["landed_sha"]
    clone = tmp_path / "check"
    subprocess.run(["git", "clone", "-q", str(fx["origin"]), str(clone)], check=True)
    assert subprocess.run(["sh", "-c", "test ! \\( -f src/f.py -a -f src/other.py \\)"],
                          cwd=clone).returncode != 0              # red together, on the pushed tree
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    spec = {"list": {f"run list --commit {sha}": [
        {"databaseId": 1, "headSha": sha, "status": "completed", "conclusion": "failure",
         "createdAt": "2026-11-15T10:00:00Z", "workflowName": "CI", "workflowDatabaseId": 7,
         "event": "push", "attempt": 1}]},
        "jobs": {"1": [{"name": "tests", "conclusion": "failure",
                        "steps": [{"name": "pytest", "conclusion": "failure"}]}]}}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setenv("FAKE_GH_SPEC", str(tmp_path / "spec.json"))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    workflows = Path(fx["root"]) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on: push\n")
    carried = lst.load(iso["plan_dir"]).get("carried_gates")
    res = finish_ci.ci_verdict(fx["root"], sha, branch="main", timeout_s=0, gh=str(gh), carried=carried)
    assert (res["verdict"], res["match"]) == ("red", "exact") and carried
    assert all(g in res["follow_up"] for g in carried) and "Nothing was reverted" in res["follow_up"]
    assert _pushed(fx, iso)                                            # nothing was reverted
    _note("7", "on", {"plan": "adds src/f.py", "main": "adds src/other.py", "ci-check": "fails on both"},
          **_view(fx, iso, out, since), ci_check_runs=runs.read_text().count("r"),
          post_push_ci=res["verdict"], follow_up=res["follow_up"][:120])


# --------------------------------------------------------------------------
# (11) the fifth consecutive carried ci-check runs for real
# --------------------------------------------------------------------------
@pytest.mark.parametrize("carried_n,reruns", [(3, False), (4, True)])
def test_replay_the_fifth_consecutive_carried_ci_check_runs_again(tmp_path, monkeypatch, carried_n,
                                                                  reruns):
    runs = tmp_path / "ci-runs"
    ci = {**_gate(f"echo r >> {runs}; true"), "phase2": "carry"}
    fx, iso = _setup(tmp_path, monkeypatch, "on", gates={"ci-check": ci},
                     plan_gates=("marker-gate", "review-gate", "ci-check"))
    st = lst.load(iso["plan_dir"])
    st["argv_gates"]["ci-check"]["carried_n"] = carried_n         # carried this many times in a row
    lst.save(iso["plan_dir"], lst.context(iso["plan_dir"]), st)
    since = len(_events(iso["plan_dir"], "land_carry"))
    _push_window_move(fx, iso, [[("w", "src/other.py", "O = 1\n")]])
    out = _land(iso)
    assert out["action"] == "landed", out
    assert (runs.read_text().count("r") == 2) is reruns
    ev = _window(iso)
    assert ("ci-check" in ev["rerun"]) is reruns
    _note("11", "on", {"ci-check carried in a row before this move": carried_n}, **_view(fx, iso, out, since),
          ci_check_runs=runs.read_text().count("r"), carried=ev["carried"], rerun=ev["rerun"])


# --------------------------------------------------------------------------
# (14) the marker holds a push from another clone; (15) a lease without a phase
# --------------------------------------------------------------------------
def _clone_b(fx, tmp_path):
    """A second clone of the same remote whose pushes run the shipped pre-push hook."""
    b = tmp_path / "clone-b"
    subprocess.run(["git", "clone", "-q", str(fx["origin"]), str(b)], check=True)
    for k, v in (("user.email", "b@example.com"), ("user.name", "B"),
                 ("core.hooksPath", str(SCRIPTS.parents[2] / "githooks"))):
        git(["config", k, v], b, check=True)
    (b / "b.txt").write_text("b\n")
    git(["add", "-A"], b, check=True)
    git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "from clone b"], b, check=True)
    return b


def _detached_push(b):
    """Hook text: clone B pushes to main detached (it shares no pipe with the land's push, and
    never sees the land's token); the sleep lets B reach its wait before our push goes on."""
    return ('[ "$n" = 0 ] && { (unset PLAN_LAND_PHASE_TOKEN; cd ' + str(b)
            + ' && git push -q origin HEAD:main) >/dev/null 2>&1 </dev/null & sleep 2; }')


def _writer_log(fx):
    f = Path(fx["home"]) / ".claude" / lw.MARKER_DIRNAME / lw.LOG_NAME
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _wait_log(fx, n, timeout=40):
    end = time.time() + timeout
    while time.time() < end and len([r for r in _writer_log(fx) if r["outcome"] != "other-branch"]) < n:
        time.sleep(0.2)
    return [r for r in _writer_log(fx) if r["outcome"] != "other-branch"]


@pytest.mark.parametrize("crash", [False, True], ids=["land-finishes", "land-crashes"])
def test_replay_a_marker_from_clone_a_holds_a_push_from_clone_b(tmp_path, monkeypatch, crash):
    """(14) Clone B pushes to main while clone A's land holds its marker. B waits. When
    the land finishes the marker goes and B is released (`clear`); when the land crashes
    and leaves the marker, B is released at its expiry (`expired`)."""
    monkeypatch.setattr(lp2, "DEADLINE_S", 15)       # roomy: a loaded machine must not time our own push out
    monkeypatch.setattr(lp2, "MARKER_GRACE_S", 3)
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    if crash:                                                      # the window is never closed
        monkeypatch.setattr(lp2, "_unmark", lambda: None)
    b = _clone_b(fx, tmp_path)
    _hook(fx, iso, extra=_detached_push(b))
    t0 = time.time()
    out = _land(iso)
    assert out["action"] == "landed", out
    rows = _wait_log(fx, 1)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["outcome"] == ("expired" if crash else "clear"), row
    assert row["waited_s"] > 0.4                                   # it WAITED, it was not waved through
    marker = json.loads(next((Path(fx["home"]) / ".claude" / lw.MARKER_DIRNAME).glob("*.json")).read_text()) \
        if crash else None
    if crash:
        assert marker is not None and marker["token"] != "" and (time.time() - t0) >= 15   # held until the marker's expiry
    assert (_markers(fx["home"]) == []) is (not crash)
    _note("14", "on", {"clone A": "land holding its marker", "clone B": "pushes to main meanwhile",
                       "land": "crashes, marker left" if crash else "finishes"},
          action=out["action"], writer_outcome=row["outcome"], waited_s=row["waited_s"],
          marker_left=crash)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_replay_a_lease_without_a_phase_behaves_as_today(tmp_path, monkeypatch, mode):
    """(15) With the switch not `on` the lease never carries a phase field and no marker is
    written: the land is today's, and a local push to main is never held."""
    fx, iso = _setup(tmp_path, monkeypatch, mode)
    _hook(fx, iso)
    out = _land(iso)
    assert out["action"] == "landed", out
    markers, lease, token = _snap(fx)
    assert markers == [] and token == "" and "phase" not in lease and "phase_expires_at" not in lease
    assert not (Path(fx["home"]) / ".claude" / lw.MARKER_DIRNAME).exists()
    t0 = time.time()
    assert lw.wait_for_land(str(fx["origin"]), cap_s=5) == "no-marker" and time.time() - t0 < 2
    _note("15", mode, {"lease": "no phase field", "a local push to main": "asks the hook"},
          action=out["action"], lease_phase=lease.get("phase"), hook_outcome="no-marker")


def test_replay_git_env_cleared_decoy_untouched(tmp_path, monkeypatch):
    """GIT_DIR and friends name a decoy repository; the three-move replay runs; the decoy
    is byte-for-byte unchanged, so every git call went to the fixture's repositories."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=clean)
    before = {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()}
    for k, v in (("GIT_DIR", str(decoy / ".git")), ("GIT_WORK_TREE", str(decoy)),
                 ("GIT_INDEX_FILE", str(decoy / ".git" / "index"))):
        monkeypatch.setenv(k, v)
    _scrub_git_env(monkeypatch)
    assert not [k for k in os.environ if k.startswith("GIT_")]
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))             # back, to prove `_setup` scrubs them
    fx, iso, runs, since = _three_moves(tmp_path / "w", monkeypatch, "on")
    assert _land(iso)["action"] == "landed"
    assert {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()} == before
    _note("git-env", "on", {"GIT_DIR": "a decoy repository"}, decoy_untouched=True)
