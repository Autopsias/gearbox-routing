"""Parallel-group manifest contract — the single shared checker.

The contract itself: ``../references/parallel-group-contract.md`` (contract v1,
FROZEN 2026-08-12 by plan session S06 / item PL-02). Read it before changing
anything here; this module is its implementation, not its source of truth. Rule
IDs in the messages below (R1, M1, M2, M2a, M3, M5, §1, §3) are that document's.

**One implementation, two call sites.** The same rules are enforced at BUILD
time (plan-builder's ``validate_spec.py``, over ``spec.json``) and again at
DISPATCH time (``run.py begin``, over ``manifest.json``). They must not drift, so
neither side reimplements them — both import this module. That double enforcement
is deliberate: a manifest can be hand-edited, produced by an older builder, or
mutated in flight by ``run.py add-session`` / ``amend-session``, so *the gate
protecting the tree must not live only in the tool that wrote the manifest.*

One function reads both shapes because ``spec.json`` and ``manifest.json`` carry
identical field paths for everything checked here::

    sessions[].dispatch.parallel_group     sessions[].dispatch.depends_on
    sessions[].dispatch.isolation          sessions[].dispatch.integrates_group
    sessions[].post_session.git            sessions[].verify.gates
    sessions[].items  (item ids)           items[].touches

Returns problems rather than raising: the caller owns the refusal wording and its
exit path (``SystemExit`` at dispatch, a validation error at build).

WHAT THIS MODULE CANNOT DO (contract §7): every check reads DECLARED manifest
fields. A member that shells out to ``git commit``, runs ``uv add``, or writes a
file it never declared passes all of them. The after-the-fact detection for that
is the integration session's containment + baseline checks (contract §3 rules
6-7), which are executor behaviour, not manifest shape, and live in run.py.
"""

import posixpath
import re

# Contract §6 — the rules marked v3+ in the §5 table apply only from this
# manifest schema version. A missing or non-integer stamp counts as below it.
# Plans built before the contract existed never gain a new refusal mode
# retroactively; same gating pattern as
# ``closeout_pipeline.PLAN_IMPACT_MIN_SCHEMA``. This is load-bearing: two v2
# plans in this repo have parallel members carrying ``post_session.git:
# "commit"``, which M1 now forbids.
#
# R1 is deliberately NOT gated — it is enforced for all versions today
# (``structural_gate._group_coherence``), and gating it would REGRESS existing
# behaviour rather than preserve it.
MIN_SCHEMA = 3

CONTRACT_DOC = "skills/plan-execute/references/parallel-group-contract.md"

# Contract M3 — the ONLY legal isolation value. ``None``/absent means a
# shared-tree group.
ISOLATION_WORKTREE = "worktree"

# Whether THIS executor build can actually honour ``isolation: "worktree"``
# (orchestrator-managed ``git worktree add``: staggered creation off a pinned and
# verified base ref, per-member gates run inside the member's worktree,
# producer-first integration merge, explicit never-automatic cleanup).
#
# S06 froze the contract; S06B built the mechanism (``worktree.py``, wired into
# ``run.py`` cmd_begin/cmd_apply and ``verify.gate_cwd``) and flipped this True.
#
# DO NOT flip this back to True-by-default in a build that cannot honour it. A
# False here REFUSES a worktree declaration rather than accepting it and silently
# providing a shared tree — accepting an inert isolation flag would let a plan
# believe it is protected when it is not, which is the exact failure the contract
# was written to end. The Codex harness still refuses the declaration at dispatch
# (run.py ``_isolation_prep``): it runs sessions serially in the shared tree and
# creates no worktrees.
ISOLATION_IMPLEMENTED = True

# Contract M2 — closed list of dependency-manifest / lockfile basenames, matched
# case-insensitively. Explicit rather than pattern-only so a refusal can name
# exactly what it matched.
DEPENDENCY_FILES = frozenset(
    x.lower()
    for x in (
        "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
        "pnpm-lock.yaml", "bun.lockb",
        "requirements.txt", "requirements-dev.txt", "constraints.txt",
        "Pipfile", "Pipfile.lock", "pyproject.toml", "poetry.lock", "uv.lock",
        "setup.py", "setup.cfg",
        "Gemfile", "Gemfile.lock",
        "go.mod", "go.sum",
        "Cargo.toml", "Cargo.lock",
        "composer.json", "composer.lock",
        "Podfile", "Podfile.lock",
        "pubspec.yaml", "pubspec.lock",
        "mix.exs", "mix.lock",
        "build.gradle", "build.gradle.kts", "pom.xml",
    )
)
_DEPENDENCY_PATTERNS = (re.compile(r"^requirements.*\.txt$"), re.compile(r"^.*\.lock$"))

# `post_session.git` enum is "none | commit | commit-push | commit-push-pr";
# anything outside this set is a shipping action (contract M1).
_NON_SHIPPING_GIT = frozenset(("", "none"))


def _dispatch(session):
    return session.get("dispatch") or {}


def _ships(session):
    git = (session.get("post_session") or {}).get("git")
    return bool(git) and str(git).strip().lower() not in _NON_SHIPPING_GIT


def _gates(session):
    return frozenset((session.get("verify") or {}).get("gates") or [])


def _norm(path):
    """Normalise one declared path for comparison: posix, no trailing slash."""
    return posixpath.normpath(str(path).strip().replace("\\", "/")).rstrip("/")


def _touch_paths(raw):
    """`touches` is a free-form string like "a/b.py, tests, pyproject.toml"."""
    if not raw:
        return []
    return [_norm(p) for p in re.split(r"[,\n]", str(raw)) if p.strip()]


def _overlaps(a, b):
    """Same path, or one is a directory prefix of the other."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def dependency_hits(touches):
    """The dependency-manifest / lockfile basenames named in a `touches` string."""
    hits = []
    for path in _touch_paths(touches):
        base = posixpath.basename(path).lower()
        if base in DEPENDENCY_FILES or any(p.match(base) for p in _DEPENDENCY_PATTERNS):
            hits.append(path)
    return hits


def groups(doc):
    """``{group_name: [session, ...]}`` in document order.

    Keyed on a NON-EMPTY string (contract §1): ``dispatch.next_action`` batches on
    truthiness, so an empty-string group would be a member here and not a group
    there. ``_empty_group_ids`` reports those separately rather than dropping
    them silently.
    """
    out = {}
    for s in doc.get("sessions") or []:
        pg = _dispatch(s).get("parallel_group")
        if isinstance(pg, str) and pg.strip():
            out.setdefault(pg, []).append(s)
    return out


def _empty_group_ids(doc):
    return [
        s["id"]
        for s in doc.get("sessions") or []
        if (pg := _dispatch(s).get("parallel_group")) is not None
        and (not isinstance(pg, str) or not pg.strip())
    ]


def symmetry_problems(doc):
    """R1 — every member of a group must share ONE ``depends_on`` set.

    Split out and UNGATED because ``structural_gate`` reports this one as a
    containment problem with its own ``(check, subject)`` identity, for every
    schema version, exactly as it did before this contract existed.
    """
    out = []
    for pg, members in sorted(groups(doc).items()):
        shapes = {s["id"]: frozenset(_dispatch(s).get("depends_on") or []) for s in members}
        if len(set(shapes.values())) > 1:
            shape = "; ".join(f"{sid} → {sorted(deps) or '(none)'}" for sid, deps in shapes.items())
            out.append((
                pg,
                f"members of parallel_group {pg!r} do not share one depends_on set "
                f"({shape}). dispatch.next_action batches the first READY session with "
                "ready peers of the same group, so they become ready at different times "
                "and the group silently degrades to sequential dispatch",
            ))
    return out


def _isolation_problems(pg, members):
    """M3 + §1 uniform isolation. Keys on KEY PRESENCE and VALUE, never on
    truthiness, so a typo is refused rather than quietly read as "no isolation"."""
    out = []
    declared = {}
    for s in members:
        d = _dispatch(s)
        if "isolation" in d and d["isolation"] is not None:
            declared[s["id"]] = d["isolation"]
            if d["isolation"] != ISOLATION_WORKTREE:
                out.append(
                    f"[M3] session {s['id']} declares dispatch.isolation={d['isolation']!r}; the "
                    f"only legal value is {ISOLATION_WORKTREE!r} (orchestrator-managed "
                    f"`git worktree add`). See {CONTRACT_DOC} §2 M3."
                )
    if declared and len(declared) != len(members):
        missing = sorted({s["id"] for s in members} - set(declared))
        out.append(
            f"[§1] parallel_group {pg!r} is half-isolated: {sorted(declared)} declare "
            f"dispatch.isolation but {missing} do not. Isolation is a GROUP property — "
            f"every member must declare the same value. See {CONTRACT_DOC} §1."
        )
    if declared and not ISOLATION_IMPLEMENTED:
        out.append(
            f"[M3] parallel_group {pg!r} declares dispatch.isolation="
            f"{ISOLATION_WORKTREE!r}, but THIS executor build does not implement "
            "orchestrator-managed worktree dispatch yet, so it cannot honour the "
            "declaration. Refusing rather than running the group in a shared tree, which "
            "would leave the plan believing it is isolated when it is not. Remove the "
            f"declaration to run as a shared-tree group. See {CONTRACT_DOC} §2 M3."
        )
    return out


def _isolated(members):
    return any(_dispatch(s).get("isolation") == ISOLATION_WORKTREE for s in members)


def _write_sets(members, touches_by_item):
    """``{session_id: [declared path, ...]}`` — the M2a/M5 input."""
    return {
        s["id"]: [p for i in (s.get("items") or []) for p in _touch_paths(touches_by_item.get(i))]
        for s in members
    }


def check(doc, *, schema_version):
    """Return ``(refusals, warnings)`` — both lists of display strings.

    ``schema_version`` is REQUIRED and has no default on purpose: a checker that
    silently passes because its version input was absent is worse than no
    checker. Dispatch passes ``manifest["plan_schema_version"]``; the builder
    passes its own ``PLAN_SCHEMA_VERSION``.
    """
    refusals, warnings = [], []
    grouped = groups(doc)

    # R1 is UNGATED (contract §6) — it must behave identically on every version.
    refusals += [f"[R1] {msg}" for _, msg in symmetry_problems(doc)]

    gated = (schema_version or 0) >= MIN_SCHEMA
    if not gated:
        if grouped:
            # Contract §6: warn rather than stay silent, so the diagnostic still
            # reaches the operator on the "produced by an older builder" case
            # that is the whole reason the dispatch gate exists.
            warnings.append(
                f"plan_schema_version={schema_version!r} is below {MIN_SCHEMA}, so the "
                f"parallel-group contract rules are NOT enforced for {sorted(grouped)}. "
                f"Rebuild the plan to adopt them; see {CONTRACT_DOC} §6."
            )
        return refusals, warnings

    for sid in _empty_group_ids(doc):
        refusals.append(
            f"[§1] session {sid} has a dispatch.parallel_group that is present but not a "
            "non-empty string. dispatch.next_action batches on truthiness, so this session "
            "would be a group member by the schema and a lone session at dispatch. Use a "
            f"non-empty group name or null. See {CONTRACT_DOC} §1."
        )

    touches_by_item = {
        it.get("id"): it.get("touches") for it in (doc.get("items") or []) if it.get("id")
    }
    all_sessions = doc.get("sessions") or []

    for pg, members in sorted(grouped.items()):
        ids = sorted(s["id"] for s in members)
        member_ids = set(ids)
        refusals += _isolation_problems(pg, members)
        isolated = _isolated(members)

        for s in members:
            if _ships(s):
                refusals.append(
                    f"[M1] session {s['id']} is a member of parallel_group {pg!r} and declares "
                    f"post_session.git={(s.get('post_session') or {}).get('git')!r}. Concurrent "
                    "committers race on .git/index.lock, and on a shared tree a commit made "
                    "mid-flight captures a peer's half-written state. Shipping belongs to the "
                    f"group's integration session (dispatch.integrates_group={pg!r}). "
                    f"See {CONTRACT_DOC} §2 M1."
                )
            for item_id in s.get("items") or []:
                raw = touches_by_item.get(item_id)
                # M2a — FAIL CLOSED. Measured at freeze time: 307 manifest items
                # across this repo's plans, 0 carrying `touches` (the builder did
                # not copy the field into manifest.json), against 177 spec items
                # that declare it. A gate keyed on an absent field returns
                # "clean" on every plan ever built — worse than no gate. So a
                # member item with no `touches` is a REFUSAL, never a skip.
                if not (raw and str(raw).strip()):
                    refusals.append(
                        f"[M2a] session {s['id']} is a member of parallel_group {pg!r} and owns "
                        f"item {item_id}, which declares no `touches`. M2 (dependency/lockfile "
                        "ban) and M5 (overlapping writes) are both computed from that field, so "
                        "without it BOTH tree-protecting rules are silently inert. Declare what "
                        "the item writes, or take the session out of the group. "
                        f"See {CONTRACT_DOC} §2 M2a."
                    )
                    continue
                hits = dependency_hits(raw)
                if hits:
                    refusals.append(
                        f"[M2] session {s['id']} (member of parallel_group {pg!r}) owns item "
                        f"{item_id}, whose touches names {', '.join(hits)}. Two members each "
                        "running an install regenerate the whole lockfile and conflict on nearly "
                        "every line, and an agent 'resolving' that silently drops dependencies. "
                        "Move the dependency change out of the group, or narrow touches to what "
                        f"the session actually writes. See {CONTRACT_DOC} §2 M2."
                    )

        # M5 — overlapping writes. Corruption on a shared tree; on an isolated
        # group it is a MERGE COST (S02 measured a planted conflict failing
        # loudly), so it is reported as a quantified warning instead.
        writes = _write_sets(members, touches_by_item)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                shared = sorted({x for x in writes[a] for y in writes[b] if _overlaps(x, y)})
                if not shared:
                    continue
                if isolated:
                    warnings.append(
                        f"[M5] parallel_group {pg!r}: {a} and {b} both declare writes under "
                        f"{', '.join(shared)}. Isolated, so this is a MERGE COST, not "
                        "corruption — the integration session will have to reconcile it"
                    )
                else:
                    refusals.append(
                        f"[M5] parallel_group {pg!r} is NOT isolated and members {a} and {b} both "
                        f"declare writes under {', '.join(shared)}. Interleaved writes to one "
                        "file in one working tree corrupt both members' work and nothing "
                        "downstream can reconstruct it. Give the group "
                        f"dispatch.isolation={ISOLATION_WORKTREE!r}, split the file, or run the "
                        f"two sessions serially. See {CONTRACT_DOC} §2 M5."
                    )

        # §3 — the integration session is DECLARED, never derived. Derivation was
        # tried and rejected in review: it cannot be evaluated without already
        # knowing the answer, admits two qualifying sessions with no tie-break,
        # and silently conscripts any capstone that depends on everything.
        integrators = [s for s in all_sessions if _dispatch(s).get("integrates_group") == pg]
        if not integrators:
            msg = (
                f"[§3] parallel_group {pg!r} has no session declaring "
                f"dispatch.integrates_group={pg!r}. Its members are barred from shipping (M1), "
                "so the group's work will not be committed by this plan"
            )
            if isolated:
                # An isolated group MUST have one: nothing else merges the
                # member branches, so without it the work is stranded on them.
                refusals.append(f"{msg}, and nothing merges the member branches. "
                                f"See {CONTRACT_DOC} §3.")
            else:
                # Shared tree: a plan that never commits is legitimate, so
                # stranding it on a shipping rule it does not use would be worse
                # than the defect this catches.
                warnings.append(msg)
        elif len(integrators) > 1:
            refusals.append(
                f"[§3] parallel_group {pg!r} has {len(integrators)} sessions declaring "
                f"integrates_group={pg!r} ({sorted(s['id'] for s in integrators)}). Exactly one "
                f"is allowed — two independent shippers is an error, not an ambiguity. "
                f"See {CONTRACT_DOC} §3."
            )
        for integ in integrators:
            deps = set(_dispatch(integ).get("depends_on") or [])
            if integ["id"] in member_ids:
                refusals.append(
                    f"[§3] session {integ['id']} is both a member of parallel_group {pg!r} and "
                    f"its integration session. See {CONTRACT_DOC} §3 rule 2."
                )
            elif not member_ids <= deps:
                refusals.append(
                    f"[§3] integration session {integ['id']} does not depend on every member of "
                    f"parallel_group {pg!r}: missing {sorted(member_ids - deps)}. It would "
                    f"dispatch before they finish. See {CONTRACT_DOC} §3 rule 2."
                )
            union = frozenset().union(*(_gates(s) for s in members)) if members else frozenset()
            if not union <= _gates(integ):
                refusals.append(
                    f"[§3] integration session {integ['id']} does not re-run the full gate set "
                    f"for parallel_group {pg!r}: missing {sorted(union - _gates(integ))}. A clean "
                    "textual merge does not imply a working tree — a member renaming a symbol "
                    "and a peer adding a caller of the old name merge without complaint and are "
                    f"caught only here. See {CONTRACT_DOC} §3 rule 3."
                )
            if not _ships(integ):
                warnings.append(
                    f"[§3] integration session {integ['id']} for parallel_group {pg!r} declares "
                    "no post_session.git action, and its members are barred from shipping (M1), "
                    "so the group's work will not be committed by this plan"
                )

    return refusals, warnings


def refusal_text(refusals):
    """One multi-line block for a ``SystemExit`` / validation error."""
    head = (
        f"parallel-group contract violation ({len(refusals)} problem"
        f"{'' if len(refusals) == 1 else 's'}) — {CONTRACT_DOC}:"
    )
    return "\n".join([head] + [f"  - {r}" for r in refusals])
