"""Tests for agent_janitor.py — orphan detection and tree-kill.

Run: pytest scripts/test_agent_janitor.py -q  (from ~/.claude or your harness source)

Nothing about ps parsing is mocked: every Proc row comes from a real
``ps -axww`` on this machine, and the kill tests spawn real double-forked
process trees and really kill them. Where a test needs an OLD process it takes
a real snapshot and rewrites only the ``age`` field — the parsing, the argv,
the PPID and the process itself stay real.

The disk pruner and its protect-list are in test_agent_janitor_prune.py.
Fixtures live in conftest.py; row builders in janitor_helpers.py.
"""
import argparse
import os
import shutil
import subprocess
import time
import uuid
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_janitor as aj  # noqa: E402
from janitor_helpers import HOUR, TEST_SLUG, aged, fake, logged, only, reap_args  # noqa: E402


# ps parsing — real output only
# --------------------------------------------------------------------------

def test_ps_snapshot_parses_this_machine():
    procs = aj.ps_snapshot()
    by_pid = {p.pid: p for p in procs}
    assert len(procs) > 50
    assert 1 in by_pid and "launchd" in by_pid[1].command
    me = by_pid[os.getpid()]
    assert me.uid == aj.UID and me.age >= 0 and me.command


def test_parse_etime_covers_every_form_ps_emits():
    assert aj.parse_etime("26:14") == 26 * 60 + 14
    assert aj.parse_etime("01:02:03") == 3723
    assert aj.parse_etime("13-13:19:46") == 13 * 86400 + 13 * 3600 + 19 * 60 + 46
    assert aj.parse_etime("  02:28:40 ") == 2 * 3600 + 28 * 60 + 40
    # and every etime string ps actually prints right now must parse
    raw = subprocess.run(["ps", "-axo", "etime="], capture_output=True, text=True).stdout
    values = [ln for ln in raw.splitlines() if ln.strip()]
    assert values
    assert all(aj.parse_etime(v) >= 0 for v in values)


def test_dd_hh_mm_ss_etimes_are_present_and_parsed():
    """PID 1 is as old as the boot; if uptime > 1d we exercise the dd- form."""
    procs = {p.pid: p for p in aj.ps_snapshot()}
    if procs[1].age < 86400:
        pytest.skip("machine booted less than a day ago")
    fresh = aj.parse_etime(subprocess.run(["ps", "-o", "etime=", "-p", "1"],
                                          capture_output=True, text=True).stdout)
    assert abs(procs[1].age - fresh) <= 5


def test_argv0_survives_spaces_in_the_executable_path(tmp_path):
    exe = tmp_path / "Code Helper (Renderer)"
    exe.write_text("#!/bin/sh\n")
    assert aj.shell_argv0(f"{exe} --type=renderer --x") == str(exe)
    assert aj.shell_argv0("/bin/zsh -c source /x") == "/bin/zsh"
    assert aj.shell_argv0("claude") == "claude"


def test_verify_identity_matches_self_and_rejects_a_wrong_snapshot():
    me = {p.pid: p for p in aj.ps_snapshot()}[os.getpid()]
    assert aj.verify_identity(me.pid, me.identity)
    assert not aj.verify_identity(me.pid, ("Mon Jan  1 00:00:00 2001", me.command))
    assert not aj.verify_identity(me.pid, (me.lstart, "totally different command"))


# --------------------------------------------------------------------------
# hard excludes — the negatives that must NEVER be listed
# --------------------------------------------------------------------------

VSCODE_CODEX = (f"{aj.HOME}/.vscode/extensions/openai.chatgpt-<ver>-darwin-arm64"
                "/bin/macos-aarch64/codex -c features.code_mode_host=true app-server exec")
APP_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex app-server exec --listen stdio://"
CODEX_FRAMEWORK = ("/Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework"
                   "/Versions/<ver>/Helpers/browser_crashpad_handler --monitor-self")
CODE_HELPER = (f"{aj.HOME}/Applications/Cursor.app/Contents/Frameworks/Code Helper (Renderer)"
               " --type=renderer exec")
# no excluded prefix, spaces in the path, and not on this disk: only the raw
# command line can carry the "/Code Helper" evidence here
ODD_CODE_HELPER = "/srv/vendor/electron/Code Helper (Renderer) --type=renderer exec"


@pytest.mark.parametrize("command", [VSCODE_CODEX, APP_CODEX, CODEX_FRAMEWORK,
                                     CODE_HELPER, ODD_CODE_HELPER])
def test_excluded_executables_are_never_candidates(command):
    """argv[0] basename is literally `codex` for the VS Code helpers — measured
    live, none under /Applications. A substring match
    on 'codex' would tree-kill the operator's editor and GUI app."""
    proc = fake(command)
    assert aj._is_excluded(proc), proc.exe
    cands, _, _ = aj.detect(procs=[proc], registry=[])
    assert cands == []


def test_interactive_claude_tui_is_registry_not_candidate():
    proc = fake("claude", pid=999002)
    assert aj.match_pattern(proc) is None
    cands, registry, _ = aj.detect(procs=[proc])
    assert cands == []
    assert [e["pid"] for e in registry] == [999002]


def test_live_machine_dry_run_lists_no_app_or_editor_process():
    """The real, unfiltered end-to-end run on this machine."""
    cands, _, _ = aj.detect()
    for c in cands:
        assert not c.proc.exe.startswith("/Applications/"), c.proc.command
        assert ".vscode/extensions/" not in c.proc.exe, c.proc.command
        assert ".cursor/extensions/" not in c.proc.exe, c.proc.command
        assert c.proc.base != "claude", c.proc.command


# --------------------------------------------------------------------------
# classification — three classes, one killable
# --------------------------------------------------------------------------

def test_dead_session_dir_is_killable_class(session_dirs):
    _, path = session_dirs(age_hours=26)
    proc = fake(f"tail -f {path}/tasks/x.output", age=700)
    cand = aj.classify(proc, "scratchpad-ref", time.time())
    assert cand.cls == "dead-session" and cand.reason == "session-dir-dead"


def test_dead_session_below_600s_is_not_reported(session_dirs):
    _, path = session_dirs(age_hours=26)
    proc = fake(f"tail -f {path}/tasks/x.output", age=599)
    assert aj.classify(proc, "scratchpad-ref", time.time()) is None


def test_fresh_session_dir_is_unknown_not_dead(session_dirs):
    _, path = session_dirs(age_hours=0)
    proc = fake(f"tail -f {path}/tasks/x.output", age=10 * HOUR)
    cand = aj.classify(proc, "scratchpad-ref", time.time())
    assert cand.cls == "unknown" and cand.reason == "session-dir-fresh"


def test_missing_session_dir_is_unknown_not_dead():
    proc = fake(f"tail -f {aj.SCRATCHPAD_BASE}/-nope/{uuid.uuid4()}/tasks/x.output",
                age=10 * HOUR)
    cand = aj.classify(proc, "scratchpad-ref", time.time())
    assert cand.cls == "unknown" and cand.reason == "session-dir-missing"


def test_no_session_evidence_is_bare_at_four_hours():
    cmd = f"/bin/zsh -c source {aj.SHELL_SNAPSHOT_DIR}/snapshot-zsh-1.sh"
    assert aj.classify(fake(cmd, age=4 * HOUR - 1), "shell-snapshot", time.time()) is None
    cand = aj.classify(fake(cmd, age=4 * HOUR + 1), "shell-snapshot", time.time())
    assert cand.cls == "bare" and cand.reason == "no-session-evidence"


def test_session_dir_own_mtime_is_not_trusted(session_dirs):
    """A live session's dir mtime was measured a day stale while a file two levels
    down had been written seconds earlier — so liveness reads the whole tree."""
    _, path = session_dirs(age_hours=26)
    Path(f"{path}/tasks/x.output").write_text("fresh write")
    assert os.stat(path).st_mtime < time.time() - 24 * HOUR
    proc = fake(f"tail -f {path}/tasks/x.output", age=10 * HOUR)
    assert aj.classify(proc, "scratchpad-ref", time.time()).cls == "unknown"


# --------------------------------------------------------------------------
# live-session registry — the cross-session contract prune imports
# --------------------------------------------------------------------------

def test_registry_shape_and_this_process_is_a_live_descendant():
    registry = aj.build_live_session_registry()
    assert registry, "no claude/codex process found — run this from a Claude session"
    for e in registry:
        assert set(e) >= {"pid", "cwd", "session_id", "scratchpad_root",
                          "session_roots", "descendants", "kind", "incomplete"}
        assert isinstance(e["descendants"], set)
    # cwd is checked only on the entry that owns THIS process, below: any other
    # session on the machine can legitimately read as cwd=None (it exited
    # between `ps` and `lsof`), which the registry reports as `incomplete`.
    assert any(e["kind"] == "session" for e in registry)

    # Whether THIS process sits inside a session's tree depends on how the suite
    # was launched, not on the registry being correct: under `nohup`/launchd the
    # runner is reparented to PID 1 and is legitimately nobody's descendant.
    # So establish the precondition independently — walk our own ancestry — and
    # only then assert. A registry that misses us while an agent process really
    # IS an ancestor is a genuine bug and still fails here.
    procs = aj.ps_snapshot()
    parent_of = {p.pid: p.ppid for p in procs}
    agent_pids = {e["pid"] for e in registry}
    ancestors, cur = set(), parent_of.get(os.getpid())
    while cur and cur > 1 and cur not in ancestors:
        ancestors.add(cur)
        cur = parent_of.get(cur)
    if not (ancestors & agent_pids):
        pytest.skip("detached run: no claude/codex process is an ancestor of this "
                    "test process, so no registry entry can legitimately claim it")
    owning = [e for e in registry if os.getpid() in e["descendants"]]
    assert owning, "this test process should be inside a live session's tree"
    assert all(e["cwd"] for e in owning), owning


def test_unreadable_cwd_marks_only_that_session_incomplete(monkeypatch):
    """Regression: a `make check` gate failed because ANOTHER
    session's cwd came back empty from lsof. That must degrade only that
    entry to incomplete, never drop it or poison its neighbours."""
    ok = fake("claude --resume", pid=990001, age=60)
    gone = fake("claude", pid=990002, age=60)
    monkeypatch.setattr(aj, "_cwds", lambda pids: {990001: "/tmp/x"})
    reg = {e["pid"]: e for e in aj.build_live_session_registry(procs=[ok, gone])}
    assert reg[990001]["cwd"] == "/tmp/x" and not reg[990001]["incomplete"]
    assert reg[990002]["cwd"] is None and reg[990002]["incomplete"]
    assert reg[990002]["kind"] == "session"


def test_session_dir_by_birth_picks_the_dir_born_with_the_process(session_dirs):
    sid, path = session_dirs(age_hours=0)  # born now
    session_dirs(age_hours=26)  # decoy under the same slug, born 26h ago
    assert aj.session_dir_by_birth(TEST_SLUG, time.time()) == (sid, path)
    assert aj.session_dir_by_birth(TEST_SLUG, time.time() - 10 * HOUR) is None


def test_registry_resolves_a_session_that_names_no_scratchpad_path(spawner, tmp_path):
    """Directory birth time, not argv and not freshness: a live session that is
    idle — no bash child, nothing in its subtree argv — must stay identifiable,
    or its own stale scratchpad reads as a dead session and its debris as
    reapable. Measured: argv alone resolved a minority of live sessions."""
    cwd = os.path.realpath(str(tmp_path))
    slug_dir = f"{aj.SCRATCHPAD_BASE}/{aj.cwd_slug(cwd)}"
    sid = str(uuid.uuid4())
    os.makedirs(f"{slug_dir}/{sid}")
    try:
        live = spawner(argv0="claude", cwd=cwd)  # tag is empty: no path in argv
        assert aj.SCRATCHPAD_BASE not in live.command
        entry = next(e for e in aj.build_live_session_registry() if e["pid"] == live.pid)
        assert entry["cwd"] == cwd
        assert entry["session_id"] == sid
        assert entry["scratchpad_root"] == f"{slug_dir}/{sid}"
        assert entry["scratchpad_root"] in entry["session_roots"]
    finally:
        shutil.rmtree(slug_dir, ignore_errors=True)


def test_every_live_session_resolves_a_scratchpad_root_that_exists():
    # `scratchpad_root` is DERIVED from the session, not proof one was created:
    # the harness makes the directory lazily, so a live session that never used
    # its scratchpad legitimately has none (observed: a long-running worktree
    # session, janitor log clean — it was never created, not reaped).
    #
    # Two states are therefore skipped, and only those:
    #   - the session exited mid-scan (the registry is a snapshot: TOCTOU),
    #   - the whole per-cwd slug dir is absent, i.e. nothing ever materialised.
    # A missing session dir INSIDE an existing slug dir still fails — there the
    # siblings materialised and this one vanished, which is what we guard.
    for e in aj.build_live_session_registry():
        root = e["scratchpad_root"]
        if not root or os.path.isdir(root):
            continue
        try:
            os.kill(e["pid"], 0)
        except OSError:
            continue
        if not os.path.isdir(os.path.dirname(root)):
            continue
        raise AssertionError(f"live session lost its scratchpad dir: {e}")


def test_veto_sets_cover_every_descendant():
    registry = aj.build_live_session_registry()
    pids, _ = aj.veto_sets(registry)
    for e in registry:
        assert e["pid"] in pids and e["descendants"] <= pids


def test_a_live_codex_exec_at_ppid_1_is_vetoed_not_reaped():
    """`codex exec` matches the codex-exec pattern AND sits at PPID 1 when the
    plugin detaches it — the registry membership veto is what saves it."""
    live = fake(f"{aj.HOME}/.npm-global/bin/codex exec --model gpt-5.6-sol", pid=999003)
    assert aj.match_pattern(live) == "codex-exec"
    assert not aj._is_excluded(live)

    cands, registry, vetoed = aj.detect(procs=[live])
    assert [e["pid"] for e in registry] == [999003]
    assert cands == [] and vetoed == 1


def test_candidate_referencing_a_live_session_root_is_vetoed(spawner, session_dirs):
    """The veto beats the age rule: same process, same dead-looking directory —
    listed as killable when no live session claims it, vetoed when one does."""
    _, path = session_dirs(age_hours=26)
    orphan = spawner(f"{path}/tasks/x.output")

    procs = aged(aj.ps_snapshot(), {orphan.pid}, 5 * HOUR)
    cands, _, _ = aj.detect(procs=procs)
    assert [c.cls for c in cands if c.proc.pid == orphan.pid] == ["dead-session"]

    live = spawner(path, argv0="claude")
    procs = aged(aj.ps_snapshot(), {orphan.pid}, 5 * HOUR)
    registry = aj.build_live_session_registry(procs)
    assert any(path in e["session_roots"] for e in registry if e["pid"] == live.pid)
    cands, _, vetoed = aj.detect(procs=procs)
    assert [c for c in cands if c.proc.pid == orphan.pid] == []
    assert vetoed >= 1


def test_detached_task_of_a_live_session_is_never_killable(spawner):
    """A `codex exec` or backgrounded build of a LIVE session double-forks to
    PPID 1 and carries no scratchpad path, so it is indistinguishable from
    debris on pattern alone. It is `bare` — report-only — at every age tier."""
    spawner(argv0="claude")
    task = spawner(f"{aj.SHELL_SNAPSHOT_DIR}/snapshot-zsh-9.sh")

    def classes(age):
        procs = aged(aj.ps_snapshot(), {task.pid}, age)
        cands, _, _ = aj.detect(procs=procs)
        return [c.cls for c in cands if c.proc.pid == task.pid]

    assert classes(601) == []  # below the bare tier it is not even reported
    assert classes(4 * HOUR + 1) == ["bare"]
    assert classes(100 * HOUR) == ["bare"]


# --------------------------------------------------------------------------
# kill path
# --------------------------------------------------------------------------

def _alive(pid):
    return subprocess.run(["ps", "-o", "pid=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip() != ""


def test_apply_kills_the_whole_descendant_tree(spawner, session_dirs):
    _, path = session_dirs(age_hours=26)
    root = spawner(f"{path}/tasks/x.output")
    kids = aj.descendants(root.pid, aj.children_map(aj.ps_snapshot()))
    assert kids, "test tree has no child to prove tree-kill against"

    procs = only(aj.ps_snapshot(), {root.pid}, 5 * HOUR)
    rc = aj.cmd_reap(reap_args(apply=True), out=open(os.devnull, "w"), procs=procs)

    assert rc == 0
    assert not _alive(root.pid)
    for kid in kids:
        assert not _alive(kid), f"grandchild {kid} survived"
    assert logged("killed")


def test_apply_skips_when_gearbox_deploy_holds_its_lock(spawner, session_dirs):
    """[HARDENED:codex-verify-r2 HIGH] the blast-radius cap bounds DAMAGE but
    does not stop a launchd tick running a half-deployed module mid-`gearbox
    deploy` -- the shared .gearbox.lock is the thing that actually stops it."""
    _, path = session_dirs(age_hours=26)
    root = spawner(f"{path}/tasks/x.output")
    procs = only(aj.ps_snapshot(), {root.pid}, 5 * HOUR)

    os.mkdir(os.path.join(aj.CLAUDE_ROOT, aj.GEARBOX_LOCK_NAME))
    try:
        rc = aj.cmd_reap(reap_args(apply=True), out=open(os.devnull, "w"), procs=procs)
    finally:
        os.rmdir(os.path.join(aj.CLAUDE_ROOT, aj.GEARBOX_LOCK_NAME))

    assert rc == 0
    assert _alive(root.pid), "must never kill while gearbox deploy holds the lock"
    assert logged("gearbox-deploy-lock-busy")


def test_two_sibling_sessions_a_ends_b_is_neither_listed_nor_killed(spawner, session_dirs):
    """[HARDENED:codex-HIGH] required test: siblings A and B share the same
    scratchpad ROOT. Ending A's session must scope the reap to EXACTLY A's own
    directory -- B's dead-session debris must not even appear in the report,
    let alone be killed."""
    sid_a, path_a = session_dirs(age_hours=26)
    sid_b, path_b = session_dirs(age_hours=26)
    proc_a = spawner(f"{path_a}/tasks/x.output")
    proc_b = spawner(f"{path_b}/tasks/x.output")

    procs = only(aj.ps_snapshot(), {proc_a.pid, proc_b.pid}, 5 * HOUR)
    rc = aj.cmd_reap(reap_args(apply=True, session_id=sid_a),
                     out=open(os.devnull, "w"), procs=procs)

    assert rc == 0
    assert not _alive(proc_a.pid), "A's own debris must be reaped"
    assert _alive(proc_b.pid), "B's debris must survive A's session-scoped reap"


def test_apply_never_kills_bare_or_unknown(spawner, session_dirs):
    _, fresh = session_dirs(age_hours=0)
    bare = spawner(f"{aj.SHELL_SNAPSHOT_DIR}/snapshot-zsh-8.sh")
    unknown = spawner(f"{fresh}/tasks/x.output")

    procs = only(aj.ps_snapshot(), {bare.pid, unknown.pid}, 9 * HOUR)
    seen = {c.proc.pid: c.cls for c in aj.detect(procs=procs)[0]}
    assert seen.get(bare.pid) == "bare" and seen.get(unknown.pid) == "unknown"

    rc = aj.cmd_reap(reap_args(apply=True), out=open(os.devnull, "w"), procs=procs)

    assert rc == 0
    assert _alive(bare.pid) and _alive(unknown.pid)
    assert logged("WARN deferred"), "declining all work is not a clean run"
    assert not logged("INFO killed")


def test_pid_recycled_is_skipped_not_killed(spawner, session_dirs, monkeypatch):
    _, path = session_dirs(age_hours=26)
    root = spawner(f"{path}/tasks/x.output")
    procs = aged(aj.ps_snapshot(), {root.pid}, 5 * HOUR)
    cands, _, _ = aj.detect(procs=procs)
    cand = next(c for c in cands if c.proc.pid == root.pid)

    monkeypatch.setattr(aj, "verify_identity", lambda pid, ident: False)
    killed, _ = aj.kill_tree(cand, "testhash")

    assert killed == set()
    assert _alive(root.pid)
    assert logged("pid-recycled")


def test_refuses_and_exits_nonzero_above_max_kills(spawner, session_dirs, monkeypatch):
    """A refusal that exits 0 is recorded by launchd as success."""
    _, path = session_dirs(age_hours=26)
    root = spawner(f"{path}/tasks/x.output")
    monkeypatch.setattr(aj, "MAX_KILLS", 1)
    monkeypatch.setattr(aj, "notify", lambda msg: notified.append(msg))
    notified = []

    procs = only(aj.ps_snapshot(), {root.pid}, 5 * HOUR)
    rc = aj.cmd_reap(reap_args(apply=True), out=open(os.devnull, "w"), procs=procs)

    assert rc == 3
    assert _alive(root.pid)
    assert notified and "MAX_KILLS" in notified[0]
    assert logged("refused-max-kills")


def test_apply_aborts_when_the_registry_is_incomplete(monkeypatch):
    monkeypatch.setattr(aj, "_cwds", lambda pids: {})
    rc = aj.cmd_reap(reap_args(apply=True), out=open(os.devnull, "w"))
    assert rc == 2
    assert logged("registry-incomplete")


# --------------------------------------------------------------------------
# locking, logging, heartbeat
# --------------------------------------------------------------------------

def test_second_instance_exits_immediately_without_queueing():
    with aj.Lock(aj.REAP_LOCK_NAME) as held:
        assert held is not None
        start = time.time()
        rc = aj.cmd_reap(reap_args(), out=open(os.devnull, "w"))
    assert rc == 0
    assert time.time() - start < 2
    assert logged("lock-busy")


def test_session_end_run_that_loses_the_lock_defers_instead_of_vanishing(session_dirs):
    """[HARDENED:codex-HIGH] the id must resolve to a real, unambiguous
    scratchpad dir before deferral even applies -- a fake id is refused
    non-zero, never deferred, never falls back to machine-wide reaping."""
    sid, _ = session_dirs(age_hours=1)
    with aj.Lock(aj.REAP_LOCK_NAME):
        rc = aj.cmd_reap(reap_args(session_id=sid), out=open(os.devnull, "w"))
    assert rc == 0
    pending = os.listdir(os.path.join(aj.LOG_DIR, aj.PENDING_DIR_NAME))
    assert pending == [sid]

    aj.cmd_reap(reap_args(session_id=sid), out=open(os.devnull, "w"))
    assert os.listdir(os.path.join(aj.LOG_DIR, aj.PENDING_DIR_NAME)) == []
    assert logged("consumed-pending")


def test_session_id_resolving_to_exactly_one_dir_scopes_the_reap(session_dirs):
    """Outcome 2 of 3: one match -> a scoped reap, exit 0, no refusal logged."""
    sid, path = session_dirs(age_hours=1)
    assert aj._resolve_session_dir(sid) == ("one", path)
    rc = aj.cmd_reap(reap_args(session_id=sid), out=open(os.devnull, "w"))
    assert rc == 0
    assert not logged("session-scope-unresolved")
    assert not logged("session-scope-empty")
    assert logged("heartbeat")


def test_session_with_no_scratchpad_dir_is_a_clean_noop_not_an_error():
    """Outcome 1 of 3: a real SessionEnd for a cwd that never created a
    scratchpad dir owns no debris, so it must exit 0 without reaping anything.

    Regression: the deployed hook logged ERROR session-scope-unresolved and
    exited non-zero for exactly this case (in an end-to-end check). Refusing to widen is correct; calling it
    an error is not.
    """
    sid = "abc-123-does-not-exist"
    assert aj._resolve_session_dir(sid) == ("none", None)
    rc = aj.cmd_reap(reap_args(session_id=sid), out=open(os.devnull, "w"))
    assert rc == 0
    assert logged("session-scope-empty")
    assert not logged("session-scope-unresolved")
    assert not logged("killed")
    assert not os.path.isdir(os.path.join(aj.LOG_DIR, aj.PENDING_DIR_NAME))


def test_ambiguous_session_id_is_still_refused_nonzero_never_deferred(session_dirs):
    """Outcome 3 of 3: the SAME id under two different slug dirs is genuinely
    ambiguous -- ownership is unknowable, so it must keep erroring non-zero."""
    sid, _ = session_dirs(age_hours=1)
    twin = os.path.join(aj.SCRATCHPAD_BASE, TEST_SLUG + "-twin", sid)
    os.makedirs(twin, exist_ok=True)
    try:
        assert aj._resolve_session_dir(sid) == ("unresolved", None)
        rc = aj.cmd_reap(reap_args(session_id=sid), out=open(os.devnull, "w"))
        assert rc == 4
        assert logged("session-scope-unresolved")
        assert not logged("session-scope-empty")
        assert not os.path.isdir(os.path.join(aj.LOG_DIR, aj.PENDING_DIR_NAME))
    finally:
        shutil.rmtree(os.path.join(aj.SCRATCHPAD_BASE, TEST_SLUG + "-twin"),
                      ignore_errors=True)


def test_malformed_session_id_is_refused_nonzero_not_treated_as_empty():
    """A malformed id is bad input, never 'this session has no scratchpad'."""
    rc = aj.cmd_reap(reap_args(session_id="../../etc"), out=open(os.devnull, "w"))
    assert rc == 4
    assert logged("session-scope-unresolved")


def test_every_run_writes_one_heartbeat_with_per_class_counts():
    """An idle log and a frozen janitor must never look the same."""
    aj.cmd_reap(reap_args(), out=open(os.devnull, "w"), procs=[])
    beats = logged("heartbeat")
    assert len(beats) == 1
    for field in ("dead_session=0", "bare=0", "unknown=0", "killed=0", "vetoed=0", "hash="):
        assert field in beats[0]


def test_declining_all_work_is_not_a_clean_run():
    cmd = f"/bin/zsh -c source {aj.SHELL_SNAPSHOT_DIR}/snapshot-zsh-7.sh"
    aj.cmd_reap(reap_args(), out=open(os.devnull, "w"), procs=[fake(cmd, age=9 * HOUR)])
    assert logged("WARN deferred")
    assert "bare=1" in logged("WARN deferred")[0]


def test_empty_machine_stays_a_clean_success():
    aj.cmd_reap(reap_args(), out=open(os.devnull, "w"), procs=[])
    assert logged("heartbeat") and not logged("WARN deferred")


def test_log_rotates_at_one_megabyte_keeping_one_old():
    os.makedirs(aj.LOG_DIR, exist_ok=True)
    path = os.path.join(aj.LOG_DIR, aj.LOG_NAME)
    Path(path).write_text("x" * (aj.LOG_MAX_BYTES - 10))
    aj.log("INFO", "trigger-rotation", pad="y" * 100)
    assert os.path.exists(path + ".old")
    assert os.path.getsize(path) < 1000
    assert "trigger-rotation" in Path(path).read_text()


def test_status_reports_deferred_when_work_is_declined(capsys):
    rc = aj.cmd_status(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "state:" in out and ("OK" in out or "DEFERRED" in out)


# --------------------------------------------------------------------------
# silence alarm -- "a wedged janitor and a spotless
# machine must not print the same thing"
# --------------------------------------------------------------------------

def _stamp_log(ts_epoch, action="heartbeat"):
    """Write one real-shaped log line stamped at `ts_epoch` (UTC)."""
    os.makedirs(aj.LOG_DIR, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_epoch))
    Path(os.path.join(aj.LOG_DIR, aj.LOG_NAME)).write_text(
        f"{ts} INFO {action} mode=dry-run dead_session=0 killed=0 hash=abc123def456\n")


def test_log_freshness_says_never_run_when_there_is_no_log():
    verdict, detail = aj.log_freshness()
    assert verdict == "NEVER RUN"
    assert "nothing has run" in detail


def test_log_freshness_is_fresh_inside_the_window():
    now = time.time()
    _stamp_log(now - 60)
    verdict, _ = aj.log_freshness(now=now)
    assert verdict == "FRESH"


def test_log_freshness_goes_stale_past_twice_the_reap_interval():
    """THE POINT OF THE ALARM: this machine looks identical to a clean one --
    zero candidates, zero prunable bytes -- so the ONLY signal that the janitor
    stopped running is the age of its newest log line."""
    now = time.time()
    _stamp_log(now - (aj.REAP_INTERVAL_S * aj.STALE_LOG_FACTOR + 60))
    verdict, detail = aj.log_freshness(now=now)
    assert verdict == "STALE"
    assert "may have stopped running" in detail


def test_log_freshness_reads_the_newest_LINE_not_the_file_mtime():
    """Rotation and an interrupted write both bump mtime without a run."""
    now = time.time()
    _stamp_log(now - 10 * aj.REAP_INTERVAL_S)
    os.utime(os.path.join(aj.LOG_DIR, aj.LOG_NAME), (now, now))  # fresh mtime
    assert aj.log_freshness(now=now)[0] == "STALE"


def test_status_surfaces_the_stale_heartbeat(capsys):
    now = time.time()
    _stamp_log(now - (aj.REAP_INTERVAL_S * aj.STALE_LOG_FACTOR + 60))
    rc = aj.cmd_status(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "heartbeat: STALE" in out


def test_module_hash_is_stable_and_short():
    assert aj.module_hash() == aj.module_hash()
    assert len(aj.module_hash()) == 12
