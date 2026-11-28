"""Read JSON another process wrote: a wrong shape reads as absent, never raises.

Two rules plan reviews kept re-finding (memory: guard-the-parse-not-the-type):

* Text that parses is not yet a record. ``[]``, ``"x"``, ``1`` and ``null`` are
  all valid JSON, and ``.get`` on any of them raises. Check the shape.
* An empty value is a value. ``""``, ``[]`` and ``{}`` are present: test
  ``is None`` (or ``key in d``), never truthiness, when absent means something.

Every getter returns ``default`` when ``d`` is not a dict, the key is missing, or
the value has the wrong type. ``bool`` is never an ``int`` here. A caller that
must tell "missing" from "wrong type" (to refuse the second) checks ``key in d``.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc          # not datetime.UTC: the pre-push hook may run on Python 3.9


def load_dict(text):
    """The parsed object when ``text`` is a JSON object, else None (bad JSON,
    truncated text, bytes that are not UTF-8, nested too deep, or any non-object value)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError, RecursionError):  # UnicodeDecodeError is a ValueError; a deeply nested row raises RecursionError on 3.11/3.12
        return None
    return data if isinstance(data, dict) else None


def load_rows(text):
    """Read NDJSON: (the object rows, the count of skipped rows). A row that is bad
    JSON, not an object or not UTF-8 is skipped and counted; blank lines are neither.
    ``text`` may be str or bytes. A row is an object, not yet a typed one: read its
    fields with the get_* getters."""
    rows, skipped = [], 0
    for line in text.split(b"\n" if isinstance(text, bytes) else "\n"):
        if not line.strip():
            continue
        row = load_dict(line)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped


def as_dict(value):
    return value if isinstance(value, dict) else None


def _typed(d, key, types, default):
    v = d.get(key) if isinstance(d, dict) else None
    if isinstance(v, bool) and bool not in types:
        return default
    return v if isinstance(v, types) else default


def get_str(d, key, default=None):
    return _typed(d, key, (str,), default)


def get_int(d, key, default=None):
    return _typed(d, key, (int,), default)


def get_bool(d, key, default=None):
    return _typed(d, key, (bool,), default)


def get_list(d, key, default=None):
    return _typed(d, key, (list,), default)


def get_dict(d, key, default=None):
    return _typed(d, key, (dict,), default)


def parse_iso_aware(value):
    """A timezone-aware datetime from an ISO-8601 string, or None. A naive stamp
    reads as UTC, so naive and aware stamps compare without a TypeError."""
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def resolve_inside(base, name):
    """The path `base/name`, or ValueError when `name` would leave `base`.

    Refuses, never repairs: an empty name, an absolute path, a `..` part, and a
    symlink that points outside `base` all raise. The returned path is
    `Path(base) / name` as given, so a valid name resolves exactly as before.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"unsafe name: {name!r}")
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts or "\x00" in name:
        raise ValueError(f"unsafe name: {name!r}")
    root = Path(base).resolve()
    target = (Path(base) / name).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError(f"name leaves its folder: {name!r}")
    return Path(base) / name
