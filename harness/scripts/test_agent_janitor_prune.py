"""agent_janitor prune — the disk pruner and its protect-list.

Split out of test_agent_janitor.py, which stood 638 lines past the 800-line
house limit for a test file. Same discipline as its sibling: nothing about ps
parsing is mocked, and the protect-list matcher is checked directly because it
is pure and root-relative.

Fixtures live in conftest.py; row builders in janitor_helpers.py.

Run: pytest scripts/test_agent_janitor_prune.py -q
"""
import argparse
import os
import subprocess
import time
import uuid
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_janitor as aj  # noqa: E402
from janitor_helpers import HOUR, logged  # noqa: E402


# ==========================================================================
# prune -- disk pruner + protect-list, PRUNE-01..04
#
# Every root here is a throwaway tmp_path tree (`prune_roots`), never the
# real ~/.claude / ~/.codex / /private/tmp -- a --root override exists
# precisely so an --apply test can genuinely delete without touching this
# machine. `registry=[]` is passed explicitly wherever liveness is not the
# thing under test, so no test spawns a real ps_snapshot() by accident.
# ==========================================================================

from types import SimpleNamespace  # noqa: E402


def age_tree(path, age_days):
    """Backdate every file/dir under `path` (and `path` itself), deepest
    first -- restamping a child after its parent would bump the parent back
    to now. Mirrors the `session_dirs` fixture's own re-stamp order above.

    `follow_symlinks=False` is load-bearing: a bare os.utime() on a symlink
    restamps its TARGET, not the link -- which would both leave the link's
    own (fresh) mtime out of newest_mtime()'s reach and corrupt whatever the
    link points at (a protected file, in the nested-veto tests).
    """
    stamp = time.time() - age_days * HOUR * 24
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in dirs + files:
            os.utime(os.path.join(root, name), (stamp, stamp), follow_symlinks=False)
    os.utime(path, (stamp, stamp), follow_symlinks=False)


@pytest.fixture
def prune_roots(tmp_path):
    """Sandboxed claude/codex/scratchpad/tmp-cwd roots -- pytest tears down
    tmp_path itself, no manual cleanup needed."""
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    scratch = tmp_path / "scratchpad"
    tmpcwd = tmp_path / "tmpcwd"
    for d in (claude, codex, scratch, tmpcwd):
        d.mkdir()
    return SimpleNamespace(claude=str(claude), codex=str(codex),
                           scratch=str(scratch), tmpcwd=str(tmpcwd))


def run_prune_sandboxed(roots, apply, now=None, registry=None, **overrides):
    """run_prune with every root pinned to the sandbox, one line per call site."""
    kwargs = dict(claude_root=roots.claude, codex_root=roots.codex,
                  scratchpad_base=roots.scratch, tmp_cwd_dir=roots.tmpcwd,
                  registry=[] if registry is None else registry, now=now)
    kwargs.update(overrides)
    return aj.run_prune(apply, **kwargs)


# --------------------------------------------------------------------------
# protect-list matcher -- pure, root-relative, checked directly
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel,rule", [
    ("projects/some-proj/transcript.jsonl", "protect-projects"),
    ("projects/some-proj/memory/notes.md", "protect-projects"),
    ("_plans/example-plan/PLAN.html", "protect-plans"),
    ("settings.json", "protect-settings"),
    ("settings.local.json", "protect-settings"),
    (".credentials-backup", "protect-credentials"),
    ("skills/plan-execute/SKILL.md", "protect-skills"),
    ("commands/foo.md", "protect-commands"),
    ("agents/foo.md", "protect-agents"),
    ("hooks/foo.py", "protect-hooks"),
    ("scripts/agent_janitor.py", "protect-scripts"),
    ("rules/git-safety.md", "protect-rules"),
    ("CLAUDE.md", "protect-claude-md"),
    ("history.jsonl", "protect-history"),
    ("plugins/marketplace.json", "protect-plugins"),
    ("shell-snapshots/snapshot-1.sh", "protect-shell-snapshots"),
    ("todos/abc.json", "protect-todos"),
    ("logs/agent-janitor.log", "protect-logs"),
])
def test_is_protected_covers_everything_gearbox_harvests(rel, rule):
    """[HARDENED:claude-HIGH] the corrected, non-inverted claude-root
    protect-list -- 'projects/**' is the whole tree, not just memory/."""
    protected, rule_id = aj.is_protected("claude", rel)
    assert protected and rule_id == rule


def test_is_protected_claude_carveout_excludes_only_plugin_temp_debris():
    for rel in ("plugins/cache/temp_git_abc123/objects/pack/x.pack",
               "plugins/cache/temp_subdir_xyz.clone/README.md"):
        assert aj.is_protected("claude", rel) == (False, None)
    # a sibling, non-debris plugins/ path stays protected
    assert aj.is_protected("claude", "plugins/cache/installed-plugin/x.json") \
        == (True, "protect-plugins")


@pytest.mark.parametrize("rel", [
    "auth.json", "history.jsonl", "state_5.sqlite", "session_index.jsonl",
    "sessions/2020/01/15/auth.json", "AGENTS.md", "skills/foo/SKILL.md",
])
def test_is_protected_codex_default_protects_everything_but_the_carveout(rel):
    """'everything under the codex root stays veto-protected' except the one
    named carve-out -- the boundary is a rule_id in code, per PRUNE-03."""
    protected, rule_id = aj.is_protected("codex", rel)
    assert protected and rule_id == "protect-codex-default"


def test_is_protected_codex_carveout_is_exactly_rollout_files():
    assert aj.is_protected(
        "codex", "sessions/2026/01/15/rollout-2026-01-15T00-00-00-abc.jsonl") == (False, None)
    # a non-rollout file living beside rollouts is NOT carved out
    protected, rule_id = aj.is_protected("codex", "sessions/2026/01/15/notes.txt")
    assert protected and rule_id == "protect-codex-default"


def test_is_protected_rejects_an_unknown_root_kind():
    with pytest.raises(ValueError):
        aj.is_protected("bogus", "whatever")


# --------------------------------------------------------------------------
# PRUNE-01a -- scratchpad session dirs
# --------------------------------------------------------------------------

def test_scan_scratchpad_stale_unprotected_dies_on_apply(prune_roots):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "tasks").mkdir()
    (sess / "tasks" / "x.output").write_text("scratch debris")
    age_tree(sess, 20)  # > 14d
    now = time.time()

    deleted, vetoed, fresh = aj.scan_scratchpad(now, scratchpad_base=prune_roots.scratch,
                                                registry=[])
    assert vetoed == [] and fresh == []
    assert [i.path for i in deleted] == [str(sess)]
    assert sess.exists(), "scan must never mutate"

    result = run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert not sess.exists()
    assert (str(sess), deleted[0].nbytes) in result["deleted"]


def test_scan_scratchpad_refuses_a_base_other_users_can_write(prune_roots):
    # A base another local user can write lets them swap a parent for a
    # symlink between the last check and rmtree: scan nothing under it.
    base = Path(prune_roots.scratch)
    sess = base / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    age_tree(sess, 20)
    base.chmod(0o777)
    try:
        deleted, vetoed, fresh = aj.scan_scratchpad(time.time(), scratchpad_base=str(base),
                                                    registry=[])
    finally:
        base.chmod(0o700)
    assert deleted == []
    assert vetoed == [(str(base), "veto-base-not-owner-only")]


def test_scan_scratchpad_fresh_dir_survives(prune_roots):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "x.txt").write_text("d")
    now = time.time()
    deleted, vetoed, fresh = aj.scan_scratchpad(now, scratchpad_base=prune_roots.scratch,
                                                registry=[])
    assert deleted == [] and vetoed == []
    assert fresh == [str(sess)]


def test_scan_scratchpad_live_session_veto_beats_staleness(prune_roots):
    """Staleness alone is NOT sufficient for a scratchpad session dir -- a
    session paused for days on a human checkpoint is idle, not dead."""
    sid = str(uuid.uuid4())
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / sid
    sess.mkdir(parents=True)
    (sess / "x.txt").write_text("d")
    age_tree(sess, 20)
    now = time.time()
    live_registry = [{
        "pid": 424242, "cwd": "/Users/x/repo", "session_id": sid,
        "scratchpad_root": str(sess), "session_roots": {str(sess)},
        "descendants": set(), "kind": "session", "incomplete": False,
        "command": "claude",
    }]

    deleted, vetoed, fresh = aj.scan_scratchpad(now, scratchpad_base=prune_roots.scratch,
                                                registry=live_registry)
    assert deleted == []
    assert vetoed == [(str(sess), "veto-live-session")]

    result = run_prune_sandboxed(prune_roots, apply=True, now=now, registry=live_registry)
    assert sess.exists()
    assert (str(sess), "veto-live-session") in result["vetoed"]


def test_scan_scratchpad_nested_hardlink_vetoes_the_whole_directory(prune_roots):
    """[HARDENED:codex-HIGH] a whole-directory delete must enumerate every
    descendant first: one unsafe descendant vetoes the ENTIRE directory, and
    the file that caused the veto must still exist afterwards."""
    scratch = Path(prune_roots.scratch)
    outside = scratch.parent / "shared-inode-target"
    outside.write_text("aliased content")
    sess = scratch / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    linked = sess / "aliased.dat"
    os.link(outside, linked)
    age_tree(sess, 20)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_scratchpad(now, scratchpad_base=prune_roots.scratch,
                                                registry=[])
    assert deleted == []
    assert vetoed == [(str(sess), "veto-hardlink")]

    run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert sess.exists() and linked.exists() and outside.exists()


# --------------------------------------------------------------------------
# PRUNE-01b -- /private/tmp/claude-*-cwd marker files
# --------------------------------------------------------------------------

def test_scan_tmp_cwd_stale_marker_dies_fresh_survives(prune_roots):
    base = Path(prune_roots.tmpcwd)
    stale = base / "claude-abcd-cwd"
    stale.write_text("/Users/x/repo\n")
    os.utime(stale, (time.time() - 10 * HOUR * 24,) * 2)
    fresh_marker = base / "claude-efgh-cwd"
    fresh_marker.write_text("/Users/y/repo\n")
    now = time.time()

    deleted, vetoed, fresh = aj.scan_tmp_cwd(now, tmp_dir=str(base))
    assert vetoed == []
    assert [i.path for i in deleted] == [str(stale)]
    assert fresh == [str(fresh_marker)]

    run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert not stale.exists() and fresh_marker.exists()


def test_scan_tmp_cwd_hardlinked_marker_is_refused_unconditionally(prune_roots):
    """ponytail: st_nlink > 1 is refused outright rather than proven unshared
    with a protected path -- see agent_janitor._is_unsafe's docstring."""
    base = Path(prune_roots.tmpcwd)
    other = base / "other-name"
    other.write_text("shared inode")
    marker = base / "claude-hlink-cwd"
    os.link(other, marker)
    os.utime(marker, (time.time() - 10 * HOUR * 24,) * 2)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_tmp_cwd(now, tmp_dir=str(base))
    assert deleted == []
    assert vetoed == [(str(marker), "veto-hardlink")]


# --------------------------------------------------------------------------
# PRUNE-02 -- plugin-cache temp clones
# --------------------------------------------------------------------------

def test_scan_plugin_cache_stale_temp_clone_dies_on_apply(prune_roots):
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    entry = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry.mkdir(parents=True)
    (entry / ".git").mkdir()
    (entry / "README.md").write_text("clone debris")
    age_tree(entry, 10)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_plugin_cache(now, claude_root=prune_roots.claude)
    assert vetoed == [] and fresh == []
    assert [i.path for i in deleted] == [str(entry)]
    expected_bytes = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
    assert deleted[0].nbytes == expected_bytes

    result = run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert not entry.exists()
    assert result["bytes_reclaimed"] >= expected_bytes


def test_scan_plugin_cache_fresh_clone_survives(prune_roots):
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    entry = cache / f"temp_subdir_{uuid.uuid4().hex[:8]}.clone"
    entry.mkdir(parents=True)
    (entry / "x").write_text("d")
    now = time.time()
    deleted, vetoed, fresh = aj.scan_plugin_cache(now, claude_root=prune_roots.claude)
    assert deleted == [] and vetoed == []
    assert fresh == [str(entry)]


def test_scan_plugin_cache_ignores_installed_plugins_even_if_stale(prune_roots):
    """Only temp_git_*/temp_subdir_* are debris -- the walk never even lists
    an installed plugin cache as a candidate, so it can never appear in
    `deleted` OR `vetoed`: it was never a candidate at all."""
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    installed = cache / "some-installed-plugin"
    installed.mkdir(parents=True)
    (installed / "plugin.json").write_text("{}")
    age_tree(installed, 30)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_plugin_cache(now, claude_root=prune_roots.claude)
    assert deleted == [] and vetoed == [] and fresh == []
    assert installed.exists()


def test_nested_symlink_inside_stale_temp_clone_vetoes_whole_directory(prune_roots):
    """[HARDENED:codex-HIGH] NESTED case: a protected/unsafe file inside a
    stale directory candidate vetoes the whole directory; the file (and its
    target) still exist afterwards. Mirrors a real clone
    (temp_git_example/AGENTS.md -> CLAUDE.md) that this exact
    code path correctly refused to touch during manual verification."""
    claude_root = Path(prune_roots.claude)
    protected_target = claude_root / "CLAUDE.md"
    protected_target.write_text("do not touch")
    cache = claude_root / "plugins" / "cache"
    entry = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry.mkdir(parents=True)
    (entry / "README.md").write_text("clone debris")
    (entry / "AGENTS.md").symlink_to(protected_target)
    age_tree(entry, 10)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_plugin_cache(now, claude_root=str(claude_root))
    assert deleted == []
    assert vetoed == [(str(entry), "veto-symlink")]

    result = run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert entry.exists() and protected_target.exists()
    assert (str(entry), "veto-symlink") in result["vetoed"]


# --------------------------------------------------------------------------
# PRUNE-04 -- codex session rollouts
# --------------------------------------------------------------------------

def _codex_day(root, y="2020", m="01", d="15"):
    day = Path(root) / "sessions" / y / m / d
    day.mkdir(parents=True)
    return day


def test_scan_codex_stale_rollout_dies_fresh_rollout_survives(prune_roots):
    day = _codex_day(prune_roots.codex)
    stale = day / "rollout-2020-01-15T00-00-00-019a2701-47d6-7132-9a43-72e30d9609a8.jsonl"
    stale.write_text('{"x":1}\n')
    os.utime(stale, (time.time() - 90 * HOUR * 24,) * 2)
    fresh_file = day / "rollout-2020-01-15T01-00-00-019a2701-47d6-7132-9a43-72e30d9609a9.jsonl"
    fresh_file.write_text('{"x":2}\n')
    now = time.time()

    deleted, vetoed, fresh = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert [i.path for i in deleted] == [str(stale)]
    assert fresh == [str(fresh_file)]
    assert vetoed == []

    run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert not stale.exists() and fresh_file.exists()


def test_scan_codex_stale_auth_json_survives_beside_stale_rollout(prune_roots):
    """(b)/(d): a stale, non-rollout file inside the debris root is planted
    ON the deletion path and must appear in `vetoed` with its rule_id -- it
    is genuinely spared by the veto, not merely never considered."""
    day = _codex_day(prune_roots.codex)
    rollout = day / "rollout-2020-01-15T00-00-00-abc.jsonl"
    rollout.write_text("x")
    auth = day / "auth.json"
    auth.write_text("secret")
    stamp = time.time() - 90 * HOUR * 24
    os.utime(rollout, (stamp, stamp))
    os.utime(auth, (stamp, stamp))
    now = time.time()

    deleted, vetoed, fresh = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert [i.path for i in deleted] == [str(rollout)]
    assert vetoed == [(str(auth), "protect-codex-default")]

    result = run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert not rollout.exists()
    assert auth.exists()
    assert (str(auth), "protect-codex-default") in result["vetoed"]


def test_mutation_check_emptying_the_protect_list_lets_auth_json_die(prune_roots, monkeypatch):
    """MUTATION CHECK: with the protect-list monkeypatched empty, the exact
    same seeded file that survived above IS deleted -- proving the veto, not
    the allowlist, is what saved it."""
    day = _codex_day(prune_roots.codex)
    auth = day / "auth.json"
    auth.write_text("secret")
    os.utime(auth, (time.time() - 90 * HOUR * 24,) * 2)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert deleted == []
    assert vetoed == [(str(auth), "protect-codex-default")]
    assert auth.exists()

    monkeypatch.setattr(aj, "is_protected", lambda root_kind, rel: (False, None))
    deleted2, vetoed2, fresh2 = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert vetoed2 == []
    assert [i.path for i in deleted2] == [str(auth)]
    assert aj._apply_delete(deleted2[0], "testhash")
    assert not auth.exists()


def test_symlinked_date_dir_under_codex_sessions_is_refused_not_followed(prune_roots):
    outside_day = Path(prune_roots.codex).parent / "outside-target" / "evil"
    outside_day.mkdir(parents=True)
    planted = outside_day / "rollout-2020-01-01T00-00-00-evil.jsonl"
    planted.write_text("should never be touched")
    os.utime(planted, (time.time() - 90 * HOUR * 24,) * 2)

    year_dir = Path(prune_roots.codex) / "sessions" / "2020"
    year_dir.mkdir(parents=True)
    (year_dir / "01").symlink_to(outside_day, target_is_directory=True)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert deleted == []
    assert planted.exists()
    assert (str(year_dir / "01"), "veto-symlink") in vetoed

    run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert planted.exists(), "a symlinked date dir must never be followed, let alone deleted"


def test_symlink_named_rollout_pointing_at_protected_file_is_refused(prune_roots):
    day = _codex_day(prune_roots.codex)
    target = Path(prune_roots.claude) / "CLAUDE.md"
    target.write_text("protected content")
    fake_rollout = day / "rollout-2020-01-15T00-00-00-fake.jsonl"
    fake_rollout.symlink_to(target)
    now = time.time()

    deleted, vetoed, fresh = aj.scan_codex(now, codex_root=prune_roots.codex)
    assert deleted == []
    assert (str(fake_rollout), "veto-symlink") in vetoed

    run_prune_sandboxed(prune_roots, apply=True, now=now)
    assert target.exists() and target.read_text() == "protected content"


def test_apply_removes_now_empty_date_dirs_after_rollout_deletion(prune_roots):
    day = _codex_day(prune_roots.codex)
    stale = day / "rollout-2020-01-15T00-00-00-abc.jsonl"
    stale.write_text("x")
    os.utime(stale, (time.time() - 90 * HOUR * 24,) * 2)
    now = time.time()

    run_prune_sandboxed(prune_roots, apply=True, now=now)

    codex_root = Path(prune_roots.codex)
    assert not day.exists()
    assert not (codex_root / "sessions" / "2020" / "01").exists()
    assert not (codex_root / "sessions" / "2020").exists()
    assert (codex_root / "sessions").exists(), "the sessions/ root itself is never rmdir'd"


# --------------------------------------------------------------------------
# whole-run wiring -- byte counts, dry-run safety, locking, CLI
# --------------------------------------------------------------------------

def test_run_prune_apply_deletes_correct_paths_and_bytes_across_all_four_roots(prune_roots):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "out.txt").write_text("scratch debris" * 100)
    age_tree(sess, 20)

    marker = Path(prune_roots.tmpcwd) / "claude-zzzz-cwd"
    marker.write_text("/Users/x")
    os.utime(marker, (time.time() - 10 * HOUR * 24,) * 2)

    clone = Path(prune_roots.claude) / "plugins" / "cache" / f"temp_git_{uuid.uuid4().hex[:8]}"
    clone.mkdir(parents=True)
    (clone / "f").write_text("clone" * 50)
    age_tree(clone, 10)

    day = _codex_day(prune_roots.codex)
    rollout = day / "rollout-2020-01-15T00-00-00-abc.jsonl"
    rollout.write_text("rollout" * 30)
    os.utime(rollout, (time.time() - 90 * HOUR * 24,) * 2)

    expected_paths = {str(sess), str(marker), str(clone), str(rollout)}
    expected_bytes = (
        sum(f.stat().st_size for f in sess.rglob("*") if f.is_file())
        + marker.stat().st_size
        + sum(f.stat().st_size for f in clone.rglob("*") if f.is_file())
        + rollout.stat().st_size
    )

    now = time.time()
    result = run_prune_sandboxed(prune_roots, apply=True, now=now)

    assert {p for p, _ in result["deleted"]} == expected_paths
    assert result["bytes_reclaimed"] == expected_bytes
    assert not sess.exists() and not marker.exists()
    assert not clone.exists() and not rollout.exists()


def test_prune_refuses_above_max_delete_paths(prune_roots, monkeypatch):
    """A refusal that exits 0 is recorded by launchd as success -- exercised
    at the cmd_prune CLI layer, same as reap's MAX_KILLS test."""
    monkeypatch.setattr(aj, "SCRATCHPAD_BASE", prune_roots.scratch)
    monkeypatch.setattr(aj, "TMP_CWD_DIR", prune_roots.tmpcwd)
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "out.txt").write_text("x")
    age_tree(sess, 20)
    monkeypatch.setattr(aj, "MAX_DELETE_PATHS", 0)
    notified = []
    monkeypatch.setattr(aj, "notify", lambda msg: notified.append(msg))

    args = argparse.Namespace(apply=True, dry_run=False, force=True,
                              claude_root=prune_roots.claude, codex_root=prune_roots.codex)
    rc = aj.cmd_prune(args, out=open(os.devnull, "w"))

    assert rc == 3
    assert sess.exists(), "must never delete above the blast-radius cap"
    assert notified and "exceed" in notified[0]
    assert logged("refused-max-delete")


def test_prune_refuses_above_max_delete_bytes(prune_roots, monkeypatch):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "out.txt").write_text("x" * 1000)
    age_tree(sess, 20)
    monkeypatch.setattr(aj, "MAX_DELETE_BYTES", 10)

    result = run_prune_sandboxed(prune_roots, apply=True)

    assert result["refused"] is True
    assert sess.exists()
    assert logged("refused-max-delete")


def test_prune_apply_skips_when_gearbox_deploy_holds_its_lock(prune_roots):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "out.txt").write_text("x")
    age_tree(sess, 20)

    lockdir = os.path.join(prune_roots.claude, aj.GEARBOX_LOCK_NAME)
    os.mkdir(lockdir)
    try:
        result = run_prune_sandboxed(prune_roots, apply=True)
    finally:
        os.rmdir(lockdir)

    assert result["lock_busy"] is True
    assert sess.exists(), "must never delete while gearbox deploy holds the lock"
    assert logged("prune-gearbox-deploy-lock-busy")


def test_dry_run_never_deletes_anything(prune_roots):
    sess = Path(prune_roots.scratch) / "-Users-x-repo" / str(uuid.uuid4())
    sess.mkdir(parents=True)
    (sess / "f").write_text("d")
    age_tree(sess, 20)
    now = time.time()

    result = run_prune_sandboxed(prune_roots, apply=False, now=now)
    assert sess.exists()
    assert any(p == str(sess) for p, _ in result["deleted"])


def test_second_prune_instance_exits_immediately_without_queueing(prune_roots):
    with aj.Lock(aj.PRUNE_LOCK_NAME) as held:
        assert held is not None
        start = time.time()
        result = run_prune_sandboxed(prune_roots, apply=False)
    assert result["lock_busy"] is True
    assert time.time() - start < 2
    assert result["deleted"] == [] and result["vetoed"] == []


def test_cmd_prune_dry_run_reports_and_touches_nothing(prune_roots, monkeypatch):
    monkeypatch.setattr(aj, "SCRATCHPAD_BASE", prune_roots.scratch)
    monkeypatch.setattr(aj, "TMP_CWD_DIR", prune_roots.tmpcwd)
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    entry = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry.mkdir(parents=True)
    (entry / "f").write_text("d")
    age_tree(entry, 10)

    args = argparse.Namespace(apply=False, dry_run=True,
                              claude_root=prune_roots.claude, codex_root=prune_roots.codex)
    rc = aj.cmd_prune(args, out=open(os.devnull, "w"))
    assert rc == 0
    assert entry.exists()


def test_cmd_prune_apply_actually_deletes(prune_roots, monkeypatch):
    """Abort condition: a pruner whose delete test never deletes is
    unshippable. This is the CLI entry point, not just the library call."""
    monkeypatch.setattr(aj, "SCRATCHPAD_BASE", prune_roots.scratch)
    monkeypatch.setattr(aj, "TMP_CWD_DIR", prune_roots.tmpcwd)
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    entry = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry.mkdir(parents=True)
    (entry / "f").write_text("d")
    age_tree(entry, 10)

    args = argparse.Namespace(apply=True, dry_run=False,
                              claude_root=prune_roots.claude, codex_root=prune_roots.codex)
    rc = aj.cmd_prune(args, out=open(os.devnull, "w"))
    assert rc == 0
    assert not entry.exists()


def test_cmd_prune_apply_stamp_gate_skips_within_24h_force_bypasses(prune_roots):
    """The prune plist wakes every 6h (sleep-safe catch-up) but real
    deletion work happens at most once per real day."""
    cache = Path(prune_roots.claude) / "plugins" / "cache"
    entry1 = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry1.mkdir(parents=True)
    (entry1 / "f").write_text("d")
    age_tree(entry1, 10)

    args = argparse.Namespace(apply=True, dry_run=False, force=False,
                              claude_root=prune_roots.claude, codex_root=prune_roots.codex)
    rc = aj.cmd_prune(args, out=open(os.devnull, "w"))
    assert rc == 0
    assert not entry1.exists()

    entry2 = cache / f"temp_git_{uuid.uuid4().hex[:8]}"
    entry2.mkdir(parents=True)
    (entry2 / "f").write_text("d")
    age_tree(entry2, 10)

    rc = aj.cmd_prune(args, out=open(os.devnull, "w"))
    assert rc == 0
    assert entry2.exists(), "a second tick within 24h must not delete again"
    assert logged("prune-stamp-not-due")

    args_forced = argparse.Namespace(apply=True, dry_run=False, force=True,
                                     claude_root=prune_roots.claude, codex_root=prune_roots.codex)
    rc = aj.cmd_prune(args_forced, out=open(os.devnull, "w"))
    assert rc == 0
    assert not entry2.exists(), "--force must bypass the stamp gate"


def test_prune_heartbeat_is_logged():
    aj.run_prune(apply=False, claude_root="/nonexistent-x", codex_root="/nonexistent-y",
                scratchpad_base="/nonexistent-z", tmp_cwd_dir="/nonexistent-w", registry=[])
    assert logged("prune-heartbeat")


# --------------------------------------------------------------------------
# launchd install/uninstall -- plist rendering and the
# bootout/bootstrap PAIR, exercised against a THROWAWAY label only. `install`
# itself is never invoked in a plan session (see agent_janitor.py docstring).
# --------------------------------------------------------------------------

def test_launchd_plists_are_valid_and_declare_the_documented_intervals():
    for name, interval in (("reap", 1800), ("prune", 21600)):
        path = os.path.join(aj.LAUNCHD_SRC_DIR, f"{aj.LAUNCHD_LABELS[name]}.plist")
        r = subprocess.run(["plutil", "-lint", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        text = Path(path).read_text()
        assert f"<integer>{interval}</integer>" in text
        assert "<key>StartCalendarInterval</key>" not in text, (
            "a calendar-scheduled job is missed with no catch-up during sleep"
        )
        assert "<key>StartInterval</key>" in text
        assert "--apply" in text
        assert "__HOME__/.claude/scripts/agent_janitor.py" in text


def test_render_plist_substitutes_home():
    rendered = aj._render_plist("reap")
    assert "__HOME__" not in rendered
    assert f"{aj.HOME}/.claude/scripts/agent_janitor.py" in rendered


def test_bootout_then_bootstrap_pair_against_a_throwaway_label(tmp_path):
    """[HARDENED:codex] install/uninstall are printed/exercised as a PAIR --
    this proves the underlying launchctl sequence agent_janitor.py runs
    (bootout swallowing first-run 'no such service', then bootstrap, then a
    parseable `launchctl print`) actually works on this macOS version, without
    ever touching the real com.gearbox.agent-janitor.* labels."""
    label = f"com.gearbox.janitor-test-{os.getpid()}"
    plist = tmp_path / f"{label}.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>/bin/echo</string><string>hi</string></array>
<key>StartInterval</key><integer>86400</integer>
<key>RunAtLoad</key><false/>
</dict></plist>""")
    target = f"gui/{aj.UID}/{label}"
    try:
        r1 = subprocess.run(["launchctl", "bootout", target], capture_output=True)
        assert r1.returncode != 0, "expected 'no such service' on a never-installed label"

        r2 = subprocess.run(["launchctl", "bootstrap", f"gui/{aj.UID}", str(plist)],
                            capture_output=True, text=True)
        assert r2.returncode == 0, r2.stderr

        r3 = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
        assert r3.returncode == 0
        assert f"{target} = {{" in r3.stdout, "launchctl print output must parse"
        assert "state = " in r3.stdout
    finally:
        subprocess.run(["launchctl", "bootout", target], capture_output=True)
