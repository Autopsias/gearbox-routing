"""REG-01 — `plans-status`: derive active plans / stale branches / worktrees
from git's own bookkeeping plus the plan-side state this repo already writes.

Every check that touches git runs against a REAL git repository (no mocked
subprocess) — the same discipline test_worktree.py uses, for the same reason:
a parser or a classification rule that only ever sees a hand-typed string
proves nothing about what git 2.48 actually emits.

Split along the same seams as registry.py itself (was 871 LOC, over the test
file-size limit): THIS file covers git bookkeeping (`registry_git.py`) and
plan-lifecycle classification (`registry_plans.py`) — porcelain parsing,
`.lock` reads, ACTIVE/STALE-LOCK/DONE classification, and the mandatory
cross-process pid-liveness proof. Branch/worktree OWNERSHIP classification
(`registry_owners.py`) plus the end-to-end `collect()`/`render_table()` and
CLI `plans-status` subprocess tests live in the sibling
`test_registry_owners.py`, which imports this file's fixture helpers exactly
as this file already imports `test_worktree.py`'s.

Two failure modes this suite is built to catch, both drawn straight from the
s01 session brief:

  * PID LIVENESS MISTAKEN FOR PLAN LIVENESS. `run.py begin` acquires the lock
    with its own pid and exits while the dispatched session runs for minutes,
    so a plan that is genuinely mid-session shows a DEAD pid for the entire
    time. `test_cross_process_*` proves this the only way that actually
    reproduces it: a real subprocess that exits, not an in-process fixture
    holding `os.getpid()` alive for the duration of the test.
  * `prunable` STRUCTURALLY BLIND to a locked, plan-owned worktree — proved
    in `test_registry_owners.py`
    (`test_owned_locked_worktree_reported_stale_via_registry_not_prunable`).

Run: pytest skills/plan-execute/scripts/test_registry.py test_registry_owners.py -q
"""

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import registry as reg  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import worktree as wt  # noqa: E402
from test_worktree import _repo as _wt_repo  # noqa: E402

RUN_PY = SCRIPTS / "run.py"


# --------------------------------------------------------------------------
# fixture helpers (also imported by test_registry_owners.py)
# --------------------------------------------------------------------------
def _dead_pid():
    """A pid guaranteed to have just died — a real subprocess, waited on."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _write_lock(plan_dir, *, pid, age_s=0.0, host=None):
    """A `.lock` pidfile. ``host`` defaults to THIS machine because staleness is
    host-aware (LCK-02): a pid recorded on another host is never judged here, so
    a fixture naming a foreign host can never be stale whatever its pid or age.
    Pass ``host=`` explicitly to exercise that path."""
    host = host or socket.gethostname()
    Path(plan_dir).mkdir(parents=True, exist_ok=True)
    started = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    (Path(plan_dir) / ".lock").write_text(
        json.dumps({"pid": pid, "started_at": started, "host": host})
    )


def _spec(sessions):
    items = sorted({iid for s in sessions for iid in s.get("items", [])})
    return {
        "title": "Registry Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": iid, "title": iid.upper(), "category": "work"} for iid in items],
        "phases": [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": items[:1]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def _make_plan(project_root, plan_name, sessions):
    """A REAL plan (manifest.json + PLAN.html + prompts) via plan-builder's own
    builder — reused rather than hand-typed HTML, so the anchors registry.py
    reads are the real shape, not a guess at it."""
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text("{}")
    plan_dir = project_root / "_plans" / plan_name
    build_plan.build(_spec(sessions), plan_dir, project_root=str(project_root))
    return plan_dir


def _stamp_schema(plan_dir, version):
    """Pin a plan's `plan_schema_version` EXPLICITLY.

    REG-02's refusal is about plans that SHARE a working tree, and since s10's
    activation `build_plan` stamps at the isolation gate — two freshly built
    plans now isolate and correctly do not refuse each other. A test about the
    refusal therefore has to say which side of the gate its fixture is on rather
    than inherit whatever the builder happens to stamp today. The two-isolated-
    plans case is proved end to end in `test_two_plan_e2e.py`.
    """
    manifest = plan_dir / "manifest.json"
    data = json.loads(manifest.read_text())
    data["plan_schema_version"] = version
    manifest.write_text(json.dumps(data, indent=2))
    return plan_dir


def _todo_session(sid="s01"):
    return [{"id": sid, "title": sid.upper(), "model": "Sonnet",
              "items": [f"w-{sid}"], "prompt": "do"}]


# --------------------------------------------------------------------------
# git worktree list --porcelain -z parser
# --------------------------------------------------------------------------
def test_parses_a_locked_and_a_prunable_worktree(tmp_path):
    root = _wt_repo(tmp_path)
    wt.git(["branch", "feature-a"], root, check=True)
    locked = tmp_path / "wt-locked"
    wt.git(["worktree", "add", "-q", "--lock", "-b", "lockedbr", str(locked), "feature-a"],
           root, check=True)
    pruned = tmp_path / "wt-pruned"
    wt.git(["worktree", "add", "-q", "-b", "prunbr", str(pruned), "main"], root, check=True)
    import shutil
    shutil.rmtree(pruned)

    entries = {e["branch"]: e for e in reg.list_worktrees(root)}
    assert entries["lockedbr"]["locked"] is True
    assert entries["lockedbr"]["prunable"] is False  # constraint 1: git never flags a locked wt
    assert entries["prunbr"]["prunable"] is True
    assert entries["prunbr"]["locked"] is False


def test_porcelain_parse_refuses_to_guess_on_a_malformed_stanza():
    """Abort condition: a stanza missing the `worktree` line is a genuine
    format deviation, not something to silently skip."""
    with pytest.raises(wt.WorktreeError):
        reg.parse_worktree_porcelain("HEAD deadbeef\x00branch refs/heads/main\x00\x00")


# --------------------------------------------------------------------------
# lock pid-liveness detector — NEUTER-ONCE (a live pid must read alive, a dead
# one must not; a detector that can't fail either way is worse than none)
# --------------------------------------------------------------------------
def test_lock_info_distinguishes_live_pid_from_dead_pid(tmp_path):
    live_dir = tmp_path / "live"
    _write_lock(live_dir, pid=__import__("os").getpid())
    dead_dir = tmp_path / "dead"
    _write_lock(dead_dir, pid=_dead_pid())

    live = reg.lock_info(live_dir)
    dead = reg.lock_info(dead_dir)
    assert live["alive"] is True
    assert dead["alive"] is False  # the neuter: a broken alive-check would say True here too


def test_lock_info_none_when_no_lock_file(tmp_path):
    assert reg.lock_info(tmp_path / "unlocked") is None


def test_lock_info_degrades_age_s_none_on_naive_started_at(tmp_path):
    """Rework fix (MEDIUM, gate llm-review-low attempt 1): a naive (no
    tzinfo) `started_at` — a legacy or hand-edited pidfile — used to raise an
    uncaught TypeError from `datetime.now(UTC) - naive_dt`, since only
    ValueError was caught. That crashed the whole read-only plans-status run
    instead of degrading. This test fails without the fix."""
    plan_dir = tmp_path / "naive-lock"
    plan_dir.mkdir()
    (plan_dir / ".lock").write_text(json.dumps({
        "pid": _dead_pid(),
        "started_at": datetime.now().isoformat(),  # naive: no tzinfo
        "host": "test-host",
    }))
    info = reg.lock_info(plan_dir)
    assert info is not None
    assert info["age_s"] is None


@pytest.mark.parametrize("bad_contents", ["[]", '"x"', "3", "null"])
def test_lock_info_degrades_on_valid_json_that_is_not_an_object(tmp_path, bad_contents):
    """Rework fix (gate llm-review-low, attempt 3) — fourth instance of the
    same defect family: a `.lock` file that is valid JSON but not a dict
    (a list, a bare string, a number, or `null` — e.g. hand-edited or
    truncated) used to raise an uncaught AttributeError from
    `info.get("pid")`. This test fails without the fix."""
    plan_dir = tmp_path / "bad-lock"
    plan_dir.mkdir()
    (plan_dir / ".lock").write_text(bad_contents)
    info = reg.lock_info(plan_dir)
    assert info == {"pid": None, "started_at": None, "host": None, "alive": False, "age_s": None}


def test_lock_info_degrades_on_pid_field_wrong_type(tmp_path):
    """Field-level known positive (coordinator refinement): the `.lock` file
    itself is a valid JSON object — passes the whole-value `isinstance(info,
    dict)` check above — but its `pid` FIELD is a string, not an int (e.g. a
    hand-edited pidfile). `isinstance(pid, int)` already guards the
    liveness check; this proves the read degrades to alive=False instead of
    `os.kill`/`rsi._pid_alive` raising on a non-int pid."""
    plan_dir = tmp_path / "string-pid-lock"
    plan_dir.mkdir()
    (plan_dir / ".lock").write_text(json.dumps({
        "pid": "not-a-number", "started_at": datetime.now(UTC).isoformat(), "host": "test-host",
    }))
    info = reg.lock_info(plan_dir)
    assert info["pid"] == "not-a-number"
    assert info["alive"] is False  # the neuter: a broken guard would crash or say True


# --------------------------------------------------------------------------
# plan classification — ACTIVE is non-terminal regardless of pid; STALE-LOCK
# needs BOTH dead pid AND age past the threshold
# --------------------------------------------------------------------------
def test_active_plan_stays_active_with_a_dead_recent_lock(tmp_path):
    """(a)/(b) — dead pid alone must NOT demote a non-terminal plan. Only a
    STALE (old) dead lock does that — this is constraint 2's whole point."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    _write_lock(plan_dir, pid=_dead_pid(), age_s=5)  # dead, but seconds old
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"
    assert row["lock"]["alive"] is False


def test_stale_lock_needs_both_dead_pid_and_old_age(tmp_path):
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    _write_lock(plan_dir, pid=_dead_pid(), age_s=rsi.STALE_LOCK_SECONDS + 10)
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "stale-lock"


def test_stale_lock_from_another_host_stays_active(tmp_path):
    """LCK-02 — identical to the test above except for the host name: a dead pid
    and an old timestamp recorded on ANOTHER machine say nothing here, so the
    plan is reported as active (with its host) rather than reclaimable."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    _write_lock(plan_dir, pid=_dead_pid(), age_s=rsi.STALE_LOCK_SECONDS + 10,
                host="some-other-machine")
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"
    assert row["lock"]["host"] == "some-other-machine"


def test_old_lock_with_a_live_pid_is_still_active(tmp_path):
    """An old lock is not damning by itself — a live pid means the classifier
    never gets to STALE-LOCK at all."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    _write_lock(plan_dir, pid=__import__("os").getpid(), age_s=rsi.STALE_LOCK_SECONDS + 10)
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"


def test_all_terminal_sessions_classify_done_even_with_a_stale_lock(tmp_path):
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE")
    _write_lock(plan_dir, pid=_dead_pid(), age_s=rsi.STALE_LOCK_SECONDS + 10)
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "done"


def test_all_blocked_plan_is_active_and_marked_needs_attention(tmp_path):
    """An all-BLOCKED plan (no `.lock` file — `begin` already exited) must
    still classify `active` (BLOCKED is non-terminal: `dispatch.next_action`
    returns `action="blocked"`, not `"complete"`) — but it must ALSO be
    distinguishable from a plan that is still progressing, which nothing did
    before this fix. Fails without the fix: `needs_attention` would be
    KeyError/False because `plan_lifecycle_active` never told `classify_plan`
    the non-terminal state came from BLOCKED sessions."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="BLOCKED")
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"
    assert row["needs_attention"] is True


def test_normally_progressing_plan_does_not_carry_needs_attention(tmp_path):
    """A plan with an ordinary TODO session (nothing BLOCKED) must NOT carry
    the marker — it exists only to flag the all-BLOCKED case."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"
    assert row["needs_attention"] is False


def test_unreadable_manifest_classifies_unknown(tmp_path):
    plan_dir = tmp_path / "not-a-plan"
    plan_dir.mkdir()
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "unknown"


def test_classify_plan_degrades_a_list_lock_instead_of_raising(tmp_path):
    """`classify_plan` used to call `lock_info` OUTSIDE any local try/except,
    and `collect`/`plans-status` maps `classify_plan` over every plan with no
    surrounding guard either — so a raw AttributeError from `lock_info` (the
    `[]`-lock case fixed above) would have aborted the whole read-only run
    for every OTHER plan too. This is the end-to-end proof at the
    `classify_plan` level: the plan's `lifecycle` still resolves from its
    manifest/PLAN.html, and the lock degrades to the null record instead of
    raising. `lock_info` is also now routed through `_guarded` inside
    `classify_plan` so ANY future exception from it — not just this one —
    degrades the same way rather than propagating."""
    plan_dir = _make_plan(tmp_path / "proj", "fixture", _todo_session())
    (plan_dir / ".lock").write_text("[]")

    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "active"  # manifest/PLAN.html still readable
    assert row["lock"] == {
        "pid": None, "started_at": None, "host": None, "alive": False, "age_s": None,
    }


def test_manifest_valid_json_but_not_a_dict_classifies_unknown_not_crash(tmp_path):
    """Rework fix (gate llm-review-low, attempt 2) — a second malformed-input
    path the sweep found: manifest.json that parses (no JSONDecodeError) but
    is the wrong shape — a JSON list, not an object — used to raise a raw
    AttributeError out of `mio.load_manifest`'s `m.get(...)` call, uncaught
    by `plan_lifecycle_active`'s narrower `except (ManifestError, OSError,
    ValueError)`. This test fails without the fix."""
    plan_dir = tmp_path / "list-manifest"
    plan_dir.mkdir()
    (plan_dir / "manifest.json").write_text("[]")
    row = reg.classify_plan(plan_dir)
    assert row["lifecycle"] == "unknown"


def test_corrupt_manifest_is_not_a_silent_degrade(tmp_path):
    """Rework fix (gate llm-review-low, attempt 4): `plan_lifecycle_active`
    used to catch its `_DEGRADE_ERRORS` internally and return `None` WITHOUT
    recording anything into `errors` — `classify_plan` set `lifecycle =
    "unknown"` but `errors` stayed empty, so neither `collect()`'s `errors`
    dict nor `render_table`'s `[UNREADABLE — see ERRORS]` marker ever showed
    a corrupt manifest.json happened. That contradicts every OTHER degraded
    read in this module: the degrade must be legible (a question the
    operator can see), never a bland absence. This test fails without the
    fix — both at the `collect()` level (`errors` populated) and at the
    `render_table` level (the row is marked, not silently blank)."""
    root = _wt_repo(tmp_path)
    good_dir = _make_plan(root, "good-plan", _todo_session())
    _write_lock(good_dir, pid=__import__("os").getpid())

    broken_dir = root / "_plans" / "broken-manifest-plan"
    broken_dir.mkdir(parents=True)
    (broken_dir / "manifest.json").write_text("[]")  # valid JSON, wrong shape

    data = reg.collect(root, root / "_plans")
    assert "broken-manifest-plan" in data["errors"]
    assert "manifest" in data["errors"]["broken-manifest-plan"][0]

    broken_row = next(p for p in data["plans"] if p["plan"] == "broken-manifest-plan")
    assert broken_row["lifecycle"] == "unknown"
    assert broken_row["errors"] != []

    good_row = next(p for p in data["plans"] if p["plan"] == "good-plan")
    assert good_row["lifecycle"] == "active"
    assert good_row["errors"] == []

    table = reg.render_table(data)
    lines = {ln.split()[0]: ln for ln in table.splitlines() if ln.strip().startswith(("broken", "good"))}
    assert "[UNREADABLE" in lines["broken-manifest-plan"]
    assert "[UNREADABLE" not in lines["good-plan"]


# --------------------------------------------------------------------------
# MANDATORY cross-process test — the in-process fixture is exactly what hides
# the PID-liveness bug (see module docstring). This spawns `run.py begin` as
# a REAL subprocess, lets it exit, and asserts plans-status still reports the
# plan ACTIVE though the pid it left behind is dead.
#
# Defect 2 (MEDIUM, granted-attempt fix): the test used to WRITE the committed
# evidence artifact itself, deriving the path from the script location
# (`SCRIPTS.parent.parent.parent / "_plans" / ...`). That dirtied the repo
# with run-specific absolute temp paths on every `pytest` run, and the same
# test run from the deployed `~/.claude` tree would create a stray `_plans/`
# directory there. A test must never be the thing that produces a required
# deliverable — see `generate_s01_evidence` below for the deliberate, explicit
# step that does. The probe itself is factored out so both share one real
# subprocess run rather than the test faking what the generator does for real.
# --------------------------------------------------------------------------
def _cross_process_begin_probe(root_dir):
    """Spawn `run.py begin` as a real subprocess against a scratch plan under
    `root_dir`, let it exit, and return (plan_dir, result, lock, row) — the
    proof that `classify_plan` reports ACTIVE though the pid `begin` left
    behind is already dead. Pure: writes only under `root_dir`, never touches
    the repository `_plans/` tree."""
    project_root = Path(root_dir) / "proj"
    project_root.mkdir()
    plan_dir = _make_plan(project_root, "fixture", _todo_session())

    import os
    env = dict(os.environ)
    env["PLAN_EXECUTE_EGRESS_ROOT"] = str(project_root)
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "begin", str(plan_dir), "--sessions", "s01"],
        cwd=str(project_root), env=env, capture_output=True, text=True, timeout=90,
    )
    lock = json.loads((plan_dir / ".lock").read_text()) if (plan_dir / ".lock").exists() else None
    row = reg.classify_plan(plan_dir)
    return plan_dir, result, lock, row


def test_cross_process_begin_leaves_plan_active_with_dead_pid(tmp_path):
    plan_dir, result, lock, row = _cross_process_begin_probe(tmp_path)
    assert result.returncode == 0, result.stderr

    # The subprocess that wrote this pid has already exited (subprocess.run
    # blocks until it does) — this IS the "no live pid for the whole session"
    # shape the session brief measured, reproduced for real rather than typed
    # in by hand.
    assert reg.lock_info(plan_dir)["alive"] is False
    assert row["lifecycle"] == "active"


# --------------------------------------------------------------------------
# REG-02 — `begin` refuses when another plan is registry-active in the same
# repo; `--concurrent` overrides and logs the override.
# --------------------------------------------------------------------------
def _begun(plan_dir):
    """Mark a plan as having actually dispatched. `run.ndjson` is the signal
    `registry_plans.has_begun` reads; `rsi.log_event` is what writes it for
    real."""
    (Path(plan_dir) / "run.ndjson").write_text(
        '{"ts": "2026-08-23T00:00:00Z", "event": "dispatch_started"}\n')
    return plan_dir


def test_other_active_plans_excludes_self_and_non_active_lifecycles(tmp_path):
    """(reg.other_active_plans) A plan that has BEGUN and whose sessions are
    non-terminal is active; a DONE plan is not; the plan asking the question is
    never counted against itself.

    `plan-a` is explicitly marked begun. Before 2026-08-23 merely BUILDING it
    was enough — see the sibling test below for why that changed."""
    root = _wt_repo(tmp_path)
    _begun(_make_plan(root, "plan-a", _todo_session()))
    plan_b = _make_plan(root, "plan-b", _todo_session())
    plan_c_done = _make_plan(root, "plan-c-done", _todo_session())
    ab.apply_mutation(plan_c_done / "PLAN.html", "s01", status="DONE")

    others = reg.other_active_plans(plan_b)
    assert [o["plan"] for o in others] == ["plan-a"]


def test_a_plan_that_was_never_begun_is_not_a_tree_sharing_neighbour(tmp_path):
    """REG-02's subject is "who else is dispatching into this tree", and a plan
    nobody ever started is not dispatching into anything.

    `lifecycle` stays ACTIVE for it — constraint 2 is unchanged, and the row
    still says so — but the begin refusal no longer counts it. Before this,
    every drafted-and-unrun plan blocked forever: seven had piled up in gearbox
    by 2026-08-23, so every new plan needed `--concurrent`, and a flag you pass
    reflexively is a control you have switched off for the case that matters.

    The race is NOT reopened, which the second half asserts: becoming active
    requires a begin, and begin writes run.ndjson, so of two never-begun plans
    whichever starts first blocks the other."""
    root = _wt_repo(tmp_path)
    drafted = _make_plan(root, "plan-drafted", _todo_session())
    plan_b = _make_plan(root, "plan-b", _todo_session())

    row = [p for p in reg.collect(root, root / "_plans")["plans"]
           if p["plan"] == "plan-drafted"][0]
    assert row["lifecycle"] == "active"       # the vocabulary is unchanged ...
    assert row["has_begun"] is False          # ... the marker is what is new
    assert reg.other_active_plans(plan_b) == []

    # KNOWN POSITIVE — the moment it actually begins, it blocks again.
    _begun(drafted)
    assert [o["plan"] for o in reg.other_active_plans(plan_b)] == ["plan-drafted"]


def test_other_active_plans_noop_outside_a_git_repo(tmp_path):
    """No registry to scan — `begin`'s guard must stay a no-op for a plan
    built under a bare (never `git init`ed) directory, the shape most of
    this suite's non-registry tests already use."""
    plan_dir = tmp_path / "bare" / "_plans" / "p"
    plan_dir.mkdir(parents=True)
    (plan_dir / "manifest.json").write_text("{}")
    assert reg.other_active_plans(plan_dir) == []


def test_begin_refuses_when_another_plan_is_active_in_the_same_repo(tmp_path):
    """KNOWN POSITIVE — a live-pid sibling plan (plan-a) makes plan-b's
    `begin` refuse, naming plan-a's directory and pid, with nothing
    dispatched (plan-b's session never leaves TODO).

    Both plans are pinned BELOW the isolation gate: a plan that does not isolate
    dispatches into the operator's checkout, which is the collision this refusal
    exists for."""
    root = _wt_repo(tmp_path)
    plan_a = _stamp_schema(_make_plan(root, "plan-a", _todo_session()), 6)
    _write_lock(plan_a, pid=os.getpid())
    plan_b = _stamp_schema(_make_plan(root, "plan-b", _todo_session()), 6)

    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan_b, ["s01"])
    msg = str(exc.value)
    assert str(plan_a) in msg
    assert str(os.getpid()) in msg
    assert "another plan is active" in msg
    assert run._statuses(plan_b)["s01"] == "TODO"


def test_begin_concurrent_flag_overrides_and_logs_event(tmp_path):
    """FALSIFICATION CONTROL for the refusal above: the identical fixture,
    `--concurrent` passed, must proceed AND log `concurrent_begin_override`
    naming plan-a — never silently skip the log the way a check that cannot
    fail would."""
    root = _wt_repo(tmp_path)
    plan_a = _stamp_schema(_make_plan(root, "plan-a", _todo_session()), 6)
    _write_lock(plan_a, pid=os.getpid())
    plan_b = _stamp_schema(_make_plan(root, "plan-b", _todo_session()), 6)

    run.cmd_begin(plan_b, ["s01"], concurrent=True)
    events = [json.loads(ln) for ln in (plan_b / "run.ndjson").read_text().splitlines()]
    overrides = [e for e in events if e["event"] == "concurrent_begin_override"]
    assert len(overrides) == 1, events
    assert overrides[0]["active_plans"] == [str(plan_a)]
    assert run._statuses(plan_b)["s01"] == "DOING"   # the override actually let it through
    run.cmd_release(plan_b)


def test_begin_ignores_a_stale_lock_or_done_sibling_plan(tmp_path):
    """A sibling plan that is STALE-LOCK or DONE is not "active" — this is
    the ordinary resume/next-plan path, not a collision, and must dispatch
    with no `--concurrent` needed."""
    root = _wt_repo(tmp_path)
    plan_done = _make_plan(root, "plan-done", _todo_session())
    ab.apply_mutation(plan_done / "PLAN.html", "s01", status="DONE")
    plan_b = _make_plan(root, "plan-b", _todo_session())

    run.cmd_begin(plan_b, ["s01"])
    assert run._statuses(plan_b)["s01"] == "DOING"
    run.cmd_release(plan_b)


def generate_s01_evidence(out_path=None):
    """Deliberate, explicit step (never a test side effect — see Defect 2
    above) that produces the committed REG-01 cross-process proof:
    `_plans/example-isolation-plan-2026-08-20/_evidence/s01/cross-process-active.txt`.

    Run directly: `python3 skills/plan-execute/scripts/test_registry.py --generate-evidence`
    """
    out_path = Path(out_path) if out_path else (
        SCRIPTS.parent.parent.parent / "_plans" / "example-isolation-plan-2026-08-20"
        / "_evidence" / "s01" / "cross-process-active.txt"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        plan_dir, result, lock, row = _cross_process_begin_probe(Path(td))
        if result.returncode != 0:
            raise RuntimeError(f"begin subprocess failed: {result.stderr}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            "REG-01 cross-process proof — run.py begin spawned as a real subprocess, "
            "exited, plan still classified ACTIVE with a dead pid.\n\n"
            f"plan_dir: {plan_dir}\n"
            f"begin subprocess returncode: {result.returncode}\n"
            f"begin subprocess stdout (first 2000 chars):\n{result.stdout[:2000]}\n\n"
            f".lock contents after subprocess exit: {lock}\n"
            f"registry.lock_info(plan_dir): {reg.lock_info(plan_dir)}\n"
            f"registry.classify_plan(plan_dir): {row}\n"
        )
    return out_path


if __name__ == "__main__":
    if "--generate-evidence" in sys.argv:
        written = generate_s01_evidence()
        print(f"wrote {written}")
    else:
        raise SystemExit(
            "test_registry.py is a pytest suite — run `pytest test_registry.py -q`.\n"
            "To regenerate the committed REG-01 cross-process proof instead, run:\n"
            "  python3 skills/plan-execute/scripts/test_registry.py --generate-evidence"
        )
