"""govrun's slot table and its config-file/env override.

Split out of govrun.py, which this — the slot table, its validation, and the
low-level state-dir/file-read/lock-identity helpers everything else in govrun
shares — pushed over the file-size ratchet. See govrun.py's own module
docstring for what govrun is and why.

`_lock_path`/`_lock_identity` live here rather than alongside `_try_lock` in
govrun_locks.py because `validate_slots` needs them (to detect two slot names
that resolve to one lock file) and govrun_locks imports `state_dir` from this
module — putting them here too avoids the two modules importing each other.
"""
import json
import os
import re
from pathlib import Path

from govrun_errors import Refused

# Authoritative slot table. Override: GOVRUN_SLOTS env (JSON) > <state
# dir>/config.json "slots" > this. Tunables live in a govrun-OWNED config file
# and never in ~/.claude/settings.json: the Claude Code binary rewrites that
# file on a `/model` switch, and a concurrent rewrite is exactly what forced
# `autoCompactWindow` out of it (in an earlier commit) to stop `gearbox deploy`
# refusing. A govrun key there would reproduce that.
#
# ONE slot of 4, SHARED by both classes — not two slots of 4. The 2x4 table this
# replaces shipped the exact configuration one project measured as FATAL: its
# .github/workflows/ci.yml records that at `max-parallel: 1`, two
# shards at `-n 4` — 8 xdist workers — produced "symmetric death at ~10min,
# 0-byte logs, no artifacts", and that 4 workers fit the CI memory budget —
# measured as roughly 2 GB per worker in that project, more than an earlier estimate carried
# over from another suite: a worker COUNT is not a RAM budget across suites, so
# the ceiling has to be the MACHINE's, not one class's. And a busy laptop may
# have no headroom to lend: it can sit deep in swap at IDLE with no suite running.
# So the machine-wide ceiling is 4 workers TOTAL, and with only 4 to hand out
# there is nothing left to reserve per class.
#
# ACCEPTED CONSEQUENCE, stated plainly: one slot means no reservation, so an
# interactive job launched while a CI shard holds the slot now reaches its 120 s
# `--max-wait` and exits 75 with "queue timeout, not a test failure". That is the
# intended trade — a legible refusal beats an OOM kill, and the caller can re-run
# or pass `--max-wait 0` and launch in the BACKGROUND.
DEFAULT_SLOTS = [
    {"name": "shared", "workers": 4, "classes": ["ci", "interactive"]},
]
DEFAULT_CLASS = "interactive"

# An un-defaulted wait is this design's worst failure mode. The Bash tool's hard
# ceiling is 600 s and a full suite can run most of that ceiling, so a wait
# anywhere near the ceiling converts a queue-time kill into a MID-TEST kill —
# strictly worse. 120 s fails fast and legibly and leaves the caller room to
# run. A caller who genuinely wants to queue passes `--max-wait 0` and launches
# in the BACKGROUND: no single foreground Bash call can hold wait + run for a
# suite of this length. CI gets no limit — a shard should queue, and its
# GitHub `timeout-minutes` is the real bound.
DEFAULT_MAX_WAIT = {"interactive": 120, "ci": 0}  # 0 = wait forever

POLL_SECONDS = 0.5
NICE_BY_CLASS = {"interactive": 10}  # CPU priority only; does NOT cap memory
EXIT_REFUSED = 2
EXIT_QUEUE_TIMEOUT = 75

# A slot NAME is a filename: `<state dir>/<name>.lock`. This pattern closes
# CONTAINMENT only — a name with a `/` or a `..` would write outside the state
# dir. It deliberately does NOT try to decide whether two names collide; that
# question belongs to the filesystem and is answered by `_lock_identity`.
SLOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


# --- configuration ----------------------------------------------------------

def state_dir():
    d = Path(os.environ.get("GOVRUN_STATE_DIR") or Path.home() / ".machine-governor")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_text(path):
    """`--status` is the only liveness check for a hook that FAILS OPEN, so it
    must never crash on an unreadable file — it would look like a clean bill."""
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _read_json(path):
    """Guard the PARSE and the TYPE — a valid JSON scalar is not a dict."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value


def load_slots():
    """env > config.json > module default. Every override is validated."""
    raw = os.environ.get("GOVRUN_SLOTS")
    if raw:
        try:
            slots = json.loads(raw)
        except ValueError as exc:
            raise Refused(f"GOVRUN_SLOTS is not valid JSON: {exc}")
        source = "GOVRUN_SLOTS env"
    else:
        path = state_dir() / "config.json"
        config = _read_json(path)
        if path.exists() and not isinstance(config, dict):
            # A config file that EXISTS but cannot be read as an object is a
            # broken override, not "no override". Falling back to defaults here
            # would silently ignore whatever the operator wrote.
            raise Refused(f"{path} exists but is not a JSON object — fix it or "
                          "delete it (defaults apply only when it is absent)")
        slots = config.get("slots") if isinstance(config, dict) else None
        source = "config.json"
    if slots is None:
        return DEFAULT_SLOTS
    validate_slots(slots, source)
    return slots


def _default_classes():
    return {c for slot in DEFAULT_SLOTS for c in slot["classes"]}


def validate_slots(slots, source):
    """Refuse any override that STRANDS a class or breaks the machine budget.

    An override is otherwise free to split the budget however it likes — two
    slots of 2 is as valid as the shipped one of 4, and handing one class every
    slot (starvation) is allowed. What it may not do is raise the machine-wide
    worker total (the incident this exists to stop), or leave a class with no
    slot that admits it at all.

    Distinctness is decided on the LOCK FILE each name resolves to, never on the
    name itself — see `_lock_identity`.
    """
    if not isinstance(slots, list) or not slots:
        raise Refused(f"{source}: slots must be a non-empty list")
    seen = {}  # (st_dev, st_ino) -> the first slot name that opened that file
    for slot in slots:
        if not isinstance(slot, dict):
            raise Refused(f"{source}: each slot must be an object, got {slot!r}")
        name, workers, classes = (slot.get("name"), slot.get("workers"),
                                  slot.get("classes"))
        if not isinstance(name, str) or not SLOT_NAME_RE.match(name):
            raise Refused(f"{source}: slot 'name' must be letters, digits, '_' "
                          f"or '-' (it becomes a lock FILENAME): {name!r}")
        try:
            identity = _lock_identity(_lock_path(slot))
        except OSError as exc:
            raise Refused(f"{source}: slot {name!r} cannot open its lock file "
                          f"{_lock_path(slot)}: {exc}")
        if identity in seen:
            raise Refused(
                f"{source}: slots {seen[identity]!r} and {name!r} resolve to the "
                "SAME lock file, so the classes they reserve would contend for "
                "ONE lock and each could starve the other. Give every slot a "
                "name that is a DISTINCT FILE on this filesystem — names that "
                "differ only in case, or only in unicode normalization, are the "
                "same file on a default macOS volume.")
        seen[identity] = name
        # bool is a subclass of int, so `"workers": true` passes a bare
        # isinstance(..., int) and then budgets a slot at 1 worker.
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise Refused(f"{source}: slot {name!r} needs 'workers' to be a "
                          f"whole number >= 1, got {workers!r}")
        if not isinstance(classes, list) or not classes or not all(
                isinstance(c, str) for c in classes):
            raise Refused(f"{source}: slot {name!r} needs a non-empty "
                          "'classes' list of strings")

    for klass in sorted(_default_classes()):
        if not any(klass in slot["classes"] for slot in slots):
            raise Refused(
                f"{source}: class {klass!r} is STRANDED — no slot in this table "
                f"admits it, so every {klass!r} job is refused outright instead "
                "of queued, and nothing can ever run at that class again. Add "
                f"{klass!r} to some slot's 'classes'.")

    total = sum(slot["workers"] for slot in slots)
    ceiling = sum(slot["workers"] for slot in DEFAULT_SLOTS)
    if total > ceiling:
        raise Refused(f"{source}: total workers {total} exceeds the machine "
                      f"budget of {ceiling}. Lower 'workers', or accept the "
                      "default table.")




# --- lock identity (shared by validate_slots and _try_lock) ------------

def _lock_path(slot):
    return state_dir() / f"{slot['name']}.lock"


def _lock_identity(path):
    """`(st_dev, st_ino)` of the file this path actually opens — the identity
    the FILESYSTEM reports, not the one the name spells.

    The invariant that matters is "two slots must never resolve to one lock
    file"; comparing NAMES cannot decide that, and two rounds of trying it
    failed. Measured on macOS (APFS): the names 'same' and
    'SAME' open the same file, and so do the NFC and NFD spellings of 'café' —
    Apple's APFS Guide calls the default variant case-insensitive and
    "normalization-preserving, but not normalization-sensitive". Case-folding
    the name would have caught the first and walked past the second, and a
    symlink between two lock files collides on EVERY filesystem while no rule
    about names could ever see it. Asking the filesystem answers all three at
    once, and is also correct in the other direction: on a case-SENSITIVE
    volume 'same' and 'SAME' really are two locks, so admitting them is right.

    `O_CREAT` because a lock file that does not exist yet has no identity to
    compare, and this is the same file `_try_lock` creates moments later — the
    only side effect is an empty file in govrun's own state dir. NO lock is
    taken. Nothing is ever unlinked, because another process may hold the very
    lock we would be deleting.

    `os.stat` FIRST, and the open only when the file does not exist yet — never
    the other way round, and never an unconditional open/close. A POSIX record
    lock is released when the owning process CLOSES ANY DESCRIPTOR on the locked
    file, so an unconditional open/close here silently hands away a slot this
    process is holding. That is not hypothetical: it was measured
    with an out-of-process probe, and is pinned by
    `test_validation_does_not_drop_a_lock_the_same_process_holds`. Ordering
    (validate before acquire) also happens to avoid it, but ordering is a
    convention one refactor can break, and `os.stat` opens no descriptor at all.
    The open is unreachable while we hold a lock: a file we have locked exists.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)
    return st.st_dev, st.st_ino

