#!/usr/bin/env python3
"""Shared primitives for the repo-health collector.

A leaf module on purpose: health.py and workflow_checks.py both need these, and
routing them through health.py would make the import graph a cycle. Nothing here
imports a sibling.
"""
import fnmatch
import json
import subprocess

FIRST_PARTY = ("actions/", "github/")
# Build output that is never this repo's own source. ONE definition: cq.file-size
# feeds it to excluded() as an exclude list and hyg.tracked-junk asks whether a
# path component IS one, and two hand-written lists of the same five names is how
# they drift apart. JUNK_PARTS is derived, never retyped.
JUNK_DIRS = ("node_modules/", ".venv/", "__pycache__/", "dist/", "build/")
JUNK_PARTS = {d.strip("/") for d in JUNK_DIRS}


# ---------- the one type guard ----------

def typed(value, want, default):
    """`value`, but only when it really IS `want` — otherwise `default`.

    THE guard for one whole family of defects: guarding the parse but not the
    parsed value's TYPE. `json.loads` succeeding says nothing about the shape the
    next line assumes — `[]` is valid JSON and has no `.get()`, a `cells` key can
    hold a string, a history line can be truncated mid-write. Three live instances
    were found in this collector, and point-fixing that family elsewhere left a
    sibling path unguarded each time. So
    every reader of parsed data routes through here or through parse_json below.
    """
    return value if isinstance(value, want) else default


def typed_items(value, want=dict):
    """The members of `value` that really are `want` — [] if it is not a list.

    The member half of the same defect: a list that parses fine can still hold a
    string where a dict was assumed, and `for c in card["checks"]` reads exactly
    the same either way.
    """
    return [v for v in typed(value, list, []) if isinstance(v, want)]


def parse_json(text, want=dict, default=None):
    """json.loads AND the type check, in ONE call — so neither can be done alone.

    Two guards that can be applied separately is how one of them gets forgotten;
    this is the only JSON entry point in the collector for that reason.
    """
    try:
        return typed(json.loads(text), want, default)
    except (ValueError, TypeError):
        return default


def run(cmd, cwd):
    """Run a command; a MISSING BINARY is a non-zero result, never a crash.

    Every caller already handles "rc != 0" — that is the whole contract. But an
    absent executable raises FileNotFoundError out of subprocess instead, which
    took the RENDERER down on a machine with no git: no dashboard written, a
    traceback where a page should be (review). 127 is the shell's
    own "command not found", so callers that log the code say something true.
    """
    try:
        # errors="replace": a path or commit subject that is not valid UTF-8 becomes
        # a name that will not open — counted as unread — never a traceback.
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           errors="replace")
    except OSError as e:
        # OSError, not a hand-listed pair. The first version caught
        # FileNotFoundError and NotADirectoryError, which left PermissionError
        # (a git on PATH without its execute bit) still raising a traceback out
        # of render and collect — the same crash, one errno over. Every "the OS
        # would not start this process" failure is an OSError subclass, and
        # listing a subset of them is how the next errno gets missed
        # (review).
        return 127, f"{cmd[0]}: could not run on this machine ({e.__class__.__name__})"
    return p.returncode, (p.stdout + p.stderr).strip()


# ---------- the one measurement guard ----------

# ONE definition of what a check may say — the statuses, the tiers, the layers and
# the record shape, in the leaf module every other one already imports. LAYERS
# moved down here from health.py to sit with them; health.py re-exports it, so
# `health.LAYERS` and health_render.py's import are unchanged.
LAYERS = {
    "security": "Security",
    "code_quality": "Code quality",
    "tests": "Tests",
    "ci": "CI workflow",
    "ai_readiness": "AI readiness",
    "hygiene": "Hygiene & docs",
}
STATUSES = ("pass", "warn", "fail", "na", "pending")
TIERS = ("blocking", "advisory")
# The distinction the whole skill turns on. A CONCLUSIVE status claims a
# measurement HAPPENED and produced a verdict; `na` says the collector looked and
# the check does not apply here; `pending` says nobody has looked at all.
CONCLUSIVE = ("pass", "warn", "fail")
MEASURED = CONCLUSIVE + ("na",)

# Every field a reader of a check dict assumes. Filled in at the two places a
# check enters the program — check() below and card.load_card — so `c["status"]`
# is total for health_render.py, routes.py and the scoring alike instead of each of them
# guarding separately. `pending` is the safe default on purpose: a check with no
# status was never measured, and unmeasured must never read as `pass`.
CHECK_FIELDS = {"id": "", "layer": "", "tier": "", "title": "",
                "status": "pending", "detail": "", "fix": ""}


def certified(c):
    """One check record, made TOTAL and its vocabulary checked.

    The null half of one family: SOMETHING EMPTY OR NULL READ AS SOMETHING
    MEASURED. `{**CHECK_FIELDS, **c}` fills in a MISSING key but not an explicit
    `null`, so `{"status": null}` stayed None — it left the pending list, skipped
    the score cap, read green on the dashboard, and then killed render with
    `KeyError: None`. A tier outside TIERS did the same through `WEIGHT['']`, and
    a status the operator typed wrong (`passs`) through `CHIP[...]`.

    A status outside STATUSES is never repaired into a verdict: the check is
    marked NOT MEASURED (`pending`), and the value that could not be read is
    NAMED in the detail so the typo is fixable. health_render.py indexes CHIP, GLYPH and
    VALUE by status directly BECAUSE of this — the totality is proven here, once,
    rather than re-guessed with a `.get()` default at every lookup, which is a
    guess that prints a plausible glyph over a status nobody can read.

    An unreadable TIER is deliberately left alone. A check that says `fail` has
    been measured, and rewriting it to `pending` would throw a real finding away
    to fix a routing problem; routes.py keeps it visible in every mode with
    UNTIERED on it, and render.blocking() weighs it as blocking. LAYER is made a
    str like id/title/detail/fix: a null or list layer reached
    `LAYERS.get(layer, layer).lower()` in render and killed the WHOLE dashboard
    with AttributeError/TypeError -- found by review, after this
    docstring had called it "a display gap". An unknown layer string still just
    costs the check its chip; an unreadable one no longer costs the render.
    """
    c = {**CHECK_FIELDS, **typed(c, dict, {})}
    for k in ("id", "title", "detail", "fix", "layer"):
        c[k] = "" if c[k] is None else str(c[k])   # esc(), `in` and .lower() need a str
    if c["status"] not in STATUSES:
        c["detail"] = (f"UNREADABLE status={c['status']!r} — NOT MEASURED"
                       + (f": {c['detail']}" if c["detail"] else ""))
        c["status"] = "pending"
    return c


def check(cid, layer, tier, title, status, detail="", fix="", *,
          measured=None, nothing=""):
    """Build one check record — and refuse a verdict nothing was measured for.

    The empty half of the same family. `measured` is the population the verdict
    was computed over, as a count or as the collection itself, and any check
    whose answer comes from a LIST passes it. Zero units with a conclusive status
    is the lie this skill exists to catch: cq.file-size and cq.function-length
    both reported "none found" after every candidate file had been filtered out,
    over a repo whose only tracked source sat under build/. There is no honest
    verdict over an empty population, so it becomes `na` and `nothing` says what
    was looked for — the same contract every other `na` in this collector keeps.
    """
    if status in CONCLUSIVE and measured is not None and not measured:
        status, fix = "na", ""
        detail = nothing or f"nothing measured — {title.lower()} was not checked"
    return certified({"id": cid, "layer": layer, "tier": tier, "title": title,
                      "status": status, "detail": detail, "fix": fix})


def read_or_none(repo, rel):
    """The file's text, or None when it could not be OPENED.

    `read()` below collapses "not there" and "there, and unreadable" into the
    same "", and every caller then measures the empty string as content. That is
    this family's widest door: an unreadable tracked workflow scored as a
    workflow with no dangerous pattern and no unpinned action — two BLOCKING
    security checks passing over bytes nothing had read — and an unreadable
    requirements.txt read as "no dependency manifest tracked", taking five
    dependency checks off the board with a sentence that was simply false.

    So a caller that KNOWS the file is tracked asks HERE, and treats None as
    unmeasured. A caller for which absent and empty really are the same answer
    (a missing README is a thin README) keeps using read().
    """
    try:
        return (repo / rel).read_text(errors="replace")
    except OSError:
        return None


def read(repo, rel):
    text = read_or_none(repo, rel)
    return "" if text is None else text


def read_or_unread(repo, rel, unread, tracked=None):
    """`read()`, except a file that is PRESENT and cannot be opened is appended
    to `unread` instead of silently reading as "". Absent still reads as "".

    The one distinction this family keeps needing: present and unopenable is
    "could not measure"; absent is "absent". Review found that
    `suite_command` read a chmod-0 Makefile as "no test suite detected" and
    turned the BLOCKING test.suite into `na` -- off the fix-routes board with a
    sentence that was false -- and three more callers did the same.

    PRESENT means `os.path.lexists()` OR listed in `tracked`, never
    `Path.exists()`: that follows symlinks, so a dangling-symlink Makefile took
    the absent path, and a tracked file unlinked from the worktree (a sparse
    checkout, a mid-`git rm`) is not on disk at all -- both read as "no
    Makefile" (second review of the same fix). Callers that know the
    tracked list pass it; those that do not still get the lexists half.
    """
    import os
    text = read_or_none(repo, rel)
    if text is None:
        if os.path.lexists(repo / rel) or (tracked is not None and rel in tracked):
            unread.append(rel)
        return ""
    return text


def read_files(repo, rels):
    """({rel: text} for the files that OPENED, [the ones that did not])."""
    got = {rel: read_or_none(repo, rel) for rel in rels}
    return ({k: v for k, v in got.items() if v is not None},
            [k for k, v in got.items() if v is None])


def unmeasured(c, read, unread, note):
    """Fold "part of the input was never read" into a finished check record.

    ONE rule, and every check that reads a set of inputs applies it: name what
    was not read, and NEVER let a clean reading over a partial input stand as
    `pass`. A check that opened four of five workflows has not seen the fifth, so
    it cannot say "every action pinned" — it says what it found and what it
    missed. The sentence is the caller's, because only the caller knows what its
    inputs are and why they would not open; the rule is not.

    Nothing read at all is `pending`, NOT `na`, and the difference is the whole
    point. `na` means the collector LOOKED and the check does not apply here — a
    measured answer, and `routes.CLEAR` drops it from fix-routes, `render` counts
    it as "not applicable" and the score ignores it. Routing "we could not read
    it" to `na` therefore made three BLOCKING security checks vanish from
    fix-routes over an unreadable workflow, which is the exact silent all-clear
    this skill exists to prevent (found by review, in the first fix
    for this very family). `pending` is the status that already means "nobody
    measured this": it caps the score, prints as NOT RUN, and gets a row.

    The full-unread case also stamps `unread` (the files) onto the record. That
    is what tells `routes.fix_rows` this `pending` is a DIAGNOSIS — "could not
    read X" — and not an unrun probe: routing it to PROBE told the agent to run
    a probe that does not exist for these deterministic checks, and dropped
    their real fix route (found by review).
    """
    if not unread:
        return c
    if not read:
        return {**c, "status": "pending", "detail": note.strip(" ()"),
                "unread": sorted(set(unread))}
    return {**c, "detail": c["detail"] + note,
            "status": "warn" if c["status"] == "pass" else c["status"]}


def pending_if_unread(c, unread, what, finding_stands=False):
    """A check whose CONCLUSION rests on a file that is present but would not
    open has measured nothing: `pending`, naming the file. Stricter than
    `unmeasured()` on purpose -- that rule lets a partial read stand with a note,
    which is right for a SET of workflows and wrong here: one unopenable
    Makefile invalidates "no test suite detected" entirely (review).

    `finding_stands=True` says the caller's `warn`/`fail` was read out of files
    it COULD open, so the unread sibling can only add to it, never retract it:
    the status, the evidence and the fix route survive. It is the caller's call
    because only the caller knows where its evidence came from -- `ai.lockfiles`
    reads its unpinned specs from OTHER manifests, while `ai.verify-command`
    derives "no suite, no gate" FROM the Makefile that would not open, and that
    `fail` is an artifact of the unread file, not a finding (review).

    Stamps `unread` (the files) onto the record for the same reason `unmeasured`
    does: it marks this `pending` as a "could not read X" DIAGNOSIS rather than
    an unrun probe, which is what lets `routes.fix_rows` route it to the real
    fix instead of PROBE (review).
    """
    if not unread:
        return c
    note = (f"could not read {', '.join(sorted(set(unread)))} — {what} NOT MEASURED; "
            f"fix permissions and re-run")
    # Evidence the caller read elsewhere KEEPS its status, its detail and its
    # fix route: demoting it to `pending` dropped the concrete finding AND its
    # route, and `pending` is excluded from the weighted score entirely
    # (render.VALUE), so a repo with a KNOWN unpinned dependency scored HIGHER
    # for also owning an unreadable file -- it reported health it had measured
    # the opposite of (review).
    if finding_stands and c.get("status") in ("warn", "fail"):
        detail = f"{c['detail']}  ({note})" if c.get("detail") else note
        return {**c, "detail": detail, "unread": sorted(set(unread))}
    # A probe that already stood at `pending` with a command ("run: npm test")
    # KEEPS it: the conclusion survives an unopenable sibling file, and dropping
    # the command left the operator with nothing to run (review).
    # `pass`/`na` was a conclusion the unread file invalidates: replaced.
    was_unrun_probe = c.get("status") == "pending"
    detail = f"{c['detail']}  ({note})" if was_unrun_probe and c.get("detail") else note
    out = {**c, "status": "pending", "detail": detail}
    # ...and it keeps its PROBE ROUTE too. `unread` is the marker that says "this
    # pending is a could-not-read DIAGNOSIS", and routes.fix_rows sends anything
    # carrying it to unreadable() — "restore the file". Stamping it on a probe
    # that simply never ran replaced "run: npm test" with "restore the Makefile",
    # so the probe was never run and the check stayed unmeasured. The first
    # fix restored the detail and left the route wrong; this is the other half
    # (review). Only a conclusion the unread file actually
    # invalidates gets the marker.
    if not was_unrun_probe:
        out["unread"] = sorted(set(unread))
    return out


def quality_excludes(repo):
    """The repo's own "not our source" list — or () when it does not have one.

    Every step is type-guarded, not just the parse: `exclude = "web"` is valid
    TOML and `tuple("web")` silently became `('w', 'e', 'b')`, which excludes
    nothing and reports the whole repo as in scope. Same family as parse_json.
    """
    try:
        import tomllib
        cfg = tomllib.loads(read(repo, "pyproject.toml"))
    except Exception:
        return ()
    q = typed(typed(cfg.get("tool"), dict, {}).get("claude-quality"), dict, {})
    return tuple(typed_items(q.get("exclude"), str))


def excluded(path, excl):
    """Is this path outside "our source" — by a dot-directory, or by the repo's list.

    A pattern matches the three ways the house ratchet's own `is_excluded()`
    (scripts/quality/check_file_sizes.py) matches it: a bare path component, a
    glob over the whole relative path, or a directory prefix of it. This read
    components only, so `exclude = ["_plans/*/_evidence"]` — a glob the ratchet
    honours — excluded nothing here, and cq.file-size and cq.function-length
    reported plan-evidence scripts the repo had already ruled out of its source.
    Two readers of one list must agree on it.
    """
    parts = path.split("/")
    if any(p.startswith(".") for p in parts[:-1]):
        return True
    for e in excl:
        pat = e.strip("/")
        if (pat in parts or fnmatch.fnmatch(path, pat)
                or fnmatch.fnmatch(path, pat + "/*")):
            return True
    return False
