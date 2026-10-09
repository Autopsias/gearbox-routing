"""Shared fixtures for the scripts/ suite.

`isolated_logs` is autouse and must reach every agent-janitor test, so it lives
here rather than in a helper module: pytest resolves a conftest fixture without
anyone importing it, and an IMPORTED fixture is shadowed by the test's own
parameter of the same name.

The govrun section below (harness for test_govrun.py and
test_govrun_pytest_budget.py, split out of test_govrun.py to stay under the
file-size ratchet) is unrelated to agent-janitor and shares this file only
because pytest auto-discovers one conftest.py per directory — nothing here
needs to be imported to get the FIXTURES (`state`, `fake_pytest`, `jobs`); the
plain helper functions and constants (`govrun`, `govrun_module`, `GOVRUN`,
`EXIT_REFUSED`, ...) are imported explicitly where a test file uses them.
"""
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_janitor as aj  # noqa: E402

HOUR = 3600
TEST_SLUG = f"-agent-janitor-test-{os.getpid()}"
TEST_SLUG_DIR = f"{aj.SCRATCHPAD_BASE}/{TEST_SLUG}"

GOVRUN = Path(__file__).resolve().parent / "govrun.py"
SHIM = Path(__file__).resolve().parent / "govrun"
BUDGET = 4  # the shipped per-slot worker budget
EXIT_REFUSED = 2
EXIT_QUEUE_TIMEOUT = 75


# --- agent-janitor -----------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Never write to the real ~/.claude/logs from a test run. Also pins
    CLAUDE_ROOT to a sandbox dir -- reap's GearboxLock reads it as its
    default and must never touch the real ~/.claude/.gearbox.lock, which a
    concurrent `gearbox deploy` might legitimately be holding.

    notify() is stubbed for the same reason: a refusal test would otherwise
    fire a REAL macOS notification about sandbox numbers ("1 paths / 1000
    bytes"), which reads as a live janitor failure. Tests that assert on the
    message patch notify themselves with a capturing stub."""
    monkeypatch.setattr(aj, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(aj, "notify", lambda msg: None)
    claude_root = tmp_path / "claude-root"
    claude_root.mkdir()
    monkeypatch.setattr(aj, "CLAUDE_ROOT", str(claude_root))


@pytest.fixture
def spawner():
    """Spawns double-forked (PPID 1) processes and kills them all afterwards."""
    markers = []

    def spawn(tag="", argv0=None, cwd=None):
        """A real PPID-1 `/bin/sh` carrying `tag` in its argv, plus a real child.

        "& wait" is load-bearing: given a single command, sh execs it and
        replaces its own argv, taking the tag (and the scratchpad path the
        detector keys on) with it — and leaving no descendant to tree-kill.
        """
        marker = uuid.uuid4().hex[:12]
        markers.append(marker)
        body = f"sleep 900 & wait  # janitor-test-{marker} {tag}"
        inner = f"/bin/sh -c {_q(body)}"
        if argv0:
            # bash's exec -a is the only way to control argv[0]; used to fake a
            # live `claude` process without running the real binary.
            subprocess.run(["/bin/bash", "-c", f"exec -a {argv0} {inner} &"],
                           cwd=cwd, check=True)
        else:
            subprocess.run(["/bin/sh", "-c", f"{inner} &"], cwd=cwd, check=True)
        for _ in range(100):
            hit = [p for p in aj.ps_snapshot() if marker in p.command and p.ppid == 1]
            if hit:
                return hit[0]
            time.sleep(0.05)
        raise AssertionError(f"detached process {marker} never appeared at PPID 1")

    yield spawn

    for marker in markers:
        for p in aj.ps_snapshot():
            if marker in p.command:
                try:
                    os.kill(p.pid, signal.SIGKILL)
                except OSError:
                    pass


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


@pytest.fixture
def session_dirs():
    """Real scratchpad session dirs under a throwaway slug, aged on demand."""
    made = []

    def make(age_hours):
        sid = str(uuid.uuid4())
        path = f"{TEST_SLUG_DIR}/{sid}"
        os.makedirs(f"{path}/tasks", exist_ok=True)
        Path(f"{path}/tasks/x.output").write_text("")
        stamp = time.time() - age_hours * HOUR
        # deepest-first, or re-stamping a child bumps its parent back to now
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files + dirs:
                os.utime(os.path.join(root, name), (stamp, stamp))
        os.utime(path, (stamp, stamp))
        made.append(path)
        return sid, path

    yield make
    shutil.rmtree(TEST_SLUG_DIR, ignore_errors=True)


# --- govrun --------------------------------------------------------------

@pytest.fixture
def state(tmp_path):
    """A throwaway state dir. Never the real ~/.machine-governor."""
    d = tmp_path / "governor"
    d.mkdir()
    return d


@pytest.fixture
def fake_pytest(tmp_path):
    """An env dict whose PATH leads to a STUB executable named `pytest`.

    The budget check is scoped to payloads that ARE pytest, so every test of it
    must wrap a command NAMED pytest — but running the real pytest from inside
    this suite would recursively collect this very file. The stub exits 0 and
    runs nothing. It also makes the refusal tests safe against regression: if a
    broken check ever ADMITS an over-budget command, what execs is this stub,
    not a real 8-worker suite.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir()
    stub = bindir / "pytest"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}


@pytest.fixture
def jobs():
    """Launches govrun jobs and guarantees every one is dead afterwards."""
    started = []

    def launch(state, payload, klass=None, max_wait=None, env=None, shim=False):
        argv = [str(SHIM)] if shim else [sys.executable, str(GOVRUN)]
        if klass:
            argv += ["--class", klass]
        if max_wait is not None:
            argv += ["--max-wait", str(max_wait)]
        argv += ["--"] + payload
        environ = dict(os.environ, GOVRUN_STATE_DIR=str(state))
        environ.pop("GOVRUN_SLOTS", None)
        environ.pop("PYTEST_ADDOPTS", None)  # govrun reads it; never inherit ours
        environ.update(env or {})
        proc = subprocess.Popen(argv, env=environ, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started.append(proc)
        return proc

    yield launch

    for proc in started:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def govrun(state, *args, env=None, cwd=None):
    """A govrun invocation run to completion.

    `cwd` matters since govrun reads a pytest `addopts` out of the config file
    the payload's working directory resolves to; the ini tests below hand it a
    throwaway repo so they cannot be answered by THIS repo's pytest.ini.
    """
    environ = dict(os.environ, GOVRUN_STATE_DIR=str(state))
    environ.pop("GOVRUN_SLOTS", None)
    environ.pop("PYTEST_ADDOPTS", None)  # govrun reads it; never inherit ours
    environ.update(env or {})
    return subprocess.run([sys.executable, str(GOVRUN), *args], env=environ,
                          cwd=cwd, capture_output=True, text=True, timeout=60)


def file_identity(path):
    """`(st_dev, st_ino)` of the file this name opens, creating it if needed."""
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        st = os.fstat(fd)
        return st.st_dev, st.st_ino
    finally:
        os.close(fd)


def lock_is_free(path):
    """Whether the lock on `path` is unheld, asked by TRYING it.

    `lockf`, matching govrun — NOT `flock`. The two are separate interfaces and
    are not guaranteed to block each other, so an asker in the wrong family can
    read a HELD slot as free and every assertion built on it passes no matter
    what govrun does. On macOS they happen to conflict — measured:
    all four holder/asker combinations of flock and lockf blocked — so on macOS
    a mismatched family would NOT be caught by
    `test_the_free_slot_probe_can_say_held`. That is exactly why the family is
    matched here by construction rather than left to a platform accident; the
    suite also runs on Linux, where the two are independent mechanisms.

    The lock file merely EXISTING proves nothing — validation creates it to ask
    the filesystem for its identity — so "a refused table never took a slot" has
    to be tested on the lock itself. Closing the fd releases what we took.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def govrun_module():
    """govrun.py imported as a module, for the few checks that compare its
    reading of an argv against another parser's. Everything about slots and
    processes is tested through a real subprocess instead."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("govrun_mod", GOVRUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def holder(state, slot, timeout=10.0):
    """Block until `slot` has holder metadata, then return it."""
    path = state / f"{slot}.holder.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass  # mid-write
        time.sleep(0.02)
    raise AssertionError(f"{slot} never recorded a holder within {timeout}s")


def comm(pid):
    out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)],
                         capture_output=True, text=True)
    return out.stdout.strip()


def wait_for_exec(state, slot, name, timeout=10.0):
    """Block until the slot's holder pid has become `name` — i.e. execvp has
    replaced govrun with the payload. Returns the pid."""
    pid = holder(state, slot)["pid"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(comm(pid)).name == name:
            return pid
        time.sleep(0.02)
    raise AssertionError(f"holder {pid} never became {name!r} (is {comm(pid)!r})")
