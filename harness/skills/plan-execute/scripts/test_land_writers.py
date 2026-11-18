"""§18.5 — the pre-push hook waits, bounded and fail-open, while a land pushes main.

Every push here is a REAL ``git push`` through the shipped ``githooks/pre-push``
to a real bare origin, with HOME pointed at a temp dir so the marker and the
writers log are the test's own.

GIT_* variables are cleared before any git call: a git hook exports GIT_DIR and
friends, and a test run under them would rewrite the PUSHING repository (memory
note in _land_fixture._arm_prepush). ``test_git_env_cleared_decoy_untouched``
proves it with a decoy repository named in those variables.
"""

import io
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import land_writers as lw

HOOKS = Path(__file__).resolve().parents[3] / "githooks"


def _git(args, cwd, env):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=60, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    return _make_repo(tmp_path, monkeypatch)


def _make_repo(tmp_path, monkeypatch):
    """A bare origin and two clones, A and B, whose pushes run the shipped hook."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(lw.TOKEN_ENV, raising=False)
    env = {**os.environ}
    origin = tmp_path / "origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(origin)], tmp_path, env)
    clones = {}
    for name in ("a", "b"):
        c = tmp_path / name
        _git(["clone", "-q", str(origin), str(c)], tmp_path, env)
        _git(["config", "user.email", "t@example.com"], c, env)
        _git(["config", "user.name", "T"], c, env)
        clones[name] = c
    # Fixture commits skip the repo's pre-commit (gitleaks, ratchets): not under test.
    _git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "base"],
         clones["a"], env)
    _git(["push", "-q", "origin", "HEAD:main"], clones["a"], env)
    _git(["pull", "-q", "origin", "main"], clones["b"], env)
    _git(["remote", "set-head", "origin", "main"], clones["b"], env)
    for c in clones.values():
        _git(["config", "core.hooksPath", str(HOOKS)], c, env)
    return {"origin": origin, "home": home, "env": env, **clones}


EXPIRY_FORMATS = {
    "isoformat": lambda e: e.isoformat(),                 # the writer's format (§18.6)
    # whole-second forms add 1 s so dropping the fraction never ends the hold early
    "z-suffix": lambda e: (e + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "plus-one-hour": lambda e: (e + timedelta(seconds=1)).astimezone(
        timezone(timedelta(hours=1))).replace(microsecond=0).isoformat(),
    "fraction-z": lambda e: e.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
}


def _write_marker(url, *, token="tok-A", expires_in=60.0, fmt="isoformat", **extra):
    lw.marker_dir().mkdir(parents=True, exist_ok=True)
    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    m = {"marker_version": 1, "remote_hash": lw.remote_hash(url), "plan_slug": "p",
         "token": token, "pid": 1, "host": "h", "started_at": "x",
         "expires_at": EXPIRY_FORMATS[fmt](exp), **extra}
    p = lw.marker_path(url)
    p.write_text(json.dumps(m))
    return p


def _push(fx, clone="b", ref="main", env_extra=None):
    """Commit in ``clone`` and push it to ``ref``; returns the wall seconds."""
    c = fx[clone]
    env = {**fx["env"], **(env_extra or {})}
    _git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "x"],
         c, env)
    t = time.monotonic()
    _git(["push", "-q", "origin", f"HEAD:{ref}"], c, env)
    return time.monotonic() - t


def _log():
    lines = (lw.marker_dir() / lw.LOG_NAME).read_text().splitlines()
    return [json.loads(ln) for ln in lines]


def _url(fx, clone):
    return _git(["remote", "get-url", "origin"], fx[clone], fx["env"])


def test_no_marker_no_wait(repo):
    wall = _push(repo)
    assert _log()[-1]["outcome"] == "no-marker"
    assert wall < 5


def test_marker_from_clone_a_holds_clone_b_until_it_ends(repo):
    """Two clones, one remote: A's marker is found by B's hook; B's push waits
    for it and goes on the moment it is removed."""
    marker = _write_marker(_url(repo, "a"))
    threading.Timer(2.0, marker.unlink).start()
    wall = _push(repo, "b")
    rec = _log()[-1]
    assert rec["outcome"] == "clear" and rec["marker_token"] == "tok-A"
    assert rec["remote_hash"] == lw.remote_hash(_url(repo, "b"))
    assert 1.8 <= wall < 30 and rec["waited_s"] >= 1.0


@pytest.mark.parametrize("fmt", sorted(EXPIRY_FORMATS))
def test_marker_expiry_ends_the_wait(repo, tmp_path, fmt):
    """Each expiry spelling holds the push until it passes. The hook runs on the
    oldest python3 this Mac has (/usr/bin/python3 is 3.9, whose fromisoformat
    refuses a trailing Z), so a 3.9-only parse failure shows up here."""
    env_extra = None
    if Path("/usr/bin/python3").exists():
        bin_dir = tmp_path / "py39bin"
        bin_dir.mkdir()
        (bin_dir / "python3").symlink_to("/usr/bin/python3")
        env_extra = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    _write_marker(_url(repo, "a"), expires_in=1.5, fmt=fmt)
    wall = _push(repo, env_extra=env_extra)
    assert _log()[-1]["outcome"] == "expired"
    assert 1.0 <= wall < 30


@pytest.mark.parametrize("blocked", [False, True], ids=["file", "named-pipe"])
def test_cap_lets_the_push_go(repo, monkeypatch, blocked):
    p = _write_marker(_url(repo, "a"))
    if blocked:  # a read that never returns: only the hook's own timer can end it
        p.unlink()
        os.mkfifo(p)
        monkeypatch.setattr(lw, "WAIT_CAP_S", 0.3)
        monkeypatch.chdir(repo["b"])
        t = time.monotonic()
        assert lw.hook(["origin", _url(repo, "b")], io.StringIO("l 1 refs/heads/main 2\n")) == 0
        assert time.monotonic() - t < 0.3 + lw.DEADLINE_GRACE_S + 1.5
        assert _log()[-1]["outcome"] in ("error", "cap")
        return
    t = time.monotonic()
    assert lw.wait_for_land(_url(repo, "b"), cap_s=1.0) == "cap"
    assert 1.0 <= time.monotonic() - t < 3
    assert _log()[-1]["outcome"] == "cap"


@pytest.mark.parametrize("kind", ["garbage", "unreadable", "naive-expiry", "no-expiry"])
def test_unreadable_marker_lets_the_push_go(repo, kind):
    p = _write_marker(_url(repo, "a"))
    if kind == "garbage":
        p.write_text("{not json")
    elif kind == "unreadable":
        p.chmod(0)
    elif kind == "naive-expiry":
        p.write_text(json.dumps({"token": "t", "expires_at": "2999-01-01T00:00:00"}))
    else:
        p.write_text(json.dumps({"token": "t"}))
    try:
        wall = _push(repo)
    finally:
        p.chmod(0o644)
    rec = _log()[-1]
    assert rec["outcome"] == "error" and rec["error"]
    assert wall < 5


def test_push_to_another_branch_is_never_held(repo):
    _write_marker(_url(repo, "a"))
    wall = _push(repo, ref="feature")
    assert _log()[-1]["outcome"] == "other-branch"
    assert wall < 5


def test_the_lands_own_push_is_never_held(repo):
    _write_marker(_url(repo, "a"), token="tok-L")
    wall = _push(repo, env_extra={lw.TOKEN_ENV: "tok-L"})
    assert _log()[-1]["outcome"] == "own-token"
    assert wall < 5


def test_phase1_marker_never_holds(repo):
    """Only a marker in phase "push" holds; a land still in phase 1 never does."""
    _write_marker(_url(repo, "a"), phase="gates")
    wall = _push(repo)
    assert _log()[-1]["outcome"] == "expired"
    assert wall < 5


@pytest.mark.parametrize("url", ["https://h/o/r", "https://h/o/r/", "https://h/o/r.git",
                                 "https://h/o/r.git/"])
def test_remote_hash_ignores_trailing_slash_and_dot_git(url):
    assert lw.remote_hash(url) == lw.remote_hash("https://h/o/r")


def test_git_env_cleared_decoy_untouched(tmp_path, monkeypatch):
    """A decoy repository named in GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE must come
    out byte-identical after the fixture and a held push ran."""
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    before = {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()}
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    fx = _make_repo(tmp_path / "fx", monkeypatch)
    _write_marker(_url(fx, "a"), expires_in=0.5)
    _push(fx)
    after = {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()}
    assert after == before
    assert _log()[-1]["outcome"] == "expired"
