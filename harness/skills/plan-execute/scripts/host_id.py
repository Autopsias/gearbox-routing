"""Which machine wrote a lock — stable across macOS hostname flips.

macOS `gethostname()` flips between `Mac.lan` and `Users-MacBook-Pro.local`,
so a raw string compare treated a dead lock as a foreign host's and never
reclaimed it. Compare the part before the first dot, case-folded, OR the stable
`uuid.getnode()` recorded in new locks (old locks without it use the name only).
"""

import socket
import uuid


def _host_key(host):
    return str(host or "").lower().split(".")[0]


def host_fields():
    """`host` + `machine_id` fields for a new lock record."""
    return {"host": socket.gethostname(), "machine_id": uuid.getnode()}


def same_host(info):
    """True when the lock record names THIS machine (or names no host)."""
    info = info or {}
    mid = info.get("machine_id")
    if mid is not None and mid == uuid.getnode():
        return True
    host = info.get("host")
    return not host or _host_key(host) == _host_key(socket.gethostname())
