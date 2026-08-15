"""Structural mutation of a LIVE plan — add / amend / retire a session (RP-03).

Why this exists
---------------
`/plan-execute` could mutate a plan's *state* (apply, verify-*, ship-*) but had no
operation for changing the plan's *shape*. A run that discovers new work mid-flight
had one path: hand-edit PLAN.html and manifest.json. On the COS Ingestion II run six
sessions were added that way and nearly every pass introduced a different defect —
articles appended outside their section, invented `data-cat` values, a stale nav
strip, stale header counts — and every one of them passed the structural gate. The
operator found the one that mattered by *looking at the page*.

The one renderer rule
---------------------
The defects above all come from a SECOND HTML assembler diverging from the first.
So this module has no HTML assembler. It mutates `spec.json` — the authoring input
plan-builder already keeps in every plan directory — and then re-runs plan-builder's
own `render_html` / `gen_manifest` / `gen_session_prompt_md`, carrying recorded state
forward with `article_block.carry_over_state`. Every surface (article containment,
nav strip, header counts, "Session N of M" steps, next-buttons, category sections)
is therefore correct by construction, because it is produced by the code that
produced the rest of the page.

This is NOT `build_plan.py --rebuild`. A rebuild rewrites run_state.json and every
session prompt from spec, and is invoked as a fresh build over a live directory.
Here the render functions are called in-process, run_state.json is never touched,
and only the prompts of sessions this mutation actually changed are regenerated.
Before writing, the module re-renders the UNMUTATED spec and refuses if that alone
would change the page (`builder_drift`) — so a builder-version upgrade can never
ride in unannounced on top of an add-session.

Multi-file transaction
----------------------
One mutation writes PLAN.html + spec.json + manifest.json + N session files +
the change log. temp+rename is atomic for ONE file; a crash between renames would
leave a mixed-generation plan — exactly the corruption this command exists to
eliminate. So the write is journalled:

  1. every target's COMPLETE new content is written into `_mutation/<txn>/`
     and fsynced;
  2. `_mutation/<txn>/journal.json` is written temp+rename — this single atomic
     rename is the COMMIT POINT. Before it lands, no live file has been touched
     and recovery is "delete the staging directory";
  3. each target is `os.replace`d from staging (removing it from staging as it
     lands, which makes replay idempotent);
  4. the journal is removed last.

Recovery rolls FORWARD: a journal on disk means the mutation was fully computed
and validated, so `recover()` replays the renames that have not landed yet. It is
called at the top of every run.py subcommand, so a crashed mutation heals on the
next command rather than waiting to be noticed. Because every staged file holds a
COMPLETE generation (never a delta, and the change log is staged as old+appended
content rather than an append), replay is idempotent.

Change log
----------
Every mutation appends exactly one JSON line to `_changelog.ndjson`. That file is
the clean append point for the rendered change-log section (S05); read it with
`read_changelog()`.

The containment gate (RP-04)
----------------------------
"Correct by construction" is a claim about the renderer, and a claim is not a
check. So every mutation runs `structural_gate.containment_problems` over the
generation it has just COMPUTED — before the commit point, while no live file has
been touched — and refuses on any problem the plan did not already have. The
baseline diff is deliberate: a live plan in this repo already has 5 items in no
infographic group, and refusing a mutation over a defect it did not introduce
would strand the plan on someone else's bug. Problems are matched by their
`(check, subject)` identity rather than by message text, and one invariant-shaped
check (`SOFT_ON_PRIOR_BREACH`) is tolerated wholesale on a plan that already
breaks it — see `containment_delta`.

Recorded state binds to the manifest DIGEST, and a mutation rewrites
manifest.json — so the same restructuring that adds a session can invalidate
verify state that has nothing to do with it. Two rules keep that honest:

  * **Refuse** while a session holds UNSETTLED verify state (a gate cycle
    mid-flight) or a persisted-but-unreplayed closeout. Both mean a write is
    half-done; mutating under them turns "resume" into `state-drift` + halt.
  * **Migrate** the settled ones forward, IN THE SAME TRANSACTION, preserving
    `rework_count`. Otherwise the operator's only way out of the state-drift
    refusal is to delete the file — which hands a session that had already
    exhausted `max_rework` a silent fresh budget.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_BUILDER = SCRIPT_DIR.parent.parent / "plan-builder" / "scripts"
if str(_BUILDER) not in sys.path:
    sys.path.insert(0, str(_BUILDER))

import article_block as ab  # noqa: E402
import build_plan as bp  # noqa: E402
import dispatch as dsp  # noqa: E402
import structural_gate as sg  # noqa: E402

CHANGELOG_FILE = "_changelog.ndjson"
JOURNAL_DIR = "_mutation"
JOURNAL_NAME = "journal.json"
VERIFY_STATE_DIR = "_verify_state"
CLOSEOUT_DIR = "_closeouts"
# A verify cycle that has stopped moving. Anything else (no outcome yet, or
# `rework` awaiting the re-dispatch) is a write in flight.
SETTLED_VERIFY = {"passed", "halted"}
# `<sid>.rN.json` — state a redispatch round already moved aside (run.py's
# `_archive_session_state`). History, not live state.
_ARCHIVED_RE = re.compile(r"^r\d+$")

# A session whose work is finished or deliberately abandoned cannot be amended:
# its prompt/model/deps describe a dispatch that already happened.
AMENDABLE = {"TODO"}
# Retirement is a decision NOT to do work. Refuse where there is work in flight
# (DOING), a recorded result (DONE / AWAITS_REVIEW), or nothing left to retire
# (WONTFIX). PARTIAL/BLOCKED/DEFERRED are the honest "we are dropping this" cases.
RETIREABLE = {"TODO", "BLOCKED", "DEFERRED", "PARTIAL"}


class MutationError(Exception):
    """Refusal: the requested mutation is invalid or unsafe. Nothing was written."""


def _now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Plan I/O
# --------------------------------------------------------------------------
def spec_path(plan_dir):
    return Path(plan_dir) / "spec.json"


def load_spec(plan_dir):
    p = spec_path(plan_dir)
    if not p.is_file():
        raise MutationError(
            f"{p} not found. The mutation commands re-render the plan through "
            "plan-builder's own renderer, which needs the spec that built it. A plan "
            "without spec.json can only be changed by rebuilding it from a spec — "
            "there is no safe second renderer, and writing one is how the 2026-08-02 "
            "hand edits corrupted the page."
        )
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise MutationError(f"{p} is not valid JSON: {e}") from e


def project_root_for(plan_dir):
    """The project root a plan sits in (`…/<root>/_plans/<plan>`), or None."""
    p = Path(plan_dir).resolve()
    for parent in p.parents:
        if parent.name == "_plans":
            return str(parent.parent)
    return None


def read_changelog(plan_dir):
    """Every recorded mutation, oldest first. The render seam for S05."""
    p = Path(plan_dir) / CHANGELOG_FILE
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def record_change(plan_dir, *, op, session, summary, sessions_touched=(), items_touched=()):
    """Append ONE change-log entry and re-render the page's Plan-changes section.

    The seam for the commands that change a plan WITHOUT going through the
    journalled generation above — `redispatch` (RP-02) and a REPLAN resolution
    (RP-05). Both already rewrite PLAN.html article-by-article through
    `apply_mutation`, so there is no generation to stage; this uses the same
    atomic whole-file writer.

    ponytail: two atomic writes, not one transaction. A crash between them
    leaves the ndjson one entry ahead of the page — and the next entry heals it,
    because the section is always rendered from the WHOLE log. Upgrade path if
    that ever matters: stage both through `commit()`.
    """
    entry = {
        "at": _now(),
        "op": op,
        "session": session,
        "summary": summary,
        "sessions_touched": sorted(set(sessions_touched)),
        "items_touched": sorted(set(items_touched)),
    }
    path = Path(plan_dir) / CHANGELOG_FILE
    head = path.read_bytes() if path.is_file() else b""
    if head and not head.endswith(b"\n"):
        head += b"\n"
    # Whole-file temp+rename, not an append: a torn append would leave the ONE
    # record of what changed as unparseable trailing bytes.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-changelog-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(head + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    rendered = ab.write_change_log(Path(plan_dir) / "PLAN.html", read_changelog(plan_dir))
    return {"entry": entry, "rendered": rendered}


def _statuses(plan_dir):
    return ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_bytes().decode("utf-8"))


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


# --------------------------------------------------------------------------
# Rendering one complete generation
# --------------------------------------------------------------------------
def _validate(spec, plan_dir, *, baseline_ok):
    """plan-builder's own build-time guards. `validate_spec` (pure schema: ids,
    categories, dispatch cycles, model/reasoning tiers) is ALWAYS hard. The
    registry-resolving guards are downgraded to warnings when the plan already
    failed them BEFORE this mutation — a gate id that drifted months ago is not
    this command's to fix, and refusing would strand the plan."""
    try:
        bp.validate_spec(spec)
    except ValueError as e:
        # Every refusal leaves this module as a MutationError, so the CLI prints
        # one clear line instead of a traceback from inside the builder.
        raise MutationError(f"the resulting plan would be invalid: {e}") from e
    warnings = []
    root = project_root_for(plan_dir)
    for name in ("validate_shipping_resolves", "validate_verify_resolves"):
        try:
            getattr(bp, name)(spec, root)
        except ValueError as e:
            if name in baseline_ok:
                raise MutationError(f"{name}: {e}") from e
            warnings.append(f"{name} (pre-existing, not caused by this mutation): {e}")
    try:
        bp.validate_peer_triggers(spec)
    except ValueError as e:
        if "validate_peer_triggers" in baseline_ok:
            raise MutationError(f"validate_peer_triggers: {e}") from e
        warnings.append(f"validate_peer_triggers (pre-existing): {e}")
    return warnings


def _baseline_ok(spec, plan_dir):
    """Which registry guards the plan passes BEFORE the mutation."""
    ok = set()
    root = project_root_for(plan_dir)
    for name in ("validate_shipping_resolves", "validate_verify_resolves"):
        try:
            getattr(bp, name)(spec, root)
            ok.add(name)
        except ValueError:
            pass
    try:
        bp.validate_peer_triggers(spec)
        ok.add("validate_peer_triggers")
    except ValueError:
        pass
    return ok


def render_html_for(spec, plan_dir, prior_html, status_ops=(), *, validate_js=False,
                    changelog=None):
    """plan-builder's renderer + carried-over recorded state + status writes.

    `validate_js` runs plan-builder's own pre-write no-undef check on the
    dashboard script (node+eslint, ~3s). On for the generation that gets
    WRITTEN; off for the comparison renders, which write nothing.

    `changelog` (RP-06) replaces the rendered Plan-changes rows. Passed only for
    the generation being WRITTEN — it must show the entry this mutation is
    appending, which is not on disk yet. Left None elsewhere so the comparison
    renders carry the page's current log through unchanged.
    """
    text = ab.carry_over_state(prior_html, bp.render_html(spec, Path(plan_dir)))
    if changelog is not None:
        text = ab.set_change_log(text, changelog)
    for aid, status, note in status_ops:
        text = ab.mutate_text(text, aid, status=status, note=note)
    if validate_js:
        bp.validate_dashboard_js(text)
    return text


def builder_drift(spec_before, plan_dir, prior_html):
    """Lines the CURRENT builder would change on this page with NO mutation.

    Non-empty means the plan was built by an older plan-builder (or moved), so a
    re-render would carry a version upgrade along with the mutation. The operator
    is shown it and must opt in — a mutation command must never silently rewrite
    a page the operator did not ask it to touch.
    """
    import difflib

    rendered = render_html_for(spec_before, plan_dir, prior_html)
    if rendered == prior_html:
        return []
    return [
        line
        for line in difflib.unified_diff(
            prior_html.splitlines(), rendered.splitlines(), lineterm="", n=0
        )
        if line[:1] in "+-" and line[:3] not in ("---", "+++")
    ]


def build_generation(plan_dir, spec, *, touched_sessions, status_ops, log_entry):
    """Every file this mutation writes, as {plan-relative path: bytes}."""
    plan_dir = Path(plan_dir)
    prior_html = (plan_dir / "PLAN.html").read_bytes().decode("utf-8")
    items_by_id = {it["id"]: it for it in spec["items"]}
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    by_id = {s["id"]: s for s in spec["sessions"]}

    manifest = bp.gen_manifest(spec)
    prior_manifest_path = plan_dir / "manifest.json"
    if prior_manifest_path.is_file():
        prior = json.loads(prior_manifest_path.read_text())
        if "created" in prior:
            # `created` is stamped with today's date at build time; regenerating
            # must not rewrite the plan's birthday.
            manifest["created"] = prior["created"]
        if "plan_schema_version" in prior:
            # ...and regenerating must not rewrite the plan's CONTRACT either.
            # `gen_manifest` stamps the CURRENT builder version, so without this a
            # mid-run `amend-session` on a plan built at v5 would silently move it
            # to v6 — retroactively switching on every v6-gated runtime behaviour
            # (from ESC-02 on, that includes changing which MODEL a failing session
            # re-dispatches on) for a plan whose author never opted in. The stamp
            # records which contract the plan was BUILT under; a mutation is not a
            # rebuild. An operator who wants the new contract rebuilds deliberately.
            manifest["plan_schema_version"] = prior["plan_schema_version"]

    # The change log this generation will carry: what is on disk plus the entry
    # this mutation appends. Rendered into PLAN.html and written to
    # `_changelog.ndjson` in the SAME transaction, so the page and the record
    # can never disagree about what changed.
    changelog = [*read_changelog(plan_dir), log_entry]

    targets = {
        "PLAN.html": render_html_for(
            spec, plan_dir, prior_html, status_ops, validate_js=True, changelog=changelog
        ).encode("utf-8"),
        "spec.json": (json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
    }
    for sid in sorted(set(touched_sessions)):
        s = by_id[sid]
        targets[f"sessions/{sid}.prompt.md"] = bp.gen_session_prompt_md(
            s,
            plan_dir.name,
            items_by_id,
            post_session=bp.resolve_post_session(s, phases_by_id),
            verify=bp.resolve_verify(s, phases_by_id),
        ).encode("utf-8")
        targets[f"sessions/{sid}.context.md"] = bp.gen_session_context_md(
            s, items_by_id
        ).encode("utf-8")

    prior_log = (plan_dir / CHANGELOG_FILE)
    head = prior_log.read_bytes() if prior_log.is_file() else b""
    if head and not head.endswith(b"\n"):
        head += b"\n"
    targets[CHANGELOG_FILE] = head + (
        json.dumps(log_entry, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return targets


# --------------------------------------------------------------------------
# The containment gate + recorded-state safety (RP-04)
# --------------------------------------------------------------------------
def _live_state_files(plan_dir, subdir):
    """`<sid>.json` records in `subdir`, skipping redispatch archives."""
    out = []
    d = Path(plan_dir) / subdir
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        parts = p.name.split(".")
        if len(parts) > 2 and _ARCHIVED_RE.match(parts[1]):
            continue
        out.append(p)
    return out


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def in_flight_state(plan_dir):
    """Sessions whose recorded state is mid-write. `{"verify": [...], "closeout": [...]}`.

    An unreadable record counts as in-flight: it is exactly the state we must
    not mutate underneath, and guessing "probably fine" is how paid work gets
    stranded.
    """
    verify, closeouts = [], []
    for p in _live_state_files(plan_dir, VERIFY_STATE_DIR):
        state = _read_json(p)
        if state is None or state.get("outcome") not in SETTLED_VERIFY:
            verify.append(p.name.split(".")[0])
    for p in _live_state_files(plan_dir, CLOSEOUT_DIR):
        rec = _read_json(p)
        if rec is None or not rec.get("replayed", True):
            closeouts.append(p.name.split(".")[0])
    return {"verify": verify, "closeout": closeouts}


def _refuse_if_state_in_flight(plan_dir, op):
    st = in_flight_state(plan_dir)
    if not (st["verify"] or st["closeout"]):
        return
    raise MutationError(
        f"refusing to {op}: recorded state is mid-flight (unsettled verify state: "
        f"{st['verify'] or 'none'}; persisted-but-unreplayed closeout: "
        f"{st['closeout'] or 'none'}). Verify state binds to the manifest digest and a "
        "mutation rewrites manifest.json, so those cycles would resume as `state-drift` "
        "and halt the plan. Finish them (`verify-begin --resume` / `verify-finalize`, or "
        "re-run `apply` for the unreplayed closeout), then re-run this command."
    )


def migrate_verify_state(plan_dir, old_digest, new_manifest_bytes):
    """Rebind SETTLED verify state to the post-mutation manifest digest.

    Returns `{plan-relative path: bytes}` for the transaction. `rework_count`
    and every other field ride through untouched — a session that exhausted
    `max_rework` must not come out of a restructuring with a fresh budget.
    """
    new_digest = hashlib.sha256(new_manifest_bytes).hexdigest()
    if new_digest == old_digest:
        return {}
    out = {}
    for p in _live_state_files(plan_dir, VERIFY_STATE_DIR):
        state = _read_json(p)
        if not state or state.get("manifest_digest") != old_digest:
            continue  # already stale for another reason — not this mutation's to rewrite
        state["manifest_digest"] = new_digest
        state["migrated_from_digest"] = old_digest
        state["migrated_at"] = _now()
        out[f"{VERIFY_STATE_DIR}/{p.name}"] = (
            json.dumps(state, indent=2, ensure_ascii=False)
        ).encode("utf-8")
    return out


# Checks that describe an INVARIANT rather than a corruption: a plan either
# maintains "every item is bound to a progress-bar group" or it does not. Where
# the plan already breaks one, a mutation that adds one more breach is reported,
# not refused — the plan never had the invariant, and refusing would strand it
# on a defect no mutation caused. Where the invariant HOLDS today, breaking it is
# a refusal, because that is the 92%-vs-70% progress bar in the making.
SOFT_ON_PRIOR_BREACH = {"workstreams-unplaced"}


def containment_delta(plan_dir, prior_html, targets):
    """`(blocking, warn)` problems for the generation in `targets`.

    Run BEFORE the commit point, on the computed bytes — so a mutation that
    would corrupt a page surface is refused while every live file is untouched.
    Problems are matched by their `(check, subject)` identity, never by message
    text, so a pre-existing problem whose wording shifts is not read as new.

    A problem the plan ALREADY had is neither refused nor warned here — it is
    plan state, not this mutation's doing, and `run_gate`'s `containment` block
    reports it on every command anyway. `warn` is strictly the problems this
    mutation ADDS that a prior breach makes tolerable.
    """
    prior_manifest = _read_json(Path(plan_dir) / "manifest.json") or {"sessions": [], "items": []}
    before = sg.containment_problems(prior_html, prior_manifest)
    after = sg.containment_problems(
        targets["PLAN.html"].decode("utf-8"), json.loads(targets["manifest.json"])
    )
    known = {(check, subject) for check, subject, _ in before}
    breached = {check for check, _, _ in before}
    blocking, warn = [], []
    for check, subject, msg in after:
        if (check, subject) in known:
            continue
        (warn if check in SOFT_ON_PRIOR_BREACH and check in breached else blocking).append(msg)
    return blocking, warn


def _run(plan_dir, spec_before, spec_after, *, op, session, summary, touched_sessions,
         status_ops=(), items_touched=(), expected=None, allow_builder_drift=False):
    """Validate, render one complete generation, and commit it."""
    plan_dir = Path(plan_dir)
    recovered = recover(plan_dir)
    _refuse_if_state_in_flight(plan_dir, op)
    prior_html = (plan_dir / "PLAN.html").read_bytes().decode("utf-8")
    prior_digest = hashlib.sha256((plan_dir / "manifest.json").read_bytes()).hexdigest()

    baseline = _baseline_ok(spec_before, plan_dir)
    warnings = _validate(spec_after, plan_dir, baseline_ok=baseline)

    drift = builder_drift(spec_before, plan_dir, prior_html)
    if drift and not allow_builder_drift:
        preview = "\n".join(drift[:20])
        raise MutationError(
            f"refusing to {op}: re-rendering this plan with the CURRENT plan-builder "
            f"would change {len(drift)} line(s) of PLAN.html even with no mutation "
            "applied — the plan was built by an older builder (or from another path). "
            "Those changes would ride in on top of your mutation, unannounced. Review "
            "them and re-run with --allow-builder-drift to accept both:\n" + preview
        )

    entry = {
        "at": _now(),
        "op": op,
        "session": session,
        "summary": summary,
        "sessions_touched": sorted(set(touched_sessions)),
        "items_touched": sorted(set(items_touched)),
    }
    targets = build_generation(
        plan_dir, spec_after, touched_sessions=touched_sessions,
        status_ops=status_ops, log_entry=entry,
    )

    # The containment gate — checked on the COMPUTED generation, before the
    # commit point. Nothing live has been touched when this refuses.
    new_problems, tolerated = containment_delta(plan_dir, prior_html, targets)
    if new_problems:
        remedy = ""
        if any(p.startswith("[workstreams]") for p in new_problems):
            remedy = (
                "\nAn item in no Plan Achievement group is invisible to that section's "
                "progress bar — place it with --infographic-group NAME and re-run."
            )
        raise MutationError(
            f"refusing to {op}: the generation this mutation would write breaks "
            f"{len(new_problems)} page-containment check(s) that pass today. Nothing was "
            "written.\n  " + "\n  ".join(new_problems) + remedy
        )
    warnings += [
        f"containment: {p} (allowed: this plan already breaks that check elsewhere)"
        for p in tolerated
    ]

    # Settled verify state rides forward onto the new manifest digest, in this
    # same transaction, with `rework_count` intact.
    migrated = migrate_verify_state(plan_dir, prior_digest, targets["manifest.json"])
    targets.update(migrated)

    txn = commit(plan_dir, targets, {"op": op, "session": session})
    return {
        "txn": txn,
        "op": op,
        "session": session,
        # What the structural gate must find on disk afterwards — the caller
        # re-reads PLAN.html and asserts it, so a write that silently missed
        # its target fails loudly instead of looking like a success.
        "expected_statuses": (
            expected if expected is not None else {a: s for a, s, _ in status_ops}
        ),
        "files_written": sorted(targets),
        "sessions_touched": entry["sessions_touched"],
        "items_touched": entry["items_touched"],
        "verify_state_migrated": sorted(migrated),
        "builder_drift_lines": len(drift),
        "validation_warnings": warnings,
        "recovered": recovered,
    }


# --------------------------------------------------------------------------
# Graph helpers
# --------------------------------------------------------------------------
def _deps(session):
    return list((session.get("dispatch") or {}).get("depends_on") or [])


def dependents(spec, session_ids, *, transitive=True):
    """Sessions depending on any of `session_ids`, in spec order."""
    roots = set(session_ids)
    out, frontier = set(), set(roots)
    while frontier:
        nxt = set()
        for s in spec["sessions"]:
            sid = s["id"]
            if sid in out or sid in roots:
                continue
            if set(_deps(s)) & frontier:
                nxt.add(sid)
        out |= nxt
        frontier = nxt if transitive else set()
    return [s["id"] for s in spec["sessions"] if s["id"] in out]


def _live(statuses, sid):
    return statuses.get(sid, "TODO") not in dsp.TERMINAL_STATES


def _exclusive_items(spec, session_ids):
    owners = {}
    for s in spec["sessions"]:
        for iid in s.get("items", []) or []:
            owners.setdefault(iid, set()).add(s["id"])
    ids = set(session_ids)
    return sorted(i for i, own in owners.items() if own and own <= ids)


# --------------------------------------------------------------------------
# add-session
# --------------------------------------------------------------------------
def parse_new_item(raw):
    """`id|category|title[|summary]` -> item dict."""
    parts = [p.strip() for p in str(raw).split("|")]
    if len(parts) < 3 or not all(parts[:3]):
        raise MutationError(
            f"--new-item {raw!r} must be 'id|category|title' (optionally "
            "'id|category|title|one-line summary')"
        )
    item = {"id": parts[0], "title": parts[2], "category": parts[1]}
    if len(parts) > 3 and parts[3]:
        item["human_summary"] = parts[3]
    return item


# The Plan-Achievement infographic groups items into a visual story, and each
# shape names its group list differently. An item missing from every group is
# invisible to that section's counters — the modern form of the 2026-08-02
# "WORKSTREAMS missing new items → 92% shown against a real 70%" defect. So a
# new item is placed, or the operator is TOLD it was not.
#
# The mapping itself lives in build_plan (`INFOGRAPHIC_GROUP_KEYS`) — the same
# one validate_spec refuses against and the renderer emits from. Aliased, never
# re-declared: two copies drift, and the copy that drifts is the one the bar
# counts through.
_INFOGRAPHIC_GROUPS = bp.INFOGRAPHIC_GROUP_KEYS


def attach_to_infographic(spec, item_ids, group_name=None):
    """Add `item_ids` to a Plan-Achievement group. Returns (group_name, warning)."""
    info = spec.get("infographic") or {}
    groups = info.get(_INFOGRAPHIC_GROUPS.get(info.get("type"), ""), None)
    if not item_ids or not isinstance(groups, list) or not groups:
        return None, None
    wanted = group_name
    if wanted is None:
        # Default: the item's own category LABEL, when a group is named for it.
        labels = {c["key"]: c.get("label", "") for c in spec["categories"]}
        by_id = {it["id"]: it for it in spec["items"]}
        cats = {labels.get(by_id[i]["category"], "").strip().lower()
                for i in item_ids if i in by_id}
        names = {str(g.get("name", "")).strip().lower() for g in groups}
        hit = cats & names
        wanted = next(iter(hit)) if len(hit) == 1 else None
    if wanted is None:
        return None, (
            f"item(s) {', '.join(item_ids)} were not added to any Plan Achievement "
            f"group ({', '.join(str(g.get('name')) for g in groups)}), so that "
            "section's counters exclude them. Re-run with --infographic-group NAME "
            "to place them."
        )
    for g in groups:
        if str(g.get("name", "")).strip().lower() == str(wanted).strip().lower():
            g.setdefault("items", [])
            g["items"] += [i for i in item_ids if i not in g["items"]]
            return g.get("name"), None
    raise MutationError(
        f"--infographic-group {group_name!r} matches no group in the Plan "
        f"Achievement section. Groups: "
        f"{[str(g.get('name')) for g in groups]}"
    )


def add_session(plan_dir, *, sid, title, items=(), new_items=(), depends_on=(),
                model="Sonnet", reasoning=None, gates=(), require_evidence=False,
                prompt=None, human_summary=None, parallel_group=None,
                infographic_group=None, allow_builder_drift=False):
    spec = load_spec(plan_dir)
    after = json.loads(json.dumps(spec))
    if any(s["id"] == sid for s in after["sessions"]):
        raise MutationError(f"session {sid!r} already exists in this plan")

    cat_keys = {c["key"] for c in after["categories"]}
    parsed_new = [parse_new_item(n) for n in new_items]
    for it in parsed_new:
        if it["category"] not in cat_keys:
            raise MutationError(
                f"item {it['id']!r} has category {it['category']!r}, which matches no "
                f"section in this plan. Existing sections: {sorted(cat_keys)}. An "
                "invented data-cat renders the article into the wrong section — the "
                "exact defect this command exists to prevent."
            )
    existing_items = {it["id"] for it in after["items"]}
    for it in parsed_new:
        if it["id"] in existing_items:
            raise MutationError(f"item {it['id']!r} already exists — drop it from --new-item")

    item_ids = list(dict.fromkeys([*items, *[it["id"] for it in parsed_new]]))
    if not item_ids:
        raise MutationError(
            "a session with no items has nothing to dispatch: pass --items (existing "
            "item ids) and/or --new-item 'id|category|title'"
        )
    owner = {iid: s["id"] for s in after["sessions"] for iid in s.get("items", []) or []}
    for iid in item_ids:
        if iid in owner:
            raise MutationError(
                f"item {iid!r} is already owned by session {owner[iid]!r}; two sessions "
                "owning one item makes the item's status ambiguous"
            )
        if iid not in existing_items and iid not in {n["id"] for n in parsed_new}:
            raise MutationError(
                f"unknown item {iid!r}: it exists in neither the plan nor --new-item"
            )

    known = {s["id"] for s in after["sessions"]}
    for d in depends_on:
        if d not in known and d != sid:
            raise MutationError(f"--depends-on {d!r} is not a session in this plan")

    session = {
        "id": sid,
        "title": title,
        "model": model,
        "items": item_ids,
        "prompt": prompt or "",
    }
    if reasoning:
        session["reasoning"] = reasoning
    if human_summary:
        session["human_summary"] = human_summary
    dispatch = {}
    if depends_on:
        dispatch["depends_on"] = list(depends_on)
    if parallel_group:
        dispatch["parallel_group"] = parallel_group
    if dispatch:
        session["dispatch"] = dispatch
    if gates or require_evidence:
        verify = {}
        if gates:
            verify["gates"] = list(gates)
        if require_evidence:
            verify["require_evidence"] = True
        session["verify"] = verify

    after["items"].extend(parsed_new)
    # Appended, not inserted: document order drives the dashboard's UP-NEXT panel,
    # and a session added mid-run is by definition the newest work.
    after["sessions"].append(session)
    placed, info_warning = attach_to_infographic(
        after, [n["id"] for n in parsed_new], infographic_group
    )

    result = _run(
        plan_dir, spec, after,
        op="add-session", session=sid,
        summary=f"added {sid} ({title}) with items {', '.join(item_ids)}"
                + (f", depends on {', '.join(depends_on)}" if depends_on else ""),
        touched_sessions=[sid], items_touched=item_ids,
        expected={sid: "TODO", **{n["id"]: "TODO" for n in parsed_new}},
        allow_builder_drift=allow_builder_drift,
    )
    result["infographic_group"] = placed
    if info_warning:
        result["validation_warnings"] = [*result["validation_warnings"], info_warning]
    return result


# --------------------------------------------------------------------------
# amend-session
# --------------------------------------------------------------------------
def amend_session(plan_dir, sid, *, depends_on=None, prompt=None, model=None,
                  reasoning=None, allow_builder_drift=False):
    spec = load_spec(plan_dir)
    after = json.loads(json.dumps(spec))
    by_id = {s["id"]: s for s in after["sessions"]}
    if sid not in by_id:
        raise MutationError(f"unknown session {sid!r}")

    status = _statuses(plan_dir).get(sid, "TODO")
    if status not in AMENDABLE:
        raise MutationError(
            f"refusing to amend {sid}: it is {status}, not TODO. Amending changes what "
            "the session WILL be dispatched with; a session that is running or finished "
            "was dispatched against the old text, so an amendment would silently "
            "rewrite history. Use `redispatch` to re-run it, or `retire-session` to drop it."
        )
    if depends_on is None and prompt is None and model is None and reasoning is None:
        raise MutationError(
            "nothing to amend: pass at least one of --depends-on / --prompt / --model / --reasoning"
        )

    s = by_id[sid]
    changed = []
    if depends_on is not None:
        known = {x["id"] for x in after["sessions"]}
        for d in depends_on:
            if d not in known:
                raise MutationError(f"--depends-on {d!r} is not a session in this plan")
        s.setdefault("dispatch", {})["depends_on"] = list(depends_on)
        changed.append(f"depends_on -> [{', '.join(depends_on) or '(none)'}]")
    if prompt is not None:
        s["prompt"] = prompt
        changed.append("prompt rewritten")
    if model is not None:
        s["model"] = model
        changed.append(f"model -> {model}")
    if reasoning is not None:
        s["reasoning"] = reasoning
        changed.append(f"reasoning -> {reasoning}")

    return _run(
        plan_dir, spec, after,
        op="amend-session", session=sid,
        summary=f"amended {sid}: " + "; ".join(changed),
        touched_sessions=[sid], items_touched=s.get("items", []),
        expected={sid: "TODO"},
        allow_builder_drift=allow_builder_drift,
    )


# --------------------------------------------------------------------------
# retire-session
# --------------------------------------------------------------------------
_DEP_DROPPED_NOTE = (
    "\n\n> **Plan change ({at}):** session `{gone}` was retired — {reason}\n"
    "> This session no longer depends on it and will NOT receive its output. "
    "Do not wait for it; if its input was load-bearing here, close BLOCKED "
    "saying so rather than inventing the missing input.\n"
)


def retire_session(plan_dir, sid, *, reason, cascade=False, drop_dependency=False,
                   allow_builder_drift=False):
    if not (reason or "").strip():
        raise MutationError(
            "refusing to retire without --reason: WONTFIX is a terminal state that "
            "silently satisfies every dependent's dependency, and the plan's history is "
            "the only place the WHY can live. State it in one line."
        )
    if cascade and drop_dependency:
        raise MutationError("--cascade and --drop-dependency are alternatives; pick one")

    spec = load_spec(plan_dir)
    after = json.loads(json.dumps(spec))
    by_id = {s["id"]: s for s in after["sessions"]}
    if sid not in by_id:
        raise MutationError(f"unknown session {sid!r}")

    statuses = _statuses(plan_dir)
    status = statuses.get(sid, "TODO")
    if status not in RETIREABLE:
        raise MutationError(
            f"refusing to retire {sid}: it is {status}. Retirement records a decision "
            f"not to do the work ({'/'.join(sorted(RETIREABLE))} only) — it is not a way "
            "to cancel an in-flight dispatch or erase a recorded result."
        )

    # WONTFIX is a member of the executor's DONE_STATES: retiring a producer
    # SATISFIES its consumers' dependencies, so they would dispatch without the
    # input they were written to consume. That is a silent correctness hole, so
    # the operator must say what happens to them.
    direct = [d for d in dependents(after, [sid], transitive=False) if _live(statuses, d)]
    live_all = [d for d in dependents(after, [sid]) if _live(statuses, d)]
    transitive_only = [d for d in live_all if d not in direct]

    retire_ids = [sid]
    touched, status_ops, notes = [], [], []
    if live_all and not (cascade or drop_dependency):
        raise MutationError(
            f"refusing to retire {sid}: {len(live_all)} live session(s) still depend on "
            f"it — direct: {direct or '(none)'}; transitive: {transitive_only or '(none)'}. "
            "WONTFIX counts as done to the dispatcher, so they would run WITHOUT the "
            "input they were written to consume. Choose one and re-run:\n"
            f"  --cascade           retire them too (same reason)\n"
            f"  --drop-dependency   keep them, drop {sid} from their depends_on and warn "
            "them in their prompt (same transaction)"
        )

    if cascade and live_all:
        not_retireable = {d: statuses.get(d, "TODO") for d in live_all
                          if statuses.get(d, "TODO") not in RETIREABLE}
        if not_retireable:
            raise MutationError(
                f"refusing to cascade: dependent(s) {not_retireable} are not in a "
                f"retireable state ({'/'.join(sorted(RETIREABLE))}). Resolve them first."
            )
        retire_ids += live_all

    if drop_dependency and direct:
        not_todo = {d: statuses.get(d, "TODO") for d in live_all
                    if statuses.get(d, "TODO") != "TODO"}
        if not_todo:
            raise MutationError(
                f"refusing to drop the dependency: dependent(s) {not_todo} are not TODO, "
                "so their prompt cannot be amended (they were dispatched against the old "
                "text). Resolve them first, or --cascade."
            )
        for d in direct:
            dep = by_id[d]
            dep.setdefault("dispatch", {})["depends_on"] = [
                x for x in _deps(dep) if x != sid
            ]
            dep["prompt"] = (dep.get("prompt") or "") + _DEP_DROPPED_NOTE.format(
                at=_now()[:10], gone=sid, reason=reason.strip()
            )
            touched.append(d)
            status_ops.append((d, statuses.get(d, "TODO"),
                               f"dependency on {sid} dropped — {sid} retired: {reason.strip()}"))
        notes.append(f"dropped {sid} from depends_on of {', '.join(direct)}")

    for rid in retire_ids:
        why = reason.strip() if rid == sid else f"producer {sid} retired — {reason.strip()}"
        status_ops.append((rid, "WONTFIX", f"retired: {why}"))
    # Items owned ONLY by retired sessions have no producer left; leaving them
    # TODO overstates remaining work on a decision already taken.
    items = [i for i in _exclusive_items(after, retire_ids) if statuses.get(i, "TODO") == "TODO"]
    for iid in items:
        status_ops.append((iid, "WONTFIX", f"retired with {sid}: {reason.strip()}"))

    summary = f"retired {sid}: {reason.strip()}"
    if len(retire_ids) > 1:
        summary += f" (cascaded to {', '.join(retire_ids[1:])})"
    if notes:
        summary += " (" + "; ".join(notes) + ")"

    result = _run(
        plan_dir, spec, after,
        op="retire-session", session=sid, summary=summary,
        touched_sessions=touched, items_touched=items,
        status_ops=status_ops, allow_builder_drift=allow_builder_drift,
    )
    result["retired"] = retire_ids
    result["dependents_amended"] = touched
    return result


# --------------------------------------------------------------------------
# Consistency check — the definition of "not a mixed generation"
# --------------------------------------------------------------------------
def consistency_report(plan_dir):
    """Every surface derivable from spec.json, checked against what is on disk.

    A mixed-generation plan (some files from before a mutation, some from after)
    fails here. Used by the crash-recovery tests and cheap enough to run anywhere.
    """
    plan_dir = Path(plan_dir)
    spec = load_spec(plan_dir)
    bad = []
    prior_html = (plan_dir / "PLAN.html").read_bytes().decode("utf-8")
    if render_html_for(spec, plan_dir, prior_html) != prior_html:
        bad.append("PLAN.html does not match a render of spec.json")

    manifest = json.loads((plan_dir / "manifest.json").read_text())
    gen = bp.gen_manifest(spec)
    gen["created"] = manifest.get("created", gen.get("created"))
    # Same carve-out as `build_generation`, but ONE-WAY: a plan built by an OLDER
    # builder is CONSISTENT, not drifted, when the current builder stamps a newer
    # version. A stamp HIGHER than this builder can produce is a different thing —
    # nothing legitimate writes it, and copying it wholesale meant the field was
    # never compared at all, so a hand-edited stamp could silently opt a plan into
    # version-gated runtime behaviour (escalation among them) and never show up as
    # drift. Above the builder's own version, let the comparison fail.
    stamped = manifest.get("plan_schema_version")
    if stamped is not None and int(stamped) <= int(gen.get("plan_schema_version") or 0):
        gen["plan_schema_version"] = stamped
    if gen != manifest:
        bad.append("manifest.json does not match gen_manifest(spec.json)")

    anchored = set(ab.read_all_statuses(prior_html))
    for s in spec["sessions"]:
        if s["id"] not in anchored:
            bad.append(f"session {s['id']} has no article in PLAN.html")
        for suffix in ("prompt", "context"):
            p = plan_dir / "sessions" / f"{s['id']}.{suffix}.md"
            if not p.is_file() or not p.stat().st_size:
                bad.append(f"missing sessions/{s['id']}.{suffix}.md")
    for it in spec["items"]:
        if it["id"] not in anchored:
            bad.append(f"item {it['id']} has no article in PLAN.html")
    return {"ok": not bad, "problems": bad}
