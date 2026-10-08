"""Path confinement for finish (FIN-01 / FIN-02): every path the record,
``.gitignore`` and checkout steps read, stat or write goes through ``lstat_inside``.
Split from ``finish_record.py`` to keep each file under 500 lines."""

import os
import stat
from pathlib import Path

class Park(Exception):
    def __init__(self, reason, detail, paths=()):
        super().__init__(detail)
        self.reason, self.detail, self.paths = reason, detail, list(paths)



class Linked(Park):
    """A path that reaches through a symlink, or whose leaf is not a plain file.
    ``at`` is the offending component, ``why`` the cause; both read in a brief."""

    def __init__(self, at, why):
        self.at, self.why = str(at), why
        super().__init__("outside-record", why.format(at=self.at), [self.at])

    def under(self, prefix):
        """The same refusal with ``at`` made repo-relative."""
        return Linked(f"{prefix}/{self.at}", self.why)


SYMLINK = "symlink at {at}"
NOT_A_DIR = "{at} is a file where a directory is needed"
A_DIR = "{at} is a directory where a file is needed"
NOT_REGULAR = "{at} is not a regular file"


def lstat_inside(base, path):
    """``os.lstat`` of ``base/path`` once no component from ``base`` down is a
    symlink; None when absent. Raises ``Linked`` on a symlink anywhere on the path,
    a non-directory above the leaf, or a leaf that is neither a regular file nor a
    directory. ``base`` itself is trusted (a tree root may sit under a linked /tmp).
    Every path the record, ``.gitignore`` and checkout steps read, stat or write
    goes through here first."""
    parts = Path(path).parts
    if not parts or Path(path).is_absolute() or ".." in parts:
        raise Linked(path, "'..' or absolute path: {at}")
    p, st = Path(base), None
    for i, part in enumerate(parts):
        p = p / part
        try:
            st = os.lstat(p)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if stat.S_ISDIR(st.st_mode):
            continue
        at, leaf = Path(*parts[:i + 1]).as_posix(), i == len(parts) - 1
        if stat.S_ISLNK(st.st_mode):
            raise Linked(at, SYMLINK)
        if not leaf:
            raise Linked(at, NOT_A_DIR)
        if not stat.S_ISREG(st.st_mode):
            raise Linked(at, NOT_REGULAR)
    return st


def read_inside(base, path):
    """Regular file ``base/path``'s bytes (None when absent), never through a link."""
    st = lstat_inside(base, path)
    if st is None:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise Linked(path, A_DIR)                  # lstat_inside allows only a dir here
    # ponytail: O_NOFOLLOW guards the leaf only; a parent swapped for a link after
    # lstat_inside still races, as any check-then-use on a live tree does.
    with os.fdopen(os.open(Path(base) / path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
        return fh.read()


def _exe(base, path):
    """The one mode bit git records, from ``lstat`` of an accepted regular file."""
    return bool(lstat_inside(base, path).st_mode & 0o100)


def refuse_special(plan_dir, skip):
    """Park ``record-special-file`` when the plan folder holds a FIFO, socket or
    device (judged with ``lstat``: a file, a link and a directory are fine). A
    runtime-local path (``skip(rel)``) is not part of the record and is ignored."""
    import stat  # noqa: PLC0415
    bad = []
    for f in sorted(Path(plan_dir).rglob("*")):
        mode = f.lstat().st_mode
        inner = f.relative_to(plan_dir).as_posix()
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode) or stat.S_ISDIR(mode)) \
                and not skip(inner):
            bad.append(inner)
    if bad:
        raise Park("record-special-file", "the plan folder holds a pipe, socket or device "
                   "file; finish records only files, links and folders. Remove it, then "
                   "re-run: " + ", ".join(bad), bad)
