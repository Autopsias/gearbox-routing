"""Clearing an earlier open finding with no final record.

Split out of cf_check.py to keep it under the house 500-line limit. Owner rule (land
review r5): revalidation never clears a confirmed finding or a lead. With no final
record, only a lead can leave, through a critic entry. The marker still stores each
item's ref and trace files; nothing reads them to clear an item.
"""
import posixpath
import subprocess

# the ledger prior_status a revalidation unit carries, per kind of earlier open item;
# such a unit is bookkeeping only: it clears nothing
REVALIDATION = {"confirmed": "prior_confirmed_changed_source", "lead": "prior_needs_validation"}


def _tracked(r):
    """(regular files at HEAD, their folders, symlinks and submodules at HEAD).

    With a clean tree the first set is exactly the source the run's source_ref
    names; ignored files are not in it, and neither is anything a symlink points to."""
    p = subprocess.run(["git", "-C", str(r), "ls-tree", "-r", "-z", "HEAD"],
                       capture_output=True, text=True)
    entries = [e.split("\t", 1) for e in p.stdout.split("\0") if "\t" in e] \
        if p.returncode == 0 else []
    files = {path for meta, path in entries if meta.split(" ")[0] in ("100644", "100755")}
    special = {path for meta, path in entries if meta.split(" ")[0] not in ("100644", "100755")}
    dirs = {f.rsplit("/", i)[0] for f in files for i in range(1, f.count("/") + 1)} | {"."}
    return files, dirs, special


def _norm(path):
    return posixpath.normpath(path).rstrip("/")


def trace_files(rec):
    """The finding's own files: every trace step's file, normalised, sorted, no repeats."""
    return sorted({_norm(t["file"]) for t in rec.get("trace") or []
                   if isinstance(t, dict) and isinstance(t.get("file"), str) and t["file"]})


def _cleared(old, meta, findings):
    """{(kind, fingerprint): why} for earlier open items this run resolves WITHOUT a final
    record. The one way: for a lead only, a run-metadata `resolved_prior_leads` entry with
    a reason (HUNTING.md); it never clears a confirmed finding with the same fingerprint.

    A revalidation unit clears nothing, whatever it reviewed and whatever changed: a fixed
    finding stays open until a complete run reports it rejected (owner, land review r5).
    Fields are checked by _bad_bookkeeping first."""
    recorded = {f.get("fingerprint") for f in findings}
    critic = {e["fingerprint"]: " ".join(e["reason"].split())
              for e in meta.get("resolved_prior_leads") or [] if e["reason"].strip()}
    return {(kind, fp): f"critic: {critic[fp]}" for kind, fp in old
            if kind == "lead" and fp in critic and fp not in recorded}
