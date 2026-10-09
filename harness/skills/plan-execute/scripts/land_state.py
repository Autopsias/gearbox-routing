"""LND-01 — the land stage's RUNTIME state, its context, and §5.1c's digest.

Split out of ``land.py`` because this repo's ``pre-commit`` runs
``check_file_sizes.py --staged`` with a 500-LOC default and BLOCKS a commit that
grows a file past its baseline — the same measured constraint that split
``plan_hooks.py`` out of ``plan_worktree.py``. The split is also the import
spine: ``land_steps`` and ``land`` both depend on this, and this depends on
neither, so there is no cycle to unpick later.

**Land state is RUNTIME, not RECORD (§8.d/§8.e).** It lives in
``$GIT_COMMON_DIR/plan-state/<slug>/land.json``, outside every working tree.
Under ``_plans/`` a neighbouring schema-6 plan's repo-wide ``git add -A`` would
sweep a half-written approval record into a commit mid-land, and a merge
resolving ``--theirs`` could delete it. The approval is a decision input, and
§8.e's invariant is that every decision input is RUNTIME.
"""

import hashlib
import json
import time
from pathlib import Path

import plan_scope as ps
import plan_worktree as pwt
import run_state_io as rsi
import ship_state_io as ssio
import worktree as wt
from worktree import git

LAND_TIMEOUT = 1800
MAX_RESYNC = 3                      # §4.5 — bounded, then a human
STATE_FILE = "land.json"
PLAN_LANDS_REF = "refs/plan-lands"  # §4.8 — a ref that is NOT a branch


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rev(cwd, expr):
    rc, out, _ = git(["rev-parse", "--verify", "--quiet", expr], cwd)
    return out if rc == 0 and out else None


def range_files(cwd, base, head="HEAD"):
    """``<base>..<head>`` as a path list, or **None when git could not answer**.

    ``-z`` is normative (§10.2): it is the only form that survives paths
    containing spaces, quotes or newlines without git's own quoting. None (not
    ``[]``) on failure for the same reason ``gate_files.range_files`` returns it:
    the §5.1a stray-pathspec refusal reads this, and an empty list on a git error
    fails OPEN — "git errored" and "no stray paths" must never be the same
    answer. Callers park on None. ``--no-renames`` lists both sides of a move:
    a renamed file is a changed file (a moved flaky test clears its hold), and the
    §5.1a check only gets stricter.
    """
    rc, out, _ = git(["diff", "--name-only", "--no-renames", "-z", f"{base}..{head}"], cwd,
                     strip=False)
    return None if rc != 0 else [p for p in out.split("\0") if p]


def conflicted(cwd):
    rc, out, _ = git(["diff", "--name-only", "--diff-filter=U", "-z"], cwd, strip=False)
    return [] if rc != 0 else [p for p in out.split("\0") if p]


def merge_in_progress(cwd):
    """Is a merge half-finished in ``cwd``? (``MERGE_HEAD`` exists.)

    This is the state a process KILLED mid-merge leaves behind, and it is not the
    same thing as a conflict: git can be interrupted between writing the index and
    writing the commit with nothing conflicted at all. Told apart, because the
    recovery differs — a conflict is resolved, an interrupted clean merge is
    concluded or aborted — and because a park that reports "0 conflicted paths"
    reads like a bug.
    """
    rc, out, _ = git(["rev-parse", "--git-path", "MERGE_HEAD"], cwd)
    return rc == 0 and (Path(cwd) / out).exists()


def porcelain(cwd):
    return git(["status", "--porcelain"], cwd, strip=False)[1]


# --------------------------------------------------------------------------
# State + context
# --------------------------------------------------------------------------
def state_path(root, slug):
    return pwt.plan_state_dir(root, slug) / STATE_FILE


def load(plan_dir, ctx=None):
    ctx = ctx or context(plan_dir)
    return None if ctx is None else ssio.read_json_with_bak(state_path(ctx["root"], ctx["slug"]))


def save(plan_dir, ctx, st):
    """DURABLE (fsync + ``.bak``), and called after EVERY step — that is what makes
    a crash mid-land resume rather than restart."""
    st["updated_at"] = now()
    ssio.durable_write_json(state_path(ctx["root"], ctx["slug"]), st)
    return st


def context(plan_dir):
    """Everything the protocol needs about this plan, or None when it is not an
    isolated plan in a git checkout (§6's version gate, or §7.2's non-git target).

    ``default`` is READ from the claim, never re-derived: §4.0 resolves the
    default branch ONCE at ``begin`` and records it, so a repo whose default is
    ``trunk`` or ``master`` needs no change anywhere in this protocol.

    ``resource`` is built from ``wt.repo_root`` — i.e. ``git rev-parse
    --show-toplevel`` — and NOTHING ELSE, because the repo lease is keyed on that
    string. Two spellings of one repo (``/var/...`` and its resolved
    ``/private/var/...`` on macOS) slug to two DIFFERENT lock files and both
    "acquire" it, which is precisely the `same file? False` defect s03b measured
    and s02's repo-scoped lease exists to close. Measured again 2026-08-22 in
    this session's own fixture, which held the lease under the unresolved
    spelling and did not exclude a land at all.
    """
    plan_dir = Path(plan_dir).resolve()
    root = wt.repo_root(plan_dir)
    if root is None:
        return None
    claim = ps.claim(plan_dir)
    branch = claim.get("branch")
    if not branch:
        return None
    default = claim.get("default_branch") or claim.get("base_branch")
    # `origin` when it exists; otherwise the SOLE remote. An upstream-only repo
    # treated as "no remote" was measured landing nothing: no fetch, no push, a
    # durable local ref only — and a brief reporting a shipped land that never
    # left the machine. Multiple remotes with no `origin` stay None and carry
    # `remote_ambiguous`, which `land._preflight` parks: guessing a push target
    # is worse than refusing one.
    remotes = git(["remote"], root)[1].split()
    remote = "origin" if "origin" in remotes else (remotes[0] if len(remotes) == 1 else None)
    return {"plan_dir": str(plan_dir), "root": root, "slug": pwt.plan_slug(plan_dir),
            "branch": branch, "plan_tree": claim.get("path"), "default": default,
            "remote": remote, "resource": f"git:{root}",
            "remote_ambiguous": remotes if remote is None and len(remotes) > 1 else None,
            "upstream": f"{remote}/{default}" if remote else default}


def new_state(ctx):
    return {"plan_slug": ctx["slug"], "branch": ctx["branch"],
            "default_branch": ctx["default"], "remote": ctx["remote"],
            "state": "syncing", "created_at": now()}


# --------------------------------------------------------------------------
# §5.1c — the canonical gate digest
# --------------------------------------------------------------------------
#: The leveled reviewer families. Within one family a higher level reviews the
#: SAME surface at greater depth, so at land the highest declared level subsumes
#: the lower ones. Deterministic gates are never collapsed — each tests
#: something different.
_LEVELED = ("llm-review", "cross-family-review")
_LEVELS = ("low", "medium", "high")


def union_gates(manifest):
    """Every gate id any session declares, deduplicated and sorted (§4.4's
    "the plan's union of verify gates") — keeping only the HIGHEST declared
    level per leveled reviewer family.

    Measured on the cross-family-review-gate plan's land: the union
    held `llm-review-low` AND `llm-review-medium`, both ran over the identical
    25–30-file merged surface on all four regate rounds, and the low ledger
    never recorded a finding. A lower level of the same reviewer over the same
    surface is duplicate spend, not extra coverage.
    """
    ids = set()
    for s in manifest.get("sessions", []):
        ids.update((s.get("verify") or {}).get("gates") or [])
    for fam in _LEVELED:
        declared = [lvl for lvl in _LEVELS if f"{fam}-{lvl}" in ids]
        for lvl in declared[:-1]:
            ids.discard(f"{fam}-{lvl}")
    return sorted(ids)


def gate_digest(results):
    """§5.1c, VERBATIM: the ``(gate_id, outcome, findings_count)`` triples, sorted
    by ``gate_id`` byte-wise ascending, JSON with sorted keys and no whitespace,
    SHA-256, lowercase hex.

    Timestamps, durations, machine names and free-text finding bodies are
    DELIBERATELY excluded — they change between two runs that reached the same
    verdict, and including them would make every re-gate look like a changed
    result, which would fire §5.2's invalidation on every unattended re-sync.

    This is pinned in ONE place because an undefined digest is not a binding: two
    implementations would compute different digests for the same land, and §5.2's
    invalidation would be unenforceable.
    """
    rows = sorted(({"gate_id": r["gate_id"], "outcome": r["outcome"],
                    "findings_count": int(r.get("findings_count") or 0)}
                   for r in results), key=lambda r: r["gate_id"].encode())
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# Every rejection shape that means "the default branch moved under you" and is
# therefore RE-SYNCABLE under §4.5's bound. The last two were MEASURED here on
# 2026-08-22 and are why this is a list rather than the two strings §4.5 quotes:
# when a competing lander advances the ref *inside* our push window, the CAS is
# refused by the RECEIVING end, which words it completely differently —
#   remote: error: cannot lock ref 'refs/heads/main': is at <sha> but expected <sha>
#   ! [remote rejected] HEAD -> main (failed to update ref)
# — and matching only `fetch first`/`stale info` classified that as a hard
# failure. The land parked on a rejection whose entire meaning is "re-sync".
_RESYNCABLE = ("fetch first", "stale info", "non-fast-forward",
               "cannot lock ref", "failed to update ref")


def is_stale(text):
    low = (text or "").lower()
    return any(sig in low for sig in _RESYNCABLE)


def candidate(st):
    """The revision the human is asked to approve, and the exact set §5.1d
    re-compares immediately before the merge and again before the push.

    ``merge_sha`` is IN the set, and its absence was a live defect (found
    2026-08-23): the brief showed the merge commit, ``land.land_ack`` recorded it
    into the ack, and nothing ever compared it. ``step_worktree`` rebuilds the
    merge whenever the land worktree is gone — an operator removing it by hand is
    a case ``land_push.already_landed`` treats as ordinary — and that path skips
    the teardown branch that pops ``merge_sha``, so the rebuild produced a NEW
    merge commit (same parents, new committer timestamp) while ``plan_head``,
    ``main_head`` and ``gate_digest`` all stayed put. The triple still matched and
    a merge no human had seen was pushed to the default branch under an ack
    granted for a different one. Approving a merge means approving THAT merge.

    The other rebuild trigger, ``merged_plan_head != plan_head``, was already
    safe: ``plan_head`` itself moved, so ``invalidate_stale_ack`` cleared the ack.
    That is why this never showed up as a stale-ack failure.
    """
    return {"plan_head": st.get("plan_head"), "main_head": st.get("expected"),
            "gate_digest": st.get("gate_digest"), "merge_sha": st.get("merge_sha")}


def invalidate_stale_ack(st):
    """§5.1d's FIRST of two comparisons: the ack is re-checked immediately BEFORE
    the merge, not only before the push.

    Only the two values that exist at that point can be compared — ``plan_head``
    and ``main_head``; ``gate_digest`` is by construction not yet recomputed for
    this candidate, and comparing a stale one would be worse than not comparing.
    The second, complete comparison happens in ``step_ack`` before the push.

    Returns True when it cleared an ack, so the caller can say so.
    """
    ack = st.get("ack")
    if not ack:
        return False
    if ack.get("plan_head") == st.get("plan_head") and ack.get("main_head") == st.get("expected"):
        return False
    st["invalidated_from"] = ack
    st["ack"] = None
    return True


# --------------------------------------------------------------------------
# Parking — every refusal in §4 ends here
# --------------------------------------------------------------------------
NOTICE_FILE = "LAND_NOTICE.txt"


def write_notice(plan_dir, brief):
    """Put the brief where a human can actually READ it.

    Every decision surface in this codebase already does this — `run.py` prints
    `HALT_NOTICE.txt` as raw text ABOVE the JSON, and replan briefs are rendered
    into it — for a reason: `_out` is `json.dumps`, so §4.6's "verbatim and
    copy-pasteable" recovery block reaches the operator as
    `"...\n  cd /path\n  git merge --abort\n..."` on one line. The land is the
    one place a human authorises an irreversible write to the shared default
    branch; it is the last place that should be unreadable.
    """
    try:
        (Path(plan_dir) / NOTICE_FILE).write_text((brief or "").rstrip() + "\n")
    except OSError:
        pass                      # best-effort: the state file still has the brief


def clear_notice(plan_dir):
    (Path(plan_dir) / NOTICE_FILE).unlink(missing_ok=True)


def park(plan_dir, ctx, st, kind, brief, **extra):
    st["state"] = "parked"
    st["park"] = {"kind": kind, "at": now(), **extra}
    st["brief"] = brief
    save(plan_dir, ctx, st)
    write_notice(plan_dir, brief)
    rsi.log_event(plan_dir, "plan_land_parked", session_ids=[], slug=ctx["slug"],
                  kind=kind, **{k: v for k, v in extra.items() if isinstance(v, (str, int))})
    return {"action": "land-parked", "kind": kind, "brief": brief,
            "land_worktree": st.get("land_path"), **extra}
