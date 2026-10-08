"""What the review gate reviews -- the surface, separated from the reviewing.

Split out of ``llm_review_gate`` at the 500-LOC bound, on the seam that was
already there: this module answers WHAT is under review, that one answers what
the reviewer said about it. Every rule here exists because the gate once
reported green on a surface it could not describe.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# Re-exported so `llm_review_surface.<name>` keeps working for every caller
# and test: the split moved WHERE these live, not what this module exposes.
from llm_review_payload import (  # noqa: F401
    is_captured_output,
    split_untracked,
    untracked_block,
    _sized,
)

#: Mirror ``llm_review_gate``'s exit codes; importing them back would be a cycle.
PASS, FINDINGS, INDETERMINATE = 0, 1, 2

# Above this the working tree is too dirty for a review to be honest about what
# it covered, and the gate says so instead of quietly reviewing a subset.
UNTRACKED_MAX = 60


def untracked_files(cwd, plan_dir=None, session=None, scope=(), exclude=()):
    """Paths git calls NEW — present, not ignored, absent from `git diff HEAD`.
    `None` when git could not answer at all (not a work tree, git missing).

    THE BLIND SPOT THIS CLOSES, measured 2026-08-15 on S07 of the adaptive-routing
    plan: `git diff HEAD` excludes untracked files, so `render_report.py` — 173
    new lines — was invisible to all three review rounds while the gate reported a
    clean PASS each time. Staging it and reading it found a real bug on the first
    look. Any brand-new file could ship unreviewed and the gate's green said
    nothing about it.
    """
    try:
        proc = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                              cwd=cwd, capture_output=True, text=True, check=False,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    kept, dropped = [], []
    for path in sorted(p for p in (ln.strip() for ln in proc.stdout.splitlines()) if p):
        reason = _out_of_surface(path, plan_dir, session, scope, exclude)
        (dropped.append((path, reason)) if reason else kept.append(path))
    return kept, dropped


# Written by run.py, read by run.py, authored by nobody. Handing these to a
# reviewer costs a paragraph of "this is generated output" per file and buys
# nothing: measured 2026-08-20 on s03, 9 of 21 listed new files were the
# harness's own state. (This comment stood here TWICE, verbatim; the orphaned
# copy is dropped rather than carried.)
_BOOKKEEPING = ("/_verify_state/", "/_worktrees/")


def parse_scope(raw):
    """`--scope`/PLAN_EXECUTE_REVIEW_SCOPE -> a list of path prefixes, or []."""
    import re
    return [_norm(x) for x in re.split(r"[,:]", raw or "") if x.strip()]


def _norm(path):
    """Drop a leading `./` PREFIX -- never lstrip the character set, which turns
    `.file-size-exceptions` into `file-size-exceptions` and silently drops a
    root dotfile out of scope (found in the first manifest this produced)."""
    import re
    return re.sub(r"^(\./)+", "", str(path).strip())


def in_scope(path, scope):
    """True when `scope` is empty (review everything) or `path` sits under one.

    WHY A SCOPE EXISTS AT ALL, measured 2026-08-20 across two concurrent plans in
    one checkout: the surface was the whole tree, so each session's review read
    the OTHER session's uncommitted source too. Three rework attempts were spent
    on findings in files the reviewed session never touched -- real defects,
    charged to the wrong budget, invisible to whoever caused them. `_out_of_surface`
    below handles `_plans/**`; this handles SOURCE directories, which is where
    those three attempts went.

    Prefix on whole path components, never a bare startswith: `skills/x` must not
    swallow `skills/xy`, and that failure is silent in the dangerous direction.
    """
    if not scope:
        return True
    p = _norm(path)
    return any(p == s or p.startswith(s.rstrip("/") + "/") for s in scope)


def _out_of_surface(path, plan_dir=None, session=None, scope=(), exclude=()):
    """Why this untracked path is not THIS session's surface, or None to review it.

    Two exclusions, and both are about AUTHORSHIP, never about noise:

    * harness bookkeeping -- machine-written state nobody authored.
    * another plan's or another session's `_evidence/` -- measured 2026-08-20 on
      s03, where 5 of 6 blocking findings were against already-EXECUTED evidence
      scripts belonging to a closed sibling plan and to a finished session of
      this one. A session cannot fix those: editing a script after it produced a
      recorded result destroys the thing that makes it evidence. Holding s03 to
      them was the gate mis-attributing, not detecting.

    The findings themselves stay valuable, so the caller PRINTS everything this
    drops. An exclusion that silently shrinks the surface is how "reviewed
    clean" starts lying, which is the same class as the empty-surface bug above.

    With no plan context (plan_dir unset) only the bookkeeping rule applies --
    the scoping rule cannot be evaluated, so it does not fire.
    """
    if any(marker in f"/{path}" for marker in _BOOKKEEPING):
        return "harness bookkeeping"
    if not in_scope(path, scope):
        return f"outside this session's review_scope ({', '.join(scope)})"
    if excluded(path, exclude):
        # Contract §5.1b/§10.1: the LAND re-gate runs on the tree that will be
        # pushed, which by construction now contains the plan's own RECORD commit
        # -- `PLAN.html`, `spec.json`, `run.ndjson`, every closeout. Generated
        # content nobody authored and nobody can act on, and on this repo's own
        # plan it is several hundred KB, which is the single likeliest way to
        # push a reviewer into its indeterminate exit.
        return f"excluded from the review surface ({', '.join(exclude)})"
    if not plan_dir or not path.startswith("_plans/"):
        return None
    plan = plan_dir.strip("/").split("/")[-1]
    if not path.startswith(f"_plans/{plan}/"):
        return "another plan's files"
    if session and "/_evidence/" in path:
        owner = path.split("/_evidence/", 1)[1].split("/")[0]
        if owner != session:
            return f"session {owner}'s evidence, not {session}'s"
    return None


def _pathspec(scope, exclude=()):
    """`-- <dir>... :(exclude)<dir>...` for git, or nothing. Git matches a
    directory pathspec on whole components, so `skills/x` does not match
    `skills/xy/` -- the same rule `in_scope` applies to the untracked list, kept
    identical on purpose. `:(exclude)` is git's own magic prefix; `plan_ship`
    already relies on it for the same job."""
    parts = [s.rstrip("/") for s in scope] + [f":(exclude){e.rstrip('/')}" for e in exclude]
    return (["--"] + parts) if parts else []


def excluded(path, exclude):
    """True when `path` sits under one of `exclude`. Whole components, like
    `in_scope`, and for the same reason: `_plans/x` must not swallow `_plans/xy`."""
    if not exclude:
        return False
    p = _norm(path)
    return any(p == e.rstrip("/") or p.startswith(e.rstrip("/") + "/") for e in exclude)


def diff_stat(cwd, base=None, scope=(), exclude=()):
    """One line per changed file for the reviewed diff, or None if git failed.

    ``base`` is a git ref. With it the surface is ``git diff <base>`` — base
    against the WORKING TREE, so it covers the session's COMMITS as well as any
    uncommitted edit. Without it the surface is ``git diff HEAD``, which sees
    nothing at all once a session has committed its work.
    """
    argv = ["git", "diff", "--name-only", base or "HEAD"] + _pathspec(scope, exclude)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return sorted(ln.strip() for ln in proc.stdout.splitlines() if ln.strip())


def write_diff_file(cwd, base, out_path, scope=(), exclude=()):
    """Write the ACTUAL diff to a file, or None if git failed.

    THE REVIEWER MUST NOT BE HANDED A GIT COMMAND. Measured 2026-08-20: the gate
    named `git diff 4d56d6dc0` -- 33 files including six production modules --
    and the reviewer answered "the committed range is two documentation files",
    which is exactly `git diff origin/main`. It re-derives its own range and
    disregards the ref. Once a session pushes to main (post_session
    `commit-push` makes that routine) its work is invisible, and an empty
    findings array reads as PASS on a review of nothing.

    A file has no range to re-derive.
    """
    argv = ["git", "diff", base or "HEAD"] + _pathspec(scope, exclude)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(proc.stdout)
    except OSError:
        return None
    return out_path


def surface_size(cwd, base, scope, new_files, exclude=()):
    """(files, added, deleted) over the reviewed surface, or None if git failed.
    Untracked files count every line as added -- the reviewer reads them whole."""
    argv = ["git", "diff", "--numstat", base or "HEAD"] + _pathspec(scope, exclude)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    files = added = deleted = 0
    for ln in proc.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    for p in new_files:
        try:
            added += sum(1 for _ in open(os.path.join(cwd, p), "rb"))
            files += 1
        except OSError:
            continue
    return files, added, deleted


# Human-review defect detection by PR size, 50k-PR study (Propel, 2025):
# <=100 lines 87%, 101-300 78%, 301-600 65%, 601-1000 42%, 1000+ 28%. LLM
# reviewers sit LOWER and fall with context (SWE-PRBench 2026: 15-31% recall
# per pass, diff-only). The banner exists so a large surface is READ as what it
# is: a review that will sample, whose later-round findings are the sample.
_BANDS = ((100, "~87%"), (300, "~78%"), (600, "~65%"), (1000, "~42%"), (10**9, "~28%"))


def size_banner(size):
    files, added, deleted = size
    lines = added + deleted
    band = next(b for cap, b in _BANDS if lines <= cap)
    msg = (f"[llm-review-gate] surface size = {files} file(s), +{added}/-{deleted} "
           f"({lines} lines) -- human detection band {band}; one LLM pass samples "
           f"LOWER and degrades with size")
    if lines > 600:
        msg += (". EXPECT findings on a rework in code untouched by the fix: that is the "
                "sample, not a regression. Smaller sessions converge; this one may not.")
    return msg


SCOPE_REASON = "outside review_scope"
EXCLUDE_REASON = "excluded from the review surface"


def _report_dropped(cwd, base, scope, exclude, changed):
    """Name every MODIFIED file the scope or the exclusion dropped, with the
    reason that actually dropped it.

    NO SILENT CAPS on the diff half: a reader who sees only the scoped count
    cannot tell a correct scope (the other plan's files) from a wrong one (this
    session's own work, edited outside its declared scope, now unreviewed). An
    exclusion is the same hazard pointed the other way, so it is named too.

    And with BOTH set they are DIFFERENT reasons. Labelling every dropped path
    "outside review_scope" tells an auditor the wrong thing about why the surface
    shrank. One extra scope-only diff separates them; when only one of the two is
    set there is nothing to separate and no second diff is run.

    ACCURATE ABOUT ITS OWN REACH: no shipped caller sets both today — `verify`
    passes a scope, the land re-gate passes only the exclusion — so this branch
    is defensive rather than load-bearing, and its cost is zero until something
    does set both.
    """
    scoped = set(diff_stat(cwd, base, scope, ()) or []) if scope and exclude else None
    for path in sorted(set(diff_stat(cwd, base) or []) - set(changed or [])):
        if scoped is None:
            why = SCOPE_REASON if scope else EXCLUDE_REASON
        else:
            why = SCOPE_REASON if path not in scoped else EXCLUDE_REASON
        print(f"[llm-review-gate] not this session's surface ({why}): {path}",
              file=sys.stderr)


def _empty_surface_msg(level, surface, scope, exclude):
    """The INDETERMINATE text for a surface with nothing in it — naming whichever
    of the two filters was applied.

    NO SILENT SHRINKING on this branch either. The land re-gate excludes
    `_plans/<slug>/` and sets NO scope, so a land whose surface came out empty
    BECAUSE of the exclusion used to say nothing about it at all, and the
    operator was sent looking for a missing --base that was never the cause.
    """
    msg = (f"llm-review-{level}: INDETERMINATE — the reviewed surface is EMPTY "
           f"({surface} has 0 changed files and there are 0 untracked files), so a "
           f"green verdict would say nothing about the session's work. If the session "
           f"COMMITTED its changes, pass --base <ref> (or PLAN_EXECUTE_REVIEW_BASE) "
           f"naming the commit it started from.")
    if scope:
        msg += (f" NOTE: review_scope {', '.join(scope)} was applied -- if the "
                f"session's work lives outside it, the SCOPE is wrong; an empty "
                f"scoped surface is refused here rather than passed.")
    if exclude:
        msg += (f" NOTE: {', '.join(exclude)} was EXCLUDED from the surface; if the "
                f"session's work lives there, the exclusion is wrong.")
    return msg


def resolve_surface(level, cwd, base, plan_dir=None, session=None, scope=(), exclude=()):
    """-> (new_files, base) to review, or (None, exit_code) when it must refuse.

    THE REVIEWED SURFACE IS DECLARED, NEVER ASSUMED (see `untracked_files`).
    Every refusal below is INDETERMINATE rather than a pass, because in each
    the gate cannot say what it reviewed — and a review of an unknown or empty
    subset reporting green is the exact silent-green this file exists to refuse.
    """
    # A MISSING BASE IS NOT A SMALLER SURFACE, IT IS A DIFFERENT ONE. With no
    # base the surface falls back to `git diff HEAD`: for a session that already
    # COMMITTED, that holds whatever happens to be dirty and none of its work.
    # The emptiness guard below does not catch it -- one unrelated dirty file
    # makes the surface non-empty, and a clean review of the wrong files is a
    # genuine PASS. The check is not "did it refuse on empty", it is "was the
    # surface the session's WORK or merely non-empty" (2026-08-20).
    if plan_dir and not base:
        journal = os.path.join(plan_dir, "run.ndjson")
        if not os.path.exists(journal):
            print(f"llm-review-{level}: INDETERMINATE — no --base, and {journal} does "
                  f"not exist, so the session's starting commit cannot be derived. The "
                  f"surface would silently become `git diff HEAD`, which is NOT this "
                  f"session's work. A plan worktree is the usual cause: it gets the "
                  f"plan's TRACKED files and not its gitignored run.ndjson. Copy the "
                  f"journal into the tree, or pass --base explicitly.", file=sys.stderr)
            return None, INDETERMINATE
        # The journal exists but names no dispatch for this session: legacy, and
        # refusing every one of those would fail closed on healthy plans. Loud,
        # not fatal — silence is the part that has to go.
        print(f"[llm-review-gate] NO REVIEW BASE for session {session!r}: the surface is "
              f"`git diff HEAD`. Any work this session already COMMITTED is NOT in it.",
              file=sys.stderr)
    listed = untracked_files(cwd, plan_dir, session, scope, exclude)
    new_files, dropped = (None, []) if listed is None else listed
    for path, reason in dropped:
        # NO SILENT CAPS. A surface that shrinks without saying so reads as
        # "reviewed everything" when it did not.
        print(f"[llm-review-gate] not this session's surface ({reason}): {path}",
              file=sys.stderr)
    if new_files is None:
        print(f"llm-review-{level}: INDETERMINATE — `git ls-files --others "
              f"--exclude-standard` failed in {cwd!r}, so the gate cannot tell "
              f"whether the change contains NEW files that `git diff HEAD` omits. A "
              f"whole new module can hide there. Run the gate in a git work tree.",
              file=sys.stderr)
        return None, INDETERMINATE
    if len(new_files) > UNTRACKED_MAX:
        print(f"llm-review-{level}: INDETERMINATE — {len(new_files)} untracked "
              f"files in {cwd!r}, over the {UNTRACKED_MAX} a review can honestly "
              f"cover. Commit, ignore or remove them so the reviewed surface is "
              f"knowable. First 10:\n  %s" % "\n  ".join(new_files[:10]), file=sys.stderr)
        return None, INDETERMINATE
    changed = diff_stat(cwd, base, scope, exclude)
    if scope or exclude:
        _report_dropped(cwd, base, scope, exclude, changed)
    surface = f"`git diff {base}`" if base else "`git diff HEAD`"
    if changed is None and base:
        # An unknown/unreachable base ref reviews NOTHING and would report green.
        print(f"llm-review-{level}: INDETERMINATE — `git diff {base}` failed "
              f"in {cwd!r}. Pass a ref this repository can resolve.",
              file=sys.stderr)
        return None, INDETERMINATE
    if changed is None:
        # No base asked for and git could not diff HEAD — a repo with no commits
        # yet. That is a legitimate all-new-files surface; the emptiness guard
        # below still refuses if there is nothing to review at all.
        changed = []
    if not changed and not new_files:
        # A check that returns clean because its input was empty is worse than
        # no check. This fires when a session COMMITTED its work and no --base
        # was passed: `git diff HEAD` is empty, the reviewer is handed nothing,
        # and an empty findings array reads as a PASS. Measured 2026-08-20 on
        # the model-portability plan, where every session hit it.
        print(_empty_surface_msg(level, surface, scope, exclude), file=sys.stderr)
        return None, INDETERMINATE
    print(f"[llm-review-gate] reviewed surface = {surface} "
          f"({len(changed)} changed file(s)) + {len(new_files)} untracked file(s)"
          + (f", SCOPED to {', '.join(scope)}" if scope else ", scope=WHOLE TREE")
          # NO SILENT SHRINKING — the same rule the dropped-untracked loop above
          # follows. An exclusion nobody is told about reads as "reviewed
          # everything" when it did not.
          + (f", EXCLUDING {', '.join(exclude)}" if exclude else "")
          + (":\n  " + "\n  ".join(new_files) if new_files else ""))
    report_size(cwd, base, scope, new_files, exclude)
    return new_files, base


def report_size(cwd, base, scope, new_files, exclude=()):
    """Print the captured-output list and the detection band.

    THE BAND MODELS READ EFFORT, so it counts what the reviewer is ASKED to
    read. Counting captured output here reported a surface nobody reviews and
    pushed the estimate a whole band down: 1,116 lines (~28%) against 823
    lines of actual source (~42%) on s09b, 2026-08-23.
    """
    source, artifacts = split_untracked(cwd, new_files)
    if artifacts:
        print(f"[llm-review-gate] {len(artifacts)} untracked path(s) are CAPTURED "
              f"RUN OUTPUT: listed for the reviewer, not deep-read, and not "
              f"counted toward the surface size:\n"
              + "\n".join(_sized(cwd, p) for p in artifacts))
    size = surface_size(cwd, base, scope, source, exclude)
    if size:
        print(size_banner(size))


def prepare_surface(args, scope, exclude=()):
    """-> (led, base, changed, new_files, diff_file, expected), or an exit code.

    THE LEDGER IS RESOLVED BEFORE THE DIFF IS WRITTEN, because on a rework it
    DECIDES the diff. Ordered the other way (as it was until 2026-08-22) the
    delta could only ever be advice appended to the prompt, while the reviewer
    still received every file in the session's surface and sampled wherever it
    liked -- which is why the loop had no termination property.
    """
    new_files, base = resolve_surface(args.level, args.cwd, args.base.strip() or None,
                                      args.plan_dir.strip() or None,
                                      args.session.strip() or None, scope, exclude)
    if new_files is None:
        return base
    changed = diff_stat(args.cwd, base, scope, exclude) or []
    import llm_review_ledger as ledger  # local: keeps this module import-light
    # One ledger per (session, LEVEL, REVIEWER). A session can declare two review
    # levels -- `land` always does, because it re-runs the UNION of every session's
    # gates -- and on a shared ledger the second gate to run reads the first one's
    # surface record, sees no file changed since it, and returns INDETERMINATE
    # without ever reviewing. Measured 2026-08-23: four land runs, llm-review-medium
    # skipped in under 1.5s every time (fixed in 969feba).
    # THE REVIEWER IS THE THIRD COMPONENT, the same bug one axis over:
    # `cross-family-review-medium` and `llm-review-medium` share the LEVEL string,
    # so the second gate to run would inherit the first's surface record and skip
    # its review -- a cross-family gate that never ran. It is the DECLARED reviewer
    # (`args.reviewer`), NEVER the family that runs (`args.family`): the harness
    # flip is MANY-TO-ONE -- with the opt-in ON it maps BOTH values to claude, and
    # the two argvs differ by `--reviewer codex` alone, so declared is the only axis.
    sid, rev = args.session.strip(), getattr(args, "reviewer", "claude")
    led = ledger.context(args.plan_dir.strip(), f"{sid}.{args.level}.{rev}" if sid else "",
                         args.cwd, changed + list(new_files),
                         ledger.parse_floor(args.blocking_severity))
    focus, changed, new_files, note = ledger.narrow_to_delta(led, changed, new_files)
    if note:
        print(note, file=sys.stderr if focus == set() else sys.stdout)
    if focus == set():
        return (FINDINGS if led.priors else
                PASS if ledger.reviewed_unchanged(led) else INDETERMINATE)
    diff_file = write_diff_file(
        args.cwd, base,
        os.path.join(tempfile.gettempdir(), f"plan-execute-review-{os.getpid()}.diff"),
        sorted(focus) if focus else scope, exclude)
    if diff_file:
        print(f"[llm-review-gate] handed the reviewer {diff_file} "
              f"({len(changed)} changed + {len(new_files)} untracked file(s))")
    # COUNT WHAT THE REVIEWER WAS ASKED TO READ. Captured output is listed,
    # not deep-read, so counting it here makes `surface_attested` compare the
    # reviewer's honest count against a bigger number, and every
    # evidence-bearing session goes INDETERMINATE (caught end-to-end, not by
    # the unit tests, 2026-08-23).
    read_n = len(changed) + len(split_untracked(args.cwd, new_files)[0])
    return led, base, changed, new_files, diff_file, read_n


# What the level buys. Verbatim from the ladder in references/verify-gates.md,
# which is the binding definition of these three gates. It used to reach the
# reviewer as the code-review skill's `args`; that skill reviews the REPO, not
# the diff, so the gate no longer calls it and states the depth itself.
_DEPTH = {
    "low": "Report only FEW, HIGH-CONFIDENCE findings. Do not raise anything "
           "you cannot stand behind.",
    "medium": "Before you report a candidate finding, VERIFY it by running the "
              "code. Report what you verified.",
    "high": "Cover the change BROADLY. You may raise an UNCERTAIN finding, but "
            "say plainly that it is uncertain.",
}


def depth_line(level):
    """The one sentence that makes `--level` mean something in the prompt.

    An unknown level must not silently review at some other depth, so it gets
    the strictest instruction rather than a default or an empty string.
    """
    return _DEPTH.get(level, _DEPTH["high"])
