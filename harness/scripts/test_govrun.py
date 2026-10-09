"""govrun's suite, against REAL processes that are really killed.

Nothing here calls an internal function and calls that a proof. Every claim
govrun makes — the queue forms, the lock survives exec, a `kill -9` frees the
slot, the budget reaches the child, a second job waits for the one slot —
is only true of a real `execvp`'d process holding a real lock, so each test
spawns one and observes it from outside. The one exception names itself:
`test_validation_does_not_drop_a_lock_this_process_holds` is about an ordering
INSIDE one process, which nothing outside it can see.

Two properties of this file are load-bearing:

**Every check can fail.** The queueing test has a known NEGATIVE beside it: the
same two jobs, launched the same way and timed the same way, but under a VALID
two-slot override that splits the SAME 4-worker budget — where they MUST run
concurrently. That pair proves the timing measurement can tell serialised from
concurrent; a queueing test alone would pass just as happily if the second job
were slow for some unrelated reason.

**The shipped table is ONE slot of 4, shared by both classes.** See
`govrun_config.DEFAULT_SLOTS` for the memory measurements that sized it. So
contention here is produced the way production now produces it: ANY two jobs,
same class or not, compete for that one `shared` slot, and the loser reaches its
`--max-wait` and exits 75. There is no per-class reservation left to test; what
`validate_slots` still refuses about classes is a table that STRANDS one — a
class that no slot admits, so nothing at that class could ever run.

No pytest-xdist and no network: the gate runs this under the bare `python3`,
where xdist is not installed.

The pytest-worker-count checks (option parsing, ini/env/argv precedence,
the argparse differential table) live in test_govrun_pytest_budget.py —
split out purely to stay under the file-size ratchet; the fixtures and
helpers both files share are in conftest.py.
"""
import errno
import fcntl
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import govrun_config
import govrun_locks
from govrun_errors import Refused

from conftest import (
    BUDGET,
    EXIT_QUEUE_TIMEOUT,
    EXIT_REFUSED,
    GOVRUN,
    SHIM,
    comm,
    file_identity,
    govrun,
    govrun_module,
    lock_is_free,
    wait_for_exec,
)


# A VALID override splitting the same 4-worker machine budget across TWO slots,
# each open to both classes. It is what the known negatives below are built on:
# with the shipped 1-slot table nothing can run concurrently, so "concurrent"
# has to be demonstrated somewhere, or "serialised" is unfalsifiable.
# `test_the_two_slot_override_is_accepted` pins that the validator really takes
# it — an override it refused would exit 2, and every test resting on it would
# be measuring that refusal instead of what it claims to measure.
TWO_SLOTS = {"GOVRUN_SLOTS": json.dumps([
    {"name": "a", "workers": 2, "classes": ["ci", "interactive"]},
    {"name": "b", "workers": 2, "classes": ["ci", "interactive"]},
])}


# --- the queue forms, and the measurement can tell that it did --------------

def test_the_two_slot_override_is_accepted(state):
    """Load-bearing for both known negatives below — read the comment on
    TWO_SLOTS. Asserts the payload really RAN, not merely that govrun exited 0."""
    result = govrun(state, "--", "echo", "TWO-SLOTS-OK", env=TWO_SLOTS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TWO-SLOTS-OK" in result.stdout


def test_a_second_job_queues_behind_the_first(state, jobs):
    """KNOWN POSITIVE, shipped default table: the machine has ONE slot, so the
    second job waits for the first however it is classed."""
    first = jobs(state, ["sleep", "2"])
    wait_for_exec(state, "shared", "sleep")

    start = time.monotonic()
    second = jobs(state, ["echo", "SECOND"], max_wait=0)
    out, err = second.communicate(timeout=30)
    waited = time.monotonic() - start

    assert second.returncode == 0
    assert "SECOND" in out
    assert waited >= 1.0, f"the second job did not queue (finished in {waited:.2f}s)"
    assert "govrun: acquired shared after" in err
    assert first.poll() == 0


def test_two_jobs_do_not_queue_when_the_table_has_two_slots(state, jobs):
    """KNOWN NEGATIVE for the test above. Same launch, same timing method, same
    4-worker budget — but split across two slots, so this pair must NOT
    serialise. Without it, the queueing test proves only that the second job was
    slow for some unrelated reason."""
    jobs(state, ["sleep", "2"], env=TWO_SLOTS)
    wait_for_exec(state, "a", "sleep")

    start = time.monotonic()
    second = jobs(state, ["echo", "SECOND"], max_wait=0, env=TWO_SLOTS)
    out, _ = second.communicate(timeout=30)
    waited = time.monotonic() - start

    assert second.returncode == 0
    assert "SECOND" in out
    assert waited < 1.0, f"the second job queued despite a free slot ({waited:.2f}s)"


def test_the_lock_survives_execvp_so_the_payload_holds_the_slot(state, jobs):
    """The close-on-exec trap. govrun.py is gone by now — the process holding
    the lock IS `sleep`. If the fd were not made inheritable the slot would be
    free here and the contender would acquire at once.

    This is also the KNOWN NEGATIVE for the descendant test below: it is what
    stops "the slot frees promptly" being satisfied by a lock that never held.
    """
    jobs(state, ["sleep", "5"])
    pid = wait_for_exec(state, "shared", "sleep")
    assert Path(comm(pid)).name == "sleep"

    contender = jobs(state, ["echo", "X"], max_wait=1)
    assert contender.wait(timeout=30) == EXIT_QUEUE_TIMEOUT, (
        "the slot was free while the exec'd payload was still running — "
        "the lock died at execvp; check os.set_inheritable(fd, True)")


def test_a_backgrounded_descendant_does_not_keep_the_slot(state, jobs):
    """Review round 6's high finding: the slot must belong to the PAYLOAD, not
    to everything the payload leaves running.

    `os.set_inheritable(fd, True)` is what carries the lock through `execvp`,
    but an inheritable fd is inherited by every descendant too. Under a
    `fcntl.flock` — which belongs to the open file description — a payload that
    backgrounds anything (`pytest &`, a test that leaves a server up, a shell
    wrapper that spawns a daemon) handed the slot to a process nobody waits on,
    and the next job queued behind a ghost until that ghost died. With
    `--max-wait 0` on the ci class there is no bound on that at all.

    `fcntl.lockf` is owned by the PROCESS and is not inherited across `fork`, so
    the slot frees when the payload exits. Reverting `_try_lock` to `flock`
    turns this red: measured here, the contender waited the orphan's full 8 s.

    The orphan is deliberately given a long life relative to the assertion
    window, so this cannot pass by the orphan happening to be quick.
    """
    payload = jobs(state, ["/bin/sh", "-c", "sleep 8 & exit 0"])
    assert payload.wait(timeout=30) == 0, "the payload itself must have exited"

    start = time.monotonic()
    contender = jobs(state, ["echo", "AFTER-ORPHAN"], max_wait=6)
    out, err = contender.communicate(timeout=30)
    waited = time.monotonic() - start

    assert contender.returncode == 0, (
        f"the slot was still held {waited:.1f}s after the payload exited — a "
        f"backgrounded descendant inherited the lock. stderr: {err}")
    assert "AFTER-ORPHAN" in out
    assert waited < 4.0, (
        f"the contender waited {waited:.2f}s for a payload that had already "
        "exited; the orphaned `sleep` was holding the slot")


HOLD_THEN_VALIDATE = """
import importlib.util, os, sys, time
os.environ["GOVRUN_STATE_DIR"] = sys.argv[2]
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
slot = mod.DEFAULT_SLOTS[0]
assert mod._try_lock(slot) is not None, "could not take the slot to begin with"
if sys.argv[3] == "validate":
    mod.validate_slots(mod.DEFAULT_SLOTS, "probe")
print(slot["name"], flush=True)
time.sleep(30)
"""


@pytest.mark.parametrize("then", ["validate", "nothing"])
def test_validation_does_not_drop_a_lock_the_same_process_holds(state, then):
    """A POSIX record lock is released when the owning process closes ANY file
    descriptor on the locked file. `_lock_identity` asks the filesystem for
    every lock file's identity, and while it did that with an open/close it
    really did drop a lock the same process was holding — measured by this test,
    which failed on `then="validate"` until `_lock_identity` was changed to
    `os.stat` (which opens no descriptor). Had it shipped, govrun would have
    exec'd the payload having silently handed the slot back.

    Validating before acquiring also avoids it, but that is a convention a
    refactor can break silently, so the property asserted here is the strong
    one: validation is safe at ANY point, including while a slot is held.

    The holder has to be a CHILD, and the probe has to come from outside it: a
    process may always re-take a record lock it already holds, so asking
    in-process would answer "free" no matter what — and the asking would itself
    drop the lock. `then="nothing"` is the known negative that proves the child
    really is holding a slot the parent can see as HELD.
    """
    holder_proc = subprocess.Popen(
        [sys.executable, "-c", HOLD_THEN_VALIDATE, str(GOVRUN), str(state),
         then],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        name = holder_proc.stdout.readline().strip()
        assert name, "the holder child never reported a slot: " + \
            holder_proc.stderr.read()
        assert not lock_is_free(state / f"{name}.lock"), (
            f"the slot was free while a live process held it (then={then!r}) — "
            "validation opened and closed the lock file and dropped the record "
            "lock, so _lock_identity must run BEFORE acquire, never after")
    finally:
        holder_proc.kill()
        holder_proc.wait()
        holder_proc.stdout.close()
        holder_proc.stderr.close()
    assert lock_is_free(state / f"{name}.lock"), "and death frees it"


def test_the_free_slot_probe_can_say_held(state, jobs):
    """`lock_is_free` is the probe several assertions here rest on, so it has to
    be shown answering HELD about a slot a real payload is really holding, and
    FREE about one nothing holds. Both directions, on the same call.

    What this does NOT prove, said plainly so nobody reads more into it: on
    macOS `flock` and `lockf` block each other (measured), so this would still
    pass if the probe asked in the wrong family. Matching govrun's family is a
    construction-time requirement in `lock_is_free`, not something this test can
    catch on macOS."""
    jobs(state, ["sleep", "5"], env=TWO_SLOTS)
    wait_for_exec(state, "a", "sleep")
    assert not lock_is_free(state / "a.lock"), (
        "the probe reported a held slot as free — it is asking with the wrong "
        "lock family and every test that uses it is vacuous")
    assert lock_is_free(state / "b.lock"), "and it can still say FREE"


def test_the_free_slot_probe_cannot_be_asked_about_this_process(state):
    """The one thing `lock_is_free` cannot answer, written down because it cost
    a wrong test: a POSIX record lock belongs to the PROCESS, so a process that
    already holds one can always take it again. Asked from inside the holder the
    answer is "free" whatever the truth is — and the asking closes an fd on the
    file, which really does drop the lock. Every use of it here therefore probes
    a slot held by some OTHER process."""
    lock = state / "self.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert lock_is_free(lock), "a process can re-take its own record lock"
    finally:
        os.close(fd)


def test_kill_9_on_the_holder_frees_the_slot_within_two_seconds(state, jobs):
    """Release is process death. No cleanup path, no daemon, no stale lock."""
    first = jobs(state, ["sleep", "60"])
    pid = wait_for_exec(state, "shared", "sleep")

    waiter = jobs(state, ["echo", "AFTER-CRASH"], max_wait=0)
    time.sleep(1.0)
    assert waiter.poll() is None, "the waiter got in while the slot was held"

    os.kill(pid, signal.SIGKILL)
    start = time.monotonic()
    out, _ = waiter.communicate(timeout=30)
    freed = time.monotonic() - start

    assert "AFTER-CRASH" in out
    assert freed < 2.0, f"slot took {freed:.2f}s to free after kill -9"
    first.wait(timeout=5)


# --- a lock on a file unlinked underneath it is refused, never handed out ----

def _sabotage(kind, state):
    """Do to the lock file what `rm -rf` does, at a moment we choose."""
    path = state / "shared.lock"
    if kind == "unlinked":
        shutil.rmtree(state)                 # the operator's `rm -rf`
    elif kind == "replaced":
        # A second link keeps the locked inode's st_nlink at 1, so this reaches
        # the (st_dev, st_ino) branch of the guard rather than the nlink one —
        # without it the two cases would test the same line.
        os.link(path, str(path) + ".kept")
        os.unlink(path)
        os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o644))


@pytest.mark.parametrize("kind", ["none", "unlinked", "replaced"])
def test_a_lock_on_a_file_unlinked_underneath_it_is_refused(
        state, monkeypatch, kind):
    """A lock on an inode no name reaches guards nothing, and must never be
    handed out as a slot: the holder would run while the next job creates a
    fresh file at the same path, locks that, and runs too — 8 workers, the
    configuration one project recorded as fatal.

    The delete has to land between `_try_lock`'s `os.open` and its
    `fcntl.lockf`, which in the field is a microsecond race. Forcing it through
    `fcntl.lockf` makes it deterministic; `_try_lock`, the `os.stat`/`os.fstat`
    it guards with, and the filesystem are all real. `kind="none"` is the KNOWN
    POSITIVE that proves this harness hands out a slot when nothing is wrong —
    without it, a guard that refused everything would pass just as happily.
    """
    monkeypatch.setenv("GOVRUN_STATE_DIR", str(state))
    real_lockf = fcntl.lockf

    def sabotaging_lockf(fd, cmd, *args):
        _sabotage(kind, state)
        return real_lockf(fd, cmd, *args)

    monkeypatch.setattr(fcntl, "lockf", sabotaging_lockf)
    slot = govrun_config.DEFAULT_SLOTS[0]

    if kind == "none":
        fd = govrun_locks._try_lock(slot)
        assert fd is not None, "the control could not take a free slot"
        os.close(fd)
        return
    with pytest.raises(Refused) as caught:
        govrun_locks._try_lock(slot)
    assert str(state) in str(caught.value), (
        "the refusal must name the state directory that was removed, or the "
        f"operator cannot tell what happened: {caught.value}")


def test_the_guard_does_not_help_a_job_that_starts_after_the_delete(state, jobs):
    """The KNOWN CEILING of the guard above, pinned so nobody reads more into
    it than it does. MEASURED.

    `_try_lock` can only compare the descriptor it just locked against the name
    it opened. A job that starts AFTER the directory is gone has no such
    disagreement to find: `state_dir()` re-creates the directory, `O_CREAT`
    makes a new lock file, and descriptor and name agree perfectly — while the
    older job still holds the unlinked inode. Nothing reachable from inside an
    acquirer can see that older holder; after the delete there is no shared
    kernel object left to ask. That is why the rule still says: never delete
    the state directory.

    If a future change moves the rendezvous somewhere `rm -rf` of the state dir
    cannot reach, this test is the one to DELETE, and the rule it
    pins is the one to correct.
    """
    jobs(state, ["sleep", "5"])
    wait_for_exec(state, "shared", "sleep")
    assert not lock_is_free(state / "shared.lock"), "control: the slot is held"

    shutil.rmtree(state)
    second = jobs(state, ["echo", "AFTER-DELETE"], max_wait=1)
    out, err = second.communicate(timeout=30)
    assert second.returncode == 0 and "AFTER-DELETE" in out, (
        "the ceiling above has moved — a job starting after the delete no "
        f"longer acquires. Update the rule and delete this test. {err}")


@pytest.mark.parametrize("code,expect", [
    (errno.EAGAIN, "busy"), (errno.EACCES, "busy"),
    (errno.ENOLCK, "refused"), (errno.EBADF, "refused"),
    (errno.EDEADLK, "refused"), (errno.EOPNOTSUPP, "refused"),
])
def test_only_a_contended_lock_is_read_as_a_busy_slot(
        code, expect, state, monkeypatch):
    """`except OSError: return None` read EVERY lockf failure as contention, so
    a lock that does not work (no record locking on the filesystem, ENOLCK, a
    bad descriptor) was indistinguishable from a slot someone else holds — the
    job polls to --max-wait and exits 75 saying "queue timeout", and the real
    fault never reaches anyone. EAGAIN/EACCES are the only two POSIX gives for
    contention; both are checked because they are the SAME value on Linux and
    different on Darwin, so a test of one alone proves nothing on the other.
    """
    def refuse(*_args, **_kwargs):
        raise OSError(code, os.strerror(code))
    monkeypatch.setattr(govrun_locks.fcntl, "lockf", refuse)
    slot = {"name": "shared", "workers": BUDGET, "classes": ["ci"]}
    monkeypatch.setenv("GOVRUN_STATE_DIR", str(state))

    if expect == "busy":
        assert govrun_locks._try_lock(slot) is None
        return
    with pytest.raises(Refused) as caught:
        govrun_locks._try_lock(slot)
    assert "not a busy slot" in str(caught.value)
    assert errno.errorcode[code] in str(caught.value)


# --- what the slot hands the payload ----------------------------------------

def test_the_payload_sees_the_slot_worker_budget(state, jobs):
    proc = jobs(state, [sys.executable, "-c",
                        "import os; print(os.environ['PYTEST_XDIST_AUTO_NUM_WORKERS'])"])
    out, _ = proc.communicate(timeout=30)
    assert out.strip() == str(BUDGET)


def test_interactive_is_niced_and_ci_is_not(state, jobs):
    """CPU priority only — this does NOT address the memory failure. Read from
    the real exec'd process, not from govrun's own claim about itself.

    Asserted relative to this process's own niceness rather than against a bare
    0, so a runner that is itself niced cannot turn a working governor red.
    """
    base = os.nice(0)
    # Both payloads have to be alive AT ONCE, which the shipped 1-slot table
    # cannot do — so this runs under the two-slot override, and each job is
    # allowed to reach its slot before the next is launched, or which job lands
    # in which slot is a race.
    jobs(state, ["sleep", "5"], env=TWO_SLOTS)
    inter = wait_for_exec(state, "a", "sleep")
    jobs(state, ["sleep", "5"], klass="ci", env=TWO_SLOTS)
    ci = wait_for_exec(state, "b", "sleep")

    def nice(pid):
        return int(subprocess.run(["ps", "-o", "nice=", "-p", str(pid)],
                                  capture_output=True, text=True).stdout.strip())

    assert nice(ci) == base, "a ci payload must not be de-prioritised"
    assert nice(inter) == base + 10, "an interactive payload must run at nice +10"


# --- one shared slot: the classes contend, and the refusal says so ----------

def test_an_interactive_job_contends_with_a_ci_one_for_the_single_slot(
        state, jobs):
    """The ACCEPTED CONSEQUENCE of the 1-slot table, pinned so it can never
    become an accident: there is no reservation left, so an interactive job
    launched while a ci shard holds the slot serialises behind it and, on a
    bounded wait, exits 75 saying exactly that. A legible refusal is the trade
    taken against the 8-worker OOM the 2x4 table shipped."""
    jobs(state, ["sleep", "5"], klass="ci")
    wait_for_exec(state, "shared", "sleep")

    blocked = jobs(state, ["echo", "X"], max_wait=1)
    _, err = blocked.communicate(timeout=30)
    assert blocked.returncode == EXIT_QUEUE_TIMEOUT, err
    assert "queue timeout, not a test failure" in err


def test_the_wait_defaults_fail_fast_for_interactive_and_never_for_ci():
    """A 540s-style default would convert a queue-time kill into a MID-TEST
    kill, because the Bash tool's ceiling is 600s and a full suite can run most of
    that ceiling. Interactive must fail fast; CI must queue."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("govrun_mod", GOVRUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DEFAULT_MAX_WAIT["interactive"] == 120
    assert mod.DEFAULT_MAX_WAIT["ci"] == 0  # 0 = wait forever
    assert mod.DEFAULT_CLASS == "interactive"


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "Infinity", "-inf"])
def test_a_non_finite_max_wait_is_refused(state, value):
    """`float()` accepts 'nan' and 'inf'. Both defeat the bound this flag
    exists to impose — `waited >= nan` is ALWAYS false and `waited >= inf`
    never becomes true — so an un-defaulted infinite wait, this design's worst
    failure mode, is reachable through the very flag that bounds it."""
    result = govrun(state, "--max-wait", value, "--", "echo", "X")
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "finite" in result.stderr or "must be >= 0" in result.stderr


def test_a_non_finite_max_wait_does_not_wait_forever(state, jobs):
    """The refusal above is only worth something if the alternative was a hang.
    Hold the slot, then prove `--max-wait nan` returns promptly instead of
    queueing past the timeout it was told to honour."""
    jobs(state, ["sleep", "10"])
    wait_for_exec(state, "shared", "sleep")

    start = time.monotonic()
    blocked = govrun(state, "--max-wait", "nan", "--", "echo", "X")
    elapsed = time.monotonic() - start
    assert blocked.returncode == EXIT_REFUSED
    assert elapsed < 5.0, f"--max-wait nan queued for {elapsed:.1f}s"




# --- a broken config file is refused, never silently defaulted ---------------

@pytest.mark.parametrize("text", ["[]", '"slots"', "{not json", ""])
def test_a_config_file_that_is_not_an_object_is_refused_not_defaulted(
        state, text):
    """Round 3 (medium): a config.json that parses to a non-dict fell back to
    DEFAULT_SLOTS with no error — the operator's override was silently ignored.
    A file that EXISTS but is not a JSON object is a broken override."""
    (state / "config.json").write_text(text)
    result = govrun(state, "--", "echo", "X")
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "config.json" in result.stderr


def test_a_config_object_without_slots_still_defaults(state):
    """The carve-out that must survive: a VALID object that just doesn't set
    `slots` (e.g. only `hook_mode`) keeps the default table."""
    (state / "config.json").write_text('{"hook_mode": "warn"}')
    result = govrun(state, "--", "echo", "OK")
    assert result.returncode == 0, result.stdout + result.stderr


# --- overrides may not undo the invariant -----------------------------------

def test_an_override_that_strands_a_class_is_refused(state):
    """A class no slot admits can never acquire — every job at it is refused
    outright. The table below is otherwise legal (one slot, 4 workers, a valid
    name), so only the stranding can be what refuses it."""
    ci_only = json.dumps([{"name": "shared", "workers": 4, "classes": ["ci"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": ci_only})
    assert result.returncode == EXIT_REFUSED
    assert "STRANDED" in result.stderr
    assert "interactive" in result.stderr


def test_an_override_over_the_machine_budget_is_refused(state):
    """4 is the whole machine now, so the OLD 2x4 default table — the exact
    shape one project measured as fatal — is itself over budget and refused."""
    fat = json.dumps([{"name": "ci", "workers": 4, "classes": ["ci"]},
                      {"name": "interactive", "workers": 4,
                       "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": fat})
    assert result.returncode == EXIT_REFUSED
    assert "exceeds the machine budget" in result.stderr


def test_two_slots_sharing_a_name_are_refused(state):
    """A slot name IS a lock filename. Two slots named the same reserve two
    classes onto ONE lock, so `ci` and `interactive` contend and either can
    starve the other — while every other validation still says yes."""
    twins = json.dumps([{"name": "same", "workers": 2, "classes": ["ci"]},
                        {"name": "same", "workers": 2,
                         "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": twins})
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "SAME lock file" in result.stderr
    assert lock_is_free(state / "same.lock"), "a refused table must not lock"


def test_two_slot_names_linked_to_one_file_are_refused(state):
    """The collision no rule about NAMES could ever catch.

    `alias.lock` is a symlink to `real.lock`: two names with nothing in common,
    ONE file, ONE flock — so the two classes would contend and the reservation
    invariant would be gone. This is the case that settles WHERE the check
    belongs: distinctness is a property of the file a name opens, not of the
    name. Unlike case folding it collides on every filesystem, so this test is
    not at the mercy of whichever volume the suite runs on.
    """
    (state / "real.lock").touch()
    (state / "alias.lock").symlink_to(state / "real.lock")
    linked = json.dumps([{"name": "real", "workers": 2, "classes": ["ci"]},
                         {"name": "alias", "workers": 2,
                          "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": linked})
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "SAME lock file" in result.stderr


def test_case_only_slot_names_are_refused_wherever_the_filesystem_folds_them(
        state):
    """The finding itself, decided by the volume that actually decides it.

    On macOS's default APFS volume the names `same` and `SAME` open ONE file (Apple's
    APFS Guide: the default variant is case-insensitive and
    "normalization-preserving, but not normalization-sensitive"), so two slots
    spelled that way would share a lock while every string comparison insisted
    the names differ — which is exactly how case folding walked past two rounds
    of name validation. On a case-SENSITIVE volume they are genuinely two locks
    and admitting them is CORRECT, not a miss. So the filesystem is asked first
    and BOTH outcomes are asserted; this never degrades into a skip that proves
    nothing.
    """
    folds = file_identity(state / "probe.lock") == file_identity(
        state / "PROBE.lock")
    cased = json.dumps([{"name": "same", "workers": 2, "classes": ["ci"]},
                        {"name": "SAME", "workers": 2,
                         "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": cased})
    if folds:
        assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
        assert "SAME lock file" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_shipped_default_table_passes_its_own_validation(state, monkeypatch):
    """The default table skips validation on the fast path, so nothing else
    would notice if it stopped satisfying the rules it imposes on overrides."""
    monkeypatch.setenv("GOVRUN_STATE_DIR", str(state))
    mod = govrun_module()
    mod.validate_slots(mod.DEFAULT_SLOTS, "default")


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".", "sl ot"])
def test_a_slot_name_that_is_not_a_safe_filename_is_refused(state, name):
    """Same root cause as the duplicate-name defect: the name reaches the
    filesystem unchecked, so it could also write outside the state dir."""
    slots = json.dumps([{"name": name, "workers": 2, "classes": ["ci"]},
                        {"name": "interactive", "workers": 2,
                         "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": slots})
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "lock FILENAME" in result.stderr


@pytest.mark.parametrize("workers", [True, False])
def test_a_boolean_worker_budget_is_refused(state, workers):
    """`bool` is a subclass of `int` in Python, so `"workers": true` sails past
    a bare isinstance(..., int) and then budgets the slot at ONE worker."""
    slots = json.dumps([{"name": "ci", "workers": workers, "classes": ["ci"]},
                        {"name": "interactive", "workers": 2,
                         "classes": ["interactive"]}])
    result = govrun(state, "--", "true", env={"GOVRUN_SLOTS": slots})
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "whole number >= 1" in result.stderr


def test_config_json_supplies_slots_and_the_env_overrides_it(state):
    """Tunables live in a govrun-owned config.json, never in settings.json —
    the Claude Code binary rewrites that file and a concurrent write is what
    forced autoCompactWindow out of it."""
    (state / "config.json").write_text(json.dumps({"slots": [
        {"name": "ci", "workers": 1, "classes": ["ci"]},
        {"name": "interactive", "workers": 3, "classes": ["interactive"]}]}))
    result = govrun(state, "--", sys.executable, "-c",
                    "import os; print(os.environ['PYTEST_XDIST_AUTO_NUM_WORKERS'])")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3"

    env_slots = json.dumps([{"name": "ci", "workers": 1, "classes": ["ci"]},
                            {"name": "interactive", "workers": 1,
                             "classes": ["interactive"]}])
    result = govrun(state, "--", sys.executable, "-c",
                    "import os; print(os.environ['PYTEST_XDIST_AUTO_NUM_WORKERS'])",
                    env={"GOVRUN_SLOTS": env_slots})
    assert result.stdout.strip() == "1", "env must win over config.json"


# --- stale metadata is informational, never a lock --------------------------

def test_a_dead_holders_metadata_is_reported_stale_and_then_overwritten(state):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (state / "shared.holder.json").write_text(json.dumps(
        {"pid": dead.pid, "command": "ghost", "class": "interactive",
         "started_at": "2026-08-27T00:00:00+01:00", "queued_seconds": 0.0}))

    status = govrun(state, "--status")
    assert "FREE (stale holder:" in status.stdout
    assert f"pid={dead.pid} dead" in status.stdout

    result = govrun(state, "--", "echo", "LIVE")
    assert result.returncode == 0, result.stderr
    assert json.loads((state / "shared.holder.json").read_text())["pid"] != dead.pid


@pytest.mark.parametrize("body", ["{not json", '"a string, not an object"'])
def test_unreadable_holder_metadata_does_not_break_status(state, body):
    """Both bad shapes, one slot at a time — the shipped table has only one."""
    (state / "shared.holder.json").write_text(body)
    result = govrun(state, "--status")
    assert result.returncode == 0, result.stderr
    assert "slot shared" in result.stdout


# --- the shim -------------------------------------------------------------

def test_the_shim_is_executable_and_runs_the_payload(state, jobs):
    assert os.access(SHIM, os.X_OK), f"{SHIM} is not executable"
    proc = jobs(state, ["echo", "VIA-SHIM"], shim=True)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    assert "VIA-SHIM" in out


def test_running_nothing_is_a_refusal_not_a_silent_success(state):
    result = govrun(state)
    assert result.returncode == EXIT_REFUSED
    assert "usage" in result.stderr


# --- the probe script's own exit code -----------------------------------------

def probes_module():
    """govrun_probes.py imported as a module. Its probes sleep for tens of
    seconds, so the exit rule is asserted here rather than by running them."""
    import importlib.util
    path = GOVRUN.parent / "govrun_probes.py"
    spec = importlib.util.spec_from_file_location("govrun_probes_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_failed_probe_does_not_exit_successfully():
    """It used to. A probe wrote `FAIL:` into its transcript and the script
    still returned 0, so an operator or a closeout reading the exit code saw a
    green run of a governor that had just misbehaved. The two categories stay
    apart: ABORT means the PROBE is broken, FAILURES means the governor is."""
    mod = probes_module()
    assert mod.exit_code() == 0, "a fresh module has nothing to report"

    transcript = io.StringIO()
    write = mod.Tee(transcript)
    write("probe2: waiter acquired 0.4s after kill -9 (expect < 2s) -> PASS")
    assert mod.exit_code() == 0, "a PASS line is not a failure"

    write("probe1 control: the 2-slot table CONTROL FAIL, whatever follows")
    assert mod.exit_code() == 0, "a control failure is an ABORT, counted elsewhere"

    write("probe3: FAIL: exceeded the shipped ceiling (6 > 4)")
    assert mod.FAILURES == ["probe3: FAIL: exceeded the shipped ceiling (6 > 4)"]
    assert mod.exit_code() == 1

    mod.FAILURES.clear()
    mod.ABORT.append("probe1 control: did not run concurrently")
    assert mod.exit_code() == 1, "an ABORT alone is still a non-zero exit"


def test_a_probe_verdict_is_computed_not_scraped_out_of_its_prose():
    """Scraping the verdict back out of the transcript line had a hole exactly
    the shape of probe 2's live-holder check: it printed its expectation
    WITHOUT the word FAIL, so a slot still HELD by a live process after the
    crash-release test — the one defect that probe exists to catch — left
    FAILURES empty and the script exited 0. `Tee.check` records the pass/fail
    and writes the line FROM it, so the two can no longer disagree."""
    mod = probes_module()
    held = ["slot shared workers=4 classes=ci HELD pid=999 alive cmd=sleep 30"]

    transcript = io.StringIO()
    write = mod.Tee(transcript)
    write(f"slot lines showing a LIVE holder: {held!r} (expect empty)")
    assert mod.exit_code() == 0, (
        "the line the probe USED to write still says nothing — that is the hole, "
        "and it is why the verdict may not be read out of prose")

    write.check(not held, f"no slot shows a LIVE holder after the crash: {held!r}")
    assert mod.exit_code() == 1, "a computed failure reaches the exit code"
    assert transcript.getvalue().splitlines()[-1].startswith("FAIL: no slot shows")

    mod.FAILURES.clear()
    assert write.check(True, "no slot shows a LIVE holder after the crash: []")
    assert mod.exit_code() == 0, "a passing check records nothing"
    assert transcript.getvalue().splitlines()[-1].startswith("PASS: no slot shows")
