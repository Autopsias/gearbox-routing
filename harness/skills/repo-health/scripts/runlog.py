"""Run folders and acceptance state for the exposed route (split out of exposure.py).

Each repo clone has one run parent under ~/.gearbox-state/repo-health. accept() alone writes
the acceptance marker into a run folder and appends the folder to the accepted-runs
log; the newest logged run's marker holds the open set. Malformed state raises
ValueError, which check-run turns into pending; `exposure.py repair-log` rebuilds it.

Trust model: this state protects against accidental loss and stale state, not
against an agent with write access to the run folders (~/.gearbox-state), which can also
write sec.sast directly. The run parent is keyed by the clone's resolved path, so a
re-clone or moved checkout starts with an empty open set.
"""
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
from datetime import datetime
from pathlib import Path

DOCS = ("run-metadata.json", "coverage-ledger.json", "findings.json")
ACCEPTED = "gearbox-accepted.json"
# In the run parent, written only by accept(): accepted run names, oldest first.
# The last one holds the open set; markers in folders it never names are ignored.
ACCEPTED_LOG = "accepted-runs"
LOCK = ".check-run.lock"
RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[0-9a-f]{6}-cloudflare$")
MAX_NS = 2 ** 63 - 1            # nanosecond timestamps beyond this cannot be dates
KINDS = ("lead", "unit", "confirmed", "candidate")


def bad_text(x):
    """True when any string in x (nested lists and dicts too) is not valid UTF-8
    text, such as an unpaired surrogate from a JSON escape: it cannot be printed
    or written to the scorecard, so callers refuse it before it gets there."""
    if isinstance(x, str):
        try:
            x.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(x, dict):
        return any(bad_text(k) or bad_text(v) for k, v in x.items())
    return isinstance(x, list) and any(bad_text(v) for v in x)
MAX_DOC = 5 * 1024 * 1024       # the upstream validators' own input limit


class TooDeep(ValueError):
    """JSON nested too deeply to parse: a ValueError, told apart from malformed JSON."""


def loads(text):
    """json.loads, with nesting too deep for the parser as a ValueError like any other
    bad JSON (RecursionError is not a ValueError, so callers would miss it)."""
    try:
        return json.loads(text)
    except RecursionError:
        raise TooDeep("nested too deeply") from None


def runs_parent(repo):
    """~/.gearbox-state/repo-health/<basename>-<sha8 of resolved path>: one per clone."""
    r = Path(repo).resolve()            # resolve first: '.' has an empty name
    return Path.home() / ".gearbox-state" / "repo-health" / \
        f"{r.name}-{hashlib.sha256(str(r).encode()).hexdigest()[:8]}"


def cf_output_dir(repo, now=None):
    """Create and return a NEW run folder outside the repo; never reuse one."""
    r = Path(repo).resolve()
    parent = runs_parent(r).resolve()
    if parent == r or r in parent.parents:
        raise ValueError(f"output folder {parent} would be inside the repo {r}")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    for _ in range(100):
        leaf = parent / f"{stamp}-{secrets.token_hex(3)}-cloudflare"
        try:
            os.mkdir(leaf, 0o700)       # exclusive: only a folder this call made
        except FileExistsError:
            continue
        real = leaf.resolve()           # the parent may have been swapped meanwhile
        if real != leaf or real == r or r in real.parents:
            os.rmdir(leaf)
            raise ValueError(f"output folder {leaf} resolved to {real}, refused")
        return leaf
    raise RuntimeError(f"no free run folder under {parent}")


def prior_runs(repo):
    """Runs accept() named, newest acceptance first, whose documents are unchanged."""
    parent = runs_parent(repo)
    names = list(dict.fromkeys(reversed(_accepted_names(parent))))
    return [parent / n for n in names if (parent / n).is_dir()
            and not (parent / n).is_symlink() and _accepted(parent / n)]


def read_file(path, limit=MAX_DOC, nonblank=False):
    """THE protected reader for every file a run depends on: run documents, reports,
    exposure.json, acceptance markers, the accepted-runs log and the Sentry detail file.

    Opens with O_NOFOLLOW | O_NONBLOCK (never follows a final symlink, never blocks on a
    FIFO), requires fstat to say regular file, reads at most `limit` bytes, decodes
    strict UTF-8 and, with `nonblank`, refuses whitespace-only text. A missing file
    raises FileNotFoundError; every other refusal raises ValueError naming the path.
    Callers turn either into a failed check (pending or warn), never into a pass."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as e:
        why = "not a regular file (a symlink)" if e.errno == errno.ELOOP else \
            f"cannot open ({e.strerror or e})"
        raise ValueError(f"{path}: {why}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path}: not a regular file")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as e:
        raise ValueError(f"{path}: cannot read ({e.strerror or e})") from None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > limit:
        raise ValueError(f"{path}: over {limit} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not valid UTF-8 text") from None
    if nonblank and not text.strip():
        raise ValueError(f"{path}: blank")
    return text


def read_why(e, path):
    """The reason in a read_file refusal, without the path it names."""
    return "missing" if isinstance(e, FileNotFoundError) else str(e).removeprefix(f"{path}: ")


def _log_text(parent):
    """The text of accept()'s log, '' when there is none; ValueError when read_file
    refuses it (a FIFO would block the reader while it holds the lock)."""
    try:
        return read_file(parent / ACCEPTED_LOG)
    except FileNotFoundError:
        return ""


def _accepted_names(parent):
    """accept()'s log; a malformed one raises ValueError (check-run then gives pending)."""
    names = _log_text(parent).split()
    if not all(RUN_NAME.match(n) for n in names):
        raise ValueError(f"{parent / ACCEPTED_LOG}: not a list of run folder names")
    return names


def _newest(parent):
    """The marker of the newest accepted run, validated, or None when the log names
    none (a first run). A malformed marker, a logged folder without its marker, or a
    deleted logged folder raises ValueError: fail closed, repair-log rebuilds."""
    names = _accepted_names(parent)
    if not names:
        return None
    path = parent / names[-1] / ACCEPTED
    try:
        return _read_mark(path)
    except FileNotFoundError:
        if os.path.lexists(path.parent):
            raise ValueError(f"{path}: missing from a logged run folder") from None
        raise ValueError(f"{path.parent}: the newest logged run folder is missing: "
                         "run repair-log") from None


def _read_mark(path):
    """A well-formed acceptance marker read from `path`, or ValueError."""
    mark = loads(read_file(path))
    at = mark.get("accepted_at") if isinstance(mark, dict) else None
    items = mark.get("open") if isinstance(mark, dict) else None
    if type(at) is not int or not 0 < at <= MAX_NS or not isinstance(items, list) or not all(
            isinstance(i, dict) and i.get("kind") in KINDS and isinstance(i.get("id"), str)
            and isinstance(i.get("label"), (str, type(None)))
            and isinstance(i.get("ref"), (str, type(None)))
            and (i.get("files") is None or isinstance(i["files"], list)
                 and all(isinstance(f, str) for f in i["files"])) for i in items) or bad_text(items):
        raise ValueError(f"{path}: malformed acceptance marker")
    return mark


def _marker(m):
    """_read_mark for a marker path in a real run folder (read_file refuses a symlink,
    FIFO or other non-file before reading)."""
    if m.parent.is_symlink() or not RUN_NAME.match(m.parent.name):
        raise ValueError(f"{m}: not in a run folder")
    return _read_mark(m)


def repair_log(repo):
    """Rebuild unreadable acceptance state without dropping an open item.

    Logged runs are the run-folder names in accept()'s log. Any marker that cannot
    be read (a logged run's whose folder still exists, or one the log never named,
    which could hold the newest state when the log lost lines; a symlink or FIFO is
    refused before reading): ValueError, nothing written. Otherwise the open set is the newest logged marker's (it already
    carries every older item), plus every item of a readable marker the log does
    not name; only the newest logged marker gives a ref (first seen wins). A
    new repair run, timed by the clock alone, holds it, and the log is rewritten
    as the logged runs read plus the repair run. Returns (markers read, open items kept)."""
    parent = runs_parent(repo)
    logged = list(dict.fromkeys(n for n in _log_text(parent).split() if RUN_NAME.match(n)))
    marks = []                                  # (name, marker), logged ones newest first
    for n in reversed(logged):
        if not os.path.lexists(parent / n):     # a folder deleted by hand: nothing to read
            continue
        try:
            marks.append((n, _marker(parent / n / ACCEPTED)))
        except (OSError, ValueError, UnicodeError) as e:
            raise ValueError(f"logged run {n} has an unreadable marker ({e}): "
                             "nothing written") from None
    read, unlogged = [n for n, _ in marks], []
    for m in sorted(parent.glob(f"*/{ACCEPTED}")):
        if m.parent.name in logged:
            continue
        try:
            unlogged.append(_marker(m))
        except (OSError, ValueError, UnicodeError) as e:
            raise ValueError(
                f"unreadable marker in run folder {m.parent} ({e}): nothing written. That "
                "folder is not in the accepted-runs log, so check-run never read state from it: "
                "deleting that unlogged run folder is safe, unless you know the log lost "
                "lines; then run repair-log again") from None
    # the newest logged marker already carries every older logged item, with its ref
    items = {(i["kind"], i["id"]): i for i in (marks[0][1]["open"] if marks else [])}
    for mark in unlogged:                       # a marker the log never named adds, no ref
        for i in mark["open"]:
            items.setdefault((i["kind"], i["id"]), {**i, "ref": None})
    # a lead never stays open beside a confirmed finding of the same id (cf_check._carry)
    items = {(k, i): v for (k, i), v in items.items() if k != "lead" or ("confirmed", i) not in items}
    run = cf_output_dir(repo)
    _write_new(run / ACCEPTED, json.dumps({"status": "repair", "digest": None,
                                           "accepted_at": time.time_ns(),
                                           "open": list(items.values())}))
    _write_new(parent / ACCEPTED_LOG, "".join(f"{n}\n" for n in reversed(read)) + run.name + "\n")
    return len(marks) + len(unlogged), len(items)


def _write_new(path, text):
    """Write `path` through a fresh temp file beside it and os.replace: a symlink
    or FIFO planted at `path` is replaced, never followed or opened."""
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _digest(run):
    h = hashlib.sha256()
    for name in DOCS:
        try:
            h.update(read_file(run / name).encode())    # never blocks on a FIFO or follows a link
        except (OSError, ValueError):
            return None
    return h.hexdigest()


def accept(run, status, open_items=(), digest=None):
    """Mark a run that check-run accepted and recorded: only these are prior runs.

    The marker also carries every still-open item (confirmed findings, leads,
    deferred units), including items this run dropped, so obligations inherit
    across any number of runs and leave only on positive evidence (cf_check._carry).
    `digest` is check_run's hash of the bytes it validated: documents that no longer
    read back to it (or at all) raise ValueError and nothing is written."""
    run = Path(run).resolve()           # a link's parent is not the run parent: log there, never
    if not RUN_NAME.match(run.name):
        raise ValueError(f"{run}: not a run folder name")
    now = _digest(run)
    if now is None or digest not in (None, now):
        raise ValueError(f"{run}: run documents unreadable or changed since check-run "
                         "validated them: not accepted")
    prev = _newest(run.parent)
    # after the last acceptance even if the clock went backwards; capped, never raised:
    # order comes from the log, so a previous time in the future cannot block this
    at = min(max(time.time_ns(), prev["accepted_at"] + 1 if prev else 0), MAX_NS)
    _write_new(run / ACCEPTED, json.dumps({"status": status, "digest": digest or now,
                                           "accepted_at": at, "open": list(open_items)}))
    fd = _append_fd(run.parent / ACCEPTED_LOG)         # trusted order: only accept() writes
    try:
        os.write(fd, (run.name + "\n").encode())
    finally:
        os.close(fd)


def _append_fd(path):
    """An fd appending to `path`, created 0600: never follows a final symlink, never
    blocks on a FIFO, and only a regular file. Any refusal raises ValueError."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                     | os.O_NONBLOCK, 0o600)
    except OSError as e:
        raise ValueError(f"{path}: cannot open ({e.strerror or e})") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"{path}: not a regular file")
    return fd


@contextlib.contextmanager
def repo_lock(repo):
    """One check-run at a time per repo: the open set is read, computed, recorded
    and accepted as one step, so an overlapping run cannot erase inherited items."""
    parent = runs_parent(repo)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = _append_fd(parent / LOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)      # released when the fd closes
        yield
    finally:
        os.close(fd)


def open_set(repo):
    """({(kind, id): {"label", "ref", "files"}}, date) from the marker of the newest accepted run.

    Read even when that run's documents were edited later, and including the run
    being checked when it was recorded before. `ref` is the commit the item was
    last reported at; `files` its own trace files then (None in an older marker).
    Malformed or missing state, including a deleted newest run folder, raises
    ValueError: the caller gives pending, never an empty open set."""
    parent = runs_parent(repo)
    # a lost or blanked log must not empty the open set while markers still hold it
    if not _accepted_names(parent) and any(parent.glob(f"*/{ACCEPTED}")):
        raise ValueError("accepted markers exist but the log is empty: run repair-log")
    best = _newest(parent)
    if best is None:
        return {}, None
    items = {(i["kind"], i["id"]): {"label": i.get("label") or i["id"], "ref": i.get("ref"),
                                    "files": i.get("files")} for i in best["open"]}
    return items, datetime.fromtimestamp(best["accepted_at"] / 1e9).strftime("%Y-%m-%d")


def _accepted(run):
    try:
        mark = loads(read_file(run / ACCEPTED))
    except (OSError, ValueError):
        return False
    return isinstance(mark, dict) and mark.get("digest") not in (None, "") \
        and mark.get("digest") == _digest(run)
