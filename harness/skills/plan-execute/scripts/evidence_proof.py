"""LND-03 — a declared evidence artifact must be proof of THIS session's run.

``verify._check_evidence`` used to ask one question: does the path exist and is
it non-empty. That is not proof of anything. The artifact some EARLIER run wrote
is already sitting at the declared path before this session writes a byte —
under isolation because a worktree is a CHECKOUT OF A PINNED BASE that already
contains it, and in an ordinary shared checkout because the file is simply still
there from last week. Same defect shape as three other findings in this plan: an
artifact from an earlier state standing in as proof about the current one.

WHAT THIS ASKS INSTEAD, and why in this exact shape:

* **Every checkout the artifact can land in is searched, not just pinned ones.**
  The first cut of this module asked freshness only of artifacts inside a
  worktree. Measured against this plan's own ``manifest.json``, that made the
  check a NO-OP for all fourteen sessions: every declared ``evidence_path`` is
  ``_evidence/...``, which ``plan_scope.resolve_evidence_path`` deliberately
  resolves in the OUTER plan directory, and no session here declares
  ``isolation: worktree``. A check pointed at nothing reports green — which is
  the exact defect class this plan exists to eliminate. The shared repo root is
  therefore a first-class checkout here.

* **Against the SESSION'S BASE IN THE CHECKOUT BEING ASKED, never HEAD, and
  never through ``review_context.get_base``.** The base is derived fresh in each
  candidate tree (``review_context.derive_base``): the commit that was that
  tree's HEAD when this session was FIRST dispatched. A group member instead uses
  its group's pinned base, read from the state file that also holds its path —
  its checkout was literally created there. Comparing against HEAD is what killed
  the previous attempt at this fix (``apply`` calls ``worktree.commit_member``
  BEFORE the verify loop, so by evidence time the checkout is clean against HEAD
  and every genuine artifact reads as stale); borrowing the review gate's
  ``get_base`` killed the attempt after that, and ``_base`` records why.

* **Untracked is only proof in a FRESH checkout.** A member worktree is a
  tracked-files-only checkout made for this run, so anything untracked in it was
  produced here (``git ls-files --others``, deliberately WITHOUT
  ``--exclude-standard``, so a ``.gitignore``d artifact counts). The plan
  worktree and the shared checkout are long-lived and carry other sessions'
  leftovers, so an untracked artifact there is decided by its mtime against this
  session's first ``dispatch_started``.

* **FAIL CLOSED.** The reverted attempt's line was
  ``return rc != 0 or bool(out.strip())`` — defensive-looking, and it grants the
  check precisely when git is least trustworthy. Every git error, unreadable
  path, missing base or otherwise undecidable answer REFUSES, naming the artifact
  and which of absent / unchanged-since-base / indeterminate it was.

SCOPE — read this before trusting the check: it proves the artifact is newer than
the SESSION, not newer than the ATTEMPT. The base is pinned at first dispatch on
purpose (``review_context.record_base``), so an artifact an earlier attempt of
this same session wrote still satisfies it. Bounding per attempt would refuse a
session whose rework was about something else entirely and whose evidence is
legitimately unchanged — unfixable-by-construction, which this plan already
treats as a defect in its own right. What the check DOES close is the case it was
built for: an artifact from an earlier SESSION, or from before this session
existed, standing in as proof.

The one case that still cannot be decided is an artifact in a plan that was never
dispatched by the orchestrator at all: there is no run boundary to compare
against, so the pre-existing existence contract stands — INSIDE a checkout as
well as outside one. The single exception is a parallel-group member's worktree,
which is itself a run boundary: it was created for this run at a pinned base, so
it can date an artifact with no dispatch record at all. A plan that HAS dispatch
records but none for this session refuses rather than passes.
"""

from datetime import datetime, timezone
from pathlib import Path

import plan_scope as pscope
import review_context as rc
import ship_state_io as ssio
import worktree as wt

_DETAIL_MAX = 200


def _resolve(p):
    """``Path.resolve()`` that answers None instead of raising. A None checkout
    root is SKIPPED (it may be unrelated to this artifact) and refuses only if
    nothing else claims the artifact — see ``stale_reason``."""
    try:
        return Path(p).resolve()
    except OSError:
        return None


def _member_checkout(plan_dir, session_id):
    """(worktree path, group base sha) for an isolated parallel-group member.

    Reads the group state directly rather than going through
    ``worktree.member_path``, because the base sha and the path have to come from
    the SAME state file — pairing a path from one group with a base from another
    is how a freshness check silently compares against the wrong history.
    """
    d = Path(plan_dir) / "_worktrees"
    if not d.is_dir():
        return None, None
    for f in sorted(d.glob("*.json")):
        state = ssio.read_json_with_bak(f) or {}
        entry = (state.get("members") or {}).get(session_id)
        if entry and entry.get("path") and Path(entry["path"]).is_dir():
            return entry["path"], entry.get("base_ref") or state.get("base_ref")
    return None, None


def _checkouts(plan_dir, session_id):
    """Every checkout this session's artifacts can resolve into, most specific
    first — the same precedence ``plan_scope.plan_cwd`` uses, plus the SHARED
    repo root, which is where a non-isolated plan's evidence actually lives.

    Returns ``(path, fresh_checkout)`` pairs. ``fresh_checkout`` marks a tree
    created for THIS run, where "untracked" is by itself proof of authorship.
    """
    out = []
    m_path, _ = _member_checkout(plan_dir, session_id)
    if m_path:
        out.append((m_path, True))
    p_path = pscope.plan_worktree(plan_dir)
    if p_path:
        out.append((p_path, False))
    root = wt.repo_root(plan_dir)
    if root:
        out.append((str(root), False))
    return out


def _base(plan_dir, session_id, tree, fresh_checkout):
    """(base sha, human name) to compare ``tree`` against — or (None, why not).

    RESOLVED PER CHECKOUT, READ-ONLY, and deliberately NOT through
    ``review_context.get_base``. That helper serves the REVIEW gate and carries a
    fallback policy of its own; routing freshness through it broke this check in
    two measured ways, both reproduced against a real isolated fixture (plan
    worktree on the plan branch, ``_evidence/**`` resolving in the OUTER checkout,
    which is where §8.a puts it):

      * ``get_base`` FALLS BACK TO THE PLAN'S PINNED BASE whenever the cached
        session base is not an ancestor of the tree it is asked about — the
        DEFAULT on this path, because the review gate caches a base derived on the
        plan branch and that is never an ancestor of the outer checkout's HEAD.
        The pin is the branch CUT POINT, an ancestor of that HEAD by construction,
        so an ancestry guard placed AFTER the fallback can never fire, and every
        artifact committed to the outer checkout since the branch was cut — an
        earlier session's landed evidence included — read as this session's work.
        The answer is not another guard on top of the fallback; it is not taking
        the fallback.
      * On a cache MISS ``get_base`` calls ``record_base``, so ASKING this
        question wrote a base derived in the EVIDENCE tree into shared run state,
        where first-write-wins makes it permanent. The review gate then found that
        base unusable in its worktree, fell back to the pin, and re-reviewed every
        earlier session — the growing surface that cost 16 gate rounds. A
        read-only check must not move the reviewer's window.

    So the base is taken from the two SIDE-EFFECT-FREE halves of that helper
    instead, and the ancestry test between them is the one thing that decides:
    ``recorded_base`` (what the reviewer diffed against, measured AT dispatch)
    when ``tree`` can actually reach it, else ``derive_base`` run in ``tree``
    itself. THAT GUARD FIRES — under isolation the recorded base is a plan-branch
    commit and evidence resolves in the outer checkout, so the answer is no and
    the derivation takes over. Neither branch can reach the pin. Where the
    derivation answers, it is ``git rev-list -1 --before=<ts> HEAD`` run in that
    tree, so it is an ancestor of that tree's HEAD by construction and needs no
    guard of its own — a guard that cannot fail is the defect this module exists
    to remove. None (no dispatch record, no commit predating it, git unavailable)
    refuses below.
    """
    if not fresh_checkout:
        # The gate's RECORDED base first, READ with `recorded_base` (no derive, no
        # cache write, no pinned fallback) — it is the base the reviewer actually
        # diffed against, and it was measured AT dispatch, which re-deriving today
        # cannot always reproduce: after a merge, `rev-list --before` walks a
        # history that now interleaves the other branch's commits and answers with
        # one of those instead. Measured on this plan — three of ten sessions got a
        # base neither an ancestor nor a descendant of the recorded one.
        # It is used ONLY where this tree can diff against it. That ancestry test
        # is the one that decides, and it FIRES: under isolation the gate's base
        # was derived on the plan branch and evidence resolves in the outer
        # checkout, so the answer is no and the derivation below takes over.
        base = rc.recorded_base(plan_dir, session_id)
        if base and rc.usable_base(base, tree):
            return base, "this session's recorded base"
        return _named(rc.derive_base(plan_dir, session_id, tree),
                      "this session's base derived in this checkout")
    # The one base NOT derived from the tree's own history: a sha read from the
    # group state file, so ancestry is ASSERTED here rather than assumed. `git
    # diff` against a commit HEAD cannot reach is the SYMMETRIC difference of two
    # histories and would grant files the session never touched.
    base, name = _member_checkout(plan_dir, session_id)[1], "the group's pinned base"
    if base and not rc.usable_base(base, tree):
        return None, (f"{name} {base[:12]} is not an ancestor of this checkout's HEAD, so "
                      "a diff against it would be the symmetric difference of two unrelated "
                      "histories and would grant files this session never touched")
    return _named(base, name)


def _named(base, name):
    return (base, name) if base else (None, f"{name} is not recoverable from plan state")


def _indeterminate(what, detail):
    detail = (detail or "").strip()[:_DETAIL_MAX] or "no output"
    return (f"freshness INDETERMINATE — {what} failed ({detail}); refusing, because an "
            "evidence check that cannot decide must never grant")


def _dispatched_anywhere(plan_dir):
    """Has the orchestrator dispatched ANY session of this plan? False means the
    plan was never run through ``run.py`` and no run boundary exists at all."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return False
    return any('"dispatch_started"' in ln for ln in lines)


def _since_dispatch(plan_dir, session_id, path, where):
    """None when ``path`` was last written at or after this session's first
    dispatch; a refusal otherwise. mtime is the weakest of the answers here and
    is used ONLY where git has none: outside a checkout, or untracked in a
    long-lived one."""
    ts = rc.first_dispatch_at(plan_dir, session_id)
    if not ts:
        if _dispatched_anywhere(plan_dir):
            return ("freshness INDETERMINATE — this plan has dispatch records but none for "
                    f"session {session_id}, so there is no run boundary to date the artifact "
                    "against; refusing rather than assuming it is proof")
        return None                      # plan never orchestrated: no run to be proof of
    try:
        when = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
        started = datetime.fromisoformat(ts)
    except (OSError, ValueError) as e:
        return _indeterminate("dating the artifact", str(e))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if when >= started:
        return None
    return (f"last written {when.isoformat(timespec='seconds')}, BEFORE this session was "
            f"dispatched at {ts} — {where}, so its timestamp is the only date available, "
            "and it says an earlier run left this file behind")


def _verdict(ctx, tree, rel, fresh_checkout):
    """None when the artifact is proof of this session; a refusal otherwise.

    THE BASE IS RESOLVED ONLY WHERE IT IS USED — after the untracked question,
    not before it. Two reasons, both measured. An untracked artifact is decided
    without any base at all, so demanding one up front refused answers that did
    not depend on it. And resolving it first put a ``git merge-base`` call ahead
    of ``git ls-files``, which swallowed the broken-gitdir case into the base
    branch and left the ls-files fail-closed line with no probe that could fail
    it — a passing neuter is a broken probe, not a passing check.
    """
    plan_dir, session_id = ctx
    rc_, out, err = wt.git(["ls-files", "--others", "--", rel], tree)
    if rc_ != 0:
        return _indeterminate("git ls-files", err or out)
    if out.strip():
        if fresh_checkout:
            return None                  # untracked in a fresh checkout = written here
        return _since_dispatch(plan_dir, session_id, Path(tree) / rel,
                               "an untracked file in a long-lived checkout may be any "
                               "earlier session's leftover")
    base, base_name = _base(plan_dir, session_id, tree, fresh_checkout)
    if not base:
        if not fresh_checkout and not _dispatched_anywhere(plan_dir):
            # THE SAME never-dispatched carve-out `_since_dispatch` applies outside
            # every checkout, and for the same reason: no dispatch record anywhere
            # means no run for the artifact to be proof OF, so the pre-existing
            # existence contract stands. Splitting the two — refusing INSIDE a
            # checkout and granting outside it — is what the docs promised against,
            # and it made the check unsatisfiable by construction for a plan run by
            # hand: an artifact committed into a plan the orchestrator never
            # dispatched was refused with "base is not recoverable". An evidence
            # check a session CANNOT pass burns a rework attempt for nothing, which
            # this plan treats as a defect in its own right.
            # A member worktree is excluded because it IS a run boundary: it was
            # created for this run at a pinned base, dispatch records or not.
            return None
        return (f"freshness INDETERMINATE — {base_name}; refusing rather than falling back "
                "to HEAD (a checkout is clean against HEAD the moment the orchestrator "
                "commits it, which is what made the previous attempt at this check reject "
                "every genuine artifact)")
    rc_, out, err = wt.git(["diff", "--name-only", base, "--", rel], tree)
    if rc_ != 0:
        return _indeterminate("git diff", err or out)
    if out.strip():
        return None                      # committed or edited since the base = written here
    return (f"unchanged since {base_name} {base[:12]} — that is the commit this session "
            "started from, so the artifact is one an earlier run left behind, not proof "
            "that this session wrote anything")


def stale_reason(plan_dir, session_id, path):
    """Why ``path`` is not proof of THIS session's run, or None when it is.

    Fails CLOSED: an unreadable path, an unreadable checkout that could have held
    the artifact, a git error, or a missing base all REFUSE.
    """
    resolved = _resolve(path)
    if resolved is None:
        return "unreadable path — cannot prove this run wrote it"
    unreadable = []
    for tree, fresh_checkout in _checkouts(plan_dir, session_id):
        root = _resolve(tree)
        if root is None:
            unreadable.append(str(tree))  # may be unrelated to this artifact — see below
            continue
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue                     # artifact is not in this checkout
        return _verdict((plan_dir, session_id), str(root), str(rel), fresh_checkout)
    if unreadable:
        # Only now: no checkout CLAIMED the artifact, so one we could not read is
        # still a candidate and the answer is genuinely unknown. Refusing on an
        # unrelated checkout that merely failed to resolve would be noise.
        return _indeterminate("resolving a checkout that could hold the artifact",
                              ", ".join(unreadable))
    return _since_dispatch(plan_dir, session_id, resolved,
                           "the artifact is outside every checkout this plan tracks, so "
                           "there is no history to ask")


def artifact_problem(plan_dir, session_id, raw, *, require_fresh=True):
    """Why a declared artifact is not usable proof, or None when it is.

    ``require_fresh=False`` asks the pre-existing question ONLY — exists and is
    non-empty. That is the contract ``verify.checks[].evidence_path`` has always
    had ("this file must show X"), and it legitimately points at a repo document
    the session only reads; making it mean "this run modified it" would fail such
    a check unfixably and burn a rework attempt. The closeout's ``evidence``
    array is the surface that claims "proof this run engaged", so that one — and
    only that one — asks for freshness too.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "not a non-empty path string"
    try:
        path = pscope.resolve_evidence_path(plan_dir, session_id, raw)
        if not path.exists():
            return "does not exist"
        if path.is_file() and path.stat().st_size == 0:
            return "empty file — no proof inside"
        if path.is_dir() and not any(path.iterdir()):
            return "empty directory"
    except OSError as e:                 # unreadable → refuse, never assume proof
        return f"unreadable ({e.strerror or e}) — refusing rather than assuming proof"
    return stale_reason(plan_dir, session_id, path) if require_fresh else None
