#!/usr/bin/env python3
"""repo-health: deterministic repo scorecard collector.

Stdlib only. Subcommands:
  collect  — detect repo shape, run every cheap deterministic check, write
             .claude/health/scorecard.json (expensive probes left "pending").
             Probe results already recorded against this exact clean tree are
             carried over; --fresh discards them.
  set      — record a probe result:  set sec.secrets-history pass "no leaks"
  render   — delegate to health_render.py: score, write HEALTH.html, append history
  fix-routes — one JSON line per check FAILING or UNMEASURED: the fix dispatch
             (subagent + prompt), an operator row, or the probe still to run.
             Blocking only unless --advisory; silence = measured, none failing

Statuses: pass / warn / fail / na / pending.  Tiers: blocking / advisory.
Every check id is emitted on EVERY run: a check that does not apply says "na"
and states what was looked for, never disappears.
Verdict: any blocking fail -> AT RISK; any advisory fail -> NEEDS ATTENTION;
else HEALTHY. See references/checks.md for the catalog and sources.
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

# Sibling modules. RE-EXPORTED BY BEING IMPORTED HERE — health_render.py and the tests
# reach several of these as health.<name>, so the facade is load-bearing and the
# `noqa: F401` below is what keeps a linter from silently deleting it. shape.py
# answers "what is in this repo", probes.py holds the checks a model runs by
# hand, card.py owns the scorecard file, quality_checks.py the two size gates;
# each imports common and none imports back, so the graph stays a tree.
from card import (CHECK_FIELDS, HEALTH_REL, health_dir, load_card,  # noqa: F401
                  recorded_probes, save_card, staleness, still_fresh, tree_key)
from common import (JUNK_PARTS, LAYERS, STATUSES, check,  # noqa: F401  (LAYERS re-exported)
                    excluded, parse_json, quality_excludes, read, run, typed,
                    typed_items, unmeasured, pending_if_unread, read_or_unread)
from probes import pending_checks, suite_command
from quality_checks import cq_file_size, cq_function_length
from routes import fix_rows
from shape import (MANIFEST_LOOKED_FOR, detect_shape, tracked_files,
                   unpinned_requirements)
from workflow_checks import workflow_checks

SENSITIVE = re.compile(r"(^|/)\.env$|\.pem$|(^|/)id_rsa|\.p12$|\.pfx$")


# ---------- static security + hygiene ----------

def sec_tracked_sensitive(files):
    hits = [f for f in files if SENSITIVE.search(f)]
    return check("sec.tracked-sensitive", "security", "blocking",
                 "No tracked secret-bearing files",
                 "fail" if hits else "pass",
                 ("tracked: " + ", ".join(hits)) if hits else "no .env/.pem/keys tracked",
                 "git rm --cached the file, ROTATE the credential, add to .gitignore"
                 if hits else "")


def ci_local_gate(shape):
    return check("ci.pre-commit", "ci", "advisory", "Local commit gate installed",
                 "pass" if shape["hooks"] else "warn",
                 "pre-commit config / githooks present" if shape["hooks"]
                 else "no local hook gate — regressions slip in between CI runs",
                 "" if shape["hooks"] else "adopt pre-commit (or a githooks dir wired via core.hooksPath)")


def ci_server_gate(repo, shape):
    """Is there a gate that does NOT run on the committer's machine?

    A client-side hook is advisory by construction: `--no-verify` skips it, a
    fresh clone never installs it, and a second machine may lack the tools it
    calls. It also cannot see the one failure that matters most — a suite that
    passes locally only because of an untracked file. So a green local gate is
    not evidence that the pushed tree is green.

    Deliberately `warn`, never `fail`: plenty of repos have a remote purely as a
    backup and need no CI. The judgement of whether this repo's branch is
    actually consumed by something — a deploy, a publish, another machine — is
    in references/no-ci.md, not in a regex.
    """
    rc, out = run(["git", "remote"], repo)
    if rc != 0:
        # NOT the same as "no remote", and `pending` rather than `na`: the command
        # failed, so this was never measured, and `na` is CLEAR to fix-routes.
        return check("ci.server-side-gate", "ci", "advisory",
                     "Gate runs where it cannot be bypassed", "pending",
                     "git remote failed here — whether this repo publishes "
                     "anywhere was never established")
    if not out.strip():
        return check("ci.server-side-gate", "ci", "advisory",
                     "Gate runs where it cannot be bypassed", "na",
                     "no git remote — nothing is published from this repo")
    if shape["workflows"]:
        return check("ci.server-side-gate", "ci", "advisory",
                     "Gate runs where it cannot be bypassed", "pass",
                     f"{len(shape['workflows'])} workflow(s) re-run the checks in a clean checkout")
    local = "local hooks are the only gate, and they are bypassable" if shape["hooks"] \
        else "no gate at all, local or server-side"
    return check("ci.server-side-gate", "ci", "advisory",
                 "Gate runs where it cannot be bypassed", "warn",
                 f"pushes to the remote are unverified — {local}",
                 "add one workflow that calls the SAME contract the local gate runs "
                 "(make check / make test), or route to ci-infrastructure-builder")


def sec_dep_updates(files, shape):
    if not shape["manifests"]:
        return check("sec.dep-update-config", "security", "advisory",
                     "Automated dependency updates", "na",
                     "no dependency manifest tracked — " + MANIFEST_LOOKED_FOR)
    # Name the filenames, do not just say "no config": Renovate reads a config
    # from several locations this list does not cover (.github/renovate.json,
    # .renovaterc*), so a bare "no dependabot/renovate config" is a claim the
    # check cannot back. Saying what was looked for is the same contract every
    # `na` in this collector already keeps.
    looked_for = (".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json")
    ok = any(f in files for f in looked_for)
    return check("sec.dep-update-config", "security", "advisory",
                 "Automated dependency updates",
                 "pass" if ok else "warn",
                 "dependabot/renovate configured" if ok
                 else "none of " + ", ".join(looked_for) + " tracked",
                 "" if ok else "add .github/dependabot.yml (or run the dep audit on a schedule)")


def is_junk(f, excl):
    """Build output that should never be tracked — at ANY depth.

    Deliberately not excluded(): that predicate answers "is this OUR source",
    and its dot-directory rule made a tracked `.venv/` invisible to the one
    check that names `.venv`. The scorecard then read "no venv/node_modules/pyc
    tracked" over a committed virtualenv (observed). A root-anchored
    startswith() hid `web/node_modules/` the same way. The repo's own quality
    excludes still win — that list is the repo saying which trees it does not
    police, and it is the same list ruff and the ratchet read.

    ponytail: `dist` and `build` as bare path components will one day flag a
    repo where `build/` is real source. It is an advisory warn naming the file,
    and the repo can silence it via [tool.claude-quality].exclude.
    """
    parts = f.split("/")
    if any(e.strip("/") in parts for e in excl):
        return False
    return any(p in JUNK_PARTS for p in parts[:-1]) or f.endswith((".pyc", ".DS_Store"))


def hyg_junk(repo, files):
    excl = quality_excludes(repo)
    junk = [f for f in files if is_junk(f, excl)]
    yield check("hyg.tracked-junk", "hygiene", "advisory", "No tracked build junk",
                "warn" if junk else "pass",
                ("tracked: " + ", ".join(junk[:8])) if junk else "no venv/node_modules/pyc tracked",
                "git rm --cached and extend .gitignore" if junk else "")
    scanned = [f for f in files if not excluded(f, excl)]
    large, unread = [], []
    for f in scanned:
        try:
            if (repo / f).stat().st_size > 5_000_000:
                large.append(f)
        except OSError:
            # A file that cannot be stat'ed — a dangling symlink, a permission
            # wall — was NOT measured; a bare `except: pass` used to let "no
            # tracked file over 5 MB" speak for it anyway.
            unread.append(f)
    yield unmeasured(
        check("hyg.large-files", "hygiene", "advisory", "No large binaries tracked",
              "warn" if large else "pass",
              ("over 5 MB: " + ", ".join(large)) if large
              else "no tracked file over 5 MB",
              "move to release assets / LFS or delete" if large else "",
              measured=scanned,
              nothing="every tracked file is inside the repo's own excludes — "
                      "no file was measured for size"),
        len(scanned) - len(unread), unread,
        f" ({len(unread)} file(s) could not be measured: "
        + ", ".join(sorted(unread)[:3]) + ")")


def hyg_docs(repo, files=None):
    """The three git-reading checks below all obey one rule: a git call that
    FAILED has measured nothing. Each used to default to an empty result and
    print it as a reading — "0 TODO/FIXME markers", "last commit -1 days ago",
    "no unmerged local branches, no stashes" — every one beside a green `pass`.
    `measured=` is what refuses that; see common.check().
    """
    unread = []
    rd = read_or_unread(repo, "README.md", unread, files)
    ok = len(rd.splitlines()) > 20 and "```" in rd
    yield pending_if_unread(
        check("hyg.readme", "hygiene", "advisory", "README with working quickstart",
              "pass" if ok else "warn",
              "README has substance and runnable commands" if ok
              else "README missing, thin, or without a runnable quickstart",
              "" if ok else "add a quickstart with the exact setup + test commands"),
        unread, "README quality")
    rc, out = run(["git", "grep", "-c", "-E", "TODO|FIXME|XXX",
                   "--", "*.py", "*.ts", "*.js"], repo)
    # rc 1 is git grep's own "no matches", a real zero; any OTHER non-zero is the
    # grep itself failing.
    todo = sum(int(x.rsplit(":", 1)[1])
               for x in out.splitlines()) if rc in (0, 1) else None
    yield unmeasured(
        check("hyg.todo-density", "hygiene", "advisory", "TODO/FIXME density",
              "warn" if todo and todo > 100 else "pass",
              f"{todo} TODO/FIXME markers in source",
              # No command fixes this — /declutter is read-only and has no
              # --fix. routes.py carries the same answer as an operator row.
              "triage them: each marker is work to schedule or a stale line "
              "to delete" if todo and todo > 100 else ""),
        todo is not None, [] if todo is not None else ["git grep"],
        "git grep failed here — the TODO/FIXME count was never taken")
    rc, out = run(["git", "log", "-1", "--format=%ct"], repo)
    days = int((time.time() - int(out)) / 86400) if rc == 0 and out.isdigit() else None
    yield unmeasured(
        check("hyg.activity", "hygiene", "advisory", "Recent activity",
              "warn" if days is not None and days > 90 else "pass",
              f"last commit {days} days ago"),
        days is not None, [] if days is not None else ["git log"],
        "git log gave no commit date — the age of the last commit was never read")
    yield hyg_unmerged_work(repo)
    yield hyg_notebook_outputs(repo, tracked_files(repo))


def hyg_unmerged_work(repo):
    """Two INDEPENDENT questions — unmerged branches, and stashes.

    They get one check but two answers, and the guard has to be per-answer. A
    single `measured=(rcb == 0 and rcs == 0)` over both meant a detached HEAD
    (`git branch --no-merged main` exits 128 on the ordinary CI checkout) threw
    away a correctly measured stash finding and reported "never looked for"
    instead — a guard against unmeasured results hiding a measured one, which is
    worse than the defect it was added for (found by review).
    """
    rc, head = run(["git", "symbolic-ref", "--short", "HEAD"], repo)
    rcb, out = run(["git", "branch", "--no-merged", head if rc == 0 else "main"], repo)
    branches = [b.strip() for b in out.splitlines() if b.strip()] if rcb == 0 else []
    rcs, out = run(["git", "stash", "list"], repo)
    stashes = len(out.splitlines()) if rcs == 0 and out else 0
    asked = [q for q, ok in (("unmerged branches", rcb == 0),
                             ("stashes", rcs == 0)) if ok]
    missed = [q for q in ("unmerged branches", "stashes") if q not in asked]
    bad = bool(branches or stashes)
    return unmeasured(
        check("hyg.unmerged-work", "hygiene", "advisory", "No forgotten work",
              "warn" if bad else "pass",
              (f"unmerged branches: {', '.join(branches[:5])}; stashes: {stashes}")
              if bad else "no unmerged local branches, no stashes",
              "merge or delete the branch; pop or drop the stash" if bad else ""),
        asked, missed,
        f" (git could not list {' or '.join(missed)} here — NOT looked for)")


def hyg_notebook_outputs(repo, files):
    nbs = [f for f in files if f.endswith(".ipynb")]
    if not nbs:
        return check("hyg.notebook-outputs", "hygiene", "advisory",
                     "Notebook outputs stripped", "na", "no notebooks tracked")
    dirty = []
    for f in nbs:
        # Every step type-guarded, not just the parse: `[]` is valid notebook JSON
        # and has no .get(), and a `cells` list can hold anything at all. Both
        # raised AttributeError straight out of collect and killed the whole run.
        nb = parse_json(read(repo, f))
        if nb is None:
            dirty.append(f + " (unreadable)")
        elif any(typed(cell, dict, {}).get("outputs")
                 for cell in typed_items(nb.get("cells"))):
            dirty.append(f)
    return check("hyg.notebook-outputs", "hygiene", "advisory",
                 "Notebook outputs stripped",
                 "warn" if dirty else "pass",
                 ("outputs committed in: " + ", ".join(dirty[:5])) if dirty
                 else f"{len(nbs)} notebooks, all outputs stripped",
                 "nbstripout the files; secrets hide in output JSON" if dirty else "")


# ---------- AI readiness ----------

def ai_agents_doc(repo, files):
    doc = next((f for f in ("AGENTS.md", "CLAUDE.md") if f in files), "")
    unread = []
    body = read_or_unread(repo, doc, unread, files) if doc else ""
    has_cmds = bool(re.search(r"```|(^|[\s`])(make|pytest|npm|uv|ruff|python3?)\b", body))
    status = "pass" if doc and has_cmds else ("warn" if doc else "fail")
    return pending_if_unread(check("ai.agents-md", "ai_readiness", "advisory", "Agent instructions file",
                 status,
                 (f"{doc}: {len(body.splitlines())} lines"
                  + ("" if has_cmds else ", no runnable commands")) if doc
                 else "no AGENTS.md or CLAUDE.md at repo root",
                 "" if status == "pass"
                 else "add AGENTS.md: build/test commands, style deltas, conventions (~150 lines)"),
                 unread, "agent instructions")


def ai_verify(repo, shape, files=None):
    unread = []
    suite = suite_command(repo, shape, unread, files)
    gate = shape["hooks"] or re.search(r"^(check|verify|lint):",
                                       read_or_unread(repo, "Makefile", unread, files), re.M)
    return pending_if_unread(
        check("ai.verify-command", "ai_readiness", "advisory",
              "One-command verification loop",
              "pass" if suite and gate else ("warn" if suite or gate else "fail"),
              f"suite: {suite or 'none'}; lint gate: {'hooks/make target' if gate else 'none found'}",
              "" if suite and gate
              else "add a single `make check` (lint+types+tests) agents can run"),
        unread, "verification loop")


def ai_lockfiles(repo, shape, files=None):
    locks = ", ".join(Path(x).name for x in shape["locks"])
    if not shape["manifests"]:
        # A lockfile with nothing to lock is not "not applicable" — either the
        # manifest is untracked or shape detection is wrong, and both are worth
        # a line on the scorecard rather than silence.
        if shape["locks"]:
            return check("ai.lockfiles", "ai_readiness", "advisory",
                         "Deterministic dependencies", "warn",
                         f"lockfile committed ({locks}) but no manifest detected — "
                         + MANIFEST_LOOKED_FOR,
                         "track the manifest the lockfile was resolved from")
        return check("ai.lockfiles", "ai_readiness", "advisory",
                     "Deterministic dependencies", "na",
                     "no dependency manifest and no lockfile tracked — "
                     + MANIFEST_LOOKED_FOR)
    # shape["manifests"], not the raw file list: the detector has already
    # decided which files count, and a second list is how the two predicates
    # drifted apart in the first place.
    unpinned, unread = unpinned_requirements(repo, shape["manifests"], files)
    ok = bool(shape["locks"]) and not unpinned
    bad = ("unpinned requirements: " + ", ".join(unpinned)) if unpinned \
        else "no lockfile committed"
    # pending_if_unread, not unmeasured: ONE unopenable manifest among several
    # invalidates "no unpinned requirements found" entirely — the file that
    # would not open is exactly the one that could have held the unpinned spec,
    # so a `warn`-with-note stood over a conclusion that was never reached (an
    # unlinked tracked pyproject.toml beside uv.lock read "lockfile: uv.lock —
    # pass"; found by review). Same rule `test.suite` already keeps.
    return pending_if_unread(
        check("ai.lockfiles", "ai_readiness", "advisory", "Deterministic dependencies",
              "pass" if ok else "warn",
              ("lockfile: " + locks) if ok else bad,
              "" if ok else "commit a lockfile (uv.lock / package-lock.json) or pin versions"),
        # `not ok` — NOT `bool(unpinned)`. Both halves of a non-clean verdict are
        # read from somewhere the unread manifest cannot reach: `unpinned` comes
        # from the manifests that DID open, and "no lockfile committed" comes from
        # the tracked-file list. The narrower guard demoted a repo with NO lockfile
        # to `pending` over an unrelated unreadable manifest, which is the same
        # score inflation one line up (review).
        unread, "manifest pin status", finding_stands=not ok)


# ---------- orchestration ----------

def collect(repo, fresh=False):
    files = tracked_files(repo)
    if not files:
        sys.exit("not a git repo (or nothing tracked): " + str(repo))
    shape = detect_shape(repo, files)
    checks = list(workflow_checks(repo, shape))
    checks += [sec_tracked_sensitive(files), sec_dep_updates(files, shape),
               ci_local_gate(shape), ci_server_gate(repo, shape),
               cq_file_size(repo, files), cq_function_length(repo, shape)]
    checks += list(hyg_junk(repo, files)) + list(hyg_docs(repo, files))
    checks += [ai_agents_doc(repo, files), ai_verify(repo, shape, files),
               ai_lockfiles(repo, shape, files)]
    probes = list(pending_checks(repo, shape, files))
    checks += probes
    key = tree_key(repo)
    # Only a PROBE that came out pending is a candidate. Two conditions, not one:
    # a deterministic check has just been recomputed from the tree and its fresh
    # answer wins — and since one of them can now come out `pending` too (it read
    # nothing it needed: see common.unmeasured), `status == "pending"` alone would
    # let a recorded `pass` from a run that COULD read the file stand in for the
    # measurement this run could not take. That is the stale-artifact shape.
    probe_ids = {c["id"] for c in probes}
    kept = recorded_probes(repo, key) if not fresh else {}
    kept = {c["id"]: kept[c["id"]] for c in checks
            if c["status"] == "pending" and c["id"] in kept and c["id"] in probe_ids}
    for c in checks:
        old = kept.get(c["id"])
        if old:
            c["status"], c["detail"] = old["status"], old["detail"]
            if old.get("recorded"):     # carried, so the age bound keeps counting
                c["recorded"] = old["recorded"]
    rc, commit = run(["git", "rev-parse", "--short", "HEAD"], repo)
    save_card(repo, {"repo": repo.name, "repo_path": str(repo),
                     "commit": commit if rc == 0 else "?", "tree": key,
                     "date": time.strftime("%Y-%m-%d"), "checks": checks})
    pend = [c["id"] for c in checks if c["status"] == "pending"]
    print(f"wrote {health_dir(repo) / 'scorecard.json'}")
    if kept:
        print(f"kept {len(kept)} recorded probe(s) for tree {key[:12]}: "
              + ", ".join(sorted(kept)))
    print("pending probes: " + (", ".join(pend) or "none"))


def set_result(repo, cid, status, detail):
    # The operator types this status by hand. An unknown one used to be written
    # straight into the card, where it counted as a measured result, scored as
    # neither pass nor fail, and took render down with `KeyError: 'passs'`.
    # Refused at the boundary where a human can still fix the typo; certified()
    # is the net for a card that already holds one.
    if status not in STATUSES:
        sys.exit(f"unknown status {status!r} — one of: " + ", ".join(STATUSES))
    card = load_card(repo)
    for c in card["checks"]:
        if c["id"] == cid:
            c["status"] = status
            # When, not just what: a world-dependent probe result is only allowed
            # to survive a re-collect while it is recent (see still_fresh).
            c["recorded"] = date.today().isoformat()
            if detail:
                c["detail"] = detail
            save_card(repo, card)
            print(f"{cid} <- {status}")
            return
    # Print the catalog: fix routes are keyed on these ids, so a rename or
    # a typo has to fail legibly rather than send someone grepping.
    sys.exit(f"no check with id {cid}\nknown ids: "
             + ", ".join(sorted(c["id"] for c in card["checks"])))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["collect", "set", "render", "fix-routes"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--repo", default=".", help="target repo (default: cwd)")
    ap.add_argument("--fresh", action="store_true",
                    help="collect: discard recorded probe results instead of "
                         "carrying the ones taken on this exact clean tree")
    ap.add_argument("--advisory", action="store_true",
                    help="fix-routes: ALSO emit the advisory findings (SKILL.md "
                         "step 5's go-ahead); default is blocking only")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    if ns.cmd == "collect":
        collect(repo, fresh=ns.fresh)
    elif ns.cmd == "set":
        if len(ns.args) < 2:
            sys.exit("usage: health.py set <check-id> <status> [detail] [--repo PATH]")
        set_result(repo, ns.args[0], ns.args[1], " ".join(ns.args[2:]))
    elif ns.cmd == "fix-routes":
        # One JSON object per line: greppable, and each line is a complete
        # dispatch on its own. Silence means every check in scope was MEASURED
        # and none is failing; both ways of not knowing print instead. Never
        # collected exits non-zero with the reason on stderr (not the traceback
        # this used to raise); a `pending` check gets its own "probe" row.
        # Filtered out with the passing ones, each was a false all-clear.
        if not (health_dir(repo) / "scorecard.json").exists():
            sys.exit(f"no scorecard for {repo} — run: health.py collect --repo {repo}")
        card = load_card(repo)
        # A dispatch computed from a scorecard collected against a DIFFERENT or
        # dirty tree can send an agent to fix something already fixed, or miss
        # what is broken now. The page says so on its masthead; these rows said
        # nothing at all (review). Carried per-ROW and not as a
        # banner line, because the contract above is that each line is a
        # complete dispatch on its own — a banner would break `while read`.
        stale = staleness(repo, card)
        for row in fix_rows(card, advisory=ns.advisory):
            print(json.dumps({**row, "stale": stale} if stale else row))
    else:
        import health_render as render
        render.render(repo)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(sys.argv[1:])
