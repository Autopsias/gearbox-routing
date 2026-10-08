#!/usr/bin/env python3
"""Is this directory on a filesystem where an advisory lock actually holds?

Split out of run_state_io.py, which had grown 55 lines past its size baseline.
Self-contained by construction: it parses `mount` output and /proc/mounts and
calls nothing else in the package — which is exactly why it was the clean seam.
"""
from pathlib import Path




# --------------------------------------------------------------------------
# Filesystem lock-semantics guard (P5)
# --------------------------------------------------------------------------
# Pidfile / flock advisory locks are reliable only on a local POSIX filesystem.
# On networked or cloud-sync filesystems two runs can each believe they hold the
# lock and race PLAN.html into corruption. We detect the common cases and refuse
# by default (override: --unsafe-lock).

# Path-prefix heuristics: sync-folder roots under $HOME (cheap, no external deps).
_SYNC_FS_PREFIXES = {
    "iCloud Drive": ["Library/Mobile Documents"],
    "CloudStorage (Google Drive / OneDrive / Dropbox via Finder)": ["Library/CloudStorage"],
    "Dropbox": ["Dropbox"],
    "Google Drive": ["Google Drive"],
    "OneDrive": ["OneDrive"],
}

# fstype values that indicate a networked filesystem where POSIX locks misbehave.
_NETWORK_FSTYPES = {"nfs", "smbfs", "cifs", "afpfs", "fuse", "fuseblk", "webdav", "ftp"}


def check_lock_fs(plan_dir):
    """Return a human-readable FS-class name if ``plan_dir`` resolves onto a
    networked/sync filesystem where the lock is unreliable, else ``None``.

    Detection is best-effort: any error returns ``None`` (never block on a
    detection failure — the caller decides whether to refuse).
    """
    try:
        resolved = Path(plan_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        return None

    # 1. Path-prefix match against known sync-folder roots under $HOME.
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = None
    if home is not None and resolved.is_relative_to(home):
        rel = str(resolved.relative_to(home))
        for fs_class, prefixes in _SYNC_FS_PREFIXES.items():
            for pre in prefixes:
                if rel == pre or rel.startswith(pre + "/"):
                    return fs_class

    # 2. Mount-type probe (best-effort, guarded).
    fstype = _probe_fstype(resolved)
    if fstype and fstype.lower() in _NETWORK_FSTYPES:
        return f"network filesystem ({fstype})"
    return None


def _probe_fstype(path):
    """Best-effort fstype for ``path``'s mount point. ``None`` on any failure.

    NB: BSD/macOS ``stat -f %T`` reports the *file* type (ls -F style), not the
    fstype, so we parse ``mount`` output (macOS) / ``/proc/mounts`` (Linux) and
    match the longest mount point that is a prefix of ``path``.
    """
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, timeout=5, check=False
            )
            if out.returncode != 0:
                return None
            mounts = _parse_macos_mount(out.stdout)
        elif sys.platform.startswith("linux"):
            mounts = _parse_proc_mounts(Path("/proc/mounts").read_text())
        else:
            return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return _fstype_for_path(str(path), mounts)


def _parse_macos_mount(text):
    """macOS ``mount`` lines: ``<dev> on <mountpoint> (<fstype>, <opts>)``."""
    import re

    mounts = []
    for line in text.splitlines():
        m = re.match(r"^.*? on (.+?) \(([^,)]+)", line)
        if m:
            mounts.append((m.group(1), m.group(2).strip()))
    return mounts


def _parse_proc_mounts(text):
    """Linux ``/proc/mounts`` lines: ``<dev> <mountpoint> <fstype> <opts> 0 0``.
    Mount points escape spaces as ``\\040``."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mountpoint = parts[1].replace("\\040", " ")
            mounts.append((mountpoint, parts[2]))
    return mounts


def _fstype_for_path(path, mounts):
    """Return the fstype of the longest mount point that is a prefix of ``path``."""
    best_fstype, best_len = None, -1
    for mountpoint, fstype in mounts:
        if not mountpoint:
            continue
        norm = mountpoint.rstrip("/")
        matches = path == mountpoint or norm == "" or path.startswith(norm + "/")
        if matches and len(mountpoint) > best_len:
            best_fstype, best_len = fstype, len(mountpoint)
    return best_fstype
