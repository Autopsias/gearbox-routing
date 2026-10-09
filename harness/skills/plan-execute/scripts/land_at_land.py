"""LND-02 — the gates a repo flags ``at_land: true`` (finish-contract.md
"Registry flag at_land"; plan-isolation-contract.md §4.4 R2).

The re-gate runs the plan's union of verify gates PLUS every gate the CANDIDATE
merged tree's registry flags ``at_land``, so a repo's CI-equivalent check runs
on the exact tree before it is pushed. Read from ``<land_path>``, not the plan
directory, so a gate that reached origin while the plan ran is still found.

Discovery fails CLOSED. It parses both files itself and never goes through
``shipping._load_registry``, which swallows ``JSONDecodeError`` and ``OSError``:
an unreadable registry would silently drop the one gate it exists to run.
"""

import json
import os
from pathlib import Path

import shipping as shp
import verify_pass as vp
from worktree import git

REGISTRY = Path(".claude") / "eval-gates.json"
DEFAULT = "eval-gates.default.json"


class Unreadable(Exception):
    """A registry file exists but cannot be trusted. ``str(e)`` names the file
    and the error; the caller parks ``at-land-registry-unreadable``."""


def _read(path):
    try:
        return _parse(Path(path).read_text(), path)
    except (OSError, ValueError) as e:              # ValueError covers UTF-8 errors
        raise Unreadable(f"{path}: {type(e).__name__}: {e}") from e


def _parse(text, path):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise Unreadable(f"{path}: {type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise Unreadable(f"{path}: not a JSON object (got {type(data).__name__})")
    for gid, g in data.items():
        if isinstance(g, dict) and not isinstance(g.get("at_land", False), bool):
            raise Unreadable(f"{path}: gate {gid!r} has a non-boolean at_land "
                             f"({g['at_land']!r})")
    return data


def _present(path):
    """``lstat`` the path: only FileNotFoundError means absent. ``os.path.lexists``
    also answers False on a PermissionError, which would skip a present registry."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):   # `.claude` a plain file: absent too
        return False
    except OSError as e:
        raise Unreadable(f"{path}: {type(e).__name__}: {e}") from e
    return True


def _bundled(land_path):
    """The bundled default AS THE CANDIDATE HAS IT when the land repo ships the
    skill itself (this harness): the runtime copy is the PRE-merge one, so a
    change to it on the plan branch or on origin would go unread. Any other repo
    has no such file and gets the runtime copy, the one its gates resolve from."""
    runtime = shp._bundled_default(DEFAULT)
    candidate = Path(land_path).joinpath(*runtime.parts[-4:])  # skills/<skill>/references/<file>
    return candidate if _present(candidate) else runtime


def registry(land_path):
    """The bundled default merged under the candidate tree's project file
    (project wins per gate id). An ABSENT file contributes nothing; a present one
    that cannot be read or parsed raises ``Unreadable``. ``_present`` so a dangling
    symlink counts as present-and-unreadable, not as absent."""
    merged = {}
    for p in (_bundled(land_path), Path(land_path) / REGISTRY):
        if _present(p):
            merged.update(_read(p))
    return merged


def _rooted(plan_tree, cwd):
    # Rooted in the PLAN tree on purpose: `land_gate.gate_cwd` then re-roots it
    # into the land worktree, and parks when it cannot (an absolute or `..` cwd
    # outside the tree) — the same fail-closed mapping every other gate gets.
    if cwd is None or cwd == ".":
        return str(plan_tree)
    p = Path(cwd)
    return str(p if p.is_absolute() else Path(plan_tree) / p)


def at_land_gates(land_path, plan_tree):
    """[(gate_id, descriptor)] for every ``at_land: true`` gate, sorted by id.
    Each is resolved from the same candidate-tree entry discovery read."""
    reg = registry(land_path)
    return [(gid, shp.gate_descriptor(gid, g, lambda c=_cwd(gid, g): _rooted(plan_tree, c)))
            for gid, g in sorted(reg.items())
            if isinstance(g, dict) and g.get("at_land") is True]


def _cwd(gid, g):
    """A gate's ``cwd``: absent/null (the tree root) or a non-empty string. Anything
    else is a malformed registry, refused at discovery rather than read as the root."""
    cwd = g.get("cwd")
    if cwd is not None and not (isinstance(cwd, str) and cwd):
        raise Unreadable(f"{REGISTRY}: gate {gid!r} has an invalid cwd ({cwd!r})")
    return cwd


def ci_warning(land_path, flagged):
    """One plain line when the repo has CI workflows and no ``at_land`` gate that
    runs a check. A review gate does not count: the shared default flags
    ``llm-review-high`` at_land for every repo, and it runs no tests."""
    wf = Path(land_path) / ".github" / "workflows"
    reg = registry(land_path)
    checks = [g for g in flagged if not vp.is_review_gate({"kind": "argv", **reg.get(g, {})})]
    if checks or not any(p for ext in ("*.yml", "*.yaml") for p in wf.glob(ext)):
        return None
    return ("WARNING: CI's check does not run before the push. This repo has CI workflows "
            "in .github/workflows/, but no gate in .claude/eval-gates.json is flagged "
            "\"at_land\": true.")


def _flagged(g):
    return isinstance(g, dict) and g.get("at_land") is True


def _origin_text(land_path, rev, rel):
    """``rel`` as origin's tree ``rev`` has it, or None when that tree has no such file."""
    rc, out, err = git(["ls-tree", "--full-tree", "--name-only", rev, "--", rel], land_path)
    if rc == 0 and not out:
        return None
    if rc == 0:
        rc, out, err = git(["show", f"{rev}:{rel}"], land_path, strip=False)
    if rc != 0:
        raise Unreadable(f"{rev}:{rel}: git: {err}")
    return out


def _origin_registry(land_path, rev):
    """``registry`` as origin's tree ``rev`` (the land's expected base) has it."""
    runtime = shp._bundled_default(DEFAULT)
    merged = {}
    for rel in (Path(*runtime.parts[-4:]).as_posix(), REGISTRY.as_posix()):
        text = _origin_text(land_path, rev, rel)
        if text is not None:
            merged.update(_parse(text, f"{rev}:{rel}"))
        elif rel != REGISTRY.as_posix() and os.path.lexists(runtime):
            merged.update(_read(runtime))
    return merged


_CHANGE = {"added": "origin has no such gate; the re-gate ran the plan's copy.",
           "changed": "the re-gate ran the plan's copy, not origin's.",
           "removed": "origin runs it before the push; this re-gate did not."}


def plan_changes(land_path, rev):
    """One plain line per ``at_land`` gate the PLAN side added, changed or removed.
    The candidate is origin's ``rev`` plus this plan, so any entry that differs
    between the two registries is the plan's doing — and the plan then wrote the
    definition of the gate that grades it. A human reads it before the ack."""
    cand = registry(land_path)
    try:
        orig = _origin_registry(land_path, rev)
    except (Unreadable, ValueError) as e:
        return [f"WARNING: could not read origin's gate registry, so cannot tell whether "
                f"this plan changed an at_land gate: {e}"]
    lines = []
    for gid in sorted(set(cand) | set(orig)):
        c, o = cand.get(gid), orig.get(gid)
        if c != o and (_flagged(c) or _flagged(o)):
            change = "added" if not _flagged(o) else "removed" if not _flagged(c) else "changed"
            lines.append(f"NOTE: this plan {change} the at_land gate {gid!r} in the gate "
                         f"registry; {_CHANGE[change]}")
    return lines


def brief_note(land_path, rev, flagged):
    """The land review brief's at_land lines (CI warning, plan changes), or None."""
    return "\n".join(filter(None, [ci_warning(land_path, flagged),
                                   *plan_changes(land_path, rev)])) or None
