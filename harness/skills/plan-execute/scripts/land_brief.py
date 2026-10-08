"""LND-01 — the land stage's DECISION BRIEFS, and nothing else.

Split out of ``land.py`` for the same measured reason ``plan_hooks.py`` was split
out of ``plan_worktree.py``: this repo's ``pre-commit`` runs
``check_file_sizes.py --staged`` with a 500-LOC default and BLOCKS a commit that
grows a file past its baseline. Briefs are the bulkiest, least-branching part of
the protocol, so they move and the state machine stays readable.

Every function here returns TEXT an operator can act on without opening a file.
Contract: ``../references/plan-isolation-contract.md`` §4.3, §4.6, §4.7, §4.8,
§5.3 — the section each brief implements is named in its docstring.
"""


def owner_of_default(git, root, default):
    """Which worktree holds ``refs/heads/<default>``, or None when all are detached.

    §4.3 makes the ``-C <worktree-that-owns-main>`` normative rather than
    decorative: s03b probe 1 (d2) measured `main` living in a worktree OTHER than
    the primary checkout, and in that state the primary's own
    ``git merge --ff-only origin/<default>`` merges into whatever branch the
    primary has out — leaving the real default stale and the operator none the
    wiser.
    """
    rc, out, _ = git(["worktree", "list", "--porcelain"], root, strip=False)
    if rc != 0:
        return None
    current = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):].strip()
        elif line.strip() == f"branch refs/heads/{default}":
            return current
    return None


def _dirty_paths(git, owner):
    out = git(["status", "--porcelain"], owner, strip=False)[1]
    return [ln[3:] for ln in out.splitlines() if len(ln) > 3]


def catch_up(git, root, default, remote, plan_slug=None):
    """§4.3 — the copy-pasteable catch-up command, with its state caveat.

    Returns ``{"owner", "state", "dirty_paths", "commands", "note"}``. The
    operator's local default branch is NEVER moved by the land (§4.2's forbidden
    list), so this is the ONLY way their checkout stops being behind — and silence
    here is what makes the next plan cut its base from a stale ref (§1.3).

    The dirt is CLASSIFIED, not just detected. An orchestrator writes
    ``_plans/<slug>/`` in the primary checkout for the whole run (§8.a), so that
    directory is dirty at every land BY CONSTRUCTION — and the land's own
    ``record-plan`` commit touches exactly those paths, which is what would make
    ``merge --ff-only`` refuse. Reporting a bare "dirty" there would fire on every
    single land, and a warning that always fires is one nobody reads; worse, it
    would hide a genuinely modified source file among the noise. So the two are
    told apart and each gets the command that actually resolves it.
    """
    owner = owner_of_default(git, root, default)
    fetch = f"git fetch {remote}" if remote else "git fetch"
    if owner is None:
        # Nobody has it checked out, which is the one condition §4.2's
        # forbidden-list entry for `update-ref` does not cover.
        return {"owner": None, "state": "detached-everywhere", "note":
                f"no worktree holds refs/heads/{default}; updating the ref is safe here",
                "commands": [f"{fetch} && git update-ref refs/heads/{default} "
                             f"{remote or 'origin'}/{default}"]}
    dirty = _dirty_paths(git, owner)
    record = f"_plans/{plan_slug}/" if plan_slug else None
    record_only = bool(dirty) and record is not None and all(
        d.startswith(record) or d == record.rstrip("/") for d in dirty)
    ahead = git(["rev-list", "--count", f"{remote or 'origin'}/{default}..{default}"], owner)[1]
    if dirty and record_only:
        return {"owner": owner, "state": "dirty-plan-record-only", "dirty_paths": dirty,
                "commands": [f"git -C {owner} checkout -- {record.rstrip('/')}",
                             f"git -C {owner} fetch {remote or 'origin'} && "
                             f"git -C {owner} merge --ff-only {remote or 'origin'}/{default}"],
                "note": "the ONLY uncommitted paths are this plan's own record, which the "
                        "orchestrator writes in the primary checkout for the whole run "
                        "(§8.a) and which the land just committed a copy of. Discarding the "
                        "local copy first is safe — the landed commit is the canonical one — "
                        "and without it `merge --ff-only` refuses, exit 1."}
    if dirty:
        return {"owner": owner, "state": "dirty", "dirty_paths": dirty, "commands": [],
                "note": f"{owner} has uncommitted changes OUTSIDE this plan's record: "
                        f"{dirty[:10]}. Commit or stash them FIRST — `merge --ff-only` "
                        "fails exit 1 (`Your local changes to the following files would be "
                        "overwritten by merge`) when the landed commits touch a dirty file, "
                        "and `git pull --ff-only` fails identically."}
    if ahead and ahead != "0":
        return {"owner": owner, "state": "ahead-or-diverged", "dirty_paths": dirty,
                "commands": [],
                "note": f"{owner} is {ahead} commit(s) ahead of {remote or 'origin'}/"
                        f"{default}. You have local commits; merge or rebase them "
                        "deliberately — this contract does not automate that (§4.3)."}
    return {"owner": owner, "state": "clean", "dirty_paths": [],
            "note": "fast-forwards cleanly (measured)",
            "commands": [f"git -C {owner} fetch {remote or 'origin'} && "
                         f"git -C {owner} merge --ff-only {remote or 'origin'}/{default}"]}


def conflict(land_path, plan_dir, conflicted, full_status):
    """§4.6 — a conflict ALWAYS parks; the worktree is left with it intact.

    The recovery ends at ``land-resume``, NOT at a ``git push``: a hand-resolved
    conflict pushed by hand would land code with no current plan record, no gates
    and under an approval issued for a different tree.
    """
    return "\n".join([
        "LAND PARKED — merge conflict. Nothing was pushed and nothing was resolved for you.",
        "",
        f"Conflicted paths ({len(conflicted)}):",
        *(f"  {p}" for p in conflicted[:40]),
        "",
        "READ THE WHOLE `git status`, not just the U lines. A modify/delete conflict on "
        "one file STAGES SIBLING DELETIONS with no conflict marker (measured: "
        "`D _plans/<plan>/manifest.json` riding silently beside `UD _plans/<plan>/PLAN.html`).",
        "Current status:",
        *(f"  {ln}" for ln in full_status.splitlines()[:40]),
        "",
        "An append-only NDJSON conflict (`run.ndjson`, `_changelog.ndjson`) resolves as a "
        "UNION ordered by timestamp — never `--ours` (drops their records) and never "
        "`--theirs` (drops yours). Never regenerate `_plans_index.md` from this base: "
        "`--theirs` there silently deletes another plan's entry, exit 0.",
        "",
        "Recover:",
        f"  cd {land_path}",
        "  git status",
        "  git diff --name-only --diff-filter=U",
        "  # resolve, then:",
        "  git add <resolved paths> && git commit",
        "  # then hand control BACK to the land — do NOT push by hand:",
        f"  run.py land-resume {plan_dir}",
        "  # or abandon this land attempt:",
        "  git merge --abort",
        "",
        "`land-resume` re-enters the protocol at the top: it re-verifies the lease, "
        "re-runs record-plan, re-runs the full re-gate for a NEW gate digest, and — "
        "because a resolution always changes the plan-side tree — returns the plan to "
        "AWAITS_REVIEW for a FRESH human ack (§4.6, §5.2).",
    ])


def review(candidate, gates, plan_dir, land_path, root, prior=None, warning=None):
    """§5.1/§5.3 — the human land decision, bound to the exact revision approved.

    ``prior`` (the previously-approved candidate, if the ack was invalidated)
    makes this a DIFF view rather than a re-presentation of the whole plan: an
    approval flow that re-presents 40 files trains the operator to click through
    it, which is worse than no gate.

    Every option here is a command that RUNS. The abandon option once named
    ``run.py retire-plan``, a subcommand that does not exist — argparse exit 2 on
    the one human approval surface of the whole feature.
    """
    lines = ["LAND DECISION — the plan is merged and gated in an isolated worktree.",
             "NOTHING has been pushed. This is the last stop before the merge reaches the "
             "default branch.", ""]
    if prior:
        lines += ["THIS IS A RE-APPROVAL — your earlier ack was INVALIDATED. What changed:"]
        for key in ("plan_head", "main_head", "gate_digest"):
            if prior.get(key) != candidate.get(key):
                lines.append(f"  {key}: {str(prior.get(key))[:12]} -> "
                             f"{str(candidate.get(key))[:12]}")
        lines.append("")
    lines += [
        f"  plan head   {candidate['plan_head']}",
        f"  main head   {candidate['main_head']}   (the sha the merge was built on)",
        f"  merge       {candidate.get('merge_sha', '?')}",
        f"  gate digest {candidate['gate_digest']}",
        f"  land tree   {land_path}",
        "",
        f"Gates re-run on the merged tree ({len(gates)}):",
        *(f"  {g['gate_id']:<28} {g['outcome']}"
          + (f"  ({g['wall_s']} s)" if g.get("wall_s") is not None else "") for g in gates),
        *([warning] if warning else []),
        "",
        "Options:",
        f"  merge now  ->  run.py land-ack {plan_dir} --note '<why>'   then   "
        f"run.py land {plan_dir}",
        "  hold       ->  do nothing. The plan branch, its worktree and this land "
        "worktree all stay exactly as they are.",
        f"  abandon    ->  git -C {root} worktree remove {land_path}",
        "                 (discards ONLY this land candidate; the plan branch and its "
        "worktree are kept, local and remote — no work is ever deleted. Full plan "
        "retirement is §15's cleanup lifecycle, not a land option.)",
        "",
        "The ack is BOUND to the three shas above. If main moves, the plan branch moves, "
        "a hook rewrites the tree, a commit is amended, or any gate result changes, the "
        "ack is invalidated and the plan returns here (§5.2) — you will never land a "
        "candidate you did not see.",
    ]
    return "\n".join(lines)


def landed(merge_sha, pushed_to, catch_up_info, extra=None):
    """§4.3 + §4.8's operator-facing close-out. ``pushed_to`` is None for a
    no-remote land, which says PLAINLY that the plan landed locally only."""
    if pushed_to:
        head = [f"LANDED. {merge_sha[:12]} is now on {pushed_to}.", ""]
    else:
        head = ["LANDED LOCALLY ONLY — this repo has no remote. Nothing was pushed.",
                f"The merge commit is {merge_sha}, kept alive by the durable ref "
                f"{extra}.", "To bring it onto your default branch, run:",
                f"  git merge --no-ff {extra}", ""]
    body = ["Your own checkout was NOT touched: no local branch ref was written and no "
            "merge was performed in it (§4.2/§4.3). It is simply behind."]
    if catch_up_info.get("commands"):
        body += ["", "Catch up with:", *(f"  {c}" for c in catch_up_info["commands"])]
    if catch_up_info.get("note"):
        body += ["", f"NOTE: {catch_up_info['note']}"]
    return "\n".join(head + body)


def rejected(reason, output, land_path, branch, plan_dir, remote):
    """§4.7 — a rejected push parks and KEEPS the branch, local and remote.
    Nothing is cleaned up. The work exists only there."""
    return "\n".join([
        f"LAND PARKED — the push was REJECTED ({reason}).",
        "",
        "Nothing was cleaned up. The plan branch is KEPT, local and remote, and the land "
        "worktree is kept with the merge intact — the work exists only there.",
        "",
        "git said:",
        *(f"  {ln}" for ln in (output or "").splitlines()[:20]),
        "",
        "Recover:",
        f"  cd {land_path}",
        f"  git fetch {remote or 'origin'}          # see where the default branch is now",
        f"  run.py land-resume {plan_dir}   # re-sync, re-gate, re-approve, retry",
        "",
        f"Branch kept: {branch}. Do not delete it — §2.3 deletes a plan branch only "
        "AFTER a successful push, because a delete that runs first is how a rejected "
        "land loses its work.",
    ])


def preserved(path, plan_dir, extra):
    """§12.4 — a worktree teardown REFUSED, and what the operator must actually run.

    `teardown_worktree` judges `git status --porcelain --ignored` minus a cache
    allowlist, so the content that arms this is routinely git-IGNORED — a gate's
    own artefact written into the land worktree, say. Telling the operator to run
    plain `git status --porcelain` shows them a CLEAN tree, and the identical park
    on the next run: measured. The command here is the one that shows the file.

    A teardown can also report "preserved" because git itself failed, in which
    case there is no content at all and saying there is would send someone
    looking for it.
    """
    lines = [f"LAND PARKED — {path} was NOT removed, and nothing was forced (§12.4).", ""]
    if extra.get("error"):
        lines += [f"git refused the removal: {extra['error'].strip()}", "",
                  "That is a git failure, not uncommitted work. Read it, fix it, then:"]
    else:
        content = extra.get("content") or []
        lines += ["It holds content a removal would lose:", ""]
        lines += [f"    {c}" for c in content]
        lines += ["", "`!!` marks a git-IGNORED file — a gate's own artefact, most likely. "
                  "PLAIN `git status` WILL NOT SHOW IT, which is why the command below "
                  "carries `--ignored=matching`. Save or discard it, then:"]
        if extra.get("caches_ignored"):
            lines.append(f"({extra['caches_ignored']} cache path(s) were ignored and are "
                         "never a reason to refuse.)")
    lines += ["", f"  cd {path}", "  git status --porcelain --ignored=matching",
              f"  run.py land-resume {plan_dir}"]
    return "\n".join(lines)
