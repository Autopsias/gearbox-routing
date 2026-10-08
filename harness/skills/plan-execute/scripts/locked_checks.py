"""Locked checks — the worker cannot edit the checks the author wrote (schema v8).

Authority: ``../references/route-at-dispatch-contract.md`` (v1), section 5.

``begin`` fingerprints every path in a session's ``verify.locked`` once per
escalation generation, plus every test-configuration file under the folder the
worker writes to, plus the package ``__init__`` files pytest runs before a locked
check. ``verify-begin`` re-hashes before any gate runs; a changed, deleted or (for
test configuration and package initializers) added file fails the attempt as
``LOCKED_CHECK_EDITED``. Kept out of run.py and verify.py, which are both at their
file-size pins: they only call ``begin``, ``refusal`` and ``gate_env``.

Stated limits: the snapshot lives in the plan folder, which a worker can also
write, so it guards against a worker editing the checks, not against one forging
or deleting the record (a deleted snapshot fails the attempt, and the rework's
``begin`` then takes a fresh one); a skill-kind gate runs in the orchestrator, so ``gate_env`` reaches
argv gates only.
"""
import hashlib
import os
import shutil
import tempfile
from importlib import machinery
from pathlib import Path

import escalation_state as est
import manifest_io as mio
import plan_scope as pscope
import route_at_dispatch as rad
import run_state_io as rsi
import ship_state_io as ssio
import worktree as wt
from rework import _gate_failed
from verify_paths import LOCKED_CHECK_EDITED, _save_state

LOCKED_GATE = "locked-checks"  # the synthetic gate name the refusal is recorded under

# Contract §5's protected set: every file pytest reads as configuration or as a
# plugin, and every file Python runs at start-up. A class, matched by name at any
# depth; the start-up modules are matched by IMPORT name, in every form the import
# system loads (source, sourceless bytecode, extension module, package folder).
_CONFIG = frozenset({"pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini",
                     "pyproject.toml", "tox.ini", "setup.cfg", "conftest.py", "pyvenv.cfg"})
_STARTUP = ("sitecustomize", "usercustomize")
_FORMS = (*machinery.SOURCE_SUFFIXES, *machinery.BYTECODE_SUFFIXES, *machinery.EXTENSION_SUFFIXES)
_STARTUP_FILES = frozenset(m + s for m in _STARTUP for s in _FORMS)
_INIT_FILES = tuple("__init__" + s for s in _FORMS)  # an extension one is imported first
# The harness's own worktrees: other plans' write roots, which change under a
# shared checkout for reasons that are not this worker's. pytest never recurses
# into a dot folder, so skipping them opens nothing a gate collects.
_OTHER_WORKTREES = ".plan-worktrees/"


class LockRefused(Exception):
    pass


def snapshot_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.locked.json"


def locked_paths(manifest, session_id):
    """The session's ``verify.locked`` list; empty below schema v8."""
    if not rad.is_v8(manifest):
        return []
    s = mio.session_by_id(manifest).get(session_id) or {}
    return list((s.get("verify") or {}).get("locked") or [])


def protected(rel):
    """Is this repo-relative path a test-configuration file? Case-folded, because
    macOS resolves ``Conftest.py`` when pytest asks for ``conftest.py``."""
    *dirs, name = rel.lower().split("/")
    return (name in _CONFIG or name.endswith(".pth") or name in _STARTUP_FILES
            or any(d in _STARTUP for d in dirs)
            or (name == "entry_points.txt" and bool(dirs)
                and dirs[-1].endswith((".dist-info", ".egg-info"))))


def _sha(path):
    """sha256 of a regular file, None when absent. A symlink is fingerprinted by its
    link text AND its target's bytes ('missing' when dangling or unreadable), so a
    retarget and an edit to a target outside the repo both show."""
    p = Path(path)
    try:
        h = hashlib.sha256(p.read_bytes()).hexdigest()  # read_bytes follows the link
    except OSError:  # absent, a folder, unreadable, a link loop: not the file begin hashed
        h = None
    return f"symlink:{os.readlink(p)}:{h or 'missing'}" if p.is_symlink() else h


def package_inits(root, paths):
    """``{path: sha256 or None}`` for every import form of ``__init__`` in each
    locked path's folder and every folder above it, up to the write root. pytest
    runs them before the locked check: at collection when the chain is unbroken,
    at ``Package`` setup across a gap. Absent ones are recorded, so adding one shows.
    Bounded on purpose: every other ``__init__.py`` is code under test (contract §5)."""
    rels = {(d / f).as_posix() for p in paths
            for d in (Path(p).parent, *Path(p).parent.parents) for f in _INIT_FILES}
    return {r: _sha(Path(root) / r) for r in sorted(rels)}


def _walk(root, rel, seen):
    """Protected files inside a folder git lists but never enters: a nested
    repository or a symlinked folder. Follows links, inside the repo or out;
    ``seen`` (real paths already walked, seeded with the root) stops every loop."""
    found = {}
    for d, dirs, files in os.walk(Path(root) / rel, followlinks=True):
        real = os.path.realpath(d)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            r = (Path(d) / f).relative_to(root).as_posix()
            if protected(r):
                found[r] = _sha(Path(root) / r)
    return found


def config_files(root):
    """``{path: sha256}`` for every test-configuration file under ``root`` —
    tracked, untracked AND ignored. ``ls-files --others`` without an exclude flag
    lists ignored files too; ``git diff`` alone would miss both."""
    rc, out, err = wt.git(["ls-files", "-z", "--cached", "--others"], root, strip=False)
    if rc != 0:
        raise LockRefused(f"cannot list the files under {root}: {err.strip() or rc}")
    found, seen = {}, {os.path.realpath(root)}
    for rel in filter(None, out.split("\0")):
        if rel.endswith("/") or (Path(root, rel).is_symlink() and Path(root, rel).is_dir()):
            if not rel.startswith(_OTHER_WORKTREES):
                found.update(_walk(root, rel, seen))
        elif protected(rel):
            found[rel] = _sha(Path(root) / rel)
    return found


def _take(session_id, root, paths, generation):
    """The snapshot record, or LockRefused naming what is missing. Writes nothing."""
    if not root or not Path(root).is_dir():
        raise LockRefused(f"{session_id}: no folder to resolve verify.locked in ({root!r})")
    root = Path(root)
    locked, missing = {}, []
    for p in paths:
        f = root / p
        if f.is_symlink() or not f.is_file():
            missing.append(p)
        else:
            locked[p] = _sha(f)
    if missing:
        raise LockRefused(f"{session_id}: locked check(s) {', '.join(missing)} are not regular "
                          f"files under {root}. A locked path must exist at dispatch")
    rc, head, _ = wt.git(["rev-parse", "HEAD"], root)
    return {"session": session_id, "generation": generation, "repo_root": str(root),
            "begin_sha": head if rc == 0 else None, "locked": locked,
            "package_init": package_inits(root, paths), "test_config": config_files(root)}


def _load(plan_dir, session_id):
    return ssio.read_json_with_bak(snapshot_path(plan_dir, session_id))


def snapshot(plan_dir, session_id, repo_root, paths, generation=None):
    """Hash ``paths`` (and the test configuration) under ``repo_root`` into
    ``_verify_state/<sid>.locked.json``. Raises LockRefused when a path is missing."""
    if generation is None:
        generation = est.session_state(plan_dir, session_id)["generation"]
    rec = _take(session_id, repo_root, paths, generation)
    ssio.durable_write_json(snapshot_path(plan_dir, session_id), rec)
    rsi.log_event(plan_dir, "locked_checks_snapshot", session_ids=[session_id],
                  generation=generation, begin_sha=rec["begin_sha"], locked=sorted(rec["locked"]))
    return rec


def begin(plan_dir, manifest, sessions):
    """``begin``'s step, under the plan lock and before any session goes DOING.

    One snapshot per escalation generation: a rework keeps its generation's first
    snapshot, so a worker cannot launder an edit by being re-dispatched; a
    redispatch or amend bumps the generation and takes a new one, so a check the
    operator corrected does not fail forever. Every session is checked before any
    snapshot is written, so one missing path refuses the batch with nothing half
    recorded."""
    todo, refusals = {}, []
    for sid in sessions:
        paths = locked_paths(manifest, sid)
        if not paths:
            continue
        gen = est.session_state(plan_dir, sid)["generation"]
        if (_load(plan_dir, sid) or {}).get("generation") == gen:
            continue
        root = pscope.plan_cwd(plan_dir, sid) or wt.repo_root(plan_dir)
        try:
            todo[sid] = (root, paths, gen, _take(sid, root, paths, gen))
        except LockRefused as e:
            refusals.append(str(e))
    if refusals:
        raise SystemExit("refusing to begin (locked checks):\n  " + "\n  ".join(refusals))
    for sid, (root, paths, gen, _) in todo.items():
        snapshot(plan_dir, sid, root, paths, gen)


def _moved(before, now, kind):
    return [(p, "added" if before.get(p) is None else "deleted" if now.get(p) is None
             else "changed", kind)
            for p in sorted(set(before) | set(now)) if before.get(p) != now.get(p)]


def check(plan_dir, session_id, repo_root=None):
    """``[(path, verb, kind)]`` for every locked check changed or deleted since the
    snapshot, and every package initializer or test-configuration file added,
    deleted or changed. Empty when nothing moved. ``repo_root`` defaults to the
    folder the snapshot recorded."""
    rec = _load(plan_dir, session_id)
    if not rec:
        return [(str(snapshot_path(plan_dir, session_id)), "deleted", "lock snapshot begin took")]
    root = Path(repo_root or rec["repo_root"])
    out = []
    for p, h in sorted(rec["locked"].items()):
        now = _sha(root / p)
        if now != h:
            out.append((p, "deleted" if now is None else "changed", "locked check"))
    inits = rec.get("package_init") or {}
    out += _moved(inits, {p: _sha(root / p) for p in inits},
                  "package initializer of a locked check")
    try:
        now = config_files(root)
    except LockRefused as e:
        return out + [(str(root), "changed", f"write folder ({e})")]
    return out + _moved(rec.get("test_config") or {}, now, "test-configuration file")


def _line(path, verb, kind):
    fix = "Remove it." if verb == "added" else "Restore it."
    return (f"{LOCKED_CHECK_EDITED} (LockedCheckError): You {verb} {path}, a {kind}. {fix} "
            "If the check itself is wrong, return BLOCKED with a decision brief.")


def refusal(plan_dir, manifest, session_id, state, dry_run=False):
    """verify-begin's step, before any gate: None when every locked check is intact,
    else the failed attempt. It is charged like a failed gate (rework or halt, and a
    stuck-protocol failure with its own signature) under the gate name
    ``locked-checks``, whose status is ``LOCKED_CHECK_EDITED``, never ``failed``."""
    if dry_run or not locked_paths(manifest, session_id):
        return None
    edited = check(plan_dir, session_id)
    if not edited:
        return None
    out = _gate_failed(plan_dir, session_id, state, LOCKED_GATE,
                       "\n".join(_line(*e) for e in edited))
    state["gate_status"][LOCKED_GATE] = LOCKED_CHECK_EDITED
    _save_state(plan_dir, session_id, state)
    rsi.log_event(plan_dir, "locked_check_edited", session_ids=[session_id],
                  paths=[p for p, _, _ in edited])
    return {**out, "refusal": LOCKED_CHECK_EDITED, "edited": [list(e) for e in edited]}


def gate_env(plan_dir, session_id):
    """``PYTHONPYCACHEPREFIX`` for a locked session's argv gate: a fresh empty folder
    per run, so no bytecode cache planted in the tree (or left by an earlier attempt)
    replaces an unchanged ``conftest.py`` or locked test. Empty for any other session."""
    try:
        if not locked_paths(mio.load_manifest(plan_dir), session_id):
            return {}
    except (mio.ManifestError, OSError):
        return {}
    key = hashlib.sha256(str(Path(plan_dir).resolve()).encode()).hexdigest()[:12]
    parent = Path(tempfile.gettempdir()) / f"plan-execute-pycache-{key}-{session_id}"
    shutil.rmtree(parent, ignore_errors=True)  # one live folder per session, never a pile
    parent.mkdir(parents=True, exist_ok=True)
    return {"PYTHONPYCACHEPREFIX": tempfile.mkdtemp(dir=parent)}
