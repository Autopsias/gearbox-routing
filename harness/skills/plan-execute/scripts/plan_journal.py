#!/usr/bin/env python3
"""Journalled multi-file transaction: either every file lands, or none does.

Split out of plan_mutate.py, which had grown 52 lines past its size baseline.
The section needed only MutationError and _now from its old home, and both
belong here: MutationError IS the transaction's refusal, and the timestamp is
what the journal records.

plan_mutate re-exports both, so `pm.MutationError` still names the same class.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

JOURNAL_DIR = "_mutation"
JOURNAL_NAME = "journal.json"


class MutationError(Exception):
    """Refusal: the requested mutation is invalid or unsafe. Nothing was written."""


def _now():
    return datetime.now(timezone.utc).isoformat()




# --------------------------------------------------------------------------
# Journalled multi-file transaction
# --------------------------------------------------------------------------
def _fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fsync(path, data):
    path = Path(path)
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _crash_point(label, n):
    """Test-only crash injection. `PLAN_MUTATE_CRASH=<label>` kills the process
    at that point; for `rename` the value may be `rename:<n>` to die after the
    n-th rename. Real process death (os._exit), so the test exercises the same
    recovery path a power cut would."""
    want = os.environ.get("PLAN_MUTATE_CRASH")
    if not want:
        return
    tag, _, arg = want.partition(":")
    if tag != label:
        return
    if arg and int(arg) != n:
        return
    sys.stderr.write(f"[crash-injection] dying at {label}:{n}\n")
    sys.stderr.flush()
    os._exit(70)


def commit(plan_dir, targets, meta):
    """Journal + apply a multi-file generation.

    `targets` maps plan-relative path -> complete new bytes. Returns the txn id.
    """
    plan_dir = Path(plan_dir)
    txn = uuid.uuid4().hex[:12]
    stage = plan_dir / JOURNAL_DIR / txn
    stage.mkdir(parents=True, exist_ok=False)

    entries = []
    for i, (rel, data) in enumerate(sorted(targets.items())):
        staged = f"{i:02d}.{Path(rel).name}"
        _write_fsync(stage / staged, data)
        entries.append(
            {"path": rel, "staged": staged, "sha256": hashlib.sha256(data).hexdigest()}
        )
    _fsync_dir(stage)
    _crash_point("stage", 0)

    journal = {"txn": txn, "at": _now(), "targets": entries, **meta}
    # The commit point: one atomic rename. Nothing above this line touched a
    # live file; nothing below it can leave a mixed generation.
    fd, tmp = tempfile.mkstemp(dir=str(stage), prefix=".journal-")
    with os.fdopen(fd, "wb") as f:
        f.write(json.dumps(journal, indent=2).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, stage / JOURNAL_NAME)
    _fsync_dir(stage)
    _crash_point("journal", 0)

    _apply_journal(plan_dir, stage, journal)
    return txn


def _apply_journal(plan_dir, stage, journal):
    plan_dir = Path(plan_dir)
    applied = []
    for n, t in enumerate(journal["targets"], start=1):
        src = stage / t["staged"]
        dst = plan_dir / t["path"]
        if not src.exists():
            continue  # already landed in an earlier (interrupted) pass
        # The journal's digest is checked, not decorative: a staged file torn by
        # the same power loss that interrupted the mutation must never be renamed
        # over a good live file.
        if hashlib.sha256(src.read_bytes()).hexdigest() != t["sha256"]:
            raise MutationError(
                f"staged {t['path']} in {stage} is corrupt (sha256 mismatch); refusing to "
                f"apply it. Delete {stage} to abandon the interrupted mutation and re-run it."
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        _fsync_dir(dst.parent)
        applied.append(t["path"])
        _crash_point("rename", n)
    # Journal last: while it exists the generation is replayable.
    (stage / JOURNAL_NAME).unlink(missing_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    parent = plan_dir / JOURNAL_DIR
    try:
        parent.rmdir()
    except OSError:
        pass
    return applied


def recover(plan_dir):
    """Finish or discard any interrupted mutation. Safe to call on every command.

    Returns a list of recovery records (empty when there was nothing to do).
    """
    base = Path(plan_dir) / JOURNAL_DIR
    if not base.is_dir():
        return []
    out = []
    for stage in sorted(base.iterdir()):
        if not stage.is_dir():
            continue
        jpath = stage / JOURNAL_NAME
        if not jpath.is_file():
            # Crashed BEFORE the commit point — no live file was touched.
            shutil.rmtree(stage, ignore_errors=True)
            out.append({"txn": stage.name, "action": "discarded_uncommitted"})
            continue
        journal = json.loads(jpath.read_text())
        applied = _apply_journal(plan_dir, stage, journal)
        out.append(
            {
                "txn": journal["txn"],
                "action": "replayed",
                "op": journal.get("op"),
                "paths_completed": applied,
            }
        )
    try:
        base.rmdir()
    except OSError:
        pass
    return out
