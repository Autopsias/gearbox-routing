"""Path-safety checks for agent_janitor's prune pass.

Extracted from agent_janitor.py (file-size ratchet) together with the
owner-only base check added after a security review.
"""
import os
import stat

UID = os.getuid()


def _resolved_inside(path: str, root: str) -> bool:
    """True if the fully-resolved path is still inside the fully-resolved
    root. realpath() walks every symlink in the chain, so this alone also
    catches an ancestor directory (not just the leaf) being a symlink out."""
    rp = os.path.realpath(path)
    rr = os.path.realpath(root)
    return rp == rr or rp.startswith(rr + os.sep)


def _is_unsafe(path: str):
    """rule_id, or None if `path` itself is safe to delete.

    ponytail: st_nlink > 1 is refused unconditionally rather than proven
    unshared with a protected path -- proving an inode has no other name
    anywhere on the filesystem is unbounded work. Revisit only if a real
    hardlinked debris file shows up in the wild.
    """
    try:
        st_ = os.lstat(path)
    except OSError:
        return "veto-missing"
    if stat.S_ISLNK(st_.st_mode):
        return "veto-symlink"
    if not stat.S_ISDIR(st_.st_mode) and st_.st_nlink > 1:
        return "veto-hardlink"
    return None


def owner_only_dir(path: str) -> bool:
    """True if `path` is a real directory we own that no other user can write.

    A scratchpad base another local user created (or can write) lets them
    swap a parent for a symlink between the last check and rmtree, steering
    a delete out of the root -- so prune scans nothing under such a base.
    """
    try:
        st_ = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(st_.st_mode) and st_.st_uid == UID
            and not st_.st_mode & 0o022)


def owner_only_slugs(base: str):
    """(entries of `base`, vetoes): nothing under a base that fails owner_only_dir."""
    if not owner_only_dir(base):
        return [], [(base, "veto-base-not-owner-only")]
    try:
        return os.listdir(base), []
    except OSError:
        return [], []
