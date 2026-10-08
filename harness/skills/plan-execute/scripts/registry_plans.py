"""registry.py's plan-lifecycle group: discovering plans, reading `.lock`
pidfiles, and classifying each plan ACTIVE/STALE-LOCK/DONE/UNKNOWN.

Split out of registry.py (was 594 LOC, over the 500 file-size limit) — see
registry.py's module docstring for the full design rationale (constraint 2:
PID liveness is not plan liveness).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import article_block as ab
import dispatch as dsp
import manifest_io as mio
import run_state_io as rsi
from registry_guard import _guarded

PLAN_HTML = "PLAN.html"


def discover_plans(plans_root):
    """Every `_plans/*/` with a manifest.json — same test `status --all` uses."""
    d = Path(plans_root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if (p / "manifest.json").exists())


def lock_info(plan_dir):
    """Raw `.lock` pidfile contents plus liveness/age, or None if unlocked.

    The pidfile CONTRACT (`{pid, started_at, host}` at `<plan_dir>/.lock`) is
    documented in run_state_io.py's module docstring, so reading it directly
    here is reading a public file shape, not duplicating logic. Only the
    pid-liveness *check itself* is reused from run_state_io (`_pid_alive`)
    rather than re-implemented, per the session brief's instruction to flag
    rather than copy if that reader cannot be imported cleanly — it can.
    """
    lock_path = Path(plan_dir) / ".lock"
    if not lock_path.exists():
        return None
    try:
        info = json.loads(lock_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"pid": None, "started_at": None, "host": None, "alive": False, "age_s": None}
    if not isinstance(info, dict):
        # Valid JSON but the wrong shape (a list, string, number, bool, or
        # null — e.g. a `.lock` hand-edited to `[]` or `3`). `info.get(...)`
        # below would raise AttributeError for any of these; degrade to the
        # same null record as an unparseable pidfile rather than crash the
        # whole read-only plans-status run over one plan's malformed file.
        return {"pid": None, "started_at": None, "host": None, "alive": False, "age_s": None}
    pid = info.get("pid")
    started = info.get("started_at")
    try:
        alive = rsi._pid_alive(pid) if isinstance(pid, int) else False
    except (OSError, OverflowError):
        # A pid value out of the platform's valid range (hand-edited or
        # truncated pidfile) raises from `os.kill` itself, not just from a
        # missing process — degrade the same as "not alive", never crash.
        alive = False
    age_s = None
    if started:
        try:
            age_s = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        except (ValueError, TypeError):
            # ValueError: not a parseable ISO string. TypeError: a naive (no
            # tzinfo) started_at parses fine but can't subtract against the
            # aware `datetime.now(UTC)` above — a legacy or hand-edited
            # pidfile must degrade to age_s=None, never crash the whole
            # read-only plans-status run.
            age_s = None
    return {"pid": pid, "started_at": started, "host": info.get("host"),
            "alive": alive, "age_s": age_s}


def _html_text(plan_dir):
    p = Path(plan_dir) / PLAN_HTML
    return p.read_text() if p.is_file() else None


def plan_lifecycle_active(plan_dir, errors=None, detail=None):
    """True: at least one session is non-terminal — `dispatch.next_action`
    (the same logic `run.py plan` uses to decide whether there is anything
    left to dispatch) says the plan is not `complete`. This includes an
    ALL-BLOCKED plan: `next_action` returns `action="blocked"` (not
    `"complete"`) whenever every ready-or-blocking session has settled
    BLOCKED, so this function reports `True` there too — BLOCKED is
    non-terminal, there is a human decision outstanding, and that is real
    outstanding work, not a finished plan. False: every session settled
    DONE/WONTFIX/DEFERRED and the plan is not halted (an all-BLOCKED plan
    never reaches this branch — see above). None: manifest or PLAN.html
    could not be read (or read but malformed in any way that would
    otherwise raise) — reported unknown, never guessed. A missing
    PLAN.html (no exception, just nothing to read yet) also returns None
    but is NOT recorded in `errors` — that is a legitimate "nothing built
    yet" state, not a malformed file.

    Deliberately does NOT fold BLOCKED into STALE-LOCK on its own: an
    absent `.lock` file (the common case for an all-BLOCKED plan — `begin`
    already exited) is not staleness evidence, only an actually old dead
    lock is (see `classify_plan`) — inventing staleness from "no lock
    file" would misfire on a freshly-built, never-begun plan that also has
    no lock yet. `detail`, if given a dict, is populated with
    `{"blocked": bool}` on a successful read — True exactly when the
    non-terminal state came from `action == "blocked"` — so `classify_plan`
    can mark an active-but-blocked row without adding a new lifecycle value.

    Routed through `_guarded` (gate llm-review-low, attempt 4), not a local
    try/except: a manifest.json that parses as valid JSON but isn't the
    expected dict shape (e.g. `[]`) raises AttributeError out of
    `dispatch.next_action`'s dict walk, not `mio.ManifestError`, and that
    used to be swallowed silently right here — `classify_plan` set
    `lifecycle = "unknown"` but `errors` stayed empty, so neither the JSON
    output nor `render_table`'s `[UNREADABLE — see ERRORS]` marker ever
    showed the degrade happened. One guard, one report: the same `_guarded`
    + `errors` dict every other degraded read in this module already uses.
    """
    if errors is None:
        errors = {}

    def _read():
        manifest = mio.load_manifest(plan_dir)
        html_text = _html_text(plan_dir)
        if html_text is None:
            return None
        statuses = ab.read_all_statuses(html_text)
        action = dsp.next_action(manifest, statuses).get("action")
        if detail is not None:
            detail["blocked"] = action == "blocked"
        if action != "complete":
            return True
        # §15.3 — "A land that parks and is never revisited is REPORTED, never
        # reaped." Every session terminal is NOT the end of an isolated plan's
        # life: it still has to LAND. Read straight off `dsp.next_action`, such a
        # plan reads `done`, and `registry_owners` then classifies its worktree
        # `leftover`/`stale` — the reapable class — while an unresolved conflict,
        # a red re-gate or an un-acked candidate is still sitting in it. That is
        # §15.3 inverted, so the land state is consulted here too.
        if _land_unfinished(plan_dir) or _finish_unfinished(plan_dir):
            return True
        state = rsi.load_state(plan_dir)
        return bool((state.get("halt") or {}).get("set"))

    return _guarded(errors, Path(plan_dir).name, "manifest/PLAN.html", _read)


def _land_unfinished(plan_dir):
    """True when this plan is ISOLATED and its land has not completed.

    Imported lazily: `land_state` pulls in `plan_scope`/`plan_worktree`, and the
    registry is imported by `run.py` long before any of that is needed. A plan
    below §6's version gate holds no isolation claim, so `context` is None and
    this answers False — today's behaviour, unchanged, for every plan on disk.
    """
    try:
        import land_state as lst
        ctx = lst.context(plan_dir)
        if ctx is None:
            return False
        return (lst.load(plan_dir, ctx) or {}).get("state") != "landed"
    except Exception:                  # noqa: BLE001 — a registry read never raises
        return False


def _finish_unfinished(plan_dir):
    """True when this plan is ISOLATED, landed, and finish has not reported yet, so
    the janitor never reaps a worktree finish still has to report on. A plan that
    landed before finish existed (no ``finish.json`` and no ``landed_base``, which
    only finish-era ``land_push`` writes) is not waiting on finish. A read error
    answers live: a registry read never raises, and live is the safe answer."""
    try:
        import finish
        import land_state as lst
        ctx = lst.context(plan_dir)
        land = (lst.load(plan_dir, ctx) or {}) if ctx else {}
        if land.get("state") != "landed":
            return False
        st = finish.ssio.read_json_with_bak(finish.state_path(ctx["root"], ctx["slug"]))
        if not st and "landed_base" not in land:
            return False
        return not (st or {}).get("finished_at")
    except Exception:                  # noqa: BLE001
        return True


def land_row(plan_dir):
    """§15.3's reporting half: what `plans-status` shows for a parked land.

    None when there is nothing to say. Otherwise the state, the age of the park
    and the land worktree's path — "with the age of the park and the conflicted
    land worktree's path" is the rule, verbatim.
    """
    try:
        import land_state as lst
        ctx = lst.context(plan_dir)
        st = (lst.load(plan_dir, ctx) if ctx else None) or {}
        import plan_worktree as pwt
        retired = (pwt.load_state(plan_dir) or {}).get("retired")
    except Exception:                  # noqa: BLE001
        return None
    # `retire-plan` ended the plan and never touches land.json, so a candidate
    # parked before it would ask for an ack forever (2026-09-18: a plan merged by
    # PR outside the land stage kept "approve the land" on the fleet page).
    if not st or st.get("state") == "landed" or retired:
        return None
    park = st.get("park") or {}
    return {"state": st.get("state"), "park_kind": park.get("kind"),
            "parked_at": park.get("at"), "land_worktree": st.get("land_path"),
            "awaiting_ack": st.get("state") == "awaiting_review"}


def classify_plan(plan_dir, errors=None):
    """One registry row for a plan: `lifecycle` in
    {"active", "stale-lock", "done", "unknown"}.

    ACTIVE fires on non-terminal status alone, regardless of pid (constraint 2
    in registry.py's module docstring). STALE-LOCK is the narrower, additional
    flag: the lock itself looks abandoned (dead pid AND older than the stale
    threshold) while work is still recorded outstanding — reported, never
    reclaimed here.

    `needs_attention` is a MARKER on the row, not a fifth lifecycle value (so
    no downstream consumer keyed on the 4-value set breaks): True when the
    plan is non-terminal (`lifecycle` "active" or "stale-lock") because every
    outstanding session settled BLOCKED — `plan_lifecycle_active`'s `detail`
    output. An all-BLOCKED plan is genuinely different from one that is still
    progressing (nothing will ever dispatch again without a human decision),
    but until this marker existed nothing distinguished the two rows.

    `lock_info` is routed through `_guarded` (gate llm-review-low, attempt 3 —
    the fourth instance of the same defect family): `classify_plan` is mapped
    over every plan by `collect`/`plans-status` with no surrounding try/except
    of its own, so ANY exception `lock_info` could raise — not just the
    non-dict-JSON case that prompted this fix — would abort the whole
    read-only run over one plan's `.lock` file. `_guarded` degrades that one
    plan's lock to None (treated the same as "no lock file") and records the
    failure in `errors`, never propagates.
    """
    if errors is None:
        errors = {}
    detail = {}
    active = plan_lifecycle_active(plan_dir, errors, detail)
    lock = _guarded(errors, Path(plan_dir).name, ".lock", lambda: lock_info(plan_dir))
    if active is None:
        lifecycle = "unknown"
    elif not active:
        lifecycle = "done"
    else:
        # LCK-02: a lock recorded on ANOTHER machine is never "stale-lock" here
        # — `alive` was computed from THIS host's pid table, which says nothing
        # about a process on that one. Reported as `active` with its host name.
        stale = bool(
            lock and rsi.lock_on_this_host(lock) and not lock["alive"]
            and lock.get("age_s") is not None
            and lock["age_s"] > rsi.STALE_LOCK_SECONDS
        )
        lifecycle = "stale-lock" if stale else "active"
    needs_attention = bool(active) and bool(detail.get("blocked"))
    return {
        "plan": Path(plan_dir).name,
        "plan_dir": str(plan_dir),
        "lifecycle": lifecycle,
        "needs_attention": needs_attention,
        "has_begun": has_begun(plan_dir),
        "lock": lock,
        "land": land_row(plan_dir),
    }


def has_begun(plan_dir):
    """Has this plan EVER dispatched? A MARKER on the row, like
    ``needs_attention`` — deliberately not a fifth ``lifecycle`` value, so no
    consumer keyed on the 4-value set breaks.

    EITHER artefact answers yes:

    * ``run.ndjson`` — ``rsi.log_event`` appends to it and nothing ever deletes
      it, so its existence is a durable "work happened here". Measured
      2026-08-23 that the read-only commands leave it alone (`plan`,
      `--status` and `plans-status` were each run against a never-begun plan
      and none created it), so merely inspecting a plan cannot flip this marker.
    * ``.lock`` — only ``begin`` writes one. This second arm is not belt-and-
      braces: ``begin`` acquires the lock BEFORE it logs, so a plan interrupted
      in that window has a live lock and no events, and the ndjson test alone
      would call a genuinely-running neighbour a phantom. The existing
      REG-02 tests are built exactly that way (live pid, no events) and are
      what surfaced it.

    A plan that was BUILT and never run has neither.

    Why it exists: ``lifecycle`` is ACTIVE on non-terminal session status alone
    (constraint 2 — pid liveness is not plan liveness), which is right for a
    plan mid-run whose CLI has long since exited. But it also makes every
    drafted-and-never-started plan "active" forever, and REG-02's begin refusal
    then names them as tree-sharing neighbours. Seven such drafts had
    accumulated here by 2026-08-23, so every new plan needed ``--concurrent`` —
    and a flag you pass reflexively is a control you have switched off for the
    case that matters.
    """
    # `rsi._lock_path` rather than a re-typed ".lock": one definition, so a
    # rename there cannot leave this silently reading a path nothing writes.
    return (Path(plan_dir) / "run.ndjson").exists() or rsi._lock_path(plan_dir).exists()
